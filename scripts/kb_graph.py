#!/usr/bin/env python3
"""Knowledge base by links: the graph S -> E -> U -> option -> strategy claim.

Edges are read from what the project already stores, nothing is guessed:
- evidence.jsonl: E relies on its source S and on E/U in depends_on;
- assumptions.jsonl: U relies on ids named in derivation, note, because;
- options.md: option O relies on ids cited inside its "### O<n>." section;
- strategy.md, validation.md, decisions.md, situation.md: each line that cites
  ids is a claim node "file:line"; it relies on those ids and on options it names.

trace walks the graph both ways: "up" = what X rests on, "down" = what depends on X.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

ID_RE = re.compile(r"(?<![A-Za-z0-9])([SEUA]\d{3,})(?![0-9])")
OPTION_ID_RE = re.compile(r"(?<![A-Za-z0-9])(O\d{1,3})(?![0-9])")
OPTION_HEAD_RE = re.compile(r"^#{2,4}\s+(O\d+)[.:)\s]", re.M)
CLAIM_FILES = ("situation.md", "strategy.md", "validation.md", "decisions.md")
NODE_RE = re.compile(r"^(?:[SEUA]\d{3,}|O\d{1,3}|[\w.-]+\.md:\d+)$")


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _refs(text: str) -> list[str]:
    found: list[str] = []
    for token in ID_RE.findall(text or ""):
        if token not in found:
            found.append(token)
    return found


class Graph:
    def __init__(self) -> None:
        self.up: dict[str, set[str]] = {}
        self.down: dict[str, set[str]] = {}
        self.label: dict[str, str] = {}
        self.status: dict[str, str] = {}

    def node(self, node: str, label: str = "", status: str = "") -> None:
        self.up.setdefault(node, set())
        self.down.setdefault(node, set())
        if label and not self.label.get(node):
            self.label[node] = label
        if status:
            self.status[node] = status

    def rely(self, child: str, parent: str) -> None:
        """child rests on parent."""
        if child == parent:
            return
        self.node(child)
        self.node(parent)
        self.up[child].add(parent)
        self.down[parent].add(child)


def _clip(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build(project: Path) -> Graph:
    graph = Graph()
    for row in _jsonl(project / "sources.jsonl"):
        if row.get("id"):
            graph.node(str(row["id"]), _clip(row.get("title") or row.get("path") or row.get("url") or ""), str(row.get("status") or ""))
    for row in _jsonl(project / "evidence.jsonl"):
        eid = str(row.get("id") or "")
        if not eid:
            continue
        graph.node(eid, _clip(row.get("claim") or ""), str(row.get("status") or "active"))
        if row.get("source"):
            graph.rely(eid, str(row["source"]))
        for dep in row.get("depends_on") or []:
            graph.rely(eid, str(dep))
        if row.get("analysis"):
            graph.rely(eid, str(row["analysis"]))
    for row in _jsonl(project / "assumptions.jsonl"):
        uid = str(row.get("id") or "")
        if not uid:
            continue
        graph.node(uid, _clip(f"{row.get('name') or ''} = {row.get('value') or ''} {row.get('unit') or ''}"), str(row.get("status") or "active"))
        text = " ".join(str(row.get(key) or "") for key in ("derivation", "note", "because"))
        for ref in _refs(text):
            graph.rely(uid, ref)
    options_text = _read(project / "options.md")
    heads = list(OPTION_HEAD_RE.finditer(options_text))
    for index, head in enumerate(heads):
        oid = head.group(1)
        end = heads[index + 1].start() if index + 1 < len(heads) else len(options_text)
        section = options_text[head.start() : end]
        title = section.splitlines()[0].lstrip("#").strip()
        graph.node(oid, _clip(title))
        for ref in _refs(section):
            graph.rely(oid, ref)
    for name in CLAIM_FILES:
        text = _read(project / name)
        for lineno, line in enumerate(text.splitlines(), 1):
            refs = _refs(line)
            options = [o for o in OPTION_ID_RE.findall(line) if o in graph.up]
            if not refs and not options:
                continue
            if not refs and name != "strategy.md":
                continue
            claim = f"{name}:{lineno}"
            graph.node(claim, _clip(line))
            for ref in refs:
                graph.rely(claim, ref)
            for option in options:
                graph.rely(claim, option)
    return graph


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def walk(graph: Graph, start: str, direction: str, depth: int = 6) -> list[tuple[int, str, str]]:
    """BFS. Returns (distance, node, via) without repeats; cycles are safe."""
    edges = graph.up if direction == "up" else graph.down
    seen = {start}
    out: list[tuple[int, str, str]] = []
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, dist = queue.popleft()
        if dist >= depth:
            continue
        for nxt in sorted(edges.get(node, ()), key=_order):
            if nxt in seen:
                continue
            seen.add(nxt)
            out.append((dist + 1, nxt, node))
            queue.append((nxt, dist + 1))
    return out


def _order(node: str) -> tuple:
    match = re.match(r"([A-Za-z]+)(\d+)$", node)
    if match:
        return (0, match.group(1), int(match.group(2)))
    file, _, line = node.partition(":")
    return (1, file, int(line) if line.isdigit() else 0)


def trace(project: Path, node: str, direction: str = "both", depth: int = 6) -> dict:
    node = node.strip()
    if not NODE_RE.match(node):
        raise ValueError("Узел: S001, E001, U001, A001, O2 или файл:строка, например strategy.md:12.")
    if direction not in {"up", "down", "both"}:
        raise ValueError("direction: up, down или both.")
    graph = build(project)
    if node not in graph.up:
        return {"node": node, "known": False, "up": [], "down": []}
    result = {"node": node, "known": True, "label": graph.label.get(node, ""), "status": graph.status.get(node, "")}
    for side in ("up", "down"):
        rows = walk(graph, node, side, depth) if direction in (side, "both") else []
        result[side] = [
            {"node": n, "distance": d, "via": via, "label": graph.label.get(n, ""), "status": graph.status.get(n, "")}
            for d, n, via in rows
        ]
    return result


def format_trace(result: dict) -> str:
    node = result["node"]
    if not result.get("known"):
        return f"Узел {node} в графе проекта не найден: на него никто не ссылается и он не зарегистрирован."
    lines = [f"Узел: {node}" + (f" — {result['label']}" if result.get("label") else "")]
    if result.get("status") and result["status"] not in {"active", ""}:
        lines.append(f"Статус: {result['status']}")
    titles = {
        "up": "На чём держится (вверх по опорам):",
        "down": "Что от него зависит (вниз по ссылкам):",
    }
    for side in ("up", "down"):
        rows = result.get(side)
        if rows is None:
            continue
        lines.append(titles[side])
        if not rows:
            lines.append("- ничего")
        for row in rows:
            mark = f" [{row['status']}]" if row.get("status") not in (None, "", "active") else ""
            label = f" — {row['label']}" if row.get("label") else ""
            lines.append(f"- {'  ' * (row['distance'] - 1)}{row['node']}{mark} (через {row['via']}){label}")
    weak = [r["node"] for side in ("up",) for r in (result.get(side) or []) if r.get("status") in {"retracted", "superseded"}]
    if weak:
        lines.append("Внимание: опоры отозваны или замещены: " + ", ".join(weak) + ". Пересмотрите вывод (impact, propose-revision).")
    return "\n".join(lines)
