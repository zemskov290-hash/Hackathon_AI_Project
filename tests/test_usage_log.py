"""Time from log timestamps and tokens from the agent's own turn logs, not from self-report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import usage_log  # noqa: E402

CURSOR_TURN = [
    {"type": "system", "subtype": "init", "model": "Composer Test"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    {
        "type": "result",
        "subtype": "success",
        "duration_ms": 120000,
        "is_error": False,
        "usage": {"inputTokens": 1000, "outputTokens": 200, "cacheReadTokens": 5000, "cacheWriteTokens": 0},
    },
]
CLAUDE_TURN = [
    {"type": "system", "subtype": "init", "model": "claude-test"},
    {
        "type": "result",
        "duration_ms": 60000,
        "total_cost_usd": 0.25,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 40,
        },
    },
]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class JournalMinutesTests(unittest.TestCase):
    def test_gaps_go_to_the_later_stage_and_pauses_are_skipped(self) -> None:
        entries = [
            {"ts": "2026-11-21T10:00:00Z", "stage": 1},
            {"ts": "2026-11-21T10:06:00Z", "stage": 1},
            {"ts": "2026-11-21T10:10:00Z", "stage": 3},
            {"ts": "2026-11-21T14:00:00Z", "stage": 3},
            {"ts": "2026-11-21T14:03:00Z", "stage": 4},
            {"stage": 5, "message": "без отметки времени"},
        ]
        clock = usage_log.journal_minutes(entries)
        self.assertAlmostEqual(clock["by_stage"][1], 6.0)
        self.assertAlmostEqual(clock["by_stage"][3], 4.0)
        self.assertAlmostEqual(clock["by_stage"][4], 3.0)
        self.assertAlmostEqual(clock["total"], 13.0)
        self.assertEqual(clock["pauses"], 1)
        self.assertNotIn(5, clock["by_stage"])

    def test_out_of_order_records_are_sorted(self) -> None:
        entries = [
            {"ts": "2026-11-21T10:05:00Z", "stage": 2},
            {"ts": "2026-11-21T10:00:00Z", "stage": 1},
        ]
        clock = usage_log.journal_minutes(entries)
        self.assertAlmostEqual(clock["by_stage"][2], 5.0)


class AgentUsageTests(unittest.TestCase):
    def test_cursor_and_claude_formats_are_summed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_jsonl(folder / "turn1.jsonl", CURSOR_TURN)
            write_jsonl(folder / "turn2.jsonl", CLAUDE_TURN)
            (folder / "notes.txt").write_text("not a log", encoding="utf-8")
            report = usage_log.agent_usage(None, [str(folder)])
        self.assertEqual(len(report["turns"]), 2)
        self.assertEqual(report["total"]["input"], 1010)
        self.assertEqual(report["total"]["output"], 220)
        self.assertEqual(report["total"]["cache_read"], 5030)
        self.assertEqual(report["total"]["cache_write"], 40)
        self.assertEqual(report["total"]["duration_ms"], 180000)
        self.assertAlmostEqual(report["cost_usd"], 0.25)
        self.assertEqual(report["models"], ["Composer Test", "claude-test"])
        text = "\n".join(usage_log.format_agent_usage(report))
        self.assertIn("вход 1 010", text)
        self.assertIn("3 мин", text)

    def test_no_logs_says_unknown(self) -> None:
        text = "\n".join(usage_log.format_agent_usage(usage_log.agent_usage(None, [])))
        self.assertIn("неизвестно", text)
        self.assertIn("--agent-log", text)


class CliTests(unittest.TestCase):
    def run_cli(self, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "strat.py"), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )

    def test_cost_and_status_use_timestamps_and_agent_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "STRAT_PROJECTS_DIR": tmp, "PYTHONIOENCODING": "utf-8"}
            env.pop("STRAT_AGENT_LOGS", None)
            created = self.run_cli(env, "init", "clock", "--title", "Clock")
            self.assertEqual(created.returncode, 0, created.stderr)
            project = Path(tmp) / "clock"
            entries = [
                {"ts": "2026-11-21T10:00:00Z", "stage": 1, "message": "a", "model": "cli"},
                {"ts": "2026-11-21T10:07:00Z", "stage": 1, "message": "b", "model": "cli"},
                {"ts": "2026-11-21T10:10:00Z", "stage": 2, "message": "c", "model": "cli"},
            ]
            write_jsonl(project / "log.jsonl", entries)
            status = self.run_cli(env, "status", "clock")
            self.assertIn("по отметкам времени журнала 10 мин", status.stdout)
            self.assertIn("Токены не учитываются в режиме подписки IDE", status.stdout)
            cost = self.run_cli(env, "cost", "clock")
            self.assertIn("Этап 1 бриф: минуты 0; по журналу 7", cost.stdout)
            self.assertIn("Этап 2", cost.stdout)
            self.assertIn("неизвестно", cost.stdout)
            logs = Path(tmp) / "turns"
            logs.mkdir()
            write_jsonl(logs / "turn1.jsonl", CURSOR_TURN)
            cost = self.run_cli(env, "cost", "clock", "--agent-log", str(logs))
            self.assertIn("Фактический расход по журналам агента (1 ход.", cost.stdout)
            self.assertIn("Токены по журналам агента: вход 1 000", cost.stdout)
            (project / "agent-logs").mkdir()
            write_jsonl(project / "agent-logs" / "t.jsonl", CURSOR_TURN)
            status = self.run_cli(env, "status", "clock")
            self.assertIn("Токены по журналам агента", status.stdout)
            check = self.run_cli(env, "check", "clock")
            self.assertNotIn("materials-folder", check.stdout)
            (project / "agent-logs" / "brief.pdf").write_bytes(b"%PDF")
            check = self.run_cli(env, "check", "clock")
            self.assertIn("materials-folder", check.stdout)


if __name__ == "__main__":
    unittest.main()
