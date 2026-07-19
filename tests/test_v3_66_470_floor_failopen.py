"""v3.66.470 DEFER-FLOOR-FAILOPEN -- fail-open-into-scrub at the WACZ export
boundary.

When ``scan_floor_secrets`` returns residual at the export boundary,
``make_wacz`` now force-scrubs the flagged paths to the redaction PLACEHOLDER
and re-scans BEFORE raising ``WaczRedactionError``. The blunt placeholder scans
clean by construction, so a residual that the capture-time redactor missed is
removed rather than aborting the export -- yet the floor stays fail-closed: if
anything still scans dirty after the forced scrub, it still raises. A
value-free ``forced_floor_scrub`` count is stamped for audit.

Safety shape: strictly MORE scrubbing, never less. keep_full signed-URL
residuals are *allowed* by scan_floor_secrets (the surface_full skip) so they
never enter the residual list -> never force-scrubbed -> keep_full export stays
byte-identical. Hard credentials (jwt/userinfo/kv_secret/opaque) DO enter the
list and are blunt-scrubbed.

RED-first: on pristine @469 the forced-scrub helper does not exist and make_wacz
raises on a hard-credential residual instead of scrubbing it.
"""
import json
import zipfile
import io

from bulk_downloader import capture_artifact_redact as car
from bulk_downloader import wacz_export as wx
from bulk_downloader.capture_redact import PLACEHOLDER


def _force_helper():
    # The new force-scrub helper must exist in capture_artifact_redact.
    return getattr(car, "_force_scrub_floor")


def test_force_scrub_helper_blunt_scrubs_flagged_paths():
    """The helper replaces the leaf at each flagged path with PLACEHOLDER and
    leaves the rest untouched; the re-scan is then clean."""
    fn = _force_helper()
    cap = {
        "network_log": [
            {"url": "https://cdn.example.com/v.mp4", "method": "GET"},
            {"url": "https://x/y?token=abcDEFsecret1234567890zzz", "method": "GET"},
        ],
        "kept": "color=red",
    }
    residual = [("$.network_log[1].url", "kv_secret")]
    scrubbed = fn(cap, residual)
    assert scrubbed["network_log"][1]["url"] == PLACEHOLDER
    # untouched leaves preserved
    assert scrubbed["network_log"][0]["url"] == "https://cdn.example.com/v.mp4"
    assert scrubbed["kept"] == "color=red"
    # caller's dict not mutated
    assert cap["network_log"][1]["url"] != PLACEHOLDER


def test_force_scrub_only_touches_flagged_paths():
    """A path NOT in the residual list is never scrubbed (no over-reach)."""
    fn = _force_helper()
    cap = {"a": {"b": "user:pw@host/x?sig=zzzz", "c": "user:pw@host/x?sig=zzzz"}}
    residual = [("$.a.b", "signed_url")]
    scrubbed = fn(cap, residual)
    assert scrubbed["a"]["b"] == PLACEHOLDER
    assert scrubbed["a"]["c"] == "user:pw@host/x?sig=zzzz"


def _read_capture_json(wacz_bytes):
    with zipfile.ZipFile(io.BytesIO(wacz_bytes)) as z:
        names = [n for n in z.namelist() if n.endswith("capture.json")]
        assert names, f"no capture.json in {z.namelist()}"
        return json.loads(z.read(names[0]).decode("utf-8"))


def test_make_wacz_scrubs_residual_instead_of_raising():
    """A hard-credential residual that the capture-time redactor leaves is
    force-scrubbed at export; the WACZ is produced with the leaf placeholdered
    and a value-free forced_floor_scrub count stamped, instead of raising."""
    # Simulate a residual the redactor missed: swap scan_floor_secrets so the
    # FIRST scan reports a hard-credential residual at a real path, and rely on
    # the real scanner for the re-scan (PLACEHOLDER scans clean). wacz_export
    # imports the name from car at call time (module attr), so swap car's attr.
    real_scan = car.scan_floor_secrets
    calls = {"n": 0}

    def fake_scan(capture, profile=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [("$.network_log[0].url", "kv_secret")]
        return real_scan(capture, profile)

    car.scan_floor_secrets = fake_scan
    try:
        cap = {"network_log": [{"url": "https://x/y?token=SECRETvalue1234567890", "method": "GET"}],
               "dom_log": []}
        out = wx.build_wacz_bytes(cap)
    finally:
        car.scan_floor_secrets = real_scan
    cj = _read_capture_json(out)
    assert cj["network_log"][0]["url"] == PLACEHOLDER, "residual was not force-scrubbed"
    rp = cj.get("redaction_profile", {})
    assert rp.get("forced_floor_scrub") == 1, f"forced_floor_scrub count missing/wrong: {rp}"


def test_make_wacz_still_fails_closed_when_scrub_cannot_clear():
    """If residual persists even after the forced scrub (re-scan still dirty),
    the floor still raises -- fail-closed is preserved."""
    real_scan = car.scan_floor_secrets

    def always_dirty(capture, profile=None):
        return [("$.network_log[0].url", "opaque_token")]

    car.scan_floor_secrets = always_dirty
    raised = False
    try:
        cap = {"network_log": [{"url": "https://x/y", "method": "GET"}], "dom_log": []}
        try:
            wx.build_wacz_bytes(cap)
        except wx.WaczRedactionError:
            raised = True
    finally:
        car.scan_floor_secrets = real_scan
    assert raised, "floor must still raise when residual survives the forced scrub"
