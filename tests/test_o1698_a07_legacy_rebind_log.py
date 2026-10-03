"""O1698 a07-legacy-rebind-log (T215 lens LOW, PM O1737 item 3): the legacy first-login rebind is audited EXPLICITLY.

When bind_oidc_login admits a user whose record had no binding or a legacy/partial one, provision_user emits ONE WARN
line OIDC_ACCOUNT_REBOUND carrying the user, the OLD iss (or none), the NEW iss, the sub as a sha256 prefix (never raw)
and the UTC time. Admit/refuse decisions are unchanged; an exact-bound login, a create and a refusal log no rebind.

Hermetic: a tmp accounts store (cwd), oidc_issuer pinned, no IdP. Asserted on the emitted message, so the cases fail on
main (68ce2c95) by behaviour: its line has no old_iss / at.
"""
import datetime
import hashlib
import json
import logging
import re
import time

import pytest

import bulk_downloader.oidc as oidc
import bulk_downloader.user_accounts as ua

BD_GATE_SCOPE = "module"

ISS = "https://idp.example.test"
OLD_ISS = "https://old-idp.example.test"
REBOUND = "OIDC_ACCOUNT_REBOUND"
AT = re.compile(r" at=(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)(?: |$)")


def _rec(oidc_rec=None, role="operator"):
    rec = {"pw_hash": "00", "salt": "00", "iters": 1, "role": role, "created_ts": 1}
    if oidc_rec is not None:
        rec["oidc"] = oidc_rec
    return rec


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(oidc, "oidc_config", lambda: {"enabled": True, "issuer": ISS, "client_id": "bd"})

    def seed(**users):
        path = tmp_path / ua.ACCOUNTS_FILE
        path.write_text(json.dumps({"users": users, "signing_key": "ab" * 32}, indent=1))
        return path
    return seed


def _rebound_lines(caplog, fn):
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.oidc"):
        fn()
    return [r for r in caplog.records if REBOUND in r.getMessage()]


def _login(name, sub, iss=ISS):
    return oidc.provision_user({"iss": iss, "sub": sub, "preferred_username": name})


# ---- the legacy rebind line: user, OLD iss, NEW iss, hashed sub, UTC time --------------------------------------------
OLD = {"partial-old-idp": ({"iss": OLD_ISS}, repr(OLD_ISS)),
       "partial-same-idp": ({"iss": ISS}, repr(ISS)),
       "partial-trailing-slash": ({"iss": OLD_ISS + "/", "sub": ""}, repr(OLD_ISS + "/")),
       "no-binding": (None, "none"),
       "sub-only": ({"sub": "sub-old"}, "none"),
       "non-str-iss": ({"iss": 7, "sub": "sub-old"}, "none"),
       "non-dict": ("legacy-string", "none")}


@pytest.mark.parametrize("legacy,old_shown", list(OLD.values()), ids=list(OLD))
def test_legacy_rebind_logs_old_and_new_issuer_with_utc_time(store, caplog, legacy, old_shown):
    store(carol=_rec(oidc_rec=legacy))
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    lines = _rebound_lines(caplog, lambda: _login("carol", "sub-carol"))
    after = datetime.datetime.now(datetime.timezone.utc)
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    msg = lines[0].getMessage()
    assert "user='carol'" in msg
    assert f"old_iss={old_shown} " in msg
    assert f" iss={ISS!r} " in msg
    assert "sub_sha256=" + hashlib.sha256(b"sub-carol").hexdigest()[:16] in msg
    assert "sub-carol" not in msg and "sub-old" not in msg
    m = AT.search(msg)
    assert m, msg
    at = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    assert before <= at <= after


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset: POSIX only")
def test_at_is_utc_under_a_non_utc_local_zone(store, caplog, monkeypatch):
    monkeypatch.setenv("TZ", "IST-5:30")       # local = UTC+5:30: a local-time stamp lands outside the UTC window
    time.tzset()
    try:
        store(carol=_rec())
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        lines = _rebound_lines(caplog, lambda: _login("carol", "sub-carol"))
        after = datetime.datetime.now(datetime.timezone.utc)
    finally:
        monkeypatch.undo()
        time.tzset()
    at = AT.search(lines[0].getMessage())
    assert at, lines[0].getMessage()
    stamp = datetime.datetime.strptime(at.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    assert before <= stamp <= after


def test_old_issuer_is_quoted_so_a_stored_newline_cannot_forge_a_log_line(store, caplog):
    forged = "https://x.test\nOIDC_ACCOUNT_REBOUND user='admin'"
    store(dan=_rec(oidc_rec={"iss": forged}))
    lines = _rebound_lines(caplog, lambda: _login("dan", "sub-dan"))
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "\n" not in msg and f"old_iss={forged!r} " in msg and msg.count(REBOUND) == 2   # the forged text stays inside the quoted value


# ---- controls: no rebind -> no rebind line ---------------------------------------------------------------------------
def test_exact_bound_login_create_and_refusal_log_no_rebind(store, caplog):
    store(bob=_rec(oidc_rec={"iss": ISS, "sub": "sub-bob"}), ivy=_rec())

    def run():
        assert _login("bob", "sub-bob") == "bob"                       # exact-bound: reuse
        assert _login("erin", "sub-erin") == "erin"                    # create binds at create
        with pytest.raises(ValueError, match="OIDC_ACCOUNT_BINDING_REFUSED"):
            _login("bob", "sub-eve")                                   # other subject
        with pytest.raises(ValueError, match="OIDC_ACCOUNT_BINDING_REFUSED"):
            _login("ivy", "sub-ivy", OLD_ISS)                          # unbound, other issuer
    assert _rebound_lines(caplog, run) == []


# ---- the export: (ok, msg) unchanged; the prior legacy iss comes back through `prior` --------------------------------
def test_bind_oidc_login_keeps_its_contract_and_exports_the_prior_issuer(store):
    path = store(carol=_rec(oidc_rec={"iss": OLD_ISS}), dave=_rec())
    prior = {}
    assert ua.bind_oidc_login("carol", (ISS, "sub-carol"), rebind_issuer=ISS, prior=prior) == (True, "rebound")
    assert prior == {"iss": OLD_ISS}
    assert ua.bind_oidc_login("dave", (ISS, "sub-dave"), rebind_issuer=ISS) == (True, "rebound")
    prior = {}
    assert ua.bind_oidc_login("carol", (ISS, "sub-carol"), rebind_issuer=ISS, prior=prior) == (True, "bound")
    assert prior == {}                                                  # filled only when it rebinds
    assert json.loads(path.read_text())["users"]["carol"]["oidc"] == {"iss": ISS, "sub": "sub-carol"}
