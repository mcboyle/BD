"""RED battery for scripts/deploy.sh -- the GIT deploy path (target v3.66.848).

WHAT THIS FILE REPLACED, AND WHY. The previous battery in this file drove the
F0.1 ZIP-OVERLAY deploy (`--zip`, a sha256 gate over a release archive,
`unzip -o`). CLAUDE.md section 7 records that the box now updates with
`git fetch origin main` + `git reset --hard origin/main` + a restart, and that
"there is no zip overlay and no zip fallback". A battery that certifies the zip
script is a gate whose subject no longer exists -- it reports clean, truthfully
and uselessly (CLAUDE.md section 0). So the subject was replaced wholesale, at
the same two filenames.

RED STATUS AT THE TIME OF WRITING. Everything below except `test_script_parses_clean`
is RED against the pristine (zip-era) tree, and each is red for the reason it
names rather than merely "the script exited nonzero": the pristine script dies
`--zip is required` before touching anything, so every positive property these
tests assert (a named dirty path, an incoming-commit subject, a pip re-check, a
marker file, a distinct health diagnosis) is absent from its output and its
side effects. `test_script_parses_clean` is a GUARD -- it is green on pristine
and stays green; it is here so a syntax error in the rewrite presents as itself
rather than as twenty confusing subject failures.

HOW THE DENOMINATOR CONTAINS THE SUBJECT. Real `git` does the real work: each
test builds a real bare origin + a real working clone, so `git fetch`,
`git status --porcelain`, `git log`, `git diff --stat` and `git reset --hard`
execute for real and their effects are observed on disk. Only the process
boundaries the script must cross are shimmed -- `sudo`, `systemctl`, `curl`,
`npm`, and the venv python -- and each shim IS the interface the script calls,
so a shim that is never reached shows up as an EMPTY log, which several tests
assert on directly. Service control is deliberately NOT env-overridable in
these tests: the shims are placed on PATH so the script's literal
`sudo systemctl stop bulkdownloader` string is what gets exercised.

NO NEW `BD_`-PREFIXED NAMES. Every harness variable here is unprefixed
(PY_LOG, SUDO_LOG, CURL_MODE, ROOT_CODE, INV_MODE, ...). Only the four
already-ledgered BD_ names are used, and only because the script honors them:
BD_DEPLOY_DIR (not set here, cleared instead), BD_VENV_PYTHON,
BD_GRAPH_HASH_PIN, and BD_RESTART_CMD (set by exactly one test, which asserts
the script REFUSES it). All four are in reports/config_gui_manifest.json.
Adding a BD_ name -- even a shell local -- puts it in
`tests/test_gui_parity.py`'s scan denominator (CLAUDE.md section 4).

NO FIXED-WIDTH SOURCE WINDOWS. `test_cloud_setup_uses_shared_checker` extracts
shell heredocs on their BALANCED `<<'DELIM'` / `DELIM` delimiters, never on a
character count (CLAUDE.md section 2a).

CONTRACT THE IMPLEMENTATION MUST MEET (read this before writing the script):

  exit 0  deployed-and-verified, or already-current-and-verified
  exit 1  a step or a verification FAILED; state is not known good
  exit 2  refusal / precondition; NOTHING was mutated

  flags   --dir --discard-local --skip-graph-pin --health-url --timeout --interval
  marker  frontend/dist/.bd-built-from   holds the commit sha the bundle was built from
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deploy.sh"
CHECK_REQ = REPO / "tools" / "check_requirements.py"
CLOUD_SETUP = REPO / "scripts" / "cloud-setup.sh"
VENV_PY = REPO / "venv" / "bin" / "python"
BASH = "bash"

TREE_VERSION = "3.66.848"
MARKER_REL = "frontend/dist/.bd-built-from"

# git identity is supplied per-invocation so the harness never depends on (or
# writes to) the invoking user's global git config.
_GIT_ID = [
    "-c", "user.email=bd-deploy-test@example.invalid",
    "-c", "user.name=BD Deploy Test",
    "-c", "commit.gpgsign=false",
    "-c", "init.defaultBranch=main",
]


# ──────────────────────────────────────────────────────────── helpers


def _git(cwd, *args):
    r = subprocess.run(["git", *_GIT_ID, *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        "harness git failure (NOT a subject failure): git %s -> %s\n%s%s"
        % (" ".join(args), r.returncode, r.stdout, r.stderr))
    return r.stdout


def _write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _write_exec(path, body):
    p = _write(path, body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _read(path):
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _lines(path):
    return [ln for ln in _read(path).splitlines() if ln.strip()]


def _out(r):
    return r.stdout + r.stderr


def _low(r):
    return _out(r).lower()


def _ctx(r, extra=""):
    return "\n--- exit=%s\n--- stdout ---\n%s\n--- stderr ---\n%s\n%s" % (
        r.returncode, r.stdout, r.stderr, extra)


# ────────────────────────────────────────────────────── PATH shims
#
# Each shim logs every invocation. A test that asserts a log is EMPTY is
# asserting the script never reached that boundary; a test that asserts on log
# CONTENT is asserting the script issued the command it claims to. A shim that
# is never reached therefore cannot make a test pass by accident.

_FAKE_PYTHON = r"""#!/usr/bin/env bash
# Stands in for the venv python. Dispatches on argv so every distinct call the
# deploy script makes is observable and independently steerable.
printf '%s\n' "$*" >> "$PY_LOG"
# `-c` IS DELEGATED TO A REAL INTERPRETER, and that is load-bearing rather than
# tidiness. Step [10]'s parse read-back is literally `python -c 'json.load(...)'`
# against the file the generator just wrote, so a shim that answered exit 0 to
# every `-c` made that check unobservable: a truncated inventory sailed through
# and the deploy reported ALREADY CURRENT -- VERIFIED. The shim was the reason
# the check looked untested, not an absent test (CLAUDE.md section 0). REAL_PY
# is the interpreter running pytest, passed in explicitly so this never falls
# back to a bare `python3` (CLAUDE.md section 5).
case "${1:-}" in
  -c) exec "${REAL_PY:?REAL_PY not set by the harness}" "$@";;
esac
args=" $* "
case "$args" in
  *check_requirements.py*)
    case "${REQ_MODE:-ok}" in
      ok)          exit 0;;
      missing)     printf 'lxml\n'; exit 1;;
      install_fixes)
        if [ -f "$PIP_MARKER" ]; then exit 0; fi
        printf 'lxml\n'; exit 1;;
      unevaluable) exit 2;;
      *)           exit 0;;
    esac;;
esac
case "$args" in
  *" pip "*|*" -m pip "*)
    printf '%s\n' "$*" >> "$PIP_LOG"
    : > "$PIP_MARKER"
    exit "${PIP_EXIT:-0}";;
esac
case "$args" in
  *gui_parity_inventory.py*)
    printf '%s\n' "$*" >> "$INV_LOG"
    outdir="reports"; prev=""
    for a in "$@"; do
      if [ "$prev" = "--outdir" ]; then outdir="$a"; fi
      prev="$a"
    done
    # INV_MODE steers WHAT the generator leaves on disk, independently of the
    # exit code it returns. Without it this branch always wrote one well-formed
    # document, so "exited 0 and wrote nothing" and "exited 0 and wrote a
    # truncated file" -- the two states step [10]'s read-backs exist for -- were
    # both unreachable from the harness (CLAUDE.md section 0).
    case "${INV_MODE:-ok}" in
      nofile) exit "${INV_EXIT:-0}";;
    esac
    mkdir -p "$outdir"
    case "${INV_MODE:-ok}" in
      truncated)
        # Still carries route_source, so a failure here is attributable to the
        # PARSE read-back and not to the route_source one that follows it.
        printf '{"route_source": "live url_map", "counts": {"total"' \
            > "$outdir/gui_parity_inventory.json";;
      *)
        printf '{"route_source": "%s", "counts": {"total": 1}}\n' \
            "${INV_ROUTE_SOURCE:-live url_map}" > "$outdir/gui_parity_inventory.json";;
    esac
    exit "${INV_EXIT:-0}";;
esac
case "$args" in
  *l0_extract.py*)
    db=""; prev=""
    for a in "$@"; do
      if [ "$prev" = "--db" ]; then db="$a"; fi
      prev="$a"
    done
    if [ -n "$db" ]; then mkdir -p "$(dirname "$db")"; : > "$db"; fi
    exit 0;;
esac
case "$args" in
  *graph_build.py*)
    pin=""; prev=""
    for a in "$@"; do
      if [ "$prev" = "--hash-pin" ]; then pin="$a"; fi
      prev="$a"
    done
    case "$args" in
      *--write-hash*)
        if [ -n "$pin" ]; then
          mkdir -p "$(dirname "$pin")"
          printf 'deadbeefdeadbeef\n' > "$pin"
        fi
        exit 0;;
      *--check-hash*)
        exit "${CHECKHASH_EXIT:-0}";;
    esac
    exit 0;;
esac
exit 0
"""

_FAKE_SUDO = r"""#!/usr/bin/env bash
# Logs the ELEVATED command line, then runs it. tests assert on this log to
# prove the sudo boundary is drawn where CLAUDE.md section 5 requires.
printf '%s\n' "$*" >> "$SUDO_LOG"
exec "$@"
"""

_FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
verb=""
for a in "$@"; do
  case "$a" in
    stop|start|restart|is-active) verb="$a"; break;;
  esac
done
state="$SVC_STATE"
case "$verb" in
  stop)
    if [ "${STOP_STICKY:-0}" != "1" ]; then printf 'inactive\n' > "$state"; fi
    exit "${STOP_EXIT:-0}";;
  start|restart)
    printf 'active\n' > "$state"
    exit "${START_EXIT:-0}";;
  is-active)
    s="$(cat "$state" 2>/dev/null || printf 'unknown')"
    printf '%s\n' "$s"
    if [ "$s" = "active" ]; then exit 0; fi
    exit 3;;
esac
exit 0
"""

_FAKE_CURL = r"""#!/usr/bin/env bash
# Faithful enough for both idioms the script may use: a bare body fetch, and
# `-o <file> -w '<fmt with %{http_code}>'`. The -w format is honored verbatim
# (printf %b) so a script that parses "body\ncode" and one that parses a bare
# code both get what real curl would give them.
#
# ROOT_CODE MAKES THIS SHIM DISCRIMINATE BY URL, and it exists because without
# it the shim answered /api/health and / IDENTICALLY -- so the two could never
# disagree, which is precisely the condition step [12]'s root-URL confirmation
# exists to detect. A harness whose denominator structurally excludes its
# subject reports clean, truthfully and uselessly (CLAUDE.md section 0).
# Unset, every response is byte-for-byte what it was before this knob existed.
calls="${CURL_CALLS_FILE:-}"
count=0
if [ -n "$calls" ]; then
  [ -f "$calls" ] && count="$(cat "$calls")"
  count=$((count + 1))
  printf '%s' "$count" > "$calls"
fi
mode="${CURL_MODE:-healthy}"
ver="${CURL_VERSION:-0.0.0}"
body=""; code="200"; rc=0
case "$mode" in
  healthy)
    body="{\"ok\": true, \"db_ok\": true, \"version\": \"$ver\"}";;
  wrongversion)
    body="{\"ok\": true, \"db_ok\": true, \"version\": \"9.9.9\"}";;
  down)
    body=""; code="000"; rc=7;;
  bundle_missing)
    body=""; code="503"; rc=0;;
  versionless_then_healthy)
    if [ "${count:-2}" -le 1 ]; then
      body="{}"
    else
      body="{\"ok\": true, \"db_ok\": true, \"version\": \"$ver\"}"
    fi;;
esac
outfile=""; wfmt=""; prev=""; url=""
for a in "$@"; do
  if [ "$prev" = "-o" ] || [ "$prev" = "--output" ]; then outfile="$a"; fi
  if [ "$prev" = "-w" ] || [ "$prev" = "--write-out" ]; then wfmt="$a"; fi
  case "$a" in http://*|https://*) url="$a";; esac
  prev="$a"
done
# Stripping the longest `*/api/health` prefix leaves "" for the health URL and
# the URL itself for anything else, so this asks "is this the ROOT probe?"
# without pattern-matching a hostname the test happens to have chosen.
if [ -n "${ROOT_CODE:-}" ] && [ "${url##*/api/health}" = "$url" ]; then
  body=""; code="$ROOT_CODE"; rc=0
fi
if [ -n "$outfile" ]; then
  printf '%s' "$body" > "$outfile"
else
  printf '%s' "$body"
fi
if [ -n "$wfmt" ]; then
  printf '%b' "${wfmt//%\{http_code\}/$code}"
fi
exit "$rc"
"""

_FAKE_NPM = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$NPM_LOG"
case " $* " in
  *" run "*build*|*" run-script "*build*)
    if [ "${NPM_EMPTY:-0}" != "1" ]; then
      mkdir -p dist
      printf '<!doctype html><title>bd</title>\n' > dist/index.html
    fi;;
esac
exit "${NPM_EXIT:-0}"
"""


# ────────────────────────────────────────────────── repo + fixture


class Fx(dict):
    """Attribute access over the fixture dict, purely for readability."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:                      # pragma: no cover
            raise AttributeError(k) from exc


def _seed_files(version=TREE_VERSION):
    return {
        "bulk_downloader/__init__.py": '__version__ = "%s"\n' % version,
        "requirements.txt": "# runtime deps\nlxml>=5.0,<7.0\n",
        "requirements-test.txt": "# suite deps\npyflakes>=3.0,<4.0\n",
        "frontend/package.json": '{"name": "bd-frontend", "private": true}\n',
        "frontend/src/main.ts": "// spa entry\n",
        "tools/check_requirements.py": "# stub; the fake venv python dispatches on this path\n",
        "tools/gui_parity_inventory.py": "# stub\n",
        "tools/l0_extract.py": "# stub\n",
        "tools/graph_build.py": "# stub\n",
        ".gitignore": "frontend/dist/\nreports/\nvenv/\n__pycache__/\n*.pyc\n",
        "docs/NOTE.txt": "seed\n",
    }


def _setup(version=TREE_VERSION, **envextra):
    """Real bare origin + real clone + PATH shims. Returns an Fx."""
    work = tempfile.mkdtemp(prefix="bd_gitdeploy_")
    origin = os.path.join(work, "origin.git")
    seed = os.path.join(work, "seed")
    clone = os.path.join(work, "clone")
    binroot = os.path.join(work, "bin")

    _git(work, "init", "--bare", origin)
    os.makedirs(seed)
    _git(seed, "init")
    for rel, text in _seed_files(version).items():
        _write(os.path.join(seed, rel), text)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed commit")
    _git(seed, "remote", "add", "origin", origin)
    _git(seed, "push", "-u", "origin", "HEAD:refs/heads/main")
    _git(work, "clone", origin, clone)

    fake_py = _write_exec(os.path.join(binroot, "fake-venv-python"), _FAKE_PYTHON)
    _write_exec(os.path.join(binroot, "sudo"), _FAKE_SUDO)
    _write_exec(os.path.join(binroot, "systemctl"), _FAKE_SYSTEMCTL)
    _write_exec(os.path.join(binroot, "curl"), _FAKE_CURL)
    _write_exec(os.path.join(binroot, "npm"), _FAKE_NPM)

    logs = {k: os.path.join(work, "log-%s" % k) for k in
            ("py", "pip", "inv", "sudo", "systemctl", "npm")}

    env = dict(os.environ)
    env["PATH"] = binroot + os.pathsep + env.get("PATH", "")
    env["HOME"] = work                       # never resolve a real ~/BulkDownloader
    env.pop("BD_DEPLOY_DIR", None)           # --dir is always explicit here
    env["BD_VENV_PYTHON"] = str(fake_py)
    env["REAL_PY"] = sys.executable          # the shim delegates `-c` to this
    env["BD_GRAPH_HASH_PIN"] = os.path.join(work, "pin", "KNOWLEDGE_GRAPH.content.sha256")
    env["PY_LOG"] = logs["py"]
    env["PIP_LOG"] = logs["pip"]
    env["INV_LOG"] = logs["inv"]
    env["SUDO_LOG"] = logs["sudo"]
    env["SYSTEMCTL_LOG"] = logs["systemctl"]
    env["NPM_LOG"] = logs["npm"]
    env["PIP_MARKER"] = os.path.join(work, "pip-marker")
    env["SVC_STATE"] = os.path.join(work, "svc-state")
    env["CURL_CALLS_FILE"] = os.path.join(work, "curl-calls")
    env["CURL_VERSION"] = version
    env.update({k: str(v) for k, v in envextra.items()})
    _write(env["SVC_STATE"], "active\n")     # the box starts with the unit running

    fx = Fx(work=work, origin=origin, seed=seed, clone=clone, binroot=binroot,
            env=env, logs=logs, version=version)
    return fx


def _bundle_current(fx):
    """Make frontend/dist look freshly built from the clone's current HEAD."""
    _write(os.path.join(fx.clone, "frontend", "dist", "index.html"), "<!doctype html>\n")
    _write(os.path.join(fx.clone, MARKER_REL), _head(fx.clone) + "\n")


def _head(repo, ref="HEAD"):
    return _git(repo, "rev-parse", ref).strip()


def _advance_origin(fx, subject, rel="docs/NOTE.txt", text=None):
    """Add one commit to origin/main, leaving the clone's HEAD behind."""
    _write(os.path.join(fx.seed, rel), text if text is not None else subject + "\n")
    _git(fx.seed, "add", "-A")
    _git(fx.seed, "commit", "-m", subject)
    _git(fx.seed, "push", "origin", "HEAD:refs/heads/main")
    return _head(fx.seed)


def _deploy(fx, *args, timeout=120):
    argv = [BASH, str(SCRIPT), "--dir", fx.clone,
            "--health-url", "http://deploy-test.invalid/api/health",
            "--timeout", "5", "--interval", "1", *args]
    return subprocess.run(argv, env=fx.env, cwd=fx.work,
                          capture_output=True, text=True, timeout=timeout)


def _curl_calls(fx):
    txt = _read(fx.env["CURL_CALLS_FILE"]).strip()
    return int(txt) if txt.isdigit() else 0


# ─────────────────────────────────────────────────────────── tests


def test_script_parses_clean():
    """GUARD -- green on pristine and must stay green.

    Here so a syntax error in the rewrite presents as itself instead of as
    twenty subject failures (CLAUDE.md section 2a: a mutant that does not
    parse is INVALID, not caught).
    """
    r = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_refuses_dirty_tree_exit2_names_paths():
    fx = _setup()
    _advance_origin(fx, "incoming work", rel="bulk_downloader/mod_a.py",
                    text="A = 1\n")
    dirty = os.path.join(fx.clone, "bulk_downloader", "__init__.py")
    _write(dirty, '__version__ = "0.0.0-OPERATOR-LIVE-EDIT"\n')
    before = _head(fx.clone)

    r = _deploy(fx)

    assert r.returncode == 2, (
        "a dirty tracked tree must be a REFUSAL (exit 2, nothing mutated), "
        "not a failure and not a silent overwrite" + _ctx(r))
    assert "bulk_downloader/__init__.py" in _out(r), (
        "the refusal must name the exact path it would have destroyed" + _ctx(r))
    assert "OPERATOR-LIVE-EDIT" in _read(dirty), (
        "the operator's live edit was destroyed by a run that refused" + _ctx(r))
    assert _head(fx.clone) == before, "HEAD moved during a refusal" + _ctx(r)


def test_unpushed_commits_are_refused_then_discardable():
    """`git status` is SILENT about committed-but-unpushed work.

    A reset destroys it just as surely as an uncommitted edit, and the dirty
    check cannot see it -- so the script asks the question git actually answers,
    `git merge-base --is-ancestor HEAD origin/main`. Nothing in this file
    reached that branch before: every existing refusal test dirties the WORK
    TREE, which trips the other half of the same `if`. A test that only ever
    exercises the DIRTY term certifies the ancestry term without touching it.

    Both directions, because a refusal that also fires on a fast-forward would
    make the script refuse every ordinary deploy.
    """
    fx = _setup()
    target = _advance_origin(fx, "incoming work", rel="bulk_downloader/mod_a.py",
                             text="A = 1\n")
    _bundle_current(fx)
    # A clean tree carrying one local commit origin/main does not have. The
    # work tree is deliberately left CLEAN so the refusal cannot come from the
    # dirty check instead.
    _write(os.path.join(fx.clone, "docs", "LOCAL_ONLY.txt"), "operator work\n")
    _git(fx.clone, "add", "-A")
    _git(fx.clone, "commit", "-m", "SENTINEL-UNPUSHED-COMMIT")
    local_head = _head(fx.clone)
    assert _git(fx.clone, "status", "--porcelain", "--untracked-files=no").strip() == "", (
        "harness error, NOT a subject failure: the tree must be CLEAN or this "
        "test measures the dirty gate rather than the ancestry gate")

    r = _deploy(fx)

    assert r.returncode == 2, (
        "a commit that exists only on HEAD would be DESTROYED by "
        "`git reset --hard origin/main`, and git status says nothing about it. "
        "That is a REFUSAL (exit 2, nothing mutated), not a deploy" + _ctx(r))
    assert "SENTINEL-UNPUSHED-COMMIT" in _out(r), (
        "the refusal must name the commits it would have destroyed, not merely "
        "report that some exist" + _ctx(r))
    assert _head(fx.clone) == local_head, (
        "HEAD moved during a refusal -- exit 2 must mean nothing was mutated"
        + _ctx(r))

    # --discard-local is the deliberate override, and it must still LIST the
    # work first: nothing is destroyed silently.
    r2 = _deploy(fx, "--discard-local")

    assert r2.returncode == 0, (
        "--discard-local run did not complete" + _ctx(r2))
    assert "SENTINEL-UNPUSHED-COMMIT" in _out(r2), (
        "the destroyed commits must be listed even when the override is given"
        + _ctx(r2))
    assert _head(fx.clone) == target, (
        "the reset did not land on origin/main" + _ctx(r2))


def test_restart_cmd_override_is_refused_not_ignored():
    """BD_RESTART_CMD cannot express a stopped window, so it is REFUSED.

    Honouring it is impossible and ignoring it is the defect: the operator
    deliberately set an override, and a run that succeeds having silently
    dropped it reports success on a question it never asked. The refusal must
    land in step [0], before any mutation -- an override discovered after the
    reset is a refusal that already destroyed something.
    """
    fx = _setup(BD_RESTART_CMD="sudo systemctl restart bulkdownloader")
    _advance_origin(fx, "incoming work")
    _bundle_current(fx)
    before = _head(fx.clone)

    r = _deploy(fx)

    assert r.returncode == 2, (
        "an override that cannot be honoured is a REFUSAL, not a warning and "
        "certainly not a silent no-op" + _ctx(r))
    assert "BD_RESTART_CMD" in _out(r), (
        "the refusal must name the variable so the operator knows what to "
        "unset" + _ctx(r))
    assert _head(fx.clone) == before, (
        "the tree was reset before the override was even looked at" + _ctx(r))
    assert _lines(fx.logs["systemctl"]) == [], (
        "the service was touched during a refusal" + _ctx(r,
        "systemctl log: %r" % _read(fx.logs["systemctl"])))
    assert _lines(fx.logs["npm"]) == [] and _lines(fx.logs["pip"]) == [], (
        "exit 2 must mean NOTHING was mutated" + _ctx(r))


def test_discard_local_reports_then_resets():
    fx = _setup()
    target = _advance_origin(fx, "incoming work", rel="bulk_downloader/mod_a.py",
                             text="A = 1\n")
    dirty = os.path.join(fx.clone, "bulk_downloader", "__init__.py")
    _write(dirty, '__version__ = "0.0.0-OPERATOR-LIVE-EDIT"\n')
    _bundle_current(fx)

    r = _deploy(fx, "--discard-local")

    out = _out(r)
    assert r.returncode == 0, "--discard-local run did not complete" + _ctx(r)
    assert "bulk_downloader/__init__.py" in out, (
        "what is being DESTROYED must be listed, not silently discarded" + _ctx(r))
    assert re.search(r"\d+ files? changed|\|\s+\d+ [+-]", out), (
        "a diffstat of the doomed work must be shown before the reset" + _ctx(r))
    low = _low(r)
    assert low.index("bulk_downloader/__init__.py") < low.index("deploy ok"), (
        "the destroyed-work report must precede the success summary" + _ctx(r))
    assert _head(fx.clone) == target, "reset did not land on origin/main" + _ctx(r)
    assert "OPERATOR-LIVE-EDIT" not in _read(dirty), (
        "--discard-local was given but the local edit survived" + _ctx(r))
    assert TREE_VERSION in _read(dirty), (
        "after the reset the file must match origin/main" + _ctx(r))


def test_untracked_files_survive_and_do_not_block():
    fx = _setup()
    _advance_origin(fx, "incoming work")
    note = os.path.join(fx.clone, "OPERATOR_NOTES.txt")
    _write(note, "keep me\n")
    _bundle_current(fx)

    r = _deploy(fx)

    assert r.returncode == 0, (
        "an untracked, non-ignored file must NOT block the deploy: "
        "`git reset --hard` does not delete it, so there is nothing to lose"
        + _ctx(r))
    assert Path(note).is_file(), "an untracked file was deleted" + _ctx(r)
    assert _read(note) == "keep me\n"


def test_shows_incoming_commits_before_reset():
    fx = _setup()
    _advance_origin(fx, "SENTINEL-INCOMING-SUBJECT")
    _bundle_current(fx)

    r = _deploy(fx)

    out = _out(r)
    assert r.returncode == 0, _ctx(r)
    assert "SENTINEL-INCOMING-SUBJECT" in out, (
        "the operator must see WHAT is about to land before it lands" + _ctx(r))
    assert re.search(r"\d+ files? changed", out), (
        "a diffstat of the incoming range must be printed" + _ctx(r))
    low = _low(r)
    assert low.index("sentinel-incoming-subject") < low.index("deploy ok"), (
        "the incoming-commit listing must precede the summary" + _ctx(r))


def test_already_current_verifies_and_skips_work():
    fx = _setup()
    _bundle_current(fx)

    r = _deploy(fx)

    assert r.returncode == 0, (
        "a deploy that changes nothing must still VERIFY and exit 0" + _ctx(r))
    assert "already current" in _low(r), (
        "an unchanged tree must say 'already current', not report drift -- "
        "a gate that fires on identity gets switched off" + _ctx(r))
    assert _lines(fx.logs["npm"]) == [], (
        "npm ran on an unchanged tree; idempotence must be OBSERVED, not claimed"
        + _ctx(r, "npm log: %r" % _read(fx.logs["npm"])))
    assert _lines(fx.logs["pip"]) == [], (
        "pip ran while every requirement already resolved"
        + _ctx(r, "pip log: %r" % _read(fx.logs["pip"])))


def test_requirement_gap_installs_then_rechecks():
    fx = _setup(REQ_MODE="install_fixes")
    _advance_origin(fx, "adds a requirement")
    _bundle_current(fx)

    r = _deploy(fx)

    piplog = _read(fx.logs["pip"])
    assert r.returncode == 0, _ctx(r, "pip log: %r" % piplog)
    assert "install" in piplog and "requirements.txt" in piplog, (
        "the requirement gap must be closed with `pip install -r requirements.txt`"
        + _ctx(r, "pip log: %r" % piplog))
    checks = [ln for ln in _lines(fx.logs["py"]) if "check_requirements.py" in ln]
    assert len(checks) >= 2, (
        "pip exiting 0 is not resolution -- the check must be RE-RUN afterwards"
        + _ctx(r, "check calls: %r" % checks))


def test_test_requirements_converge_too():
    """v3.66.862. RED on v3.66.861.

    pyflakes was DECLARED in requirements-dev.txt at v3.66.861 to close a box
    capture failure, and the NEXT capture failed on pyflakes again -- because
    step [5] converges requirements.txt ONLY (:305) and check_requirements.py
    defaults to requirements.txt only (:56). The declaration landed in a
    manifest nothing on the deploy path reads, so it could not have worked.
    That is CLAUDE.md section 0's own warning: the fix reproduced the shape of
    the defect it was fixing -- a denominator that structurally excludes the
    subject.

    The box IS the gate (section 7): capture.sh runs the full suite there, and
    the suite's own dependencies are therefore deploy-path dependencies. They
    live in requirements-test.txt rather than requirements-dev.txt because the
    dev manifest also carries the packaging chain (nuitka needs gcc), and a
    deploy must not provision a build host as a side effect. This
    asserts the QUESTION is asked, not that pip runs -- with every entry
    already resolved the correct behaviour is to skip the install, and an
    assertion on the pip log would be green for the wrong reason.
    """
    fx = _setup(REQ_MODE="ok")
    _advance_origin(fx, "any change at all")
    _bundle_current(fx)

    r = _deploy(fx)

    checks = [ln for ln in _lines(fx.logs["py"]) if "check_requirements.py" in ln]
    assert checks, (
        "no requirements check ran at all -- this test's own denominator is "
        "empty and it proves nothing about the dev manifest"
        + _ctx(r, "py log: %r" % _read(fx.logs["py"])))
    dev = [ln for ln in checks if "requirements-test.txt" in ln]
    assert dev, (
        "deploy.sh never asks whether requirements-test.txt resolves, so a "
        "dependency declared there is invisible to the deploy and the box "
        "fails the suite on it (pyflakes, v3.66.861)"
        + _ctx(r, "check calls: %r" % checks))


def test_requirement_still_missing_after_install_fails():
    fx = _setup(REQ_MODE="missing")
    _bundle_current(fx)

    r = _deploy(fx)

    assert r.returncode == 1, (
        "an unresolvable requirement is a FAILED deploy (exit 1)" + _ctx(r))
    assert "lxml" in _out(r), (
        "the failure must NAME the packages that do not resolve" + _ctx(r))
    # `"lxml" in output` is satisfied REDUNDANTLY by the pre-install note, so it
    # does not pin the site it appears to be about: dropping $MISSING from the
    # post-install die leaves this test green. Pin the failing line itself.
    still = [ln for ln in _out(r).splitlines() if "still unresolved" in ln]
    assert still, (
        "no line reported the requirement as still unresolved AFTER the pip "
        "install, so this run is not about the post-install re-check" + _ctx(r))
    assert any("lxml" in ln for ln in still), (
        "the post-install failure line must carry the names itself -- the "
        "operator reads the line that FAILED, not the note above it. Lines: %r"
        % still + _ctx(r))


def test_unevaluable_requirements_fail_not_pass():
    fx = _setup(REQ_MODE="unevaluable")
    _bundle_current(fx)

    r = _deploy(fx)

    assert r.returncode == 1, (
        "UNKNOWN is a third state and it FAILS -- 'could not evaluate' must "
        "never be rendered as 'satisfied' (CLAUDE.md section 0)" + _ctx(r))
    assert re.search(r"could not evaluate|cannot evaluate|unevaluable|not satisfied",
                     _low(r)), (
        "the message must say the check could not be evaluated" + _ctx(r))
    # The FIRST exit-2 gate must fire, not the second one after a pointless
    # detour. A checker that could not read requirements.txt reports NO names,
    # so the install branch would run `pip install -r` on an EMPTY package list
    # and only then fail. Same exit code, same wording, a side effect nobody
    # asked for -- and it is invisible unless the pip log is asserted empty.
    assert _lines(fx.logs["pip"]) == [], (
        "an unevaluable requirements check must not trigger an install: "
        "unknown is a third state, and the remedy for unknown is not `pip "
        "install -r` against a file the checker could not even read"
        + _ctx(r, "pip log: %r" % _read(fx.logs["pip"])))


def test_frontend_rebuild_keyed_on_content():
    # (a) marker names HEAD and nothing under frontend/ changed -> no rebuild.
    fx = _setup()
    _bundle_current(fx)
    r = _deploy(fx)
    assert r.returncode == 0, _ctx(r)
    assert _lines(fx.logs["npm"]) == [], (
        "the bundle marker matches HEAD and frontend/ is unchanged; rebuilding "
        "anyway is a gate firing on identity"
        + _ctx(r, "npm log: %r" % _read(fx.logs["npm"])))

    # (b) a commit that touches frontend/ -> rebuild, and the marker advances.
    fx = _setup()
    _bundle_current(fx)
    target = _advance_origin(fx, "spa change", rel="frontend/src/main.ts",
                             text="// spa entry v2\n")
    r = _deploy(fx)
    npmlog = _read(fx.logs["npm"])
    assert r.returncode == 0, _ctx(r, "npm log: %r" % npmlog)
    assert "ci" in npmlog and "build" in npmlog, (
        "frontend/ changed but `npm ci` + `npm run build` did not run"
        + _ctx(r, "npm log: %r" % npmlog))
    assert _read(os.path.join(fx.clone, MARKER_REL)).strip() == target, (
        "the bundle marker must be rewritten to the commit it was built from"
        + _ctx(r, "marker: %r" % _read(os.path.join(fx.clone, MARKER_REL))))

    # (c) a marker naming an unresolvable commit -> rebuild (unknown fails
    #     toward doing the work, never toward skipping it).
    fx = _setup()
    _write(os.path.join(fx.clone, "frontend", "dist", "index.html"), "<!doctype html>\n")
    _write(os.path.join(fx.clone, MARKER_REL), "0" * 40 + "\n")
    r = _deploy(fx)
    npmlog = _read(fx.logs["npm"])
    assert r.returncode == 0, _ctx(r, "npm log: %r" % npmlog)
    assert "build" in npmlog, (
        "a marker naming an unresolvable commit is UNKNOWN provenance and must "
        "force a rebuild, not be read as current"
        + _ctx(r, "npm log: %r" % npmlog))


def test_build_exit0_without_bundle_fails():
    fx = _setup(NPM_EMPTY="1")
    # force the rebuild branch, and leave no pre-existing index.html
    _write(os.path.join(fx.clone, MARKER_REL), "0" * 40 + "\n")

    r = _deploy(fx)

    assert r.returncode == 1, (
        "vite exiting 0 is not the property anyone depends on -- an absent "
        "bundle must FAIL the deploy" + _ctx(r))
    assert "index.html" in _out(r), (
        "the failure must name frontend/dist/index.html, whose absence is a "
        "silent 503 from bulk_downloader/app.py" + _ctx(r))
    assert not Path(fx.clone, "frontend", "dist", "index.html").exists()


def test_bundle_readback_runs_on_the_build_skipped_path_too():
    """The read-back covers BOTH paths, and only the BUILT one was tested.

    Every other test in this file either forces a rebuild or calls
    `_bundle_current()`, which writes index.html -- so "the build was skipped
    and the bundle is not there anyway" was structurally unreachable from the
    harness, and scoping the read-back to the built path escaped a 26-mutant
    battery. That state is not exotic: a `git clean -x`, a half-finished
    earlier deploy, or a manually deleted dist/ all reach it while the marker
    still names HEAD, and the script would then report ALREADY CURRENT --
    VERIFIED over a tree where every asset route answers 503.

    The empty npm log is the discriminator. Without it this test would also
    pass on a script that failed for the entirely different reason of trying
    to build and failing.
    """
    fx = _setup()
    # Marker names HEAD and frontend/ is unchanged -> the build is SKIPPED.
    # Deliberately not _bundle_current(): that writes the index.html whose
    # absence is the whole subject here.
    _write(os.path.join(fx.clone, MARKER_REL), _head(fx.clone) + "\n")
    assert not Path(fx.clone, "frontend", "dist", "index.html").exists()

    r = _deploy(fx)

    npmlog = _read(fx.logs["npm"])
    assert _lines(fx.logs["npm"]) == [], (
        "harness error, NOT a subject failure: the marker names HEAD and "
        "frontend/ is unchanged, so no build should have been attempted; this "
        "case is about the SKIPPED path" + _ctx(r, "npm log: %r" % npmlog))
    assert r.returncode == 1, (
        "the build was skipped as current but frontend/dist/index.html is "
        "absent, and the deploy reported success. bulk_downloader/app.py "
        "cannot serve an absent bundle: every asset route answers 503 and "
        "nothing else says why" + _ctx(r, "npm log: %r" % npmlog))
    assert "index.html" in _out(r), (
        "the failure must name frontend/dist/index.html" + _ctx(r))
    # Anchored on the step-13 SUMMARY, not on the words "already current":
    # step [2] prints "source already current at <sha>" for any unchanged tree,
    # which has nothing to do with the verdict and would make this assertion
    # fire on a correct run.
    assert not re.search(r"\[step 13\]", _out(r)), (
        "a deploy that cannot serve the SPA reached its summary step and "
        "called itself verified" + _ctx(r))


def test_pycache_swept_venv_pruned():
    fx = _setup()
    _bundle_current(fx)
    pkg_pyc = _write(os.path.join(fx.clone, "bulk_downloader", "__pycache__",
                                  "x.cpython-312.pyc"), "stale")
    stray = _write(os.path.join(fx.clone, "stray.pyc"), "stale")
    venv_pyc = _write(os.path.join(fx.clone, "venv", "lib", "__pycache__",
                                   "y.cpython-312.pyc"), "keep")

    r = _deploy(fx)

    assert r.returncode == 0, _ctx(r)
    assert not pkg_pyc.exists(), "__pycache__ under the app was not swept" + _ctx(r)
    assert not stray.exists(), "stray .pyc was not swept" + _ctx(r)
    assert venv_pyc.is_file(), (
        "the sweep must PRUNE venv/ -- deleting the interpreter's own cached "
        "bytecode is gratuitous and slow, and the zip-era script did it"
        + _ctx(r))


def test_parity_regen_only_when_service_stopped_and_readback():
    # (a) the stop did not take -> the regen must NOT run at all.
    fx = _setup(STOP_STICKY="1")
    _bundle_current(fx)
    r = _deploy(fx)
    svclog = _read(fx.logs["systemctl"])
    assert "is-active" in svclog, (
        "the stop was never CONFIRMED with `systemctl is-active`; a stop "
        "request returning 0 is not a stopped service"
        + _ctx(r, "systemctl log:\n" + svclog))
    assert r.returncode == 1, (
        "a stop that did not take makes the following window unsafe" + _ctx(r))
    assert _lines(fx.logs["inv"]) == [], (
        "gui_parity_inventory imports bulk_downloader.app, whose top level "
        "starts db_init and five scheduler groups -- it must never run against "
        "a live service"
        + _ctx(r, "inventory log: %r" % _read(fx.logs["inv"])))

    # (b) stopped, and the regen is app-derived -> pass.
    fx = _setup()
    _bundle_current(fx)
    r = _deploy(fx)
    assert r.returncode == 0, _ctx(r)
    assert _lines(fx.logs["inv"]), (
        "the parity inventory was never regenerated in the stopped window"
        + _ctx(r))

    # (c) stopped, tool exits 0, but the inventory came from the catalog
    #     fallback -> exit 0 is not sufficient evidence; read it back.
    fx = _setup(INV_ROUTE_SOURCE="endpoint_catalog")
    _bundle_current(fx)
    r = _deploy(fx)
    assert r.returncode == 1, (
        "the generator falls back to ENDPOINT_CATALOG.md and still returns 0; "
        'the written JSON must be read back for "route_source": "live url_map"'
        + _ctx(r))

    # (d) the regen itself FAILS. Its exit status is the cheapest signal there
    #     is and it must not be discarded: the stale copy is still on disk and
    #     will read as inventory drift, which failed an otherwise-green
    #     13389-pass run at v3.66.818.
    fx = _setup(INV_EXIT="3")
    _bundle_current(fx)
    r = _deploy(fx)
    assert _lines(fx.logs["inv"]), (
        "the regen was never invoked, so this run says nothing about a regen "
        "FAILURE" + _ctx(r, "inventory log: %r" % _read(fx.logs["inv"])))
    assert r.returncode == 1, (
        "the parity inventory regen exited 3 and the deploy reported success"
        + _ctx(r))
    assert "inventory" in _low(r), (
        "the failure must name the inventory as the failing subject" + _ctx(r))

    # (e) exit 0 having written NO FILE. `[ -f "$PARITY_JSON" ]` is the only
    #     thing standing between that and a deploy that never notices.
    fx = _setup(INV_MODE="nofile")
    _bundle_current(fx)
    r = _deploy(fx)
    assert _lines(fx.logs["inv"]), (
        "the regen was never invoked" + _ctx(r))
    assert r.returncode == 1, (
        "the regen exited 0 and wrote nothing; exit 0 is not evidence a file "
        "exists, and the suite will read whatever stale copy is there"
        + _ctx(r))
    assert "gui_parity_inventory.json" in _out(r), (
        "the failure must name the path that does not exist" + _ctx(r))
    # Pin the SITE, not just the outcome. Deleting the existence check leaves
    # the deploy still exiting 1 -- the json.load read-back below it raises on
    # an absent file and dies too -- so an assertion on the exit code and the
    # path alone is satisfied redundantly and pins neither check. "does not
    # exist" is the wording only the existence check produces; a run that got
    # here via the parse check reports a JSON failure and misdiagnoses a file
    # that was never written as a file that will not parse.
    assert "does not exist" in r.stderr, (
        "an inventory that was never written must be diagnosed as ABSENT, not "
        "as unparseable -- the two have different remedies" + _ctx(r))
    assert not Path(fx.clone, "reports", "gui_parity_inventory.json").exists()

    # (f) exit 0 having written a TRUNCATED file. The two read-backs are
    #     deliberately not merged: a half-written document can still contain
    #     the route_source line while json.load raises -- and json.load is
    #     exactly what the reconcile gate does to this file. The fixture keeps
    #     route_source present so a pass here cannot be the OTHER check firing.
    fx = _setup(INV_MODE="truncated")
    _bundle_current(fx)
    r = _deploy(fx)
    written = _read(os.path.join(fx.clone, "reports", "gui_parity_inventory.json"))
    assert "route_source" in written, (
        "harness error, NOT a subject failure: the truncated fixture must still "
        "carry route_source, or this case is caught by the wrong check; wrote %r"
        % written)
    assert r.returncode == 1, (
        "a truncated inventory that still contains route_source must be caught "
        "by the PARSE read-back; the reconcile gate does json.load on this file "
        "and would fail the whole suite" + _ctx(r, "wrote: %r" % written))
    assert "json" in _low(r), (
        "the failure must name JSON parsing as what went wrong, not the "
        "route_source predicate that happens to be satisfied" + _ctx(r))


def test_sudo_wraps_only_writehash_and_systemctl():
    fx = _setup()
    _bundle_current(fx)

    r = _deploy(fx)

    assert r.returncode == 0, _ctx(r)
    sudo_lines = _lines(fx.logs["sudo"])
    assert sudo_lines, (
        "nothing was elevated -- the sudo shim was never reached, so this test "
        "would otherwise pass without exercising anything" + _ctx(r))
    py_name = os.path.basename(fx.env["BD_VENV_PYTHON"])
    for ln in sudo_lines:
        if py_name not in ln:
            # service control and pin-directory plumbing (mkdir/chmod) are
            # allowed to be elevated; the rule under test is about which
            # PYTHON invocations run as root.
            continue
        assert "graph_build.py" in ln and "--write-hash" in ln, (
            "the ONLY elevated python invocation may be "
            "`graph_build.py --hash-pin ... --write-hash`; it sets "
            "projection_mode false and returns before emitting a projection, "
            "so it writes the pin and nothing else. Anything else run under "
            "sudo builds with HOME=/root (CLAUDE.md section 5). "
            "Offending line: %r" % ln + _ctx(r))
    sudo_txt = "\n".join(sudo_lines)
    assert "l0_extract" not in sudo_txt, (
        "l0_extract was elevated" + _ctx(r, "sudo log:\n" + sudo_txt))
    assert "--check-hash" not in sudo_txt, (
        "--check-hash must run AS THE INVOKING USER -- a root-readable pin is "
        "not evidence capture.sh can read it"
        + _ctx(r, "sudo log:\n" + sudo_txt))
    pylog = _read(fx.logs["py"])
    assert "l0_extract" in pylog, (
        "the graph was never extracted unelevated" + _ctx(r, "py log:\n" + pylog))
    assert "--check-hash" in pylog, (
        "the pin was written but never verified readable+matching as the "
        "invoking user" + _ctx(r, "py log:\n" + pylog))


def test_checkhash_failure_fails_deploy():
    fx = _setup(CHECKHASH_EXIT="1")
    _bundle_current(fx)

    r = _deploy(fx)

    pylog = _read(fx.logs["py"])
    # Discrimination: exit 1 alone is what ANY broken/absent step produces.
    # The check-hash must actually have been ATTEMPTED, and the message must
    # name the subject -- otherwise this test passes on a tree where the graph
    # step was never written at all.
    assert "--check-hash" in pylog, (
        "the pin was never verified with --check-hash, so this run says "
        "nothing about a check-hash FAILURE" + _ctx(r, "py log:\n" + pylog))
    assert r.returncode == 1, (
        "a pin that does not verify means capture.sh step [2b] will report "
        "drift and capture_verdict.py will fail the whole capture; the deploy "
        "must not report success over that" + _ctx(r))
    assert re.search(r"graph|pin|check-hash", _low(r)), (
        "the failure must name the graph content pin as the failing step"
        + _ctx(r))


def test_health_diagnoses_are_distinct():
    # nothing listening on the port
    fx = _setup(CURL_MODE="down")
    _bundle_current(fx)
    r = _deploy(fx)
    low = _low(r)
    assert r.returncode == 1, _ctx(r)
    assert "listening" in low and "port" in low, (
        "a 000 from curl means nothing was listening on the port you chose -- "
        "that is a different diagnosis from a version mismatch" + _ctx(r))
    assert "9.9.9" not in _out(r)

    # the SPA bundle is missing: 503, and there is no point polling
    fx = _setup(CURL_MODE="bundle_missing")
    _bundle_current(fx)
    r = _deploy(fx, "--timeout", "10", "--interval", "1")
    low = _low(r)
    assert r.returncode == 1, _ctx(r)
    assert "503" in _out(r), "the 503 must be reported as a 503" + _ctx(r)
    assert "bundle" in low or "dist" in low, (
        "503 means the SPA bundle was not found -- say so" + _ctx(r))
    assert _curl_calls(fx) <= 2, (
        "a 503 is a definite answer; polling it for the full budget wastes the "
        "operator's time" + _ctx(r, "curl calls: %s" % _curl_calls(fx)))

    # the service answered, with the wrong version
    fx = _setup(CURL_MODE="wrongversion")
    _bundle_current(fx)
    r = _deploy(fx)
    out = _out(r)
    assert r.returncode == 1, _ctx(r)
    assert "9.9.9" in out and TREE_VERSION in out, (
        "a version mismatch must name BOTH what was seen and what the tree "
        "says -- stale bytecode and a restart that did not take look identical "
        "otherwise" + _ctx(r))


def test_versionless_then_healthy_retries():
    fx = _setup(CURL_MODE="versionless_then_healthy")
    _bundle_current(fx)

    r = _deploy(fx, "--timeout", "10", "--interval", "1")

    assert r.returncode == 0, (
        "a health response that has not yet grown a version field must be "
        "retried, not treated as a mismatch" + _ctx(r))
    assert _curl_calls(fx) >= 2, (
        "the poll gave up after one response" + _ctx(r,
        "curl calls: %s" % _curl_calls(fx)))


def test_root_url_confirmed_separately_from_api_health():
    """A 200 from `/api/health` does not mean `/` serves; only `/` proves that.

    UNTESTED UNTIL NOW, for a harness reason rather than an oversight: the curl
    shim answered every URL identically, so /api/health and / could never
    disagree -- which is the only condition this confirmation exists to detect.
    A denominator that structurally excludes its subject reports clean
    (CLAUDE.md section 0). ROOT_CODE makes the shim discriminate by URL.

    All three arms are asserted, including the SILENT one: a check that fires
    when the root is healthy would be a gate firing on identity, and those get
    switched off.
    """
    # (a) health is fine, the root is 503 -> the SPA bundle is not being served.
    #     Distinct diagnosis and distinct remedy from a 503 on /api/health.
    fx = _setup(CURL_MODE="healthy", ROOT_CODE="503")
    _bundle_current(fx)
    r = _deploy(fx)
    assert r.returncode == 1, (
        "/api/health answered 200 with the right version and / answered 503; "
        "reporting that as a verified deploy is a false SUCCESS -- the app is "
        "up and serving nothing a browser can use" + _ctx(r))
    # Anchored on STDERR, where die() writes, and on the wording specific to
    # THIS diagnosis. A first version of this assertion searched the whole
    # output for "dist" -- which step [6] prints on every skipped-build run
    # ("frontend/dist/index.html present"), so it was satisfied by unrelated
    # stdout and could never fail. It graded the generic "expected 200" fallback
    # as a correct bundle diagnosis (CLAUDE.md section 0).
    err = r.stderr
    assert "503" in err, "the 503 must be reported as a 503" + _ctx(r)
    assert "bundle" in err.lower(), (
        "a 503 on / while /api/health is healthy has its own remedy -- rebuild "
        "frontend/dist -- and must not collapse into the generic 'expected 200' "
        "message that any other code produces" + _ctx(r))
    assert "/api/health" in err, (
        "the message must say WHICH of the two probes disagreed; that is the "
        "whole reason the root is confirmed separately" + _ctx(r))

    # (b) health is fine, the root is 404 -> still a failure, and the message
    #     must carry the code RECEIVED, not the code demanded.
    fx = _setup(CURL_MODE="healthy", ROOT_CODE="404")
    _bundle_current(fx)
    r = _deploy(fx)
    out = _out(r)
    assert r.returncode == 1, (
        "GET / returned 404 and the deploy reported success" + _ctx(r))
    assert "404" in out and "200" in out, (
        "the failure must name BOTH what was received and what was expected"
        + _ctx(r))
    assert not re.search(r"GET / = 200", out), (
        "the success note restated the constant it was supposed to have "
        "verified: it printed 'GET / = 200' having received 404. A message "
        "that echoes its own literal cannot contradict a weakened check"
        + _ctx(r))

    # (c) both probes agree -> exit 0, and the note echoes the code received.
    fx = _setup(CURL_MODE="healthy", ROOT_CODE="200")
    _bundle_current(fx)
    r = _deploy(fx)
    assert r.returncode == 0, (
        "/api/health and / both answered 200 and the deploy still failed; a "
        "gate that fires on identity gets switched off" + _ctx(r))
    assert "GET / = 200" in _out(r), (
        "the health note must report the root probe it actually made" + _ctx(r))


def test_stop_failure_blocks_mutating_window():
    fx = _setup(STOP_EXIT="1")
    _bundle_current(fx)
    pkg_pyc = _write(os.path.join(fx.clone, "bulk_downloader", "__pycache__",
                                  "x.cpython-312.pyc"), "stale")

    r = _deploy(fx)

    svclog = _read(fx.logs["systemctl"])
    # Discrimination: without this, a script that never reaches the service at
    # all produces exactly the same three observations below.
    assert "stop bulkdownloader" in svclog, (
        "`systemctl stop bulkdownloader` was never issued, so this run says "
        "nothing about a stop FAILURE" + _ctx(r, "systemctl log:\n" + svclog))
    assert r.returncode == 1, (
        "if the service will not stop, the steps that follow are unsafe"
        + _ctx(r))
    assert pkg_pyc.is_file(), (
        "the bytecode sweep ran even though the service was never stopped"
        + _ctx(r))
    assert _lines(fx.logs["inv"]) == [], (
        "the parity regen ran even though the service was never stopped"
        + _ctx(r, "inventory log: %r" % _read(fx.logs["inv"])))


# ─────────────── tools/check_requirements.py -- one test per OUTCOME
#
# The helper has FOUR outcomes, not three, and the fourth is the one a
# `pip check`-shaped gate always gets wrong: a file that is READABLE but
# declares ZERO requirement names. `unresolved([])` is `[]`, so "every entry
# resolves" comes out true over an empty denominator -- true and useless. That
# is the helper's own docstring subject one level up, and it is exactly the
# shape CLAUDE.md section 2 records for bd-guardcheck, which reported
# "0 ok, 0 drifted, 7 missing" and EXITED 0 on a clean tree until v3.66.818.
# A zero-in-every-bucket summary is a failure signal, not a pass.
#
# One test per outcome so a regression names WHICH outcome broke rather than
# collapsing four subjects into one assertion chain. Each states its own
# DENOMINATOR -- how many names the file actually parsed to -- before believing
# an exit code, because every one of these codes is reachable for a wrong
# reason.


def _require_check_req_tool():
    """Preconditions, asserted BY NAME before any exit code is believed.

    Discrimination: without this, `python tools/check_requirements.py` on a
    tree where the helper is missing exits 2 -- which is also the contract's
    'unevaluable' code -- so both exit-2 cases below would pass for entirely
    the wrong reason.
    """
    assert CHECK_REQ.is_file(), (
        "tools/check_requirements.py does not exist. It is the shared "
        "requirements-resolution helper that scripts/deploy.sh and "
        "scripts/cloud-setup.sh must BOTH call; three inlined copies is the "
        "denominator that drifts (CLAUDE.md section 5).")
    assert VENV_PY.is_file(), (
        "venv/bin/python is missing at %s; this test measures the helper under "
        "the interpreter whose site-packages it is asked about. If you are in "
        "a git WORKTREE, this is environmental and not a subject failure: "
        "venv/ is gitignored, so a worktree never has one. Fix the environment "
        "(symlink the main checkout's venv in, or run from the main checkout) "
        "rather than skipping -- two mutation batteries lost a run to this and "
        "neither said 'worktree'." % VENV_PY)


def _parsed_names(body):
    """The names the helper itself parses out of `body`.

    Imported rather than re-implemented so the denominator each test states is
    the one the subject actually uses. `tests/` is outside the import-graph
    gate's frozen surface (bulk_downloader/ + tools/), so this adds no edge to
    the baseline that gate freezes.
    """
    import tools.check_requirements as cr
    return cr.requirement_names(body)


def _run_check_req(body):
    """Run the helper under the REAL venv python over `body`.

    `body is None` means no requirements.txt exists at all -- the unreadable
    case. Every call gets a fresh tmpdir so no run inherits another's file.
    """
    _require_check_req_tool()
    work = tempfile.mkdtemp(prefix="bd_reqtool_")
    if body is not None:
        _write(os.path.join(work, "requirements.txt"), body)
    return subprocess.run([str(VENV_PY), str(CHECK_REQ)], cwd=work,
                          capture_output=True, text=True, timeout=120)


def test_check_requirements_zero_names_is_unevaluable():
    """A readable file declaring NO names is UNEVALUABLE (2), not satisfied.

    Both shapes that reach zero names are exercised: a genuinely empty file,
    and one carrying only comments, blanks and option lines. Reachable in the
    field from a truncated write, a caller handed a path that exists but is
    the wrong file, or a refactor that moved the deps and left a stub behind.
    """
    _require_check_req_tool()
    shapes = (
        ("an empty file", ""),
        ("comments, blanks and option lines only",
         "# runtime deps\n\n-e .\n--index-url https://example.invalid\n"),
    )
    for label, body in shapes:
        assert _parsed_names(body) == [], (
            "harness error, NOT a subject failure: %s was expected to parse "
            "to zero requirement names, got %r" % (label, _parsed_names(body)))

        r = _run_check_req(body)

        assert r.returncode == 2, (
            "%s parsed to ZERO requirement names, so nothing was verified. "
            "unresolved([]) is [], which makes 'every entry resolves' true "
            "over an empty denominator -- true and useless. Unknown is a "
            "third state and it fails (CLAUDE.md section 0)." % label + _ctx(r))
        assert r.stdout.strip() == "", (
            "stdout must stay empty so a caller reading it for package names "
            "never mistakes a diagnostic for a package" + _ctx(r))
        assert re.search(r"zero requirement names|no requirement names",
                         r.stderr.lower()), (
            "the refusal must SAY the file parsed to zero requirement names. "
            "Exit 2 alone is also what an unreadable file produces, so a bare "
            "code does not tell the operator which condition fired" + _ctx(r))


def test_check_requirements_all_resolve_is_silent_exit0():
    _require_check_req_tool()
    body = "# comment\n-e .\npytest\n"
    names = _parsed_names(body)
    assert names, (
        "harness error, NOT a subject failure: the PASS case must have a "
        "non-empty denominator, or it proves only that nothing was checked")

    r = _run_check_req(body)

    assert r.returncode == 0, (
        "a satisfied requirements.txt (%r) must exit 0" % (names,) + _ctx(r))
    assert r.stdout.strip() == "", ("exit 0 must be silent" + _ctx(r))


def test_check_requirements_unresolved_names_exit1():
    _require_check_req_tool()
    body = "# comment\n-e .\npytest\nbd-absent-package-zzz>=1.0\n"
    names = _parsed_names(body)
    assert "pytest" in names and "bd-absent-package-zzz" in names, (
        "harness error, NOT a subject failure: the parse must see BOTH a "
        "resolvable and an unresolvable name or an exit 1 here is not about "
        "the mix; parsed %r" % (names,))

    r = _run_check_req(body)

    assert r.returncode == 1, (
        "an unresolvable requirement must exit 1" + _ctx(r))
    assert "bd-absent-package-zzz" in r.stdout, (
        "the missing distribution names must be printed on stdout" + _ctx(r))
    assert "pytest" not in r.stdout, (
        "only the UNRESOLVED names belong on stdout; a caller installs what "
        "it reads there" + _ctx(r))


def test_check_requirements_unreadable_is_exit2():
    _require_check_req_tool()

    r = _run_check_req(None)

    assert r.returncode == 2, (
        "an unevaluable check is its own state (exit 2) and must never be "
        "confused with 'satisfied'" + _ctx(r))
    assert r.stdout.strip() == "", (
        "stdout must stay empty so an error message is never read as a "
        "package name" + _ctx(r))
    assert "requirements.txt" in r.stderr, (
        "the refusal must name the file it could not read" + _ctx(r))


def test_cloud_setup_uses_shared_checker():
    """cloud-setup.sh must CALL the helper, not carry a second copy of it.

    The heredoc is located by its balanced `<<'DELIM'` / `DELIM` pair, never by
    a character offset -- a fixed-width slice of a shell construct swallows its
    own terminator and reports a bash syntax error as a subject failure
    (CLAUDE.md section 2a).
    """
    src = CLOUD_SETUP.read_text(encoding="utf-8")
    lines = src.splitlines()

    bodies = {}
    i = 0
    while i < len(lines):
        m = re.search(r"<<-?'([A-Za-z_][A-Za-z0-9_]*)'", lines[i])
        if m:
            delim = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].strip() != delim:
                j += 1
            assert j < len(lines), (
                "unterminated heredoc %r opened at line %d -- the file itself "
                "is malformed, this is not a subject failure" % (delim, i + 1))
            bodies.setdefault(delim, []).append("\n".join(lines[i + 1:j]))
            i = j + 1
            continue
        i += 1

    inline = [d for d, bs in bodies.items()
              if any("PackageNotFoundError" in b for b in bs)]
    assert not inline, (
        "scripts/cloud-setup.sh still carries an inline importlib.metadata "
        "requirements check in heredoc(s) %s. `pip install -r` exiting 0 is "
        "not proof every requirement resolves, and that logic now belongs in "
        "tools/check_requirements.py so the deploy path and the provisioner "
        "cannot drift apart." % ", ".join(sorted(inline)))
    assert "tools/check_requirements.py" in src, (
        "scripts/cloud-setup.sh does not invoke tools/check_requirements.py; "
        "the requirements-satisfaction row must come from the shared helper")


def test_capture_home_mismatch_reported_when_bd_home_is_unset():
    """Step [10]'s BD_HOME warning must ask capture.sh's question, not its own.

    capture.sh:55 does `BD_HOME="${BD_HOME:-$HOME/BulkDownloader}"` -- it
    DEFAULTS the variable when it is unset, then reads
    "$BD_HOME/reports/gui_parity_inventory.json". So the question that decides
    whether the copy this script just refreshed is the copy the suite will read
    is "does $DIR equal that EFFECTIVE path", and it has an answer whether or
    not BD_HOME happens to be exported. A warning gated on `[ -n "$BD_HOME" ]`
    asks a different question and stays silent in the default case -- which is
    the common one, since an operator only exports BD_HOME by hand when they
    already suspect a mismatch (CLAUDE.md section 0).

    Both directions are asserted: silence when the effective path IS the
    install dir (a gate that fires on identity gets switched off), and a
    warning when it is not.
    """
    # (a) BD_HOME unset, --dir is NOT $HOME/BulkDownloader -> must warn.
    fx = _setup()
    fx.env.pop("BD_HOME", None)
    _bundle_current(fx)

    r = _deploy(fx)

    out = _out(r)
    assert r.returncode == 0, _ctx(r)
    assert "BD_HOME" in out, (
        "step [10] said nothing about BD_HOME while the deploy dir (%s) is not "
        "capture.sh's effective BD_HOME (%s/BulkDownloader); the v3.66.818 "
        "staleness comes straight back and nothing said so"
        % (fx.clone, fx.work) + _ctx(r))
    assert "is not this install dir" in out, (
        "the mismatch must be NAMED, not merely alluded to" + _ctx(r))
    assert os.path.join(fx.work, "BulkDownloader") in out, (
        "the warning must print the path capture.sh will actually read, so the "
        "operator can act on it without re-deriving the default" + _ctx(r))

    # (b) BD_HOME unset, and $HOME/BulkDownloader IS this install dir -> silent.
    fx2 = _setup()
    fx2.env.pop("BD_HOME", None)
    os.symlink(fx2.clone, os.path.join(fx2.work, "BulkDownloader"))
    _bundle_current(fx2)

    r2 = _deploy(fx2)

    assert r2.returncode == 0, _ctx(r2)
    assert "is not this install dir" not in _out(r2), (
        "the effective BD_HOME resolves to this very install dir, so there is "
        "nothing to warn about; a gate that fires on identity gets switched off"
        + _ctx(r2))
