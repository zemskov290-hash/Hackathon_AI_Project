"""A certificate failure is retried once with certifi's bundle, if certifi is already there."""

from __future__ import annotations

import ssl
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import strat  # noqa: E402
import tls_fallback  # noqa: E402


def cert_error() -> urllib.error.URLError:
    return urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"))


class TlsFallbackTests(unittest.TestCase):
    def test_retry_uses_the_certifi_context(self) -> None:
        context = ssl.create_default_context()
        calls: list[dict] = []

        def fake(request, timeout, **kwargs):
            calls.append(kwargs)
            if "context" not in kwargs:
                raise cert_error()
            return "opened"

        with mock.patch.object(tls_fallback.urllib.request, "urlopen", side_effect=fake), mock.patch.object(
            tls_fallback, "certifi_context", return_value=context
        ):
            self.assertEqual(tls_fallback.urlopen("https://example.org", timeout=1), "opened")
        self.assertEqual(calls, [{}, {"context": context}])

    def test_without_certifi_the_error_stands(self) -> None:
        with mock.patch.object(tls_fallback.urllib.request, "urlopen", side_effect=cert_error()), mock.patch.object(
            tls_fallback, "certifi_context", return_value=None
        ):
            with self.assertRaises(urllib.error.URLError):
                tls_fallback.urlopen("https://example.org", timeout=1)

    def test_other_network_errors_are_not_retried(self) -> None:
        opener = mock.Mock(side_effect=urllib.error.URLError(ConnectionRefusedError("refused")))
        with mock.patch.object(tls_fallback.urllib.request, "urlopen", opener), mock.patch.object(
            tls_fallback, "certifi_context", return_value=ssl.create_default_context()
        ):
            with self.assertRaises(urllib.error.URLError):
                tls_fallback.urlopen("https://example.org", timeout=1)
        self.assertEqual(opener.call_count, 1)

    def test_fetch_names_the_certificate_and_does_not_retry_forever(self) -> None:
        with mock.patch.object(tls_fallback.urllib.request, "urlopen", side_effect=cert_error()), mock.patch.object(
            tls_fallback, "certifi_context", return_value=None
        ):
            with self.assertRaises(strat.FetchFailed) as caught:
                strat.download_url_once("https://example.org/page")
        self.assertIn("Сертификат", str(caught.exception))
        self.assertFalse(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
