"""Тесты ci/summary.py: сводка, аннотации и выходы шагов для plc-static.yml и plc-codesys.yml.

Фикстуры ci/tests/fixtures/ — настоящие отчёты инструментов шаблона (st_lint, iec_checker v0.4,
RuSTy 1.0.6 по src/ и tests/ этого репозитория; cds.py test в CODESYS 3.5.17.30), пути обезличены.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import summary

FIXTURES = Path(__file__).resolve().parent / "fixtures"

STATIC_STEPS = (
    "deps",
    "ruff",
    "ruff_format",
    "mypy",
    "pytest",
    "assemble",
    "st_lint",
    "iec_tool",
    "iec_checker",
    "rusty_tool",
    "rusty",
)
CODESYS_STEPS = ("python_check", "runtime", "test", "junit", "publish", "sa_report")

PYTEST_XML = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="0" failures="1" skipped="2" tests="311" time="13.3"/></testsuites>\n'
)


def steps_ok(ids: tuple[str, ...], **overrides: str) -> dict[str, Any]:
    """Контекст steps, как его отдаёт toJSON(steps): все шаги пройдены, кроме overrides."""
    data: dict[str, Any] = {i: {"outcome": "success", "conclusion": "success", "outputs": {}} for i in ids}
    for step, value in overrides.items():
        data[step] = {"outcome": value, "conclusion": value, "outputs": {}}
    return data


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    """Корень репозитория с build/ после зелёного прогона plc-static."""
    build = tmp_path / "repo" / "build"
    build.mkdir(parents=True)
    for name in ("st_lint.json", "iec_checker.json", "rusty.json"):
        shutil.copyfile(FIXTURES / "static" / name, build / name)
    return tmp_path / "repo"


@pytest.fixture
def codesys_root(tmp_path: Path) -> Path:
    """Корень репозитория с build/ после зелёного прогона cds.py test."""
    build = tmp_path / "repo" / "build"
    build.mkdir(parents=True)
    for name in ("test-results.json", "build.json"):
        shutil.copyfile(FIXTURES / "codesys" / name, build / name)
    return tmp_path / "repo"


# ---------------------------------------------------------------------------
# Общие функции
# ---------------------------------------------------------------------------


def test_annotation_escaping() -> None:
    line = summary.annotation("error", "100% неверно\nвторая строка", "src/a,b.st", 3, 7, "st_lint STL008: x")
    assert line == "::error file=src/a%2Cb.st,line=3,col=7,title=st_lint STL008%3A x::100%25 неверно%0Aвторая строка"
    assert summary.annotation("warning", "текст") == "::warning::текст"
    # строка без файла не имеет смысла; столбец без строки — тоже
    assert summary.annotation("error", "m", None, 5, 2, "t") == "::error title=t::m"
    assert summary.annotation("error", "m", "f.st", 0, 2) == "::error file=f.st::m"


def test_md_cell_and_table() -> None:
    assert summary.md_cell("a|b\nc  d") == "a\\|b c d"
    assert summary.md_cell(None) == ""
    rows = [(str(i),) for i in range(5)]
    lines = summary.md_table(("N",), rows, limit=3)
    assert lines[:2] == ["| N |", "|---|"]
    assert lines[2:5] == ["| 0 |", "| 1 |", "| 2 |"]
    assert "первые 3 из 5" in lines[-1]


def test_repo_path_and_split_location() -> None:
    assert summary.repo_path("", "src/x.st") == "src/x.st"
    assert summary.repo_path(".", "src\\x.st") == "src/x.st"
    assert summary.repo_path("plc/", "src/x.st") == "plc/src/x.st"
    assert summary.split_location("src/POUs/F/F.impl.st:16") == ("src/POUs/F/F.impl.st", 16)
    assert summary.split_location("C:\\x\\y.st") == ("C:\\x\\y.st", None)
    assert summary.split_location(None) == (None, None)


def test_parse_steps() -> None:
    assert summary.parse_steps(None) == {}
    assert summary.parse_steps('{"a": {"outcome": "success"}}') == {"a": {"outcome": "success"}}
    with pytest.raises(ValueError):
        summary.parse_steps("[1, 2]")
    with pytest.raises(ValueError):
        summary.parse_steps("{not json")


def test_junit_counts(tmp_path: Path) -> None:
    path = tmp_path / "pytest.xml"
    path.write_text(PYTEST_XML, encoding="utf-8")
    notes: list[str] = []
    assert summary.junit_counts(path, notes) == {"tests": 311, "failures": 1, "errors": 0, "skipped": 2}
    assert summary.junit_counts(tmp_path / "missing.xml", notes) is None
    (tmp_path / "bad.xml").write_text("<testsuites", encoding="utf-8")
    assert summary.junit_counts(tmp_path / "bad.xml", notes) is None
    assert notes and "bad.xml" in notes[0]


def test_write_outputs(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    summary.write_outputs(str(path), {"result": "success", "text": "строка 1\nстрока 2"})
    assert path.read_text(encoding="utf-8") == ("result=success\ntext<<PLC_CI_EOF\nстрока 1\nстрока 2\nPLC_CI_EOF\n")


# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------


def test_static_green(static_root: Path) -> None:
    (static_root / "build" / "pytest.xml").write_text(PYTEST_XML.replace('failures="1"', 'failures="0"'), "utf-8")
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS), summary.StaticOptions())
    assert s.result == "success"
    assert s.outputs == {
        "st-lint-errors": "0",
        "st-lint-warnings": "0",
        "iec-checker-errors": "0",
        "iec-checker-allowed": "2",
        "rusty-errors": "0",
        "result": "success",
    }
    text = summary.render(s)
    assert "## Статические проверки ПЛК-кода" in text
    assert "| pytest | пройдено | тестов 311, упало 0, пропущено 2 |" in text
    assert "| st_lint --strict | пройдено | файлов 60, ошибок 0, предупреждений 0 |" in text
    assert "| iec_checker v0.4 | пройдено | необоснованных 0, обоснованных 2, артефактов сборки 2 |" in text
    assert "RuSTy plc --check (необязательно) | пройдено" in text
    assert "| `PLCOPEN-CP9` | 2 |" in text  # обоснованные находки сгруппированы по правилам
    assert "**Итог: пройдено.**" in text
    assert s.annotations == []


def test_static_lint_findings_fail_and_annotate(static_root: Path) -> None:
    lint = load("static/st_lint.json")
    lint["findings"] = [
        {
            "rule": "STL008",
            "path": "src/POUs/F_Clamp/F_Clamp.impl.st",
            "line": 12,
            "col": 5,
            "message": "«магическое число» 42",
            "severity": "error",
        },
        {
            "rule": "STL011",
            "path": "src/POUs/PRG_Main/PRG_Main.impl.st",
            "line": 3,
            "col": 1,
            "message": "время счётом циклов",
            "severity": "warning",
        },
    ]
    lint["summary"] = {"files": 60, "errors": 1, "warnings": 1}
    write_json(static_root / "build" / "st_lint.json", lint)
    s = summary.static_summary(
        static_root, steps_ok(STATIC_STEPS, st_lint="failure"), summary.StaticOptions(), prefix="plc"
    )
    assert s.result == "failure"
    assert s.outputs["st-lint-errors"] == "1"
    assert s.annotations == [
        "::error file=plc/src/POUs/F_Clamp/F_Clamp.impl.st,line=12,col=5,title=st_lint STL008::«магическое число» 42",
        "::warning file=plc/src/POUs/PRG_Main/PRG_Main.impl.st,line=3,col=1,title=st_lint STL011::время счётом циклов",
    ]
    text = summary.render(s)
    assert "| st_lint --strict | **ошибка** |" in text
    assert "### st_lint: замечания" in text
    assert "| `src/POUs/F_Clamp/F_Clamp.impl.st:12` | STL008 | ошибка | «магическое число» 42 |" in text
    assert "**Итог: не пройдено.**" in text


def test_static_iec_errors_annotated_with_source_lines(static_root: Path) -> None:
    iec = load("static/iec_checker.json")
    iec["findings"].insert(
        0,
        {
            "id": "PLCOPEN-L17",
            "msg": "Each IF instruction should have an ELSE clause",
            "file": "F_Clamp.st",
            "line": 30,
            "column": 4,
            "source": "src/POUs/F_Clamp/F_Clamp.impl.st",
            "source_line": 9,
            "status": "error",
            "reason": "",
        },
    )
    iec["summary"]["error"] = 1
    write_json(static_root / "build" / "iec_checker.json", iec)
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS, iec_checker="failure"), summary.StaticOptions())
    assert s.result == "failure"
    assert s.outputs["iec-checker-errors"] == "1"
    assert s.annotations == [
        "::error file=src/POUs/F_Clamp/F_Clamp.impl.st,line=9,col=4,title=iec_checker PLCOPEN-L17"
        "::Each IF instruction should have an ELSE clause"
    ]
    text = summary.render(s)
    assert "### iec_checker v0.4: необоснованные находки" in text
    assert "| `src/POUs/F_Clamp/F_Clamp.impl.st:9` | PLCOPEN-L17 |" in text


def test_static_non_blocking_checks_do_not_fail(static_root: Path) -> None:
    rusty = load("static/rusty.json")
    rusty["returncode"] = 1
    rusty["errors"] = 1
    rusty["diagnostics"] = [
        {
            "severity": "error",
            "code": "E048",
            "message": "Could not resolve reference to CfUnit",
            "file": "FB_Example_Test.st",
            "line": 14,
            "col": 9,
            "source": "tests/POUs/FB_Example_Test/FB_Example_Test.impl.st",
            "source_line": 2,
        },
        {"severity": "note", "code": "E000", "message": "примечание", "file": "x.st", "line": 1, "col": 1},
    ]
    write_json(static_root / "build" / "rusty.json", rusty)
    opts = summary.StaticOptions(iec_blocking=False)
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS, rusty="failure", iec_checker="failure"), opts)
    assert s.result == "success"
    assert s.outputs["rusty-errors"] == "1"
    # необязательная проверка: ошибка RuSTy — предупреждение, примечания не аннотируются
    assert s.annotations == [
        "::warning file=tests/POUs/FB_Example_Test/FB_Example_Test.impl.st,line=2,col=9,title=RuSTy E048"
        "::Could not resolve reference to CfUnit"
    ]
    text = summary.render(s)
    assert "| RuSTy plc --check (необязательно) | **ошибка** (не блокирует) |" in text
    assert "| iec_checker v0.4 | **ошибка** (не блокирует) |" in text
    assert "### RuSTy: диагностики" in text

    blocking = summary.StaticOptions(rusty_blocking=True)
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS, rusty="failure"), blocking)
    assert s.result == "failure"
    assert s.annotations[0].startswith("::error file=tests/POUs/FB_Example_Test/FB_Example_Test.impl.st")
    assert "| RuSTy plc --check | **ошибка** |" in summary.render(s)


def test_static_disabled_and_missing_checks(static_root: Path) -> None:
    opts = summary.StaticOptions(python_checks=False, iec_checker="off", rusty=False)
    s = summary.static_summary(static_root, steps_ok(("assemble", "st_lint")), opts)
    assert s.result == "success"
    text = summary.render(s)
    assert "| ruff check | отключено |" in text
    assert "| iec_checker | отключено |" in text
    assert "| RuSTy plc --check (необязательно) | отключено |" in text
    assert "iec-checker-errors" not in s.outputs
    # обязательная проверка, которая не запускалась (например, после сбоя загрузки), — это провал
    s = summary.static_summary(static_root, {"assemble": {"outcome": "success"}}, opts)
    assert s.result == "failure"
    assert "| st_lint --strict | не запускалось |" in summary.render(s)
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS, iec_checker="skipped"), summary.StaticOptions())
    assert s.result == "failure"


def test_static_corrupted_report_is_noted(static_root: Path) -> None:
    (static_root / "build" / "iec_checker.json").write_text("{oops", encoding="utf-8")
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS), summary.StaticOptions(iec_checker="docker"))
    text = summary.render(s)
    assert "`iec_checker.json` — не JSON" in text
    assert "| iec_checker (Docker) | пройдено |  |" in text


def test_static_annotations_are_capped(static_root: Path) -> None:
    lint = load("static/st_lint.json")
    lint["findings"] = [
        {"rule": "STL003", "path": "src/a.st", "line": i, "col": 1, "message": "длинная строка", "severity": "error"}
        for i in range(1, summary.MAX_ANNOTATIONS + 11)
    ]
    write_json(static_root / "build" / "st_lint.json", lint)
    s = summary.static_summary(static_root, steps_ok(STATIC_STEPS, st_lint="failure"), summary.StaticOptions())
    assert len(s.annotations) == summary.MAX_ANNOTATIONS
    assert any("аннотаций 60, показаны первые 50" in n for n in s.notes)
    assert "_Показаны первые 50 из 60" in summary.render(s)


# ---------------------------------------------------------------------------
# codesys
# ---------------------------------------------------------------------------


def test_codesys_green(codesys_root: Path) -> None:
    steps = steps_ok(CODESYS_STEPS)
    steps["python_check"]["outputs"] = {"version": "3.12.10"}
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions())
    assert s.result == "success"
    assert s.outputs == {
        "build-errors": "0",
        "build-warnings": "0",
        "tests": "18",
        "passed": "18",
        "failed": "0",
        "skipped": "0",
        "finished": "true",
        "result": "success",
    }
    text = summary.render(s)
    assert "| Раннер: Python 3.11+, tools/codesys | пройдено | Python 3.12.10 |" in text
    assert "| Static Analysis Light: cds.py build, sa_check, sa_report | пройдено |" in text
    assert "наборов 2, тестов 18, упало 0" in text
    assert "Итог компилятора: «Компиляция завершена -- 0 ошибок, 0 предупреждений»" in text
    assert "| 2 | 18 | 18 | 0 | 0 |" in text
    assert "| CODESYS | 3.5.17.30 (профиль `CODESYS V3.5 SP17 Patch 3`) |" in text
    assert "Упавшие тесты" not in text
    assert s.annotations == []


def test_codesys_failures_and_build_messages(codesys_root: Path) -> None:
    results = load("codesys/test-results.json")
    case = results["suites"][0]["cases"][1]
    case.update(status="FAIL", message="expected 0.0, got 5.0", failure_type="E_AssertionType.Type_REAL")
    results["suites"][0]["failed"] = 1
    results["totals"].update(failed=1, passed=17)
    results["ok"] = False
    write_json(codesys_root / "build" / "test-results.json", results)
    report = load("codesys/build.json")
    report["warnings"] = 1
    report["messages"] += [
        {
            "severity": "Warning",
            "code": "C0373",
            "text": "Неявное преобразование из REAL в INT",
            "object": "F_Clamp [Device: PLC Logic: Application]",
            "position": "Строка 16 (Реализ.)",
            "source": "src/POUs/F_Clamp/F_Clamp.impl.st:16",
        },
        {"severity": "Warning", "code": None, "text": "без привязки", "object": "Library Manager", "position": None},
    ]
    write_json(codesys_root / "build" / "build.json", report)
    steps = steps_ok(CODESYS_STEPS, test="failure")
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions(), prefix=".")
    assert s.result == "failure"
    assert s.outputs["failed"] == "1"
    assert s.outputs["build-warnings"] == "2"
    assert s.annotations == [
        "::warning file=src/POUs/F_Clamp/F_Clamp.impl.st,line=16,title=CODESYS C0373"
        "::Неявное преобразование из REAL в INT",
        "::warning title=CODESYS Warning::без привязки [Library Manager]",
    ]
    text = summary.render(s)
    assert "#### Упавшие тесты" in text
    assert (
        "| PRG_Tests.fbF_ClampTest | BelowLowReturnsLow | expected 0.0, got 5.0 | E_AssertionType.Type_REAL |" in text
    )
    assert "| `src/POUs/F_Clamp/F_Clamp.impl.st:16` | Warning | C0373 |" in text
    assert "| Результаты coUnit | **ошибка** |" in text


def test_codesys_build_error_without_results(codesys_root: Path) -> None:
    (codesys_root / "build" / "test-results.json").unlink()
    report = load("codesys/build.json")
    report.update(ok=False, errors=1)
    report["messages"].append(
        {
            "severity": "Error",
            "code": "C0046",
            "text": "Идентификатор 'rValueX' не задан",
            "object": "F_Clamp",
            "position": "Строка 16, Столбец 1 (Реализ.)",
            "source": "src/POUs/F_Clamp/F_Clamp.impl.st:16",
        }
    )
    write_json(codesys_root / "build" / "build.json", report)
    steps = steps_ok(CODESYS_STEPS, test="failure", junit="skipped", publish="skipped")
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions())
    assert s.result == "failure"
    assert s.outputs["build-errors"] == "1"
    assert "tests" not in s.outputs
    assert s.annotations == [
        "::error file=src/POUs/F_Clamp/F_Clamp.impl.st,line=16,title=CODESYS C0046::Идентификатор 'rValueX' не задан"
    ]
    text = summary.render(s)
    assert "сборка с ошибками — тесты не запускались" in text
    assert "Файла `build/test-results.json` нет" in text
    assert "| Результаты coUnit | не запускалось |" in text


def test_codesys_unfinished_run(codesys_root: Path) -> None:
    results = load("codesys/test-results.json")
    results.update(finished=False, ok=False, error="тесты не завершились за 120 с")
    write_json(codesys_root / "build" / "test-results.json", results)
    s = summary.codesys_summary(codesys_root, steps_ok(CODESYS_STEPS, test="failure"), summary.CodesysOptions())
    assert s.result == "failure"
    assert s.outputs["finished"] == "false"
    assert "**Прогон не завершён:** тесты не завершились за 120 с." in summary.render(s)


def test_codesys_runtime_restart_disabled(codesys_root: Path) -> None:
    steps = steps_ok(("python_check", "test", "junit", "publish", "sa_report"))
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions(restart_runtime=False))
    assert s.result == "success"
    assert "| Рантайм: перезапуск (новое окно демо-режима 2 ч) | отключено |" in summary.render(s)
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions())
    assert s.result == "failure"  # перезапуск включён, но шаг не выполнялся


def test_codesys_sa_policy_blocking_or_advisory(codesys_root: Path) -> None:
    steps = steps_ok(CODESYS_STEPS, sa_report="failure")
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions())
    assert s.result == "failure"  # sa-fail-on new|any: новые находки SA валят итог
    s = summary.codesys_summary(codesys_root, steps, summary.CodesysOptions(sa_blocking=False))
    assert s.result == "success"
    assert "sa_report (необязательно) | **ошибка** (не блокирует)" in summary.render(s)


def test_codesys_extra_markdown(codesys_root: Path) -> None:
    sa = codesys_root / "build" / "static_analysis.md"
    sa.write_text("# Статанализ\n\n```bash\n# не заголовок\n```\n\n## SA0033\n\nтекст\n", encoding="utf-8")
    big = codesys_root / "build" / "big.md"
    big.write_text("строка\n" * (summary.MAX_EXTRA_BYTES // 6), encoding="utf-8")
    opts = summary.CodesysOptions(extra_md=["build/static_analysis.md", "build/big.md", "build/none.md"])
    s = summary.codesys_summary(codesys_root, steps_ok(CODESYS_STEPS), opts)
    text = summary.render(s)
    assert "### Статанализ" in text
    assert "#### SA0033" in text
    assert "# не заголовок" in text and "### не заголовок" not in text
    assert "Раздел обрезан до 64 КБ" in text
    assert "нет файла `none.md` — раздел пропущен" in text
    assert s.result == "success"


def test_codesys_extra_markdown_top_heading_becomes_section(codesys_root: Path) -> None:
    # отчёт ci/sa_report.py начинается с `##`: в сводке он — раздел `###`, как «Сборка» и «Тесты»,
    # а не подраздел тестов
    sa = codesys_root / "build" / "sa_report.md"
    sa.write_text("## Static Analysis Light (CODESYS)\n\n### Находки\n\n###### глубже некуда\n", encoding="utf-8")
    opts = summary.CodesysOptions(extra_md=["build/sa_report.md"])
    text = summary.render(summary.codesys_summary(codesys_root, steps_ok(CODESYS_STEPS), opts))
    assert "\n### Static Analysis Light (CODESYS)\n" in text
    assert "\n#### Находки\n" in text
    assert "\n###### глубже некуда\n" in text


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------


def test_main_static_writes_summary_outputs_and_annotations(
    static_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lint = load("static/st_lint.json")
    lint["findings"] = [
        {"rule": "STL002", "path": "src/a.st", "line": 2, "col": 1, "message": "табуляция", "severity": "error"}
    ]
    write_json(static_root / "build" / "st_lint.json", lint)
    step_summary = tmp_path / "summary.md"
    step_summary.write_text("# раньше\n", encoding="utf-8")
    outputs = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv(summary.STEPS_ENV, json.dumps(steps_ok(STATIC_STEPS, st_lint="failure")))
    code = summary.main(
        ["static", "--root", str(static_root), "--annotations", "--iec-label", "iec_checker v0.4", "--rusty", "false"]
    )
    assert code == 0
    text = step_summary.read_text(encoding="utf-8")
    assert text.startswith("# раньше\n## Статические проверки ПЛК-кода")  # дописывается, а не перезаписывается
    assert "result=failure\n" in outputs.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "::error file=src/a.st,line=2,col=1,title=st_lint STL002::табуляция" in out
    assert "не пройдено: st_lint --strict" in out


def test_main_codesys_to_stdout(
    codesys_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    steps = json.dumps(steps_ok(CODESYS_STEPS))
    code = summary.main(["codesys", "--root", str(codesys_root), "--steps-json", steps, "--artifact", "my-reports"])
    assert code == 0
    out = capsys.readouterr().out
    assert "## CODESYS: сборка и тесты coUnit" in out
    assert "артефакт `my-reports`" in out
    assert "summary.py: CODESYS: сборка и тесты coUnit — пройдено" in out


def test_main_rejects_bad_steps_json(codesys_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert summary.main(["codesys", "--root", str(codesys_root), "--steps-json", "[]"]) == 2
    assert "контекст steps" in capsys.readouterr().err


def test_boolean_options_from_workflow_inputs(codesys_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert summary.parse_bool("true") is True
    assert summary.parse_bool(" False ") is False
    with pytest.raises(Exception, match="ожидалось true или false"):
        summary.parse_bool("")
    steps = json.dumps(steps_ok(("python_check", "test", "junit")))
    args = ["codesys", "--root", str(codesys_root), "--steps-json", steps, "--summary", "-", "--outputs", ""]
    args += ["--restart-runtime", "false", "--junit-report", "false", "--sa-report", "false"]
    assert summary.main(args) == 0
    out = capsys.readouterr().out
    assert "отключено" in out and "Публикация JUnit" not in out and "Static Analysis Light" not in out
    with pytest.raises(SystemExit):
        summary.main(["codesys", "--restart-runtime", "maybe"])


def test_fixture_results_are_unchanged_by_summary(codesys_root: Path) -> None:
    before = load("codesys/test-results.json")
    snapshot = copy.deepcopy(before)
    summary.codesys_summary(codesys_root, steps_ok(CODESYS_STEPS), summary.CodesysOptions())
    assert json.loads((codesys_root / "build" / "test-results.json").read_text(encoding="utf-8")) == snapshot
