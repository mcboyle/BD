"""Row 732: portable exports redact secret names and URL userinfo at depth."""
BD_GATE_SCOPE = "repo-wide"

import json

from bulk_downloader.capture_artifact_redact import scan_artifact_secrets
from bulk_downloader.site_editor import diff_config, export_config


def test_export_descends_into_nested_config_values():
    cfg = {"name": "x", "learned": {"download": {"capture_url": "https://u:p@example.invalid/a", "password": "AAAA", "url_attribute": "href"}}}
    exported = export_config(cfg)
    assert "u:p@" not in json.dumps(exported)
    assert scan_artifact_secrets(exported) == []
    assert "password" not in exported["config"]["learned"]["download"]
    assert exported["config"]["learned"]["download"]["url_attribute"] == "href"


def test_export_names_the_stripped_secret_by_its_dotted_path():
    """The preview promises the operator which fields to re-enter; a bare
    key name cannot say WHICH nested field was removed."""
    cfg = {"name": "x", "learned": {"download": {"password": "SYNTHETIC-NOT-A-KEY"}}}
    exported = export_config(cfg)
    assert exported["_had_secrets"] == ["learned.download.password"]


def test_diff_config_masks_a_secret_nested_below_the_top_level():
    """H77: diff_config's loop tests TOP-LEVEL keys only, so a secret at
    depth >= 2 was returned raw to the preview affordance while
    export_config on the SAME input redacted it."""
    nested = {"learned": {"download": {"password": "SYNTHETIC-NOT-A-KEY"}}}
    assert "SYNTHETIC-NOT-A-KEY" not in json.dumps(export_config(nested)), (
        "precondition: export_config is the sibling surface that already redacts")
    diffs = diff_config({}, nested)
    assert diffs, "precondition: the diff is nonempty, so the assertion has a subject"
    assert "SYNTHETIC-NOT-A-KEY" not in json.dumps(diffs)
    assert diffs[0]["new"]["download"]["password"] == "(set)"
    # set/empty is the distinction the top-level branch already makes, so the
    # nested walk must make it too rather than masking everything to "(set)".
    blank = diff_config({}, {"learned": {"download": {"password": ""}}})
    assert blank[0]["new"]["download"]["password"] == "(empty)"
