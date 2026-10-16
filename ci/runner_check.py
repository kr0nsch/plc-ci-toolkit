#!/usr/bin/env python3
"""Проверка self-hosted раннера перед заданием CODESYS (.github/workflows/plc-codesys.yml).

    python <toolkit>/ci/runner_check.py [--root .] [--workspace DIR] [--toolkit-dir NAME]

1. Python не ниже 3.11 — его требует tools/codesys/cds.py;
2. корень ПЛК-репозитория (--root) — на шаблоне plc-repo-template: есть tools/codesys/cds.py и
   tools/codesys/config.json;
3. каталог выгрузки plc-ci-toolkit (по умолчанию .plc-ci-toolkit в $GITHUB_WORKSPACE) добавляется в
   .git/info/exclude: cds.py test записывает в результаты коммит с пометкой «-dirty», если в рабочем
   дереве есть посторонние файлы;
4. версия Python — выход шага version (файл $GITHUB_OUTPUT), сведения о раннере — в журнал.

Код выхода: 0 — раннер готов, 1 — нет. Сообщения об ошибках — аннотациями ::error. Синтаксис —
Python 3.7+: на старом интерпретаторе скрипт сообщает версию, а не падает с SyntaxError.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

MIN_PYTHON = (3, 11)
TOOLKIT_DIR = ".plc-ci-toolkit"
TEMPLATE_FILES = ("tools/codesys/cds.py", "tools/codesys/config.json")


def error(message: str) -> None:
    print("::error title=plc-codesys::" + message.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def python_version(info: Sequence[int] | None = None) -> tuple[str, bool]:
    """Строка версии и признак «не ниже 3.11»."""
    current = tuple(info if info is not None else sys.version_info[:3])
    text = ".".join(str(part) for part in current[:3])
    return text, current[:2] >= MIN_PYTHON


def missing_template_files(root: Path) -> list[str]:
    return [rel for rel in TEMPLATE_FILES if not (root / rel).is_file()]


def exclude_toolkit(workspace: Path, toolkit_dir: str) -> str | None:
    """Добавляет /<toolkit_dir>/ в .git/info/exclude; возвращает путь файла или None (нет .git)."""
    info = workspace / ".git" / "info"
    if not (workspace / ".git").is_dir():
        return None
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    line = "/" + toolkit_dir.strip("/\\") + "/"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in current.splitlines():
        with open(exclude, "a", encoding="utf-8", newline="\n") as fh:
            if current and not current.endswith("\n"):
                fh.write("\n")
            fh.write(line + "\n")
    return str(exclude)


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(name + "=" + value + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="runner_check.py", description="Проверка раннера для plc-codesys.yml.")
    parser.add_argument("--root", default=".", help="корень ПЛК-репозитория (working-directory)")
    parser.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", "."), help="$GITHUB_WORKSPACE")
    parser.add_argument("--toolkit-dir", default=TOOLKIT_DIR, help="каталог выгрузки plc-ci-toolkit")
    args = parser.parse_args(argv)

    ok = True
    version, version_ok = python_version()
    write_output("version", version)
    print("runner_check: Python " + version + " (" + sys.executable + "), " + platform.platform())
    if not version_ok:
        error(
            "нужен Python 3.11+ (tools/codesys/cds.py), на раннере " + version + ": установите его для "
            "пользователя, под которым работает раннер, или задайте вход python"
        )
        ok = False
    root = Path(args.root)
    missing = missing_template_files(root)
    if missing:
        error(
            "нет " + ", ".join(missing) + " в " + str(root.resolve()) + ": репозиторий должен быть на шаблоне "
            "plc-repo-template (или задайте вход working-directory)"
        )
        ok = False
    exclude = exclude_toolkit(Path(args.workspace), args.toolkit_dir)
    if exclude:
        print("runner_check: /" + args.toolkit_dir + "/ исключён из git status (" + exclude + ")")
    else:
        print("runner_check: в рабочем каталоге нет .git — коммит в результатах тестов не определится")
    print("runner_check: " + ("раннер готов" if ok else "раннер не готов"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
