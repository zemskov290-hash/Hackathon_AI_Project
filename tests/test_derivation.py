"""Qualitative premises are not «1 флаг», and forecast inputs carry a derivation, not only a reference."""

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

import derivation_check  # noqa: E402

STRAT = ROOT / "scripts" / "strat.py"


class Collector:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, bool]] = []

    def __call__(self, code: str, message: str, warning: bool = False) -> None:
        self.items.append((code, message, warning))

    def errors(self, code: str) -> list[str]:
        return [m for c, m, w in self.items if c == code and not w]

    def warnings(self, code: str) -> list[str]:
        return [m for c, m, w in self.items if c == code and w]


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["STRAT_PROJECTS_DIR"] = self.tmp.name
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        self.cli("init", "gaps", "--title", "Пробелы")
        self.project = Path(self.tmp.name) / "gaps"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STRAT_PROJECTS_DIR", None)
        else:
            os.environ["STRAT_PROJECTS_DIR"] = self.previous
        self.tmp.cleanup()

    def cli(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(STRAT), *args],
            cwd=ROOT,
            env=self.env,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def assumption(self, row: dict) -> subprocess.CompletedProcess[str]:
        return self.cli("add-assumption", "gaps", "--json", "-", input=json.dumps(row, ensure_ascii=False))


class QualitativeAssumptionTests(ProjectCase):
    def test_flag_unit_is_refused_and_qualitative_is_recorded(self) -> None:
        refused = self.assumption({"kind": "допущение", "name": "Пилот без биллинга", "value": "1", "unit": "флаг"})
        self.assertEqual(refused.returncode, 1)
        self.assertIn("--qualitative", refused.stderr + refused.stdout)
        ok = self.assumption({"kind": "допущение", "name": "Пилот без перестройки биллинга", "qualitative": True})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        row = json.loads((self.project / "assumptions.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(row["qualitative"])
        self.assertEqual(row["value"], "Пилот без перестройки биллинга")

    def test_qualitative_forecast_is_refused(self) -> None:
        refused = self.assumption({"kind": "прогноз", "name": "Рост", "qualitative": True})
        self.assertEqual(refused.returncode, 1)

    def test_qualitative_reference_needs_no_number(self) -> None:
        self.assumption({"kind": "допущение", "name": "Пилот без перестройки биллинга", "qualitative": True})
        found = Collector()
        import source_check

        text = "Пилот идёт в кабинете, допущение [U001]: без перестройки биллинга.\n"
        source_check._review_assumption_visibility(
            "strategy.md", text, source_check.load_assumptions(self.project)[0], found
        )
        self.assertEqual(found.items, [])

    def test_legacy_flag_rows_are_qualitative(self) -> None:
        import source_check

        self.assertTrue(source_check.is_qualitative({"value": "1", "unit": "флаг"}))
        self.assertIsNone(source_check.assumption_quantity({"value": "1", "unit": "флаг"}))
        self.assertFalse(source_check.is_qualitative({"value": "6000", "unit": "мес."}))


class DerivationTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.assumption({"kind": "прогноз", "name": "Подключённые группы", "value": "10000", "unit": "групп"})

    def test_derivation_arithmetic_is_checked_on_write(self) -> None:
        wrong = self.assumption(
            {"kind": "допущение", "name": "Оплаченные месяцы", "value": "6000", "unit": "мес.",
             "derivation": "`[U001] * 0,5 = 6000`"}
        )
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("не сходится", wrong.stderr + wrong.stdout)
        bare = self.assumption(
            {"kind": "допущение", "name": "Оплаченные месяцы", "value": "6000", "unit": "мес.",
             "derivation": "оценка команды"}
        )
        self.assertEqual(bare.returncode, 1)
        ok = self.assumption(
            {"kind": "допущение", "name": "Оплаченные месяцы", "value": "6000", "unit": "мес.",
             "derivation": "`[U001] * 0,6 = 6000`"}
        )
        self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)

    def test_input_without_derivation_is_a_warning(self) -> None:
        self.assumption({"kind": "допущение", "name": "Оплаченные месяцы", "value": "6000", "unit": "мес."})
        self.assumption({"kind": "допущение", "name": "Вклад", "value": "3", "unit": "млн"})
        texts = {"deliverable.md": "Вклад: допущение 3 млн [U003], расчёт `[U002]*500 = 3000000`.\n"}
        found = Collector()
        derivation_check.review(self.project, texts, found)
        warned = found.warnings("derivation")
        self.assertTrue(any("[U002]" in m for m in warned), warned)
        self.assertFalse(any("[U003]" in m for m in warned), warned)

    def test_derived_input_and_rates_pass(self) -> None:
        self.assumption(
            {"kind": "допущение", "name": "Оплаченные месяцы", "value": "6000", "unit": "мес.",
             "derivation": "`[U001] * 0,6 = 6000`"}
        )
        self.assumption({"kind": "допущение", "name": "Доля удержания", "value": "60", "unit": "%"})
        texts = {"deliverable.md": "Расчёт `[U002]*500 = 3000000` и доля `[U003] * 2 = 120 %`.\n"}
        found = Collector()
        derivation_check.review(self.project, texts, found)
        warned = [m for m in found.warnings("derivation") if "[U002]" in m or "[U003]" in m]
        self.assertEqual(warned, [])
        # U001 feeds U002's derivation and itself has none.
        self.assertTrue(any("[U001]" in m for m in found.warnings("derivation")))


if __name__ == "__main__":
    unittest.main()
