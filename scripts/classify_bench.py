#!/usr/bin/env python3
"""Compare the cheap classifier, the strong model and the rules.

Offline mode replays bench/classify/saved and never calls a model. That is what
CI and `summary --check` run. A live cursor-agent pass is `run`, then `archive`.
Costs from config/model-prices.json are labelled as an estimate, not an invoice.
Token counts are kept when the CLI JSON has a usage object.

This file does not score the agent-versus-chat benchmark and does not edit
scripts/bench.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import classify  # noqa: E402

ITEMS_PATH = ROOT / "bench" / "classify" / "items.jsonl"
FIXTURE = ROOT / "bench" / "classify" / "fixture"
SAVED_DIR = ROOT / "bench" / "classify" / "saved"
RUNS_ROOT = ROOT / "bench" / "classify" / "runs"
SUMMARY_JSON = ROOT / "bench" / "classify" / "summary.json"
SUMMARY_MD = ROOT / "docs" / "classifier-benchmark.md"
CHEAP_MODEL = "composer-2.5-fast"
STRONG_MODEL = "cursor/default"


class BenchError(Exception):
    pass


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchError(f"{path}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise BenchError(f"{path}:{line_no}: нужна запись JSON")
        rows.append(row)
    return rows


def load_items() -> list[dict]:
    if not ITEMS_PATH.is_file():
        raise BenchError(f"Нет набора {ITEMS_PATH}.")
    rows = read_jsonl(ITEMS_PATH)
    if not 20 <= len(rows) <= 40:
        raise BenchError(f"В наборе {len(rows)} примеров, нужно от 20 до 40.")
    return rows


def load_saved(name: str) -> dict[str, dict]:
    path = SAVED_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise BenchError(f"Нет учебных ответов {path}.")
    rows = {str(row["id"]): row for row in read_jsonl(path)}
    return rows


def labels_equal(task: str, predicted, gold) -> bool:
    if not isinstance(predicted, (str, list)):
        return False
    if task == "impact":
        if not isinstance(predicted, list) or not isinstance(gold, list):
            return False
        return set(predicted) == set(gold)
    return predicted == gold


def ratio(hits: int, total: int) -> str:
    if total == 0:
        return "0.0000"
    value = (Decimal(hits) / Decimal(total)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format(value, "f")


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")


def mean_seconds(values: list[float]) -> str:
    if not values:
        return "0.000"
    total = sum((Decimal(str(item)) for item in values), Decimal("0"))
    return format((total / Decimal(len(values))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f")


def attempt_for(row: dict, item: dict, model: str) -> dict:
    allowed = classify.allowed_labels(item["task"], FIXTURE)
    return classify._attempt_from_text(  # noqa: SLF001 — bench and the classifier share one parser
        str(row.get("raw") or ""),
        task=item["task"],
        allowed=allowed,
        backend="saved",
        model=model,
        elapsed=float(row.get("elapsed_seconds") or 0),
        usage=row.get("usage") if isinstance(row.get("usage"), dict) else None,
        provider_cost=None,
    )


def score_attempt(attempt: dict, item: dict) -> bool:
    return bool(attempt.get("ok")) and labels_equal(item["task"], attempt.get("label"), item["gold"])


def score_rules(items: list[dict]) -> dict:
    hits = 0
    misses = []
    for item in items:
        chosen = classify.rules_classify(item["task"], item["text"], FIXTURE, classify.allowed_labels(item["task"], FIXTURE))
        if labels_equal(item["task"], chosen["label"], item["gold"]):
            hits += 1
        else:
            misses.append({"id": item["id"], "gold": item["gold"], "label": chosen["label"]})
    total = len(items)
    return {
        "name": "rules",
        "title": "Правила, без сети",
        "hits": hits,
        "total": total,
        "errors": 0,
        "accuracy": ratio(hits, total),
        "mean_seconds": "0.000",
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0.000000",
        "note": "посчитано правилами по учебному набору, не ответ модели",
        "misses": misses,
    }


def score_saved(
    items: list[dict],
    saved: dict[str, dict],
    model: str,
    name: str,
    title: str,
    *,
    allow_missing: bool = False,
) -> dict:
    hits = 0
    errors = 0
    misses = []
    seconds: list[float] = []
    input_tokens = 0
    output_tokens = 0
    cost = Decimal("0")
    for item in items:
        row = saved.get(item["id"])
        if row is None:
            if not allow_missing:
                raise BenchError(f"В {name} нет id {item['id']}.")
            errors += 1
            misses.append({"id": item["id"], "gold": item["gold"], "label": None, "error": "нет ответа"})
            seconds.append(0.0)
            continue
        attempt = attempt_for(row, item, model)
        seconds.append(float(attempt["elapsed_seconds"]))
        input_tokens += int(attempt["input_tokens"])
        output_tokens += int(attempt["output_tokens"])
        cost += Decimal(str(attempt["estimated_cost"]))
        if not attempt.get("ok"):
            errors += 1
        if score_attempt(attempt, item):
            hits += 1
        else:
            misses.append({"id": item["id"], "gold": item["gold"], "label": attempt.get("label"), "error": attempt.get("error")})
    total = len(items)
    return {
        "name": name,
        "title": title,
        "hits": hits,
        "total": total,
        "errors": errors,
        "accuracy": ratio(hits, total),
        "mean_seconds": mean_seconds(seconds),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": money(cost),
        "note": "учебные сохранённые ответы, не прогон cursor-agent",
        "misses": misses,
    }


def score_pipeline(
    items: list[dict],
    cheap_rows: dict[str, dict],
    strong_rows: dict[str, dict],
    *,
    allow_missing: bool = False,
) -> dict:
    hits = 0
    errors = 0
    misses = []
    seconds: list[float] = []
    input_tokens = 0
    output_tokens = 0
    cost = Decimal("0")
    escalated = 0
    for item in items:
        if item["id"] not in cheap_rows or item["id"] not in strong_rows:
            if not allow_missing:
                raise BenchError(f"Нет пары учебных ответов для {item['id']}.")
            errors += 1
            misses.append({"id": item["id"], "gold": item["gold"], "label": None, "error": "нет ответа"})
            seconds.append(0.0)
            continue

        def transport(name: str, tier: str, _prompt: str, *, item=item) -> dict:
            row = cheap_rows[item["id"]] if tier == "cheap" else strong_rows[item["id"]]
            model = CHEAP_MODEL if tier == "cheap" else STRONG_MODEL
            return attempt_for(row, item, model)

        try:
            result = classify.classify(FIXTURE, item["task"], item["text"], backend="replay", transport=transport)
        except classify.ClassifyError as exc:
            result = {"ok": False, "label": None, "error": str(exc), "elapsed_seconds": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost": "0"}
        seconds.append(float(result.get("elapsed_seconds") or 0))
        input_tokens += int(result.get("input_tokens") or 0)
        output_tokens += int(result.get("output_tokens") or 0)
        cost += Decimal(str(result.get("estimated_cost") or "0"))
        if result.get("escalated"):
            escalated += 1
        if not result.get("ok"):
            errors += 1
        if result.get("ok") and labels_equal(item["task"], result.get("label"), item["gold"]):
            hits += 1
        else:
            misses.append({"id": item["id"], "gold": item["gold"], "label": result.get("label"), "error": result.get("error")})
    total = len(items)
    return {
        "name": "pipeline",
        "title": "Дешёвый ответ, затем эскалация",
        "hits": hits,
        "total": total,
        "errors": errors,
        "accuracy": ratio(hits, total),
        "mean_seconds": mean_seconds(seconds),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": money(cost),
        "escalated": escalated,
        "note": "учебный конвейер: при низкой уверенности или нарушении правила берётся сильный ответ или правило",
        "misses": misses,
    }


def offline_report(items: list[dict] | None = None) -> dict:
    items = items if items is not None else load_items()
    cheap = load_saved("cheap")
    strong = load_saved("strong")
    return {
        "kind": "offline",
        "items": len(items),
        "rows": [
            score_rules(items),
            score_saved(items, cheap, CHEAP_MODEL, "cheap-saved", "Учебный дешёвый ответ"),
            score_saved(items, strong, STRONG_MODEL, "strong-saved", "Учебный сильный ответ"),
            score_pipeline(items, cheap, strong),
        ],
    }


def iter_runs(runs_root: Path) -> list[Path]:
    if not runs_root.is_dir():
        return []
    found = []
    for child in runs_root.iterdir():
        if child.is_dir() and (child / "cheap.jsonl").is_file() and (child / "strong.jsonl").is_file():
            found.append(child)
    found.sort(key=lambda path: path.name)
    return found


def score_live_run(run_dir: Path, items: list[dict]) -> dict:
    cheap = {str(row["id"]): row for row in read_jsonl(run_dir / "cheap.jsonl")}
    strong = {str(row["id"]): row for row in read_jsonl(run_dir / "strong.jsonl")}
    report = {
        "kind": "live",
        "run": run_dir.name,
        "rows": [
            score_saved(items, cheap, CHEAP_MODEL, "cheap-live", "cursor-agent, лёгкая модель", allow_missing=True),
            score_saved(
                items,
                strong,
                STRONG_MODEL,
                "strong-live",
                "cursor-agent, сильная или модель по умолчанию",
                allow_missing=True,
            ),
            score_pipeline(items, cheap, strong, allow_missing=True),
        ],
    }
    for row in report["rows"]:
        row["note"] = "живой прогон, токены из usage если CLI их отдал; стоимость — оценка, не счёт"
    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        try:
            report["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report["meta"] = {}
    return report


def render_row(row: dict) -> str:
    return (
        f"| {row['title']} | {row['hits']}/{row['total']} | {row['accuracy']} | "
        f"{row['mean_seconds']} | {row['input_tokens']} | {row['output_tokens']} | "
        f"{row['estimated_cost']} | {row.get('errors', 0)} |"
    )


def render_markdown(offline: dict, live: dict | None) -> str:
    lines = [
        "# Классификатор: дешёвый контур, сильная модель и правила",
        "",
        "Офлайн-цифры считает `python scripts/classify_bench.py summary` по учебному набору и сохранённым ответам. Живой cursor-agent сюда не подставляется: пока нет каталога в `bench/classify/runs`, в живой таблице стоят прочерки.",
        "",
        "Стоимость во всех строках — оценка по `config/model-prices.json`, не счёт и не тариф Cursor. Токены учебных ответов заданы в корпусе. Токены живого прогона берутся из поля `usage` JSON cursor-agent, если оно есть. Кэш-токены в оценку не входят: отдельной ставки для них в таблице нет.",
        "",
        f"Учебный набор: {offline['items']} примеров в `bench/classify/items.jsonl`. Материалы вымышленные.",
        "",
        "## Офлайн, учебные сохранённые ответы",
        "",
        "Это проверка конвейера, а не замер модели. Дешёвый и сильный столбцы читают `bench/classify/saved`. Правила считаются заново.",
        "",
        "| Контур | Верно | Точность | Среднее время, с | Токены входа | Токены выхода | Оценка стоимости, не счёт | Ошибки вызова |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in offline["rows"]:
        lines.append(render_row(row))
    lines.extend(["", "Промахи офлайна:", ""])
    for row in offline["rows"]:
        shown = ", ".join(str(item["id"]) for item in row["misses"]) or "нет"
        lines.append(f"- {row['title']}: {shown}. Ошибок вызова {row.get('errors', 0)}; {row['note']}.")
    pipeline = next(row for row in offline["rows"] if row["name"] == "pipeline")
    lines.extend(
        [
            "",
            f"Эскалаций в учебном конвейере: {pipeline.get('escalated', 0)} из {pipeline['total']}.",
            "",
            "## Живой cursor-agent",
            "",
        ]
    )
    if live is None:
        lines.extend(
            [
                "Архива `bench/classify/runs` с парой cheap.jsonl и strong.jsonl нет. Цифры не выдуманы.",
                "",
                "| Контур | Верно | Точность | Среднее время, с | Токены входа | Токены выхода | Оценка стоимости, не счёт | Ошибки вызова |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
                "| cursor-agent, лёгкая модель | — | — | — | — | — | — | — |",
                "| cursor-agent, сильная или модель по умолчанию | — | — | — | — | — | — | — |",
                "| Дешёвый ответ, затем эскалация | — | — | — | — | — | — | — |",
                "",
            ]
        )
    else:
        lines.append(f"Последний архив: `{live['run']}`. Более ранние каталоги в таблицу не усредняются.")
        meta = live.get("meta") or {}
        if meta.get("cheap_model") or meta.get("strong_model"):
            lines.append(
                "Модели запуска: лёгкая "
                + str(meta.get("cheap_model") or "—")
                + ", сильная "
                + str(meta.get("strong_model") or "модель по умолчанию")
                + "."
            )
        lines.extend(
            [
                "",
                "| Контур | Верно | Точность | Среднее время, с | Токены входа | Токены выхода | Оценка стоимости, не счёт | Ошибки вызова |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in live["rows"]:
            lines.append(render_row(row))
        lines.append("")
    lines.extend(
        [
            "## Вывод",
            "",
            "Оставляем. На учебном наборе правила дают метку без сети и не ставят факт без ссылки. Польза лёгкой модели против сильной на живом cursor-agent в этой таблице не заявлена, пока в живых строках прочерки. Учебный конвейер показывает только то, что низкая уверенность и нарушение правила не остаются без следа: невалидная метка — ошибка, а не догадка. Если живой прогон не обгонит правила, в `config/routing.json` достаточно `\"backend\": \"rules\"`: модель тогда не оправдывает вызов, команда остаётся.",
            "",
            "## Как прогнать на Windows",
            "",
            "Из корня репозитория, PowerShell 5.1. В командах нет `$` и нет кавычек внутри аргумента. Кириллица лежит в файлах UTF-8, не в командной строке.",
            "",
            "```powershell",
            "python scripts/classify_bench.py models",
            "python scripts/classify_bench.py run --dry-run --limit 2 --out C:\\strat-classify",
            "python scripts/classify_bench.py run --limit 2 --out C:\\strat-classify",
            "python scripts/classify_bench.py run --out C:\\strat-classify",
            "python scripts/classify_bench.py archive --work C:\\strat-classify",
            "python scripts/classify_bench.py summary",
            "python scripts/classify_bench.py summary --check",
            "```",
            "",
            "`models` печатает `agent --list-models`. Имя лёгкой модели по умолчанию — `composer-2.5-fast` в `config/routing.json`. Если этой подписки в списке нет, запишите id в `cheap_model`. Пустой `strong_model` значит модель cursor-agent по умолчанию, без флага `--model`. На машине, где список уже снят, это строка `auto`.",
            "",
            "`run` вызывает `agent -p --trust --output-format json --workspace <пустой каталог>`. "
            "Флагов `--force` и `--mode ask` нет: ask ищет по кодовой базе, force разрешает команды, из-за этого один вызов шёл около двух минут и дочерний процесс держал временную папку. "
            "`--approve-mcps` не передаётся. Каталог `--workspace` лежит внутри `--out` и не удаляется: на Windows процесс agent может держать файл после выхода. "
            "Зависшее дерево гасится `taskkill /T /F`, на других системах — сигналом группе процессов. "
            "Ответ дописывается в jsonl сразу. Повтор того же `--out` не вызывает уже записанные примеры. "
            "Ошибка одного вызова остаётся строкой этого примера, замер идёт дальше: в сводке она неверная и видна в колонке «Ошибки вызова». "
            "`--dry-run` печатает флаги, модели, id и пропуски, не вызывая agent. `--limit 2` берёт первые два примера.",
            "",
            "`summary --check` сверяет этот файл и `bench/classify/summary.json` с пересчётом. Сеть и cursor-agent он не вызывает.",
            "",
            "Ручная метка без модели, тот же корень:",
            "",
            "```powershell",
            "python scripts/strat.py classify projects/_example --task claim-type --input-file bench/classify/samples/fact-with-cite.txt --backend rules",
            "python scripts/strat.py classify projects/_example --task claim-type --input-file bench/classify/samples/fact-without-cite.txt --backend rules",
            "python scripts/strat.py classify projects/_example --task route --input-file bench/classify/samples/route-collect.txt --backend rules",
            "python scripts/strat.py classify projects/_example --task impact --input-file bench/classify/samples/impact-e001.txt --backend rules",
            "```",
            "",
            "Команда дописывает `log.md` учебного проекта. В stdout один JSON. У строки со ссылкой метка `fact`. У строки без ссылки метка не `fact`.",
            "",
        ]
    )
    return "\n".join(lines)


def render_payload(offline: dict, live: dict | None) -> dict:
    def brief(row: dict) -> dict:
        return {key: row[key] for key in ("name", "title", "hits", "total", "errors", "accuracy", "mean_seconds", "input_tokens", "output_tokens", "estimated_cost", "note", "misses") if key in row}

    payload = {
        "offline": {"items": offline["items"], "rows": [brief(row) for row in offline["rows"]]},
        "live": None if live is None else {"run": live["run"], "rows": [brief(row) for row in live["rows"]], "meta": live.get("meta") or {}},
        "cost_note": classify.COST_NOTE,
    }
    return payload


def summary_text(runs_root: Path | None = None) -> tuple[str, dict]:
    items = load_items()
    offline = offline_report(items)
    runs = iter_runs(runs_root or RUNS_ROOT)
    live = score_live_run(runs[-1], items) if runs else None
    payload = render_payload(offline, live)
    return render_markdown(offline, live), payload


def write_summary(markdown: str, payload: dict, markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_bytes(markdown.encode("utf-8"))
    json_path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def cmd_models(_args: argparse.Namespace) -> int:
    spec = classify.agent_spec()
    if spec is None:
        print("cursor-agent не найден. Поставьте команду agent в PATH или задайте STRAT_AGENT_CMD.")
        print("Когда агент есть: agent --list-models")
        return 1
    try:
        prefix, extra = classify.cursor_launch()
    except classify.ClassifyError as exc:
        print(str(exc))
        return 1
    env = os.environ.copy()
    env.update(extra)
    argv = [*prefix, "--list-models"]
    proc = __import__("subprocess").run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    sys.stdout.write(proc.stdout or "")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print("agent --list-models завершился с ошибкой. Та же справка: agent models")
    return int(proc.returncode)


def _load_rows(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    found: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id"):
            found[str(row["id"])] = row
    return found


def _append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _progress(index: int, total: int, item_id: str, tier: str, model: str, attempt: dict) -> None:
    shown = attempt.get("label")
    if shown is None:
        shown = "ошибка: " + str(attempt.get("error") or "пустой ответ")
    print(
        f"{index}/{total} {item_id} {tier} model={model or 'default'} "
        f"{attempt.get('elapsed_seconds') or 0}s {shown}",
        flush=True,
    )


def cmd_run(args: argparse.Namespace) -> int:
    items = load_items()
    if args.limit:
        items = items[: args.limit]
    config = classify.load_routing()
    cheap_model = config["cheap_model"]
    strong_model = config["strong_model"]
    timeout = int(args.timeout or config["timeout"])
    out = Path(args.out) if args.out else None
    workspace = classify.classifier_workspace(None if out is None else out / "workspace")
    if args.dry_run:
        done_cheap = _load_rows(out / "cheap.jsonl") if out else {}
        done_strong = _load_rows(out / "strong.jsonl") if out else {}
        skip = [item["id"] for item in items if item["id"] in done_cheap and item["id"] in done_strong]
        payload = {
            "items": len(items),
            "ids": [item["id"] for item in items],
            "skip": skip,
            "timeout_seconds": timeout,
            "workspace": str(workspace),
            "cheap_model": cheap_model,
            "strong_model": strong_model or "default",
            "agent": classify.agent_spec(),
            "cheap_flags": classify.diagnostic_argv(cheap_model, str(workspace)),
            "strong_flags": classify.diagnostic_argv(strong_model, str(workspace)),
            "omitted": ["--force", "--mode", "ask", "--approve-mcps"],
            "why": (
                "ask ищет по кодовой базе, force разрешает команды. "
                "Их нет, чтобы один вызов не читал репозиторий. "
                "workspace пустой и не удаляется."
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if classify.agent_spec() is None:
        print("cursor-agent не найден. Живой замер не начат, учебные цифры не подменены.")
        print("Офлайн: python scripts/classify_bench.py summary --check")
        print("Флаги без вызова: python scripts/classify_bench.py run --dry-run --limit 2 --out " + str(out or "C:\\strat-classify"))
        return 1
    assert out is not None
    out.mkdir(parents=True, exist_ok=True)
    cheap_path = out / "cheap.jsonl"
    strong_path = out / "strong.jsonl"
    done_cheap = _load_rows(cheap_path)
    done_strong = _load_rows(strong_path)
    from datetime import datetime, timezone

    meta = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": len(items),
        "limit": int(args.limit or 0),
        "cheap_model": cheap_model,
        "strong_model": strong_model or "default",
        "timeout_seconds": timeout,
        "flags": [*classify.CURSOR_FLAGS, "--workspace"],
        "workspace": str(workspace),
        "cost_note": classify.COST_NOTE,
        "errors": 0,
    }
    if (out / "meta.json").is_file():
        try:
            previous = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            if previous.get("started_at"):
                meta["started_at"] = previous["started_at"]
        except json.JSONDecodeError:
            pass
    (out / "meta.json").write_bytes((json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    errors = 0
    total = len(items)
    for index, item in enumerate(items, 1):
        allowed = classify.allowed_labels(item["task"], FIXTURE)
        if item["id"] in done_cheap and item["id"] in done_strong:
            print(f"{index}/{total} {item['id']} пропуск, уже есть", flush=True)
            continue
        if item["id"] not in done_cheap:
            cheap = _one_call(item, allowed, cheap_model, timeout, workspace, "cheap")
            _append_row(cheap_path, _record(item["id"], cheap))
            _progress(index, total, item["id"], "cheap", cheap_model, cheap)
            if not cheap.get("ok"):
                errors += 1
        else:
            print(f"{index}/{total} {item['id']} cheap пропуск, уже есть", flush=True)
        if item["id"] not in done_strong:
            strong = _one_call(item, allowed, strong_model, timeout, workspace, "strong")
            _append_row(strong_path, _record(item["id"], strong))
            _progress(index, total, item["id"], "strong", strong_model or "default", strong)
            if not strong.get("ok"):
                errors += 1
        else:
            print(f"{index}/{total} {item['id']} strong пропуск, уже есть", flush=True)
    meta["errors"] = errors
    (out / "meta.json").write_bytes((json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"Готово. Примеров: {total}. Ошибок вызова в этом запуске: {errors}.")
    print(f"Ответы: {out}")
    print("Дальше: python scripts/classify_bench.py archive --work " + str(out))
    return 0


def _one_call(item: dict, allowed: list[str], model: str, timeout: int, workspace: Path, tier: str) -> dict:
    try:
        return classify.call_cursor(item["task"], item["text"], allowed, model, timeout, workspace=workspace)
    except Exception as exc:  # noqa: BLE001 — one example must not stop the run
        return {
            "ok": False,
            "transport_ok": False,
            "label": None,
            "confidence": None,
            "rationale": "",
            "error": f"{tier}: {exc}",
            "backend": "cursor-agent",
            "model": model or "cursor/default",
            "elapsed_seconds": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "provider_cost": None,
            "estimated_cost": "0.000000",
        }


def _record(item_id: str, attempt: dict) -> dict:
    raw_label = attempt.get("label")
    if attempt.get("ok"):
        raw = json.dumps(
            {"label": raw_label, "confidence": attempt.get("confidence"), "rationale": attempt.get("rationale")},
            ensure_ascii=False,
        )
    else:
        raw = str(attempt.get("error") or "")
    usage = {
        "inputTokens": int(attempt.get("input_tokens") or 0),
        "outputTokens": int(attempt.get("output_tokens") or 0),
        "cacheReadTokens": int(attempt.get("cache_read_tokens") or 0),
        "cacheWriteTokens": int(attempt.get("cache_write_tokens") or 0),
    }
    return {
        "id": item_id,
        "raw": raw,
        "elapsed_seconds": attempt.get("elapsed_seconds") or 0,
        "usage": usage,
        "model": attempt.get("model") or "",
        "ok": bool(attempt.get("ok")),
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))


def cmd_archive(args: argparse.Namespace) -> int:
    work = Path(args.work)
    for name in ("cheap.jsonl", "strong.jsonl", "meta.json"):
        if not (work / name).is_file():
            raise BenchError(f"В {work} нет {name}. Сначала run.")
    runs_root = Path(args.runs) if args.runs else RUNS_ROOT
    runs_root.mkdir(parents=True, exist_ok=True)
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    stamp = str(meta.get("started_at") or "").replace(":", "").replace("-", "")
    if not stamp:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = runs_root / stamp
    if target.exists():
        raise BenchError(f"Архив уже есть: {target}. Не перезаписываю.")
    target.mkdir()
    for name in ("cheap.jsonl", "strong.jsonl", "meta.json"):
        data = (work / name).read_bytes()
        (target / name).write_bytes(data)
    print(f"Архив: {target}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    markdown, payload = summary_text(Path(args.runs) if args.runs else RUNS_ROOT)
    markdown_path = Path(args.markdown) if args.markdown else SUMMARY_MD
    json_path = Path(args.json) if args.json else SUMMARY_JSON
    if args.check:
        current_md = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        current_json = json_path.read_text(encoding="utf-8") if json_path.is_file() else ""
        expected_json = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if current_md != markdown or current_json != expected_json:
            print("Сводка классификатора расходится. Запустите summary без --check.")
            return 1
        print("OK")
        return 0
    write_summary(markdown, payload, markdown_path, json_path)
    print(f"Markdown: {markdown_path}")
    print(f"JSON: {json_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Замер классификатора: правила, учебные ответы и cursor-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    models = sub.add_parser("models", help="agent --list-models")
    models.set_defaults(func=cmd_models)
    run = sub.add_parser("run", help="живой прогон cursor-agent, лёгкая модель и сильная")
    run.add_argument("--out", default="", help="каталог ответов, лучше вне репозитория")
    run.add_argument("--limit", type=int, default=0, help="сколько первых примеров, 0 значит все")
    run.add_argument("--timeout", type=int, default=0, help="секунд на один вызов, 0 значит значение из config")
    run.add_argument("--dry-run", action="store_true", help="показать флаги, id и пропуски, не вызывать модель")
    run.set_defaults(func=cmd_run)
    archive = sub.add_parser("archive", help="скопировать прогон в bench/classify/runs")
    archive.add_argument("--work", required=True)
    archive.add_argument("--runs", default="", help="другой каталог архива, для проверки")
    archive.set_defaults(func=cmd_archive)
    summary = sub.add_parser("summary", help="таблица в docs и JSON")
    summary.add_argument("--check", action="store_true")
    summary.add_argument("--markdown", default="")
    summary.add_argument("--json", default="")
    summary.add_argument("--runs", default="")
    summary.set_defaults(func=cmd_summary)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "run" and not args.dry_run and not args.out:
        print("Нужен --out, каталог лучше вне репозитория, например C:\\strat-classify")
        return 1
    if getattr(args, "runs", "") == "":
        args.runs = ""
    try:
        return int(args.func(args))
    except BenchError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
