"""Danger/disclaimer field on the config-surface model (CLI->GUI parity).

Nothing is off-limits to GUI exposure — guard-backed capture-core settings, auth
tokens, VPN/leak protection, and path roots are all configurable — so each
irrecoverable-risk setting carries danger=True + a danger_note the GUI control
surfaces as a disclaimer. This pins that classification.

RED-first: the pristine inventory has no danger/danger_note field and no
danger_count -> these fail. After the field lands -> GREEN.

Sandbox: tools-only; zero-arg fns; root from __file__.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config_surface_inventory as csi  # noqa: E402


def _item(items, key):
    return next((i for i in items if i["key"] == key), None)


def test_every_item_has_danger_fields_and_count_present():
    d = csi.build(str(ROOT))
    assert "danger_count" in d["counts"]
    assert d["counts"]["danger_count"] > 0
    for it in d["items"]:
        assert "danger" in it and isinstance(it["danger"], bool)
        assert "danger_note" in it
        # a dangerous setting MUST carry a non-empty disclaimer
        if it["danger"]:
            assert it["danger_note"].strip(), "danger=True needs a note: %s" % it["key"]
        else:
            assert it["danger_note"] == ""


def test_guard_backed_setting_is_dangerous():
    """A capture-integrity guard-file-backed env var is flagged irrecoverable."""
    d = csi.build(str(ROOT))
    it = _item(d["items"], "BD_CAPTURE_BODIES")
    assert it is not None and it["danger"] is True
    assert "IRRECOVERABLE" in it["danger_note"] or "re-derived" in it["danger_note"]
    # and its source really is one of the pinned guard files
    assert it["source_file"] in csi._GUARD_FILES


def test_auth_and_vpn_and_secret_are_dangerous():
    d = csi.build(str(ROOT))
    items = d["items"]
    auth = _item(items, "BD_AUTH_TOKEN")
    assert auth is not None and auth["danger"] is True
    # at least one vpn_config setting is dangerous
    assert any(i["danger"] for i in items if i.get("kind") == "vpn_config")
    # a per-site secret (password) is dangerous
    pw = _item(items, "password")
    assert pw is not None and pw["danger"] is True


def test_clean_low_risk_setting_is_not_dangerous():
    """A plain queue-housekeeping tunable carries no irrecoverable-effects flag."""
    d = csi.build(str(ROOT))
    it = _item(d["items"], "BD_QUEUE_HK_GC_AGE_DAYS")
    assert it is not None
    assert it["danger"] is False and it["danger_note"] == ""


def test_danger_is_orthogonal_to_exposure_baseline_unchanged():
    """Adding the danger field must NOT move the parity ratchet (open count)."""
    d = csi.build(str(ROOT))
    import json
    base = json.loads((ROOT / "reports/config_parity_baseline.json").read_text())
    assert d["counts"]["open_runtime_tunable"] == base["open_count"]
    assert csi._check(str(ROOT), d) == 0
