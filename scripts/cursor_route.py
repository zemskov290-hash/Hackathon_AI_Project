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
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import classify

ROUTING_PATH = Path(__file__).resolve().parents[1] / "config" / "routing.json"
DEFAULT_ORDER = ["cursor-agent", "claude", "codex"]
# Defaults for other agent CLIs. Not verified live in this repository: set your
# own in config/routing.json -> complete.<backend>.cheap_model / strong_model.
DEFAULT_MODELS = {
    "claude": {"cheap_model": "haiku", "strong_model": "sonnet"},
    "codex": {"cheap_model": "", "strong_model": ""},
}


def _off() -> bool:
    return os.environ.get("STRAT_CURSOR_ROUTE", "").strip() in {"0", "false", "no", "off"}


def enabled() -> bool:
    if _off():
        return False
    return classify.agent_spec() is not None


def complete_config() -> dict:
    try:
        loaded = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = {}
    section = loaded.get("complete") if isinstance(loaded, dict) else None
    return section if isinstance(section, dict) else {}


def backend_order() -> list[str]:
    raw = os.environ.get("STRAT_COMPLETE_BACKENDS")
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    order = complete_config().get("backends")
    if isinstance(order, list) and order:
        return [str(item) for item in order]
    return list(DEFAULT_ORDER)


def cli_path(name: str, which=None) -> str | None:
    which = which or shutil.which
    explicit = os.environ.get(f"STRAT_{name.upper()}_CMD")
    if explicit:
        return explicit if (Path(explicit).is_file() or which(explicit)) else None
    return which(name)


def pick_backend(which=None) -> str | None:
    """First available no-key backend: cursor-agent, then claude, then codex. None -> MODE: manual."""
    if _off():
        return None
    for name in backend_order():
        if name == "cursor-agent" and enabled():
            return name
        if name in {"claude", "codex"} and cli_path(name, which):
            return name
    return None


def other_model(backend: str, tier: str) -> str:
    env = os.environ.get(f"STRAT_{backend.upper()}_{'STRONG' if tier == 'strong' else 'CHEAP'}_MODEL")
    if env is not None:
        return env
    section = complete_config().get(backend)
    section = section if isinstance(section, dict) else {}
    key = "strong_model" if tier == "strong" else "cheap_model"
    return str(section.get(key, DEFAULT_MODELS.get(backend, {}).get(key, "")) or "")


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    started = time.monotonic()
    env = os.environ.copy()
    for key in ("STRAT_LLM_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or "", time.monotonic() - started


def parse_claude(stdout: str) -> dict:
    """claude -p --output-format json: one object with result, usage, duration_ms, modelUsage."""
    payload: dict = {}
    for chunk in (stdout.strip(), *reversed(stdout.strip().splitlines())):
        try:
            loaded = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(loaded, dict):
            payload = loaded
            break
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    models = payload.get("modelUsage") if isinstance(payload.get("modelUsage"), dict) else {}
    inp = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
    return {
        "content": str(payload.get("result") or "").strip(),
        "model": next(iter(models), "") if models else str(payload.get("model") or ""),
        "input_tokens": inp,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "duration_ms": payload.get("duration_ms") if isinstance(payload.get("duration_ms"), (int, float)) else None,
        "is_error": bool(payload.get("is_error")) or not payload,
    }


def parse_codex(stdout: str, last_message: str) -> dict:
    """codex exec --json: JSONL events; usage in turn.completed. Text from --output-last-message."""
    inp = out = 0
    model = ""
    failed = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if usage:
            inp += int(usage.get("input_tokens") or 0) + int(usage.get("cached_input_tokens") or 0)
            out += int(usage.get("output_tokens") or 0)
        if isinstance(event.get("model"), str):
            model = event["model"]
        if str(event.get("type") or "").endswith("failed") or event.get("type") == "error":
            failed = True
    return {"content": last_message.strip(), "model": model, "input_tokens": inp, "output_tokens": out,
            "duration_ms": None, "is_error": failed or not last_message.strip()}


def call_other(backend: str, prompt: str, tier: str, *, runner=None, which=None) -> dict:
    """claude or codex in an empty temp folder. Never raises."""
    requested = other_model(backend, tier)
    started = time.monotonic()
    run = runner or _run
    try:
        exe = cli_path(backend, which)
        if not exe:
            raise FileNotFoundError(f"{backend} не найден в PATH")
        with tempfile.TemporaryDirectory(prefix="strat-route-") as folder:
            work = Path(folder)
            if backend == "claude":
                argv = [exe, "-p", "--output-format", "json"]
                if requested:
                    argv += ["--model", requested]
                argv.append(prompt)
                code, stdout, err, elapsed = run(argv, work, timeout_seconds())
                parsed = parse_claude(stdout)
            else:
                last = work / "last-message.txt"
                argv = [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-o", str(last)]
                if requested:
                    argv += ["-m", requested]
                argv.append(prompt)
                code, stdout, err, elapsed = run(argv, work, timeout_seconds())
                text = last.read_text(encoding="utf-8", errors="replace") if last.is_file() else ""
                parsed = parse_codex(stdout, text)
    except Exception as exc:  # noqa: BLE001 — absent CLI, timeout, bad flags: degrade
        return {"ok": False, "error": classify.scrub(f"{backend} не сработал: {exc}"), "requested": requested,
                "model": requested or f"{backend}/default", "seconds": round(time.monotonic() - started, 2),
                "content": "", "input_tokens": None, "output_tokens": None}
    seconds = round(parsed["duration_ms"] / 1000.0, 2) if parsed["duration_ms"] else round(float(elapsed or 0), 2)
    result = {
        "ok": code == 0 and not parsed["is_error"],
        "error": None,
        "requested": requested,
        "model": parsed["model"] or requested or f"{backend}/default",
        "seconds": seconds,
        "content": classify.scrub(parsed["content"]),
        "input_tokens": parsed["input_tokens"] or None,
        "output_tokens": parsed["output_tokens"] or None,
    }
    if not result["ok"]:
        detail = (err or stdout or "пустой ответ").strip().splitlines()
        result["error"] = classify.scrub(f"{backend} код {code}: {(detail[0] if detail else '')[:300]}")
    return result


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
