// Назначение: зонд статанализа (намеренно плохой код)
FUNCTION_BLOCK FB_SaBad
VAR_INPUT
    iMode : INT;
    rIn : REAL;
END_VAR
VAR_OUTPUT
    iOut : INT;
    xEqual : BOOL;
END_VAR
VAR
    {attribute 'analysis' := '-33'}
    iUnusedLocal : INT;             // SA0033 подавлено атрибутом
    iUnused2 : INT;                 // SA0033: не используется
    diWide : DINT := 70000;
    F_Clamp : REAL;                 // SA0027: имя совпадает с функцией F_Clamp
END_VAR
