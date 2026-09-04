"""Row 671: reviewed grouped selector schemas have a complete denominator."""
from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_REPTILE = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
_LEGACY_ROWS_SHA256 = "2b6a25cd9089ffb44bafe8f20cce1abf44405cd03fb388dcb0aba87c600560e9"


def _api():
    return importlib.import_module("bulk_downloader.template_selector_verifier")


def _reviewed_selector_leaves(value: object, path: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Independent denominator for the reviewed ``selectors`` schema."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_reviewed_selector_leaves(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_reviewed_selector_leaves(child, (*path, f"[{index}]")))
    elif isinstance(value, str):
        found.append((".".join(path), value))
    return found


def test_reptyle_grouped_schema_enumerates_twelve_valid_selector_strings():
    api = _api()

    assert _REPTILE.is_file(), f"precondition: reviewed template absent: {_REPTILE}"
    template = json.loads(_REPTILE.read_text(encoding="utf-8"))
    selectors = template.get("selectors")
    assert isinstance(selectors, dict) and selectors, (
        "precondition: reviewed selectors group is absent or empty"
    )
    expected = _reviewed_selector_leaves(selectors)
    assert len(expected) == 12
    assert all(isinstance(selector, str) and selector.strip() for _, selector in expected)

    rows = api.enumerate_template_selectors(template)
    assert len(rows) == 12
    assert sum(isinstance(row["selector"], str) and bool(row["selector"].strip()) for row in rows) == 12
    assert {(row["path"].split(".selectors.", 1)[1], row["selector"]) for row in rows} == set(expected)

    parsed = api.parse_selectors([row["selector"] for row in rows])
    statuses = Counter(result["status"] for result in parsed)
    assert len(parsed) == 12
    assert statuses["VALID"] == 12
    assert statuses["MALFORMED"] == 0
    assert statuses["UNKNOWN"] == 0


def test_legacy_template_denominator_and_roles_are_byte_for_byte_unchanged():
    api = _api()
    templates_module = importlib.import_module("bulk_downloader.site_templates")
    committed = templates_module.TEMPLATES

    assert len(committed) == 91, "precondition: legacy template population changed"
    templates = {template["id"]: template for template in committed}
    assert "wowgirls_network" in templates
    sample = api.enumerate_template_selectors(templates["wowgirls_network"])
    assert len(sample) == 17
    roles = Counter(row["role"] for row in sample)
    assert sum(roles.values()) == 17
    assert roles["login"] == 11
    assert roles["trigger"] == 2
    assert roles["row"] == 4
    assert set(roles) == {"login", "trigger", "row"}

    all_rows = [
        row
        for template in committed
        for row in api.enumerate_template_selectors(template)
    ]
    encoded = json.dumps(all_rows, sort_keys=True, separators=(",", ":")).encode()
    assert len(all_rows) == 550
    assert hashlib.sha256(encoded).hexdigest() == _LEGACY_ROWS_SHA256

    audit = api.audit_committed_selector_syntax()
    assert audit["template_count"] == 91
    assert audit["selector_count"] == 550
    assert audit["checked_count"] == 550
    assert audit["malformed_count"] == 0
    assert audit["unknown_count"] == 0


def test_transform_control_imports_enumerator_without_judging_grouped_schema():
    assert callable(_api().enumerate_template_selectors)
