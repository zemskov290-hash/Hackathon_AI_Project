#!/usr/bin/env python3
"""Routing self-correction: one logged escalation after a defective step.

A step done by the cheap tier (or by a tool) can come back defective: an API
error, an empty answer, a low-quality marker, an empty search, errors from
check. Then the step is retried once on the stronger tier or with the
alternate tool, and the reason is written to log.md and state.json. There is
no silent retry and no second escalation: after one failed escalation the
step goes to the human.

Pure logic lives here; strat.py writes the journal and state.
"""

from __future__ import annotations

import re

NEXT_TIER = {"cheap": "strong"}

# Alternate tool for a defective tool step. Only tools of this repository.
ALTERNATE_TOOL = {
    "search": "search с упрощённым запросом, затем kb search по локальной базе",
    "fetch": "search по теме страницы, затем fetch по адресу из выдачи",
    "kb-search": "kb search --semantic (или поиск по основам слов, если пакета нет), затем trace по связям",
    "inspect": "fetch страницы и inspect --html по сохранённому снимку",
    "media": "готовая расшифровка .srt/.vtt/.txt рядом с файлом, затем media add --transcript",
    "check": "тот же шаг на уровне strong, затем check снова",
}

LOW_QUALITY = (
    (re.compile(r"\blorem ipsum\b", re.I), "текст-рыба lorem ipsum"),
    (re.compile(r"\b(?:TODO|TBD|FIXME)\b"), "служебная метка TODO/TBD"),
    (re.compile(r"<\s*(?:placeholder|вставить|заполнить)[^>]*>", re.I), "незаполненная заглушка"),
    (re.compile(r"\[(?:вставить|заполнить|placeholder)[^\]]*\]", re.I), "незаполненная заглушка"),
    (re.compile(r"\?\?\?"), "знаки ??? вместо данных"),
    (re.compile(r"\bas an ai\b|\bI (?:cannot|can't) (?:help|assist)\b", re.I), "отказ модели"),
    (re.compile(r"\bкак (?:языковая )?модель[^.]{0,40}не (?:могу|имею)", re.I), "отказ модели"),
)

def completion_defect(mode: str, content: str) -> str | None:
    """Reason why a model answer is defective, or None."""
    if mode == "manual":
        return None
    if mode == "error":
        return "вызов API завершился ошибкой"
    text = (content or "").strip()
    if not text:
        return "пустой ответ модели"
    for pattern, label in LOW_QUALITY:
        if pattern.search(text):
            return f"маркер низкого качества: {label}"
    return None


def search_defect(hits: int | None, error: str | None) -> str | None:
    if error:
        return f"поиск не удался: {error[:200]}"
    if not hits:
        return "поиск вернул 0 результатов"
    return None


def check_defect(errors: int) -> str | None:
    return f"check нашёл ошибок: {errors}" if errors > 0 else None


SEARCH_OPERATOR_RE = re.compile(r"\b(?:site|filetype|inurl|intitle):\S+", re.I)


def simplify_query(query: str, keep: int = 6) -> str:
    """A plainer version of a search query: no operators, quotes or boolean words.

    Keeps the longest distinct words in their original order. Returns the same
    text when nothing can be simplified, so the caller can skip a useless retry.
    """
    text = SEARCH_OPERATOR_RE.sub(" ", query)
    text = re.sub(r"[\"«»“”()\[\]|]", " ", text)
    words = [w for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё-]+", text) if w.upper() not in {"AND", "OR", "NOT"}]
    seen: list[str] = []
    for word in words:
        if word.casefold() not in {item.casefold() for item in seen}:
            seen.append(word)
    if len(seen) > keep:
        longest = sorted(seen, key=lambda w: (-len(w), seen.index(w)))[:keep]
        seen = [w for w in seen if w in longest]
    simple = " ".join(seen).strip()
    return simple or query.strip()


def escalation_target(kind: str, current: str) -> str | None:
    """Next tier for a model step, or the alternate route for a tool step."""
    if kind == "tier":
        return NEXT_TIER.get(current)
    return ALTERNATE_TOOL.get(current)


def already_escalated(state: dict, step: str) -> dict | None:
    for row in state.get("routing_escalations") or []:
        if row.get("step") == step:
            return row
    return None
