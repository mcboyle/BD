"""Row 671: reviewed grouped selector schemas have a complete denominator."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_REPTILE = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
_LEGACY_ROWS_SHA256 = "2b6a25cd9089ffb44bafe8f20cce1abf44405cd03fb388dcb0aba87c600560e9"
_REVIEWED_DIR = _REPO / "templates" / "reviewed"
_ROW455 = _REPO / "tests" / "test_row455_reviewed_template_against_a_live_dom.py"
_HAZARD = _REPO / "tests" / "fixtures" / "row671" / "grouped_hazard.template.json"

# The hazards planted in the fixture corpus, as (path, exact python value).
# ``login.email`` is the leaf measured in review: with the grouped walk landed
# but no fail-closed else it produced 11 rows, 11 VALID, and no login.email
# path at all, while the pre-grouped base produced 5 rows with 1 MALFORMED.
_HAZARD_SCALAR_PATHS = {
    "row671_grouped_hazard.selectors.login.email": 123,
    "row671_grouped_hazard.selectors.quality.open_menu": True,
    "row671_grouped_hazard.selectors.quality.resolution_option": 3.5,
}
_HAZARD_LIST_PATH = "row671_grouped_hazard.selectors.download.row_selectors.[1]"
# Excluded on purpose in BOTH walks: ``walk`` has ``elif child is not None``.
_HAZARD_DROPPED_PATH = "row671_grouped_hazard.selectors.player.play_button"
_HAZARD_STRING_MALFORMED = "row671_grouped_hazard.selectors.download.api_template"


def _api():
    return importlib.import_module("bulk_downloader.template_selector_verifier")


def _reviewed_selector_leaves(value: object, path: tuple[str, ...] = ()) -> list[tuple[str, object]]:
    """Independent denominator for the reviewed ``selectors`` schema.

    EVERY leaf, not every string leaf. The first version of this helper
    collected only ``str`` values -- the identical fail-open the production
    walk carried -- so the independent denominator could never disagree with
    the subject about a garbage leaf, which is the one disagreement it exists
    to report. ``None`` is excluded here for the same reason both walks
    exclude it: an unset optional field is an absence, not a defect.
    """
    found: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_reviewed_selector_leaves(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_reviewed_selector_leaves(child, (*path, f"[{index}]")))
    elif value is not None:
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


def _hazard_template() -> dict:
    """The fixture corpus WITH its hazards asserted PRESENT, not absent.

    A precondition demanding well-formed strings is what keeps a suite from
    ever reaching this seam: the fixture check trips before
    ``enumerate_template_selectors`` runs. This asserts the opposite -- the
    garbage leaf is here, in the corpus, where the verifier will meet it.
    """
    assert _HAZARD.is_file(), f"precondition: hazard corpus absent: {_HAZARD}"
    template = json.loads(_HAZARD.read_text(encoding="utf-8"))
    grouped = template["selectors"]
    assert isinstance(grouped, dict), grouped
    assert grouped["login"]["email"] == 123, grouped["login"]["email"]
    assert grouped["quality"]["open_menu"] is True, grouped["quality"]
    assert grouped["quality"]["resolution_option"] == 3.5, grouped["quality"]
    assert grouped["player"]["play_button"] is None, grouped["player"]
    assert grouped["download"]["row_selectors"][1] == 7, grouped["download"]
    planted = sum(
        not isinstance(value, str)
        for group in grouped.values()
        for value in (group.values() if isinstance(group, dict) else [])
        if not isinstance(value, (dict, list))
    )
    assert planted == 4, f"precondition: expected 4 planted scalar leaves, got {planted}"
    return template


def _statuses(rows: list[dict]) -> dict[str, str]:
    parsed = _api().parse_selectors([row["selector"] for row in rows])
    assert len(parsed) == len(rows), (len(parsed), len(rows))
    return {row["path"]: result["status"] for row, result in zip(rows, parsed)}


def test_grouped_walk_fails_closed_on_a_corpus_resident_garbage_leaf():
    """A non-string grouped leaf is MALFORMED evidence, never a vanished row.

    ``walk`` deliberately keeps ``elif child is not None: add(child,
    child_path)`` on the flat path. Without the same else on the grouped path
    the widening is STRICTLY WEAKER than the code it parallels: the invalid
    leaf leaves the denominator and the template reads as fully valid.
    """
    template = _hazard_template()

    rows = _api().enumerate_template_selectors(template)
    paths = [row["path"] for row in rows]
    statuses = _statuses(rows)

    # The REFUSAL first, then the arithmetic. A count is what an earlier
    # generation failed on; the fact under test is that the invalid leaf is
    # still in the denominator and is still judged invalid.
    for path, value in _HAZARD_SCALAR_PATHS.items():
        assert path in statuses, (
            f"garbage leaf silently dropped from the denominator: {path}"
        )
        row = next(item for item in rows if item["path"] == path)
        assert row["selector"] == value and type(row["selector"]) is type(value)
        assert statuses[path] == "MALFORMED", (
            f"invalid leaf reclassified as {statuses[path]}: {path}"
        )
    assert len(_HAZARD_SCALAR_PATHS) == 3

    assert statuses[_HAZARD_LIST_PATH] == "MALFORMED", statuses
    assert statuses[_HAZARD_STRING_MALFORMED] == "MALFORMED", statuses
    assert _HAZARD_DROPPED_PATH not in statuses, statuses

    assert len(rows) == 10, paths
    assert len(paths) == len(set(paths)) == 10
    counted = Counter(statuses.values())
    assert counted["MALFORMED"] == 5, counted
    assert counted["VALID"] == 5, counted
    assert counted["UNKNOWN"] == 0, counted


def test_the_independent_denominator_can_see_the_garbage_leaf_too():
    """The gate's own helper must not carry the defect it is judging.

    ``_reviewed_selector_leaves`` collected strings only, which reproduced the
    production fail-open inside the control. A denominator that drops exactly
    what the subject drops agrees with a broken subject by construction.
    """
    grouped = _hazard_template()["selectors"]

    independent = dict(_reviewed_selector_leaves(grouped))
    subject = {
        row["path"].split(".selectors.", 1)[1]: row["selector"]
        for row in _api().enumerate_template_selectors(_hazard_template())
    }

    assert len(independent) == 10, sorted(independent)
    assert independent == subject, (sorted(independent), sorted(subject))
    assert independent["login.email"] == 123
    assert independent["quality.open_menu"] is True
    assert "player.play_button" not in independent


def test_removing_the_planted_hazard_removes_the_malformed_verdict():
    """Negative control: the verdict tracks the corpus, not a constant.

    If this stayed at five MALFORMED with the hazards repaired, the assertion
    above would be measuring the test's own arithmetic, not the seam.
    """
    template = _hazard_template()
    grouped = template["selectors"]
    grouped["login"]["email"] = 'input[type="email"]'
    grouped["quality"]["open_menu"] = "[aria-label]"
    grouped["quality"]["resolution_option"] = "button"
    grouped["download"]["row_selectors"][1] = ".row-b"

    statuses = _statuses(_api().enumerate_template_selectors(template))

    assert Counter(statuses.values())["MALFORMED"] == 1, statuses
    # The survivor is the string hazard, which no else can rescue.
    assert statuses[_HAZARD_STRING_MALFORMED] == "MALFORMED"
    for path in _HAZARD_SCALAR_PATHS:
        assert statuses[path] == "VALID", (path, statuses[path])


def test_the_garbage_leaf_survives_into_the_source_verifier_denominator(tmp_path):
    """Second seam: ``verify_template_source`` must count the invalid leaf."""
    missing = tmp_path / "missing-subject.html"
    assert not missing.exists(), "precondition: negative-control subject exists"

    report = _api().verify_template_source(_hazard_template(), missing)

    assert report["template_id"] == "row671_grouped_hazard"
    reported = {row["path"] for row in report["selectors"]}
    for path in _HAZARD_SCALAR_PATHS:
        assert path in reported, (
            f"source verifier denominator lost the invalid leaf: {path}"
        )
    assert report["selector_count"] == 10, report["selector_count"]
    assert report["verdict"] == "UNKNOWN"
    assert report["ok"] is False
    assert _HAZARD_DROPPED_PATH not in reported


def test_reviewed_directory_is_the_grouped_branch_corpus():
    """The grouped branch has a real corpus, and it is not the 91 templates.

    Zero of the 91 committed templates carry a ``selectors`` mapping, so the
    branch is unreachable from ``audit_committed_selector_syntax``. Its corpus
    is ``templates/reviewed/*.template.json`` -- the files
    ``toolchain/bin/bd-template-verify`` and the row 455 gate read. Seventeen
    leaves, one of them MALFORMED on the committed tree today.
    """
    templates_module = importlib.import_module("bulk_downloader.site_templates")

    assert sum("selectors" in t for t in templates_module.TEMPLATES) == 0
    files = sorted(_REVIEWED_DIR.glob("*.template.json"))
    assert [path.name for path in files] == [
        "app.reptyle.com.template.json",
        "filthykings.com.template.json",
    ], files

    rows = []
    for path in files:
        template = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(template.get("selectors"), dict), path
        template.setdefault("id", path.name.split(".template")[0])
        rows.extend(_api().enumerate_template_selectors(template))

    statuses = _statuses(rows)
    assert len(rows) == 17, [row["path"] for row in rows]
    malformed = sorted(p for p, status in statuses.items() if status == "MALFORMED")
    assert malformed == ["filthykings.com.selectors.download.api_template"], malformed
    assert Counter(statuses.values())["VALID"] == 16


def _live_count_keys() -> list[str]:
    """Read row 455's pinned adapted-shape paths without importing it."""
    assert _ROW455.is_file(), f"precondition: row455 gate absent: {_ROW455}"
    tree = ast.parse(_ROW455.read_text(encoding="utf-8"))
    for node in tree.body:
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "_LIVE_COUNTS":
            return [ast.literal_eval(key) for key in node.value.keys]
    raise AssertionError("precondition: _LIVE_COUNTS literal not found in row455")


def test_twelve_committed_leaves_and_nineteen_adapted_rows_reconcile():
    """Row 671's 19 and this gate's 12 are two denominators of one template.

    12 is the COMMITTED grouped shape. 19 is the shape the PRODUCTION adapter
    emits, which the row 455 gate pins per path. The identity is measured
    here, not asserted from prose: the adapter drops the two ``quality``
    leaves, which have no ``learned`` home, and expands the single
    ``download.trigger`` into ten resolution-templated trigger selectors.
    """
    from bulk_downloader.template_assist import (
        selector_group,
        template_to_learned_download,
    )

    template = json.loads(_REPTILE.read_text(encoding="utf-8"))
    committed = _api().enumerate_template_selectors(template)
    assert len(committed) == 12

    login = selector_group(template, "login")
    player = selector_group(template, "player")
    adapted = _api().enumerate_template_selectors({
        "id": "row671_reconciliation_probe",
        "learned": {
            "download": template_to_learned_download(template),
            "login": {
                "user_field": login["email"],
                "pass_field": login["password"],
                "submit_btn": login["submit"],
            },
            "player": {
                "player_selectors": [player["container"], player["play_button"]],
            },
        },
    })

    pinned = _live_count_keys()
    assert len(pinned) == len(set(pinned)) == 19, pinned
    assert len(adapted) == 19, [row["path"] for row in adapted]
    assert sorted(row["path"].split(".", 1)[1] for row in adapted) == sorted(pinned)

    # The row's own acceptance sentence, measured: "19 valid selectors out of
    # that corpus". VALID here is parse_selectors' verdict, not a literal.
    adapted_statuses = Counter(_statuses(adapted).values())
    assert adapted_statuses["VALID"] == 19, adapted_statuses
    assert adapted_statuses["MALFORMED"] == 0, adapted_statuses
    assert adapted_statuses["UNKNOWN"] == 0, adapted_statuses

    quality_dropped = [row["path"] for row in committed if ".quality." in row["path"]]
    trigger_expanded = [
        row["path"] for row in adapted if "trigger_selectors" in row["path"]
    ]
    assert len(quality_dropped) == 2, quality_dropped
    assert len(trigger_expanded) == 10, trigger_expanded
    assert len(committed) - len(quality_dropped) - 1 + len(trigger_expanded) == 19
