#!/usr/bin/env python3
"""Numbers must come from the cited source, an assumption, or a checked formula.

Analysis anchors stay [A001.metric]. Assumptions are [U001] in assumptions.jsonl:
the A-prefix is already the analysis id. Years in created_at do not license a year
in a claim. Word numerals from ten upward are expanded before the check.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ASSUMPTION_KINDS = ("допущение", "прогноз", "оценка")
ASSUMPTION_ID_RE = re.compile(r"^U\d{3,}$")
KIND_STEM = {
    "допущение": "допущен",
    "прогноз": "прогноз",
    "оценка": "оценк",
}
CLAIM_FILES = (
    "questions.md",
    "situation.md",
    "options.md",
    "strategy.md",
    "validation.md",
    "decisions.md",
    "deliverable.md",
)
QTY_FILES = ("strategy.md", "deliverable.md")
TEMPLATE_MARK = "<!-- template: empty -->"
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
CITE_RE = re.compile(r"\[(S\d+|E\d+|U\d+|K\d+|I\d+)\]|\[(A\d+)\.([A-Za-z0-9_]+)\]")
QTY_MARK_RE = re.compile(r"\[qty:([^\s=\]]+)=([^\]]+)\]")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
FENCE_RE = re.compile(r"```.*?```", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
MAGNITUDE = (
    r"тыс\.?|тысячи|тысяча|тысяч|"
    r"млн\.?|миллионов|миллиона|миллион|"
    r"млрд\.?|миллиардов|миллиарда|миллиард"
)
PERCENT_WORD = r"процентами|процентам|процентов|процента|проценте|процент"
# One numeric atom. Thousand groups are NOT swallowed here: «1 000 800» is three
# atoms, and a later pass joins only an unambiguous thousand («5 000», «12 000»).
ATOM_RE = re.compile(
    rf"(?<![\w.])(?P<sign>[+\-−](?=\d))?"
    rf"(?P<whole>\d+)"
    rf"(?P<frac>[.,]\d+)?"
    rf"(?:\s*(?P<mag>{MAGNITUDE}))?"
    rf"(?:(?P<pct>\s*%|\s+(?:{PERCENT_WORD})(?!\w))"
    rf"|(?P<pts>\s*п\.\s*п\.?|\s+процентн\w*\s+пункт\w*))?",
    re.IGNORECASE,
)
SPACE_GAP_RE = re.compile(r"[ \t\u00a0\u202f\u2007\u2009]+")
DASH_GAP_RE = re.compile(r"\s*[-–—]\s*")
ABBREV_RE = re.compile(
    r"\b(руб|тыс|млн|млрд|шт|коп|др|пр|см|стр|п)\.",
    re.IGNORECASE,
)
SCALE = {
    "тысяча": 1000,
    "тысячи": 1000,
    "тысяч": 1000,
    "тыс": 1000,
    "миллион": 1_000_000,
    "миллиона": 1_000_000,
    "миллионов": 1_000_000,
    "млн": 1_000_000,
    "миллиард": 1_000_000_000,
    "миллиарда": 1_000_000_000,
    "миллиардов": 1_000_000_000,
    "млрд": 1_000_000_000,
}
CARDINAL = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "двух": 2,
    "двум": 2,
    "двумя": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
    "десяти": 10,
    "десятью": 10,
    "двадцати": 20,
    "двадцатью": 20,
    "тридцати": 30,
    "тридцатью": 30,
    "сорока": 40,
    "пятидесяти": 50,
    "пятьюдесятью": 50,
    "шестидесяти": 60,
    "шестьюдесятью": 60,
    "семидесяти": 70,
    "семьюдесятью": 70,
    "восьмидесяти": 80,
    "восьмьюдесятью": 80,
    "девяноста": 90,
    "ста": 100,
    "двухсот": 200,
    "трехсот": 300,
    "трёхсот": 300,
    "четырехсот": 400,
    "четырёхсот": 400,
    "пятисот": 500,
    "шестисот": 600,
    "семисот": 700,
    "восьмисот": 800,
    "девятисот": 900,
    "одного": 1,
    "одной": 1,
    "одному": 1,
    "одним": 1,
    "одною": 1,
    "одном": 1,
    "трёх": 3,
    "трех": 3,
    "трём": 3,
    "трем": 3,
    "тремя": 3,
    "четырёх": 4,
    "четырех": 4,
    "четырём": 4,
    "четырем": 4,
    "четырьмя": 4,
    "пяти": 5,
    "пятью": 5,
    "шести": 6,
    "шестью": 6,
    "семи": 7,
    "семью": 7,
    "восьми": 8,
    "восемью": 8,
    "восьмью": 8,
    "девяти": 9,
    "девятью": 9,
    "одиннадцати": 11,
    "одиннадцатью": 11,
    "двенадцати": 12,
    "двенадцатью": 12,
    "тринадцати": 13,
    "тринадцатью": 13,
    "четырнадцати": 14,
    "четырнадцатью": 14,
    "пятнадцати": 15,
    "пятнадцатью": 15,
    "шестнадцати": 16,
    "шестнадцатью": 16,
    "семнадцати": 17,
    "семнадцатью": 17,
    "восемнадцати": 18,
    "восемнадцатью": 18,
    "девятнадцати": 19,
    "девятнадцатью": 19,
    "двумстам": 200,
    "двумястами": 200,
    "двухстах": 200,
    "трёмстам": 300,
    "тремстам": 300,
    "тремястами": 300,
    "трёхстах": 300,
    "трехстах": 300,
    "четырёмстам": 400,
    "четыремстам": 400,
    "четырьмястами": 400,
    "четырёхстах": 400,
    "четырехстах": 400,
    "пятистам": 500,
    "пятьюстами": 500,
    "пятистах": 500,
    "шестистам": 600,
    "шестьюстами": 600,
    "шестистах": 600,
    "семистам": 700,
    "семьюстами": 700,
    "семистах": 700,
    "восьмистам": 800,
    "восемьюстами": 800,
    "восьмьюстами": 800,
    "восьмистах": 800,
    "девятистам": 900,
    "девятьюстами": 900,
    "девятистах": 900,
}
PERCENT_WORDS = {"процент", "процента", "процентов", "процентам", "процентами", "проценте"}
WORD_LIST = sorted(
    list(CARDINAL) + list(SCALE) + list(PERCENT_WORDS),
    key=len,
    reverse=True,
)
WORD_RE = re.compile(r"(?i)\b(?:" + "|".join(re.escape(word) for word in WORD_LIST) + r")\b")
MAG_FACTOR = (
    ("миллиард", 1_000_000_000),
    ("млрд", 1_000_000_000),
    ("миллион", 1_000_000),
    ("млн", 1_000_000),
    ("тысяч", 1000),
    ("тыс", 1000),
)


class Quantity:
    """A comparable magnitude. 2,0 and 2.0 match; 60% does not match 60."""

    def __init__(self, value: Decimal, percent: bool = False, points: bool = False) -> None:
        self.value = value
        self.percent = percent and not points
        self.points = points

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Quantity)
            and self.percent == other.percent
            and self.points == other.points
            and self.value == other.value
        )

    def __hash__(self) -> int:
        return hash((self.value, self.percent, self.points))

    def __repr__(self) -> str:
        if self.points:
            suffix = "п.п."
        elif self.percent:
            suffix = "%"
        else:
            suffix = ""
        return f"{self.value}{suffix}"


def expand_number_words(text: str) -> str:
    """Replace Russian numerals from ten up, and phrases with тыс/млн/процентов.

    A bare «пять» stays a word. «пять процентов», «сто тысяч» and
    «двести семьдесят девять млн» become digits. Spans in backticks are left
    as written: a formula is parsed on its own.
    """
    parts = re.split(r"(`[^`]*`)", text)
    return "".join(part if part.startswith("`") else _expand_words(part) for part in parts)


def _expand_words(text: str) -> str:
    chunks: list[str] = []
    pos = 0
    while pos < len(text):
        match = WORD_RE.search(text, pos)
        if match is None:
            chunks.append(text[pos:])
            break
        words = [match.group(0)]
        end = match.end()
        while True:
            gap = re.match(r"[ \t]+", text[end:])
            if gap is None:
                break
            nxt = WORD_RE.match(text, end + gap.end())
            if nxt is None:
                break
            words.append(nxt.group(0))
            end = nxt.end()
        rendered = _render_phrase(words)
        chunks.append(text[pos : match.start()])
        if rendered is None:
            chunks.append(text[match.start() : match.end()])
            pos = match.end()
        else:
            chunks.append(rendered)
            pos = end
    return "".join(chunks)


def _render_phrase(words: list[str]) -> str | None:
    folded = [word.casefold().rstrip(".") for word in words]
    if any(word in PERCENT_WORDS for word in folded[:-1]):
        return None
    percent = folded[-1] in PERCENT_WORDS
    body = folded[:-1] if percent else folded
    group = 0
    total = 0
    cardinal = False
    for word in body:
        if word in SCALE:
            if not cardinal and group == 0:
                return None
            total += (group or 1) * SCALE[word]
            group = 0
            cardinal = True
            continue
        if word not in CARDINAL:
            return None
        group += CARDINAL[word]
        cardinal = True
    if not cardinal:
        return None
    total += group
    if total < 10 and not percent and not any(word in SCALE for word in body):
        return None
    rendered = format(Decimal(total), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if percent:
        rendered += "%"
    return rendered


def _magnitude(token: str) -> int:
    folded = token.casefold().rstrip(".")
    for stem, factor in MAG_FACTOR:
        if folded.startswith(stem):
            return factor
    return 1


def _year_token(whole: str, frac: str | None, mag: str | None, pct: str | None) -> bool:
    digits = re.sub(r"\D", "", whole)
    if frac or mag or pct or len(digits) != 4:
        return False
    return bool(YEAR_RE.fullmatch(digits))


def _plain_group(match: re.Match[str]) -> str | None:
    """A bare integer that can continue a row of table numbers. A sign starts a new number."""
    if match.group("frac") or match.group("mag") or match.group("pct") or match.group("pts") or match.group("sign"):
        return None
    whole = match.group("whole")
    if _year_token(whole, None, None, None):
        return None
    return whole


def _cluster_head(match: re.Match[str]) -> tuple[str, str] | None:
    """(sign, digits) for an integer that may begin «+1 000». A year does not."""
    if match.group("frac") or match.group("mag") or match.group("pct") or match.group("pts"):
        return None
    whole = match.group("whole")
    if _year_token(whole, None, None, None):
        return None
    sign = (match.group("sign") or "").replace("−", "-")
    if sign not in {"", "+", "-"}:
        return None
    return sign, whole


def _atom_span(match: re.Match[str], *, keep_small: bool) -> tuple[Quantity, str, int, int] | None:
    whole = match.group("whole")
    frac = match.group("frac")
    mag = match.group("mag")
    pct = match.group("pct")
    pts = match.group("pts")
    if _year_token(whole, frac, mag, pct):
        return None
    small = len(whole) < 2 and not frac and not mag and not pct and not pts
    if small and not keep_small:
        return None
    number = whole
    if frac:
        number += "." + re.sub(r"\D", "", frac)
    try:
        value = Decimal(number) * _magnitude(mag or "")
    except InvalidOperation:
        return None
    if (match.group("sign") or "").replace("−", "-") == "-":
        value = -value
    return (Quantity(value, bool(pct), bool(pts)), match.group(0).strip(), match.start(), match.end())


def _segment_cluster(
    cluster: list[tuple[str, int, int]],
    text: str,
    *,
    keep_small: bool,
) -> list[tuple[Quantity, str, int, int]]:
    """Join «5 000» and «1 000 000». Do not join «282 275» or «1 000 800»."""
    found: list[tuple[Quantity, str, int, int]] = []
    index = 0
    total = len(cluster)
    while index < total:
        digits, start, _end = cluster[index]
        take = 1
        if index + 1 < total and len(cluster[index + 1][0]) == 3 and len(digits) <= 3:
            nxt = cluster[index + 1][0]
            if len(digits) <= 2 or nxt == "000":
                take = 2
                while index + take < total and cluster[index + take][0] == "000":
                    take += 1
        if take > 1:
            end = cluster[index + take - 1][2]
            joined = "".join(cluster[index + offset][0] for offset in range(take))
            try:
                value = Decimal(joined)
            except InvalidOperation:
                index += 1
                continue
            found.append((Quantity(value), text[start:end].strip(), start, end))
            index += take
            continue
        if len(digits) >= 2 or keep_small:
            try:
                value = Decimal(digits)
            except InvalidOperation:
                index += 1
                continue
            found.append((Quantity(value), digits, start, cluster[index][2]))
        index += 1
    return found


def _promote_percent_range(
    text: str,
    atoms: list[re.Match[str]],
    atom_index: int,
    item: tuple[Quantity, str, int, int],
) -> tuple[Quantity, str, int, int]:
    """«20–25%» is 20% and 25%, not a bare 20 beside 25%."""
    if item[0].percent or atom_index + 1 >= len(atoms):
        return item
    gap = text[atoms[atom_index].end() : atoms[atom_index + 1].start()]
    nxt = atoms[atom_index + 1]
    if not DASH_GAP_RE.fullmatch(gap):
        return item
    quantity, token, start, end = item
    if nxt.group("pct"):
        return (Quantity(quantity.value, True, False), token, start, end)
    if nxt.group("pts"):
        return (Quantity(quantity.value, False, True), token, start, end)
    return item


def iter_quantity_spans(text: str, *, keep_small: bool = False) -> list[tuple[Quantity, str, int, int]]:
    """Numbers with spans. A table row keeps each cell's number, including inside «1 000 800»."""
    atoms = list(ATOM_RE.finditer(text))
    found: list[tuple[Quantity, str, int, int]] = []
    index = 0
    while index < len(atoms):
        head = _cluster_head(atoms[index])
        if head is None:
            item = _atom_span(atoms[index], keep_small=keep_small)
            if item is not None:
                found.append(_promote_percent_range(text, atoms, index, item))
            index += 1
            continue
        sign, digits = head
        cluster = [(digits, atoms[index].start(), atoms[index].end())]
        cursor = index + 1
        while cursor < len(atoms) and _plain_group(atoms[cursor]) is not None:
            gap = text[atoms[cursor - 1].end() : atoms[cursor].start()]
            if SPACE_GAP_RE.fullmatch(gap) is None:
                break
            cluster.append((_plain_group(atoms[cursor]) or "", atoms[cursor].start(), atoms[cursor].end()))
            cursor += 1
        pieces = _segment_cluster(cluster, text, keep_small=keep_small)
        if sign == "-":
            pieces = _negate_first(pieces, cluster[0][1])
        if len(pieces) == 1 and cursor < len(atoms):
            pieces = [_promote_percent_range(text, atoms, cursor - 1, pieces[0])]
        found.extend(pieces)
        index = cursor
    return found


def _negate_first(
    pieces: list[tuple[Quantity, str, int, int]],
    origin: int,
) -> list[tuple[Quantity, str, int, int]]:
    """The minus belongs to the thousand number it opens, not to the next cell."""
    if not pieces:
        return pieces
    quantity, token, start, end = pieces[0]
    if start != origin:
        return pieces
    pieces[0] = (Quantity(-quantity.value, quantity.percent, quantity.points), token, start, end)
    return pieces


def iter_quantities(text: str, *, keep_small: bool = False) -> list[tuple[Quantity, str]]:
    """Quantities in text that has already been word-expanded. Years are skipped."""
    return [(quantity, token) for quantity, token, _start, _end in iter_quantity_spans(text, keep_small=keep_small)]


def quantities(text: str) -> set[Quantity]:
    expanded = expand_number_words(ISO_DATE_RE.sub(" ", text))
    return {item for item, _token in iter_quantities(expanded)}


def parse_quantity(text: str) -> Quantity | None:
    found = iter_quantities(expand_number_words(text), keep_small=True)
    if not found:
        return None
    return found[0][0]


def _strip_comments(text: str) -> str:
    return COMMENT_RE.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)


def _visible_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside comments and fenced code, with original line numbers."""
    visible = _strip_comments(text)
    lines = visible.splitlines()
    fence = False
    kept: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence or line.strip().startswith("#"):
            continue
        kept.append((lineno, line))
    return kept


def iter_units(text: str) -> list[tuple[int, str]]:
    """Table rows stay whole. «руб.» does not end a sentence."""
    units: list[tuple[int, str]] = []
    paragraph: list[tuple[int, str]] = []

    def emit(chunks: list[tuple[int, str]]) -> None:
        if not chunks:
            return
        protected = " ".join(ABBREV_RE.sub(lambda m: m.group(0)[:-1] + "\u0001", piece) for _n, piece in chunks)
        protected = re.sub(r"\s+", " ", protected).strip()
        starts = [0]
        cuts = []
        for match in re.finditer(r"(?<=[.!?])\s+", protected):
            cuts.append(match.start())
            starts.append(match.end())
        cuts.append(len(protected))
        origin = chunks[0][0]
        for start, end in zip(starts, cuts):
            fragment = protected[start:end].replace("\u0001", ".").strip()
            if fragment:
                units.append((origin, fragment))

    def flush() -> None:
        nonlocal paragraph
        emit(paragraph)
        paragraph = []

    for lineno, line in _visible_lines(text):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            units.append((lineno, stripped))
            continue
        if re.match(r"^(?:[-*]\s+|\d+\.\s+)", stripped):
            flush()
            emit([(lineno, re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", stripped))])
            continue
        paragraph.append((lineno, stripped))
    flush()
    return units


def _years_in(text: str) -> set[int]:
    visible = FENCE_RE.sub(" ", _strip_comments(text))
    visible = re.sub(r"`[^`]*`", " ", visible)
    return {int(match.group(0)) for match in YEAR_RE.finditer(visible)}


def _read_pdf(path: Path) -> str | None:
    import pdf_text

    try:
        return pdf_text.read_pdf(path)
    except (pdf_text.PdfUnavailable, pdf_text.PdfFailed):
        return None


def _companion_extracts(path: Path) -> list[str]:
    """A saved text next to a PDF: brief-family-devices-extract.txt or the same stem .txt."""
    if path.suffix.lower() != ".pdf":
        return []
    texts: list[str] = []
    for candidate in (path.with_name(path.stem + "-extract.txt"), path.with_suffix(".txt")):
        if candidate.is_file():
            texts.append(candidate.read_text(encoding="utf-8", errors="replace"))
    return texts


def source_text(path: Path) -> tuple[str | None, str | None]:
    """PDF text, plus a sidecar extract when the agent saved one. Other files are read as-is."""
    text, problem = read_material_text(path)
    chunks = [text] if text else []
    chunks.extend(_companion_extracts(path))
    if chunks:
        return "\n".join(chunks), None
    return None, problem or "текст не прочитан"


def read_material_text(path: Path) -> tuple[str | None, str | None]:
    """Return text and a reason when it cannot be read. None text means unknown."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        extracted = _read_pdf(path)
        if extracted is None:
            return None, "текст PDF не извлечён (нужен пакет pypdf и непустой текстовый слой)"
        return extracted, None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"файл не прочитан ({exc})"
    if suffix in {".html", ".htm"}:
        raw = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return raw, None


def _source_path(project: Path, source: dict) -> Path | None:
    ref = str(source.get("url_or_path") or "")
    if not ref or re.match(r"https?://", ref):
        return None
    path = Path(ref)
    if not path.is_absolute():
        path = project / path
    if path.is_file():
        return path
    return None


def _load_jsonl(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], None
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            return [], f"{path.name}:{lineno}: не JSON ({exc.msg})"
        if isinstance(item, dict):
            rows.append(item)
    return rows, None


def load_assumptions(project: Path) -> tuple[dict[str, dict], str | None]:
    rows, error = _load_jsonl(project / "assumptions.jsonl")
    if error:
        return {}, error
    by_id: dict[str, dict] = {}
    for row in rows:
        assumption_id = str(row.get("id") or "")
        if not ASSUMPTION_ID_RE.fullmatch(assumption_id):
            return {}, f"плохой id допущения: {assumption_id or 'пусто'} (нужен U001)"
        if assumption_id in by_id:
            return {}, f"повторяется допущение {assumption_id}"
        if row.get("kind") not in ASSUMPTION_KINDS:
            return {}, f"{assumption_id}: kind должен быть допущение, прогноз или оценка"
        if parse_quantity(str(row.get("value") or "")) is None:
            return {}, f"{assumption_id}: в value нет числа"
        by_id[assumption_id] = row
    return by_id, None


def _assumption_label(row: dict) -> str:
    """value plus unit, without printing the unit twice («10 % %», «5 п.п. п.п.»)."""
    value = str(row.get("value") or "").strip()
    unit = str(row.get("unit") or "").strip()
    if not unit:
        return value
    compact_unit = re.sub(r"\s+", "", unit).rstrip(".")
    compact_value = re.sub(r"\s+", "", value).rstrip(".")
    if compact_unit and compact_value.endswith(compact_unit):
        return value
    return f"{value} {unit}".strip()


def assumption_quantity(row: dict) -> Quantity | None:
    """Number from value plus unit. «4» and «млн» is 4 million, not 4."""
    value = str(row.get("value") or "").strip()
    unit = str(row.get("unit") or "").strip()
    direct = parse_quantity(value)
    if direct is not None and re.search(r"тыс|млн|млрд|%|процент|п\.п|пункт", value, re.I):
        return direct
    combined = parse_quantity(f"{value} {unit}".strip()) if unit else None
    return combined or direct


def _close_enough(got: Decimal, expected: Decimal) -> bool:
    """Exact match, or half a percent — rounding, not a different total."""
    diff = abs(got - expected)
    if diff <= Decimal("0.000001"):
        return True
    scale = max(abs(got), abs(expected))
    if scale == 0:
        return True
    return diff / scale <= Decimal("0.005")


def assumption_status(project: Path, assumption_id: str) -> tuple[bool, str]:
    rows, error = load_assumptions(project)
    if error:
        return False, error
    if assumption_id not in rows:
        return False, "нет допущения"
    return True, ""


def _analysis_values(project: Path, analysis_id: str, metric: str) -> set[Quantity]:
    path = project / "analysis" / analysis_id / "result.json"
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict) or metric not in metrics:
        return set()
    parsed = parse_quantity(str(metrics[metric]))
    if parsed is None:
        return set()
    return {parsed}


def _formulas(sentence: str) -> list[str]:
    found = []
    for match in re.finditer(r"`([^`]*)`", sentence):
        body = match.group(1)
        if "=" in body and re.search(r"[+\-*/×÷]", body):
            found.append(body)
    return found


def _without_code(sentence: str) -> str:
    return re.sub(r"`[^`]*`", " ", sentence)


def _eval_decimal(expr: str) -> Decimal:
    """Arithmetic only. Names and calls cannot appear: the caller already rejected letters."""
    tokens = re.findall(r"\d+(?:\.\d+)?|[()+*/-]", expr)
    pos = 0

    def peek() -> str:
        return tokens[pos] if pos < len(tokens) else ""

    def eat() -> str:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        return token

    def parse_expression() -> Decimal:
        value = parse_term()
        while peek() in ("+", "-"):
            op = eat()
            right = parse_term()
            value = value + right if op == "+" else value - right
        return value

    def parse_term() -> Decimal:
        value = parse_factor()
        while peek() in ("*", "/"):
            op = eat()
            right = parse_factor()
            if op == "*":
                value *= right
                continue
            if right == 0:
                raise ValueError("деление на ноль")
            value /= right
        return value

    def parse_factor() -> Decimal:
        if peek() == "+":
            eat()
            return parse_factor()
        if peek() == "-":
            eat()
            return -parse_factor()
        if peek() == "(":
            eat()
            value = parse_expression()
            if peek() != ")":
                raise ValueError("нет закрывающей скобки")
            eat()
            return value
        token = peek()
        if not re.fullmatch(r"\d+(?:\.\d+)?", token or ""):
            raise ValueError("в формуле допустимы только числа и + - * /")
        eat()
        return Decimal(token)

    if not tokens:
        raise ValueError("пустая формула")
    value = parse_expression()
    if pos != len(tokens):
        raise ValueError("формула разобрана не до конца")
    return value


def _substitute_assumptions(body: str, assumptions: dict[str, dict]) -> str:
    """Replace [U001] with the registry magnitude, including млн, тыс. and %."""
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        assumption_id = match.group(1)
        row = assumptions.get(assumption_id)
        if row is None:
            missing.append(assumption_id)
            return match.group(0)
        quantity = assumption_quantity(row)
        if quantity is None:
            missing.append(assumption_id)
            return match.group(0)
        literal = format(quantity.value, "f")
        if quantity.percent:
            literal += "%"
        return literal

    replaced = re.sub(r"\[(U\d+)\]", repl, body)
    if missing:
        raise ValueError("нет допущения " + ", ".join(missing))
    return replaced


def _prepare_formula(body: str, assumptions: dict[str, dict] | None = None) -> tuple[str, list[tuple[Quantity, str]]]:
    """Replace number tokens with Decimal literals. Keep their quantities."""
    raw = body.replace("×", "*").replace("÷", "/").replace("−", "-")
    if assumptions is not None and re.search(r"\[U\d+\]", raw):
        raw = _substitute_assumptions(raw, assumptions)
    expanded = expand_number_words(raw)
    pieces: list[str] = []
    quantities_found: list[tuple[Quantity, str]] = []
    cursor = 0
    for quantity, token, start, end in iter_quantity_spans(expanded, keep_small=True):
        pieces.append(expanded[cursor:start])
        pieces.append(format(quantity.value, "f"))
        quantities_found.append((quantity, token))
        cursor = end
    pieces.append(expanded[cursor:])
    expression = "".join(pieces)
    if re.search(r"[A-Za-zА-Яа-яЁё_]", expression):
        raise ValueError("в формуле допустимы только числа и + - * /")
    return expression, quantities_found


def _split_formula(
    body: str,
    assumptions: dict[str, dict] | None = None,
) -> tuple[list[tuple[Quantity, str]], Quantity]:
    uses_assumption = assumptions is not None and re.search(r"\[U\d+\]", body) is not None
    expression, _numbers = _prepare_formula(body, assumptions if uses_assumption else None)
    if expression.count("=") != 1:
        raise ValueError("в формуле нужен один знак «=»")
    left_body, right_body = body.split("=", 1)
    prepared = assumptions if uses_assumption else None
    left_expr, left_numbers = _prepare_formula(left_body, prepared)
    right_expr, right_numbers = _prepare_formula(right_body, prepared)
    if not left_numbers:
        raise ValueError("слева от «=» нет чисел")
    if not right_numbers:
        raise ValueError("справа от «=» нет числа")
    got = _eval_decimal(left_expr)
    expected = _eval_decimal(right_expr)
    percent_result = any(quantity.percent for quantity, _token in right_numbers)
    # «5000 / 8000 = 62,5 %» is 0.625 × 100, not 0.625 itself.
    matches = abs(got - expected) <= Decimal("0.000001")
    if percent_result:
        matches = matches or abs(got * 100 - expected) <= Decimal("0.000001")
    if uses_assumption:
        matches = _close_enough(got, expected)
        if percent_result:
            matches = matches or _close_enough(got * 100, expected)
    if not matches:
        scale = " × 100" if percent_result else ""
        raise ValueError(f"результат {expected} не равен вычислению {got}{scale}")
    result = right_numbers[-1][0]
    return left_numbers, result


def _snippet(sentence: str) -> str:
    compact = re.sub(r"\s+", " ", sentence).strip()
    if len(compact) > 160:
        return compact[:157] + "..."
    return compact


def _marker_present(sentence: str, kind: str, context: str = "") -> bool:
    """A column header is the marker for every cell under it, not only for its own line."""
    return KIND_STEM[kind] in f"{sentence}\n{context}".casefold()


def _any_marker(sentence: str, context: str = "") -> bool:
    folded = f"{sentence}\n{context}".casefold()
    return any(stem in folded for stem in KIND_STEM.values())


def _split_cells(row: str) -> list[str]:
    stripped = row.strip()
    if not stripped.startswith("|"):
        return [row]
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _separator_row(row: str) -> bool:
    cells = [cell for cell in _split_cells(row) if cell]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _body_headers(text: str) -> dict[int, list[str]]:
    """Line number of a body row → header cells. The header is not hard-coded text."""
    lines = text.splitlines()
    found: dict[int, list[str]] = {}
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].strip().startswith("|"):
            index += 1
        block = lines[start:index]
        if len(block) < 3 or not _separator_row(block[1]):
            continue
        header = _split_cells(block[0])
        for offset in range(2, len(block)):
            found[start + 1 + offset] = header
    return found


def _units_with_marker(text: str):
    """Yield (line, fragment, marker context). A header marks its own column."""
    headers = _body_headers(text)
    for lineno, sentence in iter_units(text):
        header = headers.get(lineno)
        if header is None or not sentence.strip().startswith("|"):
            yield lineno, sentence, ""
            continue
        for index, cell in enumerate(_split_cells(sentence)):
            if not cell or re.fullmatch(r":?-{3,}:?", cell):
                continue
            context = header[index] if index < len(header) else ""
            yield lineno, cell, context


def review(project: Path, texts: dict, sources: list[dict], evidence: list[dict], add) -> None:
    assumptions, assumption_error = load_assumptions(project)
    if assumption_error:
        add("json", assumption_error)
        assumptions = {}

    by_source = {str(row.get("id")): row for row in sources if row.get("id")}
    evidence_source = {
        str(row.get("id")): str(row.get("source") or "")
        for row in evidence
        if row.get("id")
    }
    source_cache: dict[str, tuple[set[Quantity], str | None]] = {}

    def source_quantities(source_id: str) -> tuple[set[Quantity], str | None]:
        if source_id in source_cache:
            return source_cache[source_id]
        row = by_source.get(source_id)
        if row is None:
            source_cache[source_id] = (set(), "нет источника")
            return source_cache[source_id]
        path = _source_path(project, row)
        if path is None:
            source_cache[source_id] = (set(), "нет локального текста")
            return source_cache[source_id]
        text, problem = source_text(path)
        if text is None:
            source_cache[source_id] = (set(), problem or "текст не прочитан")
            return source_cache[source_id]
        source_cache[source_id] = (quantities(text), None)
        return source_cache[source_id]

    allowed_years = _allowed_years(project, texts, by_source)
    documents = _documents(project, texts)
    for name, text in documents.items():
        if name != "brief.md":
            _review_years(name, text, allowed_years, add)
        if name in QTY_FILES:
            _review_qty_syntax(name, text, add)
        _review_numbers(
            project,
            name,
            text,
            assumptions,
            evidence_source,
            source_quantities,
            add,
        )
        _review_assumption_visibility(name, text, assumptions, add)
    review_isolation(project, add)
    review_inconsistent(documents, add)


_OTHER_PROJECT_PATH = re.compile(r"projects/([A-Za-z0-9_.-]+)/")
_ISOLATION_FILES = (
    "brief.md",
    "questions.md",
    "situation.md",
    "options.md",
    "strategy.md",
    "validation.md",
    "decisions.md",
    "deliverable.md",
    "evidence.jsonl",
)


def review_isolation(project: Path, add) -> None:
    """A project may cite only its own sources. Another folder under projects/ is foreign."""
    own = project.name
    siblings: set[str] = set()
    root = project.parent
    if root.is_dir():
        for path in root.iterdir():
            if path.is_dir() and (path / "state.json").is_file():
                siblings.add(path.name)
    for name in _ISOLATION_FILES:
        path = project / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        seen: set[str] = set()
        for match in _OTHER_PROJECT_PATH.finditer(text):
            other = match.group(1)
            if other in {own, ".", ".."} or other.startswith("."):
                continue
            if other in seen:
                continue
            seen.add(other)
            add(
                "foreign-project",
                f"{name}: путь projects/{other}/ указывает на другой проект. "
                "Материалы соседней папки не читают и не копируют.",
            )
        for other in sorted(siblings):
            if other == own or other in seen:
                continue
            if re.search(rf"(?<![\w.-]){re.escape(other)}(?![\w.-])", text):
                seen.add(other)
                add(
                    "foreign-project",
                    f"{name}: упомянут другой проект «{other}». "
                    "Свидетельства берутся только из sources этого проекта.",
                )


def _documents(project: Path, texts: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    names = ("brief.md", *CLAIM_FILES)
    for name in names:
        text = texts.get(name) if name in texts else None
        if text is None and name == "deliverable.md":
            path = project / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
        if not text or TEMPLATE_MARK in text:
            continue
        found[name] = text
    return found


def _allowed_years(project: Path, texts: dict, by_source: dict[str, dict]) -> set[int]:
    years: set[int] = set()
    brief = texts.get("brief.md")
    if brief and TEMPLATE_MARK not in brief:
        years |= _years_in(brief)
    for source_id, row in by_source.items():
        del source_id
        path = _source_path(project, row)
        if path is None:
            continue
        text, _problem = source_text(path)
        if text:
            years |= _years_in(text)
    state_path = project / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict):
            for item in state.get("confirmed_dates") or []:
                years |= _years_in(str(item))
            checkpoints = state.get("checkpoints") or {}
            if isinstance(checkpoints, dict):
                for checkpoint in checkpoints.values():
                    if isinstance(checkpoint, dict):
                        years |= _years_in(str(checkpoint.get("note") or ""))
    return years


def _review_years(name: str, text: str, allowed: set[int], add) -> None:
    for lineno, line in _visible_lines(text):
        visible = re.sub(r"`[^`]*`", " ", line)
        # The decision's own review_date is the date being set, not a claim
        # copied from a source. It does not license that year anywhere else.
        review_span = re.search(r"^-\s+review_date:\s*\d{4}-\d{2}-\d{2}\b", visible.strip())
        review_at = visible.find(review_span.group(0)) if review_span else -1
        for match in YEAR_RE.finditer(visible):
            if review_at >= 0 and review_at <= match.start() < review_at + len(review_span.group(0)):
                continue
            year = int(match.group(0))
            if year in allowed:
                continue
            add(
                "year-not-in-source",
                f"{name}:{lineno}: год {year} нет ни в одном источнике, ни в brief.md, "
                "ни в state.json как подтверждённая дата (поле confirmed_dates или заметка "
                "контрольной точки). Отметки created_at, updated_at и checkpoints.at год не подтверждают. "
                "Запишите дату в бриф или командой confirm-date.",
            )


def _review_qty_syntax(name: str, text: str, add) -> None:
    for lineno, line in _visible_lines(text):
        for match in QTY_MARK_RE.finditer(line):
            label = match.group(1).strip()
            if parse_quantity(match.group(2)) is None:
                add(
                    "inconsistent-value",
                    f"{name}:{lineno}: метка [qty:{label}=…] без числа. "
                    "Пример: [qty:vp-year=4 млн].",
                )


def _phrase_scopes(sentence: str) -> list[str]:
    """A table row is checked cell by cell. Prose is one phrase."""
    stripped = sentence.strip()
    if not stripped.startswith("|"):
        return [sentence]
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return [cell for cell in cells if cell and not re.fullmatch(r":?-{3,}:?", cell)]


def _review_assumption_visibility(name: str, text: str, assumptions: dict[str, dict], add) -> None:
    """[U010] without «4 млн» hides the number. A different number is a stale registry."""
    for lineno, sentence in iter_units(text):
        for scope in _phrase_scopes(sentence):
            ids = []
            for match in re.finditer(r"\[(U\d+)\]", scope):
                if match.group(1) not in ids:
                    ids.append(match.group(1))
            if not ids:
                continue
            present = quantities(expand_number_words(scope))
            for assumption_id in ids:
                row = assumptions.get(assumption_id)
                if row is None:
                    add(
                        "assumption-value",
                        f"{name}:{lineno}: ссылка [{assumption_id}] не найдена в assumptions.jsonl.",
                    )
                    continue
                expected = assumption_quantity(row)
                if expected is None:
                    continue
                if _reading_in(expected, present):
                    continue
                shown = _assumption_label(row)
                hint = ""
                if expected.points and any(item.percent and item.value == expected.value for item in present):
                    hint = " Проценты и процентные пункты — разные величины. Единицу ради проверки не меняют."
                if expected.percent and any(item.points and item.value == expected.value for item in present):
                    hint = " Процентные пункты и проценты — разные величины. Единицу ради проверки не меняют."
                add(
                    "assumption-value",
                    f"{name}:{lineno}: [{assumption_id}] без своего значения в этой фразе или ячейке. "
                    f"В реестре {shown}. Напишите число рядом со ссылкой, например «{shown} [{assumption_id}]». "
                    "Другое число в той же фразе реестр не подтверждает."
                    + hint,
                )


def _review_numbers(
    project: Path,
    name: str,
    text: str,
    assumptions: dict[str, dict],
    evidence_source: dict[str, str],
    source_quantities,
    add,
) -> None:
    for lineno, sentence, marker_context in _units_with_marker(text):
        citations = list(CITE_RE.finditer(sentence))
        labels = []
        analysis: list[tuple[str, str]] = []
        for match in citations:
            if match.group(1):
                labels.append(match.group(1))
            else:
                analysis.append((match.group(2), match.group(3)))
        if any(label.startswith(("K", "I")) for label in labels):
            # Knowledge-base and interview anchors are not source texts.
            # This check only judges [S], [E] and [U].
            labels = [label for label in labels if not label.startswith(("K", "I"))]
            if not labels and not analysis:
                continue
        source_ids: list[str] = []
        assumption_ids: list[str] = []
        for label in labels:
            if label.startswith("S"):
                source_ids.append(label)
            elif label.startswith("E"):
                source_ids.append(evidence_source.get(label, ""))
            elif label.startswith("U"):
                assumption_ids.append(label)
        formulas = _formulas(sentence)
        plain = expand_number_words(ISO_DATE_RE.sub(" ", _without_code(sentence)))
        numbers = iter_quantities(plain)
        formula_results: set[Quantity] = set()
        formula_operands: set[Quantity] = set()
        for body in formulas:
            uses_assumption = re.search(r"\[U\d+\]", body) is not None
            try:
                operands, result = _split_formula(body, assumptions if uses_assumption else None)
            except (ValueError, SyntaxError, InvalidOperation) as exc:
                add(
                    "assumption-arithmetic" if uses_assumption else "number-not-in-source",
                    f"{name}:{lineno}: формула `{body.strip()}` не сходится ({exc}). "
                    f"({_snippet(sentence)})",
                )
                continue
            formula_results.add(result)
            missing_operands = []
            for quantity, token in operands:
                formula_operands.add(quantity)
                if _explained(
                    quantity,
                    sentence,
                    source_ids,
                    assumption_ids,
                    assumptions,
                    analysis,
                    project,
                    source_quantities,
                    formula_results=set(),
                    marker_context=marker_context,
                ):
                    continue
                missing_operands.append(token.strip())
            if missing_operands:
                shown = ", ".join(missing_operands)
                add(
                    "number-not-in-source",
                    f"{name}:{lineno}: операнд {shown} в формуле не найден ни в цитируемом источнике, "
                    f"ни в записи допущения [U…]. ({_snippet(sentence)})",
                )
        if not numbers and not formulas:
            continue
        has_source_cite = any(label.startswith(("S", "E")) for label in labels)
        has_assumption_cite = bool(assumption_ids)
        has_analysis = bool(analysis)
        if name == "deliverable.md" and numbers and not (has_source_cite or has_assumption_cite or has_analysis or formulas):
            shown = ", ".join(token for _q, token in numbers)
            add(
                "ungrounded-number",
                f"{name}:{lineno}: число {shown} без ссылки [S…], [E…], [U…] или [A….метрика] "
                f"({_snippet(sentence)}).",
            )
            continue
        if not (has_source_cite or has_assumption_cite or has_analysis or formulas):
            continue
        for quantity, token in numbers:
            if quantity in formula_operands or quantity in formula_results:
                continue
            if _explained(
                quantity,
                sentence,
                source_ids,
                assumption_ids,
                assumptions,
                analysis,
                project,
                source_quantities,
                formula_results,
                marker_context=marker_context,
            ):
                continue
            _report_missing(
                name,
                lineno,
                token,
                quantity,
                sentence,
                source_ids,
                assumption_ids,
                assumptions,
                source_quantities,
                add,
                marker_context=marker_context,
            )


def _reading_in(quantity: Quantity, present: set[Quantity]) -> bool:
    """Same number and the same kind of unit. 8 п.п. is not 8 % and not a bare 8."""
    return any(
        item.value == quantity.value and item.percent == quantity.percent and item.points == quantity.points
        for item in present
    )


def _value_in(quantity: Quantity, present: set[Quantity]) -> bool:
    """Same magnitude, whether or not one spelling carries a percent sign."""
    if quantity in present:
        return True
    return any(item.value == quantity.value for item in present)


def _explained(
    quantity: Quantity,
    sentence: str,
    source_ids: list[str],
    assumption_ids: list[str],
    assumptions: dict[str, dict],
    analysis: list[tuple[str, str]],
    project: Path,
    source_quantities,
    formula_results: set[Quantity],
    marker_context: str = "",
) -> bool:
    if quantity in formula_results:
        return True
    for source_id in source_ids:
        if not source_id:
            continue
        present, problem = source_quantities(source_id)
        if problem:
            continue
        # «45» and «45 %» are the same magnitude. «оценка» next to a number that
        # is already in the source does not turn that number into an assumption.
        if _value_in(quantity, present):
            return True
    for assumption_id in assumption_ids:
        row = assumptions.get(assumption_id)
        if row is None:
            continue
        parsed = assumption_quantity(row)
        if (
            parsed is not None
            and _reading_in(quantity, {parsed})
            and _marker_present(sentence, str(row.get("kind")), marker_context)
        ):
            return True
    for analysis_id, metric in analysis:
        if quantity in _analysis_values(project, analysis_id, metric):
            return True
    return False


def _report_missing(
    name: str,
    lineno: int,
    token: str,
    quantity: Quantity,
    sentence: str,
    source_ids: list[str],
    assumption_ids: list[str],
    assumptions: dict[str, dict],
    source_quantities,
    add,
    marker_context: str = "",
) -> None:
    hint = ""
    unread = []
    for source_id in source_ids:
        if not source_id:
            continue
        _present, problem = source_quantities(source_id)
        if problem:
            unread.append(f"{source_id}: {problem}")
    if unread:
        hint = " " + "; ".join(unread) + "."
    if assumption_ids or _any_marker(sentence, marker_context):
        if not assumption_ids:
            add(
                "number-not-in-source",
                f"{name}:{lineno}: число {token} помечено как допущение, прогноз или оценка, "
                "но ссылка ведёт на источник, а не на запись допущения. "
                "Оформите `add-assumption` и поставьте [U…] в это предложение; "
                f"[S…] такое число не обосновывает.{hint} ({_snippet(sentence)})",
            )
            return
        kinds = []
        matched_value = False
        for assumption_id in assumption_ids:
            row = assumptions.get(assumption_id)
            if row is None:
                add(
                    "number-not-in-source",
                    f"{name}:{lineno}: ссылка [{assumption_id}] не найдена в assumptions.jsonl.",
                )
                continue
            parsed = assumption_quantity(row)
            if parsed is not None and _value_in(quantity, {parsed}):
                matched_value = True
            if not _marker_present(sentence, str(row.get("kind")), marker_context):
                add(
                    "number-not-in-source",
                    f"{name}:{lineno}: [{assumption_id}] — {row.get('kind')}, "
                    f"а в предложении нет слова «{row.get('kind')}».",
                )
            kinds.append(f"[{assumption_id}]={row.get('value')}")
        if matched_value:
            return
        shown = ", ".join(kinds) if kinds else "записи допущений"
        add(
            "number-not-in-source",
            f"{name}:{lineno}: число {token} не совпадает с цитируемым допущением ({shown}) "
            f"и его нет в источнике.{hint} ({_snippet(sentence)})",
        )
        return
    cited = ", ".join(f"[{source_id}]" for source_id in source_ids if source_id) or "источник"
    add(
        "number-not-in-source",
        f"{name}:{lineno}: число {token} нет в тексте источника {cited}.{hint} ({_snippet(sentence)})",
    )


def collect_qty(texts: dict[str, str]) -> dict[str, list[tuple[str, Quantity, int, str]]]:
    found: dict[str, list[tuple[str, Quantity, int, str]]] = {}
    for name, text in texts.items():
        if TEMPLATE_MARK in text:
            continue
        for lineno, line in _visible_lines(text):
            for match in QTY_MARK_RE.finditer(line):
                label = match.group(1).strip()
                parsed = parse_quantity(match.group(2))
                if parsed is None:
                    continue
                found.setdefault(label, []).append((name, parsed, lineno, match.group(2).strip()))
    return found


def review_inconsistent(documents: dict[str, str], add) -> None:
    grouped: dict[str, list[tuple[str, Quantity, int, str]]] = {}
    for name in QTY_FILES:
        text = documents.get(name)
        if not text:
            continue
        for label, items in collect_qty({name: text}).items():
            grouped.setdefault(label, []).extend(items)
    for label, items in grouped.items():
        distinct: list[tuple[str, Quantity, int, str]] = []
        for item in items:
            if not any(item[1] == previous[1] for previous in distinct):
                distinct.append(item)
        if len(distinct) < 2:
            continue
        shown = "; ".join(
            f"{place}:{lineno} «{raw}»" for place, _qty, lineno, raw in distinct
        )
        add(
            "inconsistent-value",
            f"величина [qty:{label}] записана по-разному: {shown}. "
            "Метка сравнивает только явно помеченные итоги; одинаковое имя с разными "
            "числами — ошибка. Без метки [qty:имя=значение] проверка величины не сопоставляет.",
        )
