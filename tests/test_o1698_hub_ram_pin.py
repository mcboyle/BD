"""O1698 C6 (queue row o1698-hub-ram-pin): bd-hot-pin.sh keeps the hub's hot fleet state in the page cache.

A harness cut (RULING-harness-cut-repo-wt.md): the candidate lives in the FIX dir and is loaded by absolute path from
BD_O1698_HUB_RAM_PIN_CANDIDATE (the bd-hot-pin.sh file; its two systemd units sit beside it). Unset -> skipped.

What the row needs, each pinned here:
  * a residency census before and after `vmtouch -t`, as ONE TSV line per run (exact page counts on an evicted fixture);
  * COULD NOT LOOK (rc 2 + a COULD-NOT-LOOK line), never OK, when vmtouch is absent, no member exists, the census is
    unparseable, or the hot set has zero pages;
  * never a lock or a daemon (-l/-L/-d/-w/-P): the brief allows one only after a measured eviction;
  * a 5-minute user timer that runs the installed path.
The positive control needs a real vmtouch: BD_HOT_PIN_TEST_VMTOUCH, else vmtouch on PATH, else the test FAILS.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_ENV = "BD_O1698_HUB_RAM_PIN_CANDIDATE"
CANDIDATE = os.environ.get(_ENV, "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason=f"{_ENV} unset: harness candidate not under test")

PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
FIELDS = ["utc", "status", "files", "pages", "resident_before", "pct_before", "resident_after", "pct_after",
          "missing", "detail"]
LOCKING = {"-l", "-L", "-d", "-w", "-P"}


def _tool() -> Path:
    tool = Path(CANDIDATE)
    assert tool.is_file() and os.access(tool, os.X_OK), f"no executable hot-pin tool at {tool}"
    return tool


def _vmtouch() -> str:
    found = os.environ.get("BD_HOT_PIN_TEST_VMTOUCH") or shutil.which("vmtouch")
    if not found:
        pytest.fail("COULD NOT LOOK: no vmtouch for the positive control (set BD_HOT_PIN_TEST_VMTOUCH)")
    return found


def _run(root: Path, members: str, out: Path, vmtouch: str | None, path: str | None = None):
    env = {"LC_ALL": "C.UTF-8", "HOME": str(root), "PATH": path or os.environ.get("PATH", "/usr/bin:/bin"),
           "BD_HOT_PIN_ROOT": str(root), "BD_HOT_PIN_SET": members, "BD_HOT_PIN_OUT": str(out)}
    if vmtouch is not None:
        env["BD_HOT_PIN_VMTOUCH"] = vmtouch
    return subprocess.run(["/bin/bash", str(_tool())], env=env, text=True, capture_output=True, timeout=120, check=False)


def _rows(out: Path) -> list[dict[str, str]]:
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "# " + "\t".join(FIELDS), lines[:1]
    return [dict(zip(FIELDS, line.split("\t"), strict=True)) for line in lines[1:]]


def _write(path: Path, size: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))
        f.flush()
        os.fsync(f.fileno())          # dirty pages cannot be evicted: the precondition needs them written back
    return -(-size // PAGE)


def _resident(vmtouch: str, *paths: Path) -> tuple[int, int]:
    out = subprocess.run([vmtouch, *map(str, paths)], text=True, capture_output=True, check=True,
                         env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}).stdout
    line = next(l for l in out.splitlines() if "Resident Pages:" in l)
    res, total = line.split(":", 1)[1].split()[0].split("/")
    return int(res), int(total)


def _fake_vmtouch(tmp_path: Path, summary: str) -> tuple[Path, Path]:
    log = tmp_path / "argv.log"
    fake = tmp_path / "fakebin" / "vmtouch"
    fake.parent.mkdir()
    fake.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> " + str(log) + "\nprintf '%b' '" + summary + "'\n",
                    encoding="ascii")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, log


OK_SUMMARY = "           Files: 1\\n  Resident Pages: 1/3  4K/12K  33%\\n"


def _seq_vmtouch(tmp_path: Path, calls: list[tuple[int, str, bool]]) -> Path:
    """A fake vmtouch whose Nth call prints calls[N][1], exits calls[N][0] and, when calls[N][2], hangs (exec sleep)."""
    count = tmp_path / "calls.n"
    count.write_text("0", encoding="ascii")
    arms = "".join(
        f"  {i}) {'exec sleep 60' if hang else f'''printf '%b' '{text}'; exit {rc}'''};;\n"
        for i, (rc, text, hang) in enumerate(calls, 1)
    )
    fake = tmp_path / "seqbin" / "vmtouch"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/bash\nn=$(( $(cat " + str(count) + ") + 1 )); echo $n > " + str(count) + "\n"
        "case $n in\n" + arms + "  *) exit 99;;\nesac\n",
        encoding="ascii",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


@pytest.mark.parametrize("target", ["read-only-file", "directory", "dev-full"])
def test_a_record_line_that_cannot_be_appended_is_could_not_look_never_ok(tmp_path: Path, target: str) -> None:
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    fake, _ = _fake_vmtouch(tmp_path, OK_SUMMARY)
    out = {"read-only-file": root / "state" / "hot-pin.tsv", "directory": root / "state" / "a-dir",
           "dev-full": Path("/dev/full")}[target]
    if target == "read-only-file":
        out.parent.mkdir(parents=True)
        out.write_text("# utc\theader\n", encoding="ascii")
        out.chmod(0o444)
    elif target == "directory":
        out.mkdir(parents=True)
    if target == "read-only-file":
        assert not os.access(out, os.W_OK), "precondition: the record is not writable for this user"
    before = out.read_bytes() if target == "read-only-file" else b""

    result = _run(root, "inbox", out, str(fake))

    assert result.returncode == 2, (target, result.returncode, result.stdout, result.stderr)
    assert f"COULD NOT LOOK: cannot append the record line to {out} (status was OK)" in result.stderr
    if target == "read-only-file":
        assert out.read_bytes() == before


@pytest.mark.parametrize(
    ("calls", "detail"),
    [
        ([(3, "", False)], "vmtouch census rc=3 (before)"),
        ([(0, "", True)], "vmtouch census rc=124 (timed out after 1s) (before)"),
        ([(0, OK_SUMMARY, False), (5, "", False)], "vmtouch -t rc=5"),
        ([(0, OK_SUMMARY, False), (0, "", True)], "vmtouch -t rc=124 (timed out after 1s)"),
        ([(0, OK_SUMMARY, False), (0, "", False), (4, "", False)], "vmtouch census rc=4 (after)"),
        ([(0, OK_SUMMARY, False), (0, "", False), (0, "  Files: 1\\n  Resident Pages: 0/0  0/0  \\n", False)],
         "hot set has zero pages (after)"),
    ],
    ids=["census-rc", "census-hang", "touch-rc", "touch-hang", "after-rc", "after-zero-pages"],
)
def test_a_failed_or_hung_vmtouch_call_is_could_not_look_with_a_row(tmp_path: Path, calls, detail) -> None:
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    fake = _seq_vmtouch(tmp_path, calls)
    out = root / "state" / "hot-pin.tsv"
    env = {"LC_ALL": "C.UTF-8", "HOME": str(root), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "BD_HOT_PIN_ROOT": str(root), "BD_HOT_PIN_SET": "inbox", "BD_HOT_PIN_OUT": str(out),
           "BD_HOT_PIN_VMTOUCH": str(fake), "BD_HOT_PIN_STEP_TIMEOUT": "1"}

    result = subprocess.run(["/bin/bash", str(_tool())], env=env, text=True, capture_output=True, timeout=60,
                            check=False)

    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    (row,) = _rows(out)
    assert (row["status"], row["detail"]) == ("COULD-NOT-LOOK", detail), row
    assert (tmp_path / "calls.n").read_text(encoding="ascii").strip() == str(len(calls))


def test_pct_after_is_measured_against_the_after_census(tmp_path: Path) -> None:
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    fake = _seq_vmtouch(tmp_path, [(0, "  Files: 1\\n  Resident Pages: 1/4  4K/16K  25%\\n", False), (0, "", False),
                                   (0, "  Files: 1\\n  Resident Pages: 3/3  12K/12K  100%\\n", False)])
    out = root / "state" / "hot-pin.tsv"

    result = _run(root, "inbox", out, str(fake))

    assert result.returncode == 0, result.stderr
    (row,) = _rows(out)
    assert (row["pages"], row["pct_before"], row["resident_after"], row["pct_after"]) == ("4", "25", "3", "100"), row


def test_an_evicted_hot_set_reads_zero_then_touch_makes_every_page_resident(tmp_path: Path) -> None:
    vmtouch = _vmtouch()
    root = tmp_path / "persist"
    pages = _write(root / "inbox" / "a.md", 300_000) + _write(root / "queues" / "q.tsv", 50_000) \
        + _write(root / "queues" / "sub" / "c.json", 9_000)
    assert pages == 74 + 13 + 3 if PAGE == 4096 else pages > 0
    subprocess.run([vmtouch, "-e", str(root / "inbox"), str(root / "queues")], check=True, capture_output=True,
                   env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"})
    if _resident(vmtouch, root / "inbox", root / "queues") != (0, pages):
        pytest.fail(f"COULD NOT LOOK: fixture not evictable here: {_resident(vmtouch, root)} of {pages}")
    out = root / "state" / "hot-pin.tsv"

    first = _run(root, "inbox queues board", out, vmtouch)

    assert first.returncode == 0, first.stdout + first.stderr
    (row,) = _rows(out)
    assert (row["status"], row["files"], row["pages"]) == ("OK", "3", str(pages))
    assert (row["resident_before"], row["pct_before"]) == ("0", "0")
    assert (row["resident_after"], row["pct_after"]) == (str(pages), "100")
    assert row["missing"] == "board"
    # Negative control: the census reads real state, so the next run starts fully resident.
    second = _run(root, "inbox queues board", out, vmtouch)
    assert second.returncode == 0, second.stderr
    assert [(r["resident_before"], r["pct_before"]) for r in _rows(out)] == [("0", "0"), (str(pages), "100")]


def test_vmtouch_absent_from_path_is_could_not_look_with_a_record_line(tmp_path: Path) -> None:
    stub = tmp_path / "stubbin"
    stub.mkdir()
    for tool in ("date", "mkdir"):
        (stub / tool).symlink_to(shutil.which(tool))
    assert shutil.which("vmtouch", path=str(stub)) is None
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    out = root / "state" / "hot-pin.tsv"

    result = _run(root, "inbox", out, None, path=str(stub))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "COULD NOT LOOK: vmtouch not found" in result.stderr
    (row,) = _rows(out)
    assert row["status"] == "COULD-NOT-LOOK" and row["detail"] == "vmtouch not found"


@pytest.mark.parametrize(
    ("members", "summary", "detail"),
    [
        ("nope gone", "", "no hot-set member under"),
        ("inbox", "garbage\\n", "unparseable vmtouch census (before)"),
        ("inbox", "           Files: 1\\n  Resident Pages: 0/0  0/0  \\n", "hot set has zero pages"),
    ],
    ids=["no-member", "unparseable", "zero-pages"],
)
def test_an_unmeasurable_hot_set_is_could_not_look_never_ok(tmp_path: Path, members, summary, detail) -> None:
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    fake, _ = _fake_vmtouch(tmp_path, summary)
    out = root / "state" / "hot-pin.tsv"

    result = _run(root, members, out, str(fake))

    assert result.returncode == 2, result.stdout + result.stderr
    (row,) = _rows(out)
    assert row["status"] == "COULD-NOT-LOOK" and row["detail"].startswith(detail), row


def test_one_run_is_two_censuses_and_one_touch_and_never_a_lock(tmp_path: Path) -> None:
    root = tmp_path / "persist"
    _write(root / "inbox" / "a.md", 10_000)
    fake, log = _fake_vmtouch(tmp_path, "           Files: 1\\n  Resident Pages: 1/3  4K/12K  33%\\n")
    out = root / "state" / "hot-pin.tsv"

    result = _run(root, "inbox", out, str(fake))

    assert result.returncode == 0, result.stderr
    calls = [line.split() for line in log.read_text(encoding="ascii").splitlines()]
    assert len(calls) == 3, calls
    assert ["-t" in c for c in calls] == [False, True, False]
    assert not any(LOCKING & set(c) for c in calls), calls
    assert all("-F" in c and c[-1] == str(root / "inbox") for c in calls), calls
    (row,) = _rows(out)
    assert (row["status"], row["pages"], row["pct_before"], row["pct_after"]) == ("OK", "3", "33", "33")


def test_the_timer_runs_the_installed_tool_every_five_minutes() -> None:
    here = _tool().parent
    service = (here / "bd-hot-pin.service").read_text(encoding="ascii").splitlines()
    timer = (here / "bd-hot-pin.timer").read_text(encoding="ascii").splitlines()
    assert "ExecStart=/home/mboyle/bd-persist/harness/bd-hot-pin.sh" in service
    assert "Type=oneshot" in service
    assert timer.count("OnUnitActiveSec=5min") == 1 and "WantedBy=timers.target" in timer
    # A oneshot without TimeoutStartSec has no start timeout: one hung pass would stop the timer for good (lens D58-D F2).
    (limit,) = [int(line.split("=", 1)[1]) for line in service if line.startswith("TimeoutStartSec=")]
    step = int(re.search(r"STEP=\$\{BD_HOT_PIN_STEP_TIMEOUT:-(\d+)\}", _tool().read_text(encoding="utf-8")).group(1))
    assert 3 * (step + 5) <= limit < 300, (step, limit)   # 3 bounded vmtouch calls fit; the run ends before the next tick
