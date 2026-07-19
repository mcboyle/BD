"""RED-first repro for F-COREBD13-03 (secret_handling / DP-08).

diagnostics_bundle is documented as producing a 'redacted / safe to share'
bundle, and the JSON snapshot IS scrubbed via redact_secrets. But _attach_logs
(bundle_as_zip include_logs=True) adds bd.log/runner.log/events.log VERBATIM,
so any secret in a log line lands in the 'safe to share' zip.

Fix: line-redact attached log content before adding it to the zip.
"""
import os
import tempfile
import zipfile
from pathlib import Path


def test_attached_logs_are_scrubbed_or_flagged():
    from bulk_downloader import diagnostics_bundle as DB
    from bulk_downloader import constants
    d = tempfile.mkdtemp()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sigSECRETVALUExyz"
    log = (
        "2026-01-01 INFO started ok\n"
        "2026-01-01 DEBUG Authorization: Bearer " + jwt + "\n"
        "2026-01-01 DEBUG GET /v.mp4?Signature=SECRETSIGVALUE&Expires=99999 -> 200\n"
        "2026-01-01 INFO done\n"
    )
    Path(d, "bd.log").write_text(log, encoding="utf-8")
    old = getattr(constants, "INSTALL_DIR", None)
    old_env = os.environ.get("BD_INSTALL_DIR")
    constants.INSTALL_DIR = d
    os.environ["BD_INSTALL_DIR"] = d
    try:
        out = tempfile.mktemp(suffix=".zip")
        res = DB.bundle_as_zip(out, include_logs=True)
        assert res.get("ok"), res
        with zipfile.ZipFile(out) as zf:
            logs = [n for n in zf.namelist() if n.startswith("logs/")]
            assert logs, "expected attached logs in bundle"
            blob = "".join(zf.read(n).decode("utf-8", "replace") for n in logs)
        assert "sigSECRETVALUExyz" not in blob, "JWT signature leaked into 'safe to share' bundle"
        assert "SECRETSIGVALUE" not in blob, "signed-URL signature leaked into 'safe to share' bundle"
        assert ("<scrubbed>" in blob or "<redacted>" in blob), "attached logs show no redaction marker"
    finally:
        constants.INSTALL_DIR = old
        if old_env is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = old_env
