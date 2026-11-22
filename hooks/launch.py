"""Entry point of every hook command in .cursor/hooks.json and .claude/settings.json.

The command in those files is a short readable ``python -c`` line (see
``hooks/launcher.py``). It finds the repository root, keeps the hook payload
from stdin and runs this file. Here the root is simply this file's parent, so
the process cwd does not matter.

Usage by hand, from anywhere::

    echo {"command": "pip install x"} | python hooks/launch.py guard_shell
"""

from __future__ import annotations

import io
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if not isinstance(sys.stdin, io.StringIO):
        probe = getattr(sys.stdin, "isatty", None)
        raw = "" if sys.stdin is None or (callable(probe) and probe()) else sys.stdin.read()
        sys.stdin = io.StringIO(raw)
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hooks")]
    import stdio_utf8

    stdio_utf8.configure_stdio()
    sys.argv = ["run.py", name]
    runpy.run_path(str(ROOT / "hooks" / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
