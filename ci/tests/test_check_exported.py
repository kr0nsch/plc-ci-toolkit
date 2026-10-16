"""Тесты ci/check_exported.py — хука pre-commit «исходники выгружены» (без CODESYS).

Состояние проектов записывает настоящий cds.py (tools/codesys/cdshost/cli.py этого репозитория,
копия в каталоге теста): тесты проверяют, что хук понимает его формат и останавливает коммит ровно
тогда, когда cds.py import отказался бы перезаписать проект, сохранённый в IDE.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

import check_exported as ce
from cdshost import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ci" / "check_exported.py"
EXPORT_HINT = "выполните python tools/codesys/cds.py export"


def base_env(root: Path) -> dict[str, str]:
    """Окружение без переменных CDS_* и без реестра: пути проектов — по умолчанию config.json."""
    home = str(root.parent / "home")
    return {"USERPROFILE": home, "HOME": home}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Репозиторий-потребитель: tools/codesys шаблона (cds.py и config.json), src/ и tests/."""
    root = tmp_path / "demo"
    codesys = root / "tools" / "codesys"
    shutil.copytree(
        REPO_ROOT / "tools" / "codesys" / "cdshost",
        codesys / "cdshost",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("stlayout.py", "config.json"):
        shutil.copy2(REPO_ROOT / "tools" / "codesys" / name, codesys / name)
    files = {
        "src/POUs/F_Demo/F_Demo.decl.st": "FUNCTION F_Demo : INT\n",
        "src/POUs/F_Demo/F_Demo.impl.st": "F_Demo := 1;\n",
        "tests/POUs/PRG_Tests/PRG_Tests.decl.st": "PROGRAM PRG_Tests\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root.resolve()


def settings_for(root: Path, env: dict[str, str] | None = None) -> cli.Settings:
    data = json.loads((REPO_ROOT / "tools" / "codesys" / "config.json").read_text(encoding="utf-8"))
    environ = base_env(root) | (env or {})
    return cli.Settings(data, root, cli.make_getenv(environ, registry=False))


def import_project(root: Path, tests: bool = False, env: dict[str, str] | None = None) -> Path:
    """Как после успешного cds.py import: файл проекта и запись cds.py в build/codesys/state.json."""
    settings = settings_for(root, env)
    project = settings.project_path(tests)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_bytes(b"project written by cds.py import")
    cli.record_project_state(settings, project, cli.layout_subdirs(tests))
    return project


def save_in_ide(project: Path) -> None:
    """Как «Сохранить» в IDE: другое содержимое и время изменения."""
    st = project.stat()
    project.write_bytes(project.read_bytes() + b" + edit in IDE")
    os.utime(project, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def check(
    root: Path, files: Sequence[str] = (), allow_unknown: bool = False, env: dict[str, str] | None = None
) -> ce.Report:
    return ce.run_checks(root, list(files), allow_unknown=allow_unknown, environ=base_env(root) | (env or {}))


# ---------------------------------------------------------------------------
# Без проектов и файлы коммита
# ---------------------------------------------------------------------------


def test_repo_without_build_passes(repo: Path) -> None:
    report = check(repo, ["src/POUs/F_Demo/F_Demo.impl.st", "README.md"])
    assert report.errors == [] and report.warnings == []


@pytest.mark.parametrize(
    "name",
    ["build/demo.project", "Build/Demo.PROJECT", "rel/demo.projectarchive", "lib/x.library", "x.compiled-library"],
)
def test_codesys_files_in_commit_block(repo: Path, name: str) -> None:
    report = check(repo, [name, "src/POUs/F_Demo/F_Demo.impl.st"])
    assert len(report.errors) == 1
    assert f'git rm --cached -- "{name}"' in report.errors[0]
    assert EXPORT_HINT in report.errors[0]


@pytest.mark.parametrize("name", ["docs/project.md", "src/POUs/FB_Library/FB_Library.decl.st", "a.project.txt"])
def test_other_files_in_commit_are_ignored(repo: Path, name: str) -> None:
    assert check(repo, [name]).errors == []


# ---------------------------------------------------------------------------
# Проект и запись cds.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tests", [False, True])
def test_project_unchanged_since_import_passes(repo: Path, tests: bool) -> None:
    import_project(repo, tests)
    report = check(repo)
    assert report.errors == [] and report.warnings == []


def test_project_saved_in_ide_blocks_with_export_hint(repo: Path) -> None:
    save_in_ide(import_project(repo))
    report = check(repo)
    assert len(report.errors) == 1
    text = report.errors[0]
    assert text.startswith("build/demo.project: проект сохранён в IDE")
    assert f"{EXPORT_HINT} и добавьте" in text
    assert "--tests" not in text
    assert "python tools/codesys/cds.py import --force" in text
    assert "--to" not in text  # исходники не менялись — экспорт не откажется


def test_tests_project_hint_uses_tests_flag(repo: Path) -> None:
    import_project(repo)
    save_in_ide(import_project(repo, tests=True))
    report = check(repo)
    assert len(report.errors) == 1
    text = report.errors[0]
    assert text.startswith("build/demo-tests.project:")
    assert f"{EXPORT_HINT} --tests" in text
    assert "import --tests --force" in text
    assert "src/ и tests/" in text


def test_size_change_alone_is_detected(repo: Path) -> None:
    project = import_project(repo)
    st = project.stat()
    project.write_bytes(b"other size")
    os.utime(project, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert len(check(repo).errors) == 1


def test_hook_agrees_with_cds_import_guard(repo: Path) -> None:
    """Хук блокирует ровно тогда, когда cds.py import отказался бы перезаписать проект."""
    settings = settings_for(repo)
    project = import_project(repo)
    cli.check_project_state(settings, project, force=False)
    assert check(repo).errors == []

    save_in_ide(project)
    with pytest.raises(cli.CdsError):
        cli.check_project_state(settings, project, force=False)
    assert len(check(repo).errors) == 1

    cli.record_project_state(settings, project, cli.layout_subdirs(False))  # как после cds.py export
    cli.check_project_state(settings, project, force=False)
    assert check(repo).errors == []


def test_sources_changed_too_suggests_export_to_directory(repo: Path) -> None:
    save_in_ide(import_project(repo))
    (repo / "src" / "POUs" / "F_Demo" / "F_Demo.impl.st").write_bytes(b"F_Demo := 2;\n")
    report = check(repo)
    assert len(report.errors) == 1
    assert "python tools/codesys/cds.py export --to build/export-check" in report.errors[0]
    assert "откажется" in report.errors[0]


@pytest.mark.parametrize("subdirs", [("src",), ("src", "tests"), ("missing",)])
def test_sources_digest_matches_cds(repo: Path, subdirs: tuple[str, ...]) -> None:
    assert ce.sources_digest(repo, subdirs) == cli.sources_digest(repo, subdirs)


def test_project_without_record_blocks_or_warns(repo: Path) -> None:
    project = repo / "build" / "demo.project"
    project.parent.mkdir(parents=True)
    project.write_bytes(b"made by hand or state.json removed")
    report = check(repo)
    assert len(report.errors) == 1
    assert "нет записи cds.py" in report.errors[0]
    assert "python tools/codesys/cds.py export --to build/export-check" in report.errors[0]
    relaxed = check(repo, allow_unknown=True)
    assert relaxed.errors == [] and len(relaxed.warnings) == 1


def test_corrupted_state_file(repo: Path) -> None:
    import_project(repo)
    (repo / ce.STATE_FILE).write_text("{", encoding="utf-8")
    report = check(repo)
    assert any("неверный JSON" in w for w in report.warnings)
    assert len(report.errors) == 1 and "нет записи cds.py" in report.errors[0]


@pytest.mark.parametrize("entry", [{"project": {"mtime_ns": "1", "size": 2}}, {"project": None}, ["x"], "x"])
def test_malformed_record_is_unknown(repo: Path, entry: object) -> None:
    project = import_project(repo)
    (repo / ce.STATE_FILE).write_text(json.dumps({str(project): entry}), encoding="utf-8")
    report = check(repo)
    assert len(report.errors) == 1 and "нет записи cds.py" in report.errors[0]


def test_project_open_in_ide_warns(repo: Path) -> None:
    project = import_project(repo)
    Path(str(project) + ".~u").write_text("user@host 1234", encoding="utf-8")
    report = check(repo)
    assert report.errors == []
    assert len(report.warnings) == 1 and "открыт в CODESYS" in report.warnings[0]


def test_record_of_deleted_project_is_ignored(repo: Path) -> None:
    import_project(repo).unlink()
    assert check(repo).errors == []


def test_records_from_another_clone_are_ignored(repo: Path, tmp_path: Path) -> None:
    """Копия клона вместе с build/: записи state.json указывают на проекты исходного клона."""
    save_in_ide(import_project(repo))
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy)
    report = check(copy.resolve())
    assert len(report.errors) == 1
    assert report.errors[0].startswith("build/demo.project: нет записи cds.py")
    assert str(repo) not in report.errors[0]


# ---------------------------------------------------------------------------
# Пути проектов по настройкам cds.py
# ---------------------------------------------------------------------------


def test_custom_project_paths_from_cds_settings(repo: Path) -> None:
    env = {"CDS_PROJECT": "out/main.project", "CDS_TEST_PROJECT": "build/unit.project"}
    main_project = import_project(repo, env=env)
    test_project = import_project(repo, tests=True, env=env)
    assert main_project == repo / "out" / "main.project"
    save_in_ide(main_project)
    save_in_ide(test_project)
    errors = check(repo, env=env).errors
    assert len(errors) == 2
    by_name = {text.split(":", 1)[0]: text for text in errors}
    assert f"{EXPORT_HINT} и" in by_name["out/main.project"]
    # имя без «-tests»: режим «тесты» известен только из настроек cds.py
    assert f"{EXPORT_HINT} --tests" in by_name["build/unit.project"]


def test_without_cds_tools_mode_is_guessed_from_file_name(tmp_path: Path) -> None:
    root = (tmp_path / "bare").resolve()
    (root / "src").mkdir(parents=True)
    project = import_project(root, tests=True)  # запись в формате cds.py, но tools/codesys нет
    save_in_ide(project)
    assert ce.resolve_projects(root) == {}
    errors = check(root).errors
    assert len(errors) == 1 and f"{EXPORT_HINT} --tests" in errors[0]


def test_broken_cds_tools_fall_back(repo: Path) -> None:
    cli_py = repo / "tools" / "codesys" / "cdshost" / "cli.py"
    cli_py.write_text("raise RuntimeError('другая версия шаблона')\n", encoding="utf-8")
    assert ce.resolve_projects(repo, base_env(repo)) == {}
    save_in_ide(import_project(repo))
    assert len(check(repo).errors) == 1


@pytest.mark.parametrize(
    "data",
    [{"mtime_ns": True, "size": 1}, {"mtime_ns": 1}, {"mtime_ns": 1.5, "size": 1}, None, []],
)
def test_fingerprint_parse_rejects_bad_values(data: object) -> None:
    assert ce.Fingerprint.parse(data) is None


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------


def test_main_exit_codes_and_output(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = import_project(repo)
    assert ce.main(["--root", str(repo)]) == ce.EXIT_OK
    assert capsys.readouterr().out == ""
    save_in_ide(project)
    assert ce.main(["--root", str(repo), "src/POUs/F_Demo/F_Demo.impl.st"]) == ce.EXIT_BLOCKED
    out = capsys.readouterr().out
    assert EXPORT_HINT in out
    assert f"{ce.HOOK_ID}: коммит остановлен, ошибок: 1" in out


def test_main_allow_unknown(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "build").mkdir()
    (repo / "build" / "demo.project").write_bytes(b"x")
    assert ce.main(["--root", str(repo), "--allow-unknown"]) == ce.EXIT_OK
    assert capsys.readouterr().out.startswith("предупреждение: build/demo.project: нет записи cds.py")


def test_bad_arguments_exit_2() -> None:
    with pytest.raises(SystemExit) as exc:
        ce.main(["--no-such-option"])
    assert exc.value.code == 2


def test_script_runs_standalone_with_utf8_output(repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "build/demo.project"],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "cp1251"},
    )
    assert proc.returncode == ce.EXIT_BLOCKED
    assert "файл CODESYS в коммите" in proc.stdout.decode("utf-8")


# ---------------------------------------------------------------------------
# Описание хука и примеры конфигурации
# ---------------------------------------------------------------------------


def _yaml_value(text: str, key: str) -> str:
    match = re.search(rf"^\s*{key}:\s*(.+?)\s*$", text, re.MULTILINE)
    assert match, f"нет ключа {key}"
    return match.group(1).strip("'\"")


def test_hook_manifest_matches_module_and_package() -> None:
    manifest = (REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    assert f"- id: {ce.HOOK_ID}" in manifest
    entry = _yaml_value(manifest, "entry")
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts[entry] == "check_exported:main"
    files = re.compile(_yaml_value(manifest, "files"))
    for suffix in ce.CODESYS_BINARY_SUFFIXES:
        assert files.search(f"build/demo{suffix}") and files.search(f"BUILD/DEMO{suffix.upper()}")
    for other in ("src/POUs/F_Demo/F_Demo.impl.st", "docs/project.md", "a.project.txt"):
        assert not files.search(other)


def test_pre_commit_configs_follow_ci_versions() -> None:
    ruff = re.search(r"^ruff==(\S+)$", (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8"), re.MULTILINE)
    assert ruff
    for rel in (".pre-commit-config.yaml", "examples/consumer/.pre-commit-config.yaml"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert re.search(rf"astral-sh/ruff-pre-commit\s+rev: v{re.escape(ruff.group(1))}\s", text), rel
        assert "tools/check/st_lint.py --strict" in text, rel
        assert f"id: {ce.HOOK_ID}" in text, rel
    consumer = (REPO_ROOT / "examples/consumer/.pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "repo: https://github.com/<GITHUB_USER>/plc-ci-toolkit" in consumer
