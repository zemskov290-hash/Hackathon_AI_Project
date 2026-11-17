"""Shared stdin handling for IDE hooks. A crash here must not block the user."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_payload() -> dict:
    if sys.stdin.isatty():
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
