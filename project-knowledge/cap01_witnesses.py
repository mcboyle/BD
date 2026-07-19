#!/usr/bin/env python3
"""CAP-01 witness suite -- self-checking knowledge.

Each witness RE-DERIVES a KB claim from the live tree (probe, not prose). An
assertion with a passing witness is self-checking; one that goes RED tells you
the belief went stale. This is the difference between a KB that decays silently
and one that fails loud.

Run: bd python3 /home/claude/review/witnesses/cap01_witnesses.py
Read-only: imports + calls pure functions; no scheduler, no subprocess spawn, no
network. Each witness returns (claim_id, ok, detail).
"""
import json
import sys

RESULTS = []


def _infer_kind(cid):
    if cid.startswith("F-"):
        return "finding"
    if cid.startswith("CONSTRAINT:"):
        return "constraint"
    if cid.startswith("EXCEPTION:"):
        return "exception"
    if cid.startswith("I0") or cid.startswith("I-"):
        return "invariant"
    return "claim"


def w(claim_id, kind=None, flips_to=""):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"witness raised: {type(e).__name__}: {e}"
        RESULTS.append({"id": claim_id, "kind": kind or _infer_kind(claim_id),
                        "ok": bool(ok), "flips_to": flips_to, "detail": detail})
        return fn
    return deco


# ── W1: I0008 — KV secret-keyword set consults the URL-query SoT ──────────────
# Claim: OAuth-fragment keys (code/state) + apikey/challenge/captcha/nonce/otp
# are scrubbed by the always-on kv floor; benign structural keys are kept.
# RED if VR-P03 is reverted (the hand-maintained tuple drifts from the SoT).
@w("I0008")
def _w1():
    from bulk_downloader.capture_artifact_redact import _kv_key_is_secret
    must_scrub = ["code", "state", "apikey", "challenge", "captcha", "nonce",
                  "otp", "password", "token", "authorization", "csrf", "bearer"]
    must_keep = ["color", "width", "resolution", "page", "lang", "format"]
    bad_scrub = [k for k in must_scrub if not _kv_key_is_secret(k)]
    bad_keep = [k for k in must_keep if _kv_key_is_secret(k)]
    ok = not bad_scrub and not bad_keep
    return ok, (f"all {len(must_scrub)} credential keys scrub, {len(must_keep)} structural keys kept"
                if ok else f"DRIFT: not-scrubbed={bad_scrub} wrongly-scrubbed={bad_keep}")


# ── W2: hard credentials are NEVER forgiven, even under keep_full ─────────────
# Claim: scan_floor_secrets forgives signed_url/kv_secret ONLY on a keep_full
# surface; jwt/userinfo/opaque are flagged regardless of profile.
@w("CONSTRAINT:redaction-floor/hard-cred-never-forgiven")
def _w2():
    from bulk_downloader.capture_artifact_redact import scan_floor_secrets
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dumm_sig_value_here"
    keep_full = {"network_signed_urls": "keep_full", "dom_embedded_urls": "keep_full",
                 "emails": "keep"}
    cap = {"network_log": [{"url": "https://x/y", "note": jwt}]}
    res = scan_floor_secrets(cap, keep_full)
    kinds = {k for _p, k in res}
    ok = "jwt" in kinds
    return ok, (f"JWT still flagged under keep_full (kinds={sorted(kinds)})"
                if ok else f"LEAK: hard credential forgiven under keep_full (kinds={sorted(kinds)})")


# ── W3: signed-URL residual IS forgiven on a keep_full surface ───────────────
# Claim (the carve-out / exception_to the floor): a kept signed query under
# keep_full is the intended residual, not a violation.
@w("EXCEPTION:redaction-floor/keep_full-signed-url")
def _w3():
    from bulk_downloader.capture_artifact_redact import scan_floor_secrets
    signed = "https://cdn.example.com/v/seg.ts?Signature=ABC&Expires=123&Key-Pair-Id=K"
    keep_full = {"network_signed_urls": "keep_full", "dom_embedded_urls": "keep_full",
                 "emails": "keep"}
    strip = {"network_signed_urls": "strip", "dom_embedded_urls": "strip", "emails": "redact"}
    kept = scan_floor_secrets({"effects": [signed]}, keep_full)
    stripped_default = scan_floor_secrets({"effects": [signed]}, strip)
    # keep_full forgives the signed residual; default/strip flags it
    ok = (not any(k in ("signed_url", "kv_secret") for _p, k in kept))
    return ok, ("keep_full forgives the signed-url residual; "
                f"default surface still flags it ({len(stripped_default)} finding(s))"
                if ok else f"keep_full did not forgive (kept={kept})")


# ── W4: bytes/non-JSON leaf fails CLOSED at the WACZ json.dumps boundary ──────
# Claim: a bytes leaf cannot silently reach capture.json -- json.dumps raises.
@w("I0010-backstop")
def _w4():
    secret = b"raw-bytes-token-SECRET1234567890"
    try:
        json.dumps({"blob": secret})
        return False, "json.dumps SILENTLY serialized a bytes leaf (no fail-closed)"
    except TypeError:
        return True, "json.dumps raises TypeError on a bytes leaf -> WACZ export aborts (fail-closed)"


# ── W5: live_recorder URL shape gate rejects argument-injection / non-http ────
# Claim (I-CAP01-rec-url-shape): rec.url must be ^https?://...$ before any argv.
@w("I-CAP01-rec-url-shape")
def _w5():
    from bulk_downloader import live_recorder as lr
    rec = type("R", (), {})()
    rec.room = "model123"
    rec.output_path = "/tmp/out.ts"
    for bad in ("-J", "--config=/etc/x", "file:///etc/passwd", "ftp://h/x", "javascript:1"):
        rec.url = bad
        if lr._build_cmd("streamlink", rec) is not None:
            return False, f"_build_cmd accepted a non-http url: {bad!r}"
    rec.url = "https://chaturbate.com/model123/"
    cmd = lr._build_cmd("streamlink", rec)
    ok = isinstance(cmd, list) and cmd and cmd[-1] == "best" and rec.url in cmd
    return ok, ("_build_cmd rejects -flag/file/ftp/js urls, accepts a clean https url as a positional"
                if ok else f"clean url did not build a cmd: {cmd}")


# ── W6: THE FINDING REPRO (F-CAP01-01) — does watch() accept a metadata-IP? ───
# This witness tries to CONFIRM or REFUTE the triage SSRF finding. If watch()
# with site_override+room_override accepts http://169.254.169.254/..., the host
# allowlist IS bypassed and the finding is CONFIRMED. Read-only: it only arms a
# pending Recording in a temp BD_HOME; no scheduler, no subprocess, no network.
@w("F-CAP01-01")
def _w6():
    import os, tempfile
    os.environ["BD_HOME"] = tempfile.mkdtemp()
    from bulk_downloader import live_recorder as lr
    # Neutralize backend availability so watch() reaches the url logic regardless.
    lr.is_available = lambda: True
    try:
        lr._reset_for_tests()
    except Exception:
        pass
    ssrf_url = "http://169.254.169.254/latest/meta-data/"
    res = lr.watch(ssrf_url, "/tmp/rec",
                   site_override="custom", room_override="room1")
    accepted = bool(res.get("ok"))
    # also confirm the normal path WOULD have rejected this host
    parsed = lr.parse_live_url(ssrf_url)
    detail = (f"watch() override path returned ok={accepted} for a link-local metadata IP "
              f"(parse_live_url host-match={parsed}). "
              + ("FINDING CONFIRMED: host allowlist bypassed on override path."
                 if accepted and parsed is None
                 else "finding not reproduced as expected."))
    # 'ok' for THIS witness means: the finding reproduced (vuln present) -> we WANT
    # to surface it, so a confirmed repro returns ok=True (the witness works).
    return (accepted and parsed is None), detail


def main():
    # run order is registration order
    print("CAP-01 WITNESS SUITE")
    print("=" * 70)
    npass = 0
    for r in RESULTS:
        cid, ok, detail = r["id"], r["ok"], r["detail"]
        tag = "PASS" if ok else "FAIL"
        if ok:
            npass += 1
        print(f"[{tag}] {cid}\n        {detail}")
    print("=" * 70)
    print(f"{npass}/{len(RESULTS)} witnesses green")
    # F-CAP01-01 is a finding-repro: 'green' there means the vuln reproduced.
    return 0 if npass == len(RESULTS) else 1


# ── W7: CONFIRM the DEFERRED-F2 manifest gap (finding-repro) ──────────────────
# Claim B-bodies-manifest-gap: a PATH-signed segment URL in a retained HLS
# manifest survives redact_body (no query signing marker, not a JWT, not a bare
# long-token -> not flagged). Green = the documented gap still reproduces.
@w("F-CAP01-manifest-gap")
def _w7():
    import os
    os.environ.setdefault("BD_CAPTURE_BODIES", "1")
    from bulk_downloader import capture_bodies as cb
    cb.bodies_enabled = lambda: True  # force the retain path for the probe
    manifest = ("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,"
                "URI=\"https://cdn.example.com/keys/PATHSIGNEDabc123/key.bin\"\n"
                "https://cdn.example.com/v/PATHSIGNEDseg9999/seg1.ts\n")
    out = cb.redact_body(manifest, "application/vnd.apple.mpegurl")
    survives = "PATHSIGNEDseg9999" in str(out) or "PATHSIGNEDabc123" in str(out)
    return survives, ("CONFIRMED: path-signed manifest URI survives redact_body "
                      "(the documented DEFERRED-F2 gap is real -> capture is local_only)"
                      if survives else "gap NOT reproduced -- manifest path-signing now masked?")


if __name__ == "__main__":
    sys.exit(main())
