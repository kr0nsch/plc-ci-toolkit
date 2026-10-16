#!/usr/bin/env python3
"""sa_check.py — Static Analysis Light CODESYS: правила из файла, генерация кода, находки с файлом и строкой.

    python ci/sa_check.py [--root R] [--tests] [--rules FILE] [--out FILE] [--log FILE]

Перед запуском проект должен быть собран из исходников: python tools/codesys/cds.py build [--tests]
(в CI — тем же заданием). Скрипт:

1. читает правила (`--rules`, иначе `<root>/ci/sa_rules.json`, иначе `ci/sa_rules.json` рядом с
   этим файлом): {"rules": {"SA0033": "warning", ...}}, режимы error | warning | off;
2. запускает CODESYS без окна со скриптом ci/codesys/sa_light.py так же, как cds.py (одна строка
   запуска, файл задания в CDS_JOB, блокировка репозитория build/codesys/cds.lock): правила
   включаются в настройках проекта только на время сеанса, затем — генерация кода
   (`app.generate_code()`); проект не сохраняется;
3. дописывает к сообщениям поле source — файл и строку раскладки (как cds.py build) и пишет
   `build/sa.json` и `build/sa.log`.

Модули шаблона (tools/codesys/cdshost, cds_common.py, build.py) берутся из `<root>/tools/codesys`:
тулкит их не меняет. Почему не `cds.py build`: Static Analysis Light выполняется только при генерации
кода, а `cds.py build` вызывает `app.rebuild()` без неё (проверено на 3.5.17.30, docs/sa_light.md).

Коды выхода: 0 — анализ выполнен (находки SA на код не влияют: политику применяет
ci/sa_report.py); 1 — ошибки компиляции при генерации кода; 2 — аргументы или файл правил;
3 — окружение (нет CODESYS, проекта, tools/codesys, внутреннего API Static Analysis Light);
4 — таймаут; 5 — блокировка занята. Только стандартная библиотека Python 3.11+.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

CI_DIR = Path(__file__).resolve().parent
SA_SCRIPT = CI_DIR / "codesys" / "sa_light.py"
DEFAULT_RULES = CI_DIR / "sa_rules.json"
STATE_FILE = "build/codesys/state.json"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENV = 3


class SaError(Exception):
    """Ошибка с понятным сообщением и кодом выхода."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


def load_sa_light() -> Any:
    """Модуль ci/codesys/sa_light.py (общий для IronPython 2.7 и Python 3): разбор правил и итогов."""
    spec = importlib.util.spec_from_file_location("sa_light", SA_SCRIPT)
    if spec is None or spec.loader is None:
        raise SaError(f"не найден {SA_SCRIPT}", EXIT_ENV)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cli(root: Path) -> Any:
    """cdshost.cli шаблона из <root>/tools/codesys (тот же код, что у cds.py этого репозитория)."""
    template = root / "tools" / "codesys"
    if not (template / "cdshost" / "cli.py").is_file():
        raise SaError(
            f"нет {template / 'cdshost' / 'cli.py'}: --root должен указывать на репозиторий из шаблона "
            "(tools/codesys/cds.py)",
            EXIT_ENV,
        )
    if str(template) not in sys.path:
        sys.path.insert(0, str(template))
    module: ModuleType = importlib.import_module("cdshost.cli")
    return module


def rules_path(root: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    local = root / "ci" / "sa_rules.json"
    return local if local.is_file() else DEFAULT_RULES


def load_rules(path: Path, sa_light: Any) -> dict[str, str]:
    """Правила из файла: {"rules": {...}} или сам словарь; проверка — sa_light.parse_rules."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SaError(f"нет файла правил {path}", EXIT_USAGE) from None
    except json.JSONDecodeError as exc:
        raise SaError(f"{path}: неверный JSON: {exc}", EXIT_USAGE) from None
    spec = data.get("rules", data) if isinstance(data, Mapping) else data
    if isinstance(spec, Mapping):
        spec = {k: v for k, v in spec.items() if not str(k).startswith("_")}
    try:
        parsed = sa_light.parse_rules(spec)
    except ValueError as exc:
        raise SaError(f"{path}: {exc}", EXIT_USAGE) from None
    return {f"SA{number:04d}": mode for number, mode in parsed}


def freshness_warnings(cli: Any, root: Path, project: Path, subdirs: Sequence[str]) -> list[str]:
    """Проект старше исходников или сохранён в IDE после import — анализ был бы не по src/."""
    try:
        state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [
            f"нет отметки import для {project.name} ({STATE_FILE}): проект мог быть собран не из текущих исходников"
        ]
    entry = state.get(str(project)) if isinstance(state, Mapping) else None
    if not isinstance(entry, Mapping):
        return [f"нет отметки import для {project.name}: сначала python tools/codesys/cds.py build"]
    warnings = []
    if entry.get("sources") != cli.sources_digest(root, subdirs):
        warnings.append(
            f"исходники {', '.join(subdirs)} изменились после import проекта {project.name}: "
            "анализ идёт по старому проекту — запустите python tools/codesys/cds.py build"
        )
    stat = project.stat()
    recorded = entry.get("project")
    if isinstance(recorded, Mapping) and recorded != {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}:
        warnings.append(f"проект {project.name} изменён после import (сохранён в IDE?)")
    return warnings


def annotate(cli: Any, root: Path, messages: list[dict[str, Any]]) -> None:
    """Поле source у сообщений: файл раскладки и строка (функции шаблона cdshost.cli)."""
    try:
        objects = cli.layout_objects(root)
    except (OSError, UnicodeDecodeError):
        return
    for msg in messages:
        msg["source"] = cli.source_location(msg.get("object"), msg.get("position"), objects)


def sa_step(result: Mapping[str, Any]) -> dict[str, Any]:
    for step in result.get("steps", []):
        if isinstance(step, Mapping) and step.get("action") == "sa":
            return dict(step)
    return {}


def print_result(step: Mapping[str, Any], out: Path) -> None:
    for rule in step.get("rules", []):
        print(f"[sa] правило {rule.get('rule')} ({rule.get('name') or '?'}): {rule.get('mode')}")
    for msg in step.get("messages", []):
        code = str(msg.get("code") or "")
        if msg.get("severity") not in ("Error", "FatalError", "Warning"):
            continue
        if msg.get("prefix") == "SA" and not code:
            continue  # «Есть сообщения доп. проверок кода» — итог по находкам ниже
        where = f"{msg['source']}: " if msg.get("source") else ""
        if not where and msg.get("object"):
            where = f"[{msg['object']}{', ' + str(msg['position']) if msg.get('position') else ''}] "
        print(f"[sa]   {where}{msg.get('severity')} {code} {msg.get('text')}".replace("  ", " ").rstrip())
    for note in step.get("notes") or []:
        print(f"[sa] предупреждение: {note}")
    sa = step.get("sa") or {}
    if sa.get("download_blocked"):
        print(
            "[sa] CODESYS: при находках SA итог генерации кода — «... : загрузка невозможна» (служебная ошибка "
            "категории компиляции). Поэтому правила включаются только на время анализа и в проект не сохраняются."
        )
    by_code = ", ".join(f"{k}: {v}" for k, v in sorted((sa.get("by_code") or {}).items())) or "нет"
    status = "выполнен" if sa.get("started") else "НЕ выполнялся"
    print(
        f"[sa] Static Analysis Light {status}: находок {sa.get('findings', 0)} ({by_code}); "
        f"ошибок компиляции {step.get('errors', 0)}, предупреждений {step.get('warnings', 0)}; отчёт: {out}"
    )
    if step.get("error"):
        print(f"[sa]   {step['error']}")


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    sa_light = load_sa_light()
    rules_file = rules_path(root, args.rules)
    rules = load_rules(rules_file, sa_light)
    cli = load_cli(root)
    try:
        settings = cli.make_settings(repo_root=root)
        project = settings.project_path(args.tests)
    except cli.CdsError as exc:
        raise SaError(str(exc), exc.code) from None
    if not project.exists():
        flag = " --tests" if args.tests else ""
        raise SaError(f"нет проекта {project}: сначала python tools/codesys/cds.py build{flag}", EXIT_ENV)
    out = Path(args.out).resolve() if args.out else root / "build" / "sa.json"
    log = Path(args.log).resolve() if args.log else root / "build" / "sa.log"
    subdirs = ("src", "tests") if args.tests else ("src",)
    for warning in freshness_warnings(cli, root, project, subdirs):
        print(f"[sa] предупреждение: {warning}")
    print(f"[sa] правила: {rules_file}")
    with contextlib.suppress(FileNotFoundError):
        out.unlink()
    job = {
        "template_dir": str(root / "tools" / "codesys"),
        "steps": [
            {"action": "sa", "project": str(project), "rules": rules, "log": str(log), "report": str(out)},
        ],
    }
    try:
        with cli.repo_lock(settings, "sa"):
            result = cli.run_codesys(settings, str(SA_SCRIPT), job, "sa")
    except cli.CdsError as exc:
        raise SaError(str(exc), exc.code) from None
    step = sa_step(result)
    if out.exists():
        data = json.loads(out.read_text(encoding="utf-8"))
        messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        annotate(cli, root, messages)
        data["messages"] = messages
        data["rules_file"] = str(rules_file)
        data["tests_project"] = bool(args.tests)
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        step["messages"] = messages
    print_result(step, out)
    if result.get("error"):
        print(f"[sa] ошибка скрипта CODESYS: {result['error']}".rstrip())
    code = int(result.get("exit_code", EXIT_ENV))
    if code == 0:
        return EXIT_OK
    return EXIT_FAILED if code == 1 else EXIT_ENV


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sa_check.py",
        description="CODESYS Static Analysis Light: правила, генерация кода, build/sa.json (docs/sa_light.md).",
    )
    parser.add_argument("--root", default=".", help="корень репозитория из шаблона (по умолчанию текущий каталог)")
    parser.add_argument("--tests", action="store_true", help="анализировать тестовый проект (src/ и tests/)")
    parser.add_argument("--rules", metavar="FILE", help="файл правил (по умолчанию <root>/ci/sa_rules.json)")
    parser.add_argument("--out", metavar="FILE", help="отчёт JSON (по умолчанию <root>/build/sa.json)")
    parser.add_argument("--log", metavar="FILE", help="журнал (по умолчанию <root>/build/sa.log)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SaError as exc:
        print(f"[sa] ОШИБКА: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("[sa] прервано", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
