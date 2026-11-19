#!/usr/bin/env python3
"""Interview guide, anonymized transcripts, and a quote-level reading.

Real transcripts require consent. Synthetic ones are labeled and are not
treated as a person's words. Personal data is stripped before anything is written.
"""

from __future__ import annotations

import re

import kb

FIRST_NAMES = (
    "Александр",
    "Алексей",
    "Анастасия",
    "Анна",
    "Борис",
    "Валентина",
    "Василий",
    "Виктор",
    "Виктория",
    "Владимир",
    "Дарья",
    "Дмитрий",
    "Евгений",
    "Екатерина",
    "Елена",
    "Иван",
    "Ирина",
    "Кирилл",
    "Мария",
    "Михаил",
    "Наталья",
    "Николай",
    "Ольга",
    "Павел",
    "Сергей",
    "Светлана",
    "Татьяна",
    "Юлия",
)
NAME_INTRO_RE = re.compile(
    r"(?i)\b(?:меня зовут|моё имя|мое имя)\s+"
    r"[А-ЯЁA-Z][а-яёa-z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-z\-]+){0,2}"
)
SPEAKER_RE = re.compile(
    r"(?m)^([А-ЯЁ][а-яё]{1,20}(?:\s+[А-ЯЁ][а-яё]{1,20}){0,2})\s*:"
)
HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,}")
CARD_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
PAIN_STEMS = ("меша", "неудоб", "проблем", "боль", "раздраж", "трудн", "дорого", "не могу")
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{4,}")
SYNTHETIC_SENTENCE = (
    "Это синтетическое интервью. Оно не является реальным интервью и не голос клиента."
)
REAL_CONSENT = "Согласие респондента: да."


class InterviewError(Exception):
    """A refused interview operation. The message is safe to show."""


def _name_pair_re() -> re.Pattern[str]:
    names = "|".join(sorted(FIRST_NAMES, key=len, reverse=True))
    return re.compile(rf"\b(?:{names})(?:\s+[А-ЯЁ][а-яё]{{1,24}}){{1,2}}\b")


NAME_PAIR_RE = _name_pair_re()


def _speaker_is_name(text: str) -> bool:
    for match in SPEAKER_RE.finditer(text):
        first = match.group(1).split()[0]
        if first in FIRST_NAMES:
            return True
    return False


def pii_kinds(text: str) -> list[str]:
    found = kb.pii_kinds(text)
    if NAME_INTRO_RE.search(text) or NAME_PAIR_RE.search(text) or _speaker_is_name(text):
        found.append("имя")
    if HANDLE_RE.search(text):
        found.append("аккаунт")
    if CARD_RE.search(text):
        found.append("номер")
    return found


def anonymize(text: str, names: list[str] | None = None) -> tuple[str, list[str]]:
    """Replace contacts and supplied names. Returns text and kinds that were removed."""
    removed: list[str] = []

    def sub(pattern: re.Pattern[str], repl: str, kind: str, source: str) -> str:
        if pattern.search(source):
            removed.append(kind)
            return pattern.sub(repl, source)
        return source

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = sub(kb.EMAIL_RE, "[email]", "email", cleaned)
    cleaned = sub(kb.PHONE_RE, "[телефон]", "телефон", cleaned)
    cleaned = sub(CARD_RE, "[номер]", "номер", cleaned)
    cleaned = sub(HANDLE_RE, "[аккаунт]", "аккаунт", cleaned)
    cleaned = sub(NAME_INTRO_RE, "[имя скрыто]", "имя", cleaned)
    explicit = [item.strip() for item in (names or []) if item.strip()]
    explicit.sort(key=len, reverse=True)
    for name in explicit:
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(cleaned):
            removed.append("имя")
            cleaned = pattern.sub("[имя]", cleaned)
    if NAME_PAIR_RE.search(cleaned):
        removed.append("имя")
        cleaned = NAME_PAIR_RE.sub("[имя]", cleaned)
    if SPEAKER_RE.search(cleaned):
        removed.append("имя")
        cleaned = SPEAKER_RE.sub("[участник]:", cleaned)
    # Stable order, no duplicates.
    kinds = []
    for kind in removed:
        if kind not in kinds:
            kinds.append(kind)
    return cleaned, kinds


def require_clean(text: str) -> None:
    leftover = pii_kinds(text)
    if leftover:
        raise InterviewError(
            "После обезличивания в тексте ещё есть персональные данные ("
            + ", ".join(leftover)
            + "). Приём отклонён, файл не записан."
        )


def sentences_of(text: str) -> list[str]:
    body = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    found = []
    for chunk in re.split(r"\n+", body):
        chunk = chunk.strip()
        if not chunk:
            continue
        for part in re.split(r"(?<=[.!?])\s+", chunk):
            part = part.strip(" \t-*")
            if len(part) >= 20 and re.search(r"[А-Яа-яЁёA-Za-z]", part):
                found.append(part)
    return found


def _stem(word: str) -> str:
    folded = word.casefold().replace("ё", "е")
    return folded[:5]


def is_pain(sentence: str) -> bool:
    folded = sentence.casefold().replace("ё", "е")
    for stem in PAIN_STEMS:
        for match in re.finditer(re.escape(stem), folded):
            before = folded[max(0, match.start() - 6) : match.start()]
            if re.search(r"(?:^|\s)не\s*$", before):
                continue
            return True
    return False


def relate(quote: str, claim: str) -> str:
    quote_tokens = [_stem(item) for item in TOKEN_RE.findall(quote)]
    claim_tokens = [_stem(item) for item in TOKEN_RE.findall(claim)]
    overlap = [token for token in claim_tokens if token in quote_tokens]
    if not overlap:
        return "не решает"
    folded = quote.casefold().replace("ё", "е")
    words = TOKEN_RE.findall(folded)
    negated = False
    for index, word in enumerate(words):
        if _stem(word) not in overlap:
            continue
        window = " ".join(words[max(0, index - 3) : index + 1])
        if re.search(r"(^|\s)не(\s|$)", window):
            negated = True
            break
    return "опровергает" if negated else "подтверждает"


def build_guide(hypotheses: list[dict], questions_text: str) -> str:
    lines = [
        "# Гайд интервью",
        "",
        "Это план разговора, не расшифровка и не свидетельство. "
        "Реплики сюда не записывают: для приёма нужна команда interview accept и согласие, "
        "либо явная пометка синтетического интервью.",
        "",
        "## Как спрашивать",
        "",
        "Один вопрос — одна гипотеза. Спросите ситуацию, а не согласие с нашей формулировкой. "
        "Если человек не говорил о теме, так и запишите: это не подтверждение.",
        "",
        "## Гипотезы",
        "",
    ]
    active = [
        row
        for row in hypotheses
        if row.get("status", "active") == "active" and row.get("type") in {"hypothesis", "synthetic"}
    ]
    if not active:
        lines.append("Гипотез в реестре нет. Гайд не подменяет сбор свидетельств.")
    for row in active:
        claim = str(row.get("claim") or "").strip()
        evidence_id = str(row.get("id") or "")
        synthetic = row.get("type") == "synthetic"
        label = "синтетическая гипотеза" if synthetic else "гипотеза"
        lines.append(f"### {evidence_id}. {label}")
        lines.append("")
        lines.append(claim)
        lines.append("")
        lines.append(
            f"- если окажется, что человек описывает то же, гипотеза {evidence_id} жива, но один разговор — не сегмент;"
        )
        lines.append(
            f"- если окажется, что человек говорит о другом или прямо отрицает это, гипотезу {evidence_id} не усиливают;"
        )
        lines.append("- источник и метод: дословная реплика из согласия на эту выдержку, не пересказ интервьюера.")
        lines.append("")
    lines.extend(["## Вопросы брифа, если они есть", ""])
    headings = re.findall(r"^###\s+Q\d+\..+$", questions_text or "", re.M)
    if not headings:
        lines.append("В questions.md нет вопросов вида ### Q1.")
    else:
        for heading in headings:
            lines.append(f"- {heading[4:].strip()}")
    lines.append("")
    return "\n".join(lines)


def analyse(transcript: str, hypotheses: list[dict], *, synthetic: bool) -> dict:
    quotes = []
    for sentence in sentences_of(transcript):
        quotes.append({"text": sentence, "pain": is_pain(sentence)})
    pains = [item for item in quotes if item["pain"]]
    key = pains[:3] or quotes[:2]
    links = []
    for row in hypotheses:
        if row.get("status", "active") != "active":
            continue
        if row.get("type") not in {"hypothesis", "synthetic"}:
            continue
        claim = str(row.get("claim") or "")
        best = None
        best_rank = None
        for item in quotes:
            relation = relate(item["text"], claim)
            rank = {"подтверждает": 0, "опровергает": 1, "не решает": 2}[relation]
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best = {"hypothesis_id": row.get("id"), "quote": item["text"], "relation": relation}
        if best and best["relation"] != "не решает":
            links.append(best)
        if len(links) >= 3:
            break
    lines = ["# Разбор интервью", ""]
    if synthetic:
        lines.insert(0, "<!-- synthetic: true -->")
        lines.insert(1, "")
    if synthetic:
        lines.append(SYNTHETIC_SENTENCE)
        lines.append("Цитаты ниже — синтетические реплики, не факты о людях.")
        lines.append("")
    else:
        lines.append(REAL_CONSENT + " Ниже только обезличенная выдержка одного разговора, не рынок.")
        lines.append("")
    lines.append("## Ключевые цитаты")
    lines.append("")
    if not key:
        lines.append("Дословных реплик длиннее 20 знаков нет. Пересказ свидетельством не стал.")
    for item in key:
        mark = "боль" if item["pain"] else "реплика"
        lines.append(f"- ({mark}) «{item['text']}»")
    lines.extend(["", "## Боли", ""])
    if not pains:
        lines.append("В выдержке нет реплики с болью. Боль не дописана.")
    else:
        for item in pains[:3]:
            lines.append(f"- «{item['text']}»")
    lines.extend(["", "## Гипотезы", ""])
    if not links:
        lines.append("Ни одна реплика не подтверждает и не опровергает гипотезу из реестра.")
    for item in links:
        lines.append(
            f"- {item['hypothesis_id']}: реплика {item['relation']} гипотезу. "
            f"«{item['quote']}»"
        )
    lines.append("")
    return {"quotes": key, "pains": pains[:3], "links": links, "markdown": "\n".join(lines)}


def render_transcript(
    interview_id: str,
    body: str,
    *,
    synthetic: bool,
    date: str,
) -> str:
    lines = [f"# Интервью {interview_id}", "", "<!-- untrusted: transcript -->"]
    if synthetic:
        lines.append("<!-- synthetic: true -->")
        lines.append("")
        lines.append(SYNTHETIC_SENTENCE)
        lines.append("Согласие: не применяется, живого респондента нет.")
    else:
        lines.append("<!-- synthetic: false -->")
        lines.append("")
        lines.append(REAL_CONSENT)
        lines.append("В файле только обезличенная выдержка, не полная запись и не персональные данные.")
    lines.extend(
        [
            f"Дата: {date}",
            "Роль: синтетический респондент" if synthetic else "Роль: респондент",
            "",
            "## Расшифровка",
            "",
            body.strip(),
            "",
        ]
    )
    return "\n".join(lines)
