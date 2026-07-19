"""v3.66.242 — relaxed-redaction (LOCAL ONLY) capture toggle.

The cockpit Capture form exposes a default-OFF "Relaxed redaction" checkbox. When
ON, the run-capture submit forwards ``reduced_redaction:true``; ``cockpit_core.
start_task`` then (a) flags the task record and (b) injects
``BD_REDACT_NETWORK_URLS=keep_full`` into the capture subprocess env, so the WACZ
writes (self-stamping local_only/reduced_redaction) even when capture-time scrubbing
misses a signing shape. This is the GUI-reachable form of the existing env hatch —
it does NOT weaken the redaction floor; the floor still refuses for normal captures.

Form/handler/badge are source-presence checks (same style as
test_cockpit_capture_form_fields). The env + rec flag are exercised against the real
``start_task`` with ``subprocess.run`` monkeypatched (no browser launch).
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_CONSOLE = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")


# ── form ↔ params ↔ surface (source presence) ────────────────────────────────
def test_reduced_redaction_checkbox_in_capture_form():
    assert 'id="cs_reduced"' in _CONSOLE, \
        "capture form missing the Relaxed redaction checkbox (#cs_reduced)"


def test_reduced_redaction_label_is_local_only_and_never_share():
    # the operator is told, in the UI, that the artifact is local-only / never share
    assert "LOCAL ONLY" in _CONSOLE and "never" in _CONSOLE.lower(), \
        "relaxed-redaction control must label itself LOCAL ONLY / never share"


def test_capture_submit_forwards_reduced_redaction_with_confirm():
    assert "reduced_redaction:reduced" in _CONSOLE, \
        "capture submit no longer forwards reduced_redaction"
    # an explicit confirm gates the relaxed path
    assert "confirm(" in _CONSOLE and "LOCAL_ONLY" in _CONSOLE, \
        "relaxed-redaction submit must confirm before sending"


def test_task_table_surfaces_local_only_badge():
    assert "t.reduced_redaction?" in _CONSOLE, \
        "task list must surface a local-only badge for relaxed captures"


# ── backend: rec flag + subprocess env injection (real start_task) ───────────
def _run_capture(reduced, label):
    import tools.cockpit_core as cc

    captured = {}

    class _R:
        returncode = 0

    def _fake_run(argv, **kw):
        captured["env"] = kw.get("env") or {}
        return _R()

    orig = cc.subprocess.run
    cc.subprocess.run = _fake_run
    try:
        rec = cc.start_task("capture", "capture_session",
                            {"url": "https://example.com/clip", "label": label,
                             "reduced_redaction": reduced})
        for _ in range(60):
            if "env" in captured:
                break
            time.sleep(0.05)
    finally:
        cc.subprocess.run = orig
    return rec, captured.get("env", {})


def test_reduced_on_injects_keep_full_both_surfaces_and_flags_rec():
    # Relaxed redaction must relax BOTH URL surfaces -- the floor governs network
    # signed URLs (network_signed_urls) and DOM-embedded signed URLs
    # (dom_embedded_urls) by SEPARATE knobs; relaxing only the network one leaves a
    # DOM-embedded signed URL (e.g. an <img>/poster ?expires=... thumbnail) refused.
    rec, env = _run_capture(True, "relaxed_on")
    assert rec["reduced_redaction"] is True
    assert env.get("BD_REDACT_NETWORK_URLS") == "keep_full"
    assert env.get("BD_REDACT_DOM_URLS") == "keep_full"


def test_floor_dom_signed_url_needs_dom_knob_not_just_network():
    """Rationale lock for the both-surfaces fix, at the floor's surface-gating
    layer (the exact mechanism the toggle controls): a signed URL on the DOM
    surface (under a dom_log key) is only ALLOWED by scan_floor_secrets when
    dom_embedded_urls is keep_full. Relaxing network_signed_urls alone leaves it
    REFUSED (the v3.66.242 GUI-toggle bug -- the toggle set only the network knob);
    relaxing both clears it. (redact_capture's DOM scrub-completeness for shapes it
    misses is a separate Phase-C concern; this pins the floor gating the fix relies
    on, so the env injection MUST relax both surfaces.)"""
    import bulk_downloader.capture_artifact_redact as car
    from bulk_downloader.redaction_profile import KEEP_FULL, KEEP_STRUCTURE

    # a signed URL sitting on the DOM surface (dom_log) -- scanned directly so the
    # test isolates the floor's per-surface allow, not the upstream scrub.
    cap = {"dom_log": [{"node": "img",
                        "url": "https://cdn.example.com/icon_1280x720.jpg"
                               "?Expires=1700000000&Signature=AbCdEf123&Key-Pair-Id=XYZ"}]}

    net_only = {"network_signed_urls": KEEP_FULL,
                "dom_embedded_urls": KEEP_STRUCTURE, "emails": "redact"}
    both = {"network_signed_urls": KEEP_FULL,
            "dom_embedded_urls": KEEP_FULL, "emails": "redact"}

    net_findings = car.scan_floor_secrets(cap, net_only)
    assert any(k == "signed_url" for _p, k in net_findings), \
        "DOM-surface signed_url must still be REFUSED when only the network knob is relaxed"
    both_findings = car.scan_floor_secrets(cap, both)
    assert not any(k == "signed_url" for _p, k in both_findings), \
        "relaxing BOTH surfaces must clear the DOM-surface signed_url"


def test_reduced_off_injects_nothing_and_rec_clean():
    rec, env = _run_capture(False, "relaxed_off")
    assert rec["reduced_redaction"] is False
    assert "BD_REDACT_NETWORK_URLS" not in env


def test_non_capture_category_never_flags_reduced():
    # a report task must never carry the capture-only relaxed flag
    import tools.cockpit_core as cc
    # report runners exist; pick any allowlisted one via the registry
    name = next(iter(cc.REPORT_RUNNERS)) if getattr(cc, "REPORT_RUNNERS", None) else None
    if not name:
        return  # no report runner to exercise; presence test covers the guard
    captured = {}

    class _R:
        returncode = 0

    def _fake_run(argv, **kw):
        captured["env"] = kw.get("env") or {}
        return _R()

    orig = cc.subprocess.run
    cc.subprocess.run = _fake_run
    try:
        rec = cc.start_task("report", name, {"reduced_redaction": True})
        for _ in range(60):
            if "env" in captured:
                break
            time.sleep(0.05)
    finally:
        cc.subprocess.run = orig
    assert rec["reduced_redaction"] is False
    assert "BD_REDACT_NETWORK_URLS" not in captured.get("env", {})


def test_floor_forgives_root_relative_signed_url_under_keep_full():
    """v3.66.244: the signed_url detector fires on a query carried by a URL OR a
    root-relative path (startswith '/'), and on signed URLs whose '://' sits past
    char 12 -- but the keep_full FORGIVENESS was gated on the stricter _url_like
    (scheme-leading / '://' in the first 12 chars). So a root-relative signed URL
    (e.g. '/media/clip.m3u8?token=...') was DETECTED yet never FORGIVEN even under
    keep_full -- the v3.66.243 'both knobs set, still refuses (3 sites)' bug. The
    skip must forgive signed_url/kv_secret for the SAME shape the detector uses.
    Hard credentials (jwt/userinfo/opaque_token) and raw (non-URL) kv secrets stay
    NEVER forgiven."""
    import bulk_downloader.capture_artifact_redact as car
    from bulk_downloader.redaction_profile import KEEP_FULL, KEEP_STRUCTURE

    net_full = {"network_signed_urls": KEEP_FULL,
                "dom_embedded_urls": KEEP_STRUCTURE, "emails": "redact"}
    strict = {"network_signed_urls": KEEP_STRUCTURE,
              "dom_embedded_urls": KEEP_STRUCTURE, "emails": "redact"}

    # root-relative signed URL on the NETWORK surface (scanned directly)
    cap_rel = {"network_log": [{"url": "/media/clip.m3u8?token=AbC123secret&Expires=1700000000"}]}
    f = car.scan_floor_secrets(cap_rel, net_full)
    assert not any(k == "signed_url" for _p, k in f), \
        "root-relative signed URL must be FORGIVEN on a keep_full network surface"
    assert not any(k == "kv_secret" for _p, k in f), \
        "the in-URL kv signing param must also be forgiven under keep_full"

    # without keep_full it is still refused -- the floor still works
    f2 = car.scan_floor_secrets(cap_rel, strict)
    assert any(k == "signed_url" for _p, k in f2), \
        "without keep_full the root-relative signed URL must STILL be refused"

    # a RAW (non-URL) kv secret -- a cookie blob, no '?'/'://'/leading '/' -- is
    # NEVER forgiven, even under keep_full (it is not URL-query signing)
    cap_cookie = {"network_log": [{"set_cookie": "sessionid=abcdef0123456789blob"}]}
    f3 = car.scan_floor_secrets(cap_cookie, net_full)
    assert any(k == "kv_secret" for _p, k in f3), \
        "a raw (non-URL) kv_secret must remain refused even under keep_full"
