"""O1673 quota probes: the AGY, kimi and grok rows of POOL_STATE.tsv.

bd-limit-watch.sh judges AGY from bd-agy-usage.sh's 429-window row plus USAGE.tsv's remaining columns (no more fixed
UNKNOWN) and carries the kimi/grok rows it does not own; bd-kimi-usage-watch.sh reaches kimi by absolute path (cron's
PATH has no ~/.local/bin); bd-grok-quota-probe.sh writes a grok row. Unparsed or stale input is UNKNOWN, never OK.
BD_O1673_QUOTA_PROBES_CANDIDATE is the candidate DIRECTORY holding the three scripts. Hermetic: temp state, fake
binaries, and tmux only on a private `-L` server.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1673_QUOTA_PROBES_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

HEADER = (
    "pool\tstate\tsince\tevidence\tpct\tresets_at\tweekly_pct\tfive_hour_remaining_pct\t"
    "gemini_five_hour_remaining_pct\tgemini_weekly_remaining_pct\tclaude_gpt_five_hour_remaining_pct\t"
    "claude_gpt_weekly_remaining_pct\tquota_observed_at"
)
NOW = "2026-10-02T20:40:00Z"
KIMI_ROW = "kimi\tOK\t2026-10-02T17:55:01Z\t5h 0% used; monthly 18% used\t0\t-\t-\t-\t-\t-\t-\t-\t2026-10-02T17:55:01Z"
GROK_ROW = "grok\tUNKNOWN\t2026-10-02T19:00:00Z\tno measured grok quota source\t-\t-\t-\t-\t-\t-\t-\t-\t2026-10-02T19:00:00Z"
USAGE_HEADER = (
    "pool\tfive_hour_pct\tresets_at\tweekly_pct\tweekly_resets\tfetched_at\tfive_hour_remaining_pct\t"
    "gemini_five_hour_remaining_pct\tgemini_weekly_remaining_pct\tclaude_gpt_five_hour_remaining_pct\t"
    "claude_gpt_weekly_remaining_pct\tquota_observed_at"
)
AGY_USAGE_HEADER = (
    "measured_at\tstate\tlast_429\tresets_at\tn429_window\twindow_s\tgemini_five_hour_remaining_pct\t"
    "gemini_weekly_remaining_pct\tclaude_gpt_five_hour_remaining_pct\tclaude_gpt_weekly_remaining_pct\tquota_observed_at"
)


def script(name):
    path = Path(CANDIDATE) / name
    assert path.is_file() and os.access(path, os.X_OK), f"candidate not executable: {path}"
    return path


def rows(path):
    lines = Path(path).read_text().splitlines()
    return lines[0], {line.split("\t")[0]: line.split("\t") for line in lines[1:]}


def exe(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------- bd-limit-watch.sh


@pytest.fixture
def watch(tmp_path):
    state = tmp_path / "POOL_STATE.tsv"
    state.write_text("\n".join([HEADER, KIMI_ROW, GROK_ROW]) + "\n")
    bindir = tmp_path / "bin"
    for name in ("tmux", "ssh"):  # nothing on this host may be reached
        exe(bindir / name, "#!/bin/bash\nexit 1\n")
    codex_usage = exe(bindir / "codex-usage", "#!/bin/bash\nexit 1\n")
    od_append = exe(bindir / "od-append", "#!/bin/bash\nexit 0\n")
    for d in ("panes", "codex-panes"):
        (tmp_path / d).mkdir()

    def _run(agy_rows, usage_agy, lock_wait=None):
        (tmp_path / "AGY-USAGE.tsv").write_text("\n".join([AGY_USAGE_HEADER, *agy_rows]) + "\n")
        (tmp_path / "USAGE.tsv").write_text(USAGE_HEADER + "\n" + (usage_agy + "\n" if usage_agy else ""))
        env = dict(
            os.environ,
            PATH=f"{bindir}:/usr/bin:/bin",
            LC_ALL="C",
            BD_PERSIST=str(tmp_path),
            BD_LIMIT_STATE=str(state),
            BD_LIMIT_NOW=NOW,
            BD_LIMIT_PANES=str(tmp_path / "panes"),
            BD_LIMIT_CODEX_PANES=str(tmp_path / "codex-panes"),
            BD_LIMIT_HOSTS=str(tmp_path / "no-hosts"),
            BD_CODEX_USAGE=str(codex_usage),
            BD_OD_APPEND=str(od_append),
            BD_USAGE_TSV=str(tmp_path / "USAGE.tsv"),
            BD_LIMIT_AGY_USAGE=str(tmp_path / "AGY-USAGE.tsv"),
            **{f"BD_LIMIT_CACHE_{p}": str(tmp_path / f"no-cache-{p}.json") for p in "ABCD"},
        )
        if lock_wait is not None:
            env["BD_LIMIT_LOCK_WAIT"] = str(lock_wait)
        r = subprocess.run(["bash", str(script("bd-limit-watch.sh"))], capture_output=True, text=True, env=env,
                           check=False, timeout=60)
        return r, state

    return _run


def agy_usage(measured, state="OK-UNMETERED", last="-", resets="-", n="0"):
    return f"{measured}\t{state}\t{last}\t{resets}\t{n}\t7200\t-\t-\t-\t-\t-"


def usage_agy(g5="68", gw="98", c5="100", cw="90", observed="2026-10-02T20:30:00Z"):
    return f"AGY\t32\t-\t2\t-\t{NOW}\t{g5}\t{g5}\t{gw}\t{c5}\t{cw}\t{observed}"


def test_agy_with_fresh_observations_is_measured_not_unknown(watch):
    r, state = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy())
    assert r.returncode == 0, r.stdout + r.stderr
    _, got = rows(state)
    agy = got["AGY"]
    assert agy[1] == "OK", f"AGY row stayed {agy[1]}: {agy[3]}"
    # binding = the most constrained budget: 5h min(68, 100) remaining -> 32 used; weekly min(98, 90) -> 10 used
    assert (agy[4], agy[6]) == ("32", "10"), agy
    assert "gemini 5h 68%" in agy[3] and "no 429 in the window" in agy[3], agy[3]
    assert agy[8:13] == ["68", "98", "100", "90", "2026-10-02T20:30:00Z"], agy[8:13]


def test_agy_reactive_429_with_reset_ahead_is_limited(watch):
    r, state = watch([agy_usage("2026-10-02T20:03:01Z", "LIMITED", "2026-10-02T20:01:00Z", "2026-10-02T23:00:00Z", "3")],
                     usage_agy())
    assert r.returncode == 0, r.stdout + r.stderr
    agy = rows(state)[1]["AGY"]
    assert (agy[1], agy[5]) == ("LIMITED", "2026-10-02T23:00:00Z"), agy


def test_agy_exhausted_remaining_budget_is_limited(watch):
    r, state = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy(c5="0"))
    assert r.returncode == 0, r.stdout + r.stderr
    agy = rows(state)[1]["AGY"]
    assert (agy[1], agy[4]) == ("LIMITED", "100"), agy


@pytest.mark.parametrize(
    "agy_rows, usage, needle",
    [
        ([], usage_agy(), "no AGY 429-window row"),
        ([agy_usage("2026-10-02T15:00:00Z")], usage_agy(), "STALE"),
        ([agy_usage("not-a-time")], usage_agy(), "no parseable measured_at"),
        ([agy_usage("2026-10-02T20:03:01Z", "COULD-NOT-LOOK")], usage_agy(), "is not a reading"),
        ([agy_usage("2026-10-02T20:03:01Z", "garbled")], usage_agy(), "is not a reading"),
        ([agy_usage("2026-10-02T20:03:01Z")], usage_agy(gw="abc"), "remaining observation is unparsed"),
        ([agy_usage("2026-10-02T20:03:01Z")], usage_agy(c5="-"), "remaining observation is unparsed"),
        ([agy_usage("2026-10-02T20:03:01Z")], usage_agy(cw="140"), "remaining observation is unparsed"),
        ([agy_usage("2026-10-02T20:03:01Z")], usage_agy(observed="2026-10-02T10:00:00Z"), "0 429s is not headroom"),
        ([agy_usage("2026-10-02T20:03:01Z")], None, "remaining observation is unparsed"),
    ],
    ids=["no-row", "stale-429-row", "bad-stamp", "could-not-look", "garbled-state", "unparsed-pct",
         "missing-pct", "pct-over-100", "stale-remaining", "no-usage-row"],
)
def test_agy_unparsed_or_stale_input_stays_unknown(watch, agy_rows, usage, needle):
    r, state = watch(agy_rows, usage)
    assert r.returncode == 0, r.stdout + r.stderr
    agy = rows(state)[1]["AGY"]
    assert agy[1] == "UNKNOWN", f"fail-open: {agy}"
    assert needle in agy[3], agy[3]


def test_limit_watch_keeps_the_kimi_and_grok_rows_it_does_not_own(watch):
    r, state = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy())
    assert r.returncode == 0, r.stdout + r.stderr
    header, got = rows(state)
    assert header == HEADER
    lines = state.read_text().splitlines()
    assert KIMI_ROW in lines and GROK_ROW in lines, f"dropped a row it does not own: {sorted(got)}"
    assert [line.split("\t")[0] for line in lines[1:]] == ["A", "B", "C", "D", "codex", "AGY", "kimi", "grok"]
    assert all(len(line.split("\t")) == 13 for line in lines), "column count changed"


def test_limit_watch_bootstraps_an_absent_state_file(watch, tmp_path):
    # lens C5 HIGH: carrying non-owned rows must not need a state file to carry them from (fresh host, the h85 fixture)
    state = tmp_path / "POOL_STATE.tsv"
    state.unlink()
    r, _ = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy())
    assert r.returncode == 0, f"rc={r.returncode} {r.stdout}{r.stderr}"
    lines = state.read_text().splitlines()
    assert lines[0] == HEADER
    assert [line.split("\t")[0] for line in lines[1:]] == ["A", "B", "C", "D", "codex", "AGY"], lines
    r2, _ = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy())
    assert r2.returncode == 0 and r2.stdout.startswith("STEADY"), r2.stdout


def test_limit_watch_waits_for_the_shared_lock_and_refuses_when_it_stays_busy(watch, tmp_path):
    state = tmp_path / "POOL_STATE.tsv"
    before = state.read_text()
    lock = tmp_path / "POOL_STATE.tsv.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "60"])  # outlives the decide phase; killed below
    try:
        subprocess.run(["bash", "-c", f'until ! flock -n "{lock}" true; do sleep 0.1; done'], timeout=5, check=True)
        r, _ = watch([agy_usage("2026-10-02T20:03:01Z")], usage_agy(), lock_wait=1)
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 4, r.stdout + r.stderr
    assert "could not lock" in r.stdout, r.stdout
    assert state.read_text() == before


# ---------------------------------------------------------------- bd-kimi-usage-watch.sh

PANEL_OK = "  5h limit      ███░░░   12% used  resets in 3h 20m\n  Monthly limit █░░░   18% used\n"


@pytest.fixture
def kimi(tmp_path):
    real_tmux = shutil.which("tmux")
    assert real_tmux, "tmux is required for the private-socket probe"
    # a private server in its own short dir: tmp_path is too long for a unix socket path (108 bytes)
    tmux_dir = tempfile.mkdtemp(prefix="o1673-")
    sock = "o1673"
    bindir = tmp_path / "bin"
    exe(bindir / "tmux", f'#!/bin/bash\nexec {real_tmux} -L {sock} "$@"\n')
    say = exe(bindir / "say", f'#!/bin/bash\necho "$*" >> {tmp_path}/say.log\n')
    persist = tmp_path / "p"
    persist.mkdir()
    pool_state = persist / "POOL_STATE.tsv"
    a_row = "A\tOK\t2026-10-02T09:30:07Z\tfive_hour 2%\t2\t-\t97\t-\t-\t-\t-\t-\t-"
    pool_state.write_text("\n".join([HEADER, a_row]) + "\n")
    home = tmp_path / "home"
    panel = tmp_path / "panel.txt"

    def _run(panel_text=PANEL_OK, install_kimi=True):
        panel.write_text(panel_text)
        if install_kimi:  # kimi lives in ~/.local/bin only, exactly as on the hub; PATH is cron's
            exe(home / ".local/bin/kimi",
                f'#!/bin/bash\nwhile IFS= read -r l; do case "$l" in */usage*) cat {panel} ;; esac; done\n')
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        env.update(
            PATH=f"{bindir}:/usr/bin:/bin",
            LC_ALL="C",
            HOME=str(home),
            TMUX_TMPDIR=tmux_dir,
            BD_KIMI_WATCH_P=str(persist),
            BD_KIMI_WATCH_SAY=str(say),
            BD_KIMI_PROBE_START_WAIT="1",
            BD_KIMI_PROBE_PAUSE="1",
        )
        try:
            r = subprocess.run(["bash", str(script("bd-kimi-usage-watch.sh"))], capture_output=True, text=True,
                               env=env, check=False, timeout=90)
        finally:
            subprocess.run([real_tmux, "-L", sock, "kill-server"], capture_output=True, check=False,
                           env=dict(os.environ, TMUX_TMPDIR=tmux_dir))
        return r, pool_state, a_row

    yield _run
    shutil.rmtree(tmux_dir, ignore_errors=True)


def test_kimi_probe_lands_5h_and_monthly_when_kimi_is_only_in_home_local_bin(kimi):
    r, pool_state, a_row = kimi()
    header, got = rows(pool_state)
    assert "kimi" in got, f"no kimi row: rc={r.returncode} {r.stderr}"
    row = got["kimi"]
    assert row[1] == "OK", f"kimi row {row[1]}: {row[3]}"
    assert row[4] == "12" and "monthly 18% used" in row[3], row
    assert header == HEADER and a_row in pool_state.read_text().splitlines()
    assert len(row) == 13


@pytest.mark.parametrize(
    "panel_text, install_kimi, needle",
    [
        ("kimi> nothing to see\n", True, "no 5h + Monthly '% used' pair"),
        ("  5h limit  █░  12% used  resets in 3h 20m\n", True, "no 5h + Monthly '% used' pair"),
        (PANEL_OK, False, "kimi binary not executable"),
    ],
    ids=["unparseable-panel", "5h-only-no-monthly", "no-kimi-binary"],
)
def test_kimi_unparsed_or_unreachable_probe_stays_unknown_with_the_reason(kimi, panel_text, install_kimi, needle):
    r, pool_state, _ = kimi(panel_text, install_kimi)
    row = rows(pool_state)[1]["kimi"]
    assert row[1] == "UNKNOWN", f"fail-open: {row}"
    assert needle in row[3], row[3]


# ---------------------------------------------------------------- bd-grok-quota-probe.sh


@pytest.fixture
def grok(tmp_path):
    pool_state = tmp_path / "POOL_STATE.tsv"
    grok_bin = exe(tmp_path / "bin/grok", "#!/bin/bash\nexit 0\n")

    def _run(*args, now=NOW, grok_path=None):
        env = dict(os.environ, LC_ALL="C", BD_GROK_PROBE_P=str(tmp_path), BD_GROK_PROBE_NOW=now,
                   BD_GROK=str(grok_path or grok_bin))
        return subprocess.run(["bash", str(script("bd-grok-quota-probe.sh")), *args], capture_output=True, text=True,
                              env=env, check=False, timeout=30)

    return _run, pool_state


def test_grok_probe_writes_a_grok_row_unknown_with_its_reason(grok):
    run, pool_state = grok
    pool_state.write_text("\n".join([HEADER, KIMI_ROW]) + "\n")
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    header, got = rows(pool_state)
    assert header == HEADER and KIMI_ROW in pool_state.read_text().splitlines()
    row = got["grok"]
    assert len(row) == 13 and row[1] == "UNKNOWN" and row[2] == NOW, row
    assert "no measured grok quota source" in row[3], row[3]


def test_grok_since_moves_only_on_transition_and_never_reads_ok(grok):
    run, pool_state = grok
    pool_state.write_text("\n".join([HEADER, GROK_ROW]) + "\n")
    assert run(now="2026-10-02T20:45:00Z").returncode == 0
    row = rows(pool_state)[1]["grok"]
    assert (row[1], row[2], row[12]) == ("UNKNOWN", "2026-10-02T19:00:00Z", "2026-10-02T20:45:00Z"), row
    pool_state.write_text("\n".join([HEADER, GROK_ROW.replace("\tUNKNOWN\t", "\tOK\t")]) + "\n")
    assert run(now="2026-10-02T20:50:00Z").returncode == 0
    row = rows(pool_state)[1]["grok"]
    assert (row[1], row[2]) == ("UNKNOWN", "2026-10-02T20:50:00Z"), row


def test_grok_missing_binary_is_named(grok, tmp_path):
    run, pool_state = grok
    pool_state.write_text(HEADER + "\n")
    assert run(grok_path=tmp_path / "absent-grok").returncode == 0
    row = rows(pool_state)[1]["grok"]
    assert row[1] == "UNKNOWN" and "grok binary not executable" in row[3], row


def test_grok_dry_run_writes_nothing(grok):
    run, pool_state = grok
    pool_state.write_text("\n".join([HEADER, KIMI_ROW]) + "\n")
    before = pool_state.read_bytes()
    r = run("--dry-run")
    assert r.returncode == 0 and r.stdout.startswith("DRY: grok\tUNKNOWN\t"), r.stdout
    assert pool_state.read_bytes() == before


def test_grok_without_a_pool_state_could_not_look(grok):
    run, pool_state = grok
    r = run()
    assert r.returncode == 4 and "COULD NOT LOOK" in r.stderr, (r.returncode, r.stderr)
    assert not pool_state.exists()
