// Назначение: итоги юнит-тестов coUnit (CfUnit) 1.2.0.0 в простых типах — для скриптов
//             (read_value), HMI, OPC UA и окна наблюдения IDE. Заполняет
//             FB_TestResultsCollector один раз, когда coUnit сохранил итоги. Обёртка
//             необязательна: cds.py test читает результаты из coUnit напрямую.
// Автор:      kr0nsch
// Версия:     0.1.0
// Документ:   docs/cfunit_results.md
{attribute 'qualified_only'}
VAR_GLOBAL
    xFinished : BOOL;           // итоги заполнены: все наборы завершены, coUnit сохранил результаты
    uiSuites : UINT;            // наборов тестов
    uiTests : UINT;             // тестов всего
    uiPassed : UINT;            // пройдено: всего - упало - пропущено
    uiFailed : UINT;            // упало
    uiSkipped : UINT;           // пропущено (тесты DISABLED_...)
    uiFailuresStored : UINT;    // заполнено элементов aFailures (не больше размера массива)
    aFailures : ARRAY[1..10] OF ST_TestFailure;     // первые упавшие тесты
END_VAR
