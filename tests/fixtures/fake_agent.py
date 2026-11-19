#!/usr/bin/env python3
"""Stand-in for cursor-agent. The real CLI is not required in CI."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    if "--linger" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--linger") + 1])
        handle = (folder / "held.txt").open("w", encoding="utf-8")
        handle.write("held")
        handle.flush()
        time.sleep(30)
        handle.close()
        return 0
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    log = os.environ.get("FAKE_AGENT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}, ensure_ascii=False) + "\n")
    if os.environ.get("FAKE_AGENT_HOLD") == "1":
        import subprocess

        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--linger", os.getcwd()])
        Path(os.getcwd(), "child.pid").write_text(str(child.pid), encoding="utf-8")
    fail = os.environ.get("FAKE_AGENT_FAIL_TEXT") or ""
    if fail and fail in prompt:
        sys.stderr.write("учебный отказ\n")
        return 1
    delay = os.environ.get("FAKE_AGENT_SLEEP")
    if delay:
        time.sleep(float(delay))
    body = json.dumps(
        {"label": "hypothesis", "confidence": 0.9, "rationale": "учебный ответ"},
        ensure_ascii=False,
    )
    sys.stdout.write(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": body,
                "usage": {"inputTokens": 10, "outputTokens": 4},
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
