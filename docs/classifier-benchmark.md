# Классификатор: дешёвый контур, сильная модель и правила

Офлайн-цифры считает `python scripts/classify_bench.py summary` по учебному набору и сохранённым ответам. Живой cursor-agent сюда не подставляется: пока нет каталога в `bench/classify/runs`, в живой таблице стоят прочерки.

Стоимость во всех строках — оценка по `config/model-prices.json`, не счёт и не тариф Cursor. Токены учебных ответов заданы в корпусе. Токены живого прогона берутся из поля `usage` JSON cursor-agent, если оно есть. Кэш-токены в оценку не входят: отдельной ставки для них в таблице нет.

Учебный набор: 36 примеров в `bench/classify/items.jsonl`. Материалы вымышленные.

## Офлайн, учебные сохранённые ответы

Это проверка конвейера, а не замер модели. Дешёвый и сильный столбцы читают `bench/classify/saved`. Правила считаются заново.

| Контур | Верно | Точность | Среднее время, с | Токены входа | Токены выхода | Оценка стоимости, не счёт |
| --- | --- | --- | --- | --- | --- | --- |
| Правила, без сети | 33/36 | 0.9167 | 0.000 | 0 | 0 | 0.000000 |
| Учебный дешёвый ответ | 30/36 | 0.8333 | 0.400 | 6480 | 1440 | 0.001836 |
| Учебный сильный ответ | 33/36 | 0.9167 | 0.900 | 7920 | 1800 | 0.050760 |
| Дешёвый ответ, затем эскалация | 34/36 | 0.9444 | 0.550 | 7800 | 1740 | 0.010296 |

Промахи офлайна:

- Правила, без сети: c09, c11, r13. посчитано правилами по учебному набору, не ответ модели.
- Учебный дешёвый ответ: c07, c09, c11, i06, i08, r13. учебные сохранённые ответы, не прогон cursor-agent.
- Учебный сильный ответ: c07, c09, c10. учебные сохранённые ответы, не прогон cursor-agent.
- Дешёвый ответ, затем эскалация: c09, i08. учебный конвейер: при низкой уверенности или нарушении правила берётся сильный ответ или правило.

Эскалаций в учебном конвейере: 6 из 36.

## Живой cursor-agent

Архива `bench/classify/runs` с парой cheap.jsonl и strong.jsonl нет. Цифры не выдуманы.

| Контур | Верно | Точность | Среднее время, с | Токены входа | Токены выхода | Оценка стоимости, не счёт |
| --- | --- | --- | --- | --- | --- | --- |
| cursor-agent, лёгкая модель | — | — | — | — | — | — |
| cursor-agent, сильная или модель по умолчанию | — | — | — | — | — | — |
| Дешёвый ответ, затем эскалация | — | — | — | — | — | — |

## Вывод

Оставляем. На учебном наборе правила дают метку без сети и не ставят факт без ссылки. Польза лёгкой модели против сильной на живом cursor-agent в этой таблице не заявлена, пока в живых строках прочерки. Учебный конвейер показывает только то, что низкая уверенность и нарушение правила не остаются без следа: невалидная метка — ошибка, а не догадка. Если живой прогон не обгонит правила, в `config/routing.json` достаточно `"backend": "rules"`: модель тогда не оправдывает вызов, команда остаётся.

## Как прогнать на Windows

Из корня репозитория, PowerShell 5.1. В командах нет `$` и нет кавычек внутри аргумента. Кириллица лежит в файлах UTF-8, не в командной строке.

```powershell
python scripts/classify_bench.py models
python scripts/classify_bench.py run --out C:\strat-classify
python scripts/classify_bench.py archive --work C:\strat-classify
python scripts/classify_bench.py summary
python scripts/classify_bench.py summary --check
```

`models` печатает `agent --list-models`. Имя лёгкой модели по умолчанию — `composer-2.5-fast` в `config/routing.json`. Если этой подписки в списке нет, запишите id в `cheap_model`. Пустой `strong_model` значит модель cursor-agent по умолчанию, без флага `--model`.

`run` вызывает `agent -p --trust --force --mode ask --output-format json`. Режим ask и временный каталог не дают классификатору править репозиторий. `--approve-mcps` не передаётся. Ключ API для этого прогона не нужен: вход по подписке Cursor.

`summary --check` сверяет этот файл и `bench/classify/summary.json` с пересчётом. Сеть и cursor-agent он не вызывает.

Ручная метка без модели, тот же корень:

```powershell
python scripts/strat.py classify projects/_example --task claim-type --input-file bench/classify/samples/fact-with-cite.txt --backend rules
python scripts/strat.py classify projects/_example --task claim-type --input-file bench/classify/samples/fact-without-cite.txt --backend rules
python scripts/strat.py classify projects/_example --task route --input-file bench/classify/samples/route-collect.txt --backend rules
python scripts/strat.py classify projects/_example --task impact --input-file bench/classify/samples/impact-e001.txt --backend rules
```

Команда дописывает `log.md` учебного проекта. В stdout один JSON. У строки со ссылкой метка `fact`. У строки без ссылки метка не `fact`.
