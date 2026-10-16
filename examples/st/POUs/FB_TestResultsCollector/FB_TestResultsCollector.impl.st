// Итоги копируются один раз: счётчики coUnit заполняются только после завершения всех
// наборов, до этого они равны 0
IF xDone
        OR NOT coUnit.GVL_coUnit.coUnitRunner.AllTestSuitesFinished
        OR NOT coUnit.GVL_coUnit.coUnitRunner.TestResults.StoredGeneralTestResults THEN
    RETURN;
END_IF

refResults REF= coUnit.GVL_coUnit.coUnitRunner.TestResults.TestSuiteResults;
GVL_TestResults.uiSuites := refResults.NumberOfTestSuites;
GVL_TestResults.uiTests := refResults.NumberOfTestCases;
GVL_TestResults.uiFailed := refResults.NumberOfFailedTestCases;
GVL_TestResults.uiSkipped := 0;
GVL_TestResults.uiFailuresStored := 0;

// По наборам и тестам: пропущенные (DISABLED_...) и подробности первых упавших.
// NumberOfTests набора — поддиапазонный тип: read_value его не читает, а код ST читает.
FOR uiSuite := 1 TO refResults.NumberOfTestSuites DO
    FOR uiTest := 1 TO refResults.TestSuiteResults[uiSuite].NumberOfTests DO
        IF refResults.TestSuiteResults[uiSuite].TestCaseResults[uiTest].TestIsSkipped THEN
            GVL_TestResults.uiSkipped := GVL_TestResults.uiSkipped + 1;
        ELSIF refResults.TestSuiteResults[uiSuite].TestCaseResults[uiTest].TestIsFailed
                AND GVL_TestResults.uiFailuresStored < UPPER_BOUND(GVL_TestResults.aFailures, 1) THEN
            GVL_TestResults.uiFailuresStored := GVL_TestResults.uiFailuresStored + 1;
            GVL_TestResults.aFailures[GVL_TestResults.uiFailuresStored].sSuite :=
                refResults.TestSuiteResults[uiSuite].Name;
            GVL_TestResults.aFailures[GVL_TestResults.uiFailuresStored].sTest :=
                refResults.TestSuiteResults[uiSuite].TestCaseResults[uiTest].TestName;
            GVL_TestResults.aFailures[GVL_TestResults.uiFailuresStored].sMessage :=
                refResults.TestSuiteResults[uiSuite].TestCaseResults[uiTest].FailureMessage;
        END_IF
    END_FOR
END_FOR

// coUnit считает пропущенные тесты успешными (NumberOfSuccessfulTestCases), здесь — без них
GVL_TestResults.uiPassed := GVL_TestResults.uiTests - GVL_TestResults.uiFailed - GVL_TestResults.uiSkipped;
GVL_TestResults.xFinished := TRUE;
xDone := TRUE;
