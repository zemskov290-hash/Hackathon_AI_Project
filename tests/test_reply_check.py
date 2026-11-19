"""Draft reply check: numbers need a real square-bracket id in the same sentence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_server  # noqa: E402
import reply_check  # noqa: E402
import strat  # noqa: E402

EXAMPLES = ROOT / "docs" / "examples" / "reply-check"
T04 = (EXAMPLES / "bad-contradiction.md").read_text(encoding="utf-8")
T05 = (EXAMPLES / "bad-sum.md").read_text(encoding="utf-8")


def run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("STRAT_PROJECTS_DIR", None)
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "strat.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
        timeout=30,
    )


class ReplyCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        self.root = Path(self.tmp.name)
        strat.cmd_init(type("N", (), {"project_id": "reply", "title": "Ответ", "title_file": None})())
        self.project = self.root / "reply"
        note = self.project / "sources" / "hall.md"
        note.write_text("В зале 340 человек, у стойки 120.\n", encoding="utf-8")
        strat.cmd_add_source(
            type("N", (), {
                "rest": ["reply", "sources/hall.md"],
                "kind": "own",
                "title": "Зал",
                "title_file": None,
                "reliability": "med",
            })()
        )
        strat.cmd_add_evidence(
            type("N", (), {
                "project": str(self.project),
                "etype": "fact",
                "confidence": "med",
                "source": "S001",
                "claim": "В зале 340 человек.",
                "claim_file": None,
                "quote": "В зале 340 человек, у стойки 120.",
                "quote_file": None,
                "locator": "",
                "locator_file": None,
                "date": None,
                "depends_on": "",
                "analysis": None,
                "metric": None,
                "inspect": None,
            })()
        )
        analysis = self.project / "analysis" / "A001"
        analysis.mkdir(parents=True)
        (analysis / "result.json").write_text(
            json.dumps({"metrics": {"sum_sat": "440"}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STRAT_PROJECTS_DIR", None)
        else:
            os.environ["STRAT_PROJECTS_DIR"] = self.previous
        self.tmp.cleanup()

    def test_t04_bold_numbers_need_square_citations(self) -> None:
        findings = reply_check.check_reply(self.project, T04)
        numbers = [item for item in findings if item["code"] == "reply-number"]
        self.assertGreaterEqual(len(numbers), 4)
        self.assertTrue(any(item["line"] == 1 and "340" in item["message"] for item in numbers))
        self.assertTrue(any(item["line"] == 1 and "120" in item["message"] for item in numbers))
        self.assertTrue(any(item["line"] == 3 and "340" in item["message"] for item in numbers))
        self.assertTrue(any(item["line"] == 3 and "120" in item["message"] for item in numbers))
        self.assertIn("[S…]", numbers[0]["message"])

    def test_t05_analyze_word_is_not_an_anchor(self) -> None:
        findings = reply_check.check_reply(self.project, T05)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 1)
        self.assertIn("440", findings[0]["message"])
        self.assertNotIn("reply-ref", {item["code"] for item in findings})
        fixed = "Сумма колонки sat = 440 [A001.sum_sat].\n"
        self.assertEqual(reply_check.check_reply(self.project, fixed), [])

    def test_missing_id_does_not_ground_the_number(self) -> None:
        findings = reply_check.check_reply(self.project, "Доля равна 15 [S099].\n")
        codes = {item["code"] for item in findings}
        self.assertIn("reply-ref", codes)
        self.assertIn("reply-number", codes)
        self.assertTrue(any("[S099]" in item["message"] for item in findings))
        bare_ref = reply_check.check_reply(self.project, "Смотри [E099], цифр здесь нет.\n")
        self.assertEqual([item["code"] for item in bare_ref], ["reply-ref"])

    def test_previous_sentence_does_not_cover_the_next(self) -> None:
        text = "Доля названа в [E001].\nДальше отдельно написано 399 без ссылки в этом предложении.\n"
        findings = reply_check.check_reply(self.project, text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 2)
        self.assertIn("399", findings[0]["message"])

    def test_reply_without_numbers_passes(self) -> None:
        text = (EXAMPLES / "ok-no-numbers.md").read_text(encoding="utf-8")
        self.assertEqual(reply_check.check_reply(self.project, text), [])
        cited = "В зале 340 человек, у стойки 120 [S001].\n"
        self.assertEqual(reply_check.check_reply(self.project, cited), [])

    def test_cli_exit_codes_match_the_fixtures(self) -> None:
        env = {"STRAT_PROJECTS_DIR": self.tmp.name}
        bad = run_cli("reply-check", "reply", "--file", str(EXAMPLES / "bad-sum.md"), env=env)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("ОШИБКИ", bad.stdout)
        self.assertIn("строка 1", bad.stdout)
        self.assertIn("440", bad.stdout)
        passed = run_cli("reply-check", "reply", "--file", str(EXAMPLES / "ok-no-numbers.md"), env=env)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertIn("OK", passed.stdout)
        self.assertIn("Чисел без ссылки нет.", passed.stdout)

    def test_mcp_tool_reports_the_t05_line(self) -> None:
        result = mcp_server.handle_reply_check({"project": "reply", "text": T05})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["errors"], 1)
        self.assertIn("440", result["content"][0]["text"])
        names = {item["name"] for item in mcp_server.build_tools()}
        self.assertIn("strat_reply_check", names)
        empty = mcp_server.handle_reply_check(
            {"project": "reply", "text": "Чисел в этом ответе нет, только отсылка к файлу.\n"}
        )
        self.assertFalse(empty["isError"])
        self.assertEqual(empty["structuredContent"]["errors"], 0)


class CommittedProjectReplyTests(unittest.TestCase):
    def test_example_and_demo_analysis_commands(self) -> None:
        bad = run_cli(
            "reply-check",
            "projects/_example",
            "--file",
            "docs/examples/reply-check/bad-contradiction.md",
            env={},
        )
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("340", bad.stdout)
        self.assertIn("120", bad.stdout)
        price = run_cli(
            "reply-check",
            "projects/_example",
            "--file",
            "docs/examples/reply-check/ok-price.md",
            env={},
        )
        self.assertEqual(price.returncode, 0, price.stdout + price.stderr)
        self.assertIn("OK", price.stdout)
        missing = run_cli(
            "reply-check",
            "projects/_example",
            "--file",
            "docs/examples/reply-check/bad-missing-id.md",
            env={},
        )
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn("[S099]", missing.stdout)
        mean = run_cli(
            "reply-check",
            "projects/demo-analysis",
            "--file",
            "docs/examples/reply-check/ok-mean.md",
            env={},
        )
        self.assertEqual(mean.returncode, 0, mean.stdout + mean.stderr)
        bare = run_cli(
            "reply-check",
            "projects/demo-analysis",
            "--file",
            "docs/examples/reply-check/bad-sum.md",
            env={},
        )
        self.assertEqual(bare.returncode, 1, bare.stdout + bare.stderr)
        self.assertIn("440", bare.stdout)
