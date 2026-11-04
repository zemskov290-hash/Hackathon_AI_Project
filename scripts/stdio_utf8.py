"""Force UTF-8 stdio.

Windows consoles often use cp1251 or cp866. A Russian message then raises
UnicodeEncodeError and the process exits before any text is written.
"""

from __future__ import annotations

import sys


def configure_stdio() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
