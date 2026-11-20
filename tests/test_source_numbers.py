"""Numbers cited to a source must occur in that source. Assumptions are [U…], not [A….metric]."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import source_check  # noqa: E402
import strat  # noqa: E402

BRIEF = """# Бриф

Цель активации — не ниже 60%. В обращении 500 руб. и 20 000 обращений в год.
Года в брифе нет.
"""

SOURCE = """# Бриф заказчика

Цель активации — не ниже 60%. Стоимость контакта 500 руб.
Объём поддержки: 20 000 обращений.
Доля активации сейчас 42%.
"""


def codes(project: Path) -> list[str]:
    return [item["code"] for item in strat.check_project(project) if not item.get("warning")]


def messages(project: Path, code: str) -> str:
    return "\n".join(
        item["message"] for item in strat.check_project(project) if item["code"] == code
    )


class QuantityNormalizationTests(unittest.TestCase):
    def test_spaces_comma_magnitude_and_percent_match(self) -> None:
        self.assertEqual(source_check.quantities("20 000"), source_check.quantities("20000"))
        self.assertEqual(source_check.quantities("1,2 млн"), source_check.quantities("1.2 млн"))
        self.assertEqual(source_check.quantities("1,2 млн"), source_check.quantities("1200000"))
        self.assertEqual(source_check.quantities("400 тыс."), source_check.quantities("400000"))
        self.assertEqual(source_check.quantities("2,0"), source_check.quantities("2.0"))
        self.assertEqual(source_check.quantities("2,0"), source_check.quantities("2,00"))
        self.assertEqual(source_check.quantities("60%"), source_check.quantities("60 процентов"))
        self.assertEqual(source_check.quantities("скидки 20–25%"), source_check.quantities("20% и 25%"))
        self.assertNotEqual(source_check.quantities("60%"), source_check.quantities("60"))
        self.assertNotEqual(source_check.quantities("4 000"), source_check.quantities("4"))

    def test_word_numerals_from_ten_and_scales(self) -> None:
        self.assertIn("100000", source_check.expand_number_words("эффект сто тысяч рублей"))
        self.assertIn(
            "279000000",
            source_check.expand_number_words("двести семьдесят девять млн"),
        )
        self.assertIn("60%", source_check.expand_number_words("шестьдесят процентов"))
        self.assertIn("10", source_check.expand_number_words("не меньше десять"))
        untouched = "пять процентных пунктов и просто источник"
        self.assertEqual(source_check.expand_number_words(untouched), untouched)
        self.assertEqual(
            source_check.assumption_quantity({"value": "4", "unit": "млн"}).value,
            4000000,
        )
        self.assertEqual(
            source_check.assumption_quantity({"value": "400", "unit": "тыс."}).value,
            400000,
        )
        percent = source_check.assumption_quantity({"value": "50", "unit": "%"})
        self.assertTrue(percent.percent)
        points = source_check.assumption_quantity({"value": "2", "unit": "п.п."})
        self.assertEqual(points.value, 2)
        self.assertFalse(points.percent)
        self.assertTrue(points.points)
        self.assertNotEqual(source_check.quantities("8 п.п."), source_check.quantities("8 %"))
        self.assertNotEqual(source_check.quantities("8 п.п."), source_check.quantities("8"))
        self.assertEqual(source_check.quantities("сто тысяч"), source_check.quantities("100000"))
        self.assertEqual(source_check.quantities("пять"), set())
        self.assertIn("60", source_check.expand_number_words("не ниже шестидесяти"))
        compounds = {
            "двухсот восьмидесяти пяти": "285",
            "двухсот восьмидесяти пяти млн": "285000000",
            "сорока двух": "42",
            "шестистах": "600",
            "пятистах": "500",
            "двумстам": "200",
            "пятьюстами": "500",
            "тремястами": "300",
            "восьмистах": "800",
            "девятистах": "900",
            "четырёхстах": "400",
            "семистах": "700",
            "двухстах": "200",
            "ста пятнадцати": "115",
            "сорока пяти": "45",
        }
        for phrase, digits in compounds.items():
            rendered = source_check.expand_number_words(f"цель не ниже {phrase} руб.")
            self.assertIn(digits, rendered, phrase)
            self.assertNotIn("280", rendered, phrase)


BRIEF_TABLE = (
    "Показатель Базовый год Плановый год План Оценка Без изменений Цель, не ниже "
    "Валовая прибыль за год, млн руб. 282 275 279 285 "
    "Активные семейные группы за последний месяц года 5 000 4 000 5 000 8 000 "
    "Подключённые семейные группы на конец года 7 500 6 500 8 000 12 000 "
    "Подключённые владельцы нескольких номеров на конец года 1 000 800 1 000 2 000 "
    "Группы, активированные за 14 дней, среди созданных в последнем квартале "
    "50 % 42 % 45 % 60 %"
)


class BriefTableTokenTests(unittest.TestCase):
    """The family-connect brief row is one line. Neighbours must stay separate tokens."""

    def test_table_line_keeps_each_cell(self) -> None:
        present = source_check.quantities(BRIEF_TABLE)
        values = {item.value for item in present}
        for number in (
            14,
            275,
            279,
            282,
            285,
            800,
            1000,
            2000,
            4000,
            5000,
            6500,
            7500,
            8000,
            12000,
        ):
            self.assertIn(number, values, number)
        for number in (42, 45, 50, 60):
            self.assertTrue(
                any(item.value == number and item.percent for item in present),
                number,
            )
        self.assertNotIn(282275279285, values)
        self.assertNotIn(1000800, values)
        self.assertNotIn(9000, values)
        self.assertNotIn(6200, values)
        self.assertFalse(any(item.value == 69 for item in present))

    def test_signed_thousand_is_one_number(self) -> None:
        row = (
            "| Подключённые группы, конец года | 8 000 [S001] | 9 000 [S001] "
            "| +1 000 за счёт пост-приглашения |"
        )
        found = source_check.iter_quantities(row)
        values = [item.value for item, _token in found]
        tokens = [token for _item, token in found]
        self.assertEqual(values, [8000, 9000, 1000])
        self.assertNotIn("000", tokens)
        self.assertEqual(source_check.quantities("+1 000"), source_check.quantities("1000"))
        self.assertEqual(source_check.quantities("−1 000"), source_check.quantities("-1000"))
        self.assertEqual(source_check.quantities("+1 000 000"), {source_check.Quantity(1000000)})


class SourceNumberCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        strat.cmd_init(Namespace(project_id="family", title="Связь", title_file=None))
        self.project = Path(self.tmp.name) / "family"
        note = self.project / "sources" / "brief.md"
        note.write_text(SOURCE, encoding="utf-8")
        strat.cmd_add_source(
            Namespace(
                rest=["family", "sources/brief.md"],
                kind="own",
                title="Бриф",
                title_file=None,
                reliability="med",
            )
        )
        (self.project / "brief.md").write_text(BRIEF, encoding="utf-8")
        quote = "Стоимость контакта 500 руб."
        strat.cmd_add_evidence(
            Namespace(
                project=str(self.project),
                etype="fact",
                confidence="med",
                source="S001",
                claim="Контакт стоит 500 руб.",
                claim_file=None,
                quote=quote,
                quote_file=None,
                locator="",
                locator_file=None,
                date=None,
                depends_on="",
                analysis=None,
                metric=None,
                inspect=None,
            )
        )

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STRAT_PROJECTS_DIR", None)
        else:
            os.environ["STRAT_PROJECTS_DIR"] = self.previous
        self.tmp.cleanup()

    def write_deliverable(self, text: str) -> None:
        (self.project / "deliverable.md").write_text(text, encoding="utf-8")

    def test_number_in_source_passes_for_s_and_e(self) -> None:
        self.write_deliverable(
            "# Итог\n\nКонтакт стоит 500 руб. [S001].\n\n"
            "Та же стоимость 500 руб. подтверждена свидетельством [E001].\n\n"
            "Цель не ниже 60% [S001]. Объём 20 000 обращений [S001].\n"
        )
        self.assertNotIn("number-not-in-source", codes(self.project))

    def test_family_connect_fixture_rejects_invented_numbers(self) -> None:
        self.write_deliverable(
            "# Итог\n\n"
            "| Показатель | Расчёт | Балл |\n"
            "| --- | --- | --- |\n"
            "| Экономия поддержки | 20 000 обращений × 200 руб. (допущение стоимости) [S001] | 2,0 |\n"
            "| Удержание | 4 000 доп. месяцев × 500 руб. [S001] | 2,0 |\n\n"
            "При полной стоимости человеко-месяца прогноз 400 тыс. руб. вложения 1,2 млн руб. [S001]\n\n"
            "Эффект словами: сто тысяч рублей экономии [S001].\n"
            "Рынок: двести семьдесят девять млн абонентов [S001].\n"
        )
        blob = messages(self.project, "number-not-in-source")
        self.assertIn("200", blob)
        self.assertIn("4000", blob.replace(" ", ""))
        self.assertIn("400", blob)
        self.assertIn("допущение", blob)
        self.assertIn("прогноз", blob)
        self.assertIn("100000", blob)
        self.assertIn("279000000", blob)
        self.assertNotIn("число 500", blob)
        self.assertNotIn("число 60%", blob)

    def test_assumption_citation_accepts_marked_number(self) -> None:
        value = self.project / "value.txt"
        name = self.project / "name.txt"
        value.write_text("200\n", encoding="utf-8")
        name.write_text("стоимость обращения\n", encoding="utf-8")
        added = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "strat.py"),
                "add-assumption",
                "family",
                "--kind",
                "допущение",
                "--value-file",
                str(value),
                "--name-file",
                str(name),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "STRAT_PROJECTS_DIR": self.tmp.name},
            timeout=30,
        )
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertIn("U001", added.stdout)
        self.write_deliverable(
            "Экономия: 20 000 обращений × 200 руб. (допущение стоимости) [S001] [U001].\n"
        )
        self.assertNotIn("number-not-in-source", codes(self.project))
        self.write_deliverable(
            "Экономия: 20 000 обращений × 200 руб. (допущение стоимости) [S001].\n"
        )
        self.assertIn("number-not-in-source", codes(self.project))

    def test_forecast_needs_assumption_not_the_brief(self) -> None:
        self.write_deliverable(
            "При полной стоимости человеко-месяца прогноз 400 тыс. руб. "
            "вложения 1,2 млн руб. [S001]\n"
        )
        self.assertIn("number-not-in-source", codes(self.project))
        for value, uname, kind in (
            ("400 тыс.", "ставка человека", "прогноз"),
            ("1,2 млн", "вложения", "прогноз"),
        ):
            strat.cmd_add_assumption(
                Namespace(
                    project="family",
                    kind=kind,
                    value=value,
                    value_file=None,
                    name=uname,
                    name_file=None,
                    unit="руб.",
                    unit_file=None,
                    note="",
                    note_file=None,
                )
            )
        self.write_deliverable(
            "При полной стоимости человеко-месяца прогноз 400 тыс. руб. [U001] "
            "вложения 1,2 млн руб. [U002]\n"
        )
        self.assertNotIn("number-not-in-source", codes(self.project))

    def test_formula_checks_result_and_operands(self) -> None:
        strat.cmd_add_assumption(
            Namespace(
                project="family",
                kind="допущение",
                value="200",
                value_file=None,
                name="ставка",
                name_file=None,
                unit="руб.",
                unit_file=None,
                note="",
                note_file=None,
            )
        )
        self.write_deliverable(
            "Эффект считается так: `20 000 * 200 = 4 000 000` [S001] [U001], это допущение ставки.\n"
        )
        self.assertNotIn("number-not-in-source", codes(self.project))
        self.write_deliverable(
            "Эффект считается так: `20 000 * 200 = 1 000` [S001] [U001], это допущение ставки.\n"
        )
        self.assertIn("не равен", messages(self.project, "number-not-in-source"))
        self.write_deliverable(
            "Эффект считается так: `20 000 * 300 = 6 000 000` [S001].\n"
        )
        self.assertIn("операнд", messages(self.project, "number-not-in-source"))

    def test_word_number_without_citation_is_visible_to_ungrounded_check(self) -> None:
        situation = (
            "# Ситуация\n\n## Факты\n\n"
            "В брифе контакт стоит 500 руб. [E001].\n\n"
            "## Противоречия\n\n"
            "Потери оцениваются в сто тысяч.\n"
        )
        (self.project / "situation.md").write_text(situation, encoding="utf-8")
        blob = messages(self.project, "ungrounded-number")
        self.assertIn("100000", blob)
        fixed = situation.replace("сто тысяч.", "сто тысяч [S001].")
        (self.project / "situation.md").write_text(fixed, encoding="utf-8")
        self.assertIn("number-not-in-source", codes(self.project))

    def test_analysis_anchor_is_not_an_assumption(self) -> None:
        folder = self.project / "analysis" / "A001"
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(
            json.dumps({"metrics": {"mean_price": "350"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.write_deliverable("Средняя цена 350 [A001.mean_price].\n")
        blob = messages(self.project, "number-not-in-source")
        self.assertNotIn("350", blob)

    def test_inconsistent_marked_quantity(self) -> None:
        (self.project / "strategy.md").write_text(
            "# Стратегия\n\nЭффект на валовую прибыль [qty:vp-year=+4 млн].\n",
            encoding="utf-8",
        )
        self.write_deliverable(
            "Итог раздела 3.3: эффект [qty:vp-year=+3,0 млн].\n\n"
            "Окупаемость: эффект [qty:vp-year=+2,4 млн].\n"
        )
        blob = messages(self.project, "inconsistent-value")
        self.assertIn("vp-year", blob)
        self.assertIn("strategy.md", blob)
        self.assertIn("deliverable.md", blob)
        self.write_deliverable("Итог: эффект [qty:vp-year=4,0 млн].\n")
        self.assertNotIn("inconsistent-value", codes(self.project))
        self.write_deliverable("В таблице +4 млн, ниже по тексту +2,4 без метки.\n")
        self.assertNotIn("inconsistent-value", codes(self.project))

    def test_year_must_be_in_source_brief_or_confirmed_date(self) -> None:
        (self.project / "strategy.md").write_text(
            "# Стратегия\n\nПросьба к руководству на конец Q1 2026.\n\n"
            "К концу Q2 2026 нужен отчёт.\n",
            encoding="utf-8",
        )
        state = json.loads((self.project / "state.json").read_text(encoding="utf-8"))
        state["created_at"] = "2026-11-20T00:00:00Z"
        state["updated_at"] = "2026-11-20T00:00:00Z"
        (self.project / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.assertIn("year-not-in-source", codes(self.project))
        self.assertIn("2026", messages(self.project, "year-not-in-source"))
        (self.project / "brief.md").write_text(BRIEF + "\nСрок человека: конец 2026 года.\n", encoding="utf-8")
        self.assertNotIn("year-not-in-source", codes(self.project))

    def test_confirmed_date_allows_year_that_timestamps_do_not(self) -> None:
        (self.project / "brief.md").write_text("# Бриф\n\nГода нет.\n", encoding="utf-8")
        (self.project / "strategy.md").write_text(
            "# Стратегия\n\nПересмотр 2027-04-01.\n",
            encoding="utf-8",
        )
        self.assertIn("2027", messages(self.project, "year-not-in-source"))
        noted = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "strat.py"),
                "confirm-date",
                "family",
                "2027-04-01",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "STRAT_PROJECTS_DIR": self.tmp.name},
            timeout=30,
        )
        self.assertEqual(noted.returncode, 0, noted.stdout + noted.stderr)
        self.assertNotIn("year-not-in-source", codes(self.project))

    def test_review_date_does_not_license_the_year_elsewhere(self) -> None:
        (self.project / "brief.md").write_text("# Бриф\n\nГода нет.\n", encoding="utf-8")
        (self.project / "decisions.md").write_text(
            "# Решения\n\n## D1. Пересмотр\n\n- review_date: 2026-12-01\n",
            encoding="utf-8",
        )
        (self.project / "strategy.md").write_text(
            "# Стратегия\n\nК концу Q2 2026 нужен отчёт.\n",
            encoding="utf-8",
        )
        blob = messages(self.project, "year-not-in-source")
        self.assertIn("strategy.md", blob)
        self.assertNotIn("decisions.md", blob)

    def test_year_in_source_is_allowed(self) -> None:
        (self.project / "sources" / "brief.md").write_text(
            SOURCE + "\nСрок решения: 2024 год.\n",
            encoding="utf-8",
        )
        (self.project / "decisions.md").write_text(
            "# Решения\n\nСрок из источника — 2024 год [S001].\n",
            encoding="utf-8",
        )
        self.assertNotIn("year-not-in-source", codes(self.project))

    def test_brief_table_false_positives_and_real_misses(self) -> None:
        note = self.project / "sources" / "brief.md"
        note.write_text(BRIEF_TABLE + "\n", encoding="utf-8")
        self.write_deliverable(
            "База сорок два процента, план сорок пять, цель не ниже шестидесяти [S001].\n"
            "Оценка 275 и прогноз 279 уже стоят в таблице брифа [S001].\n"
            "Владельцев 800 и 2 000 [S001].\n"
            "Доля `5000 / 8000 = 62,5 %` [S001].\n"
            "Прогноз 9 000 групп и 6 200 подключений [S001].\n"
            "Активация 69 % [S001].\n"
            "Эффект +4 млн [S001].\n"
            "Чужой операнд `2 * 5000 = 10000` [S001].\n"
        )
        (self.project / "strategy.md").write_text(
            "# Стратегия\n\nПросьба к руководству на конец Q1 2026.\n",
            encoding="utf-8",
        )
        blob = messages(self.project, "number-not-in-source")
        self.assertNotIn("число 45", blob)
        self.assertNotIn("число 42%", blob)
        self.assertNotIn("число 275", blob)
        self.assertNotIn("число 279", blob)
        self.assertNotIn("число 800", blob)
        self.assertNotIn("62", blob)
        flat = blob.replace(" ", "")
        self.assertIn("9000", flat)
        self.assertIn("6200", flat)
        self.assertIn("69%", blob.replace(" ", ""))
        self.assertIn("4млн", flat)
        self.assertIn("операнд", blob)
        self.assertIn("2026", messages(self.project, "year-not-in-source"))

    def _write_assumptions(self, rows: list[dict]) -> None:
        payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
        (self.project / "assumptions.jsonl").write_text(payload, encoding="utf-8")

    def test_assumption_arithmetic_rejects_two_plus_two_minus_one_as_four(self) -> None:
        rows = [
            {"id": "U001", "kind": "прогноз", "name": "удержание", "value": "4000", "unit": "", "note": "", "status": "active"},
            {"id": "U002", "kind": "прогноз", "name": "обращения", "value": "10000", "unit": "", "note": "", "status": "active"},
            {"id": "U003", "kind": "прогноз", "name": "ставка", "value": "200", "unit": "", "note": "", "status": "active"},
            {"id": "U004", "kind": "прогноз", "name": "расходы", "value": "1", "unit": "млн", "note": "", "status": "active"},
            {"id": "U005", "kind": "прогноз", "name": "итог", "value": "4", "unit": "млн", "note": "", "status": "active"},
        ]
        self._write_assumptions(rows)
        formula = "`[U001]*500 + [U002]*[U003] - [U004] = [U005]`"
        wrong = (
            "Прогноз: удержание 4000 [U001], обращения 10000 [U002], ставка 200 [U003], "
            "расходы 1 млн [U004], итог 4 млн [U005], расчёт "
            + formula
            + " [S001].\n"
        )
        self.write_deliverable(wrong)
        self.assertIn("assumption-arithmetic", codes(self.project))
        self.assertIn("3000000", messages(self.project, "assumption-arithmetic").replace(" ", ""))
        rows[-1]["value"] = "3"
        self._write_assumptions(rows)
        self.write_deliverable(wrong.replace("итог 4 млн", "итог 3 млн"))
        found = codes(self.project)
        self.assertNotIn("assumption-arithmetic", found)
        self.assertNotIn("assumption-value", found)

    def test_assumption_citation_must_show_its_registry_value(self) -> None:
        self._write_assumptions(
            [
                {
                    "id": "U001",
                    "kind": "прогноз",
                    "name": "итог",
                    "value": "4",
                    "unit": "млн",
                    "note": "",
                    "status": "active",
                }
            ]
        )
        self.write_deliverable("Прогноз [U001].\n")
        hidden = messages(self.project, "assumption-value")
        self.assertIn("U001", hidden)
        self.write_deliverable("Прогноз 9 млн [U001].\n")
        mismatch = messages(self.project, "assumption-value")
        self.assertIn("U001", mismatch)
        self.write_deliverable("Прогноз 4 млн руб. [U001].\n")
        self.assertNotIn("assumption-value", codes(self.project))
        self.write_deliverable("Прогноз 9 000 [S001].\n")
        self.assertIn("number-not-in-source", codes(self.project))

    def test_assumption_value_message_prints_the_unit_once(self) -> None:
        self._write_assumptions(
            [
                {
                    "id": "U001",
                    "kind": "прогноз",
                    "name": "доля",
                    "value": "10 %",
                    "unit": "%",
                    "note": "",
                    "status": "active",
                },
                {
                    "id": "U002",
                    "kind": "оценка",
                    "name": "сдвиг",
                    "value": "5 п.п.",
                    "unit": "п.п.",
                    "note": "",
                    "status": "active",
                },
            ]
        )
        self.write_deliverable("Прогноз [U001]. Оценка [U002].\n")
        text = messages(self.project, "assumption-value")
        self.assertNotIn("% %", text)
        self.assertNotIn("п.п. п.п.", text)
        self.assertIn("10 %", text)
        self.assertIn("5 п.п.", text)

    def test_pdf_extract_sidecar_fills_numbers_the_pdf_layer_missed(self) -> None:
        pdf = self.project / "sources" / "brief-family-devices.pdf"
        pdf.write_bytes(_minimal_pdf("Price 500 only"))
        (self.project / "sources" / "brief-family-devices-extract.txt").write_text(
            BRIEF_TABLE + "\n",
            encoding="utf-8",
        )
        strat.cmd_add_source(
            Namespace(
                rest=["family", "sources/brief-family-devices.pdf"],
                kind="doc",
                title="PDF",
                title_file=None,
                reliability="med",
            )
        )
        self.write_deliverable("В брифе 800 владельцев [S002] и выдумка 7777 [S002].\n")
        blob = messages(self.project, "number-not-in-source")
        self.assertIn("7777", blob)
        self.assertNotIn("число 800", blob)

    def test_pdf_number_is_read_or_refused(self) -> None:
        pdf = self.project / "sources" / "note.pdf"
        pdf.write_bytes(_minimal_pdf("Price 500 only"))
        strat.cmd_add_source(
            Namespace(
                rest=["family", "sources/note.pdf"],
                kind="doc",
                title="PDF",
                title_file=None,
                reliability="med",
            )
        )
        self.write_deliverable("В PDF указано 500 [S002]. Рядом выдумка 7777 [S002].\n")
        try:
            import pypdf  # noqa: F401
        except ImportError:
            blob = messages(self.project, "number-not-in-source")
            self.assertIn("PDF", blob)
            self.assertIn("7777", blob)
            return
        blob = messages(self.project, "number-not-in-source")
        self.assertIn("7777", blob)
        self.assertNotIn("число 500", blob)


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 24 Tf 40 80 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n",
        b"4 0 obj<</Length " + str(len(stream)).encode("ascii") + b">>stream\n" + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
    ]
    body = b"%PDF-1.4\n" + b"".join(objects)
    xref_at = len(body)
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    offset = len(b"%PDF-1.4\n")
    for obj in objects:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
        offset += len(obj)
    trailer = (
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n" + str(xref_at).encode("ascii") + b"\n%%EOF\n"
    )
    return body + b"".join(xref) + trailer


if __name__ == "__main__":
    unittest.main()
