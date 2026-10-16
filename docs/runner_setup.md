# Настройка Windows-раннера с CODESYS

Self-hosted раннер GitHub Actions выполняет задание `codesys` переиспользуемого workflow
[`plc-codesys.yml`](../.github/workflows/plc-codesys.yml): импорт исходников в проект CODESYS,
сборка, загрузка в CODESYS Control Win V3, прогон тестов coUnit, JUnit XML и отчёт. Здесь — что
поставить на машину и как настроить; точная процедура первой регистрации и первого прогона —
[MANUAL_STEPS.md, разделы 9–13](MANUAL_STEPS.md#9-self-hosted-раннер-порядок-действий), риски и
меры защиты — [runner_security.md](runner_security.md).

**Что проверено, а что нет.** Всё, что делает задание на раннере, — команды `cds.py` — проверено
на CODESYS V3.5 SP17 Patch 3 (3.5.17.30, 32-битная сборка ОВЕН) с CODESYS Control Win V3 в
пользовательском процессе под обычной учётной записью Windows без прав администратора, в
интерактивном сеансе (15–16.10.2026, [TEST_REPORT.md](TEST_REPORT.md)). Приложение раннера GitHub
на этой машине ещё не регистрировалось: первый прогон Windows-job подтверждает владелец
([MANUAL_STEPS.md](MANUAL_STEPS.md#13-что-подтверждает-владелец)). Пункты, помеченные
_«не проверено»_, нужно проверить при первом запуске.

## 1. Схема

| Что | Значение |
|---|---|
| Машина | отдельная Windows 10/11 x64 или Windows Server 2016–2022 (x64), лучше виртуальная машина с контрольной точкой; не рабочая станция инженера и не в сети ПЛК |
| Учётная запись | локальный стандартный пользователь `codesys-ci`, без прав администратора |
| CODESYS | V3.5 SP17 Patch 3 (3.5.17.30), дистрибутив ОВЕН, 32 бит, `C:\Program Files (x86)\CODESYS 3.5.17.30\`; coUnit 1.2.0.0 в репозитории библиотек `System` |
| Рантайм | CODESYS Control Win V3 — пользовательский процесс, которым управляет `cds.py` (раздел 6.1); служба Windows остаётся остановленной |
| Раннер | `C:\actions-runner\<репозиторий>\`, метки `self-hosted`, `windows`, `codesys`; для первого прогона — интерактивно (`run.cmd`), затем при желании — служба Windows (раздел 7) |
| Сеть | только исходящий HTTPS (443) к GitHub; входящие соединения закрыты |

## 2. Учётная запись раннера

Задания выполняются от имени учётной записи раннера и видят всё, что видит она. Поэтому — отдельный
локальный пользователь без прав администратора, а не учётная запись инженера (ключи SSH, сеансы
браузера, проекты) и не `NT AUTHORITY\NETWORK SERVICE` или `LocalSystem`: у служебных учётных
записей нет обычного профиля, а `cds.py` хранит каталог рантайма и учётные данные рантайма в
профиле (`%USERPROFILE%\.local\plc-tools\cds-runtime\`), CODESYS — настройки в `%APPDATA%`.

Создаёт администратор (консоль «от имени администратора»; `*` — запрос пароля без вывода на экран):

```bat
net user codesys-ci * /add
```

Пароль — длинный случайный, в менеджере паролей. Если раннер будет службой, отключите истечение
пароля (иначе служба однажды не запустится): `Set-LocalUser -Name codesys-ci -PasswordNeverExpires $true`
в PowerShell от имени администратора. Один раз войдите в Windows под `codesys-ci` — создастся профиль.

## 3. Программы

Ставит администратор один раз.

| Программа | Зачем | Замечания |
|---|---|---|
| CODESYS V3.5 SP17 Patch 3 (3.5.17.30), дистрибутив с сайта ОВЕН (раздел ПО для ПЛК2xx), 32 бит | IDE для скриптов `--noUI`, CODESYS Control Win V3, шлюз | ставить с компонентами CODESYS Control Win V3 и Gateway ([MANUAL_STEPS.md, раздел 1](MANUAL_STEPS.md#1-установка-codesys-351730)). Установка занимает около 2,8 ГБ. SHA-256 дистрибутива (`Get-FileHash <файл>`) запишите в паспорт машины — при переустановке будет с чем сверить |
| coUnit (CfUnit) 1.2.0.0 | библиотека тестов | ставится один раз в репозиторий `System` (`C:\ProgramData\CODESYS\Managed Libraries`, общий для всех пользователей; запись без прав администратора проверена) — [MANUAL_STEPS.md, раздел 2](MANUAL_STEPS.md#2-counit-cfunit-1200) |
| Python 3.12 x64, установщик python.org с «Install for all users» и «Add python.exe to PATH» | `cds.py` и скрипты `ci/` (Python 3.11+, только стандартная библиотека) | Python, установленный «для себя» в профиль другого пользователя, учётной записи раннера недоступен. Путь к другому Python — вход `python` workflow `plc-codesys.yml` |
| Git for Windows (для всех пользователей) | `actions/checkout` | без Git 2.18+ в `PATH` checkout скачивает архив без `.git`, и в отчётах не будет коммита |

Windows PowerShell 5.1 есть в любой Windows: им пользуются и шаги `plc-codesys.yml`, и `cds.py`
(управление рантаймом). PowerShell 7 не нужен. Пакет таргет-файлов ПЛК210 для тестов не нужен:
тестовый проект собирается под CODESYS Control Win V3.

## 4. Первая проверка под учётной записью раннера

До регистрации раннера убедитесь, что CODESYS, coUnit и рантайм работают у `codesys-ci`. Войдите
в Windows под `codesys-ci` (интерактивно) и:

1. Один раз запустите IDE CODESYS из меню «Пуск» (профиль `CODESYS V3.5 SP17 Patch 3`), ответьте
   на диалоги первого запуска и закройте IDE. Скрипты `cds.py` работают с `--noUI`, отвечать на
   диалоги там некому: процесс ждал бы до таймаута `CDS_TIMEOUT_S` (900 с). _Нужен ли этот шаг на
   чистом профиле, не проверено; он безвреден._
2. Задайте переменные (раздел 5), выгрузите репозиторий во временный каталог и выполните:

   ```bat
   python tools\codesys\cds.py runtime init
   python tools\codesys\cds.py build
   python tools\codesys\cds.py test
   python tools\codesys\cds.py runtime stop
   ```

   Ожидается: сборка без ошибок, тесты coUnit зелёные, `build\junit.xml`. `runtime init` создаёт
   `%USERPROFILE%\.local\plc-tools\cds-runtime\` и `credentials.json` со случайным паролем
   тестового пользователя рантайма `cdsci`; на свежем рантайме первый `test` создаёт этого
   пользователя (_эта ветка не проверена_; ручной путь — [MANUAL_STEPS.md, раздел 4](MANUAL_STEPS.md#4-рантайм-codesys-control-win-v3-без-прав-администратора)).

## 5. Переменные окружения

`cds.py` находит CODESYS по стандартному пути установки 3.5.17.30 сам; переменные нужны, если
путь другой или машину нужно описать явно. Полный список — [tools/README.md](../tools/README.md#переменные-окружения).

| Переменная | Значение на раннере | Когда нужна |
|---|---|---|
| `CODESYS_EXE` | `C:\Program Files (x86)\CODESYS 3.5.17.30\CODESYS\Common\CODESYS.exe` | рекомендуется задать явно |
| `CODESYS_PROFILE` | `CODESYS V3.5 SP17 Patch 3` | рекомендуется задать явно |
| `CODESYS_RUNTIME_CREDENTIALS` | файл учётных данных рантайма (`user`, `password`); по умолчанию `%USERPROFILE%\.local\plc-tools\cds-runtime\credentials.json` | только если файл в другом месте (вариант Б, раздел 6.2) |
| `COUNIT_LIBRARY` | путь к `counit.library` | только если coUnit ещё не установлена: первый `import --tests` установит её |
| `CDS_RUNTIME_LOCK` | например `C:\codesys-ci\codesys-runtime.lock` | если на машине несколько раннеров под **разными** учётными записями: блокировка рантайма по умолчанию лежит в `%LOCALAPPDATA%` и у каждого пользователя своя |
| `CODESYS_RUNTIME_EXE` | `C:\codesys-ci\GatewayPLC\CODESYSControlService.exe` | только для раннера-службы (раздел 7.3) |
| `CODESYS_RUNTIME_MAX_AGE_MIN`, `CDS_TIMEOUT_S`, `CDS_TEST_TIMEOUT_S` | 100, 900, 120 (по умолчанию) | менять не нужно |

Где задавать:

- **Переменные пользователя `codesys-ci`** — `setx ИМЯ "значение"` под этой учётной записью. `cds.py`
  сам читает `HKCU\Environment`, поэтому значения видны и процессу раннера, запущенному до `setx`.
- **Файл `.env` в каталоге раннера** (`C:\actions-runner\<репозиторий>\.env`, строки `ИМЯ=значение`)
  — переменные для всех заданий этого раннера; после правки раннер нужно перезапустить. Для
  раннера-службы надёжнее этот способ: читает ли служба переменные из профиля пользователя, _не
  проверено_.
- **Не** в YAML workflow и не в переменных репозитория: пути — свойство машины, а не репозитория.

Секретов в переменных окружения нет: пароль тестового пользователя рантайма хранится только в
`credentials.json` (профиль `codesys-ci`), `cds.py` читает его из файла и не печатает.

## 6. Рантайм CODESYS Control Win V3: два варианта

| | А. Пользовательский процесс (`cds.py`) | Б. Служба Windows «CODESYS Control Win V3» |
|---|---|---|
| Права администратора | не нужны | нужны для запуска, остановки и типа запуска службы |
| Кто запускает | `cds.py runtime restart` / `cds.py test` в задании | служба (тип запуска «Автоматически») |
| Демо-режим 2 ч | каждое задание начинает с перезапуска — новое окно 2 ч | задание должно перезапускать службу — нужны права на службу |
| Учётные данные рантайма | `runtime init` создаёт `credentials.json`, пользователь создаётся при первом входе | пользователь создаётся вручную в IDE, файл учётных данных — вручную |
| В портфолио | **используется**, проверено | не используется, с `cds.py` не проверено |

Одновременно — только один вариант: оба слушают порт 11740. Если порт занят службой,
`cds.py runtime start` отказывается, а `cds.py test` работает с рантаймом службы, не перезапуская
его («демо-лимит не контролируется»).

### 6.1 Вариант А: пользовательский процесс под управлением `cds.py`

- Процесс `"<GatewayPLC>\CODESYSControlService.exe" -d "<каталог рантайма>\boot.cfg"` запускается
  скрыто, `cds.py` ждёт порт 11740. Каталог рантайма — `%USERPROFILE%\.local\plc-tools\cds-runtime\`
  (`boot.cfg`, `work\CODESYSControl.cfg`, `credentials.json`, журнал `runtime_console.log`).
- `cds.py` останавливает только свой процесс (с `-d <boot.cfg>` в командной строке); службу Windows
  и чужие экземпляры — никогда.
- Глобальная блокировка `%LOCALAPPDATA%\plc-tools\codesys-runtime.lock`: задания разных
  репозиториев на одной машине работают с рантаймом по очереди (ожидание до `CDS_LOCK_TIMEOUT_S`,
  1800 с).
- Управление пользователями рантайма остаётся включённым ([runner_security.md](runner_security.md#310-управление-пользователями-рантайма-остаётся-включённым)).
- Служба «CODESYS Control Win V3» должна оставаться остановленной (тип запуска «Вручную» — так
  ставится по умолчанию).
- Рантайм один на машину. Не запускайте его на машине раннера под другими пользователями
  (`cds.py runtime start` от имени инженера): процесс чужого пользователя `cds.py` раннера не
  остановит, порт 11740 будет занят, и задание упадёт на шаге перезапуска рантайма. Блокировка
  `cds.py` у каждого пользователя своя; общую задаёт `CDS_RUNTIME_LOCK` (раздел 5).
- Состояние рантайма (пользователи, загруженные приложения, сертификаты) 32-битный процесс пишет в
  свой каталог установки `...\GatewayPLC\`. У интерактивного пользователя эти записи
  перенаправляет виртуализация UAC в `%LOCALAPPDATA%\VirtualStore\Program Files (x86)\CODESYS 3.5.17.30\GatewayPLC\`
  (проверено). Для раннера-службы это важно — раздел 7.3.

### 6.2 Вариант Б: служба Windows

На этой машине служба называется `CODESYS Control Win V3` (отображаемое имя «CODESYS Control Win
V3 Version 3.5.17.30»), работает от `LocalSystem`, тип запуска — «Вручную»; рабочий каталог —
`C:\ProgramData\CODESYS\CODESYSControlWinV3\<ID>\`. Включает администратор:

```bat
sc config "CODESYS Control Win V3" start= auto
sc start "CODESYS Control Win V3"
```

- Без лицензии служба тоже работает 2 часа с момента запуска. Перезапустит ли её Windows после
  завершения по демо-таймеру, _не проверено_ — рассчитывать на это нельзя: задание должно само
  перезапускать службу (`Restart-Service "CODESYS Control Win V3"`). Обычному пользователю это не
  разрешено; выдать учётной записи раннера права на запуск и остановку именно этой службы может
  администратор (`sc sdset`) — это изменение настроек безопасности Windows, его делает и
  документирует владелец машины. Шаг перезапуска службы в `plc-codesys.yml` не входит.
- `runtime init` и `runtime start` для варианта Б не нужны. Пользователя рантайма создайте в IDE
  при первом входе на устройство, его логин и пароль запишите в JSON `{"user": "...", "password": "..."}`,
  путь к файлу — в `CODESYS_RUNTIME_CREDENTIALS`; права на файл — только `codesys-ci` и
  администраторы.
- С лицензией CODESYS Control Win ограничения в 2 часа нет — тогда вариант Б с автоматическим
  запуском проще всего.

## 7. Раннер

### 7.1 Каталог, метки, число раннеров

- Каталог — короткий путь вне профилей: `C:\actions-runner\<репозиторий>\`, владелец —
  `codesys-ci` (права «Изменение»). Короткий — из-за ограничения Windows в 260 символов на путь:
  рабочий каталог задания — `_work\<репозиторий>\<репозиторий>\`, а в выгрузке plc-ci-toolkit
  (`.plc-ci-toolkit\tools\tests\fixtures\...`) пути и так длиной около 100 символов. Пробелы и
  скобки в пути шаги переносят (проверено пробным прогоном в каталоге `s p (x86)`).
- Метки: стандартные `self-hosted`, `Windows`, `X64` и своя `codesys` (`--labels codesys` при
  настройке). Workflow выбирает раннер по `["self-hosted", "windows", "codesys"]` (вход
  `runner-labels` `plc-codesys.yml`; регистр меток не важен).
- В репозиториях личной учётной записи GitHub раннер регистрируется для одного репозитория (групп
  раннеров нет — они есть только у организаций). Каждому репозиторию с Windows-job (например,
  `plc-ci-toolkit` и `plc-process-lib`) — свой раннер в своём каталоге. Все раннеры одной машины —
  под одной учётной записью `codesys-ci`: тогда блокировка рантайма `cds.py` у них общая, и задания
  из разных репозиториев ждут друг друга, а не ломают общий рантайм.
- Включение Windows-job в репозитории — переменная репозитория `PLC_CODESYS_RUNNER` = `true`
  (условие `if:` в вызывающем workflow): без раннера задание не ставится в очередь и не висит
  сутки в ожидании.

Регистрация (токен из настроек репозитория, `config.cmd`) — [MANUAL_STEPS.md, раздел 11](MANUAL_STEPS.md#11-регистрация-раннера).

### 7.2 Интерактивный запуск — для первого прогона

Под `codesys-ci` в интерактивном сеансе (вход в Windows или «Сменить пользователя»):

```bat
cd /d C:\actions-runner\<репозиторий>
run.cmd
```

Условия те же, что при проверенных прогонах `cds.py`: интерактивный сеанс, виртуализация UAC
работает, диалоги (если появятся) видны. Раннер работает, пока открыт сеанс; после перезагрузки его
нужно запустить снова (или запускать `run.cmd` планировщиком заданий при входе `codesys-ci` —
автоматический вход хранит пароль на машине, это осознанный компромисс).

### 7.3 Служба Windows под учётной записью `codesys-ci`

Служба ставится при настройке раннера (`config.cmd`) из консоли **от имени администратора**. На
вопрос «Would you like to run the runner as service? (Y/N)» — `Y`, на «User account to use for the
service» — `.\codesys-ci` (по умолчанию предлагается `NT AUTHORITY\NETWORK SERVICE` — не подходит,
раздел 2) и пароль. Ключи `--runasservice --windowslogonaccount .\codesys-ci --windowslogonpassword ...`
делают то же без вопросов, но пароль в командной строке останется в истории консоли — лучше
отвечать на вопросы. Раннер, настроенный без службы, переделать в службу нельзя: удалить его
(`config.cmd remove`) и настроить заново.

`config.cmd` сам выдаёт учётной записи право «Вход в качестве службы» и права на каталоги раннера и
`_work` (по исходному коду раннера, `WindowsServiceControlManager.cs`). Служба называется
`actions.runner.<владелец>-<репозиторий>.<имя раннера>`, её имя записано в файле `.service` в
каталоге раннера. Управление (PowerShell; запуск и остановка — от имени администратора):

```powershell
Get-Service "actions.runner.*"
Stop-Service "actions.runner.*"
Start-Service "actions.runner.*"
```

**Что меняется для CODESYS в службе** — _не проверено_, проверить первым прогоном после перехода:

1. **Виртуализация UAC.** Она включается только для интерактивных стандартных пользователей и
   выключена для неинтерактивных процессов ([UAC References, раздел Applicability](https://learn.microsoft.com/en-us/previous-versions/bb756883(v=msdn.10))).
   Под службой рантайм не сможет писать состояние в `C:\Program Files (x86)\...\GatewayPLC\`
   (нет прав) — вероятный отказ при запуске рантайма или при первом входе. Решение, которым
   пользуются для запуска Control Win обычным пользователем: копия каталога `GatewayPLC` (около
   250 МБ) в каталог с правом записи и путь к ней в `CODESYS_RUNTIME_EXE`:

   ```bat
   robocopy "C:\Program Files (x86)\CODESYS 3.5.17.30\GatewayPLC" C:\codesys-ci\GatewayPLC /E
   ```

   Права «Изменение» на `C:\codesys-ci\` — `codesys-ci`; после обновления CODESYS копию нужно
   обновить. Состояние рантайма тогда хранится в копии.
2. **Нет рабочего стола (сеанс 0).** Диалог CODESYS в `--noUI` (первый запуск, лицензия,
   обновления) некому закрыть: `cds.py` остановит процесс через `CDS_TIMEOUT_S`. Раздел 4
   выполните до перехода на службу.
3. **Переменные окружения** — через файл `.env` раннера (раздел 5).

Если хоть один пункт не заработал — оставайтесь на интерактивном запуске (7.2).

### 7.4 Обновления раннера

Нужна версия раннера не ниже **v2.327.1**: actions в workflow (`actions/checkout` v7,
`actions/setup-python` v7, `actions/upload-artifact` v7, `mikepenz/action-junit-report` v6)
выполняются на Node.js 24, а его поддержка появилась в этой версии (README этих actions). Архив со
страницы «New self-hosted runner» всегда свежее.

Раннер обновляется сам (при получении задания или в течение недели после выхода версии). С ключом
`--disableupdate` обновлять придётся вручную не позже 30 дней после выхода новой версии — иначе
GitHub перестанет отдавать раннеру задания. Оставьте автообновление: у `codesys-ci` есть права на
запись в каталог раннера.

## 8. Демо-режим 2 часа и как с ним жить

Без лицензии рантайм CODESYS Control Win работает 2 часа с момента запуска процесса и
завершается ([справка CODESYS](https://content.helpme-codesys.com/en/CODESYS%20Control/_rtsl_product_overview.html)).
`python tools/codesys/cds.py runtime status` показывает, сколько процесс работает (`age_min`) и
сколько осталось (`demo_left_min`). Задание `plc-codesys.yml` устроено так:

1. **Перезапуск в начале задания** — шаг `cds.py runtime restart` (вход `restart-runtime`, по
   умолчанию включён): каждое задание получает новое окно 2 часа.
2. **Страховка** — `cds.py test` сам перезапускает рантайм, если тот работает дольше
   `CODESYS_RUNTIME_MAX_AGE_MIN` (100 минут).
3. **Таймаут задания** — вход `timeout-minutes` (по умолчанию 90); задание guard не пропустит
   значение 110 и больше. Обычный прогон короче: один шаг `cds.py` (импорт, сборка, тесты) на
   примере шаблона занимает около 20 секунд, всё задание — минуты.
4. **По очереди** — раннер выполняет одно задание за раз; `concurrency` в `plc-codesys.yml` ставит
   задания репозитория в очередь, блокировка `cds.py` — задания разных репозиториев на машине.
5. В конце задания (и после его отмены) рантайм останавливается — шаг `cds.py runtime stop`, вход
   `stop-runtime`, по умолчанию включён: следующее задание всё равно начнёт с перезапуска, а в
   простое порты рантайма закрыты. С `stop-runtime: false` рантайм работает до следующего задания
   или до конца демо-окна.

Для варианта Б то же самое достигается перезапуском службы в начале задания (раздел 6.2) или
лицензией.

## 9. Права учётной записи раннера

| Нужно | Зачем |
|---|---|
| локальный пользователь, группа «Пользователи» | задания не должны иметь прав администратора |
| «Вход в качестве службы» | только раннер-служба; выдаёт `config.cmd` |
| «Изменение» на `C:\actions-runner\<репозиторий>\` (включая `_work`) | файлы раннера, рабочие каталоги заданий; для службы выдаёт `config.cmd` |
| чтение и запуск `C:\Program Files (x86)\CODESYS 3.5.17.30\` | есть у всех пользователей по умолчанию |
| запись в `C:\ProgramData\CODESYS\` | временные файлы CODESYS, установка coUnit; отдельно выдавать не нужно: в SP17 рабочие каталоги CODESYS в `%ProgramData%` доступны на запись всем локальным пользователям (уязвимость VDE-2024-027, исправлена в 3.5.20.10) |
| свой профиль | каталог рантайма и `credentials.json` (`%USERPROFILE%\.local\plc-tools\`), блокировка (`%LOCALAPPDATA%\plc-tools\`), настройки CODESYS (`%APPDATA%`) |
| «Изменение» на `C:\codesys-ci\` | только раннер-служба с копией `GatewayPLC` |

| Не нужно | Почему |
|---|---|
| группа «Администраторы» | задания выполняют чужой по отношению к машине код |
| права на службы CODESYS | вариант А службу не трогает |
| доступ к профилям других пользователей, сетевым папкам, сетям ПЛК | раннеру нужен только GitHub |
| вход по RDP | только на время настройки, если иначе нельзя |

## 10. Журналы

| Что | Где |
|---|---|
| приложение раннера | `C:\actions-runner\<репозиторий>\_diag\Runner_*.log` (новый файл при каждом запуске, время в UTC) |
| задания на раннере | `_diag\Worker_*.log`; журнал шагов — в GitHub: Actions → запуск → задание |
| служба раннера | журнал Windows «Приложение», источник `ActionsRunnerService`: `Get-EventLog -LogName Application -Source ActionsRunnerService -Newest 20` |
| `cds.py` | `build\import.log`, `build\build.log`, `build\test.log`, `build\codesys\*.stdout.log`, `build\codesys\result-*.json` в рабочем каталоге задания; задание выгружает их в артефакт `plc-codesys-reports` |
| результаты тестов | `build\test-results.json`, `build\junit.xml` (в артефакте) и сводка запуска |
| рантайм, вариант А | консоль процесса — `%USERPROFILE%\.local\plc-tools\cds-runtime\runtime_console.log` и `.err`, `runtime.pid.json`; журнал рантайма `CODESYS Control Win V3.log` и состояние (`PlcLogic`, `cert`, загруженное приложение) — `%LOCALAPPDATA%\VirtualStore\Program Files (x86)\CODESYS 3.5.17.30\GatewayPLC\` (интерактивно) или копия `GatewayPLC` (служба); XML coUnit — `%LOCALAPPDATA%\VirtualStore\coUnit_xunit_testresults.xml` (`cds.py` его не использует: результаты читаются из переменных coUnit) |
| рантайм, вариант Б | `C:\ProgramData\CODESYS\CODESYSControlWinV3\<ID>\` |
| шлюз CODESYS | `C:\ProgramData\CODESYS\CODESYSGatewayV3\<ID>\` |

Учётные данные рантайма `cds.py` в журналы не пишет. Журналы `_diag` и артефакты содержат пути и
имя машины — не публикуйте их целиком.

## 11. Обслуживание

- **Обновления Windows.** После перезагрузки служба раннера запускается сама; интерактивный раннер —
  только после входа `codesys-ci`.
- **CODESYS остаётся 3.5.17.30** — версия портфолио. В 3.5.20.10 рабочие каталоги рантайма и шлюза
  переехали из `%ProgramData%` в `%APPDATA%` профиля службы ([VDE-2024-027](https://certvde.com/en/advisories/VDE-2024-027)):
  при переходе на новую версию проверьте `runtime init` и этот документ заново.
- **Смена пароля пользователя рантайма.** Остановить рантайм, удалить `credentials.json` и
  состояние рантайма (каталог `VirtualStore\...\GatewayPLC\` — необратимо: рантайм забудет
  пользователей и приложения), выполнить `runtime init` и `cds.py test`.
- **Удаление раннера.** Settings → Actions → Runners → раннер → Remove: GitHub покажет команду
  `config.cmd remove --token <токен>`; для службы — из консоли администратора.

## 12. Чек-лист готовности раннера

- [ ] Отдельная машина или ВМ, не в сети ПЛК; исходящий HTTPS к GitHub есть, входящие закрыты
  ([runner_security.md, раздел 3.8](runner_security.md#38-сеть-раннера)).
- [ ] Пользователь `codesys-ci` без прав администратора, профиль создан.
- [ ] CODESYS 3.5.17.30 (32 бит), служба «CODESYS Control Win V3» остановлена и в режиме «Вручную».
- [ ] coUnit 1.2.0.0 в репозитории `System`.
- [ ] Python 3.11+ и Git — для всех пользователей, в `PATH` у `codesys-ci`.
- [ ] Под `codesys-ci`: `runtime init`, `cds.py build`, `cds.py test` — зелёные (раздел 4).
- [ ] Раннер версии не ниже v2.327.1 (раздел 7.4), зарегистрирован с меткой `codesys`, в репозитории
  `PLC_CODESYS_RUNNER` = `true`.
- [ ] Первый Windows-job прошёл ([MANUAL_STEPS.md, раздел 12](MANUAL_STEPS.md#12-первый-прогон-windows-job)).

## Источники

- GitHub Docs: [Adding self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners),
  [Configuring the self-hosted runner application as a service](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application),
  [Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
  (требования к ОС, связь с GitHub, обновления),
  [Monitoring and troubleshooting self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/monitor-and-troubleshoot),
  [Running scripts before or after a job](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
  (файл `.env`).
- Исходный код раннера: [actions/runner, WindowsServiceControlManager.cs](https://github.com/actions/runner/blob/main/src/Runner.Listener/Configuration/WindowsServiceControlManager.cs)
  (учётная запись службы, имя службы, права).
- Microsoft: [UAC References — Virtualization Reference](https://learn.microsoft.com/en-us/previous-versions/bb756883(v=msdn.10)).
- CODESYS: [Product overview — демо-режим 2 часа](https://content.helpme-codesys.com/en/CODESYS%20Control/_rtsl_product_overview.html);
  CERT@VDE: [VDE-2024-027](https://certvde.com/en/advisories/VDE-2024-027).
- Проверенные факты о CODESYS 3.5.17.30 на этой машине — [tools/README.md](../tools/README.md#codesyscdspy--codesys-из-консоли),
  [MANUAL_STEPS.md](MANUAL_STEPS.md), [TEST_REPORT.md](TEST_REPORT.md).
