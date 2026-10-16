#!/usr/bin/env python3
"""Хук pre-commit «исходники выгружены»: правки проекта CODESYS не остаются вне src/ и tests/.

В репозиториях на шаблоне plc-repo-template источник истины — тексты ST в src/ и tests/. Проект
CODESYS (по умолчанию build/<имя>.project и build/<имя>-tests.project) собирается из них командой
``python tools/codesys/cds.py import`` и в git не хранится. Если проект правили в IDE, правки нужно
выгрузить в тексты (``cds.py export``) до коммита: иначе они останутся только в build/ и пропадут
при следующем ``cds.py import --force`` или ``--clean``.

Проверки:

1. Пути, которые передаёт pre-commit (файлы коммита): в коммите нет файлов CODESYS
   (.project, .projectarchive, .library, .compiled-library) — проект в git не храним.
2. Каждый проект в build/ (и по путям из настроек cds.py) не менялся после последнего успешного
   ``cds.py import/export``. Отпечаток проекта (mtime_ns и размер) cds.py записывает в
   build/codesys/state.json; то же сравнение делает сам ``cds.py import``, прежде чем перезаписать
   проект. Отпечаток другой — проект сохранён в IDE после import/export: коммит блокируется с
   подсказкой «выполните python tools/codesys/cds.py export». Записи о проекте нет — состояние
   неизвестно: тоже блокировка (с ``--allow-unknown`` — только предупреждение).
3. Есть файл блокировки ``<проект>.~u`` — проект открыт в CODESYS: несохранённые правки хук не видит,
   выводится предупреждение.

Хук ничего не записывает и CODESYS не запускает; без CODESYS и без build/ проверять нечего — успех.
Описание и ограничения — docs/pre_commit.md.

Коды выхода: 0 — можно коммитить; 1 — коммит остановлен; 2 — неверные аргументы.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

HOOK_ID = "codesys-sources-exported"
STATE_FILE = "build/codesys/state.json"
BUILD_DIR = "build"
LOCK_SUFFIX = ".~u"
CODESYS_BINARY_SUFFIXES = (".project", ".projectarchive", ".library", ".compiled-library")
CDS = "python tools/codesys/cds.py"
EXPORT_CHECK_DIR = "build/export-check"
DOCS_URL = "https://github.com/<GITHUB_USER>/plc-ci-toolkit/blob/main/docs/pre_commit.md"

MODE_NORMAL = "normal"
MODE_TESTS = "tests"

EXIT_OK = 0
EXIT_BLOCKED = 1

# Состояния проекта
STATE_OK = "ok"
STATE_CHANGED = "changed"
STATE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fingerprint:
    """Отпечаток файла проекта в том же виде, что пишет cds.py (cdshost/cli.py, _fingerprint)."""

    mtime_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> Fingerprint | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return cls(st.st_mtime_ns, st.st_size)

    @classmethod
    def parse(cls, data: object) -> Fingerprint | None:
        if not isinstance(data, Mapping):
            return None
        mtime_ns, size = data.get("mtime_ns"), data.get("size")
        if type(mtime_ns) is not int or type(size) is not int:
            return None
        return cls(mtime_ns, size)


@dataclass
class ProjectCheck:
    """Итог проверки одного проекта."""

    path: Path
    mode: str
    state: str
    open_in_ide: bool = False
    sources_changed: bool = False


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Состояние cds.py: build/codesys/state.json
# ---------------------------------------------------------------------------


def path_key(path: Path | str) -> str:
    """Ключ для сравнения путей (регистр и разделители — как в ОС)."""
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(frozen=True)
class StateEntry:
    """Запись state.json: путь проекта (как записал cds.py) и данные записи."""

    path: Path
    data: object


def load_state(root: Path) -> tuple[dict[str, StateEntry], str | None]:
    """Записи state.json по ключу path_key(путь проекта) и текст ошибки чтения (или None)."""
    path = root / STATE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"не читается {STATE_FILE}: {exc}"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {}, f"{STATE_FILE}: неверный JSON ({exc})"
    if not isinstance(data, dict):
        return {}, f"{STATE_FILE}: ожидался объект JSON"
    return {path_key(key): StateEntry(Path(key), value) for key, value in data.items()}, None


def sources_digest(root: Path, subdirs: Sequence[str]) -> str:
    """SHA-256 файлов *.st раскладки — тот же алгоритм, что cds.py (cdshost/cli.py, sources_digest)."""
    digest = hashlib.sha256()
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.st")):
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def layout_subdirs(mode: str) -> tuple[str, ...]:
    return ("src", "tests") if mode == MODE_TESTS else ("src",)


# ---------------------------------------------------------------------------
# Пути проектов по настройкам cds.py (config.json и переменные окружения)
# ---------------------------------------------------------------------------


def _load_cds_cli(root: Path) -> ModuleType | None:
    """Модуль cdshost.cli репозитория (tools/codesys) или None, если его нет или он не загрузился."""
    cli_path = root / "tools" / "codesys" / "cdshost" / "cli.py"
    if not cli_path.is_file():
        return None
    codesys_dir = str(cli_path.parent.parent)
    added = codesys_dir not in sys.path
    if added:
        sys.path.insert(0, codesys_dir)  # cli.py импортирует stlayout из tools/codesys
    name = "_cds_cli_" + hashlib.sha256(path_key(cli_path).encode("utf-8")).hexdigest()[:12]
    try:
        spec = importlib.util.spec_from_file_location(name, cli_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:  # другая версия шаблона, синтаксис, импорт — работаем без настроек cds.py
        sys.modules.pop(name, None)
        return None
    finally:
        if added:
            with contextlib.suppress(ValueError):
                sys.path.remove(codesys_dir)


def resolve_projects(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, tuple[Path, str]]:
    """Проекты cds.py этого репозитория: {path_key: (путь, режим)}; пусто, если настройки недоступны.

    Пути разрешает сам cds.py (config.json, переменные окружения, в Windows — и HKCU\\Environment),
    поэтому хук находит проекты там же, где их создаёт ``cds.py import``. ``environ`` — окружение
    вместо настоящего (без реестра), для тестов.
    """
    cli = _load_cds_cli(root)
    if cli is None:
        return {}
    try:
        if environ is None:
            settings = cli.make_settings(root)
        else:
            getenv = cli.make_getenv(environ, registry=False)
            config = Path(getenv("CDS_CONFIG") or root / "tools" / "codesys" / "config.json")
            settings = cli.Settings(cli.load_config(config), root.resolve(), getenv)
        normal = Path(str(settings.project_path(False)))
        tests = Path(str(settings.project_path(True)))
    except Exception:  # ошибка config.json или переменных — проверка по state.json и build/*.project
        return {}
    return {path_key(normal): (normal, MODE_NORMAL), path_key(tests): (tests, MODE_TESTS)}


def guess_mode(path: Path) -> str:
    """Режим по имени файла, если настройки cds.py недоступны: <имя>-tests.project — тестовый проект."""
    return MODE_TESTS if path.name.lower().endswith("-tests.project") else MODE_NORMAL


def is_inside(path: Path, root: Path) -> bool:
    base = path_key(root).rstrip("\\/")
    return path_key(path).startswith(base + os.sep)


def find_projects(
    root: Path, state: Mapping[str, StateEntry], resolved: Mapping[str, tuple[Path, str]]
) -> list[tuple[Path, str]]:
    """Существующие проекты для проверки: build/*.project, пути из настроек cds.py и записи state.json.

    Записи state.json о проектах вне репозитория (например, оставшиеся в копии другого клона)
    пропускаются, если это не путь из настроек cds.py.
    """
    found: dict[str, tuple[Path, str]] = {}

    def add(path: Path) -> None:
        key = path_key(path)
        if key in found or not path.is_file():
            return
        mode = resolved[key][1] if key in resolved else guess_mode(path)
        found[key] = (path, mode)

    for path in sorted((root / BUILD_DIR).glob("*.project")):
        add(path)
    for path, _mode in resolved.values():
        add(path)
    for key, entry in state.items():
        if entry.path.is_absolute() and (is_inside(entry.path, root) or key in resolved):
            add(entry.path)
    return sorted(found.values(), key=lambda item: path_key(item[0]))


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------


def check_project(root: Path, path: Path, mode: str, entry: object) -> ProjectCheck:
    recorded = Fingerprint.parse(entry.get("project")) if isinstance(entry, Mapping) else None
    current = Fingerprint.of(path)
    result = ProjectCheck(path=path, mode=mode, state=STATE_UNKNOWN)
    result.open_in_ide = Path(str(path) + LOCK_SUFFIX).exists()
    if recorded is None or current is None:
        return result
    if recorded == current:
        result.state = STATE_OK
        return result
    result.state = STATE_CHANGED
    sources = entry.get("sources") if isinstance(entry, Mapping) else None
    if isinstance(sources, str):
        result.sources_changed = sources != sources_digest(root, layout_subdirs(mode))
    return result


def staged_codesys_files(files: Iterable[str]) -> list[str]:
    """Файлы CODESYS среди путей коммита (расширение без учёта регистра)."""
    return [name for name in files if name.lower().endswith(CODESYS_BINARY_SUFFIXES)]


def display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def export_command(mode: str) -> str:
    return f"{CDS} export" + (" --tests" if mode == MODE_TESTS else "")


def import_command(mode: str, force: bool = False) -> str:
    return f"{CDS} import" + (" --tests" if mode == MODE_TESTS else "") + (" --force" if force else "")


def describe(check: ProjectCheck, root: Path, allow_unknown: bool, report: Report) -> None:
    name = display(check.path, root)
    layout = "src/ и tests/" if check.mode == MODE_TESTS else "src/"
    if check.state == STATE_CHANGED:
        lines = [
            f"{name}: проект сохранён в IDE после последнего cds.py import/export — правки не выгружены в {layout}.",
            f"  Чтобы сохранить правки, выполните {export_command(check.mode)} и добавьте изменения "
            f"{layout} в коммит (git status).",
        ]
        if check.sources_changed:
            lines += [
                f"  Внимание: {layout} тоже менялись после того import/export — cds.py export откажется их "
                "перезаписать. Выгрузите проект в отдельный каталог и перенесите правки вручную:",
                f"  {export_command(check.mode)} --to {EXPORT_CHECK_DIR}",
            ]
        lines.append(
            f"  Правки из IDE не нужны — {import_command(check.mode, force=True)} (проект пересоберётся из {layout})."
        )
        report.errors.append("\n".join(lines))
    elif check.state == STATE_UNKNOWN:
        lines = [
            f"{name}: нет записи cds.py о последнем import/export ({STATE_FILE}) — нельзя проверить, "
            "выгружены ли правки из IDE.",
            f"  Если проект правили в IDE, сравните выгрузку с {layout}: {export_command(check.mode)} "
            f"--to {EXPORT_CHECK_DIR}, затем выполните {export_command(check.mode)}.",
            f"  Если не правили — {import_command(check.mode)} (проект пересоберётся из {layout}, запись появится).",
        ]
        (report.warnings if allow_unknown else report.errors).append("\n".join(lines))
    if check.open_in_ide:
        report.warnings.append(
            f"{name}: проект открыт в CODESYS (есть {name}{LOCK_SUFFIX}) или IDE завершилась со сбоем — "
            "несохранённые правки хук не видит.\n"
            f"  Сохраните проект, закройте IDE и выполните {export_command(check.mode)}."
        )


def run_checks(
    root: Path,
    files: Sequence[str],
    allow_unknown: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Report:
    report = Report()
    for name in staged_codesys_files(files):
        report.errors.append(
            f"{name}: файл CODESYS в коммите. Проект в git не храним — он собирается из src/ и tests/ "
            f"({CDS} import).\n"
            f'  Уберите файл из индекса: git rm --cached -- "{name}"; правки из IDE — '
            f"выполните {CDS} export (с --tests для тестового проекта)."
        )
    state, state_error = load_state(root)
    if state_error:
        report.warnings.append(f"{state_error}; проекты без записи считаются непроверенными")
    resolved = resolve_projects(root, environ)
    for path, mode in find_projects(root, state, resolved):
        entry = state.get(path_key(path))
        check = check_project(root, path, mode, entry.data if entry else None)
        describe(check, root, allow_unknown, report)
    return report


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------


def git_toplevel() -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = proc.stdout.strip()
    return Path(top) if proc.returncode == 0 and top else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_exported.py",
        description=(
            "Проверка перед коммитом: правки проекта CODESYS из IDE выгружены в src/ и tests/ "
            "(cds.py export), файлов .project и .library в коммите нет."
        ),
    )
    parser.add_argument("files", nargs="*", help="файлы коммита (передаёт pre-commit)")
    parser.add_argument("--root", help="корень репозитория (по умолчанию — корень рабочей копии git)")
    parser.add_argument(
        "--allow-unknown",
        action="store_true",
        help="проект без записи cds.py в build/codesys/state.json — предупреждение вместо ошибки",
    )
    return parser


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output()
    args = build_parser().parse_args(argv)
    root = Path(args.root) if args.root else (git_toplevel() or Path.cwd())
    report = run_checks(root.resolve(), args.files, allow_unknown=args.allow_unknown)
    for text in report.warnings:
        print(f"предупреждение: {text}")
    for text in report.errors:
        print(f"ОШИБКА: {text}")
    if report.errors:
        print(f"{HOOK_ID}: коммит остановлен, ошибок: {len(report.errors)}. Описание проверки — {DOCS_URL}")
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
