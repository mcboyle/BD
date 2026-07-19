"""RED-first guard for F-TOOLSO04-03.

The autonomy output-writers redact site-config keys before embedding them into a
staged candidate (``autonomy_staging._redact_behavioral`` via ``_is_secret_key``)
or a redacted live-config backup (``autonomy_live._redact_site``). Both use a
hand-maintained ``_SECRET_RE`` narrower than the server I0008 secret-keyword SoT:
code/state/challenge/captcha/nonce/otp/csrf/bearer are NOT treated as secret, so
a value under one of those keys is embedded into the output rather than stripped.

The fix delegates the floor check to the canonical
``capture_artifact_redact._kv_key_is_secret`` (after the local selector/PII
allowlists, which must keep winning so intended non-secret keys survive).

Pre-fix: the floor keys are classified non-secret and survive into the output ->
these tests fail.
"""
from tools import autonomy_staging as S
from tools import autonomy_live as L

# Floor keys the autonomy _SECRET_RE misses (per the SoT).
_FLOOR_KEYS = ["code", "state", "challenge", "captcha", "nonce", "otp",
               "csrf", "bearer"]


def test_staging_is_secret_key_covers_floor():
    missed = [k for k in _FLOOR_KEYS if not S._is_secret_key(k)]
    assert not missed, f"autonomy_staging._is_secret_key misses SoT keys: {missed}"


def test_staging_redact_behavioral_strips_floor_keys():
    block = {k: f"LEAK-{k}" for k in _FLOOR_KEYS}
    block["url"] = "https://ex/keep"          # selector allowlist: must survive
    block["success_url"] = "https://ex/ok"    # selector allowlist: must survive
    out = S._redact_behavioral(block)
    leaked = [k for k in _FLOOR_KEYS if k in out]
    assert not leaked, f"_redact_behavioral embedded floor secrets: {leaked}"
    assert out.get("url") == "https://ex/keep"
    assert out.get("success_url") == "https://ex/ok"


def test_live_redact_site_strips_floor_keys():
    site = {k: f"LEAK-{k}" for k in _FLOOR_KEYS}
    site["output"] = "/tmp/x"                 # keep-list allowlist: must survive
    out = L._redact_site(site)
    leaked = [k for k in _FLOOR_KEYS if k in out]
    assert not leaked, f"autonomy_live._redact_site kept floor secrets: {leaked}"
    assert out.get("output") == "/tmp/x"


def test_common_secret_keys_not_regressed():
    # Regression guard: pre-existing coverage must hold in both writers.
    for k in ("password", "api_token", "session_cookie"):
        assert S._is_secret_key(k), f"staging no longer treats {k} as secret"
    live = L._redact_site({"password": "p", "api_token": "t", "keep": "k"})
    assert "password" not in live and "api_token" not in live
    assert live.get("keep") == "k"
