"""Build the shell command stored in hooks.json and Claude settings.

The command is `python -c` plus base64. It contains neither `$` nor `%`, so
PowerShell, cmd and sh do not expand a project variable before Python starts.
"""

from __future__ import annotations

import base64
from pathlib import Path

NAMES = ("post_write", "guard_shell", "guard_read", "stage_end")
BODY = Path(__file__).with_name("bootstrap.py").read_bytes()


def command_for(name: str) -> str:
    if name not in NAMES:
        raise SystemExit(f"unknown hook name: {name}")
    encoded = base64.b64encode(BODY).decode("ascii")
    if any(token in encoded for token in ("$", "%", '"')):
        raise SystemExit("launcher encoding picked up a shell metacharacter")
    return f'python -c "import base64;exec(base64.b64decode(\'{encoded}\'))" {name}'
