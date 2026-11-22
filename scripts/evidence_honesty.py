"""Synthetic and test evidence must not read as confirmation of a real-world claim.

Three general checks, no list of projects or briefs:

* wording: a sentence that cites synthetic evidence and says it confirms, proves
  or supports something (``подтвержда*``, ``доказыва*``, ``подкрепля*``) without a
  negation right before the verb;
* basis: a decision block in decisions.md, or strategy.md as a whole, whose cited
  evidence is all synthetic. A rehearsal decision says so with ``- kind: rehearsal``.
  In a project where every source is synthetic (a drill, a benchmark) this is a warning;
* test inputs: a source marked ``"test": true`` (``add-source --test``) is a
  rehearsal of new data. Its evidence cannot be high confidence, and a sentence
  citing it carries the word «тестов». A source file that calls itself a test
  input but is not marked gets a warning.
"""

from __future__ import annotations

import re
from pathlib import Path

CITE_RE = re.compile(r"\[(E\d+)\]")
# Assertions only: present and past forms and the nouns. «Что подтвердит», «подтвердить»,
# «доказать» describe a future test, not a claim that something was confirmed.
CONFIRM_RE = re.compile(
    r"(?i)подтвержда\w*|подтвердил\w*|подтвержд[её]н\w*|доказыва\w*|доказал\w*|доказан\w*|доказательств\w*"
    r"|подкрепля\w*|подкрепил\w*|подкрепл[её]н\w*|\bconfirm(?:s|ed)\b|\bprove[sdn]\b|\bvalidated\b"
)
# «не», «без», «ни», «нельзя считать», «not», «no» within a couple of words before the verb.
NEGATION_RE = re.compile(
    r"(?i)(?:^|[\s(«\"])(?:не|без|ни|нельзя|not|no|never|without)(?:\s+\S+){0,2}\s*$"
)
TEST_LABEL = "тестов"
TEST_SELF_DESCRIPTION_RE = re.compile(
    r"(?i)тестов\w*\s+(?:\w+\s+){0,2}(?:сведени|данн|вход|материал)|\btest\s+(?:input|data)\b|\binjected\b"
)
REHEARSAL_RE = re.compile(r"(?mi)^\s*-\s*kind:\s*rehearsal\b")
DECISION_RE = re.compile(r"(?m)^##\s+D\d+\.")
SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|\n")
CLAIM_FILES = (
    "brief.md",
    "research_plan.md",
    "situation.md",
    "options.md",
    "strategy.md",
    "validation.md",
    "decisions.md",
    "deliverable.md",
)


def _plain(text: str) -> str:
    return text.replace("*", "").replace("_", " ")


def confirms(text: str) -> bool:
    """True when the text asserts confirmation that is not negated just before the verb."""
    plain = _plain(text)
    for match in CONFIRM_RE.finditer(plain):
        if not NEGATION_RE.search(plain[: match.start()]):
            return True
    return False


def _sentences(text: str) -> list[str]:
    return [part for part in SENTENCE_RE.split(text) if part.strip()]


def _is_template(text: str) -> bool:
    return "<!-- template: empty -->" in text


def _decision_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(DECISION_RE.finditer(text))
    blocks = []
    for index, match in enumerate(matches):
        end = len(text)
        following = re.search(r"(?m)^##\s", text[match.end() :])
        if following:
            end = match.end() + following.start()
        if index + 1 < len(matches):
            end = min(end, matches[index + 1].start())
        block = text[match.start() : end]
        blocks.append((block.splitlines()[0].strip(), block))
    return blocks


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def is_test_source(row: dict | None) -> bool:
    return bool(row) and row.get("test") is True


def review(project: Path, texts: dict, sources: list[dict], evidence: list[dict], add) -> None:
    by_source = {str(row.get("id")): row for row in sources}
    # A wholly synthetic project (a drill or a benchmark scenario) has nothing else to stand on and
    # nothing real to be mistaken for: findings about synthetic wording and basis are warnings there.
    observed = any(row.get("kind") != "synthetic" for row in sources)
    basis_hint = "" if observed else " Проект целиком синтетический, поэтому это предупреждение: решение здесь — учебное."
    drill_hint = "" if observed else " Проект целиком синтетический, поэтому это предупреждение."
    by_evidence = {str(row.get("id")): row for row in evidence}

    def synthetic(evidence_id: str) -> bool:
        row = by_evidence.get(evidence_id)
        if not row:
            return False
        source = by_source.get(str(row.get("source")))
        return row.get("type") == "synthetic" or bool(source and source.get("kind") == "synthetic")

    def from_test(evidence_id: str) -> bool:
        row = by_evidence.get(evidence_id)
        return bool(row) and is_test_source(by_source.get(str(row.get("source"))))

    for row in evidence:
        if row.get("status") not in (None, "active"):
            continue
        evidence_id = str(row.get("id"))
        if synthetic(evidence_id) and confirms(str(row.get("claim") or "")):
            add(
                "synthetic-claim",
                f"evidence.jsonl {evidence_id}: синтетическая запись говорит, что что-то подтверждает или доказывает. "
                "Синтетика ничего о рынке не подтверждает: перепишите claim («синтетическая реплика иллюстрирует…») "
                "через supersede." + drill_hint,
                warning=not observed,
            )
        if from_test(evidence_id) and row.get("confidence") == "high":
            add(
                "test-source",
                f"evidence.jsonl {evidence_id}: тезис из тестового источника {row.get('source')} записан с confidence=high. "
                "Тестовое сведение — репетиция, не наблюдение: не выше med.",
            )

    for row in sources:
        source_id = str(row.get("id"))
        if is_test_source(row):
            if row.get("reliability") == "high":
                add(
                    "test-source",
                    f"sources.jsonl {source_id}: тестовый источник с reliability=high. Это не наблюдение.",
                )
            continue
        ref = str(row.get("url_or_path") or "")
        if not ref or re.match(r"https?://", ref):
            continue
        path = project / ref
        if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        if TEST_SELF_DESCRIPTION_RE.search(head):
            add(
                "test-source-unmarked",
                f"{ref}: файл называет себя тестовым сведением, а {source_id} не помечен как тестовый. "
                "Зарегистрируйте его через add-source --test, чтобы выводы на нём не выглядели наблюдением.",
                warning=True,
            )

    for name in CLAIM_FILES:
        body = texts.get(name) if name in texts else _read(project / name)
        if not body or _is_template(body):
            continue
        for lineno, line in enumerate(body.splitlines(), 1):
            if not CITE_RE.search(line):
                continue
            for sentence in _sentences(line):
                ids = CITE_RE.findall(sentence)
                if not ids:
                    continue
                synthetic_ids = [evidence_id for evidence_id in ids if synthetic(evidence_id)]
                if synthetic_ids and confirms(sentence):
                    add(
                        "synthetic-wording",
                        f"{name}:{lineno}: [{synthetic_ids[0]}] синтетика рядом со словом «подтверждает/доказывает». "
                        "Синтетика не подтверждает гипотезу; пишите «иллюстрирует», «гипотеза для проверки»."
                        + drill_hint,
                        warning=not observed,
                    )
                test_ids = [evidence_id for evidence_id in ids if from_test(evidence_id)]
                if test_ids and TEST_LABEL not in sentence.casefold():
                    add(
                        "test-label",
                        f"{name}:{lineno}: [{test_ids[0]}] из тестового источника без пометки «тестовое».",
                    )

    decisions = texts.get("decisions.md")
    if decisions and not _is_template(decisions):
        for title, block in _decision_blocks(decisions):
            if REHEARSAL_RE.search(block):
                continue
            ids = [evidence_id for evidence_id in CITE_RE.findall(block) if evidence_id in by_evidence]
            if ids and all(synthetic(evidence_id) for evidence_id in ids):
                add(
                    "synthetic-basis",
                    f"decisions.md «{title}»: все свидетельства решения синтетические ({', '.join(sorted(set(ids)))}). "
                    "Синтетика не может быть единственным основанием решения. Добавьте наблюдение "
                    "или пометьте блок строкой «- kind: rehearsal», если это репетиция, а не решение." + basis_hint,
                    warning=not observed,
                )
    strategy = texts.get("strategy.md")
    if strategy and not _is_template(strategy):
        ids = [evidence_id for evidence_id in CITE_RE.findall(strategy) if evidence_id in by_evidence]
        if ids and all(synthetic(evidence_id) for evidence_id in ids):
            add(
                "synthetic-basis",
                "strategy.md опирается только на синтетические свидетельства. Нужна хотя бы одна опора-наблюдение."
                + basis_hint,
                warning=not observed,
            )
