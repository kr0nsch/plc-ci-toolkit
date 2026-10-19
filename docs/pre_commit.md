# pre-commit: проверки перед коммитом

Те же проверки, что в CI, но до коммита и за секунды: ruff, линтер ST `st_lint` и проверка
«исходники выгружены» — в коммите нет правок проекта CODESYS, которые не попали в `src/` и `tests/`.
Пример настройки для репозитория на шаблоне — [examples/consumer/.pre-commit-config.yaml](../examples/consumer/.pre-commit-config.yaml),
описание опубликованного хука — [.pre-commit-hooks.yaml](../.pre-commit-hooks.yaml).

## Подключение

1. Скопировать `examples/consumer/.pre-commit-config.yaml` в корень своего репозитория. В `rev:`
   для `plc-ci-toolkit` — точный тег выпуска `v1.x.y` (до первого выпуска — SHA коммита `main`), а не
   плавающий `v1`, как у workflow: pre-commit хранит копию хука по значению `rev` и не заметит, что
   тег сдвинулся.
2. Установить pre-commit и хуки (один раз в каждом клоне; Python 3.11+):

   ```bat
   python -m pip install pre-commit
   python -m pre_commit install
   ```

   В активированном venv pre-commit ставится в него (ключ `--user` там не работает: «Can not
   perform a '--user' install»); без venv, если общий каталог пакетов закрыт на запись, pip сам
   ставит пакет для пользователя. Форма `python -m pre_commit` работает, даже если каталог
   `Scripts` пользователя не в `PATH`.
   Первый запуск скачивает ruff и ставит хуки в `%USERPROFILE%\.cache\pre-commit` — нужен доступ к
   GitHub и PyPI; дальше хуки работают без сети.
3. Проверить все файлы: `python -m pre_commit run --all-files`. Обновить версии хуков:
   `python -m pre_commit autoupdate` (версию ruff держите равной `requirements-dev.txt` — так её
   проверяет CI).

Проверено на Windows 11, Python 3.12, pre-commit 4.7.0: установка опубликованного хука из
репозитория plc-ci-toolkit (`pip install .`), коммиты в cmd и Git Bash, сценарии таблицы ниже — с
настоящим CODESYS 3.5.17.30 (`cds.py import`, `import --tests`, `export`, `export --tests`).

## Хуки

| Хук | Когда запускается | Что проверяет | Как в CI |
|---|---|---|---|
| `ruff-check`, `ruff-format` | изменены файлы Python (и Markdown — `ruff format`) | правила и формат `pyproject.toml` | `ruff check .`, `ruff format --check .` (та же версия) |
| `st-lint` (локальный) | изменены файлы `.st` или `tools/check/st_lint.toml` | `python tools/check/st_lint.py --strict`: вся раскладка `src/` и `tests/` — правила раскладки (LAY004, LAY006) касаются нескольких файлов, поэтому линтер получает каталоги, а не список файлов | тот же вызов |
| `codesys-sources-exported` | при каждом коммите | проекты CODESYS в `build/` не сохранялись в IDE после последнего `cds.py import/export`; в коммите нет `.project`, `.projectarchive`, `.library`, `.compiled-library` | в CI проектов CODESYS нет — проверка только локальная |

`ruff format` в 0.17 форматирует и Markdown (блоки кода Python в документах) — как и
`ruff format --check .` в CI.

## «Исходники выгружены»: как работает

Проект CODESYS собирается из `src/` и `tests/` командой `python tools/codesys/cds.py import` и в git
не хранится. Правки, сделанные в IDE, живут только в `build/<имя>.project`, пока их не выгрузят
командой `python tools/codesys/cds.py export` (тестовый проект — `export --tests`).

После каждого успешного `import` и `export` (а также `build` и `test`, которые начинаются с импорта)
`cds.py` записывает в `build/codesys/state.json` отпечаток проекта — время изменения (`mtime_ns`) и
размер файла — и SHA-256 исходников. Хук сравнивает отпечаток с текущим файлом. Это та же проверка,
которой `cds.py import` защищает проект от перезаписи: хук останавливает коммит ровно тогда, когда
`import` отказался бы перезаписать проект (тест `test_hook_agrees_with_cds_import_guard`).

Какие проекты проверяются: все `build/*.project`, пути проектов из настроек `cds.py`
(`tools/codesys/config.json`, переменные `CDS_PROJECT`, `CDS_TEST_PROJECT`, `CDS_PROJECT_NAME` —
хук разрешает их тем же кодом `cds.py`) и записи `state.json` внутри репозитория. Своё состояние хук
не ведёт и ничего не записывает; CODESYS для проверки не нужен.

| Ситуация | Результат | Что делать |
|---|---|---|
| нет `build/` или проектов (свежий клон, CI) | успех | — |
| проект не менялся после `import`/`export` | успех | — |
| проект сохранён в IDE после `import`/`export` | **коммит остановлен** | `python tools/codesys/cds.py export` (или `export --tests`), проверить `git status`, добавить изменения в коммит. Правки не нужны — `cds.py import --force` |
| то же, и `src/` тоже менялись после `import`/`export` | **коммит остановлен**, подсказка про конфликт | `cds.py export` откажется перезаписывать `src/`: `python tools/codesys/cds.py export --to build/export-check`, перенести правки вручную |
| проект есть, записи `cds.py` о нём нет (удалён `state.json`, проект скопирован вручную, клон перенесён в другой каталог) | **коммит остановлен** | проверить выгрузкой `export --to build/export-check`, затем `export`; если в IDE не правили — `cds.py import` (запись появится) |
| в коммите `.project`, `.projectarchive`, `.library`, `.compiled-library` | **коммит остановлен** | `git rm --cached -- <файл>`; правки из IDE — `cds.py export` |
| проект открыт в IDE (есть `<проект>.~u`) | успех с предупреждением | несохранённые правки хук не видит: сохранить, закрыть IDE, `cds.py export` |

Пример сообщения:

```text
ОШИБКА: build/plc-process-lib.project: проект сохранён в IDE после последнего cds.py import/export — правки не выгружены в src/.
  Чтобы сохранить правки, выполните python tools/codesys/cds.py export и добавьте изменения src/ в коммит (git status).
  Правки из IDE не нужны — python tools/codesys/cds.py import --force (проект пересоберётся из src/).
codesys-sources-exported: коммит остановлен, ошибок: 1. Описание проверки — https://github.com/kr0nsch/plc-ci-toolkit/blob/main/docs/pre_commit.md
```

Параметры хука (`args:` в `.pre-commit-config.yaml`): `--allow-unknown` — проект без записи `cds.py`
даёт предупреждение вместо ошибки. Запуск без pre-commit:
`python ci/check_exported.py [--root DIR] [--allow-unknown] [файлы коммита...]` — код выхода 0 или 1.

## Ограничения

- Несохранённые правки в открытой IDE не видны: хук только предупреждает, если проект открыт.
- Отпечаток — время изменения и размер файла, как у `cds.py`. Если файл проекта трогали без правок
  (копирование, сохранение без изменений), хук остановит коммит; `cds.py export` без изменений в
  `src/` снимет блокировку за 10–20 секунд.
- Хук не проверяет, что в коммит добавлены **все** выгруженные файлы: после `export` смотрите
  `git status`.
- Объекты, которых нет в раскладке (визуализации, дерево устройств, `ACTION`, графические языки),
  `export` не выгружает и предупреждает о них — их правки хук не отличает от остальных
  ([MANUAL_STEPS.md](MANUAL_STEPS.md#6-работа-в-ide-и-экспорт-перед-коммитом)).
- Формат `build/codesys/state.json` принадлежит `cds.py` шаблона. Совместимость с версией шаблона в
  этом репозитории проверяют тесты `ci/tests/test_check_exported.py` (записи делает настоящий
  `cdshost/cli.py`). Если `tools/codesys` в репозитории-потребителе другой версии и не загружается,
  хук работает по `build/*.project` и `state.json`, а режим проекта определяет по имени
  (`<имя>-tests.project` — тестовый).

## Пропустить проверку

Только осознанно и для одного коммита — например, коммит одной документации, когда проект в IDE
заведомо не правили, а запись `cds.py` потеряна:

```bat
set SKIP=codesys-sources-exported
git commit -m "docs: ..."
set SKIP=
```

В PowerShell — `$env:SKIP = "codesys-sources-exported"`, в Git Bash —
`SKIP=codesys-sources-exported git commit ...`. `git commit --no-verify` отключает все хуки сразу —
лучше не использовать.
