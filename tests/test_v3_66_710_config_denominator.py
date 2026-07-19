"""v3.66.710 (A-GUI Cut 2) -- the DENOMINATOR.

A parity number is a fraction. Every parity number BD reports has been computed
against a denominator that excludes the thing being asked about:

  * `_scan_site_keys()`'s docstring says "Primary source = authoritative CFG_FIELDS
    (app.py)". THE FUNCTION NEVER OPENS app.py. It reads only site_editor.py and
    derives keys from its metadata dicts, so any per-site key without editor
    metadata is invisible. CFG_FIELDS has 235 keys; the inventory scored 57.
    (Those 180 keys ARE exposed -- the site-settings page renders ~230 controls --
    so the inventory understated parity. Its errors run in BOTH directions, which
    is worse than a wrong number: it is a number nobody can sign.)

  * reports/config_gui_manifest.json collapses the ENTIRE global_config store into
    one row, `"(global_config store)": "full"`. 90 keys behind a single assertion.
    The ratchet counts ROWS, so it read open=0 while automation.master_off_switch --
    the emergency stop -- was unwritable (fixed at 709).

Fix the denominator, or no coverage number that follows means anything.
"""
import ast
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inventory():
    # build() defaults to root="." and the test harness CHDIRs into a temp BD_HOME,
    # so a bare build() silently scans an empty tree and reports zero of everything.
    # Always pass the repo root explicitly.
    from tools import config_surface_inventory as csi

    return csi.build(ROOT)


def _cfg_fields():
    """CFG_FIELDS as the authoritative per-site editable key set."""
    from bulk_downloader.app import CFG_FIELDS

    return set(CFG_FIELDS)


def test_site_key_inventory_covers_cfg_fields():
    """Every GUI-editable per-site key must be IN the inventory that claims to
    track per-site keys. CFG_FIELDS is the set app.py persists (app.py:1244 rebuilds
    site cfg as {k for k in CFG_FIELDS} -- a key outside it is DROPPED on save)."""
    scored = {i["key"] for i in _inventory()["items"] if i["kind"] == "site_key"}
    missing = sorted(_cfg_fields() - scored)
    assert not missing, (
        "%d per-site keys are in CFG_FIELDS but absent from the config inventory. "
        "_scan_site_keys() claims CFG_FIELDS is its source but never opens app.py. "
        "First 10: %s" % (len(missing), missing[:10]))


def test_scan_site_keys_actually_reads_app_py():
    """The docstring must not lie. If the function claims CFG_FIELDS/app.py as its
    source, it has to read it -- this is the bug, stated as a test.

    NOTE: the docstring itself contains "CFG_FIELDS (app.py)", so a naive substring
    check over the whole function PASSES on the broken code. The docstring is the
    thing that lies; it must be stripped before the CODE is examined."""
    src = open(os.path.join(ROOT, "tools", "config_surface_inventory.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_scan_site_keys")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]  # drop the docstring -- it is the claim under test
    code = "\n".join(ast.get_source_segment(src, n) or "" for n in body)
    assert "app.py" in code or "CFG_FIELDS" in code, (
        "_scan_site_keys() CODE never references app.py / CFG_FIELDS, yet its "
        "docstring calls them its primary source. It reads only site_editor.py, so "
        "any per-site key without editor metadata is invisible (57 of 235 scored).")


def test_global_config_is_enumerated_per_key_not_one_row():
    """The catch-all row hides 90 keys behind a single 'full' assertion."""
    man = json.load(open(os.path.join(ROOT, "reports", "config_gui_manifest.json"),
                         encoding="utf-8"))["exposed"]
    assert "(global_config store)" not in man, (
        "the manifest still collapses the whole global_config store into ONE row -- "
        "a gate counting rows cannot see the 90 keys behind it")


def test_every_global_config_key_has_its_own_manifest_row():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA

    man = json.load(open(os.path.join(ROOT, "reports", "config_gui_manifest.json"),
                         encoding="utf-8"))["exposed"]
    missing = sorted(k for k in GLOBAL_CONFIG_SCHEMA if k not in man)
    assert not missing, (
        "%d declared global_config keys have no manifest row: %s"
        % (len(missing), missing[:8]))


def test_automation_keys_are_visible_to_the_parity_surface():
    """The 21 keys declared at 709 must now be COUNTABLE, not hidden behind a
    catch-all. This is what makes the A-GUI gap measurable instead of invisible."""
    man = json.load(open(os.path.join(ROOT, "reports", "config_gui_manifest.json"),
                         encoding="utf-8"))["exposed"]
    autos = [k for k in man if k.startswith("automation.")]
    assert len(autos) >= 26, (
        "only %d automation.* rows in the GUI manifest; the parity surface still "
        "cannot see the automation program" % len(autos))
