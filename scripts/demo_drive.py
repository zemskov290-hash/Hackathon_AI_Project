#!/usr/bin/env python3
"""Run a scripted multi-turn demo of the strategist through cursor-agent.

Each turn is a file ``t1.txt``, ``t2.txt``, … in the turns folder: one
message of the manager. Every turn runs ``agent -p --output-format
stream-json`` in the repository, its log goes to ``turnN.jsonl`` and to
``projects/<project>/agent-logs/`` so ``strat.py cost`` reads real tokens.

The driver stops at the first failed turn and never runs the next one
silently. A turn has failed when the client exits with a non-zero code, when
its log has no closing ``result`` record or ends with an error, or when it
runs longer than ``--turn-timeout``. The reason is printed (a usage limit is
named as such) together with the command that resumes from that turn.

On Windows ``agent.cmd`` passes through cmd.exe, which cuts an argument at the
first newline. The launcher is resolved to node + index.js like the bench
does; when that is not possible the prompt lines are joined with spaces.

Before the first turn ``--expect-account`` compares ``agent status`` with the
account the run must use and stops on a mismatch. The driver never logs in or
out.

Usage:
  python scripts/demo_drive.py --project family-connect-10 --turns C:/runs/demo-run10
        [--start 3] [--end 9] [--model composer-2.5-fast] [--expect-account me@example.com]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import usage_log  # noqa: E402

AGENT_FLAGS = ["-p", "--force", "--trust", "--approve-mcps", "--output-format", "stream-json"]
LIMIT_RE = re.compile(
    r"usage limit|limit reached|not included in your|rate.?limit|quota|insufficient credits|too many requests",
    re.IGNORECASE,
)
AUTH_RE = re.compile(r"not logged in|login required|unauthori[sz]ed|authentication", re.IGNORECASE)


class DriveError(Exception):
    pass


def turn_files(folder: Path) -> dict[int, Path]:
    found = {}
    for path in folder.glob("t*.txt"):
        match = re.fullmatch(r"t(\d+)", path.stem)
        if match:
            found[int(match.group(1))] = path
    return dict(sorted(found.items()))


def launcher(explicit: str | None) -> tuple[list[str], dict[str, str], bool]:
    """(argv prefix, extra env, prompt must be one line)."""
    spec = explicit or os.environ.get("STRAT_AGENT_CMD") or shutil.which("agent")
    if not spec:
        raise DriveError("Не найден agent. Нужен cursor-agent в PATH, --agent-cmd или STRAT_AGENT_CMD.")
    path = Path(spec)
    if not path.is_file():
        found = shutil.which(spec)
        if not found:
            raise DriveError(f"Не найден файл агента: {spec}")
        path = Path(found)
    if path.suffix.lower() == ".py":
        return [sys.executable, str(path)], {}, False
    if path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
        try:
            import bench

            prefix, env, _version = bench.resolve_cursor_launcher(path)
            if prefix and Path(prefix[0]).suffix.lower() not in {".cmd", ".ps1", ".bat"}:
                return prefix, env, False
        except Exception:  # noqa: BLE001 - fall back to cmd.exe with a one-line prompt
            pass
        return [str(path)], {}, True
    return [str(path)], {}, False


def one_line(prompt: str) -> str:
    return " ".join(line.strip() for line in prompt.splitlines() if line.strip())


def child_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in extra.items():
        env.setdefault(key, value)
    return env


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        import classify

        classify.kill_process_tree(proc.pid)
    except Exception:  # noqa: BLE001
        proc.kill()


def run_turn(argv: list[str], env: dict[str, str], log_path: Path, timeout: float) -> tuple[int | None, float]:
    """Exit code (None on timeout) and seconds. Output streams into the log file."""
    started = time.time()
    kwargs: dict = {"cwd": str(ROOT), "env": env, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with log_path.open("wb") as handle:
        proc = subprocess.Popen(argv, stdout=handle, **kwargs)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            code = None
    return code, time.time() - started


def failure_reason(code: int | None, log_path: Path, timeout: float) -> str | None:
    """Why this turn must stop the run, or None when it finished properly."""
    if code is None:
        return f"ход не закончился за {int(timeout)} с (завис или очень долгий), процесс остановлен"
    _turns, problem = usage_log.scan_turn_log(log_path)
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if code == 0 and problem is None:
        return None
    if code == 0 and problem and problem.startswith("после итоговой записи"):
        return None
    detail = []
    if code != 0:
        detail.append(f"код выхода {code}")
    if problem:
        detail.append(problem)
    tail = text[-2000:]
    if LIMIT_RE.search(tail):
        detail.append("клиент сообщил о лимите использования")
    elif AUTH_RE.search(tail):
        detail.append("клиент сообщил о входе или авторизации")
    return "; ".join(detail)


def snapshot(project: str, command: str, dest: Path) -> int:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "strat.py"), command, project],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    dest.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc.returncode


def check_account(prefix: list[str], env: dict[str, str], expected: str, out: Path) -> None:
    proc = subprocess.run(
        [*prefix, "status"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    (out / "agent-status.txt").write_text(text, encoding="utf-8")
    if expected.casefold() not in text.casefold():
        raise DriveError(
            f"agent status не показывает аккаунт {expected}. Ходы не запускались. "
            "Вход и выход драйвер не выполняет: перелогиньтесь сами. Вывод в agent-status.txt."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сценарный прогон демо через cursor-agent с остановкой на сбое.")
    parser.add_argument("--project", required=True, help="id проекта в projects/")
    parser.add_argument("--turns", required=True, help="папка с t1.txt … tN.txt; туда же пишутся журналы")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=0, help="последний ход (по умолчанию — последний файл)")
    parser.add_argument("--model", default=os.environ.get("STRAT_DEMO_MODEL", ""))
    parser.add_argument("--agent-cmd", default=None)
    parser.add_argument("--expect-account", default="", help="почта, которую должен показать agent status")
    parser.add_argument("--turn-timeout", type=float, default=float(os.environ.get("STRAT_DEMO_TURN_TIMEOUT", "3600")))
    args = parser.parse_args(argv)

    out = Path(args.turns).resolve()
    files = turn_files(out)
    if not files:
        print(f"В {out} нет файлов t1.txt …", file=sys.stderr)
        return 2
    end = args.end or max(files)
    order = [number for number in files if args.start <= number <= end]
    missing = [number for number in range(args.start, end + 1) if number not in files]
    if missing:
        print("Нет файлов ходов: " + ", ".join(f"t{n}.txt" for n in missing), file=sys.stderr)
        return 2
    try:
        prefix, extra, needs_one_line = launcher(args.agent_cmd)
        env = child_env(extra)
        if args.expect_account:
            check_account(prefix, env, args.expect_account, out)
    except (DriveError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"DEMO-STOPPED до первого хода: {exc}", flush=True)
        return 2
    timing = out / "timing.txt"
    with timing.open("a", encoding="utf-8") as handle:
        handle.write(f"--- start turn{args.start} {datetime.now().isoformat(timespec='seconds')} model={args.model or 'default'}\n")
    logs = ROOT / "projects" / args.project / "agent-logs"
    failed: tuple[int, str] | None = None
    for number in order:
        prompt = files[number].read_text(encoding="utf-8").strip()
        if needs_one_line:
            prompt = one_line(prompt)
        command = [*prefix, *AGENT_FLAGS]
        if args.model:
            command += ["--model", args.model]
        command.append(prompt)
        log_path = out / f"turn{number}.jsonl"
        code, seconds = run_turn(command, env, log_path, args.turn_timeout)
        if logs.parent.is_dir():
            logs.mkdir(exist_ok=True)
            shutil.copyfile(log_path, logs / log_path.name)
        reason = failure_reason(code, log_path, args.turn_timeout)
        with timing.open("a", encoding="utf-8") as handle:
            handle.write(
                f"turn{number} rc={code if code is not None else 'timeout'} {int(seconds)}s "
                f"model={args.model or 'default'}" + (f" FAILED: {reason}" if reason else "") + "\n"
            )
        if (ROOT / "projects" / args.project / "state.json").is_file():
            snapshot(args.project, "status", out / f"status{number}.txt")
        print(f"turn{number} done rc={code} {int(seconds)}s" + (f" — {reason}" if reason else ""), flush=True)
        if reason:
            failed = (number, reason)
            break
    if (ROOT / "projects" / args.project / "state.json").is_file():
        snapshot(args.project, "check", out / "check.txt")
        snapshot(args.project, "cost", out / "cost.txt")
    if failed:
        number, reason = failed
        resume = f"python scripts/demo_drive.py --project {args.project} --turns {out} --start {number}"
        if args.model:
            resume += f" --model {args.model}"
        print(f"STOPPED at turn{number}: {reason}. Журнал: turn{number}.jsonl. Продолжить: {resume}", flush=True)
        print("DEMO-STOPPED", flush=True)
        return 1
    print("DEMO-DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
