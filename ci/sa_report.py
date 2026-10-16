#!/usr/bin/env python3
"""sa_report.py — отчёт по находкам CODESYS Static Analysis Light (коды SAxxxx) и политика базовой линии.

    python ci/sa_report.py [--root R] [--input FILE] [--baseline FILE] [--fail-on new|any|never]
                           [--json FILE] [--md FILE] [--summary FILE] [--annotations [--path-prefix P]]
                           [--allow-incomplete] [--update-baseline] [--max-rows N]

Вход (`--input`, по умолчанию `<root>/build/sa.json`):
- `build/sa.json` и `build/sa.log` из ci/sa_check.py (генерация кода с правилами Light);
- `build/build.json` и `build/build.log` из `tools/codesys/cds.py build` — тот же формат сообщений.
  Но cds.py build вызывает `app.rebuild()` без генерации кода, а правила Light проверяются только
  при генерации кода (проверено на 3.5.17.30): такой вход отчёт помечает как неполный анализ.

Находка — сообщение с кодом SA и номером больше 0 (SA0033). Место — поле source (файл и строка
раскладки, его дописывают cds.py и sa_check.py); если его нет (журнал), место вычисляется функциями
шаблона `tools/codesys/cdshost/cli.py` (source_location) по объекту и позиции из сообщения.

Базовая линия (`--baseline`, по умолчанию `<root>/ci/sa_baseline.json`): принятые находки с
обоснованием. Сравнение — по правилу, объекту и тексту сообщения (без номера строки: сдвиг строк
не делает находку новой), с учётом количества. Политика `--fail-on`: `new` — код 1, если есть
находки вне базовой линии (по умолчанию); `any` — если есть любые находки; `never` — только отчёт.
Неполный анализ (не было генерации кода или Static Analysis Light не выполнялся) — тоже код 1,
кроме `--fail-on never` и `--allow-incomplete`. `--update-baseline` записывает текущие находки в
базовую линию (обоснования совпавших записей сохраняются) и завершается с кодом 0.

Выход: JSON (`<root>/build/sa_report.json`), Markdown (`<root>/build/sa_report.md`), по желанию —
дописать Markdown в файл (`--summary "$GITHUB_STEP_SUMMARY"`) и аннотации GitHub Actions для новых
находок (`--annotations`). Коды выхода: 0 — политика выполнена; 1 — нет (или неполный анализ);
2 — аргументы или неверная базовая линия; 3 — нет входного файла. Только стандартная библиотека.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INPUT = 3

SCHEMA = 1
POLICIES = ("new", "any", "never")
CATEGORY_SA_LIGHT = "220493a1-f49b-4416-9a3f-a545db707cbe"  # «Доп. проверки кода»

# Правила Static Analysis Light в CODESYS 3.5.17.30: (название в IDE на русском, на английском)
RULES: dict[str, tuple[str, str]] = {
    "SA0004": ("Множественный доступ по записи на выходе", "Multiple write access on output"),
    "SA0006": ("Параллельный доступ", "Write access from several tasks"),
    "SA0027": ("Многократное использование имени", "Multiple uses of identifiers"),
    "SA0028": ("Перекрывающиеся области памяти", "Overlapping memory areas"),
    "SA0033": ("Неиспользуемые переменные", "Unused variables"),
    "SA0167": ("Сообщать о временных экземплярах ФБ", "Report temporary FunctionBlock instances"),
}

_SA_CODE_RE = re.compile(r"^SA(\d+)$")
_SA_START_RE = re.compile(r"^(?:Доп\. проверки кода|Additional code checks)\s*\.\.\.", re.IGNORECASE)
_SA_END_RE = re.compile(
    r"(?:Доп\. проверки кода завершены|Additional code checks complete)\s*--\s*(\d+)", re.IGNORECASE
)
_CODEGEN_RE = re.compile(r"^(?:генерировать код|generate code)|Размер генерируемого кода|Size of generated code", re.I)
_SOURCE_RE = re.compile(r"^(?P<file>.+?)(?::(?P<line>\d+))?$")
# Строка журнала build.py/sa_light.py: «2026-10-16 01:35:31 WARN  Warning     SA0033 текст [объект, позиция]»
_LOG_RE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (?:INFO|WARN|ERROR)\s+"
    r"(?P<severity>FatalError|Error|Warning|Information|Text|\?)\s+(?:(?P<code>[A-Z]+\d{4}) )?(?P<rest>.*)$"
)
_WHERE_RE = re.compile(r"^(?P<text>.*) \[(?P<object>[A-Za-z_][A-Za-z0-9_.]*)(?:, (?P<position>[^\[\]]+))?\]$")


class ReportError(Exception):
    """Ошибка с понятным сообщением и кодом выхода."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Модель
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """Находка Static Analysis Light: правило, место, сообщение и статус относительно базовой линии."""

    rule: str
    severity: str
    message: str
    object: str | None = None
    position: str | None = None
    file: str | None = None
    line: int | None = None
    group: str | None = None
    status: str = "new"  # new | baseline
    reason: str | None = None

    def key(self) -> tuple[str, str, str]:
        """Ключ сравнения с базовой линией: правило, объект (или файл), текст — без номера строки."""
        return (self.rule, self.object or self.file or "", normalize(self.message))

    @property
    def where(self) -> str:
        if self.file:
            return f"{self.file}:{self.line}" if self.line else self.file
        if self.object:
            return f"{self.object}, {self.position}" if self.position else self.object
        return "—"

    def to_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "name": rule_name(self.rule),
            "severity": self.severity,
            "message": self.message,
            "group": self.group,
            "object": self.object,
            "position": self.position,
            "file": self.file,
            "line": self.line,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class Analysis:
    """Что известно о прогоне: выполнялся ли анализ, была ли генерация кода, итоги CODESYS."""

    source: str
    findings: list[Finding] = field(default_factory=list)
    sa_started: bool = False
    sa_reported: int | None = None
    code_generation: bool = False
    compile_errors: int = 0
    project: str | None = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.sa_started and self.code_generation and self.compile_errors == 0


@dataclass
class BaselineEntry:
    rule: str
    object: str | None
    file: str | None
    message: str
    count: int = 1
    reason: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.object or self.file or "", normalize(self.message))

    def to_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "object": self.object,
            "file": self.file,
            "message": self.message,
            "count": self.count,
            "reason": self.reason,
        }


@dataclass
class Comparison:
    findings: list[Finding]
    fixed: list[BaselineEntry]
    baseline_size: int

    @property
    def new(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "new"]


def normalize(text: str) -> str:
    return " ".join(str(text).split())


def rule_name(code: str) -> str:
    return RULES.get(code, ("", ""))[0]


def sa_code(code: Any) -> str | None:
    """'SA0033' -> 'SA0033'; код без номера (SA0000) или не SA — None."""
    match = _SA_CODE_RE.match(str(code or ""))
    if not match or int(match.group(1)) == 0:
        return None
    return f"SA{int(match.group(1)):04d}"


def rule_code(text: Any) -> str | None:
    """Код правила, записанный человеком: 'SA0033', 'sa33', '33' -> 'SA0033'; иначе None."""
    value = str(text or "").strip().upper()
    value = value.removeprefix("SA")
    return f"SA{int(value):04d}" if value.isdigit() and int(value) > 0 else None


def split_source(source: Any) -> tuple[str | None, int | None]:
    """'src/POUs/F/F.impl.st:16' -> ('src/POUs/F/F.impl.st', 16)."""
    if not source:
        return None, None
    match = _SOURCE_RE.match(str(source))
    if match is None:
        return str(source), None
    line = match.group("line")
    return match.group("file"), int(line) if line else None


# ---------------------------------------------------------------------------
# Вход: JSON сообщений (build.json, sa.json) или журнал (build.log, sa.log)
# ---------------------------------------------------------------------------


def parse_log(text: str) -> list[dict[str, Any]]:
    """Сообщения из журнала build.py/sa_light.py (строки «время уровень важность [код] текст [объект, позиция]»)."""
    messages: list[dict[str, Any]] = []
    for raw in text.splitlines():
        match = _LOG_RE.match(raw.rstrip())
        if match is None:
            continue
        rest = match.group("rest")
        obj = position = None
        where = _WHERE_RE.match(rest)
        if where is not None:
            rest, obj, position = where.group("text"), where.group("object"), where.group("position")
        messages.append(
            {
                "severity": match.group("severity"),
                "code": match.group("code"),
                "text": rest.strip(),
                "object": obj,
                "position": position.strip() if position else None,
                "source": None,
                "category_guid": None,
            }
        )
    return messages


def load_input(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Сообщения и метаданные из JSON (build.json/sa.json) или текстового журнала."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ReportError(
            f"нет входного файла {path}: сначала python ci/sa_check.py (или cds.py build)", EXIT_INPUT
        ) from None
    except UnicodeDecodeError as exc:
        raise ReportError(f"{path}: не UTF-8 ({exc})", EXIT_INPUT) from None
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReportError(f"{path}: неверный JSON: {exc}", EXIT_INPUT) from None
        if not isinstance(data, Mapping) or not isinstance(data.get("messages"), list):
            raise ReportError(f"{path}: ожидается объект с полем messages (build.json, sa.json)", EXIT_INPUT)
        messages = [dict(m) for m in data["messages"] if isinstance(m, Mapping)]
        meta = {k: v for k, v in data.items() if k != "messages"}
        meta["format"] = "json"
        return messages, meta
    return parse_log(text), {"format": "log"}


def template_cli(root: Path) -> Any | None:
    """cdshost.cli шаблона из <root>/tools/codesys (привязка сообщений к файлам) или None, если шаблона нет."""
    template = root / "tools" / "codesys"
    if not (template / "cdshost" / "cli.py").is_file():
        return None
    if str(template) not in sys.path:
        sys.path.insert(0, str(template))
    try:
        return importlib.import_module("cdshost.cli")
    except ImportError:
        return None


def locate(messages: Sequence[dict[str, Any]], root: Path, cli: Any | None) -> int:
    """Поле source по объекту и позиции (source_location шаблона); возвращает число привязанных сообщений."""
    pending = [m for m in messages if not m.get("source") and m.get("object")]
    if not pending or cli is None:
        return 0
    try:
        objects = cli.layout_objects(root)
    except (OSError, UnicodeDecodeError):
        return 0
    found = 0
    for msg in pending:
        msg["source"] = cli.source_location(msg.get("object"), msg.get("position"), objects)
        found += 1 if msg["source"] else 0
    return found


# Служебная ошибка категории компиляции «Есть сообщения доп. проверок кода. См. категорию …»
# (и то же у полной версии Static Analysis) — признак находок SA, а не ошибка компиляции
_SA_MARKERS = (
    "есть сообщения доп. проверок кода",
    "message(s) of additional code checks available",
    "есть сообщения статического анализа",
    "static analysis message(s) available",
)


def is_sa_marker(msg: Mapping[str, Any], text: str) -> bool:
    if str(msg.get("prefix") or "") == "SA" and not msg.get("code"):
        return True
    lower = text.lower()
    return any(marker in lower for marker in _SA_MARKERS)


def analyse(messages: Sequence[Mapping[str, Any]], meta: Mapping[str, Any], source: str) -> Analysis:
    """Находки SA и признаки полноты анализа из сообщений CODESYS (в порядке вывода)."""
    result = Analysis(source=source)
    result.project = str(meta["project"]) if meta.get("project") else None
    result.rules = [dict(r) for r in meta.get("rules", []) if isinstance(r, Mapping)]
    result.code_generation = bool(meta.get("code_generation"))
    # Заголовок группы («Следующие переменные обращаются к одной и той же памяти:») относится к
    # идущим за ним подряд находкам одного правила
    group: str | None = None
    group_rule: str | None = None
    for msg in messages:
        text = str(msg.get("text") or "")
        severity = str(msg.get("severity") or "")
        code = sa_code(msg.get("code"))
        if _CODEGEN_RE.search(text):
            result.code_generation = True
        if _SA_START_RE.search(text):
            result.sa_started = True
            group = None
            continue
        end = _SA_END_RE.search(text)
        if end:
            result.sa_reported = int(end.group(1))
            group = None
            continue
        if code is not None:
            if group is not None and group_rule not in (None, code):
                group = None
            group_rule = code
            file, line = split_source(msg.get("source"))
            message = normalize(text)
            title = group.rstrip(":").strip() if group else ""
            result.findings.append(
                Finding(
                    rule=code,
                    severity=severity,
                    message=message,
                    object=str(msg["object"]) if msg.get("object") else None,
                    position=str(msg["position"]) if msg.get("position") else None,
                    file=file,
                    line=line,
                    group=group if group and not message.startswith(title) else None,
                )
            )
            continue
        in_sa_category = msg.get("category_guid") == CATEGORY_SA_LIGHT
        if severity == "Text" and text.endswith(":") and (in_sa_category or result.sa_started):
            group = normalize(text)
            group_rule = None
            continue
        if severity in ("Error", "FatalError") and not in_sa_category and not is_sa_marker(msg, text):
            result.compile_errors += 1
    if not result.sa_started:
        result.notes.append(
            "Static Analysis Light не выполнялся (нет сообщения «Доп. проверки кода...»): ошибки компиляции "
            "или правила не включены — находок нет не потому, что код чистый."
        )
    elif not result.code_generation:
        result.notes.append(
            "Во входе нет генерации кода: правила Static Analysis Light проверяются только при генерации кода, "
            "а cds.py build вызывает app.rebuild() без неё (3.5.17.30). Используйте python ci/sa_check.py."
        )
    if result.compile_errors:
        result.notes.append(f"Ошибок компиляции: {result.compile_errors} — анализ не полный.")
    if result.sa_reported is not None and result.sa_reported != len(result.findings):
        result.notes.append(
            f"CODESYS сообщает находок {result.sa_reported}, разобрано {len(result.findings)} "
            "(сообщения с кодом SA без номера или другой формат журнала)."
        )
    return result


# ---------------------------------------------------------------------------
# Базовая линия
# ---------------------------------------------------------------------------


def load_baseline(path: Path | None) -> list[BaselineEntry]:
    if path is None or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path}: неверный JSON базовой линии: {exc}") from None
    if not isinstance(data, Mapping) or not isinstance(data.get("findings", []), list):
        raise ReportError(f"{path}: ожидается объект с полем findings (список)")
    entries = []
    for i, item in enumerate(data.get("findings", [])):
        if not isinstance(item, Mapping) or not item.get("rule") or not item.get("message"):
            raise ReportError(f"{path}: запись {i + 1}: нужны поля rule и message")
        rule = rule_code(item["rule"])
        if rule is None:
            raise ReportError(f"{path}: запись {i + 1}: неверное правило {item['rule']!r}")
        count = item.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ReportError(f"{path}: запись {i + 1}: count должен быть целым ≥ 1")
        entries.append(
            BaselineEntry(
                rule=rule,
                object=str(item["object"]) if item.get("object") else None,
                file=str(item["file"]) if item.get("file") else None,
                message=normalize(str(item["message"])),
                count=count,
                reason=str(item.get("reason") or ""),
            )
        )
    return entries


def compare(findings: Sequence[Finding], baseline: Sequence[BaselineEntry]) -> Comparison:
    """Помечает находки new/baseline (с учётом количества) и находит исправленные записи базовой линии."""
    left: Counter[tuple[str, str, str]] = Counter()
    reasons: dict[tuple[str, str, str], str] = {}
    for entry in baseline:
        left[entry.key()] += entry.count
        if entry.reason:
            reasons.setdefault(entry.key(), entry.reason)
    marked = []
    for finding in findings:
        key = finding.key()
        if left[key] > 0:
            left[key] -= 1
            finding.status = "baseline"
            finding.reason = reasons.get(key)
        else:
            finding.status = "new"
            finding.reason = None
        marked.append(finding)
    fixed = []
    for entry in baseline:
        rest = min(left[entry.key()], entry.count)
        if rest > 0:
            left[entry.key()] -= rest
            fixed.append(BaselineEntry(entry.rule, entry.object, entry.file, entry.message, rest, entry.reason))
    return Comparison(marked, fixed, sum(e.count for e in baseline))


def updated_baseline(findings: Sequence[Finding], old: Sequence[BaselineEntry]) -> dict[str, Any]:
    """Базовая линия из текущих находок: одинаковые находки — одна запись с count, обоснования сохраняются."""
    reasons = {e.key(): e.reason for e in old if e.reason}
    grouped: dict[tuple[str, str, str], BaselineEntry] = {}
    for f in findings:
        key = f.key()
        if key in grouped:
            grouped[key].count += 1
        else:
            grouped[key] = BaselineEntry(f.rule, f.object, f.file, f.message, 1, reasons.get(key, ""))
    entries = sorted(grouped.values(), key=lambda e: (e.rule, e.file or "", e.object or "", e.message))
    return {
        "_doc": [
            "Базовая линия CODESYS Static Analysis Light для ci/sa_report.py: принятые находки с обоснованием.",
            "Сравнение — по правилу, объекту и тексту (язык IDE!), без номера строки; count — сколько одинаковых.",
            "Обновление: python ci/sa_report.py --update-baseline; обоснования (reason) заполняет человек.",
        ],
        "schema": SCHEMA,
        "findings": [e.to_json() for e in entries],
    }


# ---------------------------------------------------------------------------
# Политика и отчёты
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    passed: bool
    reason: str


def verdict(analysis: Analysis, cmp: Comparison, policy: str, allow_incomplete: bool) -> Verdict:
    if policy == "never":
        return Verdict(True, "политика never: только отчёт")
    if not analysis.complete and not allow_incomplete:
        return Verdict(False, "анализ неполный (см. замечания)")
    if policy == "any" and cmp.findings:
        return Verdict(False, f"находок {len(cmp.findings)} (политика any)")
    if policy == "new" and cmp.new:
        return Verdict(False, f"новых находок {len(cmp.new)} (вне базовой линии)")
    if policy == "any":
        return Verdict(True, "находок нет")
    return Verdict(True, "новых находок нет" if cmp.findings else "находок нет")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _basename(path: str) -> str:
    """Имя файла из пути Windows или POSIX (отчёт строится и на Linux по JSON с Windows)."""
    return re.split(r"[\\/]", path)[-1]


def by_rule(findings: Iterable[Finding]) -> dict[str, list[Finding]]:
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.rule, []).append(f)
    return dict(sorted(groups.items()))


def render_markdown(
    analysis: Analysis,
    cmp: Comparison,
    result: Verdict,
    policy: str,
    baseline_path: str | None,
    max_rows: int,
) -> str:
    rules_text = ", ".join(f"{r.get('rule')} {r.get('mode')}" for r in analysis.rules) or "—"
    groups = by_rule(cmp.findings)
    lines = [
        "## Static Analysis Light (CODESYS)",
        "",
        "| Параметр | Значение |",
        "|---|---|",
        f"| Вход | `{_cell(analysis.source)}` (генерация кода: {'да' if analysis.code_generation else '**нет**'}) |",
        f"| Проект | {f'`{_cell(_basename(analysis.project))}`' if analysis.project else '—'} |",
        f"| Правила | {_cell(rules_text)} |",
        f"| Базовая линия | {f'`{_cell(baseline_path)}`' if baseline_path else '—'} (записей {cmp.baseline_size}) |",
        f"| Политика | `--fail-on {policy}` |",
        f"| Итог | **{'пройдено' if result.passed else 'не пройдено'}**: {_cell(result.reason)} |",
        "",
    ]
    if groups:
        lines += ["| Правило | Название | Всего | Новых | В базовой линии |", "|---|---|---|---|---|"]
        for code, items in groups.items():
            new = sum(1 for f in items if f.status == "new")
            lines.append(f"| {code} | {_cell(rule_name(code)) or '—'} | {len(items)} | {new} | {len(items) - new} |")
        lines.append("")
    else:
        lines += ["Находок нет." if analysis.sa_started else "Находок нет — но и анализа не было (см. замечания).", ""]
    shown = 0
    for code, items in groups.items():
        lines += [f"### {code} — {_cell(rule_name(code)) or 'правило полной версии Static Analysis'}", ""]
        lines += ["| Место | Объект | Важность | Сообщение | Статус |", "|---|---|---|---|---|"]
        for f in sorted(items, key=lambda x: (x.status != "new", x.file or "", x.line or 0, x.message)):
            if shown >= max_rows:
                break
            message = f"{f.group} {f.message}" if f.group else f.message
            status = "**новая**" if f.status == "new" else f"базовая линия: {_cell(f.reason) or 'без обоснования'}"
            lines.append(
                f"| `{_cell(f.where)}` | {_cell(f.object) or '—'} | {_cell(f.severity)} | {_cell(message)} | {status} |"
            )
            shown += 1
        lines.append("")
    if shown < len(cmp.findings):
        lines += [f"_Показано {shown} из {len(cmp.findings)} находок; полный список — JSON-отчёт._", ""]
    if cmp.fixed:
        lines += ["### Исправлено (записи базовой линии без находок — уберите их)", ""]
        lines += ["| Правило | Объект | Сообщение | Количество |", "|---|---|---|---|"]
        for e in cmp.fixed:
            lines.append(f"| {e.rule} | {_cell(e.object or e.file) or '—'} | {_cell(e.message)} | {e.count} |")
        lines.append("")
    if analysis.notes:
        lines += ["### Замечания", ""] + [f"- {_cell(n)}" for n in analysis.notes] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_json(
    analysis: Analysis, cmp: Comparison, result: Verdict, policy: str, baseline: str | None
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for code, items in by_rule(cmp.findings).items():
        new = sum(1 for f in items if f.status == "new")
        counts[code] = {"total": len(items), "new": new, "baseline": len(items) - new}
    return {
        "schema": SCHEMA,
        "tool": "CODESYS Static Analysis Light",
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "input": analysis.source,
        "project": analysis.project,
        "rules": analysis.rules,
        "analysis": {
            "complete": analysis.complete,
            "sa_started": analysis.sa_started,
            "code_generation": analysis.code_generation,
            "compile_errors": analysis.compile_errors,
            "reported_by_codesys": analysis.sa_reported,
        },
        "policy": policy,
        "baseline": baseline,
        "passed": result.passed,
        "verdict": result.reason,
        "totals": {
            "findings": len(cmp.findings),
            "new": len(cmp.new),
            "baseline": len(cmp.findings) - len(cmp.new),
            "fixed": sum(e.count for e in cmp.fixed),
            "by_rule": counts,
        },
        "findings": [f.to_json() for f in cmp.findings],
        "fixed": [e.to_json() for e in cmp.fixed],
        "notes": analysis.notes,
    }


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def github_annotations(cmp: Comparison, level: str, prefix: str) -> list[str]:
    """Команды GitHub Actions для новых находок: ::warning file=...,line=...,title=SA0033::текст."""
    out = []
    for f in cmp.new:
        props = []
        if f.file:
            path = f"{prefix.rstrip('/')}/{f.file}" if prefix else f.file
            props.append(f"file={_escape_prop(path)}")
            if f.line:
                props.append(f"line={f.line}")
        props.append(f"title={_escape_prop(f'{f.rule} {rule_name(f.rule)}'.strip())}")
        where = "" if f.file else f" [{f.where}]"
        out.append(f"::{level} {','.join(props)}::{_escape_data(f.message + where)}")
    return out


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def default_input(root: Path) -> Path:
    return root / "build" / "sa.json"


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    input_path = Path(args.input) if args.input else default_input(root)
    if not input_path.is_absolute():
        input_path = root / input_path
    baseline_path = Path(args.baseline) if args.baseline else root / "ci" / "sa_baseline.json"
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path
    messages, meta = load_input(input_path)
    locate(messages, root, template_cli(root))
    analysis = analyse(messages, meta, _rel(input_path, root))
    baseline = load_baseline(baseline_path)
    if not baseline_path.exists():
        analysis.notes.append(f"Базовой линии {_rel(baseline_path, root)} нет: все находки считаются новыми.")
    unexplained = [e for e in baseline if not e.reason.strip()]
    if unexplained:
        analysis.notes.append(f"Записей базовой линии без обоснования (reason): {len(unexplained)}.")
    if args.update_baseline:
        data = updated_baseline(analysis.findings, baseline)
        _write(baseline_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        empty = sum(1 for e in data["findings"] if not e["reason"])
        print(f"[sa-report] базовая линия {baseline_path}: записей {len(data['findings'])}, без обоснования {empty}")
        return EXIT_OK
    cmp = compare(analysis.findings, baseline)
    result = verdict(analysis, cmp, args.fail_on, args.allow_incomplete)
    baseline_rel = _rel(baseline_path, root) if baseline_path.exists() else None
    md = render_markdown(analysis, cmp, result, args.fail_on, baseline_rel, args.max_rows)
    report = render_json(analysis, cmp, result, args.fail_on, baseline_rel)
    json_path = Path(args.json) if args.json else root / "build" / "sa_report.json"
    md_path = Path(args.md) if args.md else root / "build" / "sa_report.md"
    _write(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _write(md_path, md)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(md + "\n")
    if args.annotations:
        level = "error" if args.fail_on != "never" else "warning"
        for line in github_annotations(cmp, level, args.path_prefix or ""):
            print(line)
    for f in cmp.new:
        print(f"[sa-report] новая: {f.where}: {f.rule} {f.message}")
    for note in analysis.notes:
        print(f"[sa-report] замечание: {note}")
    print(
        f"[sa-report] находок {len(cmp.findings)}, новых {len(cmp.new)}, в базовой линии "
        f"{len(cmp.findings) - len(cmp.new)}, исправлено {sum(e.count for e in cmp.fixed)} — "
        f"{'пройдено' if result.passed else 'НЕ пройдено'} ({result.reason}); {md_path}"
    )
    return EXIT_OK if result.passed else EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sa_report.py",
        description="Отчёт CODESYS Static Analysis Light (SAxxxx) и политика базовой линии (docs/sa_light.md).",
    )
    parser.add_argument("--root", default=".", help="корень репозитория (по умолчанию текущий каталог)")
    parser.add_argument(
        "--input", metavar="FILE", help="sa.json, sa.log, build.json или build.log (по умолчанию build/sa.json)"
    )
    parser.add_argument("--baseline", metavar="FILE", help="базовая линия (по умолчанию ci/sa_baseline.json)")
    parser.add_argument("--fail-on", choices=POLICIES, default="new", help="политика кода выхода (по умолчанию new)")
    parser.add_argument("--allow-incomplete", action="store_true", help="неполный анализ не считать провалом")
    parser.add_argument("--json", metavar="FILE", help="JSON-отчёт (по умолчанию build/sa_report.json)")
    parser.add_argument("--md", metavar="FILE", help="Markdown-отчёт (по умолчанию build/sa_report.md)")
    parser.add_argument("--summary", metavar="FILE", help="дописать Markdown в файл, например $GITHUB_STEP_SUMMARY")
    parser.add_argument("--annotations", action="store_true", help="аннотации GitHub Actions для новых находок")
    parser.add_argument("--path-prefix", default="", help="префикс путей аннотаций (репозиторий в подкаталоге)")
    parser.add_argument("--update-baseline", action="store_true", help="записать текущие находки в базовую линию")
    parser.add_argument("--max-rows", type=int, default=200, help="строк находок в Markdown (по умолчанию 200)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.max_rows < 1:
        print("[sa-report] ОШИБКА: --max-rows должен быть ≥ 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        return run(args)
    except ReportError as exc:
        print(f"[sa-report] ОШИБКА: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
