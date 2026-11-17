#!/usr/bin/env python3
"""Block secret writes and commands copied out of source snapshots.

Exit 0 with permission=allow when this script fails. Invalid JSON must not
be the failure mode: permission hooks treat a bad body as a block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import read_payload  # noqa: E402
from policy import decide  # noqa: E402


def emit(payload: dict, permission: str, reason: str) -> None:
    event = str(payload.get("hook_event_name") or "")
    if event == "PreToolUse":
        body = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if permission == "allow" else "deny",
                "permissionDecisionReason": reason,
            }
        }
    else:
        body = {"permission": permission if permission in {"allow", "deny", "ask"} else "allow"}
        if permission == "deny" and reason:
            body["user_message"] = reason
            body["agent_message"] = reason
    sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")


def main() -> int:
    try:
        payload = read_payload()
    except Exception as exc:  # noqa: BLE001 — fail open with valid JSON
        sys.stderr.write(f"хук guard пропущен: {exc}\n")
        emit({}, "allow", "")
        return 0
    try:
        permission, reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 — fail open with valid JSON
        sys.stderr.write(f"хук guard пропущен: {exc}\n")
        emit(payload, "allow", "")
        return 0
    emit(payload, permission, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
