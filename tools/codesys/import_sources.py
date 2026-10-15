# -*- coding: utf-8 -*-
"""Импорт раскладки src/ (и tests/) в проект CODESYS — скрипт CODESYS Scripting (IronPython 2.7).

Запуск — только через tools/codesys/cds.py (python tools/codesys/cds.py import [--tests]):
он пишет файл задания (путь — в переменной окружения CDS_JOB) и запускает
CODESYS.exe --noUI --runscript=<этот файл>.

Что делает шаг import:
1. Читает src/ (и tests/ с флагом) через stlayout.scan; ошибки раскладки — отказ без изменений.
2. Открывает проект или создаёт его: устройство (config.json: device), приложение.
3. Идемпотентно создаёт или обновляет DUT, GVL, интерфейсы (методы, свойства; объявление
   интерфейса по умолчанию 'EXTENDS __System.IQueryInterface' заменяется текстом файла), POU с
   методами и свойствами (аксессоры Get/Set — ровно как в раскладке, лишние удаляются), папки
   (подкаталоги src/ — папки проекта, tests/ — папка Tests). Текст меняется, только если он
   отличается: повторный импорт без правок ничего не меняет. Имя, отличающееся от раскладки
   только регистром (объект, метод, свойство, папка), переименовывается.
   Перед открытием проекта удаляется кэш предкомпиляции (cds_common.remove_precompile_cache):
   с ним сборка после замены объявления POU с методами могла видеть старый текст.
4. Удаляет объекты управляемых видов (POU, DUT, GVL, интерфейсы, методы и свойства POU),
   которых больше нет в исходниках, и опустевшие папки — с записью в отчёт.
5. Конфигурация задач и ссылки на библиотеки — по режиму (normal/test) из config.json.
6. Сохраняет проект. Журнал — job["log"] (build/import.log), отчёт — job["report"] (build/import.json).
"""
from __future__ import absolute_import, print_function, unicode_literals

import os

import cds_common as C
import stlayout as L

MYPY = False
if MYPY:  # pragma: no cover
    from typing import Any, Dict, List, Optional, Tuple  # noqa: F401


class Report(object):
    """Изменения проекта за шаг импорта."""

    def __init__(self, log):
        # type: (C.Log) -> None
        self.log = log
        self.created = []  # type: List[str]
        self.updated = []  # type: List[str]
        self.removed = []  # type: List[str]
        self.moved = []  # type: List[str]
        self.unchanged = 0

    def add(self, bucket, what):
        # type: (str, str) -> None
        getattr(self, bucket).append(what)
        self.log.info('%s: %s' % (bucket, what))

    @property
    def changes(self):
        # type: () -> int
        return len(self.created) + len(self.updated) + len(self.removed) + len(self.moved)

    def as_dict(self):
        # type: () -> Dict[str, Any]
        return {
            'created': self.created,
            'updated': self.updated,
            'removed': self.removed,
            'moved': self.moved,
            'unchanged': self.unchanged,
            'changes': self.changes,
        }


# ---------------------------------------------------------------------------
# Чтение раскладки
# ---------------------------------------------------------------------------


def scan_sources(repo_root, with_tests, tests_folder, log):
    # type: (str, bool, str, C.Log) -> List[Tuple[L.StObject, List[str]]]
    """Объекты раскладки с путём папки в проекте: [(объект, [папка, ...])]."""
    wanted = []  # type: List[Tuple[L.StObject, List[str]]]
    subdirs = [('src', [])]  # type: List[Tuple[str, List[str]]]
    if with_tests:
        subdirs.append(('tests', [tests_folder]))
    failed = False
    for subdir, prefix in subdirs:
        result = L.scan(repo_root, subdir)
        for issue in result.issues:
            text = issue.format()
            if issue.severity == 'error':
                log.error(text)
                failed = True
            else:
                log.warn(text)
        for obj in result.objects:
            if subdir == 'src' and obj.folder and obj.folder[0].lower() == tests_folder.lower():
                log.error(
                    '%s: папка верхнего уровня %s в src/ зарезервирована под tests/' % (obj.main_path(), tests_folder)
                )
                failed = True
            wanted.append((obj, prefix + list(obj.folder)))
        log.info('%s/: объектов %d, замечаний %d' % (subdir, len(result.objects), len(result.issues)))
    seen = {}  # type: Dict[str, L.StObject]
    for obj, _ in wanted:
        key = obj.name.lower()
        if key in seen:
            log.error(
                "объект '%s' объявлен дважды: %s и %s (имена CODESYS без учёта регистра)"
                % (obj.name, seen[key].main_path(), obj.main_path())
            )
            failed = True
        seen[key] = obj
    if failed:
        raise C.StepError('ошибки раскладки исходников (см. %s) — проект не изменён' % (log.path or 'журнал'))
    return wanted


# ---------------------------------------------------------------------------
# Синхронизация объектов
# ---------------------------------------------------------------------------


def _set_text(doc, text):
    # type: (Any, str) -> bool
    """Заменяет текст документа, если он отличается. True — текст изменён."""
    current = C._text(doc.text)
    if current == text:
        return False
    doc.replace(text)
    return True


def _header_keyword(text):
    # type: (Optional[str]) -> Optional[str]
    hdr = L.parse_header(text or '')
    return hdr.keyword if hdr is not None else None


class Syncer(object):
    def __init__(self, app, log, report):
        # type: (Any, C.Log, Report) -> None
        self.app = app
        self.log = log
        self.report = report
        self.se = C.api()
        self._folders = {}  # type: Dict[Tuple[str, ...], Any]
        self._renamed = set()  # type: set

    # --- обход проекта ---------------------------------------------------

    def existing_objects(self):
        # type: () -> Dict[str, Tuple[Any, str, List[str]]]
        """Объекты управляемых видов в приложении: имя (нижний регистр) -> (объект, вид, папка)."""
        found = {}  # type: Dict[str, Tuple[Any, str, List[str]]]

        def walk(parent, path):
            # type: (Any, List[str]) -> None
            for obj in C.children(parent):
                kind = C.kind_of(obj)
                if kind == C.K_FOLDER:
                    walk(obj, path + [C.obj_name(obj)])
                elif kind in C.MANAGED_KINDS:
                    found[C.obj_name(obj).lower()] = (obj, kind, list(path))

        walk(self.app, [])
        return found

    def folder(self, path):
        # type: (List[str]) -> Any
        """Папка проекта по пути (создаётся при необходимости); [] — само приложение."""
        key = tuple(p.lower() for p in path)
        if not path:
            return self.app
        if key in self._folders:
            return self._folders[key]
        parent = self.folder(path[:-1])
        name = path[-1]
        found = C.find_child(parent, name, (C.K_FOLDER,))
        if found is None:
            parent.create_folder(name)
            found = C.find_child(parent, name, (C.K_FOLDER,))
            if found is None:
                raise C.StepError("не удалось создать папку '%s'" % '/'.join(path))
            self.report.add('created', 'FOLDER %s' % '/'.join(path))
        elif C.obj_name(found) != name:
            # имена CODESYS без учёта регистра: подкаталог переименован только регистром
            old = C.obj_name(found)
            found.rename(name)
            self.report.add('updated', 'FOLDER %s (имя: %s)' % ('/'.join(path), old))
        self._folders[key] = found
        return found

    # --- регистр имён ------------------------------------------------------

    def fix_name_case(self, wanted, existing):
        # type: (List[Tuple[L.StObject, List[str]]], Dict[str, Tuple[Any, str, List[str]]]) -> None
        """Регистр имён объектов, методов и свойств — как в раскладке (до синхронизации текстов).

        Поиск в проекте идёт без учёта регистра (как в CODESYS), поэтому переименование
        файла только регистром (FB_example -> FB_Example) без этого шага не дошло бы до
        проекта, и export вернул бы старое имя файла.
        """
        for obj, _ in wanted:
            entry = existing.get(obj.name.lower())
            if entry is None or entry[1] != obj.kind:
                continue  # новый объект или будет пересоздан
            cur = entry[0]
            old = C.obj_name(cur)
            if old != obj.name:
                cur.rename(obj.name)
                self._renamed.add(obj.name.lower())
                self.report.add('updated', '%s %s (имя: %s)' % (obj.kind.upper(), obj.name, old))
            if obj.kind not in (L.KIND_POU, L.KIND_ITF):
                continue
            names = {}  # type: Dict[Tuple[str, str], str]
            for method in obj.methods:
                names[(C.K_METHOD, method.name.lower())] = method.name
            for prop in obj.properties:
                names[(C.K_PROPERTY, prop.name.lower())] = prop.name
            for child in C.children(cur):
                kind = C.kind_of(child)
                old_member = C.obj_name(child)
                new_member = names.get((kind, old_member.lower()))
                if new_member is not None and new_member != old_member:
                    child.rename(new_member)
                    self._renamed.add(obj.name.lower())
                    self.report.add('updated', '%s %s.%s (имя: %s)' % (kind.upper(), obj.name, new_member, old_member))

    # --- создание --------------------------------------------------------

    def create(self, obj, parent):
        # type: (L.StObject, Any) -> Any
        se = self.se
        hdr = obj.header
        if obj.kind == L.KIND_DUT:
            kind = hdr.type_kind if hdr is not None else None
            if kind == 'alias':
                td = L.parse_type_decl(obj.decl)
                base = (td.alias_type if td is not None else None) or 'INT'
                return parent.create_dut(obj.name, se.DutType.Alias, base)
            dut_type = {
                'struct': se.DutType.Structure,
                'enum': se.DutType.Enumeration,
                'union': se.DutType.Union,
            }.get(kind, se.DutType.Structure)
            return parent.create_dut(obj.name, dut_type)
        if obj.kind == L.KIND_GVL:
            return parent.create_gvl(obj.name)
        if obj.kind == L.KIND_PVL:
            # В SP17 нет create_persistentvars (проверено: AttributeError) — ручной шаг
            return None
        if obj.kind == L.KIND_ITF:
            return parent.create_interface(obj.name)
        if obj.kind == L.KIND_POU:
            if obj.pou_kind == L.POU_PROGRAM:
                return parent.create_pou(name=obj.name, type=se.PouType.Program)
            if obj.pou_kind == L.POU_FUNCTION:
                return_type = (hdr.return_type if hdr is not None else None) or 'INT'
                return parent.create_pou(name=obj.name, type=se.PouType.Function, return_type=return_type)
            return parent.create_pou(name=obj.name, type=se.PouType.FunctionBlock)
        raise C.StepError('неизвестный вид объекта %s' % obj.kind)

    def needs_recreate(self, cur, cur_kind, obj):
        # type: (Any, str, L.StObject) -> Optional[str]
        """Причина пересоздания существующего объекта или None."""
        if cur_kind != obj.kind:
            return 'вид %s -> %s' % (cur_kind, obj.kind)
        if obj.kind == L.KIND_POU:
            keyword = _header_keyword(C.decl_text(cur))
            wanted = obj.header.keyword if obj.header is not None else None
            if keyword != wanted:
                return 'тип POU %s -> %s' % (keyword, wanted)
            if not C.has_impl(cur):
                return 'реализация не на ST'
        if not C.has_decl(cur):
            return 'нет текстового объявления'
        return None

    def sync_object(self, obj, folder_path, existing):
        # type: (L.StObject, List[str], Dict[str, Tuple[Any, str, List[str]]]) -> None
        label = '%s %s' % (obj.kind.upper(), obj.name)
        parent = self.folder(folder_path)
        entry = existing.get(obj.name.lower())
        cur = None
        if entry is not None:
            cur, cur_kind, cur_path = entry
            reason = self.needs_recreate(cur, cur_kind, obj)
            if reason is not None:
                cur.remove()
                self.report.add('removed', '%s (пересоздание: %s)' % (label, reason))
                cur = None
            elif [p.lower() for p in cur_path] != [p.lower() for p in folder_path]:
                cur.move(parent, -1)
                where = (label, '/'.join(cur_path) or '.', '/'.join(folder_path) or '.')
                self.report.add('moved', '%s: %s -> %s' % where)
        if cur is None:
            cur = self.create(obj, parent)
            if cur is None:
                self.log.warn(
                    '%s: Persistent Variables скриптом SP17 не создаются — создайте объект %s вручную '
                    '(docs/MANUAL_STEPS.md), при следующем импорте текст обновится' % (obj.main_path(), obj.name)
                )
                return
            self.report.add('created', label)
            created = True
        else:
            created = False
        changed = []  # type: List[str]
        if _set_text(cur.textual_declaration, obj.decl):
            changed.append('decl')
        if obj.kind == L.KIND_POU and _set_text(cur.textual_implementation, obj.impl or ''):
            changed.append('impl')
        if obj.kind in (L.KIND_POU, L.KIND_ITF):
            changed.extend(self.sync_members(cur, obj))
        if created:
            return
        if changed:
            self.report.add('updated', '%s (%s)' % (label, ', '.join(changed)))
        elif obj.name.lower() not in self._renamed:
            self.report.unchanged += 1

    # --- методы и свойства ----------------------------------------------

    def sync_members(self, cur, obj):
        # type: (Any, L.StObject) -> List[str]
        is_itf = obj.kind == L.KIND_ITF
        changed = []  # type: List[str]
        members = {}  # type: Dict[str, Tuple[Any, str]]
        for child in C.children(cur):
            kind = C.kind_of(child)
            if kind in (C.K_METHOD, C.K_PROPERTY):
                members[C.obj_name(child).lower()] = (child, kind)
            elif kind in (C.K_ACTION, C.K_TRANSITION):
                self.log.warn(
                    '%s.%s: %s не входит в раскладку — остаётся в проекте, но не экспортируется'
                    % (obj.name, C.obj_name(child), kind)
                )
        wanted = {}  # type: Dict[str, str]
        for method in obj.methods:
            wanted[method.name.lower()] = C.K_METHOD
        for prop in obj.properties:
            wanted[prop.name.lower()] = C.K_PROPERTY
        for key, (child, kind) in sorted(members.items()):
            if wanted.get(key) != kind:
                child.remove()
                del members[key]
                self.report.add('removed', '%s %s.%s' % (kind.upper(), obj.name, C.obj_name(child)))
                changed.append('-' + C.obj_name(child))
        for method in obj.methods:
            entry = members.get(method.name.lower())
            label = 'METHOD %s.%s' % (obj.name, method.name)
            if entry is None:
                hdr = method.header
                return_type = hdr.return_type if hdr is not None else None
                child = cur.create_method(method.name, return_type)
                self.report.add('created', label)
                changed.append('+' + method.name)
            else:
                child = entry[0]
            parts = []  # type: List[str]
            if _set_text(child.textual_declaration, method.decl):
                parts.append('decl')
            if not is_itf and C.has_impl(child) and _set_text(child.textual_implementation, method.impl or ''):
                parts.append('impl')
            if parts and entry is not None:
                self.report.add('updated', '%s (%s)' % (label, ', '.join(parts)))
                changed.append(method.name)
        for prop in obj.properties:
            entry = members.get(prop.name.lower())
            child = self.sync_property(cur, obj, prop, entry[0] if entry is not None else None, is_itf)
            if child is not None:
                changed.append(prop.name)
        return changed

    def sync_property(self, cur, obj, prop, child, is_itf):
        # type: (Any, L.StObject, L.StProperty, Any, bool) -> Any
        """Создаёт или обновляет свойство; возвращает объект, если что-то изменилось."""
        label = 'PROPERTY %s.%s' % (obj.name, prop.name)
        wanted = {ACC_GET: prop.has_get, ACC_SET: prop.has_set}
        if child is not None:
            accessors = dict((C.obj_name(a), a) for a in C.children(child) if C.kind_of(a) == C.K_ACCESSOR)
            missing = [name for name, need in sorted(wanted.items()) if need and name not in accessors]
            if missing:
                # отдельно аксессор скриптом не создаётся: пересоздаём свойство (появятся оба)
                child.remove()
                self.report.add('removed', '%s (пересоздание: нет аксессора %s)' % (label, ', '.join(missing)))
                child = None
        created = child is None
        if created:
            hdr = prop.header
            return_type = (hdr.return_type if hdr is not None else None) or 'INT'
            child = cur.create_property(prop.name, return_type)
            self.report.add('created', label)
        parts = []  # type: List[str]
        if _set_text(child.textual_declaration, prop.decl):
            parts.append('decl')
        accessors = dict((C.obj_name(a), a) for a in C.children(child) if C.kind_of(a) == C.K_ACCESSOR)
        for name in (ACC_GET, ACC_SET):
            acc = accessors.get(name)
            if acc is None:
                continue
            if not wanted[name]:
                acc.remove()
                if created:  # create_property создаёт оба аксессора — лишний убираем без записи в отчёт
                    self.log.info('ACCESSOR %s.%s.%s удалён (нет в раскладке)' % (obj.name, prop.name, name))
                else:
                    self.report.add('removed', 'ACCESSOR %s.%s.%s' % (obj.name, prop.name, name))
                    parts.append('-' + name)
                continue
            if name == ACC_GET:
                acc_decl, acc_impl = prop.get_decl, prop.get_impl
            else:
                acc_decl, acc_impl = prop.set_decl, prop.set_impl
            if acc_decl is None:
                acc_decl = '' if is_itf else C.DEFAULT_ACCESSOR_DECL
            if C.has_decl(acc) and _set_text(acc.textual_declaration, acc_decl):
                parts.append(name + '.decl')
            if not is_itf and C.has_impl(acc) and _set_text(acc.textual_implementation, acc_impl or ''):
                parts.append(name + '.impl')
        if parts and not created:
            self.report.add('updated', '%s (%s)' % (label, ', '.join(parts)))
        return child if (parts or created) else None

    # --- удаление лишнего ---------------------------------------------------

    def remove_stale(self, wanted_names, existing):
        # type: (Dict[str, L.StObject], Dict[str, Tuple[Any, str, List[str]]]) -> None
        for key in sorted(existing):
            if key in wanted_names:
                continue
            cur, kind, path = existing[key]
            where = '/'.join(path) or '.'
            name = C.obj_name(cur)
            cur.remove()
            self.report.add('removed', '%s %s (папка %s: нет в исходниках)' % (kind.upper(), name, where))
            del existing[key]

    def remove_empty_folders(self, wanted_paths):
        # type: (set) -> None
        def walk(parent, path):
            # type: (Any, List[str]) -> None
            for obj in C.children(parent):
                if C.kind_of(obj) != C.K_FOLDER:
                    continue
                sub = path + [C.obj_name(obj)]
                walk(obj, sub)
                if C.children(obj):
                    continue
                if tuple(p.lower() for p in sub) in wanted_paths:
                    continue
                obj.remove()
                self._folders.pop(tuple(p.lower() for p in sub), None)
                self.report.add('removed', 'FOLDER %s (пустая)' % '/'.join(sub))

        walk(self.app, [])


ACC_GET = C.ACCESSOR_GET
ACC_SET = C.ACCESSOR_SET


# ---------------------------------------------------------------------------
# Задачи и библиотеки
# ---------------------------------------------------------------------------


def _task_pou_names(task):
    # type: (Any) -> List[str]
    names = []  # type: List[str]
    pous = task.pous
    for i in range(len(pous)):
        item = pous[i]
        if isinstance(item, C.STRING_TYPES):
            names.append(C._text(item))
        else:
            names.append(C._text(item[0]))
    return names


def sync_tasks(app, tasks_cfg, log, report):
    # type: (Any, List[Dict[str, Any]], C.Log, Report) -> None
    tc = None
    for obj in C.children(app):
        try:
            if obj.is_task_configuration:
                tc = obj
                break
        except Exception:
            continue
    if tc is None:
        tc = app.create_task_configuration()
        report.add('created', 'TASK_CONFIGURATION')
    existing = {}  # type: Dict[str, Any]
    for obj in C.children(tc):
        try:
            if obj.is_task:
                existing[C.obj_name(obj).lower()] = obj
        except Exception:
            continue
    wanted = set()
    for cfg in tasks_cfg:
        name = cfg['name']
        wanted.add(name.lower())
        task = existing.get(name.lower())
        created = task is None
        if created:
            task = tc.create_task(name)
            report.add('created', 'TASK %s' % name)
        parts = []  # type: List[str]
        ms = float(cfg['interval_ms'])
        try:
            unit = task.interval_unit
        except Exception:
            unit = None
        if C.interval_ms(task.interval, unit) != ms:
            task.interval = '%d' % int(ms) if ms == int(ms) else '%s' % ms
            try:
                task.interval_unit = 'ms'
            except Exception:
                pass
            parts.append('interval=%sms' % cfg['interval_ms'])
        if cfg.get('priority') is not None and C._text(task.priority) != '%s' % cfg['priority']:
            task.priority = '%s' % cfg['priority']
            parts.append('priority=%s' % cfg['priority'])
        want_pous = [C._text(p) for p in cfg.get('pous', [])]
        if [p.lower() for p in _task_pou_names(task)] != [p.lower() for p in want_pous]:
            while len(task.pous):
                task.pous.remove(0)
            for pou in want_pous:
                task.pous.add(pou)
            parts.append('pous=%s' % ','.join(want_pous))
        if parts and not created:
            report.add('updated', 'TASK %s (%s)' % (name, '; '.join(parts)))
        elif parts:
            log.info('TASK %s: %s' % (name, '; '.join(parts)))
        else:
            report.unchanged += 1
    for key in sorted(existing):
        if key not in wanted:
            existing[key].remove()
            report.add('removed', 'TASK %s (нет в config.json для этого режима)' % C.obj_name(existing[key]))


def _lib_matches(ml, spec):
    # type: (Any, Dict[str, Any]) -> bool
    namespace = spec.get('namespace')
    if namespace and C._text(ml.default_namespace or '').lower() != namespace.lower():
        return False
    version = spec.get('version')
    if version and C._text(ml.version) != version:
        return False
    title = spec.get('title_contains')
    return not (title and title.lower() not in C._text(ml.title or '').lower())


def _ref_matches(ref, spec):
    # type: (Any, Dict[str, Any]) -> bool
    try:
        if ref.is_managed:
            return _lib_matches(ref.managed_library, spec)
    except Exception:
        pass
    namespace = spec.get('namespace')
    try:
        return bool(namespace) and C._text(ref.namespace or '').lower() == namespace.lower()
    except Exception:
        return False


def find_library(spec, log):
    # type: (Dict[str, Any], C.Log) -> Any
    """Библиотека в репозиториях CODESYS; при отсутствии — установка из spec['install_from']."""
    se = C.api()
    lm = se.librarymanager
    best = None
    for ml in lm.get_all_libraries(True):
        if _lib_matches(ml, spec):
            if best is None or C._text(ml.version) > C._text(best.version):
                best = ml
    if best is not None:
        return best
    path = spec.get('install_from')
    label = spec.get('label') or spec.get('namespace')
    if not path:
        raise C.StepError(
            'библиотека %s %s не установлена в репозитории CODESYS; задайте путь к .library в %s '
            '(docs/MANUAL_STEPS.md)' % (label, spec.get('version') or '', spec.get('install_env') or 'config.json')
        )
    if not os.path.isfile(path):
        raise C.StepError('файл библиотеки %s не найден: %s' % (label, path))
    log.info('установка библиотеки %s из %s в репозиторий %s' % (label, path, C._text(lm.repositories[0].name)))
    best = lm.install_library(path, lm.repositories[0], True)
    if not _lib_matches(best, spec):
        raise C.StepError(
            'установленная библиотека %s (%s, ns %s) не соответствует config.json'
            % (C._text(best.displayname), C._text(best.version), C._text(best.default_namespace))
        )
    return best


def sync_libraries(app, wanted_specs, other_specs, log, report):
    # type: (Any, List[Dict[str, Any]], List[Dict[str, Any]], C.Log, Report) -> None
    libman = None
    for obj in C.children(app):
        try:
            if obj.is_libman:
                libman = obj
                break
        except Exception:
            continue
    if libman is None:
        libman = app.get_library_manager()
    refs = list(libman.references)
    for spec in wanted_specs:
        label = spec.get('label') or spec.get('namespace')
        if any(_ref_matches(ref, spec) for ref in refs):
            report.unchanged += 1
            continue
        ml = find_library(spec, log)
        libman.add_library(ml)
        report.add('created', 'LIBRARY %s (%s)' % (label, C._text(ml.displayname)))
    for spec in other_specs:
        if any(_lib_matches_spec_equal(spec, w) for w in wanted_specs):
            continue
        for ref in refs:
            if _ref_matches(ref, spec):
                name = C._text(ref.name)
                libman.remove_library(name)
                report.add('removed', 'LIBRARY %s (не нужна в этом режиме)' % name)


def _lib_matches_spec_equal(a, b):
    # type: (Dict[str, Any], Dict[str, Any]) -> bool
    return (a.get('namespace') or '').lower() == (b.get('namespace') or '').lower()


# ---------------------------------------------------------------------------
# Шаг import
# ---------------------------------------------------------------------------


def check_device(proj, device, log):
    # type: (Any, Dict[str, Any], C.Log) -> None
    dev = C.find_device(proj)
    if dev is None:
        raise C.StepError('в проекте нет устройства; пересоздайте проект: cds.py import --clean')
    ident = dev.get_device_identification()
    have = (int(ident.type), C._text(ident.id), C._text(ident.version))
    want = (int(device['type']), C._text(device['id']), C._text(device['version']))
    if have != want:
        raise C.StepError(
            'устройство проекта %s не совпадает с config.json %s; пересоздайте проект: cds.py import --clean'
            % (have, want)
        )


def run(ctx, step):
    # type: (C.Context, Dict[str, Any]) -> Dict[str, Any]
    log = C.Log(step.get('log'))
    report = Report(log)
    try:
        repo_root = step['repo_root']
        with_tests = bool(step.get('with_tests'))
        tests_folder = step.get('tests_folder') or 'Tests'
        log.info('импорт: репозиторий %s, режим %s, тесты %s' % (repo_root, step.get('mode'), with_tests))
        wanted = scan_sources(repo_root, with_tests, tests_folder, log)
        proj, created = ctx.open_project(
            step['project'],
            create=True,
            device=step['device'],
            clean=bool(step.get('clean')),
            reopen=bool(step.get('reopen')),
            log=log,
        )
        if created:
            report.add('created', 'PROJECT %s' % step['project'])
        else:
            check_device(proj, step['device'], log)
        app = C.application(proj)
        syncer = Syncer(app, log, report)
        existing = syncer.existing_objects()
        wanted_names = dict((obj.name.lower(), obj) for obj, _ in wanted)
        syncer.remove_stale(wanted_names, existing)
        syncer.fix_name_case(wanted, existing)
        for obj, folder_path in wanted:
            syncer.sync_object(obj, folder_path, existing)
        wanted_paths = set()
        for _, folder_path in wanted:
            for i in range(1, len(folder_path) + 1):
                wanted_paths.add(tuple(p.lower() for p in folder_path[:i]))
        syncer.remove_empty_folders(wanted_paths)
        mode_cfg = step.get('mode_config') or {}
        sync_tasks(app, mode_cfg.get('tasks') or [], log, report)
        sync_libraries(app, mode_cfg.get('libraries') or [], step.get('other_libraries') or [], log, report)
        proj.save()
        log.info(
            'проект сохранён: создано %d, обновлено %d, удалено %d, перемещено %d, без изменений %d'
            % (len(report.created), len(report.updated), len(report.removed), len(report.moved), report.unchanged)
        )
        result = report.as_dict()
        result.update(
            {
                'ok': not log.errors,
                'project': step['project'],
                'mode': step.get('mode'),
                'with_tests': with_tests,
                'objects': len(wanted),
                'warnings': log.warnings,
                'errors': log.errors,
            }
        )
        return result
    except C.StepError as exc:
        log.error('%s' % exc)
        raise
    except Exception as exc:
        log.error('исключение: %s' % exc)
        raise
    finally:
        if step.get('report'):
            data = report.as_dict()
            data.update({'warnings': log.warnings, 'errors': log.errors, 'project': step.get('project')})
            try:
                C.write_json(step['report'], data)
            except Exception:
                pass
        log.close()


if __name__ == '__main__':
    C.run_main('import')
