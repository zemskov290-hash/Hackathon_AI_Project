#!/usr/bin/env python3
"""Print the text of a project material. Office formats need optional packages."""

from __future__ import annotations

import sys
from pathlib import Path

from stdio_utf8 import configure_stdio

configure_stdio()


def die(message: str) -> None:
    print(message)
    raise SystemExit(1)


def finish_extract(label: str, path: Path, text: str) -> str:
    cleaned = text.strip()
    if cleaned:
        return cleaned
    return f"{label} открыт ({path.name}), текстовый слой пуст. Процитируйте фрагмент вручную."


def read_pdf(path: Path) -> str:
    import pdf_text

    try:
        text = pdf_text.read_pdf(path)
    except pdf_text.PdfUnavailable:
        die(
            "Для PDF нужен пакет pypdf: pip install pypdf. "
            "Ядро агента работает и без него — откройте файл в IDE и перенесите цитату вручную."
        )
    except pdf_text.PdfFailed as exc:
        die(f"PDF открыть не удалось ({path.name}): {exc}. Процитируйте фрагмент вручную.")
    return finish_extract("PDF", path, text)


def read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        die(
            "Для DOCX нужен пакет python-docx: pip install python-docx. "
            "Ядро агента работает и без него — откройте файл в IDE и перенесите цитату вручную."
        )
    try:
        document = docx.Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:  # noqa: BLE001
        die(f"DOCX открыть не удалось ({path.name}): {exc}. Процитируйте фрагмент вручную.")
    return finish_extract("DOCX", path, text)


def read_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        die(
            "Для XLSX нужен пакет openpyxl: pip install openpyxl. "
            "Ядро агента работает и без него — откройте таблицу в IDE и перенесите цитату вручную."
        )
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        chunks: list[str] = []
        for sheet in workbook.worksheets:
            chunks.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                chunks.append("\t".join("" if cell is None else str(cell) for cell in row))
        workbook.close()
        text = "\n".join(chunks)
    except Exception as exc:  # noqa: BLE001
        die(f"XLSX открыть не удалось ({path.name}): {exc}. Процитируйте фрагмент вручную.")
    return finish_extract("XLSX", path, text)


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
