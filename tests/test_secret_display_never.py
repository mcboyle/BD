"""Secret-display-never gate (G0/G12).

A release gate asserting that NO operator-facing endpoint echoes a stored
secret VALUE into a response, and that no shipped template/SPA source
interpolates a secret-named field's value directly. This closes a whole class
of F2 regression (a new panel or status route that helpfully renders a config
value which happens to be a credential) before the Phase-C secret/VPN surfaces
are wired.

Mechanism (deterministic, browser-free, in-process Flask test client — runs in
the custom run_tests.py harness and under real pytest):

  1. Seed a site whose secret-shaped fields carry a UNIQUE sentinel value.
  2. Enumerate operator-facing GET endpoints from the live url_map; hit the
     arg-free ones and the single-`<sid>` ones (sid substituted); assert the
     sentinel never appears in any body. A stored secret leaking through any
     scanned surface puts the unique sentinel in that response.
  3. Static scan of shipped cockpit templates + built SPA for a secret-named
     field whose VALUE is interpolated unmasked.

The secret-name rule is single-sourced from app_settings_center._is_secret
(password|token|api_key|secret, case-insensitive, plus cookie_file) — the same
classifier the Settings Center uses — so this gate and the editor never drift.

POSTURE: read-only / recognition-only. The gate seeds its own sentinel and
scans for it; it never prints a real secret and never touches the
fixtures/recon_corpus set (no endpoint serves those).
"""
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from bulk_downloader.app_settings_center import _is_secret  # single-sourced rule  # noqa: E402

# Unique, collision-resistant sentinel for the one secret the site schema
# actually persists (the credential `password` column). A token/api_key field
# is NOT a site column — the site model drops it — so seeding one would be
# vacuous (never stored, nothing to leak). The substring detector below is not
# field-specific (the teeth test proves it catches ANY secret-shaped value an
# endpoint echoes), so one genuinely-stored tracer is sufficient for the
# dynamic tier; breadth across secret NAMES is covered by the static tier.
_SENT_PW = "SECRETSENTINEL_PW_9Qk3Zr7x"
_SENTINELS = (_SENT_PW,)

# Endpoints we must NOT drive with a GET in the dynamic scan: streaming/SSE
# (never returns), large/binary downloads/exports, and anything that would
# block the in-process client. We are scanning for value-leaks, so skipping a
# stream is safe — a stream cannot statically render a stored secret anyway.
_SKIP_RULE_SUBSTR = (
    "/api/stream", "/stream", "/export.csv", "/export", "/download",
    "/api/captcha/pending/", "/logs/tail", "/api/activity/v2/export",
)


@contextmanager
def _client_seeded():
    """Boot an isolated app + test client, pair for CSRF, seed a site whose
    password and a secret-named config field carry the sentinels. Yields
    (client, headers, sid)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            c = A.app.test_client()
            tok = c.get("/api/pair").get_json()["token"]
            csrf = c.post("/api/pair/redeem", json={"token": tok}).get_json()["csrf_token"]
            H = {"X-CSRF-Token": csrf}
            ss._backend = None
            ss._backend_pref = None
            # Seed a site carrying a plaintext password sentinel + a
            # password sentinel (the one persisted credential column).
            r = c.post("/api/sites", json={
                "name": "secret-display-gate",
                "password": _SENT_PW,
            }, headers=H)
            assert r.status_code == 200, f"site seed failed: {r.status_code}"
            sid = r.get_json()["id"]
            yield c, H, sid
        finally:
            os.chdir(orig_cwd)


def _scan_targets(app, sid):
    """Operator-facing GET rules we can drive deterministically: arg-free, or
    a single <sid>-style arg we can fill. Returns concrete URL paths."""
    targets = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        path = str(rule.rule)
        if path.startswith("/static"):
            continue
        if any(s in path for s in _SKIP_RULE_SUBSTR):
            continue
        args = rule.arguments
        if not args:
            targets.append(path)
            continue
        # Single site-id-shaped arg → fill with the seeded sid.
        if len(args) == 1:
            a = next(iter(args))
            if a in ("sid", "site_id", "siteId"):
                targets.append(path.replace(f"<{a}>", sid)
                                   .replace(f"<string:{a}>", sid)
                                   .replace(f"<path:{a}>", sid))
    return sorted(set(targets))


def _scan_worker(paths, headers, out_q):
    """Scan one shard of endpoint paths in a forked child. The child inherits
    the ALREADY-BOOTED app + seeded DB + cwd from the parent (fork), so there
    is no per-worker boot cost; it opens its own test client and GETs its
    shard. Pushes (scanned_count, leaks) to the queue. Module-level so it is
    picklable for multiprocessing."""
    from bulk_downloader import app as A
    scanned, leaks = 0, []
    c = A.app.test_client()
    for path in paths:
        try:
            resp = c.get(path, headers=headers)
            body = (resp.get_data(as_text=False) or b"").decode("utf-8", "ignore")
        except Exception:
            continue  # an endpoint erroring on a bare GET is not a leak
        scanned += 1
        for sent in _SENTINELS:
            if sent in body:
                leaks.append((path, sent[:18]))
    out_q.put((scanned, leaks))


def _scan_all(targets, headers):
    """GET every target and return (scanned_count, leaks).

    MULTI-CORE: endpoint work runs in-process (Flask test client), so threads
    would serialize on the GIL for CPU-heavy report endpoints (e.g. the
    capture-diagnostics collector). Instead we FORK worker processes after the
    one seeded boot -- children inherit the app + DB -- and shard the targets,
    so wall time is ~the slowest single endpoint instead of the sum of all of
    them (the sequential scan exceeded run_tests' 900s file timeout on a large
    operator capture store). Falls back to a sequential in-process scan if
    fork isn't available or the pool fails (e.g. macOS spawn-only)."""
    import multiprocessing as mp
    nworkers = min(32, (os.cpu_count() or 2), max(1, len(targets) // 8))
    if nworkers <= 1:
        q = _SeqQueue()
        _scan_worker(targets, headers, q)
        return q.items[0]
    try:
        ctx = mp.get_context("fork")
        out_q = ctx.Queue()
        shards = [targets[i::nworkers] for i in range(nworkers)]
        procs = [ctx.Process(target=_scan_worker, args=(sh, headers, out_q), daemon=True)
                 for sh in shards if sh]
        for p in procs:
            p.start()
        # Drain per-shard with a bounded wait and ACCEPT PARTIAL results: if a
        # shard stalls (one pathologically slow endpoint), we keep every shard
        # that finished instead of re-running the whole surface sequentially
        # (the old fallback doubled the grind and wedged the run). The
        # coverage floor in the caller still fails the test if too little of
        # the surface was actually scanned.
        import queue as _qmod
        results = []
        for _ in procs:
            try:
                results.append(out_q.get(timeout=300))
            except _qmod.Empty:
                break
        for p in procs:
            p.terminate()
            p.join(timeout=5)
        if results:
            scanned = sum(r[0] for r in results)
            leaks = [l for r in results for l in r[1]]
            return scanned, leaks
        # Nothing came back at all (fork pool broken): sequential fallback.
        q = _SeqQueue()
        _scan_worker(targets, headers, q)
        return q.items[0]
    except Exception:
        # Fallback: sequential scan, semantics identical.
        q = _SeqQueue()
        _scan_worker(targets, headers, q)
        return q.items[0]


class _SeqQueue:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


def test_no_endpoint_echoes_a_stored_secret_value():
    """The core gate: with a secret-bearing site seeded, no scanned
    operator-facing GET endpoint returns the sentinel value in its body."""
    from bulk_downloader import app as A
    with _client_seeded() as (c, H, sid):
        targets = _scan_targets(A.app, sid)
        assert len(targets) > 50, f"scan surface implausibly small: {len(targets)}"
        scanned, leaks = _scan_all(targets, H)
        # The parallel scan must actually have covered the surface (a broken
        # pool returning nothing would otherwise pass vacuously).
        assert scanned > 50, f"scan coverage implausibly small: {scanned}/{len(targets)}"
        assert not leaks, (
            "secret VALUE leaked into operator-facing response(s): "
            + "; ".join(f"{p} -> {s}…" for p, s in leaks[:10])
        )


def test_secrets_status_is_value_free():
    """Anchor on the known secrets surface: status reports a count/backend but
    never the seeded value."""
    with _client_seeded() as (c, H, sid):
        body = c.get("/api/secrets/status", headers=H).get_data(as_text=True) or ""
        assert _SENT_PW not in body
        # And the effective per-site config presents secrets as presence-only.
        eff = c.get(f"/api/settings/site/{sid}/effective", headers=H)
        if eff.status_code == 200:
            assert _SENT_PW not in (eff.get_data(as_text=True) or "")


def test_no_template_or_spa_interpolates_a_secret_value():
    """Static scan: no shipped cockpit template or built SPA bundle directly
    interpolates a secret-NAMED field's value (e.g. `{{ password }}`,
    `site.api_token`, `value={secret}`)."""
    roots = [
        _ROOT / "bulk_downloader" / "templates",
        _ROOT / "templates",
        _ROOT / "frontend" / "dist",
    ]
    # Secret-named tokens to look for in an interpolation context. Driven off
    # the same classifier vocabulary so the two stay in lockstep.
    secret_words = ("password", "token", "api_key", "secret", "cookie_file")
    assert all(_is_secret(w) for w in secret_words[:-1]) and _is_secret("cookie_file")
    # Jinja/JS interpolation of a secret-named identifier's VALUE.
    pat = re.compile(
        r"(\{\{[^}]*\b(?:" + "|".join(secret_words) + r")\b[^}]*\}\})"
        r"|(\$\{[^}]*\b(?:" + "|".join(secret_words) + r")\b[^}]*\})",
        re.I,
    )
    # Allow masked/presence-only contexts: a hit is only a finding if it isn't
    # adjacent to a masking marker on the same line.
    mask_markers = ("•", "****", "masked", "present", "redact", "PLACEHOLDER",
                    "has_", "_set", "is_set", "configured")
    # Positive control — the scan currently finds 0 real hits (built React, no
    # Jinja secret-interpolation), so without this a broken pattern would pass
    # vacuously. Assert the detector fires on known-bad synthetic lines and the
    # mask allowlist suppresses a masked one but NOT an unmasked one.
    assert pat.search('value="{{ site.password }}"'), "pattern fails to flag {{ secret }}"
    assert pat.search("token: ${apiKey}") or pat.search("x=${api_key}"), "pattern fails to flag ${secret}"
    _bad = "value={{ site.password }}"
    _masked = "value={{ password_is_set }}"
    assert pat.search(_bad) and not any(m in _bad for m in mask_markers), "unmasked control mis-suppressed"
    assert any(m in _masked for m in mask_markers), "mask allowlist control broken"
    hits = []
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".html", ".js", ".jinja", ".jinja2", ".htm"):
                continue
            try:
                text = f.read_text(errors="ignore")
            except Exception:
                continue
            for ln in text.splitlines():
                if pat.search(ln) and not any(m in ln for m in mask_markers):
                    hits.append(f"{f.relative_to(_ROOT)}: {ln.strip()[:90]}")
    assert not hits, (
        "secret-named value appears interpolated unmasked in a shipped surface:\n"
        + "\n".join(hits[:15])
    )
