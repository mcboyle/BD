"""Method-aware SPA<->inventory matching (gui_parity_inventory).

Locks in the v3.66.190 parity-scanner fix: the matcher must qualify by HTTP
method so a shared-path method pair (POST + GET on the same route) is no longer
mutually credited (the method-blind phantom). The fetch() method-option must be
read, not assumed GET (the SPA deletes a site via raw fetch{method:"DELETE"}).

Hermetic: builds a tiny synthetic frontend/src so the assertions don't drift
with the live SPA tree.
"""
import tempfile
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent


_SNIPPET = """
// list reads (GET) on shared paths whose write sibling is NOT called here
const q1 = apiGet<SharesList>("/api/shares");
const q2 = apiGet<UserTemplatesList>("/api/user_templates");

// create POST on a path the SPA does NOT GET (vpn list is read via /status)
apiPost("/api/vpn/tunnels", { name: "x" });

// edit/delete on the tunnel detail path; the detail GET is never called
apiPut(`/api/vpn/tunnels/${encodeURIComponent(id)}`, body);
apiDelete(`/api/vpn/tunnels/${encodeURIComponent(id)}`);

// dispatcher: trailing var bound to a static literal table -> POST start/stop
const acts = [{ action: "start" }, { action: "stop" }];
apiPost(`/api/vpn/tunnels/${encodeURIComponent(id)}/${action}`, {});

// site delete via RAW fetch with a method option (must NOT be read as GET)
fetch(`/api/sites/${encodeURIComponent(sid)}`, {
  method: "DELETE",
  credentials: "same-origin",
});

// multi-line + typed call still detected
apiGet<HealthLite>(
  "/api/health/v2"
);

// SSE is always GET
const es = new EventSource("/api/stream");
"""


def _build_fake_root():
    root = tempfile.mkdtemp(prefix="parity_ma_")
    src = Path(root) / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "App.tsx").write_text(_SNIPPET, encoding="utf-8")
    return root


def test_method_qualified_harvest():
    root = _build_fake_root()
    eps, meth = g._spa_wiring(root)

    # path-only fallback set still contains every referenced path
    for p in ("/api/shares", "/api/user_templates", "/api/vpn/tunnels",
              "/api/vpn/tunnels/*", "/api/sites/*", "/api/health/v2", "/api/stream"):
        assert p in eps, p

    # method pairs: verbs are correctly attributed
    assert ("GET", "/api/shares") in meth
    assert ("GET", "/api/user_templates") in meth
    assert ("POST", "/api/vpn/tunnels") in meth
    assert ("PUT", "/api/vpn/tunnels/*") in meth
    assert ("DELETE", "/api/vpn/tunnels/*") in meth
    assert ("POST", "/api/vpn/tunnels/*/*") in meth   # dispatcher start/stop
    assert ("GET", "/api/health/v2") in meth          # typed + multiline
    assert ("GET", "/api/stream") in meth             # EventSource

    # the fetch DELETE must be DELETE, never GET
    assert ("DELETE", "/api/sites/*") in meth
    assert ("GET", "/api/sites/*") not in meth

    # the write siblings the SPA never calls must NOT appear as method pairs
    assert ("POST", "/api/shares") not in meth
    assert ("POST", "/api/user_templates") not in meth
    assert ("GET", "/api/vpn/tunnels") not in meth


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


def test_phantom_broken_no_regression():
    """End-to-end against the live tree: the six known phantoms read unwired,
    while their genuinely-called siblings stay wired."""
    root = str(_REPO)
    inv = g.build(root)
    items = inv["items"]

    # phantoms -> must be unwired (method-blind matcher used to credit these)
    assert _wired(items, "POST /api/shares") is False
    assert _wired(items, "POST /api/user_templates") is False
    assert _wired(items, "GET /api/library/<int:lid>") is False

    # genuinely-called writes -> must stay wired
    assert _wired(items, "POST /api/vpn/tunnels") is True
    # v3.66.336: the per-site editor (SiteSettings.tsx) now GETs the tunnel list to
    # populate the vpn_tunnel_id <select>, so the LIST GET is genuinely wired (was a
    # method-blind phantom).
    assert _wired(items, "GET /api/vpn/tunnels") is True
    # v3.66.769 (6B leak-test wiring): the VPN page's Leak tests card now GETs the
    # single-tunnel detail via apiGet(`/api/vpn/tunnels/${id}`) to show status
    # alongside the leak results, so the per-tunnel GET is now GENUINELY wired
    # (it was the last method-blind phantom on this path).
    assert _wired(items, "GET /api/vpn/tunnels/<tunnel_id>") is True
    assert _wired(items, "PATCH|PUT /api/vpn/tunnels/<tunnel_id>") is True
    assert _wired(items, "DELETE /api/vpn/tunnels/<tunnel_id>") is True
    assert _wired(items, "DELETE /api/sites/<sid>") is True   # raw-fetch DELETE
    # v3.66.310 Phase 4.1: the per-site settings editor (SiteSettings.tsx) writes via
    # apiPut("/api/sites/<sid>"), so this bare PUT is now GENUINELY wired (was a phantom
    # the method-blind matcher credited while no real PUT caller existed).
    assert _wired(items, "PUT /api/sites/<sid>") is True

    # Phase 29 AI-teach pair (propose + commit) — both wired together so the
    # operator_facing_unwired tally reaches 0; neither may silently regress.
    assert _wired(items, "POST /api/ai/diff_repair") is True
    assert _wired(items, "POST /api/sites/<sid>/learned/apply_repairs") is True


def test_match_is_a_tightening_only():
    """Patched spa_wired must be a strict subset of the path-only verdict:
    method-awareness can only remove phantoms, never add a false positive."""
    root = str(_REPO)
    eps, _ = g._spa_wiring(root)
    inv = g.build(root)
    for it in inv["items"]:
        ep = g._endpoint_path(it)
        if ep and it.get("spa_wired"):
            assert g._norm_ep(ep) in eps, ep   # wired => path was referenced


def test_non_spa_surface_excluded_from_operator_gaps():
    """Option-B denominator cleanup: cockpit-native + extension data-plane routes
    are operator-facing but NOT SPA-wireable, so they drop out of the
    operator_facing_unwired tally (the actionable SPA backlog)."""
    root = str(_REPO)
    gge = g.build(root)["counts"]["gui_gated_endpoints"]
    assert gge["operator_facing_unwired"] == 0, gge
    # v3.66.757: +1 (8 -> 9) for /cockpit/api/takeover/<sid>/input (MOD-1 remote
    # takeover). It is operator-facing + gui-gated but lives on the cockpit
    # non-SPA surface, so it is wired from the SPA (TakeoverViewer apiPost) yet
    # not spa_wired-credited (the SPA-wiring scan tracks /api/ literals only).
    # It is therefore EXCLUDED from operator gaps (operator_facing_unwired stays
    # 0), not a real SPA backlog item -- the tripwire just grew by one.
    assert gge["non_spa_surface_unwired"] == 9, gge

    # cockpit-native and the two extension data routes are non-SPA surfaces
    assert g._is_non_spa_surface("/cockpit/api/shell/open")
    assert g._is_non_spa_surface("/api/secrets/extension/pair")
    assert g._is_non_spa_surface("/api/secrets/extension/fetch_one")
    # operator-management extension routes stay genuine SPA gaps
    assert not g._is_non_spa_surface("/api/secrets/extension/pair_issue")
    assert not g._is_non_spa_surface("/api/secrets/extension/revoke")
    assert not g._is_non_spa_surface("/api/secrets/configure")


def test_secrets_t18_writes_stay_spa_wired():
    """Parity-lock (v3.66.191): the secrets-vault + T18 stream WRITE surfaces
    wired into the SPA this release must stay spa_wired. Guards a silent
    regression that would re-orphan an operator-facing write (drop a row's
    /api/... literal, break the scanner, and the surface goes dark with no
    test catching it)."""
    items = g.build(str(_REPO))["items"]
    wired = {
        it["command_or_endpoint"].split(" ", 1)[-1]
        for it in items
        if it.get("spa_wired") is True
    }
    required = [
        "/api/secrets/configure",
        "/api/secrets/unlock",
        "/api/secrets/lock",
        "/api/secrets/change_password",
        "/api/secrets/migrate",
        "/api/secrets/delete",
        "/api/secrets/import_file",
        "/api/secrets/import_apply",
        "/api/secrets/extension/pair_issue",
        "/api/secrets/extension/revoke",
        "/api/stream/rotate_secret",
        "/api/stream/token/<int:hid>",
    ]
    missing = [e for e in required if e not in wired]
    assert not missing, f"regressed (no longer spa_wired): {missing}"
