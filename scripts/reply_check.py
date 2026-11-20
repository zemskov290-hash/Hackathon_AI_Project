#!/usr/bin/env python3
"""Check a draft reply before it is sent to the person.

A number must sit in the same sentence as a square-bracket id that exists
in this project's registry: [S###], [E###] or [A###.metric]. The benchmark
scorer is a different tool and is not used here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import grounding
import source_check

REPLY_CITE_RE = re.compile(r"\[(S\d+|E\d+|U\d+)\]|\[(A\d+)\.([A-Za-z0-9_]+)\]")
LIST_MARKER_RE = re.compile(r"^(?:[-*]\s+|\d+\.\s+)")


def _strip_comments_keep_lines(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return re.sub(r"<!--.*?-->", repl, text, flags=re.S)


def _sentences(text: str) -> list[tuple[int, str]]:
    """Sentences with the 1-based line where each one starts. Fences are skipped."""
    visible = _strip_comments_keep_lines(text)
    found: list[tuple[int, str]] = []
    paragraph: list[tuple[int, str]] = []
    fence = False

    def emit(chunks: list[tuple[int, str]]) -> None:
        if not chunks:
            return
        index_line: list[int] = []
        pieces: list[str] = []
        for lineno, piece in chunks:
            if pieces:
                pieces.append(" ")
                index_line.append(lineno)
            pieces.append(piece)
            index_line.extend([lineno] * len(piece))
        joined = "".join(pieces)
        start = 0
        spans = list(re.finditer(r"(?<=[.!?])\s+", joined))
        points = [match.end() for match in spans]
        cuts = [match.start() for match in spans] + [len(joined)]
        for cut, end in zip([0, *points], cuts):
            fragment = joined[cut:end].strip()
            if not fragment:
                continue
            origin = index_line[cut] if cut < len(index_line) else chunks[0][0]
            # If strip removed a prefix, keep the line of the first remaining character.
            offset = cut
            while offset < len(joined) and joined[offset].isspace():
                offset += 1
            if offset < len(index_line):
                origin = index_line[offset]
            found.append((origin, fragment))
            start = end
        del start

    def flush() -> None:
        nonlocal paragraph
        emit(paragraph)
        paragraph = []

    for lineno, raw in enumerate(visible.splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush()
            fence = not fence
            continue
        if fence or not stripped or stripped.startswith("#"):
            flush()
            continue
        if LIST_MARKER_RE.match(stripped):
            flush()
            emit([(lineno, LIST_MARKER_RE.sub("", stripped))])
            continue
        paragraph.append((lineno, stripped))
    flush()
    return found


def _load_ids(project: Path) -> tuple[set[str], set[str], list[str]]:
    sources, source_error = grounding._load_jsonl(project / "sources.jsonl")
    evidence, evidence_error = grounding._load_jsonl(project / "evidence.jsonl")
    problems = [item for item in (source_error, evidence_error) if item]
    source_ids = {str(row.get("id")) for row in sources if row.get("id")}
    evidence_ids = {str(row.get("id")) for row in evidence if row.get("id")}
    return source_ids, evidence_ids, problems


def _metric_problem(project: Path, analysis_id: str, metric: str) -> str | None:
    path = project / "analysis" / analysis_id / "result.json"
    if not path.is_file():
        return f"нет analysis/{analysis_id}/result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return f"analysis/{analysis_id}/result.json не читается"
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict) or metric not in metrics:
        return f"в analysis/{analysis_id}/result.json нет метрики {metric}"
    return None


def _citations(sentence: str) -> list[tuple[str, str | None, str | None]]:
    """Return (label, analysis_id, metric). analysis_id is set only for anchors."""
    found = []
    for match in REPLY_CITE_RE.finditer(sentence):
        if match.group(1):
            found.append((match.group(1), None, None))
        else:
            found.append((f"{match.group(2)}.{match.group(3)}", match.group(2), match.group(3)))
    return found


def _citation_ok(
    project: Path,
    label: str,
    analysis_id: str | None,
    metric: str | None,
    source_ids: set[str],
    evidence_ids: set[str],
) -> str | None:
    if analysis_id is not None and metric is not None:
        return _metric_problem(project, analysis_id, metric)
    if label.startswith("S"):
        if label not in source_ids:
            return "нет такой записи в sources.jsonl"
        return None
    if label.startswith("E"):
        if label not in evidence_ids:
            return "нет такой записи в evidence.jsonl"
        return None
    if label.startswith("U"):
        import source_check

        ok, reason = source_check.assumption_status(project, label)
        if not ok:
            return reason or "нет такой записи в assumptions.jsonl"
        return None
    return "неизвестный id"


def check_reply(project: Path, text: str) -> list[dict]:
    """Errors for one draft. Each item has line, code and message."""
    source_ids, evidence_ids, problems = _load_ids(project)
    errors: list[dict] = []
    seen: set[tuple[int, str, str]] = set()

    def add(line: int, code: str, message: str) -> None:
        key = (line, code, message)
        if key in seen:
            return
        seen.add(key)
        errors.append({"line": line, "code": code, "message": message})

    for problem in problems:
        add(0, "registry", problem)

    for line, sentence in _sentences(text):
        sentence = source_check.expand_number_words(sentence)
        citations = _citations(sentence)
        valid = False
        for label, analysis_id, metric in citations:
            problem = _citation_ok(project, label, analysis_id, metric, source_ids, evidence_ids)
            if problem is None:
                valid = True
                continue
            add(
                line,
                "reply-ref",
                f"строка {line}: ссылка [{label}] не найдена в реестре проекта ({problem}).",
            )
        if valid:
            continue
        for token in grounding._numbers(sentence):
            snippet = re.sub(r"\s+", " ", sentence).strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            add(
                line,
                "reply-number",
                f"строка {line}: число {token} без ссылки [S…], [E…], [U…] или [A….метрика] "
                f"в том же предложении ({snippet}). "
                "Поставьте id из реестра в это предложение или уберите число и назовите файл.",
            )
    errors.sort(key=lambda item: (item["line"], item["code"], item["message"]))
    return errors


def format_reply_report(project_name: str, findings: list[dict]) -> str:
    if not findings:
        return f"Проверка ответа {project_name}: OK\nЧисел без ссылки нет."
    lines = [f"Проверка ответа {project_name}: ОШИБКИ ({len(findings)})"]
    lines.extend(f"- [{item['code']}] {item['message']}" for item in findings)
    return "\n".join(lines)
