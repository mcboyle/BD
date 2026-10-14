"""t154-coupling-residual r1: the last two T154 cross-subsystem edges are gone.

T154 (2026-09-29) added five edges under the meter of its day (statements,
substring subsystems; across 246 -> 251). Under the current bd-coupling-meter
(O1673 M1 unique module pairs, M2 anchored subsystems) three were new module
pairs; edge #1 (detect -> runner_transport) was removed by o1673-t154-edge1.
The two that remained on ba4827d9, both runner -> core:

  runner_extractors -> constants       (lazy import of LOGOUT_CONTROL_JS)
  runner_transport  -> session_keeper  (lazy import of _LOGIN_URL_RE)

LOGOUT_CONTROL_JS now lives beside its only consumer in runner_extractors.py.
The login-URL vocabulary (SIGNIN_WORD, LOGIN_URL_RE) now lives in constants.py,
which runner_transport already imported at module level; session_keeper binds
the same objects under its old names, so every reader sees ONE regex.

Asserted three ways, without importing the product in-process (so this file
adds no edge of its own to the import graph):
  1. AST: runner_extractors imports no `.constants`; runner_transport imports no
     `.session_keeper` -- each with the import it DOES keep as positive control;
  2. bd-coupling-meter's own derivation has neither pair, with the two pairs the
     refactor relies on (runner_transport -> constants, session_keeper ->
     constants) present as positive controls;
  3. the pinned interpreter, in a subprocess, sees one compiled LOGIN_URL_RE
     object shared by constants / session_keeper / runner_transport, with the
     T154 pattern and IGNORECASE intact, and LOGOUT_CONTROL_JS defined by
     runner_extractors and absent from constants.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "bulk_downloader"
METER = ROOT / "toolchain" / "bin" / "bd-coupling-meter"

BD_GATE_SCOPE = "module"

T154_PATTERN = r"\b(?:log|sign)[\s_-]?in\b|/auth(?:/|$)|/sessions?/new\b"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(("." * n.level) + (n.module or ""))
            if n.level and not n.module:
                mods.update("." + a.name for a in n.names)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    return mods


def _meter():
    """The meter's own edge derivation, not a re-implementation of it. The script
    ends in an unguarded `sys.exit(main())`, so exec its body without that line."""
    src = METER.read_text(encoding="utf-8")
    tail = "sys.exit(main())"
    assert src.rstrip().endswith(tail), "bd-coupling-meter changed shape; re-derive this loader"
    ns = {"__name__": "bd_coupling_meter_t154_residual", "__file__": str(METER)}
    sys.path.insert(0, str(METER.parent))
    try:
        exec(compile(src.rstrip()[: -len(tail)], str(METER), "exec"), ns)
    finally:
        sys.path.remove(str(METER.parent))
    return ns


def test_runner_extractors_does_not_import_constants():
    mods = _imports(PKG / "runner_extractors.py")
    assert ".runner_util" in mods, "positive control: runner_extractors lost its runner_util import"
    assert ".constants" not in mods, (
        "T154_RESIDUAL_EDGE runner_extractors -> constants: the runner imports "
        "constants again (LOGOUT_CONTROL_JS belongs beside _spa_trailer_only)")


def test_runner_transport_does_not_import_session_keeper():
    mods = _imports(PKG / "runner_transport.py")
    assert ".constants" in mods, "positive control: runner_transport lost its constants import"
    assert ".session_keeper" not in mods, (
        "T154_RESIDUAL_EDGE runner_transport -> session_keeper: the transport reads "
        "the login-URL vocabulary from the keeper again (it is constants.LOGIN_URL_RE)")


def test_the_coupling_meter_sees_neither_residual_edge():
    meter = _meter()
    edges = set(meter["edges_from_ast"](str(ROOT)))
    assert ("runner_transport", "constants") in edges, (
        "positive control: the meter no longer derives runner_transport -> constants")
    assert ("session_keeper", "constants") in edges, (
        "positive control: the meter no longer derives session_keeper -> constants")
    assert ("runner_extractors", "constants") not in edges, (
        "T154_RESIDUAL_EDGE: bd-coupling-meter still derives runner_extractors -> constants")
    assert ("runner_transport", "session_keeper") not in edges, (
        "T154_RESIDUAL_EDGE: bd-coupling-meter still derives runner_transport -> session_keeper")
    sub = meter["subsys_of"]
    assert (sub("runner_extractors"), sub("runner_transport")) == ("runner", "runner")
    assert (sub("constants"), sub("session_keeper")) == ("core", "core")


def test_the_symbols_are_defined_where_the_refactor_put_them():
    constants = (PKG / "constants.py").read_text(encoding="utf-8")
    keeper = (PKG / "session_keeper.py").read_text(encoding="utf-8")
    extractors = (PKG / "runner_extractors.py").read_text(encoding="utf-8")
    assert "\nLOGIN_URL_RE = re.compile(" in constants, "constants.py no longer defines LOGIN_URL_RE"
    assert "\nSIGNIN_WORD = " in constants, "constants.py no longer defines SIGNIN_WORD"
    assert "\nLOGOUT_CONTROL_JS = " not in constants, (
        "T154_RESIDUAL_EDGE: LOGOUT_CONTROL_JS is back in constants.py")
    assert "\nLOGOUT_CONTROL_JS = " in extractors, "runner_extractors.py no longer defines LOGOUT_CONTROL_JS"
    assert "\n_LOGIN_URL_RE = re.compile(" not in keeper, (
        "T154_RESIDUAL_EDGE: session_keeper compiles its own _LOGIN_URL_RE again (two sources)")
    assert "from .constants import LOGIN_URL_RE as _LOGIN_URL_RE" in keeper


_PROBE = """
import json, re
from bulk_downloader import constants as c, session_keeper as sk, runner_transport as rt
from bulk_downloader import runner_extractors as rx
print(json.dumps({
    "one_regex": sk._LOGIN_URL_RE is c.LOGIN_URL_RE and rt.LOGIN_URL_RE is c.LOGIN_URL_RE,
    "one_word": sk._SIGNIN_WORD is c.SIGNIN_WORD,
    "pattern": c.LOGIN_URL_RE.pattern,
    "ignorecase": bool(c.LOGIN_URL_RE.flags & re.IGNORECASE),
    "constants_has_logout_js": hasattr(c, "LOGOUT_CONTROL_JS"),
    "extractors_logout_js": isinstance(getattr(rx, "LOGOUT_CONTROL_JS", None), str),
    "wall": [rt._is_login_wall_href(h) for h in
             ("/login-required/", "/members/sign-in", "/auth/", "/sessions/new",
              "/video/42", "/design-intent", "", None)],
}))
"""


def test_one_login_url_regex_is_shared_and_unchanged():
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["one_regex"], "T154_RESIDUAL_EDGE: session_keeper / runner_transport / constants hold different LOGIN_URL_RE objects"
    assert got["one_word"], "session_keeper._SIGNIN_WORD is not constants.SIGNIN_WORD"
    assert got["pattern"] == T154_PATTERN, got["pattern"]
    assert got["ignorecase"], "LOGIN_URL_RE lost re.I"
    assert got["constants_has_logout_js"] is False, "LOGOUT_CONTROL_JS is back on constants"
    assert got["extractors_logout_js"] is True, "runner_extractors.LOGOUT_CONTROL_JS is not a str"
    assert got["wall"] == [True, True, True, True, False, False, False, False], got["wall"]
