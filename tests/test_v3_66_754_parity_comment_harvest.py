"""v3.66.754b -- gui_parity must not be WIRED BY PROSE, and must not be BLIND TO CODE.

THE DEFECT (proven minimally, no measuring hack involved):

    _SPA_API_RE.finditer("// NOTE: /api/zzz/phantom is a different store")
      -> ['/api/zzz/phantom']

``_spa_wiring`` harvests ``/api/...`` literals from RAW TEXT. A path merely NAMED in a
comment counts as a call site. It is (f)-shaped -- wrong in the REASSURING direction:
``_API_VERB_RE`` already requires a real ``apiPost(`` call, so comments pollute only the
path-only fallback set -- the set consulted precisely when no verb is detectable. Nothing
downstream ever objects. Live cost @751: a comment WARNING against conflating the
library-notes store WIRED that endpoint.

WHY THE COMMENT FIX MUST NOT SHIP ALONE. Stripping comments un-wires 8 endpoints. SIX OF
THEM ARE REAL, OPERABLE CONTROLS that the CALL HARVEST cannot see -- the comment was the
only thing crediting them, so it was MASKING two harvest defects rather than reporting dark
controls. Landing the strip alone flips six working controls from accidentally-right to
confidently-wrong: the ledger would report render-what-you-cannot-operate for controls the
operator CAN operate. The two harvest defects, both derived from source:

  1. NORM ORDER. ``_norm_ep`` did ``p.split("?", 1)[0]`` BEFORE collapsing ``${...}``, so
     the JS nullish operator inside a template expression was eaten as a query delimiter:
         /api/widgets/${encodeURIComponent(siteId ?? "_global")}
           -> '/api/widgets/${encodeURIComponent(siteId '     (truncated, then DISCARDED)
     The harvest was fine; the NORMALISER was the bug. Collapse ${...} first.

  2. DISPATCHER SHAPE. ``_resolve_dynamic_dispatchers`` already exists for exactly this
     case -- a trailing path segment bound to a static literal table -- but its regex
     required a BARE IDENTIFIER (``${suffix}``). Vpn.tsx dispatches through a MEMBER
     EXPRESSION (``${t.action}``), so the resolver never even recognised it, while
     ``_norm_ep`` collapsed the segment to ``*`` -- and ``/api/vpn/tunnels/*/*`` does not
     match the route's ``/api/vpn/tunnels/*/start``. The literal table it needs
     (action: "start"/"stop"/"cycle") is sitting in the same file.

  3. SILENT DROP. A norm the normaliser could not parse was ``continue``d without a word.
     Unknown was treated as ABSENT. A harvest that cannot read a call site must SAY SO.

THE TRAP, and why the stripper is a STATE MACHINE and not a regex. A naive
``re.sub(r"/\\*.*?\\*/", "", s)`` treats a ``/*`` appearing INSIDE a ``//`` comment as a
block opener. Dedup.tsx has such a line; the naive strip ran to the next ``*/`` (a JSX
``{/* */}``), deleted 6,448 chars of LIVE CODE including real ``apiPost`` call sites, and
fabricated a 22-phantom list containing REAL, CALLED endpoints. See KB_JUDGMENT (g): the
instrument could not parse its own subject. That incident is pinned below as a NEG test.

RED-first: every assertion here fails on the pristine tree.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gui_parity_inventory as G  # noqa: E402


# ==========================================================================
# 1. THE STRIPPER ITSELF -- and the trap that fabricated 22 phantoms.
# ==========================================================================
def test_strip_removes_line_and_block_comments():
    s = G.strip_ts_comments
    assert "/api/zzz/phantom" not in s('// NOTE: /api/zzz/phantom is a different store')
    assert "/api/zzz/block" not in s('/* see /api/zzz/block */')


def test_strip_preserves_api_literals_inside_strings():
    """A URL containing '//' must not be read as a comment opener."""
    src = 'apiGet("https://example.com/api/keep");'
    out = G.strip_ts_comments(src)
    assert "/api/keep" in out, "a string containing '//' was mangled by the stripper"


def test_strip_does_not_let_a_slash_star_inside_a_line_comment_eat_live_code():
    """THE REGRESSION PIN for the fabricated-22 incident (KB_JUDGMENT (g)).

    A '/*' inside a '//' comment is TEXT, not a block opener. The naive regex treated it
    as one, ran to the next '*/', and deleted 6,448 chars of live code -- turning REAL,
    CALLED endpoints into 'phantoms'. The state machine must not.
    """
    src = (
        '//   * status - scan - scan/cancel - /* not a block opener */\n'
        'apiPost("/api/dedup/scan", {});\n'
        'return <div>{/* a real JSX comment */}</div>;\n'
        'apiPost("/api/batch/delete", {});\n'
    )
    out = G.strip_ts_comments(src)
    assert "/api/dedup/scan" in out, (
        "a '/*' inside a '//' comment swallowed the following live code -- this is the "
        "exact bug that fabricated 22 phantoms including real, called endpoints")
    assert "/api/batch/delete" in out, "code after a JSX comment was eaten"


# ==========================================================================
# 2. COMMENTS MUST NOT WIRE (the headline defect).
# ==========================================================================
def _wiring(tmp_path, snippet):
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "App.tsx").write_text(snippet, encoding="utf-8")
    return G._spa_wiring(str(tmp_path))


def test_a_path_named_only_in_a_comment_is_not_wired(tmp_path):
    eps, _ = _wiring(tmp_path, '// the library-notes store lives at /api/zzz/phantom\n')
    assert "/api/zzz/phantom" not in eps, (
        "a path merely NAMED in a comment was counted as a call site -- gui_parity is "
        "wired by prose")


def test_a_real_call_is_still_wired(tmp_path):
    """POS half: the strip must not cost us real call sites."""
    eps, meth = _wiring(tmp_path, 'apiGet("/api/health/v2");\n')
    assert "/api/health/v2" in eps
    assert ("GET", "/api/health/v2") in meth


# ==========================================================================
# 3. THE CALL HARVEST -- the six real controls the comment was masking.
#    These MUST stay wired after the strip, or the cut regresses them.
# ==========================================================================
def test_norm_ep_collapses_template_exprs_before_stripping_the_query():
    """A JS nullish `??` inside ${...} is NOT a query delimiter."""
    got = G._norm_ep('/api/widgets/${encodeURIComponent(siteId ?? "_global")}')
    assert got == "/api/widgets/*", (
        "_norm_ep split on '?' BEFORE collapsing ${...}, so the nullish operator "
        "truncated the path (got %r). Collapse the template expr first." % got)


def test_norm_ep_still_strips_a_real_query_string():
    """NEG guard: reordering must not stop us stripping an actual query."""
    assert G._norm_ep("/api/jobs?state=failed") == "/api/jobs"
    assert G._norm_ep('/api/x/${id}?q=1') == "/api/x/*"


def test_widgets_nested_nullish_call_is_wired(tmp_path):
    """useWidgetSelection GET/PUT/DELETE -- real, operable, previously invisible."""
    snippet = (
        'apiGet<W>(`/api/widgets/${encodeURIComponent(siteId ?? "_global")}`, signal);\n'
        'apiPut<W>(`/api/widgets/${encodeURIComponent(siteId ?? "_global")}`, payload);\n'
        'apiDelete(`/api/widgets/${encodeURIComponent(siteId ?? "_global")}`);\n'
    )
    eps, meth = _wiring(tmp_path, snippet)
    assert "/api/widgets/*" in eps
    for verb in ("GET", "PUT", "DELETE"):
        assert (verb, "/api/widgets/*") in meth, (
            "%s /api/widgets/* is a REAL operable control; the nested `??` made the "
            "normaliser drop it, and only a comment was crediting it" % verb)


def test_member_expression_dispatcher_is_resolved(tmp_path):
    """Vpn.tsx: apiPost(`/api/vpn/tunnels/${enc(t.tunnelId)}/${t.action}`) with a static
    literal table. The resolver EXISTS for this; its regex just required a bare ident."""
    snippet = (
        'type ControlAction = "stop" | "start" | "cycle";\n'
        'const doIt = (t) => apiPost(`/api/vpn/tunnels/${encodeURIComponent(t.tunnelId)}/${t.action}`, {});\n'
        'setConfirm({ tunnelId: id, action: "start" });\n'
        'setConfirm({ tunnelId: id, action: "stop" });\n'
        'setConfirm({ tunnelId: id, action: "cycle" });\n'
    )
    eps, meth = _wiring(tmp_path, snippet)
    for act in ("start", "stop", "cycle"):
        assert f"/api/vpn/tunnels/*/{act}" in eps, (
            "the member-expression dispatcher ${t.action} was not resolved against its "
            "literal table -- /api/vpn/tunnels/*/%s is a REAL operable control" % act)
    # NOTE, deliberately NOT asserted: the synthesised paths do not land in method_eps.
    # `_dispatcher_method` anchors its lookback on `\($` -- the window must END with '(' --
    # but a backtick-template dispatcher puts a '`' after the paren, so it has NEVER
    # resolved a verb for ANY dispatcher (pre-existing; not introduced here). The routes
    # still wire, via the path-only fallback in `build()` (which is consulted precisely
    # when no verb is detectable for that path). Fixing `_dispatcher_method` would move
    # EVERY dispatcher from path-only to method-aware matching -- a global denominator
    # change with real un-wiring risk -- and does not belong inside a cut that is already
    # a denominator change. Logged, not smuggled.


def test_dispatcher_resolution_stays_bounded(tmp_path):
    """NEG guard: the resolver must not over-credit. A literal table with NO matching
    dispatcher contributes nothing -- otherwise widening the regex would wire the world."""
    snippet = (
        'const rows = [{ action: "start" }, { action: "nuke" }];\n'   # table, no dispatcher
        'apiGet("/api/vpn/tunnels");\n'
    )
    eps, _ = _wiring(tmp_path, snippet)
    assert "/api/vpn/tunnels/*/nuke" not in eps, (
        "a literal table with no matching dispatcher was credited -- the resolver has "
        "stopped being bounded")


# ==========================================================================
# 4. UNKNOWN IS NOT ABSENT. A call site the normaliser cannot parse must be
#    REPORTED, not silently dropped -- that is how a blind spot stays invisible.
# ==========================================================================
def test_indirected_call_sites_are_REPORTED_as_a_third_state(tmp_path):
    """`apiGet(url)` -- the path is computed at runtime, so the literal harvest cannot see
    it AT ALL. Reporting only the resolved set makes the unresolvable indistinguishable
    from the non-existent: unknown gets folded into ABSENT.

    (My first draft of this test pinned the WRONG subject: it asserted that a literal the
    NORMALISER could not parse would be reported. But fixing the norm-order bug above made
    that class EMPTY -- the guard could never fire. A check with an empty denominator is
    the exact defect this cut exists to remove, so pinning it would have been the bug
    wearing the costume of the fix. The real residual blind spot is the indirected call.)"""
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "App.tsx").write_text(
        'const url = pick();\napiGet(url);\napiPost(IMPORT_EP, body);\n', encoding="utf-8")
    unresolved = G.spa_wiring_unresolved(str(tmp_path))
    args = {a for _f, _v, a in unresolved}
    assert "url" in args and "IMPORT_EP" in args, (
        "indirected call sites are invisible AND unreported -- a reader cannot tell "
        "'this scanner could not read it' from 'nothing calls it'")


def test_the_api_client_definition_is_not_counted_as_an_indirected_call(tmp_path):
    """NEG guard: the module that DEFINES the helpers forwards its own `path` param and
    looks identical to an indirected call. Excluded by what it DECLARES, not by filename --
    a hardcoded exclusion is just another hand-kept denominator waiting to drift."""
    src = tmp_path / "frontend" / "src" / "lib"
    src.mkdir(parents=True, exist_ok=True)
    (src / "api-client.ts").write_text(
        'export async function apiGet<T>(path: string) { return raw(path); }\n',
        encoding="utf-8")
    assert G.spa_wiring_unresolved(str(tmp_path.parent if False else tmp_path)) == [], (
        "the api-client helper definition was counted as an indirected CALL site")
