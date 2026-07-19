"""Tests for the P9 data-engineering tooling (#13):
template_analytics, document_schemas, capture_analytics.

Runs under run_tests.py: zero-arg functions, repo root from __file__.
"""
import os
import json
import shutil
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO))

import template_analytics as TA  # noqa: E402
import document_schemas as DS  # noqa: E402
import capture_analytics as CA  # noqa: E402


import contextlib  # noqa: E402


@contextlib.contextmanager
def _seeded_reptyle_draft():
    """Self-seed a reptyle draft from the shipped reviewed gold into the real
    templates/drafts when absent, then remove ONLY what we seeded. The clean zip
    ships only the reviewed gold, so a draft (total>=2, draft<->gold drift, draft
    yield per_host) is otherwise an env-coupled sandbox-RED / stash-GREEN
    divergence. The 515 pattern."""
    fname = "app.reptyle.com.template-draft.json"
    gold = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
    drafts = _REPO / "templates" / "drafts"
    cands = _REPO / "templates" / "review_candidates"
    pre_existing = (cands / fname).is_file() or (drafts / fname).is_file()
    seeded = None
    if not pre_existing:
        assert gold.is_file(), f"shipped reviewed gold missing: {gold}"
        cand = json.loads(gold.read_text("utf-8"))
        cand["host"] = "app.reptyle.com"
        cand["status"] = "draft"
        drafts.mkdir(parents=True, exist_ok=True)
        seeded = drafts / fname
        seeded.write_text(json.dumps(cand), "utf-8")
    try:
        yield
    finally:
        if seeded is not None and seeded.is_file():
            seeded.unlink()


# ── template_analytics ─────────────────────────────────────────────

def test_template_analytics_real_tree():
    with _seeded_reptyle_draft():
        a = TA.analyze(str(_REPO))
        assert a["total_templates"] >= 2, a["total_templates"]
        assert a["completeness"]["overall"]["n"] == a["total_templates"]
        assert a["gate_ready"]["rate"] is not None
        groups = a["selector_group_coverage"]
        for g in ("download", "login", "player", "quality"):
            assert g in groups, (g, groups)
        # reptyle draft vs gold drift was compared
        assert a["drift"]["available"] is True
        assert a["drift"]["compared"] >= 1


def test_template_analytics_empty_tree_no_crash():
    root = tempfile.mkdtemp(prefix="ta_")
    try:
        os.makedirs(os.path.join(root, "templates", "reviewed"))
        a = TA.analyze(root)
        assert a["total_templates"] == 0
        assert a["gate_ready"]["rate"] is None
        assert a["completeness"]["overall"]["n"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_template_analytics_markdown_renders():
    md = TA._md(TA.analyze(str(_REPO)))
    assert "# Template analytics" in md
    assert "Resolution coverage" in md


# ── document_schemas ───────────────────────────────────────────────

def test_document_schemas_has_all_db_tables():
    doc = DS.render(str(_REPO))
    for tbl in ("history", "queue", "push_subscriptions", "session_history"):
        assert f"`{tbl}`" in doc, tbl


def test_document_schemas_has_template_contract_and_status():
    doc = DS.render(str(_REPO))
    assert "Completeness contract" in doc
    assert "Promotion gate" in doc
    assert "enabled" in doc
    # introspected gold schema marker present
    assert "schema marker" in doc


def test_document_schemas_db_parse_direct():
    tables, indexes = DS._db_schema(str(_REPO))
    names = {t[0] for t in tables}
    assert {"history", "queue", "push_subscriptions", "session_history"} <= names
    assert len(indexes) >= 6  # db.py defines several indexes


# ── capture_analytics ──────────────────────────────────────────────

def test_capture_analytics_graceful_and_yield():
    with _seeded_reptyle_draft():
        # dirs -> a nonexistent capture dir: the artifact count must be 0 by
        # CONSTRUCTION, not by assuming the tree carries no captures. On the
        # operator host the real capture store legitimately lives under the
        # repo (hundreds of .wacz), which used to fail this clean-tree
        # assumption. Yield is dirs-independent (template_inventory.scan on
        # root), so the test's real assertion -- graceful zero-artifact result
        # WITH the draft yield reported -- is unchanged.
        a = CA.analyze(str(_REPO), dirs=["__bd_no_capture_dirs__"])
        # no artifacts in the (deliberately empty) searched dirs
        assert a["artifacts"]["count"] == 0
        # but the draft yield is reported
        assert a["yield"]["available"] is True
        assert "app.reptyle.com" in a["yield"]["per_host"]


def test_capture_analytics_counts_artifacts_in_fixture():
    root = tempfile.mkdtemp(prefix="ca_")
    try:
        cdir = os.path.join(root, "captures")
        os.makedirs(cdir)
        # a fake wacz + a capture json carrying a host
        open(os.path.join(cdir, "app.reptyle.com.session.wacz"), "wb").write(b"PK\x03\x04stub")
        import json as _j
        with open(os.path.join(cdir, "capture_001.json"), "w") as fh:
            _j.dump({"host": "x.example.com"}, fh)
        a = CA.analyze(root, dirs=["captures"])
        assert a["artifacts"]["count"] == 2
        assert a["artifacts"]["total_bytes"] > 0
        assert a["artifacts"]["by_host"].get("app.reptyle.com") == 1
        assert a["artifacts"]["by_host"].get("x.example.com") == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
