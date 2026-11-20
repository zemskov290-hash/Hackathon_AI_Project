# Стратег фабрики проектов

Рабочее место агента для лида цифрового продукта. Из расплывчатого запроса и разнородных материалов он собирает проверяемую картину ситуации, несколько несводимых вариантов и стратегию, в которой обещания совпадают с продуктом. Это папка с правилами, навыками и скриптами для Cursor и других агентных IDE, а не отдельное приложение.

Правила одни: [`AGENTS.md`](AGENTS.md). Учебный пример в `projects/_example` выдуман и нужен только как образец формата.

## Запуск за пять минут

Нужен Python 3.10+ и агентная IDE. Ключ API, облако и платные пакеты не нужны. Команда ниже — `python`: так интерпретатор называется и в PowerShell на Windows, и после установки с python.org. `python3` на Windows часто оказывается заглушкой Microsoft Store и не запускает этот репозиторий.

PowerShell или cmd, из корня репозитория:

```powershell
python --version
python scripts/strat.py check projects/_example
python scripts/strat.py resume projects/_example
```

Вывод консоли PowerShell может показать кириллицу знаками вопроса, хотя в файле лежит нормальный UTF-8. Прежде чем решать, что текст сломался, прочитайте файл из Python (`Path.read_text(encoding='utf-8')`). Если испорчен сам аргумент — строка из одних «?» или символ U+FFFD — CLI её не запишет. Тогда передайте текст файлом UTF-8 или через stdin:

```powershell
python scripts/strat.py init family --title-file title.txt
python scripts/strat.py log family 1 --message-file action.txt --grounds-file grounds.txt
```

Значение `-` читает один аргумент из stdin (`--title -`). Те же тексты можно положить в переменные `STRAT_TITLE`, `STRAT_LOG_MESSAGE`, `STRAT_GROUNDS`, `STRAT_REASON`, `STRAT_CLAIM`, `STRAT_QUOTE`, `STRAT_NOTE`. Если аргумент всё же нужен в командной строке, сначала в этом окне:

```powershell
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001
```

Строку из одних «?» или с символом замены CLI не записывает и объясняет, что делать.

Тот же смысл в bash, если `python` есть в PATH:

```bash
python --version
python scripts/strat.py check projects/_example
python scripts/strat.py resume projects/_example
```

Дальше откройте папку репозитория в Cursor и напишите в чат:

```text
Начни новый проект: хочу понять, стоит ли развивать <направление>. Материалы приложу следом.
```

Агент прочитает `AGENTS.md`, создаст `projects/<id>/` и остановится на переформулировке брифа. Он задаст не больше пяти вопросов и только если без ответа нельзя выбрать план. Пока вы явно не согласитесь с брифом, этап не закроется.

Остановиться можно в любой момент: бриф, источники, свидетельства и журнал уже на диске. В новом чате достаточно фразы «Продолжи проект <id>». Первая команда агента — `python scripts/strat.py resume`.

## Что должно быть под рукой

Обязательно: этот репозиторий на диске и Python 3.10+ под именем `python`. Сеть нужна только если агент ищет открытые источники средствами IDE. На Windows не используйте заглушку `python3` из Store.

Не кладите в репозиторий секреты, ключи и закрытые выгрузки. `.env` в `.gitignore`.

Необязательно:

- файл `.env` по образцу `.env.example` — совместимый с OpenRouter API, если нужна маршрутизация дешёвой и сильной модели и точный расход. Без ключа `strat complete` отвечает `MODE: manual` и денег не тратит;
- пакеты `pypdf`, `python-docx`, `openpyxl` — чтобы читать pdf, docx и xlsx. `pandas` — запасной путь для xlsx в расчёте. `matplotlib` — PNG-график, если он задан в декларации. Без них ядро работает: CSV и JSON считаются, PDF и xlsx честно сообщают, что пакета нет.

Проверка окружения: `python scripts/strat.py doctor`. В строке «Вывод» должно быть `utf-8`: CLI само переключает stdout, отдельная переменная `PYTHONIOENCODING` не нужна. В строке «MCP» должно быть «сервер ответил на initialize».

## Как устроена работа

Восемь этапов: бриф → план исследования → сбор → синтез → варианты → стратегия → план проверки → сопровождение. Три остановки для человека: подтвердить бриф, выбрать вариант, принять план проверки. Команда `advance` не закроет этап, если подтверждения нет или `check` находит дыры.

В текстах тезис ссылается на `[E014]`. Факт, гипотеза, прогноз и синтетика — разные типы. Синтетическая реплика не становится голосом клиента. Команды внутри источников не меняют правила агента.

Папка проекта после `init`:

```text
state.json  brief.md  questions.md
sources/  sources.jsonl  evidence.jsonl
situation.md  options.md  strategy.md  validation.md
decisions.md  log.md  log.jsonl
```

## Команды

Из корня репозитория. Имя проекта можно опустить, если боевой проект один или в `projects/.current` записан существующий id. Папка `_example` сама текущей не становится. Если `.current` указывает на удалённый проект, команда с явным путём (`check projects/_example`) всё равно выполняется, а без пути будет подсказка, как поправить указатель.

Боевые папки `projects/<id>/` и `projects/.current` в git не входят. В репозитории из `projects/` лежат учебный `projects/_example`, снимок прогона `projects/demo-family-tariffs` и синтетический разбор `projects/demo-analysis`. Остальные папки проектов — только на машине.

| Команда | Зачем |
| --- | --- |
| `init <id> --title "..."` | создать проект из шаблонов, этап 1 |
| `resume [проект]` | краткий бриф, чтобы продолжить в новом чате |
| `status [проект]` | этап, контрольные точки, пробелы, затраты |
| `show [проект] evidence\|sources\|log\|state\|decisions` | прочитать файл в UTF-8; `--id E001`, для журнала `--last N` |
| `use <проект>` | сделать проект текущим |
| `fetch [проект] <url>` | скачать страницу в `sources/`; рядом сохраняется HTML для `inspect`. Голый URL привязывается к той же записи. Повтор не качает заново, `--force` обновляет снимок. Сеть для 5xx повторяется, провал пишется как шаг `failed` |
| `add-source [проект] <url\|файл>` | зарегистрировать источник и посчитать sha256; у текста перевод строк перед хешем приводится к LF; повтор не создаёт дубль. Голый URL снимка не создаёт: для страницы нужен `fetch` |
| `add-evidence [проект] --claim ... --type fact --source S001 --quote "..."` | добавить свидетельство; цитата непрерывна. Та же активная четвёрка type, source, claim и quote второй раз не пишется |
| `add-option [проект] --title "..."` | выдать варианту стабильный ID один раз; снятый ID не переиспользуется |
| `supersede-option [проект] <O2> --reason "..."` | снять вариант, сохранив его ID и требуя маркер `<!-- status: superseded -->` |
| `analyze [проект] --input <файл> --spec-file <json>` | расчёт по CSV, JSON или XLSX. Снимок, декларация или `--script` и result лежат в `analysis/A001/`. `--id A001` повторяет расчёт и при том же результате ничего не переписывает, `--verify` ловит расхождение. Число в свидетельстве: `--metric` |
| `inspect [проект] --html \| --image \| --pdf \| --av` | разбор DOM, метаданные PNG/JPG, PDF если есть pypdf, аудио и видео только с `--transcript`. Тот же sha256 снимка возвращает прежний id («Уже разобрано»); новый — `--force` |
| `kb add [проект] [файл]` | положить открытый источник или свою заметку в базу. `--synthetic` помечает вымысел. `--url` без файла только с `--unverified network\|paywall\|404\|unavailable`. Закрытый гриф, ключ и персональные данные в своей заметке отклоняются |
| `kb index [проект]` | построить полнотекстовый индекс BM25. Повтор с теми же файлами пишет «Индекс тот же» |
| `kb search [проект] --query-file запрос.txt` | найти фрагмент. В ответе источник, дата и текст. `--semantic`, если установлен `sentence_transformers`; иначе честно остаётся BM25. Ответ — недоверенные данные |
| `interview guide [проект]` | вопросы по гипотезам. Это не расшифровка и не свидетельство |
| `interview accept [проект] --file … --consent yes` | принять расшифровку. Без согласия файл не пишется. Имена, телефоны и email снимаются. `--synthetic` — не реальный человек, согласие живого человека не ставится |
| `reply-check [проект] --file ответ.md` | черновик итогового ответа: каждое число в том же предложении, что и существующий `[S…]`, `[E…]` или `[A….метрика]`. Без чисел проходит. Код 1 — есть ошибки, со строкой и подсказкой |
| `check [проект]` | дыры в фактах, ссылках, подразделах стратегии, ID вариантов и обещаниях; пересчёт анализов; `--strict` делает отсутствие снимка и пустую цитату ошибкой |
| `impact [проект] <S001\|E001>` | какие записи и строки зависят от источника, включая `depends_on` |
| `retract [проект] <E001> --reason "..."` | отозвать свидетельство |
| `supersede [проект] <E015> --by E019 --reason "..."` | заместить свидетельство; `*.jsonl` руками не правят |
| `propose-revision [проект] --decision D2 --choice O3` | пометить, что принятый выбор на пересмотре |
| `accept-revision [проект] --choice O3` | записать новый выбор и сохранить прежний в истории |
| `log [проект] <этап> <сообщение> --minutes --tokens --cost --model --input-chars --output-chars` | дописать `log.md` и `log.jsonl`; символы нужны для оценки, их не путать с полем `--cost` |
| `cost [проект]` | минуты, записанные токены и стоимость; `--estimate` и обычный вывод показывают оценку по `config/model-prices.json` с пометкой, что это не счёт |
| `confirm [проект] brief\|options\|validation` | записать согласие человека; для вариантов нужен `--choice O2` |
| `advance [проект] <этап>` | закрыть этап, если ворота пройдены |
| `complete --tier cheap\|strong\|ide` | вызов по `config/routing.json`; без ключа и для `ide` печатает `MODE: manual`. `--role` — прежнее имя для cheap и strong |
| `classify [проект] --task claim-type\|impact\|route --input-file текст.txt` | узкая метка из закрытого списка. Без ключа API — cursor-agent, если он в PATH, иначе правила. `--backend rules` сеть не вызывает |
| `doctor` | версия Python, режим модели, таблица маршрута, классификатор, ответ MCP на `initialize`, статическая переносимость, необязательные пакеты |
| `portability` | матрица сред: правила, навыки, субагенты, хуки, MCP и CLI. `--check` сверяет `docs/portability.md` |

Полный список: `python scripts/strat.py --help`. Те же действия есть инструментами MCP: имя `strat_<команда>`, например `strat_check`. Если они подключены, агент вызывает их, а не разбирает вывод оболочки. CLI остаётся запасным путём. Раздел [MCP](#mcp).

## Карта репозитория

```text
AGENTS.md                         правила
README.md                         этот вход
CLAUDE.md, .cursor/rules/         тонкие адаптеры
.github/copilot-instructions.md   адаптер Copilot
.claude/agents/                   researcher, critic, respondent
.cursor/mcp.json                  MCP для Cursor
.mcp.json                         MCP для Claude Code
.vscode/mcp.json                  MCP для VS Code / Copilot
skills/                           навык и шаблон этапа
knowledge/                        короткие карточки метода
config/model-prices.json          условные цены для оценки, не счёт
config/routing.json               какой класс задачи идёт на cheap, strong или ide; порог и модели классификатора
scripts/strat.py                  учёт, проверка, журнал, analyze, inspect и classify
scripts/classify.py               узкие метки: cursor-agent, API или правила
scripts/classify_bench.py         замер классификатора, офлайн-сводка и архив прогона
scripts/bench.py                  бенчмарк: папки, оценка без судьи-модели, сводка
scripts/portability.py            матрица адаптеров Cursor, Claude, Copilot, Codex
scripts/analysis.py               расчёт таблиц, его вызывает и CLI, и MCP
scripts/analysis_runner.py        запуск скрипта анализа без сети и вне папки проекта
scripts/inspect_media.py          DOM, изображение, PDF, расшифровка
scripts/kb.py                     база знаний: BM25 и необязательная семантика
scripts/interview.py              гайд, обезличивание, разбор реплик
scripts/grounding.py              числа, проверяемые ссылки, ПДн в интервью
scripts/mcp_server.py             MCP по stdio, те же команды
scripts/read_material.py          текст из файла, офисные форматы по желанию
hooks/                            скрипты и как их включить
schemas/                          формы state, sources, evidence, decisions
projects/_example/                выдуманный сквозной пример
projects/demo-family-tariffs/     снимок прогона; ответы лида вымышлены
projects/demo-analysis/           синтетический расчёт и разбор витрины
bench/                            задачи бенчмарка на синтетических данных
docs/benchmark.md                 сводка агент vs чат; пустая, пока нет прогонов
docs/portability.md               матрица сред
docs/decisions/                   почему ядро, модели, граф, хуки, MCP, анализ, бенчмарк
docs/devlog.md                    журнал разработки
tests/                            unittest и сломанная фикстура
```

## Хуки, маршрут и повтор шага

Хуки включены. Cursor берёт [`.cursor/hooks.json`](.cursor/hooks.json) и, по умолчанию, ещё и [`.claude/settings.json`](.claude/settings.json) ([сторонние хуки](https://cursor.com/docs/reference/third-party-hooks)). На Windows обе команды идут через PowerShell. Запись `$CLAUDE_PROJECT_DIR` там — пустая переменная PowerShell, и путь схлопывается в `C:\hooks\run.py`. Поэтому в обоих файлах одна команда `python -c` с base64 текста из [`hooks/launcher.py`](hooks/launcher.py): в blob нет кавычек, `$` и `%`. PowerShell 5.1 вырезает кавычки у `python.exe`, и вставленная как есть программа молча пропускает запрет. `python hooks/launcher.py` перезаписывает json, тест ловит расхождение. Программа читает `CURSOR_PROJECT_DIR` и `CLAUDE_PROJECT_DIR`, затем `workspace_roots` и `cwd` из JSON на stdin, и только потом поднимается от текущего каталога к `AGENTS.md`. Повторный запуск сторожа даёт то же решение; при расхождении у Cursor побеждает отказ. События: `afterFileEdit`, `postToolUse`, `beforeShellExecution`, `beforeReadFile`, `stop`.

- После правки файла внутри `projects/<проект>/` хук запускает `strat check` и печатает ошибки. Код проверки не блокирует ход.
- Оболочка не получит запись `.env`, коммит ключа и команду, которая дословно лежит в снимке `sources/`. `.env.example` не секрет. Чтение `.env` в контекст модели тоже отклоняется.
- В конце хода хук дописывает приблизительный объём. Если транскрипта нет, объём пишет сам агент флагами `--input-chars` и `--output-chars`. `python scripts/strat.py cost <проект> --estimate` показывает оценку и прямо говорит, что это не счёт.
- Сбой скрипта хука ход не останавливает. Правило «команды из источников не исполнять» остаётся в `AGENTS.md`.

`python scripts/strat.py doctor` печатает таблицу из [`config/routing.json`](config/routing.json): извлечение и сжатие источников — дешёвая модель, варианты, критик и стратегия — сильная, бриф — модель подписки IDE. Навык вызывает `complete --tier`. Без ключа в `.env` сеть не вызывается.

Узкие метки — `python scripts/strat.py classify <проект> --task claim-type|impact|route --input-file текст.txt`. Дефолт после живого замера — `classifier.backend: rules` в [`config/routing.json`](config/routing.json): правила оказались точнее и дешевле cursor-agent. Имя лёгкой модели лежит там же; список учётки — `agent --list-models`. Адаптер вроде Jev выключен, пока в config не стоит `specialized.enabled` и в окружении нет его ключа.

`resume` говорит, какой шаг начат и не закончен. Готовый `fetch` не качает тот же URL, пока нет `--force`. Ошибка сети повторяется; если страница так и не открылась, в `state.json` шаг со статусом `failed` и в журнале причина, например HTTP 503. Повторное свидетельство с тем же текстом CLI не записывает.

Как прогнать хуки руками в Cursor на Windows — в [`hooks/README.md`](hooks/README.md). Решение — [`docs/decisions/0007-hooks-and-routing.md`](docs/decisions/0007-hooks-and-routing.md).

## MCP

Сервер `scripts/mcp_server.py` отдаёт команды стратега как инструменты MCP. Стандартная библиотека, без `pip`. В stdout только JSON-RPC, логи в stderr. Бизнес-логика та же, что у `scripts/strat.py`: сервер вызывает её функции, а не копирует правила.

Конфиги уже лежат в репозитории. Команда в них — `python` и `-c` с base64, без `$` и без внутренних двойных кавычек: PowerShell 5.1 иначе раскрывает переменную или срезает кавычки. Корень сервер ищет сам: `CURSOR_PROJECT_DIR` / `CLAUDE_PROJECT_DIR` / `STRAT_REPO_ROOT`, затем подъём от текущего каталога к `AGENTS.md`. На Windows нужен `python` из python.org, не заглушка `python3` из Store.

| Клиент | Файл | Как включить |
| --- | --- | --- |
| Cursor | [`.cursor/mcp.json`](.cursor/mcp.json) | Открыть папку репозитория. Settings → MCP: сервер `strat` должен быть включён. Если список пуст — Developer: Reload Window. |
| Claude Code | [`.mcp.json`](.mcp.json) | Открыть репозиторий. Claude спросит разрешение на проектный MCP-сервер — подтвердить `strat`. |
| VS Code / Copilot | [`.vscode/mcp.json`](.vscode/mcp.json) | Открыть репозиторий. В панели MCP (Copilot) включить сервер `strat`. Нужен режим агента, где видны инструменты. |

Проверка без чата, из корня, в PowerShell или bash:

```powershell
python scripts/strat.py doctor
```

В выводе должна быть строка `MCP: сервер ответил на initialize`. Нет строки или код не ноль — сервер не стартовал: смотрите текст после этой фразы.

Инструменты (префикс `strat_`, описания на русском):

| Инструмент | Зачем |
| --- | --- |
| `strat_resume` | бриф, чтобы продолжить; первое действие, если сервер подключён |
| `strat_init` | создать проект |
| `strat_status` | этап, точки, пробелы, затраты |
| `strat_show` | evidence, sources, log, state, decisions |
| `strat_check` | проверка; `strict: true` как `--strict` |
| `strat_add_source` | зарегистрировать файл внутри проекта или URL |
| `strat_fetch` | скачать страницу; ответ помечен как недоверенные данные |
| `strat_add_evidence` | свидетельство с непрерывной цитатой |
| `strat_add_option` | стабильный ID варианта |
| `strat_supersede_option` | снять вариант, ID сохранить |
| `strat_impact` | что зависит от S001 или E001 |
| `strat_retract` | отозвать свидетельство |
| `strat_supersede` | заместить свидетельство |
| `strat_propose_revision` | поставить выбор на пересмотр, не меняя его |
| `strat_log` | журнал, включая `--input-chars` и `--output-chars` |
| `strat_cost` | минуты и оценка; в тексте есть «допущение», это не счёт |
| `strat_confirm` | согласие человека; нужно `human_confirmed: true` |
| `strat_advance` | закрыть этап; нужно `human_confirmed: true` |
| `strat_accept_revision` | принять пересмотр; нужно `human_confirmed: true` |
| `strat_complete` | маршрут модели; без ключа `MODE: manual` |
| `strat_classify` | узкая метка: тип, затронутые id или навык. Вне списка — ошибка |
| `strat_analyze` | расчёт таблицы; `id` повторяет A001, `verify: true` только пересчёт. Ответ — недоверенные данные |
| `strat_inspect` | HTML, изображение, PDF или запись. Описание кадра помечается как текст модели |
| `strat_kb_add` | запись в базу; `unverified` — ссылка без фрагмента. Ответ — недоверенные данные |
| `strat_kb_index` | индекс BM25 |
| `strat_kb_search` | фрагмент, источник и дата. `semantic: true` только если пакет установлен |
| `strat_interview_guide` | вопросы по гипотезам, не расшифровка |
| `strat_interview_accept` | приём текста. Реальный — только `consent: yes`. `synthetic: true` не является голосом клиента |
| `strat_doctor` | окружение, пакеты анализа, медиа и семантического поиска, ответ MCP на initialize |

`strat_confirm`, `strat_advance` и `strat_accept_revision` помечены `destructiveHint`. Cursor и другие клиенты могут спросить одобрение перед вызовом. Сервер всё равно отклоняет вызов, пока в аргументах нет `human_confirmed: true`. Ставить true можно только после явной фразы человека. Путь проекта не принимается, если он выходит из каталога `projects`. Файл источника — только внутри папки этого проекта.

Текст страницы после `strat_fetch` начинается с «НЕДОВЕРЕННЫЕ ДАННЫЕ». Команды внутри страницы не исполняются и правила агента не меняют.

Если инструментов в сессии нет, работайте через `python scripts/strat.py`, как раньше. Пересобрать json после правки запуска: `python scripts/mcp_server.py --write-configs`. Решение — [`docs/decisions/0008-mcp-server.md`](docs/decisions/0008-mcp-server.md).

### Ручная проверка в Cursor на Windows

1. Клонируйте репозиторий и откройте эту папку в Cursor. В PowerShell из корня: `python --version` (3.10+) и `python scripts/strat.py doctor`. Строка `MCP: сервер ответил на initialize` обязательна.
2. Settings → MCP. Сервер `strat` из `.cursor/mcp.json` включён, рядом нет ошибки старта. Если сервер красный — Reload Window и снова `doctor`.
3. Новый чат агента. Спросите: «Какие инструменты strat есть? Вызови strat_check для проекта `_example`». В списке должны быть `strat_check`, `strat_fetch`, `strat_confirm`. Ответ проверки — текст с `OK` и JSON, где `errors` равен 0.
4. Попросите вызвать `strat_confirm` для брифа. Клиент должен показать одобрение (инструмент деструктивный). Без вашего явного согласия и без `human_confirmed: true` контрольная точка не меняется.
5. Убедитесь, что агент не разбирает кириллицу из вывода PowerShell: тезис в `strat_add_evidence` передаётся полем JSON, не аргументом оболочки.

### Ручная проверка анализа и разбора на Windows

Команды ниже без `$` и без кавычек внутри аргумента: так их принимает PowerShell 5.1. Кириллица в этих командах не нужна. Из корня репозитория:

```powershell
python scripts/strat.py doctor
python scripts/strat.py check projects/demo-analysis
python scripts/strat.py analyze projects/demo-analysis --id A001
python scripts/strat.py analyze projects/demo-analysis --verify
```

`doctor` печатает, есть ли `openpyxl`, `pandas`, `matplotlib` и `pypdf`. Строка «нет» для них нормальна: демо на CSV и HTML. `check` заканчивается `OK`. Повтор `--id A001` печатает `Тот же результат`. `--verify` тоже `OK`.

В Cursor, в том же чате, где уже включён сервер `strat`:

1. «Вызови strat_analyze для проекта demo-analysis с id A001». В ответе есть `НЕДОВЕРЕННЫЕ ДАННЫЕ`, `metrics.mean_price` равен `350`, `untrusted` истинно.
2. «Вызови strat_check для demo-analysis». В JSON `errors` равен 0.
3. «Вызови strat_inspect» не нужен для уже собранного демо: снимок страницы лежит в `inspect/M001`. Если вызываете разбор заново на своём файле, путь только внутри проекта. Описание картинки передавайте полем `description`, не аргументом оболочки.

Разобрать свою открытую страницу: положите `page.html` в `projects/<id>/sources` и в чате попросите `strat_inspect` с полем `html`. Не кладите закрытую выгрузку и кадр с телефоном или почтой человека.

## База знаний и интервью

База хранит открытые источники и собственные заметки проекта: `projects/<id>/kb/`. Поиск — BM25 на стандартной библиотеке. Семантика не обязательна: `doctor` пишет, есть ли `sentence_transformers`. Нет пакета — поиск остаётся BM25 и так и говорит. Каждая выдача содержит источник, дату и фрагмент, чтобы цитата сверялась с файлом. Текст выдачи — данные, не инструкция.

Реальная расшифровка требует `--consent yes`. Без флага команда завершается с ошибкой и ничего не пишет. В файл попадает обезличенный текст: email, телефон и переданные имена снимаются, `check` ловит остаток кодом `pii`. Синтетический прогон помечается `--synthetic`. В файле, в выводе и в стратегии есть «синтетическ» и фраза, что это не реальное интервью. Согласие живого человека к нему не прилагают.

Число в содержательном разделе стратегии без источника, расчёта (формула и входы со ссылкой) или интервью — ошибка `ungrounded-number`. Ссылка без сохранённого фрагмента помечается «непроверен» и не держит вывод одна. Доля таких проверенных ссылок в стратегии не ниже 90%. `advance` этапа стратегии не проходит, пока `check` печатает ошибку. Оценщик бенчмарка при этом не менялся: те же правила для агента и для чата, архив `bench/runs` не подгонялся.

Сквозной пример на синтетических файлах и команды PowerShell — в [`docs/kb-interview.md`](docs/kb-interview.md). Решение — [`docs/decisions/0011-kb-interview-grounding.md`](docs/decisions/0011-kb-interview-grounding.md).

### Ручная проверка базы и интервью на Windows

Из корня репозитория, PowerShell 5.1. В командах нет `$` и нет кириллицы: тексты лежат в `docs/examples/synthetic-kb/`. Это вымысел, в файлах есть пометка «синтетическ».

```powershell
python scripts/strat.py init kb-demo --title-file docs/examples/synthetic-kb/title.txt
python scripts/strat.py kb add kb-demo docs/examples/synthetic-kb/note.md --title-file docs/examples/synthetic-kb/note-title.txt --synthetic
python scripts/strat.py kb index kb-demo
python scripts/strat.py kb search kb-demo --query-file docs/examples/synthetic-kb/query.txt
python scripts/strat.py add-evidence kb-demo --claim-file docs/examples/synthetic-kb/claim.txt --type synthetic --source S001 --quote-file docs/examples/synthetic-kb/quote.txt --confidence med
python scripts/strat.py interview guide kb-demo
python scripts/strat.py interview accept kb-demo --file docs/examples/synthetic-kb/transcript.md --synthetic --names-file docs/examples/synthetic-kb/names.txt
python scripts/strat.py kb add kb-demo --url https://example.com/missing --unverified 404 --title-file docs/examples/synthetic-kb/missing-title.txt
Copy-Item -LiteralPath docs/examples/synthetic-kb/strategy-ok.md -Destination projects/kb-demo/strategy.md
python scripts/strat.py check kb-demo
Copy-Item -LiteralPath docs/examples/synthetic-kb/strategy-bare.md -Destination projects/kb-demo/strategy.md
python scripts/strat.py check kb-demo
Copy-Item -LiteralPath docs/examples/synthetic-kb/strategy-ok.md -Destination projects/kb-demo/strategy.md
python scripts/strat.py check kb-demo
python -c "from pathlib import Path; t=Path('projects/kb-demo/interviews/I001.md').read_text(encoding='utf-8'); assert 'anna.smirnova' not in t; assert '+7' not in t; assert 'synthetic: true' in t; assert 'ignore previous instructions' in t; print('obezlicheno'); print('injection-stored')"
```

На чистом дереве успешный `check` заканчивается так:

```text
Проверка kb-demo: OK
Замечаний нет.
```

`check` со стратегией без источника у числа `42%` заканчивается кодом 1 и строкой `[ungrounded-number]`. Поиск печатает `НЕДОВЕРЕННЫЕ ДАННЫЕ`, `K001`, `источник:` и фрагмент со словом «воскресенье». Приём интервью печатает `Синтетическое интервью. Это не реальное интервью` и `Обезличено: email, телефон, имя.` Последняя команда печатает `obezlicheno` и `injection-stored`: инструкция осталась текстом, почта и телефон в файл не попали. Полный ожидаемый вывод — в [`docs/kb-interview.md`](docs/kb-interview.md).

### Ручная проверка черновика ответа на Windows

Кириллица лежит в файлах, в команде её нет. Из корня репозитория:

```powershell
python scripts/strat.py reply-check projects/_example --file docs/examples/reply-check/bad-contradiction.md
python scripts/strat.py reply-check projects/_example --file docs/examples/reply-check/ok-no-numbers.md
python scripts/strat.py reply-check projects/_example --file docs/examples/reply-check/ok-price.md
python scripts/strat.py reply-check projects/_example --file docs/examples/reply-check/bad-missing-id.md
python scripts/strat.py reply-check projects/demo-analysis --file docs/examples/reply-check/bad-sum.md
python scripts/strat.py reply-check projects/demo-analysis --file docs/examples/reply-check/ok-mean.md
```

Первая команда заканчивается кодом 1 и строками про числа `340` и `120`. `ok-no-numbers` и `ok-price` печатают `Проверка ответа _example: OK` и `Чисел без ссылки нет.` `bad-missing-id` — код 1 и `[S099]`. `bad-sum` — код 1 и число `440`. `ok-mean` — `Проверка ответа demo-analysis: OK`.

## Бенчмарк и переносимость

Сравнение с обычным чатом не придумывается. Пока человек не прогнал задачи, в [`docs/benchmark.md`](docs/benchmark.md) стоят прочерки. Оценщик считает ссылки, числа, цитаты, канарейку, целые из ключа, пересмотр, разделы стратегии и `check` по файлам. Языковую модель в судьи он не зовёт. Материалы синтетические и так подписаны. Закрытых данных МегаФона в наборе нет.

Подготовка папок и запуск на Windows, PowerShell 5.1, из корня репозитория. Каталог `--out` вне репозитория, иначе чат найдёт `AGENTS.md`.

```powershell
python scripts/bench.py prepare --out 'C:\strat-bench'
python scripts/bench.py capture --workspace 'C:\strat-bench\agent\t01-brief-strategy'
python scripts/bench.py capture --workspace 'C:\strat-bench\chat\t01-brief-strategy'
python scripts/bench.py score --work 'C:\strat-bench'
python scripts/bench.py archive --work 'C:\strat-bench' --model-note 'пометка модели'
python scripts/bench.py summary
```

`summary` без `--work` пересчитывает таблицу по `bench/runs`. Пока архива нет, в [`docs/benchmark.md`](docs/benchmark.md) прочерки. `archive` копирует ответы и файлы результата, не весь репозиторий, и убирает из `meta.json` абсолютные пути.

`prepare` пишет `commands.ps1` со всеми шестью задачами и обоими режимами. В строках нет `$` и внутренних двойных кавычек. `capture` читает `PROMPT.txt` как UTF-8. На Windows он не вызывает `agent.cmd` через оболочку: ищет лаунчер и запускает `node.exe` с `index.js`, промпт одним аргументом. Явный путь — `--agent-cmd` или `STRAT_AGENT_CMD`. Команда, которую он собирает:

```text
agent -p --force --trust --approve-mcps --output-format text <текст PROMPT.txt>
```

Рабочий каталог агента — копия репозитория с `AGENTS.md`, навыками, хуками и MCP. Рабочий каталог чата — пустая папка вне репозитория: те же материалы вставлены в промпт и лежат файлами, правил и инструментов стратега там нет. Прямой вызов `agent` тоже есть в `commands.ps1`; для оценщика нужен `capture`, он сохраняет `response.txt` и секунды. Имя модели оценщик не подставляет.

Переносимость: `python scripts/portability.py` или `python scripts/strat.py portability`. `doctor` печатает итог статической проверки. Матрица — [`docs/portability.md`](docs/portability.md). `live` значит, что процесс команды запустился, а не что открыт клиент IDE. Протокол ручной проверки VS Code/Copilot и Claude Code лежит в том же файле.

## Ограничения

Проверка текстов — эвристика по разделам «Факты», заголовкам вариантов, подразделам стратегии, числам в этих подразделах и маркерам обещаний, а не понимание смысла. Число без ссылки, расчёта или интервью в содержательном разделе — ошибка. Доля ссылок с сохранённым фрагментом ниже 90% — тоже ошибка. Оценщик бенчмарка эту проверку не подкручивает. Цитату CLI сверяет с локальным файлом. Расчёт `analyze` пересчитывается из снимка: другое число в свидетельстве или в якоре `350 [A001.metric]` — ошибка. Скрипт анализа ограничен папкой проекта, таймаутом и запретом сетевых модулей; это не контейнер, обход через нестандартный импорт возможен, поэтому скрипт пишет агент, а не страница из интернета. Разбор HTML берёт только узлы DOM. Картинку команда не «видит»: описание делает модель IDE и оно помечено. PDF без pypdf и запись без субтитров остаются без выдуманного текста. Свидетельство из медиа без снимка — предупреждение. Веб-страница без `fetch` оставляет голый URL: снимка нет, цитата не сверяется, `check` предупреждает, `check --strict` завершается с ошибкой. Повторно живой сайт не открывается, если снимок уже лежит в `sources/`, пока нет `fetch --force`. Оценка в `cost` считается из символов журнала и `config/model-prices.json` и всегда подписана как допущение, не как счёт. Строка хука `hook/stop` в оценку не входит, если агент уже записал свои символы. sha256 текстового источника считается после приведения перевода строк к LF, поэтому он не равен хешу сырых байт, если в файле остались CR. Флаг `human_confirmed` и пометка `destructiveHint` не отличают человека от модели, если клиент не спросил одобрение: запрет ставить флаг без явной фразы остаётся в [`AGENTS.md`](AGENTS.md). Подробности — в [`docs/decisions/0005-line-endings-and-utf8.md`](docs/decisions/0005-line-endings-and-utf8.md), [`docs/decisions/0006-snapshot-and-stable-option-ids.md`](docs/decisions/0006-snapshot-and-stable-option-ids.md), [`docs/decisions/0008-mcp-server.md`](docs/decisions/0008-mcp-server.md), [`docs/decisions/0009-analysis-and-media.md`](docs/decisions/0009-analysis-and-media.md), [`docs/decisions/0010-benchmark-and-portability.md`](docs/decisions/0010-benchmark-and-portability.md), [`docs/decisions/0011-kb-interview-grounding.md`](docs/decisions/0011-kb-interview-grounding.md) и [`docs/devlog.md`](docs/devlog.md). Бенчмарк измеряет файлы и строки, не смысл ответа.
