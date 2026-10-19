// Назначение: копирует итоги coUnit (CfUnit) 1.2.0.0 в GVL_TestResults — один раз, когда
//             все наборы завершены и coUnit сохранил общие результаты. Считает
//             пропущенные тесты и записывает подробности первых упавших. Вызывать в
//             PRG_Tests каждый цикл после coUnit.RUN(). Читает внутренние переменные
//             раннера coUnit 1.2.0.0 (проверено в CODESYS 3.5.17.30): при смене версии
//             coUnit проверить пути GVL_coUnit.coUnitRunner.TestResults.
// Автор:      kr0nsch
// Версия:     0.1.0
// Документ:   docs/cfunit_results.md
FUNCTION_BLOCK FB_TestResultsCollector
VAR_OUTPUT
    xDone : BOOL;               // итоги скопированы в GVL_TestResults
END_VAR
VAR
    refResults : REFERENCE TO coUnit.ST_TestSuiteResults;   // итоги раннера coUnit
    uiSuite : UINT;             // индекс набора
    uiTest : UINT;              // индекс теста в наборе
END_VAR
