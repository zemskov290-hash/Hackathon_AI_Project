"""A cut-off turn log is reported, and stages logged in one burst share the time before it."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import usage_log  # noqa: E402


class CostLogTests(unittest.TestCase):
    def test_truncated_turn_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "turn1.jsonl").write_text(
                json.dumps({"type": "system", "model": "m"}) + "\n"
                + json.dumps({"type": "result", "subtype": "success", "usage": {"inputTokens": 5, "outputTokens": 1}})
                + "\n",
                encoding="utf-8",
            )
            (folder / "turn2.jsonl").write_text(
                json.dumps({"type": "system", "model": "m"}) + "\n"
                + json.dumps({"type": "assistant", "message": {}}) + "\n",
                encoding="utf-8",
            )
            report = usage_log.agent_usage(None, [str(folder)])
            self.assertEqual(len(report["turns"]), 1)
            lines = usage_log.format_agent_usage(report)
            self.assertTrue(any("turn2.jsonl" in line and "result" in line for line in lines), lines)
            self.assertFalse(any("turn1.jsonl" in line for line in lines))

    def test_burst_of_stages_shares_the_gap(self) -> None:
        entries = [
            {"ts": "2026-11-22T10:00:00Z", "stage": 5},
            {"ts": "2026-11-22T10:09:00Z", "stage": 5},
            {"ts": "2026-11-22T10:09:00Z", "stage": 6},
            {"ts": "2026-11-22T10:09:01Z", "stage": 7},
            {"ts": "2026-11-22T10:12:00Z", "stage": 8},
        ]
        clock = usage_log.journal_minutes(entries)
        self.assertAlmostEqual(clock["by_stage"][5], 3.0, places=1)
        self.assertAlmostEqual(clock["by_stage"][6], 3.0, places=1)
        self.assertAlmostEqual(clock["by_stage"][7], 3.0, places=1)
        self.assertAlmostEqual(clock["by_stage"][8], 3.0, places=1)
        self.assertAlmostEqual(clock["total"], 12.0, places=1)


if __name__ == "__main__":
    unittest.main()
