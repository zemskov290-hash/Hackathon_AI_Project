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
        self.assertIn("utf-8", doctor.stdout.casefold())
        complete = self.cli("complete", "--role", "cheap", "--prompt", "ping")
        assert_code(complete)
        self.assertIn("MODE: manual", complete.stdout)

    def test_api_error_does_not_leak_the_key(self) -> None:
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
                "STRAT_LLM_BASE_URL": "http://127.0.0.1:9/v1",
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
        self.assertIn("advance", result.stdout)


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
        assert_code(self.cli("log", "happy", "5", "Three options", "--grounds", "status quo and close included"))
        no_choice = self.cli("advance", "happy", "5")
        assert_code(no_choice, 1)
        assert_code(self.cli("confirm", "happy", "options", "--choice", "O2"))
        assert_code(self.cli("advance", "happy", "5"))

        (project / "strategy.md").write_text(
            "# Стратегия\n\n## Продуктовая стратегия\n\n"
            "Аудитория — действующие подписчики. Направление O2, отказ от немедленной переделки цены.\n"
            "Свойство PP1: протокол из пяти интервью и решение по их итогам, без новой функции в этом цикле.\n\n"
            "## Коммуникационная стратегия\n\n"
            "Сообщение команде: сначала назовём барьер, наружу ничего не обещаем.\n\n"
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


if __name__ == "__main__":
    unittest.main()
