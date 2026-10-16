#!/usr/bin/env python3
"""Сводка проверок ПЛК-репозитория для GitHub Actions: $GITHUB_STEP_SUMMARY, аннотации, выходы.

Запускается шагом «Сводка» переиспользуемых workflow (.github/workflows/plc-static.yml и
plc-codesys.yml) из копии plc-ci-toolkit, которую workflow выгружает рядом с репозиторием:

    python <toolkit>/ci/summary.py static  [опции]   # Linux: ruff, pytest, assemble, st_lint, iec_checker, RuSTy
    python <toolkit>/ci/summary.py codesys [опции]   # Windows: рантайм, cds.py test, JUnit, отчёт статанализа

Что читает:
- итоги шагов — JSON контекста `steps` (переменная окружения PLC_CI_STEPS или --steps-json);
- отчёты инструментов шаблона в <root>/build/: st_lint.json, pytest.xml, iec_checker.json,
  rusty.json (static); test-results.json, build.json (codesys) и Markdown из --extra-md.

Что пишет:
- Markdown-сводку — дописывает в файл --summary (по умолчанию $GITHUB_STEP_SUMMARY; `-` или нет
  переменной — stdout);
- аннотации (--annotations) — команды `::error file=...,line=...::` в stdout: замечания st_lint,
  необоснованные находки iec_checker, диагностики RuSTy, ошибки и предупреждения компилятора CODESYS;
- выходы шага — в файл $GITHUB_OUTPUT (--outputs): result (success|failure) и счётчики.

Итог `result` — failure, если хотя бы одна обязательная проверка не прошла, отменена или не
запускалась. Необязательные проверки (RuSTy по умолчанию) показываются, но на итог не влияют.
Код выхода 0, если сводка записана (итог проверок передаётся выходом result, а не кодом выхода);
2 — неверные аргументы. Только стандартная библиотека Python 3.11+.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STEPS_ENV = "PLC_CI_STEPS"
MAX_ROWS = 50
MAX_ANNOTATIONS = 50
MAX_EXTRA_BYTES = 64 * 1024
Row = Sequence[object]
ERROR_SEVERITIES = ("Error", "FatalError")
WARNING_SEVERITIES = ("Warning",)

OUTCOME_TEXT = {
    "success": "пройдено",
    "failure": "**ошибка**",
    "cancelled": "отменено",
    "skipped": "пропущено",
}
NOT_RUN = "не запускалось"


# ---------------------------------------------------------------------------
# Модель сводки
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """Строка таблицы итогов: шаг workflow (id), его название и подробности."""

    step: str
    label: str
    required: bool = True
    enabled: bool = True
    details: str = ""
    outcome: str | None = None

    def passed(self) -> bool:
        if not self.enabled or not self.required:
            return True
        return self.outcome == "success"

    def cell(self) -> str:
        if not self.enabled:
            return "отключено"
        text = OUTCOME_TEXT.get(self.outcome or "", NOT_RUN)
        if not self.required and self.outcome not in (None, "success", "skipped"):
            text += " (не блокирует)"
        return text


@dataclass
class Summary:
    title: str
    checks: list[Check] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def result(self) -> str:
        return "success" if all(c.passed() for c in self.checks) else "failure"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def read_json(path: Path, notes: list[str]) -> Any:
    """JSON-отчёт или None (нет файла); испорченный файл — замечание в сводке, а не падение."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        notes.append(f"не удалось прочитать `{path.name}`: {exc}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        notes.append(f"`{path.name}` — не JSON ({exc.msg}, строка {exc.lineno})")
        return None


def parse_steps(text: str | None) -> dict[str, Any]:
    """Контекст `steps` из toJSON(steps): {id: {"outcome": ..., "outputs": {...}}}."""
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("ожидался объект JSON контекста steps")
    return data


def outcome(steps: Mapping[str, Any], step: str) -> str | None:
    info = steps.get(step)
    if not isinstance(info, Mapping):
        return None
    value = info.get("outcome")
    return str(value) if value else None


def step_output(steps: Mapping[str, Any], step: str, name: str) -> str:
    info = steps.get(step)
    if not isinstance(info, Mapping):
        return ""
    outputs = info.get("outputs")
    if not isinstance(outputs, Mapping):
        return ""
    return str(outputs.get(name) or "")


def md_cell(value: object) -> str:
    """Текст для ячейки таблицы Markdown: без переводов строк и с экранированной '|'."""
    text = "" if value is None else str(value)
    return " ".join(text.replace("|", "\\|").split())


def md_table(headers: Sequence[str], rows: Sequence[Sequence[object]], limit: int = MAX_ROWS) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(md_cell(v) for v in row) + " |" for row in rows[:limit]]
    if len(rows) > limit:
        lines += ["", f"_Показаны первые {limit} из {len(rows)}; полный список — в артефакте._"]
    return lines


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(
    level: str,
    message: str,
    file: str | None = None,
    line: int | None = None,
    col: int | None = None,
    title: str | None = None,
) -> str:
    """Команда workflow `::error file=...,line=...,col=...,title=...::сообщение`."""
    props: list[str] = []
    if file:
        props.append(f"file={escape_property(file)}")
        if line and line > 0:
            props.append(f"line={line}")
            if col and col > 0:
                props.append(f"col={col}")
    if title:
        props.append(f"title={escape_property(title)}")
    head = f"::{level}" + (" " + ",".join(props) if props else "")
    return f"{head}::{escape_data(message)}"


def repo_path(prefix: str, path: str) -> str:
    """Путь для аннотации — от корня рабочего каталога GitHub (с учётом working-directory)."""
    rel = path.replace("\\", "/").lstrip("/")
    base = prefix.replace("\\", "/").strip("/")
    if base in ("", "."):
        return rel
    return f"{base}/{rel}"


def split_location(location: str | None) -> tuple[str | None, int | None]:
    """'src/POUs/F/F.impl.st:16' -> ('src/POUs/F/F.impl.st', 16)."""
    if not location:
        return None, None
    path, sep, line = location.rpartition(":")
    if sep and line.isdigit() and path:
        return path, int(line)
    return location, None


def as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return 0


def junit_counts(path: Path, notes: list[str]) -> dict[str, int] | None:
    """Итоги JUnit XML (pytest --junitxml): tests, failures, errors, skipped."""
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        return None
    except (ET.ParseError, OSError) as exc:
        notes.append(f"`{path.name}` — не разобран JUnit XML ({exc})")
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += as_int(suite.get(key, "0"))
    return totals


def capped(items: Iterable[str], limit: int, label: str, notes: list[str]) -> list[str]:
    result = list(items)
    if len(result) > limit:
        notes.append(f"{label}: аннотаций {len(result)}, показаны первые {limit}")
        result = result[:limit]
    return result


# ---------------------------------------------------------------------------
# static: Python-проверки и статанализ ST на Linux
# ---------------------------------------------------------------------------


@dataclass
class StaticOptions:
    python_checks: bool = True
    iec_checker: str = "binary"
    iec_blocking: bool = True
    iec_label: str = ""
    rusty: bool = True
    rusty_blocking: bool = False
    paths: str = "src tests"
    artifact: str = "plc-static-reports"


def static_summary(root: Path, steps: Mapping[str, Any], opts: StaticOptions, prefix: str = "") -> Summary:
    build = root / "build"
    s = Summary("Статические проверки ПЛК-кода")
    notes = s.notes

    py = opts.python_checks
    pytest_xml = junit_counts(build / "pytest.xml", notes) if py else None
    pytest_details = ""
    if pytest_xml is not None:
        failed = pytest_xml["failures"] + pytest_xml["errors"]
        pytest_details = f"тестов {pytest_xml['tests']}, упало {failed}, пропущено {pytest_xml['skipped']}"

    lint = read_json(build / "st_lint.json", notes)
    lint_findings: list[Mapping[str, Any]] = []
    lint_details = ""
    if isinstance(lint, Mapping):
        lint_findings = [f for f in lint.get("findings", []) if isinstance(f, Mapping)]
        summ = lint.get("summary") or {}
        lint_details = (
            f"файлов {as_int(summ.get('files'))}, ошибок {as_int(summ.get('errors'))}, "
            f"предупреждений {as_int(summ.get('warnings'))}"
        )
        s.outputs["st-lint-errors"] = str(as_int(summ.get("errors")))
        s.outputs["st-lint-warnings"] = str(as_int(summ.get("warnings")))

    iec_enabled = opts.iec_checker != "off"
    iec = read_json(build / "iec_checker.json", notes) if iec_enabled else None
    iec_findings: list[Mapping[str, Any]] = []
    iec_details = ""
    if isinstance(iec, Mapping):
        iec_findings = [f for f in iec.get("findings", []) if isinstance(f, Mapping)]
        summ = iec.get("summary") or {}
        iec_details = (
            f"необоснованных {as_int(summ.get('error'))}, обоснованных {as_int(summ.get('allowed'))}, "
            f"артефактов сборки {as_int(summ.get('artifact'))}"
        )
        s.outputs["iec-checker-errors"] = str(as_int(summ.get("error")))
        s.outputs["iec-checker-allowed"] = str(as_int(summ.get("allowed")))

    rusty = read_json(build / "rusty.json", notes) if opts.rusty else None
    rusty_diags: list[Mapping[str, Any]] = []
    rusty_details = ""
    if isinstance(rusty, Mapping):
        rusty_diags = [d for d in rusty.get("diagnostics", []) if isinstance(d, Mapping)]
        rusty_details = (
            f"файлов {len(rusty.get('files') or [])}, исключено {len(rusty.get('excluded') or {})}, "
            f"ошибок {as_int(rusty.get('errors'))}, предупреждений {as_int(rusty.get('warnings'))}"
        )
        s.outputs["rusty-errors"] = str(as_int(rusty.get("errors")))

    iec_label = opts.iec_label or {"binary": "iec_checker v0.4", "docker": "iec_checker (Docker)"}.get(
        opts.iec_checker, "iec_checker"
    )
    s.checks = [
        Check("deps", "Python: зависимости (requirements)", enabled=py),
        Check("ruff", "ruff check", enabled=py),
        Check("ruff_format", "ruff format --check", enabled=py),
        Check("mypy", "mypy", enabled=py),
        Check("pytest", "pytest", enabled=py, details=pytest_details),
        Check("assemble", "assemble: склейка ST", details=f"каталоги: `{opts.paths}`"),
        Check("st_lint", "st_lint --strict", details=lint_details),
    ]
    if iec_enabled:
        s.checks.append(Check("iec_tool", f"{iec_label}: загрузка", required=opts.iec_blocking))
    s.checks.append(
        Check("iec_checker", iec_label, required=opts.iec_blocking, enabled=iec_enabled, details=iec_details)
    )
    if opts.rusty:
        s.checks.append(Check("rusty_tool", "RuSTy: загрузка", required=opts.rusty_blocking))
    s.checks.append(
        Check(
            "rusty",
            "RuSTy plc --check" + ("" if opts.rusty_blocking else " (необязательно)"),
            required=opts.rusty_blocking,
            enabled=opts.rusty,
            details=rusty_details,
        )
    )
    for check in s.checks:
        check.outcome = outcome(steps, check.step)

    # --- разделы с находками ---
    rows: list[Row]
    if lint_findings:
        rows = [
            (
                f"`{f.get('path')}:{as_int(f.get('line'))}`",
                f.get("rule"),
                "ошибка" if f.get("severity") == "error" else "предупреждение",
                f.get("message"),
            )
            for f in lint_findings
        ]
        s.sections += ["### st_lint: замечания", "", *md_table(("Место", "Правило", "Уровень", "Сообщение"), rows), ""]
        s.annotations += capped(
            (
                annotation(
                    "error" if f.get("severity") == "error" else "warning",
                    str(f.get("message") or ""),
                    repo_path(prefix, str(f.get("path") or "")),
                    as_int(f.get("line")),
                    as_int(f.get("col")),
                    f"st_lint {f.get('rule')}",
                )
                for f in lint_findings
            ),
            MAX_ANNOTATIONS,
            "st_lint",
            notes,
        )

    errors = [f for f in iec_findings if f.get("status") == "error"]
    if errors:
        rows = [(f"`{_iec_location(f)}`", f.get("id"), f.get("msg")) for f in errors]
        s.sections += [
            f"### {iec_label}: необоснованные находки",
            "",
            *md_table(("Место", "Правило", "Сообщение"), rows),
        ]
        s.sections += [
            "",
            "Исправьте код или добавьте обоснование в `tools/check/iec_checker_allow.toml` "
            "(разбор — `docs/STATIC_ANALYSIS.md`).",
            "",
        ]
        level = "error" if opts.iec_blocking else "warning"
        s.annotations += capped(
            (
                annotation(
                    level,
                    str(f.get("msg") or ""),
                    repo_path(prefix, str(f.get("source"))) if f.get("source") else None,
                    as_int(f.get("source_line")),
                    as_int(f.get("column")),
                    f"iec_checker {f.get('id')}",
                )
                for f in errors
            ),
            MAX_ANNOTATIONS,
            "iec_checker",
            notes,
        )
    allowed = Counter(str(f.get("id")) for f in iec_findings if f.get("status") == "allowed")
    if allowed:
        rows = [(f"`{rule}`", count) for rule, count in sorted(allowed.items())]
        s.sections += [
            f"### {iec_label}: обоснованные находки по правилам",
            "",
            *md_table(("Правило", "Находок"), rows),
            "",
            "Обоснования — `tools/check/iec_checker_allow.toml`; подробный отчёт — `build/iec_checker.md` в артефакте.",
            "",
        ]
    if isinstance(iec, Mapping) and iec.get("unused_allow"):
        notes.append(
            f"{iec_label}: неиспользованных обоснований {len(iec['unused_allow'])} (см. `build/iec_checker.md`)"
        )

    if rusty_diags:
        rows = [
            (f"`{_rusty_location(d)}`", f"{d.get('severity')}[{d.get('code')}]", d.get("message")) for d in rusty_diags
        ]
        s.sections += ["### RuSTy: диагностики", "", *md_table(("Место", "Код", "Сообщение"), rows), ""]
        s.annotations += capped(
            (
                annotation(
                    "error" if d.get("severity") == "error" and opts.rusty_blocking else "warning",
                    str(d.get("message") or ""),
                    repo_path(prefix, str(d.get("source"))) if d.get("source") else None,
                    as_int(d.get("source_line")),
                    as_int(d.get("col")),
                    f"RuSTy {d.get('code')}",
                )
                for d in rusty_diags
                if d.get("severity") in ("error", "warning")
            ),
            MAX_ANNOTATIONS,
            "RuSTy",
            notes,
        )

    s.sections.append(f"Отчёты целиком — артефакт `{opts.artifact}` (каталог `build/`).")
    s.outputs["result"] = s.result
    return s


def _iec_location(finding: Mapping[str, Any]) -> str:
    if finding.get("source"):
        return f"{finding.get('source')}:{as_int(finding.get('source_line'))}"
    return f"{finding.get('file')}:{as_int(finding.get('line'))}"


def _rusty_location(diag: Mapping[str, Any]) -> str:
    if diag.get("source"):
        return f"{diag.get('source')}:{as_int(diag.get('source_line'))}"
    return f"{diag.get('file')}:{as_int(diag.get('line'))}"


# ---------------------------------------------------------------------------
# codesys: сборка и тесты coUnit на Windows с CODESYS
# ---------------------------------------------------------------------------


@dataclass
class CodesysOptions:
    restart_runtime: bool = True
    sa_report: bool = True
    sa_blocking: bool = True
    junit_report: bool = True
    extra_md: list[str] = field(default_factory=list)
    artifact: str = "plc-codesys-reports"


def codesys_summary(root: Path, steps: Mapping[str, Any], opts: CodesysOptions, prefix: str = "") -> Summary:
    build = root / "build"
    s = Summary("CODESYS: сборка и тесты coUnit")
    notes = s.notes
    results = read_json(build / "test-results.json", notes)
    report = read_json(build / "build.json", notes)
    if not isinstance(results, Mapping):
        results = None
    if not isinstance(report, Mapping):
        report = None

    totals: Mapping[str, Any] = (results or {}).get("totals") or {}
    test_details = ""
    if results is not None:
        test_details = (
            f"наборов {as_int(totals.get('suites'))}, тестов {as_int(totals.get('tests'))}, "
            f"упало {as_int(totals.get('failed'))}"
        )
    elif report is not None and not report.get("ok", True):
        test_details = "сборка с ошибками — тесты не запускались"
    python_version = step_output(steps, "python_check", "version")
    # порядок строк — порядок шагов plc-codesys.yml
    s.checks = [
        Check(
            "python_check",
            "Раннер: Python 3.11+, tools/codesys",
            details=f"Python {python_version}" if python_version else "",
        ),
    ]
    if opts.sa_report:
        label = "Static Analysis Light: cds.py build, sa_check, sa_report"
        s.checks.append(
            Check("sa_report", label + ("" if opts.sa_blocking else " (необязательно)"), required=opts.sa_blocking)
        )
    s.checks += [
        Check(
            "runtime",
            "Рантайм: перезапуск (новое окно демо-режима 2 ч)",
            enabled=opts.restart_runtime,
        ),
        Check("test", "cds.py test: импорт, сборка, загрузка, прогон coUnit", details=test_details),
        Check("junit", "cds.py junit: JUnit XML", required=False, details="`build/junit.xml`"),
    ]
    if opts.junit_report:
        s.checks.append(Check("publish", "Публикация JUnit (аннотации и сводка)", required=False))
    for check in s.checks:
        check.outcome = outcome(steps, check.step)

    # --- сборка ---
    rows: list[Row]
    if report is not None:
        messages = [m for m in report.get("messages", []) if isinstance(m, Mapping)]
        errors = [m for m in messages if m.get("severity") in ERROR_SEVERITIES]
        warnings = [m for m in messages if m.get("severity") in WARNING_SEVERITIES]
        n_errors = max(as_int(report.get("errors")), len(errors))
        n_warnings = max(as_int(report.get("warnings")), len(warnings))
        s.outputs["build-errors"] = str(n_errors)
        s.outputs["build-warnings"] = str(n_warnings)
        line = f"Ошибок **{n_errors}**, предупреждений **{n_warnings}**."
        if report.get("summary_line"):
            line += f" Итог компилятора: «{md_cell(report['summary_line'])}»."
        s.sections += ["### Сборка", "", line, ""]
        if errors or warnings:
            rows = [
                (
                    f"`{m.get('source')}`" if m.get("source") else md_cell(m.get("object") or "—"),
                    m.get("severity"),
                    m.get("code") or "",
                    m.get("text"),
                )
                for m in errors + warnings
            ]
            s.sections += [*md_table(("Место", "Важность", "Код", "Сообщение"), rows), ""]
        s.annotations += capped(
            (_build_annotation(m, prefix) for m in errors + warnings),
            MAX_ANNOTATIONS,
            "CODESYS",
            notes,
        )

    # --- тесты ---
    if results is not None:
        s.outputs["tests"] = str(as_int(totals.get("tests")))
        s.outputs["passed"] = str(as_int(totals.get("passed")))
        s.outputs["failed"] = str(as_int(totals.get("failed")))
        s.outputs["skipped"] = str(as_int(totals.get("skipped")))
        s.outputs["finished"] = "true" if results.get("finished") else "false"
        meta = [
            ("CODESYS", f"{results.get('codesys_version') or '?'} (профиль `{results.get('profile') or '?'}`)"),
            ("Устройство в проекте", results.get("device")),
            ("Рантайм", results.get("runtime")),
            ("Фреймворк тестов", results.get("framework")),
            ("Коммит", results.get("commit")),
        ]
        s.sections += ["### Тесты coUnit", "", *md_table(("Параметр", "Значение"), meta), ""]
        rows = [
            (
                as_int(totals.get("suites")),
                as_int(totals.get("tests")),
                as_int(totals.get("passed")),
                as_int(totals.get("failed")),
                as_int(totals.get("skipped")),
            )
        ]
        s.sections += [*md_table(("Наборов", "Тестов", "Пройдено", "Упало", "Пропущено"), rows), ""]
        if results.get("finished"):
            wait = results.get("wait_s")
            if wait is not None:
                s.sections += [f"Все наборы завершились за {md_cell(wait)} с после старта приложения.", ""]
        else:
            reason = results.get("error") or "AllTestSuitesFinished не стал TRUE за отведённое время"
            s.sections += [f"**Прогон не завершён:** {md_cell(reason)}.", ""]
        if as_int(totals.get("tests")) == 0 and results.get("finished"):
            s.sections += ["**coUnit не нашёл ни одного теста.**", ""]
        failures = [
            (suite.get("name"), case.get("name"), case.get("message"), case.get("failure_type"))
            for suite in results.get("suites", [])
            if isinstance(suite, Mapping)
            for case in suite.get("cases", [])
            if isinstance(case, Mapping) and case.get("status") == "FAIL"
        ]
        if failures:
            s.sections += [
                "#### Упавшие тесты",
                "",
                *md_table(("Набор", "Тест", "Сообщение coUnit", "Тип проверки"), failures),
                "",
            ]
    else:
        s.sections += [
            "### Тесты coUnit",
            "",
            "Файла `build/test-results.json` нет: прогон не дошёл до тестов (см. `build/test.log`, `build/build.log`).",
            "",
        ]

    for extra in opts.extra_md:
        s.sections += _extra_section(root / extra, notes)

    s.sections.append(
        f"Журналы и отчёты — артефакт `{opts.artifact}` (`build/*.log`, `build/*.json`, `build/junit.xml`)."
    )
    if results is None or not results.get("ok"):
        # результатов нет или coUnit сообщил об ошибке: итог — failure, даже если шаг «test» не запускался
        s.checks.append(Check("results", "Результаты coUnit", outcome="failure" if results else None))
    s.outputs["result"] = s.result
    return s


def _build_annotation(msg: Mapping[str, Any], prefix: str) -> str:
    path, line = split_location(msg.get("source"))
    level = "error" if msg.get("severity") in ERROR_SEVERITIES else "warning"
    text = str(msg.get("text") or "")
    if not path and msg.get("object"):
        text += f" [{msg.get('object')}{', ' + str(msg.get('position')) if msg.get('position') else ''}]"
    code = msg.get("code") or msg.get("severity")
    return annotation(level, text, repo_path(prefix, path) if path else None, line, None, f"CODESYS {code}")


def _extra_section(path: Path, notes: list[str]) -> list[str]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        notes.append(f"нет файла `{path.name}` — раздел пропущен")
        return []
    except OSError as exc:
        notes.append(f"не удалось прочитать `{path.name}`: {exc}")
        return []
    truncated = len(data) > MAX_EXTRA_BYTES
    text = data[:MAX_EXTRA_BYTES].decode("utf-8", "replace").rstrip()
    if truncated:
        text = text[: text.rfind("\n")] if "\n" in text else text
        text += f"\n\n_Раздел обрезан до {MAX_EXTRA_BYTES // 1024} КБ; полный файл `{path.name}` — в артефакте._"
    return [_demote_headings(text), ""]


def _heading_level(line: str) -> int:
    """Уровень заголовка Markdown (`## текст` -> 2) или 0, если строка не заголовок."""
    hashes = len(line) - len(line.lstrip("#"))
    return hashes if 0 < hashes <= 6 and line[hashes : hashes + 1] == " " else 0


def _demote_headings(text: str) -> str:
    """Заголовки вложенного Markdown сдвигаются так, что старший становится `###` — разделом
    сводки наравне со «Сборкой» и «Тестами» (# -> ###, ## -> ####; у отчёта с `##` вверху —
    ## -> ###); глубже `######` не уходят. Блоки кода ``` не трогаются."""
    lines = text.splitlines()
    levels: list[int] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            levels.append(0)
        else:
            levels.append(0 if in_fence else _heading_level(line))
    top = min((level for level in levels if level), default=3)
    shift = max(3 - top, 0)
    out = [
        "#" * min(level + shift, 6) + line[level:] if level else line for line, level in zip(lines, levels, strict=True)
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------


def render(summary: Summary) -> str:
    verdict = "пройдено" if summary.result == "success" else "не пройдено"
    rows = [(c.label, c.cell(), c.details) for c in summary.checks]
    lines = [f"## {summary.title}", "", *md_table(("Проверка", "Итог", "Подробности"), rows, limit=len(rows))]
    lines += ["", f"**Итог: {verdict}.** Необязательные проверки на итог не влияют.", ""]
    if summary.notes:
        lines += ["Замечания сводки:", "", *[f"- {md_cell(n)}" for n in summary.notes], ""]
    lines += summary.sections
    return "\n".join(lines).rstrip("\n") + "\n"


def write_outputs(path: str, outputs: Mapping[str, str]) -> None:
    """Выходы шага в формате $GITHUB_OUTPUT (многострочные — через разделитель)."""
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for key, value in outputs.items():
            if "\n" in value:
                delimiter = "PLC_CI_EOF"
                while delimiter in value:
                    delimiter += "_"
                fh.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                fh.write(f"{key}={value}\n")


def parse_bool(text: str) -> bool:
    """Значение входа workflow типа boolean: в выражениях GitHub оно становится строкой true/false."""
    value = text.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"ожидалось true или false, получено {text!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="summary.py", description="Сводка проверок ПЛК-репозитория для GitHub Actions."
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    def flag(p: argparse.ArgumentParser, name: str, default: bool, text: str) -> None:
        p.add_argument(name, type=parse_bool, default=default, metavar="true|false", help=text)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", default=".", help="корень ПЛК-репозитория (каталог с build/)")
        p.add_argument("--path-prefix", default="", help="путь корня от $GITHUB_WORKSPACE (для аннотаций)")
        p.add_argument("--steps-json", help=f"JSON контекста steps (по умолчанию — переменная {STEPS_ENV})")
        p.add_argument("--summary", help="файл сводки (по умолчанию $GITHUB_STEP_SUMMARY, иначе stdout)")
        p.add_argument("--outputs", help="файл выходов шага (по умолчанию $GITHUB_OUTPUT)")
        p.add_argument("--annotations", action="store_true", help="вывести аннотации ::error/::warning")
        p.add_argument("--artifact", help="имя артефакта с отчётами (для текста сводки)")

    p = sub.add_parser("static", help="Python-проверки и статанализ ST (plc-static.yml)")
    common(p)
    flag(p, "--python-checks", True, "были ли включены ruff, mypy и pytest")
    p.add_argument("--iec-checker", choices=("binary", "docker", "off"), default="binary")
    flag(p, "--iec-blocking", True, "необоснованные находки iec_checker валят итог")
    p.add_argument("--iec-label", default="", help="название iec_checker в сводке (версия или образ)")
    flag(p, "--rusty", True, "была ли включена проверка RuSTy")
    flag(p, "--rusty-blocking", False, "ошибки RuSTy валят итог")
    p.add_argument("--paths", default="src tests", help="каталоги раскладки (для текста сводки)")

    p = sub.add_parser("codesys", help="сборка и тесты coUnit в CODESYS (plc-codesys.yml)")
    common(p)
    flag(p, "--restart-runtime", True, "был ли включён перезапуск рантайма")
    flag(p, "--sa-report", True, "был ли включён отчёт статанализа")
    flag(p, "--sa-blocking", True, "нарушение политики статанализа валит итог (sa-fail-on не never)")
    flag(p, "--junit-report", True, "была ли включена публикация JUnit")
    p.add_argument("--extra-md", action="append", default=[], metavar="FILE", help="Markdown-отчёт от --root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        steps = parse_steps(args.steps_json if args.steps_json is not None else os.environ.get(STEPS_ENV))
    except ValueError as exc:  # json.JSONDecodeError — подкласс ValueError
        print(f"summary.py: контекст steps: {exc}", file=sys.stderr)
        return 2
    root = Path(args.root)
    if args.mode == "static":
        static_opts = StaticOptions(
            python_checks=args.python_checks,
            iec_checker=args.iec_checker,
            iec_blocking=args.iec_blocking,
            iec_label=args.iec_label,
            rusty=args.rusty,
            rusty_blocking=args.rusty_blocking,
            paths=args.paths,
            artifact=args.artifact or StaticOptions.artifact,
        )
        summary = static_summary(root, steps, static_opts, args.path_prefix)
    else:
        codesys_opts = CodesysOptions(
            restart_runtime=args.restart_runtime,
            sa_report=args.sa_report,
            sa_blocking=args.sa_blocking,
            junit_report=args.junit_report,
            extra_md=list(args.extra_md),
            artifact=args.artifact or CodesysOptions.artifact,
        )
        summary = codesys_summary(root, steps, codesys_opts, args.path_prefix)

    text = render(summary)
    # --summary - — в stdout; --outputs "" — не писать выходы (пустое значение не берётся из окружения)
    target = args.summary if args.summary is not None else os.environ.get("GITHUB_STEP_SUMMARY")
    if target and target != "-":
        with open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text)
    if args.annotations:
        for line in summary.annotations:
            print(line)
    outputs_path = args.outputs if args.outputs is not None else os.environ.get("GITHUB_OUTPUT")
    if outputs_path:
        write_outputs(outputs_path, summary.outputs)
    failed = [c.label for c in summary.checks if not c.passed()]
    verdict = "пройдено" if summary.result == "success" else "не пройдено: " + ", ".join(failed)
    print(f"summary.py: {summary.title} — {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
