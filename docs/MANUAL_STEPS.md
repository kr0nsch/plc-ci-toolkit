# Ручные шаги

Шаги, которые выполняет человек: в консоли Windows, в IDE CODESYS и в настройках GitHub. Всё, что
можно выполнить без CODESYS (сборка текстов, линтер, статанализ), выполняется в CI.

Для plc-ci-toolkit главное — **разделы 9–14**: подготовка self-hosted раннера, его регистрация и
первый прогон Windows-job. Критерий готовности «Windows-job хотя бы раз прошёл на моём раннере:
сборка, тесты, JUnit в отчёте» подтверждает владелец ([раздел 13](#13-что-подтверждает-владелец)).
Разделы 1–8 — общие шаги шаблона: они нужны и на машине раннера (CODESYS, coUnit, переменные,
рантайм), и для работы с примером ST в `src/` и `tests/`.

Команды разделов 3–7 проверены прогоном на CODESYS V3.5 SP17 Patch 3 (3.5.17.30, 32-битная сборка
ОВЕН) и CODESYS Control Win V3 в пользовательском процессе (16.10.2026). Разделы 9–14 — процедура
для первого запуска: приложение раннера на этой машине ещё не регистрировалось, поэтому в них
отмечено, что проверено заранее, а что проверит первый прогон. Подробности об инструменте —
[tools/README.md](../tools/README.md#codesyscdspy--codesys-из-консоли).

## 1. Установка CODESYS 3.5.17.30

1. Скачать CODESYS V3.5 SP17 Patch 3 (3.5.17.30) с сайта ОВЕН (раздел ПО для ПЛК2xx) и установить
   с компонентами CODESYS Control Win V3 и Gateway. Сборка ОВЕН 32-битная и ставится в
   `C:\Program Files (x86)\CODESYS 3.5.17.30\` — этот путь `cds.py` находит сам.
2. Для рабочего ПЛК — пакет таргет-файлов ОВЕН ПЛК210: «Инструменты → Установщик пакетов»
   (Tools → Package Manager). Версию таргет-файла сверить с прошивкой ПЛК. Для тестов он не нужен:
   тестовый проект собирается под CODESYS Control Win V3.
3. Имя профиля — по файлу в каталоге профилей (для 3.5.17.30 — `CODESYS V3.5 SP17 Patch 3`):

   ```bat
   dir "C:\Program Files (x86)\CODESYS 3.5.17.30\CODESYS\Profiles"
   ```

   `CODESYS.exe --help` и запуск без `--profile` открывают окно IDE (диалог выбора профиля), а не
   выводят справку в консоль — для проверки их не используйте.
4. Нужен Python 3.11+ (`python --version`), сторонние пакеты для `cds.py` не нужны.

## 2. coUnit (CfUnit) 1.2.0.0

В версии 1.2.0.0 CfUnit называется co♻e / coUnit, пространство имён в коде — `coUnit`
(см. [ASSUMPTIONS.md](ASSUMPTIONS.md), C6). Лицензия MIT.

1. Скачать `counit.library` из SVN проекта counit на CODESYS Forge (`tags/v1.2.0.0/`) обычным
   `GET` (браузером: запрос `HEAD` даёт 401).
2. Установить одним из способов:
   - в IDE: «Инструменты → Репозиторий библиотек → Установить» (Tools → Library Repository → Install);
   - или автоматически: задать путь к файлу в `COUNIT_LIBRARY` (раздел 3) — первый
     `cds.py import --tests`, не найдя coUnit 1.2.0.0 в репозиториях, установит её в репозиторий
     `System` (`librarymanager.install_library`). _Ветка установки на этой машине не выполнялась:
     библиотека уже была установлена; поиск по пространству имён и версии — проверен._
3. Параметры библиотеки менять не нужно: с параметрами по умолчанию тестовое приложение под
   Control Win собирается без ошибок (проверено, C7). Для ПЛК210 с меньшей памятью — сверить при
   переносе тестов на контроллер.

## 3. Переменные окружения

Абсолютных путей пользователя в репозитории нет: `cds.py` берёт их из переменных окружения, а без
них — из значений по умолчанию в `tools/codesys/config.json`. Обычно достаточно двух:

| Переменная | Значение | По умолчанию |
|---|---|---|
| `CODESYS_EXE` | полный путь к `CODESYS.exe` | `%ProgramFiles(x86)%\CODESYS 3.5.17.30\CODESYS\Common\CODESYS.exe`, затем `%ProgramFiles%\...` |
| `CODESYS_PROFILE` | имя профиля | `CODESYS V3.5 SP17 Patch 3` |
| `COUNIT_LIBRARY` | путь к `counit.library` (только если coUnit ещё не установлена) | — |
| `CODESYS_RUNTIME_CREDENTIALS` | файл с логином и паролем тестового пользователя рантайма | `%USERPROFILE%\.local\plc-tools\cds-runtime\credentials.json` |

Остальные (устройство, пути рантайма, таймауты, блокировка) — в
[tools/README.md](../tools/README.md#переменные-окружения). Задать для пользователя:

```bat
setx CODESYS_EXE "C:\Program Files (x86)\CODESYS 3.5.17.30\CODESYS\Common\CODESYS.exe"
setx CODESYS_PROFILE "CODESYS V3.5 SP17 Patch 3"
```

`setx` не меняет окружение уже открытых консолей; `cds.py` дочитывает переменные пользователя из
реестра (`HKCU\Environment`), поэтому перезапуск консоли не обязателен.

## 4. Рантайм CODESYS Control Win V3 без прав администратора

Служба Windows «CODESYS Control Win V3» требует прав администратора — её не используем. Тесты идут
в пользовательском процессе `CODESYSControlService.exe`, которым управляет `cds.py`. Команды
одинаково работают в cmd, PowerShell и Git Bash (из корня репозитория):

| Команда | Что делает |
|---|---|
| `python tools/codesys/cds.py runtime init` | один раз: каталог, `boot.cfg`, копия `CODESYSControl.cfg`, `credentials.json` |
| `python tools/codesys/cds.py runtime start` | скрытый запуск, ждёт порт 11740 |
| `python tools/codesys/cds.py runtime status` | PID, сколько работает, остаток демо-режима; код 1 — рантайм не запущен |
| `python tools/codesys/cds.py runtime restart` | остановить свой процесс и запустить заново |
| `python tools/codesys/cds.py runtime stop` | остановить свой процесс (службу Windows не трогает) |

- `runtime init` создаёт `%USERPROFILE%\.local\plc-tools\cds-runtime\`: `boot.cfg`, `work\` с
  копией штатного `CODESYSControl.cfg` (из `C:\ProgramData\CODESYS\CODESYSControlWinV3\<id>\`, файл
  только читается) и `credentials.json` с пользователем `cdsci` и случайным паролем. Пароль не
  печатается; файл лежит вне репозитория — не копируйте и не коммитьте его.
- Без лицензии рантайм работает 2 часа с момента запуска. `cds.py test` сам перезапускает его, если
  он работает дольше 100 минут (`CODESYS_RUNTIME_MAX_AGE_MIN`). Запуск и остановка — только своего
  процесса (с `-d <boot.cfg>`); если порт 11740 занят службой или чужим экземпляром, `cds.py`
  использует его, но не перезапускает.
- Состояние рантайма (пользователи, загруженные приложения, сертификаты, PlcLogic) из-за
  виртуализации UAC лежит не в `work\`, а в
  `%LOCALAPPDATA%\VirtualStore\Program Files (x86)\CODESYS 3.5.17.30\GatewayPLC\`; туда же
  попадает XML-отчёт coUnit (`%LOCALAPPDATA%\VirtualStore\coUnit_xunit_testresults.xml`).
  Чтобы «обнулить» рантайм (забыть пользователей и приложения), остановите его и удалите этот
  каталог вручную — это необратимо.

**Первый вход и создание пользователя.** Управление пользователями рантайма остаётся включённым
(отключать его не нужно). На свежем рантайме без пользователей `cds.py test` сам создаёт первого
пользователя из `credentials.json` (`set_credentials_for_initial_user` и подключение), если обычный
вход не удался. _Эта ветка реализована по API, но на этой машине не выполнялась: пользователь
`cdsci` уже был создан._ Если автоматически не получится — вручную в IDE: открыть тестовый проект
(раздел 6), «Устройство → Настройки соединения» (Communication Settings), выбрать шлюз `Gateway-1`
(localhost:1217) и устройство, войти — IDE предложит создать пользователя; ввести логин и пароль
из `credentials.json`.

## 5. Импорт, сборка, тесты

Все команды — из корня репозитория (cmd, PowerShell или Git Bash). Каждая запускает CODESYS без
окна (`--noUI`), длится 10–20 секунд и пишет журналы в `build/`.

| Команда | Что делает |
|---|---|
| `python tools/codesys/cds.py import` | `src/` → `build/<имя>.project` (MainTask 20 мс → `PRG_Main`) |
| `python tools/codesys/cds.py import --tests` | `src/` и `tests/` → `build/<имя>-tests.project` (TestTask 10 мс → `PRG_Tests`, coUnit) |
| `python tools/codesys/cds.py build` | импорт и полная сборка: `build/build.log`, `build/build.json` |
| `python tools/codesys/cds.py build --tests` | то же для тестового проекта |
| `python tools/codesys/cds.py test` | импорт с тестами, сборка, прогон в рантайме |
| `python tools/codesys/cds.py roundtrip` | импорт в чистый проект → экспорт → сравнение с `src/` и `tests/` |
| `python tools/codesys/cds.py report` | итоги последнего `test` → `docs/TEST_REPORT.md` |

Что смотреть:

- `import` печатает, что создано, обновлено, удалено и перемещено. Повторный импорт без правок —
  «создано 0, обновлено 0, удалено 0». Ошибки раскладки (коды LAY) — отказ без изменений проекта.
- `build`: ошибки и предупреждения компилятора с файлом и строкой раскладки, например
  `src/POUs/F_Clamp/F_Clamp.impl.st:16: Error C0046 Идентификатор 'rValueX' не задан`. Сообщения
  IDE — на русском. Если в IDE видны ошибки, которых нет в выводе, — скопируйте их в
  [BUILD_ERRORS.md](BUILD_ERRORS.md). Перед открытием проекта `cds.py` удаляет кэш предкомпиляции
  `build/<имя>_project.precompilecache`: с ним CODESYS 3.5.17.30 после замены объявления ФБ с
  методами собирал старый текст (сломанное объявление — «0 ошибок», исправленное — старая ошибка).
- `test` перед прогоном удаляет `build/test-results.json` и `build/junit.xml` прошлого запуска:
  при ошибке сборки или входа в рантайм старые зелёные результаты не остаются.
- `test`: итог по наборам и тестам; упавшие — с сообщением coUnit. Результаты — в
  `build/test-results.json` и `build/junit.xml`, журнал — `build/test.log`.
- `roundtrip`: «экспорт совпадает с src/ и tests/ побайтно» и «повторный импорт … — 0 изменений».
  Любой дифф — ошибка скриптов (issue).

Коды выхода: 0 — успех; 1 — ошибки раскладки или сборки, упавшие тесты, дифф roundtrip;
2 — аргументы или `config.json`; 3 — окружение (нет CODESYS, рантайма, файла); 4 — таймаут;
5 — блокировка занята. Рантайм один на машину: команды, которые с ним работают, ждут друг друга
(блокировка `%LOCALAPPDATA%\plc-tools\codesys-runtime.lock`).

## 6. Работа в IDE и экспорт перед коммитом

1. Собрать проект: `python tools\codesys\cds.py import` (или `import --tests` для проекта с тестами).
2. Открыть его в IDE: «Файл → Открыть проект» → `build\<имя>.project` (`build\<имя>-tests.project`),
   либо из консоли cmd (из PowerShell — эту же строку через `cmd /c '<строка>'`: PowerShell и Git
   Bash переставляют кавычки в `--profile="..."`, и CODESYS не находит профиль; `cmd /c` проверен
   с `--noUI --runscript`). `%CODESYS_EXE%` виден в консолях, открытых после `setx`:

   ```bat
   "%CODESYS_EXE%" --profile="CODESYS V3.5 SP17 Patch 3" --project="%CD%\build\plc-ci-toolkit.project"
   ```

   Вход в рантайм из IDE: «Онлайн → Логин» с пользователем из `credentials.json`. _Открытие из
   консоли с `--project` здесь не проверялось (ключ описан в справке CODESYS по командной строке)._
3. Править, сохранить проект, **закрыть IDE**. Пока проект открыт в IDE, `import`, `build`, `test`
   и `export` сразу отказываются: «проект … открыт в другом экземпляре CODESYS (пользователь, компьютер,
   PID)», код выхода 3 (проверено). Файл блокировки `<проект>.~u`, оставшийся после сбоя CODESYS
   или его остановки по таймауту, `cds.py` удаляет сам, если процесса с этим PID больше нет.
4. Выгрузить правки в тексты и проверить дифф:

   ```bat
   python tools/codesys/cds.py export
   python tools/codesys/cds.py export --tests
   git diff --stat
   ```

   `export` выгружает `build/<имя>.project` в `src/`; `export --tests` — `build/<имя>-tests.project`
   в `src/` и `tests/`.

Защита от потери правок:

- `import` отказывается перезаписывать проект, сохранённый в IDE после последнего
  `import`/`export`, — сначала `export` (или `--force`, если правки не нужны);
- `export` в репозиторий отказывается, если `src/` (`tests/`) изменились после последнего
  `import`/`export` этого проекта: экспорт удаляет файлы, которых нет в проекте. Сравнить можно
  выгрузкой в отдельный каталог: `cds.py export --to build\cmp`; перезапись — `--force`.

Не входит в раскладку (только в IDE, при экспорте не сохраняется — описывайте здесь и в docs):

- [ ] дерево устройств ПЛК210 и конфигурация модулей ввода-вывода; конфигураторы обмена (Modbus, OPC UA);
- [ ] визуализации, графические языки (FBD/LD/SFC/CFC), действия (`ACTION`) — `export` предупреждает о них;
- [ ] **Persistent Variables** (`*.pvl.st`): в SP17 скриптом не создаются. Создайте объект вручную
  («Application → Добавить объект → Persistent Variables» с именем как у файла), затем
  `cds.py import` обновит его текст из файла, а `export` выгрузит обратно.

Задачи и ссылки на библиотеки не правьте в IDE: их создаёт `import` по `tools/codesys/config.json`
(лишние задачи удаляются).

## 7. Релиз: `.project` и `.library`

`.project` и `.library` в git не хранятся — прикладываются к релизу GitHub.

1. `python tools\codesys\cds.py build` — 0 ошибок; `python tools\codesys\cds.py test` — тесты зелёные.
2. `.project`: приложить `build\<имя>.project` (рабочий проект; тестовый — по желанию). Для
   переноса на другую машину удобнее архив: «Файл → Архив проекта → Сохранить/Отправить архив»
   (`.projectarchive`).
3. `.library` (для репозиториев-библиотек): открыть проект в IDE, заполнить «Проект → Информация о
   проекте» (компания, название, версия), выполнить «Сборка → Проверить все объекты пула» и
   сохранить «Файл → Сохранить проект как скомпилированную библиотеку» (`.compiled-library`) или
   «Сохранить проект как…» с типом «Библиотека» (`.library`). _Названия пунктов меню — сверить с
   русской IDE 3.5.17.30; в этом прогоне не выполнялось._
4. Записать в [TEST_REPORT.md](TEST_REPORT.md) итоги (`cds.py report`) и в `CHANGELOG.md` — версию.

## 8. Зеркало на GitVerse или GitFlic

1. Создать пустой репозиторий на GitVerse или GitFlic (без README и лицензии).
2. Добавить его как второй remote и отправить все ветки и теги:

   ```bash
   git remote add mirror <url-зеркала>
   git push mirror --all
   git push mirror --tags
   ```

3. После каждого релиза повторять `git push mirror --all` и `git push mirror --tags`.

## 9. Self-hosted раннер: порядок действий

Windows-job — задание `codesys` переиспользуемого workflow `plc-codesys.yml`: импорт `src/` и
`tests/` в проект, сборка, загрузка в CODESYS Control Win V3, прогон coUnit, JUnit XML и сводка.
Где он запускается:

- в plc-ci-toolkit — workflow **CODESYS** (`.github/workflows/codesys.yml`): самопроверка на примере
  ST шаблона (18 тестов в двух наборах);
- в репозитории-потребителе (первым — библиотека `plc-process-lib`) — задание `codesys` его CI по
  образцу [examples/consumer-ci.yml](../examples/consumer-ci.yml).

Раннер регистрируется для каждого такого репозитория отдельно. Для критерия готовности достаточно
одного успешного прогона; порядок — сначала plc-ci-toolkit, затем библиотека (те же шаги 3–6).

| № | Действие | Кто и где | Подробно |
|---|---|---|---|
| 1 | Машина, учётная запись `codesys-ci`, CODESYS 3.5.17.30, coUnit, Python, Git | администратор, Windows | [runner_setup.md, разделы 1–3](runner_setup.md#1-схема), здесь — разделы 1–2 |
| 2 | `cds.py build` и `cds.py test` под `codesys-ci` | `codesys-ci`, консоль | [runner_setup.md, раздел 4](runner_setup.md#4-первая-проверка-под-учётной-записью-раннера) |
| 3 | Настройки репозитория | владелец, GitHub | [раздел 10](#10-настройки-репозитория-на-github) |
| 4 | Регистрация раннера и `run.cmd` | `codesys-ci` и GitHub | [раздел 11](#11-регистрация-раннера) |
| 5 | Ручной запуск Windows-job | владелец, GitHub | [раздел 12](#12-первый-прогон-windows-job) |
| 6 | Подтверждение и запись результата | владелец | [раздел 13](#13-что-подтверждает-владелец) |

Риски и почему настройки именно такие — [runner_security.md](runner_security.md).

## 10. Настройки репозитория на GitHub

В каждом репозитории с Windows-job, до регистрации раннера. Settings → Actions → General:

1. **Actions permissions** — «Allow <владелец>, and select non-<владелец>, actions and
   reusable workflows» (в репозиториях портфолио — «Allow kr0nsch, and select non-kr0nsch, ...»):
   отметить «Allow actions created by GitHub» и добавить в список `mikepenz/action-junit-report@*`.
   Переиспользуемые workflow plc-ci-toolkit у того же владельца разрешены; в репозитории другого
   владельца добавить и их ([USAGE.md, раздел 1](USAGE.md#1-за-10-минут-проверки-без-codesys), шаг 7).
2. **Approval for running fork pull request workflows from contributors** — «Require approval for
   all external contributors».
3. **Workflow permissions** — права только на чтение (`contents` и `packages`); флажок «Allow GitHub
   Actions to create and approve pull requests» снят.
4. Нажать **Save** в каждом блоке.

Картинка для ссылок на репозиторий: Settings → General → Social preview → **Edit** → загрузить
`docs/media/social-preview.png` (1280 × 640).

По желанию: Settings → Rules → Rulesets — правило для `main` (запрет принудительного push и
удаления); окружение `codesys` — [runner_security.md, раздел 3.3](runner_security.md#33-окружение-с-обязательным-одобрением)
(пока `plc-codesys.yml` окружение не объявляет, правило ни на что не влияет).

Переменную `PLC_CODESYS_RUNNER` пока не создавать: без раннера задание ждало бы его в очереди
до 24 часов и завершилось бы ошибкой. Её создаёт шаг 7 раздела 11.

## 11. Регистрация раннера

Перед началом: выполнены разделы 1–2 этого документа и разделы 2–4 [runner_setup.md](runner_setup.md)
(под `codesys-ci` зелёные `cds.py build` и `cds.py test`). Для каждого репозитория — свой каталог
и своя регистрация; ниже — для plc-ci-toolkit.

1. Войдите в Windows под `codesys-ci` («Пуск» → пользователь → «Сменить пользователя») и откройте
   **Windows PowerShell без прав администратора**.
2. В браузере откройте репозиторий на GitHub → Settings → Actions → Runners → **New self-hosted
   runner** → Runner image **Windows**, Architecture **x64**. Чтобы не оставлять сеанс GitHub в
   профиле `codesys-ci`, откройте страницу в окне InPrivate и закройте его после шага 4. _Вместо
   этого можно открыть консоль `codesys-ci` в своём сеансе (`runas /user:codesys-ci powershell`) и
   копировать команды из своего браузера; что рантайм CODESYS в таком окне работает так же, как в
   полном сеансе, не проверено._
3. Создайте каталог и выполните блок **Download** со страницы как есть: он скачивает архив раннера
   текущей версии, **сверяет SHA-256** (строка с `Get-FileHash` — не пропускать; при «Computed
   checksum did not match» — остановиться) и распаковывает его:

   ```powershell
   mkdir C:\actions-runner\plc-ci-toolkit
   cd C:\actions-runner\plc-ci-toolkit
   # далее — команды блока Download со страницы GitHub (Invoke-WebRequest, Get-FileHash, распаковка)
   ```

4. Выполните команду из блока **Configure**, добавив имя и метку (токен — со страницы, действует
   час; не сохраняйте и не пересылайте его):

   ```powershell
   .\config.cmd --url https://github.com/kr0nsch/plc-ci-toolkit --token <токен> --name codesys-01 --labels codesys --work _work
   ```

   На вопрос «Would you like to run the runner as service? (Y/N)» ответьте **N**: первый прогон —
   интерактивный ([runner_setup.md, раздел 7.2](runner_setup.md#72-интерактивный-запуск--для-первого-прогона)).
   Ожидается сообщение об успешной регистрации и «Settings Saved».
5. Запустите раннер и не закрывайте окно:

   ```powershell
   .\run.cmd
   ```

   Ожидается «Connected to GitHub» и «Listening for Jobs». Можно вернуться в свой сеанс Windows
   («Сменить пользователя»): сеанс `codesys-ci` и раннер продолжат работать.
6. На GitHub: Settings → Actions → Runners — раннер `codesys-01` в состоянии **Idle**, метки
   `self-hosted`, `Windows`, `X64`, `codesys`.
7. Settings → Secrets and variables → Actions → вкладка **Variables** → New repository variable:
   имя `PLC_CODESYS_RUNNER`, значение `true`.

Для `plc-process-lib` — те же шаги с каталогом `C:\actions-runner\plc-process-lib`, URL библиотеки и
её токеном, под той же учётной записью `codesys-ci` (тогда задания двух репозиториев делят рантайм
по очереди — [runner_setup.md, раздел 7.1](runner_setup.md#71-каталог-метки-число-раннеров)).
Каждый раннер — своё окно `run.cmd`. Перевод раннера в службу Windows — после успешного первого
прогона, по [runner_setup.md, раздел 7.3](runner_setup.md#73-служба-windows-под-учётной-записью-codesys-ci).

## 12. Первый прогон Windows-job

1. На машине раннера (под `codesys-ci`): IDE CODESYS закрыта; служба рантайма остановлена —
   `sc query "CODESYS Control Win V3"` показывает `STOPPED`; другие пользователи рантайм не
   запускали ([runner_setup.md, раздел 6.1](runner_setup.md#61-вариант-а-пользовательский-процесс-под-управлением-cdspy)).
2. На GitHub: репозиторий → **Actions** → workflow **CODESYS** → **Run workflow** → ветка `main` →
   **Run workflow**. В репозитории-потребителе — workflow его CI (в образце — **PLC CI**). Из консоли
   с установленным GitHub CLI то же самое: `gh workflow run codesys.yml --ref main`.
3. Что должно произойти:
   - задание **guard** (GitHub-hosted Linux, секунды) завершается строкой
     `разрешено: workflow_dispatch refs/heads/main`;
   - задание **codesys** ждёт раннер, окно `run.cmd` показывает «Running job»; шаги по порядку:
     выгрузка репозитория и plc-ci-toolkit, «Проверка раннера», «Static Analysis Light (SA)»,
     «Рантайм — перезапуск», `cds.py test`, `cds.py junit`, «Публикация JUnit», «Сводка»,
     «Рантайм — остановка», «Артефакты»;
   - длительность — минуты: шаг `cds.py test` на примере шаблона — около 20 секунд после
     перезапуска рантайма, шаг Static Analysis Light — ещё одна сборка; первый запуск дольше —
     раннер скачивает actions.
4. Где результат:
   - страница запуска → **Summary**: таблица «CODESYS coUnit» (шаг публикации JUnit) и сводка
     `ci/summary.py` — сборка, тесты, статанализ;
   - журнал шага `cds.py test`: `[cds] test: OK (наборов 2, тестов 18, пройдено 18, упало 0,
     пропущено 0, …)` и путь к `build\junit.xml`;
   - артефакт **plc-codesys-reports**: `test.log`, `build.log`, `test-results.json`, `junit.xml`,
     журналы `build/codesys/`.
5. Не прошёл — [раздел 14](#14-если-windows-job-не-прошёл).

## 13. Что подтверждает владелец

Критерий спецификации: «Windows-job хотя бы раз прошёл на моём раннере: сборка, тесты, JUnit в
отчёте». Подтверждено, если в одном запуске:

- [ ] задание `codesys` выполнено на своём раннере: в шаге «Set up job» журнала — имя раннера
  (`codesys-01`);
- [ ] сборка без ошибок: в журнале `cds.py test` или в сводке — «ошибок 0»;
- [ ] тесты coUnit выполнены и зелёные: упало 0 (для примера plc-ci-toolkit — 18 из 18);
- [ ] JUnit в отчёте: в Summary есть таблица «CODESYS coUnit», в артефакте — `junit.xml`;
- [ ] (по желанию) pull request из ветки не запускает задание `codesys`: в репозитории с «PLC CI» оно
  «skipped», в plc-ci-toolkit workflow CODESYS для PR не запускается вовсе.

Записать:

1. В [TEST_REPORT.md](TEST_REPORT.md) — раздел «Windows-job на self-hosted раннере»: ссылка на
   запуск (`https://github.com/kr0nsch/<репозиторий>/actions/runs/<id>`), дата, имя раннера,
   CODESYS 3.5.17.30, число тестов. Раздел «Последний прогон в CODESYS» можно обновить по
   результатам CI: скачать артефакт и выполнить
   `python tools/codesys/cds.py report --results <каталог артефакта>\test-results.json`.
2. В `CHANGELOG.md` — запись о первом прогоне; в README отметить критерий выполненным.

## 14. Если Windows-job не прошёл

| Признак | Причина | Что делать |
|---|---|---|
| задание `codesys` не появилось | нет переменной `PLC_CODESYS_RUNNER=true` или запуск не из `main` | раздел 11, шаг 7; запуск с ветки `main` |
| guard: «событие … запрещено» или «push в … : разрешены только …» | запуск из PR или из другой ветки | запускать `workflow_dispatch` с `main`, push в `main` или тег `v*` |
| «Waiting for a runner» долго | раннер выключен или без метки `codesys` | окно `run.cmd`, Settings → Actions → Runners (Idle, метки) |
| «Python not found» | Python не установлен для всех пользователей | [runner_setup.md, раздел 3](runner_setup.md#3-программы) или вход `python` workflow |
| перезапуск рантайма: «порт 11740 занят другим процессом» | запущена служба «CODESYS Control Win V3» или рантайм другого пользователя | остановить службу (администратор) или процесс другого пользователя |
| `cds.py test`, код 3: проект «открыт в другом экземпляре CODESYS» | остался `CODESYS.exe` прерванного запуска | завершить процесс `CODESYS.exe` учётной записи `codesys-ci` (Диспетчер задач) |
| `cds.py test`, код 3: не удался вход в рантайм | пользователь рантайма не создан или пароль не совпадает с `credentials.json` | создать пользователя вручную ([раздел 4](#4-рантайм-codesys-control-win-v3-без-прав-администратора)) под `codesys-ci` и повторить |
| `cds.py test`, код 4 (таймаут) | диалог CODESYS в `--noUI` | [runner_setup.md, раздел 4](runner_setup.md#4-первая-проверка-под-учётной-записью-раннера): запустить IDE под `codesys-ci` и ответить на диалоги |
| coUnit не найдена | библиотека не установлена | [раздел 2](#2-counit-cfunit-1200) или `COUNIT_LIBRARY` |
| только для раннера-службы: рантайм не стартует или не принимает вход | нет виртуализации UAC в службе | [runner_setup.md, раздел 7.3](runner_setup.md#73-служба-windows-под-учётной-записью-codesys-ci): копия `GatewayPLC` или интерактивный запуск |

Журналы — [runner_setup.md, раздел 10](runner_setup.md#10-журналы). Код выхода `cds.py` — в конце
журнала шага, строка «Process completed with exit code N» (0 — успех, 1 — проверка не прошла,
3 — окружение, 4 — таймаут, 5 — блокировка): шаги запускаются через `powershell -File`, который
передаёт код без изменений.

## 15. pre-commit в рабочей копии

В каждом клоне репозитория (включая машину раннера, если там правят код) — один раз:

```bat
python -m pip install pre-commit
python -m pre_commit install
```

Перед коммитом запускаются ruff, `st_lint` и проверка «исходники выгружены»: правки проекта
CODESYS из IDE без `cds.py export` коммит остановят с подсказкой, что выполнить. Подробно —
[pre_commit.md](pre_commit.md).

## 16. Static Analysis Light: правила в IDE

В CI и в консоли ручной шаг не нужен: `python ci/sa_check.py` включает правила Static Analysis
Light на время анализа и генерирует код, `python ci/sa_report.py` строит отчёт
([sa_light.md](sa_light.md)). Включать правила в IDE нужно, только чтобы видеть находки при
работе в IDE, или если `sa_check.py` завершился с кодом 3 «внутренний API плагина
StaticAnalysisManager отличается от 3.5.17.30» (другая версия CODESYS).

1. Открыть **рабочий** проект (`build\<имя>.project`, [раздел 6](#6-работа-в-ide-и-экспорт-перед-коммитом)).
   Тестовый проект не трогать: при включённых правилах любая находка даёт итог генерации кода
   «… 1 ошибок …: загрузка невозможна» (проверено скриптом), а в тестовом проекте шаблона находки
   есть (11 × SA0167 — экземпляры `fbDut` в методах-тестах).
2. **Проект → Установки проекта...** → в списке категорий слева — **Static Analysis Light** →
   вкладка **Доп. проверки компилятора**.
3. Отметить правила (в Light их шесть):
   - «Неиспользуемые переменные» (SA0033);
   - «Перекрывающиеся области памяти» (SA0028);
   - «Параллельный доступ» (SA0006);
   - «Множественный доступ по записи на выходе» (SA0004);
   - «Многократное использование имени» (SA0027);
   - «Сообщать о временных экземплярах ФБ» (SA0167).

   В IDE находка всегда ошибка: выбрать «предупреждение» диалог Light не даёт (это умеет только
   `sa_check.py`, режим `warning` в `ci/sa_rules.json`).
4. **OK**, сохранить проект (**Файл → Сохранить проект**), закрыть IDE.
5. Проверить: **Компиляция → Генерировать код** (не **Компиляция → Компиляция**: обычная
   компиляция правила Light не проверяет — проверено скриптом). Находки — в окне «Сообщения»,
   категория **Доп. проверки кода**, строки вида `SA0033 … Неиспользуемый Переменная '…'`.
   Итог — строка «Доп. проверки кода завершены -- N ошибки (-ок)».
6. Если находки нужно сохранить в отчёт без `sa_check.py`: скопировать сообщения категории в
   [BUILD_ERRORS.md](BUILD_ERRORS.md) (формат записи там же).

Что ещё учесть:

- Настройки хранятся в самом `.project` (скрытый объект «Установки проекта» — так их сохраняет
  диалог IDE), а `.project` в git не хранится: в новом проекте (`--clean`, новый клон, CI) правила
  снова выключены — в CI их включает `sa_check.py`. `cds.py import` в существующий проект установки
  проекта не меняет, поэтому они должны сохраниться (не проверялось).
- Подавить находку в коде: `{attribute 'analysis' := '-33'}` строкой перед объявлением переменной
  или перед заголовком POU (номер правила с минусом), `{attribute 'no-analysis'}` — объект целиком
  (проверено). Каждое подавление и каждую запись базовой линии `ci/sa_baseline.json` обосновать в
  [STATIC_ANALYSIS.md](STATIC_ANALYSIS.md).
- _Названия меню и диалога взяты из локализации CODESYS 3.5.17.30 (`Settings\Standard (CODESYS V3.5
  SP17 Patch 3).opt.menu`, ресурсы плагинов WorkspaceEditor, BuildCommands, StaticAnalysisManager);
  в IDE эти шаги вручную не выполнялись — при расхождении поправить этот раздел._

## 17. Выпуск plc-ci-toolkit и бейджи

Репозитории-потребители вызывают workflow по тегу основной версии: `...plc-static.yml@v1`
([USAGE.md, раздел 6](USAGE.md#6-версии-и-закрепление)). Тег ставит владелец, когда CI в `main`
зелёный.

1. Actions → **CI** для последнего коммита `main` — зелёный (задание `plc-static (пример ST)`
   заодно проверяет выгрузку скриптов по `job.workflow_repository` и `job.workflow_sha`), и
   workflow **CODESYS** для того же коммита — не «Invalid workflow file»: зелёный или с
   пропущенным заданием, если раннера ещё нет. GitHub разбирает `plc-codesys.yml` и при
   пропущенном задании, а `concurrency.queue` и свойства `job.workflow_*` actionlint 1.7.12 не
   проверяет — ошибка в них сломала бы у потребителей весь вызывающий workflow, включая проверки
   pull request. В `CHANGELOG.md` раздел `[Unreleased]` переименован в версию с датой.
2. Тег выпуска и подвижный тег основной версии (из чистой рабочей копии `main`):

   ```bash
   git switch main
   git pull --ff-only
   git tag -a v1.0.0 -m "plc-ci-toolkit v1.0.0" HEAD
   git tag -f -a v1 -m "plc-ci-toolkit v1 -> v1.0.0" HEAD
   git push origin v1.0.0
   git push -f origin v1
   ```

   Команды одинаково работают в cmd, PowerShell и Git Bash. Push тега запускает workflow **CI** и
   (с переменной `PLC_CODESYS_RUNNER`) **CODESYS** на этом коммите.
   Следующие выпуски без несовместимых изменений: новый тег `v1.x.y`, затем те же две последние
   команды для `v1`. Переименование или удаление входа, другой смысл выхода — новый основной тег
   `v2`; потребители переходят на него сами.
3. GitHub → **Releases** → **Draft a new release** → тег `v1.0.0`, описание — из `CHANGELOG.md`.
4. В репозитории-потребителе ничего менять не нужно: `@v1` возьмёт новый коммит при следующем
   запуске. Проверка — запуск **PLC CI** вручную.

Бейджи README plc-ci-toolkit:

- **CI** — динамический с самого начала (workflow `ci.yml` идёт на GitHub-hosted раннере).
- **CODESYS** — пока статичный «pending first run»: задание без раннера пропускается, а что при этом
  показывает бейдж GitHub, не проверено. После первого прогона по разделу 13 замените строку бейджа
  в README на динамическую:

  ```markdown
  [![CODESYS](https://github.com/kr0nsch/plc-ci-toolkit/actions/workflows/codesys.yml/badge.svg?branch=main)](https://github.com/kr0nsch/plc-ci-toolkit/actions/workflows/codesys.yml)
  ```

Бейджи репозитория-потребителя — [USAGE.md, раздел 7](USAGE.md#7-бейджи).
