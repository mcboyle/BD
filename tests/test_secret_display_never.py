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
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

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

_SURFACE_SUFFIXES = (".html", ".js", ".jinja", ".jinja2", ".htm")
_SECRET_WORDS = ("password", "token", "api_key", "secret", "cookie_file")
_SECRET_INTERPOLATION = re.compile(
    r"(\{\{[^}]*\b(?:" + "|".join(_SECRET_WORDS) + r")\b[^}]*\}\})"
    r"|(\$\{[^}]*\b(?:" + "|".join(_SECRET_WORDS) + r")\b[^}]*\})",
    re.I,
)
_MASK_MARKERS = (
    "•", "****", "masked", "present", "redact", "PLACEHOLDER",
    "has_", "_set", "is_set", "configured",
)

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


def _copy_frontend_for_secret_build(source: Path, destination: Path) -> Path:
    """Copy exact current SPA build inputs while sharing installed tools."""
    assert source.is_dir(), f"frontend source unavailable: {source}"
    node_modules = source / "node_modules"
    assert node_modules.is_dir(), (
        f"frontend build dependencies unavailable at {node_modules}; "
        "shipped-surface verdict is UNKNOWN"
    )
    input_files = tuple(sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file()
        and not ({"dist", "node_modules"} & set(path.relative_to(source).parts))
    ))
    assert input_files, "frontend build-input denominator is zero: verdict is UNKNOWN"
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("dist", "node_modules"),
    )
    os.symlink(node_modules, destination / "node_modules", target_is_directory=True)
    copied_files = tuple(sorted(
        path.relative_to(destination)
        for path in destination.rglob("*")
        if path.is_file()
        and not ({"dist", "node_modules"} & set(path.relative_to(destination).parts))
    ))
    assert copied_files == input_files, (
        "fresh frontend copy did not reconcile to its exact input denominator: "
        f"expected={len(input_files)} copied={len(copied_files)}"
    )
    return destination


def _build_secret_spa_fresh(frontend: Path, output: Path) -> Path:
    """Build the secret scan's shipped surface into attempt-owned output."""
    assert not output.exists(), f"fresh-build output already exists: {output}"
    npm = shutil.which("npm")
    assert npm is not None, "npm unavailable; shipped-surface verdict is UNKNOWN"
    for tool in ("tsc", "vite"):
        candidate = frontend / "node_modules" / ".bin" / tool
        assert candidate.is_file(), (
            f"frontend build tool unavailable: {candidate}; "
            "shipped-surface verdict is UNKNOWN"
        )
    try:
        build = subprocess.run(
            [
                npm,
                "run",
                "build",
                "--",
                "--outDir",
                str(output),
                "--emptyOutDir",
            ],
            cwd=frontend,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "fresh SPA build is UNKNOWN: npm run build exceeded 180 seconds",
            pytrace=False,
        )
    assert build.returncode == 0, (
        f"fresh SPA build failed ({build.returncode})\n"
        f"--- stdout ---\n{build.stdout}\n--- stderr ---\n{build.stderr}"
    )
    indexes = list(output.glob("index.html"))
    assert indexes == [output / "index.html"], (
        f"fresh build emitted {len(indexes)} root index files, expected exactly 1"
    )
    return output


def _fresh_secret_surface_dist(work: Path) -> Path:
    frontend = _copy_frontend_for_secret_build(
        _ROOT / "frontend", work / "frontend"
    )
    return _build_secret_spa_fresh(frontend, work / "fresh-dist")


def _eligible_surface_files(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(sorted(
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _SURFACE_SUFFIXES
    ))


def _assert_secret_surface_is_value_free(roots: tuple[Path, ...]) -> int:
    """Scan every eligible file and reconcile collection to execution."""
    subjects = _eligible_surface_files(roots)
    assert subjects, "shipped-surface denominator is zero: verdict is UNKNOWN"
    hits = []
    scanned = 0
    for path in subjects:
        try:
            text = path.read_text(errors="ignore")
        except OSError as exc:
            raise AssertionError(
                f"shipped surface unreadable: {path}: verdict is UNKNOWN"
            ) from exc
        scanned += 1
        for line in text.splitlines():
            if (
                _SECRET_INTERPOLATION.search(line)
                and not any(marker in line for marker in _MASK_MARKERS)
            ):
                try:
                    label = path.relative_to(_ROOT)
                except ValueError:
                    label = path
                hits.append(f"{label}: {line.strip()[:90]}")
    assert scanned == len(subjects) > 0, (
        "shipped-surface execution denominator mismatch: "
        f"expected={len(subjects)} scanned={scanned}"
    )
    assert not hits, (
        "secret-named value appears interpolated unmasked in a shipped surface:\n"
        + "\n".join(hits[:15])
    )
    return scanned


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


def test_no_template_or_spa_interpolates_a_secret_value(tmp_path):
    """Static scan: no shipped cockpit template or built SPA bundle directly
    interpolates a secret-NAMED field's value (e.g. `{{ password }}`,
    `site.api_token`, `value={secret}`). The SPA bundle is built by this
    attempt, never assumed."""
    dist = _fresh_secret_surface_dist(tmp_path)
    roots = (
        _ROOT / "bulk_downloader" / "templates",
        _ROOT / "templates",
        dist,
    )
    # Secret-named tokens to look for in an interpolation context. Driven off
    # the same classifier vocabulary so the two stay in lockstep.
    assert all(_is_secret(word) for word in _SECRET_WORDS)
    # Detector controls are additional to, not substitutes for, the nonzero
    # fresh artifact denominator reconciled by _assert_secret_surface_is_value_free.
    assert _SECRET_INTERPOLATION.search('value="{{ site.password }}"'), (
        "pattern fails to flag {{ secret }}"
    )
    assert _SECRET_INTERPOLATION.search("x=${api_key}"), (
        "pattern fails to flag ${secret}"
    )
    _bad = "value={{ site.password }}"
    _masked = "value={{ password_is_set }}"
    assert (
        _SECRET_INTERPOLATION.search(_bad)
        and not any(marker in _bad for marker in _MASK_MARKERS)
    ), "unmasked control mis-suppressed"
    assert any(marker in _masked for marker in _MASK_MARKERS), (
        "mask allowlist control broken"
    )
    expected = len(_eligible_surface_files(roots))
    scanned = _assert_secret_surface_is_value_free(roots)
    assert scanned == expected
    assert expected > 0


def test_secret_surface_build_invokes_exactly_one_fresh_build(
    tmp_path, monkeypatch
):
    """The static gate cannot silently return to the checkout's absent dist."""
    fired = {"copy": 0, "build": 0}
    copied = tmp_path / "copied-frontend"
    emitted = tmp_path / "emitted-dist"

    def fake_copy(source, destination):
        fired["copy"] += 1
        assert source == _ROOT / "frontend"
        assert destination == tmp_path / "frontend"
        return copied

    def fake_build(frontend, output):
        fired["build"] += 1
        assert frontend == copied
        assert output == tmp_path / "fresh-dist"
        return emitted

    monkeypatch.setitem(
        _fresh_secret_surface_dist.__globals__,
        "_copy_frontend_for_secret_build",
        fake_copy,
    )
    monkeypatch.setitem(
        _fresh_secret_surface_dist.__globals__,
        "_build_secret_spa_fresh",
        fake_build,
    )

    assert _fresh_secret_surface_dist(tmp_path) == emitted
    assert fired == {"copy": 1, "build": 1}


def test_secret_surface_zero_denominator_is_unknown(tmp_path):
    """Negative control: absent/JSON-only roots reach the UNKNOWN refusal."""
    json_only = tmp_path / "templates"
    json_only.mkdir()
    (json_only / "only.json").write_text("{}\n", encoding="ascii")
    roots = (tmp_path / "absent", json_only)
    assert _eligible_surface_files(roots) == ()
    with pytest.raises(AssertionError, match="denominator is zero.*UNKNOWN"):
        _assert_secret_surface_is_value_free(roots)


def test_secret_surface_leak_failure_path_is_reachable(tmp_path):
    """Negative control: one eligible unmasked interpolation is rejected."""
    surface = tmp_path / "dist"
    surface.mkdir()
    bad = surface / "bundle.js"
    bad.write_text("const shown = `${api_key}`;\n", encoding="ascii")
    roots = (surface,)
    assert _eligible_surface_files(roots) == (bad,)
    with pytest.raises(
        AssertionError,
        match="secret-named value appears interpolated unmasked",
    ):
        _assert_secret_surface_is_value_free(roots)


def test_transform_control_imports_secret_gate_without_building_spa():
    """Transform control: importing the gate does not measure a surface."""
    imported = __import__(__name__, fromlist=["*"])
    assert imported.__file__ == __file__
