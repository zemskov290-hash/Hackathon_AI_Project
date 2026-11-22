#!/usr/bin/env python3
"""Economics of an option read the same way in every file.

* Consistency: capex, opex, effect on profit, net effect and payback of an
  option are taken from its «Экономика:» line in options.md. The same figure
  in strategy.md or deliverable.md must carry the same value. A number cited
  with [U…] takes its kind from the registry name («Capex пилота» is capex),
  so «CAPEX 8 млн [U012]» in the deliverable is compared with «capex 18 млн
  [U001]» of the option. A mismatch is an error for the recommended option
  and a warning for the others.
* No payback: a recommended option that does not pay back within the plan
  horizon (said in words, or effect not above opex, or payback longer than a
  stated horizon) needs the subsection «Почему рекомендуем без окупаемости»:
  gates, the value of the option, and when to stop.

The checks read figures with a money or time unit only. They do not guess
which option a sentence is about: a line names the option by ID or title, a
table column is headed by it, or the text is about the recommended option
(the strategy economics subsection, the deliverable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import plan_review
import source_check

JUSTIFICATION_HEADING = "Почему рекомендуем без окупаемости"
KINDS = ("capex", "opex", "effect", "net", "payback")
KIND_LABELS = {
    "capex": "capex",
    "opex": "opex",
    "effect": "эффект на прибыль",
    "net": "чистый эффект",
    "payback": "окупаемость",
}
# Order matters: «чистый прирост прибыли» is net, not effect.
KIND_RES = (
    ("net", re.compile(r"чист\w*\s+(?:эффект|прирост|прибыл|вклад)", re.IGNORECASE)),
    ("capex", re.compile(r"capex|капекс|капзатрат|капитальн\w*\s+(?:затрат|вложен|расход)|вложени|инвестиц", re.IGNORECASE)),
    (
        "opex",
        re.compile(
            r"opex|опекс|операционн\w*\s+(?:затрат|расход)|прям\w*\s+расход|расход\w*\s+(?:в|за)\s+год|"
            r"поддержк\w*\s+в\s+год",
            re.IGNORECASE,
        ),
    ),
    (
        "effect",
        re.compile(r"эффект|прирост\w*(?:\s+[\w«»-]+){0,2}\s+(?:прибыл|марж)|доп\w*\s+прибыл", re.IGNORECASE),
    ),
    ("payback", re.compile(r"окупаем|payback|срок\w*\s+возврат", re.IGNORECASE)),
)
MONEY_RE = re.compile(
    r"(?<![\w.,])([-−]?\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?)\s*(млрд|млн|тыс\.?|руб|₽)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)`?\s*(лет|год\w*|мес\w*)", re.IGNORECASE)
CITE_AFTER_RE = re.compile(r"^[\s.]*(?:[\w.]+\s+){0,4}?\[(U\d+)\]")
OPTION_ID_RE = re.compile(r"(?<![\w])O(\d+)(?![\w])")
NO_PAYBACK_RE = re.compile(
    r"неокупаем|не\s+окуп|окупаемост\w*[^.;]{0,60}(?:не\s+ожида|не\s+достига|дольше|за\s+пределами|вне\s+горизонт)|"
    r"не\s+покрыва",
    re.IGNORECASE,
)
HORIZON_RE = re.compile(
    r"горизонт\w*(?:\s+[\w«»]+){0,3}?\s*[—:-]?\s*(\d+(?:[.,]\d+)?)\s*(год\w*|лет|мес\w*)",
    re.IGNORECASE,
)
GATE_RE = re.compile(r"ворот|порог|критери|kpi|метрик|услови\w*\s+масштаб|если\s+[^.]{0,40}(?:не\s+ниже|выше|≥)", re.IGNORECASE)
OPTION_VALUE_RE = re.compile(
    r"опцион|ценност\w*\s+(?:знани|опыт|данн|вариант)|узна\w*|проверим|обучени|стратегическ|защит\w*\s+баз|"
    r"удержан|данн\w*\s+для\s+решени|без\s+(?:пилота|проверки)\s+[^.]{0,40}не",
    re.IGNORECASE,
)
STOP_RE = re.compile(r"стоп|останов|прекра|закры|откат|сворач|выход\w*\s+из|отказ\w*\s+от\s+масштаб", re.IGNORECASE)
OTHER_FILES = ("strategy.md", "deliverable.md", "decisions.md")
UNIT_SCALE = {"млрд": 1000.0, "млн": 1.0, "тыс": 0.001, "руб": 0.000001, "₽": 0.000001}


@dataclass
class Figure:
    kind: str
    value: float
    unit: str  # "млн" for money, "лет" for time
    shown: str
    file: str
    line: int
    cite: str = ""


def _number(text: str) -> float:
    return float(text.replace("−", "-").replace("\u00a0", "").replace(" ", "").replace(",", "."))


def _registry_kind(row: dict | None) -> str | None:
    if not row:
        return None
    name = str(row.get("name") or "")
    for kind, pattern in KIND_RES:
        if pattern.search(name):
            return kind
    return None


def _values(text: str) -> list[tuple[int, int, float, str, str]]:
    """(start, end, value, unit, shown) for money in млн and time in years."""
    found = []
    for match in MONEY_RE.finditer(text):
        unit = match.group(2).casefold().rstrip(".")
        scale = UNIT_SCALE.get(unit, 1.0)
        found.append((match.start(), match.end(), _number(match.group(1)) * scale, "млн", match.group(0).strip()))
    for match in TIME_RE.finditer(text):
        value = _number(match.group(1))
        if value >= 1900:  # a year, not a duration
            continue
        if match.group(2).casefold().startswith("мес"):
            value = value / 12
        found.append((match.start(), match.end(), value, "лет", match.group(0).replace("`", "").strip()))
    found.sort()
    return found


def _keywords(text: str) -> list[tuple[int, int, str]]:
    marks: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in KIND_RES:
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in taken):
                continue
            marks.append((match.start(), match.end(), kind))
            taken.append((match.start(), match.end()))
    marks.sort()
    return marks


def figures_in(text: str, file: str, line: int, assumptions: dict, default_kind: str | None = None) -> list[Figure]:
    """Economic figures in one clause, cell or line."""
    text = re.sub(r"<!--.*?-->", " ", text)
    values = _values(text)
    marks = _keywords(text)
    figures: list[Figure] = []
    used: set[int] = set()
    cited: set[int] = set()
    for start, end, value, unit, shown in values:
        cite = CITE_AFTER_RE.match(text[end:])
        if cite:
            row = assumptions.get(cite.group(1))
            if row:
                # The registry names the figure; a nearby keyword does not re-label it.
                cited.add(start)
            kind = _registry_kind(row)
            if kind and (unit == "лет") == (kind == "payback"):
                figures.append(Figure(kind, value, unit, shown, file, line, cite.group(1)))
                used.add(start)
    # Without a registry kind: the first figure after a keyword, before the next keyword.
    for index, (k_start, k_end, kind) in enumerate(marks):
        limit = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        for start, end, value, unit, shown in values:
            if start < k_end or start >= limit:
                continue
            if (unit == "лет") != (kind == "payback"):
                continue
            if start in cited:
                break
            if start not in used:
                figures.append(Figure(kind, value, unit, shown, file, line))
                used.add(start)
            break
    if default_kind and not marks:
        for start, end, value, unit, shown in values:
            if start not in used and start not in cited and (unit == "лет") == (default_kind == "payback"):
                figures.append(Figure(default_kind, value, unit, shown, file, line))
                used.add(start)
                break
    return figures


def _clauses(text: str) -> list[str]:
    return [part for part in re.split(r";|(?<=[.!?])\s+(?=[А-ЯA-Z«])", text) if part.strip()]


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _short_titles(options_text: str) -> dict[str, str]:
    titles = {}
    for option_id, title, _block in plan_review.option_blocks(options_text):
        short = re.split(r"[:—(]", title)[0].strip().strip("«»\"").casefold()
        if len(short) >= 6:
            titles[option_id] = short
    return titles


def _line_option(line: str, titles: dict[str, str]) -> str | None | bool:
    """O-id the line is about; False when it names several."""
    ids = {"O" + item for item in OPTION_ID_RE.findall(line)}
    folded = line.casefold()
    ids.update(option_id for option_id, short in titles.items() if short in folded)
    if len(ids) > 1:
        return False
    return next(iter(ids)) if ids else None


def option_references(options_text: str, assumptions: dict) -> dict[str, list[Figure]]:
    refs: dict[str, list[Figure]] = {}
    lines = options_text.splitlines()
    for option_id, _title, block in plan_review.option_blocks(options_text):
        line = plan_review.economics_line(block)
        if line is None:
            continue
        lineno = next((index + 1 for index, text in enumerate(lines) if line.strip()[:60] in text), 0)
        figures: list[Figure] = []
        for clause in _clauses(line):
            figures.extend(figures_in(clause, "options.md", lineno, assumptions))
        refs[option_id] = figures
    return refs


def other_figures(texts: dict, options_text: str, recommended: str | None, assumptions: dict) -> list[tuple[str, Figure]]:
    titles = _short_titles(options_text)
    found: list[tuple[str, Figure]] = []
    for name in OTHER_FILES:
        text = texts.get(name) or ""
        if not text or source_check.TEMPLATE_MARK in text:
            continue
        text_lines = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S).splitlines()
        section_owner: str | None = None
        header: list[str] | None = None
        column_owner: list = []
        for index, raw in enumerate(text_lines):
            lineno = index + 1
            heading = re.match(r"^(#{2,6})\s+(.*?)\s*$", raw)
            if heading:
                title = heading.group(2).strip()
                header = None
                if name == "strategy.md":
                    if title in (plan_review.ECONOMICS_HEADING, JUSTIFICATION_HEADING):
                        section_owner = recommended
                    elif len(heading.group(1)) <= 3:
                        section_owner = None
                continue
            default_owner = recommended if name == "deliverable.md" else section_owner
            if raw.lstrip().startswith("|"):
                cells = _cells(raw)
                if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                    continue
                if header is None:
                    header = cells
                    column_owner = [_line_option(cell, titles) for cell in header]
                    if any(isinstance(owner, str) for owner in column_owner):
                        continue  # a header row of option columns
                    column_owner = []
                    # the first row may carry figures too
                row_owner = _line_option(cells[0], titles) if cells else None
                row_kind = None
                for kind, pattern in KIND_RES:
                    if cells and pattern.search(cells[0]):
                        row_kind = kind
                        break
                for col, cell in enumerate(cells):
                    owner = None
                    if column_owner and col < len(column_owner) and isinstance(column_owner[col], str):
                        owner = column_owner[col]
                    elif isinstance(row_owner, str):
                        owner = row_owner
                    elif row_owner is None:
                        cell_owner = _line_option(cell, titles)
                        owner = cell_owner if isinstance(cell_owner, str) else (default_owner if cell_owner is None else None)
                    if not owner:
                        continue
                    for figure in figures_in(cell, name, lineno, assumptions, default_kind=row_kind if col else None):
                        found.append((owner, figure))
                continue
            header = None
            column_owner = []
            if not raw.strip():
                continue
            for clause in _clauses(raw):
                owner = _line_option(clause, titles)
                if owner is False:
                    continue
                owner = owner or _line_option(raw, titles) or default_owner
                if not isinstance(owner, str) or not owner:
                    continue
                for figure in figures_in(clause, name, lineno, assumptions):
                    found.append((owner, figure))
    return found


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.051, 0.006 * max(abs(a), abs(b)))


def _fmt(value: float, unit: str) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} {unit}"


def review_consistency(texts: dict, state: dict, assumptions: dict, add) -> None:
    options_text = texts.get("options.md") or ""
    if not options_text or source_check.TEMPLATE_MARK in options_text:
        return
    recommended_list = plan_review.recommended_options(options_text, state)
    recommended = recommended_list[0] if len(recommended_list) == 1 else None
    refs = option_references(options_text, assumptions)
    expected: dict[tuple[str, str], list[Figure]] = {}
    for option_id, figures in refs.items():
        for figure in figures:
            expected.setdefault((option_id, figure.kind), []).append(figure)
        by_kind = {figure.kind: figure for figure in figures}
        if "net" not in by_kind and "effect" in by_kind:
            net = by_kind["effect"].value - (by_kind["opex"].value if "opex" in by_kind else 0.0)
            expected[(option_id, "net")] = [
                Figure("net", net, "млн", "эффект − opex", "options.md", by_kind["effect"].line)
            ]
    reported: set[tuple] = set()
    first_seen: dict[tuple[str, str], Figure] = {}
    for owner, figure in other_figures(texts, options_text, recommended, assumptions):
        key = (owner, figure.kind)
        reference = expected.get(key)
        if not reference:
            if key not in first_seen:
                first_seen[key] = figure
                continue
            reference = [first_seen[key]]
        if any(_close(figure.value, item.value) for item in reference):
            continue
        mark = (owner, figure.kind, figure.file, round(figure.value, 3))
        if mark in reported:
            continue
        reported.add(mark)
        ref_text = "; ".join(
            f"{item.file}:{item.line} — {_fmt(item.value, item.unit)}"
            + (f" [{item.cite}]" if item.cite else "")
            + (" (эффект − opex)" if item.shown == "эффект − opex" else "")
            for item in reference
        )
        cite = f" [{figure.cite}]" if figure.cite else ""
        add(
            "economics-consistency",
            f"{figure.file}:{figure.line}: {owner} — {KIND_LABELS[figure.kind]} {_fmt(figure.value, figure.unit)}{cite}, "
            f"а в {ref_text}. Одна и та же величина варианта — одно число во всех файлах; "
            "этапы (Q1 и полная программа) называйте в строке «Экономика:» обоими числами.",
            warning=owner != recommended,
        )


def _first(figures: list[Figure], kind: str) -> Figure | None:
    return next((item for item in figures if item.kind == kind), None)


def horizon_years(texts: dict) -> float | None:
    for name in ("brief.md", "strategy.md", "options.md", "deliverable.md"):
        match = HORIZON_RE.search(re.sub(r"<!--.*?-->", " ", texts.get(name) or "", flags=re.S))
        if match:
            value = _number(match.group(1))
            return value / 12 if match.group(2).casefold().startswith("мес") else value
    return None


def no_payback_reason(line: str, figures: list[Figure], horizon: float | None) -> str | None:
    if NO_PAYBACK_RE.search(line):
        return "в строке «Экономика:» сказано, что вариант не окупается"
    capex = _first(figures, "capex")
    opex = _first(figures, "opex")
    effect = _first(figures, "effect")
    net = _first(figures, "net")
    payback = _first(figures, "payback")
    net_value = net.value if net else (effect.value - (opex.value if opex else 0.0) if effect else None)
    if capex and capex.value > 0 and net_value is not None and net_value <= 0:
        return f"эффект за вычетом opex {_fmt(net_value, 'млн')} не покрывает capex {_fmt(capex.value, 'млн')}"
    if horizon is not None:
        years = payback.value if payback else (capex.value / net_value if capex and net_value else None)
        if years is not None and years > horizon + 1e-9:
            return f"окупаемость {_fmt(years, 'лет')} дольше горизонта плана {_fmt(horizon, 'лет')}"
    return None


def review_justification(texts: dict, state: dict, assumptions: dict, add) -> None:
    options_text = texts.get("options.md") or ""
    if not options_text or source_check.TEMPLATE_MARK in options_text:
        return
    blocks = {option_id: block for option_id, _title, block in plan_review.option_blocks(options_text)}
    horizon = horizon_years(texts)
    refs = option_references(options_text, assumptions)
    for option_id in plan_review.recommended_options(options_text, state):
        block = blocks.get(option_id)
        line = plan_review.economics_line(block) if block else None
        if not line:
            continue
        reason = no_payback_reason(line, refs.get(option_id, []), horizon)
        if not reason:
            continue
        body = None
        for name in ("strategy.md", "options.md", "decisions.md", "deliverable.md"):
            text = texts.get(name) or ""
            if text and source_check.TEMPLATE_MARK not in text:
                body = plan_review._heading_body(text, JUSTIFICATION_HEADING)
                if body is not None:
                    break
        if body is None:
            add(
                "economics",
                f"{option_id} рекомендован без окупаемости в горизонте плана ({reason}), "
                f"но подраздела «{JUSTIFICATION_HEADING}» нет. Добавьте его в продуктовую стратегию: "
                "ворота (пороги, по которым идём дальше), ценность варианта (что узнаем или сохраним) "
                "и условия остановки.",
            )
            continue
        visible = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
        missing = [
            label
            for label, pattern in (
                ("ворота или пороги", GATE_RE),
                ("ценность варианта: что узнаем или сохраним", OPTION_VALUE_RE),
                ("условия остановки", STOP_RE),
            )
            if not pattern.search(visible)
        ]
        if missing:
            add(
                "economics",
                f"«{JUSTIFICATION_HEADING}» для {option_id}: нет — {', '.join(missing)}.",
            )
