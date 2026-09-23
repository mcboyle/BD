"""Row 1053 (RESCOPED per RULING-2338): no bulk_downloader child inherits stdin.

A child that prompts -- ffmpeg "File exists. Overwrite? [y/N]", sudo, ssh --
blocks forever reading an inherited open stdin; with stdin=DEVNULL it reads
EOF and exits. The census below walks every subprocess call in the package,
however the module is spelled (``subprocess.run``, ``import subprocess as
_sp``, ``from subprocess import run``) and however the function reaches its
call (a parameter default, ``runner or subprocess.run``, ``self._run``), and
requires stdin=, input=, -nostdin, or a kwargs splat that carries stdin. A
literal None is the inherit default spelled out, so it guards nothing; a
reference the census cannot follow to a call fails closed.

T66 dropped this row (CI job 107014040928): subprocess.DEVNULL opens
/dev/null O_RDWR, and app_health.build_identity runs on the /api/health boot
path that bd-opv verifies inside its resource boundary, so
tests/test_row_282_bd_opv_isolates_every_store.py saw a write escape the
sandbox. That site now closes git's stdin with an empty pipe (input="");
the build_identity tests below pin EOF, the exact git calls and zero
write-mode opens.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bulk_downloader import container_repair
from bulk_downloader.subprocess_helpers import isolated_popen_kwargs

# Its subject is every subprocess call site in the package: a tree property.
BD_GATE_SCOPE = "repo-wide"

_PKG = Path(__file__).resolve().parents[1] / "bulk_downloader"
_CALLS = {"run", "Popen", "check_output", "check_call", "call"}
# Exact population at this cut (rule 8): a new call site must be guarded AND
# counted here, so the census cannot silently shrink. 68 literal
# ``subprocess.<call>`` sites (render_diagnostics' default Popen and
# import_profiler x2 among them)
# + 2 aliased ones (ytdlp_extractor ``_subprocess.run``, app_widgets_api
# ``_sp.run``) + 5 reached through a binding (ai_boot_observation,
# netns_isolation x2, ollama_boot_probe, provider_resolve_impl/youtube).
_EXPECTED_SITES = 75  # row 1046: payload_verifier ffprobe subprocess.run (stdin=DEVNULL)


def _not_none(value: ast.AST) -> bool:
    """stdin=None / input=None is the inherit default spelled out."""
    return not (isinstance(value, ast.Constant) and value.value is None)


def _dict_carries_stdin(fn: ast.AST, name: str) -> bool:
    for node in ast.walk(fn):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            v = node.value
            if isinstance(v, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "stdin" and _not_none(val)
                    for k, val in zip(v.keys, v.values)):
                return True
            if isinstance(v, ast.Call) and getattr(v.func, "id", None) == "dict" and any(
                    k.arg == "stdin" and _not_none(k.value) for k in v.keywords):
                return True
    return False


def _guarded(call: ast.Call, src: str, scope: ast.AST) -> bool:
    if "-nostdin" in (ast.get_source_segment(src, call) or ""):
        return True
    for kw in call.keywords:
        if kw.arg in ("stdin", "input") and _not_none(kw.value):
            return True
        if kw.arg is None:
            v = kw.value
            if isinstance(v, ast.Call) and getattr(v.func, "id", None) == "isolated_popen_kwargs":
                return True
            if isinstance(v, ast.Name) and _dict_carries_stdin(scope, v.id):
                return True
    return False


def _spawn_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the subprocess module, and to its _CALLS by from-import."""
    modules, functions = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == "subprocess" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions |= {a.asname or a.name for a in node.names if a.name in _CALLS}
    return modules, functions


def _spawn_ref(node: ast.AST, modules: set[str], functions: set[str]) -> str | None:
    """``node`` reads a subprocess spawn function, called or not."""
    if not isinstance(getattr(node, "ctx", None), ast.Load):
        return None
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in modules and node.attr in _CALLS):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name) and node.id in functions:
        return node.id
    return None


def _hints(tree: ast.AST) -> set[ast.AST]:
    """Nodes inside annotations: ``proc: subprocess.Popen`` spawns nothing."""
    roots = [n.annotation for n in ast.walk(tree) if isinstance(n, (ast.arg, ast.AnnAssign))]
    roots += [n.returns for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return {node for root in roots if root is not None for node in ast.walk(root)}


def _enclosing(node: ast.AST, parents: dict, kinds) -> ast.AST | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, kinds):
            return node
    return None


def _uses(ref: ast.AST, parents: dict, tree: ast.AST) -> list[ast.Call]:
    """The calls that invoke ``ref``: directly, as ``(spawn or ref)(...)``, or
    through the name it is bound to by a parameter default or an assignment,
    including a ``self.<attr> = <name>`` hop inside its class."""
    top = ref
    while isinstance(parents.get(top), (ast.BoolOp, ast.IfExp)):
        top = parents[top]
    parent = parents.get(top)
    if isinstance(parent, ast.Call) and parent.func is top:
        return [parent]
    name, scope = None, tree
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is top:
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            name = targets[0].id
            scope = _enclosing(parent, parents, (ast.FunctionDef, ast.AsyncFunctionDef)) or tree
    elif isinstance(parent, ast.arguments):
        positional = parent.posonlyargs + parent.args
        pairs = list(zip(positional[len(positional) - len(parent.defaults):], parent.defaults))
        pairs += list(zip(parent.kwonlyargs, parent.kw_defaults))
        name = next((arg.arg for arg, default in pairs if default is top), None)
        scope = parents[parent]
    if name is None:
        return []
    calls = [n for n in ast.walk(scope) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == name]
    attrs = {t.attr for n in ast.walk(scope) if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.Name) and n.value.id == name
             for t in n.targets if isinstance(t, ast.Attribute)
             and isinstance(t.value, ast.Name) and t.value.id == "self"}
    cls = _enclosing(scope, parents, ast.ClassDef)
    if attrs and cls is not None:
        calls += [n for n in ast.walk(cls) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                  and n.func.value.id == "self" and n.func.attr in attrs]
    return calls


def census(src: str, label: str) -> tuple[int, list[str]]:
    tree = ast.parse(src)
    modules, functions = _spawn_names(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    hints = _hints(tree)
    sites: dict[ast.Call, str] = {}
    open_sites = []
    for node in ast.walk(tree):
        spawn = _spawn_ref(node, modules, functions)
        if spawn is None or node in hints:
            continue
        calls = _uses(node, parents, tree)
        if not calls:
            open_sites.append(f"{label}:{node.lineno} {spawn} unresolved")
        for call in calls:
            sites.setdefault(call, spawn if call.func is node
                             else f"{spawn} via {ast.unparse(call.func)}")
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for call, spawn in sites.items():
        scope = next((s for s in scopes if s.lineno <= call.lineno <= s.end_lineno
                      and any(c is call for c in ast.walk(s))), tree)
        if not _guarded(call, src, scope):
            open_sites.append(f"{label}:{call.lineno} {spawn}")
    return len(sites), open_sites


def test_every_bulk_downloader_subprocess_call_closes_stdin():
    total, open_sites = 0, []
    for path in sorted(_PKG.rglob("*.py")):
        t, o = census(path.read_text(encoding="utf-8"), str(path.relative_to(_PKG.parent)))
        total += t
        open_sites += o
    assert open_sites == [], f"ROW1053-STDIN-OPEN {len(open_sites)} site(s) inherit stdin: {open_sites}"
    assert total == _EXPECTED_SITES, f"ROW1053-CENSUS-COUNT {total} != {_EXPECTED_SITES}"


# Positive control (rule 7): the base shapes of the five product Popen sites
# (container_repair, stream_verifier, upscale_detector x2, db_replication),
# the app_file GUI launch and an unguarded **kwargs splat are all flagged.
_BASE_SHAPES = '''
import subprocess
def a(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
def b(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
def c(path):
    return subprocess.Popen(["litestream", "replicate", "-config", path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
def d(p):
    subprocess.Popen(["explorer", str(p)], close_fds=True)
def e(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)
def f(cmd):
    kw: dict = {"start_new_session": True}
    return subprocess.Popen(cmd, **kw)
'''


def test_census_flags_the_base_shapes():
    total, open_sites = census(_BASE_SHAPES, "base")
    assert total == 6
    assert [s.split(":")[1].split()[0] for s in open_sites] == ["4", "7", "10", "14", "16", "19"]


def test_census_accepts_each_guard_form():
    src = '''
import subprocess
def g(cmd, data):
    subprocess.run(cmd, stdin=subprocess.DEVNULL)
    subprocess.run(cmd, input=data)
    subprocess.run(["ffmpeg", "-nostdin", "-i", "x"])
    subprocess.Popen(cmd, **isolated_popen_kwargs())
    kw = {"stdin": subprocess.DEVNULL}
    subprocess.Popen(cmd, **kw)
    ann: dict = dict(stdin=subprocess.DEVNULL)
    subprocess.Popen(cmd, **ann)
'''
    assert census(src, "neg") == (6, [])


# Positive control for the alias and None rules: the two shapes the literal
# ``subprocess.`` census could not see at 6cba183e (ytdlp_extractor:178,
# app_widgets_api:621 -- line 5 here), a from-import spelling, and explicit
# inherit as a keyword, a dict value and input=None are flagged; the same
# spellings carrying a real guard (lines 14-17) are accepted.
_ALIAS_SHAPES = '''
import subprocess as _sp
from subprocess import run as spawn, Popen
def a(cmd):
    return _sp.run(cmd, capture_output=True, text=True, timeout=120)
def b(cmd):
    return spawn(cmd, stdin=None)
def c(cmd):
    kw = {"stdin": None}
    return Popen(cmd, **kw)
def d(cmd):
    return spawn(cmd, input=None, stdout=_sp.PIPE)
def e(cmd, data):
    _sp.run(cmd, stdin=_sp.DEVNULL, capture_output=True)
    spawn(cmd, input=data)
    kw = dict(stdin=_sp.DEVNULL)
    return Popen(cmd, **kw)
'''


def test_census_resolves_aliased_spawns_and_rejects_explicit_inherit():
    total, open_sites = census(_ALIAS_SHAPES, "alias")
    assert total == 7
    assert set(open_sites) == {"alias:5 _sp.run", "alias:7 spawn",
                               "alias:10 Popen", "alias:12 spawn"}
    assert len(open_sites) == 4


# Positive control for bound spawns: the shapes a call-only census could not
# see at 6cba183e -- a parameter default (ai_boot_observation), ``runner or
# subprocess.run`` (netns_isolation x2), a default kept on self
# (ollama_boot_probe) and ``(spawn or subprocess.Popen)(...)``
# (render_diagnostics) -- are followed to their calls and flagged; the
# assignment shape of provider_resolve_impl/youtube with a guard is accepted;
# a reference with no call to follow fails closed; annotations spawn nothing.
_BOUND_SHAPES = '''
import subprocess
def a(cmd, run=subprocess.run):
    return run(cmd, capture_output=True)
def b(cmd, runner=None):
    r = runner or subprocess.run
    return r(cmd, capture_output=True)
def c(cmd, spawn=None):
    return (spawn or subprocess.Popen)(cmd)
class D:
    def __init__(self, *, run=subprocess.run):
        self._run = run
    def go(self, cmd):
        return self._run(cmd, capture_output=True)
def e(cmd, _run=None):
    if _run is None:
        _run = subprocess.run
    return _run(cmd, stdin=subprocess.DEVNULL)
def f(cmds):
    return list(map(subprocess.run, cmds))
def g(proc: subprocess.Popen) -> subprocess.Popen:
    return proc
'''


def test_census_follows_bound_spawns_to_their_calls():
    total, open_sites = census(_BOUND_SHAPES, "bound")
    assert total == 5
    assert set(open_sites) == {"bound:4 subprocess.run via run",
                               "bound:7 subprocess.run via r",
                               "bound:9 subprocess.Popen via spawn or subprocess.Popen",
                               "bound:14 subprocess.run via self._run",
                               "bound:20 subprocess.run unresolved"}
    assert len(open_sites) == 5


def test_isolated_popen_kwargs_closes_stdin():
    assert isolated_popen_kwargs()["stdin"] is subprocess.DEVNULL


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_overwrite_prompt_does_not_wedge_on_open_stdin(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    src, dst = tmp_path / "src.wav", tmp_path / "dst.wav"
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc",
                    "-t", "0.2", "-y", str(src)], check=True, stdin=subprocess.DEVNULL)
    dst.write_bytes(b"operator-owned")
    # fd 0 = a pipe held open and never written: exactly what an unguarded
    # child inherits when the runner's own stdin is a live terminal/pipe.
    r, w = os.pipe()
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        rc, _out, err = container_repair._run_isolated(
            [ffmpeg, "-hide_banner", "-i", str(src), str(dst)], timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail("ROW1053-WEDGE ffmpeg blocked on its overwrite prompt reading inherited stdin")
    finally:
        os.dup2(saved, 0)
        for fd in (saved, r, w):
            os.close(fd)
    # ffmpeg answers EOF with "Not overwriting - exiting" (rc 0 on 6.x).
    assert "Not overwriting" in err, (rc, err[-300:])
    assert dst.read_bytes() == b"operator-owned"


# build_identity is the only subprocess site bd-opv's OPV-HEALTH reaches
# (measured: two `git rev-parse HEAD` spawns, nothing else). The probe runs in
# a child interpreter so its audit hook dies with it. The fake git drains
# stdin to EOF before answering, so an inherited open stdin wedges it until
# build_identity's own 10 s timeout: the identity it returns proves EOF.
_FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"
_FAKE_WHEN = "2026-01-02T03:04:05+00:00"
_FAKE_GIT = f"""#!/bin/sh
while IFS= read -r _line; do :; done
case "$*" in
  "rev-parse HEAD") echo {_FAKE_SHA} ;;
  "log -1 --format=%cI") echo {_FAKE_WHEN} ;;
  *) exit 2 ;;
esac
"""
# The write classifier is test_row_282's: a write-mode string, or any of
# O_WRONLY/O_RDWR/O_CREAT/O_TRUNC/O_APPEND, on a path argument.
_PROBE = r'''
import json, os, subprocess, sys
from bulk_downloader import app_health
WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
writes, spawned = [], []
def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if ((isinstance(mode, str) and any(m in mode for m in "wax+"))
                or (isinstance(flags, int) and flags & WRITE)):
            writes.append(os.path.abspath(os.fsdecode(args[0])))
    elif event == "subprocess.Popen":
        spawned.append(list(args[1]))
held_r, held_w = os.pipe()  # fd 0 = a pipe nobody ever writes or closes
os.dup2(held_r, 0)
sys.addaudithook(audit)
if sys.argv[2] == "control":  # the T66 defect's shape, outside the product
    subprocess.run(["git", "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                   cwd=sys.argv[1], capture_output=True, text=True, timeout=10)
    identity = None
else:
    identity = app_health.build_identity(sys.argv[1])
print(json.dumps({"identity": identity, "writes": writes, "spawned": spawned}))
'''


def _run_child_probe(tmp_path: Path, source: str, *args: str,
                     path_prefix: Path | None = None):
    """Run ``source`` in a child interpreter on this checkout (HOME, BD_HOME,
    TMPDIR inside tmp_path) and return its last stdout line as JSON."""
    for directory in (tmp_path / "home", tmp_path / "state"):
        directory.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if key not in {"BD_INSTALL_DIR", "BD_HOME", "HOME", "PYTHONPATH", "TMPDIR"}}
    env.update(HOME=str(tmp_path / "home"), BD_HOME=str(tmp_path / "state"),
               TMPDIR=str(tmp_path), PYTHONPATH=str(_PKG.parent),
               PYTHONDONTWRITEBYTECODE="1", BD_DISABLE_KEEPALIVE="1",
               LC_ALL="C")  # host locale must not decide the child (test_v3_66_1197)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{os.environ.get('PATH', '')}"
    done = subprocess.run([sys.executable, "-c", source, *args],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          cwd=str(tmp_path), env=env, timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-1000:] + done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _probe_build_identity(tmp_path: Path, mode: str) -> dict:
    fake_bin, install = tmp_path / "bin", tmp_path / "install"
    for directory in (fake_bin, install):
        directory.mkdir()
    git = fake_bin / "git"
    git.write_text(_FAKE_GIT, encoding="utf-8")
    git.chmod(0o755)
    return _run_child_probe(tmp_path, _PROBE, str(install), mode, path_prefix=fake_bin)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd 0 and a /bin/sh fake git")
def test_build_identity_hands_git_eof_and_opens_nothing_for_writing(tmp_path):
    report = _probe_build_identity(tmp_path, "product")
    # EOF, not the held-open pipe: the fake git answered inside the timeout.
    assert report["identity"] == {"sha": _FAKE_SHA[:12], "built_at": _FAKE_WHEN,
                                  "source": "git"}, f"ROW1053-WEDGE {report}"
    # Exact seam: both git calls ran, in order, through the fake.
    assert report["spawned"] == [["git", "rev-parse", "HEAD"],
                                 ["git", "log", "-1", "--format=%cI"]]
    assert report["writes"] == [], (
        f"ROW1053-SANDBOX-WRITE build_identity opened {report['writes']} for writing")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd 0 and a /bin/sh fake git")
def test_write_classifier_sees_the_devnull_stdin_shape(tmp_path):
    """Positive control: the same probe flags stdin=DEVNULL's O_RDWR open."""
    report = _probe_build_identity(tmp_path, "control")
    assert report["spawned"] == [["git", "rev-parse", "HEAD"]]
    assert report["writes"] == [os.devnull]


# Seam of an aliased site (``_subprocess.run``, invisible to a literal
# ``subprocess.`` census): ytdlp_extractor._default_run, the yt-dlp -j info
# probe. Its child waits up to 3 s for stdin to turn readable: a held-open
# inherited pipe never does ("OPEN"); DEVNULL is at EOF at once ("EOF").
_EOF_CHILD = ("import select, sys; ready = select.select([sys.stdin], [], [], 3)[0]; "
              "print('EOF' if ready and sys.stdin.read() == '' else 'OPEN')")
_INFO_PROBE = r'''
import json, os, subprocess, sys
held_r, held_w = os.pipe()  # fd 0 = a pipe nobody ever writes or closes
os.dup2(held_r, 0)
child = [sys.executable, "-c", sys.argv[1]]
if sys.argv[2] == "control":  # the base shape: no stdin guard, fd 0 inherited
    done = subprocess.run(child, capture_output=True, text=True, timeout=60)
    print(json.dumps([done.returncode, done.stdout, done.stderr]))
else:
    from bulk_downloader import ytdlp_extractor
    print(json.dumps(ytdlp_extractor._default_run(child)))
'''


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd 0 and select() on a pipe")
def test_ytdlp_info_probe_hands_its_child_eof_not_the_inherited_stdin(tmp_path):
    result = _run_child_probe(tmp_path, _INFO_PROBE, _EOF_CHILD, "product")
    assert result == [0, "EOF\n", ""], (
        f"ROW1053-STDIN-OPEN ytdlp_extractor._default_run child read the inherited stdin: {result}")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd 0 and select() on a pipe")
def test_eof_probe_sees_an_inherited_open_stdin(tmp_path):
    """Positive control: the same child reports OPEN when it inherits fd 0."""
    assert _run_child_probe(tmp_path, _INFO_PROBE, _EOF_CHILD, "control") == [0, "OPEN\n", ""]


# Seam of a bound site: render_diagnostics.record_frames with no injected
# spawn runs ffmpeg through its own Popen default (row840's tests always inject
# one). The fake ffmpeg writes the frame it was asked for, and writes into it
# what it saw on stdin.
_FAKE_FFMPEG = f"""#!{sys.executable}
import select, sys
ready = select.select([sys.stdin], [], [], 3)[0]
state = "EOF" if ready and sys.stdin.read() == "" else "OPEN"
with open(sys.argv[-1].replace("%06d", "000001"), "w") as frame:
    frame.write(state)
"""
_RENDER_PROBE = r'''
import json, os, sys
from pathlib import Path
held_r, held_w = os.pipe()  # fd 0 = a pipe nobody ever writes or closes
os.dup2(held_r, 0)
from bulk_downloader import render_diagnostics
frames = render_diagnostics.record_frames(Path(sys.argv[2]), frame_count=1, fps=1,
                                          timeout_s=30, ffmpeg_path=sys.argv[1])
print(json.dumps([[frame.name, frame.read_text()] for frame in frames]))
'''


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd 0 and select() on a pipe")
def test_render_diagnostics_default_spawn_hands_ffmpeg_eof(tmp_path):
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(_FAKE_FFMPEG, encoding="utf-8")
    ffmpeg.chmod(0o755)
    frames = _run_child_probe(tmp_path, _RENDER_PROBE, str(ffmpeg), str(tmp_path / "frames"))
    assert frames == [["frame-000001.png", "EOF"]], (
        f"ROW1053-STDIN-OPEN render_diagnostics.record_frames ffmpeg read the inherited stdin: {frames}")
