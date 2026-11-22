#!/usr/bin/env python3
"""Strategy grounding: numbers, verifiable citations, interview privacy.

Errors, not warnings. Existing projects without these gaps stay clean:
numbers are judged inside substantive ### sections, and a missing `verified`
field means the local file is the check, not a new failure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import interview
import kb

PRODUCT_HEADING = "Продуктовая стратегия"
COMM_HEADING = "Коммуникационная стратегия"
PRODUCT_SUBSECTIONS = (
    "Целевой сегмент и работа (JTBD)",
    "Ценностное предложение",
    "Границы MVP",
    "Метрики",
    "Риски",
    "Что мы не делаем",
)
COMM_SUBSECTIONS = (
    "Аудитории",
    "Ключевые сообщения",
    "Каналы",
    "Сроки и последовательность",
    "Работа с возражениями",
    "Как измеряется успех",
)
SUBSTANTIVE = {
    PRODUCT_HEADING: PRODUCT_SUBSECTIONS,
    COMM_HEADING: COMM_SUBSECTIONS,
}
CITE_RE = re.compile(r"\[(E\d+|S\d+|K\d+|I\d+|U\d+)\]|\[(A\d+)\.([A-Za-z0-9_]+)\]")
NUM_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?%?)(?![\w])")
# Same number tokens as scripts/bench.py. The scorer is not imported: bench imports strat.
SCORER_YEAR_RE = re.compile(r"^(19[9]\d|20[0-2]\d|203[0-5])$")
SCORER_PATH_RE = re.compile(r"(?:[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+)|(?:[\w.+-]+/)+[\w.+-]+")
SCORER_CODE_RE = re.compile(r"`[^`]*`")
SCORER_ERROR_RE = re.compile(
    r"(?i)(?:\[?\s*(?:errno|winerror|oserror)\s*[:#-]?\s*\d+\]?)"
    r"|(?:\b(?:http|https)\s*[:#-]?\s*\d{3}\b)"
    r"|(?:\b(?:ошибк\w*|error|статус|status)\s*[:#-]?\s*\d+\b)"
    r"|(?:\b\d{3}\s+(?:ошибк\w*|error)\b)"
)
SUBSTANTIVE_NUMBER_FILES = (
    "brief.md",
    "questions.md",
    "situation.md",
    "options.md",
    "validation.md",
    "decisions.md",
)
ALLOWED_PROJECT_DIRS = {"sources", "kb", "interviews", "analysis", "inspect"}
# Turn logs of the agent client (stream-json) for measured tokens and time. Only logs.
AGENT_LOG_DIR = "agent-logs"
AGENT_LOG_SUFFIXES = {".jsonl", ".json"}
CALC_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[+\-*/×÷]\s*(\d+(?:[.,]\d+)?)\s*=\s*(\d+(?:[.,]\d+)?%?)"
)
MIN_VERIFIED_SHARE = 0.9
SYNTHETIC_MARK = "синтет"
NOT_REAL = "не является реальным интервью"


def _load_jsonl(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], None
    rows = []
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


def _sections(strategy: str) -> list[tuple[str, str, str]]:
    lines = strategy.splitlines()
    found: list[tuple[str, str, str]] = []
    parent = None
    current = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, current
        if parent and current is not None:
            found.append((parent, current, "\n".join(buf)))
        buf = []

    for line in lines:
        if line.startswith("## "):
            flush()
            current = None
            title = line[3:].strip()
            parent = title if title in SUBSTANTIVE else None
            continue
        if line.startswith("### ") and parent:
            flush()
            current = line[4:].strip()
            continue
        if line.startswith("#"):
            flush()
            current = None
            continue
        if current is not None:
            buf.append(line)
    flush()
    return found


def _claims(body: str) -> list[str]:
    visible = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    visible = re.sub(r"```.*?```", " ", visible, flags=re.S)
    parts: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            parts.append(" ".join(paragraph))
            paragraph.clear()

    for line in visible.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            flush()
            parts.append(re.sub(r"^[-*]\s+|^\d+\.\s+", "", stripped))
            continue
        paragraph.append(stripped)
    flush()
    claims = []
    for block in parts:
        for part in re.split(r"(?<=[.!?])\s+", block):
            part = part.strip()
            if part:
                claims.append(part)
    return claims


def scorer_numbers(sentence: str) -> list[str]:
    """Numbers the benchmark counts: two digits or more, not a year 1990–2035.

    A day in «12 апреля 2026» is a number. The year is not. This is the same
    token rule as bench.number_tokens. The scorer module itself is unchanged.
    """
    masked = SCORER_CODE_RE.sub(" ", sentence)
    masked = SCORER_PATH_RE.sub(" ", masked)
    masked = SCORER_ERROR_RE.sub(" ", masked)
    found = []
    for match in NUM_RE.finditer(masked):
        token = match.group(1)
        bare = token.rstrip("%").replace(",", ".")
        if SCORER_YEAR_RE.match(bare):
            continue
        digits = re.sub(r"\D", "", token)
        if len(digits) < 2 and "." not in bare and "," not in token and not token.endswith("%"):
            continue
        found.append(token)
    return found


def _numbers(claim: str) -> list[str]:
    masked = re.sub(r"`[^`]*`", " ", claim)
    masked = re.sub(r"https?://\S+", " ", masked)
    masked = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", masked)
    return [match.group(1) for match in NUM_RE.finditer(masked)]


def _norm_num(token: str) -> str:
    return token.rstrip("%").replace(",", ".")


def _cited(claim: str) -> bool:
    return CITE_RE.search(claim) is not None


def _calc_grounded(claim: str, section_claims: list[str]) -> bool:
    if not re.search(r"расч[её]т|формул", claim, re.I):
        return False
    match = CALC_RE.search(claim)
    if match is None:
        return False
    operands = {_norm_num(match.group(1)), _norm_num(match.group(2))}
    result = _norm_num(match.group(3))
    cited_numbers: set[str] = set()
    for other in section_claims:
        if not _cited(other):
            continue
        cited_numbers.update(_norm_num(token) for token in _numbers(other))
    if not operands <= cited_numbers:
        return False
    found = {_norm_num(token) for token in _numbers(claim)}
    return found <= operands | {result}


def _source_verified(project: Path, source: dict | None) -> tuple[bool, str]:
    if not source:
        return False, "нет источника"
    if source.get("verified") is False:
        return False, str(source.get("unverified_reason") or "unavailable")
    ref = str(source.get("url_or_path") or "")
    if not ref:
        return False, "пустой путь"
    if re.match(r"https?://", ref):
        return False, "нет снимка"
    path = Path(ref)
    if not path.is_absolute():
        path = project / path
    if not path.is_file():
        return False, "файл не найден"
    return True, ""


def _kb_by_id(project: Path) -> tuple[dict[str, dict], str | None]:
    rows, error = _load_jsonl(project / "kb" / "kb.jsonl")
    return {str(row.get("id")): row for row in rows if row.get("id")}, error


def _interview_file(project: Path, interview_id: str) -> Path | None:
    path = project / "interviews" / f"{interview_id}.md"
    if path.is_file():
        return path
    return None


def _citation_status(
    project: Path,
    token: str,
    *,
    evidence: dict[str, dict],
    sources: dict[str, dict],
    kb_rows: dict[str, dict],
) -> tuple[bool, str]:
    """Return verified flag and a short reason when it is not."""
    if token.startswith("E"):
        row = evidence.get(token)
        if row is None:
            return False, "нет свидетельства"
        return _source_verified(project, sources.get(str(row.get("source") or "")))
    if token.startswith("S"):
        return _source_verified(project, sources.get(token))
    if token.startswith("K"):
        row = kb_rows.get(token)
        if row is None:
            return False, "нет записи базы"
        if row.get("verified") is False:
            return False, str(row.get("unverified_reason") or "unavailable")
        path = project / str(row.get("path") or "")
        if not path.is_file():
            return False, "нет фрагмента"
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(re.sub(r"\s+", "", text)) < 40:
            return False, "фрагмент пуст"
        return True, ""
    if token.startswith("I"):
        path = _interview_file(project, token)
        if path is None:
            return False, "нет расшифровки"
        return True, ""
    if token.startswith("A"):
        analysis_id = token.split(".", 1)[0]
        result = project / "analysis" / analysis_id / "result.json"
        if not result.is_file():
            return False, "нет расчёта"
        return True, ""
    if token.startswith("U"):
        import source_check

        return source_check.assumption_status(project, token)
    return False, "неизвестная ссылка"


def _tokens(claim: str) -> list[str]:
    found = []
    for match in CITE_RE.finditer(claim):
        if match.group(1):
            found.append(match.group(1))
        elif match.group(2):
            found.append(f"{match.group(2)}.{match.group(3)}")
    return found


def review_strategy(
    project: Path,
    strategy: str | None,
    sources: list[dict],
    evidence: list[dict],
    add,
) -> None:
    if not strategy or "<!-- template: empty -->" in strategy:
        return
    by_source = {str(row.get("id")): row for row in sources if row.get("id")}
    by_evidence = {str(row.get("id")): row for row in evidence if row.get("id")}
    kb_rows, kb_error = _kb_by_id(project)
    if kb_error:
        add("json", kb_error)

    verified_links = 0
    total_links = 0
    visible = re.sub(r"<!--.*?-->", " ", strategy, flags=re.S)
    visible = re.sub(r"```.*?```", " ", visible, flags=re.S)
    for match in CITE_RE.finditer(visible):
        token = match.group(1) or f"{match.group(2)}.{match.group(3)}"
        total_links += 1
        ok, _reason = _citation_status(
            project,
            token,
            evidence=by_evidence,
            sources=by_source,
            kb_rows=kb_rows,
        )
        if ok:
            verified_links += 1
    if total_links and verified_links * 10 < total_links * 9:
        share = verified_links / total_links
        add(
            "grounding-rate",
            "strategy.md: проверено "
            f"{verified_links} из {total_links} ссылок ({share:.0%}). "
            "Нужно не меньше 90%: предпочитайте открытый источник или запись базы "
            "с сохранённым фрагментом. Непроверенную ссылку пометьте и не держите на ней вывод один.",
        )

    def inspect_claim(claim: str, where: str) -> None:
        tokens = _tokens(claim)
        if not tokens:
            return
        statuses = []
        for token in tokens:
            ok, reason = _citation_status(
                project,
                token,
                evidence=by_evidence,
                sources=by_source,
                kb_rows=kb_rows,
            )
            statuses.append((token, ok, reason))
            if token.startswith("K"):
                row = kb_rows.get(token)
                if row and row.get("synthetic") and SYNTHETIC_MARK not in claim.casefold():
                    add(
                        "label",
                        f"strategy.md: [{token}] — синтетическая запись базы без пометки «синтетическ»: {claim[:160]}",
                    )
            if token.startswith("I"):
                path = _interview_file(project, token)
                if path is not None:
                    file_text = path.read_text(encoding="utf-8", errors="replace")
                    if "<!-- synthetic: true -->" in file_text and SYNTHETIC_MARK not in claim.casefold():
                        add(
                            "label",
                            f"strategy.md: [{token}] — синтетическое интервью без пометки «синтетическ»: {claim[:160]}",
                        )
        unverified = [token for token, ok, _reason in statuses if not ok]
        if unverified and "непроверен" not in claim.casefold():
            shown = ", ".join(f"[{token}]" for token in unverified)
            add(
                "unverified-label",
                f"strategy.md: {where}ссылка {shown} не проверена, а в утверждении нет слова «непроверен»: {claim[:180]}",
            )
        if unverified and len(unverified) == len(statuses):
            shown = ", ".join(f"[{token}]" for token in unverified)
            add(
                "unverified-only",
                "strategy.md: вывод держится только на непроверенной ссылке "
                f"{shown}. Рядом нужен источник с сохранённым фрагментом: {claim[:180]}",
            )

    for claim in _claims(strategy):
        inspect_claim(claim, "")

    for parent, title, body in _sections(strategy):
        if title not in SUBSTANTIVE.get(parent, ()):
            continue
        claims = _claims(body)
        import source_check

        label_list = source_check.registry_labels(project)
        for claim in claims:
            expanded = source_check.expand_number_words(claim)
            numbers = [
                token
                for token in _numbers(expanded)
                if not source_check.token_only_inside_labels(expanded, token, label_list)
            ]
            if not numbers:
                continue
            if _cited(claim) or _calc_grounded(claim, claims):
                continue
            shown = ", ".join(numbers)
            add(
                "ungrounded-number",
                f"strategy.md: в «{title}» число {shown} без источника, расчёта или интервью: {claim[:180]}",
            )


def review_privacy(project: Path, add) -> None:
    kb_rows, error = _kb_by_id(project)
    if error:
        add("json", error)
    for row in kb_rows.values():
        path = project / str(row.get("path") or "")
        if not path.is_file():
            add("file", f"{row.get('id')}: нет файла базы {row.get('path')}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        blocked = kb.closed_or_secret(text)
        if blocked:
            add("closed-data", f"{row.get('id')}: {blocked}")
        origin = str(row.get("origin") or "")
        if origin in {"own", "interview"} and not row.get("synthetic"):
            kinds = interview.pii_kinds(text)
            if kinds:
                add(
                    "pii",
                    f"{path.relative_to(project).as_posix()}: персональные данные ("
                    + ", ".join(kinds)
                    + "). В репозитории их не хранят.",
                )
        if row.get("synthetic") and SYNTHETIC_MARK not in text.casefold():
            add(
                "synthetic-interview",
                f"{row.get('id')}: синтетическая запись базы без слова «синтетическ».",
            )
    folder = project / "interviews"
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("I*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(project).as_posix()
        kinds = interview.pii_kinds(text)
        if kinds:
            add(
                "pii",
                f"{relative}: персональные данные (" + ", ".join(kinds) + "). В репозитории их не хранят.",
            )
        blocked = kb.closed_or_secret(text)
        if blocked:
            add("closed-data", f"{relative}: {blocked}")
        synthetic = "<!-- synthetic: true -->" in text
        if synthetic:
            if SYNTHETIC_MARK not in text.casefold() or NOT_REAL not in text.casefold():
                add(
                    "synthetic-interview",
                    f"{relative}: синтетическое интервью должно быть помечено словом «синтетическ» "
                    "и фразой «не является реальным интервью».",
                )
        elif path.name.endswith("-analysis.md"):
            continue
        elif re.fullmatch(r"I\d+\.md", path.name):
            if interview.REAL_CONSENT not in text:
                add(
                    "consent",
                    f"{relative}: нет строки «{interview.REAL_CONSENT}». "
                    "Реальная расшифровка без согласия в репозитории не хранится.",
                )
            if NOT_REAL in text.casefold():
                add(
                    "synthetic-interview",
                    f"{relative}: файл без пометки synthetic содержит фразу, что интервью не реальное. "
                    "Синтетику помечают <!-- synthetic: true -->.",
                )


def review_temp_files(project: Path, add) -> None:
    """Argument scratch files are not project material."""
    if not project.is_dir():
        return
    pattern = re.compile(r"(?i)^_tmp_|^tmp_.*\.txt$|\.tmp$")
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not pattern.search(path.name):
            continue
        add(
            "temp-file",
            f"{relative.as_posix()}: посторонний временный файл в папке проекта. "
            "Несколько полей передавайте одним JSON в stdin (--json -), не файлами _tmp_.",
            warning=True,
        )


def review_project_layout(project: Path, add) -> None:
    """Raw inputs belong in sources/ or in kb/docs written by kb add."""
    if not project.is_dir():
        return
    for child in sorted(project.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith(".") or child.name in ALLOWED_PROJECT_DIRS:
            continue
        files = [path for path in child.rglob("*") if path.is_file()]
        if child.name == AGENT_LOG_DIR:
            files = [path for path in files if path.suffix.lower() not in AGENT_LOG_SUFFIXES]
        if not files:
            continue
        shown = ", ".join(path.relative_to(project).as_posix() for path in files[:4])
        add(
            "materials-folder",
            f"папка {child.name}/ не для сырья ({shown}). "
            "Входные материалы кладите только в sources/ через add-source или в kb/docs через kb add. "
            "Копия письма или таблицы в другой папке проекта — след, которого быть не должно.",
        )


def review_substantive_numbers(
    project: Path,
    texts: dict,
    sources: list[dict],
    evidence: list[dict],
    add,
) -> None:
    """Numbers in synthesis files need [S]/[E]/[A.metric] from this registry.

    strategy.md keeps its own section check. log.md is not a claim file.
    A template file is skipped. The number tokens match the benchmark scorer.
    """
    import reply_check

    source_ids = {str(row.get("id")) for row in sources if row.get("id")}
    evidence_ids = {str(row.get("id")) for row in evidence if row.get("id")}
    for name in SUBSTANTIVE_NUMBER_FILES:
        text = texts.get(name)
        if not text or "<!-- template: empty -->" in text:
            continue
        import source_check

        label_list = source_check.registry_labels(project)
        for line, sentence in reply_check._sentences(text):
            expanded = source_check.expand_number_words(sentence)
            numbers = [
                token
                for token in scorer_numbers(expanded)
                if not source_check.token_only_inside_labels(expanded, token, label_list)
            ]
            citations = reply_check._citations(sentence)
            valid = False
            for label, analysis_id, metric in citations:
                problem = reply_check._citation_ok(
                    project, label, analysis_id, metric, source_ids, evidence_ids
                )
                if problem is None:
                    valid = True
                    continue
                if label.startswith("E"):
                    continue
                add(
                    "ref",
                    f"{name}:{line}: ссылка [{label}] не найдена в реестре проекта ({problem}).",
                )
            if valid or not numbers:
                continue
            shown = ", ".join(numbers)
            snippet = re.sub(r"\s+", " ", sentence).strip()
            if len(snippet) > 140:
                snippet = snippet[:137] + "..."
            add(
                "ungrounded-number",
                f"{name}:{line}: число {shown} без ссылки [S…], [E…] или [A….метрика] "
                f"в том же предложении ({snippet}). "
                "День в дате — тоже число. Поставьте id из реестра в это предложение или уберите число.",
            )
