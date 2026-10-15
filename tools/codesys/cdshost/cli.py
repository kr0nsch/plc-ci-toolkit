"""cds.py — единственная команда для человека: импорт, сборка, тесты и экспорт CODESYS из консоли.

Реализация командной строки tools/codesys/cds.py (сам cds.py — короткий запускатель, совместимый
по синтаксису с IronPython 2.7, как все файлы tools/codesys/*.py). Python 3.11+, только стандартная
библиотека. Скрипты CODESYS (import_sources.py, export_sources.py,
build.py, run_tests.py — IronPython 2.7) запускаются как
    "<CODESYS.exe>" --profile="<профиль>" --noUI --runscript="<скрипт>"
одной строкой с кавычками после '=' (иначе CODESYS не видит профиль), а параметры получают через
файл задания JSON (путь — в переменной окружения CDS_JOB): аргументы скриптов CODESYS ненадёжны.

Подкоманды:
    import [--tests] [--clean] [--force]   исходники -> проект (build/<имя>.project или <имя>-tests.project)
    export [--tests] [--to DIR]            проект -> раскладка src/ (и tests/)
    build [--tests] [--no-import]          импорт и полная сборка, сообщения -> build/build.log, build.json
    test [--restart-runtime]               импорт с тестами, сборка, прогон coUnit в рантайме -> build/test-results.json
    roundtrip                              импорт в чистый проект -> экспорт во временный каталог -> сравнение
    runtime start|stop|status|restart|init пользовательский процесс CODESYS Control Win V3
    junit [--results F] [--out F]          build/test-results.json -> build/junit.xml
    report [--results F] [--doc F]         раздел последнего прогона в docs/TEST_REPORT.md

Коды выхода: 0 — успех; 1 — проверка не прошла (ошибки раскладки или сборки, упавшие тесты, дифф
roundtrip); 2 — неверные аргументы или config.json; 3 — окружение (нет CODESYS, рантайма, файла);
4 — таймаут; 5 — не удалось взять блокировку.

Настройки — tools/codesys/config.json (между репозиториями отличается только он), переменные
окружения — tools/README.md, ручные шаги — docs/MANUAL_STEPS.md.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import difflib
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import stlayout as L

TOOLS_CODESYS = Path(__file__).resolve().parent.parent
DEFAULT_REPO_ROOT = TOOLS_CODESYS.parent.parent
CONFIG_NAME = "config.json"
JOB_ENV = "CDS_JOB"
STATE_FILE = "build/codesys/state.json"
WORK_DIR = "build/codesys"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENV = 3
EXIT_TIMEOUT = 4
EXIT_LOCK = 5

REPORT_BEGIN = "<!-- cds:test-report:begin (раздел обновляет `python tools/codesys/cds.py report`) -->"
REPORT_END = "<!-- cds:test-report:end -->"
RUNTIME_IMAGE = "CODESYSControlService.exe"

Getenv = Callable[[str], "str | None"]


class CdsError(Exception):
    """Ошибка с понятным сообщением и кодом выхода."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Переменные окружения и config.json
# ---------------------------------------------------------------------------


def _registry_env(name: str) -> str | None:
    """Переменная пользователя из HKCU\\Environment (setx не меняет окружение запущенных процессов)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if not isinstance(value, str) or not value:
        return None
    if kind == winreg.REG_EXPAND_SZ:
        value = os.path.expandvars(value)
    return value


def make_getenv(environ: Mapping[str, str] | None = None, registry: bool = True) -> Getenv:
    """Функция чтения переменной: окружение процесса, затем (Windows) реестр пользователя."""
    env = os.environ if environ is None else environ

    def getenv(name: str) -> str | None:
        value = env.get(name)
        if value:
            return value
        if sys.platform == "win32" and environ is None:
            # Windows: имена переменных без учёта регистра
            for key, val in env.items():
                if key.upper() == name.upper() and val:
                    return val
        return _registry_env(name) if registry else None

    return getenv


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_()]*)\}")
_PLACEHOLDER_RE = re.compile(r"\{(repo|repo_name|name|runtime_dir)\}")


class Settings:
    """Разрешённые настройки: config.json + переменные окружения + подстановки."""

    def __init__(self, config: Mapping[str, Any], repo_root: Path, getenv: Getenv) -> None:
        self.config = config
        self.repo_root = repo_root
        self.getenv = getenv
        self._cache: dict[str, Any] = {}

    # --- разрешение значений -------------------------------------------------

    def node(self, dotted: str) -> Any:
        node: Any = self.config
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                raise CdsError(f"config.json: нет ключа {dotted} / missing key {dotted}", EXIT_USAGE)
            node = node[part]
        return node

    def expand(self, text: str) -> str:
        def var(match: re.Match[str]) -> str:
            name = match.group(1)
            value = self.getenv(name)
            if value is None:
                raise CdsError(
                    f"переменная окружения {name} не задана (нужна для {text}) / environment variable {name} not set",
                    EXIT_ENV,
                )
            return value

        def placeholder(match: re.Match[str]) -> str:
            key = match.group(1)
            if key == "repo":
                return str(self.repo_root)
            if key == "repo_name":
                return self.repo_root.name
            if key == "name":
                return str(self.value("project.name"))
            return str(self.path("runtime.dir"))

        result = _VAR_RE.sub(var, text)
        result = _PLACEHOLDER_RE.sub(placeholder, result)
        if result.startswith("~"):
            home = self.getenv("USERPROFILE") or self.getenv("HOME") or str(Path.home())
            result = home + result[1:]
        return result

    def _raw(self, dotted: str) -> tuple[Any, str | None]:
        node = self.node(dotted)
        if isinstance(node, Mapping) and ("env" in node or "default" in node):
            env_name = node.get("env")
            if env_name:
                value = self.getenv(str(env_name))
                if value is not None:
                    return value, str(env_name)
            return node.get("default"), None
        return node, None

    def value(self, dotted: str) -> Any:
        """Значение настройки (строки — с подстановками)."""
        if dotted in self._cache:
            return self._cache[dotted]
        raw, _ = self._raw(dotted)
        if isinstance(raw, str):
            raw = self.expand(raw)
        self._cache[dotted] = raw
        return raw

    def number(self, dotted: str) -> float:
        raw = self.value(dotted)
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise CdsError(f"config.json/окружение: {dotted} = {raw!r} — не число / not a number", EXIT_USAGE) from None

    def path(self, dotted: str, must_exist: bool = False) -> Path:
        """Путь из настройки; список кандидатов — первый существующий; относительный — от корня репозитория."""
        raw, env_name = self._raw(dotted)
        if raw is None:
            raise CdsError(f"не задан путь {dotted} / path {dotted} is not configured", EXIT_ENV)
        candidates = raw if isinstance(raw, list) else [raw]
        resolved: list[Path] = []
        errors: list[str] = []
        for cand in candidates:
            try:
                text = self.expand(str(cand))
            except CdsError as exc:
                errors.append(str(exc))
                continue
            path = Path(text)
            if not path.is_absolute():
                path = self.repo_root / path
            resolved.append(path)
            if path.exists():
                return path
        if must_exist or (isinstance(raw, list) and len(raw) > 1):
            source = f"переменная {env_name}" if env_name else f"config.json: {dotted}"
            tried = ", ".join(str(p) for p in resolved) or "; ".join(errors)
            raise CdsError(f"не найден файл ({source}): {tried} / file not found", EXIT_ENV)
        if not resolved:
            raise CdsError("; ".join(errors), EXIT_ENV)
        return resolved[0]

    # --- готовые значения -------------------------------------------------

    @property
    def codesys_exe(self) -> Path:
        return self.path("codesys.exe", must_exist=True)

    @property
    def profile(self) -> str:
        return str(self.value("codesys.profile"))

    def project_path(self, tests: bool) -> Path:
        return self.path("project.test_path" if tests else "project.path")

    @property
    def tests_folder(self) -> str:
        return str(self.value("project.tests_folder"))

    def device(self) -> dict[str, Any]:
        return {
            "name": str(self.value("device.name")),
            "type": int(self.number("device.type")),
            "id": str(self.value("device.id")),
            "version": str(self.value("device.version")),
        }

    def device_label(self) -> str:
        dev = self.device()
        return f"{self.value('device.title')} ({dev['type']} / {dev['id']} / {dev['version']})"

    def library_spec(self, key: str) -> dict[str, Any]:
        libs = self.node("libraries")
        if key not in libs:
            raise CdsError(f"config.json: библиотека {key} не описана в libraries / unknown library {key}", EXIT_USAGE)
        spec: dict[str, Any] = {k: self.value(f"libraries.{key}.{k}") for k in libs[key] if k != "install_from"}
        node = libs[key].get("install_from")
        spec["install_env"] = node.get("env") if isinstance(node, Mapping) else None
        spec["install_from"] = None
        if node is not None and self._raw(f"libraries.{key}.install_from")[0]:
            spec["install_from"] = str(self.path(f"libraries.{key}.install_from"))
        spec["key"] = key
        return spec

    def mode_config(self, mode: str) -> dict[str, Any]:
        modes = self.node("modes")
        if mode not in modes:
            raise CdsError(f"config.json: нет режима {mode} / no mode {mode}", EXIT_USAGE)
        cfg = modes[mode]
        return {
            "tasks": list(cfg.get("tasks", [])),
            "libraries": [self.library_spec(k) for k in cfg.get("libraries", [])],
        }

    def other_libraries(self, mode: str) -> list[dict[str, Any]]:
        modes = self.node("modes")
        mine = set(modes[mode].get("libraries", []))
        keys = sorted({k for m in modes.values() for k in m.get("libraries", [])} - mine)
        return [self.library_spec(k) for k in keys]


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CdsError(f"нет файла настроек {path} / config not found", EXIT_USAGE) from None
    except json.JSONDecodeError as exc:
        raise CdsError(f"{path}: неверный JSON / invalid JSON: {exc}", EXIT_USAGE) from None
    if not isinstance(data, dict):
        raise CdsError(f"{path}: ожидался объект JSON / expected a JSON object", EXIT_USAGE)
    return data


def make_settings(repo_root: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    getenv = make_getenv(environ)
    root = repo_root or Path(getenv("CDS_REPO_ROOT") or DEFAULT_REPO_ROOT)
    config_path = Path(getenv("CDS_CONFIG") or (root / "tools" / "codesys" / CONFIG_NAME))
    return Settings(load_config(config_path), root.resolve(), getenv)


# ---------------------------------------------------------------------------
# Командная строка CODESYS и файл задания
# ---------------------------------------------------------------------------


def build_cmdline(exe: str | Path, profile: str, script: str | Path) -> str:
    """Командная строка CODESYS одной строкой: кавычки после '=' обязательны буквально.

    Список аргументов subprocess превратил бы --profile="..." в "--profile=...", и CODESYS
    ответил бы «Для работы в режиме --noUI необходимо определить профиль» (проверено на 3.5.17.30).
    """
    parts = {"exe": str(exe), "profile": profile, "script": str(script)}
    for name, value in parts.items():
        if '"' in value or "\n" in value or "\r" in value:
            raise CdsError(f"недопустимый символ в {name}: {value!r} / invalid character", EXIT_USAGE)
        if not value:
            raise CdsError(f"пустое значение {name} / empty {name}", EXIT_USAGE)
    return f'"{parts["exe"]}" --profile="{parts["profile"]}" --noUI --runscript="{parts["script"]}"'


def write_job(path: Path, job: Mapping[str, Any]) -> None:
    """Файл задания для скриптов CODESYS (UTF-8, LF)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(job, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _oem_encoding() -> str:
    if sys.platform == "win32":
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    else:
        return "utf-8"


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:  # pragma: no cover - CODESYS только под Windows
        with contextlib.suppress(OSError):
            os.kill(pid, 9)


def run_codesys(
    settings: Settings,
    script: str,
    job: dict[str, Any],
    label: str,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Запускает скрипт CODESYS с заданием; возвращает результат (job['result'])."""
    work = settings.repo_root / WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    job_path = work / f"job-{label}.json"
    result_path = work / f"result-{label}.json"
    stdout_path = work / f"{label}.stdout.log"
    with contextlib.suppress(FileNotFoundError):
        result_path.unlink()
    job = dict(job)
    job["result"] = str(result_path)
    write_job(job_path, job)
    cmdline = build_cmdline(settings.codesys_exe, settings.profile, TOOLS_CODESYS / script)
    env = dict(os.environ)
    env[JOB_ENV] = str(job_path)
    timeout = timeout_s if timeout_s is not None else settings.number("codesys.timeout_s")
    started = time.time()
    print(f"[cds] CODESYS {settings.profile}: {script} ({label}) ...", flush=True)
    proc = subprocess.Popen(
        cmdline,
        cwd=str(settings.repo_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        out, _ = proc.communicate()
        stdout_path.write_text(out.decode(_oem_encoding(), "replace"), encoding="utf-8")
        raise CdsError(
            f"CODESYS не завершился за {timeout:.0f} с (проект открыт в IDE? диалог в noUI?) — процесс остановлен; "
            f"вывод: {stdout_path} / CODESYS timed out",
            EXIT_TIMEOUT,
        ) from None
    elapsed = time.time() - started
    stdout_path.write_text(out.decode(_oem_encoding(), "replace"), encoding="utf-8", newline="\n")
    if not result_path.exists():
        raise CdsError(
            f"CODESYS завершился с кодом {proc.returncode} за {elapsed:.0f} с, но не записал результат {result_path}; "
            f"вывод: {stdout_path} / no result file",
            EXIT_ENV,
        )
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    result["process_exit_code"] = proc.returncode
    result["elapsed_s"] = round(elapsed, 1)
    if proc.returncode != result.get("exit_code"):
        print(
            f"[cds] предупреждение: код выхода CODESYS {proc.returncode}, в результате {result.get('exit_code')}",
            flush=True,
        )
    return result


# ---------------------------------------------------------------------------
# Блокировки
# ---------------------------------------------------------------------------


class FileLock:
    """Межпроцессная блокировка файлом (msvcrt.locking / fcntl.flock); снимается и при смерти процесса."""

    def __init__(self, path: Path, timeout_s: float, poll_s: float = 0.5, purpose: str = "") -> None:
        self.path = path
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.purpose = purpose
        self._handle: IO[bytes] | None = None

    @property
    def owner_path(self) -> Path:
        return self.path.with_name(self.path.name + ".owner")

    def _try_lock(self, handle: IO[bytes]) -> bool:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            return True

    def _unlock(self, handle: IO[bytes]) -> None:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def owner(self) -> str:
        try:
            return self.owner_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "неизвестно"

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout_s
        announced = False
        while not self._try_lock(handle):
            if time.monotonic() >= deadline:
                handle.close()
                raise CdsError(
                    f"блокировка {self.path} занята дольше {self.timeout_s:.0f} с (владелец: {self.owner()}) "
                    "/ lock timeout",
                    EXIT_LOCK,
                )
            if not announced:
                print(f"[cds] ожидание блокировки {self.path} (владелец: {self.owner()}) ...", flush=True)
                announced = True
            time.sleep(self.poll_s)
        self._handle = handle
        info = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "purpose": self.purpose,
            "since": dt.datetime.now().isoformat(timespec="seconds"),
        }
        with contextlib.suppress(OSError):
            self.owner_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    def release(self) -> None:
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            self.owner_path.unlink()
        self._unlock(self._handle)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def runtime_lock(settings: Settings, purpose: str) -> FileLock:
    """Глобальная блокировка рантайма: один рантайм на машину, запуски из разных репозиториев ждут друг друга."""
    return FileLock(settings.path("lock.path"), settings.number("lock.timeout_s"), purpose=purpose)


def repo_lock(settings: Settings, purpose: str) -> FileLock:
    """Блокировка репозитория: один процесс CODESYS на проекты этого репозитория."""
    return FileLock(
        settings.repo_root / WORK_DIR / "cds.lock",
        settings.number("lock.timeout_s"),
        purpose=f"{purpose} {settings.repo_root}",
    )


# ---------------------------------------------------------------------------
# Состояние проекта: защита правок из IDE от перезаписи импортом и исходников — экспортом
# ---------------------------------------------------------------------------


def layout_subdirs(tests: bool) -> tuple[str, ...]:
    return ("src", "tests") if tests else ("src",)


def _fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


def sources_digest(root: Path, subdirs: Sequence[str]) -> str:
    """SHA-256 файлов *.st раскладки (пути и содержимое) — признак «исходники не менялись»."""
    digest = hashlib.sha256()
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.st")):
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def _state(settings: Settings) -> dict[str, Any]:
    path = settings.repo_root / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _entry(settings: Settings, project: Path) -> dict[str, Any]:
    entry = _state(settings).get(str(project))
    return entry if isinstance(entry, dict) else {}


def record_project_state(settings: Settings, project: Path, subdirs: Sequence[str]) -> None:
    """Запоминает отпечаток проекта и исходников после успешного import/export."""
    data = _state(settings)
    fp = _fingerprint(project)
    if fp is None:
        data.pop(str(project), None)
    else:
        data[str(project)] = {"project": fp, "sources": sources_digest(settings.repo_root, subdirs)}
    path = settings.repo_root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_project_state(settings: Settings, project: Path, force: bool) -> None:
    """Отказ импорта, если проект менялся вне cds.py (сохранён в IDE) после последнего import/export."""
    if force or not project.exists():
        return
    recorded = _entry(settings, project).get("project")
    current = _fingerprint(project)
    if recorded is not None and current is not None and recorded != current:
        raise CdsError(
            f"проект {project} изменён вне cds.py (сохранён в IDE?): импорт перезапишет правки. "
            "Сначала выгрузите их: cds.py export [--tests], либо повторите с --force / "
            "project was modified outside cds.py; export first or use --force",
            EXIT_FAILED,
        )


def check_sources_state(settings: Settings, project: Path, subdirs: Sequence[str], force: bool) -> None:
    """Отказ экспорта в репозиторий, если исходники менялись после последнего import/export проекта.

    Экспорт записывает раскладку целиком и удаляет файлы *.st, которых нет в проекте: файлы,
    добавленные в src/ после импорта, пропали бы.
    """
    if force:
        return
    recorded = _entry(settings, project).get("sources")
    if recorded is None:
        return
    if recorded != sources_digest(settings.repo_root, subdirs):
        raise CdsError(
            f"исходники {', '.join(subdirs)} изменились после последнего import/export проекта {project}: "
            "экспорт перезаписал бы их. Сначала cds.py import (правки из IDE при этом пропадут) или сравните "
            "вручную: cds.py export --to <каталог>; перезапись — с --force / "
            "sources changed since last import; export --to DIR or use --force",
            EXIT_FAILED,
        )


# ---------------------------------------------------------------------------
# Шаги заданий
# ---------------------------------------------------------------------------


def import_step(
    settings: Settings,
    tests: bool,
    project: Path,
    clean: bool = False,
    log_dir: Path | None = None,
    name: str = "import",
    reopen: bool = False,
) -> dict[str, Any]:
    mode = "test" if tests else "normal"
    logs = log_dir or (settings.repo_root / "build")
    return {
        "action": "import",
        "repo_root": str(settings.repo_root),
        "with_tests": tests,
        "tests_folder": settings.tests_folder,
        "mode": mode,
        "mode_config": settings.mode_config(mode),
        "other_libraries": settings.other_libraries(mode),
        "project": str(project),
        "device": settings.device(),
        "clean": clean,
        "reopen": reopen,
        "log": str(logs / f"{name}.log"),
        "report": str(logs / f"{name}.json"),
    }


def build_step(settings: Settings, project: Path) -> dict[str, Any]:
    return {
        "action": "build",
        "project": str(project),
        "log": str(settings.repo_root / "build" / "build.log"),
        "report": str(settings.repo_root / "build" / "build.json"),
    }


def export_step(
    settings: Settings, project: Path, out_root: Path, tests: bool, log_dir: Path | None = None
) -> dict[str, Any]:
    logs = log_dir or (settings.repo_root / "build")
    return {
        "action": "export",
        "project": str(project),
        "out_root": str(out_root),
        "with_tests": tests,
        "tests_folder": settings.tests_folder,
        "log": str(logs / "export.log"),
        "report": str(logs / "export.json"),
    }


def test_meta(settings: Settings) -> dict[str, Any]:
    return {
        "codesys_version": str(settings.value("codesys.version")),
        "profile": settings.profile,
        "device": settings.device_label(),
        "runtime": str(settings.value("runtime.title")),
        "framework": str(settings.value("tests.framework")),
        "repo": settings.repo_root.name,
    }


def test_step(settings: Settings, project: Path) -> dict[str, Any]:
    return {
        "action": "test",
        "project": str(project),
        "credentials": str(settings.path("runtime.credentials")),
        "runtime": {
            "gateway_name": str(settings.value("runtime.gateway.name")),
            "gateway_host": str(settings.value("runtime.gateway.host")),
            "gateway_port": int(settings.number("runtime.gateway.port")),
            "device_address": str(settings.value("runtime.device_address")),
            "device_port": int(settings.number("runtime.port")),
        },
        "tests": {
            "runner": str(settings.value("tests.runner")),
            "timeout_s": settings.number("tests.timeout_s"),
            "poll_ms": int(settings.number("tests.poll_ms")),
            "max_suites": int(settings.number("tests.max_suites")),
            "max_tests_per_suite": int(settings.number("tests.max_tests_per_suite")),
        },
        "meta": test_meta(settings),
        "log": str(settings.repo_root / "build" / "test.log"),
        "results": str(settings.path("tests.results")),
    }


_POS_LINE_RE = re.compile(r"(\d+)")
_POS_DECL_RE = re.compile(r"Объявл|Decl", re.IGNORECASE)
_POS_IMPL_RE = re.compile(r"Реализ|Impl", re.IGNORECASE)


def layout_objects(repo_root: Path) -> list[L.StObject]:
    """Объекты раскладки src/ и tests/ (для привязки сообщений компилятора к файлам)."""
    objects: list[L.StObject] = []
    for subdir in ("src", "tests"):
        objects.extend(L.scan(str(repo_root), subdir).objects)
    return objects


def source_location(obj_path: str | None, position: str | None, objects: Sequence[L.StObject]) -> str | None:
    """Файл раскладки и строка для сообщения компилятора: 'src/POUs/F_Clamp/F_Clamp.impl.st:16'.

    obj_path — путь объекта из сообщения (папки через точку, затем объект, метод/свойство,
    аксессор: 'Tests.FB_X_Test.Run', 'FB_Example.Count.Get'); position — текст позиции IDE
    ('Строка 16, Столбец 1 (Реализ.)', 'Строка 26 (Объявл.)'). Строка в CODESYS совпадает со
    строкой файла: объявление и тело лежат в раскладке отдельными файлами.
    """
    if not obj_path:
        return None
    index = {o.name.lower(): o for o in objects}
    parts = obj_path.split(".")
    obj = None
    rest: list[str] = []
    for i, part in enumerate(parts):
        if part.lower() in index:
            obj = index[part.lower()]
            rest = parts[i + 1 :]
            break
    if obj is None:
        return None
    section = None
    line = None
    if position:
        if _POS_DECL_RE.search(position):
            section = "decl"
        elif _POS_IMPL_RE.search(position):
            section = "impl"
        match = _POS_LINE_RE.search(position)
        line = int(match.group(1)) if match else None
    files = [rel for rel, _ in L.object_files(obj)]
    base = obj.rel_dir()
    wanted: str | None = None
    if not rest:
        if obj.kind in (L.KIND_POU,) and section == "impl":
            wanted = f"{base}/{obj.name}.impl.st"
        else:
            wanted = files[0]
    elif len(rest) == 1:
        member = rest[0].lower()
        for method in obj.methods:
            if method.name.lower() == member:
                suffix = "method.impl.st" if section == "impl" else "method.decl.st"
                wanted = f"{base}/{method.name}.{suffix}"
        for prop in obj.properties:
            if prop.name.lower() == member:
                wanted = f"{base}/{prop.name}.prop.decl.st"
    elif len(rest) == 2:
        for prop in obj.properties:
            if prop.name.lower() == rest[0].lower() and rest[1].lower() in ("get", "set"):
                kind = "decl" if section == "decl" else "impl"
                wanted = f"{base}/{prop.name}.{rest[1].lower()}.{kind}.st"
    if wanted is None or wanted not in files:
        return None
    return f"{wanted}:{line}" if line else wanted


def annotate_build(settings: Settings, result: Mapping[str, Any]) -> None:
    """Добавляет к сообщениям сборки поле source (файл:строка) и переписывает build/build.json."""
    step = next((s for s in result.get("steps", []) if s.get("action") == "build"), None)
    if not step or not step.get("messages"):
        return
    try:
        objects = layout_objects(settings.repo_root)
    except (OSError, UnicodeDecodeError):
        return
    for msg in step["messages"]:
        msg["source"] = source_location(msg.get("object"), msg.get("position"), objects)
    report = settings.repo_root / "build" / "build.json"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        data["messages"] = step["messages"]
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        report.write_text(text, encoding="utf-8", newline="\n")


def _step(result: Mapping[str, Any], action: str, index: int = 0) -> dict[str, Any]:
    found = [s for s in result.get("steps", []) if s.get("action") == action]
    return dict(found[index]) if len(found) > index else {}


def _print_steps(result: Mapping[str, Any]) -> None:
    for step in result.get("steps", []):
        action = step.get("action")
        status = "OK" if step.get("ok") else "ОШИБКА"
        detail = ""
        if action == "import":
            detail = (
                f"создано {len(step.get('created', []))}, обновлено {len(step.get('updated', []))}, "
                f"удалено {len(step.get('removed', []))}, перемещено {len(step.get('moved', []))}, "
                f"без изменений {step.get('unchanged', 0)}"
            )
            for bucket in ("removed",):
                for item in step.get(bucket, []):
                    print(f"[cds]   удалено: {item}")
        elif action == "build":
            detail = f"ошибок {step.get('errors')}, предупреждений {step.get('warnings')}"
            for msg in step.get("messages", []):
                if msg.get("severity") in ("Error", "FatalError", "Warning"):
                    where = f" [{msg['object']}{', ' + msg['position'] if msg.get('position') else ''}]"
                    if not msg.get("object"):
                        where = ""
                    prefix = f"{msg['source']}: " if msg.get("source") else ""
                    print(f"[cds]   {prefix}{msg['severity']} {msg.get('code') or ''} {msg.get('text')}{where}")
        elif action == "export":
            detail = (
                f"файлов {step.get('files')}, объектов src {step.get('objects_src')}, tests {step.get('objects_tests')}"
            )
        elif action == "test":
            totals = step.get("totals") or {}
            if totals:
                detail = (
                    f"наборов {totals.get('suites')}, тестов {totals.get('tests')}, "
                    f"пройдено {totals.get('passed')}, упало {totals.get('failed')}, "
                    f"пропущено {totals.get('skipped')}"
                )
            else:
                detail = "тесты не выполнялись"
        print(f"[cds] {action}: {status} ({detail}, {step.get('duration_s')} с)")
        warnings = step.get("warnings")
        for warn in warnings if isinstance(warnings, list) else []:
            print(f"[cds]   предупреждение: {warn}")
        if step.get("error"):
            print(f"[cds]   {step['error']}".rstrip())
    if result.get("error"):
        print(f"[cds] ошибка скрипта CODESYS: {result['error']}".rstrip())


def _finish(result: Mapping[str, Any]) -> int:
    _print_steps(result)
    code = int(result.get("exit_code", EXIT_FAILED))
    if code == 0:
        return EXIT_OK
    return EXIT_FAILED if code == 1 else EXIT_ENV


# ---------------------------------------------------------------------------
# Подкоманды: import, export, build, test, roundtrip
# ---------------------------------------------------------------------------


def cmd_import(settings: Settings, args: argparse.Namespace) -> int:
    project = settings.project_path(args.tests)
    with repo_lock(settings, "import"):
        check_project_state(settings, project, args.force or args.clean)
        job = {"steps": [import_step(settings, args.tests, project, clean=args.clean)]}
        result = run_codesys(settings, "import_sources.py", job, "import")
        if result.get("ok"):
            record_project_state(settings, project, layout_subdirs(args.tests))
            print(f"[cds] проект: {project}")
    return _finish(result)


def cmd_export(settings: Settings, args: argparse.Namespace) -> int:
    project = settings.project_path(args.tests)
    out_root = Path(args.to).resolve() if args.to else settings.repo_root
    if not project.exists():
        raise CdsError(f"нет проекта {project}: сначала cds.py import{' --tests' if args.tests else ''}", EXIT_ENV)
    with repo_lock(settings, "export"):
        if out_root == settings.repo_root:
            check_sources_state(settings, project, layout_subdirs(args.tests), args.force)
        job = {"steps": [export_step(settings, project, out_root, args.tests)]}
        result = run_codesys(settings, "export_sources.py", job, "export")
        if result.get("ok") and out_root == settings.repo_root:
            record_project_state(settings, project, layout_subdirs(args.tests))
            print("[cds] проверьте изменения: git status / git diff --stat")
    return _finish(result)


def cmd_build(settings: Settings, args: argparse.Namespace) -> int:
    project = settings.project_path(args.tests)
    steps: list[dict[str, Any]] = []
    with repo_lock(settings, "build"):
        if not args.no_import:
            check_project_state(settings, project, args.force)
            steps.append(import_step(settings, args.tests, project))
        elif not project.exists():
            raise CdsError(f"нет проекта {project}: уберите --no-import или выполните import", EXIT_ENV)
        steps.append(build_step(settings, project))
        result = run_codesys(settings, "build.py", {"steps": steps}, "build")
        if _step(result, "import").get("ok"):
            record_project_state(settings, project, layout_subdirs(args.tests))
    annotate_build(settings, result)
    return _finish(result)


def _git_commit(repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        return None
    dirty = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def cmd_test(settings: Settings, args: argparse.Namespace) -> int:
    project = settings.project_path(True)
    results_path = settings.path("tests.results")
    junit_path = settings.path("tests.junit")
    with repo_lock(settings, "test"), runtime_lock(settings, f"test {settings.repo_root.name}"):
        # Результаты прошлого прогона удаляются: при ошибке сборки или входа в рантайм старый
        # junit.xml с зелёными тестами иначе остался бы в build/ и ввёл бы в заблуждение CI
        for stale in (results_path, junit_path):
            with contextlib.suppress(FileNotFoundError):
                stale.unlink()
        check_project_state(settings, project, args.force)
        runtime = ensure_runtime(settings, restart=args.restart_runtime, allow_start=not args.no_runtime_start)
        steps = [
            import_step(settings, True, project, clean=args.clean),
            build_step(settings, project),
            test_step(settings, project),
        ]
        timeout = settings.number("codesys.timeout_s") + settings.number("tests.timeout_s")
        result = run_codesys(settings, "run_tests.py", {"steps": steps}, "test", timeout_s=timeout)
        if _step(result, "import").get("ok"):
            record_project_state(settings, project, layout_subdirs(True))
    annotate_build(settings, result)
    build = _step(result, "build")
    if results_path.exists():
        data = json.loads(results_path.read_text(encoding="utf-8"))
        data["commit"] = _git_commit(settings.repo_root)
        data["build"] = {"errors": build.get("errors"), "warnings": build.get("warnings")}
        imp = _step(result, "import")
        data["import"] = {k: len(imp.get(k, [])) for k in ("created", "updated", "removed", "moved")}
        data["runtime_process"] = runtime
        data["command"] = "python tools/codesys/cds.py test"
        results_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(results_to_junit(data), encoding="utf-8", newline="\n")
        print(f"[cds] результаты: {results_path}, JUnit: {junit_path}")
    elif _step(result, "build") and not build.get("ok"):
        print("[cds] сборка с ошибками — тесты не запускались (build/build.log)")
    return _finish(result)


def compare_trees(expected_root: Path, actual_root: Path, subdirs: Sequence[str]) -> list[str]:
    """Сравнивает файлы *.st раскладки; возвращает строки отчёта (пусто — совпадают)."""

    def files(root: Path) -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        for sub in subdirs:
            base = root / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.st")):
                found[path.relative_to(root).as_posix()] = path.read_bytes()
        return found

    want = files(expected_root)
    have = files(actual_root)
    report: list[str] = []
    for rel in sorted(set(want) - set(have)):
        report.append(f"только в исходниках: {rel}")
    for rel in sorted(set(have) - set(want)):
        report.append(f"только в экспорте: {rel}")
    for rel in sorted(set(want) & set(have)):
        if want[rel] == have[rel]:
            continue
        report.append(f"различается: {rel}")
        a = want[rel].decode("utf-8", "replace").splitlines(keepends=True)
        b = have[rel].decode("utf-8", "replace").splitlines(keepends=True)
        diff = list(difflib.unified_diff(a, b, f"src:{rel}", f"export:{rel}", n=1))
        report.extend("    " + line.rstrip("\n").replace("\r", "\\r") for line in diff[:60])
    return report


def cmd_roundtrip(settings: Settings, args: argparse.Namespace) -> int:
    rt_dir = settings.path("project.roundtrip_dir")
    export_dir = rt_dir / "export"
    project = rt_dir / f"{settings.value('project.name')}-roundtrip.project"
    with repo_lock(settings, "roundtrip"):
        if export_dir.exists():
            shutil.rmtree(export_dir)
        rt_dir.mkdir(parents=True, exist_ok=True)
        steps = [
            import_step(settings, True, project, clean=True, log_dir=rt_dir, name="import"),
            export_step(settings, project, export_dir, True, log_dir=rt_dir),
            import_step(settings, True, project, log_dir=rt_dir, name="import2", reopen=True),
        ]
        result = run_codesys(settings, "import_sources.py", {"steps": steps}, "roundtrip")
    _print_steps(result)
    if not result.get("ok"):
        return EXIT_FAILED if result.get("exit_code") == 1 else EXIT_ENV
    report = compare_trees(settings.repo_root, export_dir, ("src", "tests"))
    second = _step(result, "import", 1)
    changes = int(second.get("changes", -1))
    if report:
        print(f"[cds] roundtrip: ДИФФ между исходниками и экспортом ({export_dir}):")
        for line in report:
            print(f"[cds]   {line}")
    else:
        n_files = len(list((export_dir / "src").rglob("*.st"))) + len(list((export_dir / "tests").rglob("*.st")))
        print(f"[cds] roundtrip: экспорт совпадает с src/ и tests/ побайтно ({n_files} файлов)")
    if changes != 0:
        print(f"[cds] roundtrip: повторный импорт изменил проект ({changes} изменений) — импорт не идемпотентен:")
        for bucket in ("created", "updated", "removed", "moved"):
            for item in second.get(bucket, []):
                print(f"[cds]   {bucket}: {item}")
    else:
        print("[cds] roundtrip: повторный импорт после открытия проекта с диска — 0 изменений")
    summary = {
        "ok": not report and changes == 0,
        "diff": report,
        "second_import_changes": changes,
        "export_dir": str(export_dir),
    }
    (rt_dir / "roundtrip.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return EXIT_OK if summary["ok"] else EXIT_FAILED


# ---------------------------------------------------------------------------
# Рантайм: пользовательский процесс CODESYS Control Win V3
# ---------------------------------------------------------------------------


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _powershell(script: str, timeout_s: float = 60) -> str:
    if sys.platform != "win32":
        raise CdsError("управление рантаймом — только в Windows / runtime management requires Windows", EXIT_ENV)
    full = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ErrorActionPreference = 'Stop'; " + script
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") or proc.stderr.decode(_oem_encoding(), "replace")
        raise CdsError(f"PowerShell: {err.strip() or out.strip()}", EXIT_ENV)
    return out


def _norm(text: str) -> str:
    return os.path.normcase(text.replace("/", "\\")).replace('"', "")


def parse_processes(text: str) -> list[dict[str, Any]]:
    """Разбор ConvertTo-Json от Get-CimInstance Win32_Process (объект или массив)."""
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    items = data if isinstance(data, list) else [data]
    result = []
    for item in items:
        created = item.get("Created")
        result.append(
            {
                "pid": int(item.get("ProcessId")),
                "cmdline": item.get("CommandLine") or "",
                "exe": item.get("ExecutablePath") or "",
                "created": created,
            }
        )
    return result


def runtime_processes() -> list[dict[str, Any]]:
    script = (
        f"$p = @(Get-CimInstance Win32_Process -Filter \"Name='{RUNTIME_IMAGE}'\" | ForEach-Object {{ "
        "[pscustomobject]@{ ProcessId = $_.ProcessId; CommandLine = $_.CommandLine; "
        "ExecutablePath = $_.ExecutablePath; "
        "Created = $_.CreationDate.ToString('o') } }); if ($p.Count -gt 0) { ConvertTo-Json -InputObject $p -Compress }"
    )
    return parse_processes(_powershell(script))


def is_our_runtime(proc: Mapping[str, Any], boot_cfg: Path) -> bool:
    """Наш процесс — с ключом -d <boot.cfg>; служба Windows и чужие экземпляры не трогаются."""
    return _norm(str(boot_cfg)) in _norm(str(proc.get("cmdline", "")))


def port_open(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _age_min(created: str | None) -> float | None:
    if not created:
        return None
    try:
        start = dt.datetime.fromisoformat(created)
    except ValueError:
        return None
    now = dt.datetime.now(start.tzinfo) if start.tzinfo else dt.datetime.now()
    return (now - start).total_seconds() / 60.0


def runtime_status(settings: Settings) -> dict[str, Any]:
    boot = settings.path("runtime.boot_cfg")
    port = int(settings.number("runtime.port"))
    procs = runtime_processes()
    ours = [p for p in procs if is_our_runtime(p, boot)]
    others = [p for p in procs if not is_our_runtime(p, boot)]
    status: dict[str, Any] = {
        "running": bool(ours),
        "pid": ours[0]["pid"] if ours else None,
        "started": ours[0]["created"] if ours else None,
        "age_min": round(_age_min(ours[0]["created"]) or 0, 1) if ours else None,
        "port": port,
        "port_open": port_open("127.0.0.1", port),
        "boot_cfg": str(boot),
        "other_instances": [{"pid": p["pid"], "cmdline": p["cmdline"]} for p in others],
    }
    if ours:
        status["demo_left_min"] = round(settings.number("runtime.demo_limit_min") - (status["age_min"] or 0), 1)
    return status


def runtime_init(settings: Settings) -> list[str]:
    """Каталог рантайма: boot.cfg (CRLF), work/CODESYSControl.cfg (копия штатного), credentials.json."""
    done: list[str] = []
    work = settings.path("runtime.work_dir")
    boot = settings.path("runtime.boot_cfg")
    work.mkdir(parents=True, exist_ok=True)
    if not boot.exists():
        boot.parent.mkdir(parents=True, exist_ok=True)
        work_text = str(work).rstrip("\\/") + "\\"
        boot.write_bytes(f"[SysFile]\r\nWindows.WorkingDirectory={work_text}\r\n".encode())
        done.append(f"создан {boot}")
    cfg = work / "CODESYSControl.cfg"
    if not cfg.exists():
        pattern = str(settings.path("runtime.cfg_template"))
        matches = sorted(glob.glob(pattern))
        if not matches:
            raise CdsError(
                f"не найден штатный CODESYSControl.cfg ({pattern}): установите CODESYS Control Win V3 "
                "или задайте CODESYS_RUNTIME_CFG_TEMPLATE / runtime config template not found",
                EXIT_ENV,
            )
        shutil.copyfile(matches[0], cfg)
        done.append(f"скопирован {matches[0]} -> {cfg}")
    creds = settings.path("runtime.credentials")
    if not creds.exists():
        creds.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "user": "cdsci",
            "password": secrets.token_urlsafe(18),
            "note": "Тестовый пользователь локального рантайма CODESYS Control Win (создан cds.py runtime init). "
            "Не коммитить. Первый вход на свежий рантайм создаёт этого пользователя.",
        }
        creds.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        done.append(f"создан {creds} (пароль сгенерирован, в вывод не печатается)")
    return done


def _start_hidden(exe: Path, arguments: Sequence[str], work: Path, log: Path, err: Path, marker: Path) -> int:
    """Скрытый запуск процесса через Start-Process; PID — через файл-маркер.

    Вывод PowerShell нельзя читать через канал: дочерний процесс наследует дескрипторы
    PowerShell, канал не закрывается, пока работает рантайм, и чтение зависает (проверено).
    Поэтому PowerShell запускается без каналов (DEVNULL), а PID или ошибку пишет в marker.
    """
    if sys.platform != "win32":
        raise CdsError("управление рантаймом — только в Windows / runtime management requires Windows", EXIT_ENV)
    with contextlib.suppress(FileNotFoundError):
        marker.unlink()
    args = ", ".join(_ps_quote(a) for a in arguments)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"try {{ $p = Start-Process -FilePath {_ps_quote(str(exe))} -ArgumentList @({args}) "
        f"-WorkingDirectory {_ps_quote(str(work))} -WindowStyle Hidden -PassThru "
        f"-RedirectStandardOutput {_ps_quote(str(log))} -RedirectStandardError {_ps_quote(str(err))}; "
        f"Set-Content -LiteralPath {_ps_quote(str(marker))} -Value ('OK ' + $p.Id) -Encoding UTF8 }} "
        f"catch {{ Set-Content -LiteralPath {_ps_quote(str(marker))} -Value ('ERR ' + $_.Exception.Message) "
        "-Encoding UTF8 }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    try:
        text = marker.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        raise CdsError("Start-Process не сообщил PID рантайма / runtime start failed", EXIT_ENV) from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            marker.unlink()
    if not text.startswith("OK "):
        raise CdsError(f"Start-Process: {text.removeprefix('ERR ').strip()}", EXIT_ENV)
    return int(text[3:].strip())


def runtime_start(settings: Settings) -> dict[str, Any]:
    status = runtime_status(settings)
    if status["running"]:
        return status
    if status["port_open"]:
        others = ", ".join(str(p["pid"]) for p in status["other_instances"]) or "?"
        raise CdsError(
            f"порт {status['port']} занят другим процессом (PID {others}; служба CODESYS Control Win V3?). "
            "Остановите его сами или используйте его без cds.py runtime / port is busy",
            EXIT_ENV,
        )
    for line in runtime_init(settings):
        print(f"[cds] {line}")
    exe = settings.path("runtime.exe", must_exist=True)
    boot = settings.path("runtime.boot_cfg")
    work = settings.path("runtime.work_dir")
    log = settings.path("runtime.log_file")
    err = log.with_suffix(".err")
    pid_file = settings.path("runtime.pid_file")
    pid = _start_hidden(exe, ["-d", f'"{boot}"'], work, log, err, pid_file.with_suffix(".start"))
    pid_file.write_text(
        json.dumps({"pid": pid, "started": dt.datetime.now().isoformat(timespec="seconds"), "boot_cfg": str(boot)})
        + "\n",
        encoding="utf-8",
    )
    deadline = time.monotonic() + settings.number("runtime.start_timeout_s")
    port = int(settings.number("runtime.port"))
    while time.monotonic() < deadline:
        if port_open("127.0.0.1", port):
            break
        if not any(p["pid"] == pid for p in runtime_processes()):
            raise CdsError(f"рантайм (PID {pid}) завершился при старте; журнал: {log}, {err}", EXIT_ENV)
        time.sleep(1.0)
    else:
        raise CdsError(f"рантайм (PID {pid}) не открыл порт {port} за отведённое время; журнал: {log}", EXIT_TIMEOUT)
    return runtime_status(settings)


def runtime_stop(settings: Settings) -> list[int]:
    boot = settings.path("runtime.boot_cfg")
    stopped = []
    for proc in runtime_processes():
        if not is_our_runtime(proc, boot):
            continue
        _powershell(f"Stop-Process -Id {int(proc['pid'])} -Force")
        stopped.append(int(proc["pid"]))
    deadline = time.monotonic() + 30
    while stopped and time.monotonic() < deadline:
        alive = {p["pid"] for p in runtime_processes()}
        if not alive & set(stopped):
            break
        time.sleep(0.5)
    with contextlib.suppress(FileNotFoundError):
        settings.path("runtime.pid_file").unlink()
    return stopped


def ensure_runtime(settings: Settings, restart: bool = False, allow_start: bool = True) -> dict[str, Any]:
    """Рантайм для тестов: запуск при отсутствии, перезапуск перед истечением демо-режима (2 ч)."""
    status = runtime_status(settings)
    max_age = settings.number("runtime.restart_after_min")
    if status["running"] and (restart or (status["age_min"] or 0) >= max_age):
        reason = "--restart-runtime" if restart else f"работает {status['age_min']} мин, демо-режим — 2 ч"
        print(f"[cds] перезапуск рантайма ({reason})")
        runtime_stop(settings)
        status = runtime_status(settings)
    if status["running"]:
        print(f"[cds] рантайм: PID {status['pid']}, работает {status['age_min']} мин")
        return status
    if status["port_open"]:
        print(
            f"[cds] рантайм: порт {status['port']} открыт сторонним экземпляром — используется он "
            "(демо-лимит не контролируется)"
        )
        return status
    if not allow_start:
        raise CdsError("рантайм не запущен: cds.py runtime start / runtime is not running", EXIT_ENV)
    print("[cds] запуск рантайма ...")
    status = runtime_start(settings)
    print(f"[cds] рантайм запущен: PID {status['pid']}")
    return status


def cmd_runtime(settings: Settings, args: argparse.Namespace) -> int:
    action = args.action
    if action == "status":
        status = runtime_status(settings)
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return EXIT_OK if status["running"] or status["port_open"] else EXIT_FAILED
    if action == "init":
        done = runtime_init(settings)
        for line in done or ["каталог рантайма уже подготовлен"]:
            print(f"[cds] {line}")
        return EXIT_OK
    with runtime_lock(settings, f"runtime {action}"):
        if action in ("stop", "restart"):
            stopped = runtime_stop(settings)
            print(f"[cds] остановлено: {stopped or 'нечего останавливать'}")
            if action == "stop":
                return EXIT_OK
        status = runtime_start(settings)
        print(json.dumps(status, indent=2, ensure_ascii=False))
    return EXIT_OK


# ---------------------------------------------------------------------------
# JUnit и отчёт
# ---------------------------------------------------------------------------


def results_to_junit(results: Mapping[str, Any]) -> str:
    """JUnit XML из build/test-results.json (формат, который понимают CI и IDE)."""
    totals = results.get("totals") or {}
    root = ET.Element(
        "testsuites",
        {
            "name": str(results.get("framework") or "coUnit"),
            "tests": str(totals.get("tests", 0)),
            "failures": str(totals.get("failed", 0)),
            "skipped": str(totals.get("skipped", 0)),
            "errors": "0" if results.get("finished") else "1",
        },
    )
    if not results.get("finished"):
        err_suite = ET.SubElement(root, "testsuite", {"name": "coUnit", "tests": "1", "failures": "0", "errors": "1"})
        case = ET.SubElement(err_suite, "testcase", {"name": "AllTestSuitesFinished", "classname": "coUnit"})
        ET.SubElement(case, "error", {"message": str(results.get("error") or "прогон не завершён (таймаут)")})
    for suite in results.get("suites", []):
        attrs = {
            "name": str(suite.get("name")),
            "tests": str(suite.get("tests", 0)),
            "failures": str(suite.get("failed", 0)),
            "skipped": str(suite.get("skipped", 0)),
            "errors": "0",
        }
        if results.get("started"):
            attrs["timestamp"] = str(results["started"])
        node = ET.SubElement(root, "testsuite", attrs)
        for case in suite.get("cases", []):
            tc = ET.SubElement(
                node,
                "testcase",
                {"name": str(case.get("name")), "classname": str(case.get("class_name") or suite.get("name"))},
            )
            if case.get("status") == "FAIL":
                failure = ET.SubElement(
                    tc,
                    "failure",
                    {"message": str(case.get("message") or ""), "type": str(case.get("failure_type") or "")},
                )
                failure.text = str(case.get("message") or "")
            elif case.get("status") == "SKIP":
                ET.SubElement(tc, "skipped")
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _status_ru(status: str) -> str:
    return {"PASS": "пройден", "FAIL": "**упал**", "SKIP": "пропущен"}.get(status, status)


def render_report(results: Mapping[str, Any]) -> str:
    """Раздел docs/TEST_REPORT.md с итогами последнего прогона (между маркерами)."""
    totals = results.get("totals") or {}
    started = str(results.get("started") or "").replace("T", " ")
    finished = bool(results.get("finished"))
    failed = int(totals.get("failed") or 0)
    ok = finished and failed == 0 and int(totals.get("tests") or 0) > 0
    build = results.get("build") or {}
    lines = [
        REPORT_BEGIN,
        "## Последний прогон в CODESYS",
        "",
        f"Проверено в CODESYS {results.get('codesys_version', '?')} / "
        f"{results.get('runtime', 'CODESYS Control Win V3')}"
        f" — {started}. Итог: **{'все тесты пройдены' if ok else 'есть ошибки'}**.",
        "",
        "| Параметр | Значение |",
        "|---|---|",
        f"| Дата прогона | {_cell(started)} |",
        f"| Коммит | {_cell(results.get('commit') or '— (нет коммита)')} |",
        f"| CODESYS | {_cell(results.get('codesys_version'))} (профиль `{_cell(results.get('profile'))}`) |",
        f"| Устройство в проекте | {_cell(results.get('device'))} |",
        f"| Рантайм | {_cell(results.get('runtime'))} |",
        f"| Фреймворк тестов | {_cell(results.get('framework'))} |",
        f"| Сборка | ошибок {_cell(build.get('errors'))}, предупреждений {_cell(build.get('warnings'))} |",
        f"| Способ запуска | `{_cell(results.get('command') or 'python tools/codesys/cds.py test')}` |",
        "",
        "| Набор | Тестов | Пройдено | Упало | Пропущено | Итог |",
        "|---|---|---|---|---|---|",
    ]
    for suite in results.get("suites", []):
        s_failed = int(suite.get("failed") or 0)
        s_skipped = int(suite.get("skipped") or 0)
        s_tests = int(suite.get("tests") or 0)
        verdict = "пройдено" if s_failed == 0 and s_tests > 0 else "**упало**"
        lines.append(
            f"| `{_cell(suite.get('name'))}` | {s_tests} | {s_tests - s_failed - s_skipped} | {s_failed} | "
            f"{s_skipped} | {verdict} |"
        )
    lines.append(
        f"| **Всего** | {totals.get('tests', 0)} | {totals.get('passed', 0)} | {totals.get('failed', 0)} | "
        f"{totals.get('skipped', 0)} | **{'пройдено' if ok else 'не пройдено'}** |"
    )
    if not finished:
        lines += ["", f"Прогон не завершён: {_cell(results.get('error') or 'таймаут ожидания AllTestSuitesFinished')}."]
    lines += ["", "### Результаты тестов", "", "| Набор | Тест | Итог |", "|---|---|---|"]
    for suite in results.get("suites", []):
        for case in suite.get("cases", []):
            status = _status_ru(str(case.get("status")))
            lines.append(f"| `{_cell(suite.get('name'))}` | `{_cell(case.get('name'))}` | {status} |")
    lines += ["", "### Упавшие тесты", ""]
    failures = [(s, c) for s in results.get("suites", []) for c in s.get("cases", []) if c.get("status") == "FAIL"]
    if failures:
        lines += ["| Набор | Тест | Сообщение coUnit | Тип проверки |", "|---|---|---|---|"]
        for suite, case in failures:
            lines.append(
                f"| `{_cell(suite.get('name'))}` | `{_cell(case.get('name'))}` | {_cell(case.get('message'))} | "
                f"{_cell(case.get('failure_type'))} |"
            )
    else:
        lines.append("_Нет._")
    lines += ["", "Машиночитаемые результаты: `build/test-results.json`, `build/junit.xml` (не в git).", REPORT_END]
    return "\n".join(lines) + "\n"


def update_report(doc: str, section: str) -> str:
    """Заменяет раздел между маркерами; без маркеров — вставляет перед первым заголовком '## '."""
    begin = doc.find(REPORT_BEGIN.split(" (")[0])
    end = doc.find(REPORT_END)
    if begin >= 0 and end > begin:
        tail = doc[end + len(REPORT_END) :]
        if tail.startswith("\n"):
            tail = tail[1:]
        return doc[:begin] + section + tail
    match = re.search(r"^## ", doc, re.MULTILINE)
    if match is None:
        return doc.rstrip("\n") + "\n\n" + section
    return doc[: match.start()] + section + "\n" + doc[match.start() :]


def _load_results(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CdsError(f"нет файла результатов {path}: сначала cds.py test / no results file", EXIT_ENV) from None
    if not isinstance(data, dict):
        raise CdsError(f"{path}: ожидался объект JSON", EXIT_USAGE)
    return data


def cmd_junit(settings: Settings, args: argparse.Namespace) -> int:
    results_path = Path(args.results) if args.results else settings.path("tests.results")
    out = Path(args.out) if args.out else settings.path("tests.junit")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(results_to_junit(_load_results(results_path)), encoding="utf-8", newline="\n")
    print(f"[cds] JUnit: {out}")
    return EXIT_OK


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    results_path = Path(args.results) if args.results else settings.path("tests.results")
    doc_path = Path(args.doc) if args.doc else settings.path("tests.report_doc")
    results = _load_results(results_path)
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else "# Отчёт о тестировании\n"
    new = update_report(doc, render_report(results))
    doc_path.write_text(new, encoding="utf-8", newline="\n")
    print(f"[cds] обновлён раздел «Последний прогон в CODESYS» в {doc_path}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Разбор аргументов
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cds.py",
        description="Импорт, сборка, тесты и экспорт CODESYS 3.5 из консоли (docs/MANUAL_STEPS.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="исходники src/ (и tests/) -> проект CODESYS")
    p.add_argument("--tests", action="store_true", help="с tests/ в тестовый проект (режим test)")
    p.add_argument("--clean", action="store_true", help="пересоздать проект с нуля")
    p.add_argument("--force", action="store_true", help="перезаписать проект, даже если он сохранён в IDE")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="проект CODESYS -> раскладка src/ (и tests/)")
    p.add_argument("--tests", action="store_true", help="экспорт тестового проекта, включая tests/")
    p.add_argument("--to", metavar="DIR", help="каталог вместо корня репозитория (пишутся DIR/src, DIR/tests)")
    p.add_argument("--force", action="store_true", help="перезаписать src/, даже если исходники менялись после импорта")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("build", help="импорт и сборка (build/build.log, build/build.json)")
    p.add_argument("--tests", action="store_true", help="собрать тестовый проект")
    p.add_argument("--no-import", action="store_true", help="собрать проект как есть, без импорта")
    p.add_argument("--force", action="store_true", help="импортировать, даже если проект сохранён в IDE")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("test", help="импорт с тестами, сборка, прогон coUnit в рантайме")
    p.add_argument("--restart-runtime", action="store_true", help="перезапустить рантайм перед прогоном")
    p.add_argument("--no-runtime-start", action="store_true", help="не запускать рантайм автоматически")
    p.add_argument("--clean", action="store_true", help="пересоздать тестовый проект с нуля")
    p.add_argument("--force", action="store_true", help="импортировать, даже если проект сохранён в IDE")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("roundtrip", help="импорт -> экспорт во временный каталог -> сравнение с src/ и tests/")
    p.set_defaults(func=cmd_roundtrip)

    p = sub.add_parser("runtime", help="пользовательский процесс CODESYS Control Win V3")
    p.add_argument("action", choices=("start", "stop", "status", "restart", "init"))
    p.set_defaults(func=cmd_runtime)

    p = sub.add_parser("junit", help="build/test-results.json -> build/junit.xml")
    p.add_argument("--results", metavar="FILE")
    p.add_argument("--out", metavar="FILE")
    p.set_defaults(func=cmd_junit)

    p = sub.add_parser("report", help="обновить раздел последнего прогона в docs/TEST_REPORT.md")
    p.add_argument("--results", metavar="FILE")
    p.add_argument("--doc", metavar="FILE")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        settings = make_settings()
        code: int = args.func(settings, args)
        return code
    except CdsError as exc:
        print(f"[cds] ОШИБКА: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("[cds] прервано", file=sys.stderr)
        return EXIT_FAILED
