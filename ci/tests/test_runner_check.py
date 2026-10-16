"""Тесты ci/runner_check.py: проверка раннера перед заданием CODESYS."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import runner_check

SCRIPT = Path(runner_check.__file__)


def make_repo(root: Path, with_git: bool = True) -> Path:
    for rel in runner_check.TEMPLATE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    if with_git:
        (root / ".git" / "info").mkdir(parents=True)
        (root / ".git" / "info" / "exclude").write_text(
            "# git ls-files --others --exclude-from=.git/info/exclude", "utf-8"
        )
    return root


def test_python_version() -> None:
    assert runner_check.python_version((3, 11, 0)) == ("3.11.0", True)
    assert runner_check.python_version((3, 12, 10)) == ("3.12.10", True)
    assert runner_check.python_version((3, 10, 14)) == ("3.10.14", False)
    assert runner_check.python_version()[1] is True  # тесты идут на 3.11+


def test_exclude_toolkit_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    path = runner_check.exclude_toolkit(repo, ".plc-ci-toolkit")
    assert path is not None
    runner_check.exclude_toolkit(repo, ".plc-ci-toolkit/")
    text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert text.endswith("\n/.plc-ci-toolkit/\n")
    assert text.count("/.plc-ci-toolkit/") == 1
    assert runner_check.exclude_toolkit(tmp_path / "no-git", ".plc-ci-toolkit") is None


def test_main_ok_writes_version_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path / "repo")
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    assert runner_check.main(["--root", str(repo), "--workspace", str(repo)]) == 0
    assert outputs.read_text(encoding="utf-8").startswith("version=3.")
    out = capsys.readouterr().out
    assert "раннер готов" in out
    assert "/.plc-ci-toolkit/ исключён из git status" in out


def test_main_reports_missing_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert runner_check.main(["--root", str(empty), "--workspace", str(empty)]) == 1
    out = capsys.readouterr().out
    assert "::error title=plc-codesys::нет tools/codesys/cds.py, tools/codesys/config.json" in out
    assert "в рабочем каталоге нет .git" in out


def test_main_rejects_old_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path / "repo", with_git=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(runner_check, "python_version", lambda: ("3.10.14", False))
    assert runner_check.main(["--root", str(repo), "--workspace", str(repo)]) == 1
    assert "нужен Python 3.11+" in capsys.readouterr().out


def test_script_parses_with_old_python_grammar() -> None:
    """Скрипт должен сообщить о старом Python, а не упасть с SyntaxError: грамматика 3.7."""
    source = SCRIPT.read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 7))
    assert "from __future__ import annotations" in source  # аннотации не вычисляются на 3.7–3.9
