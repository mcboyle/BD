"""Phase 4 gap closure, cuts A1 + A2 (v3.66.323).

A1 (GAP5 + GAP6): the SPA Settings page gains controls for the two global_config
keys that round-tripped but had no editor -- template_auto_detect_mode and
ui_logging_level -- both written via the already-wired POST /api/global_config.

A2 (GAP3 + GAP4): two per-site boolean toggles become editable in the schema-driven
SiteSettings editor -- use_captcha_relay (added to CFG_FIELDS; auto gui-safe via the
use_* category) and ai_login_assist_enabled (categorized auth/login for grouping but
opened as a named non-credential feature toggle in _gui_class).

RED on pristine (keys absent from the editable set / the Settings page / the
GlobalConfigSubset type, counts at 197/225); GREEN after wiring.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _settings_tsx():
    return (_REPO / "frontend" / "src" / "routes" / "Settings.tsx").read_text(encoding="utf-8")


def _api_types():
    return (_REPO / "frontend" / "src" / "lib" / "api-types.ts").read_text(encoding="utf-8")


def _gcs_block():
    t = _api_types()
    m = re.search(r"interface GlobalConfigSubset\s*\{(.*?)\n\}", t, re.S)
    assert m, "GlobalConfigSubset interface not found"
    return m.group(1)


# ── A2: per-site editable schema ─────────────────────────────────────────────
def test_a2_both_toggles_editable():
    import bulk_downloader.app_settings_center as asc
    ed = asc._editable_field_set()
    assert "use_captcha_relay" in ed, "use_captcha_relay must be a gui-safe editable field"
    assert "ai_login_assist_enabled" in ed, "ai_login_assist_enabled must be a gui-safe editable field"


def test_a2_ai_login_gui_class_is_gui_safe():
    import bulk_downloader.app_settings_center as asc
    cls = asc._gui_class("ai_login_assist_enabled", asc._is_secret("ai_login_assist_enabled"))
    assert cls == "gui-safe", cls


def test_a2_captcha_in_cfg_fields():
    import bulk_downloader.app_settings_center as asc
    assert "use_captcha_relay" in set(asc._cfg_fields()), "use_captcha_relay must be in CFG_FIELDS"


def test_a2_field_counts_advanced():
    import bulk_downloader.app_settings_center as asc
    # >= floor (not ==): the absolute editable/unique counts move on every later
    # additive cut; the canonical exact pins live in test_settings_center_slice5
    # (_EDITABLE_COUNT) + test_settings_center_wiring (unique_fields), updated per
    # cut. This per-cut test asserts the FEATURE keys raised the count to at least
    # what this cut delivered, so a later tranche cannot whack-a-mole re-break it.
    assert len(asc._editable_field_set()) >= 199, len(asc._editable_field_set())
    assert asc._schema()["unique_fields"] >= 226, asc._schema()["unique_fields"]


# ── A1: global Settings controls + type ──────────────────────────────────────
def test_a1_settings_page_has_global_controls():
    s = _settings_tsx()
    assert "template_auto_detect_mode" in s, "Settings page must control template_auto_detect_mode"
    assert "ui_logging_level" in s, "Settings page must control ui_logging_level"


def test_a1_settings_writes_via_global_config_literal():
    # the write must go through the full /api/global_config literal (parity scanner)
    s = _settings_tsx()
    assert '"/api/global_config"' in s or "'/api/global_config'" in s


def test_a1_global_config_subset_declares_both_keys():
    b = _gcs_block()
    assert "template_auto_detect_mode" in b, "GlobalConfigSubset must declare template_auto_detect_mode"
    assert "ui_logging_level" in b, "GlobalConfigSubset must declare ui_logging_level"
