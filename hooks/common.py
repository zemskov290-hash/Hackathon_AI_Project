"""Shared stdin handling for IDE hooks. A crash here must not block the user."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stdio_utf8 import configure_stdio  # noqa: E402

configure_stdio()


def write_json(body: dict) -> None:
    """UTF-8 when the console allows it, otherwise ASCII escapes. Never raise."""
    text = json.dumps(body, ensure_ascii=False) + "\n"
    try:
        sys.stdout.write(text)
        return
    except UnicodeEncodeError:
        pass
    sys.stdout.write(json.dumps(body, ensure_ascii=True) + "\n")


def read_payload() -> dict:
    probe = getattr(sys.stdin, "isatty", None)
    if callable(probe) and probe():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("ожидался объект JSON")
    return data


def project_of(file_path: str) -> Path | None:
    path = Path(file_path)
    for parent in [path.parent, *path.parents]:
        if (parent / "state.json").is_file() and (parent / "evidence.jsonl").is_file():
            return parent
        if parent == parent.parent:
            break
    return None
