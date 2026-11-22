"""Writes the hook command into the IDE configs. The hook itself is ``hooks/launch.py``.

Why the command looks the way it does
--------------------------------------
Cursor on Windows runs hook commands through PowerShell. ``$CLAUDE_PROJECT_DIR``
in a command is expanded by PowerShell before Python starts. That variable is
empty (it is not ``$env:CLAUDE_PROJECT_DIR``), so ``python
"$CLAUDE_PROJECT_DIR/hooks/run.py"`` opens ``C:\\hooks\\run.py``. Cursor's CLI
has also been seen starting hooks with cwd outside the repository, so a plain
``python hooks/launch.py`` is not safe either.

Cursor also loads ``.claude/settings.json`` when third-party imports are on
(the default). Every matching hook runs. If the answers disagree, deny wins.

PowerShell 5.1 strips double quotes inside an argument of ``python.exe``. So
the command is one ``python -c "..."`` line with readable Python that has only
single quotes, no ``$``, no ``%``, no backslash and no ``!``. It reads the hook
payload from stdin, finds the root (``CURSOR_PROJECT_DIR``,
``CLAUDE_PROJECT_DIR``, cwd, then ``workspace_roots`` and ``cwd`` from the
payload), puts the payload back on stdin and runs ``hooks/launch.py <name>``.
No root: guards answer allow, others ``{}``.

Regenerate the configs after editing ``BOOTSTRAP``::

    python hooks/launcher.py                   # interpreter «python»
    python hooks/launcher.py --python python3  # where only python3 is on PATH

The unit test fails if a committed config drifts from ``command_for``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# One line of Python, split here only for reading. Keep it free of " $ % \\ and !.
BOOTSTRAP_PARTS = (
    "import io,json,os,runpy,sys",
    "from pathlib import Path",
    # Keep the payload: the hook reads it after the root is found.
    "raw='' if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()",
    "sys.stdin=io.StringIO(raw)",
    "hit=lambda s:[p for p in (Path(s),*Path(s).parents) if (p/'hooks'/'launch.py').is_file() and (p/'AGENTS.md').is_file()] if s else []",
    "found=hit(os.environ.get('CURSOR_PROJECT_DIR'))+hit(os.environ.get('CLAUDE_PROJECT_DIR'))+hit(os.getcwd())",
    # The payload is parsed only when the environment and cwd did not lead to the root.
    "data=json.loads(raw) if not found and raw.strip()[:1]=='{' else {}",
    "roots=data.get('workspace_roots') or []",
    "found+=[p for s in ([roots] if isinstance(roots,str) else list(roots))+[data.get('cwd')] for p in hit(s)]",
    "runpy.run_path(str(found[0]/'hooks'/'launch.py'),run_name='__main__') if found else "
    "(print(json.dumps(dict(permission='allow')) if sys.argv[-1].startswith('guard_') else '{}'),"
    "sys.stderr.write('hook root not found'+chr(10)))",
)
BOOTSTRAP = ";".join(BOOTSTRAP_PARTS)
FORBIDDEN = ('"', "$", "%", "\\", "!", "`")
INTERPRETERS = ("python", "python3", "py")

NAMES = ("post_write", "guard_shell", "guard_read", "stage_end")
ROOT = Path(__file__).resolve().parents[1]


def bootstrap_text() -> str:
    """The Python placed inside the hook command."""
    for banned in FORBIDDEN:
        if banned in BOOTSTRAP:
            raise SystemExit(f"hook bootstrap contains {banned!r}; the shell command would break")
    return BOOTSTRAP


def command_for(name: str, interpreter: str = "python") -> str:
    if name not in NAMES:
        raise SystemExit(f"unknown hook name: {name}")
    if interpreter not in INTERPRETERS:
        raise SystemExit(f"interpreter must be one of {', '.join(INTERPRETERS)}")
    return f'{interpreter} -c "{bootstrap_text()}" {name}'


def embedded_program(command: str) -> str:
    """The Python between the only pair of double quotes."""
    if command.count('"') != 2:
        raise ValueError("command is not the hook bootstrap")
    return command.split('"')[1]


def configured_interpreter(path: Path) -> str:
    """Interpreter used by an existing config, so a regeneration keeps the choice."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "python"
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "command" and isinstance(item, str):
                    found.append(item.split(" ", 1)[0])
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found[0] if found and found[0] in INTERPRETERS else "python"


def _configs(interpreter: str = "python") -> tuple[dict, dict]:
    commands = {name: command_for(name, interpreter) for name in NAMES}
    cursor = {
        "version": 1,
        "hooks": {
            "afterFileEdit": [{"command": commands["post_write"], "timeout": 60}],
            "postToolUse": [{"command": commands["post_write"], "matcher": "Write", "timeout": 60}],
            "beforeShellExecution": [{"command": commands["guard_shell"], "timeout": 15}],
            "beforeReadFile": [{"command": commands["guard_read"], "timeout": 10}],
            "stop": [{"command": commands["stage_end"], "timeout": 20}],
        },
    }
    claude = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|PowerShell|Read",
                    "hooks": [{"type": "command", "command": commands["guard_shell"]}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Edit|Write|MultiEdit",
                    "hooks": [{"type": "command", "command": commands["post_write"]}],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": commands["stage_end"]}]}],
        }
    }
    return cursor, claude


def write_configs(interpreter: str = "python") -> None:
    cursor, claude = _configs(interpreter)
    rendered = {
        ROOT / ".cursor" / "hooks.json": cursor,
        ROOT / "hooks" / "cursor-hooks.json.example": cursor,
        ROOT / ".claude" / "settings.json": claude,
        ROOT / "hooks" / "claude-settings.json.example": claude,
    }
    for path, payload in rendered.items():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path.relative_to(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="записать команду хуков в конфиги IDE")
    parser.add_argument(
        "--python",
        choices=INTERPRETERS,
        default="python",
        help="имя интерпретатора в команде: python (по умолчанию), python3 или py",
    )
    args = parser.parse_args(argv)
    write_configs(args.python)
    return 0


if __name__ == "__main__":
    sys.exit(main())
