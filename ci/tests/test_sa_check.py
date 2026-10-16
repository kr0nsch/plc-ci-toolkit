"""Тесты ci/sa_check.py и чистых функций ci/codesys/sa_light.py — без CODESYS.

Запуск CODESYS подменяется: run_codesys пишет вместо скрипта CODESYS настоящий результат зонда
(ci/tests/samples/sa_probe.json, снят ci/sa_check.py на CODESYS 3.5.17.30). Совместимость
sa_light.py с IronPython 2.7 проверяет проверка шаблона tools/tests/test_py27_compat.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import sa_check as S
import sa_report as R
from cdshost import cli

REPO = Path(__file__).resolve().parents[2]
SAMPLES = Path(__file__).resolve().parent / "samples"
SA_LIGHT = S.load_sa_light()
ALL_WARNING = dict.fromkeys(("SA0004", "SA0006", "SA0027", "SA0028", "SA0033", "SA0167"), "warning")


def sample_records(name: str) -> list[dict[str, Any]]:
    data = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    return [dict(m) for m in data["messages"]]


# ---------------------------------------------------------------------------
# sa_light.py: чистые функции (общие для IronPython 2.7 и Python 3)
# ---------------------------------------------------------------------------


def test_sa_light_imports_without_codesys() -> None:
    assert SA_LIGHT.C is None and SA_LIGHT.B is None  # нет CDS_JOB — модули шаблона не загружаются


def test_rule_number() -> None:
    assert [SA_LIGHT.rule_number(x) for x in ("SA0033", "sa33", "33", 167, " SA0004 ")] == [33, 33, 33, 167, 4]
    with pytest.raises(ValueError):
        SA_LIGHT.rule_number("C0032")


def test_parse_rules_full_set_in_order() -> None:
    assert SA_LIGHT.parse_rules({"SA0033": "Warning", "sa28": "error"}) == [
        (4, "off"),
        (6, "off"),
        (27, "off"),
        (28, "error"),
        (33, "warning"),
        (167, "off"),
    ]
    assert SA_LIGHT.parse_rules(None) == [(n, "off") for n in SA_LIGHT.LIGHT_RULES]


@pytest.mark.parametrize("spec", [{"SA0001": "error"}, {"SA0033": "loud"}, ["SA0033"], {"X": "off"}])
def test_parse_rules_errors(spec: Any) -> None:
    with pytest.raises(ValueError):
        SA_LIGHT.parse_rules(spec)


def test_light_rules_match_report_catalog() -> None:
    assert {f"SA{n:04d}" for n in SA_LIGHT.LIGHT_RULES} == set(R.RULES)
    assert SA_LIGHT.CATEGORY_SA_LIGHT == R.CATEGORY_SA_LIGHT


def test_summarize_probe_sample() -> None:
    s = SA_LIGHT.summarize(sample_records("sa_probe.json"))
    assert s["compile_errors"] == 0 and s["compile_warnings"] == 2  # C0373 при генерации кода
    assert s["sa_started"] and s["sa_reported"] == 13 and s["sa_findings"] == 13
    assert s["sa_by_code"] == {"SA0004": 2, "SA0006": 4, "SA0027": 1, "SA0028": 2, "SA0033": 3, "SA0167": 1}
    assert s["download_blocked"] is True
    assert "загрузка невозможна" in s["summary_line"]


def test_summarize_clean_and_compile_error() -> None:
    clean = SA_LIGHT.summarize(sample_records("sa_clean.json"))
    assert (clean["sa_findings"], clean["download_blocked"], clean["compile_errors"]) == (0, False, 0)
    assert "готово к загрузке" in clean["summary_line"]
    broken = SA_LIGHT.summarize(sample_records("sa_compile_error.json"))
    assert broken["compile_errors"] == 1 and broken["sa_findings"] == 13


def test_sa_code_number() -> None:
    assert SA_LIGHT.sa_code_number("SA0167") == 167
    assert SA_LIGHT.sa_code_number("SA0000") is None
    assert SA_LIGHT.sa_code_number("C0373") is None
    assert SA_LIGHT.sa_code_number(None) is None


def test_sa_light_is_py27_compatible() -> None:
    """Проверка шаблона (ast: без f-строк, аннотаций, модулей Python 3) — в отдельном процессе."""
    paths = ["tools/tests", "tools", "tools/codesys", "tools/check"]  # как pythonpath pytest для tools/tests
    code = (
        f"import sys; from pathlib import Path; sys.path[:0] = {paths!r}; "
        "from test_py27_compat import problems_of; "
        "print('\\n'.join(problems_of(Path('ci/codesys/sa_light.py'))))"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------------------
# sa_check.py: правила, свежесть проекта, привязка к файлам
# ---------------------------------------------------------------------------


def test_repository_rules_file() -> None:
    assert S.load_rules(S.DEFAULT_RULES, SA_LIGHT) == ALL_WARNING


def test_load_rules_variants(tmp_path: Path) -> None:
    plain = tmp_path / "plain.json"
    plain.write_text('{"_doc": "x", "SA0033": "error"}', encoding="utf-8")
    assert S.load_rules(plain, SA_LIGHT)["SA0033"] == "error"
    assert S.load_rules(plain, SA_LIGHT)["SA0004"] == "off"
    for text in ('{"rules": {"SA0033": "loud"}}', "{not json"):
        bad = tmp_path / "bad.json"
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(S.SaError) as err:
            S.load_rules(bad, SA_LIGHT)
        assert err.value.code == S.EXIT_USAGE
    with pytest.raises(S.SaError) as missing:
        S.load_rules(tmp_path / "none.json", SA_LIGHT)
    assert missing.value.code == S.EXIT_USAGE


def test_rules_path_precedence(tmp_path: Path) -> None:
    assert S.rules_path(tmp_path, None) == S.DEFAULT_RULES
    local = tmp_path / "ci" / "sa_rules.json"
    local.parent.mkdir()
    local.write_text("{}", encoding="utf-8")
    assert S.rules_path(tmp_path, None) == local
    assert S.rules_path(tmp_path, "x.json") == Path("x.json")


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(SAMPLES / "sa_probe_src", root)
    (root / "build").mkdir()
    (root / "build" / "plc-demo.project").write_bytes(b"project")
    return root


def record_state(root: Path, project: Path) -> None:
    stat = project.stat()
    state = {
        str(project): {
            "project": {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size},
            "sources": cli.sources_digest(root, ("src",)),
        }
    }
    path = root / S.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_freshness_warnings(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    project = root / "build" / "plc-demo.project"
    assert "нет отметки import" in S.freshness_warnings(cli, root, project, ("src",))[0]
    record_state(root, project)
    assert S.freshness_warnings(cli, root, project, ("src",)) == []
    gvl = root / "src" / "GVLs" / "GVL_SaProbe.gvl.st"
    gvl.write_text(gvl.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    project.write_bytes(b"project saved in IDE")
    warnings = S.freshness_warnings(cli, root, project, ("src",))
    assert len(warnings) == 2
    assert "изменились после import" in warnings[0] and "сохранён в IDE" in warnings[1]


def test_annotate_restores_sources(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    original = sample_records("sa_probe.json")
    stripped = [dict(m, source=None) for m in original]
    S.annotate(cli, root, stripped)
    assert [m["source"] for m in stripped] == [m["source"] for m in original]


def test_load_cli_needs_template(tmp_path: Path) -> None:
    with pytest.raises(S.SaError) as err:
        S.load_cli(tmp_path)
    assert err.value.code == S.EXIT_ENV
    assert S.load_cli(REPO) is cli


# ---------------------------------------------------------------------------
# sa_check.py: запуск с подменённым CODESYS
# ---------------------------------------------------------------------------


class FakeCodesys:
    """Подмена cdshost.cli: run_codesys пишет отчёт зонда, остальное — настоящие функции шаблона."""

    CdsError = cli.CdsError

    def __init__(self, root: Path, exit_code: int = 0, raise_code: int | None = None) -> None:
        self.root = root
        self.exit_code = exit_code
        self.raise_code = raise_code
        self.jobs: list[dict[str, Any]] = []
        self.sources_digest = cli.sources_digest
        self.layout_objects = cli.layout_objects
        self.source_location = cli.source_location

    def make_settings(self, repo_root: Path) -> Any:
        return SimpleNamespace(
            project_path=lambda tests: repo_root / "build" / f"plc-demo{'-tests' if tests else ''}.project"
        )

    @contextlib.contextmanager
    def repo_lock(self, settings: Any, purpose: str) -> Iterator[None]:
        yield

    def run_codesys(self, settings: Any, script: str, job: dict[str, Any], label: str) -> dict[str, Any]:
        self.jobs.append(job)
        if self.raise_code is not None:
            raise cli.CdsError("CODESYS не завершился", self.raise_code)
        step = job["steps"][0]
        data = json.loads((SAMPLES / "sa_probe.json").read_text(encoding="utf-8"))
        for msg in data["messages"]:
            msg["source"] = None  # как пишет скрипт CODESYS: source дописывает хост
        Path(step["report"]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = dict(data, action="sa", ok=self.exit_code == 0)
        return {"ok": self.exit_code == 0, "exit_code": self.exit_code, "steps": [result]}


def run_main(monkeypatch: pytest.MonkeyPatch, fake: FakeCodesys, *extra: str) -> int:
    monkeypatch.setattr(S, "load_cli", lambda root: fake)
    return S.main(["--root", str(fake.root), *extra])


def test_run_writes_annotated_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    record_state(root, root / "build" / "plc-demo.project")
    fake = FakeCodesys(root)
    assert run_main(monkeypatch, fake) == S.EXIT_OK
    job = fake.jobs[0]
    assert job["template_dir"] == str(root / "tools" / "codesys")
    step = job["steps"][0]
    assert step["action"] == "sa" and step["rules"] == ALL_WARNING
    assert step["project"] == str(root / "build" / "plc-demo.project")
    report = json.loads((root / "build" / "sa.json").read_text(encoding="utf-8"))
    sources = {m["source"] for m in report["messages"] if m.get("code") == "SA0033"}
    assert sources == {
        "src/GVLs/GVL_SaProbe.gvl.st:8",
        "src/POUs/FB_SaBad/FB_SaBad.decl.st:14",
        "src/POUs/FB_SaBad/Check.method.decl.st:4",
    }
    assert report["tests_project"] is False and report["rules_file"].endswith("sa_rules.json")
    out = capsys.readouterr().out
    assert "предупреждение" not in out  # проект свежий
    assert "находок 13" in out and "загрузка невозможна" in out
    assert "src/POUs/FB_SaBad/FB_SaBad.decl.st:16: Warning SA0027" in out
    assert "Error Есть сообщения" not in out  # служебное сообщение CODESYS не печатается
    # отчёт годится для sa_report.py
    assert R.main(["--root", str(root), "--baseline", str(tmp_path / "none.json")]) == R.EXIT_FAILED


def test_run_options_and_stale_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    (root / "build" / "plc-demo-tests.project").write_bytes(b"tests")
    rules = tmp_path / "rules.json"
    rules.write_text('{"rules": {"SA0167": "off", "SA0033": "error"}}', encoding="utf-8")
    fake = FakeCodesys(root)
    out_json = tmp_path / "out" / "sa-tests.json"
    out_json.parent.mkdir()
    code = run_main(monkeypatch, fake, "--tests", "--rules", str(rules), "--out", str(out_json))
    assert code == S.EXIT_OK
    step = fake.jobs[0]["steps"][0]
    assert step["project"].endswith("plc-demo-tests.project")
    assert step["rules"]["SA0033"] == "error" and step["rules"]["SA0167"] == "off"
    assert step["log"] == str(root / "build" / "sa.log")
    assert json.loads(out_json.read_text(encoding="utf-8"))["tests_project"] is True
    assert "нет отметки import" in capsys.readouterr().out


@pytest.mark.parametrize(("exit_code", "expected"), [(1, S.EXIT_FAILED), (3, S.EXIT_ENV)])
def test_run_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, expected: int) -> None:
    root = make_repo(tmp_path)
    assert run_main(monkeypatch, FakeCodesys(root, exit_code=exit_code)) == expected


def test_run_codesys_failure_and_missing_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path)
    assert run_main(monkeypatch, FakeCodesys(root, raise_code=4)) == 4  # таймаут CODESYS
    (root / "build" / "plc-demo.project").unlink()
    fake = FakeCodesys(root)
    assert run_main(monkeypatch, fake) == S.EXIT_ENV
    assert fake.jobs == []


def test_bad_rules_file_is_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path)
    rules = tmp_path / "rules.json"
    rules.write_text('{"rules": {"SA0001": "error"}}', encoding="utf-8")
    assert run_main(monkeypatch, FakeCodesys(root), "--rules", str(rules)) == S.EXIT_USAGE
