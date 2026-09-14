"""Row 806 -- /api/health must NAME the deployed cloak capability, not omit it.

MEASURED DEFECT, re-verified on c9dd4176 (v3.66.1540) before this file was
written: ``scripts/deploy.sh`` records the cloak disposition durably, beside
the graph pin, in ``<pin>.deploy-capabilities`` ("cloak=OK" / "cloak=ABSENT" /
"cloak=UNKNOWN"), and prints it at three surfaces of its own stdout.  NOTHING
READS THAT RECORD.  ``bulk_downloader/app_health.py`` and
``bulk_downloader/healthcheck.py`` contain zero occurrences of "cloak", so the
/api/health payload -- the one machine-readable surface the fleet polls --
carries no cloak field at all.  MEASURED, not assumed: the current readers
take a NAMED subset of the payload, not all of it (bd-fleet-audit-cmd.sh:14
the status code only; bd-fleet-deploy.sh the code plus ``version`` at :438-440
and, in the 503 branch only, ``degraded``/``credentials.state``/``version`` at
:477-487; deploy.sh's health gate the code and ``version``).  So a host whose
cloak reach is ABSENT is indistinguishable from a verified one through that
surface because THE FIELD DOES NOT EXIST, and naming it here is what a fleet
reader would need before it could read it.  That is the absent-looks-
green shape CLAUDE.md A7 names, and it is row 736's own closing sentence: that
closure landed deploy.sh's three stdout surfaces and explicitly did NOT meet
the "appears in the health payload" clause, which is this row.

WHAT THIS FILE PROVES

  1. The payload carries the recorded state: cloak=OK -> OK, cloak=ABSENT ->
     ABSENT, cloak=UNKNOWN -> UNKNOWN, on BOTH /api/health and /api/health/v2
     (v2's docstring calls itself a SUPERSET of /api/health, so a field on one
     and not the other would make that sentence false).
  2. NO RECORD IS AN EXPLICIT UNKNOWN, and it is DISTINGUISHABLE from a
     RECORDED UNKNOWN: both say state UNKNOWN, and the two blocks are not
     equal -- ``recorded`` is False for one and True for the other.  An
     unmeasured host and a host measured as unmeasurable are two states with
     different operator actions; collapsing them is the A7 defect this row
     forbids in its own acceptance sentence.
  3. EXACTLY ONE FIELD carries the disposition: the payload's cloak-bearing
     top-level keys are exactly ["cloak"], an exact count of 1.
  4. THE PROBE NEVER FAILS HEALTH.  Every cloak state above -- including
     ABSENT, UNKNOWN, an unreadable record and no record at all -- answers
     HTTP 200 with ok True.  Row 686 ruled exit 0 on a cloak WARN to be
     COMPLIANCE, and row 806 keeps it: the qualifier beside the result carries
     the truth, the status code does not.
  5. NEGATIVE CONTROL for 4, so the 200s above are not vacuous: the SAME
     client over a hold store that cannot be read answers 503 with
     ok False.  The assertion can say no.
  6. NEGATIVE CONTROL for 1, failing for the intended reason: a record that
     says ABSENT is never reported as OK, and a record whose content is not a
     cloak line at all is UNKNOWN with its OWN source token -- neither OK nor
     silently merged with "no record".
  7. EVERY BRANCH OF THE READER IS PINNED, including the ones a passing
     payload never exercises: a record whose first six bytes are not
     "cloak=" but whose tail IS a valid state (``abcdefOK``) is
     ``unrecognized_record``, never OK -- drop the ``startswith`` guard and
     that file reports a VERIFIED cloak; an UNREADABLE record is
     ``unreadable_record`` with ``recorded`` True, never merged into
     ``no_record``; and if the reader itself raises, the attachment reports
     ``health_probe_failed``/UNKNOWN rather than any measured state.  Each of
     these was an ESCAPED mutant of this file's first generation
     (806-B2-B-20260914-local, REFUTE, M6/M5/M12).
  8. THE READER'S DEFAULT PATH IS DEPLOY.SH'S.  The default pin location and
     the ".deploy-capabilities" suffix are parsed OUT OF scripts/deploy.sh and
     compared to the health reader's own default.  A drift here would arm a
     reader that watches a file the deploy never writes -- two green surfaces
     and no signal, which is the shape deploy.sh's own comment at the pin
     default warns about.

RED-first on c9dd4176: every test below fails with ``KeyError: 'cloak'`` at
``body["cloak"]`` (and test 8 with ``AttributeError: module
'bulk_downloader.healthcheck' has no attribute 'cloak_record_path'``) -- the
field does not exist, which is exactly the defect.

Preconditions are asserted, never assumed: the record file is read back from
disk and its exact bytes checked BEFORE any verdict about what the payload
made of it, so a test that silently wrote nothing fails as a fixture failure
rather than passing as an "UNKNOWN".
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import auth_throttle as at
from bulk_downloader import healthcheck as hc
from bulk_downloader import secrets_store as ss

# Its subject is two named modules' health surface plus deploy.sh's record
# contract -- one seam, not the whole tree.
BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"

# The three dispositions scripts/deploy.sh can record. Pinned as a literal
# here rather than imported from the reader under test: deriving the
# expectation from the thing being measured is how a dropped state passes.
RECORDED_STATES = ("OK", "ABSENT", "UNKNOWN")

# Zero-entropy documented fixture value (CLAUDE.md A4). It unlocks a vault that
# exists only inside this test's tmp_path and holds nothing.
_MASTER = "row806-synthetic-master-password"
_ITERATIONS = 1_000


@contextmanager
def _memory_db():
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _health_client(monkeypatch):
    """A health blueprint over a memory DB, zero runners, zero sites."""
    monkeypatch.setattr(app_health, "db_conn", _memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: {})
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health, "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None,
                              "source": "unknown"})
    flask_app = Flask("row806-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


def _write_record(monkeypatch, root: Path, content: str | None) -> Path:
    """Point the pin at `root` and write (or deliberately omit) the record.

    The record is read BACK off the disk and its bytes asserted, so a fixture
    that wrote nothing fails here instead of arriving at the endpoint as an
    indistinguishable "no record".
    """
    pin = root / "validation" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BD_GRAPH_HASH_PIN", str(pin))
    record = Path(str(pin) + ".deploy-capabilities")
    if content is None:
        assert not record.exists(), record
        return record
    record.write_text(content, encoding="utf-8")
    # PRECONDITION: the shape really is on disk, with the bytes intended.
    assert record.is_file(), record
    assert record.read_text(encoding="utf-8") == content, record
    return record


def _install_ready_vault(monkeypatch, root: Path):
    """A vault that is initialized AND unlocked, proven at each step.

    Without it the baseline payload is already ``ok: False`` with
    ``degraded: credential_vault_uninitialized`` -- and then "the cloak never
    fails health" would be asserted over a payload that was failing for an
    unrelated reason, which proves nothing. This makes the baseline genuinely
    healthy so the cloak states below are the only variable.
    """
    assert ss._CRYPTO_AVAILABLE, (
        "cryptography is required: without it the healthy baseline cannot be "
        "built and the never-503 claim would be vacuous")
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = _ITERATIONS
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    at.reset()
    # PRECONDITION: a brand-new vault is neither initialized nor unlocked ...
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    # ... and the first unlock is the product's own first-use setup path.
    assert backend.unlock(_MASTER) is True
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    return backend


def _measure(monkeypatch, root: Path, content: str | None,
             path: str = "/api/health"):
    _write_record(monkeypatch, root, content)
    response = _health_client(monkeypatch).get(path)
    body = response.get_json()
    assert body is not None, response.get_data(as_text=True)
    return response, body


# ── 1. the recorded state reaches the payload, on both surfaces ─────────────

@pytest.mark.parametrize("state", RECORDED_STATES)
@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_the_recorded_cloak_state_is_named_in_the_health_payload(
        clean_workdir, monkeypatch, path, state):
    _, body = _measure(monkeypatch, clean_workdir, f"cloak={state}\n",
                       path=path)
    assert "cloak" in body, sorted(body)
    block = body["cloak"]
    assert block["state"] == state, block
    assert block["recorded"] is True, block
    assert block["source"] == "deploy_record", block


# ── 2. no record is an EXPLICIT unknown, distinguishable from a recorded one ─

def test_no_record_is_an_explicit_unknown_not_an_absent_field(
        clean_workdir, monkeypatch):
    _, body = _measure(monkeypatch, clean_workdir, None)
    assert "cloak" in body, sorted(body)
    block = body["cloak"]
    assert block["state"] == "UNKNOWN", block
    assert block["recorded"] is False, block
    assert block["source"] == "no_record", block


def test_an_unmeasured_host_and_a_recorded_unknown_are_distinguishable(
        clean_workdir, monkeypatch):
    """Two states, opposite operator actions, must not share one word."""
    _, absent_body = _measure(monkeypatch, clean_workdir, None)
    _, recorded_body = _measure(monkeypatch, clean_workdir, "cloak=UNKNOWN\n")
    missing, recorded = absent_body["cloak"], recorded_body["cloak"]
    # Both are UNKNOWN -- that is the point, and why one word is not enough.
    assert missing["state"] == recorded["state"] == "UNKNOWN", (missing,
                                                                recorded)
    # ... and the blocks are NOT equal: the payload tells them apart.
    assert missing != recorded, missing
    assert missing["recorded"] is False and recorded["recorded"] is True, (
        missing, recorded)
    # Neither is confusable with the two measured dispositions.
    _, ok_body = _measure(monkeypatch, clean_workdir, "cloak=OK\n")
    _, gone_body = _measure(monkeypatch, clean_workdir, "cloak=ABSENT\n")
    states = [missing["state"], recorded["state"],
              ok_body["cloak"]["state"], gone_body["cloak"]["state"]]
    assert states == ["UNKNOWN", "UNKNOWN", "OK", "ABSENT"], states
    blocks = [missing, recorded, ok_body["cloak"], gone_body["cloak"]]
    # EXACT COUNT: four dispositions, four distinct blocks.
    assert len({tuple(sorted(b.items())) for b in blocks}) == 4, blocks


# ── 3. exactly one field ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_the_disposition_lives_in_exactly_one_payload_field(
        clean_workdir, monkeypatch, path):
    record = _write_record(monkeypatch, clean_workdir, "cloak=ABSENT\n")
    # PRECONDITION: the fixture built a NONZERO shape to be reported.
    assert record.read_text(encoding="utf-8").strip() == "cloak=ABSENT"
    body = _health_client(monkeypatch).get(path).get_json()
    bearers = sorted(k for k in body if "cloak" in k.lower())
    assert bearers == ["cloak"], bearers
    assert len(bearers) == 1, bearers


# ── 4 + 5. never fails health, and the 200 is not vacuous ───────────────────

@pytest.mark.parametrize("content", ["cloak=OK\n", "cloak=ABSENT\n",
                                     "cloak=UNKNOWN\n", "not-a-cloak-line\n",
                                     None])
def test_no_cloak_state_ever_fails_the_health_probe(
        clean_workdir, monkeypatch, content):
    _install_ready_vault(monkeypatch, clean_workdir / "vault")
    response, body = _measure(monkeypatch, clean_workdir, content)
    # PRECONDITION: the baseline is healthy for reasons that are not the
    # cloak, so what follows measures the cloak and nothing else.
    assert body["vault_ready"] is True, body
    assert body["download_hold"]["state"] == "clear", body["download_hold"]
    assert body["cloak"]["state"] in ("OK", "ABSENT", "UNKNOWN"), body["cloak"]
    assert body["ok"] is True, body
    assert response.status_code == 200, (response.status_code, body)
    assert "cloak" not in str(body.get("degraded", "")), body


def test_the_same_client_does_report_503_when_something_really_is_degraded(
        clean_workdir, monkeypatch):
    """NEGATIVE CONTROL for the assertion above: it can say no.

    An unreadable hold store is a genuine degradation (download_hold's
    UNKNOWN), and this client answers it 503 -- so the 200s asserted over
    every cloak state are a measurement, not a constant.
    """
    _install_ready_vault(monkeypatch, clean_workdir / "vault")
    _write_record(monkeypatch, clean_workdir, "cloak=ABSENT\n")
    (clean_workdir / "app_config.json").write_text("{ not json",
                                                   encoding="utf-8")
    response = _health_client(monkeypatch).get("/api/health")
    body = response.get_json()
    assert body["download_hold"]["state"] == "unknown", body["download_hold"]
    assert body["ok"] is False, body
    assert response.status_code == 503, response.status_code
    # And the cloak field is still NAMED on the degraded host -- reporting
    # health with the state named is row 686's amended negative control.
    assert body["cloak"]["state"] == "ABSENT", body["cloak"]


# ── 6. negative control: a degraded record is never laundered into OK ───────

@pytest.mark.parametrize("content,expected_state,expected_source", [
    ("cloak=ABSENT\n", "ABSENT", "deploy_record"),
    ("cloak=UNKNOWN\n", "UNKNOWN", "deploy_record"),
    ("cloak=\n", "UNKNOWN", "unrecognized_record"),
    ("not-a-cloak-line\n", "UNKNOWN", "unrecognized_record"),
    ("cloak=OK extra\n", "UNKNOWN", "unrecognized_record"),
])
def test_a_degraded_or_unparseable_record_never_reads_as_ok(
        clean_workdir, monkeypatch, content, expected_state, expected_source):
    _, body = _measure(monkeypatch, clean_workdir, content)
    block = body["cloak"]
    assert block["state"] != "OK", (content, block)
    assert block["state"] == expected_state, (content, block)
    assert block["source"] == expected_source, (content, block)
    # An unparseable record is NOT the same as no record: the file was there.
    if expected_source == "unrecognized_record":
        assert block["recorded"] is True, block


# ── 7. every reader branch is pinned, including the unreachable-looking ones ─

# Records whose first six bytes are NOT "cloak=" but whose seventh byte onward
# IS a recorded state. Each one is a near-miss a deploy could plausibly write
# (an uppercase key, a colon for the equals) plus one arbitrary six-byte
# prefix. Without the ``startswith("cloak=")`` guard the reader slices blind
# and every one of them reports a VERIFIED cloak -- absent-looks-green inside
# the reader built to end it. This is ESCAPED mutant M6 of generation
# 806-B2-B-20260914-local, pinned.
_SIX_BYTE_PREFIX_THEN_STATE = ["abcdefOK\n", "CLOAK=OK\n", "cloak:ABSENT\n"]


@pytest.mark.parametrize("content", _SIX_BYTE_PREFIX_THEN_STATE)
def test_a_record_that_is_not_a_cloak_line_is_never_read_as_a_state(
        clean_workdir, monkeypatch, content):
    # POSITIVE CONTROL: the genuine six-byte prefix IS accepted, so what
    # follows is a discrimination and not a reader that rejects everything.
    _, ok_body = _measure(monkeypatch, clean_workdir, "cloak=OK\n")
    assert ok_body["cloak"]["state"] == "OK", ok_body["cloak"]
    assert ok_body["cloak"]["source"] == "deploy_record", ok_body["cloak"]
    # PRECONDITION: the fixture really is the shape this test is about --
    # six bytes that are not the key, then a state the reader would accept.
    token = content.strip()
    assert not token.startswith("cloak="), token
    assert token[6:] in RECORDED_STATES, token

    _, body = _measure(monkeypatch, clean_workdir, content)
    block = body["cloak"]
    assert block["state"] == "UNKNOWN", (content, block)
    assert block["source"] == "unrecognized_record", (content, block)
    assert block["recorded"] is True, (content, block)
    # ... and it is the SAME disposition as any other unparseable record: the
    # tail happening to spell a state buys it nothing.
    _, plain_body = _measure(monkeypatch, clean_workdir, "not-a-cloak-line\n")
    assert block == plain_body["cloak"], (block, plain_body["cloak"])


def _unreadable_record(monkeypatch, root: Path) -> Path:
    """Point the pin at a record path that EXISTS and cannot be read.

    A directory at the record path is the cheapest real OSError (open() raises
    IsADirectoryError, an OSError subclass) that does not depend on running as
    a non-root user, which a chmod-000 file would.
    """
    pin = root / "validation" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BD_GRAPH_HASH_PIN", str(pin))
    record = Path(str(pin) + ".deploy-capabilities")
    record.mkdir()
    # PRECONDITION: it exists, and reading it really does raise.
    assert record.exists(), record
    with pytest.raises(OSError):
        record.open(encoding="utf-8")
    return record


def test_an_unreadable_record_is_its_own_disposition_not_a_missing_one(
        clean_workdir, monkeypatch):
    """ESCAPED mutant M5, pinned: unreadable must not collapse into no_record.

    A host that never recorded anything and a host whose record cannot be read
    lead to different operator actions -- deploy the one, fix the filesystem of
    the other -- so under CLAUDE.md A7 they may not share one answer.
    """
    record = _unreadable_record(monkeypatch, clean_workdir)
    body = _health_client(monkeypatch).get("/api/health").get_json()
    block = body["cloak"]
    assert block["state"] == "UNKNOWN", block
    assert block["source"] == "unreadable_record", block
    assert block["recorded"] is True, block
    assert block["record_path"] == str(record), block
    assert block["detail"] == "IsADirectoryError", block
    # NEGATIVE CONTROL: the no-record disposition, measured the same way but
    # over a PRISTINE root (this one's record path is now a directory and can
    # never be "absent" again), is a DIFFERENT block -- so "recorded" above is
    # a reading, not a constant.
    _, missing_body = _measure(monkeypatch, clean_workdir / "pristine", None)
    assert missing_body["cloak"]["source"] == "no_record", missing_body
    assert missing_body["cloak"]["recorded"] is False, missing_body
    assert block != missing_body["cloak"], block


def test_an_unreadable_record_still_answers_the_probe_200(
        clean_workdir, monkeypatch):
    """Item 4 names the unreadable record explicitly; this measures it."""
    _install_ready_vault(monkeypatch, clean_workdir / "vault")
    _unreadable_record(monkeypatch, clean_workdir)
    response = _health_client(monkeypatch).get("/api/health")
    body = response.get_json()
    assert body["cloak"]["source"] == "unreadable_record", body["cloak"]
    assert body["ok"] is True, body
    assert response.status_code == 200, (response.status_code, body)


def _undecodable_record(monkeypatch, root: Path) -> Path:
    """Point the pin at a record path that EXISTS and is not valid UTF-8.

    UnicodeDecodeError is a ValueError subclass, not an OSError, so the
    reader's ``except OSError`` did not catch it (correctness-leg finding
    ROW A, VERDICT-correctness.md).
    """
    pin = root / "validation" / "KNOWLEDGE_GRAPH.content.sha256"
    pin.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BD_GRAPH_HASH_PIN", str(pin))
    record = Path(str(pin) + ".deploy-capabilities")
    record.write_bytes(b"cloak=\xff\xfe\n")
    # PRECONDITION: it exists, and reading it as UTF-8 really does raise --
    # the probe can say yes.
    assert record.exists(), record
    with pytest.raises(UnicodeDecodeError):
        record.open(encoding="utf-8").read()
    return record


def test_an_undecodable_record_is_unreadable_not_a_probe_failure(
        clean_workdir, monkeypatch):
    """Correctness leg finding ROW A, pinned: same disposition as M5's
    unreadable OSError, not the caller's generic health_probe_failed.

    POSITIVE CONTROL beside the fixture: a plain "cloak=OK\\n" record (ASCII,
    valid UTF-8) is read normally by the same client first.
    """
    _install_ready_vault(monkeypatch, clean_workdir / "vault")
    _, ok_body = _measure(monkeypatch, clean_workdir, "cloak=OK\n")
    assert ok_body["cloak"]["state"] == "OK", ok_body["cloak"]
    assert ok_body["cloak"]["source"] == "deploy_record", ok_body["cloak"]

    record = _undecodable_record(monkeypatch, clean_workdir)
    body = _health_client(monkeypatch).get("/api/health").get_json()
    block = body["cloak"]
    assert block["state"] == "UNKNOWN", block
    assert block["source"] == "unreadable_record", block
    assert block["recorded"] is True, block
    assert block["record_path"] == str(record), block
    assert block["detail"] == "UnicodeDecodeError", block
    # NEGATIVE CONTROL: not the caller-level generic failure disposition.
    assert block["source"] != "health_probe_failed", block
    assert body["ok"] is True, body


def test_the_payload_names_a_cloak_probe_that_itself_raises(
        clean_workdir, monkeypatch):
    """ESCAPED mutant M12, pinned: the fail-open path reports WHY, as UNKNOWN.

    ``_attach_cloak_capability`` swallows anything ``cloak_capability`` throws
    so a reader defect can never 503 a healthy host (row 686). Unpinned, that
    handler is free to answer OK -- a health payload that reports a VERIFIED
    cloak precisely when the cloak was never read. It answers UNKNOWN with its
    own source token instead, and this is the only test that reaches it.
    """
    _install_ready_vault(monkeypatch, clean_workdir / "vault")
    _write_record(monkeypatch, clean_workdir, "cloak=OK\n")

    # POSITIVE CONTROL: over that record, un-sabotaged, the payload says OK --
    # so the UNKNOWN below is caused by the raise and by nothing else.
    healthy = _health_client(monkeypatch).get("/api/health").get_json()
    assert healthy["cloak"]["state"] == "OK", healthy["cloak"]

    def _boom(path=None):
        raise RuntimeError("row806 synthetic reader failure")

    monkeypatch.setattr(hc, "cloak_capability", _boom)
    response = _health_client(monkeypatch).get("/api/health")
    body = response.get_json()
    block = body["cloak"]
    assert block["state"] == "UNKNOWN", block
    assert block["state"] != "OK", block
    assert block["source"] == "health_probe_failed", block
    assert block["recorded"] is False, block
    assert block["record_path"] is None, block
    assert block["detail"] == "RuntimeError", block
    # A reader that fell over still does not fail the host's health.
    assert body["ok"] is True, body
    assert response.status_code == 200, (response.status_code, body)


# ── 8. the reader watches the file the deploy writes ────────────────────────

def _deploy_sh_record_path() -> str:
    """The record path scripts/deploy.sh actually writes, parsed from it."""
    text = DEPLOY_SH.read_text(encoding="utf-8")
    pin = re.search(r'^PIN="\$\{BD_GRAPH_HASH_PIN:-([^}"]+)\}"$',
                    text, re.M)
    suffix = re.search(r'^PIN_CLOAK_RECORD="\$\{PIN\}(\S+)"$', text, re.M)
    # POSITIVE CONTROL: the parse found the real lines, not an empty match.
    assert pin is not None, "PIN default not found in scripts/deploy.sh"
    assert suffix is not None, "PIN_CLOAK_RECORD not found in scripts/deploy.sh"
    assert pin.group(1).startswith("/"), pin.group(1)
    assert suffix.group(1) == ".deploy-capabilities", suffix.group(1)
    return pin.group(1) + suffix.group(1)


def test_the_health_reader_default_is_the_path_deploy_sh_writes(monkeypatch):
    monkeypatch.delenv("BD_GRAPH_HASH_PIN", raising=False)
    assert hc.cloak_record_path() == _deploy_sh_record_path()


def test_the_health_reader_follows_the_pin_override_deploy_sh_honours(
        tmp_path, monkeypatch):
    """BD_GRAPH_HASH_PIN moves both sides, or a test host reads the wrong file."""
    pin = tmp_path / "pin.sha256"
    monkeypatch.setenv("BD_GRAPH_HASH_PIN", str(pin))
    assert hc.cloak_record_path() == str(pin) + ".deploy-capabilities"
    # NEGATIVE CONTROL: the override is what moved it, not a constant.
    assert hc.cloak_record_path() != _deploy_sh_record_path()
