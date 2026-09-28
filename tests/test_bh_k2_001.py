"""BH-K2-001 re-cut (O1484 P2 fix queue, bd-worker-A2-A): findings kimi-audit-2-003 (council claim keeper)
and 008 (auto-away marker) only.

Both lenses REFUTED gen 1 as stale against live (harness-cuts/bh-k2-001-bd-kimi-worker-2/.review). Findings
001/005/007/009/012/014/015/016/017 are SUPERSEDED by landed BH-RB-2 a05b401, BH-RB-4 a6a8e8d, BH-RB-3 2dfb1c8 and
O1457 b8a415a (bd-cas-pointer retired). 010 is left to BH-004 and 019 was FOUND NONE (gen 1 DONE).

Candidates live outside the repo under harness-work/FIX/bh-k2-001-r1-bd-worker-A2-A/ (orig/ == live harness at re-cut).
Each class opts in with its own BD_BH_K2_001_<NAME>_CANDIDATE. Every stub (tmux, bd-say) is a PATH/env fixture:
nothing leaves the host and no live harness path is touched.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

FIX_ENV = os.environ.get("BD_BH_K2_001_FIXDIR", "")
FIX = Path(
    FIX_ENV
).resolve()  # Path("") resolves to the cwd, so opt-in is keyed on FIX_ENV below
KEEPER_CAND = os.environ.get("BD_BH_K2_001_KEEPER_CANDIDATE", "")
AUTOAWAY_CAND = os.environ.get("BD_BH_K2_001_AUTOAWAY_CANDIDATE", "")

needs_fixdir = pytest.mark.skipif(
    not FIX_ENV, reason="BD_BH_K2_001_FIXDIR opt-in required"
)


def _skip_no(env: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(not env, reason="candidate opt-in required")


def _run(
    cmd: list[str], env: dict[str, str], timeout: int = 60, **kw: object
) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(
        cmd, env=e, timeout=timeout, capture_output=True, text=True, check=False, **kw
    )


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------- 003 keeper
@needs_fixdir
@_skip_no(KEEPER_CAND)
class TestKeeper:
    def _bins(self, tmp_path: Path, session: str, log: Path) -> Path:
        b = tmp_path / "bin"
        b.mkdir(exist_ok=True)
        # `tmux ls -F '#S'` prints the BARE name; has-session gets "-t NAME:" and
        # tmux session names are CASE-SENSITIVE (that is the finding's defect).
        _stub(
            b / "tmux",
            f"""#!/bin/bash
echo "$@" >> {log}
cmd=$1; arg=$3
case "$cmd" in
  has-session) [ "$arg" = "{session}:" ] && exit 0; exit 1 ;;
  list-panes) echo 4242 ;;
  ls) [ -n "{session}" ] && echo "{session}" ;;
esac
exit 0
""",
        )
        return b

    def test_red_wrong_case_default_skips_forever(self, tmp_path: Path) -> None:
        log = tmp_path / "tmux.log"
        b = self._bins(tmp_path, "AGY-council", log)
        claims = tmp_path / "claims.sh"
        _stub(claims, f'#!/bin/bash\necho "$@" >> {tmp_path}/claims.out\nexit 0\n')
        env = {
            "PATH": f"{b}:{os.environ['PATH']}",
            "BD_ROLE_CLAIM_HELPER": str(claims),
            "BD_OPERATOR_PRESENCE": str(
                _write(tmp_path / "persist/OPERATOR-PRESENCE.md", "PRESENT\n")
            ),
            "BD_COUNCIL_STATE": str(tmp_path / "state"),
        }
        orig = FIX / "orig" / "bd-council-claim-keeper.sh"
        r = _run(["bash", str(orig)], env)
        assert r.returncode == 0
        assert "SKIP AGY-Council: no tmux session" in r.stdout
        assert not (tmp_path / "claims.out").exists(), "claim path must never run"

    def test_green_case_insensitive_resolution_claims(self, tmp_path: Path) -> None:
        log = tmp_path / "tmux.log"
        b = self._bins(tmp_path, "AGY-council", log)
        claims = tmp_path / "claims.sh"
        _stub(
            claims,
            f"""#!/bin/bash
echo "$@" >> {tmp_path}/claims.log
[ "$1" = claim ] && exit 0 || exit 1
""",
        )
        _write(tmp_path / "persist/OPERATOR-PRESENCE.md", "PRESENT\n")
        env = {
            "PATH": f"{b}:{os.environ['PATH']}",
            "BD_ROLE_CLAIM_HELPER": str(claims),
            "BD_OPERATOR_PRESENCE": str(tmp_path / "persist/OPERATOR-PRESENCE.md"),
            "BD_COUNCIL_STATE": str(tmp_path / "state"),
        }
        r = _run(["bash", KEEPER_CAND], env)
        assert "CLAIMED advisor AGY-council pid=4242" in r.stdout, r.stdout
        assert "claim advisor AGY-council 4242" in (tmp_path / "claims.log").read_text()

    def test_green_explicit_wrong_case_env_is_adopted(self, tmp_path: Path) -> None:
        log = tmp_path / "tmux.log"
        b = self._bins(tmp_path, "AGY-council", log)
        claims = tmp_path / "claims.sh"
        _stub(
            claims,
            f"""#!/bin/bash
echo "$@" >> {tmp_path}/claims.out
[ "$1" = claim ] && exit 0 || exit 1
""",
        )
        _write(tmp_path / "persist/OPERATOR-PRESENCE.md", "PRESENT\n")
        env = {
            "PATH": f"{b}:{os.environ['PATH']}",
            "BD_ROLE_CLAIM_HELPER": str(claims),
            "BD_OPERATOR_PRESENCE": str(tmp_path / "persist/OPERATOR-PRESENCE.md"),
            "BD_COUNCIL_STATE": str(tmp_path / "state"),
            "BD_COUNCIL_SEAT": "AGY-Council",
        }
        r = _run(["bash", KEEPER_CAND], env)
        assert "NOTE AGY-Council -> AGY-council (case fix)" in r.stdout, r.stdout
        assert "CLAIMED advisor AGY-council" in r.stdout

    def test_green_skip_counter_alerts(self, tmp_path: Path) -> None:
        log = tmp_path / "tmux.log"
        b = self._bins(tmp_path, "NO-session-at-all", log)
        claims = tmp_path / "claims.sh"
        _stub(claims, "#!/bin/bash\nexit 1\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        _write(tmp_path / "persist/OPERATOR-PRESENCE.md", "PRESENT\n")
        env = {
            "PATH": f"{b}:{os.environ['PATH']}",
            "BD_ROLE_CLAIM_HELPER": str(claims),
            "BD_OPERATOR_PRESENCE": str(tmp_path / "persist/OPERATOR-PRESENCE.md"),
            "BD_COUNCIL_STATE": str(tmp_path / "state"),
            "BD_COUNCIL_SAY": str(say),
            "BD_COUNCIL_SKIP_ALERT_N": "2",
        }
        r1 = _run(["bash", KEEPER_CAND], env)
        assert "SKIP" in r1.stdout and not saylog.exists()
        r2 = _run(["bash", KEEPER_CAND], env)
        assert "SKIP" in r2.stdout
        assert saylog.exists(), "second consecutive SKIP must alert"
        assert "SKIP x2" in saylog.read_text()

    def test_green_garbage_skip_count_restarts(self, tmp_path: Path) -> None:
        log = tmp_path / "tmux.log"
        b = self._bins(tmp_path, "NO-session-at-all", log)
        claims = tmp_path / "claims.sh"
        _stub(claims, "#!/bin/bash\nexit 1\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        _write(tmp_path / "persist/OPERATOR-PRESENCE.md", "PRESENT\n")
        count = _write(tmp_path / "state/council-keeper.skip.count", "08\n")
        env = {
            "PATH": f"{b}:{os.environ['PATH']}",
            "BD_ROLE_CLAIM_HELPER": str(claims),
            "BD_OPERATOR_PRESENCE": str(tmp_path / "persist/OPERATOR-PRESENCE.md"),
            "BD_COUNCIL_STATE": str(tmp_path / "state"),
            "BD_COUNCIL_SAY": str(say),
            "BD_COUNCIL_SKIP_ALERT_N": "2",
        }
        r = _run(["bash", KEEPER_CAND], env)
        assert "SKIP" in r.stdout and "value too great" not in r.stderr, r.stderr
        assert count.read_text() == "1\n" and not saylog.exists()


# -------------------------------------------------------------- 008 auto-away
@needs_fixdir
@_skip_no(AUTOAWAY_CAND)
class TestAutoAway:
    def _env(self, tmp_path: Path, marker: str) -> dict[str, str]:
        p = tmp_path / "persist"
        _write(p / "OPERATOR-PRESENCE.md", "PRESENT\nsince ever\n")
        _write(p / "OPERATOR-LAST-TYPED", marker)
        _write(p / "PM-SEAT", "bd-pm-A\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        return {
            "BD_AUTO_AWAY_PERSIST": str(p),
            "BD_AUTO_AWAY_SAY": str(say),
        }

    def test_red_garbage_marker_instant_false_away(self, tmp_path: Path) -> None:
        seamed = FIX / "red-orig-plus-seams" / "bd-auto-away.sh"
        env = self._env(tmp_path, "garbage\n")
        r = _run(["bash", str(seamed)], env)
        p = tmp_path / "persist"
        assert (p / "OPERATOR-PRESENCE.md").read_text().startswith("AWAY"), (
            "garbage marker flipped AWAY instantly"
        )
        assert re.search(r"AUTO-AWAY \d{6,}m", (tmp_path / "say.log").read_text()), (
            "the PM was told an absurd age"
        )
        assert r.returncode == 0

    def test_red_empty_marker_silent_exit(self, tmp_path: Path) -> None:
        seamed = FIX / "red-orig-plus-seams" / "bd-auto-away.sh"
        env = self._env(tmp_path, "")
        r = _run(["bash", str(seamed)], env)
        assert r.returncode == 0
        assert "COULD-NOT-LOOK" not in r.stdout

    def test_green_garbage_marker_could_not_look(self, tmp_path: Path) -> None:
        env = self._env(tmp_path, "garbage\n")
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 2
        assert "COULD-NOT-LOOK" in r.stdout and "not an epoch" in r.stdout
        assert (
            (tmp_path / "persist/OPERATOR-PRESENCE.md")
            .read_text()
            .startswith("PRESENT")
        )
        assert not (tmp_path / "say.log").exists()

    def test_green_empty_marker_could_not_look(self, tmp_path: Path) -> None:
        env = self._env(tmp_path, "")
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 2
        assert "COULD-NOT-LOOK" in r.stdout

    def test_green_future_marker_could_not_look(self, tmp_path: Path) -> None:
        env = self._env(tmp_path, f"{int(time.time()) + 7200}\n")
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 2
        assert "future" in r.stdout

    @pytest.mark.parametrize(
        "marker",
        ["12 34\n", "08\n", "0{now}\n", "99999999999999999999\n"],
        ids=["inner-blank", "octal", "leading-zero-epoch", "overflow"],
    )
    def test_green_malformed_epoch_could_not_look(
        self, tmp_path: Path, marker: str
    ) -> None:
        env = self._env(tmp_path, marker.format(now=int(time.time())))
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 2 and "not an epoch" in r.stdout, (r.stdout, r.stderr)
        assert (
            (tmp_path / "persist/OPERATOR-PRESENCE.md")
            .read_text()
            .startswith("PRESENT")
        )
        assert not (tmp_path / "say.log").exists()

    def test_green_valid_recent_noop(self, tmp_path: Path) -> None:
        env = self._env(tmp_path, f"{int(time.time()) - 10}\n")
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 0
        assert (
            (tmp_path / "persist/OPERATOR-PRESENCE.md")
            .read_text()
            .startswith("PRESENT")
        )

    def test_green_valid_old_flips_away(self, tmp_path: Path) -> None:
        env = self._env(tmp_path, f"{int(time.time()) - 4000}\n")
        r = _run(["bash", AUTOAWAY_CAND], env)
        assert r.returncode == 0
        assert "AUTO-AWAY after" in r.stdout
        assert (
            (tmp_path / "persist/OPERATOR-PRESENCE.md").read_text().startswith("AWAY")
        )
