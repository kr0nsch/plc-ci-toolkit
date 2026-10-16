CASE iMode OF
    1: iOut := 10;
    2: iOut := 20;
END_CASE
iOut := DINT_TO_INT(diWide);
diWide := iOut;
xEqual := rIn = 1.5;
F_Clamp := rIn;
;
IF iMode > 100 THEN
    RETURN;
    iOut := 0;
END_IF
