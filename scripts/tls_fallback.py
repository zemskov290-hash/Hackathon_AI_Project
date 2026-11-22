"""Open a URL; on a certificate verification failure retry once with certifi's CA bundle.

Some Windows machines lack a root (for example HARICA TLS ECC Root CA 2021) in the
system store, and urllib then fails on sites that browsers open. certifi is not a
dependency and is never installed here: if it is already importable, its bundle is
used for the retry; otherwise the original error stands and the message says why.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request

CERT_HINT = (
    "Сертификат сайта не проверен хранилищем корней системы. "
    "Если certifi уже установлен, повтор идёт с его набором; ставить его ради этого не нужно."
)


def certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        return None


def is_cert_failure(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(reason)


def urlopen(request, timeout: float):
    """Like urllib.request.urlopen; one retry with certifi only for a certificate failure."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        if not is_cert_failure(exc):
            raise
        context = certifi_context()
        if context is None:
            raise
        return urllib.request.urlopen(request, timeout=timeout, context=context)
