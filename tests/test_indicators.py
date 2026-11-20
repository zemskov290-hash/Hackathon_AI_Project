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

    def cli(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "strat.py"), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            input=input,
            timeout=30,
        )

    def init_project(self, project: str, source: str) -> Path:
        created = self.cli("init", project, "--title", "Синтетический бриф")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        switched = self.cli("active", project)
        self.assertEqual(switched.returncode, 0, switched.stdout + switched.stderr)
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

    def _strip_template(self, folder: Path, name: str) -> None:
        path = folder / name
        text = path.read_text(encoding="utf-8").replace("<!-- template: empty -->", "")
        path.write_text(text, encoding="utf-8")

    def test_generated_tables_pass_check_without_hand_edits(self) -> None:
        registries = [
            (
                "registry-percent",
                "Показатель старт если ничего не менять порог\nотток 20 % 15 % 8 %\nвыручка 6 млн 10 млн 14 млн\n",
                [
                    ("отток", "старт", "20", "%", "меньше", "контекст"),
                    ("отток", "если ничего не менять", "15", "%", "меньше", "база"),
                    ("отток", "порог", "8", "%", "меньше", "цель"),
                    ("выручка", "старт", "6", "млн", "больше", "контекст"),
                    ("выручка", "если ничего не менять", "10", "млн", "больше", "база"),
                    ("выручка", "порог", "14", "млн", "больше", "цель"),
                ],
                [
                    ("отток", "с программой", "7", "%", "отток с программой"),
                    ("выручка", "с программой", "14", "млн", "выручка с программой"),
                ],
            ),
            (
                "registry-points",
                "Показатель база сдвига цель сдвига\nдоля возврата 4 п.п. 9 п.п.\n",
                [
                    ("доля возврата", "база сдвига", "4", "п.п.", "больше", "база"),
                    ("доля возврата", "цель сдвига", "9", "п.п.", "больше", "цель"),
                ],
                [("доля возврата", "после шага", "9", "п.п.", "сдвиг после шага")],
            ),
        ]
        for project, source, cells, forecasts in registries:
            folder = self.init_project(project, source)
            added = self.cli(
                "indicators",
                "add",
                project,
                "--json",
                "-",
                input=json.dumps(
                    [
                        {
                            "indicator": indicator,
                            "scenario": scenario,
                            "value": value,
                            "unit": unit,
                            "direction": direction,
                            "role": role,
                            "source": "S001",
                        }
                        for indicator, scenario, value, unit, direction, role in cells
                    ],
                    ensure_ascii=False,
                ),
            )
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            self.assertFalse(list(folder.glob("_tmp_*")))
            assumed = self.cli(
                "add-assumption",
                project,
                "--json",
                "-",
                input=json.dumps(
                    [
                        {
                            "kind": "прогноз",
                            "name": name,
                            "value": value,
                            "unit": unit,
                            "indicator": indicator,
                            "scenario": scenario,
                        }
                        for indicator, scenario, value, unit, name in forecasts
                    ],
                    ensure_ascii=False,
                ),
            )
            self.assertEqual(assumed.returncode, 0, assumed.stdout + assumed.stderr)
            for name in ("brief.md", "strategy.md", "deliverable.md"):
                placed = self.cli("indicators", "table", project, "--into", name)
                self.assertEqual(placed.returncode, 0, placed.stdout + placed.stderr)
                if name != "deliverable.md":
                    self._strip_template(folder, name)
            found = [item for item in strat.check_project(folder) if not item.get("warning")]
            self.assertEqual(found, [], "\n".join(item["code"] + ": " + item["message"] for item in found))
            strategy = (folder / "strategy.md").read_text(encoding="utf-8")
            self.assertIn("прогноз:", strategy)
            self.assertNotIn("прогноз  ", strategy)
            edited = strategy.replace(" [U001]", " прогноз [U001]", 1)
            (folder / "strategy.md").write_text(edited, encoding="utf-8")
            self.assertIn("indicators-table", codes(folder))

    def test_column_header_is_the_kind_marker_for_its_cells(self) -> None:
        project = "header-mark"
        folder = self.init_project(project, "Показатель шаг\nдоля 12 %\n")
        added = self.add_cell(
            project,
            indicator="доля",
            scenario="шаг",
            value="12",
            unit="%",
            direction="больше",
            role="база",
        )
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        forecast = self.cli(
            "add-assumption",
            project,
            "--kind",
            "прогноз",
            "--name",
            "доля после",
            "--value",
            "12",
            "--unit",
            "%",
            "--indicator",
            "доля",
            "--scenario",
            "после",
        )
        self.assertEqual(forecast.returncode, 0, forecast.stdout + forecast.stderr)
        (folder / "deliverable.md").write_text(
            "| показатель | прогноз: после |\n| --- | --- |\n| доля | 12 % [U001] |\n",
            encoding="utf-8",
        )
        self.assertNotIn("number-not-in-source", codes(folder))
        (folder / "deliverable.md").write_text(
            "| показатель | итог |\n| --- | --- |\n| доля | 12 % [U001] |\n",
            encoding="utf-8",
        )
        self.assertIn("number-not-in-source", codes(folder))

    def test_temp_file_inside_project_is_a_warning_and_json_outside_is_removed(self) -> None:
        project = "scratch"
        folder = self.init_project(project, "Показатель шаг\nдоля 4 %\n")
        (folder / "_tmp_fields.txt").write_text("черновик\n", encoding="utf-8")
        warned = [item for item in strat.check_project(folder) if item["code"] == "temp-file"]
        self.assertTrue(warned)
        self.assertTrue(all(item.get("warning") for item in warned))
        outside = self.projects / "_tmp_outside.json"
        outside.write_text(
            json.dumps(
                {
                    "indicator": "доля",
                    "scenario": "шаг",
                    "value": "4",
                    "unit": "%",
                    "direction": "больше",
                    "role": "база",
                    "source": "S001",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        added = self.cli("indicators", "add", project, "--json", str(outside))
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertFalse(outside.exists())
        self.assertIn("удалён", added.stdout)
        inside = folder / "cells.json"
        inside.write_text("{}", encoding="utf-8")
        refused = self.cli("indicators", "add", project, "--json", str(inside))
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(inside.exists())
        self.assertIn("--json -", refused.stdout)


if __name__ == "__main__":
    unittest.main()
