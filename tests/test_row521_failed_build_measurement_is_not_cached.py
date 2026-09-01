"""Row 521 -- /api/health must not cache a build measurement that FAILED.

``build_identity`` wrote ``_BUILD_IDENTITY_CACHE[root] = result``
unconditionally, so the failure value (sha None, built_at None, source
unknown) was retained exactly like a success.  The docstring justified the
cache with "the deployed commit changes only on a deploy, and a deploy
restarts the service" -- a property of a SUCCESSFUL measurement, not of a
failed one.

Two disjoint routes reach that write and both are exercised below: an
exception from the git subprocess (git absent, the timeout elapsing under
deploy load, an OSError from fork), and a nonzero returncode that never
raises at all (a work tree git refuses, e.g. dubious-ownership exit 128).
``api_health`` attaches the block only when a sha was obtained, so the
endpoint then answers HTTP 200 ok true with NO build key -- indistinguishable
from a pre-B1.3 build -- until a restart, the very event that created the
state.

CONTRACT: a measurement that failed is not evidence and must not be cached;
a measurement that SUCCEEDED still is.  Negative control 1 pins the second
half, so the fix cannot be "delete the cache".
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys

import pytest
from flask import Flask

from bulk_downloader import app_health


BD_GATE_SCOPE = "module"


# ── fixture plumbing ──────────────────────────────────────────────────────

@contextlib.contextmanager
def _memory_db():
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _healthy_credentials(_sites_config):
    return {
        "backend": "test", "is_initialized": True, "is_unlocked": True,
        "reference_count": 0, "stored_count": 0, "missing_count": 0,
        "ok": True, "resolved_count": 0, "state": "ready",
        "unavailable_count": 0,
    }


def _health_client(monkeypatch):
    """A fresh app carrying the real ``build_identity``.

    Deliberately NOT the monkeypatch that tests/test_row402_durable_vault_
    verifier.py:115-123 installs over ``build_identity`` -- that fixture
    replaces the exact subject this file measures.
    """
    monkeypatch.setattr(app_health, "db_conn", _memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: {})
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(app_health, "credential_health", _healthy_credentials)
    monkeypatch.setattr(
        app_health, "_attach_download_hold",
        lambda payload: payload.__setitem__(
            "download_hold", {"downloads_allowed": True, "state": "released"}))
    assert app_health.build_identity.__module__ == "bulk_downloader.app_health", (
        "build_identity must be the real one; this file measures IT")
    flask_app = Flask("row521-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


def _git_work_tree(tmp_path):
    """One real commit in a tmp work tree, so ``git rev-parse HEAD`` has an
    answer when the boundary is allowed to succeed."""
    root = tmp_path / "install"
    root.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
               GIT_CONFIG_SYSTEM="/dev/null", GIT_TERMINAL_PROMPT="0")
    def _git(*args):
        proc = subprocess.run(["git", *args], cwd=str(root), env=env,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
        return proc.stdout.strip()
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "row521@example.invalid")
    _git("config", "user.name", "row521")
    (root / "seed.txt").write_text("row 521\n", encoding="utf-8")
    _git("add", "seed.txt")
    _git("commit", "-q", "--no-gpg-sign", "-m", "row521 seed")
    head = _git("rev-parse", "HEAD")
    assert len(head) == 40, head
    return root, head


class _GitBoundary:
    """Counts only the git invocations build_identity makes against ``root``.

    A7 self-audit: the filter is deliberately narrow.  A wrapper that counted
    every subprocess in the process would report a denominator that is not
    the subject, which is the same class of defect this row is about.
    """

    def __init__(self, root, *, failures):
        self.root = str(root)
        self.failures = failures       # list of callables, one per early call
        self.calls: list[list[str]] = []   # the CURRENT measurement window
        self.total = 0                     # every invocation, ever
        self._real = subprocess.run

    def install(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self)
        return self

    def __call__(self, argv, *args, **kwargs):
        mine = (isinstance(argv, (list, tuple)) and argv
                and argv[0] == "git" and str(kwargs.get("cwd")) == self.root)
        if not mine:
            return self._real(argv, *args, **kwargs)
        self.calls.append(list(argv))
        # TWO DENOMINATORS, DELIBERATELY SEPARATE.  ``total`` schedules the
        # injected failures and never resets; ``calls`` is the per-request
        # measurement window and does.  Indexing the schedule off the window
        # made take() re-arm the failure, so every request failed identically
        # and the fix looked unfixed -- the harness reproducing the very
        # stale-denominator shape this row is about (CLAUDE.md A7).
        index = self.total
        self.total += 1
        if index < len(self.failures):
            return self.failures[index](argv)
        return self._real(argv, *args, **kwargs)

    def take(self):
        """Invocations since the last take(), as the argv list; resets.

        Returns the LIST, not a count.  A7: an assertion that reset the
        window and then printed ``self.calls`` in its own failure message
        reported an empty argv every time -- a diagnostic that collapsed
        "no git ran" into "some other git ran", which are opposite
        diagnoses.  The window is the message.
        """
        taken = self.calls
        self.calls = []
        return taken


def _raise_oserror(_argv):
    raise OSError("row521: git could not be executed")


def _refuse_dubious_ownership(argv):
    return subprocess.CompletedProcess(
        list(argv), 128, "",
        "fatal: detected dubious ownership in repository")


@pytest.fixture
def cache_cleared():
    """Prove the precondition and leave no residue for a neighbour test."""
    saved = dict(app_health._BUILD_IDENTITY_CACHE)
    app_health._BUILD_IDENTITY_CACHE.clear()
    try:
        yield app_health._BUILD_IDENTITY_CACHE
    finally:
        app_health._BUILD_IDENTITY_CACHE.clear()
        app_health._BUILD_IDENTITY_CACHE.update(saved)


def _install_dir(monkeypatch, root):
    monkeypatch.setenv("BD_INSTALL_DIR", str(root))


# ── RED: the failure is retained for the process lifetime ─────────────────

@pytest.mark.parametrize(
    "first_failure, label",
    [(_raise_oserror, "subprocess raised"),
     (_refuse_dubious_ownership, "nonzero returncode, no exception")],
)
def test_a_failed_build_measurement_is_re_measured_next_request(
        monkeypatch, tmp_path, cache_cleared, first_failure, label):
    root, head = _git_work_tree(tmp_path)
    _install_dir(monkeypatch, root)

    # PRECONDITION: nothing is cached for this root yet, so request 1 is a
    # real measurement rather than a cache read.
    assert str(root) not in cache_cleared, cache_cleared

    boundary = _GitBoundary(root, failures=[first_failure]).install(monkeypatch)
    client = _health_client(monkeypatch)

    first = client.get("/api/health")
    body_one = first.get_json()
    # PRECONDITION: the injected failure really fired, exactly once, and it is
    # the FIRST git call rather than some later one.
    opened = boundary.take()
    assert [a[1] for a in opened] == ["rev-parse"], opened
    assert first.status_code == 200, body_one
    assert body_one["ok"] is True, body_one
    assert "build" not in body_one, (label, body_one.get("build"))

    second = client.get("/api/health")
    body_two = second.get_json()

    # THE DEFECT, and the fix.  On the unfixed tree the second request makes
    # ZERO further git calls and returns no build key, because the failure was
    # cached exactly like a success.
    re_measured = boundary.take()
    assert [a[1] for a in re_measured] == ["rev-parse", "log"], (
        f"{label}: the second request must RE-MEASURE (rev-parse then git "
        f"log); observed argv={re_measured}")
    assert second.status_code == 200, body_two
    assert body_two["ok"] is True, body_two
    assert body_two.get("build"), (label, body_two)
    assert body_two["build"]["source"] == "git", body_two["build"]
    assert body_two["build"]["sha"] == head[:12], (body_two["build"], head)
    assert body_two["build"]["built_at"], body_two["build"]

    # And the SUCCESS is now cached, which is the half the contract keeps.
    assert cache_cleared[str(root)]["sha"] == head[:12], cache_cleared


# ── NEGATIVE CONTROL 1: the fix is not deletion of the cache ──────────────

def test_a_successful_measurement_is_still_cached(
        monkeypatch, tmp_path, cache_cleared):
    """THE MIRROR DEFECT.  A cache that stops caching a good measurement pays
    two git subprocesses on every health request forever."""
    root, head = _git_work_tree(tmp_path)
    _install_dir(monkeypatch, root)
    assert str(root) not in cache_cleared, cache_cleared

    boundary = _GitBoundary(root, failures=[]).install(monkeypatch)
    client = _health_client(monkeypatch)

    first = client.get("/api/health").get_json()
    opened = boundary.take()
    assert [a[1] for a in opened] == ["rev-parse", "log"], opened
    assert first["build"]["sha"] == head[:12], first["build"]

    shas = [first["build"]["sha"]]
    for _ in range(3):
        body = client.get("/api/health").get_json()
        assert boundary.take() == [], "a cached success must re-measure nothing"
        shas.append(body["build"]["sha"])

    assert shas == [head[:12]] * 4, shas


# ── NEGATIVE CONTROL 2: the fix is not attaching a block unconditionally ──

def test_a_directory_that_is_genuinely_not_a_build_still_omits_the_block(
        monkeypatch, tmp_path, cache_cleared):
    """An install dir that is neither a git tree nor holding build_info.json
    still answers 200 ok true with exactly 0 build keys -- the shape
    tests/test_b1_3_build_identity.py::test_health_omits_build_when_info_absent
    pins, asserted here too so this file cannot pass by making every state
    report a build."""
    root = tmp_path / "not-a-build"
    root.mkdir()
    _install_dir(monkeypatch, root)
    assert not (root / ".git").exists()
    assert not (root / "build_info.json").exists()

    boundary = _GitBoundary(root, failures=[]).install(monkeypatch)
    client = _health_client(monkeypatch)

    for _ in range(2):
        response = client.get("/api/health")
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["ok"] is True, body
        assert "build" not in body, body["build"]

    # The boundary really was consulted -- an assertion of absence over a
    # never-run measurement would be vacuous.
    assert len(boundary.calls) >= 1, boundary.calls
    # ... and nothing unknown was cached, so the next deploy is re-measured.
    assert str(root) not in cache_cleared, cache_cleared


def test_build_info_json_fallback_is_cached_because_it_succeeded(
        monkeypatch, tmp_path, cache_cleared):
    """The third source. A recorded-file answer is a SUCCESSFUL measurement of
    a different thing, so it caches; ``source`` says which, per the function's
    own docstring."""
    import json

    root = tmp_path / "release"
    root.mkdir()
    (root / "build_info.json").write_text(
        json.dumps({"sha": "0123456789ab", "built_at": "2026-09-01T00:00:00"}),
        encoding="utf-8")
    _install_dir(monkeypatch, root)

    boundary = _GitBoundary(root, failures=[]).install(monkeypatch)
    client = _health_client(monkeypatch)

    body = client.get("/api/health").get_json()
    assert body["build"]["source"] == "build_info.json", body["build"]
    assert body["build"]["sha"] == "0123456789ab", body["build"]
    used = boundary.take()
    assert len(used) >= 1, used

    again = client.get("/api/health").get_json()
    assert boundary.take() == [], "a cached build_info answer re-measures nothing"
    assert again["build"] == body["build"]


def test_the_cache_is_the_only_retention_mechanism(cache_cleared):
    """Pins the premise: one dict, and this file empties it. If a second store
    appears, these measurements stop meaning what they say."""
    source = (app_health.__file__)
    text = open(source, encoding="utf-8").read()
    assert text.count("_BUILD_IDENTITY_CACHE") == 3, text.count(
        "_BUILD_IDENTITY_CACHE")
    assert sys.modules["bulk_downloader.app_health"]._BUILD_IDENTITY_CACHE \
        is cache_cleared
