"""O1567 fx-relogin-vault-locked: a re-login refused because the vault is still locked must not spend anything.

MEASURED on test4 (10.0.70.85) 2026-09-29, site-ma-brazzers 1167e615 / site-ma-bangbros 146f9c87: after each
``systemctl restart bulkdownloader`` the restart_resume workers hit auth within ~1 s, before the credential vault
had unlocked.  do_login answered "Credential vault locked: password" (journal "login: SKIPPED -- ... credential
vault is LOCKED", 21:19:50Z and 21:19:51Z per site, again 21:38:18Z) -- no page opened, nothing submitted -- yet
  * each refusal was a session_history ``login_attempt`` (rows 4863-4866, 4872): a day-cap slot per restart, and
  * each spent a re-login retry ("re-logging in (try 1/2)", "(try 2/2)"), so when bangbros' first REAL login
    settled ok at 21:21:44Z its scene went straight to dead_letter "re-login retries exhausted" without a try.

Rule: a vault-locked refusal is like a login cancelled before submit (dl95-cancel-relogin-cap-1): the reservation
is withdrawn and the job keeps its retry budget (pending, retried after the backoff).  Every other failure still
counts both.

Hermetic: real SiteRunner, _handle_auth_required, login_async and session_keeper accounting on the isolated DB;
do_login faked at the runner seam.  No site.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import session_keeper  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SITE = "o1567VaultLocked"
SCENE = "https://site-ma.example.com/scene/11524249/"
VAULT_LOCKED = (False, "Credential vault locked: password", [])
REJECTED = (False, "Rejected login landing: https://example.com/badlogin", [])


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner():
    db_init()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u",
        "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#username"], "pass_field": ["#password"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
        "max_retries": 2,
        session_keeper.LOGIN_CAP_KEY: 10,
    }
    r = SiteRunner(SITE, cfg)
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")   # never a real browser
    r._update_job(SCENE, "running", "Claimed by worker")
    return r


def _counted():
    got = session_keeper.login_attempts_for_day(SITE)
    assert got["status"] == "OK", got
    return got["count"]


def _relogin(r, verdict):
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=verdict):
        r._handle_auth_required(SCENE)
    job = r.jobs[SCENE]
    return job.get("status"), job.get("retries", 0)


def test_vault_locked_relogin_spends_no_day_cap_attempt():
    r = _runner()
    _relogin(r, VAULT_LOCKED)
    assert _counted() == 0, (
        f"O1567 VAULT-LOCKED: a refused (vault locked, nothing submitted) re-login spent {_counted()} "
        "day-cap login attempt(s)")


def test_vault_locked_relogins_keep_the_retry_budget():
    r = _runner()
    for _ in range(3):   # three restarts' worth of refusals
        status, retries = _relogin(r, VAULT_LOCKED)
    assert status == "pending", f"O1567 VAULT-LOCKED: job went {status!r} on vault-locked refusals"
    assert retries == 0, (
        f"O1567 VAULT-LOCKED: vault-locked refusals spent {retries} re-login retr(ies); the first real login "
        "then finds the budget exhausted -> dead_letter")


def test_control_a_real_rejected_login_still_counts_and_spends_a_retry():
    r = _runner()
    status, retries = _relogin(r, REJECTED)
    assert _counted() == 1
    assert (status, retries) == ("pending", 1)
    status, retries = _relogin(r, REJECTED)
    status, retries = _relogin(r, REJECTED)
    assert status == "dead_letter", f"real failures must still exhaust the budget (got {status!r}, {retries})"
