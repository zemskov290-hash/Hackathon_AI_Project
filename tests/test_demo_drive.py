"""The demo driver stops at the first failed, cut-off or hung turn and checks the account first."""

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

DRIVE = ROOT / "scripts" / "demo_drive.py"


FAKE = """import json, os, sys, time
args = sys.argv[1:]
if args and args[0] == "status":
    print("Logged in as " + os.environ.get("FAKE_ACCOUNT", "someone@example.com"))
    sys.exit(0)
prompt = args[-1]
if "ЗАВИСНИ" in prompt:
    time.sleep(30)
if "ЛИМИТ" in prompt:
    print("Error: Weekly usage limit reached. Resets in 3 days.")
    sys.exit(1)
if "ОБРЫВ" in prompt:
    print(json.dumps({"type": "system", "model": "fake"}))
    sys.exit(0)
print(json.dumps({"type": "system", "model": "fake"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                  "duration_ms": 10, "usage": {"inputTokens": 3, "outputTokens": 1}}))
"""


class DemoDriveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.fake = self.folder / "fake_agent.py"
        self.fake.write_text(FAKE, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def drive(self, turns: list[str], *extra: str, account: str = "someone@example.com") -> subprocess.CompletedProcess[str]:
        for number, text in enumerate(turns, start=1):
            (self.folder / f"t{number}.txt").write_text(text, encoding="utf-8")
        env = os.environ.copy()
        env["FAKE_ACCOUNT"] = account
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(DRIVE), "--project", "no-such-project-xyz", "--turns", str(self.folder),
             "--agent-cmd", str(self.fake), *extra],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )

    def test_all_turns_run(self) -> None:
        done = self.drive(["первый\nход", "второй"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("DEMO-DONE", done.stdout)
        self.assertTrue((self.folder / "turn2.jsonl").is_file())

    def test_stops_at_first_failure_and_names_the_limit(self) -> None:
        stopped = self.drive(["первый", "ЛИМИТ", "третий"])
        self.assertEqual(stopped.returncode, 1)
        self.assertIn("STOPPED at turn2", stopped.stdout)
        self.assertIn("лимит", stopped.stdout)
        self.assertIn("--start 2", stopped.stdout)
        self.assertFalse((self.folder / "turn3.jsonl").exists())

    def test_turn_without_result_is_a_failure(self) -> None:
        stopped = self.drive(["ОБРЫВ", "второй"])
        self.assertEqual(stopped.returncode, 1)
        self.assertIn("result", stopped.stdout)
        self.assertFalse((self.folder / "turn2.jsonl").exists())

    def test_hang_is_stopped_by_timeout(self) -> None:
        stopped = self.drive(["ЗАВИСНИ", "второй"], "--turn-timeout", "2")
        self.assertEqual(stopped.returncode, 1)
        self.assertIn("не закончился", stopped.stdout)

    def test_wrong_account_stops_before_the_first_turn(self) -> None:
        stopped = self.drive(["первый"], "--expect-account", "owner@example.com", account="other@example.com")
        self.assertEqual(stopped.returncode, 2)
        self.assertFalse((self.folder / "turn1.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
