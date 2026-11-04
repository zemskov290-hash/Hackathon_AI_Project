"""CLI tests for the factory strategist. Standard-library unittest only."""

from __future__ import annotations

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


def run(*args: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
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
        timeout=30,
    )


def assert_code(result: subprocess.CompletedProcess[str], code: int = 0) -> None:
    if result.returncode != code:
        raise AssertionError(
            f"ожидался код {code}, получен {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


class ExampleAndHelpTests(unittest.TestCase):
    def test_help(self) -> None:
        result = run("--help")
        assert_code(result)
        self.assertIn("init", result.stdout)
        self.assertIn("check", result.stdout)

    def test_example_check_passes(self) -> None:
        result = run("check", "projects/_example")
        assert_code(result)
        self.assertIn("OK", result.stdout)

    def test_example_cost_and_resume(self) -> None:
        cost = run("cost", "projects/_example")
        assert_code(cost)
        self.assertIn("токены 7000", cost.stdout)
        self.assertIn("стоимость 0.02", cost.stdout)
        self.assertIn("минуты 90", cost.stdout)
        resume = run("resume", "projects/_example")
        assert_code(resume)
        self.assertIn("сопровождение", resume.stdout)
        self.assertIn("O2", resume.stdout)
        status = run("status", "projects/_example")
        assert_code(status)
        self.assertIn("Проверка: OK", status.stdout)

    def test_example_impact_is_transitive(self) -> None:
        result = run("impact", "projects/_example", "E003")
        assert_code(result)
        self.assertIn("E005", result.stdout)
        self.assertIn("через depends_on", result.stdout)
        self.assertIn("situation.md", result.stdout)

    def test_no_default_project_when_only_example_exists(self) -> None:
        result = run("status")
        assert_code(result, 1)
        self.assertIn("Нет проекта", result.stdout)

    def test_broken_fixture_fails_with_expected_codes(self) -> None:
        result = run("check", str(BROKEN))
        assert_code(result, 1)
        for code in ("facts", "type", "retracted", "ref", "options", "promise", "source", "source-missing"):
            self.assertIn(f"[{code}]", result.stdout)

    def test_doctor_and_manual_mode(self) -> None:
        doctor = run("doctor")
        assert_code(doctor)
        self.assertIn("ручной режим", doctor.stdout)
        self.assertIn("utf-8", doctor.stdout.casefold())
        complete = run("complete", "--role", "cheap", "--prompt", "ping")
        assert_code(complete)
        self.assertIn("MODE: manual", complete.stdout)

    def test_api_error_does_not_leak_the_key(self) -> None:
        secret = "secret-test-key-should-not-leak"
        result = run(
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
        env = os.environ.copy()
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp1251"
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "empty.pdf"
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

    def test_hooks_do_not_block_without_a_project(self) -> None:
        for name in ("post_write.py", "stage_end.py"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "hooks" / name)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
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

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run(*args, env=self.env)

    def test_init_source_evidence_and_gates(self) -> None:
        created = self.cli("init", "happy", "--title", "Проверка ворот")
        assert_code(created)
        self.assertIn("id: happy", created.stdout)
        assert_code(self.cli("check"), 0)

        blocked = self.cli("advance", "1")
        assert_code(blocked, 1)
        self.assertIn("не подтверждена", blocked.stdout)

        project = Path(self.tmp.name) / "happy"
        (project / "brief.md").write_text("# Бриф\n\nЛюди с подпиской решают, оставлять ли её.\n", encoding="utf-8")
        still = self.cli("advance", "happy", "1")
        assert_code(still, 1)
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
        assert_code(self.cli("advance", "happy", "3"))

        (project / "situation.md").write_text(
            "# Ситуация\n\n## Факты\n\nЛюди уходят, это точно известно всем.\n\n## Противоречия\n\nНет.\n\n## Неизвестно\n\n## Гипотезы\n\n## Прогнозы\n",
            encoding="utf-8",
        )
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
        assert_code(self.cli("advance", "happy", "6"))

        (project / "validation.md").write_text(
            "# Проверка\n\n"
            "- **Ожидаемое изменение:** команда называет один барьер словами пяти интервью и принимает решение\n"
            "- **База сравнения:** сейчас барьер не назван, проведённых интервью нет\n"
            "- **Срок наблюдения:** две недели на интервью и одно решение\n"
            "- **Нежелательные эффекты:** исследование затянется и решение не будет принято в срок\n",
            encoding="utf-8",
        )
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


if __name__ == "__main__":
    unittest.main()
