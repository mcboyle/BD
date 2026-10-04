"""O1698 / FR-31b (row o1698-31b-write-hooks-warn): write hooks WARN, the collect gate REFUSES.

Candidates live outside the repo under harness-work/FIX/o1698-31b-write-hooks-warn/ (the PM installs after BOARD):
  bd-tripwire-any.py       Edit/Write/MultiEdit/NotebookEdit + AGY write tools into the main checkout: exit 0 / allow,
                           stderr WARNING, `WARNED T16 seat=<seat> path=<JSON string | ?>` log line; Bash-shape T16 refuses.
  bd-checkout-guard-hook.py  leaves write tools to bd-tripwire-any.py (same PreToolUse entry); Bash refusals unchanged.
  bd-t16-collect-check.sh  REFUSE (rc 1) a seat whose warned write is still live in the checkout; UNKNOWN (rc 4) when a
                           warned line of the seat names no parseable path inside the cut's lifetime (r2, A2 REFUTE: never
                           a false NONE, FR-6); NOTE rc 0 otherwise.
  bd-precollect-guard.sh   local mode calls the check (lifetime = mtime of the cut's .git) and refuses the DONE.
Opt-in: BD_O1698_31B_WRITE_HOOKS_WARN_CANDIDATE=<FIX dir>. A supplied dir missing a candidate FAILS, never skips.
Every fixture is a temp git repo / temp log: no live log, checkout or remote is touched.
"""

from __future__ import annotations

import calendar
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_31B_WRITE_HOOKS_WARN_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

SEAT = "bd-worker-X"
MAIN = "/home/mboyle/BulkDownloader"   # the hook's protected root; only ever named in a temp log, never stat'ed
OLD_TS = "2026-01-01T00:00:00Z"        # older than any fixture cut
OLD_EPOCH = calendar.timegm(time.strptime(OLD_TS, "%Y-%m-%dT%H:%M:%SZ"))


def _cand(name: str) -> Path:
    p = Path(CANDIDATE) / name
    assert p.is_file(), f"candidate {p} is absent (opt-in was supplied)"
    return p


def _git_env(tmp: Path) -> dict[str, str]:
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(LC_ALL="C", HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return env


def _canon(tmp: Path, env: dict[str, str]) -> Path:
    canon = tmp / "canon"
    subprocess.run(["git", "init", "-q", "-b", "main", str(canon)], check=True, env=env)
    (canon / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(canon), "add", "a.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(canon), "commit", "-qm", "base"], check=True, env=env)
    return canon.resolve()


def _usos(tmp: Path, env: dict[str, str]) -> Path:
    usos = tmp / "usos"
    if (usos / ".git").exists():
        return usos.resolve()
    subprocess.run(["git", "init", "-q", "-b", "main", str(usos)], check=True, env=env)
    (usos / "u.txt").write_text("u\n")
    subprocess.run(["git", "-C", str(usos), "add", "u.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(usos), "commit", "-qm", "base"], check=True, env=env)
    return usos.resolve()


def _w(seat: str, path: str | Path) -> str:
    """A WARNED line in the candidate hook's format (path JSON-quoted)."""
    return f"WARNED T16 seat={seat} path={json.dumps(str(path))}"


def _log(tmp: Path, *lines: str, ts: str = OLD_TS) -> Path:
    log = tmp / "tripwire.log"
    log.write_text("".join(f"{ts} {line} :: 'Edit x'\n" for line in lines))
    return log


def _check(seat: str, canon: Path | str, log: Path | None, env: dict[str, str],
           since: int | None = None) -> subprocess.CompletedProcess[str]:
    """canon: one root, or a colon-separated root list (r4: every T16 protected root)."""
    first = Path(str(canon).split(":")[0])
    e = dict(env, BD_T16_LOG=str(log) if log else str(first.parent / "no-such.log"))
    argv = ["bash", str(_cand("bd-t16-collect-check.sh")), seat, str(canon)] + ([str(since)] if since is not None else [])
    return subprocess.run(argv, capture_output=True, text=True, env=e, timeout=60)


def test_tripwire_any_selftest_write_tools_warn(tmp_path: Path) -> None:
    env = dict(os.environ, LC_ALL="C", BD_TRIPWIRE_LOG=str(tmp_path / "t.log"))
    r = subprocess.run([sys.executable, str(_cand("bd-tripwire-any.py")), "--selftest"],
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest write-hooks-warn OK (15 cases)" in r.stdout, r.stdout


def test_checkout_guard_selftest_leaves_write_tools(tmp_path: Path) -> None:
    env = dict(os.environ, LC_ALL="C", BD_CHECKOUT_GUARD_LOG=str(tmp_path / "cg.log"))
    r = subprocess.run([sys.executable, str(_cand("bd-checkout-guard-hook.py")), "--selftest"],
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checkout-guard selftest OK (11 cases)" in r.stdout, r.stdout


def test_collect_check_refuses_a_live_warned_write(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / "x.py").write_text("leak\n")                    # untracked write left in the checkout
    (canon / "a.txt").write_text("changed\n")                # tracked file modified
    log = _log(tmp_path, _w(SEAT, canon / "x.py"), _w(SEAT, canon / "a.txt"))
    r = _check(SEAT, canon, log, env)
    assert r.returncode == 1, r.stdout
    assert r.stdout.startswith("REFUSE -- T16 WRITE LEFT IN A PROTECTED ROOT"), r.stdout
    assert f"{canon}/x.py" in r.stdout and f"{canon}/a.txt" in r.stdout and "2 path(s)" in r.stdout, r.stdout


def test_collect_check_path_with_whitespace_is_judged_whole(tmp_path: Path) -> None:
    # A2 REFUTE (B): `path=([^ ]+)` cut "<canon>/my notes.md" to "<canon>/my" (clean) -> false "since undone".
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / "my notes.md").write_text("leak\n")
    r = _check(SEAT, canon, _log(tmp_path, _w(SEAT, canon / "my notes.md")), env)
    assert r.returncode == 1 and f"{canon}/my notes.md" in r.stdout, r.stdout


def test_collect_check_path_is_a_literal_pathspec(tmp_path: Path) -> None:
    # B1 lens r4: `git status -- <path>` reads the path as a pathspec; ":(attr:zz)a.py" matched nothing -> false NONE,
    # and "f[1].py" globbed onto another seat's "f1.py" -> false REFUSE. GIT_LITERAL_PATHSPECS=1 judges the path itself.
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / ":(attr:zz)a.py").write_text("leak\n")
    r = _check(SEAT, canon, _log(tmp_path, _w(SEAT, canon / ":(attr:zz)a.py")), env)
    assert r.returncode == 1 and f"{canon}/:(attr:zz)a.py" in r.stdout, r.stdout
    (canon / ":(attr:zz)a.py").unlink()
    (canon / "f1.py").write_text("other seat\n")
    r = _check(SEAT, canon, _log(tmp_path, _w(SEAT, canon / "f[1].py")), env)
    assert r.returncode == 0 and "1 since undone" in r.stdout, r.stdout


def test_collect_check_unparseable_warned_path_is_unknown_inside_the_lifetime(tmp_path: Path) -> None:
    # A2 REFUTE (A): an unparseable write-tool payload is WARNED with path=? ; it must never read as FOUND NONE.
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / "x.py").write_text("leak\n")
    for line in (f"WARNED T16 seat={SEAT} path=? (unreadable hook payload: JSONDecodeError)",
                 f"WARNED T16 seat={SEAT} path={canon}/x.py",          # unquoted: not the hook's format
                 f'WARNED T16 seat={SEAT} path="{canon}/unterminated'):
        log = _log(tmp_path, line)
        r = _check(SEAT, canon, log, env)                               # no lifetime given: all lines count
        assert r.returncode == 4 and r.stdout.startswith("UNKNOWN -- T16"), (line, r.stdout)
        r = _check(SEAT, canon, log, env, since=OLD_EPOCH - 60)        # line inside the cut's lifetime
        assert r.returncode == 4, (line, r.stdout)
        r = _check(SEAT, canon, log, env, since=OLD_EPOCH + 60)        # older than the cut, but the checkout is dirty
        assert r.returncode == 4 and "older than this cut" in r.stdout, (line, r.stdout)
    # older than the cut AND the checkout is clean -> NONE is provably true (r3, C4 note)
    (canon / "x.py").unlink()
    r = _check(SEAT, canon, _log(tmp_path, f"WARNED T16 seat={SEAT} path=?"), env, since=OLD_EPOCH + 60)
    assert r.returncode == 0 and "1 unjudgeable before" in r.stdout, r.stdout
    (canon / "x.py").write_text("leak\n")
    # a live path still refuses whatever its age (a live write in the checkout is never stale)
    r = _check(SEAT, canon, _log(tmp_path, _w(SEAT, canon / "x.py")), env, since=OLD_EPOCH + 60)
    assert r.returncode == 1, r.stdout


def test_collect_check_never_writes_the_checkout_index(tmp_path: Path) -> None:
    # r3 (C4 BOUNCE): plain `git status` refreshes stat info and rewrites <canon>/.git/index (+ index.lock) -- a seat
    # writing the main checkout's .git from the gate meant to stop seat writes. A touched, content-clean warned file
    # is exactly the stat-dirty case; the check must leave the index byte- and mtime-identical.
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    index = canon / ".git" / "index"
    # stat-dirty, content clean; a PAST mtime (a future one is "racily clean" and git would not rewrite the index)
    os.utime(canon / "a.txt", (time.time() - 3600, time.time() - 3600))
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    for line in (_w(SEAT, canon / "a.txt"), f"WARNED T16 seat={SEAT} path=?"):  # per-path, then whole-tree status
        r = _check(SEAT, canon, _log(tmp_path, line), env, since=OLD_EPOCH + 60)
        assert r.returncode == 0, r.stdout
        assert (index.read_bytes(), index.stat().st_mtime_ns) == before, "the check rewrote <canon>/.git/index"
        assert not (canon / ".git" / "index.lock").exists()
    # control: the same plain status DOES rewrite this index (the probe can say yes)
    subprocess.run(["git", "-C", str(canon), "status", "--porcelain"], env=env, capture_output=True, check=True)
    assert (index.read_bytes(), index.stat().st_mtime_ns) != before, "fixture is not stat-dirty; control cannot fail"


def test_collect_check_ignored_path_is_not_undone(tmp_path: Path) -> None:
    # r4 (A1 REFUTE F1): `status --ignored=no` prints nothing for a gitignored file, so a warned write to bd_home/ or
    # *.db read as "since undone". An ignored file that is still present cannot be shown undone -> REFUSE inside the
    # cut's lifetime; before the cut it is a NOTE (it cannot be pinned on this cut); deleted -> undone.
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / ".gitignore").write_text("*.db\nbd_home/\n")
    subprocess.run(["git", "-C", str(canon), "add", ".gitignore"], check=True, env=env)
    subprocess.run(["git", "-C", str(canon), "commit", "-qm", "ignore"], check=True, env=env)
    (canon / "bd_home").mkdir()
    (canon / "bd_home" / "creds.json").write_text("{}\n")
    (canon / "hist.db").write_text("x\n")
    log = _log(tmp_path, _w(SEAT, canon / "bd_home" / "creds.json"), _w(SEAT, canon / "hist.db"))
    r = _check(SEAT, canon, log, env, since=OLD_EPOCH - 60)
    assert r.returncode == 1 and f"{canon}/bd_home/creds.json" in r.stdout and f"{canon}/hist.db" in r.stdout, r.stdout
    assert "ignored" in r.stdout, r.stdout
    r = _check(SEAT, canon, log, env, since=OLD_EPOCH + 60)
    assert r.returncode == 0 and "2 ignored-present before this cut" in r.stdout, r.stdout
    (canon / "hist.db").unlink()
    (canon / "bd_home" / "creds.json").unlink()
    r = _check(SEAT, canon, log, env, since=OLD_EPOCH - 60)
    assert r.returncode == 0 and "2 since undone" in r.stdout, r.stdout


def test_collect_check_judges_every_protected_root(tmp_path: Path) -> None:
    # r4 (A1 REFUTE F2): the hook warns writes into UniversalSwarmOS too; a check that judged only the main checkout left
    # them with no refusal anywhere. The root list is colon-separated; each root is judged the same way.
    env = _git_env(tmp_path)
    canon, usos = _canon(tmp_path, env), _usos(tmp_path, env)
    (usos / "zz.md").write_text("leak\n")
    log = _log(tmp_path, _w(SEAT, usos / "zz.md"))
    r = _check(SEAT, f"{canon}:{usos}", log, env)
    assert r.returncode == 1 and f"{usos}/zz.md" in r.stdout, r.stdout
    assert _check(SEAT, canon, log, env).returncode == 0       # control: the root list decides what is judged
    (usos / "zz.md").unlink()
    r = _check(SEAT, f"{canon}:{usos}", log, env)
    assert r.returncode == 0 and "1 since undone" in r.stdout, r.stdout
    assert _check(SEAT, f"{canon}:{tmp_path / 'home'}", log, env).returncode == 4   # a root that is not a repo


def test_collect_check_controls(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    (canon / "x.py").write_text("leak\n")
    # another seat's live / unparseable write is not this seat's; a .git path is plumbing
    log = _log(tmp_path, _w("bd-worker-Y", canon / "x.py"), "WARNED T16 seat=bd-worker-Y path=?",
               _w(SEAT, canon / ".git" / "HEAD"))
    r = _check(SEAT, canon, log, env)
    assert r.returncode == 0 and "FOUND NONE live (0 warned" in r.stdout, r.stdout
    # the same seat's write, since undone (clean again): NOTE, rc 0
    log = _log(tmp_path, _w(SEAT, canon / "a.txt"))
    r = _check(SEAT, canon, log, env)
    assert r.returncode == 0 and "FOUND NONE live (1 warned path(s)" in r.stdout and "1 since undone" in r.stdout, r.stdout
    # could not look -> UNKNOWN rc 4, never a pass (FR-6)
    assert _check(SEAT, canon, None, env).returncode == 4
    assert _check("not-a-seat", canon, log, env).returncode == 4
    assert _check(SEAT, tmp_path / "home", log, env).returncode == 4
    assert _check(SEAT, canon, log, env, since="x").returncode == 4  # type: ignore[arg-type]


def test_hook_log_lines_round_trip_into_the_check(tmp_path: Path) -> None:
    """The check reads what the candidate hook actually writes (not a hand-written line)."""
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    log = tmp_path / "hook.log"
    henv = dict(env, BD_SEAT=SEAT, BD_TRIPWIRE_LOG=str(log), PWD=str(tmp_path))

    def hook(payload: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(_cand("bd-tripwire-any.py"))], input=payload, capture_output=True,
                              text=True, env=henv, cwd=tmp_path, timeout=60)

    # (A) truncated Edit payload -> WARN + `path=?` -> the check says UNKNOWN, never NONE
    r = hook('{"tool_name": "Edit", "tool_input": {"file_path": "' + MAIN + '/x.py"')
    assert r.returncode == 0 and "bd-tripwire: WARNING" in r.stderr, r.stderr
    assert _check(SEAT, canon, log, env, since=0).returncode == 4
    # (B) a path with a space and a quote, as the hook logs it; re-rooted on the fixture checkout
    log.write_text("")
    name = 'my "odd" notes.md'
    r = hook(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{MAIN}/{name}"}}))
    assert r.returncode == 0 and "bd-tripwire: WARNING" in r.stderr, r.stderr
    line = log.read_text()
    prefix = json.dumps(MAIN + "/")[:-1]
    assert line.count(prefix) == 1, line
    log.write_text(line.replace(prefix, json.dumps(str(canon) + "/")[:-1]))
    (canon / name).write_text("leak\n")
    r = _check(SEAT, canon, log, env, since=0)
    assert r.returncode == 1 and f"{canon}/{name}" in r.stdout, r.stdout


def _cut(tmp: Path, canon: Path, env: dict[str, str], dirname: str, seat_line: str) -> Path:
    base = subprocess.run(["git", "-C", str(canon), "rev-parse", "HEAD"], capture_output=True, text=True,
                          env=env, check=True).stdout.strip()
    cut = tmp / dirname
    subprocess.run(["git", "-C", str(canon), "worktree", "add", "-q", "--detach", str(cut), base], check=True, env=env)
    (cut / "b.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(cut), "add", "b.txt"], check=True, env=env)
    (cut / "DONE.md").write_text(f"VERDICT: PATCH\n{seat_line}BASE: {base}\nRED COMMAND: true\n")
    return cut


def _guard(cut: Path, canon: Path, log: Path, env: dict[str, str],
           usos: Path | None = None) -> subprocess.CompletedProcess[str]:
    genv = dict(env, BD_GUARD_CANON=str(canon), BD_T16_LOG=str(log),
                BD_GUARD_T16_ROOTS=f"{canon}:{usos or _usos(cut.parent, env)}",
                BD_GUARD_T16_CHECK=str(_cand("bd-t16-collect-check.sh")))
    return subprocess.run(["bash", str(_cand("bd-precollect-guard.sh")), "local", str(cut)],
                          capture_output=True, text=True, env=genv, timeout=120)


# seat source: DONE.md `SEAT:` line (worktree name carries no seat), else the `-bd-<seat>` worktree suffix
@pytest.mark.parametrize("dirname,seat_line", [("cut-plain", f"SEAT: {SEAT}\n"), (f"cut-{SEAT}", "")],
                         ids=["done-seat-line", "worktree-suffix"])
def test_precollect_guard_refuses_the_done_of_the_seat_that_wrote_main(tmp_path: Path, dirname: str,
                                                                        seat_line: str) -> None:
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    cut = _cut(tmp_path, canon, env, dirname, seat_line)
    (canon / "x.py").write_text("leak\n")
    log = _log(tmp_path, _w(SEAT, canon / "x.py"))
    r = _guard(cut, canon, log, env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"REFUSE local {cut} -- T16 WRITE LEFT IN A PROTECTED ROOT" in r.stdout, r.stdout
    (canon / "x.py").unlink()                                   # the seat undoes its write
    r = _guard(cut, canon, log, env)
    assert "T16 WRITE LEFT" not in r.stdout and "FOUND NONE live" in r.stdout, r.stdout
    assert r.returncode == 0 and r.stdout.splitlines()[-1].startswith(f"OK local {cut}"), r.stdout + r.stderr


def test_precollect_guard_unparseable_warn_in_this_cut_is_unknown(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    cut = _cut(tmp_path, canon, env, "cut-plain", f"SEAT: {SEAT}\n")
    unk = f"WARNED T16 seat={SEAT} path=? (unreadable hook payload: JSONDecodeError)"
    # written after the cut's .git existed -> inside its lifetime -> UNKNOWN rc 4
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 5))
    r = _guard(cut, canon, _log(tmp_path, unk, ts=now), env)
    assert r.returncode == 4 and f"UNKNOWN local {cut} -- T16" in r.stdout, r.stdout + r.stderr
    # the same line from before this cut existed is not this cut's -> NOTE, OK
    r = _guard(cut, canon, _log(tmp_path, unk, ts=OLD_TS), env)
    assert r.returncode == 0 and r.stdout.splitlines()[-1].startswith(f"OK local {cut}"), r.stdout + r.stderr


def test_precollect_guard_refuses_a_live_write_in_the_second_root(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    canon, usos = _canon(tmp_path, env), _usos(tmp_path, env)
    cut = _cut(tmp_path, canon, env, "cut-plain", f"SEAT: {SEAT}\n")
    (usos / "zz.md").write_text("leak\n")
    r = _guard(cut, canon, _log(tmp_path, _w(SEAT, usos / "zz.md")), env, usos=usos)
    assert r.returncode == 1 and f"{usos}/zz.md" in r.stdout, r.stdout + r.stderr


def test_precollect_guard_without_a_seat_is_unknown(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    canon = _canon(tmp_path, env)
    cut = _cut(tmp_path, canon, env, "cut-plain", "")
    r = _guard(cut, canon, _log(tmp_path, _w(SEAT, canon / "x.py")), env)
    assert r.returncode == 4 and f"UNKNOWN local {cut} -- T16 collect check" in r.stdout, r.stdout + r.stderr
