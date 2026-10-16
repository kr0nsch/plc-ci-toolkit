// Назначение: подробности упавшего теста coUnit (CfUnit) — набор, тест и сообщение
//             первой упавшей проверки. Элемент массива GVL_TestResults.aFailures.
// Автор:      <ИМЯ ФАМИЛИЯ>
// Версия:     0.1.0
// Документ:   docs/cfunit_results.md
TYPE ST_TestFailure :
STRUCT
    sSuite : STRING(255);       // путь экземпляра набора: Device.Application.PRG_Tests.fbExampleTest
    sTest : STRING(255);        // имя теста из coUnit.TEST('...')
    sMessage : STRING(255);     // сообщение первой упавшей проверки (coUnit обрезает до 253 символов)
END_STRUCT
END_TYPE
