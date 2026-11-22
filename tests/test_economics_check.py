"""Economics of an option read the same in every file; an unprofitable pick says why."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import economics_check  # noqa: E402
import source_check  # noqa: E402


class Collector:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, bool]] = []

    def __call__(self, code: str, message: str, warning: bool = False) -> None:
        self.items.append((code, message, warning))

    def errors(self, code: str) -> list[str]:
        return [m for c, m, w in self.items if c == code and not w]

    def warnings(self, code: str) -> list[str]:
        return [m for c, m, w in self.items if c == code and w]


REGISTRY = {
    "U001": {"id": "U001", "kind": "оценка", "name": "Capex пилота «Семейный радар»", "value": "18", "unit": "млн руб."},
    "U002": {"id": "U002", "kind": "оценка", "name": "Opex пилота", "value": "5", "unit": "млн руб. в год"},
    "U003": {"id": "U003", "kind": "прогноз", "name": "Прирост валовой прибыли от радара", "value": "7", "unit": "млн руб. в год"},
    "U004": {"id": "U004", "kind": "оценка", "name": "Capex панели мульти-SIM", "value": "12", "unit": "млн руб."},
    "U010": {"id": "U010", "kind": "допущение", "name": "Новые прямые расходы пилота за год", "value": "1,2", "unit": "млн"},
    "U012": {"id": "U012", "kind": "допущение", "name": "CAPEX разработки пилота Q1", "value": "8", "unit": "млн"},
    "U026": {"id": "U026", "kind": "прогноз", "name": "Валовая прибыль инициативы", "value": "278", "unit": "млн"},
    "U027": {"id": "U027", "kind": "допущение", "name": "Чистый прирост валовой прибыли над базой", "value": "-1", "unit": "млн"},
}

OPTIONS = """# Варианты

### O1. Оставить как есть
<!-- role: status-quo -->

Экономика: вложений нет, эффект на прибыль — база.

### O2. Семейный радар: сигналы и активация
<!-- role: change -->

Экономика: затраты — capex оценка 18 млн руб. [U001], opex оценка 5 млн руб. в год [U002]; эффект на прибыль — прогноз 7 млн руб. в год [U003]; окупаемость `18 / (7 - 5) = 9` лет.

### O3. Панель мульти-SIM: свои устройства
<!-- role: change -->

Экономика: затраты — capex оценка 12 млн руб. [U004], opex 2 млн руб. в год; эффект на прибыль — 3 млн руб. в год; окупаемость `12 / (3 - 2) = 12` лет.

### O4. Закрыть направление
<!-- role: close -->

Остановить.
"""

STATE = {"checkpoints": {"options": {"choice": "O2"}}}


def consistency(**files: str) -> Collector:
    found = Collector()
    texts = {"options.md": OPTIONS}
    texts.update({name.replace("_", ".", 1) if name.endswith("_md") else name: body for name, body in files.items()})
    economics_check.review_consistency(texts, STATE, REGISTRY, found)
    return found


class ConsistencyTests(unittest.TestCase):
    def test_same_numbers_everywhere_pass(self) -> None:
        found = consistency(
            deliverable_md="**CAPEX:** оценка 18 млн [U001]; opex 5 млн в год [U002]; прогноз 278 млн [U026].\n",
            strategy_md="## Продуктовая стратегия\n\n### Экономика: затраты и эффект\n\nCapex 18 млн [U001], "
            "эффект на прибыль 7 млн в год [U003], чистый эффект 2 млн в год, окупаемость 9 лет.\n",
        )
        self.assertEqual(found.items, [])

    def test_other_capex_of_recommended_option_is_an_error(self) -> None:
        found = consistency(
            deliverable_md="| Статья | Значение |\n|---|---|\n| **CAPEX** | допущение 8 млн [U012] |\n"
        )
        errors = found.errors("economics-consistency")
        self.assertEqual(len(errors), 1, found.items)
        self.assertIn("deliverable.md:3", errors[0])
        self.assertIn("capex 8 млн [U012]", errors[0])
        self.assertIn("18 млн [U001]", errors[0])

    def test_registry_name_sets_the_kind(self) -> None:
        # «Прямые расходы» without the word opex: the registry name tells it is opex.
        found = consistency(deliverable_md="Новая статья: допущение 1,2 млн [U010].\n")
        self.assertTrue(any("opex 1,2 млн [U010]" in m for m in found.errors("economics-consistency")))

    def test_net_effect_is_checked_against_effect_minus_opex(self) -> None:
        found = consistency(
            strategy_md="## Продуктовая стратегия\n\n### Экономика: затраты и эффект\n\n"
            "Окупаемость: допущение -1 млн [U027] не покрывает оценку 18 млн [U001].\n"
        )
        errors = found.errors("economics-consistency")
        self.assertEqual(len(errors), 1, found.items)
        self.assertIn("чистый эффект -1 млн [U027]", errors[0])
        self.assertIn("эффект − opex", errors[0])

    def test_other_option_mismatch_is_a_warning(self) -> None:
        found = consistency(decisions_md="Отклонили O3: capex 15 млн, слишком дорого.\n")
        self.assertEqual(found.errors("economics-consistency"), [])
        self.assertTrue(any("O3" in m and "15 млн" in m for m in found.warnings("economics-consistency")))

    def test_option_columns_of_a_table(self) -> None:
        found = consistency(
            deliverable_md="| Статья | O2 | O3 |\n|---|---|---|\n| Capex | 18 млн | 10 млн |\n"
        )
        self.assertEqual(found.errors("economics-consistency"), [])
        self.assertTrue(any("O3" in m and "10 млн" in m for m in found.warnings("economics-consistency")))

    def test_title_names_the_option(self) -> None:
        found = consistency(decisions_md="Панель мульти-SIM: capex 15 млн.\n")
        self.assertTrue(any("O3" in m for m in found.warnings("economics-consistency")))

    def test_unrelated_cited_figure_near_the_word_effect_is_not_compared(self) -> None:
        found = consistency(
            deliverable_md="Денежный эффект удержания только после Q2; прогноз 278 млн [U026] ниже базы.\n"
        )
        self.assertEqual(found.items, [])

    def test_assumption_id_keeps_its_value(self) -> None:
        found = Collector()
        source_check._review_assumption_visibility(
            "deliverable.md", "CAPEX пилота 8 млн [U001].\n", REGISTRY, found
        )
        self.assertTrue(found.errors("assumption-value"))


class JustificationTests(unittest.TestCase):
    def review(self, line: str, strategy: str = "", brief: str = "") -> Collector:
        options = OPTIONS.replace(
            OPTIONS.split("\n")[10], "Экономика: " + line
        )
        found = Collector()
        texts = {"options.md": options, "strategy.md": strategy, "brief.md": brief}
        economics_check.review_justification(texts, STATE, REGISTRY, found)
        return found

    BLOCK = (
        "## Продуктовая стратегия\n\n### Экономика: затраты и эффект\n\nСм. options.md.\n\n"
        "#### Почему рекомендуем без окупаемости\n\n"
        "Ворота: масштаб в Q2 только если активация не ниже 60%.\n"
        "Ценность варианта: за пилот узнаем, двигает ли ранний сигнал активные группы.\n"
        "Остановка: если активация ниже 40%, пилот закрываем.\n"
    )

    def test_unprofitable_in_words_needs_the_block(self) -> None:
        found = self.review("capex 18 млн [U001]; эффект 3 млн в год; окупаемость дольше горизонта, вариант неокупаем.")
        self.assertTrue(any("Почему рекомендуем без окупаемости" in m for m in found.errors("economics")))

    def test_full_block_passes(self) -> None:
        found = self.review(
            "capex 18 млн [U001]; эффект 3 млн в год; вариант неокупаем в горизонте плана.", strategy=self.BLOCK
        )
        self.assertEqual(found.items, [])

    def test_block_without_stop_condition_is_an_error(self) -> None:
        strategy = self.BLOCK.replace("Остановка: если активация ниже 40%, пилот закрываем.\n", "")
        found = self.review("capex 18 млн; эффект 3 млн в год; не окупается.", strategy=strategy)
        self.assertTrue(any("условия остановки" in m for m in found.errors("economics")))

    def test_effect_not_above_opex_means_no_payback(self) -> None:
        found = self.review("capex 18 млн; opex 5 млн в год; эффект на прибыль 4 млн в год.")
        self.assertTrue(any("не покрывает capex" in m for m in found.errors("economics")))

    def test_payback_beyond_stated_horizon(self) -> None:
        line = "capex 18 млн; opex 5 млн в год; эффект на прибыль 7 млн в год; окупаемость `18 / (7 - 5) = 9` лет."
        found = self.review(line, brief="Горизонт плана — 3 года.\n")
        self.assertTrue(any("дольше горизонта" in m for m in found.errors("economics")))
        self.assertEqual(self.review(line).items, [])  # no horizon stated: nothing to compare

    def test_payback_within_horizon_passes(self) -> None:
        found = self.review(
            "capex 4 млн; opex 1 млн в год; эффект на прибыль 3 млн в год; окупаемость `4 / (3 - 1) = 2` года.",
            brief="Горизонт плана — 3 года.\n",
        )
        self.assertEqual(found.items, [])


if __name__ == "__main__":
    unittest.main()
