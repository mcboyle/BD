"""Row 282: bd-opv must not mutate an operator's persistent stores.

The assertions in this file observe Python's filesystem and SQLite resource
boundaries at runtime.  They deliberately do not infer safety from bd-opv's
source text or from the paths the verifier says it intended to use.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-opv"
_AUDIT_PREFIX = "ROW282-RESOURCE "
_ISOLATION_ENV = {
    "BD_AUTH_TOKEN",
    "BD_CAPTURE_VAULT",
    "BD_CAPTURES_ROOT",
    "BD_DOWNLOAD_DIR",
    "BD_ENVFILE",
    "BD_HOME",
    "BD_INSTALL_DIR",
    "BD_LOG_FILE",
    "BD_SECRETS_FILE",
    "BD_SITES_CONFIG_PATH",
    "BD_VPN_CONFIG_PATH",
    "BD_WIDGETS_CONFIG_PATH",
    "HOME",
    "MPLCONFIGDIR",
    "PYTHONPYCACHEPREFIX",
    "SQLITE_TMPDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "_BD_OPV_RESOURCE_ROOT",
}


def _sitecustomize_source() -> str:
    """Instrument write/connect/SQL boundaries before bd-opv imports BD."""
    return textwrap.dedent(
        f"""
        import atexit
        import importlib.abc
        import json
        import os
        import socket
        import sqlite3
        import sys
        import types

        PREFIX = {_AUDIT_PREFIX!r}

        def emit(kind, **fields):
            row = {{"kind": kind, **fields}}
            os.write(2, (PREFIX + json.dumps(row, sort_keys=True) + "\\n").encode())

        class DenyAmbientPyWebPush(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "pywebpush" or fullname.startswith("pywebpush."):
                    raise ImportError("row 294 fixture denies ambient pywebpush")
                return None

        # SAVE BEFORE WIPING, RESTORE AT EXIT. This runs in a CHILD process, so it
        # cannot actually leak the parent's module table -- but
        # tests/test_v3_66_1034_guards_survive_a_module_wipe.py is a TEXTUAL
        # heuristic that cannot know that, and it is deliberately biased toward
        # over-reporting because a false clean silently re-opens the class it
        # exists to close. It read this file as a 14th leaker over a budget of 13.
        # Raising that budget would weaken a ratchet to admit a new entry, which
        # the contract forbids; making the save/restore real and explicit costs
        # two lines and is honest either way.
        saved_modules = {{k: v for k, v in sys.modules.items() if k == "pywebpush"}}
        atexit.register(lambda: sys.modules.update(saved_modules))
        sys.modules.pop("pywebpush", None)
        sys.meta_path.insert(0, DenyAmbientPyWebPush())
        try:
            import pywebpush
        except ImportError as error:
            emit("pywebpush.import_absent", error=str(error))
        else:
            raise AssertionError("row 294 fixture did not make pywebpush absent")

        pywebpush_stub = types.ModuleType("pywebpush")
        class WebPushException(Exception):
            response = None
        def webpush(*args, **kwargs):
            raise WebPushException("row 294 fixture provides no push service")
        pywebpush_stub.WebPushException = WebPushException
        pywebpush_stub.webpush = webpush
        sys.modules["pywebpush"] = pywebpush_stub

        def absolute(value):
            if isinstance(value, bytes):
                value = os.fsdecode(value)
            if not isinstance(value, str):
                return None
            return os.path.abspath(value)

        def audit(event, args):
            if event == "open":
                path = absolute(args[0])
                mode = args[1] if len(args) > 1 else None
                flags = args[2] if len(args) > 2 else 0
                write_flags = (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                               os.O_TRUNC | os.O_APPEND)
                writing = ((isinstance(mode, str) and
                            any(mark in mode for mark in ("w", "a", "+", "x")))
                           or (isinstance(flags, int) and bool(flags & write_flags)))
                if path is not None and writing:
                    emit("open.write", path=path)
            elif event == "os.chdir":
                path = absolute(args[0])
                if path is not None:
                    emit("os.chdir", path=path)
            elif event == "socket.connect":
                emit("socket.connect", address=repr(args[-1]))
                raise RuntimeError("row 282 fixture blocks every outbound socket")

        sys.addaudithook(audit)

        real_connect = sqlite3.connect
        def traced_connect(database, *args, **kwargs):
            target = database
            if target == ":memory:":
                emit("sqlite.memory")
                return real_connect(database, *args, **kwargs)
            if isinstance(target, str) and target.startswith("file:"):
                target = target[5:].split("?", 1)[0]
            emit("sqlite.connect", path=absolute(target))
            cx = real_connect(database, *args, **kwargs)
            cx.set_trace_callback(lambda statement: emit("sqlite.sql", sql=statement))
            return cx
        sqlite3.connect = traced_connect

        def final_environment():
            keys = {sorted(_ISOLATION_ENV)!r}
            emit("final.environment", cwd=os.getcwd(),
                 environment={{key: os.environ.get(key) for key in keys}})
        atexit.register(final_environment)
        """
    )


def _clean_environment(instrumentation: Path, caller_home: Path,
                       caller_tmp: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Removing first is load-bearing: merely omitting assignments would inherit
    # an operator's install/config/cache selectors into this subprocess.
    for key in _ISOLATION_ENV | {
        "BD_SLOW_QUERY_LOG", "BD_WORK", "PLAYWRIGHT_BROWSERS_PATH", "PYTHONPATH",
        "_BD_OPV_REEXEC",
    }:
        env.pop(key, None)
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "BD_SLOW_QUERY_LOG": "0",
        "BD_WORK": str(_REPO),
        "HOME": str(caller_home),
        "TMPDIR": str(caller_tmp),
        "TEMP": str(caller_tmp),
        "TMP": str(caller_tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(instrumentation),
        "_BD_OPV_REEXEC": "1",
    })
    return env


def _seed_push_store(path: Path) -> None:
    with sqlite3.connect(path) as cx:
        cx.execute(
            """CREATE TABLE push_subscriptions(
                endpoint TEXT PRIMARY KEY,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_agent TEXT DEFAULT '',
                created_at TEXT,
                last_sent_at REAL DEFAULT 0
            )"""
        )
    with sqlite3.connect(path) as cx:
        tables = {
            row[0] for row in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        count = cx.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    assert tables == {"push_subscriptions"}
    assert count == 0


def _run_opv(tmp_path: Path, check_id: str, *, seed_push: bool = False,
             extra_env: dict[str, str] | None = None) -> dict:
    caller = tmp_path / "operator-state"
    caller_home = tmp_path / "caller-home"
    caller_tmp = tmp_path / "caller-tmp"
    instrumentation = tmp_path / "instrumentation"
    for directory in (caller, caller_home, caller_tmp, instrumentation):
        directory.mkdir()
    (instrumentation / "sitecustomize.py").write_text(
        _sitecustomize_source(), encoding="utf-8"
    )

    live_db = caller / "downloader_history.db"
    if seed_push:
        _seed_push_store(live_db)
    before = {
        path.relative_to(caller).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in caller.rglob("*") if path.is_file()
    }

    child_env = _clean_environment(instrumentation, caller_home, caller_tmp)
    child_env.update(extra_env or {})
    done = subprocess.run(
        [sys.executable, str(_TOOL), "--only", check_id],
        cwd=caller,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    events = []
    for line in done.stderr.splitlines():
        if line.startswith(_AUDIT_PREFIX):
            events.append(json.loads(line[len(_AUDIT_PREFIX):]))
    final = [event for event in events if event["kind"] == "final.environment"]
    dependency_probes = [event for event in events
                         if event["kind"] == "pywebpush.import_absent"]
    writes = [event for event in events
              if event["kind"] in {"open.write", "sqlite.connect"}]
    after = {
        path.relative_to(caller).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in caller.rglob("*") if path.is_file()
    }
    return {
        "done": done,
        "events": events,
        "final": final,
        "dependency_probes": dependency_probes,
        "writes": writes,
        "before": before,
        "after": after,
        "caller": caller,
    }


def _assert_resource_boundary(run: dict) -> Path:
    done = run["done"]
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 PASS" in done.stdout, done.stdout
    assert len(run["final"]) == 1, (
        f"expected exactly one final environment record, got {len(run['final'])}"
    )
    assert len(run["writes"]) > 0, "resource instrumentation observed zero stores"

    environment = run["final"][0]["environment"]
    claimed = environment.get("_BD_OPV_RESOURCE_ROOT")
    # On the defective tree there is no stronger claimed root.  HOME is the
    # throwaway boundary bd-opv was supposed to provide, so an escaped cwd/DB
    # write still produces the intended data-loss failure rather than an
    # unrelated "marker absent" assertion.
    root = Path(claimed or environment["HOME"]).resolve()
    escaped = sorted({
        event["path"] for event in run["writes"]
        if event.get("path") and not Path(event["path"]).resolve().is_relative_to(root)
    })
    assert not escaped, f"resource writes escaped bd-opv sandbox: {escaped}"
    assert claimed, "bd-opv did not publish its owned resource root"
    assert run["before"] == run["after"], (
        f"operator store changed: before={run['before']} after={run['after']}"
    )
    return root


def test_real_health_boot_writes_only_owned_config_cache_database_and_cwd(tmp_path) -> None:
    run = _run_opv(tmp_path, "OPV-HEALTH")
    root = _assert_resource_boundary(run)

    environment = run["final"][0]["environment"]
    expected = {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "TEMP": root / "tmp",
        "TMP": root / "tmp",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_CACHE_HOME": root / "cache",
        "XDG_DATA_HOME": root / "data",
        "XDG_RUNTIME_DIR": root / "runtime",
        "MPLCONFIGDIR": root / "cache" / "matplotlib",
        "PYTHONPYCACHEPREFIX": root / "cache" / "pycache",
        "SQLITE_TMPDIR": root / "tmp",
        "BD_HOME": root / "state",
        "BD_INSTALL_DIR": root / "state",
        "BD_DOWNLOAD_DIR": root / "downloads",
        "BD_CAPTURES_ROOT": root / "captures",
        "BD_LOG_FILE": root / "state" / "logs" / "bulk_downloader.log",
        "BD_SECRETS_FILE": root / "state" / "opv-secrets.json",
        "BD_ENVFILE": root / "config" / ".env",
        "BD_SITES_CONFIG_PATH": root / "config" / "sites_config.json",
        "BD_VPN_CONFIG_PATH": root / "config" / "vpn_config.json",
        "BD_WIDGETS_CONFIG_PATH": root / "config" / "widgets_config.json",
    }
    assert len(expected) == 21
    assert {key: Path(environment[key]) for key in expected} == expected
    assert environment["BD_CAPTURE_VAULT"] == "1"

    chdirs = [Path(event["path"]) for event in run["events"]
              if event["kind"] == "os.chdir"]
    assert chdirs.count(root / "cwd") == 1
    db_targets = {Path(event["path"]) for event in run["events"]
                  if event["kind"] == "sqlite.connect"}
    assert db_targets == {root / "state" / "downloader_history.db"}
    written_names = {Path(event["path"]).name for event in run["events"]
                     if event["kind"] == "open.write"}
    assert {"app_config.tmp", "bulk_downloader.log"} <= written_names


def test_inherited_capture_vault_override_cannot_escape_the_owned_boundary(
        tmp_path) -> None:
    outside = tmp_path / "operator-capture-vault.json"
    run = _run_opv(
        tmp_path,
        "OPV-HEALTH",
        extra_env={
            "BD_CAPTURE_VAULT": "1",
            "BD_SECRETS_FILE": str(outside),
        },
    )
    root = _assert_resource_boundary(run)
    environment = run["final"][0]["environment"]
    assert environment["BD_CAPTURE_VAULT"] == "1"
    assert Path(environment["BD_SECRETS_FILE"]) == (
        root / "state" / "opv-secrets.json"
    )
    assert not outside.exists()
    assert not outside.with_name("operator-capture-vault_meta.json").exists()


def test_forged_resource_marker_cannot_initialize_the_callers_vault(
        tmp_path) -> None:
    """An inherited environment marker is not proof that bd-opv owns a path."""
    caller = tmp_path / "caller"
    caller.mkdir()
    env = dict(os.environ)
    for key in _ISOLATION_ENV | {
        "BD_CAPTURE_VAULT", "BD_SECRETS_FILE", "BD_WORK", "PYTHONPATH",
        "_BD_OPV_REEXEC",
    }:
        env.pop(key, None)
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "BD_HOME": str(caller),
        "BD_INSTALL_DIR": str(caller),
        "BD_WORK": str(_REPO),
        "HOME": str(caller),
        "PYTHONDONTWRITEBYTECODE": "1",
        "_BD_OPV_REEXEC": "1",
        # This is deliberately forged: _enter_resource_boundary was not called.
        "_BD_OPV_RESOURCE_ROOT": str(caller),
    })
    probe = textwrap.dedent(
        f"""
        import importlib.machinery
        import importlib.util
        import json
        from pathlib import Path

        tool = Path({str(_TOOL)!r})
        spec = importlib.util.spec_from_loader(
            "row282_forged_boundary_probe",
            importlib.machinery.SourceFileLoader(
                "row282_forged_boundary_probe", str(tool)
            ),
        )
        assert spec is not None and spec.loader is not None
        opv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(opv)
        print(json.dumps(opv.chk_health_version()))
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=caller,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    verdict = json.loads(done.stdout.splitlines()[-1])
    assert verdict[0] == "FAIL", verdict
    assert "owned bd-opv resource boundary" in verdict[1]
    assert not (caller / "secrets.json").exists()
    assert not (caller / "secrets_meta.json").exists()


def test_push_subscription_and_vapid_stores_are_inside_the_same_boundary(tmp_path) -> None:
    run = _run_opv(tmp_path, "OPV-PUSH", seed_push=True)
    root = _assert_resource_boundary(run)

    assert run["dependency_probes"] == [{
        "error": "row 294 fixture denies ambient pywebpush",
        "kind": "pywebpush.import_absent",
    }]

    statements = [event["sql"] for event in run["events"]
                  if event["kind"] == "sqlite.sql"]
    inserts = [sql for sql in statements
               if "INSERT OR REPLACE INTO push_subscriptions" in sql]
    deletes = [sql for sql in statements
               if "DELETE FROM push_subscriptions" in sql]
    dispatch_reads = [sql for sql in statements
                      if "SELECT endpoint, p256dh, auth, last_sent_at" in sql]
    assert len(inserts) == 1
    assert len(deletes) == 1
    assert len(dispatch_reads) == 1
    db_targets = {Path(event["path"]) for event in run["events"]
                  if event["kind"] == "sqlite.connect"}
    assert db_targets == {root / "state" / "downloader_history.db"}
    vapid_writes = [event for event in run["events"]
                    if event["kind"] == "open.write"
                    and Path(event["path"]).name == "vapid_keys.json.tmp"]
    assert len(vapid_writes) == 1
    socket_attempts = [event for event in run["events"]
                       if event["kind"] == "socket.connect"]
    assert len(socket_attempts) == 0
    assert "'failed': 1" in run["done"].stdout


def test_escape_detector_fails_loudly_on_the_exact_outside_store(tmp_path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "operator" / "downloader_history.db"
    run = {
        "done": subprocess.CompletedProcess([], 0, "1 PASS", ""),
        "events": [],
        "final": [{"kind": "final.environment", "environment": {
            "HOME": str(root / "home"),
            "_BD_OPV_RESOURCE_ROOT": str(root),
        }}],
        "writes": [
            {"kind": "open.write", "path": str(root / "inside.json")},
            {"kind": "sqlite.connect", "path": str(outside)},
        ],
        "before": {},
        "after": {},
    }
    assert len(run["writes"]) == 2
    with pytest.raises(AssertionError, match="resource writes escaped bd-opv sandbox") as caught:
        _assert_resource_boundary(run)
    assert str(outside) in str(caught.value)


def test_transform_control_imports_bd_opv_without_judging_isolation() -> None:
    spec = importlib.util.spec_from_loader(
        "row282_bd_opv_import_control",
        importlib.machinery.SourceFileLoader("row282_bd_opv_import_control", str(_TOOL)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.REGISTRY) > 0
