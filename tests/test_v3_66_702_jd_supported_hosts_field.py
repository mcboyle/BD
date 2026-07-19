"""v3.66.702 -- JD-3: promote ``jd_supported_hosts_path`` to a first-class,
GUI-exposed per-site config field (operator decision C).

694 shipped it as an UNDECLARED cfg key (a documented assumption, overridable
per-site, fail-soft) so JD host-coverage worked without adding config surface.
Decision C reverses that: make it a real, typed, GUI-editable field -- exactly
the WS4b (@468) promotion of ``jd_host``/``jd_port`` from free-text to typed
fields.

Consequences (RED-first here, GREEN after the edits):
  * declared in ``CFG_FIELDS`` so it survives a reload (like jd_host/jd_port);
  * typed ``string`` in ``site_editor._FIELD_TYPES`` so the schema + GUI render a
    labelled control (the FE is data-driven -> no component code);
  * classified ``full`` in the config-parity manifest so the parity ratchet
    (open_count == 0 floor) stays satisfied -- a runtime-tunable key left
    undeclared would RAISE open_count and fail the ratchet;
  * env-tranche safe: the name has no ``BD_`` prefix, so it cannot trip
    config_surface_inventory's BD_* token scan (the 700 lesson).
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from tools import config_surface_inventory as csi
from bulk_downloader import site_editor as SE
from bulk_downloader import app_settings_center as SC

KEY = "jd_supported_hosts_path"


# ── A. declared so it survives reload ─────────────────────────────────────
def test_key_in_cfg_fields():
    """Without this in CFG_FIELDS the value is dropped on reload (the exact
    reason jd_host/jd_port were added @468)."""
    import ast
    src = (ROOT / "bulk_downloader" / "app_kernel.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fields = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CFG_FIELDS" for t in node.targets
        ) and isinstance(node.value, ast.List):
            fields = [e.value for e in node.value.elts
                      if isinstance(e, ast.Constant)]
            break
    assert fields is not None, "CFG_FIELDS list not found"
    assert KEY in fields, "jd_supported_hosts_path must be in CFG_FIELDS"


# ── B. typed so the schema + GUI render a control ─────────────────────────
def test_field_typed_string():
    t = SE._FIELD_TYPES.get(KEY)
    assert t is not None, "jd_supported_hosts_path must be typed in _FIELD_TYPES"
    assert t[0] == "string", t
    assert t[1] and "endpoint" in t[1].lower(), "needs a human label"


def test_schema_emits_typed_string():
    sch = SE.generate_json_schema(["jd_host", "jd_port", KEY])
    text = json.dumps(sch)
    assert KEY in text, "jd_supported_hosts_path missing from generated schema"

    def _find(o):
        if isinstance(o, dict):
            node = o.get(KEY)
            if isinstance(node, dict) and "type" in node:
                return node
            for v in o.values():
                r = _find(v)
                if r is not None:
                    return r
        return None
    node = _find(sch)
    assert node is not None, "jd_supported_hosts_path has no typed schema node"
    assert node.get("type") == "string", node


def test_field_descriptor_is_editable_string():
    """The settings-center descriptor (what the FE consumes to render a control)
    must expose it as a non-secret string -- so SiteSettings renders it."""
    d = SC._field_descriptor(KEY, current="")
    assert d is not None
    assert d.get("type") == "string", d
    assert d.get("secret") in (False, None), "path is not a secret"


# ── C. parity ratchet: classified full, not left open ─────────────────────
def test_manifest_classifies_full():
    m = json.loads((ROOT / "reports" / "config_gui_manifest.json").read_text())
    assert m["exposed"].get(KEY) == "full", (
        "a runtime-tunable per-site key must be gui_exposure=full or the "
        "parity ratchet (open_count==0) fails")


def test_parity_baseline_stays_at_zero_open():
    """v3.66.710: the floor is no longer 0, and it never honestly was.

    open_count read 0 because the denominator excluded the things that were open:
    the config manifest collapsed the ENTIRE global_config store into one row
    ("(global_config store)": "full") and the site-key scan truncated CFG_FIELDS at
    the first nested ']' (57 of 235). A ratchet that counts rows reports 0 while
    `automation.master_off_switch` -- the emergency stop -- is unwritable (it was,
    until 709).

    With a real denominator the open debt is 37: the 26 automation.* keys and the
    legacy autonomy globals (auto_promote/auto_quarantine/auto_refresh/auto_repair
    et al) that have no GUI control yet. Cut 3 lands those controls and this number
    falls. `accounts` is NOT counted -- it is a decided exclusion (nested
    credentials), not undelivered parity.

    So: pin the RATCHET PROPERTY (open == the pinned baseline; it may only fall),
    not a zero that was an artifact of not looking.
    """
    b = json.loads((ROOT / "reports" / "config_parity_baseline.json").read_text())
    d = csi.build(str(ROOT))
    assert d["counts"]["open_runtime_tunable"] == b["open_count"], (
        "open settings moved without re-pinning the baseline")
    assert all(k.startswith("automation.") or not k.startswith("BD_")
               for k in b.get("open", [])), (
        "an ENV var became open -- the env tranche is supposed to be fully exposed")
    assert KEY not in b.get("open", []), (
        "jd_supported_hosts_path must not be an OPEN (un-exposed) setting")


# ── D. env-tranche safety (the 700 footgun) ───────────────────────────────
def test_no_bd_prefix_on_the_key():
    assert not KEY.upper().startswith("BD_")
    for p in ("app_kernel.py", "site_editor.py", "jd_bridge.py",
              "app_sites_queue.py"):
        src = (ROOT / "bulk_downloader" / p).read_text(encoding="utf-8")
        assert "BD_JD_SUPPORTED" not in src, (
            "must not introduce a BD_* literal for this field (env-tranche gate)")


# ── E. the docstrings no longer call it 'undeclared' ──────────────────────
def test_docstrings_updated_from_undeclared():
    jb = (ROOT / "bulk_downloader" / "jd_bridge.py").read_text(encoding="utf-8")
    aq = (ROOT / "bulk_downloader" / "app_sites_queue.py").read_text(encoding="utf-8")
    assert "undeclared cfg key `jd_supported_hosts_path`" not in jb
    assert "undeclared site-cfg key `jd_supported_hosts_path`" not in aq
