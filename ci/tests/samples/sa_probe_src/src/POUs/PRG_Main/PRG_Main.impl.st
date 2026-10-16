// 1. Однократные команды HMI выполняем до вызова ФБ, чтобы выход в этом же цикле
//    отражал результат; после выполнения команду сбрасывает ПЛК.
IF stCmd.xAck THEN
    stCmd.xAck := FALSE;
    fbPump.Acknowledge();
END_IF

IF stCmd.xReset THEN
    stCmd.xReset := FALSE;
    itfResettable := fbPump;
    itfResettable.Reset();
END_IF

// 2. Регулирование
fbPump(eMode := stCmd.eMode,
       rValue_pct := rLevel_pct,
       xValueValid := xLevelValid,
       rSetpoint_pct := stCmd.rSetpoint_pct,
       xManualOn := stCmd.xManualOn,
       xOut => xPumpOn);

// 3. Состояние для HMI
stSts.eMode := stCmd.eMode;
stSts.xOut := xPumpOn;
stSts.xFault := fbPump.xFault;
stSts.rSetpointUsed_pct := fbPump.rSetpointUsed_pct;
stSts.udiCount := fbPump.Count;

// зонд статанализа
fbSaBad(iMode := 1, rIn := 1.5);
xTmp := fbSaBad.Check() AND F_SaTemp(xIn := TRUE);
GVL_SaProbe.diShared := 5;
GVL_SaProbe.xOutBad := xTmp;
GVL_SaProbe.wOverlapA := 1;
GVL_SaProbe.bOverlapB := 2;
fbSaSkip(xIn := xTmp);
