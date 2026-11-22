"""PDF reading: pypdf noise stays quiet, and a missing pypdf is a warning, not a defect."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import optional_packages  # noqa: E402
import pdf_text  # noqa: E402
import strat  # noqa: E402
from test_source_numbers import _minimal_pdf  # noqa: E402


def _broken_xref_pdf(text: str) -> bytes:
    """Valid objects, but every xref offset points at byte 0, as in damaged briefs."""
    good = _minimal_pdf(text)
    head, _sep, tail = good.partition(b"xref\n")
    lines = tail.split(b"\n")
    fixed = []
    for line in lines:
        if line.endswith(b" 00000 n "):
            line = b"0000000000 00000 n "
        fixed.append(line)
    return head + b"xref\n" + b"\n".join(fixed)


def _has_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


class PypdfLoggerTests(unittest.TestCase):
    def test_reader_lowers_pypdf_logger(self) -> None:
        logger = logging.getLogger(pdf_text.PYPDF_LOGGER)
        previous = logger.level
        try:
            logger.setLevel(logging.NOTSET)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "x.pdf"
                path.write_bytes(_minimal_pdf("Price 500"))
                try:
                    pdf_text.read_pdf(path)
                except pdf_text.PdfUnavailable:
                    pass
            self.assertGreaterEqual(logger.getEffectiveLevel(), logging.ERROR)
            self.assertGreaterEqual(logging.getLogger("pypdf._reader").getEffectiveLevel(), logging.ERROR)
        finally:
            logger.setLevel(previous)

    @unittest.skipUnless(_has_pypdf(), "pypdf не установлен")
    def test_damaged_xref_is_read_without_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.pdf"
            path.write_bytes(_broken_xref_pdf("Price 500 only"))
            err = io.StringIO()
            out = io.StringIO()
            handler = logging.StreamHandler(err)
            logging.getLogger().addHandler(handler)
            try:
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                    text = pdf_text.read_pdf(path)
            finally:
                logging.getLogger().removeHandler(handler)
            self.assertIn("500", text)
            # Without the fix pypdf prints "Object ID 2,0 ref repaired" or
            # "Ignoring wrong pointing object ..." for this file.
            self.assertEqual("", (err.getvalue() + out.getvalue()).strip())


class MissingPypdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("STRAT_PROJECTS_DIR")
        os.environ["STRAT_PROJECTS_DIR"] = self.tmp.name
        with contextlib.redirect_stdout(io.StringIO()):
            strat.cmd_init(Namespace(project_id="pdfcase", title="PDF", title_file=None))
        self.project = Path(self.tmp.name) / "pdfcase"
        (self.project / "sources" / "note.md").write_text("Цена 500 руб.\n", encoding="utf-8")
        (self.project / "sources" / "brief.pdf").write_bytes(_minimal_pdf("Window 14 days"))
        with contextlib.redirect_stdout(io.StringIO()):
            for rel in ("sources/note.md", "sources/brief.pdf"):
                strat.cmd_add_source(
                    Namespace(rest=["pdfcase", rel], kind="doc", title=rel, title_file=None, reliability="med")
                )

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("STRAT_PROJECTS_DIR", None)
        else:
            os.environ["STRAT_PROJECTS_DIR"] = self.previous
        self.tmp.cleanup()

    def _findings(self, deliverable: str, *, pypdf: bool) -> list[dict]:
        (self.project / "deliverable.md").write_text(deliverable, encoding="utf-8")
        real_present = optional_packages.present

        def present(name: str) -> bool:
            return pypdf if name == "pypdf" else real_present(name)

        def no_text(_path):
            if pypdf:
                raise pdf_text.PdfFailed("empty layer")
            raise pdf_text.PdfUnavailable("pypdf")

        with mock.patch.object(optional_packages, "present", side_effect=present), mock.patch.object(
            pdf_text, "read_pdf", side_effect=no_text
        ):
            return strat.check_project(self.project)

    def test_mixed_citation_is_unverified_warning_without_pypdf(self) -> None:
        found = self._findings("Окно 14 дней [S001] [S002].\n", pypdf=False)
        hard = [item for item in found if not item.get("warning")]
        self.assertFalse([item for item in hard if item["code"] in {"number-not-in-source", "tooling"}], hard)
        soft = [item for item in found if item.get("warning")]
        unverified = "\n".join(item["message"] for item in soft if item["code"] == "number-unverified")
        tooling = "\n".join(item["message"] for item in soft if item["code"] == "tooling")
        self.assertIn("14", unverified)
        self.assertIn("pypdf", tooling)

    def test_number_cited_only_to_readable_text_stays_an_error(self) -> None:
        found = self._findings("Цена 777 руб. [S001].\n", pypdf=False)
        hard = [item["code"] for item in found if not item.get("warning")]
        self.assertIn("number-not-in-source", hard)

    def test_empty_layer_with_pypdf_is_still_an_error(self) -> None:
        found = self._findings("Окно 14 дней [S001] [S002].\n", pypdf=True)
        hard = [item["code"] for item in found if not item.get("warning")]
        self.assertIn("tooling", hard)
        self.assertNotIn("number-unverified", [item["code"] for item in found])


if __name__ == "__main__":
    unittest.main()
