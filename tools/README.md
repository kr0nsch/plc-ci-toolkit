# Инструменты шаблона (`tools/`)

Исходники ST лежат в `src/` и `tests/` в раскладке [docs/LAYOUT.md](../docs/LAYOUT.md). Инструменты ниже
читают эту раскладку, собирают из неё обычный ST и проверяют его без CODESYS.

| Инструмент | Что делает | Python |
|---|---|---|
| `codesys/stlayout.py` | единственный разбор и запись раскладки: лексер ST, заголовки, объекты, проверки | IronPython 2.7 и 3.8+ |
| `assemble.py` | склейка раскладки в `build/assembled/` (варианты `iec`, `rusty`, `iec-checker`) | 3.11+ |
| `st_flavors.py` | замены текста для вариантов `rusty` и `iec-checker` (используется `assemble.py`) | 3.11+ |
| `check/st_lint.py` | линтер ST по правилам `CONTRIBUTING.md` | 3.11+ |
| `check/run_iec_checker.py` | статанализ iec_checker с обоснованием находок | 3.11+ |
| `check/run_rusty.py` | проверка компилятором RuSTy (`plc --check`) | 3.11+ |
| `codesys/cds.py` | командная строка CODESYS: импорт, сборка, тесты coUnit в рантайме, экспорт, roundtrip, отчёт ([ниже](#codesyscdspy--codesys-из-консоли)) | 3.11+ |
| `codesys/import_sources.py`, `export_sources.py`, `build.py`, `run_tests.py`, `cds_common.py` | шаги в CODESYS (CODESYS Scripting); запускаются только через `cds.py` | IronPython 2.7 |

Зависимости для разработки: `python -m pip install -r requirements-dev.txt` (ruff, mypy, pytest, hypothesis).
Настройки ruff, mypy и pytest — в `pyproject.toml`.

Быстрая проверка всего, что не требует CODESYS:

```
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest
python tools/assemble.py
python tools/check/st_lint.py
python tools/check/run_iec_checker.py            # бинарник v0.4 (в CI — тоже); образ nightly: --docker IMAGE
python tools/check/run_rusty.py --optional       # если RuSTy не установлен — пропуск
```

## `codesys/stlayout.py` — контракт раскладки

Модуль написан на общем подмножестве Python 2.7 и 3.8+, чтобы его без изменений импортировали и
скрипты CODESYS (IronPython 2.7), и инструменты на Python 3: без f-строк, аннотаций (типы — в
комментариях `# type:`), `pathlib`, `dataclasses`; файлы — через `io.open(..., encoding='utf-8')`.
Совместимость проверяет `tools/tests/test_py27_compat.py` (разбор `ast`: интерпретатора 2.7 в окружении нет).
Формат кода скриптов `tools/codesys/*.py` ruff не трогает: форматтер рассчитан на Python 3.

Основной API:

```python
import stlayout as L

result = L.scan(root, "src")  # ScanResult: objects (детерминированный порядок), issues, errors, ok
for obj in result.objects:  # StObject
    obj.kind  # dut | gvl | pvl | itf | pou
    obj.pou_kind  # program | function_block | function (для pou)
    obj.name, obj.folder  # имя и папки проекта CODESYS внутри категории
    obj.decl, obj.impl  # тексты (нормализованные)
    obj.header  # DeclHeader: keyword, name, modifiers, extends, implements, return_type, type_kind
    obj.methods  # [StMethod: name, decl, impl (None у интерфейса и ABSTRACT без тела)]
    obj.properties  # [StProperty: name, decl, get_decl, get_impl, set_decl, set_impl, has_get, has_set]
L.write_objects(objects, root, "src", clean=True)  # экспорт: те же файлы; clean — удалить лишние *.st;
# регистр имён существующих каталогов и файлов приводится к именам объектов (переименование в IDE
# только регистром на Windows не теряет файлы)
L.object_files(obj)  # [(путь от корня, текст)] — какие файлы соответствуют объекту
L.parse_header(text), L.parse_var_blocks(text), L.parse_type_decl(text), L.tokenize(text)
L.normalize_text(text), L.read_text(path), L.write_text(path, text)
```

**Нормализация текста** (`normalize_text`, её применяют `scan` и `write_objects`): убирается BOM;
CRLF и одиночный CR заменяются на LF; у каждой строки срезаются хвостовые пробелы и табы; хвостовые
пустые строки убираются; непустой текст заканчивается ровно одним `\n`; текст из одних пробельных
символов становится пустым (пустой файл — 0 байт). Табы в начале и середине строк не меняются
(их ловит линтер). Цикл `scan → write_objects` для нормализованного входа даёт побайтно те же файлы;
для ненормализованного — нормализованные, и следующий цикл уже ничего не меняет.

**Разбор заголовков** пропускает комментарии `//`, `(* *)` (с вложенностью), `/* */` и прагмы `{...}`;
строки `'...'`/`"..."` с экранированием `$` и типизированные литералы (`T#5s`, `INT#3`, `E_Mode#Auto`)
лексер разбирает целиком, поэтому ключевые слова внутри них не мешают.

В раскладке игнорируются скрытые файлы (`.gitkeep`) и `*.md`. Замечания `scan()`:

| Код | Что не так |
|---|---|
| LAY001 | неизвестный вид файла (`Foo.txt`, `X.method.impl.st` в интерфейсе, `.decl.st` чужого объекта) |
| LAY002 | имя в тексте не совпадает с именем файла (`FB_A.decl.st` объявляет `FB_B`) |
| LAY003 | нет обязательного файла: тела POU, тела метода (кроме `ABSTRACT`), аксессоров свойства |
| LAY004 | файл без пары: тело метода без объявления, аксессор без `<P>.prop.decl.st` |
| LAY005 | недопустимое содержимое: `END_FUNCTION_BLOCK` и т. п. в объявлении или теле, нет `END_TYPE`/`END_VAR`, методы у `FUNCTION`; предупреждение — `.pvl.st` без `PERSISTENT` |
| LAY006 | имя повторяется (имена CODESYS — без учёта регистра): объекты, методы и свойства одного POU, `src/` и `tests/` |
| LAY007 | файл не в UTF-8 |
| LAY008 | не на своём месте: неизвестный каталог верхнего уровня, файл в папке без объекта, каталог внутри объекта |
| LAY009 | нет или неверный заголовок (`TYPE`, `VAR_GLOBAL`, `INTERFACE`, `METHOD`, `PROPERTY ... : тип`, ...) |
| LAY010 | имя файла или каталога — не идентификатор ST |
| ASM001 | (`assemble.py`) цикл наследования `EXTENDS` |

## `assemble.py` — склейка

```
python tools/assemble.py [--root R] [--src src] [--tests] [--tests-dir tests]
                         [--flavor iec|rusty|iec-checker] [--out DIR] [--strip-namespace NS]... [--check-only] [-q]
```

| Флаг | Смысл |
|---|---|
| `--src`, `--tests-dir` | каталоги раскладки (от `--root`, по умолчанию корень репозитория) |
| `--tests` | добавить объекты `tests/` (имена должны быть уникальны вместе с `src/`) |
| `--flavor` | вариант сборки, по умолчанию `iec` |
| `--out` | каталог результата; по умолчанию `build/assembled/` для `iec` и `build/assembled/<вариант>/` для остальных |
| `--strip-namespace` | убрать префикс библиотеки (`CfUnit.`, `PL.`); можно повторять; точка в конце не обязательна |
| `--check-only` | только проверить раскладку и сборку, ничего не писать |

Результат: `<Имя>.st` на каждый объект верхнего уровня, `all.st` и `sourcemap.json`. Перед записью из
каталога результата удаляются старые `*.st` (только верхнего уровня, подкаталоги вариантов не трогаются).
Порядок в `all.st`: DUT, GVL, интерфейсы, функции, функциональные блоки, программы; внутри группы
базовые типы раньше производных (`EXTENDS`), затем по имени. `sourcemap.json` — список сегментов
`[строка в собранном файле, число строк, исходный файл или null, строка в исходном файле]`;
`null` — строки, созданные сборкой. По карте `run_rusty.py` и `run_iec_checker.py` показывают находки в
строках исходников. Все замены сохраняют число строк фрагмента, поэтому карта точная.

Коды выхода: 0 — успех, 1 — ошибки раскладки или сборки (сообщения `путь:строка: КОД текст`), 2 — аргументы.

### Варианты сборки

| Вариант | Для чего | Что меняется |
|---|---|---|
| `iec` | чтение, `all.st`, основа других вариантов | ничего: объявление, методы (`METHOD ... END_METHOD`), свойства (`PROPERTY_GET`/`PROPERTY_SET ... END_PROPERTY`), тело, `END_...` — как в docs/LAYOUT.md |
| `rusty` | `run_rusty.py` | `iec` + замены совместимости RuSTy (таблица ниже) |
| `iec-checker` | `run_iec_checker.py` | понижение для разборщика iec_checker v0.4 (таблица ниже); **только для статанализа**, семантика приближённая |

### Замены совместимости RuSTy (`--flavor rusty`)

Найдены прогоном RuSTy 1.0.6 `plc --check` на реалистичном ST CODESYS
(`tools/tests/fixtures/sample/`, плюс пробные файлы).

| № | Конструкция CODESYS | Замена | Ошибка RuSTy без замены |
|---|---|---|---|
| R1 | `itfX <> 0`, `itfX = 0`, `0 <> itfX`; переменная с префиксом `itf` (также `THIS^.itfX`, `stCfg.itfX`) | `__ItfIsSet(ADR(itfX))`, `(NOT __ItfIsSet(ADR(itfX)))`; функция `__ItfIsSet` — в `__RustyCompat.st` и один раз в начале `all.st`, только если замена была | E031: `__FATPOINTER` и `DINT` несовместимы |
| R2 | `NS.Имя` для `--strip-namespace NS` | `Имя` (объявления — в прокладках `tools/check/rusty_shims/`) | E048: не найдено `CfUnit` |
| R3 | `GVL_Имя.переменная` (имена GVL/PVL проекта) | `переменная` — в RuSTy у `VAR_GLOBAL` нет имени; одинаковые имена в разных GVL дадут ошибку дубля | E048: не найдено `GVL_Const` |
| R4 | `x S= e;`, `x R= e;` | `IF e THEN x := TRUE; END_IF;` / `... := FALSE` | E007: разбор |
| R5 | `__ISVALIDREF(r)` | `(ADR(r) <> 0)` | E048 |
| R6 | `STRING(80)`, `WSTRING(80)` | `STRING[80]`, `WSTRING[80]` | предупреждение E014 |
| R7 | `VAR_INST`, `VAR_STAT` | `VAR` (тип тот же, время жизни — нет: только для проверки типов) | E007 |
| R8 | `VAR_GLOBAL PERSISTENT RETAIN` | `VAR_GLOBAL RETAIN` | E138/E006 |
| R9 | `UNION ... END_UNION` | `STRUCT ... END_STRUCT` (типы полей проверяются, раскладка памяти другая) | E007 |
| R10 | `TYPE ST_B EXTENDS ST_A : STRUCT` | поля `ST_A` (и её баз) одной строкой в начале `ST_B`, `EXTENDS` убирается; база не из проекта — без изменений | E006/E007 |
| R11 | `PROPERTY_GET PUBLIC Имя` (модификатор доступа свойства) | `PROPERTY_GET Имя` | E138, E001 |
| R12 | типы `CTU`, `CTD`, `CTUD` в объявлениях | `CDS_CTU`, `CDS_CTD`, `CDS_CTUD` из `rusty_shims/codesys_standard.st` (у CODESYS Standard входы `RESET`/`LOAD` и тип `WORD`, у RuSTy — `R`/`LD` и `INT`; сигнатуры CODESYS — сверить с источником) | E089, E048 |

Ожидаемые предупреждения RuSTy, которые не считаются ошибкой: E049 «Illegal access to private member» —
обращение производного ФБ к `VAR` базового (в CODESYS допустимо); E067 «Implicit downcast» для литералов.
RuSTy принимает без замен: `METHOD` внутри `FUNCTION_BLOCK`, `EXTENDS`, `IMPLEMENTS`, `ABSTRACT`, `SUPER^`,
`THIS^`, `REFERENCE TO`, `REF=`, `POINTER TO`, `ADR`, `LTIME`, прагмы `{attribute ...}`, `AT %IX0.0`,
`INT(0..100)`, `ARRAY[*]`, перечисления с базовым типом и `E_Mode.Auto`, WSTRING с кириллицей.

### Понижение для iec_checker (`--flavor iec-checker`)

iec_checker v0.4 разбирает подмножество МЭК 61131-3. Понижение проверено настоящим бинарником v0.4
на `tools/tests/fixtures/sample/`, на `src/` и `tests/` шаблона. С образом `nightly` понижение локально
не проверялось (Docker не установлен) — его проверяет необязательное задание CI `iec_checker_nightly`.

| Конструкция CODESYS | Понижение | Влияние на анализ |
|---|---|---|
| `FUNCTION_BLOCK`/`PROGRAM` с методами | тело ФБ без методов; метод → `FUNCTION <ФБ>__<Метод> : <тип или BOOL>`; в `VAR` — копии переменных ФБ (и базовых ФБ проекта), которые упоминает метод, и переменная-результат `<Метод>`; в конце `<ФБ>__<Метод> := <Метод>;` | **приближение**: состояние экземпляра не связано с методом; находки на сгенерированных строках — статус `artifact` |
| свойство ФБ | `FUNCTION <ФБ>__<Свойство>__get : <тип>` и `__set : BOOL` с `VAR_INPUT <Свойство>` | как для методов |
| свойство интерфейса | `METHOD <Свойство>__get : <тип>` / `METHOD <Свойство>__set` | — |
| `SUPER^.M(` | `<База>__M(` (база из проекта); `THIS^.x`, `SUPER^.x` → `x`; `p^` → `p` | разыменование не анализируется |
| `EXTENDS`, `IMPLEMENTS`, `ABSTRACT`, `FINAL`, `PUBLIC`, `INTERNAL` у ФБ | убираются | наследование не учитывается |
| `FUNCTION` без типа результата | `: BOOL` | — |
| прагмы `{...}` | удаляются | — |
| `/* ... */` | `(* ... *)` | — |
| GVL, PVL (`VAR_GLOBAL` вне конфигурации) | обёртка `CONFIGURATION <Имя> ... END_CONFIGURATION`; `PERSISTENT` убирается | — |
| `POINTER TO`, `REFERENCE TO` | `REF_TO`; `r REF= x;` → `r := ADR(x);` | — |
| `S=`, `R=` | `IF ... THEN ... END_IF;` | — |
| `VAR_INST`, `VAR_STAT` | `VAR` | — |
| `STRING(n)`, `INT(0..100)`, `ARRAY[*]` | `STRING[n]`, `INT`, `ARRAY[0..0]` | — |
| `UNION`; `STRUCT EXTENDS`; массив в `STRUCT`; базовый тип перечисления `) INT;` | `STRUCT`; поля базы в начале; тип элемента; `) ;` | массивы в структурах не анализируются |
| начальные значения не-литералы (`:= E_Mode.Auto`, `:= GVL_Const.X`, `:= (a := 1)`, `[3(0)]`) | удаляются | отсюда дополнительные PLCOPEN-CP3 |
| метки `CASE` `E_Mode.Auto`, `GVL_Const.STEP` | целое значение из DUT/GVL проекта | неизвестная метка остаётся — ParserError |
| строковые литералы: пробел, `$`, кавычки, не-ASCII | `$hh` (STRING; не-ASCII → `$3F`), `$hhhh` (WSTRING) | — |
| идентификаторы с ведущим `_` (`__NEW`, `__QUERYINTERFACE`); `__ISVALIDREF(x)` | `CDS__NEW`, ...; `(ADR(x) <> 0)` | — |

Не понижается — iec_checker v0.4 сообщит ParserError, конструкцию нужно переписать: пустой оператор `;`
и пустые ветви `IF`/`CASE`, `JMP` и метки переходов, метки `CASE` с неизвестными символами.

## `check/st_lint.py` — линтер

```
python tools/check/st_lint.py [пути...] [--root R] [--config FILE | --no-config] [--json] [--strict] [--list-rules]
```

Пути — каталоги раскладки от `--root` (по умолчанию `[lint] paths` = `src`, `tests`). Вывод:
`путь:строка:столбец: ПРАВИЛО [уровень] текст` и итоговая строка; `--json` — JSON
(`findings`, `summary`). Код выхода 1 — есть ошибки (с `--strict` — и предупреждения), 2 — ошибка настроек.
Замечания раскладки (LAYxxx) — всегда ошибки.

| Правило | Проверка | По умолчанию |
|---|---|---|
| STL001 | заголовок-комментарий POU (до первой секции `VAR`) упоминает «назначение», «автор», «версия», «документ» и путь `docs/...` | ошибка |
| STL002 | табуляция | ошибка |
| STL003 | строка длиннее `max_line_length` (120) символов | ошибка |
| STL004 | `TODO`/`FIXME` в комментарии без номера issue: нужно `TODO(#12)` | ошибка |
| STL005 | не-ASCII символ в идентификаторе или вне комментариев и строк | ошибка |
| STL006 | префиксы имён: `PRG_`, `FB_`, `F_`, `ST_`, `E_`, `U_`, `I_`, `GVL_` (PVL — `PVL_` или `GVL_`; псевдонимы типов не проверяются); методы и свойства — PascalCase (`^[A-Z][A-Za-z0-9]*$`, кроме `FB_init`, `FB_exit`, `FB_reinit`) | ошибка |
| STL007 | префикс переменной по типу (`x` BOOL, `i` INT, `di` DINT, `r` REAL, `s` STRING, `ws` WSTRING, `t` TIME, `a` массив, `p` указатель, `ref` ссылка, `e` `E_*`, `st` `ST_*`, `u` `U_*`, `fb` `FB_*` и TON/TOF/TP/R_TRIG/..., `itf` `I_*`; после префикса — заглавная буква или цифра); константы `VAR CONSTANT`/`VAR_GLOBAL CONSTANT` — UPPER_SNAKE_CASE; проверяются POU, методы, аксессоры, GVL, поля DUT; неизвестные типы пропускаются; исключения — `allow_names` | ошибка |
| STL008 | «магическое число»: десятичный литерал в теле (POU, метода, аксессора), кроме 0, 1, 2 (по значению, `-1` тоже); не проверяются индексы `[...]`, метки `CASE`, номер бита `x.3`, литералы с основанием (`16#FF`) и типизированные (`T#5s`, `INT#3`), объявления (начальные значения, границы массивов), `GVL_Const`, по умолчанию `tests/*` | ошибка |
| STL009 | файл не нормализован: не UTF-8, BOM, CRLF/CR, нет `\n` в конце, пустые строки в конце, хвостовые пробелы | ошибка |
| STL010 | не-ASCII в литерале `STRING '...'` — для кириллицы `WSTRING "..."` | ошибка |
| STL011 | эвристика «время счётом циклов»: `X := X + 1` (или `1 + X`), где имя `X` содержит `cycle`/`cyc`/`scan`/`tick`, или `X` сравнивается с идентификатором «времени» (`time`, `delay`, `timeout`, `_ms`, `_s`, `sec`) | предупреждение |
| STL012 | неверное подавление `lint:allow`: нет причины или неизвестное правило | ошибка |

**Формат заголовка POU** (STL001). Комментарий стоит перед строкой `PROGRAM`/`FUNCTION_BLOCK`/`FUNCTION`
или сразу после неё, до первой секции `VAR`; можно `(* *)` или строки `//`:

```
// Назначение: что делает POU и в каких режимах
// Автор:      <ИМЯ ФАМИЛИЯ>
// Версия:     0.1.0
// Документ:   docs/ARCHITECTURE.md, раздел «...»
FUNCTION_BLOCK FB_Example
```

**Подавление**: `// lint:allow STL008 причина` (или `(* lint:allow STL008, STL004 причина *)`) действует
на свою строку, а если комментарий стоит в строке один — и на следующую строку. Без причины подавление
не действует и само даёт STL012.

**Настройки** — `tools/check/st_lint.toml` (значения по умолчанию повторяют `st_lint.py`, тест
`test_toml_matches_defaults` следит за этим): `[lint]` (`paths`, `exclude`, `max_line_length`),
`[severity]` (`error`/`warning`/`off` для каждого правила), `[rules.STLxxx]` (параметры правила и
`exclude` — шаблоны fnmatch путей от корня; `*` захватывает и `/`).

## `check/run_iec_checker.py` — статанализ

```
python tools/check/run_iec_checker.py [--tests] [--binary PATH | --docker IMAGE] [--allow FILE]
                                     [--out DIR] [--json FILE] [--md FILE] [--strip-namespace NS]... [--optional]
```

1. Собирает вариант `iec-checker` (по умолчанию только `src/`, с `--tests` — и `tests/`) в
   `build/assembled/iec-checker/`.
2. Запускает iec_checker **по каждому файлу** (`-o json`): у находок, кроме ошибок разбора, iec_checker
   не заполняет поле `file`, поэтому общий запуск по каталогу не позволяет понять, где находка.
3. Пересчитывает строки на исходники по `sourcemap.json`; находки на весь файл (строка 0, например
   PLCOPEN-CP9) относятся к объявлению объекта.
4. Сверяет с `tools/check/iec_checker_allow.toml` и пишет `build/iec_checker.json` и `build/iec_checker.md`.

Бинарник: `--binary`, переменная `IEC_CHECKER`, `PATH` (`iec_checker`, `iec_checker_Linux_x86_64`,
`iec_checker_Windows_x86_64.exe`), `~/.local/plc-tools/iec-checker/`. Docker: `--docker jubnzv1/iec-checker:nightly`
(`docker run --rm -v <каталог>:/work -w /work <образ> -o json <файл>`; точка входа образа — `iec_checker`).

| Статус | Значение | Код выхода |
|---|---|---|
| `error` | необоснованная находка; ParserError, LexingError и сбой инструмента — всегда `error` | 1 |
| `allowed` | есть запись в файле обоснований | 0 |
| `artifact` | находка на строке, созданной сборкой (копии переменных ФБ в функциях-методах и т. п.) | 0 |

Файл обоснований:

```toml
[[allow]]
id = "PLCOPEN-CP3"          # правило iec_checker (шаблон fnmatch)
file = "src/POUs/F_Clamp/*" # путь исходника или имя собранного файла (шаблон fnmatch, * захватывает /)
match = "RVALUE|RMIN"       # необязательно: регулярное выражение по тексту сообщения
reason = "..."              # обязательно; подробный разбор — docs/STATIC_ANALYSIS.md
```

Неиспользованные записи выводятся предупреждением (записи для `tests/` — только при `--tests`).
`--optional`: нет бинарника или docker — предупреждение и код 0.

## `check/run_rusty.py` — проверка RuSTy

```
python tools/check/run_rusty.py [--plc PATH] [--stdlib DIR] [--no-tests] [--exclude-file FILE] [--shims DIR]
                                [--strip-namespace NS]... [--out DIR] [--log FILE] [--json FILE] [--optional]
```

Собирает вариант `rusty` (`src/` и `tests/`) в `build/assembled/rusty/` и запускает
`plc --check <файлы> -i <stdlib/*.st> -i <rusty_shims/*.st> --error-format clang` (компоновки нет — только
разбор и проверка типов). Диагностики выводятся в строках исходников; журнал — `build/rusty.log`,
сводка — `build/rusty.json`. Код выхода 1 — `plc` вернул ошибку (предупреждения не считаются).

| Что | Откуда |
|---|---|
| компилятор `plc` | `--plc`, `RUSTY_PLC`, `PATH`, `~/.local/plc-tools/rusty/plc[.exe]` |
| объявления stdlib (`*.st`) | `--stdlib`, `RUSTY_STDLIB_INCLUDE`, рядом с `plc`: `stdlib/stdlib/include`, `stdlib/include`, `include` |
| исключения | `tools/check/rusty_exclude.txt`: `<Имя>.st  # причина` на строку, причина обязательна |
| прокладки | `tools/check/rusty_shims/*.st` — только объявления для проверки |
| пространства имён | по умолчанию убираются `coUnit.` и `CfUnit.`; `--strip-namespace` задаёт свой список |

Прокладки:
- `cfunit.st` — CfUnit/coUnit: `FB_TestSuite` с методами `Assert*` и `AreAllTestsFinished`, функции `TEST`,
  `TEST_ORDERED`, `TEST_FINISHED`, `TEST_FINISHED_NAMED`, `IS_TEST_FINISHED`, `RUN`, `RUN_IN_SEQUENCE`,
  тип `T_MaxString`. Имена и сигнатуры взяты из исследования API coUnit 1.2.0.0
  (`_research/cfunit.md`, раздел 2, по исходнику библиотеки на CODESYS Forge); компилятором CODESYS они не
  проверялись. В коде namespace библиотеки v1.2 — `coUnit`, в v1.0/1.1 — `CfUnit`; убираются оба.
- `codesys_standard.st` — счётчики `CDS_CTU`, `CDS_CTD`, `CDS_CTUD` с сигнатурами CODESYS Standard (замена R12).

`--optional`: нет компилятора или stdlib — предупреждение и код 0 (необязательный шаг CI).

## `codesys/cds.py` — CODESYS из консоли

Единственная команда, которую запускает человек: импорт раскладки в проект CODESYS, сборка, тесты
coUnit в рантайме, экспорт обратно. Python 3.11+, только стандартная библиотека; нужен CODESYS
3.5.17.30 под Windows (шаги установки — [docs/MANUAL_STEPS.md](../docs/MANUAL_STEPS.md)). Каталог
`tools/codesys/` переносится в другие репозитории портфолио без правок — отличается только
`config.json`. Проверено на CODESYS V3.5 SP17 Patch 3 (32 бит, сборка ОВЕН) с CODESYS Control
Win V3 в пользовательском процессе и coUnit 1.2.0.0.

| Команда (`python tools/codesys/cds.py ...`) | Что делает | Артефакты в `build/` |
|---|---|---|
| `import [--tests] [--clean] [--force]` | раскладка → проект. Режим normal: `src/` → `<имя>.project`, задача MainTask 20 мс → `PRG_Main`. С `--tests` режим test: `src/` и `tests/` (в папке `Tests`) → `<имя>-tests.project`, TestTask 10 мс → `PRG_Tests`, библиотека coUnit | `import.log`, `import.json` |
| `build [--tests] [--no-import] [--force]` | импорт того же режима и полная сборка (`app.rebuild()`) | `build.log`, `build.json` |
| `test [--restart-runtime] [--no-runtime-start] [--clean] [--force]` | `import --tests` → сборка → вход в рантайм, загрузка, старт, опрос coUnit, результаты, стоп, выход | `test.log`, `test-results.json`, `junit.xml` |
| `export [--tests] [--to DIR] [--force]` | проект → раскладка `src/` (с `--tests` — тестовый проект и `tests/`) | `export.log`, `export.json` |
| `roundtrip` | импорт в чистый проект `build/roundtrip/` → экспорт в `build/roundtrip/export/` → побайтное сравнение с `src/` и `tests/`; затем проект открывается с диска и импортируется повторно — изменений должно быть 0 | `roundtrip/*` |
| `runtime start\|stop\|status\|restart\|init` | пользовательский процесс CODESYS Control Win V3 (ниже) | — |
| `junit [--results F] [--out F]` | `test-results.json` → JUnit XML | `junit.xml` |
| `report [--results F] [--doc F]` | раздел «Последний прогон в CODESYS» в `docs/TEST_REPORT.md` (между маркерами `cds:test-report`) | — |

Коды выхода: 0 — успех; 1 — проверка не прошла (ошибки раскладки или сборки, упавшие тесты, дифф
roundtrip, защита от потери правок); 2 — аргументы или `config.json`; 3 — окружение (нет CODESYS,
рантайма, файла); 4 — таймаут; 5 — блокировка занята.

### Как запускается CODESYS

- Строка запуска собирается одной строкой с кавычками после `=`:
  `"<CODESYS.exe>" --profile="<профиль>" --noUI --runscript="<скрипт>"` (список аргументов
  `subprocess` превращает это в `"--profile=..."`, и CODESYS не видит профиль).
- Аргументы скриптам CODESYS не передаются: `cds.py` пишет файл задания
  `build/codesys/job-<команда>.json` и передаёт его путь в переменной `CDS_JOB`. Задание — список
  шагов (`import`, `export`, `build`, `test`), они выполняются в одном процессе CODESYS: `test` — это
  import + build + test за ~20 с.
- Итог скрипт пишет в `build/codesys/result-<команда>.json` и возвращает кодом выхода процесса:
  `sys.exit(n)` и `system.exit(n)` доходят до ОС, необработанное исключение даёт 1 (проверено на
  3.5.17.30). `cds.py` опирается на файл результата; вывод CODESYS — в `build/codesys/<команда>.stdout.log`.
- Таймаут процесса — `CDS_TIMEOUT_S` (900 с): если проект открыт в IDE или CODESYS ждёт диалога,
  процесс останавливается.

| Файл | Где выполняется | Назначение |
|---|---|---|
| `cds.py` | Python 3.11+ | запускатель (синтаксис совместим с 2.7, как все `tools/codesys/*.py`); реализация — `cdshost/cli.py` |
| `cdshost/cli.py` | Python 3.11+ | командная строка, `config.json`, задания, блокировки, рантайм, JUnit, отчёт |
| `cds_common.py` | IronPython 2.7 и Python 3 | задание и диспетчер шагов, журнал, GUID типов объектов, разбор значений `read_value` |
| `import_sources.py`, `export_sources.py`, `build.py`, `run_tests.py` | IronPython 2.7 (CODESYS) | шаги `import`, `export`, `build`, `test` |
| `config.json` | — | проект, устройство, задачи и библиотеки по режимам, рантайм, тесты, блокировка |

### Импорт и экспорт: правила

- Раскладка читается и пишется только через `stlayout.py`. Ошибки раскладки — отказ импорта без
  изменений проекта. Подкаталоги категорий — папки проекта CODESYS; `tests/` — папка `Tests`
  (папка верхнего уровня `Tests` в `src/` запрещена).
- Импорт идемпотентен: текст объявления или тела заменяется, только если он отличается, поэтому
  повторный импорт без правок ничего не меняет (проверяет `roundtrip`). Объект другого вида или
  POU другого типа (`FUNCTION_BLOCK` → `PROGRAM`) пересоздаётся; объект в другой папке переносится.
  Имя, отличающееся от файла только регистром (объект, метод, свойство, папка), переименовывается
  (`rename`), иначе `export` вернул бы старое имя файла (проверено).
- Перед открытием проекта удаляется кэш предкомпиляции `<имя>_project.precompilecache`, а после
  открытия читается всё дерево объектов. Причина (воспроизведено на 3.5.17.30): с кэшем и
  непрочитанными методами ФБ замена объявления ФБ скриптом не доходила до компилятора —
  сломанное объявление собиралось с «0 ошибок», исправленное давало старую ошибку.
- Интерфейс создаётся с базовым `__System.IQueryInterface` по умолчанию, затем объявление
  заменяется текстом файла (там свой `EXTENDS` или его нет).
- Аксессоры свойства — ровно как в раскладке: `create_property` создаёт Get и Set, лишний удаляется;
  недостающий аксессор отдельно не создаётся — свойство пересоздаётся. Объявление аксессора POU без
  файла `<P>.get.decl.st` — `VAR` / `END_VAR`; при экспорте такое объявление файла не даёт.
- Удаляются объекты управляемых видов (POU, DUT, GVL, интерфейсы, их методы и свойства), которых
  нет в исходниках, и опустевшие папки — каждый случай в отчёте. Действия (`ACTION`), визуализации и
  прочие объекты не трогаются, но `export` о них предупреждает.
- Задачи — по `modes.<режим>.tasks` (лишние удаляются). Библиотеки — по `modes.<режим>.libraries`:
  ищутся в репозиториях CODESYS по пространству имён и версии, при отсутствии ставятся из
  `install_from` (`COUNIT_LIBRARY`); библиотеки других режимов удаляются из проекта.
- Persistent Variables (`*.pvl.st`) в SP17 скриптом не создаются (`create_persistentvars` нет):
  предупреждение и ручной шаг; существующий объект импорт обновляет, экспорт выгружает.
- Экспорт: DUT, GVL, PVL, интерфейсы и POU на ST приложения (не библиотеки, не дерево устройств и не
  задачи); метод `ABSTRACT` с пустым телом файла тела не даёт; POU на графических языках
  пропускаются с предупреждением. Запись — `stlayout.write_objects(clean=True)`.
- `build`: сообщения всех активных категорий (`system.get_message_objects`) с важностью, текстом,
  кодом (`C0046`), объектом и позицией; `cds.py` дописывает поле `source` — файл и строку раскладки
  (`src/POUs/F_Clamp/F_Clamp.impl.st:16`): строки объявления и тела в CODESYS совпадают со строками
  файлов. Ошибка — важность Error/FatalError (сверяется со строкой итога «Компиляция завершена --
  N ошибок, M предупреждений»).
- `test`: результаты coUnit читаются `read_value` из `coUnit.GVL_coUnit.coUnitRunner`: флаг
  `AllTestSuitesFinished`, итоги, затем наборы и тесты по индексам `[1..]`, пока имя не пустое
  (счётчики `NumberOfTests` наборов — поддиапазонного типа, `read_value` их не читает). Статус:
  `TestIsSkipped` → SKIP (тесты `DISABLED_...`), `TestIsFailed` → FAIL с `FailureMessage` и
  `FailureType`.

### Блокировки и защита от потери правок

- Глобальная блокировка рантайма `%LOCALAPPDATA%\plc-tools\codesys-runtime.lock` (`CDS_RUNTIME_LOCK`)
  на время `test` и `runtime start|stop|restart`: параллельные запуски из разных репозиториев ждут друг
  друга (`CDS_LOCK_TIMEOUT_S`, 1800 с). Блокировка репозитория `build/codesys/cds.lock` — один
  процесс CODESYS на проекты репозитория. Обе снимаются ОС, если процесс упал; владелец — в `*.owner`.
- Файл блокировки CODESYS `<проект>.~u` (пользователь, компьютер, PID): проект открыт в IDE или
  другом процессе CODESYS — отказ с понятным сообщением и кодом 3. Если процесса с этим PID на этом
  компьютере нет (сбой IDE, остановка CODESYS по таймауту), файл удаляется с предупреждением:
  CODESYS в noUI сам такой файл не снимает и не открыл бы проект (проверено).
- `test` в начале удаляет `build/test-results.json` и `build/junit.xml` прошлого прогона.
- `build/codesys/state.json` хранит отпечаток проекта и исходников после `import`/`export`:
  `import` не перезапишет проект, сохранённый в IDE после этого (сначала `export` или `--force`);
  `export` в репозиторий не перезапишет `src/`/`tests/`, изменённые после этого (`--to DIR` или `--force`).

### Рантайм

- Процесс: `"<GatewayPLC>\CODESYSControlService.exe" -d "<runtime_dir>\boot.cfg"`, запуск скрытый
  (PowerShell `Start-Process -WindowStyle Hidden`; PID — через файл-маркер, а не канал: процесс
  наследует дескрипторы, и чтение канала зависало бы), ожидание порта 11740, PID — в
  `<runtime_dir>/runtime.pid.json`. Свой процесс узнаётся по `-d <boot.cfg>` в командной строке;
  служба Windows и чужие экземпляры не останавливаются никогда.
- `runtime init` готовит `<runtime_dir>` (`~/.local/plc-tools/cds-runtime`): `boot.cfg` (CRLF,
  `[SysFile] Windows.WorkingDirectory`), `work/CODESYSControl.cfg` (копия штатного),
  `credentials.json` (пользователь `cdsci`, случайный пароль; не печатается). Учётные данные
  (и имя пользователя) читаются только из файла и в журналы не попадают.
- `test` запускает рантайм, если его нет, и перезапускает, если он работает дольше
  `CODESYS_RUNTIME_MAX_AGE_MIN` (100 мин; демо-режим — 2 ч). Вход: шлюз `Gateway-1`
  (добавляется, если его нет), адрес 127.0.0.1:11740, `login(OnlineChangeOption.Never, True)`;
  если вход не удался — создание первого пользователя (`set_credentials_for_initial_user`) и
  повторный вход (ветка не проверена: на этой машине пользователь уже был). Если не удались обе
  попытки (рантайм недоступен, неверный пароль), — одна строка с обоими сообщениями CODESYS и код 3;
  недоступный адрес устройства даёт ответ примерно через 20 с (проверено).
- Состояние рантайма (пользователи, приложения, сертификаты) из-за виртуализации UAC лежит в
  `%LOCALAPPDATA%\VirtualStore\Program Files (x86)\CODESYS 3.5.17.30\GatewayPLC\`, а не в `work/`.

### Переменные окружения

| Переменная | Что задаёт | По умолчанию (`config.json`) |
|---|---|---|
| `CODESYS_EXE` | `CODESYS.exe` | `${ProgramFiles(x86)}/CODESYS 3.5.17.30/CODESYS/Common/CODESYS.exe`, затем `${ProgramFiles}/...` |
| `CODESYS_PROFILE` | профиль CODESYS | `CODESYS V3.5 SP17 Patch 3` |
| `CDS_TIMEOUT_S` | таймаут процесса CODESYS, с | 900 |
| `CDS_PROJECT_NAME` | имя проекта | имя каталога репозитория |
| `CDS_PROJECT`, `CDS_TEST_PROJECT` | файлы проектов | `build/{name}.project`, `build/{name}-tests.project` |
| `CDS_DEVICE_TYPE`, `CDS_DEVICE_ID`, `CDS_DEVICE_VERSION` | устройство проекта | 4096, `0000 0001` (Control Win V3, 32 бит), `3.5.17.30`; x64 — `0000 0004`, RTE — `0000 0002` |
| `COUNIT_LIBRARY` | `counit.library` для установки, если её нет в репозиториях | — |
| `CODESYS_RUNTIME_DIR` | каталог рантайма | `~/.local/plc-tools/cds-runtime` |
| `CODESYS_RUNTIME_EXE` | `CODESYSControlService.exe` | `${ProgramFiles(x86)}/CODESYS 3.5.17.30/GatewayPLC/CODESYSControlService.exe` |
| `CODESYS_RUNTIME_BOOT_CFG`, `CODESYS_RUNTIME_WORK_DIR` | `boot.cfg`, рабочий каталог | `{runtime_dir}/boot.cfg`, `{runtime_dir}/work` |
| `CODESYS_RUNTIME_CFG_TEMPLATE` | штатный `CODESYSControl.cfg` для `runtime init` | `${ProgramData}/CODESYS/CODESYSControlWinV3/*/CODESYSControl.cfg` |
| `CODESYS_RUNTIME_CREDENTIALS` | файл учётных данных (`user`, `password`) | `{runtime_dir}/credentials.json` |
| `CODESYS_RUNTIME_PID_FILE`, `CODESYS_RUNTIME_LOG` | PID-файл, консольный журнал рантайма | `{runtime_dir}/runtime.pid.json`, `{runtime_dir}/runtime_console.log` |
| `CODESYS_RUNTIME_MAX_AGE_MIN` | перезапуск перед тестами, мин | 100 |
| `CODESYS_DEVICE_ADDRESS` | адрес устройства | `127.0.0.1` |
| `CODESYS_GATEWAY_HOST`, `CODESYS_GATEWAY_PORT` | шлюз (если его нужно добавить) | `localhost`, 1217 |
| `CDS_TEST_TIMEOUT_S` | ожидание `AllTestSuitesFinished`, с | 120 |
| `CDS_RUNTIME_LOCK`, `CDS_LOCK_TIMEOUT_S` | глобальная блокировка, ожидание блокировок | `${LOCALAPPDATA}/plc-tools/codesys-runtime.lock`, 1800 |
| `CDS_REPO_ROOT`, `CDS_CONFIG` | корень репозитория, `config.json` (тесты, нестандартное размещение) | каталог над `tools/`, `tools/codesys/config.json` |
| `CDS_JOB` | (внутренняя) путь к файлу задания для скрипта CODESYS | ставит `cds.py` |

Переменная ищется в окружении процесса, затем (Windows) в `HKCU\Environment` — значения `setx`
видны без перезапуска консоли. В `config.json` значение `{"env": ИМЯ, "default": ...}` читается так
же; подстановки: `~`, `${ИМЯ}`, `{repo}`, `{repo_name}`, `{name}`, `{runtime_dir}`.

## Тесты инструментов

`python -m pytest` (каталог `tools/tests/`):

| Файл | Что проверяет |
|---|---|
| `test_stlayout.py` | лексер без потерь (hypothesis), комментарии и прагмы, заголовки, секции VAR, DUT, метки CASE, `scan` и все коды LAY, цикл `scan → write` побайтно, запись поверх файлов с другим регистром имён |
| `test_py27_compat.py` | `tools/codesys/*.py` без конструкций и модулей только Python 3 |
| `test_cds.py` | `cds.py` без CODESYS: строка запуска, `config.json` и переменные окружения, задания, JUnit, отчёт, блокировки, защита от потери правок, сравнение roundtrip, привязка сообщений компилятора к файлам, процессы рантайма, `runtime init`, удаление старых результатов `test`; чистые функции `cds_common.py` (значения `read_value`, интервалы задач, GUID, файл блокировки `.~u`, кэш предкомпиляции, коды выхода шагов) |
| `test_assemble.py` | эталоны всех вариантов (`tools/tests/golden/<вариант>/`), порядок `all.st`, карта строк, CLI, каждое правило замен rusty и понижения iec-checker |
| `test_st_lint.py` | каждое правило — срабатывание и отсутствие, подавления, настройки, CLI |
| `test_run_rusty.py`, `test_run_iec_checker.py` | поиск инструментов, команды, разбор вывода, обоснования, отчёты; интеграционные — с настоящими `plc` и iec_checker, в том числе `src/` и `tests/` шаблона: каждое обоснование `iec_checker_allow.toml` должно использоваться |

Интеграционные тесты помечены `@pytest.mark.integration` и пропускаются, если инструмента нет
(`python -m pytest -m integration` — только они). Эталоны обновляются так:
`UPDATE_GOLDEN=1 python -m pytest tools/tests/test_assemble.py` — после этого просмотрите дифф.
Реалистичный пример раскладки для тестов — `tools/tests/fixtures/sample/` (вымышленный двигатель
с контактором: наследование ФБ, интерфейсы со свойствами, UNION, STRUCT EXTENDS, PVL, тесты CfUnit).
