// Назначение: зонд статанализа (намеренно плохой код)
{attribute 'qualified_only'}
VAR_GLOBAL
    diShared : DINT;                // SA0006: пишут две задачи
    wOverlapA AT %MW10 : WORD;      // SA0028: перекрывается с bOverlapB
    bOverlapB AT %MB21 : BYTE;
    xOutBad AT %QX0.0 : BOOL;       // SA0004: запись в двух местах
    iGlobalUnused : INT;            // SA0033: глобальная не используется
END_VAR
