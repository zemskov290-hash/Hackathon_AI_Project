# Хуки

Хуки не включены. Без них агент всё равно обязан вызывать `strat check` и `strat log` по `AGENTS.md`. Включите хуки, когда ведёте боевой проект и хотите напоминание.

Рабочие скрипты — `hooks/post_write.py` и `hooks/stage_end.py`. Их запускает тот же интерпретатор, что и CLI (`sys.executable` внутри, снаружи — команда `python`). Оболочки `post-write.sh` и `stage-end.sh` только ищут сначала `python`, потом `python3`: на Windows `python3` часто является заглушкой Store.

Оба скрипта сначала опустошают stdin, если их запустил хук.

## Cursor

Скопируйте `hooks/cursor-hooks.json.example` в `.cursor/hooks.json`. Команды считаются от корня репозитория. `afterFileEdit` запускает проверку текущего проекта. `stop` только печатает напоминание и выходит с кодом 0, ход не блокирует.

Формат сверен с документацией Cursor Hooks: проектный файл `.cursor/hooks.json`, события `afterFileEdit` и `stop`.

## Claude Code

Фрагмент из `hooks/claude-settings.json.example` добавьте в `.claude/settings.json`, не затирая другие ключи. `PostToolUse` с матчером `Edit|Write` гоняет проверку. `Stop` печатает напоминание. Не возвращайте код 2: это удержало бы ход.

Проверка ищет проект через `projects/.current` или единственный боевой проект. Папка `projects/_example` сама по себе текущей не становится. Пока боевого проекта нет, `post_write.py` ничего не ломает и выходит с кодом 0.

Ручной запуск:

```powershell
python scripts/strat.py check
python scripts/strat.py status
python hooks/post_write.py
```
