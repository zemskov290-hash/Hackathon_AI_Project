"""Synthetic and test evidence must not be written up as confirmation or as the only basis."""

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

import evidence_honesty  # noqa: E402

SOURCES = [
    {"id": "S001", "url_or_path": "sources/report.md", "kind": "doc", "reliability": "med"},
    {"id": "S002", "url_or_path": "sources/synth.md", "kind": "synthetic", "reliability": "low"},
    {"id": "S003", "url_or_path": "sources/news.md", "kind": "own", "reliability": "low", "test": True},
]
EVIDENCE = [
    {"id": "E001", "claim": "Доля выросла", "type": "fact", "source": "S001", "confidence": "high", "status": "active"},
    {"id": "E002", "claim": "Синтетическая реплика: дорого", "type": "synthetic", "source": "S002", "confidence": "low", "status": "active"},
    {"id": "E003", "claim": "Синтетическая реплика: неудобно", "type": "synthetic", "source": "S002", "confidence": "low", "status": "active"},
    {"id": "E004", "claim": "Тестовое: задержка 30 минут", "type": "fact", "source": "S003", "confidence": "med", "status": "active"},
]


def run_review(texts: dict, evidence=None, sources=None, project: Path | None = None) -> list[tuple[str, str, bool]]:
    found: list[tuple[str, str, bool]] = []

    def add(code: str, message: str, warning: bool = False) -> None:
        found.append((code, message, warning))

    with tempfile.TemporaryDirectory() as tmp:
        evidence_honesty.review(
            project or Path(tmp), texts, sources or SOURCES, evidence or EVIDENCE, add
        )
    return found


def codes(found) -> list[str]:
    return [code for code, _message, warning in found if not warning]


class WordingTests(unittest.TestCase):
    def test_confirming_synthetic_is_an_error(self) -> None:
        found = run_review({"situation.md": "Синтетическая реплика [E002] подтверждает гипотезу о цене.\n"})
        self.assertIn("synthetic-wording", codes(found))

    def test_negated_or_real_evidence_is_fine(self) -> None:
        texts = {
            "situation.md": (
                "Синтетическая реплика [E002] **не** подтверждает гипотезу, а иллюстрирует вопрос.\n"
                "Синтетика [E003] без подтверждения рынком. Отчёт [E001] подтверждает рост.\n"
            )
        }
        self.assertEqual(codes(run_review(texts)), [])

    def test_future_test_criteria_are_not_assertions(self) -> None:
        texts = {"options.md": "Что подтвердит: рост визитов [E002]. Чтобы доказать спрос, нужен пилот [E003].\n"}
        self.assertEqual(codes(run_review(texts)), [])

    def test_wholly_synthetic_project_wording_is_a_warning(self) -> None:
        texts = {"strategy.md": "Синтетическая реплика [E002] подтверждает цену, и [E003] тоже.\n"}
        found = run_review(texts, sources=[SOURCES[1]], evidence=EVIDENCE[1:3])
        self.assertIn(("synthetic-wording", True), [(code, warning) for code, _m, warning in found])
        self.assertEqual(codes(found), [])

    def test_only_the_sentence_with_the_synthetic_cite_counts(self) -> None:
        texts = {"situation.md": "Отчёт [E001] подтверждает рост. Синтетическая реплика [E002] иллюстрирует цену.\n"}
        self.assertEqual(codes(run_review(texts)), [])

    def test_synthetic_claim_with_confirmation_is_an_error(self) -> None:
        evidence = [*EVIDENCE, {"id": "E005", "claim": "Синтетическая реплика доказывает спрос", "type": "synthetic", "source": "S002", "status": "active"}]
        self.assertIn("synthetic-claim", codes(run_review({}, evidence=evidence)))


class BasisTests(unittest.TestCase):
    def test_decision_on_synthetic_only_is_an_error(self) -> None:
        texts = {"decisions.md": "## D1. Запуск\n\n- evidence: синтетические [E002], [E003]\n\n## D2. Другое\n\n- evidence: [E001]\n"}
        found = codes(run_review(texts))
        self.assertEqual(found.count("synthetic-basis"), 1)

    def test_rehearsal_marker_and_mixed_basis_pass(self) -> None:
        texts = {
            "decisions.md": (
                "## D1. Репетиция\n\n- kind: rehearsal\n- evidence: синтетические [E002]\n\n"
                "## D2. Запуск\n\n- evidence: [E001], синтетическая [E003]\n"
            )
        }
        self.assertEqual(codes(run_review(texts)), [])

    def test_wholly_synthetic_project_only_warns(self) -> None:
        texts = {"decisions.md": "## D1. Учебное\n\n- evidence: синтетическая [E002]\n"}
        found = run_review(texts, sources=[SOURCES[1]], evidence=EVIDENCE[1:3])
        self.assertEqual([(code, warning) for code, _m, warning in found], [("synthetic-basis", True)])

    def test_strategy_on_synthetic_only_is_an_error(self) -> None:
        texts = {"strategy.md": "Синтетическая реплика [E002] иллюстрирует.\n"}
        self.assertIn("synthetic-basis", codes(run_review(texts)))


class TestSourceTests(unittest.TestCase):
    def test_test_source_needs_label_and_not_high(self) -> None:
        evidence = [*EVIDENCE[:3], {**EVIDENCE[3], "confidence": "high"}]
        found = codes(run_review({"decisions.md": "- причина: задержка 30 минут [E004]\n"}, evidence=evidence))
        self.assertIn("test-label", found)
        self.assertIn("test-source", found)
        found = codes(run_review({"decisions.md": "- причина: тестовое сведение о задержке [E004]\n"}))
        self.assertEqual(found, [])

    def test_unmarked_self_described_test_file_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "sources").mkdir()
            (project / "sources" / "note.md").write_text(
                "# Сведение\n\nТестовое новое сведение (не из брифа).\n", encoding="utf-8"
            )
            sources = [{"id": "S001", "url_or_path": "sources/note.md", "kind": "own", "reliability": "high"}]
            found: list = []
            evidence_honesty.review(project, {}, sources, [], lambda c, m, warning=False: found.append((c, warning)))
        self.assertEqual(found, [("test-source-unmarked", True)])


class InterviewWordingTests(unittest.TestCase):
    def test_synthetic_interview_links_never_say_confirms(self) -> None:
        import interview

        hypotheses = [{"id": "E001", "claim": "Клиенты забывают оплатить второй номер", "type": "hypothesis"}]
        transcript = "Я часто забываю оплатить второй номер, и роутер отключается."
        real = interview.analyse(transcript, hypotheses, synthetic=False)
        synthetic = interview.analyse(transcript, hypotheses, synthetic=True)
        self.assertEqual(real["links"][0]["relation"], "подтверждает")
        section = synthetic["markdown"].split("## Гипотезы", 1)[1]
        self.assertFalse(evidence_honesty.confirms(section), section)
        self.assertFalse(
            evidence_honesty.confirms(interview.relation_text("подтверждает", synthetic=True))
        )


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

    def test_add_source_test_flag_and_confidence_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "STRAT_PROJECTS_DIR": tmp, "PYTHONIOENCODING": "utf-8"}
            self.assertEqual(self.run_cli(env, "init", "probe", "--title", "Probe").returncode, 0)
            project = Path(tmp) / "probe"
            (project / "sources" / "note.md").write_text("Задержка до 30 минут.\n", encoding="utf-8")
            high = self.run_cli(env, "add-source", "probe", "sources/note.md", "--test", "--reliability", "high")
            self.assertNotEqual(high.returncode, 0)
            added = self.run_cli(env, "add-source", "probe", "sources/note.md", "--test")
            self.assertEqual(added.returncode, 0, added.stderr)
            row = json.loads((project / "sources.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIs(row["test"], True)
            self.assertEqual(row["reliability"], "low")
            common = ["add-evidence", "probe", "--type", "fact", "--source", "S001", "--claim", "Тестовое: задержка", "--quote", "Задержка до 30 минут."]
            refused = self.run_cli(env, *common, "--confidence", "high")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("тестовый источник", refused.stderr + refused.stdout)
            accepted = self.run_cli(env, *common, "--confidence", "med")
            self.assertEqual(accepted.returncode, 0, accepted.stderr)


if __name__ == "__main__":
    unittest.main()
