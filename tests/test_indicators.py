"""Indicator cells are (name, scenario, value), not a bag of numbers from the source.

The fixtures are synthetic and use different column orders. They are not a
particular product brief.
"""

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

import pdf_text  # noqa: E402
import strat  # noqa: E402

LAYOUT_A = """# Сводка

Показатель | старт | если ничего не менять | порог
отток | 20 % | 15 % | 8 %
выручка | 6 млн | 10 млн | 14 млн
"""

LAYOUT_B = """# Сводка службы

Показатель | цель службы | оценка сейчас | инерция
простой линии | 3 ч | 9 ч | 7 ч
"""


def codes(project: Path) -> list[str]:
    return [item["code"] for item in strat.check_project(project) if not item.get("warning")]


class IndicatorScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.env = {**os.environ, "STRAT_PROJECTS_DIR": str(self.projects)}
        self.addCleanup(self.tmp.cleanup)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "strat.py"), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            timeout=30,
        )

    def init_project(self, project: str, source: str) -> Path:
        created = self.cli("init", project, "--title", "Синтетический бриф")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        folder = self.projects / project
        (folder / "sources" / "table.md").write_text(source, encoding="utf-8")
        added = self.cli("add-source", project, "sources/table.md", "--kind", "own", "--title", "Таблица")
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        return folder

    def add_cell(self, project: str, **kwargs: str) -> subprocess.CompletedProcess[str]:
        args = ["indicators", "add", project, "--source", "S001"]
        for key, value in kwargs.items():
            args.extend([f"--{key}", value])
        return self.cli(*args)

    def untie_template(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8").replace("<!-- template: empty -->", "")
        path.write_text(text, encoding="utf-8")

    def test_worse_forecast_and_swapped_column_on_first_layout(self) -> None:
        project = "layout-a"
        folder = self.init_project(project, LAYOUT_A)
        cells = [
            ("отток", "старт", "20", "%", "меньше", "контекст"),
            ("отток", "если ничего не менять", "15", "%", "меньше", "база"),
            ("отток", "порог", "8", "%", "меньше", "цель"),
            ("выручка", "старт", "6", "млн", "больше", "контекст"),
            ("выручка", "если ничего не менять", "10", "млн", "больше", "база"),
            ("выручка", "порог", "14", "млн", "больше", "цель"),
        ]
        for indicator, scenario, value, unit, direction, role in cells:
            added = self.add_cell(
                project,
                indicator=indicator,
                scenario=scenario,
                value=value,
                unit=unit,
                direction=direction,
                role=role,
            )
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        missing_column = self.add_cell(
            project,
            indicator="отток",
            scenario="нет такого столбца",
            value="15",
            unit="%",
            direction="меньше",
            role="контекст",
        )
        self.assertNotEqual(missing_column.returncode, 0)
        self.assertIn("дословно", missing_column.stdout)
        churn = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "отток с программой",
            "--value",
            "16",
            "--unit",
            "%",
            "--indicator",
            "отток",
            "--scenario",
            "с программой",
        )
        self.assertEqual(churn.returncode, 0, churn.stdout + churn.stderr)
        revenue = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "выручка с программой",
            "--value",
            "12",
            "--unit",
            "млн",
            "--indicator",
            "выручка",
            "--scenario",
            "с программой",
        )
        self.assertEqual(revenue.returncode, 0, revenue.stdout + revenue.stderr)
        for name in ("brief.md", "strategy.md"):
            placed = self.cli("indicators", "table", project, "--into", name)
            self.assertEqual(placed.returncode, 0, placed.stdout + placed.stderr)
            self.untie_template(folder / name)
        strategy = folder / "strategy.md"
        strategy.write_text(
            strategy.read_text(encoding="utf-8")
            + "\nВ сценарии если ничего не менять выручка равна 6 млн.\n",
            encoding="utf-8",
        )
        found = codes(folder)
        self.assertIn("worse-than-baseline", found)
        self.assertIn("goal-miss", found)
        self.assertIn("scenario-mismatch", found)
        self.assertNotIn("indicators-table", found)

    def test_lower_is_better_with_columns_in_another_order(self) -> None:
        project = "layout-b"
        folder = self.init_project(project, LAYOUT_B)
        cells = [
            ("простой линии", "цель службы", "3", "ч", "меньше", "цель"),
            ("простой линии", "оценка сейчас", "9", "ч", "меньше", "контекст"),
            ("простой линии", "инерция", "7", "ч", "меньше", "база"),
        ]
        for indicator, scenario, value, unit, direction, role in cells:
            added = self.add_cell(
                project,
                indicator=indicator,
                scenario=scenario,
                value=value,
                unit=unit,
                direction=direction,
                role=role,
            )
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        forecast = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "простой после ремонта",
            "--value",
            "6",
            "--unit",
            "ч",
            "--indicator",
            "простой линии",
            "--scenario",
            "после ремонта",
        )
        self.assertEqual(forecast.returncode, 0, forecast.stdout + forecast.stderr)
        placed = self.cli("indicators", "table", project, "--into", "strategy.md")
        self.assertEqual(placed.returncode, 0, placed.stdout + placed.stderr)
        strategy = folder / "strategy.md"
        self.untie_template(strategy)
        strategy.write_text(
            strategy.read_text(encoding="utf-8")
            + "\nВ сценарии инерция простой линии составляет 9 ч.\n",
            encoding="utf-8",
        )
        found = codes(folder)
        self.assertNotIn("worse-than-baseline", found)
        self.assertIn("goal-miss", found)
        self.assertIn("scenario-mismatch", found)
        goal_id = next(
            json.loads(line)["id"]
            for line in (folder / "indicators.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["role"] == "goal"
        )
        strategy.write_text(
            strategy.read_text(encoding="utf-8").replace(
                "В сценарии инерция простой линии составляет 9 ч.",
                "В сценарии инерция простой линии составляет 7 ч.",
            )
            + f"\nПрогноз не достигает цели [{goal_id}].\n",
            encoding="utf-8",
        )
        found = codes(folder)
        self.assertNotIn("scenario-mismatch", found)
        self.assertNotIn("goal-miss", found)
        self.assertNotIn("worse-than-baseline", found)

    def test_duplicate_unit_swap_and_visible_points(self) -> None:
        project = "points"
        folder = self.init_project(project, "Показатель сдвиг\nдоля возврата | сдвиг\n8 п.п.\n")
        added = self.add_cell(
            project,
            indicator="доля возврата",
            scenario="сдвиг",
            value="8",
            unit="п.п.",
            direction="больше",
            role="база",
        )
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        first = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "сдвиг доли",
            "--value",
            "8",
            "--unit",
            "п.п.",
            "--indicator",
            "доля возврата",
            "--scenario",
            "после шага",
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        swapped = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "сдвиг доли",
            "--value",
            "8",
            "--unit",
            "%",
            "--indicator",
            "доля возврата",
            "--scenario",
            "эквивалент",
            "--forecast-role",
            "alternate",
            "--note",
            "для проверки",
        )
        self.assertNotEqual(swapped.returncode, 0)
        self.assertIn("проверк", swapped.stdout)
        same_value = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "сдвиг доли",
            "--value",
            "8",
            "--unit",
            "%",
            "--indicator",
            "доля возврата",
            "--scenario",
            "эквивалент",
            "--forecast-role",
            "alternate",
        )
        self.assertNotEqual(same_value.returncode, 0)
        self.assertIn("тем же значением", same_value.stdout)
        rows = [
            json.loads(line)
            for line in (folder / "assumptions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows.append(
            {
                "id": "U002",
                "kind": "прогноз",
                "name": "сдвиг доли",
                "value": "8",
                "unit": "%",
                "indicator": "доля возврата",
                "scenario": "эквивалент",
                "forecast_role": "alternate",
                "note": "как эквивалент для проверки",
                "status": "active",
            }
        )
        (folder / "assumptions.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        (folder / "deliverable.md").write_text("Прогноз 8 процентов [U001].\n", encoding="utf-8")
        found = codes(folder)
        self.assertIn("duplicate-assumption", found)
        self.assertIn("assumption-note", found)
        self.assertIn("assumption-value", found)
        (folder / "assumptions.jsonl").write_text(
            json.dumps(rows[0], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (folder / "deliverable.md").write_text("Прогноз 8 п.п. [U001].\n", encoding="utf-8")
        found = codes(folder)
        self.assertNotIn("duplicate-assumption", found)
        self.assertNotIn("assumption-note", found)
        self.assertNotIn("assumption-value", found)

    def test_positioned_glyphs_stay_in_their_columns(self) -> None:
        rows = pdf_text.rows_from_positions(
            [
                (0, 100, "Показатель"),
                (14, 100, "линии"),
                (220, 100, "сейчас"),
                (440, 100, "порог"),
                (0, 70, "простой"),
                (220, 70, "9"),
                (440, 70, "3"),
            ]
        )
        self.assertEqual(rows[0][0], "Показатель линии")
        self.assertEqual(rows[0][1], "сейчас")
        self.assertEqual(rows[0][2], "порог")
        self.assertEqual(rows[1], ["простой", "9", "3"])
        table = pdf_text.markdown_table(rows)
        self.assertIn("| простой | 9 | 3 |", table)
        self.assertNotIn("93", table)


if __name__ == "__main__":
    unittest.main()
