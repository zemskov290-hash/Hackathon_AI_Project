#!/usr/bin/env python3
"""Model routing without an API key: cheap and strong tiers through cursor-agent.

When no STRAT_LLM_API_KEY / OPENROUTER_API_KEY is set, `complete --tier cheap|strong`
runs one `agent -p --model <m> --output-format stream-json` call in an empty
folder with the model from config/routing.json -> classifier.cursor_agent
(cheap_model, strong_model). The actual model is read from the system.init
event, tokens and duration from the result event. If the agent CLI is absent,
or STRAT_CURSOR_ROUTE=0, the caller keeps MODE: manual as before.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import classify


def enabled() -> bool:
    if os.environ.get("STRAT_CURSOR_ROUTE", "").strip() in {"0", "false", "no", "off"}:
        return False
    return classify.agent_spec() is not None


def model_for(tier: str) -> str:
    config = classify.load_routing()
    if tier == "strong":
        return os.environ.get("STRAT_CURSOR_STRONG_MODEL") or config.get("strong_model") or ""
    return os.environ.get("STRAT_CURSOR_CHEAP_MODEL") or config.get("cheap_model") or ""


def timeout_seconds() -> int:
    try:
        return int(os.environ.get("STRAT_CURSOR_TIMEOUT") or classify.load_routing().get("timeout") or 180)
    except ValueError:
        return 180


def parse_stream(stdout: str) -> dict:
    """Text, actual model, tokens and duration from stream-json lines. Unknown lines are skipped."""
    model = ""
    parts: list[str] = []
    result_text = None
    usage: dict = {}
    duration_ms = None
    is_error = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and isinstance(event.get("model"), str):
            model = event["model"]
        elif kind == "assistant":
            message = event.get("message") or {}
            for item in message.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        elif kind == "result":
            if isinstance(event.get("result"), str):
                result_text = event["result"]
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            if isinstance(event.get("duration_ms"), (int, float)):
                duration_ms = event["duration_ms"]
            is_error = bool(event.get("is_error"))
            if isinstance(event.get("model"), str) and not model:
                model = event["model"]
    input_tokens, output_tokens, cache_read, _cache_write = classify._usage_tokens(usage)
    return {
        "content": (result_text if result_text is not None else "".join(parts)).strip(),
        "model": model,
        "input_tokens": input_tokens + cache_read,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "is_error": is_error,
    }


def call(prompt: str, tier: str, *, runner=None, workspace: Path | None = None) -> dict:
    """One call. Never raises: failures come back as ok=False with a scrubbed reason."""
    requested = model_for(tier)
    started = time.monotonic()
    try:
        prefix, extra = classify.cursor_launch()
        folder = classify.classifier_workspace(workspace)
        argv = [*prefix, "-p", "--trust", "--output-format", "stream-json", "--workspace", str(folder)]
        if requested:
            argv.extend(["--model", requested])
        argv.append(prompt)
        run = runner or classify._run_agent
        code, stdout, err, elapsed = run(argv, folder, classify._child_env(extra), timeout_seconds())
    except Exception as exc:  # noqa: BLE001 — missing CLI, locked folder: degrade, never crash
        return {"ok": False, "error": classify.scrub(f"cursor-agent не запустился: {exc}"), "requested": requested,
                "model": requested or "cursor/default", "seconds": round(time.monotonic() - started, 2),
                "content": "", "input_tokens": None, "output_tokens": None}
    parsed = parse_stream(stdout or "")
    seconds = round(parsed["duration_ms"] / 1000.0, 2) if parsed["duration_ms"] else round(float(elapsed or 0), 2)
    result = {
        "ok": code == 0 and not parsed["is_error"],
        "error": None,
        "requested": requested,
        "model": parsed["model"] or requested or "cursor/default",
        "seconds": seconds,
        "content": classify.scrub(parsed["content"]),
        "input_tokens": parsed["input_tokens"] or None,
        "output_tokens": parsed["output_tokens"] or None,
    }
    if not result["ok"]:
        detail = (err or stdout or "ненулевой код").strip().splitlines()
        result["error"] = classify.scrub(f"cursor-agent код {code}: {(detail[0] if detail else '')[:300]}")
    return result
