"""Closed list of optional packages. requirements.txt holds a minimum, not a pin.

The core CSV and JSON path does not import these. strat install is the only
installer, and only after the human says yes. A version already at or above
the minimum counts as installed and is not downgraded.
"""

from __future__ import annotations

import importlib.metadata
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


def version_tuple(value: str) -> tuple[int, ...]:
    core = re.split(r"[-+!]", value.strip(), maxsplit=1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        match = re.match(r"(\d+)", piece)
        parts.append(int(match.group(1)) if match else 0)
    return tuple(parts)


def version_at_least(installed: str, minimum: str) -> bool:
    left = version_tuple(installed)
    right = version_tuple(minimum)
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left >= right


def minima() -> dict[str, str]:
    """name>=version lines for the closed list. An exact pin is not a minimum."""
    found: dict[str, str] = {}
    if not REQUIREMENTS.is_file():
        return found
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ">=" not in line:
            continue
        name, version = line.split(">=", 1)
        name = name.strip()
        version = version.strip()
        if name in CATALOG and re.fullmatch(r"[A-Za-z0-9_.+\-]+", version or ""):
            found[name] = version
    return found


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(canonical(name))
    except importlib.metadata.PackageNotFoundError:
        return None


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
    """True when the import works and the installed version is not below the minimum."""
    module = CATALOG[canonical(name)]["import_name"]
    try:
        __import__(module)
    except ImportError:
        return False
    installed = installed_version(name)
    minimum = minima().get(canonical(name))
    if installed and minimum and not version_at_least(installed, minimum):
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
    minimum = minima().get(canon, "")
    installed = installed_version(canon)
    if installed and minimum and not version_at_least(installed, minimum):
        head = f"Пакет {canon} {installed} ниже минимума {minimum}."
    else:
        head = f"Нет пакета {canon}. Нужна версия не ниже {minimum}."
    return (
        f"{head} Для {item['need']} нужен пакет {canon}. "
        f"Без него {item['without']}. "
        "Спросите человека, ставить ли его. "
        f"После явного «да» в этом ходе: {ask_command(canon)}"
    )


def already_message(name: str, installed: str, minimum: str) -> str:
    return (
        f"Пакет {name} {installed} уже установлен, это не ниже {minimum}. "
        "Понижать версию не нужно. Ничего не ставится."
    )


def ask_block(name: str, minimum: str, project: str, installed: str | None = None) -> str:
    item = CATALOG[name]
    if installed:
        state = f"Пакет {name} {installed} ниже минимума {minimum}."
    else:
        state = f"Пакет {name} не установлен. Нужна версия не ниже {minimum}."
    return (
        f"{state}\n"
        f"Зачем: без него {item['without']}.\n"
        "Установка не выполнена. Спросите человека. "
        "Флаг --confirmed ставится только после явного «да» в этом ходе.\n"
        f"После согласия: python scripts/strat.py install {project} {name} --confirmed\n"
        f"Ставится {name}>={minimum}. Уже установленную совместимую версию это не понижает."
    )


def _dangerous(spec: str) -> bool:
    if not spec or spec[:1] in "-/\\." or any(char in spec for char in " \t@\\"):
        return True
    folded = spec.casefold()
    if "://" in spec or folded.startswith("git+") or "/" in spec:
        return True
    return False


def plan(spec: str) -> tuple[str, str, str | None, list[str]]:
    """Return the pip name, minimum, requested floor and the only argv install may run.

    requested is None for a bare name (anything at or above the minimum).
    An explicit version below the minimum is rejected. The argv never pins a
    downgrade to the minimum itself.
    """
    raw = spec.strip()
    if _dangerous(raw):
        raise InstallRejected(
            "URL, путь, git+ и лишние флаги отклонены. "
            "Ставится только имя из белого списка. Ничего не установлено."
        )
    requested: str | None = None
    if "==" in raw or ">=" in raw:
        separator = "==" if "==" in raw else ">="
        name, version = raw.split(separator, 1)
        if not re.fullmatch(r"[A-Za-z0-9_.+\-]+", version or ""):
            raise InstallRejected(f"Версия «{version}» не принята. Ничего не установлено.")
        requested = version
        explicit = separator
    else:
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", raw):
            raise InstallRejected(f"Пакет «{raw}» вне белого списка. Ничего не установлено.")
        name, explicit = raw, None
    canon = canonical(name)
    minimum = minima().get(canon)
    if not minimum:
        raise InstallRejected(
            f"Для {canon} нет нижней границы в requirements.txt. Ничего не установлено."
        )
    if requested is not None and not version_at_least(requested, minimum):
        raise InstallRejected(
            f"Версия {canon} {requested} ниже минимума {minimum}. Ничего не установлено."
        )
    if explicit == "==" and requested is not None:
        requirement = f"{canon}=={requested}"
    elif explicit == ">=" and requested is not None:
        requirement = f"{canon}>={requested}"
    else:
        requirement = f"{canon}>={minimum}"
    argv = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        requirement,
    ]
    return canon, minimum, requested, argv


def request_met(installed: str | None, minimum: str, requested: str | None) -> bool:
    if not installed or not version_at_least(installed, minimum):
        return False
    if requested and not version_at_least(installed, requested):
        return False
    return True
