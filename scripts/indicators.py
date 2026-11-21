#!/usr/bin/env python3
"""Indicator cells: one fact per (indicator, scenario), then forecasts.

Column titles are whatever the source wrote. The agent assigns a role
(baseline, goal, context) and a direction (higher or lower). Check compares
later text to those records. It does not know the names of any particular brief.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import source_check

ROLES = {"baseline", "goal", "context"}
DIRECTIONS = {"higher", "lower"}
ROLE_ALIASES = {
    "baseline": "baseline",
    "goal": "goal",
    "context": "context",
    "база": "baseline",
    "цель": "goal",
    "контекст": "context",
}
DIRECTION_ALIASES = {
    "higher": "higher",
    "lower": "lower",
    "больше": "higher",
    "меньше": "lower",
}
ROLE_CAPTION = {
    "baseline": "база прогноза: сценарий без изменений на период прогноза",
    "goal": "цель",
    "context": "контекст, не база и не цель",
}
DIRECTION_CAPTION = {
    "higher": "больше — лучше",
    "lower": "меньше — лучше",
}
BLOCK_START = "<!-- indicators:start -->"
BLOCK_END = "<!-- indicators:end -->"
GAMING_RE = re.compile(
    r"для\s+проверки|ради\s+проверки|чтобы\s+прош\w*\s+проверк|для\s+check",
    re.IGNORECASE,
)
MISS_PHRASE = "не достигает цели"
BECAUSE_MIN = 40
DOCUMENTS = ("brief.md", "strategy.md", "deliverable.md")


def canonical_role(token: str) -> str | None:
    return ROLE_ALIASES.get(token.strip().casefold())


def canonical_direction(token: str) -> str | None:
    return DIRECTION_ALIASES.get(token.strip().casefold())


def load_indicators(project: Path) -> tuple[list[dict], str | None]:
    rows, error = source_check._load_jsonl(project / "indicators.jsonl")
    if error:
        return [], error
    seen: set[str] = set()
    for row in rows:
        cell_id = str(row.get("id") or "")
        if not re.fullmatch(r"Q\d{3,}", cell_id):
            return [], f"плохой id показателя: {cell_id or 'пусто'} (нужен Q001)"
        if cell_id in seen:
            return [], f"повторяется показатель {cell_id}"
        seen.add(cell_id)
        if row.get("role") not in ROLES:
            return [], f"{cell_id}: role должен быть baseline, goal или context"
        if row.get("direction") not in DIRECTIONS:
            return [], f"{cell_id}: direction должен быть higher или lower"
        if not str(row.get("indicator") or "").strip():
            return [], f"{cell_id}: пустое название показателя"
        if not str(row.get("scenario") or "").strip():
            return [], f"{cell_id}: пустой сценарий"
        if source_check.assumption_quantity(row) is None:
            return [], f"{cell_id}: в value нет числа"
    return rows, None


def active_indicators(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("status", "active") == "active"]


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _same(left: str, right: str) -> bool:
    return _fold(left) == _fold(right)


def _quantity(row: dict):
    return source_check.assumption_quantity(row)


def _reading_equal(left, right) -> bool:
    if left is None or right is None:
        return False
    return (
        left.value == right.value
        and left.percent == right.percent
        and left.points == right.points
    )


def _unit_label(quantity) -> str:
    if quantity is None:
        return ""
    if quantity.points:
        return "п.п."
    if quantity.percent:
        return "%"
    return ""


def render_block(cells: list[dict], assumptions: list[dict]) -> str:
    """Markdown the documents must contain. Hand-typed tables are not this block."""
    active = active_indicators(cells)
    lines = [BLOCK_START, "Роли столбцов. Их подтверждает человек на контрольной точке брифа."]
    seen_scenario: list[str] = []
    for cell in active:
        if cell["scenario"] not in seen_scenario:
            seen_scenario.append(cell["scenario"])
    for scenario in seen_scenario:
        role = next(cell["role"] for cell in active if cell["scenario"] == scenario)
        lines.append(f"- «{scenario}» — {ROLE_CAPTION[role]}.")
    names: list[str] = []
    for cell in active:
        if cell["indicator"] not in names:
            names.append(cell["indicator"])
    forecasts: list[str] = []
    for row in assumptions:
        if row.get("status", "active") != "active":
            continue
        if not str(row.get("indicator") or "").strip():
            continue
        scenario = str(row.get("scenario") or "").strip()
        if scenario and scenario not in forecasts:
            forecasts.append(scenario)
    header = ["Показатель", *seen_scenario]
    header.extend(f"прогноз: {scenario}" for scenario in forecasts)
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for name in names:
        direction = next(cell["direction"] for cell in active if cell["indicator"] == name)
        row_cells = [f"{name} ({DIRECTION_CAPTION[direction]})"]
        for scenario in seen_scenario:
            cell = next(
                (
                    item
                    for item in active
                    if item["indicator"] == name and item["scenario"] == scenario
                ),
                None,
            )
            if cell is None:
                row_cells.append("—")
            else:
                shown = source_check._assumption_label(cell)
                row_cells.append(f"{shown} [{cell['id']}] [{cell['source']}]")
        for scenario in forecasts:
            assumption = next(
                (
                    item
                    for item in assumptions
                    if item.get("status", "active") == "active"
                    and _same(str(item.get("indicator") or ""), name)
                    and _same(str(item.get("scenario") or ""), scenario)
                ),
                None,
            )
            if assumption is None:
                row_cells.append("—")
            else:
                shown = source_check._assumption_label(assumption)
                row_cells.append(f"{shown} [{assumption['id']}]")
        lines.append("| " + " | ".join(row_cells) + " |")
    lines.append(BLOCK_END)
    return "\n".join(lines)


def place_block(text: str, block: str) -> str:
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.S)
    replacement = block.strip()
    if pattern.search(text):
        return pattern.sub(replacement, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + replacement + "\n"


def _normalize_block(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _extract_block(text: str) -> str | None:
    match = re.search(
        re.escape(BLOCK_START) + r"\s*(.*?)\s*" + re.escape(BLOCK_END),
        text,
        re.S,
    )
    if match is None:
        return None
    return BLOCK_START + "\n" + match.group(1).strip() + "\n" + BLOCK_END


def _without_block(text: str) -> str:
    return re.sub(
        re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END),
        " ",
        text,
        flags=re.S,
    )


def gaming_note(text: str) -> bool:
    return GAMING_RE.search(text or "") is not None


def _meaning_key(row: dict) -> tuple[str, str] | None:
    indicator = str(row.get("indicator") or "").strip()
    if indicator:
        return ("indicator", _fold(indicator))
    name = str(row.get("name") or "").strip()
    if name:
        return ("name", _fold(name))
    return None


def _find_spans(text: str, labels: list[str]) -> list[tuple[int, int, str]]:
    folded = text.casefold()
    found: list[tuple[int, int, str]] = []
    for label in sorted({item for item in labels if item}, key=len, reverse=True):
        needle = label.casefold()
        if len(needle) < 2:
            continue
        start = 0
        while True:
            at = folded.find(needle, start)
            if at < 0:
                break
            before = folded[at - 1] if at else " "
            after_at = at + len(needle)
            after = folded[after_at] if after_at < len(folded) else " "
            if before.isalnum() or after.isalnum():
                start = at + 1
                continue
            found.append((at, after_at, label))
            start = after_at
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, label in found:
        if any(not (end <= left or start >= right) for left, right in occupied):
            continue
        kept.append((start, end, label))
        occupied.append((start, end))
    return kept


def _nearest(origin: int, spans: list[tuple[int, int, str]]) -> str | None:
    if not spans:
        return None
    best = min(spans, key=lambda item: min(abs(origin - item[0]), abs(origin - item[1])))
    return best[2]


def _cell_for(cells: list[dict], indicator: str, scenario: str) -> dict | None:
    for cell in cells:
        if _same(cell["indicator"], indicator) and _same(cell["scenario"], scenario):
            return cell
    return None


def _assumption_for(assumptions: list[dict], indicator: str, scenario: str) -> dict | None:
    for row in assumptions:
        if row.get("status", "active") != "active":
            continue
        if _same(str(row.get("indicator") or ""), indicator) and _same(str(row.get("scenario") or ""), scenario):
            return row
    return None


def _matches_pair(quantity, cell: dict | None, assumption: dict | None) -> bool:
    if cell is not None and _reading_equal(quantity, _quantity(cell)):
        return True
    if assumption is not None and _reading_equal(quantity, _quantity(assumption)):
        return True
    return False


def _other_scenario(cells: list[dict], indicator: str, scenario: str, quantity) -> dict | None:
    for cell in cells:
        if not _same(cell["indicator"], indicator):
            continue
        if _same(cell["scenario"], scenario):
            continue
        if _reading_equal(quantity, _quantity(cell)):
            return cell
    return None


def _bound(assumptions: list[dict], indicator: str) -> list[dict]:
    found = []
    for row in assumptions:
        if row.get("status", "active") != "active":
            continue
        if _same(str(row.get("indicator") or ""), indicator):
            found.append(row)
    return found


def _initiative(bound: list[dict]) -> dict | None:
    if len(bound) == 1:
        return bound[0]
    marked = [row for row in bound if row.get("forecast_role", "initiative") == "initiative"]
    if len(marked) == 1:
        return marked[0]
    return None


def _close(left: Decimal, right: Decimal) -> bool:
    return source_check._close_enough(left, right)


def _worse(direction: str, baseline: Decimal, forecast: Decimal) -> bool:
    if _close(baseline, forecast):
        return False
    if direction == "higher":
        return forecast < baseline
    return forecast > baseline


def _misses(direction: str, goal: Decimal, forecast: Decimal) -> bool:
    if _close(goal, forecast):
        return False
    if direction == "higher":
        return forecast < goal
    return forecast > goal


def _document_text(project: Path, texts: dict) -> str:
    chunks = []
    for name in DOCUMENTS:
        text = texts.get(name)
        if text is None and name == "deliverable.md":
            path = project / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
        if text and "<!-- template: empty -->" not in text:
            chunks.append(text)
    return "\n".join(chunks)


def review(project: Path, texts: dict, add) -> None:
    cells, error = load_indicators(project)
    if error:
        add("indicators", error)
        return
    assumptions, assumption_error = source_check.load_assumptions(project)
    if assumption_error:
        add("json", assumption_error)
        assumptions = {}
    rows = list(assumptions.values())
    _review_assumption_hygiene(rows, add)
    active = active_indicators(cells)
    if not active:
        return
    _review_registry(active, add)
    _review_duplicates(rows, add)
    _review_blocks(project, texts, cells, rows, add)
    _review_scenario_citations(project, texts, active, rows, add)
    _review_forecasts(project, texts, active, rows, add)


def _review_assumption_hygiene(rows: list[dict], add) -> None:
    for row in rows:
        if row.get("status", "active") != "active":
            continue
        note = " ".join(
            part for part in (str(row.get("note") or ""), str(row.get("because") or "")) if part
        )
        if gaming_note(note):
            add(
                "assumption-note",
                f"{row.get('id')}: в заметке нельзя писать, что число подогнано под проверку. "
                "Исправьте смысл или расчёт. Единицу и формулировку ради прохождения check не меняют.",
            )


def _review_registry(cells: list[dict], add) -> None:
    by_scenario: dict[str, set[str]] = {}
    by_indicator: dict[str, set[str]] = {}
    pairs: set[tuple[str, str]] = set()
    for cell in cells:
        key = (_fold(cell["indicator"]), _fold(cell["scenario"]))
        if key in pairs:
            add(
                "indicator-duplicate",
                f"две активные ячейки «{cell['indicator']}» / «{cell['scenario']}». "
                "Одна пара показатель-сценарий — одна запись.",
            )
        pairs.add(key)
        by_scenario.setdefault(_fold(cell["scenario"]), set()).add(cell["role"])
        by_indicator.setdefault(_fold(cell["indicator"]), set()).add(cell["direction"])
    for cell in cells:
        roles = by_scenario[_fold(cell["scenario"])]
        if len(roles) > 1:
            add(
                "indicator-role",
                f"сценарий «{cell['scenario']}» назначен разными ролями. "
                "У столбца одна роль: база прогноза, цель или контекст.",
            )
            break
    for cell in cells:
        directions = by_indicator[_fold(cell["indicator"])]
        if len(directions) > 1:
            add(
                "indicator-role",
                f"у показателя «{cell['indicator']}» разное направление. "
                "Больше лучше или меньше лучше — одно на весь показатель.",
            )
            break
    if not any(cell["role"] == "baseline" for cell in cells):
        add(
            "indicator-role",
            "не назначен сценарий без изменений на период прогноза (role baseline). "
            "Какой столбец им является, решают по заголовкам источника и подтверждают в брифе.",
        )


def _review_duplicates(rows: list[dict], add) -> None:
    active = [row for row in rows if row.get("status", "active") == "active"]
    seen_pair: dict[tuple[str, str], str] = {}
    seen_meaning: dict[tuple[tuple[str, str], str], str] = {}
    for row in active:
        indicator = str(row.get("indicator") or "").strip()
        scenario = str(row.get("scenario") or "").strip()
        if indicator and scenario:
            key = (_fold(indicator), _fold(scenario))
            previous = seen_pair.get(key)
            if previous:
                add(
                    "duplicate-assumption",
                    f"{row.get('id')} повторяет {previous}: один показатель и один сценарий. "
                    "Вторую запись с другой единицей не заводят.",
                )
            else:
                seen_pair[key] = str(row.get("id"))
        meaning = _meaning_key(row)
        quantity = _quantity(row)
        if meaning is None or quantity is None:
            continue
        meaning_key = (meaning, format(quantity.value, "f"))
        previous = seen_meaning.get(meaning_key)
        if previous and previous != str(row.get("id")):
            add(
                "duplicate-assumption",
                f"{row.get('id')} повторяет {previous}: то же значение и тот же смысл. "
                "Проценты и процентные пункты не делают из одной величины две записи.",
            )
        else:
            seen_meaning[meaning_key] = str(row.get("id"))


def _review_blocks(project: Path, texts: dict, cells: list[dict], assumptions: list[dict], add) -> None:
    expected = _normalize_block(render_block(cells, assumptions))
    for name in DOCUMENTS:
        text = texts.get(name)
        if text is None and name == "deliverable.md":
            path = project / name
            text = path.read_text(encoding="utf-8") if path.is_file() else None
        if not text or "<!-- template: empty -->" in text:
            continue
        found = _extract_block(text)
        if found is None:
            add(
                "indicators-table",
                f"{name}: нет таблицы показателей из реестра. "
                "Вставьте её командой indicators table, не переписывайте ячейки вручную.",
            )
            continue
        if _normalize_block(found) != expected:
            add(
                "indicators-table",
                f"{name}: таблица показателей не совпадает с реестром ячеек и прогнозов. "
                "Обновите её командой indicators table.",
            )


def _texts_with_deliverable(project: Path, texts: dict) -> dict:
    merged = dict(texts)
    if not merged.get("deliverable.md"):
        path = project / "deliverable.md"
        if path.is_file():
            merged["deliverable.md"] = path.read_text(encoding="utf-8")
    return merged


def _review_scenario_citations(
    project: Path,
    texts: dict,
    cells: list[dict],
    assumptions: list[dict],
    add,
) -> None:
    texts = _texts_with_deliverable(project, texts)
    indicator_names = list(dict.fromkeys(cell["indicator"] for cell in cells))
    scenario_names = list(dict.fromkeys(cell["scenario"] for cell in cells))
    for row in assumptions:
        scenario = str(row.get("scenario") or "").strip()
        if scenario and scenario not in scenario_names:
            scenario_names.append(scenario)
    blob_names = ("brief.md", "strategy.md", "deliverable.md", "situation.md", "options.md")
    for name in blob_names:
        text = texts.get(name) or ""
        if not text or "<!-- template: empty -->" in text:
            continue
        visible = _without_block(text)
        for _lineno, sentence in source_check.iter_units(visible):
            expanded = source_check.expand_number_words(sentence)
            indicators = _find_spans(expanded, indicator_names)
            scenarios = _find_spans(expanded, scenario_names)
            if not indicators or not scenarios:
                continue
            for quantity, _token, start, _end in source_check.iter_quantity_spans(expanded, keep_small=True):
                indicator = _nearest(start, indicators)
                scenario = _nearest(start, scenarios)
                if indicator is None or scenario is None:
                    continue
                cell = _cell_for(cells, indicator, scenario)
                assumption = _assumption_for(assumptions, indicator, scenario)
                if _matches_pair(quantity, cell, assumption):
                    continue
                other = _other_scenario(cells, indicator, scenario, quantity)
                if other is None:
                    continue
                add(
                    "scenario-mismatch",
                    f"{name}: для «{indicator}» назван сценарий «{scenario}», "
                    f"а число совпадает со сценарием «{other['scenario']}» "
                    f"({source_check._assumption_label(other)}). "
                    "Берите значение этой пары показатель-сценарий, не соседний столбец.",
                )


def _review_forecasts(project: Path, texts: dict, cells: list[dict], assumptions: list[dict], add) -> None:
    document = _document_text(project, texts)
    names = list(dict.fromkeys(cell["indicator"] for cell in cells))
    for name in names:
        bound = _bound(assumptions, name)
        if not bound:
            continue
        if len(bound) > 1 and _initiative(bound) is None:
            add(
                "forecast-role",
                f"у «{name}» несколько прогнозов. Один пометьте ролью initiative, остальные alternate.",
            )
            continue
        forecast = _initiative(bound)
        if forecast is None:
            continue
        direction = next(cell["direction"] for cell in cells if _same(cell["indicator"], name))
        forecast_q = _quantity(forecast)
        baseline = next((cell for cell in cells if _same(cell["indicator"], name) and cell["role"] == "baseline"), None)
        goal = next((cell for cell in cells if _same(cell["indicator"], name) and cell["role"] == "goal"), None)
        if baseline is not None:
            base_q = _quantity(baseline)
            if forecast_q is not None and base_q is not None and _unit_label(forecast_q) != _unit_label(base_q):
                add(
                    "scenario-unit",
                    f"прогноз {forecast.get('id')} для «{name}» в единицах «{_unit_label(forecast_q) or 'без единицы'}», "
                    f"а сценарий «{baseline['scenario']}» — «{_unit_label(base_q) or 'без единицы'}». "
                    "Единицу не подменяют.",
                )
            elif (
                forecast_q is not None
                and base_q is not None
                and _worse(direction, base_q.value, forecast_q.value)
            ):
                because = str(forecast.get("because") or "").strip()
                if gaming_note(because) or len(because) < BECAUSE_MIN or because not in document:
                    add(
                        "worse-than-baseline",
                        f"прогноз {forecast.get('id')} для «{name}» хуже сценария «{baseline['scenario']}» "
                        f"({DIRECTION_CAPTION[direction]}). "
                        "Либо исправьте прогноз, либо запишите обоснование в because и повторите его в тексте. "
                        "Подгонка единицы не заменяет обоснование.",
                    )
        if goal is None or forecast_q is None:
            continue
        goal_q = _quantity(goal)
        if goal_q is None or _unit_label(forecast_q) != _unit_label(goal_q):
            continue
        misses = _misses(direction, goal_q.value, forecast_q.value)
        phrase = f"{MISS_PHRASE} [{goal['id']}]"
        said = phrase.casefold() in document.casefold()
        if misses and not said:
            add(
                "goal-miss",
                f"прогноз {forecast.get('id')} для «{name}» не достигает цели «{goal['scenario']}». "
                f"Напишите в тексте фразу «{phrase}». Не называйте цель выполненной.",
            )
        if said and not misses:
            add(
                "goal-miss",
                f"в тексте сказано «{phrase}», но прогноз «{name}» цель «{goal['scenario']}» достигает. "
                "Фразу о недостижении уберите или поправьте прогноз.",
            )
