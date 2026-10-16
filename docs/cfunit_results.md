# Результаты coUnit (CfUnit) 1.2.0.0 в CI

Как coUnit отдаёт результаты, какой путь выбран для CI и зачем нужна (или не нужна) обёртка
`GVL_TestResults` (пункт 3 спецификации). Устройство библиотеки — по её исходнику (CODESYS Forge,
SVN `lib,counit,code`, trunk = 1.2.0.0); всё, что помечено «проверено», — прогоны на CODESYS V3.5 SP17 Patch 3
(3.5.17.30) и CODESYS Control Win V3 в пользовательском процессе, coUnit 1.2.0.0, 16.10.2026.

## Итог

**Путь CI — чтение переменных раннера coUnit через `read_value` по индексам**, без файлов на ПЛК.
Он уже реализован в шаблоне: `tools/codesys/run_tests.py` (шаг `test` команды `cds.py test`) читает
`coUnit.GVL_coUnit.coUnitRunner`, пишет `build/test-results.json`, а `cds.py` собирает из него
JUnit XML на хосте (`build/junit.xml`). Тулкит этот код не меняет и только публикует результаты
(`.github/workflows/plc-codesys.yml`, `ci/summary.py`).

Обёртка `GVL_TestResults` для этого пути **не нужна**, но добавлена как необязательный пример
([examples/st/](../examples/st/)): она даёт итоги в простых типах тем, кто читает их не скриптом
шаблона, — HMI, OPC UA, окну наблюдения, своему скрипту (раздел «Обёртка» ниже).

## Как coUnit 1.2.0.0 отдаёт результаты

| Канал | Что внутри | Как получить в CI | Вывод |
|---|---|---|---|
| журнал устройства (CmpLog, логгер `coUnit`) | по набору и тесту: статус PASS/FAIL/SKIP, сообщение и тип проверки; итог «Test suites / Tests / Successful tests / Failed tests» | только онлайн-журналом устройства: в стабах Scripting 3.5.17.30 (`ScriptOnline.pyi`, `ScriptDeviceObject.pyi`) метода чтения журнала устройства не нашлось | для CI не годится: по исходнику итог пишется классом LOG_ERROR и при зелёном прогоне, а строки длиннее 127 символов делятся на две записи в обратном порядке |
| файл xUnit XML | `<testsuites>`/`<testsuite>`/`<testcase status=…>` и `<failure message type>` | забрать файл с файловой системы рантайма | ненадёжно (ниже) |
| переменные `GVL_coUnit.coUnitRunner` | флаг завершения, итоги, наборы и тесты с сообщениями | `read_value` в том же сеансе, что и загрузка | **выбран** |

### Файл xUnit: что показал прогон

Параметры библиотеки по умолчанию — `XUNITENABLEPUBLISH = TRUE`, `XUNITFILEPATH =
'C:\coUnit_xunit_testresults.xml'`, `XUNITBUFFERSIZE = 65535` (прочитаны скриптом, раздел
«Параметры библиотеки»). Прогон зонда: 3 набора, 21 тест, 1 упал, 1 пропущен (`DISABLED_`).

- **Файл не там, где задан.** В `C:\` его нет: 32-битный рантайм без прав администратора пишет в
  `C:\` через виртуализацию UAC, и файл оказывается в `%LOCALAPPDATA%\VirtualStore\coUnit_xunit_testresults.xml`
  профиля пользователя, запустившего рантайм (проверено; [ASSUMPTIONS.md, D11, D13](ASSUMPTIONS.md)).
  У службы рантайма и у ПЛК210 путь будет другим, с 3.5.19 по данным форума CODESYS действует
  `ForceIecFilePath` (файл уходит в `PlcLogic`) — не проверялось; с ПЛК файл ещё нужно выгрузить.
- **Неверный итог.** `<testsuites failures="1" tests="20">` при 21 тесте: атрибут `tests` берётся из
  `NumberOfSuccessfulTestCases`, а coUnit считает пропущенный тест успешным.
- **Пропуски не видны JUnit.** У пропущенного теста только `status="SKIP"`, элемента `<skipped/>`
  нет — обычный разбор JUnit посчитает его пройденным.
- **Нет сигнала готовности.** По исходнику библиотеки файл пишется один раз после всех наборов,
  документ собирается в буфер `XUNITBUFFERSIZE` (больше — обрезка, тикет #63 на CODESYS Forge), а
  сообщение «TEST RESULTS EXPORTED» в журнале печатается без проверки результата записи. Отличить «ещё не записан» от «обрезан» или «не записан» снаружи
  нельзя.

Фрагмент файла этого прогона (одна строка, 2989 байт; здесь разбит):

```xml
<testsuites disabled="" failures="1" tests="20">
  <testsuite id="2" name="Device.Application.PRG_Tests.fbWrapperTest" tests="3" failures="1">
    <testcase name="Passing" classname="Device.Application.PRG_Tests.fbWrapperTest" status="PASS"></testcase>
    <testcase name="Failing" classname="Device.Application.PRG_Tests.fbWrapperTest" status="FAIL">
      <failure message="expected fail" type="INT"/></testcase>
    <testcase name="Skipped" classname="Device.Application.PRG_Tests.fbWrapperTest" status="SKIP"></testcase>
```

### Переменные раннера (выбранный путь)

Префикс выражений — `coUnit.GVL_coUnit.coUnitRunner.`; без пространства имён `coUnit.` —
«Неверное выражение». Значения `read_value` — строки, их разбирает `cds_common.plc_value`.

| Выражение | Пример значения | Назначение |
|---|---|---|
| `AllTestSuitesFinished` | `TRUE` | все наборы завершены (или прогон прерван) — условие опроса |
| `TestResults.StoredGeneralTestResults` | `TRUE` | итоговые счётчики заполнены (до этого они 0) |
| `TestResults.TestSuiteResults.NumberOfTestSuites` | `UINT#3` | наборов |
| `….NumberOfTestCases` / `NumberOfSuccessfulTestCases` / `NumberOfFailedTestCases` | `UINT#21` / `UINT#20` / `UINT#1` | итоги (в «успешных» — и пропущенные) |
| `….TestSuiteResults[i].Name` | `'Device.Application.PRG_Tests.fbWrapperTest'` | путь экземпляра набора; пустое имя — наборы кончились |
| `….TestSuiteResults[i].TestCaseResults[j].TestName` | `'Failing'` | имя теста (у `DISABLED_X` — `X`); пустое — тесты набора кончились |
| `….TestIsFailed`, `….TestIsSkipped` | `TRUE`/`FALSE` | статус |
| `….FailureMessage`, `….FailureType` | `'expected fail'`, `E_AssertionType.Type_INT` | первая упавшая проверка |
| `….TestSuiteResults[i].NumberOfTests` | — | **не читается**: поддиапазонный тип, «Type 'Subrange' is not a literal type» |

Почему этот путь надёжнее файла:

1. **Тот же канал, что и загрузка.** Нужен только вход в рантайм, который уже есть для загрузки и
   старта; нет зависимости от файловой системы ПЛК, UAC, `ForceIecFilePath` и выгрузки файла —
   одинаково для Control Win и для ПЛК через шлюз.
2. **Точный признак готовности:** `AllTestSuitesFinished`, затем `StoredGeneralTestResults`;
   таймаут ожидания — `CDS_TEST_TIMEOUT_S`. Все наборы примера завершаются за 0,5–0,6 с.
3. **Полные и верные данные:** имена, сообщения и тип первой упавшей проверки, явный SKIP; итоги
   считаются на хосте по прочитанным тестам (пройдено = всего − упало − пропущено), счётчики
   coUnit — только для сверки (`reported_*` в `test-results.json`). Нет буфера и обрезки.
4. **JUnit на хосте** (`cds.py junit`): правильный `tests`, `skipped`, `<skipped/>`, `<failure>` —
   его понимают GitHub Actions и IDE.
5. **Поддиапазонные счётчики не мешают:** обход по индексам до пустого имени не требует
   `NumberOfTests` набора.

Цена — число вызовов: шесть выражений на тест (`read_values` за один вызов) и обход до пустого
имени; шаг `test` целиком (вход, загрузка, прогон, чтение) на 21 тесте — около 10 с. Верхние границы — `tests.max_suites` и
`tests.max_tests_per_suite` в `tools/codesys/config.json` (1000 и 100, как параметры библиотеки).

### Параметры библиотеки

Штатный API Scripting 3.5.17.30 читает и меняет параметры библиотеки:
`ScriptLibraryReference.parameters` (стаб `ScriptLibManObject.pyi`). Проверено на тестовом проекте:
восемь параметров читаются (`MAXNUMBEROFTESTSUITES = 1000`, `MAXNUMBEROFTESTSFOREACHTESTSUITE = 100`,
`MAXNUMBEROFASSERTSFOREACHTESTSUITE = 1000`, `LOGEXTENDEDRESULTS = FALSE`, `XUNITENABLEPUBLISH = TRUE`,
`XUNITBUFFERSIZE = 65535`, `XUNITFILEPATH = 'C:\coUnit_xunit_testresults.xml'`,
`TIMEBETWEENTESTSUITESEXECUTION = TIME#0ms`), запись `parameters['xUnitEnablePublish'] = 'FALSE'`
принимается и проект собирается. Проект в этом прогоне не сохранялся, действие в рантайме не
проверялось. Так можно отключить файл xUnit или уменьшить лимиты для ПЛК210
([ASSUMPTIONS.md, C7](ASSUMPTIONS.md)); импорт шаблона (`import_sources.py`) параметры библиотек не
меняет — менять их пришлось бы отдельным скриптом.

## Обёртка `GVL_TestResults` (необязательно)

Спецификация предлагает обёртку, «если штатного пути нет». Штатный путь есть, поэтому обёртка — не
замена, а дополнение для других читателей. Она полезна, когда:

- итоги нужны **без обхода по индексам**: семь простых переменных читаются одним `read_values`;
- нужны **правильные пройденные и пропущенные**: coUnit считает пропуск успехом, обёртка — нет;
- итоги публикуются в **OPC UA или HMI** (конфигурация символов): переменная GVL проекта —
  обычный символ, а внутренние переменные ФБ библиотеки публиковать неудобно;
- нужен **стабильный контракт**: при смене coUnit (форк CoUnit-v2, другая версия) меняется только
  сборщик, а читатели обёртки — нет.

Не нужна, если результаты читает только `cds.py test` (как в CI тулкита).

Состав ([examples/st/](../examples/st/), раскладка `docs/LAYOUT.md`, проходит `st_lint --strict`):

| Файл | Объект | Что |
|---|---|---|
| `GVLs/GVL_TestResults.gvl.st` | `GVL_TestResults` | `xFinished`, `uiSuites`, `uiTests`, `uiPassed`, `uiFailed`, `uiSkipped`, `uiFailuresStored`, `aFailures[1..10]` |
| `DUTs/ST_TestFailure.dut.st` | `ST_TestFailure` | `sSuite`, `sTest`, `sMessage` (`STRING(255)`) |
| `POUs/FB_TestResultsCollector/` | `FB_TestResultsCollector` | один раз после `StoredGeneralTestResults` копирует итоги, считает пропущенные, записывает первые упавшие |

Подключение (файлы — в `tests/`, вызов — после `coUnit.RUN()`):

```iecst
PROGRAM PRG_Tests
VAR
    fbResults : FB_TestResultsCollector;    // итоги coUnit -> GVL_TestResults
END_VAR
coUnit.RUN();
fbResults();
```

Чтение: `GVL_TestResults.xFinished`, `GVL_TestResults.uiFailed`,
`GVL_TestResults.aFailures[1].sMessage` — `read_value` без префикса библиотеки.

**Проверено** (CODESYS 3.5.17.30, Control Win V3): код ST читает внутренние переменные раннера
(`coUnit.GVL_coUnit.coUnitRunner.TestResults.TestSuiteResults`, тип `coUnit.ST_TestSuiteResults`) и
поддиапазонный `NumberOfTests` — компилятор это разрешает. Прогон зонда (наборы примера и набор из
пройденного, упавшего и пропущенного теста; `cds.py test`, затем чтение `read_value`):

| Переменная | Значение | coUnit для сравнения |
|---|---|---|
| `xFinished` | `TRUE` через 0,6 с после старта | `AllTestSuitesFinished = TRUE` |
| `uiSuites` / `uiTests` | `3` / `21` | `NumberOfTestSuites = 3`, `NumberOfTestCases = 21` |
| `uiPassed` / `uiFailed` / `uiSkipped` | `19` / `1` / `1` | `NumberOfSuccessfulTestCases = 20` (с пропущенным), `NumberOfFailedTestCases = 1` |
| `aFailures[1]` | `'Device.Application.PRG_Tests.fbWrapperTest'`, `'Failing'`, `'expected fail'` | то же в `TestSuiteResults[3].TestCaseResults[2]` |
| `aFailures[2].sTest` | `''` | — |

Ограничения: обёртка зависит от внутренних имён coUnit 1.2.0.0 (переменные `VAR` раннера, не
документированный API); если прогон прерван (`AbortRunningTestSuites`) и итоги не сохранены,
`xFinished` останется `FALSE` — читатель должен ждать с таймаутом, как `cds.py test`.
Сообщение в `ST_TestFailure` — первая упавшая проверка (coUnit хранит одну на тест).

## Открытые вопросы

- Работа с ПЛК210 (меньше памяти, путь файлов, загрузка через шлюз ОВЕН) — не проверялась.
- Действие `XUNITENABLEPUBLISH = FALSE` в рантайме не проверялось (только приём значения и сборка).
- Журнал устройства (`coUnit`) скриптом 3.5.17.30 не читается — не исследовалось глубже.

