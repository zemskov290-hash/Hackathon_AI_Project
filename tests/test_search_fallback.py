"""Search retries anti-bot answers, tries the POST form and falls back to links a site published."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import web_find  # noqa: E402


class SearchFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {key: os.environ.get(key) for key in ("STRAT_SEARCH_BASES", "STRAT_SEARCH_RETRY_DELAY", "STRAT_SEARCH_RETRIES")}
        os.environ["STRAT_SEARCH_RETRY_DELAY"] = "0"
        os.environ["STRAT_SEARCH_RETRIES"] = "1"

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_202_is_retried_then_parsed(self) -> None:
        os.environ["STRAT_SEARCH_BASES"] = "duckduckgo|https://ddg.test/html/"
        html = (ROOT / "tests" / "fixtures" / "search" / "duckduckgo-html.html").read_text(encoding="utf-8")
        answers = iter([(202, ""), (200, html)])
        with mock.patch.object(web_find, "http_get", side_effect=lambda url, data=None: next(answers)):
            engine, hits = web_find.search_query("тарифы")
        self.assertEqual(engine, "duckduckgo")
        self.assertTrue(hits)

    def test_post_engine_sends_form(self) -> None:
        os.environ["STRAT_SEARCH_BASES"] = "duckduckgo-post|https://ddg.test/html/"
        html = (ROOT / "tests" / "fixtures" / "search" / "duckduckgo-html.html").read_text(encoding="utf-8")
        seen = []

        def fake(url, data=None):
            seen.append((url, data))
            return 200, html

        with mock.patch.object(web_find, "http_get", side_effect=fake):
            engine, _hits = web_find.search_query("тарифы")
        self.assertEqual(engine, "duckduckgo-post")
        self.assertEqual(seen[0][0], "https://ddg.test/html/")
        self.assertIn(b"q=", seen[0][1])

    def test_captcha_page_then_site_links(self) -> None:
        os.environ["STRAT_SEARCH_BASES"] = "bing|https://bing.test/search"
        home = (
            '<a href="/tariffs/semya/">Тариф Семья</a><a href="/about/">О компании</a>'
            '<a href="https://other.test/semya">чужой</a>'
        )

        def fake(url, data=None):
            if "bing.test" in url:
                return 200, "<html>Вы не робот? captcha</html>"
            if url == "https://shop.example.ru/":
                return 200, home
            return 404, ""

        with mock.patch.object(web_find, "http_get", side_effect=fake):
            engine, hits = web_find.search_query("site:shop.example.ru семья тариф")
        self.assertIn("shop.example.ru", engine)
        urls = [hit["url"] for hit in hits]
        self.assertEqual(urls, ["https://shop.example.ru/tariffs/semya/"])

    def test_no_host_no_guessing(self) -> None:
        os.environ["STRAT_SEARCH_BASES"] = "bing|https://bing.test/search"
        with mock.patch.object(web_find, "http_get", side_effect=lambda url, data=None: (202, "")):
            with self.assertRaises(web_find.WebError) as raised:
                web_find.search_query("семейный тариф")
        self.assertIn("HTTP 202", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
