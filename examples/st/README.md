# Пример ST: обёртка `GVL_TestResults` над итогами coUnit

Необязательная обёртка: итоги юнит-тестов coUnit (CfUnit) 1.2.0.0 в простых типах для HMI, OPC UA,
окна наблюдения IDE или своего скрипта. `cds.py test` она не нужна — он читает результаты из coUnit
напрямую. Зачем и когда использовать — [docs/cfunit_results.md](../../docs/cfunit_results.md).

Файлы — в раскладке [docs/LAYOUT.md](../../docs/LAYOUT.md):

| Файл | Объект |
|---|---|
| `GVLs/GVL_TestResults.gvl.st` | итоги: `xFinished`, `uiSuites`, `uiTests`, `uiPassed`, `uiFailed`, `uiSkipped`, `uiFailuresStored`, `aFailures[1..10]` |
| `DUTs/ST_TestFailure.dut.st` | подробности упавшего теста: `sSuite`, `sTest`, `sMessage` |
| `POUs/FB_TestResultsCollector/` | сборщик: один раз после сохранения итогов coUnit копирует их в `GVL_TestResults` |

Подключение к тестовому проекту репозитория из шаблона:

1. Скопировать `GVLs/`, `DUTs/` и `POUs/` этого каталога в `tests/` (рядом с наборами тестов).
2. В `tests/POUs/PRG_Tests/PRG_Tests.decl.st` объявить `fbResults : FB_TestResultsCollector;`, а в
   `PRG_Tests.impl.st` после `coUnit.RUN();` вызвать `fbResults();`.
3. `python tools/codesys/cds.py test` — как обычно; итоги — `GVL_TestResults.*` (например,
   `read_value('GVL_TestResults.uiFailed')`).

Проверено в CODESYS 3.5.17.30 / CODESYS Control Win V3 с coUnit 1.2.0.0 (16.10.2026): 3 набора,
21 тест — `uiPassed = 19`, `uiFailed = 1`, `uiSkipped = 1`, подробности упавшего теста в
`aFailures[1]`. Проверка `python tools/check/st_lint.py --root examples st --strict` — без замечаний.
