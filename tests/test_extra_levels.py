"""Routing self-correction, links graph, stem search, media intake and offline UX inspect.

External tools (API, search engine, ffmpeg, whisper) are mocked: the tests
check behaviour with and without them, and that a missing tool never crashes.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kb  # noqa: E402
import kb_graph  # noqa: E402
import media  # noqa: E402
import routing  # noqa: E402
import strat  # noqa: E402
import web_find  # noqa: E402


def run(func, namespace: Namespace) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = func(namespace) or 0
        except SystemExit as exc:
            code = int(exc.code or 1) if not isinstance(exc.code, str) else 1
    return code, out.getvalue()


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.get(k) for k in ("STRAT_PROJECTS_DIR", "STRAT_LLM_API_KEY", "STRAT_MODEL_CHEAP", "STRAT_MODEL_STRONG")}
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        self.saved.setdefault("STRAT_CURSOR_ROUTE", os.environ.get("STRAT_CURSOR_ROUTE"))
        os.environ["STRAT_CURSOR_ROUTE"] = "0"
        run(strat.cmd_init, Namespace(project_id="lv", title="Уровни", title_file=None))
        self.project = Path(self.tmp.name) / "lv"

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def state(self) -> dict:
        return json.loads((self.project / "state.json").read_text(encoding="utf-8"))

    def log_md(self) -> str:
        return (self.project / "log.md").read_text(encoding="utf-8")


class RoutingLogicTests(unittest.TestCase):
    def test_defects(self) -> None:
        self.assertIsNone(routing.completion_defect("manual", ""))
        self.assertIn("ошибкой", routing.completion_defect("error", ""))
        self.assertIn("пустой", routing.completion_defect("api", "  "))
        self.assertIn("TODO", routing.completion_defect("api", "Раздел про рынок: TODO дописать"))
        self.assertIn("заглушка", routing.completion_defect("api", "Выручка составит <вставить число> млн"))
        self.assertIsNone(routing.completion_defect("api", "черновик выжимки"))
        self.assertIn("0 результатов", routing.search_defect(0, None))
        self.assertIsNone(routing.search_defect(3, None))
        self.assertEqual(routing.escalation_target("tier", "cheap"), "strong")
        self.assertIsNone(routing.escalation_target("tier", "strong"))

    def test_simplify_query(self) -> None:
        simple = routing.simplify_query('"семейный тариф" site:example.ru AND (дети OR подростки)')
        self.assertNotIn("site:", simple)
        self.assertNotIn('"', simple)
        self.assertIn("семейный", simple)
        self.assertEqual(routing.simplify_query("тарифы"), "тарифы")


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.body = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def complete_ns(project: str, tier: str = "cheap") -> Namespace:
    return Namespace(prompt="Сделай выжимку", prompt_file=None, tier=tier, role=None, project=project, stage=None, no_escalate=False)


class RoutingCommandTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ.update({"STRAT_LLM_API_KEY": "k-test", "STRAT_MODEL_CHEAP": "m-cheap", "STRAT_MODEL_STRONG": "m-strong"})

    def test_cheap_defect_escalates_once_and_is_logged(self) -> None:
        answers = iter([FakeResponse(""), FakeResponse("Нормальная выжимка по трём источникам.")])
        models = []

        def fake(request, timeout=5):
            models.append(json.loads(request.data.decode())["model"])
            return next(answers)

        with mock.patch.object(strat.urllib.request, "urlopen", side_effect=fake):
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(code, 0, out)
        self.assertEqual(models, ["m-cheap", "m-strong"])
        self.assertIn("ROUTING", out)
        rows = self.state()["routing_escalations"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["from"], rows[0]["to"]), ("cheap", "strong"))
        self.assertIn("пустой ответ", rows[0]["reason"])
        self.assertIn("Маршрутизация", self.log_md())

        # Same step again: no second escalation, no silent retry.
        with mock.patch.object(strat.urllib.request, "urlopen", return_value=FakeResponse("")) as called:
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(called.call_count, 1)
        self.assertIn("уже эскалировался", out)

    def test_good_answer_and_no_escalate(self) -> None:
        with mock.patch.object(strat.urllib.request, "urlopen", return_value=FakeResponse("Хороший ответ без меток.")) as called:
            run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(called.call_count, 1)
        ns = complete_ns("lv")
        ns.no_escalate = True
        with mock.patch.object(strat.urllib.request, "urlopen", return_value=FakeResponse("")) as called:
            run(strat.cmd_complete, ns)
        self.assertEqual(called.call_count, 1)
        self.assertNotIn("routing_escalations", self.state())

    def test_strong_without_model_stays_failure(self) -> None:
        os.environ.pop("STRAT_MODEL_STRONG")
        with mock.patch.object(strat.urllib.request, "urlopen", side_effect=OSError("down")):
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(code, 1)
        self.assertIn("MODE: manual", out)
        self.assertNotIn("k-test", out)

    def test_reroute_records_once(self) -> None:
        ns = Namespace(project="lv", step="stage5-options", defect="check: 3 ошибки", from_tier="cheap", tool=None)
        code, out = run(strat.cmd_reroute, ns)
        self.assertEqual(code, 0)
        self.assertIn("cheap -> strong", out)
        code, out = run(strat.cmd_reroute, ns)
        self.assertEqual(code, 2)
        tool = Namespace(project="lv", step="kb-1", defect="ничего не найдено", from_tier=None, tool="kb-search")
        code, out = run(strat.cmd_reroute, tool)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.state()["routing_escalations"]), 2)

    def test_search_empty_retries_simplified_query(self) -> None:
        queries = []

        def fake(query):
            queries.append(query)
            if len(queries) == 1:
                raise web_find.WebError("ни один движок не ответил")
            return "duckduckgo", [{"title": "Тариф", "url": "https://example.org/t", "snippet": ""}]

        with mock.patch.object(web_find, "search_query", side_effect=fake), mock.patch.object(web_find, "format_search", return_value="hits"):
            code, out = run(strat.cmd_search, Namespace(rest=["lv", '"семейный тариф" site:example.org'], query_file=None, no_escalate=False))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(queries), 2)
        self.assertNotIn("site:", queries[1])
        self.assertEqual(self.state()["routing_escalations"][0]["kind"], "tool")

    def test_search_fails_twice_reports_failure(self) -> None:
        with mock.patch.object(web_find, "search_query", side_effect=web_find.WebError("нет сети")):
            code, out = run(strat.cmd_search, Namespace(rest=["lv", '"тариф" site:x.org'], query_file=None, no_escalate=False))
        self.assertEqual(code, 1)
        self.assertIn("запасной путь", out)


class GraphTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        p = self.project
        (p / "sources.jsonl").write_text(json.dumps({"id": "S001", "title": "Отчёт"}, ensure_ascii=False) + "\n", encoding="utf-8")
        rows = [
            {"id": "E001", "claim": "Доля семей 40%", "source": "S001", "status": "active", "depends_on": []},
            {"id": "E002", "claim": "Рост подписок", "source": "S001", "status": "retracted", "depends_on": ["E001"]},
        ]
        (p / "evidence.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (p / "assumptions.jsonl").write_text(
            json.dumps({"id": "U005", "name": "конверсия", "value": "5", "unit": "%", "derivation": "из [E001] и E002"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (p / "options.md").write_text("# Варианты\n\n### O1. База\nОпора [E001].\n\n### O2. Семья\nОпора [U005].\n", encoding="utf-8")
        (p / "strategy.md").write_text("# Стратегия\n\nРекомендуем O2: прирост [U005].\n", encoding="utf-8")

    def test_up_and_down(self) -> None:
        up = kb_graph.trace(self.project, "O2", "up")
        nodes = [row["node"] for row in up["up"]]
        self.assertEqual(nodes[:1], ["U005"])
        self.assertIn("E001", nodes)
        self.assertIn("S001", nodes)
        down = kb_graph.trace(self.project, "U005", "down")
        down_nodes = [row["node"] for row in down["down"]]
        self.assertIn("O2", down_nodes)
        self.assertIn("strategy.md:3", down_nodes)
        text = kb_graph.format_trace(kb_graph.trace(self.project, "U005", "up"))
        self.assertIn("E002", text)
        self.assertIn("отозваны", text)

    def test_cli_unknown_and_bad_node(self) -> None:
        code, out = run(strat.cmd_trace, Namespace(rest=["lv", "E999"], direction="both", depth=6, json=False))
        self.assertEqual(code, 0)
        self.assertIn("не найден", out)
        code, _out = run(strat.cmd_trace, Namespace(rest=["lv", "rm -rf"], direction="both", depth=6, json=False))
        self.assertNotEqual(code, 0)

    def test_empty_project_does_not_crash(self) -> None:
        for name in ("evidence.jsonl", "sources.jsonl", "assumptions.jsonl", "options.md", "strategy.md"):
            (self.project / name).unlink(missing_ok=True)
        (self.project / "evidence.jsonl").write_text("{broken\n", encoding="utf-8")
        self.assertFalse(kb_graph.trace(self.project, "O2")["known"])


class StemSearchTests(unittest.TestCase):
    def chunks(self) -> list[dict]:
        return [
            {"id": "K001#1", "doc_id": "K001", "text": "Абоненты жалуются на стоимость семейных тарифов."},
            {"id": "K002#1", "doc_id": "K002", "text": "Погода в Москве."},
        ]

    def test_synonyms_and_stems(self) -> None:
        hits = kb._fuzzy(self.chunks(), "цена тарифа для клиентов", 5)
        self.assertEqual([h[1]["doc_id"] for h in hits], ["K001"])
        self.assertEqual(kb.stem("тарифов"), kb.stem("тарифы"))

    def test_search_falls_back_without_package(self) -> None:
        payload = {"chunks": self.chunks()}
        with mock.patch.object(kb, "load_index", return_value=payload), mock.patch.object(kb, "default_embedder", return_value=None):
            text = kb.search(Path("."), "цена для клиентов", semantic=True)
        self.assertIn("Метод: stems", text)
        self.assertIn("K001", text)

    def test_broken_embedder_degrades(self) -> None:
        payload = {"chunks": self.chunks()}

        def bad(_texts):
            raise RuntimeError("no model")

        with mock.patch.object(kb, "load_index", return_value=payload):
            text = kb.search(Path("."), "стоимость", semantic=True, embedder=bad)
        self.assertIn("Метод: stems", text)


SRT = "1\n00:00:05,000 --> 00:00:09,000\nМне страшно отпускать ребёнка без связи.\n\n2\n00:01:10,000 --> 00:01:15,500\nТариф дорогой, я не понимаю, за что плачу.\n"


class MediaTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        (self.project / "sources").mkdir(exist_ok=True)

    def add(self, name: str, **extra) -> tuple[int, str]:
        ns = Namespace(rest=["lv", name], transcript=None, no_auto=False, evidence=0, synthetic=False)
        for key, value in extra.items():
            setattr(ns, key, value)
        return run(strat.cmd_media_add, ns)

    def test_parse_segments_forms(self) -> None:
        segs = media.parse_segments(SRT)
        self.assertEqual(segs[1]["start"], 70.0)
        self.assertEqual(media.timecode(segs[1]["start"]), "00:01:10")
        inline = media.parse_segments("[01:02] Первая реплика\n[1:05:00] Вторая")
        self.assertEqual(inline[1]["start"], 3900.0)
        plain = media.parse_segments("Абзац один.\n\nАбзац два.")
        self.assertIsNone(plain[0]["start"])

    def test_sidecar_transcript_becomes_evidence_with_timecodes(self) -> None:
        (self.project / "sources" / "call.mp3").write_bytes(b"ID3")
        (self.project / "sources" / "call.srt").write_text(SRT, encoding="utf-8")
        with mock.patch.object(media, "recognizer_available", return_value=None):
            code, out = self.add("sources/call.mp3", evidence=2)
        self.assertEqual(code, 0, out)
        rows = [json.loads(l) for l in (self.project / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows), 2)
        self.assertIn("00:01:10", rows[1]["locator"])
        self.assertIn("human", rows[1]["locator"])
        meta = json.loads((self.project / "inspect" / "M001" / "media.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["transcript_origin"], "human")
        code, out = run(strat.cmd_check, Namespace(rest=["lv"], strict=False, project=None))
        self.assertNotIn("цитата не найдена", out.casefold())

    def test_auto_transcript_marked_low_confidence(self) -> None:
        (self.project / "sources" / "talk.wav").write_bytes(b"RIFF")
        fake = mock.Mock(return_value=[{"start": 1.0, "end": 3.0, "text": "Семейный тариф слишком сложный для меня."}])
        with mock.patch.object(media, "recognizer_available", return_value="faster_whisper"), mock.patch.object(media, "transcribe", fake):
            code, out = self.add("sources/talk.wav", evidence=1)
        self.assertEqual(code, 0, out)
        row = json.loads((self.project / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["confidence"], "low")
        self.assertIn("auto-transcript", row["locator"])
        self.assertIn("не проверена человеком", row["claim"])

    def test_recognizer_crash_degrades_to_pending(self) -> None:
        (self.project / "sources" / "talk.wav").write_bytes(b"RIFF")
        with mock.patch.object(media, "recognizer_available", return_value="whisper"), mock.patch.object(
            media, "transcribe", side_effect=media.MediaError("модель не скачалась")
        ):
            code, out = self.add("sources/talk.wav", evidence=3)
        self.assertEqual(code, 0, out)
        self.assertIn("ждёт расшифровки", out)
        self.assertFalse((self.project / "evidence.jsonl").read_text(encoding="utf-8").strip())

    def test_video_without_ffmpeg_and_with_ffmpeg(self) -> None:
        video = self.project / "sources" / "ad.mp4"
        video.write_bytes(b"\x00\x00")
        with mock.patch.object(media.shutil, "which", return_value=None), mock.patch.object(media, "recognizer_available", return_value=None):
            code, out = self.add("sources/ad.mp4")
        self.assertEqual(code, 0, out)
        self.assertIn("ffmpeg не найден", out)

        def fake_run(argv, **_kw):
            target = Path(argv[-1])
            if target.suffix == ".wav":
                target.write_bytes(b"RIFF")
            else:
                (target.parent / "frame-001.png").write_bytes(b"png")
            return mock.Mock(returncode=0)

        info = media.prepare(
            self.project, video, which=lambda _n: "/usr/bin/ffmpeg", run=fake_run,
            find_spec=lambda _n: object(), recognize=lambda _a, _b: [{"start": 0.0, "end": 2.0, "text": "Подключи семью"}],
        )
        self.assertEqual(info["origin"], "auto-transcript")
        self.assertEqual(len(info["frames"]), 1)
        self.assertTrue(info["transcript"].is_file())

    def test_bad_suffix_and_outside_path(self) -> None:
        (self.project / "sources" / "x.exe").write_bytes(b"MZ")
        code, _ = self.add("sources/x.exe")
        self.assertNotEqual(code, 0)
        code, _ = self.add("../../etc/passwd")
        self.assertNotEqual(code, 0)


class UxInspectOfflineTests(ProjectCase):
    def test_saved_html_inspects_without_network(self) -> None:
        page = self.project / "sources" / "competitor.html"
        page.parent.mkdir(exist_ok=True)
        page.write_text(
            "<html><body><h1>Семейный тариф</h1><p class='price'>590 ₽/мес</p><button>Подключить</button></body></html>",
            encoding="utf-8",
        )
        ns = Namespace(rest=["lv"], html="sources/competitor.html", image=None, pdf=None, av=None, transcript=None,
                       description=None, description_file=None, synthetic=False, force=False, claim=None, claim_file=None,
                       quote=None, quote_file=None, etype="fact", confidence="med")
        with mock.patch.object(strat.urllib.request, "urlopen", side_effect=AssertionError("сеть не нужна")):
            code, out = run(strat.cmd_inspect, ns)
        self.assertEqual(code, 0, out)
        self.assertIn("Подключить", out)

    def test_skills_require_inspect_of_pages(self) -> None:
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "skills").rglob("*.md"))
        self.assertIn("inspect --html", text)
        self.assertIn("видимый элемент", text.casefold())


class McpExtraToolsTests(ProjectCase):
    def test_tools_registered_and_callable(self) -> None:
        import mcp_server

        for name in ("strat_trace", "strat_reroute", "strat_media_add"):
            self.assertIn(name, mcp_server.TOOLS_BY_NAME)
        result = mcp_server.handle_trace({"project": "lv", "node": "S001"})
        self.assertIn("content", result)


class CaseCompareTests(ProjectCase):
    def test_missing_numbers_stay_dash_and_rubles_need_price(self) -> None:
        import case_compare

        chat = Path(self.tmp.name) / "chat.json"
        chat.write_text(json.dumps({"seconds": 120, "input_tokens": 1000000, "output_tokens": 500000, "missed": ["нет снимков"]}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = case_compare.main([str(self.project), "--chat", str(chat)])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("| обычный чат | 2 | 1000000 | 500000 | — |", text)
        self.assertIn("цена за миллион токенов не задана", text)
        self.assertIn("аналитик", text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            case_compare.main([str(self.project), "--chat", str(chat), "--rub-per-1m-in", "100", "--rub-per-1m-out", "300"])
        self.assertIn("| 250 |", out.getvalue())
        self.assertIn("допущение", out.getvalue())

    def test_bench_subcommand(self) -> None:
        import bench

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bench.main(["case-cost", str(self.project)])
        self.assertEqual(code, 0)
        self.assertIn("замера нет", out.getvalue())


def stream(model: str, text: str, inp: int = 120, out: int = 8, ms: int = 2500) -> str:
    events = [
        {"type": "system", "subtype": "init", "model": model},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success", "result": text, "duration_ms": ms, "is_error": False,
         "usage": {"inputTokens": inp, "outputTokens": out}},
    ]
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


class CursorRouteTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        for key in ("STRAT_LLM_API_KEY", "OPENROUTER_API_KEY"):
            os.environ.pop(key, None)
        os.environ["STRAT_CURSOR_ROUTE"] = "1"
        import cursor_route

        self.cr = cursor_route

    def fake_agent(self, answers: list[str]):
        calls = []

        def runner(argv, folder, env, timeout):
            calls.append(argv)
            return 0, answers[len(calls) - 1], "", 2.0

        return calls, runner

    def test_parse_stream(self) -> None:
        parsed = self.cr.parse_stream("мусор\n" + stream("Composer 2.5 Fast", "Ответ"))
        self.assertEqual(parsed["model"], "Composer 2.5 Fast")
        self.assertEqual(parsed["content"], "Ответ")
        self.assertEqual((parsed["input_tokens"], parsed["output_tokens"], parsed["duration_ms"]), (120, 8, 2500))

    def test_no_key_uses_cursor_agent_and_escalates_on_defect(self) -> None:
        calls, runner = self.fake_agent([stream("composer-2.5-fast", "TODO"), stream("grok-4.7-high", "Нормальный ответ по делу.")])
        real_call = self.cr.call
        with mock.patch.object(self.cr, "enabled", return_value=True), mock.patch.object(
            self.cr.classify, "cursor_launch", return_value=(["agent"], {})
        ), mock.patch.object(self.cr, "call", side_effect=lambda p, t: real_call(p, t, runner=runner, workspace=Path(self.tmp.name) / "ws")):
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(code, 0, out)
        self.assertIn("MODE: cursor-agent", out)
        self.assertEqual(len(calls), 2)
        self.assertIn("composer-2.5-fast", calls[0])
        self.assertIn(self.cr.model_for("strong"), calls[1])
        self.assertTrue(self.cr.model_for("strong"))
        rows = [json.loads(l) for l in (self.project / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        models = [(r.get("tier"), r.get("model")) for r in rows if r.get("tier")]
        self.assertIn(("cheap", "composer-2.5-fast"), models)
        self.assertIn(("strong", "grok-4.7-high"), models)
        self.assertIn("маркер низкого качества", self.state()["routing_escalations"][0]["reason"])
        entries = strat.load_log(self.project)
        split = "\n".join(strat.format_model_split(entries))
        self.assertIn("cheap / composer-2.5-fast: вызовов 1", split)
        self.assertIn("strong / grok-4.7-high", split)

    def test_agent_missing_keeps_manual(self) -> None:
        with mock.patch.object(self.cr.classify, "agent_spec", return_value=None), mock.patch.object(
            self.cr.shutil, "which", return_value=None
        ):
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(code, 0)
        self.assertIn("MODE: manual", out)

    def test_launch_failure_does_not_crash(self) -> None:
        with mock.patch.object(self.cr.classify, "cursor_launch", side_effect=RuntimeError("нет agent")):
            outcome = self.cr.call("ping", "cheap")
        self.assertFalse(outcome["ok"])
        self.assertIn("не запустился", outcome["error"])


class OtherBackendTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        for key in ("STRAT_LLM_API_KEY", "OPENROUTER_API_KEY", "STRAT_CLAUDE_CMD", "STRAT_CODEX_CMD"):
            os.environ.pop(key, None)
        os.environ["STRAT_CURSOR_ROUTE"] = "1"
        import cursor_route

        self.cr = cursor_route

    def test_order_cursor_then_claude_then_codex(self) -> None:
        which = lambda name: f"/bin/{name}" if name in {"claude", "codex"} else None  # noqa: E731
        with mock.patch.object(self.cr, "enabled", return_value=True):
            self.assertEqual(self.cr.pick_backend(which), "cursor-agent")
        with mock.patch.object(self.cr, "enabled", return_value=False):
            self.assertEqual(self.cr.pick_backend(which), "claude")
            self.assertEqual(self.cr.pick_backend(lambda n: "/bin/codex" if n == "codex" else None), "codex")
            self.assertIsNone(self.cr.pick_backend(lambda n: None))
        os.environ["STRAT_CURSOR_ROUTE"] = "0"
        self.assertIsNone(self.cr.pick_backend(which))

    def test_claude_call_parsed(self) -> None:
        seen = []

        def runner(argv, cwd, timeout):
            seen.append(argv)
            payload = {"type": "result", "result": "Рим", "is_error": False, "duration_ms": 1800,
                       "usage": {"input_tokens": 40, "output_tokens": 2}, "modelUsage": {"claude-opus-x": {}}}
            return 0, json.dumps(payload), "", 1.9

        out = self.cr.call_other("claude", "Столица Италии?", "strong", runner=runner, which=lambda n: "/bin/claude")
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["content"], out["model"], out["seconds"]), ("Рим", "claude-opus-x", 1.8))
        self.assertIn("--model", seen[0])
        self.assertIn("opus", seen[0])

    def test_codex_call_reads_last_message(self) -> None:
        def runner(argv, cwd, timeout):
            last = Path(argv[argv.index("-o") + 1])
            last.write_text("Париж", encoding="utf-8")
            events = [{"type": "thread.started"}, {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 3}}]
            return 0, "\n".join(json.dumps(e) for e in events), "", 2.0

        out = self.cr.call_other("codex", "Столица Франции?", "cheap", runner=runner, which=lambda n: "/bin/codex")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["content"], "Париж")
        self.assertEqual(out["input_tokens"], 30)
        self.assertEqual(out["model"], "codex/default")

    def test_failures_degrade(self) -> None:
        def boom(argv, cwd, timeout):
            raise TimeoutError("долго")

        out = self.cr.call_other("claude", "x", "cheap", runner=boom, which=lambda n: "/bin/claude")
        self.assertFalse(out["ok"])
        out = self.cr.call_other("codex", "x", "cheap", runner=lambda a, c, t: (0, "", "", 0.1), which=lambda n: "/bin/codex")
        self.assertFalse(out["ok"])

    def test_complete_uses_claude_when_cursor_absent(self) -> None:
        fake = {"ok": True, "error": None, "requested": "haiku", "model": "claude-haiku", "seconds": 1.0,
                "content": "Ответ без меток по делу.", "input_tokens": 10, "output_tokens": 3}
        with mock.patch.object(self.cr, "pick_backend", return_value="claude"), mock.patch.object(self.cr, "call_other", return_value=fake):
            code, out = run(strat.cmd_complete, complete_ns("lv"))
        self.assertEqual(code, 0, out)
        self.assertIn("MODE: claude", out)
        rows = [json.loads(l) for l in (self.project / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertIn(("cheap", "claude-haiku"), [(r.get("tier"), r.get("model")) for r in rows])


if __name__ == "__main__":
    unittest.main()
