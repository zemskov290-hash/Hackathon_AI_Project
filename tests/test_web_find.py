"""Search results and failed-fetch suggestions. No invented paths, no live network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import web_find  # noqa: E402

STRAT = ROOT / "scripts" / "strat.py"
FIXTURES = ROOT / "tests" / "fixtures" / "search"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


DDG_HTML = fixture("duckduckgo-html.html")
LITE_HTML = fixture("duckduckgo-lite.html")
BING_HTML = fixture("bing.html")


def stop_http_server(server, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join()
    server.server_close()


class ParseTests(unittest.TestCase):
    def test_duckduckgo_unwraps_redirect_and_keeps_snippet(self) -> None:
        hits = web_find.parse_duckduckgo_html(fixture("duckduckgo-html.html"))
        self.assertEqual(hits[0]["url"], "https://example.com/tariffs")
        self.assertEqual(hits[0]["title"], "Тарифы")
        self.assertIn("Семейный", hits[0]["snippet"])
        self.assertEqual(hits[1]["url"], "https://news.example.com/review")
        self.assertEqual(hits[1]["snippet"], "Короткий обзор")

    def test_duckduckgo_lite_unwraps_result_link(self) -> None:
        hits = web_find.parse_duckduckgo_lite(fixture("duckduckgo-lite.html"))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["url"], "https://shop.example.com/plans")
        self.assertEqual(hits[0]["title"], "Планы")
        self.assertEqual(hits[0]["snippet"], "Сравнение планов")

    def test_bing_decodes_result_url(self) -> None:
        hits = web_find.parse_bing_html(fixture("bing.html"))
        self.assertEqual(hits[0]["url"], "https://example.com/tariffs")
        self.assertEqual(hits[0]["title"], "Тарифы")
        self.assertIn("Семейный", hits[0]["snippet"])
        self.assertEqual(hits[1]["url"], "https://news.example.com/review")
        self.assertEqual(hits[1]["snippet"], "Короткий обзор")

    def test_default_chain_uses_a_browser_get(self) -> None:
        previous = os.environ.pop("STRAT_SEARCH_BASES", None)
        try:
            requests = web_find.engine_requests("тарифы")
        finally:
            if previous is not None:
                os.environ["STRAT_SEARCH_BASES"] = previous
        names = [name for name, _url in requests]
        self.assertEqual(names, ["duckduckgo", "duckduckgo-lite", "bing"])
        self.assertTrue(requests[0][1].startswith("https://html.duckduckgo.com/html/?q="))
        self.assertTrue(requests[1][1].startswith("https://lite.duckduckgo.com/lite/?q="))
        self.assertTrue(requests[2][1].startswith("https://www.bing.com/search?q="))
        self.assertIn("Mozilla", web_find.USER_AGENT)
        self.assertNotIn("strat-factory", web_find.USER_AGENT)

    def test_path_words_ignore_short_and_extensions(self) -> None:
        words = web_find.path_words("https://host/no-such-tariffs/index.html")
        self.assertIn("tariffs", words)
        self.assertIn("such", words)
        self.assertNotIn("no", words)
        self.assertNotIn("html", words)


class LocalServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[str] = []

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent.seen.append(self.path.split("?", 1)[0])
                path = self.path.split("?", 1)[0]
                if path == "/ddg":
                    self._send(200, "text/html", DDG_HTML)
                    return
                if path == "/challenge":
                    self._send(202, "text/html", "<html>bot</html>")
                    return
                if path == "/empty":
                    self._send(200, "text/html", "<html><body>нет ссылок</body></html>")
                    return
                if path == "/lite":
                    self._send(200, "text/html", LITE_HTML)
                    return
                if path == "/bing":
                    self._send(200, "text/html", BING_HTML)
                    return
                if path in {"/", "/index.html"}:
                    html = (
                        "<html><body>"
                        "<a href=\"/tariffs/family\">Family tariffs</a>"
                        "<a href=\"/about\">About</a>"
                        "<a href=\"https://evil.example/tariffs\">offsite</a>"
                        "</body></html>"
                    )
                    self._send(200, "text/html", html)
                    return
                if path == "/robots.txt":
                    loc = f"http://127.0.0.1:{parent.port}/sitemap.xml"
                    self._send(200, "text/plain", f"User-agent: *\nSitemap: {loc}\n")
                    return
                if path == "/sitemap.xml":
                    host = f"http://127.0.0.1:{parent.port}"
                    xml = (
                        "<urlset>"
                        f"<url><loc>{host}/tariffs/family</loc></url>"
                        f"<url><loc>{host}/news</loc></url>"
                        "</urlset>"
                    )
                    self._send(200, "application/xml", xml)
                    return
                if path == "/tariffs/family":
                    self._send(200, "text/html", "<html><title>Тариф</title><body>Пакет 9</body></html>")
                    return
                self._send(404, "text/plain", "missing")

            def _send(self, status: int, content_type: str, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(stop_http_server, self.server, self.thread)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.environ.copy()
        self.env["STRAT_PROJECTS_DIR"] = self.tmp.name
        self.env["STRAT_FETCH_RETRIES"] = "1"
        self.env["STRAT_FETCH_BACKOFF_SECONDS"] = "0"
        self.env["STRAT_SEARCH_BASES"] = (
            f"duckduckgo|http://127.0.0.1:{self.port}/ddg,"
            f"duckduckgo-lite|http://127.0.0.1:{self.port}/lite,"
            f"bing|http://127.0.0.1:{self.port}/bing"
        )

    def cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        merged = dict(self.env)
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, str(STRAT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=merged,
            timeout=30,
        )

    def test_search_uses_local_engine_and_marks_untrusted(self) -> None:
        created = self.cli("init", "look", "--title", "Поиск")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        found = self.cli("search", "look", "семейные тарифы")
        self.assertEqual(found.returncode, 0, found.stdout + found.stderr)
        self.assertIn("НЕДОВЕРЕННЫЕ ДАННЫЕ", found.stdout)
        self.assertIn("https://example.com/tariffs", found.stdout)
        self.assertIn("Тарифы", found.stdout)
        self.assertIn("Семейный", found.stdout)
        project = Path(self.tmp.name) / "look"
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        steps = [key for key in state["steps"] if key.startswith("search:")]
        self.assertEqual(len(steps), 1)
        self.assertEqual(state["steps"][steps[0]]["status"], "done")
        log_text = (project / "log.md").read_text(encoding="utf-8")
        self.assertIn("Поиск", log_text)
        self.assertIn("удалось 1 из 1", log_text.casefold())

    def test_search_falls_back_when_the_first_engine_fails(self) -> None:
        self.env["STRAT_SEARCH_BASES"] = (
            f"duckduckgo|http://127.0.0.1:{self.port}/challenge,"
            f"duckduckgo-lite|http://127.0.0.1:{self.port}/empty,"
            f"bing|http://127.0.0.1:{self.port}/bing"
        )
        created = self.cli("init", "fallback", "--title", "Запасной")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        found = self.cli("search", "fallback", "планы")
        self.assertEqual(found.returncode, 0, found.stdout + found.stderr)
        self.assertIn("bing", found.stdout)
        self.assertIn("https://example.com/tariffs", found.stdout)
        self.assertNotIn("https://shop.example.com/plans", found.stdout)

    def test_fetch_404_suggests_published_links_and_does_not_guess(self) -> None:
        created = self.cli("init", "miss", "--title", "Промах")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        missing = f"http://127.0.0.1:{self.port}/no-such-tariffs"
        self.seen.clear()
        failed = self.cli("fetch", "miss", missing)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("404", failed.stdout)
        self.assertIn("НЕДОВЕРЕННЫЕ ДАННЫЕ", failed.stdout)
        self.assertIn(f"http://127.0.0.1:{self.port}/tariffs/family", failed.stdout)
        self.assertNotIn("/about", failed.stdout)
        self.assertNotIn("evil.example", failed.stdout)
        self.assertNotIn("/news", failed.stdout)
        allowed = {"/no-such-tariffs", "/", "/robots.txt", "/sitemap.xml"}
        self.assertTrue(set(self.seen) <= allowed, self.seen)
        project = Path(self.tmp.name) / "miss"
        log_text = (project / "log.md").read_text(encoding="utf-8")
        self.assertIn("удалось 0 из 1", log_text.casefold())
        self.seen.clear()
        again = self.cli("fetch", "miss", missing)
        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        self.assertTrue(set(self.seen) <= allowed, self.seen)
        self.assertIn("удалось 0 из 2", again.stdout)


if __name__ == "__main__":
    unittest.main()
