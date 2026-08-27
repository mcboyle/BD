"""Row 312: ``bd-jobs reap`` must signal through a held process identity.

The registry's PID/start-time pair identifies the intended process at rest, but
the numeric PID can be recycled after that check.  These tests force that
interleave at the signal boundary and account for every attempted effect.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import signal
from collections import Counter
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_jobs_row312", str(_TOOL))
    spec = importlib.util.spec_from_loader("bd_jobs_row312", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_transform_control_imports_subject_without_reap_assertions():
    """Mutation transform control: importability is not a reap verdict."""
    jobs = _load()
    assert callable(jobs.cmd_reap)


def _exercise_reap(monkeypatch, *, replace_at_identity_acquisition: bool):
    jobs = _load()
    entry = {
        "id": "test5-4242",
        "pid": 4242,
        "starttime": 111,
        "purpose": "row 312 identity target",
        "owned_paths": [],
        "log": None,
    }
    rows = jobs._RegistrySnapshot([entry])
    assert len(rows) == 1 and rows[0] is entry, (
        "precondition: the reaper population must contain exactly one target")

    fired = Counter()
    effects: dict[str, list] = {
        "numeric": [], "pidfd": [], "closed": [], "forgotten": [],
    }
    state = {"replacement": False}

    monkeypatch.setattr(jobs, "load_all", lambda: rows)

    def pidfd_open(pid, flags=0):
        fired["pidfd_open"] += 1
        assert (pid, flags) == (entry["pid"], 0)
        if replace_at_identity_acquisition:
            state["replacement"] = True
        return 73

    def proc_starttime(pid):
        fired["starttime"] += 1
        assert pid == entry["pid"]
        return 222 if state["replacement"] else entry["starttime"]

    def getpgid(pid):
        fired["getpgid"] += 1
        assert pid == entry["pid"]
        if replace_at_identity_acquisition:
            # This is the defective implementation's check-to-signal seam:
            # its first start-time read has already returned, the owner exits,
            # and a new group leader receives the same numeric identity.
            state["replacement"] = True
            return pid
        return pid - 1

    def numeric_signal(kind, pid, sig):
        fired[kind] += 1
        effects["numeric"].append((kind, pid, sig, state["replacement"]))

    def pidfd_signal(fd, sig, *args):
        fired["pidfd_send_signal"] += 1
        effects["pidfd"].append((fd, sig, args))

    def close(fd):
        fired["close"] += 1
        effects["closed"].append(fd)

    def forget(value):
        fired["forget"] += 1
        effects["forgotten"].append(value["id"])
        return {"cleanup_complete": True, "notes": ["test entry removed"]}

    monkeypatch.setattr(jobs.os, "pidfd_open", pidfd_open)
    monkeypatch.setattr(jobs, "proc_starttime", proc_starttime)
    monkeypatch.setattr(jobs.os, "getpgid", getpgid)
    monkeypatch.setattr(
        jobs.os, "killpg", lambda pid, sig: numeric_signal("killpg", pid, sig))
    monkeypatch.setattr(
        jobs.os, "kill", lambda pid, sig: numeric_signal("kill", pid, sig))
    monkeypatch.setattr(signal, "pidfd_send_signal", pidfd_signal)
    monkeypatch.setattr(jobs.os, "close", close)
    monkeypatch.setattr(jobs, "_forget_or_retain", forget)

    rc = jobs.cmd_reap(type("Args", (), {"id": entry["id"]})())
    return rc, entry, fired, effects


def test_exit_and_reuse_after_acquisition_never_signals_the_replacement(
        monkeypatch, capsys):
    rc, entry, fired, effects = _exercise_reap(
        monkeypatch, replace_at_identity_acquisition=True)
    out, err = capsys.readouterr()

    assert effects["numeric"] == [], (
        "reap signaled the replacement through a reusable numeric identity: "
        f"{effects['numeric']}")
    assert effects["pidfd"] == [], (
        "the identity-bound handle referred to an exited owner and must not "
        f"signal after procfs reports its replacement: {effects['pidfd']}")
    assert fired["pidfd_open"] == 1, (
        "the reaper did not acquire exactly one identity-bearing handle")
    assert fired["starttime"] == 1, (
        "the held identity was not verified exactly once before disposition")
    assert effects["closed"] == [73], (
        "the one acquired identity handle was not closed exactly once")
    assert effects["forgotten"] == [entry["id"]], (
        "a proven replacement must retire exactly its one stale record")
    assert rc == 0 and "0 reaped, 0 refused" in out, (rc, out, err)
    assert "stale" in err and "nothing killed" in err, err


def test_stable_identity_is_signaled_once_through_its_handle(
        monkeypatch, capsys):
    rc, entry, fired, effects = _exercise_reap(
        monkeypatch, replace_at_identity_acquisition=False)
    out, err = capsys.readouterr()

    assert effects["numeric"] == [], (
        "negative control: a stable target was still signaled numerically: "
        f"{effects['numeric']}")
    assert effects["pidfd"] == [(73, signal.SIGKILL, ())], (
        "negative control: the stable target did not receive exactly one "
        f"identity-bound SIGKILL: {effects['pidfd']}")
    assert fired == Counter({
        "pidfd_open": 1,
        "starttime": 1,
        "getpgid": 1,
        "pidfd_send_signal": 1,
        "close": 1,
        "forget": 1,
    }), f"the stable control did not traverse the exact intended seam: {fired}"
    assert effects["closed"] == [73]
    assert effects["forgotten"] == [entry["id"]]
    assert rc == 0 and "1 reaped, 0 refused" in out and err == "", (
        rc, out, err)


def test_unavailable_identity_handle_is_unknown_and_authorizes_nothing(
        monkeypatch, capsys):
    jobs = _load()
    entry = {
        "id": "test5-5151", "pid": 5151, "starttime": 222,
        "purpose": "unavailable pidfd", "owned_paths": [], "log": None,
    }
    rows = jobs._RegistrySnapshot([entry])
    assert len(rows) == 1 and rows[0]["pid"] == 5151, (
        "precondition: the unavailable measurement must judge one target")
    fired = Counter()
    effects = []

    monkeypatch.setattr(jobs, "load_all", lambda: rows)

    def unavailable(pid, flags=0):
        fired["pidfd_open"] += 1
        assert (pid, flags) == (5151, 0)
        raise PermissionError(1, "injected pidfd denial")

    def forbidden(name):
        def fire(*args):
            effects.append((name, args))
        return fire

    monkeypatch.setattr(jobs.os, "pidfd_open", unavailable)
    monkeypatch.setattr(jobs.os, "kill", forbidden("kill"))
    monkeypatch.setattr(jobs.os, "killpg", forbidden("killpg"))
    monkeypatch.setattr(signal, "pidfd_send_signal", forbidden("pidfd"))
    monkeypatch.setattr(
        jobs, "_forget_or_retain", lambda value: effects.append(
            ("forget", value["id"])))

    rc = jobs.cmd_reap(type("Args", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert fired == Counter({"pidfd_open": 1}), (
        f"the unavailable identity seam did not fire exactly once: {fired}")
    assert effects == [], (
        "an unavailable identity measurement authorized an effect: "
        f"{effects}")
    assert rc != 0 and "0 reaped, 1 refused" in out, (rc, out, err)
    assert ("REFUSED" in err and "UNKNOWN" in err
            and "injected pidfd denial" in err), err


def test_unavailable_starttime_after_pidfd_is_unknown_not_stale_cleanup(
        monkeypatch, capsys):
    jobs = _load()
    entry = {
        "id": "test5-6161", "pid": 6161, "starttime": 333,
        "purpose": "unavailable procfs identity", "owned_paths": [],
        "log": None,
    }
    rows = jobs._RegistrySnapshot([entry])
    assert len(rows) == 1 and rows[0] is entry, (
        "precondition: exactly one acquired identity needs revalidation")
    fired = Counter()
    effects = []

    monkeypatch.setattr(jobs, "load_all", lambda: rows)

    def pidfd_open(pid, flags=0):
        fired["pidfd_open"] += 1
        assert (pid, flags) == (6161, 0)
        return 88

    def starttime(pid):
        fired["starttime"] += 1
        assert pid == 6161
        return None

    def exact_identity(pid):
        fired["identity"] += 1
        assert pid == 6161
        return ("UNKNOWN", None, "injected procfs stat denial")

    def close(fd):
        fired["close"] += 1
        effects.append(("close", fd))

    def forbidden(name):
        def fire(*args):
            effects.append((name, args))
        return fire

    monkeypatch.setattr(jobs.os, "pidfd_open", pidfd_open)
    monkeypatch.setattr(jobs, "proc_starttime", starttime)
    monkeypatch.setattr(jobs, "_reap_proc_identity", exact_identity)
    monkeypatch.setattr(jobs.os, "getpgid", forbidden("getpgid"))
    monkeypatch.setattr(jobs.os, "kill", forbidden("kill"))
    monkeypatch.setattr(jobs.os, "killpg", forbidden("killpg"))
    monkeypatch.setattr(signal, "pidfd_send_signal", forbidden("pidfd"))
    monkeypatch.setattr(jobs.os, "close", close)
    monkeypatch.setattr(
        jobs, "_forget_or_retain",
        lambda value: (effects.append(("forget", (value["id"],))) or
                       {"cleanup_complete": True, "notes": ["removed"]}))

    rc = jobs.cmd_reap(type("Args", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert effects == [("close", 88)], (
        "unavailable start-time evidence authorized signaling or stale "
        f"cleanup: {effects}")
    assert fired == Counter({
        "pidfd_open": 1, "starttime": 1, "identity": 1, "close": 1,
    }), f"post-acquisition measurement counts were not exact: {fired}"
    assert rc != 0 and "0 reaped, 1 refused" in out, (rc, out, err)
    assert "UNKNOWN" in err and "injected procfs stat denial" in err, err
