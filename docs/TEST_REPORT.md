# Отчёт о тестировании

Результаты юнит-тестов CfUnit (coUnit 1.2.0.0) в CODESYS Control Win V3 и проверок без CODESYS.
Запись «проверено в CODESYS» появляется только после реального прогона. Раздел «Последний прогон
в CODESYS» обновляет `python tools/codesys/cds.py report` по итогам `cds.py test`
(`build/test-results.json`); правки внутри него вручную перезаписываются.

<!-- cds:test-report:begin (раздел обновляет `python tools/codesys/cds.py report`) -->
## Последний прогон в CODESYS

Проверено в CODESYS 3.5.17.30 / CODESYS Control Win V3 (пользовательский процесс) — 2026-10-16 01:03:11. Итог: **все тесты пройдены**.

| Параметр | Значение |
|---|---|
| Дата прогона | 2026-10-16 01:03:11 |
| Коммит | e3e7513-dirty |
| CODESYS | 3.5.17.30 (профиль `CODESYS V3.5 SP17 Patch 3`) |
| Устройство в проекте | CODESYS Control Win V3 (4096 / 0000 0001 / 3.5.17.30) |
| Рантайм | CODESYS Control Win V3 (пользовательский процесс) |
| Фреймворк тестов | coUnit (CfUnit) 1.2.0.0 |
| Сборка | ошибок 0, предупреждений 0 |
| Способ запуска | `python tools/codesys/cds.py test` |

| Набор | Тестов | Пройдено | Упало | Пропущено | Итог |
|---|---|---|---|---|---|
| `PRG_Tests.fbF_ClampTest` | 7 | 7 | 0 | 0 | пройдено |
| `PRG_Tests.fbExampleTest` | 11 | 11 | 0 | 0 | пройдено |
| **Всего** | 18 | 18 | 0 | 0 | **пройдено** |

### Результаты тестов

| Набор | Тест | Итог |
|---|---|---|
| `PRG_Tests.fbF_ClampTest` | `InsideRangeReturnsValue` | пройден |
| `PRG_Tests.fbF_ClampTest` | `BelowLowReturnsLow` | пройден |
| `PRG_Tests.fbF_ClampTest` | `AboveHighReturnsHigh` | пройден |
| `PRG_Tests.fbF_ClampTest` | `AtLowBoundaryReturnsLow` | пройден |
| `PRG_Tests.fbF_ClampTest` | `AtHighBoundaryReturnsHigh` | пройден |
| `PRG_Tests.fbF_ClampTest` | `EqualBoundsReturnsBound` | пройден |
| `PRG_Tests.fbF_ClampTest` | `LowGreaterThanHighSwapsBounds` | пройден |
| `PRG_Tests.fbExampleTest` | `AutoSwitchesOnBelowThreshold` | пройден |
| `PRG_Tests.fbExampleTest` | `AutoThresholdsAreInclusive` | пройден |
| `PRG_Tests.fbExampleTest` | `AutoHoldsStateInsideHysteresis` | пройден |
| `PRG_Tests.fbExampleTest` | `InvalidValueLatchesFault` | пройден |
| `PRG_Tests.fbExampleTest` | `AcknowledgeClearsFaultWhenValid` | пройден |
| `PRG_Tests.fbExampleTest` | `AcknowledgeKeepsFaultWhileInvalid` | пройден |
| `PRG_Tests.fbExampleTest` | `ManualFollowsCommand` | пройден |
| `PRG_Tests.fbExampleTest` | `LocalAndOutOfServiceSwitchOff` | пройден |
| `PRG_Tests.fbExampleTest` | `DefaultModeIsOutOfService` | пройден |
| `PRG_Tests.fbExampleTest` | `ParametersAreClamped` | пройден |
| `PRG_Tests.fbExampleTest` | `CountsSwitchOnsAndResetClears` | пройден |

### Упавшие тесты

_Нет._

Машиночитаемые результаты: `build/test-results.json`, `build/junit.xml` (не в git).
<!-- cds:test-report:end -->

## Что проверяют тесты

| Набор | Тест | Что проверяет |
|---|---|---|
| `FB_F_Clamp_Test` | `InsideRangeReturnsValue` | значение внутри диапазона не меняется |
| `FB_F_Clamp_Test` | `BelowLowReturnsLow` | значение ниже диапазона → нижняя граница |
| `FB_F_Clamp_Test` | `AboveHighReturnsHigh` | значение выше диапазона → верхняя граница |
| `FB_F_Clamp_Test` | `AtLowBoundaryReturnsLow` | значение на нижней границе не меняется |
| `FB_F_Clamp_Test` | `AtHighBoundaryReturnsHigh` | значение на верхней границе не меняется |
| `FB_F_Clamp_Test` | `EqualBoundsReturnsBound` | вырожденный диапазон `rLow = rHigh` |
| `FB_F_Clamp_Test` | `LowGreaterThanHighSwapsBounds` | `rLow > rHigh`: границы меняются местами |
| `FB_Example_Test` | `AutoSwitchesOnBelowThreshold` | авторежим: значение ниже порога включения (уставка − гистерезис) включает выход, отказа нет |
| `FB_Example_Test` | `AutoThresholdsAreInclusive` | авторежим: на пороге включения выход включается, на уставке — отключается |
| `FB_Example_Test` | `AutoHoldsStateInsideHysteresis` | авторежим: в зоне гистерезиса выход сохраняет состояние (после включения и после отключения) |
| `FB_Example_Test` | `InvalidValueLatchesFault` | недостоверное измерение в авторежиме: отказ, выход отключён; отказ держится после восстановления измерения |
| `FB_Example_Test` | `AcknowledgeClearsFaultWhenValid` | `Acknowledge()` при достоверном измерении снимает отказ и возвращает TRUE, выход снова управляется |
| `FB_Example_Test` | `AcknowledgeKeepsFaultWhileInvalid` | `Acknowledge()` при недостоверном измерении возвращает FALSE, отказ остаётся |
| `FB_Example_Test` | `ManualFollowsCommand` | ручной режим: выход повторяет `xManualOn`, недостоверное измерение отказа не даёт |
| `FB_Example_Test` | `LocalAndOutOfServiceSwitchOff` | `Local` и `OutOfService` отключают выход при любых измерении и команде |
| `FB_Example_Test` | `DefaultModeIsOutOfService` | режим по умолчанию — `OutOfService`: выход отключён |
| `FB_Example_Test` | `ParametersAreClamped` | уставка и гистерезис вне диапазона ограничиваются `EXAMPLE_SP_MAX_PCT` и `EXAMPLE_HYST_MIN_PCT` |
| `FB_Example_Test` | `CountsSwitchOnsAndResetClears` | счётчик `Count` считает только фронты включения; `Reset()` через `I_Resettable` отключает выход и обнуляет счётчик |

## Проверки без CODESYS

Последний прогон CI на GitHub — [39259547098](https://github.com/kr0nsch/plc-ci-toolkit/actions/runs/39259547098)
(19.10.2026, коммит `3bfe95a`, задание `plc-static (пример ST)`); локально — та же версия `main`,
Windows 11, Python 3.12, iec_checker v0.4 и RuSTy v1.0.6 (Windows-сборки).

| Проверка | GitHub, 19.10.2026 | Локально, 19.10.2026 | Примечание |
|---|---|---|---|
| `ruff check`, `ruff format --check`, `mypy` | пройдено | пройдено | mypy: 29 файлов без замечаний |
| `pytest` | 682 пройдено, 4 пропущено | 686 пройдено | на GitHub RuSTy загружается после pytest, его интеграционные тесты пропускаются ([ASSUMPTIONS.md](ASSUMPTIONS.md#переиспользуемые-workflow-plc-ci-toolkit), W9); без iec_checker и RuSTy локально — 676 пройдено, 10 пропущено |
| `tools/assemble.py --tests` | пройдено | пройдено | 11 объектов: 8 из `src/`, 3 из `tests/` |
| `tools/check/st_lint.py --strict` | пройдено | пройдено | 60 файлов, 0 замечаний |
| iec_checker v0.4 (`run_iec_checker.py --tests`) | пройдено | пройдено | 11 файлов, ошибок разбора нет; необоснованных находок 0, обоснованных 100 — разбор в [STATIC_ANALYSIS.md](STATIC_ANALYSIS.md) |
| RuSTy 1.0.6 (`run_rusty.py`) | пройдено | пройдено | 11 файлов (`src/` и `tests/` с прокладкой coUnit), исключений нет, 0 ошибок |
| actionlint 1.7.12 | пройдено | не запускался | четыре workflow и `examples/consumer-ci.yml` |
| `pytest ci/tests` | пройдено: Ubuntu (Python 3.11, 3.13), Windows (3.11) | входит в `pytest` | скрипты `ci/` |

Первый push на GitHub ([39248140323](https://github.com/kr0nsch/plc-ci-toolkit/actions/runs/39248140323),
коммит `5a0849e`) упал на шаге mypy в заданиях `plc-static (пример ST)` и `Скрипты ci/ (ubuntu-latest,
Python 3.11)`: под Linux mypy считал недостижимым код для Windows. Исправлено в `1aee06c` (`platform =
"win32"` в `pyproject.toml`); с прогона [39248625868](https://github.com/kr0nsch/plc-ci-toolkit/actions/runs/39248625868)
CI в `main` зелёный.

## Пробные прогоны workflow

Шаги обоих переиспользуемых workflow выполнялись локально в том же порядке и с теми же условиями
`if:`, на собственном примере ST. На настоящем self-hosted раннере GitHub Windows-задание ещё не
выполнялось ([MANUAL_STEPS.md, разделы 9–13](MANUAL_STEPS.md#9-self-hosted-раннер-порядок-действий)).

**16.10.2026.**

- Linux-задание — в Git Bash, с Windows-сборками iec_checker v0.4 и RuSTy v1.0.6 вместо загрузки
  Linux-бинарников: все 17 шагов зелёные, pytest — 463 теста, `st_lint` — 0 замечаний,
  iec_checker — 0 необоснованных находок, RuSTy — 0 ошибок. С внесённым «магическим числом»
  задание падает, сводка и аннотация указывают на файл и строку.
- Windows-задание — в той же обёртке Windows PowerShell 5.1, что у раннера, с CODESYS 3.5.17.30 и
  Control Win V3: Static Analysis Light — 0 находок, рантайм перезапущен (окно 120 мин),
  18 из 18 тестов coUnit, JUnit, `result=success`. Со сломанным тестом задание падает, JUnit и
  сводка называют тест и сообщение coUnit; с ошибкой компиляции — аннотация на строке в `src/`;
  без CODESYS шаг падает с кодом 3, и этот код виден в журнале шага.
- Подключение к чистому репозиторию из шаблона по [USAGE.md](USAGE.md) (копия
  `examples/consumer-ci.yml`, CI шаблона удалён): оба задания зелёные — 311 тестов pytest шаблона,
  0 находок Static Analysis Light (правила — по умолчанию из plc-ci-toolkit, базовой линии нет),
  18 из 18 тестов coUnit; Windows-задание — и в каталоге с пробелами и скобками в пути.

**19.10.2026** — после синхронизации инструментов с шаблоном; с этих прогонов сняты GIF в README:
Linux-задание — pytest 680 тестов, `st_lint` 0 замечаний, iec_checker 0 необоснованных находок,
RuSTy 0 ошибок; Windows-задание — Static Analysis Light 0 находок, сборка без ошибок, 18 из 18
тестов coUnit, JUnit XML; pre-commit останавливает коммит с «магическим числом». Позже в тот же день
в `main` добавлены тесты ключа `checker` (`run_iec_checker.py`) — сейчас pytest собирает 686 тестов.
