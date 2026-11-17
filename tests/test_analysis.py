"""Reproducible analysis and media inspection. Standard-library unittest only."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import analysis  # noqa: E402
import inspect_media  # noqa: E402
from test_mcp import initialize, start_server, tool_body, tool_text  # noqa: E402
from test_strat import assert_code, minimal_pdf, run, stop_http_server  # noqa: E402

FIXTURE_HTML = (ROOT / "tests" / "fixtures" / "ui_page.html").read_text(encoding="utf-8")
SPEC = {
    "filters": [{"column": "segment", "eq": "family"}],
    "metrics": [
        {"id": "n", "fn": "count"},
        {"id": "mean_price", "fn": "mean", "column": "price"},
    ],
    "chart": {"type": "bar", "column": "operator", "agg": "count"},
}
CSV = "operator,segment,price\nАльфа,family,300\nАльфа,family,400\nБета,family,350\nБета,solo,900\n"


class DomAndImageTests(unittest.TestCase):
    def test_html_fixture_comes_from_the_dom(self) -> None:
        extracted = inspect_media.extract_dom(FIXTURE_HTML)
        self.assertEqual(extracted["title"], "ПримерСвязь — семейный тариф")
        headings = [item["text"] for item in extracted["headings"]]
        self.assertIn("Семейный тариф «Вместе»", headings)
        self.assertIn("Тарифы", headings)
        prices = [item["text"] for item in extracted["prices"]]
        self.assertIn("399 ₽", prices)
        self.assertNotIn("999 ₽", prices)
        self.assertTrue(any("Вместе 30" in item["context"] for item in extracted["prices"]))
        self.assertEqual(extracted["ctas"][0]["text"], "Подключить")
        self.assertEqual(extracted["ctas"][0]["href"], "/connect")
        self.assertEqual(extracted["steps"][0], ["Выберите тариф", "Укажите номера", "Подтвердите"])
        screens = {item["id"]: item["label"] for item in extracted["screens"]}
        self.assertEqual(screens["plans"], "Тарифы")
        self.assertEqual(screens["how"], "Как подключить")

    def test_png_and_jpeg_sizes(self) -> None:
        png = inspect_media.png_bytes(2, 3)
        self.assertEqual(inspect_media.png_size(png), (2, 3))
        jpeg = inspect_media.jpeg_bytes(4, 5)
        self.assertEqual(inspect_media.jpeg_size(jpeg), (4, 5))

    def test_transcript_drops_timestamps(self) -> None:
        plain = inspect_media.transcript_plain("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nБерём семейный тариф\n")
        self.assertIn("Берём семейный тариф", plain)
        self.assertNotIn("-->", plain)


class AnalysisCliTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.env = {"STRAT_PROJECTS_DIR": str(self.projects)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, env: dict | None = None) -> object:
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run(*args, env=merged)

    def project(self, name: str = "calc") -> Path:
        assert_code(self.cli("init", name, "--title", "Расчёт"))
        return self.projects / name

    def write_table(self, project: Path) -> None:
        (project / "sources" / "tariffs.csv").write_text(CSV, encoding="utf-8")
        (project / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")

    def test_repeat_matches_and_check_catches_a_changed_number(self) -> None:
        project = self.project()
        self.write_table(project)
        first = self.cli(
            "analyze",
            "calc",
            "--input",
            "sources/tariffs.csv",
            "--spec-file",
            "spec.json",
            "--synthetic",
            "--metric",
            "mean_price",
            "--claim",
            "В синтетической таблице семейных цен средняя цена равна 350.",
            "--confidence",
            "high",
        )
        assert_code(first)
        self.assertIn("analysis_id: A001", first.stdout)
        self.assertIn("metric mean_price: 350", first.stdout)
        self.assertIn("id: E001", first.stdout)
        report = (project / "analysis" / "A001" / "report.md").read_text(encoding="utf-8")
        result_bytes = (project / "analysis" / "A001" / "result.json").read_bytes()
        self.assertIn("- mean_price: 350", report)
        self.assertIn("input_sha256:", report)
        self.assertIn("синтетические данные", first.stdout.casefold() + report.casefold())
        if __import__("importlib").util.find_spec("matplotlib"):
            self.assertTrue((project / "analysis" / "A001" / "chart.png").is_file())
        else:
            self.assertIn("matplotlib не установлен", first.stdout)
        again = self.cli("analyze", "calc", "--id", "A001")
        assert_code(again)
        self.assertIn("Тот же результат.", again.stdout)
        self.assertEqual((project / "analysis" / "A001" / "result.json").read_bytes(), result_bytes)
        checked = self.cli("check", "calc")
        assert_code(checked)
        self.assertNotIn("[analysis-mismatch]", checked.stdout)
        rows = [
            json.loads(line)
            for line in (project / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(rows[0]["analysis_id"], "A001")
        self.assertEqual(rows[0]["metric"], "mean_price")
        self.assertEqual(rows[0]["value"], "350")
        self.assertEqual(rows[0]["input_sha256"], json.loads((project / "analysis" / "A001" / "manifest.json").read_text(encoding="utf-8"))["input_sha256"])
        self.assertTrue(rows[0]["analysis_input"].endswith("input.csv"))
        rows[0]["value"] = "999"
        (project / "evidence.jsonl").write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
        (project / "brief.md").write_text(
            (project / "brief.md").read_text(encoding="utf-8") + "\n999 [A001.mean_price]\n",
            encoding="utf-8",
        )
        mismatch = self.cli("check", "calc")
        assert_code(mismatch, 1)
        self.assertIn("[analysis-mismatch]", mismatch.stdout)
        self.assertIn("999", mismatch.stdout)
        self.assertIn("350", mismatch.stdout)
        verified = self.cli("analyze", "calc", "--verify")
        assert_code(verified, 1)
        self.assertIn("[analysis-mismatch]", verified.stdout)

    def test_result_file_drift_is_caught(self) -> None:
        project = self.project("drift")
        self.write_table(project)
        assert_code(
            self.cli("analyze", "drift", "--input", "sources/tariffs.csv", "--spec-file", "spec.json")
        )
        result_path = project / "analysis" / "A001" / "result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["metrics"]["mean_price"] = "1"
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatch = self.cli("check", "drift")
        assert_code(mismatch, 1)
        self.assertIn("result.json", mismatch.stdout)

    def test_script_timeout_escape_network_and_source_refusal(self) -> None:
        project = self.project("box")
        (project / "sources" / "tariffs.csv").write_text("price\n1\n", encoding="utf-8")
        work = project / "work"
        work.mkdir()
        secret = self.projects / "secret.txt"
        secret.write_text("секрет-снаружи", encoding="utf-8")
        outside = secret.resolve()
        (work / "escape.py").write_text(
            textwrap.dedent(
                f"""
                from pathlib import Path
                Path({str(outside)!r}).read_text(encoding="utf-8")
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        escaped = self.cli(
            "analyze",
            "box",
            "--input",
            "sources/tariffs.csv",
            "--script",
            "work/escape.py",
            "--timeout",
            "5",
        )
        assert_code(escaped, 1)
        self.assertIn("выход за папку проекта", escaped.stdout)
        self.assertNotIn("секрет-снаружи", escaped.stdout + escaped.stderr)

        class Handler(BaseHTTPRequestHandler):
            hits = 0

            def do_GET(self) -> None:  # noqa: N802
                Handler.hits += 1
                body = b"nope"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(stop_http_server, server, thread)
        (work / "net.py").write_text(
            textwrap.dedent(
                f"""
                import socket
                socket.create_connection(("127.0.0.1", {port}), timeout=1)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        networked = self.cli(
            "analyze",
            "box",
            "--input",
            "sources/tariffs.csv",
            "--script",
            "work/net.py",
            "--timeout",
            "5",
        )
        assert_code(networked, 1)
        self.assertIn("сеть в анализе запрещена", networked.stdout)
        self.assertEqual(Handler.hits, 0)

        (work / "sleep.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        slowed = self.cli(
            "analyze",
            "box",
            "--input",
            "sources/tariffs.csv",
            "--script",
            "work/sleep.py",
            "--timeout",
            "1",
        )
        assert_code(slowed, 1)
        self.assertIn("лимит времени", slowed.stdout)

        (project / "sources" / "evil.py").write_text(
            "import os\nos.system('echo pwned')\n",
            encoding="utf-8",
        )
        refused = self.cli(
            "analyze",
            "box",
            "--input",
            "sources/tariffs.csv",
            "--script",
            "sources/evil.py",
        )
        assert_code(refused, 1)
        self.assertIn("sources/", refused.stdout)

        key = "super-secret-key"
        (work / "leak.py").write_text(
            textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path
                Path(os.environ["STRAT_ANALYSIS_RESULT"]).write_text(
                    json.dumps({"metrics": {"leaked": os.environ.get("STRAT_LLM_API_KEY", "")}}),
                    encoding="utf-8",
                )
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        leaked = self.cli(
            "analyze",
            "box",
            "--input",
            "sources/tariffs.csv",
            "--script",
            "work/leak.py",
            env={"STRAT_LLM_API_KEY": key},
        )
        assert_code(leaked)
        self.assertNotIn(key, leaked.stdout + leaked.stderr)
        stored = (project / "analysis").rglob("result.json")
        blobs = "\n".join(path.read_text(encoding="utf-8") for path in stored)
        self.assertNotIn(key, blobs)
        self.assertIn('"leaked": ""', blobs)

    def test_xlsx_explains_missing_package(self) -> None:
        project = self.project("sheet")
        (project / "sources" / "book.xlsx").write_bytes(b"not a workbook")
        (project / "spec.json").write_text(
            json.dumps({"metrics": [{"id": "n", "fn": "count"}]}),
            encoding="utf-8",
        )
        result = self.cli("analyze", "sheet", "--input", "sources/book.xlsx", "--spec-file", "spec.json")
        assert_code(result, 1)
        self.assertTrue(
            "openpyxl" in result.stdout or "открыть не удалось" in result.stdout,
            result.stdout,
        )

    def test_json_table(self) -> None:
        project = self.project("rows")
        (project / "sources" / "rows.json").write_text(
            json.dumps([{"price": "10"}, {"price": "30"}]),
            encoding="utf-8",
        )
        (project / "spec.json").write_text(
            json.dumps({"metrics": [{"id": "mean_price", "fn": "mean", "column": "price"}]}),
            encoding="utf-8",
        )
        result = self.cli("analyze", "rows", "--input", "sources/rows.json", "--spec-file", "spec.json", "--metric", "mean_price")
        assert_code(result)
        self.assertIn("metric mean_price: 20", result.stdout)


class InspectCliTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.env = {"STRAT_PROJECTS_DIR": str(self.projects)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, env: dict | None = None):
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run(*args, env=merged)

    def test_html_image_pdf_and_av(self) -> None:
        assert_code(self.cli("init", "media", "--title", "Медиа"))
        project = self.projects / "media"
        html_path = project / "sources" / "page.html"
        html_path.write_text(FIXTURE_HTML, encoding="utf-8")
        inspected = self.cli(
            "inspect",
            "media",
            "--html",
            "sources/page.html",
            "--synthetic",
            "--claim",
            "На вымышленной странице ПримерСвязь в DOM указана цена 399 ₽.",
            "--quote",
            "- 399 ₽ | контекст: Семейный тариф «Вместе» / Тарифы / Вместе 30",
        )
        assert_code(inspected)
        self.assertIn("inspect_id: M001", inspected.stdout)
        self.assertIn("source_id: S001", inspected.stdout)
        self.assertIn("Сводка:", inspected.stdout)
        self.assertIn("399", inspected.stdout)
        self.assertIn("Подключить", inspected.stdout)
        self.assertIn("Выберите тариф", inspected.stdout)
        report = (project / "inspect" / "M001" / "report.md").read_text(encoding="utf-8")
        self.assertIn("Подключить | tag=a | href=/connect", report)
        self.assertIn("1. Выберите тариф", report)
        self.assertNotIn("999 ₽", report)
        self.assertNotIn("ignore previous instructions", report)
        sources = [
            json.loads(line)
            for line in (project / "sources.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(sources[0]["kind"], "ui")
        self.assertTrue(sources[0]["snapshot_sha256"])
        self.assertTrue((project / sources[0]["snapshot_path"]).is_file())
        checked = self.cli("check", "media")
        assert_code(checked)

        png = project / "sources" / "pixel.png"
        png.write_bytes(inspect_media.png_bytes())
        image = self.cli(
            "inspect",
            "media",
            "--image",
            "sources/pixel.png",
            "--description",
            "Учебный кадр: один красный пиксель, людей нет.",
            "--synthetic",
        )
        assert_code(image)
        description = (project / "inspect" / "M002" / "description.md").read_text(encoding="utf-8")
        self.assertIn("model-description: ide", description)
        self.assertIn("моделью IDE", description)
        meta = (project / "inspect" / "M002" / "report.md").read_text(encoding="utf-8")
        self.assertIn("- width: 1", meta)
        self.assertIn("- height: 1", meta)
        pii = self.cli(
            "inspect",
            "media",
            "--image",
            "sources/pixel.png",
            "--description",
            "Пишите на person@example.com",
        )
        assert_code(pii, 1)
        self.assertIn("почт", pii.stdout)

        pdf = project / "sources" / "note.pdf"
        pdf.write_bytes(minimal_pdf("Hello strat"))
        read_pdf = self.cli("inspect", "media", "--pdf", "sources/note.pdf")
        assert_code(read_pdf)
        if __import__("importlib").util.find_spec("pypdf"):
            self.assertIn("Hello strat", read_pdf.stdout + (project / "inspect" / "M003" / "report.md").read_text(encoding="utf-8"))
        else:
            self.assertIn("pypdf", read_pdf.stdout)
            self.assertIn("не извлеч", read_pdf.stdout)

        clip = project / "sources" / "clip.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42not-a-real-movie")
        silent = self.cli("inspect", "media", "--av", "sources/clip.mp4")
        assert_code(silent)
        self.assertIn("Расшифровки", silent.stdout)
        self.assertIn("не выдумывается", silent.stdout)
        vtt = project / "sources" / "clip.vtt"
        vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nСемейный тариф без витрины\n", encoding="utf-8")
        heard = self.cli("inspect", "media", "--av", "sources/clip.mp4", "--transcript", "sources/clip.vtt")
        assert_code(heard)
        heard_report = list((project / "inspect").glob("M00*/report.md"))
        self.assertTrue(any("Семейный тариф без витрины" in path.read_text(encoding="utf-8") for path in heard_report))

    def test_repeat_inspect_returns_the_same_snapshot(self) -> None:
        assert_code(self.cli("init", "again", "--title", "Повтор"))
        project = self.projects / "again"
        (project / "sources" / "page.html").write_text(FIXTURE_HTML, encoding="utf-8")
        (project / "sources" / "pixel.png").write_bytes(inspect_media.png_bytes())
        assert_code(self.cli("inspect", "again", "--html", "sources/page.html", "--synthetic"))
        image = self.cli(
            "inspect",
            "again",
            "--image",
            "sources/pixel.png",
            "--description",
            "Учебный кадр: один красный пиксель, людей нет.",
            "--synthetic",
        )
        assert_code(image)
        self.assertIn("description_source_id: S003", image.stdout)
        self.assertIn("- width: 1", image.stdout)
        self.assertIn("- format: png", image.stdout)

        def ids_and_bytes() -> tuple[list[str], list[str], dict[str, bytes]]:
            inspect_ids = sorted(path.name for path in (project / "inspect").iterdir() if path.is_dir())
            sources = [
                json.loads(line)["id"]
                for line in (project / "sources.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            blobs = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            }
            return inspect_ids, sources, blobs

        before_ids, before_sources, before_blobs = ids_and_bytes()
        self.assertEqual(before_ids, ["M001", "M002"])
        self.assertEqual(before_sources, ["S001", "S002", "S003"])
        html_again = self.cli("inspect", "again", "--html", "inspect/M001/input.html")
        assert_code(html_again)
        self.assertIn("Уже разобрано", html_again.stdout)
        self.assertIn("inspect_id: M001", html_again.stdout)
        self.assertIn("source_id: S001", html_again.stdout)
        image_again = self.cli("inspect", "again", "--image", "inspect/M002/input.png", "--description", "другой текст")
        assert_code(image_again)
        self.assertIn("Уже разобрано", image_again.stdout)
        self.assertIn("inspect_id: M002", image_again.stdout)
        self.assertIn("description_source_id: S003", image_again.stdout)
        after_ids, after_sources, after_blobs = ids_and_bytes()
        self.assertEqual(after_ids, before_ids)
        self.assertEqual(after_sources, before_sources)
        self.assertEqual(after_blobs, before_blobs)
        forced = self.cli("inspect", "again", "--html", "sources/page.html", "--force")
        assert_code(forced)
        self.assertIn("inspect_id: M003", forced.stdout)
        self.assertNotIn("Уже разобрано", forced.stdout)
        self.assertEqual(
            sorted(path.name for path in (project / "inspect").iterdir() if path.is_dir()),
            ["M001", "M002", "M003"],
        )

    def test_media_evidence_without_snapshot_warns(self) -> None:
        assert_code(self.cli("init", "gap", "--title", "Дыра"))
        project = self.projects / "gap"
        (project / "sources.jsonl").write_text(
            json.dumps(
                {
                    "id": "S001",
                    "url_or_path": "https://example.test/screen",
                    "title": "Кадр без файла",
                    "accessed": "2026-11-17",
                    "kind": "media",
                    "sha256": None,
                    "reliability": "low",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (project / "evidence.jsonl").write_text(
            json.dumps(
                {
                    "id": "E001",
                    "claim": "На кадре что-то есть, но снимка нет.",
                    "type": "hypothesis",
                    "source": "S001",
                    "quote": "",
                    "locator": "",
                    "confidence": "low",
                    "date": "2026-11-17",
                    "status": "active",
                    "superseded_by": None,
                    "depends_on": [],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        checked = self.cli("check", "gap")
        assert_code(checked)
        self.assertIn("[media-snapshot]", checked.stdout)

    def test_fetch_keeps_html_for_inspect(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = FIXTURE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(stop_http_server, server, thread)
        assert_code(self.cli("init", "page", "--title", "Страница"))
        url = f"http://127.0.0.1:{server.server_address[1]}/plans"
        assert_code(self.cli("fetch", "page", url))
        project = self.projects / "page"
        html_files = list((project / "sources").glob("*.html"))
        self.assertEqual(len(html_files), 1)
        self.assertIn("399 ₽", html_files[0].read_text(encoding="utf-8"))
        relative = html_files[0].relative_to(project).as_posix()
        inspected = self.cli("inspect", "page", "--html", relative, "--synthetic")
        assert_code(inspected)
        report = next((project / "inspect").glob("M001/report.md")).read_text(encoding="utf-8")
        self.assertIn("399 ₽", report)
        self.assertNotIn("999 ₽", report)


class McpAnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        self.server = start_server(self.projects)
        self.addCleanup(self.server.close)
        self.addCleanup(self.tmp.cleanup)
        initialize(self.server)

    def test_analyze_tool_is_stable(self) -> None:
        created = tool_body(self.server.call("strat_init", {"project_id": "mcpcalc", "title": "MCP расчёт"}))
        self.assertFalse(created["isError"], tool_text(created))
        project = self.projects / "mcpcalc"
        (project / "sources" / "prices.csv").write_text("price\n10\n20\n30\n", encoding="utf-8")
        spec = json.dumps(
            {"metrics": [{"id": "n", "fn": "count"}, {"id": "mean_price", "fn": "mean", "column": "price"}]}
        )
        first = tool_body(
            self.server.call(
                "strat_analyze",
                {
                    "project": "mcpcalc",
                    "input": "sources/prices.csv",
                    "spec": spec,
                    "metric": "mean_price",
                    "claim": "Средняя цена в учебной таблице равна 20.",
                },
            )
        )
        self.assertFalse(first["isError"], tool_text(first))
        self.assertTrue(first["structuredContent"]["untrusted"])
        self.assertIn("НЕДОВЕРЕННЫЕ ДАННЫЕ", tool_text(first))
        self.assertEqual(first["structuredContent"]["analysis_id"], "A001")
        self.assertEqual(first["structuredContent"]["metrics"]["mean_price"], "20")
        self.assertEqual(first["structuredContent"]["metrics"]["n"], "3")
        self.assertTrue(first["structuredContent"]["input_sha256"])
        second = tool_body(self.server.call("strat_analyze", {"project": "mcpcalc", "id": "A001"}))
        self.assertFalse(second["isError"], tool_text(second))
        self.assertEqual(second["structuredContent"]["metrics"], first["structuredContent"]["metrics"])
        self.assertIn("Тот же результат", tool_text(second))
        outside = self.server.call(
            "strat_analyze",
            {"project": "mcpcalc", "input": str(ROOT / "README.md"), "spec": spec},
        )
        self.assertTrue(tool_body(outside)["isError"])
        self.assertIn("вне проекта", tool_text(tool_body(outside)).casefold() + tool_text(tool_body(outside)))

    def test_inspect_tool_returns_the_extract(self) -> None:
        created = tool_body(self.server.call("strat_init", {"project_id": "mcpmedia", "title": "MCP разбор"}))
        self.assertFalse(created["isError"], tool_text(created))
        project = self.projects / "mcpmedia"
        (project / "sources" / "page.html").write_text(FIXTURE_HTML, encoding="utf-8")
        (project / "sources" / "pixel.png").write_bytes(inspect_media.png_bytes(2, 4))
        page = tool_body(
            self.server.call("strat_inspect", {"project": "mcpmedia", "html": "sources/page.html", "synthetic": True})
        )
        self.assertFalse(page["isError"], tool_text(page))
        self.assertIn("НЕДОВЕРЕННЫЕ ДАННЫЕ", tool_text(page))
        self.assertIn("Подключить", tool_text(page))
        self.assertTrue(page["structuredContent"]["untrusted"])
        extract = page["structuredContent"]["extract"]
        self.assertEqual(extract["title"], "ПримерСвязь — семейный тариф")
        self.assertTrue(any("399" in item["text"] for item in extract["prices"]))
        self.assertTrue(any(item["text"] == "Подключить" for item in extract["ctas"]))
        self.assertIn("Выберите тариф", extract["steps"][0])
        image = tool_body(
            self.server.call("strat_inspect", {"project": "mcpmedia", "image": "sources/pixel.png"})
        )
        self.assertFalse(image["isError"], tool_text(image))
        self.assertEqual(image["structuredContent"]["extract"], {"format": "png", "width": 2, "height": 4, "bytes": len(inspect_media.png_bytes(2, 4))})
        self.assertIn("- width: 2", tool_text(image))
