// Назначение: зонд статанализа — объект без анализа
{attribute 'no-analysis'}
FUNCTION_BLOCK FB_SaSkip
VAR_INPUT
    xIn : BOOL;
END_VAR
VAR
    iSkippedUnused : INT;           // SA0033 не должно быть: no-analysis
END_VAR
