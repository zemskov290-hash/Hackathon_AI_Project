"""Find the repo and start hooks/run.py. Executed from python -c, so it must not use $ or %."""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from pathlib import Path


def _isatty() -> bool:
    probe = getattr(sys.stdin, "isatty", None)
    return bool(probe()) if callable(probe) else False


def _payload(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _seeds(payload: dict) -> list[str]:
    found: list[str] = []
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


def _climb(start: str) -> Path | None:
    if not start:
        return None
    current = Path(start).expanduser()
    for parent in [current, *current.parents]:
        if (parent / "hooks" / "run.py").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    return None


def _allow(name: str) -> None:
    if name.startswith("guard_"):
        sys.stdout.write('{"permission":"allow"}\n')
    else:
        sys.stdout.write("{}\n")
    sys.stderr.write("hook root not found\n")


def main() -> None:
    raw = "" if _isatty() else sys.stdin.read()
    sys.stdin = io.StringIO(raw)
    payload = _payload(raw)
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    root = None
    for seed in _seeds(payload):
        root = _climb(seed)
        if root is not None:
            break
    if root is None:
        _allow(name)
        raise SystemExit(0)
    sys.path[:0] = [str(root / "scripts"), str(root / "hooks")]
    import stdio_utf8

    stdio_utf8.configure_stdio()
    sys.argv = ["run.py", name]
    runpy.run_path(str(root / "hooks" / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
