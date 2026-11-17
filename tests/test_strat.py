"""CLI tests for the factory strategist. Standard-library unittest only."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRAT = ROOT / "scripts" / "strat.py"
BROKEN = ROOT / "tests" / "fixtures" / "broken_project"


def run(
    *args: str,
    env: dict | None = None,
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in (
        "STRAT_LLM_API_KEY",
        "OPENROUTER_API_KEY",
        "STRAT_PROJECTS_DIR",
        "STRAT_MODEL_CHEAP",
        "STRAT_MODEL_STRONG",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    ):
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(STRAT), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
        input=input,
        timeout=30,
    )


def assert_code(result: subprocess.CompletedProcess[str], code: int = 0) -> None:
    if result.returncode != code:
        raise AssertionError(
            f"ожидался код {code}, получен {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def stop_http_server(server, thread) -> None:
    """shutdown, then join, then close the socket.

    Closing the socket while serve_forever is inside select() prints
    OSError WinError 10038 from the background thread on Windows.
    """
    server.shutdown()
    thread.join()
    server.server_close()


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF whose text layer is the given Latin string."""
    stream = f"BT /F1 24 Tf 40 80 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n",
        b"4 0 obj<</Length " + str(len(stream)).encode("ascii") + b">>stream\n" + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
    ]
    body = b"%PDF-1.4\n" + b"".join(objects)
    xref_at = len(body)
    offsets = []
    cursor = len(b"%PDF-1.4\n")
    for obj in objects:
        offsets.append(cursor)
        cursor += len(obj)
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer<</Size 6/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return body + b"".join(xref) + trailer


class ExampleAndHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name)
        shutil.copytree(ROOT / "projects" / "_example", self.projects / "_example")
        self.env = {"STRAT_PROJECTS_DIR": str(self.projects)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(
        self, *args: str, env: dict | None = None, input: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run(*args, env=merged, input=input)

    def test_help(self) -> None:
        result = self.cli("--help")
        assert_code(result)
        self.assertIn("init", result.stdout)
        self.assertIn("check", result.stdout)

    def test_example_check_passes(self) -> None:
        result = self.cli("check", "projects/_example")
        assert_code(result)
        self.assertIn("OK", result.stdout)

    def test_example_cost_and_resume(self) -> None:
        cost = self.cli("cost", "projects/_example")
        assert_code(cost)
        self.assertIn("токены 7000", cost.stdout)
        self.assertIn("стоимость 0.02", cost.stdout)
        self.assertIn("минуты 90", cost.stdout)
        resume = self.cli("resume", "projects/_example")
        assert_code(resume)
        self.assertIn("сопровождение", resume.stdout)
        self.assertIn("O2", resume.stdout)
        status = self.cli("status", "projects/_example")
        assert_code(status)
        self.assertIn("Проверка: OK", status.stdout)

    def test_example_impact_is_transitive(self) -> None:
        result = self.cli("impact", "projects/_example", "E003")
        assert_code(result)
        self.assertIn("E005", result.stdout)
        self.assertIn("через depends_on", result.stdout)
        self.assertIn("situation.md", result.stdout)

    def test_no_default_project_when_only_example_exists(self) -> None:
        result = self.cli("status")
        assert_code(result, 1)
        self.assertIn("Нет проекта", result.stdout)

    def test_stale_current_is_a_hint(self) -> None:
        (self.projects / ".current").write_text("smoke-local\n", encoding="utf-8")
        status = self.cli("status")
        assert_code(status, 1)
        self.assertIn("Нет проекта", status.stdout)
        self.assertIn("smoke-local", status.stdout)
        self.assertIn("Подсказка", status.stdout)
        checked = self.cli("check", "projects/_example")
        assert_code(checked)
        self.assertIn("OK", checked.stdout)
        doctor = self.cli("doctor")
        assert_code(doctor)
        self.assertIn("smoke-local", doctor.stdout)

    def test_corrupted_title_is_refused(self) -> None:
        broken = self.cli("init", "broken-title", "--title", "???? ?????? ? ????????? ??????????")
        assert_code(broken, 1)
        self.assertIn("испорчен", broken.stdout)
        self.assertIn("chcp 65001", broken.stdout)
        self.assertFalse((self.projects / "broken-title").exists())
        replaced = self.cli("init", "replaced-title", "--title", "Тариф\ufffd")
        assert_code(replaced, 1)
        self.assertIn("U+FFFD", replaced.stdout)
        self.assertFalse((self.projects / "replaced-title").exists())

    def test_title_file_keeps_cyrillic(self) -> None:
        title_path = self.projects / "title.txt"
        title_path.write_text("Семейные тарифы в мобильном приложении\n", encoding="utf-8")
        created = self.cli("init", "family", "--title-file", str(title_path))
        assert_code(created)
        state = json.loads((self.projects / "family" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["title"], "Семейные тарифы в мобильном приложении")
        via_stdin = self.cli("init", "family-stdin", "--title", "-", input="Семейные тарифы\n")
        assert_code(via_stdin)
        state_stdin = json.loads((self.projects / "family-stdin" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state_stdin["title"], "Семейные тарифы")
        via_env = self.cli("init", "family-env", env={"STRAT_TITLE": "Семейные тарифы"})
        assert_code(via_env)
        state_env = json.loads((self.projects / "family-env" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state_env["title"], "Семейные тарифы")

    def test_log_grounds_file_and_rejects_question_marks(self) -> None:
        assert_code(self.cli("init", "journ", "--title", "Latin title"))
        project = self.projects / "journ"
        bad = self.cli("log", "journ", "1", "Step", "--grounds", "???")
        assert_code(bad, 1)
        self.assertIn("испорчен", bad.stdout)
        log_text = (project / "log.jsonl").read_text(encoding="utf-8")
        self.assertNotIn('"grounds":"???"', log_text)
        grounds_path = self.projects / "grounds.txt"
        grounds_path.write_text("Основание на русском\n", encoding="utf-8")
        message_path = self.projects / "action.txt"
        message_path.write_text("Зафиксирован бриф\n", encoding="utf-8")
        ok = self.cli(
            "log",
            "journ",
            "1",
            "--message-file",
            str(message_path),
            "--grounds-file",
            str(grounds_path),
        )
        assert_code(ok)
        markdown = (project / "log.md").read_text(encoding="utf-8")
        payload = (project / "log.jsonl").read_text(encoding="utf-8")
        self.assertIn("Зафиксирован бриф", markdown)
        self.assertIn("Основание на русском", markdown)
        self.assertIn("Зафиксирован бриф", payload)
        self.assertIn("Основание на русском", payload)

    def test_broken_fixture_fails_with_expected_codes(self) -> None:
        result = self.cli("check", str(BROKEN))
        assert_code(result, 1)
        for code in ("facts", "type", "retracted", "ref", "options", "promise", "source", "source-missing"):
            self.assertIn(f"[{code}]", result.stdout)

    def test_doctor_and_manual_mode(self) -> None:
        doctor = self.cli("doctor")
        assert_code(doctor)
        self.assertIn("ручной режим", doctor.stdout)
        self.assertIn("MCP: сервер ответил на initialize", doctor.stdout)
        self.assertIn("utf-8", doctor.stdout.casefold())
        self.assertIn("Маршрутизация", doctor.stdout)
        self.assertIn("extract: cheap", doctor.stdout)
        self.assertIn("options: strong", doctor.stdout)
        self.assertIn("critic: strong", doctor.stdout)
        complete = self.cli("complete", "--role", "cheap", "--prompt", "ping")
        assert_code(complete)
        self.assertIn("MODE: manual", complete.stdout)

    def test_api_error_does_not_leak_the_key(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"nope")

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(stop_http_server, server, thread)
        secret = "secret-test-key-should-not-leak"
        result = self.cli(
            "complete",
            "--role",
            "cheap",
            "--prompt",
            "ping",
            env={
                "STRAT_LLM_API_KEY": secret,
                "STRAT_MODEL_CHEAP": "dummy",
                "STRAT_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
            },
        )
        assert_code(result, 1)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("MODE: error", result.stdout)

    def test_read_material_explains_missing_pdf_package(self) -> None:
        blocker = self.projects / "blockers"
        blocker.mkdir()
        (blocker / "pypdf.py").write_text("raise ImportError('simulated missing pypdf')\n", encoding="utf-8")
        env = os.environ.copy()
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp1251"
        prior = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(blocker) if not prior else str(blocker) + os.pathsep + prior
        pdf = self.projects / "empty.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "read_material.py"), str(pdf)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pypdf", result.stdout)
        self.assertIn("нужен", result.stdout)

    def test_read_material_prints_text_when_pypdf_is_installed(self) -> None:
        if importlib.util.find_spec("pypdf") is None:
            self.skipTest("pypdf не установлен: ветка отсутствия пакета проверяется отдельно")
        pdf = self.projects / "hello.pdf"
        pdf.write_bytes(minimal_pdf("Hello strat"))
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "read_material.py"), str(pdf)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), result.stderr)
        self.assertTrue(
            "Hello strat" in result.stdout or "текстовый слой" in result.stdout,
            result.stdout,
        )

    def test_hooks_do_not_block_without_a_project(self) -> None:
        env = os.environ.copy()
        env["STRAT_PROJECTS_DIR"] = str(self.projects)
        for name in ("post_write.py", "stage_end.py"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "hooks" / name)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertIn("advance", result.stderr)
        self.assertNotIn("followup_message", result.stdout)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"STRAT_PROJECTS_DIR": self.tmp.name}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(
        self, *args: str, env: dict | None = None, input: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run(*args, env=merged, input=input)

    def test_init_source_evidence_and_gates(self) -> None:
        created = self.cli("init", "happy", "--title", "Проверка ворот")
        assert_code(created)
        self.assertIn("id: happy", created.stdout)
        assert_code(self.cli("check"), 0)

        blocked = self.cli("advance", "1")
        assert_code(blocked, 1)
        self.assertIn("журнале", blocked.stdout)

        project = Path(self.tmp.name) / "happy"
        (project / "brief.md").write_text("# Бриф\n\nЛюди с подпиской решают, оставлять ли её.\n", encoding="utf-8")
        assert_code(self.cli("log", "happy", "1", "Brief drafted", "--grounds", "ready for confirm"))
        still = self.cli("advance", "happy", "1")
        assert_code(still, 1)
        self.assertIn("не подтверждена", still.stdout)
        assert_code(self.cli("confirm", "happy", "brief", "--note", "согласовано"))
        assert_code(self.cli("advance", "happy", "1"))

        skip = self.cli("advance", "happy", "3")
        assert_code(skip, 1)
        self.assertIn("открыт этап 2", skip.stdout)

        questions = """# Вопросы

## Уточнения к брифу

Нет.

## Вопросы, меняющие решение

### Q1. Если падение доли уже считается проблемой?

- если да: сравниваем исследование и закрытие
- если нет: ближе статус-кво
- источник и метод: ответ заказчика

## Открытые вопросы
"""
        (project / "questions.md").write_text(questions, encoding="utf-8")
        assert_code(self.cli("log", "happy", "2", "Research questions", "--grounds", "Q1 changes the choice"))
        assert_code(self.cli("advance", "happy", "2"))

        no_sources = self.cli("advance", "happy", "3")
        assert_code(no_sources, 1)
        note = project / "sources" / "note.md"
        note.write_text("Доля продлений в учебной заметке равна 42 процентам.\n", encoding="utf-8")
        first = self.cli("add-source", "happy", "sources/note.md", "--kind", "own", "--title", "Заметка")
        assert_code(first)
        self.assertIn("id: S001", first.stdout)
        second = self.cli("add-source", "happy", "sources/note.md", "--kind", "own")
        assert_code(second)
        self.assertIn("Уже зарегистрирован", second.stdout)
        rows = [json.loads(line) for line in (project / "sources.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["sha256"]), 64)

        bad_type = self.cli(
            "add-evidence",
            "happy",
            "--claim",
            "вымысел",
            "--type",
            "synthetic",
            "--source",
            "S001",
            "--quote",
            "нет",
        )
        assert_code(bad_type, 1)
        evidence = self.cli(
            "add-evidence",
            "happy",
            "--claim",
            "В заметке сказано, что доля равна 42 процентам.",
            "--type",
            "fact",
            "--source",
            "S001",
            "--quote",
            "Доля продлений в учебной заметке равна 42 процентам.",
            "--locator",
            "note.md",
            "--confidence",
            "high",
        )
        assert_code(evidence)
        self.assertIn("id: E001", evidence.stdout)
        assert_code(self.cli("log", "happy", "3", "Source and fact recorded", "--grounds", "S001 E001"))
        assert_code(self.cli("advance", "happy", "3"))

        (project / "situation.md").write_text(
            "# Ситуация\n\n## Факты\n\nЛюди уходят, это точно известно всем.\n\n## Противоречия\n\nНет.\n\n## Неизвестно\n\n## Гипотезы\n\n## Прогнозы\n",
            encoding="utf-8",
        )
        assert_code(self.cli("log", "happy", "4", "Situation draft", "--grounds", "facts still wrong"))
        bad_facts = self.cli("advance", "happy", "4")
        assert_code(bad_facts, 1)
        self.assertIn("[facts]", bad_facts.stdout)
        (project / "situation.md").write_text(
            "# Ситуация\n\n## Факты\n\nВ файле выгрузки сказано, что доля продлений равна сорока двум процентам [E001].\n\n## Противоречия\n\nПротиворечий между фактами нет.\n\n## Неизвестно\n\nНеизвестно, почему доля снизилась.\n\n## Гипотезы\n\nОтдельной гипотезы нет.\n\n## Прогнозы\n\nПрогнозов нет.\n",
            encoding="utf-8",
        )
        assert_code(self.cli("advance", "happy", "4"))

        (project / "options.md").write_text(
            "# Варианты\n\n"
            "### O1. Оставить как есть\n<!-- role: status-quo -->\n\n"
            "Ничего не менять, пока нет живых интервью. Опора: [E001].\n\n"
            "Возражение: доля уже описана как низкая, и бездействие это не лечит.\n\n"
            "### O2. Уточнить барьер коротким исследованием\n<!-- role: change -->\n\n"
            "Сначала пять интервью, потом решение о продукте.\n\n"
            "Возражение: исследование откладывает решение, если падение продолжится.\n\n"
            "### O3. Закрыть направление\n<!-- role: close -->\n\n"
            "Остановить развитие.\n\n"
            "Возражение: одного упоминания доли [E001] мало, чтобы закрыть направление.\n",
            encoding="utf-8",
        )
        assert_code(self.cli("add-option", "happy", "--title", "Оставить как есть"))
        assert_code(self.cli("add-option", "happy", "--title", "Уточнить барьер коротким исследованием"))
        assert_code(self.cli("add-option", "happy", "--title", "Закрыть направление"))
        assert_code(self.cli("log", "happy", "5", "Three options", "--grounds", "status quo and close included"))
        no_choice = self.cli("advance", "happy", "5")
        assert_code(no_choice, 1)
        assert_code(self.cli("confirm", "happy", "options", "--choice", "O2"))
        assert_code(self.cli("advance", "happy", "5"))

        (project / "strategy.md").write_text(
            "# Стратегия\n\n## Продуктовая стратегия\n\n"
            "### Целевой сегмент и работа (JTBD)\n\n"
            "Аудитория — действующие подписчики, которые решают, оставлять ли продукт. Работа — понять барьер до переделки.\n\n"
            "### Ценностное предложение\n\n"
            "Свойство PP1: протокол из пяти интервью и решение по их итогам, без новой функции в этом цикле.\n\n"
            "### Границы MVP\n\n"
            "В этот цикл входит только протокол интервью и решение. Интерфейс и цену не переписываем.\n\n"
            "### Метрики\n\n"
            "Метрика цикла — есть письменный барьер и решение продолжать или остановить, а не выручка.\n\n"
            "### Риски\n\n"
            "Исследование может затянуться, и решение не будет принято, пока падение доли продолжится.\n\n"
            "### Что мы не делаем\n\n"
            "Направление O2, отказ от немедленной переделки цены и от обещания наружу до интервью.\n\n"
            "## Коммуникационная стратегия\n\n"
            "### Аудитории\n\n"
            "Аудитория сообщения — команда продукта, а не внешние подписчики этого цикла.\n\n"
            "### Ключевые сообщения\n\n"
            "Сообщение команде: сначала назовём барьер, наружу ничего не обещаем до решения.\n\n"
            "### Каналы\n\n"
            "Канал — внутренний разбор протокола, не рекламная кампания и не пуш подписчикам.\n\n"
            "### Сроки и последовательность\n\n"
            "Сначала пять интервью, затем решение. Внешнее обещание не выходит раньше этого решения.\n\n"
            "### Работа с возражениями\n\n"
            "Возражение «исследование откладывает решение» закрывается сроком в плане проверки и явным решением.\n\n"
            "### Как измеряется успех\n\n"
            "Успех коммуникации внутри команды — одно названное барьером решение, а не охват рекламы.\n\n"
            "<!-- promise: P1 -->\n"
            "Команде показываем выводы интервью до любой переделки продукта.\n"
            "<!-- product: PP1 -->\n",
            encoding="utf-8",
        )
        assert_code(self.cli("log", "happy", "6", "Strategy written", "--grounds", "P1 maps to PP1"))
        assert_code(self.cli("advance", "happy", "6"))

        (project / "validation.md").write_text(
            "# Проверка\n\n"
            "- **Ожидаемое изменение:** команда называет один барьер словами пяти интервью и принимает решение\n"
            "- **База сравнения:** сейчас барьер не назван, проведённых интервью нет\n"
            "- **Срок наблюдения:** две недели на интервью и одно решение\n"
            "- **Нежелательные эффекты:** исследование затянется и решение не будет принято в срок\n",
            encoding="utf-8",
        )
        assert_code(self.cli("log", "happy", "7", "Validation plan", "--grounds", "baseline and guardrail"))
        assert_code(self.cli("confirm", "happy", "validation"))
        assert_code(self.cli("advance", "happy", "7"))
        (project / "decisions.md").write_text(
            "# Решения\n\n## D1. Сначала интервью\n\n"
            "- owner: лид продукта\n"
            "- metric: есть письменный барьер и решение продолжать или остановить\n"
            "- review_date: 2026-12-01\n"
            "- revisit_if: протокол не собран к дате пересмотра\n"
            "- evidence: [E001]\n"
            "- option: O2\n"
            "- status: open\n",
            encoding="utf-8",
        )
        assert_code(self.cli("log", "happy", "8", "Decision D1", "--grounds", "review date set"))
        assert_code(self.cli("advance", "happy", "8"))
        status = self.cli("status", "happy")
        assert_code(status)
        self.assertIn("done", status.stdout)

        note.write_text(note.read_text(encoding="utf-8") + "ещё байт\n", encoding="utf-8")
        mismatch = self.cli("check", "happy")
        assert_code(mismatch, 1)
        self.assertIn("[hash]", mismatch.stdout)

    def test_retract_flags_dependent_text(self) -> None:
        shutil.copytree(ROOT / "projects" / "_example", Path(self.tmp.name) / "_example")
        # The example id starts with underscore, so pass the path explicitly.
        target = str(Path(self.tmp.name) / "_example")
        retracted = self.cli("retract", target, "E001", "--reason", "выгрузка оказалась черновиком")
        assert_code(retracted)
        checked = self.cli("check", target)
        assert_code(checked, 1)
        self.assertIn("[retracted]", checked.stdout)
        self.assertIn("E001", checked.stdout)

    def test_cycle_and_direct_jsonl(self) -> None:
        assert_code(self.cli("init", "cycle", "--title", "Цикл"))
        project = Path(self.tmp.name) / "cycle"
        (project / "sources" / "note.md").write_text("Одна фраза для цитаты без выводов.\n", encoding="utf-8")
        assert_code(self.cli("add-source", "cycle", "sources/note.md", "--kind", "own"))
        (project / "evidence.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "E001",
                            "claim": "Первая запись цикла.",
                            "type": "hypothesis",
                            "source": "S001",
                            "quote": "Одна фраза для цитаты без выводов.",
                            "locator": "note.md",
                            "confidence": "low",
                            "date": "2026-11-04",
                            "status": "active",
                            "superseded_by": None,
                            "depends_on": ["E002"],
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "id": "E002",
                            "claim": "Вторая запись цикла.",
                            "type": "hypothesis",
                            "source": "S001",
                            "quote": "Одна фраза для цитаты без выводов.",
                            "locator": "note.md",
                            "confidence": "low",
                            "date": "2026-11-04",
                            "status": "active",
                            "superseded_by": None,
                            "depends_on": ["E001"],
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        checked = self.cli("check", "cycle")
        assert_code(checked, 1)
        self.assertIn("[depends]", checked.stdout)
        impact = self.cli("impact", "cycle", "S001")
        assert_code(impact)
        self.assertIn("E001", impact.stdout)
        self.assertIn("E002", impact.stdout)

    def test_crlf_text_matches_lf_hash(self) -> None:
        dest = Path(self.tmp.name) / "_example"
        shutil.copytree(ROOT / "projects" / "_example", dest)
        converted = 0
        for path in dest.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".jsonl"}:
                data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                path.write_bytes(data)
                converted += 1
        self.assertGreater(converted, 0)
        checked = self.cli("check", str(dest))
        assert_code(checked)
        self.assertIn("OK", checked.stdout)

    def test_current_project_resolution(self) -> None:
        assert_code(self.cli("init", "one", "--title", "Первый"))
        assert_code(self.cli("init", "two", "--title", "Второй"))
        status = self.cli("status")
        assert_code(status)
        self.assertIn("one", status.stdout)
        assert_code(self.cli("use", "two"))
        switched = self.cli("resume")
        assert_code(switched)
        self.assertIn("проект: two", switched.stdout)

    def test_supersede_revision_and_subscription_cost(self) -> None:
        assert_code(self.cli("init", "rev", "--title", "Revision"))
        project = Path(self.tmp.name) / "rev"
        (project / "sources" / "note.md").write_text("Первая фраза.\nВторая фраза.\n", encoding="utf-8")
        assert_code(self.cli("add-source", "rev", "sources/note.md", "--kind", "own"))
        assert_code(
            self.cli(
                "add-evidence",
                "rev",
                "--claim",
                "Старая гипотеза.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--quote",
                "Первая фраза.",
                "--confidence",
                "low",
            )
        )
        assert_code(
            self.cli(
                "add-evidence",
                "rev",
                "--claim",
                "Новая гипотеза.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--quote",
                "Вторая фраза.",
                "--confidence",
                "med",
            )
        )
        done = self.cli("supersede", "rev", "E001", "--by", "E002", "--reason", "новые данные")
        assert_code(done)
        rows = [json.loads(line) for line in (project / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
        old = next(row for row in rows if row["id"] == "E001")
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], "E002")
        self.assertIn("Замещено E001", (project / "log.md").read_text(encoding="utf-8"))
        proposed = self.cli(
            "propose-revision",
            "rev",
            "--decision",
            "D2",
            "--choice",
            "O3",
            "--reason",
            "ворота не пройдены",
        )
        assert_code(proposed)
        status = self.cli("status", "rev")
        assert_code(status)
        self.assertIn("ожидает решения", status.stdout)
        self.assertIn("Токены не учитываются в режиме подписки IDE", status.stdout)
        self.assertNotIn("токены 0", status.stdout)
        accepted = self.cli("accept-revision", "rev", "--choice", "O3", "--note", "лид выбрал O3")
        assert_code(accepted)
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["checkpoints"]["options"]["choice"], "O3")
        self.assertEqual(state["checkpoints"]["options"]["history"][0]["choice"], None)
        self.assertEqual(state["revision"]["status"], "accepted")
        resumed = self.cli("resume", "rev")
        assert_code(resumed)
        self.assertIn("accepted D2", resumed.stdout)

    def test_quote_must_be_continuous_and_env_is_checked(self) -> None:
        refused = self.cli("init", "envbad", env={"STRAT_TITLE": "???? ??????"})
        assert_code(refused, 1)
        self.assertIn("испорчен", refused.stdout)
        self.assertFalse((Path(self.tmp.name) / "envbad").exists())

        assert_code(self.cli("init", "span", "--title", "Span"))
        project = Path(self.tmp.name) / "span"
        (project / "sources" / "note.md").write_text(
            "расход интернета, иначе они выжигают пакет за два дня.\n\n"
            "Сообщение «акция» звучит нормально. Но если после «добавить» ничего не видно.\n"
            "Тариф\u2011лайт держит пакет.\n",
            encoding="utf-8",
        )
        assert_code(self.cli("add-source", "span", "sources/note.md", "--kind", "synthetic"))
        spliced = self.cli(
            "add-evidence",
            "span",
            "--claim",
            "Склейка двух кусков.",
            "--type",
            "synthetic",
            "--source",
            "S001",
            "--quote",
            "расход интернета… если после «добавить» ничего не видно",
            "--confidence",
            "low",
        )
        assert_code(spliced, 1)
        self.assertIn("многоточие", spliced.stdout)
        self.assertEqual((project / "evidence.jsonl").read_text(encoding="utf-8").strip(), "")
        accepted = self.cli(
            "add-evidence",
            "span",
            "--claim",
            "Непрерывный фрагмент с другими кавычками и дефисом.",
            "--type",
            "synthetic",
            "--source",
            "S001",
            "--quote",
            'Тариф-лайт   держит\nпакет.',
            "--confidence",
            "low",
        )
        assert_code(accepted)
        assert_code(self.cli("check", "span"))
        shown = self.cli("show", "span", "evidence", "--id", "E001")
        assert_code(shown)
        self.assertIn("Тариф", shown.stdout)

    def test_inactive_bad_quote_does_not_fail_check(self) -> None:
        assert_code(self.cli("init", "oldq", "--title", "Old quote"))
        project = Path(self.tmp.name) / "oldq"
        (project / "sources" / "note.md").write_text("Только эта фраза есть в файле.\n", encoding="utf-8")
        assert_code(self.cli("add-source", "oldq", "sources/note.md", "--kind", "own"))
        quote = "Только эта фраза есть в файле."
        assert_code(
            self.cli(
                "add-evidence",
                "oldq",
                "--claim",
                "Живой факт до замены.",
                "--type",
                "fact",
                "--source",
                "S001",
                "--quote",
                quote,
                "--confidence",
                "high",
            )
        )
        assert_code(
            self.cli(
                "add-evidence",
                "oldq",
                "--claim",
                "Новая опора.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--quote",
                quote,
                "--confidence",
                "med",
            )
        )
        assert_code(self.cli("supersede", "oldq", "E001", "--by", "E002", "--reason", "заменено"))
        rows = [
            json.loads(line)
            for line in (project / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in rows:
            if row["id"] == "E001":
                row["quote"] = "первая часть… вторая часть"
        rows.append(
            {
                "id": "E003",
                "claim": "Отозванный факт без цитаты.",
                "type": "fact",
                "source": "S001",
                "quote": "",
                "locator": "note.md",
                "confidence": "low",
                "date": "2026-11-04",
                "status": "retracted",
                "superseded_by": None,
                "depends_on": [],
            }
        )
        (project / "evidence.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        warned = self.cli("check", "oldq")
        assert_code(warned, 0)
        self.assertIn("Предупреждения", warned.stdout)
        self.assertIn("E001", warned.stdout)
        self.assertIn("E003", warned.stdout)
        self.assertNotIn("ОШИБКИ", warned.stdout)
        for row in rows:
            if row["id"] == "E002":
                row["quote"] = "нет такого фрагмента… совсем"
        (project / "evidence.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        failed = self.cli("check", "oldq")
        assert_code(failed, 1)
        self.assertIn("[quote-mismatch]", failed.stdout)
        self.assertIn("E002", failed.stdout)

    def test_early_options_must_be_marked_preliminary(self) -> None:
        assert_code(self.cli("init", "early", "--title", "Early"))
        assert_code(self.cli("log", "early", "1", "Brief", "--grounds", "ready"))
        project = Path(self.tmp.name) / "early"
        bare = (
            "# Варианты\n\n"
            "### O1. Оставить как есть\n<!-- role: status-quo -->\n\n"
            "Ничего не менять.\n\nВозражение: можно упустить окно.\n\n"
            "### O2. Снизить цену\n<!-- role: change -->\n\n"
            "Урезать тариф.\n\nВозражение: маржа не посчитана.\n\n"
            "### O3. Закрыть направление\n<!-- role: close -->\n\n"
            "Не запускать.\n\nВозражение: запрос руководства останется.\n"
        )
        (project / "options.md").write_text(bare, encoding="utf-8")
        unmarked = self.cli("check", "early")
        assert_code(unmarked, 1)
        self.assertIn("[preliminary]", unmarked.stdout)
        refused = self.cli("confirm", "early", "options", "--choice", "O2", "--note", "рано")
        assert_code(refused, 1)
        self.assertIn("ещё не открыт", refused.stdout)
        (project / "options.md").write_text(
            "<!-- preliminary -->\n\nгипотезы, не подтверждены, свидетельств 0\n\n" + bare,
            encoding="utf-8",
        )
        marked = self.cli("check", "early")
        assert_code(marked)
        self.assertIn("OK", marked.stdout)
        status = self.cli("status", "early")
        assert_code(status)
        self.assertIn("предварительные", status.stdout)

    def test_fetch_saves_page_and_reports_network_errors(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = (
                    "<html><head><title>Витрина eSIM</title></head><body>"
                    "<p>Пакет для поездки стоит 5 долларов.</p>"
                    "<script>secret-token</script></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
        assert_code(self.cli("init", "pages", "--title", "Pages"))
        url = f"http://127.0.0.1:{port}/plans"
        fetched = self.cli("fetch", "pages", url)
        assert_code(fetched)
        self.assertIn("id: S001", fetched.stdout)
        project = Path(self.tmp.name) / "pages"
        saved = list((project / "sources").glob("*.md"))
        saved = [path for path in saved if path.name != "README.md"]
        self.assertEqual(len(saved), 1)
        text = saved[0].read_text(encoding="utf-8")
        self.assertIn(url, text)
        self.assertIn("Пакет для поездки стоит 5 долларов.", text)
        self.assertNotIn("secret-token", text)
        rows = [json.loads(line) for line in (project / "sources.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(rows[0]["kind"], "web")
        self.assertTrue(rows[0]["sha256"])
        again = self.cli("fetch", "pages", url)
        assert_code(again)
        self.assertIn("Уже загружено", again.stdout)
        self.assertEqual(len([path for path in (project / "sources").glob("*.md") if path.name != "README.md"]), 1)
        import socket

        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        refused = self.cli("fetch", "pages", f"http://127.0.0.1:{closed_port}/closed")
        assert_code(refused, 1)
        self.assertIn("не открыл", refused.stdout)


STRATEGY_SECTIONS = {
    "Продуктовая стратегия": [
        (
            "Целевой сегмент и работа (JTBD)",
            "Сегмент — действующие подписчики за несколько дней до списания. Работа — понять, пользуются ли они продуктом.",
        ),
        (
            "Ценностное предложение",
            "Свойство PP1: протокол из пяти интервью и письменное решение, без новой функции в этом цикле.",
        ),
        (
            "Границы MVP",
            "В этот выпуск входит только протокол интервью и решение команды. Цену и интерфейс не меняем.",
        ),
        (
            "Метрики",
            "Метрика цикла — названный барьер и решение продолжать или остановить, а не обещание выручки.",
        ),
        (
            "Риски",
            "Исследование может затянуться, и решение не будет принято, пока доля продлений продолжит падать.",
        ),
        (
            "Что мы не делаем",
            "Не обещаем скидку и не запускаем внешнюю кампанию, пока барьер не назван словами интервью.",
        ),
    ],
    "Коммуникационная стратегия": [
        (
            "Аудитории",
            "Аудитория сообщения — команда продукта, а не внешние подписчики этого короткого цикла.",
        ),
        (
            "Ключевые сообщения",
            "Сообщение команде: сначала называем барьер, наружу ничего не обещаем до решения по протоколу.",
        ),
        (
            "Каналы",
            "Канал — внутренний разбор протокола. Рекламный пуш и внешняя рассылка в этот цикл не входят.",
        ),
        (
            "Сроки и последовательность",
            "Сначала пять интервью, затем одно решение. Внешнее обещание не выходит раньше этого решения.",
        ),
        (
            "Работа с возражениями",
            "Возражение «исследование только откладывает решение» закрывается датой решения в плане проверки.",
        ),
        (
            "Как измеряется успех",
            "Успех — одно названное барьером решение команды, а не охват рекламы и не прогноз выручки.",
        ),
    ],
}


def strategy_markdown(replace: dict[str, str] | None = None, omit: str | None = None) -> str:
    parts = ["# Стратегия", ""]
    for parent, sections in STRATEGY_SECTIONS.items():
        parts.append(f"## {parent}")
        parts.append("")
        for title, body in sections:
            if title == omit:
                continue
            if replace and title in replace:
                body = replace[title]
            parts.append(f"### {title}")
            parts.append("")
            parts.append(body)
            parts.append("")
    parts.extend(
        [
            "<!-- promise: P1 -->",
            "Команде показываем выводы интервью до любой переделки продукта.",
            "<!-- product: PP1 -->",
            "",
        ]
    )
    return "\n".join(parts)


class MinimumCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"STRAT_PROJECTS_DIR": self.tmp.name}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess[str]:
        merged = dict(self.env)
        if env:
            merged.update(env)
        return run(*args, env=merged, input=input)

    def test_url_without_snapshot_warns_until_strict(self) -> None:
        assert_code(self.cli("init", "web", "--title", "Веб без снимка"))
        added = self.cli("add-source", "web", "https://example.com/tariff", "--title", "Страница")
        assert_code(added)
        self.assertIn("Локального снимка нет", added.stdout)
        assert_code(
            self.cli(
                "add-evidence",
                "web",
                "--claim",
                "На странице что-то сказано, но файла нет.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--quote",
                "цитата не с чем сверить",
                "--confidence",
                "med",
            )
        )
        warned = self.cli("check", "web")
        assert_code(warned)
        self.assertIn("[snapshot]", warned.stdout)
        self.assertIn("Предупреждения", warned.stdout)
        strict = self.cli("check", "web", "--strict")
        assert_code(strict, 1)
        self.assertIn("[snapshot]", strict.stdout)
        self.assertIn("ОШИБКИ", strict.stdout)

    def test_missing_quote_warns_until_strict(self) -> None:
        assert_code(self.cli("init", "noquote", "--title", "Без цитаты"))
        project = Path(self.tmp.name) / "noquote"
        (project / "sources" / "note.md").write_text("В файле есть фраза, но её не процитировали.\n", encoding="utf-8")
        assert_code(self.cli("add-source", "noquote", "sources/note.md", "--kind", "own"))
        assert_code(
            self.cli(
                "add-evidence",
                "noquote",
                "--claim",
                "Гипотеза без дословной опоры.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--confidence",
                "low",
            )
        )
        warned = self.cli("check", "noquote")
        assert_code(warned)
        self.assertIn("E001: нет дословной цитаты", warned.stdout)
        strict = self.cli("check", "noquote", "--strict")
        assert_code(strict, 1)
        self.assertIn("[quote]", strict.stdout)

    def test_low_confidence_recommendation_is_warning(self) -> None:
        assert_code(self.cli("init", "lowrec", "--title", "Низкая уверенность"))
        project = Path(self.tmp.name) / "lowrec"
        (project / "sources" / "note.md").write_text(
            "Рабочая гипотеза команды: барьер не в цене.\n",
            encoding="utf-8",
        )
        assert_code(self.cli("add-source", "lowrec", "sources/note.md", "--kind", "own"))
        assert_code(
            self.cli(
                "add-evidence",
                "lowrec",
                "--claim",
                "Гипотеза, что барьер не в цене.",
                "--type",
                "hypothesis",
                "--source",
                "S001",
                "--quote",
                "Рабочая гипотеза команды: барьер не в цене.",
                "--confidence",
                "low",
            )
        )
        text = strategy_markdown()
        text = text.replace(
            "Сегмент — действующие подписчики за несколько дней до списания.",
            "Сегмент — действующие подписчики. Опора слабая: гипотеза [E001].",
        )
        (project / "strategy.md").write_text(text, encoding="utf-8")
        (project / "decisions.md").write_text(
            "# Решения\n\n## D1. Сначала интервью\n\n"
            "Решение держится на слабой записи [E001], это надо видеть в проверке.\n\n"
            "- owner: лид продукта\n"
            "- metric: есть письменный барьер и решение продолжать или остановить\n"
            "- review_date: 2026-12-01\n"
            "- revisit_if: протокол не собран к дате пересмотра\n",
            encoding="utf-8",
        )
        checked = self.cli("check", "lowrec")
        assert_code(checked)
        self.assertIn("[confidence]", checked.stdout)
        self.assertIn("E001", checked.stdout)
        self.assertIn("OK", checked.stdout)

    def test_strategy_sections_fail_when_missing_or_placeholder(self) -> None:
        assert_code(self.cli("init", "stratgap", "--title", "Дыра в стратегии"))
        project = Path(self.tmp.name) / "stratgap"
        (project / "strategy.md").write_text(strategy_markdown(omit="Метрики"), encoding="utf-8")
        missing = self.cli("check", "stratgap")
        assert_code(missing, 1)
        self.assertIn("нет подраздела «Метрики»", missing.stdout)
        (project / "strategy.md").write_text(
            strategy_markdown(replace={"Риски": "TODO"}),
            encoding="utf-8",
        )
        placeholder = self.cli("check", "stratgap")
        assert_code(placeholder, 1)
        self.assertIn("подраздел «Риски» пуст или заполнен заглушкой", placeholder.stdout)
        (project / "strategy.md").write_text(strategy_markdown(), encoding="utf-8")
        assert_code(self.cli("check", "stratgap"))

    def test_option_ids_stay_stable(self) -> None:
        assert_code(self.cli("init", "opts", "--title", "Стабильные варианты"))
        project = Path(self.tmp.name) / "opts"
        first = self.cli("add-option", "opts", "--title", "Оставить как есть")
        assert_code(first)
        self.assertIn("id: O1", first.stdout)
        assert_code(self.cli("add-option", "opts", "--title", "Другой путь"))
        assert_code(self.cli("add-option", "opts", "--title", "Закрыть направление"))
        reused = self.cli("add-option", "opts", "--id", "O2", "--title", "Ещё один путь")
        assert_code(reused, 1)
        self.assertIn("уже занят", reused.stdout)
        duplicate = (
            "# Варианты\n\n"
            "### O1. Оставить как есть\n<!-- role: status-quo -->\n\nТекст.\n\nВозражение: слабое.\n\n"
            "### O1. Другой путь\n<!-- role: change -->\n\nТекст второго.\n\nВозражение: то же самое.\n\n"
            "### O3. Закрыть направление\n<!-- role: close -->\n\nТекст третьего.\n\nВозражение: рано.\n"
        )
        (project / "options.md").write_text(duplicate, encoding="utf-8")
        dup = self.cli("check", "opts")
        assert_code(dup, 1)
        self.assertIn("[options-id]", dup.stdout)
        self.assertIn("не дублируют", dup.stdout)
        swapped = (
            "<!-- preliminary -->\n\nгипотезы, не подтверждены, свидетельств 0\n\n"
            "### O1. Другой путь\n<!-- role: status-quo -->\n\n"
            "Ничего не менять, пока нет интервью и отдельной развилки.\n\nВозражение: доля уже падает.\n\n"
            "### O2. Оставить как есть\n<!-- role: change -->\n\n"
            "Сначала короткое исследование барьера, потом решение о продукте.\n\nВозражение: исследование откладывает ход.\n\n"
            "### O3. Закрыть направление\n<!-- role: close -->\n\n"
            "Остановить развитие этого направления в текущем квартале.\n\nВозражение: одного сигнала мало.\n"
        )
        (project / "options.md").write_text(swapped, encoding="utf-8")
        renumbered = self.cli("check", "opts")
        assert_code(renumbered, 1)
        self.assertIn("перенумерац", renumbered.stdout)
        removed = self.cli("supersede-option", "opts", "O2", "--reason", "путь снят, номер остаётся")
        assert_code(removed)
        self.assertIn("сохраняет ID", removed.stdout)
        nxt = self.cli("add-option", "opts", "--title", "Новый путь после снятия")
        assert_code(nxt)
        self.assertIn("id: O4", nxt.stdout)
        self.assertNotIn("id: O2", nxt.stdout)
        stable = (
            "<!-- preliminary -->\n\nгипотезы, не подтверждены, свидетельств 0\n\n"
            "### O1. Оставить как есть\n<!-- role: status-quo -->\n\n"
            "Ничего не менять, пока нет живых интервью.\n\nВозражение: доля уже описана как низкая.\n\n"
            "### O2. Другой путь\n<!-- role: change -->\n<!-- status: superseded -->\n\n"
            "Снятый путь остаётся под своим номером и не получает новый.\n\nВозражение: его уже сняли.\n\n"
            "### O3. Закрыть направление\n<!-- role: close -->\n\n"
            "Остановить развитие, не обещая экономию.\n\nВозражение: стоимость закрытия не посчитана.\n\n"
            "### O4. Новый путь после снятия\n<!-- role: change -->\n\n"
            "Короткое исследование барьера до любой переделки продукта.\n\nВозражение: исследование может затянуть решение.\n"
        )
        (project / "options.md").write_text(stable, encoding="utf-8")
        assert_code(self.cli("check", "opts"))

    def test_cost_estimate_is_not_a_bill(self) -> None:
        assert_code(self.cli("init", "priced", "--title", "Оценка"))
        assert_code(
            self.cli(
                "log",
                "priced",
                "3",
                "Сбор страниц",
                "--minutes",
                "12",
                "--input-chars",
                "4000",
                "--output-chars",
                "800",
                "--model",
                "ide/subscription",
            )
        )
        result = self.cli("cost", "priced", "--estimate")
        assert_code(result)
        self.assertIn("минуты 12", result.stdout)
        self.assertIn("Оценка (не счёт, допущение)", result.stdout)
        self.assertIn("Допущение, не счёт", result.stdout)
        self.assertIn("вход 1000", result.stdout)
        self.assertIn("выход 200", result.stdout)
        self.assertIn("0.006", result.stdout)
        self.assertIn("Это не выставленный счёт", result.stdout)
        self.assertNotIn("счёт провайдера: 0.006", result.stdout)
        rows = [
            json.loads(line)
            for line in (Path(self.tmp.name) / "priced" / "log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        recorded = next(row for row in rows if row.get("message") == "Сбор страниц")
        self.assertEqual(recorded["input_chars"], 4000)
        self.assertEqual(recorded["output_chars"], 800)
        self.assertIsNone(recorded["cost"])
        self.assertIsNone(recorded["tokens"])

    def test_fetch_binds_snapshot_to_existing_url(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = "<html><head><title>Тариф</title></head><body><p>Семейный пакет стоит 5 долларов.</p></body></html>".encode(
                    "utf-8"
                )
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
        assert_code(self.cli("init", "bind", "--title", "Привязка снимка"))
        url = f"http://127.0.0.1:{server.server_address[1]}/family"
        assert_code(self.cli("add-source", "bind", url, "--title", "Уже известный адрес"))
        fetched = self.cli("fetch", "bind", url)
        assert_code(fetched)
        self.assertIn("Снимок привязан", fetched.stdout)
        self.assertIn("id: S001", fetched.stdout)
        project = Path(self.tmp.name) / "bind"
        rows = [
            json.loads(line)
            for line in (project / "sources.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "S001")
        self.assertEqual(rows[0]["origin_url"], url)
        self.assertTrue(str(rows[0]["sha256"]))
        self.assertFalse(str(rows[0]["url_or_path"]).startswith("http"))
        assert_code(
            self.cli(
                "add-evidence",
                "bind",
                "--claim",
                "На странице сказано, что пакет стоит 5 долларов.",
                "--type",
                "fact",
                "--source",
                "S001",
                "--quote",
                "Семейный пакет стоит 5 долларов.",
                "--confidence",
                "high",
            )
        )
        checked = self.cli("check", "bind", "--strict")
        assert_code(checked)
        self.assertNotIn("[snapshot]", checked.stdout)

    def test_fetch_retries_records_failure_and_skips_snapshot(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            flaky_hits = 0

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/down"):
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"unavailable")
                    return
                if self.path.startswith("/flaky"):
                    Handler.flaky_hits += 1
                    if Handler.flaky_hits < 3:
                        self.send_response(503)
                        self.end_headers()
                        return
                body = (
                    "<html><head><title>Витрина</title></head><body>"
                    "<p>Пакет стоит 9 долларов.</p></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
        env = {"STRAT_FETCH_RETRIES": "3", "STRAT_FETCH_BACKOFF_SECONDS": "0"}
        assert_code(self.cli("init", "retry", "--title", "Повторы"))
        flaky = f"http://127.0.0.1:{port}/flaky"
        fetched = self.cli("fetch", "retry", flaky, env=env)
        assert_code(fetched)
        self.assertIn("id: S001", fetched.stdout)
        self.assertGreaterEqual(Handler.flaky_hits, 3)
        project = Path(self.tmp.name) / "retry"
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["fetch:" + flaky]["status"], "done")
        again = self.cli("fetch", "retry", flaky, env=env)
        assert_code(again)
        self.assertIn("Уже загружено", again.stdout)
        self.assertEqual(Handler.flaky_hits, 3)
        forced = self.cli("fetch", "retry", flaky, "--force", env=env)
        assert_code(forced)
        self.assertIn("Страница обновлена", forced.stdout)
        pages = [path for path in (project / "sources").glob("*.md") if path.name != "README.md"]
        self.assertEqual(len(pages), 1)
        down = f"http://127.0.0.1:{port}/down"
        failed = self.cli("fetch", "retry", down, env=env)
        assert_code(failed, 1)
        self.assertIn("503", failed.stdout)
        self.assertIn("Попыток: 3", failed.stdout)
        self.assertIn("failed", failed.stdout)
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["fetch:" + down]["status"], "failed")
        self.assertIn("503", state["steps"]["fetch:" + down]["detail"])
        log_text = (project / "log.jsonl").read_text(encoding="utf-8")
        self.assertIn("Загрузка не удалась", log_text)
        resume = self.cli("resume", "retry")
        assert_code(resume)
        self.assertIn("503", resume.stdout)
        self.assertIn("failed", resume.stdout)

    def test_resume_names_unfinished_step_and_evidence_is_idempotent(self) -> None:
        assert_code(self.cli("init", "steps", "--title", "Шаги"))
        project = Path(self.tmp.name) / "steps"
        note = project / "sources" / "note.md"
        note.write_text("Доля продлений равна 42 процентам.\n", encoding="utf-8")
        assert_code(self.cli("add-source", "steps", str(note), "--title", "Заметка"))
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        state["steps"] = {
            "fetch:https://example.com/paused": {
                "status": "started",
                "inputs_hash": "abc",
                "updated_at": "2026-11-17T00:00:00Z",
                "detail": None,
            }
        }
        (project / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        resume = self.cli("resume", "steps")
        assert_code(resume)
        self.assertIn("Продолжить незавершённый шаг", resume.stdout)
        self.assertIn("fetch:https://example.com/paused", resume.stdout)
        quote = "Доля продлений равна 42 процентам."
        first = self.cli(
            "add-evidence",
            "steps",
            "--claim",
            "В заметке доля равна 42 процентам.",
            "--type",
            "fact",
            "--source",
            "S001",
            "--quote",
            quote,
        )
        assert_code(first)
        self.assertIn("id: E001", first.stdout)
        second = self.cli(
            "add-evidence",
            "steps",
            "--claim",
            "В заметке доля равна 42 процентам.",
            "--type",
            "fact",
            "--source",
            "S001",
            "--quote",
            quote,
        )
        assert_code(second, 1)
        self.assertIn("уже есть", second.stdout)
        self.assertIn("E001", second.stdout)
        rows = [
            json.loads(line)
            for line in (project / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 1)

    def test_complete_logs_tier_and_does_not_call_ide_tier(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        calls = {"n": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                calls["n"] += 1
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = json.dumps(
                    {
                        "choices": [{"message": {"content": "черновик выжимки"}}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 5, "cost": 0.002},
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(stop_http_server, server, thread)
        secret = "secret-route-key"
        env = {
            "STRAT_LLM_API_KEY": secret,
            "STRAT_MODEL_CHEAP": "dummy-cheap",
            "STRAT_MODEL_STRONG": "dummy-strong",
            "STRAT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}/v1",
        }
        assert_code(self.cli("init", "routed", "--title", "Маршрут"))
        ide = self.cli("complete", "--tier", "ide", "--prompt", "ping", env=env)
        assert_code(ide)
        self.assertIn("MODE: manual", ide.stdout)
        self.assertEqual(calls["n"], 0)
        missing = self.cli("complete", "--prompt", "ping")
        assert_code(missing, 1)
        self.assertIn("Укажите --tier", missing.stdout)
        api = self.cli("complete", "--tier", "cheap", "--stage", "3", "--prompt", "сожми страницу", env=env)
        assert_code(api)
        self.assertEqual(calls["n"], 1)
        self.assertIn("MODE: api", api.stdout)
        self.assertIn("TIER: cheap", api.stdout)
        self.assertIn("черновик выжимки", api.stdout)
        self.assertIn("tier=cheap", api.stdout)
        self.assertNotIn(secret, api.stdout + api.stderr)
        project = Path(self.tmp.name) / "routed"
        rows = [
            json.loads(line)
            for line in (project / "log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        call = next(row for row in rows if row.get("message") == "Вызов complete, уровень cheap")
        self.assertEqual(call["tier"], "cheap")
        self.assertEqual(call["model"], "dummy-cheap")
        self.assertEqual(call["tokens"], 16)
        self.assertEqual(call["cost"], 0.002)
        self.assertGreater(call["input_chars"], 0)
        self.assertGreater(call["output_chars"], 0)
        self.assertNotIn(secret, json.dumps(call))


if __name__ == "__main__":
    unittest.main()
