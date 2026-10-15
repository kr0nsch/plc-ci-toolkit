# -*- coding: utf-8 -*-
"""Общая часть скриптов CODESYS Scripting (IronPython 2.7): задание, журнал, результат, дерево объектов.

Модуль импортируют import_sources.py, export_sources.py, build.py и run_tests.py. Он написан на
общем подмножестве Python 2.7 и 3.x (как stlayout.py): чистые функции (разбор значений ПЛК,
определение вида объекта по GUID, пути) проверяются pytest без CODESYS, а функции, которым нужен
CODESYS, обращаются к модулю scriptengine (в среде CODESYS он импортируется явно — проверено на
3.5.17.30: в импортируемых модулях доступны projects, system, online, librarymanager, PouType,
DutType, DeviceID, OnlineChangeOption, Severity).

Параметры передаются не аргументами командной строки, а файлом задания (JSON): путь к нему — в
переменной окружения CDS_JOB. Его пишет tools/codesys/cds.py. Задание — список шагов
(import, export, build, test), шаги выполняются по порядку в одном процессе CODESYS; итог
записывается в файл результата (job["result"]) и возвращается кодом выхода процесса
(sys.exit(n) доходит до ОС — проверено на 3.5.17.30; необработанное исключение даёт код 1).
"""
from __future__ import absolute_import, print_function, unicode_literals

import io
import json
import os
import re
import sys
import time
import traceback

try:  # pragma: no cover - есть только в среде CODESYS
    import scriptengine as _se  # type: ignore
except ImportError:  # Python 3 (pytest) — без CODESYS
    _se = None

MYPY = False
if MYPY:  # pragma: no cover
    from typing import Any, Dict, List, Optional, Tuple  # noqa: F401

try:
    STRING_TYPES = (basestring,)  # type: ignore
except NameError:  # Python 3
    STRING_TYPES = (str,)

JOB_ENV = 'CDS_JOB'

# Коды выхода процесса CODESYS (их же видит cds.py)
EXIT_OK = 0
EXIT_FAILED = 1  # проверка не прошла: ошибки раскладки, сборки, упавшие тесты
EXIT_INTERNAL = 3  # исключение в скрипте, нет задания и т.п.

# ---------------------------------------------------------------------------
# GUID типов объектов (str(obj.type)). Проверено распечаткой дерева на 3.5.17.30
# (probe_tree.py), кроме отмеченных «не проверено».
# ---------------------------------------------------------------------------
TYPE_FOLDER = '738bea1e-99bb-4f04-90bb-a7a567e74e3a'
TYPE_POU = '6f9dac99-8de1-4efc-8465-68ac443b7d08'
TYPE_DUT = '2db5746d-d284-4425-9f7f-2663a34b0ebc'
TYPE_GVL = 'ffbfa93a-b94d-45fc-a329-229860183b1d'
# Persistent Variables: не проверено (создать объект скриптом в SP17 нельзя), GUID из сообщества
TYPE_PVL = ('261bd6e6-249c-4232-bb6f-84c2fbeef430', '3183921b-cc91-4712-9781-c3b6555122b5')
TYPE_ITF = '6654496c-404d-479a-aad2-8551054e5f1e'
TYPE_METHOD_POU = 'f8a58466-d7f6-439f-bbb8-d4600e41d099'
TYPE_METHOD_ITF = 'f89f7675-27f1-46b3-8abb-b7da8e774ffd'
TYPE_PROPERTY = '5a3b8626-d3e9-4f37-98b5-66420063d91e'
TYPE_ACCESSOR_POU = '792f2eb6-721e-4e64-ba20-bc98351056db'
TYPE_ACCESSOR_ITF = '28747452-a93d-4b34-8d05-d2c6018edd7d'
TYPE_ACTION = '8ac092e5-3128-4e26-9e7e-11016c6684f2'
TYPE_TRANSITION = 'a10c6218-cb94-436f-91c6-e1652575253d'  # не проверено
TYPE_LIBMAN = 'adb5cb65-8e1d-4a00-b70a-375ea27582f3'
TYPE_TASK_CONFIG = 'ae1de277-a207-4a28-9efb-456c06bd52f3'
TYPE_TASK = '98a2708a-9b18-4f31-82ed-a1465b24fa2d'

K_FOLDER = 'folder'
K_POU = 'pou'
K_DUT = 'dut'
K_GVL = 'gvl'
K_PVL = 'pvl'
K_ITF = 'itf'
K_METHOD = 'method'
K_PROPERTY = 'property'
K_ACCESSOR = 'accessor'
K_ACTION = 'action'
K_TRANSITION = 'transition'
K_LIBMAN = 'libman'
K_TASK_CONFIG = 'task_config'
K_TASK = 'task'
K_OTHER = 'other'

_KIND_OF_TYPE = {
    TYPE_FOLDER: K_FOLDER,
    TYPE_POU: K_POU,
    TYPE_DUT: K_DUT,
    TYPE_GVL: K_GVL,
    TYPE_PVL[0]: K_PVL,
    TYPE_PVL[1]: K_PVL,
    TYPE_ITF: K_ITF,
    TYPE_METHOD_POU: K_METHOD,
    TYPE_METHOD_ITF: K_METHOD,
    TYPE_PROPERTY: K_PROPERTY,
    TYPE_ACCESSOR_POU: K_ACCESSOR,
    TYPE_ACCESSOR_ITF: K_ACCESSOR,
    TYPE_ACTION: K_ACTION,
    TYPE_TRANSITION: K_TRANSITION,
    TYPE_LIBMAN: K_LIBMAN,
    TYPE_TASK_CONFIG: K_TASK_CONFIG,
    TYPE_TASK: K_TASK,
}

# Объекты приложения, которыми управляет раскладка (их import создаёт, обновляет и удаляет)
MANAGED_KINDS = (K_POU, K_DUT, K_GVL, K_PVL, K_ITF)

# Объявление аксессора свойства POU по умолчанию (CODESYS 3.5.17.30) — без файла <P>.get.decl.st
DEFAULT_ACCESSOR_DECL = 'VAR\nEND_VAR\n'

ACCESSOR_GET = 'Get'
ACCESSOR_SET = 'Set'


def kind_of_type(type_guid):
    # type: (str) -> str
    """Вид объекта по GUID типа (строка в любом регистре, с фигурными скобками или без)."""
    key = '%s' % (type_guid,)
    key = key.strip().strip('{}').lower()
    return _KIND_OF_TYPE.get(key, K_OTHER)


def kind_of(obj):
    # type: (Any) -> str
    """Вид объекта CODESYS (folder, pou, dut, ...) по obj.type."""
    try:
        return kind_of_type('%s' % (obj.type,))
    except Exception:
        return K_OTHER


def squash_ws(text):
    # type: (Optional[str]) -> str
    return ' '.join((text or '').split())


def is_default_accessor_decl(text):
    # type: (Optional[str]) -> bool
    return squash_ws(text) == squash_ws(DEFAULT_ACCESSOR_DECL)


# ---------------------------------------------------------------------------
# Значения ПЛК из read_value (строки: 'TRUE', 'UINT#7', "'text'", '16#FF', '1.5')
# ---------------------------------------------------------------------------

_INT_TYPES = (
    'SINT', 'INT', 'DINT', 'LINT', 'USINT', 'UINT', 'UDINT', 'ULINT',
    'BYTE', 'WORD', 'DWORD', 'LWORD',
)  # fmt: skip
_REAL_TYPES = ('REAL', 'LREAL')
_ESCAPES = {'$': '$', "'": "'", '"': '"', 'L': '\n', 'N': '\n', 'P': '\f', 'R': '\r', 'T': '\t'}
_HEX_RE = re.compile(r'[0-9A-Fa-f]+\Z')


def unescape_plc_string(body, wide=False):
    # type: (str, bool) -> str
    """Снимает экранирование $ в литерале STRING/WSTRING (без кавычек)."""
    out = []
    i = 0
    n = len(body)
    width = 4 if wide else 2
    while i < n:
        char = body[i]
        if char != '$' or i + 1 >= n:
            out.append(char)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt.upper() in _ESCAPES:
            out.append(_ESCAPES[nxt.upper()])
            i += 2
            continue
        code = body[i + 1:i + 1 + width]
        if len(code) == width and _HEX_RE.match(code):
            out.append(chr(int(code, 16)) if sys.version_info[0] >= 3 else unichr(int(code, 16)))  # type: ignore  # noqa: F821
            i += 1 + width
            continue
        out.append(char)
        i += 1
    return ''.join(out)


def _parse_int(text):
    # type: (str) -> Optional[int]
    raw = text.replace('_', '')
    base = 10
    if '#' in raw:
        base_text, _, raw = raw.partition('#')
        try:
            base = int(base_text)
        except ValueError:
            return None
    try:
        return int(raw, base)
    except ValueError:
        return None


def plc_value(text):
    # type: (Optional[str]) -> Any
    """Переводит строку из read_value в значение Python.

    'TRUE'/'FALSE' -> bool, 'UINT#7' и '16#FF' -> int, 'REAL#1.5' и '1.5' -> float,
    "'abc'" -> 'abc' (с разбором $-экранирования), прочее (TIME#..., перечисления) — строка как есть.
    """
    if text is None:
        return None
    value = ('%s' % (text,)).strip()
    if value == 'TRUE':
        return True
    if value == 'FALSE':
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return unescape_plc_string(value[1:-1], wide=value[0] == '"')
    prefix = None
    rest = value
    if '#' in value:
        head, _, tail = value.partition('#')
        if head.upper() in _INT_TYPES or head.upper() in _REAL_TYPES:
            prefix = head.upper()
            rest = tail
    if prefix in _REAL_TYPES:
        try:
            return float(rest.replace('_', ''))
        except ValueError:
            return value
    number = _parse_int(rest)
    if number is not None:
        return number
    if prefix is None:
        try:
            return float(value)
        except ValueError:
            pass
    return value


def interval_ms(value, unit=None):
    # type: (Any, Optional[str]) -> Optional[float]
    """Интервал задачи в мс из строки CODESYS ('t#20ms', 'T#1s', '20' с единицей unit)."""
    if value is None:
        return None
    text = ('%s' % (value,)).strip().lower().replace('_', '')
    if text.startswith('time#'):
        text = text[5:]
    elif text.startswith('t#'):
        text = text[2:]
    else:
        try:
            number = float(text)
        except ValueError:
            return None
        factor = {'us': 0.001, 'ms': 1.0, 's': 1000.0, 'm': 60000.0, None: 1.0, '': 1.0}
        return number * factor.get((unit or '').strip().lower(), 1.0)
    total = 0.0
    matched = False
    factors = {'d': 86400000.0, 'h': 3600000.0, 'm': 60000.0, 's': 1000.0, 'ms': 1.0, 'us': 0.001, 'ns': 0.000001}
    for number, suffix in re.findall(r'([0-9]+(?:\.[0-9]+)?)(ms|us|ns|d|h|m|s)', text):
        total += float(number) * factors[suffix]
        matched = True
    return total if matched else None


# ---------------------------------------------------------------------------
# Файлы: JSON, журнал
# ---------------------------------------------------------------------------


def _text(value):
    # type: (Any) -> str
    if isinstance(value, bytes) and not isinstance(value, STRING_TYPES):
        return value.decode('utf-8', 'replace')
    return '%s' % (value,)


def ensure_dir(path):
    # type: (str) -> None
    if path and not os.path.isdir(path):
        os.makedirs(path)


def read_json(path):
    # type: (str) -> Any
    with io.open(path, 'r', encoding='utf-8') as handle:
        return json.loads(handle.read())


def write_json(path, data):
    # type: (str, Any) -> None
    """Пишет JSON в UTF-8 (LF, отступ 2, ключи по алфавиту) — детерминированно."""
    ensure_dir(os.path.dirname(path))
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, separators=(',', ': '))
    with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(_text(text) + '\n')


class Log(object):
    """Журнал шага: строки с меткой времени в UTF-8; предупреждения и ошибки копятся в списках."""

    def __init__(self, path=None):
        # type: (Optional[str]) -> None
        self.path = path
        self.warnings = []  # type: List[str]
        self.errors = []  # type: List[str]
        self._handle = None
        if path:
            ensure_dir(os.path.dirname(path))
            self._handle = io.open(path, 'w', encoding='utf-8', newline='\n')

    def _write(self, level, message):
        # type: (str, str) -> None
        line = '%s %-5s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), level, _text(message))
        if self._handle is not None:
            self._handle.write(line + '\n')
            self._handle.flush()

    def info(self, message):
        # type: (str) -> None
        self._write('INFO', message)

    def warn(self, message):
        # type: (str) -> None
        self.warnings.append(_text(message))
        self._write('WARN', message)

    def error(self, message):
        # type: (str) -> None
        self.errors.append(_text(message))
        self._write('ERROR', message)

    def close(self):
        # type: () -> None
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class StepError(Exception):
    """Ожидаемая ошибка шага (понятное сообщение без трассировки).

    code — код выхода процесса: EXIT_FAILED (проверка не прошла, по умолчанию) или
    EXIT_INTERNAL (окружение: рантайм недоступен, нет файла и т.п.).
    """

    def __init__(self, message, code=EXIT_FAILED):
        # type: (str, int) -> None
        Exception.__init__(self, message)
        self.code = code


# ---------------------------------------------------------------------------
# Доступ к CODESYS
# ---------------------------------------------------------------------------


def api():
    # type: () -> Any
    """Модуль scriptengine (только в среде CODESYS)."""
    if _se is None:
        raise StepError('модуль scriptengine недоступен: скрипт нужно запускать в CODESYS (cds.py)')
    return _se


def obj_name(obj):
    # type: (Any) -> str
    try:
        return _text(obj.get_name(False))
    except Exception:
        return '?'


def children(obj):
    # type: (Any) -> List[Any]
    try:
        return list(obj.get_children(False))
    except Exception:
        return []


def has_decl(obj):
    # type: (Any) -> bool
    try:
        return bool(obj.has_textual_declaration)
    except Exception:
        return False


def has_impl(obj):
    # type: (Any) -> bool
    try:
        return bool(obj.has_textual_implementation)
    except Exception:
        return False


def decl_text(obj):
    # type: (Any) -> Optional[str]
    return _text(obj.textual_declaration.text) if has_decl(obj) else None


def impl_text(obj):
    # type: (Any) -> Optional[str]
    return _text(obj.textual_implementation.text) if has_impl(obj) else None


def object_path(obj):
    # type: (Any) -> Optional[str]
    """Путь объекта внутри приложения через точку (FB_Example.Reset) или None."""
    if obj is None:
        return None
    parts = []
    current = obj
    for _ in range(64):
        if current is None:
            break
        try:
            if current.is_root:
                break
        except Exception:
            pass
        try:
            if current.is_application or current.is_device:
                break
        except Exception:
            pass
        parts.append(obj_name(current))
        try:
            current = current.parent
        except Exception:
            break
    parts.reverse()
    return '.'.join(parts) if parts else None


def find_device(proj):
    # type: (Any) -> Any
    for obj in children(proj):
        try:
            if obj.is_device:
                return obj
        except Exception:
            continue
    return None


def find_child(parent, name, kinds=None):
    # type: (Any, str, Optional[Tuple[str, ...]]) -> Any
    """Дочерний объект по имени (без учёта регистра, как в CODESYS) и виду."""
    lower = name.lower()
    for obj in children(parent):
        if obj_name(obj).lower() != lower:
            continue
        if kinds is None or kind_of(obj) in kinds:
            return obj
    return None


# ---------------------------------------------------------------------------
# Контекст задания и диспетчер шагов
# ---------------------------------------------------------------------------

ACTION_MODULES = {
    'import': 'import_sources',
    'export': 'export_sources',
    'build': 'build',
    'test': 'run_tests',
}


def _norm_path(path):
    # type: (str) -> str
    return os.path.normcase(os.path.abspath(path))


def precompile_cache_paths(project_path):
    # type: (str) -> List[str]
    """Файлы кэша предкомпиляции CODESYS рядом с проектом: <имя>_project.precompilecache.

    Имя проверено на 3.5.17.30 (plcrev.project -> plcrev_project.precompilecache); второй
    вариант (все точки имени файла -> '_') — на случай имени проекта с точками.
    """
    folder, name = os.path.split(os.path.abspath(project_path))
    stem = os.path.splitext(name)[0]
    names = [stem + '_project.precompilecache', name.replace('.', '_') + '.precompilecache']
    paths = []  # type: List[str]
    for item in names:
        path = os.path.join(folder, item)
        if path not in paths:
            paths.append(path)
    return paths


def remove_precompile_cache(project_path, log=None):
    # type: (str, Optional[Log]) -> List[str]
    """Удаляет кэш предкомпиляции проекта перед открытием. Возвращает удалённые файлы.

    Зачем (проверено на 3.5.17.30): если проект открыт с кэшем, а у POU с методами или
    свойствами объявление заменено скриптом до того, как дочерние объекты были прочитаны,
    сборка берёт объявление из кэша. Итог — ложные результаты: сломанное объявление
    (неизвестный тип переменной) собирается без ошибок, а исправленное продолжает давать
    старую ошибку. Без кэша CODESYS разбирает тексты заново, а для малых проектов это
    занимает доли секунды.
    """
    removed = []  # type: List[str]
    for path in precompile_cache_paths(project_path):
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            removed.append(path)
            if log is not None:
                log.info('удалён кэш предкомпиляции %s' % path)
        except OSError as exc:
            if log is not None:
                log.warn('не удалось удалить кэш предкомпиляции %s (%s): проект открыт в IDE?' % (path, exc))
    return removed


def project_lock_path(project_path):
    # type: (str) -> str
    return project_path + '.~u'


def read_project_lock(project_path):
    # type: (str) -> Optional[Dict[str, Any]]
    """Файл блокировки CODESYS <проект>.~u — проект открыт в IDE или другом процессе CODESYS.

    Формат (проверено на 3.5.17.30): строки «пользователь», «компьютер», «PID», «время».
    Файл не удерживается открытым, а после аварийного завершения CODESYS остаётся на диске.
    """
    path = project_lock_path(project_path)
    if not os.path.exists(path):
        return None
    try:
        with io.open(path, 'r', encoding='utf-8', errors='replace') as handle:
            lines = [line.strip() for line in handle.read().splitlines()]
    except (IOError, OSError):
        lines = []
    info = {
        'path': path,
        'user': lines[0] if lines else '',
        'host': lines[1] if len(lines) > 1 else '',
        'pid': None,
    }  # type: Dict[str, Any]
    if len(lines) > 2:
        try:
            info['pid'] = int(lines[2])
        except ValueError:
            pass
    return info


def process_name(pid):
    # type: (int) -> Optional[str]
    """Имя работающего процесса по PID или None (через .NET — только в IronPython/CODESYS)."""
    try:
        import System  # type: ignore
    except ImportError:
        return None
    try:
        return _text(System.Diagnostics.Process.GetProcessById(int(pid)).ProcessName)
    except Exception:
        return None


def lock_is_stale(info, host, name_of_pid):
    # type: (Dict[str, Any], str, Any) -> bool
    """Блокировка устарела: оставлена на этом компьютере процессом, которого уже нет.

    Блокировку с другого компьютера (проект на общем диске) или без PID проверить нельзя —
    она считается действующей. Процесс с тем же PID, но не CODESYS — PID переиспользован.
    """
    if not info.get('host') or not host or info['host'].lower() != host.lower():
        return False
    if info.get('pid') is None:
        return False
    name = name_of_pid(info['pid'])
    return not (name and name.lower().startswith('codesys'))


def check_project_lock(project_path, log=None, host=None, name_of_pid=None):
    # type: (str, Optional[Log], Optional[str], Any) -> None
    """Проверка <проект>.~u перед открытием: устаревшая блокировка удаляется, действующая — отказ.

    Проверено на 3.5.17.30: при живой или устаревшей блокировке projects.open в noUI сразу
    падает с «Выбранный проект используется '<user>' на '<host>'», а устаревшая блокировка
    остаётся после остановки CODESYS по таймауту (taskkill) — без этой проверки все
    следующие import/build/test падали бы, пока файл не удалят вручную.
    """
    info = read_project_lock(project_path)
    if info is None:
        return
    if host is None:
        host = os.environ.get('COMPUTERNAME', '')
    if lock_is_stale(info, host, name_of_pid or process_name):
        os.remove(info['path'])
        if log is not None:
            log.warn('удалён устаревший файл блокировки %s (процесс CODESYS PID %s не работает)'
                     % (info['path'], info['pid']))
        return
    raise StepError(
        'проект %s открыт в другом экземпляре CODESYS (пользователь %s, компьютер %s, PID %s): закройте его '
        'в IDE и повторите; если CODESYS там не запущен — удалите файл блокировки %s'
        % (project_path, info.get('user') or '?', info.get('host') or '?', info.get('pid') or '?', info['path']),
        EXIT_INTERNAL,
    )


def load_object_tree(proj):
    # type: (Any) -> int
    """Читает всё дерево объектов проекта (get_children рекурсивно) сразу после открытия.

    Вторая защита от той же ошибки CODESYS: если дочерние объекты POU прочитаны до замены
    объявления, сборка видит новый текст даже при наличии кэша (проверено на 3.5.17.30).
    """
    try:
        return len(list(proj.get_children(True)))
    except Exception:
        return -1


class Context(object):
    """Общий контекст шагов одного процесса CODESYS: задание и открытые проекты."""

    def __init__(self, job):
        # type: (Dict[str, Any]) -> None
        self.job = job
        self._projects = {}  # type: Dict[str, Any]

    def cached_project(self, path):
        # type: (str) -> Any
        return self._projects.get(_norm_path(path))

    def open_project(self, path, create=False, device=None, clean=False, reopen=False, log=None):
        # type: (str, bool, Optional[Dict[str, Any]], bool, bool, Optional[Log]) -> Any
        """Открывает проект (или создаёт с устройством). Возвращает (проект, создан_ли)."""
        key = _norm_path(path)
        cached = self._projects.get(key)
        if cached is not None and (clean or reopen):
            try:
                cached.close()
            except Exception:
                pass
            del self._projects[key]
            cached = None
        if cached is not None:
            return cached, False
        se = api()
        if os.path.exists(path):
            check_project_lock(path, log)
        if clean and os.path.exists(path):
            os.remove(path)
            if log is not None:
                log.info('удалён проект %s (--clean)' % path)
        remove_precompile_cache(path, log)
        if os.path.exists(path):
            if log is not None:
                log.info('открытие проекта %s' % path)
            try:
                proj = se.projects.open(path)
            except Exception as exc:
                raise StepError('не удалось открыть проект %s: %s' % (path, squash_ws(_text(exc))), EXIT_INTERNAL)
            load_object_tree(proj)
            created = False
        elif create:
            if device is None:
                raise StepError('для создания проекта нужно описание устройства (config.json: device)')
            ensure_dir(os.path.dirname(os.path.abspath(path)))
            if log is not None:
                log.info('создание проекта %s' % path)
            proj = se.projects.create(path, True)
            device_id = se.DeviceID(int(device['type']), _text(device['id']), _text(device['version']))
            proj.add(_text(device.get('name') or 'Device'), device_id)
            created = True
        else:
            raise StepError('проект %s не найден: сначала выполните cds.py import' % path)
        self._projects[key] = proj
        return proj, created

    def close(self):
        # type: () -> None
        for proj in list(self._projects.values()):
            try:
                proj.close()
            except Exception:
                pass
        self._projects = {}


def application(proj):
    # type: (Any) -> Any
    app = proj.active_application
    if app is None:
        raise StepError('в проекте нет активного приложения (Application)')
    return app


def load_job():
    # type: () -> Dict[str, Any]
    path = os.environ.get(JOB_ENV)
    if not path:
        raise StepError('не задана переменная окружения %s (путь к файлу задания); запускайте через cds.py' % JOB_ENV)
    if not os.path.isfile(path):
        raise StepError('файл задания %s не найден' % path)
    job = read_json(path)  # type: Dict[str, Any]
    job['_path'] = path
    return job


def _result_path(job):
    # type: (Optional[Dict[str, Any]]) -> Optional[str]
    if job and job.get('result'):
        return _text(job['result'])
    path = os.environ.get(JOB_ENV)
    if path:
        return os.path.splitext(path)[0] + '.result.json'
    return None


def run_main(entry_action):
    # type: (str) -> None
    """Точка входа скрипта: выполняет шаги задания, пишет результат, завершает процесс с кодом."""
    started = time.time()
    result = {
        'entry': entry_action,
        'started': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'steps': [],
        'python': sys.version,
    }  # type: Dict[str, Any]
    code = EXIT_INTERNAL
    job = None
    ctx = None
    try:
        job = load_job()
        ctx = Context(job)
        steps = job.get('steps') or [dict(job, action=entry_action)]
        code = EXIT_OK
        for step in steps:
            action = step.get('action', entry_action)
            step_result = {'action': action, 'ok': False}  # type: Dict[str, Any]
            step_started = time.time()
            try:
                module = __import__(ACTION_MODULES[action])
                step_result.update(module.run(ctx, step) or {})
            except StepError as exc:
                step_result['ok'] = False
                step_result['error'] = _text(exc)
                step_result['exit_code'] = getattr(exc, 'code', EXIT_FAILED)
            except Exception:
                step_result['ok'] = False
                step_result['error'] = _text(traceback.format_exc())
                step_result['internal'] = True
            step_result['duration_s'] = round(time.time() - step_started, 1)
            result['steps'].append(step_result)
            if not step_result.get('ok'):
                code = EXIT_INTERNAL if step_result.get('internal') else step_result.get('exit_code', EXIT_FAILED)
                break
    except StepError as exc:
        result['error'] = _text(exc)
        code = EXIT_INTERNAL
    except Exception:
        result['error'] = _text(traceback.format_exc())
        code = EXIT_INTERNAL
    finally:
        if ctx is not None:
            ctx.close()
        result['ok'] = code == EXIT_OK
        result['exit_code'] = code
        result['duration_s'] = round(time.time() - started, 1)
        path = _result_path(job)
        if path:
            try:
                write_json(path, result)
            except Exception:
                pass
    sys.exit(code)
