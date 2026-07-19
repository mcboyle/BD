"""v3.66.720 (Cut 9) -- the last 7 open parity keys. Operator decisions executed.

Decision: wire the 6 oidc.* SSO keys as controls; reclassify ffmpeg_path as
display-only (it is a boot-time binary path resolved in ffmpeg_bin.py -- a GUI write
to a value read once at startup is meaningless, exactly like the deploy env knobs
reclassified at 713).

Result: open parity debt 7 -> 0. The config-parity ratchet finally floors at 0 for a
REAL denominator (447 items), not the artifact-of-not-looking 0 it read before 710.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")

OIDC = ["oidc_enabled", "oidc_issuer", "oidc_client_id", "oidc_client_secret",
        "oidc_redirect_uri", "oidc_scopes"]


def _fe_source():
    src = ""
    for dp, _dn, fns in os.walk(FE):
        for fn in sorted(fns):
            if fn.endswith((".ts", ".tsx")) and not fn.endswith(".test.tsx"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                    src += fh.read()
    return src


def _inv():
    from tools import config_surface_inventory as csi

    return csi.build(ROOT)


def test_every_oidc_key_has_a_control():
    src = _fe_source()
    missing = [k for k in OIDC if '"%s"' % k not in src and "'%s'" % k not in src]
    assert not missing, "oidc keys with no control: %s" % missing


def test_oidc_client_secret_is_a_secret_field():
    """The client secret must use SecretField (write-only, blank keeps current), not a
    plain Input that would echo it back."""
    src = open(os.path.join(FE, "routes", "Settings.tsx"),
               encoding="utf-8", errors="replace").read()
    i = src.index("oidc_client_secret")
    window = src[max(0, i - 600):i + 600]
    assert "SecretField" in window, "oidc_client_secret is not a SecretField"


def test_oidc_keys_are_typed_in_the_config_subset():
    types = open(os.path.join(FE, "lib", "api-types.ts"),
                 encoding="utf-8", errors="replace").read()
    missing = [k for k in OIDC if k not in types]
    assert not missing, "oidc keys missing from GlobalConfigSubset: %s" % missing


def test_ffmpeg_path_is_reclassified_display_only():
    items = {i["key"]: i for i in _inv()["items"]}
    ff = items.get("ffmpeg_path")
    assert ff, "ffmpeg_path missing from the inventory"
    assert ff["gui_exposure"] == "display-only", (
        "ffmpeg_path is a boot-time binary path (ffmpeg_bin.py) -- a GUI write to it is "
        "meaningless; it must be display-only, not open debt")


def test_open_parity_debt_is_zero():
    d = _inv()
    base = json.load(open(os.path.join(ROOT, "reports", "config_parity_baseline.json"),
                          encoding="utf-8"))
    assert d["counts"]["open_runtime_tunable"] == base["open_count"], "re-pin the baseline"
    assert base["open_count"] == 0, (
        "open parity debt is %d; wiring the 6 oidc controls + reclassifying ffmpeg_path "
        "should take 7 -> 0" % base["open_count"])
