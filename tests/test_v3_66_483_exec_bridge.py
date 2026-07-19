"""v3.66.483 X1 (plugin-v3): suffix -> interpreter table (any-language plugins).

Generalizes the node bridge (``plugin_node.py``) and the ``.py`` bridge
(``plugin_py_bridge.py``, R1) into a single interpreter-keyed exec bridge so a
plugin in ANY language that speaks the existing manifest + JSON contract loads
with ~zero new bridge code:

  * ``<interp> <file> --manifest``  -> ONE JSON line manifest.
  * ``<interp> <file> <event>``     -> reads JSON request on stdin, writes the
    result (a bare object, or the R1 ``{"ok","result"}`` envelope) on stdout.

The default interpreter table is ``INTERPRETER_BY_SUFFIX`` (``.rb`` -> ruby,
``.sh`` -> sh, ``.php`` -> php); a no-suffix executable (shebang / compiled
binary, exec-bit set) is run **directly** (the shebang chooses the interpreter).
Node (``.js``/``.mjs``) and bridge ``.py`` keep their dedicated paths unchanged.

The per-suffix interpreter is overridable via ``plugins.json`` ``interpreters``
(``{".rb": "/opt/ruby"}``) -- a plugins.json config key, parity-consistent with
``node_bin``; X1 intentionally adds NO new ``BD_*`` env var (the 482 governance
lesson). An absent interpreter is a CLEAN SKIP (never a crash), mirroring node's
``node_available`` -- and the load continues for the other plugins.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
tempfile.mkdtemp, env/module globals restored in try/finally.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(tmp, name, body, *, execbit=False):
    fp = Path(tmp) / name
    fp.write_text(body, "utf-8")
    if execbit:
        fp.chmod(fp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return fp


# ── Fixtures: POSIX-sh plugins speaking the manifest/JSON contract ─────

# A .sh processor: manifest on --manifest, a result envelope on a fire.
_SH_PROC = """\
#!/bin/sh
if [ "$1" = "--manifest" ]; then
  printf '%s\\n' '{"api_version":2,"kind":"processor","name":"sh_tagger","priority":60}'
  exit 0
fi
cat >/dev/null
printf '%s\\n' '{"ok":true,"result":{"shell":true,"lang":"sh"}}'
"""

# A no-suffix executable (shebang chooses sh) -- same protocol, run directly.
_BIN_PROC = """\
#!/bin/sh
if [ "$1" = "--manifest" ]; then
  printf '%s\\n' '{"api_version":2,"kind":"processor","name":"bin_tagger","priority":60}'
  exit 0
fi
cat >/dev/null
printf '%s\\n' '{"ok":true,"result":{"binary":true}}'
"""

# A .rb processor body (only fired if ruby is overridden to /bin/sh in a test,
# so the body is never actually parsed as ruby -- we only need the dispatch).
_RB_PROC = """\
#!/usr/bin/env ruby
puts '{"api_version":2,"kind":"processor","name":"rb_tagger","priority":60}'
"""

# A bridge .py (R1) -- co-present to prove the .py path still works (regression).
_PY_BRIDGE = """\
# bd:bridge
import json, sys
PLUGIN = {"api_version": 2, "kind": "processor", "name": "py_tagger", "priority": 50}
def handle(event, payload, ctx):
    return {"tagged": True}
if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--manifest":
        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)
    raw = sys.stdin.read()
    req = json.loads(raw) if raw.strip() else {}
    r = handle(req.get("event") or arg, req.get("payload") or {}, req.get("ctx") or {})
    sys.stdout.write(json.dumps({"ok": True, "result": r}))
"""


def test_exec_bridge_module_importable():
    """The generic exec bridge lives in its own module (declarable import edge)."""
    from bulk_downloader import plugin_exec as X
    assert hasattr(X, "load_exec_plugin")
    assert hasattr(X, "interpreter_for")
    assert isinstance(getattr(X, "INTERPRETER_BY_SUFFIX", None), dict)


def test_sh_plugin_registers_and_fires():
    """(a) A .sh plugin speaking the protocol registers + fires via `sh <file>`."""
    if not shutil.which("sh"):
        return  # sh is universally present; skip defensively
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "tagger.sh", _SH_PROC)
        res = P.load_all()
        assert res["loaded"] == 1, res
        names = [p["name"] for p in P.list_processors()]
        assert "sh_tagger" in names, names
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "sh_tagger"]
        assert got and got[0]["ok"], out
        assert got[0]["result"] == {"shell": True, "lang": "sh"}, got
    finally:
        P._plugin_dir = orig
        P.reset()


def test_no_suffix_execbit_plugin_fires_via_direct_exec():
    """(b) A no-suffix exec-bit plugin fires via direct-exec (shebang chooses interp)."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "bintool", _BIN_PROC, execbit=True)
        res = P.load_all()
        assert res["loaded"] == 1, res
        names = [p["name"] for p in P.list_processors()]
        assert "bin_tagger" in names, names
        out = P.run_processors({"site_id": "demo"})
        got = [r for r in out if r["name"] == "bin_tagger"]
        assert got and got[0]["ok"] and got[0]["result"] == {"binary": True}, out
    finally:
        P._plugin_dir = orig
        P.reset()


def test_absent_interpreter_skips_and_load_continues():
    """(c) An absent interpreter -> CLEAN SKIP (not error), and other plugins still load."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        # Force the .rb interpreter to a binary that does not exist.
        (Path(tmp) / "plugins.json").write_text(
            json.dumps({"interpreters": {".rb": "no_such_interp_xyz"}}), "utf-8")
        _write(tmp, "needs_ruby.rb", _RB_PROC)
        _write(tmp, "ok.sh", _SH_PROC)  # this one must still load
        res = P.load_all()
        assert res["loaded"] == 1, res          # only the .sh
        assert res["skipped"] >= 1, res          # the .rb skipped, not errored
        assert res["errors"] == 0, res
        names = [p["name"] for p in P.list_processors()]
        assert "sh_tagger" in names and "rb_tagger" not in names, names
    finally:
        P._plugin_dir = orig
        P.reset()


def test_interpreters_override_resolves():
    """(d) plugins.json `interpreters` overrides the default; default otherwise."""
    from bulk_downloader import plugin_exec as X
    # default table
    assert X.interpreter_for(Path("x.sh"), {}) == ["sh"]
    assert X.interpreter_for(Path("x.rb"), {}) == ["ruby"]
    # override wins
    ov = {"interpreters": {".rb": "/opt/ruby/bin/ruby", ".sh": "dash"}}
    assert X.interpreter_for(Path("x.rb"), ov) == ["/opt/ruby/bin/ruby"]
    assert X.interpreter_for(Path("x.sh"), ov) == ["dash"]
    # no-suffix executable -> direct exec (empty argv prefix)
    assert X.interpreter_for(Path("bintool"), {}) == []
    # node / .py are NOT exec-bridge's job -> None
    assert X.interpreter_for(Path("x.js"), {}) is None
    assert X.interpreter_for(Path("x.py"), {}) is None
    # an unknown suffix -> None
    assert X.interpreter_for(Path("x.txt"), {}) is None


def test_py_bridge_path_unchanged_alongside_exec():
    """Regression: a bridge .py still loads via plugin_py_bridge next to a .sh."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "py_one.py", _PY_BRIDGE)
        _write(tmp, "sh_one.sh", _SH_PROC)
        res = P.load_all()
        assert res["loaded"] == 2, res
        names = [p["name"] for p in P.list_processors()]
        assert "py_tagger" in names and "sh_tagger" in names, names
    finally:
        P._plugin_dir = orig
        P.reset()
