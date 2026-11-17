#!/usr/bin/env python3
"""Start one hook by name. The repo root is this file's parent, not the process cwd.

Cursor's CLI has been observed to spawn project hooks with cwd outside the
repository (C:\\). Importing this file by an absolute path still finds the scripts.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(ROOT / "scripts"))

from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()

SCRIPTS = {
    "guard_shell": "guard_shell.py",
    "guard_read": "guard_read.py",
    "post_write": "post_write.py",
    "stage_end": "stage_end.py",
}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    target_name = SCRIPTS.get(name)
    if target_name is None:
        sys.stderr.write("хук: неизвестное имя скрипта\n")
        sys.stdout.write("{}\n")
        return 0
    os.chdir(ROOT)
    target = HOOKS / target_name
    sys.argv = [str(target), *sys.argv[2:]]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code not in (0, None):
            sys.stderr.write(f"хук завершился с кодом {code}, ход не блокируется\n")
        return 0
    except Exception as exc:  # noqa: BLE001 — fail open after a valid JSON body when possible
        sys.stderr.write(f"хук пропущен: {exc}\n")
        if name.startswith("guard_"):
            sys.stdout.write('{"permission":"allow"}\n')
        else:
            sys.stdout.write("{}\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
