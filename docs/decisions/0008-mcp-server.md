# ADR 0008. MCP-сервер — тонкая обёртка над CLI

- Статус: принято
- Дата: 2026-11-17

## Контекст

Агент в Cursor, Claude Code и VS Code/Copilot менял `projects/<id>/` через оболочку и сам разбирал текст `strat.py`. На Windows PowerShell 5.1 это ещё и ломает кириллицу в аргументах и раскрывает `$VAR`. У этапа 1 тот же урок: команда хука не может полагаться на `$CLAUDE_PROJECT_DIR` и на внутренние двойные кавычки, а рабочий каталог бывает не корнем репозитория.

Нужен один и тот же набор действий в любом MCP-клиенте: структурированный вызов, JSON на выходе, правила `AGENTS.md` на месте. Отдельный SDK (`mcp` на PyPI) тянет пакеты. На машине хакатона их может не быть, а `python3` в Windows часто оказывается заглушкой Store.

## Решение

Сервер — `scripts/mcp_server.py`, только стандартная библиотека, транспорт stdio. В stdout только JSON-RPC 2.0: одна строка на сообщение, а если клиент прислал `Content-Length`, ответ в том же кадре. Логи — в stderr. Методы: `initialize`, `tools/list`, `tools/call`, `ping`. Неизвестный метод и битый JSON — ошибки JSON-RPC (`-32601`, `-32602`, `-32700`). Сбой инструмента — результат с `isError: true` и текстом по-русски, чтобы модель его видела.

Инструменты вызывают функции `strat.py` (`cmd_*`, `check_project`, `format_report`, `affected_evidence`, `format_cost`). Второй реализации ворот, цитат и реестров нет. У каждого инструмента JSON Schema и описание на русском. Рядом с текстом CLI лежит `structuredContent`.

Имена: `strat_init`, `strat_resume`, `strat_status`, `strat_show`, `strat_check` (параметр `strict`), `strat_add_source`, `strat_fetch`, `strat_add_evidence`, `strat_add_option`, `strat_supersede_option`, `strat_impact`, `strat_retract`, `strat_supersede`, `strat_propose_revision`, `strat_accept_revision`, `strat_log`, `strat_cost`, `strat_confirm`, `strat_advance`, `strat_complete`, `strat_doctor`. `strat_resume` и `strat_init` добавлены к обязательному списку: без них первое действие из конституции снова уходит в оболочку.

`strat_confirm`, `strat_advance` и `strat_accept_revision` требуют `human_confirmed=true`. Без `true` состояние не меняется. В аннотациях MCP у них `destructiveHint: true` и `readOnlyHint: false`, чтобы клиент спросил человека. Флаг не криптография: агент может солгать, как и при вызове CLI. Разница в том, что IDE видит деструктивный инструмент и может не пустить его без одобрения. Конституция по-прежнему запрещает ставить флаг без явной фразы человека.

`strat_fetch` помечает ответ `untrusted: true` и начинает текст с «НЕДОВЕРЕННЫЕ ДАННЫЕ». Снимок страницы — данные, не инструкции. Путь проекта после разрешения обязан лежать в каталоге `projects` (`STRAT_PROJECTS_DIR`, если он задан). Файл для `strat_add_source` — только внутри папки этого проекта. Значение «-» не читает stdin протокола. Ключ API в ответ не попадает: его вырезает тот же `scrub`, что и в CLI.

Команда подключения одна и та же в `.cursor/mcp.json`, `.mcp.json` (Claude Code) и `.vscode/mcp.json` (Copilot): `python` и аргумент `-c` с `exec` от base64. В строке нет `$`, `%` и внутренних двойных кавычек. Программа читает `CURSOR_PROJECT_DIR`, `CLAUDE_PROJECT_DIR` и `STRAT_REPO_ROOT` из окружения процесса, затем поднимается от текущего каталога к `AGENTS.md` и `scripts/mcp_server.py`. Переменные не написаны в команде, поэтому PowerShell 5.1 их не раскрывает. Пересобрать json: `python scripts/mcp_server.py --write-configs`.

SDK не подключали. Запасной путь — прежний CLI, не вторая реализация сервера. Если инструментов в сессии нет, агент вызывает `python scripts/strat.py`.

`python scripts/strat.py doctor` запускает команду из `.cursor/mcp.json` из чужого каталога с `CURSOR_PROJECT_DIR` и требует ответ на `initialize`. Нет ответа — ненулевой код.

## Последствия

Агент может вести проект вызовами, а не разбором stdout. Цена — клиент без MCP остаётся на CLI, и это нормально. `human_confirmed` не отличает человека от модели, если IDE проигнорирует `destructiveHint`. Содержимое страницы по-прежнему может попасть в контекст: баннер говорит, что это данные, но не стирает текст. Официальный пакет `mcp` не нужен для запуска и не является запасным сервером.
