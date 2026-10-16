"""Тесты ci/sa_report.py: разбор сообщений CODESYS, находки SA, базовая линия, политика, отчёты.

Образцы ci/tests/samples/ сняты настоящим CODESYS 3.5.17.30 (пути обезличены):
- sa_probe.json, sa_probe.log — ci/sa_check.py по намеренно плохому ST (sa_probe_src/src/):
  13 находок всех шести правил Light;
- sa_clean.json — src/ шаблона: находок нет; sa_tests_project.json — тестовый проект шаблона
  (--tests): 11 × SA0167 (экземпляр fbDut в VAR методов-тестов, допущение C9);
- sa_compile_error.json — генерация кода с ошибкой компиляции C0032;
- build_rebuild.json, build_rebuild.log — cds.py build (app.rebuild() без генерации кода).
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import sa_report as R
from cdshost import cli

SAMPLES = Path(__file__).resolve().parent / "samples"

PROBE_BY_RULE = {"SA0004": 2, "SA0006": 4, "SA0027": 1, "SA0028": 2, "SA0033": 3, "SA0167": 1}


def sample(name: str) -> R.Analysis:
    messages, meta = R.load_input(SAMPLES / name)
    return R.analyse(messages, meta, name)


def probe_repo(tmp_path: Path) -> Path:
    """Репозиторий с исходниками зонда (для привязки сообщений журнала к файлам)."""
    root = tmp_path / "repo"
    shutil.copytree(SAMPLES / "sa_probe_src", root)
    return root


def finding(rule: str = "SA0033", message: str = "Неиспользуемый Переменная 'iX'", **kw: Any) -> R.Finding:
    defaults: dict[str, Any] = {"severity": "Warning", "object": "FB_A", "file": "src/POUs/FB_A/FB_A.decl.st"}
    defaults.update(kw)
    return R.Finding(rule=rule, message=message, **defaults)


def write_baseline(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "findings": entries}, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Настоящие образцы
# ---------------------------------------------------------------------------


def test_probe_json_all_light_rules() -> None:
    a = sample("sa_probe.json")
    assert a.complete
    assert a.sa_started and a.code_generation
    assert a.compile_errors == 0  # «Есть сообщения доп. проверок кода» — не ошибка компиляции
    assert a.sa_reported == 13
    assert Counter(f.rule for f in a.findings) == PROBE_BY_RULE
    assert {r["rule"] for r in a.rules} == set(R.RULES)
    assert a.project is not None and a.project.endswith("plc-demo.project")
    assert a.notes == []


def test_probe_json_locations_from_source_field() -> None:
    a = sample("sa_probe.json")
    where = {(f.rule, f.message): (f.file, f.line, f.object) for f in a.findings}
    assert where[("SA0033", "Неиспользуемый Переменная 'iUnused2'")] == (
        "src/POUs/FB_SaBad/FB_SaBad.decl.st",
        14,
        "FB_SaBad",
    )
    assert where[("SA0167", "Временный экземпляр ФБ: 'fbPulse'")] == (
        "src/POUs/FB_SaBad/Check.method.decl.st",
        3,
        "FB_SaBad.Check",
    )
    assert where[("SA0033", "Неиспользуемый Переменная 'iGlobalUnused'")] == (
        "src/GVLs/GVL_SaProbe.gvl.st",
        8,
        "GVL_SaProbe",
    )
    impl = [f for f in a.findings if f.rule == "SA0006"]
    assert {(f.file, f.line) for f in impl} == {
        ("src/POUs/PRG_Main/PRG_Main.impl.st", 32),
        ("src/POUs/PRG_Main/PRG_Main.impl.st", 33),
        ("src/POUs/PRG_SaTask2/PRG_SaTask2.impl.st", 1),
        ("src/POUs/PRG_SaTask2/PRG_SaTask2.impl.st", 2),
    }


def test_probe_pragmas_suppressed_findings() -> None:
    """{attribute 'analysis' := '-33'}, '-167' и 'no-analysis' в исходниках зонда — находок по ним нет."""
    texts = " ".join(f.message for f in sample("sa_probe.json").findings)
    assert "iUnusedLocal" not in texts  # подавлено атрибутом analysis -33
    assert "fbDelay" not in texts  # F_SaTemp: analysis -167
    assert "iSkippedUnused" not in texts  # FB_SaSkip: no-analysis
    src = (SAMPLES / "sa_probe_src/src/POUs/FB_SaBad/FB_SaBad.decl.st").read_text(encoding="utf-8")
    assert "{attribute 'analysis' := '-33'}" in src


def test_group_header_attached_only_where_informative() -> None:
    a = sample("sa_probe.json")
    overlap = [f for f in a.findings if f.rule == "SA0028"]
    assert [f.message for f in overlap] == ["wOverlapA AT %MW10", "bOverlapB AT %MB21"]
    assert {f.group for f in overlap} == {"Следующие переменные обращаются к одной и той же памяти:"}
    # заголовок «Множественная запись на выходе:» повторяет текст находки — не дублируется
    assert all(f.group is None for f in a.findings if f.rule == "SA0004")
    # заголовок SA0004 не переходит на следующие правила
    assert all(f.group is None for f in a.findings if f.rule == "SA0006")


def test_probe_log_equals_json_with_template_mapping(tmp_path: Path) -> None:
    root = probe_repo(tmp_path)
    messages, meta = R.load_input(SAMPLES / "sa_probe.log")
    assert meta["format"] == "log"
    assert all(not m["source"] for m in messages)
    assert R.locate(messages, root, cli) == 15  # 13 находок SA и 2 предупреждения C0373
    from_log = R.analyse(messages, meta, "sa_probe.log")
    from_json = sample("sa_probe.json")

    def keys(a: R.Analysis) -> list[tuple[Any, ...]]:
        return [(f.rule, f.severity, f.message, f.object, f.position, f.file, f.line, f.group) for f in a.findings]

    assert keys(from_log) == keys(from_json)
    assert from_log.complete and from_log.sa_reported == 13


def test_locate_without_template_keeps_objects(tmp_path: Path) -> None:
    messages, meta = R.load_input(SAMPLES / "sa_probe.log")
    assert R.template_cli(tmp_path) is None
    assert R.locate(messages, tmp_path, None) == 0
    a = R.analyse(messages, meta, "sa_probe.log")
    assert all(f.file is None and f.object for f in a.findings)
    assert a.findings[0].where == "PRG_Main, Строка 33, Столбец 1 (Реализ.)"


def test_clean_template_sources() -> None:
    a = sample("sa_clean.json")
    assert a.complete and a.findings == [] and a.sa_reported == 0
    cmp = R.compare(a.findings, [])
    assert R.verdict(a, cmp, "any", allow_incomplete=False).passed


def test_tests_project_sample() -> None:
    a = sample("sa_tests_project.json")
    assert a.complete
    assert Counter(f.rule for f in a.findings) == {"SA0167": 11}
    assert {f.message for f in a.findings} == {"Временный экземпляр ФБ: 'fbDut'"}
    assert all(f.file and f.file.startswith("tests/POUs/FB_Example_Test/") for f in a.findings)
    assert len({f.object for f in a.findings}) == 11  # по методу на находку


def test_rebuild_without_code_generation_is_incomplete() -> None:
    for name in ("build_rebuild.json", "build_rebuild.log"):
        a = sample(name)
        assert a.sa_started, name  # «Доп. проверки кода...» есть и после rebuild()
        assert not a.code_generation, name
        assert a.findings == [] and not a.complete
        assert any("ci/sa_check.py" in n for n in a.notes)
        cmp = R.compare(a.findings, [])
        assert not R.verdict(a, cmp, "new", allow_incomplete=False).passed
        assert R.verdict(a, cmp, "new", allow_incomplete=True).passed
        assert R.verdict(a, cmp, "never", allow_incomplete=False).passed


def test_compile_error_sample() -> None:
    a = sample("sa_compile_error.json")
    assert a.compile_errors == 1
    assert not a.complete
    assert len(a.findings) == 13  # в 3.5.17.30 SA Light выполнился и при ошибке C0032
    assert any("Ошибок компиляции: 1" in n for n in a.notes)


# ---------------------------------------------------------------------------
# Журнал: разбор строк
# ---------------------------------------------------------------------------


def test_parse_log_line_variants() -> None:
    ts = "2026-10-16 01:35:31 "
    text = "\n".join(
        [
            ts + "WARN  Warning     SA0033 Неиспользуемый Переменная 'aBuf[1]' [FB_A, Строка 4 (Объявл.)]",
            ts + "ERROR Error       SA0006 Concurrent write access to 'x' in Tasks A, B "
            "[PRG_A, Line 2, Column 1 (Impl)]",
            ts + "INFO  Text        Additional code checks ...",
            ts + "INFO  Text        Additional code checks complete -- 2 errors",
            ts + "INFO  Information Size of generated code: 100 bytes",
            ts + "ERROR Error       C0032 Cannot convert type 'DINT' to type 'INT' [FB_A, Line 5, Column 1 (Impl)]",
            ts + "INFO  сборка (rebuild) приложения проекта C:\\x.project",
            "просто текст",
        ]
    )
    msgs = R.parse_log(text)
    assert [m["code"] for m in msgs] == ["SA0033", "SA0006", None, None, None, "C0032"]
    assert msgs[0]["text"] == "Неиспользуемый Переменная 'aBuf[1]'"
    assert (msgs[0]["object"], msgs[0]["position"]) == ("FB_A", "Строка 4 (Объявл.)")
    assert (msgs[1]["object"], msgs[1]["position"]) == ("PRG_A", "Line 2, Column 1 (Impl)")
    a = R.analyse(msgs, {}, "x.log")
    assert a.sa_started and a.code_generation and a.sa_reported == 2
    assert [f.rule for f in a.findings] == ["SA0033", "SA0006"]
    assert a.compile_errors == 1


def test_sa_markers_are_not_compile_errors() -> None:
    texts = [
        "Есть сообщения доп. проверок кода. См. категорию 'Доп. проверки кода'",
        "Message(s) of additional code checks available. See category 'Additional code checks'",
        "Есть сообщения статического анализа. См. категорию 'Сообщения статического анализа'",
    ]
    msgs: list[dict[str, Any]] = [{"severity": "Error", "text": t} for t in texts]
    msgs += [{"severity": "Error", "prefix": "SA", "code": None, "text": "что-то от SA"}]
    msgs += [{"severity": "Error", "prefix": "C", "code": "C0032", "text": "Невозможно конвертировать тип"}]
    assert R.analyse(msgs, {}, "x").compile_errors == 1
    assert R.is_sa_marker({"prefix": "SA", "code": "SA0033"}, "x") is False


def test_message_with_brackets_but_no_object() -> None:
    msgs = R.parse_log("2026-10-16 01:35:31 INFO  Text        Размер массива [0..9]")
    assert msgs[0]["object"] is None and msgs[0]["text"] == "Размер массива [0..9]"


def test_no_sa_run_note() -> None:
    a = R.analyse([{"severity": "Text", "text": "Компиляция завершена -- 0 ошибок, 0 предупреждений"}], {}, "x")
    assert not a.sa_started and not a.complete
    assert "не выполнялся" in a.notes[0]


def test_reported_count_mismatch_note() -> None:
    msgs = [
        {"severity": "Text", "text": "Доп. проверки кода..."},
        {"severity": "Warning", "code": "SA0033", "text": "x", "object": "A"},
        {"severity": "Text", "text": "Доп. проверки кода завершены -- 3 ошибки (-ок)"},
    ]
    a = R.analyse(msgs, {"code_generation": True}, "x")
    assert any("сообщает находок 3, разобрано 1" in n for n in a.notes)


def test_sa_code_and_split_source() -> None:
    assert R.sa_code("SA0033") == "SA0033"
    assert R.sa_code("SA0000") is None
    assert R.sa_code("C0032") is None and R.sa_code(None) is None
    assert R.split_source("src/POUs/F/F.impl.st:16") == ("src/POUs/F/F.impl.st", 16)
    assert R.split_source("src/GVLs/G.gvl.st") == ("src/GVLs/G.gvl.st", None)
    assert R.split_source(None) == (None, None)


def test_load_input_errors(tmp_path: Path) -> None:
    with pytest.raises(R.ReportError) as missing:
        R.load_input(tmp_path / "nope.json")
    assert missing.value.code == R.EXIT_INPUT
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(R.ReportError):
        R.load_input(bad)
    other = tmp_path / "other.json"
    other.write_text('{"suites": []}', encoding="utf-8")
    with pytest.raises(R.ReportError) as no_messages:
        R.load_input(other)
    assert no_messages.value.code == R.EXIT_INPUT


# ---------------------------------------------------------------------------
# Базовая линия
# ---------------------------------------------------------------------------


def test_compare_counts_and_ignores_line_numbers() -> None:
    current = [finding(line=10), finding(line=20), finding(rule="SA0006", message="m", object="PRG_A")]
    base = [R.BaselineEntry("SA0033", "FB_A", None, "Неиспользуемый  Переменная 'iX'", 1, "резерв")]
    cmp = R.compare(current, base)
    assert [f.status for f in cmp.findings] == ["baseline", "new", "new"]
    assert cmp.findings[0].reason == "резерв"
    assert cmp.fixed == [] and cmp.baseline_size == 1


def test_compare_reports_fixed_entries() -> None:
    base = [
        R.BaselineEntry("SA0033", "FB_A", None, "Неиспользуемый Переменная 'iX'", 2, "r"),
        R.BaselineEntry("SA0167", "F_B", None, "Временный экземпляр ФБ: 'fb'", 1, ""),
    ]
    cmp = R.compare([finding()], base)
    assert [f.status for f in cmp.findings] == ["baseline"]
    assert [(e.rule, e.count) for e in cmp.fixed] == [("SA0033", 1), ("SA0167", 1)]


def test_key_falls_back_to_file_without_object() -> None:
    f = finding(object=None)
    assert f.key() == ("SA0033", "src/POUs/FB_A/FB_A.decl.st", "Неиспользуемый Переменная 'iX'")


def test_updated_baseline_groups_and_keeps_reasons() -> None:
    current = [finding(), finding(), finding(rule="SA0167", message="Временный экземпляр ФБ: 'fb'", object="F_B")]
    old = [R.BaselineEntry("SA0033", "FB_A", None, "Неиспользуемый Переменная 'iX'", 1, "резерв для HMI")]
    data = R.updated_baseline(current, old)
    entries = data["findings"]
    assert [(e["rule"], e["count"], e["reason"]) for e in entries] == [
        ("SA0033", 2, "резерв для HMI"),
        ("SA0167", 1, ""),
    ]
    reloaded = R.compare(current, [R.BaselineEntry(**e) for e in entries])
    assert all(f.status == "baseline" for f in reloaded.findings)


def test_load_baseline_validation(tmp_path: Path) -> None:
    assert R.load_baseline(None) == [] and R.load_baseline(tmp_path / "none.json") == []
    good = write_baseline(tmp_path / "b.json", [{"rule": "sa33", "object": "FB_A", "message": " a  b ", "count": 2}])
    entry = R.load_baseline(good)[0]
    assert (entry.rule, entry.message, entry.count, entry.reason) == ("SA0033", "a b", 2, "")
    for bad_entry in (
        {"rule": "SA0033"},
        {"rule": "X1", "message": "m"},
        {"rule": "SA0033", "message": "m", "count": 0},
    ):
        path = write_baseline(tmp_path / "bad.json", [bad_entry])
        with pytest.raises(R.ReportError) as err:
            R.load_baseline(path)
        assert err.value.code == R.EXIT_USAGE
    broken = tmp_path / "broken.json"
    broken.write_text("[", encoding="utf-8")
    with pytest.raises(R.ReportError):
        R.load_baseline(broken)


def test_tests_project_against_baseline_from_itself() -> None:
    a = sample("sa_tests_project.json")
    data = R.updated_baseline(a.findings, [])
    assert len(data["findings"]) == 11
    entries = [R.BaselineEntry(**e) for e in data["findings"]]
    cmp = R.compare(a.findings, entries)
    assert cmp.new == [] and cmp.fixed == []
    assert R.verdict(a, cmp, "new", allow_incomplete=False).passed
    assert not R.verdict(a, cmp, "any", allow_incomplete=False).passed
    # один тест исправили, другой метод добавил временный экземпляр
    changed = [*a.findings[1:], finding(rule="SA0167", message="Временный экземпляр ФБ: 'fbDut'", object="X.Y")]
    cmp2 = R.compare(changed, entries)
    assert len(cmp2.new) == 1 and len(cmp2.fixed) == 1


# ---------------------------------------------------------------------------
# Политика и отчёты
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "statuses", "passed"),
    [
        ("new", [], True),
        ("new", ["baseline"], True),
        ("new", ["new"], False),
        ("any", ["baseline"], False),
        ("any", [], True),
        ("never", ["new"], True),
    ],
)
def test_verdict_matrix(policy: str, statuses: list[str], passed: bool) -> None:
    a = sample("sa_clean.json")
    findings = [finding(status=s) for s in statuses]
    cmp = R.Comparison(findings, [], 0)
    assert R.verdict(a, cmp, policy, allow_incomplete=False).passed is passed


def test_markdown_and_json_from_probe() -> None:
    a = sample("sa_probe.json")
    base = [R.BaselineEntry("SA0028", "GVL_SaProbe", None, "wOverlapA AT %MW10", 1, "общая область | наладка")]
    cmp = R.compare(a.findings, base)
    v = R.verdict(a, cmp, "new", allow_incomplete=False)
    md = R.render_markdown(a, cmp, v, "new", "ci/sa_baseline.json", max_rows=200)
    assert "| SA0006 | Параллельный доступ | 4 | 4 | 0 |" in md
    assert "| SA0028 | Перекрывающиеся области памяти | 2 | 1 | 1 |" in md
    assert "`src/POUs/FB_SaBad/FB_SaBad.decl.st:14`" in md
    assert "базовая линия: общая область \\| наладка" in md
    assert "Следующие переменные обращаются к одной и той же памяти: bOverlapB AT %MB21" in md
    assert "**не пройдено**: новых находок 12" in md
    assert "`plc-demo.project`" in md
    data = R.render_json(a, cmp, v, "new", "ci/sa_baseline.json")
    assert data["totals"]["findings"] == 13 and data["totals"]["new"] == 12 and data["totals"]["baseline"] == 1
    assert data["totals"]["by_rule"]["SA0028"] == {"total": 2, "new": 1, "baseline": 1}
    assert data["analysis"]["complete"] is True and data["passed"] is False
    assert data["findings"][0]["name"] == R.rule_name(data["findings"][0]["rule"])
    json.dumps(data, ensure_ascii=False)


def test_markdown_row_limit_and_unknown_rule() -> None:
    a = sample("sa_clean.json")
    findings = [finding(line=i, message=f"m{i}") for i in range(5)] + [finding(rule="SA0140", message="x")]
    cmp = R.compare(findings, [])
    md = R.render_markdown(a, cmp, R.verdict(a, cmp, "new", False), "new", None, max_rows=3)
    assert "_Показано 3 из 6 находок" in md
    assert "### SA0140 — правило полной версии Static Analysis" in md


def test_markdown_without_findings_and_without_analysis() -> None:
    clean = sample("sa_clean.json")
    cmp = R.compare([], [])
    assert "Находок нет." in R.render_markdown(clean, cmp, R.verdict(clean, cmp, "new", False), "new", None, 10)
    none = R.analyse([], {}, "x")
    md = R.render_markdown(none, cmp, R.verdict(none, cmp, "new", False), "new", None, 10)
    assert "анализа не было" in md and "### Замечания" in md


def test_github_annotations() -> None:
    cmp = R.compare(
        [
            finding(file="src/A.decl.st", line=4, message="a, b: 50%"),
            finding(file=None, object="GVL_X", position="Строка 2"),
        ],
        [],
    )
    lines = R.github_annotations(cmp, "error", "lib")
    assert lines[0] == ("::error file=lib/src/A.decl.st,line=4,title=SA0033 Неиспользуемые переменные::a, b: 50%25")
    assert lines[1].endswith("::Неиспользуемый Переменная 'iX' [GVL_X, Строка 2]")
    assert "file=" not in lines[1]


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------


def cli_repo(tmp_path: Path, sample_name: str = "sa_probe.json") -> Path:
    root = tmp_path / "repo"
    (root / "build").mkdir(parents=True)
    shutil.copy(SAMPLES / sample_name, root / "build" / "sa.json")
    return root


def test_cli_fails_on_new_and_writes_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = cli_repo(tmp_path)
    summary = tmp_path / "summary.md"
    summary.write_text("# before\n", encoding="utf-8")
    code = R.main(["--root", str(root), "--summary", str(summary), "--annotations"])
    assert code == R.EXIT_FAILED
    out = capsys.readouterr().out
    assert out.count("::error file=src/") == 13
    assert "новых 13" in out
    report = json.loads((root / "build" / "sa_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and report["baseline"] is None
    md = (root / "build" / "sa_report.md").read_text(encoding="utf-8")
    assert md.startswith("## Static Analysis Light (CODESYS)")
    assert summary.read_text(encoding="utf-8").startswith("# before\n## Static Analysis Light")


def test_cli_update_baseline_then_pass(tmp_path: Path) -> None:
    root = cli_repo(tmp_path)
    assert R.main(["--root", str(root), "--update-baseline"]) == R.EXIT_OK
    baseline = root / "ci" / "sa_baseline.json"
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert sum(e["count"] for e in data["findings"]) == 13
    data["findings"][0]["reason"] = "обосновано в docs/STATIC_ANALYSIS.md"
    baseline.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert R.main(["--root", str(root)]) == R.EXIT_OK
    assert R.main(["--root", str(root), "--fail-on", "any"]) == R.EXIT_FAILED
    # повторное обновление сохраняет обоснование
    assert R.main(["--root", str(root), "--update-baseline"]) == R.EXIT_OK
    again = json.loads(baseline.read_text(encoding="utf-8"))
    assert again["findings"][0]["reason"] == "обосновано в docs/STATIC_ANALYSIS.md"


def test_cli_incomplete_input(tmp_path: Path) -> None:
    root = cli_repo(tmp_path, "build_rebuild.json")
    assert R.main(["--root", str(root)]) == R.EXIT_FAILED
    assert R.main(["--root", str(root), "--allow-incomplete"]) == R.EXIT_OK
    assert R.main(["--root", str(root), "--fail-on", "never"]) == R.EXIT_OK


def test_cli_explicit_input_and_outputs(tmp_path: Path) -> None:
    root = probe_repo(tmp_path)
    (root / "tools").mkdir()
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    code = R.main(
        [
            "--root",
            str(root),
            "--input",
            str(SAMPLES / "sa_probe.log"),
            "--json",
            str(out_json),
            "--md",
            str(out_md),
            "--fail-on",
            "never",
        ]
    )
    assert code == R.EXIT_OK
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["totals"]["findings"] == 13
    # без tools/codesys/cdshost в корне привязки к файлам нет — место по объекту
    assert all(f["file"] is None for f in report["findings"])


def test_cli_errors(tmp_path: Path) -> None:
    assert R.main(["--root", str(tmp_path)]) == R.EXIT_INPUT
    root = cli_repo(tmp_path)
    write_baseline(root / "ci" / "sa_baseline.json", [{"rule": "SA0033"}])
    assert R.main(["--root", str(root)]) == R.EXIT_USAGE
    assert R.main(["--root", str(root), "--max-rows", "0"]) == R.EXIT_USAGE
    with pytest.raises(SystemExit):
        R.main(["--fail-on", "sometimes"])


def test_repository_baseline_is_valid() -> None:
    path = Path(__file__).resolve().parents[1] / "sa_baseline.json"
    entries = R.load_baseline(path)
    assert all(e.reason.strip() for e in entries), "у каждой записи базовой линии нужно обоснование"
