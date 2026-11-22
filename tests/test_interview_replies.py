"""Only the respondent's replies become interview evidence: no headers, disclaimers or questions."""

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

import interview  # noqa: E402

STRAT = ROOT / "scripts" / "strat.py"


class InterviewJunkTests(unittest.TestCase):
    TRANSCRIPT = (
        "Синтетическая расшифровка. Не является реальным интервью.\n\n"
        "Роль респондента: организатор семейной группы.\n"
        "Дата: 2026-11-22.\n"
        "Персона собрана из брифа и свидетельств сбора; вымышленная.\n\n"
        "И: Как вы узнаёте, что у кого-то кончается интернет?\n"
        "Р: Чаще всего когда ребёнок пишет, что всё встало, мне это очень неудобно.\n"
        "И: Помогла бы строчка о списании?\n"
        "Р: Да, если коротко и без жаргона, иначе перестану читать такие сообщения.\n"
    )

    def test_only_respondent_replies_become_quotes(self) -> None:
        cleaned, _removed = interview.anonymize(self.TRANSCRIPT)
        self.assertIn("Дата: 2026-11-22", cleaned)
        result = interview.analyse(interview.SYNTHETIC_SENTENCE + "\n\n" + cleaned, [], synthetic=True)
        quotes = [item["text"] for item in result["quotes"]]
        self.assertTrue(quotes)
        for text in quotes:
            self.assertNotIn("Синтетическ", text)
            self.assertNotIn("реальным", text)
            self.assertNotIn("Роль", text)
            self.assertNotIn("Как вы узнаёте", text)

    def test_bold_callouts_are_not_speakers(self) -> None:
        transcript = (
            "**Важно:** это не разговор с реальным человеком. Персона «[имя]» собрана из учебного брифа. "
            "Нельзя цитировать как факт.\n\n"
            "**Профиль (вымышленный):** женщина около 45 лет.\n\n"
            "**В:** Как вы в последний раз создавали семейную группу?\n\n"
            "**[имя]:** Создала группу в приложении, мама тянула с приглашением неделю.\n\n"
            "**В:** Как часто заходите в раздел?\n\n"
            "**[имя]:** Раз в месяц, когда приходит счёт или кто-то жалуется на списание.\n"
        )
        quotes = [item["text"] for item in interview.analyse(transcript, [], synthetic=True)["quotes"]]
        self.assertEqual(len(quotes), 2, quotes)
        self.assertTrue(quotes[0].startswith("Создала группу"))
        for text in quotes:
            self.assertNotIn("реальным", text)
            self.assertNotIn("Персона", text)
            self.assertNotIn("*", text)

    FIXTURES = ROOT / "tests" / "fixtures" / "interviews"

    def replies(self, name: str) -> list[str]:
        text = (self.FIXTURES / name).read_text(encoding="utf-8")
        return interview.sentences_of("\n".join(interview.reply_lines(text)))

    def test_run10_transcripts_show_pains_of_every_kind(self) -> None:
        # Synthetic transcripts written by the agent in demo run 10. Before the
        # broader patterns no reply in them counted as a pain.
        kinds: dict[str, list[str]] = {}
        for name in ("run10-synthetic-I001.md", "run10-synthetic-I002.md"):
            for reply in self.replies(name):
                for kind in interview.pain_kinds(reply):
                    kinds.setdefault(kind, []).append(reply)
        self.assertEqual(set(kinds), {"страх", "непонимание", "деньги", "доверие", "усилия"})
        self.assertTrue(any("страшно" in item for item in kinds["страх"]))
        self.assertTrue(any("Не понял" in item for item in kinds["непонимание"]))
        self.assertTrue(any("списали" in item for item in kinds["деньги"]))
        self.assertTrue(any("приложению — не очень" in item for item in kinds["доверие"]))
        self.assertTrue(any("отключу" in item for item in kinds["усилия"]))

    def test_run10_analysis_names_barrier_kinds(self) -> None:
        text = (self.FIXTURES / "run10-synthetic-I002.md").read_text(encoding="utf-8")
        result = interview.analyse(text, [], synthetic=True)
        self.assertTrue(result["pains"])
        self.assertIn("Барьеры по типам", result["markdown"])
        self.assertNotIn("Боль не дописана", result["markdown"])
        for item in result["quotes"]:
            self.assertNotIn("реальным", item["text"])

    def test_key_quotes_cover_different_kinds(self) -> None:
        text = (self.FIXTURES / "run10-synthetic-I001.md").read_text(encoding="utf-8")
        result = interview.analyse(text, [], synthetic=True)
        covered = set()
        for item in result["quotes"]:
            covered.update(item["kinds"])
        self.assertGreaterEqual(len(covered), 3)

    def test_negated_or_neutral_reply_is_not_a_pain(self) -> None:
        for text in (
            "Всё понятно и удобно, недорого.",
            "Не страшно, справлюсь сама.",
            "Мне нравится, что видно остаток.",
            "Посмотреть, сколько гигабайт у кого.",
        ):
            self.assertEqual(interview.pain_kinds(text), [], text)

    def test_synthetic_records_are_not_hypotheses(self) -> None:
        rows = [
            {"id": "E001", "type": "synthetic", "status": "active", "claim": "ребёнок пишет что всё встало"},
            {"id": "E002", "type": "hypothesis", "status": "active", "claim": "организатор узнаёт от ребёнка, что всё встало"},
        ]
        result = interview.analyse(self.TRANSCRIPT, rows, synthetic=True)
        linked = {item["hypothesis_id"] for item in result["links"]}
        self.assertNotIn("E001", linked)

    def test_accept_writes_no_service_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["STRAT_PROJECTS_DIR"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            run = lambda *a: subprocess.run(  # noqa: E731
                [sys.executable, str(STRAT), *a], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8"
            )
            self.assertEqual(run("init", "iv", "--title", "Интервью").returncode, 0)
            source = Path(tmp) / "synthetic.txt"
            source.write_text(self.TRANSCRIPT, encoding="utf-8")
            accepted = run("interview", "accept", "iv", "--file", str(source), "--synthetic")
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            rows = [json.loads(line) for line in (Path(tmp) / "iv" / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(rows)
            for row in rows:
                self.assertNotIn("Не является реальным интервью", row["quote"])
                self.assertNotIn("Синтетическая расшифровка", row["quote"])
                self.assertFalse(row["quote"].startswith("Роль"))


if __name__ == "__main__":
    unittest.main()
