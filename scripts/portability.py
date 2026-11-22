#!/usr/bin/env python3
"""Check that IDE adapters point at one core and that their commands actually start.

Live means this process launched a hook, an MCP initialize or the CLI.
It does not mean Cursor, VS Code or Claude Code was opened.
Static means the file was read and parsed.
Unsupported means this repository does not wire that capability for that environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "portability.md"

CAPABILITIES = ("rules", "skills", "subagents", "hooks", "mcp", "cli")
CAPABILITY_TITLES = {
    "rules": "Правила",
    "skills": "Навыки",
    "subagents": "Субагенты",
    "hooks": "Хуки",
    "mcp": "MCP",
    "cli": "CLI",
}
ENVIRONMENTS = (
    ("cursor", "Cursor"),
    ("claude", "Claude Code"),
    ("copilot", "VS Code / Copilot"),
    ("codex", "Codex и прочие"),
)
CORE_BANS = (".env", "human_confirmed", "synthetic", "недоверен", "scripts/")
WEAK_PHRASES = ("можно писать .env", "можно коммитить .env", "synthetic как fact")
SKILL_MIN = 8
SUBAGENTS = (
    ".claude/agents/researcher.md",
    ".claude/agents/critic.md",
    ".claude/agents/respondent.md",
)
RULE_FILES = {
    "cursor": ".cursor/rules/strategist.mdc",
    "claude": "CLAUDE.md",
    "copilot": ".github/copilot-instructions.md",
    "codex": "AGENTS.md",
}
HOOK_FILES = {
    "cursor": ".cursor/hooks.json",
    "claude": ".claude/settings.json",
}
MCP_FILES = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
    "copilot": ".vscode/mcp.json",
}


class PortabilityError(Exception):
    pass


def write_if_changed(path: Path, text: str) -> bool:
    if not text.endswith("\n"):
        text += "\n"
    data = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def read_text(root: Path, rel: str) -> str | None:
    path = root / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def cell(status: str, note: str) -> dict:
    return {"status": status, "note": note}


def shell_problems(command: str) -> list[str]:
    problems = []
    if "$" in command:
        problems.append("в команде есть $")
    if "%" in command:
        problems.append("в команде есть %")
    if command.count('"') != 2:
        problems.append("в команде не ровно одна пара двойных кавычек")
    return problems


INTERPRETERS = ("python", "python3", "py")


def split_hook_command(command: str) -> list[str] | None:
    interpreter, _, rest = command.partition(" ")
    prefix = '-c "'
    if interpreter not in INTERPRETERS or not rest.startswith(prefix) or command.count('"') != 2:
        return None
    rest = rest[len(prefix) :]
    source, _, name = rest.partition('"')
    argv = [interpreter, "-c", source]
    tail = name.strip()
    if tail:
        argv.append(tail)
    return argv


def walk_commands(payload: object, found: list[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "command" and isinstance(value, str):
                found.append(value)
            else:
                walk_commands(value, found)
    elif isinstance(payload, list):
        for item in payload:
            walk_commands(item, found)


def mcp_argv(payload: dict) -> list[str] | None:
    servers = payload.get("mcpServers") or payload.get("servers") or {}
    if not isinstance(servers, dict):
        return None
    item = servers.get("strat")
    if not isinstance(item, dict):
        return None
    command = item.get("command")
    args = item.get("args")
    if command not in INTERPRETERS or not isinstance(args, list) or not args:
        return None
    return [str(command), *[str(arg) for arg in args]]


def argv_problems(argv: list[str]) -> list[str]:
    problems = []
    blob = " ".join(argv)
    if "$" in blob or "%" in blob:
        problems.append("в аргументах есть $ или %")
    for arg in argv[1:]:
        if '"' in arg:
            problems.append("во внутреннем аргументе есть двойная кавычка")
            break
    return problems


def core_problems(text: str | None) -> list[str]:
    if text is None:
        return ["нет AGENTS.md"]
    folded = text.casefold()
    missing = [item for item in CORE_BANS if item.casefold() not in folded]
    return [f"в AGENTS.md нет «{item}»" for item in missing]


def adapter_problems(text: str | None, rel: str) -> list[str]:
    if text is None:
        return [f"нет файла {rel}"]
    problems = []
    if "AGENTS.md" not in text and rel != "AGENTS.md":
        problems.append(f"{rel} не ссылается на AGENTS.md")
    if "human_confirmed" in text and "true" not in text:
        problems.append(f"{rel} упоминает human_confirmed без true")
    folded = text.casefold()
    for phrase in WEAK_PHRASES:
        if phrase.casefold() in folded:
            problems.append(f"{rel} содержит послабление: {phrase}")
    if rel != "AGENTS.md" and "ignore previous instructions" in folded and "игнорируй" not in folded:
        problems.append(f"{rel} содержит ignore previous instructions без запрета")
    return problems


def skill_report(root: Path) -> tuple[dict, list[str]]:
    folder = root / "skills"
    files = sorted(folder.glob("*/SKILL.md")) if folder.is_dir() else []
    problems = []
    if len(files) < SKILL_MIN:
        problems.append(f"навыков с SKILL.md меньше {SKILL_MIN}")
    for path in files:
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:30])
        if "name:" not in head:
            problems.append(f"{path.relative_to(root).as_posix()} без name в начале")
    if problems:
        return cell("error", "; ".join(problems)), problems
    return (
        cell("static", f"{len(files)} файлов skills/*/SKILL.md на месте. Среда сама навыки не исполняет."),
        [],
    )


def subagent_files_ok(root: Path) -> list[str]:
    problems = []
    for rel in SUBAGENTS:
        text = read_text(root, rel)
        if text is None or len(text.strip()) < 80:
            problems.append(f"нет или пуст {rel}")
    return problems


SUBSTITUTED: set[str] = set()


def resolve_interpreter(argv: list[str]) -> list[str]:
    """Run the config's interpreter; if it is not on PATH, use this Python and remember that.

    The IDE would fail with the same config, so the report names the gap and the fix
    (regenerate configs with --python python3) instead of hiding it.
    """
    if shutil.which(argv[0]):
        return argv
    SUBSTITUTED.add(argv[0])
    return [sys.executable, *argv[1:]]


def launch(argv: list[str], stdin: bytes, root: Path, timeout: float = 20) -> subprocess.CompletedProcess[bytes]:
    argv = resolve_interpreter(argv)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["CURSOR_PROJECT_DIR"] = str(root)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env.pop("CURSOR_TRANSCRIPT_PATH", None)
    env.pop("STRAT_PROJECTS_DIR", None)
    with tempfile.TemporaryDirectory(prefix="strat-port-") as tmp:
        return subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            cwd=tmp,
            env=env,
            timeout=timeout,
        )


def hook_payload(name: str, root: Path) -> bytes:
    if name == "guard_shell":
        body = {"command": "python --version", "cwd": str(root)}
    elif name == "guard_read":
        body = {"file_path": str(root / "README.md")}
    else:
        body = {}
    return (json.dumps(body) + "\n").encode("utf-8")


def hook_ok(proc: subprocess.CompletedProcess[bytes], name: str) -> bool:
    if proc.returncode != 0:
        return False
    if name.startswith("guard_"):
        return b"allow" in proc.stdout
    return True


def initialize_ok(proc: subprocess.CompletedProcess[bytes]) -> bool:
    if proc.returncode not in {0, None}:
        # The server exits 0 after stdin closes. A non-zero code is a failure
        # even if a JSON line was printed.
        pass
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        info = (payload.get("result") or {}).get("serverInfo") or {}
        if info.get("name") == "strat":
            return proc.returncode == 0
    return False


def evaluate_hooks(root: Path, rel: str, *, live: bool) -> tuple[dict, list[str]]:
    text = read_text(root, rel)
    if text is None:
        return cell("error", f"нет {rel}"), [f"нет {rel}"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return cell("error", f"{rel} не JSON"), [f"{rel} не JSON: {exc.msg}"]
    commands = []
    walk_commands(payload, commands)
    problems = []
    if not commands:
        problems.append(f"{rel} без команд")
    argv_list = []
    for command in commands:
        problems.extend(f"{rel}: {item}" for item in shell_problems(command))
        argv = split_hook_command(command)
        if argv is None:
            problems.append(f"{rel}: команда не разбирается как python -c")
        else:
            argv_list.append(argv)
    if problems:
        return cell("error", "; ".join(problems)), problems
    if not live:
        return cell("static", f"{rel} разобран, процессы не запускались."), []
    for argv in argv_list:
        name = argv[-1] if len(argv) > 3 else ""
        try:
            proc = launch(argv, hook_payload(name, root), root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            message = f"{rel}: не запустился {name or 'хук'}: {exc}"
            return cell("error", message), [message]
        if not hook_ok(proc, name):
            message = f"{rel}: хук {name or 'команда'} не вернул ожидаемый ответ"
            return cell("error", message), [message]
    return (
        cell("live", "Команды хуков запущены, сторож вернул allow. Клиент IDE не открывался."),
        [],
    )


def evaluate_mcp(root: Path, rel: str, *, live: bool) -> tuple[dict, list[str]]:
    text = read_text(root, rel)
    if text is None:
        return cell("error", f"нет {rel}"), [f"нет {rel}"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return cell("error", f"{rel} не JSON"), [f"{rel} не JSON: {exc.msg}"]
    argv = mcp_argv(payload)
    if argv is None:
        return cell("error", f"{rel} без сервера strat"), [f"{rel} без сервера strat"]
    problems = [f"{rel}: {item}" for item in argv_problems(argv)]
    if problems:
        return cell("error", "; ".join(problems)), problems
    if not live:
        return cell("static", f"{rel} разобран, initialize не запускался."), []
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "portability", "version": "0"},
        },
    }
    stdin = (json.dumps(message) + "\n").encode("utf-8")
    try:
        proc = launch(argv, stdin, root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        message_text = f"{rel}: MCP не запустился: {exc}"
        return cell("error", message_text), [message_text]
    if not initialize_ok(proc):
        message_text = f"{rel}: нет ответа initialize от strat"
        return cell("error", message_text), [message_text]
    return (
        cell("live", "Команда из файла запущена, сервер ответил на initialize. Клиент IDE не открывался."),
        [],
    )


def evaluate_cli(root: Path, *, live: bool) -> tuple[dict, list[str]]:
    script = root / "scripts" / "strat.py"
    if not script.is_file():
        return cell("error", "нет scripts/strat.py"), ["нет scripts/strat.py"]
    if not live:
        return cell("static", "Файл scripts/strat.py на месте, процесс не запускался."), []
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            cwd=root,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return cell("error", f"CLI не запустился: {exc}"), [f"CLI не запустился: {exc}"]
    if proc.returncode != 0:
        return cell("error", "strat.py --help завершился с ошибкой"), ["strat.py --help завершился с ошибкой"]
    return cell("live", "Запущен scripts/strat.py --help. Это запасной путь, если MCP нет."), []


def evaluate(root: Path | None = None, *, live: bool = True) -> dict:
    root = (root or ROOT).resolve()
    errors: list[str] = []
    cells: dict[str, dict[str, dict]] = {}
    core = core_problems(read_text(root, "AGENTS.md"))
    errors.extend(core)
    skill_cell, skill_errors = skill_report(root)
    errors.extend(skill_errors)
    sub_problems = subagent_files_ok(root)
    errors.extend(sub_problems)
    cli_cell, cli_errors = evaluate_cli(root, live=live)
    errors.extend(cli_errors)

    for env_id, _title in ENVIRONMENTS:
        row: dict[str, dict] = {}
        rule_rel = RULE_FILES[env_id]
        rule_text = read_text(root, rule_rel)
        rule_issues = adapter_problems(rule_text, rule_rel)
        if env_id == "codex":
            rule_issues = [item for item in rule_issues if "не ссылается" not in item]
            rule_issues.extend(core)
        if rule_issues:
            row["rules"] = cell("error", "; ".join(rule_issues))
            errors.extend(rule_issues)
        else:
            note = "Файл ссылается на AGENTS.md." if env_id != "codex" else "Ядро правил, отдельного адаптера нет."
            row["rules"] = cell("static", note)

        row["skills"] = skill_cell

        if sub_problems:
            row["subagents"] = cell("error", "; ".join(sub_problems))
        elif env_id == "claude":
            row["subagents"] = cell("static", "Файлы .claude/agents на месте. Клиент их не запускал.")
        elif env_id == "cursor":
            pointer = rule_text or ""
            if ".claude/agents" in pointer:
                row["subagents"] = cell(
                    "static",
                    "Правило Cursor указывает на .claude/agents. Субагент в этом прогоне не запускался.",
                )
            else:
                row["subagents"] = cell("error", "правило Cursor не указывает на .claude/agents")
                errors.append("правило Cursor не указывает на .claude/agents")
        elif env_id == "copilot":
            pointer = (rule_text or "").casefold()
            if "critic" in pointer or "inline" in pointer:
                row["subagents"] = cell(
                    "static",
                    "Инструкция Copilot описывает роль в том же чате. Отдельный субагент средой не запускался.",
                )
            else:
                row["subagents"] = cell("unsupported", "в инструкции Copilot нет роли субагента")
        else:
            row["subagents"] = cell(
                "unsupported",
                "У Codex в этом репозитории нет механизма субагентов. Текст ролей можно прочитать в том же чате.",
            )

        hook_rel = HOOK_FILES.get(env_id)
        if hook_rel is None:
            row["hooks"] = cell("unsupported", "Эта среда в репозитории не подключает хуки. Запреты остаются в AGENTS.md и CLI.")
        else:
            hook_cell, hook_errors = evaluate_hooks(root, hook_rel, live=live)
            row["hooks"] = hook_cell
            errors.extend(hook_errors)

        mcp_rel = MCP_FILES.get(env_id)
        if mcp_rel is None:
            row["mcp"] = cell("unsupported", "Отдельного MCP-конфига нет. Запасной путь — CLI.")
        else:
            mcp_cell, mcp_errors = evaluate_mcp(root, mcp_rel, live=live)
            row["mcp"] = mcp_cell
            errors.extend(mcp_errors)

        row["cli"] = cli_cell
        cells[env_id] = row

    # The same CLI and the same skill files are reported on every row.
    # Duplicate error strings from that sharing are collapsed.
    unique_errors = []
    seen = set()
    for item in errors:
        if item not in seen:
            seen.add(item)
            unique_errors.append(item)
    return {"root": str(root), "live": live, "cells": cells, "errors": unique_errors, "ok": not unique_errors}


def configured_interpreters(root: Path) -> list[str]:
    """Interpreter names the IDE configs start: hooks and MCP."""
    names: list[str] = []
    for rel in (*HOOK_FILES.values(), *MCP_FILES.values()):
        text = read_text(root, rel)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        commands: list[str] = []
        walk_commands(payload, commands)
        for command in commands:
            name = command.split(" ", 1)[0]
            if name in INTERPRETERS and name not in names:
                names.append(name)
    return names


def interpreter_warning(root: Path | None = None) -> str | None:
    """Name a config interpreter missing from PATH and the command that fixes it."""
    root = (root or ROOT).resolve()
    missing = [name for name in configured_interpreters(root) if not shutil.which(name)]
    if not missing:
        return None
    present = next((name for name in INTERPRETERS if shutil.which(name)), None)
    fix = (
        f" Перепишите конфиги: {present} hooks/launcher.py --python {present} и "
        f"{present} scripts/mcp_server.py --write-configs --python {present}."
        if present
        else ""
    )
    return (
        f"Предупреждение: в PATH нет {', '.join(missing)}, а конфиги хуков и MCP запускают именно его. "
        f"IDE с этими конфигами хук и MCP не стартует.{fix}"
    )


def summary_line(report: dict) -> str:
    if report["ok"] and not report["live"]:
        return (
            "Переносимость: статически без ошибок. "
            "Живой запуск команд: python scripts/portability.py --check. Матрица: docs/portability.md."
        )
    if report["ok"]:
        return "Переносимость: ошибки не найдены. Матрица: docs/portability.md."
    return "Переносимость: " + "; ".join(report["errors"][:4])


def render_text(report: dict) -> str:
    lines = [summary_line(report)]
    warning = interpreter_warning(Path(report["root"]))
    if warning:
        lines.append(warning)
    if SUBSTITUTED:
        lines.append(
            f"Живой запуск шёл через {sys.executable}: в PATH нет {', '.join(sorted(SUBSTITUTED))}."
        )
    lines.append("")
    for env_id, title in ENVIRONMENTS:
        bits = []
        for cap in CAPABILITIES:
            status = report["cells"][env_id][cap]["status"]
            bits.append(f"{CAPABILITY_TITLES[cap]}={status}")
        lines.append(f"{title}: " + ", ".join(bits))
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    lines = [
        "# Переносимость между средами",
        "",
        "Один набор правил — `AGENTS.md`. Адаптеры либо ссылаются на него, либо являются им. "
        "Команда `python scripts/portability.py` строит матрицу заново. Повтор без изменений файл не переписывает.",
        "",
        "Пометки:",
        "",
        "- **live** — этот процесс запустил команду (хук, MCP `initialize` или `strat.py --help`). Клиент IDE при этом не открывался.",
        "- **static** — файл прочитан и разобран, процесс этой возможности не запускался.",
        "- **unsupported** — среда так не подключена в этом репозитории. Это не скрытый успех.",
        "- **error** — файл отсутствует, расходится с ядром или команда не стартовала.",
        "",
        "Матрица ниже снята режимом, в котором её записали. Проверка в CI запускает процессы и сверяет текст.",
        "",
        "| Среда | " + " | ".join(CAPABILITY_TITLES[item] for item in CAPABILITIES) + " |",
        "| --- | " + " | ".join("---" for _ in CAPABILITIES) + " |",
    ]
    for env_id, title in ENVIRONMENTS:
        values = [report["cells"][env_id][cap]["status"] for cap in CAPABILITIES]
        lines.append("| " + " | ".join([title, *values]) + " |")
    lines.extend(["", "## Пояснения", ""])
    for env_id, title in ENVIRONMENTS:
        lines.append(f"### {title}")
        lines.append("")
        for cap in CAPABILITIES:
            item = report["cells"][env_id][cap]
            lines.append(f"- {CAPABILITY_TITLES[cap]}: {item['status']}. {item['note']}")
        lines.append("")
    if report["errors"]:
        lines.append("## Ошибки")
        lines.append("")
        for item in report["errors"]:
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(
        [
            "## Что команда не делает",
            "",
            "Она не открывает Cursor, VS Code и Claude Code и не доказывает, что кнопки в интерфейсе нажаты. "
            "Живой протокол ниже для человека. Пока его не прошли на машине, в журнале разработки это не записано как сделанное.",
            "",
            "## Ручная проверка: VS Code и Copilot",
            "",
            "Ожидание: агент вызывает MCP, `strat_check` для `projects/_example` заканчивается без ошибок, а `strat_confirm` без явного согласия контрольную точку не меняет.",
            "",
            "1. Откройте корень репозитория в VS Code. В терминале `python --version` печатает 3.10 или новее.",
            "2. `python scripts/strat.py doctor` содержит строку, что MCP ответил на initialize, и строку про переносимость.",
            "3. В панели MCP включите сервер `strat` из `.vscode/mcp.json`. Тип сервера — stdio. Рядом не должно быть ошибки старта.",
            "4. Новый чат в режиме агента, где видны инструменты. Попросите: вызвать `strat_check` для проекта `_example`. В ответе есть `OK`, в JSON `errors` равен 0.",
            "5. Попросите вызвать `strat_confirm` для брифа, не говоря, что бриф принят. Контрольная точка `_example` не меняется: в `state.json` те же байты, что до просьбы.",
            "",
            "## Ручная проверка: Claude Code",
            "",
            "Ожидание: проектный MCP разрешён, `strat_check` для `_example` без ошибок, роль критика при недоступном субагенте начинается с заголовка `## Роль: critic (inline, субагент недоступен)`.",
            "",
            "1. Откройте корень репозитория в Claude Code. Подтвердите проектный сервер `strat` из `.mcp.json`.",
            "2. В терминале `python scripts/strat.py doctor` показывает ответ MCP на initialize.",
            "3. Попросите вызвать `strat_check` для `_example`. Ответ содержит `OK`.",
            "4. Попросите сильнейшее возражение на варианты `_example`. Если субагент не стартовал, первая строка блока — указанный заголовок, и в журнале есть запись, что роль сыграна inline.",
            "5. `python scripts/portability.py` завершается с кодом 0. В матрице хуки и MCP Claude Code стоят как live, субагенты — как static.",
            "",
            "Codex и прочие читают `AGENTS.md` и вызывают `python scripts/strat.py`. Отдельных хуков и MCP для них в репозитории нет, в матрице это unsupported.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    sys.path.insert(0, str(ROOT / "scripts"))
    from stdio_utf8 import configure_stdio

    configure_stdio()
    parser = argparse.ArgumentParser(description="Матрица переносимости адаптеров стратега.")
    parser.add_argument("--static", action="store_true", help="не запускать процессы")
    parser.add_argument("--write", action="store_true", help="обновить docs/portability.md, если текст изменился")
    parser.add_argument("--check", action="store_true", help="код 1, если docs/portability.md отличается")
    parser.add_argument("--root", help="корень для проверки, по умолчанию репозиторий")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else ROOT
    report = evaluate(root, live=not args.static)
    sys.stdout.write(render_text(report))
    text = render_markdown(report)
    target = root / "docs" / "portability.md"
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print("docs/portability.md расходится с проверкой.", file=sys.stderr)
            return 1
    elif args.write:
        write_if_changed(target, text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
