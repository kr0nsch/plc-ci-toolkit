// Назначение: зонд статанализа (намеренно плохой код)
{attribute 'analysis' := '-167'}
FUNCTION F_SaTemp : BOOL
VAR_INPUT
    xIn : BOOL;
END_VAR
VAR
    fbDelay : FB_Example;           // SA0167: временный экземпляр ФБ в функции
END_VAR
