# Хуки

Хуки не включены. Без них агент всё равно обязан вызывать `strat check` и `strat log` по `AGENTS.md`. Включите хуки, когда ведёте боевой проект и хотите напоминание.

Скрипты лежат здесь, а не внутри каталога IDE: и Cursor, и Claude Code вызывают одну и ту же команду. Оба скрипта сначала опустошают stdin, если их запустил хук.

## Cursor

Скопируйте `hooks/cursor-hooks.json.example` в `.cursor/hooks.json`. Команды считаются от корня репозитория. `afterFileEdit` запускает проверку текущего проекта. `stop` только печатает напоминание и выходит с кодом 0, ход не блокирует.

Формат сверен с документацией Cursor Hooks: проектный файл `.cursor/hooks.json`, события `afterFileEdit` и `stop`.

## Claude Code

Фрагмент из `hooks/claude-settings.json.example` добавьте в `.claude/settings.json`, не затирая другие ключи. `PostToolUse` с матчером `Edit|Write` гоняет проверку. `Stop` печатает напоминание. Не возвращайте код 2: это удержало бы ход.

Проверка ищет проект через `projects/.current` или единственный боевой проект. Папка `projects/_example` сама по себе текущей не становится. Пока боевого проекта нет, `post-write.sh` ничего не ломает и выходит с кодом 0.

Ручной запуск тот же:

```text
python3 scripts/strat.py check
python3 scripts/strat.py status
```
