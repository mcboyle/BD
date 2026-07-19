"""v3.66.254 — promote-path hardening (NEXT-1).

The Workbench ``POST /api/template_manager/promote`` -> ``template_manager.promote_draft``
historically ran only the selector-lint refusal and then wrote the *raw* builder
draft straight into ``reviewed/`` — skipping the normalize step and the
resolutions / meaningful-pattern / selector-shape / BAD_TERMS gate that the CLI
(``tools/promote_template.py``) enforces. That let a resolution-less, un-normalized
draft get ENABLED through the GUI.

These tests pin the hardened contract: promote_draft must (1) normalize a raw
draft into the runtime review-candidate shape, and (2) run the same readiness gate
the CLI does, refusing (without writing/enabling) a draft that does not pass.

RED on v3.66.253: promote_draft accepts the empty-resolutions raw draft (ok:True),
writes it in raw ``template_draft`` shape, and ``promote_gate_errors`` does not exist.
"""
import glob
import json
import os
import shutil
import tempfile


# A raw builder draft that the OLD path accepts but should be refused:
# safe download selector (passes selector-lint) but NO resolutions and a
# non-media network pattern.
_EMPTY_RAW = {
    "schema": "bulk_downloader.template_draft.v1",
    "host": "bad.example.com",
    "selectors": {"download": {"button": "button.download-action"}},
    "network_patterns": ["https://bad.example.com/static/app.js"],
    "resolutions": [],
}

# A well-formed raw draft: download trigger + a media pattern + resolutions.
_WELLFORMED_RAW = {
    "schema": "bulk_downloader.template_draft.v1",
    "host": "example.com",
    "selectors": {"download": {"button": "button.download-btn"}},
    "network_patterns": ["https://example.com/video/play_1080.mp4"],
    "resolutions": [1080, 720],
}

_REVIEW_CANDIDATE_SCHEMA = "bulk_downloader.template.review_candidate.v1"


def _stage(draft, host):
    """Write a draft to a fresh drafts dir; return (filename, drafts_dir, reviewed_dir)."""
    dd = tempfile.mkdtemp()
    rd = tempfile.mkdtemp()
    fn = f"{host}.template-draft.json"
    with open(os.path.join(dd, fn), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(draft))
    return fn, dd, rd


def test_raw_draft_with_empty_resolutions_is_refused():
    """A raw draft with empty resolutions / no media pattern must be REFUSED,
    and nothing may be written to reviewed/ (it must not be enabled)."""
    from bulk_downloader.template_manager import promote_draft
    fn, dd, rd = _stage(_EMPTY_RAW, "bad.example.com")
    try:
        res = promote_draft(fn, enable=True, reviewed_dir=rd, drafts_dir=dd)
        assert res.get("ok") is False, f"expected refusal, got {res}"
        # The error should name a readiness gate, not the generic selector-lint.
        blob = json.dumps(res).lower()
        assert ("resolution" in blob) or ("media" in blob) or ("api-relevant" in blob), \
            f"expected a resolutions/meaningful gate error, got {res}"
        # Crucially: nothing enabled / written.
        written = glob.glob(os.path.join(rd, "*.template.json"))
        assert written == [], f"a refused promote must not write a template: {written}"
    finally:
        shutil.rmtree(dd, ignore_errors=True)
        shutil.rmtree(rd, ignore_errors=True)


def test_wellformed_raw_draft_is_normalized_then_promoted():
    """A well-formed raw draft must be NORMALIZED (runtime review-candidate shape)
    and then promoted+enabled — proving normalize ran before the write."""
    from bulk_downloader.template_manager import promote_draft
    fn, dd, rd = _stage(_WELLFORMED_RAW, "example.com")
    try:
        res = promote_draft(fn, enable=True, reviewed_dir=rd, drafts_dir=dd)
        assert res.get("ok") is True, f"expected promote, got {res}"
        written = glob.glob(os.path.join(rd, "*.template.json"))
        assert len(written) == 1, f"expected exactly one written template: {written}"
        t = json.loads(open(written[0], encoding="utf-8").read())
        # normalize ran -> runtime review-candidate schema, NOT the raw draft schema.
        assert t.get("schema") == _REVIEW_CANDIDATE_SCHEMA, \
            f"expected normalized schema, got {t.get('schema')}"
        assert "network_discovery" not in t, "normalized template must not carry network_discovery"
        assert t.get("resolutions") == [1080, 720], f"resolutions not carried: {t.get('resolutions')}"
        assert isinstance(t.get("network_patterns"), list) and t["network_patterns"], \
            "normalized template must carry a flat non-empty network_patterns list"
        # promote IS the enable step.
        assert t.get("status") == "enabled", f"expected enabled, got {t.get('status')}"
    finally:
        shutil.rmtree(dd, ignore_errors=True)
        shutil.rmtree(rd, ignore_errors=True)


def test_promote_gate_errors_unit():
    """The shared readiness gate (the same one the CLI enforces) flags each
    failure mode and passes a clean runtime-shape candidate."""
    from bulk_downloader.template_manager import promote_gate_errors
    from bulk_downloader.bad_terms import BAD_TERMS

    clean = {
        "schema": _REVIEW_CANDIDATE_SCHEMA,
        "status": "review_ready",
        "selectors": {"download": {"trigger": "button.download-btn"}},
        "network_patterns": ["https://example.com/video/play_1080.mp4"],
        "resolutions": [1080, 720],
    }
    assert promote_gate_errors(clean) == [], "a clean candidate must pass the gate"

    # empty resolutions
    no_res = dict(clean, resolutions=[])
    assert any("resolution" in e.lower() for e in promote_gate_errors(no_res))

    # no media/api-relevant pattern
    no_media = dict(clean, network_patterns=["https://example.com/static/app.js"])
    assert any(("media" in e.lower()) or ("api-relevant" in e.lower())
               for e in promote_gate_errors(no_media))

    # no download selector
    no_dl = dict(clean, selectors={"player": {"video": "video"}})
    assert any("download" in e.lower() for e in promote_gate_errors(no_dl))

    # a blocked term in reusable material (defense-in-depth parity with the CLI)
    bad = dict(clean,
               network_patterns=[f"https://example.com/video/{BAD_TERMS[0]}/play_1080.mp4"])
    assert any(BAD_TERMS[0].lower() in e.lower() for e in promote_gate_errors(bad)), \
        "the gate must flag a blocked BAD_TERMS substring in reusable material"

    # a RAW builder draft (network_discovery present) must be refused as un-normalized
    raw = dict(clean)
    raw["network_discovery"] = {"api_patterns": ["/api/x"]}
    assert any(("raw" in e.lower()) or ("normalize" in e.lower())
               for e in promote_gate_errors(raw))
