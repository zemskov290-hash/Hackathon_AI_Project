#!/usr/bin/env python3
"""DOM, image, PDF and transcript inspection. CLI and MCP call these functions.

Prices, headings, CTAs and steps come from the markup that is actually there.
A model description of a picture is stored apart from that and marked as such.
Audio and video without a transcript are not transcribed.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import zlib
from html.parser import HTMLParser
from pathlib import Path

ID_RE = re.compile(r"^M\d{3,}$")
PRICE_RE = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?\s*(?:₽|руб\.?|р\.|RUB|USD|EUR|€|\$)",
    re.IGNORECASE,
)
CTA_CLASS_RE = re.compile(r"(?:^|[\s_-])(?:btn|button|cta)(?:$|[\s_-])", re.IGNORECASE)
CTA_TEXT_RE = re.compile(
    r"подключ|купить|оформи|выбрать|перейти|начать|попробова|заказать|войти|регистр|"
    r"subscribe|\bbuy\b|get started|sign up|\border\b|добавить",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-()]{0,3}\d{3}[\s\-()]{0,3}\d{3}[\s\-]{0,3}\d{2}[\s\-]{0,3}\d{2}")
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}
SKIP_TAGS = {"script", "style", "noscript"}
SCREEN_TAGS = {"section", "main", "nav", "header", "footer", "form"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
AV_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".mkv", ".avi"}
TRANSCRIPT_SUFFIXES = {".vtt", ".srt", ".txt", ".md"}
MAX_SNAPSHOT_BYTES = 20_000_000


class InspectError(Exception):
    """A user-visible media failure. The message is Russian."""


class Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []


class DomBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack: list[Node] = [self.root]
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {key: value or "" for key, value in attrs}
        node = Node(tag, mapping)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)
        if self._in_title and data.strip():
            self.title_parts.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)


def parse_html(html: str) -> tuple[Node, str]:
    parser = DomBuilder()
    parser.feed(html)
    parser.close()
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    return parser.root, title


def text_of(node: Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in SKIP_TAGS:
            parts.append(text_of(child))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def preorder(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if current.tag in SKIP_TAGS:
            continue
        for child in reversed([item for item in current.children if isinstance(item, Node)]):
            stack.append(child)


def _walk(node: Node, headings: list[tuple[int, str]], found: dict) -> None:
    if node.tag in SKIP_TAGS:
        return
    if node.tag in HEADING_TAGS:
        level = int(node.tag[1])
        title = text_of(node)
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, title))
        found["headings"].append({"level": level, "text": title})
    for child in node.children:
        if isinstance(child, str):
            for match in PRICE_RE.finditer(child):
                context = " / ".join(title for _level, title in headings if title)
                found["prices"].append({"text": re.sub(r"\s+", " ", match.group(0)).strip(), "context": context})
        else:
            _walk(child, headings, found)


def collect_ctas(root: Node) -> list[dict]:
    found = []
    for node in preorder(root):
        if node.tag in SKIP_TAGS:
            continue
        text = text_of(node)
        href = node.attrs.get("href", "")
        classes = node.attrs.get("class", "")
        role = node.attrs.get("role", "").lower()
        if node.tag == "button" or (node.tag == "input" and node.attrs.get("type", "").lower() in {"submit", "button"}):
            label = text or node.attrs.get("value") or node.attrs.get("aria-label") or ""
            label = re.sub(r"\s+", " ", label).strip()
            if label:
                found.append({"text": label, "tag": node.tag, "href": href})
        elif node.tag == "a":
            label = re.sub(r"\s+", " ", text or node.attrs.get("aria-label") or "").strip()
            interesting = role == "button" or bool(CTA_CLASS_RE.search(classes)) or (
                bool(label) and len(label) <= 80 and bool(CTA_TEXT_RE.search(label))
            )
            if interesting and label:
                found.append({"text": label, "tag": "a", "href": href})
    return found


def collect_steps(root: Node) -> list[list[str]]:
    paths = []
    for node in preorder(root):
        if node.tag != "ol":
            continue
        items = []
        for child in node.children:
            if isinstance(child, Node) and child.tag == "li":
                label = text_of(child)
                if label:
                    items.append(label)
        if items:
            paths.append(items)
    return paths


def collect_screens(root: Node) -> list[dict]:
    screens = []
    for node in preorder(root):
        if node.tag not in SCREEN_TAGS:
            continue
        heading = ""
        for child in preorder(node):
            if child is node:
                continue
            if child.tag in HEADING_TAGS:
                heading = text_of(child)
                if heading:
                    break
        label = heading or node.attrs.get("aria-label", "") or node.attrs.get("id", "")
        label = re.sub(r"\s+", " ", label).strip()
        if not label:
            continue
        screens.append({"tag": node.tag, "id": node.attrs.get("id", ""), "label": label})
    return screens


def extract_dom(html: str) -> dict:
    root, title = parse_html(html)
    found: dict = {"headings": [], "prices": []}
    _walk(root, [], found)
    return {
        "title": title,
        "headings": found["headings"],
        "prices": found["prices"],
        "ctas": collect_ctas(root),
        "steps": collect_steps(root),
        "screens": collect_screens(root),
    }


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise InspectError("Файл не похож на PNG: нет подписи или блока IHDR.")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def jpeg_size(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise InspectError("Файл не похож на JPEG: нет маркера SOI.")
    index = 2
    while index + 3 < len(data):
        if data[index] != 0xFF:
            raise InspectError("Заголовок JPEG повреждён.")
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or index + 1 >= len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    raise InspectError("В JPEG нет размера кадра.")


def image_metadata(path: Path) -> dict:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".png":
        width, height = png_size(data)
        image_format = "png"
    elif suffix in {".jpg", ".jpeg"}:
        width, height = jpeg_size(data)
        image_format = "jpeg"
    else:
        raise InspectError("Изображение: PNG или JPG. Другие форматы не разбираются.")
    return {"format": image_format, "width": width, "height": height, "bytes": len(data)}


def transcript_plain(raw: str) -> str:
    kept: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if stripped.upper().startswith("WEBVTT"):
            continue
        if stripped.upper().startswith("NOTE"):
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        if "-->" in stripped:
            continue
        kept.append(stripped)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def read_pdf(path: Path) -> dict:
    import pdf_text

    try:
        text = pdf_text.read_pdf_pages(path)
    except pdf_text.PdfUnavailable:
        return {
            "status": "unavailable",
            "message": (
                "Текст PDF не извлечён: пакет pypdf не установлен (pip install pypdf). "
                "Ядро работает без него. Содержимое страниц не выдумывается."
            ),
            "pages": [],
        }
    except pdf_text.PdfFailed as exc:
        return {
            "status": "unavailable",
            "message": f"PDF открыть не удалось ({path.name}): {exc}. Текст страниц не выдумывается.",
            "pages": [],
        }
    pages = [{"page": index, "text": chunk} for index, chunk in enumerate(text, start=1)]
    return {"status": "ok", "message": "", "pages": pages}


def contains_pii(text: str) -> str | None:
    if EMAIL_RE.search(text or ""):
        return "В тексте есть адрес почты."
    if PHONE_RE.search(text or ""):
        return "В тексте есть телефон."
    return None


def content_digest(path: Path) -> str:
    import strat

    return strat.content_digest(path)


def rel(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def require_inside(project: Path, raw: str) -> Path:
    import analysis

    try:
        return analysis.require_inside(project, raw)
    except analysis.AnalysisError as exc:
        raise InspectError(str(exc)) from exc


def next_id(project: Path) -> str:
    root = project / "inspect"
    numbers = []
    if root.is_dir():
        for path in root.iterdir():
            match = re.fullmatch(r"M(\d+)", path.name)
            if match:
                numbers.append(int(match.group(1)))
    return f"M{(max(numbers) if numbers else 0) + 1:03d}"


def load_manifest(project: Path, inspect_id: str) -> dict:
    if not ID_RE.fullmatch(inspect_id or ""):
        raise InspectError(f"Идентификатор разбора: M001, получено {inspect_id or 'пусто'}.")
    path = project / "inspect" / inspect_id / "manifest.json"
    if not path.is_file():
        raise InspectError(f"Нет разбора {inspect_id}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InspectError(f"manifest {inspect_id} повреждён: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise InspectError(f"manifest {inspect_id} повреждён.")
    return payload


def copy_snapshot(source: Path, dest: Path) -> None:
    if source.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise InspectError(f"Файл {source.name} больше 20 МБ.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    text_suffixes = {".html", ".htm", ".md", ".txt", ".vtt", ".srt", ".csv", ".json"}
    if suffix in text_suffixes:
        dest.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    else:
        shutil.copyfile(source, dest)


def render_html_report(inspect_id: str, snapshot_rel: str, digest: str, extracted: dict, synthetic: bool) -> str:
    lines = [
        f"# Разбор интерфейса {inspect_id}",
        "",
        "Извлечено из DOM снимка. Строки ниже — текст узлов, не догадка модели.",
        "",
    ]
    if synthetic:
        lines.append("СИНТЕТИЧЕСКИЕ ДАННЫЕ. Вымышленная витрина, не страница реальной компании и не закрытая выгрузка.")
        lines.append("")
    lines.extend([
        f"- inspect_id: {inspect_id}",
        "- kind: ui",
        f"- file: {snapshot_rel}",
        f"- sha256: {digest}",
        f"- synthetic: {'да' if synthetic else 'нет'}",
        f"- title: {extracted.get('title') or 'нет'}",
        "",
        "## Разделы",
        "",
    ])
    if not extracted["headings"]:
        lines.append("- нет заголовков")
    for item in extracted["headings"]:
        lines.append(f"- h{item['level']}: {item['text']}")
    lines.extend(["", "## Экраны", ""])
    if not extracted["screens"]:
        lines.append("- нет section, main, nav, header, footer или form с подписью")
    for item in extracted["screens"]:
        suffix = f"#{item['id']}" if item["id"] else ""
        lines.append(f"- {item['tag']}{suffix}: {item['label']}")
    lines.extend(["", "## Цены", ""])
    if not extracted["prices"]:
        lines.append("- нет цен в тексте узлов")
    for item in extracted["prices"]:
        context = item["context"] or "нет"
        lines.append(f"- {item['text']} | контекст: {context}")
    lines.extend(["", "## CTA", ""])
    if not extracted["ctas"]:
        lines.append("- нет кнопок и ссылок-CTA")
    for item in extracted["ctas"]:
        href = item["href"] or "нет"
        lines.append(f"- {item['text']} | tag={item['tag']} | href={href}")
    lines.extend(["", "## Шаги", ""])
    if not extracted["steps"]:
        lines.append("- нет нумерованных списков ol")
    for index, path_items in enumerate(extracted["steps"], start=1):
        lines.append(f"- path {index}")
        for step_index, step in enumerate(path_items, start=1):
            lines.append(f"  {step_index}. {step}")
    lines.append("")
    return "\n".join(lines)


def render_image_report(inspect_id: str, snapshot_rel: str, digest: str, meta: dict, synthetic: bool) -> str:
    lines = [
        f"# Метаданные изображения {inspect_id}",
        "",
        "Описание пикселей моделью лежит в description.md и помечено model-description. "
        "Пока там нет готового текста или стоит pending, это не факт наблюдения.",
        "",
    ]
    if synthetic:
        lines.append("СИНТЕТИЧЕСКИЕ ДАННЫЕ. Учебный кадр, не скриншот клиента и не материал с персональными данными.")
        lines.append("")
    lines.extend([
        f"- inspect_id: {inspect_id}",
        "- kind: image",
        f"- file: {snapshot_rel}",
        f"- sha256: {digest}",
        f"- synthetic: {'да' if synthetic else 'нет'}",
        f"- format: {meta['format']}",
        f"- width: {meta['width']}",
        f"- height: {meta['height']}",
        f"- bytes: {meta['bytes']}",
        "",
    ])
    return "\n".join(lines)


def description_document(text: str | None) -> str:
    if text and text.strip():
        body = text.strip()
        return (
            "<!-- model-description: ide -->\n\n"
            "Описание сделано моделью IDE. Это не дословный текст пикселей и не факт наблюдения.\n\n"
            f"{body}\n"
        )
    return (
        "<!-- model-description: pending -->\n\n"
        "Описание изображения делает модель IDE в этом файле. "
        "Пока его нет: не выдумывай, что на картинке, и не ставь fact по пикселям.\n"
    )


def render_pdf_report(inspect_id: str, snapshot_rel: str, digest: str, parsed: dict, synthetic: bool) -> str:
    lines = [
        f"# Разбор PDF {inspect_id}",
        "",
        f"- inspect_id: {inspect_id}",
        "- kind: pdf",
        f"- file: {snapshot_rel}",
        f"- sha256: {digest}",
        f"- synthetic: {'да' if synthetic else 'нет'}",
        f"- status: {parsed['status']}",
        "",
    ]
    if parsed["status"] != "ok":
        lines.append(parsed["message"])
        lines.append("")
        return "\n".join(lines)
    lines.append(f"- pages: {len(parsed['pages'])}")
    lines.append("")
    for page in parsed["pages"]:
        lines.append(f"## Страница {page['page']}")
        lines.append("")
        lines.append(page["text"].strip() or "(текстовый слой пуст)")
        lines.append("")
    return "\n".join(lines)


def render_av_report(
    inspect_id: str,
    snapshot_rel: str,
    digest: str,
    transcript_rel: str | None,
    transcript_hash: str | None,
    plain: str | None,
    synthetic: bool,
) -> str:
    lines = [
        f"# Разбор записи {inspect_id}",
        "",
        f"- inspect_id: {inspect_id}",
        "- kind: av",
        f"- file: {snapshot_rel}",
        f"- sha256: {digest}",
        f"- synthetic: {'да' if synthetic else 'нет'}",
        "",
    ]
    if plain is None:
        lines.append(
            "Расшифровки или субтитров нет. Аудио и видео без готового текста не распознаются: "
            "тяжёлые зависимости не подключаются и текст не выдумывается."
        )
        lines.append("")
        lines.append("- transcript: нет")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"- transcript: {transcript_rel}")
    lines.append(f"- transcript_sha256: {transcript_hash}")
    lines.append("")
    lines.append("## Расшифровка")
    lines.append("")
    lines.append(plain or "(пустая расшифровка)")
    lines.append("")
    return "\n".join(lines)


def _kind_for(html: Path | None, image: Path | None, pdf: Path | None, av: Path | None) -> str:
    if html is not None:
        return "ui"
    if image is not None:
        return "image"
    if pdf is not None:
        return "pdf"
    return "av"


def iter_manifests(project: Path):
    root = project / "inspect"
    if not root.is_dir():
        return
    folders = [
        path
        for path in root.iterdir()
        if path.is_dir() and ID_RE.fullmatch(path.name) and (path / "manifest.json").is_file()
    ]
    for folder in sorted(folders, key=lambda item: item.name):
        yield load_manifest(project, folder.name)


def find_existing(project: Path, kind: str, digest: str, transcript_hash: str | None) -> dict | None:
    for manifest in iter_manifests(project):
        if manifest.get("kind") != kind or manifest.get("input_sha256") != digest:
            continue
        if kind == "av" and (manifest.get("transcript_sha256") or None) != transcript_hash:
            continue
        return manifest
    return None


def _clip(text: str, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def format_summary(kind: str, structured: dict) -> str:
    """Short Russian digest printed by the CLI and included in the MCP text."""
    lines = ["Сводка:"]
    if kind == "ui":
        lines.append(f"- title: {_clip(structured.get('title') or 'нет', 120)}")
        headings = list(structured.get("headings") or [])
        for item in headings[:12]:
            lines.append(f"- раздел h{item['level']}: {_clip(item.get('text') or '', 120)}")
        if len(headings) > 12:
            lines.append(f"- разделов ещё: {len(headings) - 12}")
        for item in list(structured.get("screens") or [])[:8]:
            ident = f"#{item['id']}" if item.get("id") else ""
            lines.append(f"- экран {item.get('tag')}{ident}: {_clip(item.get('label') or '', 120)}")
        for item in list(structured.get("prices") or [])[:12]:
            lines.append(
                f"- цена: {_clip(item.get('text') or '', 40)} | {_clip(item.get('context') or 'нет', 80)}"
            )
        for item in list(structured.get("ctas") or [])[:12]:
            lines.append(
                f"- CTA: {_clip(item.get('text') or '', 60)} | tag={item.get('tag')} | href={_clip(item.get('href') or 'нет', 80)}"
            )
        for index, path_items in enumerate(list(structured.get("steps") or [])[:4], start=1):
            shown = "; ".join(_clip(step, 60) for step in list(path_items)[:8])
            lines.append(f"- шаги {index}: {shown}")
        return "\n".join(lines)
    if kind == "image":
        lines.append(f"- format: {structured.get('format')}")
        lines.append(f"- width: {structured.get('width')}")
        lines.append(f"- height: {structured.get('height')}")
        lines.append(f"- bytes: {structured.get('bytes')}")
        return "\n".join(lines)
    if kind == "pdf":
        lines.append(f"- status: {structured.get('status')}")
        lines.append(f"- pages: {structured.get('pages')}")
        if structured.get("message"):
            lines.append(f"- {_clip(structured.get('message') or '', 240)}")
        for index, text in enumerate(list(structured.get("page_text") or [])[:3], start=1):
            if str(text).strip():
                lines.append(f"- страница {index}: {_clip(text, 240)}")
        return "\n".join(lines)
    lines.append(f"- transcript: {'да' if structured.get('transcript') else 'нет'}")
    if structured.get("text"):
        lines.append(f"- текст: {_clip(structured.get('text') or '', 400)}")
    elif not structured.get("transcript"):
        lines.append("- Расшифровки нет, текст не извлечён.")
    return "\n".join(lines)


def bound_extract(kind: str, structured: dict) -> dict:
    """Capped extract for MCP structuredContent."""
    truncated = False

    def take(items: list, limit: int) -> list:
        nonlocal truncated
        if len(items) > limit:
            truncated = True
        return items[:limit]

    if kind == "ui":
        steps_in = list(structured.get("steps") or [])
        steps = []
        for path_items in take(steps_in, 6):
            path_items = list(path_items)
            if len(path_items) > 12:
                truncated = True
            steps.append([_clip(step, 80) for step in path_items[:12]])
        return {
            "title": _clip(structured.get("title") or "", 160),
            "headings": [
                {"level": item.get("level"), "text": _clip(item.get("text") or "")}
                for item in take(list(structured.get("headings") or []), 20)
            ],
            "screens": [
                {"tag": item.get("tag"), "id": item.get("id") or "", "label": _clip(item.get("label") or "")}
                for item in take(list(structured.get("screens") or []), 20)
            ],
            "prices": [
                {"text": _clip(item.get("text") or "", 40), "context": _clip(item.get("context") or "", 120)}
                for item in take(list(structured.get("prices") or []), 20)
            ],
            "ctas": [
                {
                    "text": _clip(item.get("text") or "", 80),
                    "tag": item.get("tag"),
                    "href": _clip(item.get("href") or "", 120),
                }
                for item in take(list(structured.get("ctas") or []), 20)
            ],
            "steps": steps,
            "truncated": truncated,
        }
    if kind == "image":
        return {
            "format": structured.get("format"),
            "width": structured.get("width"),
            "height": structured.get("height"),
            "bytes": structured.get("bytes"),
        }
    if kind == "pdf":
        pages = [str(text) for text in list(structured.get("page_text") or [])]
        if len(pages) > 5:
            truncated = True
        return {
            "status": structured.get("status"),
            "pages": structured.get("pages"),
            "message": _clip(structured.get("message") or "", 300),
            "page_text": [_clip(text, 400) for text in pages[:5]],
            "truncated": truncated or any(len(text) > 400 for text in pages[:5]),
        }
    text = str(structured.get("text") or "")
    return {
        "transcript": bool(structured.get("transcript")),
        "text": _clip(text, 800),
        "truncated": len(text) > 800,
    }


def extract_saved(project: Path, manifest: dict) -> dict:
    """Read the stored snapshot in memory. Does not write."""
    snapshot = project / str(manifest.get("input_path") or "")
    kind = str(manifest.get("kind") or "")
    if kind == "ui":
        return extract_dom(snapshot.read_text(encoding="utf-8", errors="replace"))
    if kind == "image":
        return image_metadata(snapshot)
    if kind == "pdf":
        parsed = read_pdf(snapshot)
        return {
            "status": parsed["status"],
            "pages": len(parsed["pages"]),
            "page_text": [str(page.get("text") or "") for page in parsed["pages"]],
            "message": parsed.get("message") or "",
        }
    plain = None
    transcript_rel = manifest.get("transcript_path")
    if transcript_rel:
        transcript_path = project / str(transcript_rel)
        if transcript_path.is_file():
            plain = transcript_plain(transcript_path.read_text(encoding="utf-8", errors="replace"))
    return {"transcript": plain is not None, "text": plain or ""}


def run_inspect(
    project: Path,
    *,
    html: Path | None = None,
    image: Path | None = None,
    pdf: Path | None = None,
    av: Path | None = None,
    transcript: Path | None = None,
    description: str | None = None,
    synthetic: bool = False,
    force: bool = False,
) -> dict:
    chosen = [item for item in (html, image, pdf, av) if item is not None]
    if len(chosen) != 1:
        raise InspectError("Укажите ровно один файл: --html, --image, --pdf или --av.")
    if description and image is None:
        raise InspectError("Текст описания модели принимается только вместе с изображением.")
    if description and contains_pii(description):
        raise InspectError(
            "В описании изображения есть почта или телефон. В репозиторий не кладут ПДн со скриншотов и записей."
        )
    source = chosen[0]
    kind = _kind_for(html, image, pdf, av)
    transcript_hash = content_digest(transcript) if transcript is not None else None
    if not force:
        existing = find_existing(project, kind, content_digest(source), transcript_hash)
        if existing is not None:
            return {
                "id": existing["id"],
                "manifest": existing,
                "structured": extract_saved(project, existing),
                "gap": bool(existing.get("gap")),
                "report_rel": existing.get("report_path"),
                "already": True,
                "message": "",
            }
    inspect_id = next_id(project)
    folder = project / "inspect" / inspect_id
    folder.mkdir(parents=True)
    try:
        built = _build(project, folder, inspect_id, source, html, image, pdf, av, transcript, description, synthetic)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    built["already"] = False
    return built


def _build(
    project: Path,
    folder: Path,
    inspect_id: str,
    source: Path,
    html: Path | None,
    image: Path | None,
    pdf: Path | None,
    av: Path | None,
    transcript: Path | None,
    description: str | None,
    synthetic: bool,
) -> dict:
    snapshot = folder / ("input" + source.suffix.lower())
    copy_snapshot(source, snapshot)
    digest = content_digest(snapshot)
    snapshot_rel = rel(project, snapshot)
    structured: dict = {}
    source_kind = "media"
    gap = False
    if html is not None:
        if source.suffix.lower() not in {".html", ".htm"}:
            raise InspectError("Для разбора интерфейса нужен файл .html или .htm, снимок DOM, а не текст страницы.")
        extracted = extract_dom(snapshot.read_text(encoding="utf-8", errors="replace"))
        report = render_html_report(inspect_id, snapshot_rel, digest, extracted, synthetic)
        structured = extracted
        source_kind = "ui"
        kind = "ui"
    elif image is not None:
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise InspectError("Изображение: PNG или JPG.")
        meta = image_metadata(snapshot)
        report = render_image_report(inspect_id, snapshot_rel, digest, meta, synthetic)
        (folder / "description.md").write_text(description_document(description), encoding="utf-8", newline="\n")
        structured = meta
        kind = "image"
    elif pdf is not None:
        if source.suffix.lower() != ".pdf":
            raise InspectError("Для --pdf нужен файл .pdf.")
        parsed = read_pdf(snapshot)
        report = render_pdf_report(inspect_id, snapshot_rel, digest, parsed, synthetic)
        structured = {
            "status": parsed["status"],
            "pages": len(parsed["pages"]),
            "page_text": [str(page.get("text") or "") for page in parsed["pages"]],
            "message": parsed.get("message") or "",
        }
        gap = parsed["status"] != "ok"
        kind = "pdf"
    else:
        assert av is not None
        if source.suffix.lower() not in AV_SUFFIXES:
            raise InspectError(
                "Аудио или видео: mp3, wav, m4a, aac, ogg, flac, mp4, webm, mov, mkv, avi. "
                "Без расшифровки текст не извлекается."
            )
        plain = None
        transcript_rel = None
        transcript_hash = None
        if transcript is not None:
            if transcript.suffix.lower() not in TRANSCRIPT_SUFFIXES:
                raise InspectError("Расшифровка: .vtt, .srt, .txt или .md внутри проекта.")
            transcript_copy = folder / ("transcript" + transcript.suffix.lower())
            copy_snapshot(transcript, transcript_copy)
            transcript_rel = rel(project, transcript_copy)
            transcript_hash = content_digest(transcript_copy)
            plain = transcript_plain(transcript_copy.read_text(encoding="utf-8", errors="replace"))
        else:
            gap = True
        report = render_av_report(inspect_id, snapshot_rel, digest, transcript_rel, transcript_hash, plain, synthetic)
        structured = {"transcript": plain is not None, "text": plain or ""}
        kind = "av"
    report_path = folder / "report.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    manifest = {
        "id": inspect_id,
        "kind": kind,
        "source_kind": source_kind,
        "input_path": snapshot_rel,
        "input_sha256": digest,
        "origin": rel(project, source),
        "report_path": rel(project, report_path),
        "description_path": rel(project, folder / "description.md") if image is not None else None,
        "model_description": bool(description and description.strip()) if image is not None else False,
        "synthetic": bool(synthetic),
        "gap": gap,
        "transcript_path": transcript_rel if av is not None else None,
        "transcript_sha256": transcript_hash if av is not None else None,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "id": inspect_id,
        "manifest": manifest,
        "structured": structured,
        "gap": gap,
        "report_rel": manifest["report_path"],
        "message": report if gap else "",
    }


def verify_project(project: Path) -> list[dict]:
    import strat

    findings: list[dict] = []
    root = project / "inspect"
    if root.is_dir():
        for folder in sorted(path for path in root.iterdir() if path.is_dir() and ID_RE.fullmatch(path.name)):
            try:
                manifest = load_manifest(project, folder.name)
            except InspectError as exc:
                findings.append({"code": "media", "message": str(exc), "warning": False})
                continue
            snapshot = project / str(manifest.get("input_path") or "")
            if not snapshot.is_file():
                findings.append(
                    {
                        "code": "media-snapshot",
                        "message": f"{folder.name}: нет снимка {manifest.get('input_path')}.",
                        "warning": True,
                    }
                )
            elif content_digest(snapshot) != manifest.get("input_sha256"):
                findings.append(
                    {
                        "code": "media-snapshot",
                        "message": f"{folder.name}: sha256 снимка не совпадает с manifest.",
                        "warning": False,
                    }
                )
            description = folder / "description.md"
            if description.is_file():
                pii = contains_pii(description.read_text(encoding="utf-8", errors="replace"))
                if pii:
                    findings.append(
                        {
                            "code": "pii",
                            "message": f"{folder.name}: {pii} Уберите ПДн из описания.",
                            "warning": True,
                        }
                    )
    sources, _errors = strat.load_jsonl(project / "sources.jsonl")
    evidence, _evidence_errors = strat.load_jsonl(project / "evidence.jsonl")
    by_source = {row.get("id"): row for row in sources}
    for row in sources:
        if row.get("kind") not in {"ui", "media"}:
            continue
        source_id = str(row.get("id") or "")
        snapshot_path = str(row.get("snapshot_path") or "")
        snapshot_hash = row.get("snapshot_sha256")
        if not snapshot_path or not snapshot_hash:
            findings.append(
                {
                    "code": "media-snapshot",
                    "message": f"{source_id}: источник {row.get('kind')} без снимка и sha256.",
                    "warning": True,
                }
            )
            continue
        path = Path(snapshot_path)
        if not path.is_absolute():
            path = project / path
        if not path.is_file():
            findings.append(
                {
                    "code": "media-snapshot",
                    "message": f"{source_id}: снимок {snapshot_path} не найден.",
                    "warning": True,
                }
            )
        elif content_digest(path) != snapshot_hash:
            findings.append(
                {
                    "code": "media-snapshot",
                    "message": f"{source_id}: sha256 снимка {snapshot_path} не совпадает.",
                    "warning": False,
                }
            )
    for row in evidence:
        source = by_source.get(row.get("source")) or {}
        if not source:
            continue
        model = bool(row.get("model_description") or source.get("model_description"))
        if model and row.get("type") == "fact" and row.get("status") == "active":
            findings.append(
                {
                    "code": "model-description",
                    "message": (
                        f"{row.get('id')}: fact опирается на описание модели. "
                        "Это не наблюдение пикселей; для такого текста нужен hypothesis и пометка в тексте."
                    ),
                    "warning": True,
                }
            )
        if source.get("kind") in {"ui", "media"} and row.get("status") == "active":
            if not source.get("snapshot_path") or not source.get("snapshot_sha256"):
                findings.append(
                    {
                        "code": "media-snapshot",
                        "message": (
                            f"{row.get('id')}: свидетельство из медиа без снимка и sha256 источника {source.get('id')}."
                        ),
                        "warning": True,
                    }
                )
    return findings


def png_bytes(width: int = 1, height: int = 1, rgb: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Small uncompressed-enough PNG for fixtures. RGB, 8 bit."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg_bytes(width: int = 1, height: int = 1) -> bytes:
    """A header-only JPEG large enough for the size parser, not a viewable photo."""
    sof = bytes(
        [
            0xFF,
            0xC0,
            0x00,
            0x0B,
            0x08,
            (height >> 8) & 0xFF,
            height & 0xFF,
            (width >> 8) & 0xFF,
            width & 0xFF,
            0x01,
            0x01,
            0x11,
            0x00,
        ]
    )
    return bytes([0xFF, 0xD8]) + sof + bytes([0xFF, 0xD9])
