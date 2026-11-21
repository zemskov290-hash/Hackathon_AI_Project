#!/usr/bin/env python3
"""Reproducible tables for the strategist. Standard library, optional xlsx and charts.

CLI and MCP both call these functions. A number in evidence points at an analysis id,
the snapshot file and its sha256. ``verify_project`` reruns the calculation.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import optional_packages
from statistics import StatisticsError

RUNNER = Path(__file__).resolve().parent / "analysis_runner.py"
ID_RE = re.compile(r"^A\d{3,}$")
METRIC_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
METRIC_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
ANCHOR_RE = re.compile(
    r"(?<![\d.])(\d+(?:[.,]\d+)?)\s*\[(A\d+)\.([A-Za-z_][A-Za-z0-9_.]*)\]"
)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".jsonl", ".html", ".htm", ".vtt", ".srt"}
FUNCTIONS = {"count", "sum", "mean", "median", "min", "max", "stdev", "pstdev", "value_counts", "group_mean"}
FILTERS = {"eq", "ne", "contains", "gte", "lte"}
MAX_SNAPSHOT_BYTES = 20_000_000
MAX_SCRIPT_CHARS = 100_000
RESULT_LIMIT = 1_000_000


class AnalysisError(Exception):
    """A user-visible analysis failure. The message is Russian."""


def content_digest(path: Path) -> str:
    import strat

    return strat.content_digest(path)


def rel(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def require_inside(project: Path, raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise AnalysisError("Укажите файл внутри папки проекта.")
    path = Path(str(raw).strip())
    try:
        candidate = path.resolve() if path.is_absolute() else (project / path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise AnalysisError(f"Путь не открывается: {raw}") from exc
    try:
        candidate.relative_to(project.resolve())
    except ValueError:
        raise AnalysisError(f"Файл вне проекта: {raw}. Сначала положите его в папку проекта.") from None
    if not candidate.is_file():
        raise AnalysisError(f"Файл не найден: {raw}")
    return candidate


def clamp_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("Таймаут анализа должен быть числом секунд.") from exc
    if timeout < 0.2 or timeout > 30:
        raise AnalysisError("Таймаут анализа: от 0.2 до 30 секунд.")
    return timeout


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-", "-0"}:
        return "0"
    return text


def to_decimal(cell) -> Decimal | None:
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, Decimal):
        return cell
    if isinstance(cell, int):
        return Decimal(cell)
    if isinstance(cell, float):
        return Decimal(str(cell))
    text = str(cell).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    text = text.replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def canon_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        number = value if isinstance(value, Decimal) else to_decimal(value)
        if number is None:
            raise AnalysisError("Метрика содержит нечисловое значение.")
        return decimal_text(number)
    if isinstance(value, str):
        number = to_decimal(value)
        if number is not None and re.fullmatch(r"-?\d+(?:[.,]\d+)?", value.strip().replace(" ", "")):
            return decimal_text(number)
        return value
    if isinstance(value, dict):
        return {str(key): canon_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [canon_value(item) for item in value]
    raise AnalysisError(f"Метрика содержит неподдерживаемый тип: {type(value).__name__}.")


def dump_result(metrics: dict) -> str:
    payload = {"metrics": canon_value(metrics)}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(text.encode("utf-8")) > RESULT_LIMIT:
        raise AnalysisError("Результат анализа больше 1 МБ. Сузьте метрики.")
    return text


def parse_spec(text: str) -> dict:
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"Декларация анализа не JSON: {exc.msg}.") from exc
    if not isinstance(spec, dict):
        raise AnalysisError("Декларация анализа должна быть объектом JSON.")
    metrics = spec.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise AnalysisError("В декларации нужен непустой список metrics.")
    seen: set[str] = set()
    for item in metrics:
        if not isinstance(item, dict):
            raise AnalysisError("Каждая метрика — объект с id и fn.")
        metric_id = str(item.get("id") or "")
        fn = str(item.get("fn") or "")
        if not METRIC_ID_RE.fullmatch(metric_id):
            raise AnalysisError(f"Плохой id метрики: {metric_id or 'пусто'}. Латиница, цифры и _.")
        if metric_id in seen:
            raise AnalysisError(f"Метрика {metric_id} повторяется.")
        seen.add(metric_id)
        if fn not in FUNCTIONS:
            raise AnalysisError(f"Неизвестная функция {fn}. Допустимо: {', '.join(sorted(FUNCTIONS))}.")
        if fn != "count" and not str(item.get("column") or "").strip():
            raise AnalysisError(f"Метрике {metric_id} нужна колонка column.")
        if fn == "group_mean" and not str(item.get("by") or "").strip():
            raise AnalysisError(f"Метрике {metric_id} нужно поле by — колонка группы.")
    filters = spec.get("filters") or []
    if not isinstance(filters, list):
        raise AnalysisError("filters должен быть списком.")
    for item in filters:
        if not isinstance(item, dict) or not str(item.get("column") or "").strip():
            raise AnalysisError("Каждый фильтр — объект с column и одним из eq, ne, contains, gte, lte.")
        ops = [name for name in FILTERS if name in item]
        if len(ops) != 1:
            raise AnalysisError("У фильтра ровно одно условие: eq, ne, contains, gte или lte.")
    chart = spec.get("chart")
    if chart is not None:
        if not isinstance(chart, dict) or chart.get("type") != "bar" or not str(chart.get("column") or "").strip():
            raise AnalysisError("chart: {\"type\": \"bar\", \"column\": \"...\"} и необязательные value и agg.")
        agg = str(chart.get("agg") or "count")
        if agg not in {"count", "sum", "mean"}:
            raise AnalysisError("chart.agg: count, sum или mean.")
        if agg != "count" and not str(chart.get("value") or "").strip():
            raise AnalysisError("Для chart.agg sum или mean нужна колонка value.")
    return spec


def _rows_from_records(records: list) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not records:
        return rows
    if not all(isinstance(item, dict) for item in records):
        raise AnalysisError("JSON-таблица должна быть списком объектов или {\"rows\": [...]}.")
    for record in records:
        rows.append({str(key): "" if value is None else str(value) for key, value in record.items()})
    return rows


def load_json_table(path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"JSON не разобран ({path.name}): {exc.msg}.") from exc
    except UnicodeError as exc:
        raise AnalysisError(f"JSON не в UTF-8 ({path.name}): {exc}.") from exc
    if isinstance(payload, list):
        return _rows_from_records(payload)
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return _rows_from_records(payload["rows"])
    raise AnalysisError("JSON-таблица: список объектов или объект с ключом rows.")


def load_csv_table(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise AnalysisError(f"CSV не в UTF-8 ({path.name}): {exc}.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AnalysisError(f"В CSV {path.name} нет строки заголовков.")
    rows = []
    for record in reader:
        rows.append({str(key or ""): "" if value is None else str(value) for key, value in record.items()})
    return rows


def _xlsx_openpyxl(path: Path) -> list[dict[str, str]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        grid = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    return _grid_to_rows(grid, path)


def _xlsx_pandas(path: Path) -> list[dict[str, str]]:
    import pandas as pd

    frame = pd.read_excel(path, dtype=str)
    records = frame.where(frame.notna(), "").to_dict(orient="records")
    return _rows_from_records(records)


def _grid_to_rows(grid: list[list], path: Path) -> list[dict[str, str]]:
    if not grid:
        raise AnalysisError(f"XLSX {path.name}: первый лист пуст.")
    header = []
    for index, cell in enumerate(grid[0]):
        name = "" if cell is None else str(cell).strip()
        header.append(name or f"col{index + 1}")
    rows = []
    for raw in grid[1:]:
        if raw is None or all(cell is None or str(cell).strip() == "" for cell in raw):
            continue
        record = {}
        for index, name in enumerate(header):
            cell = raw[index] if index < len(raw) else None
            record[name] = "" if cell is None else str(cell)
        rows.append(record)
    return rows


def load_xlsx_table(path: Path) -> list[dict[str, str]]:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        openpyxl = None
    else:
        openpyxl = True
    if openpyxl:
        try:
            return _xlsx_openpyxl(path)
        except AnalysisError:
            raise
        except Exception as exc:  # noqa: BLE001 — optional parser, show the reason
            raise AnalysisError(
                f"XLSX открыть не удалось ({path.name}): {exc}. Проверьте файл или сохраните CSV."
            ) from exc
    try:
        import pandas  # noqa: F401
    except ImportError:
        raise AnalysisError(optional_packages.missing_message("openpyxl")) from None
    try:
        return _xlsx_pandas(path)
    except AnalysisError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(
            f"XLSX открыть не удалось ({path.name}): {exc}. Проверьте файл или сохраните CSV."
        ) from exc


def load_table(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv_table(path)
    if suffix == ".json":
        return load_json_table(path)
    if suffix == ".xlsx":
        return load_xlsx_table(path)
    raise AnalysisError("Вход анализа: CSV, JSON или XLSX. Для текста страницы есть inspect, не analyze.")


def apply_filters(rows: list[dict[str, str]], filters: list[dict]) -> list[dict[str, str]]:
    chosen = list(rows)
    for item in filters:
        column = str(item["column"])
        if "eq" in item:
            expected = str(item["eq"])
            chosen = [row for row in chosen if str(row.get(column, "")) == expected]
        elif "ne" in item:
            expected = str(item["ne"])
            chosen = [row for row in chosen if str(row.get(column, "")) != expected]
        elif "contains" in item:
            needle = str(item["contains"])
            chosen = [row for row in chosen if needle in str(row.get(column, ""))]
        elif "gte" in item:
            limit = to_decimal(item["gte"])
            if limit is None:
                raise AnalysisError(f"Фильтр gte по колонке {column} должен быть числом.")
            chosen = [row for row in chosen if (number := to_decimal(row.get(column))) is not None and number >= limit]
        elif "lte" in item:
            limit = to_decimal(item["lte"])
            if limit is None:
                raise AnalysisError(f"Фильтр lte по колонке {column} должен быть числом.")
            chosen = [row for row in chosen if (number := to_decimal(row.get(column))) is not None and number <= limit]
    return chosen


def column_numbers(rows: list[dict[str, str]], column: str) -> list[Decimal]:
    numbers = []
    for row in rows:
        number = to_decimal(row.get(column))
        if number is not None:
            numbers.append(number)
    return numbers


def reduce_numbers(fn: str, numbers: list[Decimal]) -> str | None:
    if not numbers:
        return None
    if fn in {"stdev", "pstdev"} and len(numbers) < 2:
        return None
    if fn == "sum":
        total = sum(numbers, Decimal(0))
        return decimal_text(total)
    if fn == "min":
        return decimal_text(min(numbers))
    if fn == "max":
        return decimal_text(max(numbers))
    if fn == "mean":
        return decimal_text(sum(numbers, Decimal(0)) / Decimal(len(numbers)))
    if fn == "median":
        ordered = sorted(numbers)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return decimal_text(ordered[mid])
        return decimal_text((ordered[mid - 1] + ordered[mid]) / Decimal(2))
    if fn == "pstdev":
        mean = sum(numbers, Decimal(0)) / Decimal(len(numbers))
        variance = sum((item - mean) ** 2 for item in numbers) / Decimal(len(numbers))
        return decimal_text(variance.sqrt())
    if fn == "stdev":
        mean = sum(numbers, Decimal(0)) / Decimal(len(numbers))
        variance = sum((item - mean) ** 2 for item in numbers) / Decimal(len(numbers) - 1)
        return decimal_text(variance.sqrt())
    raise AnalysisError(f"Функция {fn} не сведена к числу.")


def compute_metrics(rows: list[dict[str, str]], spec: dict) -> dict:
    chosen = apply_filters(rows, spec.get("filters") or [])
    metrics: dict = {}
    for item in spec["metrics"]:
        metric_id = str(item["id"])
        fn = str(item["fn"])
        try:
            if fn == "count":
                metrics[metric_id] = str(len(chosen))
                continue
            column = str(item["column"])
            if fn == "value_counts":
                counts: dict[str, str] = {}
                for row in chosen:
                    key = str(row.get(column, "")).strip() or "(пусто)"
                    counts[key] = str(int(counts.get(key, "0")) + 1)
                metrics[metric_id] = {key: counts[key] for key in sorted(counts)}
                continue
            if fn == "group_mean":
                grouped: dict[str, list[Decimal]] = {}
                by = str(item["by"])
                for row in chosen:
                    key = str(row.get(by, "")).strip() or "(пусто)"
                    number = to_decimal(row.get(column))
                    if number is None:
                        continue
                    grouped.setdefault(key, []).append(number)
                metrics[metric_id] = {
                    key: decimal_text(sum(grouped[key], Decimal(0)) / Decimal(len(grouped[key])))
                    for key in sorted(grouped)
                }
                continue
            metrics[metric_id] = reduce_numbers(fn, column_numbers(chosen, column))
        except (StatisticsError, InvalidOperation) as exc:
            raise AnalysisError(f"Метрика {metric_id} не посчиталась: {exc}.") from exc
    return canon_value(metrics)


def try_chart(rows: list[dict[str, str]], spec: dict, dest: Path) -> str | None:
    chart = spec.get("chart")
    if not chart:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return optional_packages.missing_message("matplotlib")
    chosen = apply_filters(rows, spec.get("filters") or [])
    column = str(chart["column"])
    agg = str(chart.get("agg") or "count")
    buckets: dict[str, list[Decimal]] = {}
    for row in chosen:
        key = str(row.get(column, "")).strip() or "(пусто)"
        if agg == "count":
            buckets.setdefault(key, []).append(Decimal(1))
        else:
            number = to_decimal(row.get(str(chart.get("value"))))
            if number is not None:
                buckets.setdefault(key, []).append(number)
    labels = sorted(buckets)
    if agg == "count":
        values = [len(buckets[key]) for key in labels]
    elif agg == "sum":
        values = [float(sum(buckets[key], Decimal(0))) for key in labels]
    else:
        values = [float(sum(buckets[key], Decimal(0)) / Decimal(len(buckets[key]))) for key in labels]
    figure, axis = plt.subplots()
    axis.bar(labels, values)
    axis.set_title(str(chart.get("title") or column))
    figure.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(dest)
    plt.close(figure)
    return None


def copy_snapshot(source: Path, dest: Path) -> str:
    size = source.stat().st_size
    if size > MAX_SNAPSHOT_BYTES:
        raise AnalysisError(f"Файл {source.name} больше 20 МБ. Для анализа нужен фрагмент таблицы.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in TEXT_SUFFIXES:
        text = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        dest.write_bytes(text)
    else:
        shutil.copyfile(source, dest)
    return content_digest(dest)


def script_is_untrusted_source(project: Path, script: Path) -> bool:
    try:
        relative = script.resolve().relative_to(project.resolve())
    except ValueError:
        return True
    return relative.parts[0] == "sources" if relative.parts else False


def run_script(
    project: Path,
    script: Path,
    input_path: Path,
    result_path: Path,
    chart_path: Path,
    analysis_id: str,
    timeout: float,
) -> dict:
    if not RUNNER.is_file():
        raise AnalysisError("Нет scripts/analysis_runner.py.")
    env = {}
    for key in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "LANG",
        "LC_ALL",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MPLCONFIGDIR"] = str(script.parent)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(RUNNER),
                str(project.resolve()),
                str(script.resolve()),
                str(input_path.resolve()),
                str(result_path.resolve()),
                str(chart_path.resolve()),
                analysis_id,
            ],
            cwd=str(script.parent),
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(f"Превышен лимит времени анализа ({timeout:g} с). Скрипт остановлен.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace").strip()
        detail = detail[-2000:] if detail else "скрипт завершился с ошибкой"
        raise AnalysisError(detail)
    return read_result(result_path)


def read_result(path: Path) -> dict:
    if not path.is_file():
        raise AnalysisError("Скрипт не записал result JSON.")
    raw = path.read_bytes()
    if len(raw) > RESULT_LIMIT:
        raise AnalysisError("Результат анализа больше 1 МБ.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"result JSON не разобран: {exc}.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
        raise AnalysisError("Скрипт должен записать объект {\"metrics\": {...}}.")
    return canon_value(payload["metrics"])


def lookup_metric(metrics: dict, metric: str) -> str:
    if not METRIC_REF_RE.fullmatch(metric or ""):
        raise AnalysisError(f"Плохое имя метрики: {metric or 'пусто'}.")
    if metric in metrics and isinstance(metrics[metric], str):
        return str(metrics[metric])
    if "." in metric:
        head, tail = metric.split(".", 1)
        group = metrics.get(head)
        if isinstance(group, dict) and tail in group and isinstance(group[tail], str):
            return str(group[tail])
    if metric in metrics and metrics[metric] is None:
        raise AnalysisError(f"Метрика {metric} не определена: в колонке нет чисел.")
    raise AnalysisError(f"В результате нет скалярной метрики {metric}.")


def metric_line(metric: str, value: str) -> str:
    return f"- {metric}: {value}"


def render_report(manifest: dict, metrics: dict) -> str:
    lines = [
        f"# Результат анализа {manifest['id']}",
        "",
        "Расчёт воспроизводится из снимка входа и декларации или скрипта. "
        "Это свойство файла, а не оценка рынка, пока так не сказано отдельным свидетельством.",
        "",
    ]
    if manifest.get("synthetic"):
        lines.append("СИНТЕТИЧЕСКИЕ ДАННЫЕ, не наблюдение и не выгрузка компании.")
        lines.append("")
    lines.extend([
        f"- analysis_id: {manifest['id']}",
        f"- mode: {manifest['mode']}",
        f"- input: {manifest['input_path']}",
        f"- input_sha256: {manifest['input_sha256']}",
        f"- origin: {manifest['origin']}",
        f"- synthetic: {'да' if manifest.get('synthetic') else 'нет'}",
        "",
        "## Метрики",
        "",
    ])
    for key in sorted(metrics):
        value = metrics[key]
        if isinstance(value, dict):
            if not value:
                lines.append(f"- {key}: нет чисел")
                continue
            for sub in sorted(value):
                lines.append(f"- {key}.{sub}: {value[sub]}")
        elif value is None:
            lines.append(f"- {key}: нет чисел")
        else:
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## График", ""])
    if manifest.get("chart_path"):
        lines.append(f"- chart: {manifest['chart_path']}")
    else:
        lines.append("- chart: нет")
    if manifest.get("chart_note"):
        lines.append(f"- chart_note: {manifest['chart_note']}")
    lines.append("")
    return "\n".join(lines)


def analysis_dir(project: Path, analysis_id: str) -> Path:
    if not ID_RE.fullmatch(analysis_id or ""):
        raise AnalysisError(f"Идентификатор анализа: A001, получено {analysis_id or 'пусто'}.")
    return project / "analysis" / analysis_id


def next_id(project: Path) -> str:
    root = project / "analysis"
    numbers = []
    if root.is_dir():
        for path in root.iterdir():
            match = re.fullmatch(r"A(\d+)", path.name)
            if match:
                numbers.append(int(match.group(1)))
    return f"A{(max(numbers) if numbers else 0) + 1:03d}"


def load_manifest(project: Path, analysis_id: str) -> dict:
    path = analysis_dir(project, analysis_id) / "manifest.json"
    if not path.is_file():
        raise AnalysisError(f"Нет анализа {analysis_id}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"manifest {analysis_id} повреждён: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise AnalysisError(f"manifest {analysis_id} повреждён.")
    return payload


def load_metrics(project: Path, analysis_id: str) -> dict:
    manifest = load_manifest(project, analysis_id)
    result = project / str(manifest.get("result_path") or "")
    if not result.is_file():
        result = analysis_dir(project, analysis_id) / "result.json"
    return read_result(result)


def metric_value(project: Path, analysis_id: str, metric: str) -> str:
    return lookup_metric(load_metrics(project, analysis_id), metric)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _compute_saved(project: Path, folder: Path, manifest: dict, timeout: float) -> tuple[dict, str | None]:
    snapshot = project / manifest["input_path"]
    if manifest["mode"] == "spec":
        spec = parse_spec((folder / "spec.json").read_text(encoding="utf-8"))
        rows = load_table(snapshot)
        metrics = compute_metrics(rows, spec)
        chart_note = try_chart(rows, spec, folder / "chart.png")
        return metrics, chart_note
    metrics = run_script(
        project,
        folder / "script.py",
        snapshot,
        folder / "result.json",
        folder / "chart.png",
        manifest["id"],
        timeout,
    )
    chart_note = None if (folder / "chart.png").is_file() else None
    return metrics, chart_note


def run_analysis(
    project: Path,
    *,
    input_path: Path | None,
    spec_text: str | None,
    script_path: Path | None,
    analysis_id: str | None,
    timeout: float,
    synthetic: bool,
) -> dict:
    timeout = clamp_timeout(timeout)
    rerun = False
    if analysis_id:
        folder = analysis_dir(project, analysis_id)
        if folder.is_dir() and (folder / "manifest.json").is_file():
            rerun = True
        elif input_path is None:
            raise AnalysisError(f"Нет анализа {analysis_id}. Для нового расчёта передайте вход и декларацию или скрипт.")
    if rerun:
        if input_path is not None or spec_text is not None or script_path is not None:
            raise AnalysisError("Повтор анализа берёт сохранённый снимок. Новый вход — новый запуск без --id.")
        return _rerun(project, analysis_id or "", timeout)
    if input_path is None:
        raise AnalysisError("Укажите --input: CSV, JSON или XLSX внутри проекта.")
    if bool(spec_text) == bool(script_path):
        raise AnalysisError("Нужна ровно одна форма: декларация (--spec-file) или свой скрипт (--script).")
    analysis_id = analysis_id or next_id(project)
    folder = analysis_dir(project, analysis_id)
    if folder.exists():
        raise AnalysisError(f"Папка анализа {analysis_id} уже есть.")
    folder.mkdir(parents=True)
    try:
        return _create(project, folder, analysis_id, input_path, spec_text, script_path, timeout, synthetic)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _create(
    project: Path,
    folder: Path,
    analysis_id: str,
    input_path: Path,
    spec_text: str | None,
    script_path: Path | None,
    timeout: float,
    synthetic: bool,
) -> dict:
    snapshot_name = "input" + (input_path.suffix.lower() or ".dat")
    snapshot = folder / snapshot_name
    digest = copy_snapshot(input_path, snapshot)
    mode = "spec"
    if spec_text is not None:
        spec = parse_spec(spec_text)
        (folder / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        mode = "script"
        assert script_path is not None
        if script_is_untrusted_source(project, script_path):
            raise AnalysisError(
                "Скрипт из папки sources/ не исполняется: снимок источника — недоверенные данные. "
                "Положите свой скрипт в папку проекта вне sources/."
            )
        script_text = script_path.read_text(encoding="utf-8")
        if len(script_text) > MAX_SCRIPT_CHARS:
            raise AnalysisError("Скрипт анализа длиннее 100000 символов.")
        (folder / "script.py").write_text(
            script_text.replace("\r\n", "\n").replace("\r", "\n"),
            encoding="utf-8",
            newline="\n",
        )
        spec = None
    manifest = {
        "id": analysis_id,
        "mode": mode,
        "input_path": rel(project, snapshot),
        "input_sha256": digest,
        "origin": rel(project, input_path),
        "spec_path": rel(project, folder / "spec.json") if mode == "spec" else None,
        "script_path": rel(project, folder / "script.py") if mode == "script" else None,
        "result_path": rel(project, folder / "result.json"),
        "report_path": rel(project, folder / "report.md"),
        "chart_path": None,
        "chart_note": None,
        "timeout": timeout,
        "synthetic": bool(synthetic),
        "status": "done",
    }
    if mode == "spec":
        assert spec is not None
        rows = load_table(snapshot)
        metrics = compute_metrics(rows, spec)
        chart_note = try_chart(rows, spec, folder / "chart.png")
    else:
        metrics = run_script(
            project,
            folder / "script.py",
            snapshot,
            folder / "result.json",
            folder / "chart.png",
            analysis_id,
            timeout,
        )
        chart_note = None
    if (folder / "chart.png").is_file():
        manifest["chart_path"] = rel(project, folder / "chart.png")
        manifest["chart_note"] = None
    else:
        manifest["chart_note"] = chart_note
    (folder / "result.json").write_text(dump_result(metrics), encoding="utf-8", newline="\n")
    (folder / "report.md").write_text(render_report(manifest, metrics), encoding="utf-8", newline="\n")
    write_json(folder / "manifest.json", manifest)
    return {"id": analysis_id, "manifest": manifest, "metrics": metrics, "same": False}


def _script_metrics(
    project: Path,
    folder: Path,
    snapshot: Path,
    analysis_id: str,
    timeout: float,
) -> dict:
    """Run the script into a throwaway folder so an identical result does not touch the saved files."""
    sandbox = folder / "_rerun"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir()
    try:
        work_input = sandbox / snapshot.name
        shutil.copyfile(snapshot, work_input)
        work_script = sandbox / "script.py"
        shutil.copyfile(folder / "script.py", work_script)
        return run_script(
            project,
            work_script,
            work_input,
            sandbox / "result.json",
            sandbox / "chart.png",
            analysis_id,
            timeout,
        )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _rerun(project: Path, analysis_id: str, timeout: float) -> dict:
    folder = analysis_dir(project, analysis_id)
    manifest = load_manifest(project, analysis_id)
    result_path = folder / "result.json"
    previous = result_path.read_text(encoding="utf-8") if result_path.is_file() else ""
    snapshot = project / str(manifest["input_path"])
    if not snapshot.is_file():
        raise AnalysisError(f"{analysis_id}: снимок входа не найден ({manifest.get('input_path')}).")
    actual = content_digest(snapshot)
    if actual != manifest.get("input_sha256"):
        raise AnalysisError(
            f"{analysis_id}: sha256 снимка не совпадает с manifest. Файл входа анализа не переписывают руками."
        )
    if manifest.get("mode") == "spec":
        spec = parse_spec((folder / "spec.json").read_text(encoding="utf-8"))
        rows = load_table(snapshot)
        metrics = compute_metrics(rows, spec)
        chart_rows: list[dict[str, str]] | None = rows
        chart_spec: dict | None = spec
    elif manifest.get("mode") == "script":
        metrics = _script_metrics(project, folder, snapshot, analysis_id, timeout)
        chart_rows = None
        chart_spec = None
    else:
        raise AnalysisError(f"{analysis_id}: неизвестный mode.")
    rendered = dump_result(metrics)
    if rendered == previous:
        return {"id": analysis_id, "manifest": manifest, "metrics": metrics, "same": True}
    manifest["timeout"] = timeout
    chart_note = None
    if chart_rows is not None and chart_spec is not None:
        chart_note = try_chart(chart_rows, chart_spec, folder / "chart.png")
    if (folder / "chart.png").is_file():
        manifest["chart_path"] = rel(project, folder / "chart.png")
        manifest["chart_note"] = None
    else:
        manifest["chart_path"] = None
        manifest["chart_note"] = chart_note
    (folder / "result.json").write_text(rendered, encoding="utf-8", newline="\n")
    (folder / "report.md").write_text(render_report(manifest, metrics), encoding="utf-8", newline="\n")
    write_json(folder / "manifest.json", manifest)
    return {"id": analysis_id, "manifest": manifest, "metrics": metrics, "same": False}


def recompute(project: Path, analysis_id: str, *, enforce_hash: bool = True) -> dict:
    """Rerun from the stored snapshot without keeping side effects of a script."""
    folder = analysis_dir(project, analysis_id)
    manifest = load_manifest(project, analysis_id)
    timeout = clamp_timeout(manifest.get("timeout") or 5)
    snapshot = project / str(manifest.get("input_path") or "")
    if not snapshot.is_file():
        raise AnalysisError(f"{analysis_id}: снимок входа не найден.")
    if enforce_hash and content_digest(snapshot) != manifest.get("input_sha256"):
        raise AnalysisError(f"{analysis_id}: sha256 снимка не совпадает с manifest.")
    if manifest.get("mode") == "spec":
        spec = parse_spec((folder / "spec.json").read_text(encoding="utf-8"))
        return compute_metrics(load_table(snapshot), spec)
    if manifest.get("mode") != "script":
        raise AnalysisError(f"{analysis_id}: неизвестный mode.")
    sandbox = folder / "_verify"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir()
    protected = [
        folder / "result.json",
        folder / "report.md",
        folder / "chart.png",
        folder / "input" + snapshot.suffix,
        folder / "script.py",
        folder / "manifest.json",
    ]
    backup = {path: path.read_bytes() for path in protected if path.is_file()}
    try:
        work_input = sandbox / snapshot.name
        shutil.copyfile(snapshot, work_input)
        work_script = sandbox / "script.py"
        shutil.copyfile(folder / "script.py", work_script)
        return run_script(
            project,
            work_script,
            work_input,
            sandbox / "result.json",
            sandbox / "chart.png",
            analysis_id,
            timeout,
        )
    finally:
        for path, data in backup.items():
            path.write_bytes(data)
        shutil.rmtree(sandbox, ignore_errors=True)


def _numbers_outside_dates(text: str) -> list[str]:
    masked = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    masked = re.sub(r"\b(?:19|20)\d{2}\b", " ", masked)
    return NUMBER_RE.findall(masked)


def _same_number(left: str, right: str) -> bool:
    a = to_decimal(left)
    b = to_decimal(right)
    if a is None or b is None:
        return left.strip() == right.strip()
    return a == b


def verify_project(project: Path) -> list[dict]:
    import strat

    findings: list[dict] = []
    root = project / "analysis"
    recomputed: dict[str, dict | None] = {}
    errors: dict[str, str] = {}
    if root.is_dir():
        for folder in sorted(path for path in root.iterdir() if path.is_dir() and ID_RE.fullmatch(path.name)):
            analysis_id = folder.name
            manifest_path = folder / "manifest.json"
            if not manifest_path.is_file():
                findings.append(
                    {
                        "code": "analysis",
                        "message": f"{analysis_id}: нет manifest.json",
                        "warning": False,
                    }
                )
                errors[analysis_id] = "нет manifest"
                recomputed[analysis_id] = None
                continue
            try:
                manifest = load_manifest(project, analysis_id)
                snapshot = project / str(manifest.get("input_path") or "")
                if not snapshot.is_file():
                    raise AnalysisError(f"{analysis_id}: снимок входа не найден ({manifest.get('input_path')}).")
                if content_digest(snapshot) != manifest.get("input_sha256"):
                    findings.append(
                        {
                            "code": "analysis-hash",
                            "message": (
                                f"{analysis_id}: sha256 снимка {manifest.get('input_path')} "
                                "не совпадает с manifest."
                            ),
                            "warning": False,
                        }
                    )
                fresh = recompute(project, analysis_id, enforce_hash=False)
                recomputed[analysis_id] = fresh
                stored_path = folder / "result.json"
                if not stored_path.is_file():
                    raise AnalysisError(f"{analysis_id}: нет result.json")
                stored = read_result(stored_path)
                if stored != fresh:
                    findings.append(
                        {
                            "code": "analysis-mismatch",
                            "message": f"{analysis_id}: пересчёт не совпал с result.json.",
                            "warning": False,
                        }
                    )
            except AnalysisError as exc:
                errors[analysis_id] = str(exc)
                recomputed[analysis_id] = None
                findings.append({"code": "analysis-mismatch", "message": str(exc), "warning": False})

    sources, _source_errors = strat.load_jsonl(project / "sources.jsonl")
    evidence, _evidence_errors = strat.load_jsonl(project / "evidence.jsonl")
    by_source = {row.get("id"): row for row in sources}
    for row in evidence:
        evidence_id = str(row.get("id") or "")
        source = by_source.get(row.get("source")) or {}
        analysis_id = str(row.get("analysis_id") or source.get("analysis_id") or "")
        active = row.get("status") == "active"
        linked = bool(row.get("analysis_id") and row.get("metric") and row.get("input_sha256") and row.get("analysis_input"))
        measured = _numbers_outside_dates(str(row.get("claim") or "")) or _numbers_outside_dates(str(row.get("quote") or ""))
        data_source = source.get("kind") == "data" or bool(source.get("analysis_id")) or bool(row.get("analysis_id"))
        if data_source and measured and not linked:
            findings.append(
                {
                    "code": "analysis-link",
                    "message": (
                        f"{evidence_id}: число из анализа без ссылки на analysis id, файл входа и sha256. "
                        "Добавьте свидетельство с --analysis и --metric."
                    ),
                    "warning": not active,
                }
            )
        if not row.get("analysis_id"):
            continue
        analysis_id = str(row["analysis_id"])
        warning = not active
        if analysis_id in errors and recomputed.get(analysis_id) is None:
            continue
        metrics = recomputed.get(analysis_id)
        if metrics is None and analysis_id not in errors:
            try:
                metrics = recompute(project, analysis_id, enforce_hash=False)
                recomputed[analysis_id] = metrics
            except AnalysisError as exc:
                findings.append({"code": "analysis-mismatch", "message": str(exc), "warning": warning})
                continue
        if not isinstance(metrics, dict):
            continue
        metric = str(row.get("metric") or "")
        try:
            fresh_value = lookup_metric(metrics, metric)
        except AnalysisError as exc:
            findings.append({"code": "analysis-mismatch", "message": f"{evidence_id}: {exc}", "warning": warning})
            continue
        recorded = str(row.get("value") if row.get("value") is not None else "")
        if not recorded or not _same_number(recorded, fresh_value):
            findings.append(
                {
                    "code": "analysis-mismatch",
                    "message": (
                        f"{evidence_id}: значение {recorded or 'пусто'} метрики {metric} "
                        f"не совпало с пересчётом ({fresh_value})."
                    ),
                    "warning": warning,
                }
            )
        manifest = {}
        try:
            manifest = load_manifest(project, analysis_id)
        except AnalysisError:
            manifest = {}
        if row.get("input_sha256") and manifest.get("input_sha256") and row.get("input_sha256") != manifest.get("input_sha256"):
            findings.append(
                {
                    "code": "analysis-hash",
                    "message": f"{evidence_id}: input_sha256 не совпадает со снимком {analysis_id}.",
                    "warning": warning,
                }
            )
        quote = str(row.get("quote") or "")
        needle = metric_line(metric, fresh_value)
        if quote and needle not in quote and active:
            findings.append(
                {
                    "code": "analysis-mismatch",
                    "message": f"{evidence_id}: цитата не содержит строку пересчёта «{needle}».",
                    "warning": False,
                }
            )

    texts = []
    for name in strat.ARTIFACTS:
        body = strat.read_text(project / name)
        if body:
            texts.append((name, body))
    for row in evidence:
        if row.get("status") != "active":
            continue
        texts.append((str(row.get("id") or "evidence"), str(row.get("claim") or "")))
    for name, body in texts:
        for number, analysis_id, metric in ANCHOR_RE.findall(body):
            metrics = recomputed.get(analysis_id)
            if metrics is None and analysis_id not in errors:
                try:
                    metrics = recompute(project, analysis_id, enforce_hash=False)
                    recomputed[analysis_id] = metrics
                except AnalysisError as exc:
                    findings.append({"code": "analysis-mismatch", "message": f"{name}: {exc}", "warning": False})
                    errors[analysis_id] = str(exc)
                    continue
            if not isinstance(metrics, dict):
                if analysis_id not in errors:
                    findings.append(
                        {
                            "code": "analysis-mismatch",
                            "message": f"{name}: нет анализа {analysis_id} для [{analysis_id}.{metric}].",
                            "warning": False,
                        }
                    )
                    errors[analysis_id] = "нет"
                continue
            try:
                fresh_value = lookup_metric(metrics, metric)
            except AnalysisError as exc:
                findings.append({"code": "analysis-mismatch", "message": f"{name}: {exc}", "warning": False})
                continue
            if not _same_number(number, fresh_value):
                findings.append(
                    {
                        "code": "analysis-mismatch",
                        "message": (
                            f"{name}: {number} [{analysis_id}.{metric}] не совпало с пересчётом ({fresh_value})."
                        ),
                        "warning": False,
                    }
                )
    return findings

