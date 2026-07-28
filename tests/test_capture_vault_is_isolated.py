"""The capture may use its own vault, and only when it says so twice.

WHY THIS EXISTS. capture.sh stops the service and starts a fresh process
(capture.sh:206 and :452). The master-password key is in-memory only
(secrets_store.py: self._key, is_unlocked() is `self._key is not None`), with no
persistence and no auto-unlock path anywhere in the tree. So the vault is
NECESSARILY locked when the seeder runs at step 5a, and an operator unlocking
beforehand cannot help -- the capture's own restart discards it. L6/L8 were
therefore unsatisfiable under capture.sh no matter what the operator did.

Confirmed on test4 2026-07-28: identical seeder command, one variable changed.
Inside a capture it printed "REFUSED - the secrets vault is LOCKED"; standalone
with no restart it reached seed_login.

THE SHAPE OF THE FIX. The capture points BD at a SEPARATE vault file holding
exactly one credential -- the fixture's own published test password, which
tools/fixture_site.py prints in its module docstring and which authenticates
against 127.0.0.1 only. The operator's real vault is never opened, never
unlocked, and never at risk. What L6/L8 assert is that BD can store a
credential, drive a login, persist a jar and record auth health; which FILE the
vault lives in is irrelevant to whether that code path works, so this supplies
input to a real path rather than substituting for its output.

THE DANGER THIS FILE CONTAINS. MasterPasswordBackend.unlock() accepts ANY
password when the vault holds no ciphertexts, stamping it as the verifier on the
first set(). So a stray env var that silently redirected the vault would not
error -- it would hand back an empty, trivially-unlockable credential store and
look healthy. That is worse than crashing, and it is why the override needs two
keys rather than one: a path alone does nothing. Every other BD_* path override
in the tree is single-key; this one deliberately is not, and the reason is that
none of the others can silently produce a working-looking empty vault.

The password is NEVER defaulted in source. capture.sh supplies it at runtime.
A hardcoded default would mean every install shipped a known unlock.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bulk_downloader import secrets_store as ss


DEFAULT_NAME = "secrets.json"
DEFAULT_META_NAME = "secrets_meta.json"


def _resolve(monkeypatch, **env):
    """Resolve vault paths under an exact environment.

    Both keys are cleared first so a value inherited from the ambient
    environment cannot decide the outcome -- the denominator is what this test
    sets, not what the host happens to carry.
    """
    monkeypatch.delenv("BD_SECRETS_FILE", raising=False)
    monkeypatch.delenv("BD_CAPTURE_VAULT", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ss._resolve_vault_paths()


# ── the default is untouched ─────────────────────────────────────────────────

def test_with_no_override_the_vault_is_where_it_has_always_been(monkeypatch):
    secrets, meta = _resolve(monkeypatch)
    assert secrets == Path(DEFAULT_NAME), secrets
    assert meta == Path(DEFAULT_META_NAME), meta


def test_the_module_attribute_still_carries_the_resolved_default():
    """The 7 test files that monkeypatch ss.SECRETS_FILE must keep working.

    Resolution happens at import and is ASSIGNED to the module attribute, the
    way app.py:33 binds BD_SITES_CONFIG_PATH. A call-time getter would have
    left every one of those monkeypatches silently inert -- the patch would
    apply to an attribute nothing reads.
    """
    assert isinstance(ss.SECRETS_FILE, Path)
    assert isinstance(ss.SECRETS_META_FILE, Path)


# ── one key is not enough ────────────────────────────────────────────────────

def test_the_path_alone_does_not_move_the_vault(monkeypatch, tmp_path):
    """A stray BD_SECRETS_FILE must be inert.

    This is the test that matters most, and it is also the one most at risk of
    passing for the wrong reason: before the override existed it passed
    vacuously. It is mutation-tested in the cut that introduced it -- honouring
    a single key makes it FAIL.
    """
    stray = tmp_path / "somewhere-else.json"
    secrets, meta = _resolve(monkeypatch, BD_SECRETS_FILE=str(stray))
    assert secrets == Path(DEFAULT_NAME), (
        f"BD_SECRETS_FILE alone redirected the vault to {secrets}. One env var "
        f"must not be able to do this: unlock() accepts any password on a vault "
        f"with no ciphertexts, so a silent redirect yields an empty, trivially "
        f"unlockable credential store that looks healthy."
    )
    assert meta == Path(DEFAULT_META_NAME), meta


def test_the_opt_in_alone_does_not_move_the_vault(monkeypatch):
    """BD_CAPTURE_VAULT with no path has nowhere to go."""
    secrets, meta = _resolve(monkeypatch, BD_CAPTURE_VAULT="1")
    assert secrets == Path(DEFAULT_NAME), secrets
    assert meta == Path(DEFAULT_META_NAME), meta


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", " 1"])
def test_only_an_exact_opt_in_counts(monkeypatch, tmp_path, value):
    """Truthiness is not consent. Only "1" opts in."""
    stray = tmp_path / "capture-secrets.json"
    secrets, _meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=str(stray), BD_CAPTURE_VAULT=value)
    assert secrets == Path(DEFAULT_NAME), (
        f"BD_CAPTURE_VAULT={value!r} was treated as opt-in; only '1' may be."
    )


# ── both keys, and the vault moves ───────────────────────────────────────────

def test_both_keys_move_the_vault_and_its_metadata_together(monkeypatch,
                                                            tmp_path):
    """The meta file must follow the vault into the same directory.

    Left behind, the capture would write backend metadata into the operator's
    secrets_meta.json -- so the run would not be isolated after all, and the
    isolation would look complete while leaking through a second file.
    """
    target = tmp_path / "capture-secrets.json"
    secrets, meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=str(target), BD_CAPTURE_VAULT="1")
    assert secrets == target, secrets
    assert meta.parent == target.parent, (
        f"metadata stayed at {meta} while the vault moved to {secrets}; the "
        f"capture would write into the operator's metadata file."
    )
    assert meta != Path(DEFAULT_META_NAME), meta


def test_the_capture_vault_is_never_the_operators_vault(monkeypatch, tmp_path):
    """An override resolving onto the default path is refused.

    Pointing the capture at the real vault would unlock the operator's
    credentials with a throwaway password -- the exact outcome this design
    exists to prevent.
    """
    secrets, _meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=DEFAULT_NAME, BD_CAPTURE_VAULT="1")
    assert secrets == Path(DEFAULT_NAME), secrets


# ── no password may live in the tree ─────────────────────────────────────────

def test_no_capture_password_is_defaulted_in_source():
    """The password is capture.sh's to supply at runtime, never a constant.

    A default would mean every install shipped a known unlock, and unlock()'s
    any-password-on-empty behaviour would make that immediately exploitable on
    a fresh vault.
    """
    src = Path(ss.__file__).read_text(encoding="utf-8")
    for banned in ("12345", "BD_CAPTURE_VAULT_PASSWORD", "capture_password"):
        assert banned not in src, (
            f"{banned!r} appears in secrets_store.py; the capture password "
            f"must never be defaulted in source."
        )


# ── capture.sh wiring ────────────────────────────────────────────────────────
#
# The mechanism above is inert until capture.sh drives it. What follows pins
# the drive, and the teardown half matters more than the setup half: a capture
# that set the drop-in and then died would leave the operator's BD running on
# the capture vault, and their real credentials would appear to have VANISHED.
# That is the failure this file exists to make impossible.

CAPTURE_SH = Path(__file__).resolve().parent.parent / "capture.sh"

DROPIN_DIR = "/etc/systemd/system/bulkdownloader.service.d"
DROPIN_NAME = "20-capture-vault.conf"


def _strip_comments(text: str) -> str:
    """Drop comments so prose can neither satisfy nor trip these gates.

    Quote-aware, matching tests/test_capture_seeds_live_input.py. CLAUDE.md 0
    counts an over-sensitive gate as a soundness bug, and a gate that fires
    when someone edits a comment is exactly that.
    """
    out = []
    for line in text.splitlines():
        cleaned, quote = [], None
        for ch in line:
            if quote:
                cleaned.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                cleaned.append(ch)
                continue
            if ch == "#":
                break
            cleaned.append(ch)
        out.append("".join(cleaned))
    return "\n".join(out)


def _capture_body() -> str:
    return _strip_comments(CAPTURE_SH.read_text(encoding="utf-8"))


def _index(body: str, needle: str) -> int:
    at = body.find(needle)
    assert at != -1, f"capture.sh no longer contains {needle!r}"
    return at


def test_the_dropin_is_written_before_the_service_is_installed():
    """install_service.sh does the daemon-reload and the start.

    A drop-in written after it would not reach the running process, and the
    seeder would meet a service that never saw the capture vault -- refusing
    on the operator's locked vault exactly as before, while the capture
    reported that it had set one up.
    """
    body = _capture_body()
    assert _index(body, DROPIN_NAME) < _index(body, "./install_service.sh"), (
        "the capture-vault drop-in is written after install_service.sh, so the "
        "service starts without it"
    )


def test_the_dropin_is_removed_and_the_service_restarted_afterwards():
    """Removing the file is not enough -- the process keeps its environment.

    systemd passes Environment= at start. Deleting the drop-in leaves the
    RUNNING service still pointed at the capture vault until something
    restarts it, so the operator's BD would keep reporting an empty
    credential store after the capture finished.
    """
    body = _capture_body()
    # Scope to the teardown function itself. Searching the whole file would
    # match the DROPIN path's own assignment line and then find daemon-reload
    # anywhere after it -- a gate that passes on the variable being declared
    # rather than on the file being removed.
    start = body.find("cleanup_capture_vault()")
    assert start != -1, "capture.sh defines no cleanup_capture_vault"
    end = body.find("\n}", start)
    assert end != -1, "cleanup_capture_vault has no closing brace"
    fn = body[start:end]
    assert "rm -f" in fn and "DROPIN" in fn.upper(), (
        f"cleanup_capture_vault never removes the drop-in:\n{fn}"
    )
    assert "daemon-reload" in fn, (
        f"no daemon-reload after removing the drop-in:\n{fn}"
    )
    assert ("restart bulkdownloader" in fn
            or "restart  bulkdownloader" in fn), (
        "the service is never restarted after the capture vault is removed, so "
        "it keeps running with BD_SECRETS_FILE set and the operator's real "
        "credentials stay invisible until the next manual restart"
    )


def test_the_vault_teardown_runs_on_exit_so_an_interrupt_cannot_strand_it():
    """Ctrl-C must not leave the box on the capture vault.

    bash keeps ONE EXIT trap per shell (capture.sh's own note at the
    cleanup_live_seed trap says so), which is why this must be reached through
    the single registered trap rather than by adding a second one.
    """
    body = _capture_body()
    traps = [ln for ln in body.splitlines()
             if ln.strip().startswith("trap ") and " EXIT" in ln]
    assert traps, "capture.sh registers no EXIT trap"
    reached = any("capture_vault" in ln or "cleanup_all" in ln for ln in traps)
    assert reached, (
        f"no EXIT trap reaches the capture-vault teardown; found {traps!r}. A "
        f"second `trap ... EXIT` would silently REPLACE the seed teardown, so "
        f"the vault cleanup must be called from the one that already exists."
    )


def test_the_capture_vault_never_lands_in_the_shared_bundle():
    """capture.sh tars the whole of $OUT and the operator ships it.

    That bundle is routinely handed to third parties -- it is how this whole
    investigation started. Nothing vault-shaped may be written inside it.
    """
    body = _capture_body()
    # The drop-in's value is a VARIABLE, so inspecting that line alone cannot
    # see the path -- the first version of this test did exactly that and a
    # mutation placing the vault in $OUT passed it 21/21. Resolve the chain
    # instead, so the denominator contains the subject.
    assigns: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith(("if", "for", "while")):
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if name.isidentifier():
            assigns.setdefault(name, value.strip().strip('"').strip("'"))

    target = None
    for line in body.splitlines():
        if "BD_SECRETS_FILE" in line and "=" in line:
            target = line.split("BD_SECRETS_FILE=", 1)[1].strip()
    assert target is not None, "capture.sh never sets BD_SECRETS_FILE"

    seen, resolved = set(), target
    for _ in range(8):  # bounded: a cycle must not hang the suite
        ref = resolved.strip("\"'")
        if ref.startswith("$"):
            key = ref[1:].strip("{}").split("/")[0]
            if key in seen or key not in assigns:
                break
            seen.add(key)
            resolved = assigns[key] + ref[len(key) + 1:].replace("}", "")
            continue
        break

    assert "$OUT" not in resolved and "/bd_capture/" not in resolved, (
        f"the capture vault resolves to {resolved!r}, inside the directory "
        f"capture.sh:739 tars into the bundle the operator ships. Resolved "
        f"from {target!r} via {sorted(seen)}."
    )


def test_the_password_is_read_without_echo_and_never_written_down():
    """Prompted, never stored.

    -s keeps it off the terminal. The rest keeps it out of every file the
    capture produces -- a password echoed into $OUT would ship in the bundle.
    """
    body = _capture_body()
    assert "read -s" in body or "read -rs" in body, (
        "the capture-vault password is not read with -s; it would echo to the "
        "terminal and into any recorded session"
    )
    for line in body.splitlines():
        if "CAPTURE_VAULT_PW" in line and (">" in line or ">>" in line):
            raise AssertionError(
                f"the capture-vault password is redirected into a file: "
                f"{line.strip()!r}"
            )


def test_no_password_literal_is_shipped_in_capture_sh():
    """The operator supplies it per run; the tree never carries one."""
    body = _capture_body()
    for banned in ("12345", "fixturepass", "password=admin"):
        assert banned not in body, (
            f"{banned!r} appears in capture.sh; the capture-vault password "
            f"must be prompted, never shipped"
        )


def test_a_non_interactive_run_does_not_hang_waiting_for_a_prompt():
    """capture.sh must still work unattended.

    It is run from a plain shell today, but a prompt with no TTY would block
    forever -- turning an unattended capture into a hang rather than a run
    that simply skips the vault.
    """
    body = _capture_body()
    at = body.find("read -s")
    if at == -1:
        at = body.find("read -rs")
    assert at != -1, "no password prompt found"
    window = body[max(0, at - 900):at]
    assert "-t 0" in window or "tty" in window.lower(), (
        "the password prompt is not guarded by a TTY check, so a "
        "non-interactive capture would hang on it forever"
    )


def test_the_vault_is_unlocked_after_the_service_starts_and_before_seeding():
    """Ordering: install -> unlock -> seed.

    Unlocking before the service is up hits nothing; unlocking after the
    seeder runs is too late, since the seeder refuses on a locked vault.
    """
    body = _capture_body()
    install = _index(body, "./install_service.sh")
    unlock = _index(body, "/api/secrets/unlock")
    seed = _index(body, "tools/live_seed.py --seed")
    assert install < unlock < seed, (
        f"expected install({install}) < unlock({unlock}) < seed({seed})"
    )


def test_the_exit_trap_is_armed_before_the_dropin_is_written():
    """The teardown must be registered BEFORE the thing it tears down exists.

    Found live on test4 2026-07-28, immediately after this feature shipped: the
    drop-in was written at step [4] while the EXIT trap was not armed until the
    seed section 109 lines later. An interrupt anywhere in between -- steps [5]
    and [5a], which include the fixture-site startup and the seeder itself --
    would strand the drop-in, and BD would come back up on the CAPTURE vault
    with the operator's real credentials apparently gone.

    The sibling test above passes in that state, because it only asks whether
    an EXIT trap reaching the vault teardown exists ANYWHERE in the file. A
    trap that exists but is armed too late is not protection, and "the gate
    cannot see the ordering it depends on" is the same defect class this whole
    file is about.
    """
    body = _capture_body()
    dropin_at = _index(body, 'sudo tee "$CAPTURE_VAULT_DROPIN"')
    trap_at = body.find("trap cleanup_all EXIT")
    assert trap_at != -1, "capture.sh no longer arms the aggregate EXIT trap"
    assert trap_at < dropin_at, (
        f"the capture-vault drop-in is written at offset {dropin_at} but the "
        f"EXIT trap is not armed until {trap_at}. Every interrupt in that "
        f"window strands the drop-in, and the operator's BD restarts on the "
        f"capture vault -- their real credentials look like they vanished."
    )
