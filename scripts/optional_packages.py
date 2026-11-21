"""Closed list of optional packages. Versions come only from requirements.txt.

The core CSV and JSON path does not import these. strat install is the only
installer, and only after the human says yes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"

CATALOG: dict[str, dict[str, str]] = {
    "pypdf": {
        "import_name": "pypdf",
        "need": "PDF",
        "without": "не читается текст PDF в read_material, inspect и сверке чисел в check",
    },
    "python-docx": {
        "import_name": "docx",
        "need": "DOCX",
        "without": "не читается DOCX в read_material",
    },
    "openpyxl": {
        "import_name": "openpyxl",
        "need": "XLSX",
        "without": "не читается XLSX в read_material и analyze",
    },
    "pandas": {
        "import_name": "pandas",
        "need": "XLSX",
        "without": "нет запасного чтения XLSX, если openpyxl тоже отсутствует",
    },
    "matplotlib": {
        "import_name": "matplotlib",
        "need": "построения PNG-графика",
        "without": "не строится PNG-график analyze",
    },
    "sentence-transformers": {
        "import_name": "sentence_transformers",
        "need": "семантического поиска",
        "without": "семантический поиск базы знаний недоступен, остаётся BM25",
    },
}
ALIASES = {
    "docx": "python-docx",
    "sentence_transformers": "sentence-transformers",
}


class InstallRejected(ValueError):
    """The package, version, URL or flag is outside the closed list."""


def pins() -> dict[str, str]:
    """Exact name==version lines for the closed list. Comments and ranges are ignored."""
    found: dict[str, str] = {}
    if not REQUIREMENTS.is_file():
        return found
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        name = name.strip()
        version = version.strip()
        if name in CATALOG and version:
            found[name] = version
    return found


def canonical(name: str) -> str:
    folded = name.strip().casefold()
    if folded in ALIASES:
        return ALIASES[folded]
    for item in CATALOG:
        if item.casefold() == folded:
            return item
    allowed = ", ".join(CATALOG)
    raise InstallRejected(
        f"Пакет {name} вне белого списка. Разрешены только: {allowed}. Ничего не установлено."
    )


def present(name: str) -> bool:
    module = CATALOG[canonical(name)]["import_name"]
    try:
        __import__(module)
    except ImportError:
        return False
    return True


def without(name: str) -> str:
    return CATALOG[canonical(name)]["without"]


def ask_command(name: str) -> str:
    canon = canonical(name)
    return f"python scripts/strat.py install <проект> {canon} --confirmed"


def missing_message(name: str) -> str:
    canon = canonical(name)
    item = CATALOG[canon]
    pinned = pins().get(canon)
    shown = f"{canon}=={pinned}" if pinned else canon
    return (
        f"Нет пакета {shown}. Для {item['need']} нужен пакет {canon}. "
        f"Без него {item['without']}. "
        "Спросите человека, ставить ли его. "
        f"После явного «да» в этом ходе: {ask_command(canon)}"
    )


def ask_block(name: str, version: str, project: str) -> str:
    item = CATALOG[name]
    return (
        f"Пакет {name}=={version} не установлен.\n"
        f"Зачем: без него {item['without']}.\n"
        "Установка не выполнена. Спросите человека. "
        "Флаг --confirmed ставится только после явного «да» в этом ходе.\n"
        f"После согласия: python scripts/strat.py install {project} {name} --confirmed"
    )


def _dangerous(spec: str) -> bool:
    if not spec or spec[:1] in "-/\\." or any(char in spec for char in " \t@\\"):
        return True
    folded = spec.casefold()
    if "://" in spec or folded.startswith("git+") or "/" in spec:
        return True
    return False


def plan(spec: str) -> tuple[str, str, list[str]]:
    """Return the pip name, pinned version and the only argv install may run."""
    raw = spec.strip()
    if _dangerous(raw):
        raise InstallRejected(
            "URL, путь, git+ и лишние флаги отклонены. "
            "Ставится только имя из белого списка. Ничего не установлено."
        )
    if "==" in raw:
        name, version = raw.split("==", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.+\-]+", version or ""):
            raise InstallRejected(
                f"Версия «{version}» вне списка. Ничего не установлено."
            )
    else:
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", raw):
            raise InstallRejected(
                f"Пакет «{raw}» вне белого списка. Ничего не установлено."
            )
        name, version = raw, None
    canon = canonical(name)
    pinned = pins().get(canon)
    if not pinned:
        raise InstallRejected(
            f"Для {canon} нет закреплённой версии в requirements.txt. Ничего не установлено."
        )
    if version is not None and version != pinned:
        raise InstallRejected(
            f"Версия {canon}=={version} вне списка. Закреплено только {canon}=={pinned}. "
            "Ничего не установлено."
        )
    argv = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        f"{canon}=={pinned}",
    ]
    return canon, pinned, argv
