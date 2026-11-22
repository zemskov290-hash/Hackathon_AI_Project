# Демо: семейные устройства на брифе хакатона (прогон 11)

Полный прогон агента «Стратег Фабрики продуктов» на опубликованном брифе хакатона МегаФона «связь для близких и собственных устройств». От брифа до стратегии, плана проверки, пересмотра и возобновления после перерыва — 9 реплик человека.

**Данные.** Оператор в брифе вымышленный, все числа брифа учебные. Других внутренних данных нет. Закрытые данные компаний не использовались: в работе только бриф и открытые страницы (МТС, МегаФон, каталог тарифов tarifgid.ru), сохранённые 22.11.2026. Интервью — **синтетические**, помечены так в каждом файле и в реестре свидетельств: голосом клиента они не являются и гипотез не подтверждают. Сведение о задержке остатков в биллинге — **учебная вводная** для отработки пересмотра (зарегистрирована как тестовый источник), а не наблюдение.

## Как запускали

Сценарный драйвер из репозитория: [`scripts/demo_drive.py`](../scripts/demo_drive.py). Он отправляет реплики `t1.txt` … `t9.txt` в cursor-agent по одной, останавливается на первом сбое и в конце снимает `check` и `cost`.

```powershell
python scripts/demo_drive.py --project family-connect-11 --turns <папка с t1..t9> --model composer-2.5-fast --expect-account <аккаунт Cursor>
```

- 9 ходов, все с кодом 0, без обрывов.
- Время ходов по журналам агента — **51,5 мин**; по отметкам журнала проекта — 51,2 мин.
- Модель — **Composer 2.5 Fast на всех ходах**: прогон сделан до того, как в `config/routing.json` появились отдельные дешёвая и сильная модели.
- Токены по журналам клиента агента: вход **2 864 975**, выход **154 550**, чтение кэша **16 574 528**. Это числа клиента, не счёт: под подпиской IDE отдельного счёта за ход нет.
- `python scripts/strat.py check demo/family-connect-11` — **OK**, 8 предупреждений `derivation`: у восьми входных чисел расчёта есть ссылка на допущение, но нет формулы вывода.

Человек выбрал рекомендованный вариант **O2 «Пилот: активация и ранние сигналы для семейных групп»** и принял пересмотр после учебной вводной.

## Что где лежит

| Что | Файл |
|---|---|
| Реплики человека, 9 ходов | [`run/t1.txt`](run/t1.txt) … [`run/t9.txt`](run/t9.txt) |
| Время ходов, проверка, расход | [`run/timing.txt`](run/timing.txt), [`run/check.txt`](run/check.txt), [`run/cost.txt`](run/cost.txt) |
| Бриф (исходный PDF) | [`family-connect-11/sources/brief-family-devices.pdf`](family-connect-11/sources/brief-family-devices.pdf) |
| Задача, как её понял агент, и подтверждение | [`family-connect-11/brief.md`](family-connect-11/brief.md), [`questions.md`](family-connect-11/questions.md) |
| Открытые источники и реестр | [`family-connect-11/sources/`](family-connect-11/sources/), [`sources.jsonl`](family-connect-11/sources.jsonl) |
| Свидетельства (факт, гипотеза, прогноз, синтетика) | [`family-connect-11/evidence.jsonl`](family-connect-11/evidence.jsonl) |
| Допущения и показатели | [`assumptions.jsonl`](family-connect-11/assumptions.jsonl), [`indicators.jsonl`](family-connect-11/indicators.jsonl) |
| Картина ситуации | [`family-connect-11/situation.md`](family-connect-11/situation.md) |
| Варианты, включая «оставить как есть» и «закрыть направление» | [`family-connect-11/options.md`](family-connect-11/options.md) |
| Продуктовая и коммуникационная стратегии | [`family-connect-11/strategy.md`](family-connect-11/strategy.md) |
| План проверки | [`family-connect-11/validation.md`](family-connect-11/validation.md) |
| Итоговый документ для руководства | [`family-connect-11/deliverable.md`](family-connect-11/deliverable.md) |
| Решения с основаниями и пересмотр | [`family-connect-11/decisions.md`](family-connect-11/decisions.md) |
| Синтетические интервью и разбор | [`family-connect-11/interviews/`](family-connect-11/interviews/) |
| Журнал действий агента | [`family-connect-11/log.md`](family-connect-11/log.md), [`log.jsonl`](family-connect-11/log.jsonl) |
| Состояние этапов и контрольные точки | [`family-connect-11/state.json`](family-connect-11/state.json) |

Сырые журналы ходов агента (`agent-logs/*.jsonl`) в репозиторий не положены: в них локальные пути машины. Сводка расхода из них — в [`run/cost.txt`](run/cost.txt). В реплике `t1.txt` путь к брифу заменён на `<папка с брифом>`.

## Проверить самому

Из корня репозитория:

```powershell
python scripts/strat.py check demo/family-connect-11
python scripts/strat.py resume demo/family-connect-11
```

Без пакета `pypdf` проверка не читает текст PDF-брифа: числа, которые могут идти из него, помечаются `number-unverified` (предупреждение, не ошибка), рядом одно предупреждение `[tooling]`. `cost demo/family-connect-11` покажет минуты по журналу проекта; токенов в нём не будет, потому что журналы ходов не приложены — они в `run/cost.txt`.
