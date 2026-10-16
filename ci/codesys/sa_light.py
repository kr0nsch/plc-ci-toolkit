# -*- coding: utf-8 -*-
"""Static Analysis Light в CODESYS 3.5.17.30: правила на время сеанса и генерация кода (IronPython 2.7).

Скрипт CODESYS Scripting. Запускается только через ci/sa_check.py: тот пишет файл задания
(путь — в переменной окружения CDS_JOB) и запускает CODESYS так же, как tools/codesys/cds.py.
Модули шаблона (cds_common.py, build.py) берутся из каталога job["template_dir"] — это
tools/codesys репозитория-потребителя; сами файлы шаблона не меняются.

Что делает шаг "sa" (проверено на CODESYS V3.5 SP17 Patch 3, docs/sa_light.md):
1. открывает проект (cds_common.Context: проверка блокировки .~u, удаление кэша предкомпиляции);
2. включает правила Static Analysis Light в настройках проекта **только на время сеанса**.
   Штатного API нет: ScriptProject.project_settings в 3.5.17.30 умеет только загрузку исходников.
   Скрипт пишет те же ключи, что диалог «Проект → Установки проекта → Static Analysis Light»:
   хранилище настроек проекта (IOptionStorage, корень Project) плагина StaticAnalysisManager,
   ключ {29BD8D0C-3586-4548-BB48-497B9A01693F}/Rules/<номер правила>, значения bActive и
   bWarning. Проект не сохраняется: диалог IDE переносит настройки в объект «Установки проекта»
   отдельным вызовом, а proj.save() их не записывает (проверено) — файл проекта не меняется;
3. генерирует код (app.generate_code()): правила Light проверяются только при генерации кода,
   app.rebuild() (cds.py build) их не выполняет (проверено);
4. собирает сообщения всех категорий в формате build.py (+ поле prefix) и пишет журнал
   job["log"] и отчёт job["report"] (build/sa.log, build/sa.json).

Ошибки компиляции — шаг не пройден (код 1). Находки SA шаг не валят: политику (базовая линия)
применяет ci/sa_report.py. Внутренний API не найден (другая версия CODESYS) — код 3 и ручной
шаг из docs/MANUAL_STEPS.md.
"""
from __future__ import absolute_import, print_function, unicode_literals

import io
import json
import os
import re
import sys

MYPY = False
if MYPY:  # pragma: no cover
    from typing import Any, Dict, List, Optional, Tuple  # noqa: F401

JOB_ENV = 'CDS_JOB'

# Внутренние имена плагина StaticAnalysisManager 3.5.17.20 (CODESYS 3.5.17.30), найдены отражением
SA_ASSEMBLY = 'StaticAnalysisManager.plugin'
SA_ENVIRONMENT_TYPE = '_3S.CoDeSys.StaticAnalysisManager.APEnvironment'
OPTIONS_ASSEMBLY = 'OptionStorage'
OPTION_ROOT_TYPE = '_3S.CoDeSys.Core.Options.OptionRoot'
OPTION_ROOT_PROJECT = 0
SA_ROOT_KEY = '{29BD8D0C-3586-4548-BB48-497B9A01693F}'
RULES_KEY = 'Rules'
KEY_ACTIVE = 'bActive'
KEY_WARNING = 'bWarning'

# Правила Static Analysis Light в 3.5.17.30 (перечисление RuleId плагина)
LIGHT_RULES = (4, 6, 27, 28, 33, 167)
MODES = ('error', 'warning', 'off')

# Категория сообщений «Доп. проверки кода» (Additional code checks)
CATEGORY_SA_LIGHT = '220493a1-f49b-4416-9a3f-a545db707cbe'
ERROR_SEVERITIES = ('FatalError', 'Error')
WARNING_SEVERITIES = ('Warning',)
_SA_CODE_RE = re.compile(r'^SA(\d+)$')
# «Доп. проверки кода завершены -- 13 ошибки (-ок)» / «Additional code checks complete -- 2 errors»
_SA_END_RE = re.compile(r'(?:завершены|complete)\s*--\s*(\d+)', re.IGNORECASE | re.UNICODE)
_SA_START_RE = re.compile(r'^(?:Доп\. проверки кода|Additional code checks)\s*\.\.\.', re.UNICODE)


# ---------------------------------------------------------------------------
# Чистые функции (проверяются pytest без CODESYS)
# ---------------------------------------------------------------------------


def rule_number(code):
    # type: (Any) -> int
    """Номер правила из 'SA0033', 'sa33', '33' или 33."""
    text = ('%s' % (code,)).strip().upper()
    if text.startswith('SA'):
        text = text[2:]
    if not text.isdigit():
        raise ValueError('неверный код правила: %r (ожидается SA0033 или 33)' % (code,))
    return int(text)


def parse_rules(spec):
    # type: (Any) -> List[Tuple[int, str]]
    """Правила из задания: {"SA0033": "warning", ...} -> [(33, 'warning'), ...] по номеру.

    Правила Light, которых нет в задании, выключаются ('off'): набор правил задаёт только файл
    настроек, а не то, что сохранено в проекте. Неизвестный номер или режим — ValueError.
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError('правила: ожидается объект {"SA0033": "warning", ...}')
    modes = {}  # type: Dict[int, str]
    for code, mode in spec.items():
        number = rule_number(code)
        if number not in LIGHT_RULES:
            raise ValueError('SA%04d нет в Static Analysis Light 3.5.17.30 (есть: %s)'
                             % (number, ', '.join('SA%04d' % n for n in LIGHT_RULES)))
        text = ('%s' % (mode,)).strip().lower()
        if text not in MODES:
            raise ValueError('SA%04d: неверный режим %r (error, warning или off)' % (number, mode))
        modes[number] = text
    return [(number, modes.get(number, 'off')) for number in LIGHT_RULES]


def sa_code_number(code):
    # type: (Optional[str]) -> Optional[int]
    """Номер правила из кода сообщения build.py ('SA0033' -> 33); не находка SA — None."""
    match = _SA_CODE_RE.match(code or '')
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def summarize(records):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    """Итоги генерации кода: ошибки компиляции, находки SA по правилам, выполнялся ли анализ.

    Сообщение «Есть сообщения доп. проверок кода» (категория компиляции, важность Error,
    префикс SA без номера) — признак находок SA, а не ошибка компиляции. Из-за него итог
    компилятора — «1 ошибок …: загрузка невозможна», даже если правила настроены как
    предупреждения (проверено на 3.5.17.30): поле download_blocked.
    """
    compile_errors = 0
    compile_warnings = 0
    findings = 0
    by_code = {}  # type: Dict[str, int]
    started = False
    marker = False
    reported = None  # type: Optional[int]
    summary_line = None  # type: Optional[str]
    for rec in records:
        code = rec.get('code')
        text = rec.get('text') or ''
        severity = rec.get('severity')
        is_sa = (rec.get('prefix') == 'SA') or (code or '').startswith('SA')
        if sa_code_number(code) is not None:
            findings += 1
            by_code[code] = by_code.get(code, 0) + 1
            continue
        if rec.get('category_guid') == CATEGORY_SA_LIGHT:
            if _SA_START_RE.search(text):
                started = True
            match = _SA_END_RE.search(text)
            if match:
                reported = int(match.group(1))
            continue
        if is_sa:
            if severity in ERROR_SEVERITIES:
                marker = True
            continue
        if severity in ERROR_SEVERITIES:
            compile_errors += 1
        elif severity in WARNING_SEVERITIES:
            compile_warnings += 1
        if 'омпиляция завершена' in text or 'uild complete' in text or 'ompile complete' in text:
            summary_line = text
    return {
        'compile_errors': compile_errors,
        'compile_warnings': compile_warnings,
        'sa_started': started,
        'sa_reported': reported,
        'sa_findings': findings,
        'sa_by_code': by_code,
        'download_blocked': marker,
        'summary_line': summary_line,
    }


# ---------------------------------------------------------------------------
# Окружение CODESYS: модули шаблона и внутренние объекты плагина
# ---------------------------------------------------------------------------


def _read_job_template_dir():
    # type: () -> Optional[str]
    path = os.environ.get(JOB_ENV)
    if not path or not os.path.isfile(path):
        return None
    with io.open(path, 'r', encoding='utf-8') as handle:
        job = json.loads(handle.read())
    return job.get('template_dir')


def _import_template():
    # type: () -> Tuple[Any, Any]
    template_dir = _read_job_template_dir()
    if template_dir and template_dir not in sys.path:
        sys.path.insert(0, template_dir)
    # каталог шаблона известен только из задания, поэтому импорт — внутри функции
    import build
    import cds_common

    return cds_common, build


C = None  # type: Any
B = None  # type: Any
if os.environ.get(JOB_ENV):  # в среде CODESYS; pytest импортирует модуль без задания
    C, B = _import_template()


def _bindings():
    # type: () -> Any
    from System.Reflection import BindingFlags  # type: ignore

    return BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static


def _assembly(name):
    # type: (str) -> Any
    import System  # type: ignore

    for asm in System.AppDomain.CurrentDomain.GetAssemblies():
        try:
            if asm.GetName().Name == name:
                return asm
        except Exception:
            continue
    return None


def _api_error(what):
    # type: (str) -> Any
    return C.StepError(
        'Static Analysis Light: %s — внутренний API плагина StaticAnalysisManager отличается от 3.5.17.30. '
        'Включите правила вручную (docs/MANUAL_STEPS.md, раздел «Static Analysis Light») и сгенерируйте код '
        'в IDE' % what,
        C.EXIT_INTERNAL,
    )


def sa_internals():
    # type: () -> Dict[str, Any]
    """Хранилище настроек, корень Project и менеджер статанализа из плагина StaticAnalysisManager."""
    import System  # type: ignore

    asm = _assembly(SA_ASSEMBLY)
    if asm is None:
        raise _api_error('нет сборки %s' % SA_ASSEMBLY)
    env = asm.GetType(SA_ENVIRONMENT_TYPE)
    if env is None:
        raise _api_error('нет типа %s' % SA_ENVIRONMENT_TYPE)
    storage_prop = env.GetProperty('OptionStorage', _bindings())
    manager_prop = env.GetProperty('StaticAnalysisMgr', _bindings())
    if storage_prop is None or manager_prop is None:
        raise _api_error('нет свойств OptionStorage/StaticAnalysisMgr')
    storage = storage_prop.GetValue(None, None)
    manager = manager_prop.GetValue(None, None)
    options_asm = _assembly(OPTIONS_ASSEMBLY)
    root_type = options_asm.GetType(OPTION_ROOT_TYPE) if options_asm is not None else None
    if storage is None or root_type is None:
        raise _api_error('нет хранилища настроек проекта')
    root = storage.GetRootKey(System.Enum.ToObject(root_type, OPTION_ROOT_PROJECT))
    version = '%s' % (asm.GetName().Version,)
    return {'storage': storage, 'root': root, 'manager': manager, 'plugin_version': version}


def _manager_rules(manager):
    # type: (Any) -> Dict[int, Dict[str, Any]]
    """Правила, как их видит менеджер статанализа (после сброса — заново из настроек)."""
    from System.Reflection import BindingFlags  # type: ignore

    found = {}  # type: Dict[int, Dict[str, Any]]
    if manager is None:
        return found
    prop = manager.GetType().GetProperty('Rules', BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
    if prop is None:
        return found
    for item in prop.GetValue(manager, None):
        rule = item.Value
        found[int(rule.Id)] = {'name': C._text(rule.Name), 'active': bool(rule.Active), 'warning': bool(rule.Warning)}
    return found


def apply_rules(rules, log):
    # type: (List[Tuple[int, str]], Any) -> Dict[str, Any]
    """Записывает режимы правил в настройки проекта открытого (основного) проекта — без сохранения."""
    api = sa_internals()
    sa_key = api['root'].CreateSubKey(SA_ROOT_KEY)
    if sa_key.HasSubKey(RULES_KEY):
        sa_key.DeleteSubKey(RULES_KEY)
    rules_key = sa_key.CreateSubKey(RULES_KEY)
    for number, mode in rules:
        key = rules_key.CreateSubKey('%d' % number)
        key[KEY_ACTIVE] = mode != 'off'
        key[KEY_WARNING] = mode == 'warning'
    seen = _manager_rules(api['manager'])
    applied = []  # type: List[Dict[str, Any]]
    notes = []  # type: List[str]
    for number, mode in rules:
        state = seen.get(number)
        item = {'rule': 'SA%04d' % number, 'mode': mode, 'name': state['name'] if state else None}
        if state is None:
            notes.append('SA%04d: менеджер статанализа не знает это правило' % number)
        elif state['active'] != (mode != 'off') or (mode != 'off' and state['warning'] != (mode == 'warning')):
            notes.append('SA%04d: менеджер статанализа видит режим active=%s warning=%s вместо %s'
                         % (number, state['active'], state['warning'], mode))
        applied.append(item)
        log.info('правило SA%04d (%s): %s' % (number, item['name'] or '?', mode))
    for note in notes:
        log.warn(note)
    return {'plugin_version': api['plugin_version'], 'rules': applied, 'notes': notes}


def collect_messages():
    # type: () -> List[Dict[str, Any]]
    """Сообщения всех активных категорий в формате build.py (message_record) + префикс сообщения."""
    system = C.api().system
    records = []  # type: List[Dict[str, Any]]
    for cat in system.get_message_categories(True):
        guid = C._text(cat)
        try:
            description = system.get_message_category_description(cat)
        except Exception:
            description = None
        for msg in system.get_message_objects(cat):
            rec = B.message_record(guid, C._text(description) if description else None, msg)
            try:
                prefix = msg.prefix
            except Exception:
                prefix = None
            rec['prefix'] = C._text(prefix) if prefix else None
            records.append(rec)
    return records


def run(ctx, step):
    # type: (Any, Dict[str, Any]) -> Dict[str, Any]
    log = C.Log(step.get('log'))
    try:
        try:
            rules = parse_rules(step.get('rules'))
        except ValueError as exc:
            raise C.StepError('%s' % exc)
        proj, _ = ctx.open_project(step['project'], create=False, log=log)
        app = C.application(proj)
        applied = apply_rules(rules, log)
        B.clear_messages()
        log.info('генерация кода приложения проекта %s (Static Analysis Light выполняется при генерации кода)'
                 % step['project'])
        app.generate_code()
        records = collect_messages()
        for rec in records:
            line = B.format_record(rec)
            if rec['severity'] in ERROR_SEVERITIES:
                log.error(line)
            elif rec['severity'] in WARNING_SEVERITIES:
                log.warn(line)
            else:
                log.info(line)
        summary = summarize(records)
        notes = list(applied['notes'])
        if not summary['sa_started']:
            notes.append('Static Analysis Light не выполнялся: нет сообщения «Доп. проверки кода...»')
            log.warn(notes[-1])
        log.info('итог: ошибок компиляции %d, предупреждений %d, находок SA %d'
                 % (summary['compile_errors'], summary['compile_warnings'], summary['sa_findings']))
        ok = summary['compile_errors'] == 0
        result = {
            'ok': ok,
            'tool': 'CODESYS Static Analysis Light',
            'project': step['project'],
            'code_generation': True,
            'plugin_version': applied['plugin_version'],
            'rules': applied['rules'],
            'errors': summary['compile_errors'],
            'warnings': summary['compile_warnings'],
            'summary_line': summary['summary_line'],
            'sa': {
                'started': summary['sa_started'],
                'reported': summary['sa_reported'],
                'findings': summary['sa_findings'],
                'by_code': summary['sa_by_code'],
                'download_blocked': summary['download_blocked'],
            },
            'notes': notes,
            'messages': records,
        }
        if step.get('report'):
            C.write_json(step['report'], result)
        if not ok:
            result['error'] = 'генерация кода: ошибок компиляции %d (см. %s)' % (
                summary['compile_errors'], step.get('log') or 'журнал')
        return result
    except Exception as exc:
        log.error('%s' % exc)
        raise
    finally:
        log.close()


if __name__ == '__main__':
    if C is None:
        sys.stderr.write('sa_light.py: нет задания %s — запускайте через ci/sa_check.py\n' % JOB_ENV)
        sys.exit(3)
    C.ACTION_MODULES['sa'] = 'sa_light'
    C.run_main('sa')
