"""Retracting an assumption [U…] keeps history, names dependents and makes check catch stale citations."""

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import strat  # noqa: E402
from test_mcp import initialize, start_server, tool_body, tool_text  # noqa: E402


def _quiet(func, namespace: Namespace) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        func(namespace)
    return buffer.getvalue()


def _add(project: str, value: str, name: str, note: str = "", kind: str = "допущение") -> None:
    _quiet(
        strat.cmd_add_assumption,
        Namespace(
            project=project,
            kind=kind,
            value=value,
            value_file=None,
            name=name,
            name_file=None,
            unit="млн руб.",
            unit_file=None,
            note=note,
            note_file=None,
        ),
    )


class RetractAssumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        _quiet(strat.cmd_init, Namespace(project_id="ret", title="Отзыв", title_file=None))
        self.project = Path(self.tmp.name) / "ret"
        _add("ret", "2", "экономия на поддержке")
        _add("ret", "7", "прирост прибыли", note="Сумма U001 и удержания.", kind="прогноз")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STRAT_PROJECTS_DIR", None)
        else:
            os.environ["STRAT_PROJECTS_DIR"] = self.previous
        self.tmp.cleanup()

    def rows(self) -> dict[str, dict]:
        lines = (self.project / "assumptions.jsonl").read_text(encoding="utf-8").splitlines()
        return {row["id"]: row for row in (json.loads(line) for line in lines if line.strip())}

    def test_retract_keeps_record_and_lists_dependents(self) -> None:
        (self.project / "deliverable.md").write_text(
            "# Итог\n\nЭкономия — допущение 2 млн руб. [U001].\n", encoding="utf-8"
        )
        out = _quiet(
            strat.cmd_retract_assumption,
            Namespace(rest=["ret", "U001"], reason="руководитель отклонил", reason_file=None, by=None),
        )
        row = self.rows()["U001"]
        self.assertEqual(row["status"], "retracted")
        self.assertEqual(row["retracted_reason"], "руководитель отклонил")
        self.assertIn("retracted_at", row)
        self.assertEqual(row["value"], "2")
        self.assertIn("U002", out)
        self.assertIn("deliverable.md:3", out)
        log = (self.project / "log.jsonl").read_text(encoding="utf-8")
        self.assertIn("Отозвано допущение U001", log)
        again = _quiet(
            strat.cmd_retract_assumption,
            Namespace(rest=["ret", "U001"], reason="ещё раз", reason_file=None, by=None),
        )
        self.assertIn("уже отозвано", again)
        self.assertEqual(self.rows()["U001"]["retracted_reason"], "руководитель отклонил")

    def test_check_flags_stale_citation_but_not_the_retraction_note(self) -> None:
        _quiet(
            strat.cmd_retract_assumption,
            Namespace(rest=["ret", "U001"], reason="нет опоры", reason_file=None, by=None),
        )
        deliverable = self.project / "deliverable.md"
        deliverable.write_text("# Итог\n\nЭкономия — допущение 2 млн руб. [U001].\n", encoding="utf-8")
        stale = [
            item for item in strat.check_project(self.project) if item["code"] == "retracted" and not item.get("warning")
        ]
        self.assertTrue(any("[U001]" in item["message"] for item in stale), stale)
        deliverable.write_text(
            "# Итог\n\nДопущение 2 млн руб. [U001] отклонено руководителем и в расчёт не входит.\n",
            encoding="utf-8",
        )
        stale = [item for item in strat.check_project(self.project) if item["code"] == "retracted"]
        self.assertEqual(stale, [])

    def test_replacement_must_exist_and_is_recorded(self) -> None:
        with self.assertRaises(SystemExit):
            _quiet(
                strat.cmd_retract_assumption,
                Namespace(rest=["ret", "U001"], reason="замена", reason_file=None, by="U009"),
            )
        self.assertEqual(self.rows()["U001"]["status"], "active")
        _quiet(
            strat.cmd_retract_assumption,
            Namespace(rest=["ret", "U001"], reason="замена", reason_file=None, by="U002"),
        )
        self.assertEqual(self.rows()["U001"]["superseded_by"], "U002")

    def test_mcp_tool_retracts(self) -> None:
        server = start_server(Path(self.tmp.name))
        self.addCleanup(server.close)
        initialize(server)
        listed = server.request("tools/list")
        assert listed is not None
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("strat_retract_assumption", names)
        result = tool_body(
            server.call("strat_retract_assumption", {"project": "ret", "id": "U001", "reason": "отклонено"})
        )
        self.assertFalse(result["isError"], tool_text(result))
        self.assertIn("U002", tool_text(result))
        self.assertEqual(self.rows()["U001"]["status"], "retracted")


if __name__ == "__main__":
    unittest.main()
