"""Current SPA root, namespace, asset, redirect, and method contract."""
from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_rootflip_"))

_DIST = _REPO_ROOT / "frontend" / "dist" / "index.html"


def _fresh_client():
    from bulk_downloader.app import app as flask_app
    return flask_app.test_client()


class _BuiltAssetRefParser(HTMLParser):
    """Collect every local executable/style asset reference in built HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        reference = None
        if tag == "script":
            reference = attributes.get("src")
        elif tag == "link":
            rel = set((attributes.get("rel") or "").casefold().split())
            if rel & {"modulepreload", "preload", "stylesheet"}:
                reference = attributes.get("href")
        if not reference or reference.startswith("//"):
            return
        parsed = urlsplit(reference)
        if not parsed.scheme and not parsed.netloc:
            self.references.append(reference)


def _copy_frontend_for_build(source: Path, destination: Path) -> Path:
    """Copy build inputs while sharing only the installed dependency tree."""
    assert source.is_dir(), f"frontend source unavailable: {source}"
    node_modules = source / "node_modules"
    assert node_modules.is_dir(), (
        f"frontend build dependencies unavailable at {node_modules}; "
        "effective Vite base is UNKNOWN"
    )
    required = [source / "package.json", source / "vite.config.ts", source / "src"]
    assert all(path.exists() for path in required), (
        f"frontend build-input denominator is incomplete: {required}"
    )
    assert sum(1 for path in (source / "src").rglob("*") if path.is_file()) > 0
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("dist", "node_modules"),
    )
    os.symlink(node_modules, destination / "node_modules", target_is_directory=True)
    assert (destination / "vite.config.ts").read_bytes() == (
        source / "vite.config.ts"
    ).read_bytes()
    return destination


def _build_spa_fresh(
    frontend: Path,
    output: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> Path:
    """Run the shipped build script into an output owned by this attempt."""
    assert not output.exists(), f"fresh-build output already exists: {output}"
    npm = shutil.which("npm")
    assert npm is not None, "npm unavailable; effective Vite base is UNKNOWN"
    for tool in ("tsc", "vite"):
        candidate = frontend / "node_modules" / ".bin" / tool
        assert candidate.is_file(), (
            f"frontend build tool unavailable: {candidate}; effective Vite base "
            "is UNKNOWN"
        )
    build_env = dict(os.environ)
    build_env.pop("BD_ROW229_BASE", None)
    if extra_env:
        build_env.update(extra_env)
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
            env=build_env,
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


def _built_asset_references(dist: Path) -> list[str]:
    parser = _BuiltAssetRefParser()
    parser.feed((dist / "index.html").read_text(encoding="utf-8"))
    references = parser.references
    assert references, (
        "fresh Vite build emitted zero local executable/style asset references; "
        "effective base is UNKNOWN"
    )
    return references


def _assert_built_assets_are_rooted(dist: Path, references: list[str]) -> None:
    assert references, "asset-reference denominator is zero"
    off_mount = [ref for ref in references if not ref.startswith("/assets/")]
    assert off_mount == [], (
        "fresh Vite build emitted asset references off the Flask root mount: "
        f"{off_mount}"
    )
    resolved = []
    for reference in references:
        url_path = unquote(urlsplit(reference).path)
        artifact = dist / url_path.lstrip("/")
        assert artifact.is_file(), (
            f"fresh Vite asset reference has no emitted file: {reference}"
        )
        resolved.append(artifact)
    assert len(resolved) == len(references) > 0


def test_root_serves_spa():
    """GET / returns the built SPA index (200, html, #root div).
    If dist is absent (pristine sandbox), the actionable 503
    not-built surface answers instead — same contract /m2 had."""
    c = _fresh_client()
    r = c.get("/")
    if not _DIST.is_file():
        assert r.status_code == 503
        assert r.headers.get("X-BD-M2-Status") == "not-built"
        return
    assert r.status_code == 200
    assert "html" in (r.headers.get("Content-Type") or "")
    assert b'<div id="root">' in r.data, "SPA root div missing from /"


def test_root_warms_session_cookie():
    """The _bootstrap_session hook covers the SPA shell: a fresh
    GET / sets bd_session so the first /api/csrf finds a valid
    session and the first POST never races a Set-Cookie."""
    c = _fresh_client()
    r = c.get("/")
    set_cookies = [v for k, v in r.headers.items() if k.lower() == "set-cookie"]
    assert any("bd_session=" in v for v in set_cookies), \
        "fresh GET / did not warm the session cookie"


def test_spa_client_route_falls_back_to_index():
    """An unrouted non-reserved path (a React Router client route)
    returns the SPA index so the router can claim it."""
    if not _DIST.is_file():
        return  # 503 surface covered in test_root_serves_spa
    c = _fresh_client()
    for path in ("/queue", "/settings", "/sites/3"):
        r = c.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code}"
        assert b'<div id="root">' in r.data, f"{path} did not get SPA HTML"


def test_reserved_namespaces_404_not_spa_html():
    """An unrouted path under a reserved infra namespace must be a
    real 404 — never SPA HTML masquerading as success."""
    c = _fresh_client()
    for path in ("/api/definitely_not_a_route_zz",
                 "/cockpit/definitely_not_a_page_zz",
                 "/legacy/definitely_not_a_thing_zz"):
        r = c.get(path)
        assert r.status_code == 404, f"{path}: {r.status_code}"


def test_missing_asset_is_404_not_spa_html():
    """An asset-looking path (file extension) not present in dist is a
    404 when built; a clean source checkout reports the explicit 503 state."""
    c = _fresh_client()
    r = c.get("/assets/definitely-not-a-real-bundle-zz.js")
    if not _DIST.is_file():
        assert r.status_code == 503
        assert r.headers.get("X-BD-M2-Status") == "not-built"
    else:
        assert r.status_code == 404


def test_real_asset_served_from_root(tmp_path, monkeypatch):
    """Build now, then prove every emitted bundle ref uses Flask's root mount."""
    frontend = _copy_frontend_for_build(
        _REPO_ROOT / "frontend", tmp_path / "frontend"
    )
    dist = _build_spa_fresh(frontend, tmp_path / "fresh-dist")
    references = _built_asset_references(dist)
    _assert_built_assets_are_rooted(dist, references)

    import bulk_downloader.app as app_module

    monkeypatch.setattr(app_module, "_M2_DIST_ROOT", dist)
    c = _fresh_client()
    served = 0
    for reference in references:
        response = c.get(reference)
        assert response.status_code == 200, (
            f"fresh asset {reference} not served from the Flask root mount"
        )
        artifact = dist / unquote(urlsplit(reference).path).lstrip("/")
        assert response.data == artifact.read_bytes(), (
            f"Flask returned bytes other than fresh asset {reference}"
        )
        served += 1
    assert served == len(references) > 0

    missing = c.get("/assets/row229-definitely-missing.js")
    assert missing.status_code == 404, (
        "negative control did not reach Flask's missing-asset refusal"
    )


def test_real_asset_gate_invokes_one_fresh_build(tmp_path, monkeypatch):
    """The behavioral gate cannot silently regress to reading shipped dist."""
    fired = {"copy": 0, "build": 0, "references": 0, "root_check": 0}
    fixture_frontend = tmp_path / "frontend-fixture"
    fixture_dist = tmp_path / "fresh-dist"

    def fake_copy(source, destination):
        fired["copy"] += 1
        assert source == _REPO_ROOT / "frontend"
        assert destination == tmp_path / "frontend"
        return fixture_frontend

    def fake_build(frontend, output):
        fired["build"] += 1
        assert fired == {
            "copy": 1,
            "build": 1,
            "references": 0,
            "root_check": 0,
        }
        assert frontend == fixture_frontend
        assert output == fixture_dist
        (fixture_dist / "assets").mkdir(parents=True)
        (fixture_dist / "assets" / "fresh.js").write_bytes(b"fresh bytes\n")
        (fixture_dist / "index.html").write_text(
            '<div id="root"></div><script src="/assets/fresh.js"></script>\n',
            encoding="utf-8",
        )
        return fixture_dist

    def fake_references(dist):
        fired["references"] += 1
        assert fired == {
            "copy": 1,
            "build": 1,
            "references": 1,
            "root_check": 0,
        }
        assert dist == fixture_dist
        return ["/assets/fresh.js"]

    def fake_root_check(dist, references):
        fired["root_check"] += 1
        assert fired == {
            "copy": 1,
            "build": 1,
            "references": 1,
            "root_check": 1,
        }
        assert dist == fixture_dist
        assert references == ["/assets/fresh.js"]

    monkeypatch.setitem(
        test_real_asset_served_from_root.__globals__,
        "_copy_frontend_for_build",
        fake_copy,
    )
    monkeypatch.setitem(
        test_real_asset_served_from_root.__globals__,
        "_build_spa_fresh",
        fake_build,
    )
    monkeypatch.setitem(
        test_real_asset_served_from_root.__globals__,
        "_built_asset_references",
        fake_references,
    )
    monkeypatch.setitem(
        test_real_asset_served_from_root.__globals__,
        "_assert_built_assets_are_rooted",
        fake_root_check,
    )

    test_real_asset_served_from_root(tmp_path, monkeypatch)
    assert fired == {"copy": 1, "build": 1, "references": 1, "root_check": 1}


def test_transform_control_imports_gate_without_judging_effective_base():
    """Mutation transform control: importing this module does not build Vite."""
    imported = importlib.import_module(__name__)
    assert imported.__file__ == __file__


def test_legacy_route_removed():
    """Phase 4 (v3.66.334): the legacy shell AND its /legacy route were
    removed outright (dev-only tool, no external bookmarks to preserve).
    "legacy" stays a reserved prefix so /legacy + /legacy/ resolve to a
    clean 404 rather than falling through to the SPA catch-all."""
    for path in ("/legacy", "/legacy/"):
        c = _fresh_client()
        r = c.get(path)
        assert r.status_code == 404, \
            f"{path}: expected 404 (route removed), got {r.status_code}"


def test_m2_shim_preserves_deep_links_and_query():
    c = _fresh_client()
    r = c.get("/m2/sites/3?tab=runs")
    assert r.status_code == 302
    assert r.headers.get("Location") == "/sites/3?tab=runs"
    r = c.get("/m2")
    assert r.status_code == 302
    assert r.headers.get("Location") == "/"


def test_mobile_shims_redirect_to_root():
    c = _fresh_client()
    for path in ("/m", "/m/", "/m/ops", "/m/ops/"):
        r = c.get(path)
        assert r.status_code == 302, f"{path}: {r.status_code}"
        assert r.headers.get("Location") == "/", \
            f"{path} -> {r.headers.get('Location')!r}, expected /"


def test_api_routes_not_shadowed_by_catch_all():
    """Werkzeug gives <path:> rules lowest priority — explicit rules
    must keep winning. /api/health is the canary."""
    c = _fresh_client()
    r = c.get("/api/health")
    assert r.status_code in (200, 503)  # 503 only if db not ok
    body = r.get_json() or {}
    assert "version" in body, "/api/health shadowed by the SPA catch-all?"


def _strip_ts_comments(text: str) -> str:
    r"""Remove // and /* */ comments so a scan judges CODE, not prose.

    Measured 2026-08-24: `base: "/app/",  // was base: "/"` re-roots the SPA
    while the old `re.search(r'base:\s*["\']/["\']', vite_cfg)` matched the
    COMMENT and stayed green. At row 202 the behavioural sibling still read an
    already-built frontend/dist/index.html, so it did not re-derive from the
    changed config. Row 229 adds that fresh-build proof independently. Strings
    are left intact; the subject here is an assignment, and stripping quotes
    would destroy it."""
    out, i, n_, in_s = [], 0, len(text), None
    while i < n_:
        c = text[i]
        if in_s:
            out.append(c)
            if c == "\\" and i + 1 < n_:
                out.append(text[i + 1]); i += 2; continue
            if c == in_s:
                in_s = None
            i += 1; continue
        if c in "\"'`":
            in_s = c; out.append(c); i += 1; continue
        if c == "/" and i + 1 < n_ and text[i + 1] == "/":
            while i < n_ and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n_ and text[i + 1] == "*":
            i += 2
            while i + 1 < n_ and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2; continue
        out.append(c); i += 1
    return "".join(out)


def test_frontend_re_rooted_in_source():
    """vite base and router basename are both "/" -- the single coupling
    between build output and Flask mount.

    This cheap source floor remains comment-stripped and requires one literal
    assignment for a direct diagnostic. The effective value is independently
    re-derived by the fresh build in `test_real_asset_served_from_root`; the
    source-evasive computed override has its own executable negative control."""
    vite_cfg = _strip_ts_comments(
        (_REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8"))
    live = re.findall(r'\bbase\s*:\s*(["\'][^"\']*["\'])', vite_cfg)
    assert len(live) == 1, (
        f"expected exactly one live `base:` assignment, found {live}")
    assert re.fullmatch(r'["\']/["\']', live[0]), (
        f"vite base is {live[0]}, not \"/\"; the SPA is re-rooted off the "
        "Flask mount")
    main_tsx = _strip_ts_comments(
        (_REPO_ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8"))
    assert 'basename="/"' in main_tsx


def test_a_commented_out_vite_base_does_not_satisfy_the_scan():
    """EVASION FIXTURE for the measured re-rooting. The old gate passed on
    exactly this input."""
    evaded = 'export default defineConfig({\n  base: "/app/",  // was base: "/"\n})\n'
    assert re.search(r'base:\s*["\']/["\']', evaded), (
        "the fixture no longer reproduces the shape that defeated the old "
        "gate, so it is pinning nothing")
    stripped = _strip_ts_comments(evaded)
    live = re.findall(r'\bbase\s*:\s*(["\'][^"\']*["\'])', stripped)
    assert live == ['"/app/"'], live
    assert not re.fullmatch(r'["\']/["\']', live[0]), (
        "a commented-out base still reads as \"/\" after stripping")


def test_a_source_evasive_effective_base_is_rejected(tmp_path):
    """A fresh build must beat both the literal scan and stale dist evidence."""
    source_frontend = _REPO_ROOT / "frontend"
    fixture_frontend = tmp_path / "frontend"
    shutil.copytree(
        source_frontend,
        fixture_frontend,
        ignore=shutil.ignore_patterns("dist", "node_modules"),
    )
    os.symlink(
        source_frontend / "node_modules",
        fixture_frontend / "node_modules",
        target_is_directory=True,
    )

    config = fixture_frontend / "vite.config.ts"
    original = config.read_text(encoding="utf-8")
    anchor = '  base: "/",\n'
    assert original.count(anchor) == 1, "fixture mutation anchor must be unique"
    export_anchor = "export default defineConfig({\n"
    assert original.count(export_anchor) == 1
    assert original.endswith("});\n")
    config.write_text(
        original.replace(export_anchor, "const config = {\n", 1)[:-4]
        + "};\n"
        + "if (process.env.BD_ROW229_BASE) {\n"
        + "  config.base = process.env.BD_ROW229_BASE;\n"
        + "}\n"
        + "export default defineConfig(config);\n",
        encoding="utf-8",
    )
    live_literals = re.findall(
        r'\bbase\s*:\s*(["\'][^"\']*["\'])',
        _strip_ts_comments(config.read_text(encoding="utf-8")),
    )
    assert live_literals == ['"/"'], (
        "fixture must preserve the exact literal evidence accepted by the old scan"
    )

    stale_dist = fixture_frontend / "dist"
    (stale_dist / "assets").mkdir(parents=True)
    (stale_dist / "assets" / "stale.js").write_text(
        "export const stale = true;\n", encoding="utf-8"
    )
    (stale_dist / "index.html").write_text(
        '<div id="root"></div><script src="/assets/stale.js"></script>\n',
        encoding="utf-8",
    )
    stale_refs = re.findall(
        r'(?:src|href)="(/assets/[^"]+)"',
        (stale_dist / "index.html").read_text(encoding="utf-8"),
    )
    assert stale_refs == ["/assets/stale.js"]
    assert (stale_dist / stale_refs[0].lstrip("/")).is_file()

    fresh_dist = tmp_path / "fresh-dist"
    _build_spa_fresh(
        fixture_frontend,
        fresh_dist,
        extra_env={"BD_ROW229_BASE": "/app/"},
    )
    fresh_refs = _built_asset_references(fresh_dist)
    assert all(ref.startswith("/app/assets/") for ref in fresh_refs), fresh_refs
    assert (stale_dist / stale_refs[0].lstrip("/")).is_file(), (
        "fresh fixture build must not erase the stale conforming control"
    )
    with pytest.raises(
        AssertionError,
        match="fresh Vite build emitted asset references off the Flask root mount",
    ):
        _assert_built_assets_are_rooted(fresh_dist, fresh_refs)


def test_bootstrap_hook_covers_spa_root():
    """_bootstrap_session warms the cookie for the SPA shell at /. The
    legacy shell was removed in P4 (v3.66.334), so / is the only shell
    and the hook must no longer reference /legacy."""
    src = (_REPO_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    pos = src.find("def _bootstrap_session")
    assert pos > 0
    body = src[pos:pos + 2000]
    assert '"/"' in body, "hook must gate on / (the SPA shell)"
    assert '"/legacy"' not in body, \
        "legacy shell removed in P4 — hook must not reference /legacy"


# ── v3.66.204: method-semantics parity (the 203 stash-suite regression) ──
# The root catch-all accepts GET on every path, which (at 203) made a
# GET on a POST-only route fall into serve_spa_root's reserved-prefix
# 404 instead of Werkzeug's native 405 — 8 on-stash test failures, all
# one class. 204 restores full pre-flip method semantics.


def test_get_on_post_only_routes_is_405_with_allow():
    """The exact 8-failure class from the 203 on-stash suite: GET on a
    POST-only route answers 405 + Allow (pre-flip Werkzeug semantics),
    never the reserved-prefix 404 and never SPA HTML. Paths mirror the
    suites that pinned it (d3_u3/u5/u10/u11, t2, t9, v3_66_8)."""
    c = _fresh_client()
    for path in ("/api/dev/vision_test",
                 "/api/dev/fixture_site/start",
                 "/api/queue/v2/add_url",
                 "/api/queue/v2/cancel",
                 "/api/queue/v2/bulk_cancel",
                 "/api/sites/v2/bulk",
                 "/api/sites/whatever/jobs/reorder",
                 "/api/dashboard/v2/resolve"):
        r = c.get(path)
        assert r.status_code == 405, f"GET {path}: {r.status_code} (want 405)"
        allow = r.headers.get("Allow") or ""
        assert "POST" in allow, f"GET {path}: Allow={allow!r} missing POST"
        assert "GET" not in allow, \
            f"GET {path}: Allow={allow!r} must not advertise GET"


def test_wrong_nonget_method_on_real_endpoint_keeps_405_clean_allow():
    """DELETE on a POST-only route: native 405 preserved, Allow lists
    only the explicit methods (+OPTIONS) — not the catch-all's GET."""
    c = _fresh_client()
    r = c.delete("/api/queue/v2/add_url")
    assert r.status_code == 405
    allow = r.headers.get("Allow") or ""
    assert "POST" in allow and "GET" not in allow, f"Allow={allow!r}"


def test_nonget_to_unknown_reserved_or_asset_path_is_404():
    """Pre-flip parity: a non-GET to a path with NO explicit rule in a
    reserved namespace (or asset-shaped) is a plain 404 — the 405 the
    catch-all's path-match manufactured at 203 is converted back."""
    c = _fresh_client()
    assert c.post("/api/definitely_not_a_route_zz").status_code == 404
    assert c.put("/api/definitely_not_a_route_zz").status_code == 404
    assert c.post("/assets/definitely-not-real-zz.js").status_code == 404


def test_nonget_to_spa_page_path_is_405_allow_get():
    """A non-GET to a genuine SPA page path answers 405 with Allow: GET
    — exactly what the /m2 mount answered pre-flip (POST /m2/queue)."""
    c = _fresh_client()
    r = c.post("/queue")
    assert r.status_code == 405, f"POST /queue: {r.status_code}"
    allow = r.headers.get("Allow") or ""
    assert "GET" in allow, f"Allow={allow!r} missing GET"
