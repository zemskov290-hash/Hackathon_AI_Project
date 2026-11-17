"""Readable hook launcher and the command written into the IDE configs.

Why the command looks the way it does
--------------------------------------
Cursor on Windows runs hook commands through PowerShell. A command that
contains ``$CLAUDE_PROJECT_DIR`` is expanded by PowerShell before Python
starts. That variable is empty (it is not ``$env:CLAUDE_PROJECT_DIR``), so
``python "$CLAUDE_PROJECT_DIR/hooks/run.py"`` becomes ``python \\hooks\\run.py``
and Python opens ``C:\\hooks\\run.py``.

Cursor also loads ``.claude/settings.json`` when third-party imports are on
(the default). Every matching hook runs. If the answers disagree, deny wins
over allow. A broken Claude hook therefore blocks the action even when
``.cursor/hooks.json`` would have allowed it.

The command below is the same in both files. It is::

    python -c "import base64;exec(base64.b64decode('<program>'))" <name>

PowerShell 5.1 strips double quotes when it starts python.exe, so inlining the
program (which needs quotes) becomes a SyntaxError, the hook exits 1, and the
guard fails open. Base64 has no quotes, no ``$`` and no ``%``. It is only
``PROGRAM`` encoded. Decode a blob with::

    python -c "import base64; print(base64.b64decode('PASTE').decode())"

Regenerate the configs after editing ``PROGRAM``::

    python hooks/launcher.py

The unit test fails if a committed config drifts from ``command_for``.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# The program encoded into the hook command. Edit this, then run the file.
PROGRAM = """
import io
import json
import os
import runpy
import sys
from pathlib import Path

def isatty():
    probe = getattr(sys.stdin, "isatty", None)
    return bool(probe()) if callable(probe) else False

def payload_of(raw):
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def seeds(payload):
    found = []
    for key in ("CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(key)
        if value:
            found.append(value)
    roots = payload.get("workspace_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    if isinstance(roots, list):
        found.extend(str(item) for item in roots if item)
    for key in ("cwd", "workspace_root"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    tool = payload.get("tool_input")
    if isinstance(tool, dict):
        for key in ("cwd", "working_directory"):
            value = tool.get(key)
            if isinstance(value, str) and value:
                found.append(value)
    found.append(str(Path.cwd()))
    return found

def climb(start):
    if not start:
        return None
    current = Path(start).expanduser()
    for parent in [current, *current.parents]:
        if (parent / "hooks" / "run.py").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    return None

def allow(name):
    mark = chr(34)
    if name.startswith("guard_"):
        sys.stdout.write("{" + mark + "permission" + mark + ":" + mark + "allow" + mark + "}" + chr(10))
    else:
        sys.stdout.write("{}" + chr(10))
    sys.stderr.write("hook root not found" + chr(10))

def main():
    raw = "" if isatty() else sys.stdin.read()
    sys.stdin = io.StringIO(raw)
    payload = payload_of(raw)
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    root = None
    for seed in seeds(payload):
        root = climb(seed)
        if root is not None:
            break
    if root is None:
        allow(name)
        raise SystemExit(0)
    sys.path[:0] = [str(root / "scripts"), str(root / "hooks")]
    import stdio_utf8
    stdio_utf8.configure_stdio()
    sys.argv = ["run.py", name]
    runpy.run_path(str(root / "hooks" / "run.py"), run_name="__main__")

main()
"""

NAMES = ("post_write", "guard_shell", "guard_read", "stage_end")
ROOT = Path(__file__).resolve().parents[1]


def program_text() -> str:
    """The exact Python source placed inside the hook command."""
    lines = [line.rstrip() for line in PROGRAM.strip("\n").splitlines()]
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    for banned in ("'", "$", "%"):
        if banned in text:
            raise SystemExit(f"launcher program contains {banned!r}; the shell command would split")
    return text


def command_for(name: str) -> str:
    if name not in NAMES:
        raise SystemExit(f"unknown hook name: {name}")
    encoded = base64.b64encode(program_text().encode("utf-8")).decode("ascii")
    if any(token in encoded for token in ("$", "%", '"', "'")):
        raise SystemExit("base64 of the launcher picked up a shell metacharacter")
    # This quoting matched a live PowerShell 5.1 run: the payload has no quotes.
    return f'python -c "import base64;exec(base64.b64decode(\'{encoded}\'))" {name}'


def embedded_program(command: str) -> str:
    marker = "base64.b64decode('"
    start = command.find(marker)
    if start < 0:
        raise ValueError("command is not the base64 launcher")
    start += len(marker)
    end = command.find("')", start)
    if end < 0:
        raise ValueError("command is not the base64 launcher")
    return base64.b64decode(command[start:end]).decode("utf-8")


def _configs() -> tuple[dict, dict]:
    commands = {name: command_for(name) for name in NAMES}
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


def write_configs() -> None:
    cursor, claude = _configs()
    rendered = {
        ROOT / ".cursor" / "hooks.json": cursor,
        ROOT / "hooks" / "cursor-hooks.json.example": cursor,
        ROOT / ".claude" / "settings.json": claude,
        ROOT / "hooks" / "claude-settings.json.example": claude,
    }
    for path, payload in rendered.items():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    write_configs()
    sys.exit(0)
