"""Row 475: a vault password could only be tested by SPENDING an unlock.

The only way the toolchain offered to test a candidate master password was
POST /api/secrets/unlock, which shares an escalating back-off with
/api/secrets/change_password through ``auth_throttle.LABEL_MASTER_PASSWORD``,
so every test cost an attempt and enough tests locked the operator out of the
vault they were trying to open. Unifying the fleet's master password forced a
guess-and-see loop across two candidates on nine hosts.

The primitive already existed and was unused: the vault's top-level
``verifier`` is an AES-GCM envelope around the fixed PUBLIC sentinel
``_VERIFIER_PLAINTEXT``, keyed by PBKDF2-SHA256 over the vault's own stored
salt, so deriving that key offline and decrypting the verifier proves a
password with no request, no throttle and no disclosure.

CLAUDE.md A7: UNKNOWN is a failing third state. A vault carrying no verifier
CANNOT be judged this way, and answering NO there would tell an operator their
correct password is wrong.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL = _REPO_ROOT / "toolchain" / "bin" / "bd-vault-verify"

# Documented zero-entropy synthetic values.  None of these is a credential.
_RIGHT = "row475-synthetic-master-password-right"
_WRONG = "row475-synthetic-master-password-wrong"
_KEY = "bulkdl-site-row475"
_VALUE = "row475-synthetic-value"
_ITERATIONS = 1_000

_EXIT_OPENS = 0
_EXIT_NO = 1
_EXIT_UNKNOWN = 2


@pytest.fixture
def vault(monkeypatch, tmp_path) -> Path:
    """A real vault built by the PRODUCT's own code path, not hand-rolled JSON."""
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    at.reset()
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_RIGHT) is True
    backend.set(_KEY, _VALUE)

    path = tmp_path / "secrets.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert "verifier" in blob, "precondition: the product committed a verifier"
    assert blob["salt"], "precondition: the vault carries its own salt"
    assert blob["iterations"] == _ITERATIONS
    yield path
    at.reset()


def _blob(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# MEASURED on test5 at this candidate: one tool invocation costs ~0.3s (the
# fixture vaults use 1,000 KDF iterations). 60s is ~180x the slowest sample and
# stays well under the suite's 240s bound, so every budget here can actually
# fire rather than being dead code the worker timeout preempts first.
_BUDGET_S = 60


def _run(vault_path, candidate, *, extra=()):
    """Invoke the tool with the candidate on STDIN -- never on argv."""
    return subprocess.run(
        [sys.executable, str(_TOOL), "--vault", str(vault_path), *extra],
        input=candidate, capture_output=True, text=True, timeout=_BUDGET_S,
    )


# ── the tool exists and answers ─────────────────────────────────────


def test_the_offline_check_ships_as_a_first_class_tool():
    """Row 475 RED: no tool offered an offline answer at all."""
    assert _TOOL.exists(), f"{_TOOL} must exist"
    assert os.access(_TOOL, os.X_OK), "the tool must be executable"
    assert hasattr(ss, "verify_vault_password_offline"), (
        "the product must expose the offline check its tool calls"
    )


def test_the_right_password_answers_opens(vault):
    result = _run(vault, _RIGHT)
    assert result.returncode == _EXIT_OPENS, result.stderr
    assert result.stdout.startswith("OPENS")


def test_the_wrong_password_answers_no(vault):
    result = _run(vault, _WRONG)
    assert result.returncode == _EXIT_NO, result.stderr
    assert result.stdout.startswith("NO")


def test_the_candidate_never_appears_in_argv_or_the_output(vault):
    """The candidate is never transmitted, printed, or placed where /proc
    exposes it."""
    result = _run(vault, _RIGHT)
    assert _RIGHT not in result.stdout
    assert _RIGHT not in result.stderr
    source = _TOOL.read_text(encoding="utf-8")
    assert "--candidate " not in source and '"--candidate"' not in source, (
        "the candidate must never be an argv value"
    )
    # The tool's own parser must reject a candidate passed positionally.
    rejected = subprocess.run(
        [sys.executable, str(_TOOL), "--vault", str(vault), _RIGHT],
        input="", capture_output=True, text=True, timeout=_BUDGET_S,
    )
    assert rejected.returncode != _EXIT_OPENS


def test_a_candidate_file_is_also_accepted(vault, tmp_path):
    candidate_file = tmp_path / "candidate.txt"
    candidate_file.write_text(_RIGHT + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_TOOL), "--vault", str(vault),
         "--candidate-file", str(candidate_file), "--quiet"],
        capture_output=True, text=True, timeout=_BUDGET_S,
    )
    assert result.returncode == _EXIT_OPENS, result.stderr
    assert result.stdout.strip() == "OPENS"


# ── the zero-network proof, at the socket boundary ──────────────────


def test_the_check_opens_no_socket(vault, monkeypatch):
    """Instrument the resource boundary; source reading is not runtime evidence."""
    opened: list[tuple] = []
    real_socket = socket.socket
    real_create = socket.create_connection

    class _Tripwire(real_socket):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            opened.append(("socket", a))
            super().__init__(*a, **k)

    def _no_connect(*a, **k):
        opened.append(("create_connection", a))
        raise AssertionError("the offline check opened a connection")

    monkeypatch.setattr(socket, "socket", _Tripwire)
    monkeypatch.setattr(socket, "create_connection", _no_connect)

    # Prove the tripwire actually fires, so a green below is not vacuous.
    with pytest.raises(Exception):
        socket.create_connection(("127.0.0.1", 9))
    assert len(opened) == 1, "precondition: the instrument is armed and fires"
    opened.clear()

    verdict, _detail = ss.verify_vault_password_offline(_blob(vault), _RIGHT)

    assert verdict == ss.VAULT_OPENS
    assert opened == [], f"the offline check touched the network: {opened}"


def test_the_check_spends_no_unlock_attempt(vault):
    """The whole point: testing a password must not consume a throttle slot."""
    at.reset()
    before = at.snapshot() if hasattr(at, "snapshot") else None
    label = getattr(at, "LABEL_MASTER_PASSWORD", None)
    assert label is not None, "precondition: the shared throttle label exists"

    for _ in range(12):
        assert ss.verify_vault_password_offline(_blob(vault), _WRONG)[0] == ss.VAULT_NO

    allowed, _info = at.check(label)
    assert allowed is True, (
        "12 offline NO answers must leave the master-password throttle untouched"
    )
    if before is not None:
        assert at.snapshot() == before


def test_the_check_constructs_no_backend(vault, monkeypatch):
    """Construction reads durable state; an offline check must not do it."""
    constructed: list[int] = []
    real_init = ss.MasterPasswordBackend.__init__

    def _counting(self, *a, **k):
        constructed.append(1)
        return real_init(self, *a, **k)

    monkeypatch.setattr(ss.MasterPasswordBackend, "__init__", _counting)
    ss.MasterPasswordBackend()
    assert len(constructed) == 1, "precondition: the counter fires"
    constructed.clear()

    ss.verify_vault_password_offline(_blob(vault), _RIGHT)

    assert constructed == [], "the offline check constructed a backend"


def test_the_check_does_not_write_to_the_vault(vault):
    digest_before = vault.read_bytes()
    mtime_before = vault.stat().st_mtime_ns

    opens = _run(vault, _RIGHT)
    no = _run(vault, _WRONG)

    # Prove BOTH runs actually reached a verdict first: without this the file
    # is trivially unchanged whenever the tool does not run at all.
    assert opens.returncode == _EXIT_OPENS, opens.stderr
    assert opens.stdout.startswith("OPENS")
    assert no.returncode == _EXIT_NO, no.stderr
    assert no.stdout.startswith("NO")

    assert vault.read_bytes() == digest_before
    assert vault.stat().st_mtime_ns == mtime_before


# ── negative controls: UNKNOWN is never a soft NO ───────────────────


def test_a_vault_with_no_verifier_is_unknown_not_no(vault, tmp_path):
    """Negative control: a vault predating the verifier field cannot be judged."""
    blob = _blob(vault)
    assert "verifier" in blob, "precondition: it had one to remove"
    blob.pop("verifier")
    stripped = tmp_path / "no-verifier.json"
    stripped.write_text(json.dumps(blob), encoding="utf-8")

    result = _run(stripped, _RIGHT)

    assert result.returncode == _EXIT_UNKNOWN, "a nonzero exit, never NO"
    assert result.stdout.startswith("UNKNOWN")
    assert "NO" != result.stdout.split()[0]
    verdict, _ = ss.verify_vault_password_offline(json.loads(
        stripped.read_text(encoding="utf-8")), _RIGHT)
    assert verdict == ss.VAULT_UNKNOWN


def test_a_malformed_verifier_is_unknown_not_no(vault, tmp_path):
    """Negative control: a damaged verifier is unmeasurable, not a wrong password."""
    blob = _blob(vault)
    blob["verifier"] = {"nonce": "!!not base64!!", "ct": "!!"}
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps(blob), encoding="utf-8")

    result = _run(malformed, _RIGHT)

    assert result.returncode == _EXIT_UNKNOWN
    assert result.stdout.startswith("UNKNOWN")


def test_unusable_kdf_parameters_are_unknown_not_no(vault, tmp_path):
    """Negative control: the guard was not widened into 'anything odd is NO'."""
    for field, value in (("iterations", 0), ("iterations", "many"),
                         ("salt", ""), ("salt", 7)):
        blob = _blob(vault)
        blob[field] = value
        broken = tmp_path / f"broken-{field}-{value!r}.json"
        broken.write_text(json.dumps(blob), encoding="utf-8")
        verdict, detail = ss.verify_vault_password_offline(
            json.loads(broken.read_text(encoding="utf-8")), _RIGHT)
        assert verdict == ss.VAULT_UNKNOWN, (field, value, verdict, detail)


def test_an_unreadable_vault_file_is_unknown_not_no(tmp_path):
    """Negative control: an absent or unparseable file is unmeasurable."""
    absent = _run(tmp_path / "absent.json", _RIGHT)
    assert absent.returncode == _EXIT_UNKNOWN

    garbage = tmp_path / "garbage.json"
    garbage.write_bytes(b"{row475 not valid json")
    unparseable = _run(garbage, _RIGHT)
    assert unparseable.returncode == _EXIT_UNKNOWN
    assert unparseable.stdout.startswith("UNKNOWN")


def test_the_intact_vault_still_answers_no_for_a_wrong_password(vault):
    """Negative control: UNKNOWN did not swallow the real NO. An intact vault
    with a wrong candidate must still be a decisive NO, not UNKNOWN."""
    result = _run(vault, _WRONG)
    assert result.returncode == _EXIT_NO
    assert result.stdout.startswith("NO")
    assert "UNKNOWN" not in result.stdout


# ── the tool is wired (test_unwired_bd_tools_do_not_multiply) ────────


def test_the_tool_selftest_passes():
    # The selftest builds a vault and runs five verdicts: MEASURED 0.28 / 0.30
    # / 0.33s over three runs on test5 at this candidate.
    result = subprocess.run([sys.executable, str(_TOOL), "--selftest"],
                            capture_output=True, text=True, timeout=_BUDGET_S)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELFTEST PASS" in result.stdout, (
        "an exit code alone is not a verdict"
    )
