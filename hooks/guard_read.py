#!/usr/bin/env python3
"""beforeReadFile: do not send .env and key files to the model. Fail open."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import read_payload, write_json  # noqa: E402
from policy import decide  # noqa: E402


def emit(permission: str, reason: str) -> None:
    body = {"permission": "deny" if permission == "deny" else "allow"}
    if permission == "deny" and reason:
        body["user_message"] = reason
    write_json(body)


def main() -> int:
    try:
        payload = read_payload()
        permission, reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 — fail open with valid JSON
        sys.stderr.write(f"хук guard_read пропущен: {exc}\n")
        emit("allow", "")
        return 0
    emit(permission, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
