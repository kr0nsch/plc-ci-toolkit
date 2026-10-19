# Changelog

Все заметные изменения проекта записываются в этот файл.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по
[Semantic Versioning](https://semver.org/lang/ru/spec/v2.0.0.html).

## [Unreleased]

Первый выпуск plc-ci-toolkit. Репозиторий создан из шаблона plc-repo-template: записи про `tools/`,
`src/` и `tests/` — история шаблона (здесь эти файлы — тестовый стенд workflow и не меняются).

### Added

- Переиспользуемый workflow `.github/workflows/plc-static.yml` (`workflow_call`, GitHub-hosted Linux):
  ruff, ruff format, mypy, pytest, `assemble`, `st_lint --strict`, iec_checker (`binary` — v0.4 по
  размеру и SHA-256, `docker` — образ nightly по дайджесту, `off`), RuSTy v1.0.6 (необязательно);
  входы `python-version`, `python-checks`, `requirements`, `paths`, `iec-checker`, `fail-on-iec-checker`,
  `rusty`, `fail-on-rusty`, `working-directory`, `artifact-name`, `timeout-minutes`; выходы `result`,
  `st-lint-errors`, `iec-checker-errors`, `rusty-errors`. Кэш pip — только если файл зависимостей
  есть: без него понятную ошибку даёт шаг зависимостей, остальные проверки и сводка выполняются.
- Переиспользуемый workflow `.github/workflows/plc-codesys.yml` (self-hosted Windows с CODESYS,
  метки `self-hosted`, `windows`, `codesys`): задание `guard` на GitHub-hosted раннере пропускает
  только `push` в `main` и теги, `workflow_dispatch`, `schedule` и не форки; затем проверка раннера,
  Static Analysis Light, `cds.py runtime restart` (новое окно демо-режима 2 ч), `cds.py test --clean`,
  `cds.py junit`, публикация JUnit (`mikepenz/action-junit-report`, только аннотации и сводка — права
  на чтение), сводка, `cds.py runtime stop`, артефакты журналов. Очередь `concurrency` без отмены
  ожидающих, таймаут задания меньше 110 минут; выходы — счётчики тестов и сборки. Шаги — Windows
  PowerShell 5.1 через `-File`: код выхода `cds.py` (3 — окружение, 4 — таймаут, 5 — блокировка)
  доходит до журнала шага, а не сводится к 1.
- `ci/summary.py` — сводка проверок в `$GITHUB_STEP_SUMMARY`, аннотации с файлом и строкой (st_lint,
  iec_checker, RuSTy, ошибки компилятора CODESYS) и выходы шага; `ci/runner_check.py` — проверка
  self-hosted раннера; тесты `ci/tests/` на настоящих отчётах инструментов.
- `examples/consumer-ci.yml` — пример CI репозитория-потребителя (общая группа `concurrency` только
  у pull request: ожидающий запуск `main` не отменяется следующим push), `docs/USAGE.md` — подключение за
  10 минут (с заменой CI шаблона), входы и выходы, политика запуска, бейджи; `.github/actionlint.yaml`, `.github/dependabot.yml`.
- Все actions закреплены по полному SHA коммита (`actions/checkout` v7.0.1, `actions/setup-python`
  v7.0.0, `actions/upload-artifact` v7.0.2, `mikepenz/action-junit-report` v6.6.1).
- Static Analysis Light CODESYS в CI: `ci/sa_check.py` (с `ci/codesys/sa_light.py`) включает шесть
  правил Light на время сеанса и генерирует код, `ci/sa_report.py` — отчёт Markdown/JSON по правилам
  с файлом и строкой, базовая линия `ci/sa_baseline.json`, политика `--fail-on new|any|never`,
  аннотации GitHub Actions; правила — `ci/sa_rules.json`. Проверено в CODESYS 3.5.17.30;
  исследование и ручной шаг — `docs/sa_light.md`, `docs/MANUAL_STEPS.md` (раздел 16).
- `docs/cfunit_results.md`: как coUnit 1.2.0.0 отдаёт результаты (журнал, xUnit XML, переменные) и
  почему CI читает переменные; необязательная обёртка `GVL_TestResults` + `FB_TestResultsCollector`
  в `examples/st/` (проверена в Control Win V3).
- pre-commit: хук `codesys-sources-exported` (`ci/check_exported.py`, `.pre-commit-hooks.yaml`) —
  коммит останавливается, если проект CODESYS сохранён в IDE после последнего `cds.py import/export`
  или в коммит попали `.project`/`.library`; пример для потребителей
  `examples/consumer/.pre-commit-config.yaml` (ruff, `st_lint`, хук), свой `.pre-commit-config.yaml`,
  `docs/pre_commit.md`. Проверено с CODESYS 3.5.17.30 и pre-commit 4.7.0.
- Документы раннера: `docs/runner_setup.md` (учётная запись, CODESYS, рантайм как процесс или служба,
  переменные, демо-режим 2 ч), `docs/runner_security.md` (модель угроз, меры, чек-лист),
  `docs/MANUAL_STEPS.md` — регистрация раннера и первый прогон Windows-job (разделы 9–15, 17).
- README: баннер (`docs/media/banner-*.svg`), бейджи, навигация и подвал портфолио; демонстрация —
  схема конвейера с итогами локального прогона (`docs/media/diagram-pipeline-*.png`), GIF реальных
  прогонов Linux-job (`demo-static-job.gif`), Static Analysis Light (`demo-codesys-sa.gif`), сборки и
  тестов coUnit в Control Win V3 (`demo-codesys-job.gif`), остановки коммита хуком pre-commit
  (`demo-precommit-fail.gif`) и сводка задания с упавшим тестом (`docs/media/shot-codesys-summary-*.png`);
  схема пайплайна, подключение за 10 минут, безопасность раннера. `docs/ARCHITECTURE.md` — устройство
  workflow, порядок шагов Windows-job, кто пишет и читает отчёты `build/`.
- Раскладка исходников ST в `src/` и `tests/` и её формальный контракт `docs/LAYOUT.md`.
- Пример: `E_DeviceMode`, `ST_ExampleCmd`, `ST_ExampleSts`, `GVL_Const`, интерфейс `I_Resettable`,
  функция `F_Clamp`, `FB_Example` (метод `Acknowledge`, реализация `Reset`, свойство `Count`), `PRG_Main`.
- Тесты CfUnit (coUnit 1.2.0.0): наборы `FB_F_Clamp_Test` (7 тестов), `FB_Example_Test` (11 тестов:
  пороги и гистерезис, отказ и квитирование, режимы, ограничение параметров, счётчик, сброс через
  `I_Resettable`) и `PRG_Tests`.
- Инструменты: `tools/assemble.py`, `tools/check/` (линтер `st_lint.py`, запуск iec_checker и RuSTy),
  скрипты CODESYS Scripting в `tools/codesys/`.
- `tools/codesys/cds.py` — импорт, сборка, тесты coUnit в CODESYS Control Win V3 (пользовательский
  процесс), экспорт, roundtrip, JUnit и отчёт из консоли; настройки — `tools/codesys/config.json`.
  Проверено в CODESYS 3.5.17.30.
- CI GitHub Actions: ruff, mypy, pytest, сборка ST, линтер (`--strict`), iec_checker v0.4 (закреплён
  по размеру и SHA-256); необязательно — iec_checker `nightly` (образ закреплён по дайджесту) и RuSTy.
- Документы: архитектура, ручные шаги, допущения, отчёты статанализа и тестов, шаблоны ТЗ
  (ГОСТ 34.602-2020) и ПМИ.
- `LICENSE` (MIT), `.gitignore`, `.gitattributes`, `.editorconfig`.

### Changed

- `ci/summary.py`: Markdown из `--extra-md` встраивается так, что его старший заголовок — раздел `###`
  наравне со «Сборкой» и «Тестами coUnit» (отчёт Static Analysis Light больше не выглядит
  подразделом тестов).
- CI plc-ci-toolkit (`.github/workflows/ci.yml`): actionlint 1.7.12 для workflow и примера, тесты
  `ci/` на Linux и Windows, самопроверка `plc-static.yml` локальным вызовом на примере ST (и с
  образом iec_checker nightly — необязательно) вместо собственных шагов шаблона. Windows-job на
  примере ST — отдельный workflow `codesys.yml` (push в `main`, теги `v*`, вручную), включается
  переменной репозитория `PLC_CODESYS_RUNNER`.

### Fixed

- Тесты coUnit: функции `TEST` и `TEST_FINISHED` вызываются с пространством имён (`coUnit.TEST(...)`) —
  без него сборка в CODESYS 3.5.17.30 давала ошибки C0046/C0035.
- `cds.py build`/`test`: перед открытием проекта удаляется кэш предкомпиляции CODESYS — с ним
  сломанное объявление ФБ с методами собиралось с «0 ошибок», а исправленное давало старую ошибку.
- `cds.py`: устаревший файл блокировки `<проект>.~u` (CODESYS остановлен по таймауту или упал)
  удаляется, проект, открытый в IDE, — понятный отказ с кодом 3 вместо трассировки.
- `cds.py import`: переименование только регистром (файл, метод, свойство, папка) доходит до проекта.
- `cds.py test`: старые `build/junit.xml` и `build/test-results.json` удаляются перед прогоном;
  неудачный вход в рантайм — одна строка с причиной и код 3 вместо трассировки; имя пользователя
  рантайма не пишется в журнал.
- `docs/MANUAL_STEPS.md`, `README.md`: команды без `& rem` — работают в cmd, PowerShell и Git Bash.
