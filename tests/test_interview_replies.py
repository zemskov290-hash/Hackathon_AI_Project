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
