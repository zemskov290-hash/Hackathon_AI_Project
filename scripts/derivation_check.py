#!/usr/bin/env python3
"""Where a forecast number comes from.

A reference ``[U004]`` says only that the number is an assumption. A forecast
that drives the recommendation also needs its derivation: a formula from
sources and other assumptions, for example ``[U010] * 0,6 = 6000``. Without it
the reader cannot repeat the calculation, and «6000 months» stays a round
guess even though every cell has a citation.

Two places hold a derivation:

* the registry field ``derivation`` (``add-assumption --derivation``);
* a backtick formula in a project text whose result is that assumption:
  ``[U004]*500 = 3000000`` derives the 3 million cited next to it.

Rates (%, п.п.) are elementary parameters and are not required to be derived.
An «оценка» that cites a source in ``because`` or ``note`` is read off that
source and is elementary too. Everything else that feeds a formula, and every
forecast bound to an indicator, gets a ``derivation`` warning when no
derivation exists. A registry derivation whose arithmetic no longer matches
the value is a warning as well: values change after a retraction.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_check

U_RE = re.compile(r"\[(U\d{3,})\]")
REF_RE = re.compile(r"\[(?:U\d{3,}|E\d{3,}|S\d{3,}|Q\d{3,}|K\d{3,}|I\d{3,}|A\d+\.[A-Za-z0-9_]+)\]")
CITATION_ONLY_RE = re.compile(r"\[(?:E\d{3,}|S\d{3,}|Q\d{3,}|K\d{3,}|I\d{3,}|A\d+\.[A-Za-z0-9_]+)\]")
SOURCE_CITE_RE = re.compile(r"\[(?:E|S|K|I)\d{3,}\]")
OPERATOR_RE = re.compile(r"[+\-*/×÷−]")
TEXT_FILES = (
    "situation.md",
    "options.md",
    "strategy.md",
    "validation.md",
    "decisions.md",
    "deliverable.md",
)


def _formula_bodies(text: str) -> list[str]:
    """Backtick formulas if there are any, otherwise the whole text as one candidate."""
    bodies = [m.group(1) for m in re.finditer(r"`([^`]*)`", text) if "=" in m.group(1)]
    return bodies or [text]


def _evaluable(body: str) -> str:
    """Drop citations that only label a literal («500 [E018]»); [U…] stays and is substituted."""
    return CITATION_ONLY_RE.sub(" ", body)


def _evaluate(body: str, assumptions: dict[str, dict]):
    """(result Quantity, None) when the formula holds, (None, problem) when it does not.

    (None, None) means the text is not pure arithmetic and cannot be verified.
    """
    candidate = _evaluable(body)
    if candidate.count("=") != 1:
        return None, None
    try:
        _left, result = source_check._split_formula(candidate, assumptions)
    except ValueError as exc:
        message = str(exc)
        if "не равен" in message:
            return None, message
        return None, None
    return result, None


def validate_derivation(derivation: str, value: str, unit: str, project: Path) -> str | None:
    """Problem text for add-assumption, or None when the derivation is acceptable."""
    text = derivation.strip()
    if "=" not in text or not OPERATOR_RE.search(text.split("=", 1)[0]):
        return (
            "derivation — формула: входы, знак действия и «=». "
            "Пример: «`[U010] * 0,6 = 6000`» или «`8000 [Q011] * 0,75 = 6000`». "
            "Пересказ без формулы выводом не считается."
        )
    if not REF_RE.search(text):
        return (
            "В формуле вывода нет ссылки: входы берутся из источника или реестра "
            "([E…], [S…], [Q…], [U…]). Голые числа — это ещё одно допущение."
        )
    assumptions, error = source_check.load_assumptions(project)
    if error:
        return error
    for assumption_id in U_RE.findall(text):
        row = assumptions.get(assumption_id)
        if row is None:
            return f"В выводе ссылка [{assumption_id}], а такого допущения нет."
        if row.get("status", "active") != "active":
            return f"[{assumption_id}] отозвано: на него нельзя опирать новый вывод."
        if source_check.is_qualitative(row):
            return f"[{assumption_id}] — качественное допущение без числа, в формулу оно не входит."
    expected = source_check.assumption_quantity({"value": value, "unit": unit})
    for body in _formula_bodies(text):
        result, problem = _evaluate(body, assumptions)
        if problem:
            return f"Формула вывода не сходится: {problem}."
        if result is not None and expected is not None:
            if not source_check._close_enough(result.value, expected.value):
                return (
                    f"Формула вывода даёт {format(result.value, 'f')}, "
                    f"а value {value} {unit}".rstrip() + ". Исправьте расчёт, не единицу."
                )
    return None


def _is_rate(row: dict) -> bool:
    quantity = source_check.assumption_quantity(row)
    return quantity is not None and (quantity.percent or quantity.points)


def _elementary_estimate(row: dict) -> bool:
    if row.get("kind") != "оценка":
        return False
    basis = f"{row.get('because') or ''} {row.get('note') or ''}"
    return SOURCE_CITE_RE.search(basis) is not None


def _label(row: dict) -> str:
    return source_check._assumption_label(row)


def review(project: Path, texts: dict, add) -> None:
    assumptions, error = source_check.load_assumptions(project)
    if error or not assumptions:
        return
    active = {key: row for key, row in assumptions.items() if row.get("status", "active") == "active"}
    if not active:
        return
    derived: set[str] = set()
    inputs: dict[str, str] = {}

    def note_input(assumption_id: str, where: str) -> None:
        if assumption_id in active and assumption_id not in inputs:
            inputs[assumption_id] = where

    for assumption_id, row in active.items():
        derivation = str(row.get("derivation") or "").strip()
        if not derivation:
            continue
        derived.add(assumption_id)
        for body in _formula_bodies(derivation):
            if "=" in body:
                for used in U_RE.findall(body.split("=", 1)[0]):
                    note_input(used, f"вывод {assumption_id}")
            _result, problem = _evaluate(body, assumptions)
            if problem:
                add(
                    "derivation",
                    f"assumptions.jsonl: вывод {assumption_id} больше не сходится ({problem}). "
                    "Пересчитайте после изменения входов.",
                    warning=True,
                )

    for name in TEXT_FILES:
        text = texts.get(name)
        if text is None and name == "deliverable.md":
            path = project / name
            text = path.read_text(encoding="utf-8") if path.is_file() else None
        if not text or source_check.TEMPLATE_MARK in text:
            continue
        for lineno, sentence in source_check.iter_units(text):
            for body in source_check._formulas(sentence):
                if body.count("=") != 1:
                    continue
                left, right = body.split("=", 1)
                for used in U_RE.findall(left):
                    note_input(used, f"{name}:{lineno}")
                results = U_RE.findall(right)
                derived.update(item for item in results if item in active)
                if results:
                    continue
                # «допущение 3 млн [U020], расчёт `[U004]*500 = 3000000`»: the literal
                # result derives the assumption with the same value in that sentence.
                result = source_check.parse_quantity(_evaluable(right))
                if result is None:
                    continue
                outside = re.sub(r"`[^`]*`", " ", sentence)
                for candidate in U_RE.findall(outside):
                    row = active.get(candidate)
                    quantity = source_check.assumption_quantity(row) if row else None
                    if quantity is not None and source_check._close_enough(quantity.value, result.value):
                        derived.add(candidate)

    required: dict[str, str] = {}
    for assumption_id, row in active.items():
        if row.get("indicator") and row.get("kind") == "прогноз":
            required[assumption_id] = f"прогноз показателя «{row.get('indicator')}»"
    for assumption_id, where in inputs.items():
        required.setdefault(assumption_id, f"вход расчёта ({where})")

    for assumption_id in sorted(required):
        row = active[assumption_id]
        if assumption_id in derived or source_check.is_qualitative(row):
            continue
        if _is_rate(row) or _elementary_estimate(row):
            continue
        add(
            "derivation",
            f"[{assumption_id}] {_label(row)} — {required[assumption_id]}, но число не выведено: "
            "есть ссылка, нет формулы из источников и допущений. Запишите вывод "
            f"(add-assumption --derivation «`… = {row.get('value')}`» с [U…]/[E…]/[Q…]) "
            f"или формулу в тексте «`… = {row.get('value')} [{assumption_id}]`».",
            warning=True,
        )
