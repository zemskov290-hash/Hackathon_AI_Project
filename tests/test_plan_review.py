"""Plan as a reader sees it: economics of the recommended option, the communication barrier,
flag markers, assumptions in facts and placeholders."""

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

import plan_review  # noqa: E402
import strat  # noqa: E402

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


OPTIONS = """# Варианты

### O1. Оставить как есть
<!-- role: status-quo -->

Ничего не меняем.

Экономика: вложений нет, эффект на прибыль — база.

Возражение: разрыв остаётся.

### O2. Пилот
<!-- role: change -->

Запустить пилот.

{economics}
Возражение: дорого.

### O3. Закрыть направление
<!-- role: close -->

Остановить.

Возражение: теряем базу.
"""


class EconomicsTests(unittest.TestCase):
    def review(self, economics: str, strategy: str = "", choice: str = "O2") -> Collector:
        found = Collector()
        texts = {"options.md": OPTIONS.format(economics=economics), "strategy.md": strategy}
        state = {"checkpoints": {"options": {"choice": choice}}}
        plan_review.review_economics(texts, state, found, strategy_required=bool(strategy))
        return found

    def test_recommended_option_without_economics_is_an_error(self) -> None:
        found = self.review("")
        self.assertTrue(found.errors("economics"))
        self.assertIn("O2", found.errors("economics")[0])

    def test_investment_without_comparison_is_an_error(self) -> None:
        found = self.review("Экономика: capex 8 млн, эффект на прибыль 3 млн в год.\n")
        self.assertTrue(any("сравнение" in m for m in found.errors("economics")))

    def test_payback_in_words_only_is_an_error(self) -> None:
        found = self.review(
            "Экономика: затраты — capex 12 млн [U004], opex 2 млн в год [U006]; эффект на прибыль 3 млн "
            "в год [U005]; окупаемость дольше одного планового года.\n"
        )
        self.assertTrue(any("числом" in m for m in found.errors("economics")))

    def test_payback_with_years_passes(self) -> None:
        found = self.review(
            "Экономика: затраты — capex 12 млн [U004], opex 2 млн в год [U006]; эффект на прибыль 3 млн "
            "в год [U005]; окупаемость 12 лет при чистом эффекте 1 млн в год.\n"
        )
        self.assertEqual(found.items, [])

    def test_full_economics_line_passes(self) -> None:
        found = self.review(
            "Экономика: затраты — capex 9 млн и opex 1,2 млн в год; эффект на прибыль 3 млн в год; "
            "окупаемость `9 / (3 - 1,2) = 5` лет.\n"
        )
        self.assertEqual(found.items, [])

    def test_unchosen_option_is_not_forced(self) -> None:
        found = self.review("", choice="O1")
        self.assertEqual(found.errors("economics"), [])

    def test_strategy_section_needs_cost_profit_and_payback(self) -> None:
        strategy = (
            "## Продуктовая стратегия\n\n### Экономика: затраты и эффект\n\n"
            "Пилот увеличит валовую прибыль, это главное, и команда в него верит всем сердцем.\n"
        )
        found = self.review("Экономика: вложений нет, эффект на прибыль — база.\n", strategy)
        messages = found.errors("economics")
        self.assertTrue(any("strategy.md" in m and "затраты" in m for m in messages))

    def test_economics_and_barrier_are_required_subsections(self) -> None:
        self.assertIn(plan_review.ECONOMICS_HEADING, strat.PRODUCT_SUBSECTIONS)
        self.assertEqual(strat.COMM_SUBSECTIONS[0], plan_review.BARRIER_HEADING)
        import grounding

        self.assertEqual(tuple(grounding.PRODUCT_SUBSECTIONS), tuple(strat.PRODUCT_SUBSECTIONS))
        self.assertEqual(tuple(grounding.COMM_SUBSECTIONS), tuple(strat.COMM_SUBSECTIONS))


class BarrierTests(unittest.TestCase):
    def test_section_without_barrier_is_a_warning(self) -> None:
        found = Collector()
        strategy = (
            "## Коммуникационная стратегия\n\n### Барьер понимания и выбора\n\n"
            "Наша аудитория широкая, сообщение будет ярким и запоминающимся для всех людей.\n"
        )
        plan_review.review_barrier(strategy, found)
        self.assertTrue(found.warnings("strategy"))

    def test_named_barrier_passes(self) -> None:
        found = Collector()
        strategy = (
            "## Коммуникационная стратегия\n\n### Барьер понимания и выбора\n\n"
            "Организатор не замечает, что пакет кончается, и не понимает списаний.\n"
        )
        plan_review.review_barrier(strategy, found)
        self.assertEqual(found.items, [])


class FlagMarkerTests(unittest.TestCase):
    def test_flag_in_promise_is_an_error_elsewhere_a_warning(self) -> None:
        strategy = (
            "## Продуктовая стратегия\n\nВ рамках допущения 1 флаг [U002] делаем кабинет.\n\n"
            "## Обещания\n\n<!-- promise: P1 -->\nОрганизатор увидит риск раньше при допущении 1 флаг [U002].\n"
            "<!-- product: PP1 -->\n"
        )
        found = Collector()
        plan_review.review_flag_markers(Path(tempfile.gettempdir()), {"strategy.md": strategy}, found)
        self.assertEqual(len(found.errors("flag-marker")), 1)
        self.assertEqual(len(found.warnings("flag-marker")), 1)

    def test_plain_number_one_is_not_a_flag(self) -> None:
        found = Collector()
        plan_review.review_flag_markers(Path(tempfile.gettempdir()), {"strategy.md": "Один регион, 1 пилот, 10 флагов.\n"}, found)
        self.assertEqual(found.items, [])


class FactsAssumptionTests(ProjectCase):
    def test_assumption_in_facts_section_is_a_warning(self) -> None:
        (self.project / "validation.md").write_text(
            "# План проверки\n\n## Факты\n\n"
            "- Пилот идёт без перестройки биллинга, допущение [U001], это согласуется с болью [E001].\n",
            encoding="utf-8",
        )
        findings = strat.check_project(self.project)
        warned = [f for f in findings if f["code"] == "facts" and f["warning"] and "[U001]" in f["message"]]
        self.assertTrue(warned, findings)


class PlaceholderTests(unittest.TestCase):
    def review(self, text: str) -> Collector:
        found = Collector()
        plan_review.review_placeholders(Path(tempfile.gettempdir()), {"deliverable.md": text}, found)
        return found

    def test_pointer_row_with_empty_cells(self) -> None:
        found = self.review("| Показатель | База | Цель |\n| --- | --- | --- |\n| См. таблицу ниже | | |\n")
        self.assertTrue(found.warnings("placeholder"))

    def test_todo_is_an_error_but_quoted_word_is_not(self) -> None:
        self.assertTrue(self.review("Риски: TODO расписать.\n").errors("placeholder"))
        self.assertEqual(self.review("Заглушка вроде «TODO» не проходит проверку.\n").items, [])

    def test_pointer_to_missing_table(self) -> None:
        self.assertTrue(self.review("Цифры — см. таблицу ниже.\n\nДальше текст без таблиц.\n").warnings("placeholder"))
        self.assertEqual(
            self.review("Цифры — см. таблицу ниже.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n").items, []
        )


if __name__ == "__main__":
    unittest.main()
