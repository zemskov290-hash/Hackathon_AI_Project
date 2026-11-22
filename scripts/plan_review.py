#!/usr/bin/env python3
"""Checks of the plan as a reader sees it, not of single citations.

* Economics: the recommended option compares its investment (capex, opex)
  with its effect on profit, and the strategy has a cost-versus-profit
  section with payback or ROI. A forecast that only grows profit, while the
  money asked for is never set against it, is not a business case.
* Communication barrier: the audience must have a named barrier of
  understanding or choice; messages answer it.
* Flag markers: «1 флаг [U002]» is registry plumbing. In a promise or a key
  message it is an error, elsewhere a warning.
* Placeholders: TODO, «заполнить», a table row «См. таблицу ниже» with empty
  cells, a pointer to a table that is not there.
"""

from __future__ import annotations

import re

import source_check

ECONOMICS_HEADING = "Экономика: затраты и эффект"
BARRIER_HEADING = "Барьер понимания и выбора"
COST_RE = re.compile(
    r"capex|opex|капзатрат|капитальн|операционн\w* (?:затрат|расход)|затрат|инвестиц|вложени|расход|бюджет|"
    r"стоимост\w* (?:пилот|запуск|разработ|команд)|fte",
    re.IGNORECASE,
)
NO_COST_RE = re.compile(
    r"(?:вложений|затрат|инвестиций|расходов)\s+нет|без\s+(?:вложений|затрат|инвестиций)|"
    r"затраты\s*(?:—|-|:)?\s*0(?![\d,.])",
    re.IGNORECASE,
)
EFFECT_RE = re.compile(r"прибыл|выручк|доход|маржин|валов\w* (?:прибыл|марж)|вклад|эффект|экономи", re.IGNORECASE)
COMPARE_RE = re.compile(
    r"окупа|roi|payback|срок\w* возврат|возврат\w* (?:вложен|инвестиц)|npv|чист\w* эффект|"
    r"не окуп|соотношени|против затрат|минус затрат",
    re.IGNORECASE,
)
FORMULA_RE = re.compile(r"`[^`]*=[^`]*`")
BARRIER_WORDS_RE = re.compile(
    r"барьер|не понима|непонят|не замеча|не знает|не верит|не выбира|сомнева|мешает|путает|страх|опасени",
    re.IGNORECASE,
)
# «TODO» in quotes names the word; a bare TODO is the placeholder itself.
PLACEHOLDER_TOKEN_RE = re.compile(r"(?<![\w\-«\"'“„])(?:TODO|TBD|FIXME|XXX)(?![\w\-»\"'”])")
PLACEHOLDER_CELL_RE = re.compile(
    r"^(?:todo|tbd|fixme|xxx|заполнить|заполните|дополнить|дописать|будет дополнено|вставить\b.*|"
    r"placeholder|lorem ipsum.*|\.\.\.|…|\?\?\?)\.?$",
    re.IGNORECASE,
)
POINTER_RE = re.compile(
    r"^(?:см\.?|смотри(?:те)?)\s+(?:таблиц\w*|ниже|выше|раздел\w*|приложени\w*|далее)\b.*$",
    re.IGNORECASE,
)
TABLE_POINTER_RE = re.compile(r"(?:см\.?|смотри(?:те)?)\s+таблиц\w*\s+(ниже|выше)", re.IGNORECASE)
FILES = ("situation.md", "options.md", "strategy.md", "validation.md", "decisions.md", "deliverable.md")
CUSTOMER_FACING_SECTIONS = ("Ключевые сообщения",)


def _visible(text: str) -> str:
    return re.sub(r"<!--.*?-->", " ", text, flags=re.S)


def _heading_body(text: str, title: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,6})\s+(.*?)\s*$", line)
        if match and match.group(2).strip() == title:
            level = len(match.group(1))
            end = len(lines)
            for later in range(index + 1, len(lines)):
                other = re.match(r"^(#+)\s+", lines[later])
                if other and len(other.group(1)) <= level:
                    end = later
                    break
            return "\n".join(lines[index + 1 : end])
    return None


def economics_gaps(body: str) -> list[str]:
    """What a cost-versus-profit text lacks: затраты, эффект на прибыль, сравнение."""
    visible = _visible(body)
    missing = []
    no_cost = NO_COST_RE.search(visible) is not None
    if not COST_RE.search(visible) and not no_cost:
        missing.append("затраты (capex/opex)")
    if not EFFECT_RE.search(visible):
        missing.append("эффект на прибыль")
    if not no_cost and not COMPARE_RE.search(visible) and not FORMULA_RE.search(visible):
        missing.append("сравнение: окупаемость, ROI или формула «эффект − затраты»")
    return missing


def option_blocks(options_text: str) -> list[tuple[str, str, str]]:
    matches = list(source_check_option_re().finditer(options_text))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(options_text)
        blocks.append((match.group(1), match.group(2).strip(), options_text[match.start() : end]))
    return blocks


def source_check_option_re() -> re.Pattern[str]:
    return re.compile(r"^###\s+(O\d+)\.\s+(.+?)\s*$", re.M)


def economics_line(block: str) -> str | None:
    match = re.search(r"(?im)^\s*[-*]?\s*\**Экономика\**\s*:?\**\s*(.+)$", _visible(block))
    if not match:
        return None
    # The line may continue as a short list right under it.
    tail = block[match.end() :]
    extra = []
    for line in tail.splitlines()[1:6]:
        if re.match(r"^\s*[-*]\s+", line) and not re.match(r"^\s*[-*]?\s*\**Возражен", line):
            extra.append(line)
        elif line.strip():
            break
    return match.group(1) + " " + " ".join(extra)


def recommended_options(options_text: str, state: dict) -> list[str]:
    found = []
    choice = ((state.get("checkpoints") or {}).get("options") or {}).get("choice")
    if choice:
        found.append(str(choice))
    for option_id, _title, block in option_blocks(options_text):
        if "<!-- recommended -->" in block and option_id not in found:
            found.append(option_id)
    return found


def review_economics(texts: dict, state: dict, add, *, strategy_required: bool) -> None:
    options_text = texts.get("options.md") or ""
    if options_text and source_check.TEMPLATE_MARK not in options_text:
        blocks = {option_id: (title, block) for option_id, title, block in option_blocks(options_text)}
        for option_id in recommended_options(options_text, state):
            if option_id not in blocks:
                continue
            title, block = blocks[option_id]
            line = economics_line(block)
            if line is None:
                add(
                    "economics",
                    f"options.md: рекомендованный {option_id} («{title}») без строки «Экономика:». "
                    "Сопоставьте вложения (capex, opex) с эффектом на прибыль и окупаемостью.",
                )
                continue
            missing = economics_gaps(line)
            if missing:
                add(
                    "economics",
                    f"options.md: у {option_id} в «Экономика:» нет: {', '.join(missing)}. "
                    "Инвестиция не сопоставлена с эффектом на прибыль.",
                )
    strategy = texts.get("strategy.md") or ""
    if not strategy_required or not strategy or source_check.TEMPLATE_MARK in strategy:
        return
    body = _heading_body(strategy, ECONOMICS_HEADING)
    if body is None:
        return  # the missing subsection is reported by the section check
    missing = economics_gaps(body)
    if missing:
        add(
            "economics",
            f"strategy.md: в «{ECONOMICS_HEADING}» нет: {', '.join(missing)}.",
        )


def review_barrier(strategy: str | None, add) -> None:
    if not strategy or source_check.TEMPLATE_MARK in strategy:
        return
    body = _heading_body(strategy, BARRIER_HEADING)
    if body is None:
        return
    if not BARRIER_WORDS_RE.search(_visible(body)):
        add(
            "strategy",
            f"«{BARRIER_HEADING}»: не назван барьер — что аудитория не понимает или почему не выбирает. "
            "Ключевое сообщение отвечает именно на него.",
            warning=True,
        )


def _promise_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in re.finditer(r"<!--\s*promise:\s*\S+?\s*-->(.*?)<!--\s*product:\s*PP\d+\s*-->", text, flags=re.S):
        spans.append((match.start(), match.end()))
    return spans


def review_flag_markers(project, texts: dict, add) -> None:
    for name in FILES:
        text = texts.get(name)
        if text is None and name == "deliverable.md":
            path = project / name
            text = path.read_text(encoding="utf-8") if path.is_file() else None
        if not text or source_check.TEMPLATE_MARK in text:
            continue
        facing: list[tuple[int, int]] = list(_promise_spans(text))
        for title in CUSTOMER_FACING_SECTIONS:
            body = _heading_body(text, title)
            if body:
                start = text.find(body)
                if start >= 0:
                    facing.append((start, start + len(body)))
        for match in source_check.FLAG_MARKER_RE.finditer(text):
            before = text[: match.start()]
            if before.count("<!--") > before.count("-->"):
                continue
            lineno = before.count("\n") + 1
            inside = any(start <= match.start() < end for start, end in facing)
            add(
                "flag-marker",
                f"{name}:{lineno}: служебная метка «{match.group(0)}» в тексте"
                + (" обещания или ключевого сообщения" if inside else "")
                + ". Назовите посылку словами (качественное допущение [U…]), без «1 флаг».",
                warning=not inside,
            )


def _cells(row: str) -> list[str]:
    return [cell.strip().strip("*_`").strip() for cell in row.strip().strip("|").split("|")]


def review_placeholders(project, texts: dict, add) -> None:
    for name in FILES:
        text = texts.get(name)
        if text is None and name == "deliverable.md":
            path = project / name
            text = path.read_text(encoding="utf-8") if path.is_file() else None
        if not text or source_check.TEMPLATE_MARK in text:
            continue
        lines = source_check._visible_lines(text)
        all_lines = _visible(text).splitlines()
        for position, (lineno, line) in enumerate(lines):
            visible = re.sub(r"`[^`]*`", " ", line)
            stripped = visible.strip()
            if not stripped:
                continue
            token = PLACEHOLDER_TOKEN_RE.search(visible)
            if token:
                add("placeholder", f"{name}:{lineno}: заглушка «{token.group(0)}» в готовом тексте.")
                continue
            if stripped.startswith("|"):
                cells = _cells(stripped)
                if all(re.fullmatch(r":?-{3,}:?", cell or "---") for cell in cells):
                    continue
                filled = [cell for cell in cells if cell]
                if any(PLACEHOLDER_CELL_RE.match(cell) for cell in filled):
                    add("placeholder", f"{name}:{lineno}: ячейка-заглушка в таблице: {stripped[:120]}")
                    continue
                if len(filled) == 1 and len(cells) > 1 and POINTER_RE.match(filled[0]):
                    add(
                        "placeholder",
                        f"{name}:{lineno}: строка таблицы «{filled[0]}» с пустыми ячейками — "
                        "заглушка вместо содержимого. Заполните строки или уберите пустую таблицу.",
                        warning=True,
                    )
                continue
            body = re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", stripped).strip("*_ ")
            if PLACEHOLDER_CELL_RE.match(body):
                add("placeholder", f"{name}:{lineno}: строка-заглушка «{body[:60]}».")
                continue
            pointer = TABLE_POINTER_RE.search(visible)
            if pointer:
                index = lineno - 1
                if pointer.group(1).casefold() == "ниже":
                    window = all_lines[index + 1 : index + 40]
                else:
                    window = all_lines[max(0, index - 40) : index]
                has_table = any(re.match(r"^\s*\|.*\|\s*$", item) for item in window)
                if not has_table:
                    add(
                        "placeholder",
                        f"{name}:{lineno}: ссылка «{pointer.group(0)}», а таблицы рядом нет.",
                        warning=True,
                    )
