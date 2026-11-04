#!/usr/bin/env python3
"""Print the text of a project material. Office formats need optional packages."""

from __future__ import annotations

import sys
from pathlib import Path


def die(message: str) -> None:
    print(message)
    raise SystemExit(1)


def read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        die(
            "Для PDF нужен пакет pypdf: pip install pypdf. "
            "Ядро агента работает и без него — откройте файл в IDE и перенесите цитату вручную."
        )
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        die(
            "Для DOCX нужен пакет python-docx: pip install python-docx. "
            "Ядро агента работает и без него — откройте файл в IDE и перенесите цитату вручную."
        )
    document = docx.Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def read_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        die(
            "Для XLSX нужен пакет openpyxl: pip install openpyxl. "
            "Ядро агента работает и без него — откройте таблицу в IDE и перенесите цитату вручную."
        )
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    chunks: list[str] = []
    for sheet in workbook.worksheets:
        chunks.append(f"# {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            chunks.append("\t".join("" if cell is None else str(cell) for cell in row))
    workbook.close()
    return "\n".join(chunks)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die("Форма: python scripts/read_material.py <файл>")
    path = Path(argv[1])
    if not path.is_file():
        die(f"Файл не найден: {path}")
    ext = path.suffix.lower()
    if ext in {".md", ".txt", ".csv", ".json", ".jsonl"}:
        sys.stdout.write(path.read_text(encoding="utf-8"))
        return 0
    readers = {".pdf": read_pdf, ".docx": read_docx, ".xlsx": read_xlsx}
    if ext not in readers:
        die(f"Расширение {ext or 'без типа'} не поддерживается. Конвертируйте материал в текст или процитируйте его в IDE.")
    sys.stdout.write(readers[ext](path))
    if not sys.stdout.isatty():
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
