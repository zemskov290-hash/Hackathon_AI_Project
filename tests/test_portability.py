"""Статическая и живая проверка адаптеров. Клиент IDE не открывается."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import portability  # noqa: E402


class PortabilityTests(unittest.TestCase):
    def test_repository_static_matrix_has_no_errors(self) -> None:
        report = portability.evaluate(ROOT, live=False)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["cells"]["codex"]["hooks"]["status"], "unsupported")
        self.assertEqual(report["cells"]["codex"]["mcp"]["status"], "unsupported")
        self.assertEqual(report["cells"]["copilot"]["hooks"]["status"], "unsupported")
        self.assertEqual(report["cells"]["cursor"]["rules"]["status"], "static")
        self.assertEqual(report["cells"]["claude"]["subagents"]["status"], "static")
        self.assertIn("AGENTS.md", report["cells"]["cursor"]["rules"]["note"])

    def test_adapter_without_the_core_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("короткие свои правила\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("Конституцию не читать.\n", encoding="utf-8")
            report = portability.evaluate(root, live=False)
        self.assertFalse(report["ok"])
        blob = " ".join(report["errors"])
        self.assertIn("AGENTS.md", blob)
        self.assertTrue(any("не ссылается" in item or "нет файла" in item for item in report["errors"]))

    def test_dollar_in_a_hook_command_is_rejected(self) -> None:
        self.assertTrue(portability.shell_problems('python -c "$CLAUDE_PROJECT_DIR" name'))
        clean = portability.split_hook_command('python -c "print(1)" guard_shell')
        self.assertEqual(clean, ["python", "-c", "print(1)", "guard_shell"])

    def test_write_is_idempotent(self) -> None:
        report = portability.evaluate(ROOT, live=False)
        text = portability.render_markdown(report)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portability.md"
            self.assertTrue(portability.write_if_changed(path, text))
            stamp = path.stat().st_mtime_ns
            self.assertFalse(portability.write_if_changed(path, text))
            self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_live_commands_start(self) -> None:
        report = portability.evaluate(ROOT, live=True)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["cells"]["cursor"]["hooks"]["status"], "live")
        self.assertEqual(report["cells"]["claude"]["hooks"]["status"], "live")
        self.assertEqual(report["cells"]["cursor"]["mcp"]["status"], "live")
        self.assertEqual(report["cells"]["claude"]["mcp"]["status"], "live")
        self.assertEqual(report["cells"]["copilot"]["mcp"]["status"], "live")
        self.assertEqual(report["cells"]["cursor"]["cli"]["status"], "live")
        self.assertIn("Клиент IDE не открывался", report["cells"]["cursor"]["mcp"]["note"])

    def test_cli_check_matches_the_committed_matrix(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "portability.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = (ROOT / "docs" / "portability.md").read_text(encoding="utf-8")
        self.assertIn("Cursor", text)
        self.assertIn("unsupported", text)
        self.assertIn("Клиент IDE не открывался", text)
