"""One product name everywhere a person or an agent reads it."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

NAME = "Стратег Фабрики продуктов"
OLD = "фабрики проектов"


class NamingTests(unittest.TestCase):
    def test_titles_use_the_product_name(self) -> None:
        for rel in ("README.md", "AGENTS.md"):
            first = (ROOT / rel).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, f"# {NAME}", rel)

    def test_cli_and_mcp_use_the_product_name(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "strat.py"), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )
        self.assertIn(NAME, result.stdout)
        import mcp_server

        self.assertIn(NAME, mcp_server.INSTRUCTIONS)

    def test_old_name_is_gone_from_tool_texts(self) -> None:
        for rel in ("README.md", "AGENTS.md", "scripts/strat.py", "scripts/mcp_server.py"):
            self.assertNotIn(OLD, (ROOT / rel).read_text(encoding="utf-8").casefold(), rel)


if __name__ == "__main__":
    unittest.main()
