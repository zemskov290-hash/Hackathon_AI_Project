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
    def speaker(match: re.Match[str]) -> str:
        label = match.group(1)
        # «Дата:», «Роль:», «Респондент:» are header fields and roles, not a person's name.
        if META_LABEL_RE.match(label + ":") or label.casefold() in ROLE_LABELS:
            return match.group(0)
        removed.append("имя")
        return "[участник]:"

    cleaned = SPEAKER_RE.sub(speaker, cleaned)
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


ROLE_LABELS = {
    "интервьюер", "модератор", "исследователь", "вопрос", "ответ", "респондент", "участник",
    "организатор", "клиент", "пользователь", "абонент",
}
# Speaker labels of the person asking. Their lines are questions, not replies.
INTERVIEWER_LABELS = {
    "и", "в", "q", "интервьюер", "модератор", "исследователь", "вопрос", "interviewer", "moderator",
}
# Labels of header fields. «Дата: …» or «Роль респондента: …» describe the file, nobody said them.
META_LABEL_RE = re.compile(
    r"^(?:дата|время|роль(?:\s+респондента)?|персона|сегмент|согласие|формат|длительность|примечание|"
    r"контекст|источник|тема|цель|сценарий|город|возраст|профиль|метод|канал|id|интервью)\s*:",
    re.IGNORECASE,
)
SPEAKER_LINE_RE = re.compile(r"^\s*(?:\*\*)?([\[\]A-Za-zА-Яа-яЁё ]{1,24}?)(?:\*\*)?\s*:\s*(.*)$")
SERVICE_RE = re.compile(
    r"синтетическ\w*\s+(?:интервью|расшифровк\w*|респондент\w*|реплик\w*|персон\w*|данн\w*)|"
    r"не\s+является\s+реальн|не\s+голос\s+клиента|не\s+реальн\w*\s+(?:интервью|человек|респондент)|"
    r"вымышлен|персон\w*\b[^.]{0,40}\bсобран|живого\s+респондента\s+нет|обезличенн\w*\s+выдержк|"
    r"не\s+(?:разговор|беседа|диалог|интервью)\s+с\s+(?:реальн|живым)|"
    r"нельзя\s+(?:цитировать|использовать)\s+(?:как\s+факт|для\s+подтвержд)|"
    r"^\[?(?:участник|респондент)\]?\s*:\s*\d{4}-\d{2}-\d{2}",
    re.IGNORECASE,
)


def is_service_line(sentence: str) -> bool:
    """Header, label or disclaimer: part of the file, not something the respondent said."""
    text = sentence.strip().strip("*_ ")
    if not text or text.startswith("#"):
        return True
    if META_LABEL_RE.match(text):
        return True
    return SERVICE_RE.search(text) is not None


# A placeholder left by anonymisation stands for the respondent's name.
NAME_PLACEHOLDERS = {"имя", "участник", "респондент", "name"}


def _speaker(line: str) -> tuple[str, str] | None:
    match = SPEAKER_LINE_RE.match(line)
    if not match:
        return None
    label = match.group(1).strip().strip("[]").strip().casefold()
    if not label or len(label.split()) > 2:
        return None
    said = match.group(2).strip().lstrip("*_").strip()
    return label, said


def reply_lines(text: str) -> list[str]:
    """Lines the respondent said.

    In a transcript with speaker labels, lines of the interviewer and lines
    without a label (headers, notes) are dropped and the label is cut off.
    Without labels every line is kept. Header fields and disclaimers are
    dropped in both cases.
    """
    body = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    raw = [line.strip() for line in body.splitlines() if line.strip()]
    labelled = [(_speaker(line), line) for line in raw]
    has_dialogue = any(
        item is not None and item[0] in INTERVIEWER_LABELS for item, _line in labelled
    )
    if has_dialogue:
        # A speaker talks more than once. A one-off bold label («**Важно:** …»,
        # «**Профиль:** …») is a callout of the file, not a person.
        counts: dict[str, int] = {}
        for item, _line in labelled:
            if item is not None:
                counts[item[0]] = counts.get(item[0], 0) + 1
        labelled = [
            (
                item
                if item is None
                or item[0] in INTERVIEWER_LABELS
                or item[0] in ROLE_LABELS
                or item[0] in NAME_PLACEHOLDERS
                or counts.get(item[0], 0) >= 2
                else None,
                line,
            )
            for item, line in labelled
        ]
    kept = []
    for item, line in labelled:
        if has_dialogue:
            if item is None:
                continue
            label, said = item
            if label in INTERVIEWER_LABELS or META_LABEL_RE.match(line):
                continue
            line = said
        if is_service_line(line):
            continue
        kept.append(line)
    return kept


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


# Kinds of pain and barrier a respondent names in their own words. A pattern
# that starts with «не» is the negation itself; for the others a «не» right
# before the word cancels it («недорого», «не страшно»).
PAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("страх", r"страш|боюс|боит|боя(?:ть|л)|опаса|тревож|пуга|волну|нервнич|паник"),
    (
        "непонимание",
        r"не\s+понял|не\s+понима|непонят|запута|путаю|путал|не\s+знал|не\s+заметил|не\s+разобр|"
        r"не\s+ясно|неясн|не\s+в\s+курсе|не\s+чита|не\s+дочита|сложно\s+разобр|оказалось|"
        r"узна\w*\s+(?:потом|последн|случайно|поздно)|узна\w*,?\s+когда|вспомни\w*\s+только|"
        r"думал\w*,?\s+(?:что|все|всё)|редко\s+понима|не\s+помн|не\s+уверен|не\s+видел|"
        r"предупреждени\w*\s+не\s+было",
    ),
    (
        "деньги",
        r"дорог|переплат|списал|списани|лишн\w*\s+(?:деньг|плат|трат)|платить\s+за|"
        r"счет\s+(?:вырос|больше)|неожиданн\w*\s+(?:списан|счет|плат)|не\s+по\s+карману",
    ),
    (
        "доверие",
        r"не\s+довер|недовер|подозр|обман|скрыва|скрыт|мелк\w*\s+шрифт|следят|следит|слежк|"
        r"видит\s+мо|без\s+спроса|навязыва|давать\s+доступ|врет|врут|не\s+вер|верю[^.]{0,20}не\s+очень|"
        r"контролир|стыдно|обидно|конфликт",
    ),
    (
        "усилия",
        r"неудоб|долго|приходится|вручную|каждый\s+раз|лень|тянул|тяжело|мутор|много\s+шагов|"
        r"сложно|трудн|не\s+могу|не\s+получа|не\s+получил|меша|раздраж|проблем|бесит|достал|"
        r"слишком\s+много|боль\b|отключ\w*|забуд|пинать|бросает|тормоз|"
        r"(?:пять|десять|много)\s+(?:пушей|экранов|уведомлен|раз)",
    ),
)
_PAIN_RES = tuple((kind, re.compile(pattern)) for kind, pattern in PAIN_PATTERNS)


def pain_kinds(sentence: str) -> list[str]:
    """Kinds of pain or barrier in a reply: страх, непонимание, деньги, доверие, усилия."""
    folded = sentence.casefold().replace("ё", "е")
    kinds = []
    for kind, pattern in _PAIN_RES:
        for match in pattern.finditer(folded):
            if not match.group(0).startswith("не"):
                before = folded[max(0, match.start() - 6) : match.start()]
                if re.search(r"(?:^|\s)не\s*$", before) or before.endswith("не"):
                    continue
            kinds.append(kind)
            break
    return kinds


def is_pain(sentence: str) -> bool:
    return bool(pain_kinds(sentence))


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


# A synthetic reply cannot confirm or refute anything about real people: it only echoes the claim.
SYNTHETIC_RELATION = {"подтверждает": "созвучна гипотезе, но не подтверждает", "опровергает": "расходится с гипотезой, но не опровергает"}


def relation_text(relation: str, *, synthetic: bool) -> str:
    """Verb for the link between a reply and a hypothesis; synthetic replies never «confirm»."""
    if synthetic:
        return SYNTHETIC_RELATION.get(relation, relation)
    return relation


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
    for sentence in sentences_of("\n".join(reply_lines(transcript))):
        if is_service_line(sentence):
            continue
        kinds = pain_kinds(sentence)
        quotes.append({"text": sentence, "pain": bool(kinds), "kinds": kinds})
    pains = [item for item in quotes if item["pain"]]
    # Key quotes cover different kinds of pain first, then follow the transcript order.
    key: list[dict] = []
    seen_kinds: set[str] = set()
    for item in pains:
        if len(key) < 3 and not set(item["kinds"]) <= seen_kinds:
            key.append(item)
            seen_kinds.update(item["kinds"])
    for item in pains:
        if len(key) < 3 and item not in key:
            key.append(item)
    key = key or quotes[:2]
    links = []
    for row in hypotheses:
        if row.get("status", "active") != "active":
            continue
        # A synthetic record is not a hypothesis about people: linking a synthetic
        # reply to another synthetic reply only echoes the file.
        if row.get("type") != "hypothesis":
            continue
        if str(row.get("locator") or "") == "интервью":
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
        mark = "боль: " + ", ".join(item["kinds"]) if item["pain"] else "реплика"
        lines.append(f"- ({mark}) «{item['text']}»")
    lines.extend(["", "## Боли", ""])
    if not pains:
        lines.append("В выдержке нет реплики с болью. Боль не дописана.")
    else:
        for item in key:
            lines.append(f"- ({', '.join(item['kinds'])}) «{item['text']}»")
        counts: dict[str, int] = {}
        for item in pains:
            for kind in item["kinds"]:
                counts[kind] = counts.get(kind, 0) + 1
        lines.append("")
        lines.append(
            "Барьеры по типам (реплик): "
            + ", ".join(f"{kind} {counts[kind]}" for kind, _pattern in PAIN_PATTERNS if kind in counts)
            + "."
        )
    lines.extend(["", "## Гипотезы", ""])
    if not links:
        lines.append("Ни одна реплика не подтверждает и не опровергает гипотезу из реестра.")
    for item in links:
        if synthetic:
            lines.append(
                f"- {item['hypothesis_id']}: синтетическая реплика {relation_text(item['relation'], synthetic=True)}. "
                f"«{item['quote']}»"
            )
        else:
            lines.append(
                f"- {item['hypothesis_id']}: реплика {item['relation']} гипотезу. "
                f"«{item['quote']}»"
            )
    lines.append("")
    return {"quotes": key, "pains": [item for item in key if item["pain"]], "links": links, "markdown": "\n".join(lines)}


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
