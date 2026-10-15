"""One reply for an unexpected server-side failure (plex-error-redact r1, F3/F4 + media F10).

A route that catches a foreign exception must not hand ``str(e)`` to the
client: the text can carry a stored secret (a Plex token), URL userinfo
(``http://user:pw@host``) or an absolute library path. The client gets a fixed
message plus a request id; the detail goes to the server log under the same
id, redacted:

  * the caller's known secrets (raw and stripped) are replaced literally -- a
    ~20-char Plex token is below capture_artifact_redact's opaque-token length
    floor, so the content heuristics alone would miss it;
  * a ``name: value`` / ``name=value`` run whose name is in the secret-header
    class (``capture_redact.SENSITIVE_HEADER``, e.g. X-Plex-Token) loses its value;
  * each line then goes through ``capture_artifact_redact.redact_value`` (URL
    userinfo, credentialed query strings, key=secret pairs, JWTs, emails).

``scrub`` is the light pass for a message the code wrote itself (it may name a
host or a setting, never a credential): known secrets and URL userinfo only.
"""
from __future__ import annotations

import re
import uuid

from .capture_artifact_redact import redact_value
from .capture_redact import PLACEHOLDER, SENSITIVE_HEADER
from .log import get_logger

GENERIC_ERROR = "internal error (details in the server log)"
_MAX_DETAIL = 2000
_log = get_logger(__name__)

_NAMED_VALUE_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9_\-]{0,63})(?P<sep>\s*[:=]\s*)"
    r"(?P<val>[^\s&;,'\"]{1,4096})")


def _replace_secrets(text: str, secrets) -> str:
    forms = set()
    for s in secrets or ():
        if isinstance(s, str):
            forms.update(v for v in (s, s.strip()) if v)
    for v in sorted(forms, key=len, reverse=True):
        text = text.replace(v, PLACEHOLDER)
    return text


def scrub(text, secrets=()) -> str:
    """``text`` without the caller's known secrets or any URL userinfo."""
    out = _replace_secrets(str(text), secrets)
    return "\n".join(redact_value(line, redact_signed_query=False, redact_kv=False,
                                  redact_emails=False)
                     for line in out.split("\n"))


def redact_detail(text, secrets=()) -> str:
    """``text`` fit for the server log: secrets, secret-header values, userinfo,
    credentialed queries, key=secret pairs, JWTs and emails removed."""
    out = _replace_secrets(str(text), secrets)
    out = _NAMED_VALUE_RE.sub(
        lambda m: (f"{m.group('name')}{m.group('sep')}{PLACEHOLDER}"
                   if SENSITIVE_HEADER.search(m.group("name")) else m.group(0)),
        out)
    return "\n".join(redact_value(line) for line in out.split("\n"))[:_MAX_DETAIL]


def failure_reply(where: str, exc: BaseException, *, secrets=(),
                  message: str = GENERIC_ERROR, **extra) -> dict:
    """Log ``exc`` redacted under a fresh request id; return the client body:
    ``extra`` + the fixed ``message`` + that id. Never the exception text."""
    request_id = uuid.uuid4().hex[:12]
    try:
        detail = f"{type(exc).__name__}: {exc}"
    except Exception:  # a broken __str__ must not turn one failure into two
        detail = type(exc).__name__
    _log.warning("%s failed [request_id=%s]: %s", where, request_id,
                 redact_detail(detail, secrets))
    return {**extra, "error": message, "request_id": request_id}
