"""T10 / PHC-2 — import-preview redaction.

`dev_suite.import_preflight` previews a bulk-import CSV/XLSX before importing.
The preview echoes per-row would-be configs (login_url) and parser / validation
messages. A bulk-import file can carry credentials in a login_url's userinfo
(``user:pass@host``) or a signed query string, so the *preview response* must be
swept through the canonical capture redactor before it leaves the process.

RED-first: on pristine source the preview echoes the raw login_url (userinfo +
signed query) → ``scan_artifact_secrets`` is non-empty → these tests FAIL. After
the redaction shim → GREEN. Detection (dedup / collision) runs on RAW values
inside the impl; only the returned strings are scrubbed.

Sandbox runner: zero-arg test functions; root derived from __file__; no pytest
builtins.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bulk_downloader import dev_suite as ds  # noqa: E402
from bulk_downloader.capture_artifact_redact import (  # noqa: E402
    scan_artifact_secrets,
)

# A row whose login_url carries BOTH authority userinfo (user:pass@) AND a
# signed query string — the two credential vectors a bulk-import file can smuggle
# into the preview response. The username/password columns are present too.
_CSV = (
    "login_url,name,username,password\n"
    "https://u:s3cr3tPW@site.example/login?token=abc123signedTOKEN,Site One,bob,hunter2\n"
    "https://host.test/in?auth=Bearer_aaaaaaaaaaaaaaaaaaaaaaaa,Site Two,a@b.com,pw\n"
)


def test_import_preflight_preview_is_redacted():
    """No residual secret anywhere in the preview structure (rows, errors,
    warnings, file, parse_errors) — userinfo / signed query / kv-secret / email
    / token all cleared."""
    r = ds.import_preflight(text=_CSV)
    findings = scan_artifact_secrets(r)
    assert findings == [], "preview leaked secrets: %r" % (findings,)


def test_import_preflight_preview_preserves_structure():
    """Redaction keeps the preview useful: host + path survive; only the
    credential parts are placeholdered; counts / flags pass through untouched."""
    r = ds.import_preflight(text=_CSV)
    assert r["ok"] is True
    assert r["rows_parsed"] == 2
    assert r["tool"] == "import_preflight"
    row0 = r["rows"][0]
    # structure preserved …
    assert "site.example/login" in row0["login_url"]
    # … but the credential bytes are gone
    assert "s3cr3tPW" not in row0["login_url"]
    assert "abc123signedTOKEN" not in row0["login_url"]


def test_import_preflight_detection_runs_on_raw_not_redacted():
    """Two byte-identical login_urls (creds included) must flag as a duplicate —
    proving dedup runs on RAW values inside the impl, before the output scrub.
    The duplicate count is an int → untouched by redaction; the output is still
    secret-clean."""
    dup = (
        "login_url,name,username,password\n"
        "https://u:p1@dup.example/x?sig=AAAAAAAAAAAA,One,a,1\n"
        "https://u:p1@dup.example/x?sig=AAAAAAAAAAAA,Two,b,2\n"
    )
    r = ds.import_preflight(text=dup)
    assert r["rows_parsed"] == 2
    assert r["duplicate_urls_in_file"] == 1, r
    assert scan_artifact_secrets(r) == []
