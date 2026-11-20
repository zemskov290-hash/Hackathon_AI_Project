#!/usr/bin/env python3
"""Read PDF text and rebuild a table from glyph positions.

Column gaps are a typographic distance, not a list of headers. The same
function works for any brief: words that sit close stay one cell, words
that sit far apart become the next column.
"""

from __future__ import annotations

from pathlib import Path

# A gap wider than this, in PDF points, starts another cell. A space inside
# a word or between two words of one header is narrower.
COLUMN_GAP = 24.0
ROW_TOLERANCE = 3.0


def rows_from_positions(
    items: list[tuple[float, float, str]],
    *,
    column_gap: float = COLUMN_GAP,
    row_tolerance: float = ROW_TOLERANCE,
) -> list[list[str]]:
    """Group (x, y, text) into rows and cells. y grows upward, as in PDF."""
    cleaned = [(x, y, text.strip()) for x, y, text in items if text and text.strip()]
    if not cleaned:
        return []
    cleaned.sort(key=lambda item: (-item[1], item[0]))
    rows: list[list[tuple[float, float, str]]] = []
    for x, y, text in cleaned:
        if rows and abs(rows[-1][0][1] - y) <= row_tolerance:
            rows[-1].append((x, y, text))
        else:
            rows.append([(x, y, text)])
    table: list[list[str]] = []
    for row in rows:
        row.sort(key=lambda item: item[0])
        cells: list[str] = []
        last_end: float | None = None
        for x, _y, text in row:
            if last_end is None or x - last_end > column_gap:
                cells.append(text)
            else:
                cells[-1] = f"{cells[-1]} {text}".strip()
            last_end = x + max(len(text), 1) * 4.5
        if any(cell.strip() for cell in cells):
            table.append(cells)
    return table


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in padded]
    return "\n".join(lines)


def _page_plain_and_items(page: object) -> tuple[str, list[tuple[float, float, str]]]:
    items: list[tuple[float, float, str]] = []

    def visitor(text: str, cm: object, tm: object, font_dict: object, font_size: object) -> None:
        del cm, font_dict, font_size
        if not text or not str(text).strip():
            return
        try:
            x = float(tm[4])  # type: ignore[index]
            y = float(tm[5])  # type: ignore[index]
        except (TypeError, IndexError, ValueError):
            return
        items.append((x, y, str(text)))

    plain = ""
    try:
        plain = page.extract_text(visitor_text=visitor) or ""  # type: ignore[attr-defined]
    except TypeError:
        plain = page.extract_text() or ""  # type: ignore[attr-defined]
    except Exception:
        plain = ""
    layout = ""
    try:
        layout = page.extract_text(extraction_mode="layout") or ""  # type: ignore[attr-defined]
    except TypeError:
        layout = ""
    except Exception:
        layout = ""
    if layout.strip() and layout.strip() != plain.strip():
        plain = plain + "\n" + layout
    return plain, items


class PdfUnavailable(Exception):
    """pypdf is not installed."""


class PdfFailed(Exception):
    """The file did not yield a text layer."""


def read_pdf_pages(path: Path) -> list[str]:
    """One string per page: the text layer plus a table rebuilt from positions."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfUnavailable("pypdf") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise PdfFailed(str(exc)) from exc
    pages: list[str] = []
    for page in reader.pages:
        try:
            plain, items = _page_plain_and_items(page)
        except Exception:
            plain, items = "", []
        table = markdown_table(rows_from_positions(items))
        chunk = plain
        if table:
            chunk = (plain + "\n" + table).strip()
        pages.append(chunk)
    return pages


def read_pdf(path: Path) -> str:
    """Plain text plus a markdown table rebuilt from positions."""
    return "\n\n".join(read_pdf_pages(path))
