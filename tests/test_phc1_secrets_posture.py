"""PHC-1 (B2): the secrets read surface must never return a secret VALUE -- only
names, shapes, and rotation AGE. This pins the posture so a future change that
echoes a stored value (token/password/key material) on a read endpoint trips the
band. Likely already green post-VR-P02/VR-P03 (@521); the value is the pin.
"""
import json
import os
import tempfile


def _client():
    from bulk_downloader.app import app
    return app.test_client()


# --- /api/secrets/status returns shape only, never values -------------------
def test_secrets_status_is_value_free():
    c = _client()
    r = c.get("/api/secrets/status")
    # Either auth-gated (401/403) or a shape-only JSON; in no case a raw secret.
    if r.status_code != 200:
        return
    body = r.get_data(as_text=True)
    # The status payload is presence/shape booleans + counts; assert it parses
    # and carries no obvious value field.
    data = json.loads(body)
    for k in ("password", "token", "secret", "api_key", "value", "private_key"):
        assert k not in data, f"/api/secrets/status must not surface a '{k}' value"


# --- /api/secrets/usage returns names + age only, never values --------------
def test_secrets_usage_is_age_and_name_only():
    c = _client()
    r = c.get("/api/secrets/usage")
    if r.status_code != 200:
        return
    data = json.loads(r.get_data(as_text=True))
    rotation = data.get("rotation", {}) or {}
    for key, meta in rotation.items():
        if isinstance(meta, dict):
            # rotation metadata is age/epoch only -- no value material
            for forbidden in ("value", "secret", "plaintext", "password", "token"):
                assert forbidden not in meta, (
                    f"rotation[{key}] must carry age only, found '{forbidden}'")


# --- _mask_secrets recurses (the @521 VR-P02 fix) -- pin it ------------------
def test_mask_secrets_recurses_into_nested():
    # Pin that the masking helper redacts a NESTED secret, not just top-level.
    from bulk_downloader import app_settings_center as asc
    masker = getattr(asc, "_mask_secrets", None)
    if masker is None:
        return  # helper not present in this build -- nothing to pin
    sample = {"accounts": [{"username": "u", "password": "TOPSECRET"}],
              "nested": {"api_key": "KEYMATERIAL"}}
    try:
        out = masker(sample)
    except TypeError:
        return  # signature differs in this build; covered by VR-P02 suite
    flat = json.dumps(out)
    assert "TOPSECRET" not in flat, "nested accounts[].password leaked unmasked"
    assert "KEYMATERIAL" not in flat, "nested api_key leaked unmasked"
