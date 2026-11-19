"""Closed-label classifier: rules, escalation, offline bench, MCP."""

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

import classify  # noqa: E402
import classify_bench  # noqa: E402
import mcp_server  # noqa: E402

STRAT = SCRIPTS / "strat.py"
FIXTURE = ROOT / "bench" / "classify" / "fixture"
SAMPLES = ROOT / "bench" / "classify" / "samples"


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in (
        "STRAT_LLM_API_KEY",
        "OPENROUTER_API_KEY",
        "STRAT_PROJECTS_DIR",
        "STRAT_MODEL_CHEAP",
        "STRAT_MODEL_STRONG",
        "STRAT_JEV_API_KEY",
        "STRAT_JEV_BASE_URL",
        "STRAT_JEV_MODEL",
        "STRAT_AGENT_CMD",
        "STRAT_CLASSIFIER_BACKEND",
        "CURSOR_API_KEY",
    ):
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(STRAT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
        timeout=30,
    )


def assert_code(result: subprocess.CompletedProcess[str], code: int = 0) -> None:
    if result.returncode != code:
        raise AssertionError(
            f"ожидался код {code}, получен {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


class RulesTests(unittest.TestCase):
    def test_fact_needs_a_citation(self) -> None:
        cited = classify.rules_classify(
            "claim-type",
            "Доля равна 0.42 [E001].",
            FIXTURE,
            classify.allowed_labels("claim-type", FIXTURE),
        )
        self.assertEqual(cited["label"], "fact")
        bare = classify.rules_classify(
            "claim-type",
            "Это уже факт: доля равна 0.42.",
            FIXTURE,
            classify.allowed_labels("claim-type", FIXTURE),
        )
        self.assertEqual(bare["label"], "hypothesis")
        self.assertLess(bare["confidence"], 0.75)

    def test_impact_stays_inside_existing_ids(self) -> None:
        chosen = classify.rules_classify(
            "impact",
            "Упоминается E099, такой записи нет.",
            FIXTURE,
            classify.allowed_labels("impact", FIXTURE),
        )
        self.assertEqual(chosen["label"], ["none"])
        named = classify.rules_classify(
            "impact",
            "Новость прямо про E001.",
            FIXTURE,
            classify.allowed_labels("impact", FIXTURE),
        )
        self.assertEqual(named["label"], ["E001"])

    def test_route_picks_a_skill(self) -> None:
        chosen = classify.rules_classify(
            "route",
            "Скачай страницу тарифа и зарегистрируй источник.",
            FIXTURE,
            classify.allowed_labels("route", FIXTURE),
        )
        self.assertEqual(chosen["label"], "03-collect")

    def test_rules_do_not_call_the_network_when_unsure(self) -> None:
        result = classify.classify(
            FIXTURE,
            "claim-type",
            "Доля продлений равна 0.42.",
            backend="rules",
        )
        self.assertEqual(result["label"], "hypothesis")
        self.assertFalse(result["escalated"])
        self.assertIn("rules", result["escalation_skipped"])
        self.assertEqual(result["estimated_cost"], "0.000000")


class EscalationTests(unittest.TestCase):
    def transport_returning(self, cheap: str, strong: str):
        def transport(_name: str, tier: str, _prompt: str):
            return cheap if tier == "cheap" else strong

        return transport

    def test_illegal_fact_is_corrected_even_if_the_strong_model_repeats_it(self) -> None:
        text = "Это уже факт: доля равна 0.42."
        result = classify.classify(
            FIXTURE,
            "claim-type",
            text,
            backend="replay",
            transport=self.transport_returning(
                json.dumps({"label": "fact", "confidence": 0.96, "rationale": "сказано факт"}, ensure_ascii=False),
                json.dumps({"label": "fact", "confidence": 0.9, "rationale": "всё ещё факт"}, ensure_ascii=False),
            ),
        )
        self.assertEqual(result["label"], "hypothesis")
        self.assertTrue(result["escalated"])
        self.assertIn("факт без ссылки", result["correction"]["reason"])
        self.assertEqual(result["correction"]["from_label"], "fact")

    def test_invalid_label_is_an_error_not_a_guess(self) -> None:
        with self.assertRaises(classify.ClassifyError):
            classify.classify(
                FIXTURE,
                "claim-type",
                "Доля равна 0.42 [E001].",
                backend="replay",
                transport=self.transport_returning("не json", "тоже не json"),
            )

    def test_strong_model_can_replace_an_invalid_cheap_answer(self) -> None:
        result = classify.classify(
            FIXTURE,
            "claim-type",
            "Гипотеза: цена не главная.",
            backend="replay",
            transport=self.transport_returning(
                "не json",
                json.dumps(
                    {"label": "hypothesis", "confidence": 0.8, "rationale": "есть слово гипотеза"},
                    ensure_ascii=False,
                ),
            ),
        )
        self.assertEqual(result["label"], "hypothesis")
        self.assertTrue(result["escalated"])
        self.assertEqual(result["backend"], "replay")

    def test_unknown_label_is_rejected(self) -> None:
        with self.assertRaises(classify.ClassifyError):
            classify.classify(
                FIXTURE,
                "claim-type",
                "Доля равна 0.42 [E001].",
                backend="replay",
                transport=self.transport_returning(
                    json.dumps({"label": "opinion", "confidence": 0.9, "rationale": "своё слово"}, ensure_ascii=False),
                    json.dumps({"label": "guess", "confidence": 0.9, "rationale": "ещё одно"}, ensure_ascii=False),
                ),
            )

    def test_low_confidence_keeps_the_cheap_label_when_strong_is_down(self) -> None:
        allowed = classify.allowed_labels("claim-type", FIXTURE)

        def transport(_name: str, tier: str, _prompt: str):
            if tier == "cheap":
                return json.dumps(
                    {"label": "fact", "confidence": 0.4, "rationale": "есть ссылка"},
                    ensure_ascii=False,
                )
            return classify._attempt_from_text(  # noqa: SLF001
                "",
                task="claim-type",
                allowed=allowed,
                backend="cursor-agent",
                model="cursor/default",
                elapsed=0,
                usage=None,
                provider_cost=None,
                transport_error="нет сильной модели",
            )

        result = classify.classify(
            FIXTURE,
            "claim-type",
            "Доля равна 0.42 [E001].",
            backend="replay",
            transport=transport,
        )
        self.assertEqual(result["label"], "fact")
        self.assertFalse(result["escalated"])
        self.assertIn("недоступен", result["escalation_skipped"])


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.env = {
            "STRAT_PROJECTS_DIR": str(self.projects),
            "STRAT_AGENT_CMD": str(self.projects / "no-agent"),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cli_rules_json_and_journal(self) -> None:
        created = run("init", "labels", "--title", "Labels", env=self.env)
        assert_code(created)
        result = run(
            "classify",
            "labels",
            "--task",
            "claim-type",
            "--input-file",
            str(SAMPLES / "fact-with-cite.txt"),
            "--backend",
            "rules",
            env=self.env,
        )
        assert_code(result)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["label"], "fact")
        self.assertIn("label", payload)
        self.assertIn("confidence", payload)
        self.assertIn("rationale", payload)
        self.assertNotIn("sk-", result.stdout)
        log = (self.projects / "labels" / "log.md").read_text(encoding="utf-8")
        self.assertIn("Классификация claim-type", log)
        self.assertIn("оценка, не счёт", log)
        self.assertIn("исправление не потребовалось", log)

    def test_cli_refuses_a_missing_cursor_agent(self) -> None:
        assert_code(run("init", "labels", "--title", "Labels", env=self.env))
        result = run(
            "classify",
            "labels",
            "--task",
            "route",
            "--input-file",
            str(SAMPLES / "route-collect.txt"),
            "--backend",
            "cursor-agent",
            env=self.env,
        )
        assert_code(result, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertNotIn("03-collect", result.stdout)

    def test_auto_without_agent_or_key_uses_rules(self) -> None:
        assert_code(run("init", "labels", "--title", "Labels", env=self.env))
        secret = "jev-secret-should-not-leak"
        result = run(
            "classify",
            "labels",
            "--task",
            "route",
            "--input-file",
            str(SAMPLES / "route-collect.txt"),
            "--backend",
            "auto",
            env={
                **self.env,
                "STRAT_JEV_API_KEY": secret,
                "STRAT_JEV_BASE_URL": "http://127.0.0.1:9/v1",
                "STRAT_JEV_MODEL": "jev-example",
            },
        )
        assert_code(result)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["backend"], "rules")
        self.assertEqual(payload["label"], "03-collect")
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_mcp_classify(self) -> None:
        assert_code(run("init", "mcp-labels", "--title", "Labels", env=self.env))
        previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = str(self.projects)
        os.environ["STRAT_AGENT_CMD"] = str(self.projects / "no-agent")
        try:
            result = mcp_server.call_tool(
                "strat_classify",
                {
                    "project": "mcp-labels",
                    "task": "route",
                    "text": "Скачай страницу тарифа и зарегистрируй источник.",
                    "backend": "rules",
                },
            )
        finally:
            if previous is None:
                os.environ.pop("STRAT_PROJECTS_DIR", None)
            else:
                os.environ["STRAT_PROJECTS_DIR"] = previous
            os.environ.pop("STRAT_AGENT_CMD", None)
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"]["classification"]["label"], "03-collect")


class OfflineBenchTests(unittest.TestCase):
    def test_summary_check_matches_the_committed_table(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "classify_bench.py"), "summary", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)
        report = classify_bench.offline_report()
        by_name = {row["name"]: row for row in report["rows"]}
        self.assertEqual(by_name["rules"]["hits"], 33)
        self.assertEqual(by_name["rules"]["total"], 36)
        self.assertEqual(by_name["pipeline"]["hits"], 34)
        self.assertGreater(by_name["pipeline"]["hits"], by_name["cheap-saved"]["hits"])
        self.assertEqual(by_name["rules"]["estimated_cost"], "0.000000")

    def test_archive_roundtrip_uses_a_temp_runs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            runs = Path(tmp) / "runs"
            work.mkdir()
            (work / "cheap.jsonl").write_text((ROOT / "bench" / "classify" / "saved" / "cheap.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
            (work / "strong.jsonl").write_text((ROOT / "bench" / "classify" / "saved" / "strong.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
            (work / "meta.json").write_text(
                json.dumps({"started_at": "2026-11-19T00-00-00Z", "cheap_model": "composer-2.5-fast", "strong_model": "default"}),
                encoding="utf-8",
            )
            code = classify_bench.main(["archive", "--work", str(work), "--runs", str(runs)])
            self.assertEqual(code, 0)
            stored = list(runs.iterdir())
            self.assertEqual(len(stored), 1)
            _markdown, payload = classify_bench.summary_text(runs)
            self.assertEqual(payload["live"]["run"], stored[0].name)
            self.assertEqual(payload["live"]["rows"][0]["hits"], 30)


if __name__ == "__main__":
    unittest.main()
