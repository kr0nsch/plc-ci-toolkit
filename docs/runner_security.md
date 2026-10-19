# Безопасность self-hosted раннера с CODESYS

Windows-job (`plc-codesys.yml`) выполняется на машине владельца портфолио, а репозитории публичные.
Здесь — чем это опасно, что сделано в workflow и в настройках, что остаётся на совести владельца, и
чек-лист. Настройка самой машины — [runner_setup.md](runner_setup.md), первая регистрация —
[MANUAL_STEPS.md](MANUAL_STEPS.md#9-self-hosted-раннер-порядок-действий).

## 1. Почему это риск

Официальная позиция GitHub — «We recommend that you only use self-hosted runners with private
repositories» ([Adding self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)).
Причины по [Secure use reference, раздел Hardening for self-hosted runners](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners):

- **PR из форка — это чужой код на вашей машине.** Любой пользователь GitHub может сделать форк и
  открыть pull request. Для события `pull_request` GitHub берёт файлы workflow из самого PR: автор
  может добавить свой workflow с `runs-on: [self-hosted, windows, codesys]`, и он выполнится на
  раннере. Условия `if:` и проверки в YAML от этого не защищают — автор PR их просто уберёт.
- **Раннер не одноразовый.** В отличие от раннеров GitHub, self-hosted раннер не получает чистую
  виртуальную машину на каждое задание: вредоносное задание может оставить на машине что угодно
  и повлиять на следующие.
- **Всё, что видит учётная запись раннера, видит задание**: файлы, переменные окружения, токен
  `GITHUB_TOKEN` задания, секреты, переданные заданию, сеть.
- **Особенность АСУ ТП.** Машина с CODESYS часто стоит рядом с ПЛК и SCADA. Задание с доступом в
  такую сеть — это возможность загрузить программу в контроллер или атаковать стенд. Проекты
  заказчиков на такой машине — утечка.

Как одобрять запуски из форков — [Approving workflow runs from forks](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks).

## 2. Модель угроз

| Угроза | Как реализуется | Меры (раздел) | Остаточный риск |
|---|---|---|---|
| код из PR форка на раннере | workflow с `pull_request`, свой workflow в PR | политика запуска (3.1), одобрение запусков для всех внешних участников (3.2), группа раннеров — только у организаций | владелец одобрил запуск, не прочитав изменения `.github/` |
| вредоносный push в `main` или тег | украденный токен или пароль, доступ соавтора | одна учётная запись с правом записи, 2FA, защита веток и тегов, окружение с одобрением (3.3) | компрометация учётной записи владельца |
| цепочка поставок actions | обновлённое или подменённое стороннее action выполняется на раннере | только закреплённые по SHA actions (3.5) | уязвимость в закреплённой версии |
| закрепление на машине | вредоносное задание оставляет процесс, задачу планировщика, изменённые файлы | учётная запись без прав администратора (3.7), очистка (3.11), одноразовые раннеры как вариант (3.6) | постоянный раннер не гарантирует чистоту |
| утечка секретов | печать в журнал, артефакты | секретов в задании нет, учётные данные рантайма не печатаются (3.9) | — |
| атака из раннера в сеть | доступ к ПЛК, SCADA, файловым серверам | отдельная сеть, только исходящий HTTPS к GitHub (3.8) | — |
| доступ к рантайму CODESYS по сети | шлюз и рантайм слушают все адаптеры | закрытые входящие порты, `LocalAddress` шлюза, управление пользователями рантайма (3.8, 3.10) | уязвимости самого CODESYS 3.5.17 ([бюллетени CERT@VDE](https://certvde.com/en/advisories/vendor/codesys/)) |

## 3. Меры

### 3.1 Политика запуска

Windows-job запускается **только** по `push` в `main` и по тегам, вручную (`workflow_dispatch`) и
по расписанию (`schedule`). Никогда — по `pull_request`, `pull_request_target`, `issue_comment`,
`workflow_run` и в форках. Реализовано в двух местах:

1. **Вызывающий workflow** не вызывает `plc-codesys.yml` для pull request и без зарегистрированного
   раннера. В репозитории-потребителе — по образцу [examples/consumer-ci.yml](../examples/consumer-ci.yml)
   (проверки без CODESYS идут и для pull request, задание `codesys` — нет):

   ```yaml
   codesys:
     needs: static
     if: >-
       vars.PLC_CODESYS_RUNNER == 'true' && (
         github.event_name == 'workflow_dispatch' ||
         (github.event_name == 'push' &&
           (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')))
       )
     uses: kr0nsch/plc-ci-toolkit/.github/workflows/plc-codesys.yml@v1
   ```

   В самом plc-ci-toolkit задание CODESYS вынесено в отдельный workflow
   [codesys.yml](../.github/workflows/codesys.yml) без события `pull_request` вообще.
2. **Задание `guard`** в `plc-codesys.yml` выполняется на GitHub-hosted раннере до задания на
   машине с CODESYS и отклоняет любое событие, кроме `push`, `workflow_dispatch` и `schedule`, запуск
   в форке, push в ссылку вне `allowed-refs` (по умолчанию `refs/heads/main refs/tags/*`) и таймаут
   задания 110 минут и больше.

Оба рубежа защищают от **ошибок конфигурации** (кто-то добавил `pull_request` в вызывающий workflow).
От PR, который сам меняет workflow, они не защищают — для этого раздел 3.2.

`workflow_dispatch` guard пропускает с любой ветки: так владелец прогоняет ветку в CODESYS до
слияния. Ручной запуск доступен только пользователям с правом записи — тем, кто и так может
отправить код в `main` (раздел 3.3 — как ограничить ветки окружением).

### 3.2 Запуски из форков — только после одобрения

Settings → Actions → General → «Approval for running fork pull request workflows from contributors»
→ **«Require approval for all external contributors»** ([Managing GitHub Actions settings for a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)).
По умолчанию одобрение нужно только при первом PR участника: после одного принятого PR его
следующие PR запускали бы workflow без вопросов.

Перед «Approve workflows to run» владелец открывает «Files changed» и проверяет изменения в
`.github/`, `ci/`, `tools/` и всё, что выполняется в заданиях. PR, меняющий workflow или скрипты,
одобряется только после чтения изменений; при сомнении — не одобрять и попросить разделить PR.
Проверки PR без изменений workflow идут на GitHub-hosted раннере и раннер с CODESYS не трогают.

Группы раннеров с запретом публичных репозиториев и ограничением по workflow есть только у
организаций ([Managing access to self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access));
у репозиториев личной учётной записи раннер привязан к репозиторию, и главная защита — одобрение
запусков.

### 3.3 Окружение с обязательным одобрением

Окружение (Settings → Environments) `codesys` с правилами:

- **Required reviewers** — владелец. Задание, объявившее `environment: codesys`, ждёт его
  одобрения; секреты окружения задание получает только после одобрения.
- **Deployment branches and tags** — «Selected branches and tags»: `main` и `v*`. Задание из любой
  другой ветки в окружение не попадёт.
- **Prevent self-review** — **не включать**, если владелец один: он сам запускает задания и не
  сможет их одобрить.

В публичных репозиториях окружения и правила доступны на всех тарифах ([Managing environments for deployment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)).
Ограничение: правило действует на задания, которые **объявили** окружение; workflow, написанный
злоумышленником, окружение не объявит. Поэтому окружение — второй рубеж для своих запусков
(украденный токен, ошибочный push), а не замена разделам 3.1–3.2. Сейчас задание `codesys` в
`plc-codesys.yml` окружение не объявляет — для этого нужен отдельный вход workflow (открытый вопрос
в [ASSUMPTIONS.md](ASSUMPTIONS.md#ci-и-раннер-plc-ci-toolkit), E6).

### 3.4 Минимальные права `GITHUB_TOKEN`

- В настройках репозитория (Settings → Actions → General → Workflow permissions) — права только на
  чтение (`contents` и `packages`), флажок «Allow GitHub Actions to create and approve pull
  requests» снят.
- В workflow права заданы явно: `permissions: {}` у задания `guard`, `contents: read` у задания
  `codesys`. Всё, что не перечислено в `permissions`, получает `none` ([Workflow syntax, permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)).
- Публикация JUnit — в режиме аннотаций и сводки (`annotate_only`), без `checks: write`.
- `actions/checkout` с `persist-credentials: false`: токен задания не остаётся в `.git/config` на
  постоянной машине.

### 3.5 Только закреплённые actions

На раннере выполняются `actions/checkout`, `actions/upload-artifact` и `mikepenz/action-junit-report` —
все закреплены по полному SHA коммита. Обновлять — осознанно, читая изменения. В настройках
репозитория (Settings → Actions → General → Actions permissions) — «Allow OWNER, and select
non-OWNER, actions and reusable workflows» со списком используемых actions: `actions/checkout`,
`actions/setup-python`, `actions/upload-artifact`, `mikepenz/action-junit-report`.

Флажок «Require actions to be pinned to a full-length commit SHA» — после проверки: переиспользуемые
workflow вызываются по локальному пути (`./.github/workflows/...` в plc-ci-toolkit) и по тегу (`@v1`
в потребителях), а как политика обращается с такими ссылками, _не проверено_. Включив флажок,
запустите workflow вручную и убедитесь, что задания стартуют.

### 3.6 Одноразовые раннеры (вариант на будущее)

Постоянный раннер не гарантирует чистую машину. Если риск неприемлем:

- **Одноразовый раннер** (`config.cmd --ephemeral`) берёт одно задание и снимается с регистрации.
- **Раннер just-in-time**: конфигурация на одно задание создаётся запросом
  `POST /repos/{owner}/{repo}/actions/runners/generate-jitconfig` (нужен токен с правами
  администратора репозитория — хранить на хосте, а не в ВМ), раннер запускается командой
  `run.cmd --jitconfig <encoded_jit_config>` ([REST API endpoints for self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners),
  [Secure use reference, Using just-in-time runners](https://docs.github.com/en/actions/reference/security/secure-use#using-just-in-time-runners)).
- Вместе с этим — виртуальная машина Hyper-V с контрольной точкой «чистая»: CODESYS, coUnit,
  Python, Git, пользователь `codesys-ci`, выполненный `runtime init`. После каждого задания хост
  откатывает ВМ к контрольной точке. Заодно каждый запуск начинается со свежего рантайма.

Цена — сценарий на хосте (создать конфигурацию, откатить и запустить ВМ) и поддержка образа. В
портфолио не используется: здесь раннер постоянный, задания запускает только владелец.

### 3.7 Учётная запись и содержимое машины

- Раннер работает под отдельным стандартным пользователем `codesys-ci` без прав администратора
  ([runner_setup.md, раздел 2](runner_setup.md#2-учётная-запись-раннера)), не под учётной записью
  инженера и не под `LocalSystem`.
- На машине нет проектов заказчиков, сеансов браузера, ключей SSH с правом записи, токенов GitHub.
- Файлы регистрации раннера (`.runner`, `.credentials`, `.credentials_rsaparams` в каталоге раннера)
  не копировать и не выкладывать: по ним можно выдать себя за раннер. Задания выполняются от той
  же учётной записи и могут их прочитать — это ещё одна причина не запускать чужой код (3.1–3.2).
- Токен регистрации действует час; PAT для раннера не нужен (ключ `--pat` не используется).

### 3.8 Сеть раннера

- Отдельная ВМ или машина в отдельном сегменте (VLAN) **без маршрута в сети ПЛК и SCADA**.
- Исходящие — только HTTPS (443) к GitHub; список доменов — [Self-hosted runners reference,
  Communication](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#communication):
  `github.com`, `api.github.com`, `*.actions.githubusercontent.com`, `codeload.github.com`,
  `results-receiver.actions.githubusercontent.com`, `*.blob.core.windows.net` (журналы, артефакты)
  и домены обновлений раннера. Заданию CODESYS PyPI не нужен (`cds.py` — только стандартная
  библиотека). Фильтр по доменам — на межсетевом экране или прокси периметра; брандмауэр Windows
  фильтрует по адресам и портам.
- Входящие соединения закрыты (брандмауэр Windows, профиль по умолчанию — блокировать). CODESYS
  слушает: шлюз — TCP 1217, рантайм — TCP 11740–11743 и UDP 1740–1743, OPC UA — 4840
  ([порты CODESYS](https://content.helpme-codesys.com/en/CODESYS%20Control/_rtsl_start_page.html)).
  Заданию они нужны только с `127.0.0.1`. Правила, которые добавил установщик CODESYS, проверить:
  `Get-NetFirewallRule -DisplayName "*CODESYS*"`.
- Шлюз CODESYS до 3.5.21.0 слушает порт 1217 на всех адаптерах ([VDE-2025-013](https://certvde.com/en/advisories/VDE-2025-013)).
  Ограничить локальным адресом: в `C:\ProgramData\CODESYS\CODESYSGatewayV3\<ID>\Gateway.cfg`, секция
  `[CmpGwCommDrvTcp]`, строка `LocalAddress=127.0.0.1`, затем перезапуск службы «CODESYS Gateway V3»
  (администратор). _Не проверено на этой машине — после правки выполнить `cds.py test`._

### 3.9 Секреты и журналы

- Заданию CODESYS секреты GitHub не нужны. Учётные данные тестового пользователя рантайма лежат в
  `credentials.json` в профиле `codesys-ci`; `cds.py` читает их только из файла и не печатает (ни
  пароль, ни имя пользователя). Маскирование секретов GitHub к этому файлу не относится — поэтому
  его содержимое нельзя выводить в журнал ни командой `type`, ни отладочной печатью.
- В артефакт попадают только журналы и отчёты `build/` (`*.log`, `*.json`, `*.xml`, `*.md`);
  каталог рантайма и профиль пользователя — никогда. Файл задания `build/codesys/job-*.json`
  содержит путь к `credentials.json`, но не его содержимое.
- Артефакты запусков pull request (`plc-static-reports`) собраны из кода PR, в том числе из форка:
  их нельзя скачивать и исполнять в workflow с правами на запись или с секретами (`workflow_run`,
  `pull_request_target`) — это путь к подмене артефактов. В plc-ci-toolkit и в примере потребителя
  артефакты только выгружаются, ни одно задание их не скачивает.
- Если секрет когда-нибудь понадобится: секрет окружения `codesys` (3.3), передавать через `env:`,
  производные значения маскировать (`::add-mask::`), не включать отладочный журнал шагов при
  секретах в окружении.

### 3.10 Управление пользователями рантайма остаётся включённым

Рантайм слушает сетевые порты: без управления пользователями любой, кто до него достучится, может
загрузить в него программу. Поэтому:

- `SECURITY.UserMgmtEnforce=NO` в `CODESYSControl.cfg` **не** задаётся (в копии `work\CODESYSControl.cfg`,
  которую делает `runtime init`, строка закомментирована, как в штатном файле);
- тестовый пользователь `cdsci` со случайным паролем (`runtime init`, `secrets.token_urlsafe`);
  вход из скрипта — `online.set_default_credentials`;
- шифрование связи и анонимный вход не трогаются.

### 3.11 Чистый рабочий каталог

- `actions/checkout` с `clean: true` (по умолчанию) в начале задания удаляет из рабочего каталога всё,
  чего нет в git, включая `build/` с проектами и журналами прошлого прогона; `cds.py test --clean`
  пересоздаёт тестовый проект; вход в рантайм удаляет с него чужие приложения
  (`login(..., delete_foreign_apps=True)`); `cds.py test` удаляет старые `test-results.json` и `junit.xml`.
- Каталог `_work\_temp` раннер очищает сам.
- Рантайм перезапускается в начале каждого задания (`cds.py runtime restart`) и останавливается в
  конце, в том числе после отмены (`cds.py runtime stop` в шаге с `if: always()`, вход `stop-runtime`,
  по умолчанию включён): в простое порты рантайма не открыты (W5 в
  [ASSUMPTIONS.md](ASSUMPTIONS.md#переиспользуемые-workflow-plc-ci-toolkit), вместо E7).
- По желанию — сценарий перед заданием (`ACTIONS_RUNNER_HOOK_JOB_STARTED` в файле `.env` раннера,
  [Running scripts before or after a job](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)),
  который завершает процессы `CODESYS.exe` учётной записи раннера, оставшиеся от прерванного
  задания. Сценарий хранится вне каталога раннера, например `C:\codesys-ci\hooks\job-started.ps1`:

  ```powershell
  $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  Get-CimInstance Win32_Process -Filter "Name='CODESYS.exe'" | ForEach-Object {
      $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
      if ("$($owner.Domain)\$($owner.User)" -eq $me) { Stop-Process -Id $_.ProcessId -Force }
  }
  ```

  _Поиск процессов и владельца проверен (без остановки); сам сценарий в раннере не запускался._

### 3.12 Наблюдение и реакция

- Раз в месяц: история запусков Actions (кто, что, на каком раннере), журналы `_diag`, список
  раннеров (Settings → Actions → Runners), обновления Windows и бюллетени CODESYS.
- Раннер, который не нужен, — удалить (Settings → Actions → Runners → Remove).
- **Подозрение на компрометацию**: удалить раннер в GitHub; отключить машину от сети; сменить пароль
  `codesys-ci` и пароль пользователя рантайма; проверить учётную запись GitHub (сеансы, токены, ключи
  SSH, приложения); переустановить машину или откатить ВМ к чистой контрольной точке; просмотреть
  журналы запусков за период.

## 4. Чек-лист

Перед первым запуском:

- [ ] Settings → Actions → General: «Require approval for all external contributors».
- [ ] Settings → Actions → General: права `GITHUB_TOKEN` — только чтение; создание и одобрение PR
  из Actions запрещено.
- [ ] Settings → Actions → General: разрешены только используемые actions (3.5); все `uses:` в
  workflow закреплены по SHA.
- [ ] В вызывающем workflow Windows-job не вызывается для `pull_request`; `PLC_CODESYS_RUNNER` = `true`
  только в репозиториях с раннером.
- [ ] Ветка `main` защищена (Settings → Rules или Branches): без принудительного push и удаления.
- [ ] Окружение `codesys` (если используется): одобрение владельцем, ветки `main` и теги `v*`.
- [ ] Учётная запись GitHub владельца — с 2FA, право записи в репозиторий ни у кого больше нет.
- [ ] Раннер — под `codesys-ci` без прав администратора, на отдельной машине или ВМ без маршрута в
  сети ПЛК; входящие порты закрыты.
- [ ] На машине нет проектов заказчиков, токенов, ключей SSH и сеансов браузера.
- [ ] Управление пользователями рантайма включено (`SECURITY.UserMgmtEnforce=NO` нигде не задан).

Регулярно:

- [ ] Одобряя запуск PR из форка, прочитать изменения `.github/`, `ci/`, `tools/`.
- [ ] Раз в месяц — история запусков, `_diag`, список раннеров, обновления Windows и бюллетени CERT@VDE.
- [ ] Обновления actions — только с чтением изменений и новым SHA.

## Источники

- GitHub Docs: [Adding self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners);
  [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
  (разделы Hardening for self-hosted runners, Using just-in-time runners, Mitigating the risks of
  untrusted code checkout); [Approving workflow runs from forks](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks);
  [Managing GitHub Actions settings for a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository);
  [Managing environments for deployment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments);
  [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token);
  [Workflow syntax: permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions);
  [Managing access to self-hosted runners using groups](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access);
  [Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners);
  [REST API endpoints for self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners);
  [Running scripts before or after a job](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts).
- CERT@VDE: [VDE-2025-013](https://certvde.com/en/advisories/VDE-2025-013) (шлюз на всех адаптерах),
  [VDE-2024-027](https://certvde.com/en/advisories/VDE-2024-027) (права на рабочие каталоги).
- CODESYS: [порты рантайма и шлюза](https://content.helpme-codesys.com/en/CODESYS%20Control/_rtsl_start_page.html),
  [управление пользователями ПЛК](https://content.helpme-codesys.com/en/Security/_sec_user_management_plc.html).
