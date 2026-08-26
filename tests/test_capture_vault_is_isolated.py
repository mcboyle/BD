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

import fcntl
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "repo-wide"


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

    # BOTH FORMS, AND THE HYPHEN IS LOAD-BEARING. Since v3.66.1099 the capture
    # directory is /tmp/bd_capture-<runid>/, so `/bd_capture/` alone would be
    # VACUOUS -- that substring now appears in no path, and the clause would
    # pass for a reason unrelated to the vault (CLAUDE.md section 0, a gate
    # reporting OK over a subject whose shape moved underneath it).
    #
    # But a bare `/bd_capture` prefix over-corrects: the vault itself lives at
    # /tmp/bd_capture_vault/, which starts with it, so that form fails on the
    # CORRECT arrangement. Measured -- it did, on first run. `/bd_capture-`
    # matches the run-keyed output directory and not the vault.
    assert ("$OUT" not in resolved
            and "/bd_capture/" not in resolved
            and "/bd_capture-" not in resolved), (
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


# ── the restart must leave a SERVING app behind ──────────────────────────────
#
# `systemctl restart` returns once systemd has STARTED the unit, not once the
# app has bound its port. waitress needs roughly three more seconds: a boot
# journal shows "Started bulkdownloader.service" at 19:00:17 and "[waitress]
# Serving on http://0.0.0.0:5555" at 19:00:20. Steps [7] and [9] curl :5555
# immediately after cleanup_capture_vault returns, so with no wait they meet a
# refused connection -- observed on a real capture as
#   curl: (7) Failed to connect to localhost port 5555 after 0 ms
# on BOTH steps ("after 0 ms" is a refusal, not a timeout), turning an
# otherwise-clean run into dev-tools exit=1; http-smoke exit=1 and
# CAPTURE VERDICT: FAIL.
#
# Two of these gates RUN the teardown rather than reading it. A text gate
# cannot tell a bounded wait from `while true`, and boundedness is the property
# that keeps a failed restart from becoming a hang -- which would be strictly
# worse than the failure it replaced.

REPO_ROOT = CAPTURE_SH.parent
TRAP_ANCHOR = "trap cleanup_all EXIT"
PROBE_MARK = "__READINESS_WAIT_STARTS_HERE__"


def _functions(code: str) -> dict[str, str]:
    """Brace-form shell function bodies, keyed by name.

    run_graph_hash_gate is invisible here on purpose: it is declared with
    PARENTHESES (a subshell function), and it is no part of this teardown.
    """
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r"^(\w+)\(\)\s*\{\n(.*?)^\}", code,
                                 re.M | re.S)
    }


def _teardown_tail(code: str) -> str:
    """Everything cleanup_capture_vault does from the restart onwards."""
    body = _functions(code).get("cleanup_capture_vault")
    assert body is not None, "capture.sh defines no cleanup_capture_vault"
    at = body.find("restart bulkdownloader")
    assert at != -1, (
        f"cleanup_capture_vault no longer restarts the service; this gate's "
        f"anchor is stale:\n{body}"
    )
    return body[at:]


def _expand(text: str, code: str) -> str:
    """Substitute capture.sh's top-level assignments into `text`.

    The probe URL is a VARIABLE, so reading the polling code literally cannot
    see the port it polls -- the same trap
    test_the_capture_vault_never_lands_in_the_shared_bundle hit, and it cost
    that gate a mutation it should have caught. Resolve the chain instead, so
    the denominator contains the subject. Bounded: a self-referential
    assignment must not hang the suite.
    """
    assigns = {
        match.group(1): match.group(2).strip().strip("\"'")
        for match in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", code, re.M)
    }
    for _ in range(4):
        grown = re.sub(
            r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
            lambda m: assigns.get(m.group(1), m.group(0)),
            text,
        )
        if grown == text:
            break
        text = grown
    return text


def _polls_the_app(text: str) -> bool:
    return bool(
        ("CAPTURE_READY_URL" in text or "CAPTURE_APP_ORIGIN" in text)
        and "curl" in text
        and re.search(r"\b(while|until)\b", text)
    )


def _readiness_wait(code: str) -> tuple[str, str]:
    """(name to invoke, the resolved text that must do the polling).

    Resolves ONE level of call, the way the EXIT-trap gate above does: the wait
    may sit inline in the teardown or be factored into a helper the teardown
    calls. Both are legitimate, and a gate that accepted only one would fail on
    a refactor while the property it pins still held (CLAUDE.md 0 counts that
    over-sensitivity as a soundness bug).
    """
    tail = _teardown_tail(code)
    if _polls_the_app(_expand(tail, code)):
        return "cleanup_capture_vault", _expand(tail, code)
    for name, body in _functions(code).items():
        if name == "cleanup_capture_vault":
            continue
        if re.search(rf"^\s*{re.escape(name)}\b", tail, re.M) \
                and _polls_the_app(_expand(body, code)):
            return name, _expand(body, code)
    raise AssertionError(
        "cleanup_capture_vault restarts bulkdownloader and returns without "
        "waiting for the app to be SERVING again. `systemctl restart` returns "
        "as soon as systemd has started the unit; waitress needs ~3s more to "
        "bind :5555. Steps [7] and [9] curl that port immediately afterwards "
        "and got `curl: (7) ... after 0 ms` on a real capture -- dev-tools "
        "exit=1, http-smoke exit=1, CAPTURE VERDICT: FAIL.\n"
        f"teardown from the restart onwards:\n{tail}"
    )


def _stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _probe_script(tmp_path: Path, call: str, vault: str) -> Path:
    """capture.sh's real head, cut at the trap, plus a driver.

    The head is the actual script -- assignments, the TTY-guarded prompt (which
    skips itself with stdin closed), the teardown definitions and the trap.
    ``_run_probe`` redirects its executable state into ``tmp_path``; running a
    hand-written copy of the function would only test the copy.
    """
    raw = CAPTURE_SH.read_text(encoding="utf-8")
    at = raw.find(TRAP_ANCHOR)
    assert at != -1, "capture.sh no longer arms the aggregate EXIT trap"
    head = raw[: raw.index("\n", at) + 1]
    probe = tmp_path / "probe.sh"
    probe.write_text(
        head
        + "\n".join(
            [
                "trap - EXIT",
                f'CAPTURE_VAULT="{vault}"',
                f'CAPTURE_VAULT_DIR="{tmp_path / "vault"}"',
                'CAPTURE_VAULT_FILE="$CAPTURE_VAULT_DIR/secrets.json"',
                f'CAPTURE_VAULT_DROPIN="{tmp_path / "dropin.conf"}"',
                # Mark BOTH streams, so a gate asking what the wait said reads
                # only what the wait said. Without this the head's own banner
                # ("...L6/L8 will WARN as before") sits in the denominator, and
                # a mutation that silenced the timeout entirely passed 28/28 --
                # the gate matched a string from a line it was never about.
                f'echo "{PROBE_MARK}"',
                f'echo "{PROBE_MARK}" >&2',
                call,
                'echo "probe_rc=$?"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    lib_src = CAPTURE_SH.parent / "scripts" / "lib"
    lib_dst = tmp_path / "scripts" / "lib"
    lib_dst.mkdir(parents=True, exist_ok=True)
    staged = 0
    for lib in sorted(lib_src.glob("*.sh")):
        shutil.copy2(lib, lib_dst / lib.name)
        staged += 1
    assert staged, (
        f"no capture shell libraries were staged from {lib_src}; the probe "
        "would skip its real helper behavior"
    )
    return probe


def _run_probe(tmp_path: Path, *, call: str, vault: str = "1",
               curl_exit: int = 7, timeout: int = 45,
               lock_override: Path | None = None,
               expected_returncode: int = 0):
    """Run `call` out of capture.sh's own head with the world stubbed out.

    sleep is a no-op stub, so a bounded wait finishes instantly while an
    unbounded one hangs and is reported as such. That does pin the shape: a
    wall-clock-deadline loop would also hang here. Deliberate -- a capture step
    whose length is decided by the clock rather than by counted attempts cannot
    be read back off the log, and this suite must not take 30 real seconds to
    ask one question.
    """
    stub_bin = tmp_path / "bin"
    lock_dir = tmp_path / "capture-lock"
    gc_guard_log = tmp_path / "gc-guard.log"
    stub_bin.mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    if lock_override is None:
        lock_dir.mkdir(mode=0o700)
        lock_path = lock_dir / "capture-vault.lock"
    else:
        lock_path = lock_override
    log = tmp_path / "calls.log"
    _stub(stub_bin / "curl",
          f'#!/usr/bin/env bash\necho "curl" >> "{log}"\nexit {curl_exit}\n')
    _stub(stub_bin / "sleep",
          f'#!/usr/bin/env bash\necho "sleep ${{1:-0}}" >> "{log}"\nexit 0\n')
    _stub(stub_bin / "sudo",
          f'#!/usr/bin/env bash\necho "sudo $*" >> "{log}"\nexit 0\n')
    _stub(stub_bin / "systemctl",
          f'#!/usr/bin/env bash\necho "systemctl $*" >> "{log}"\nexit 0\n')
    _stub(
        tmp_path / "venv" / "bin" / "python",
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >> "${CAPTURE_GC_GUARD_LOG:?}"\n'
        'exit 0\n',
    )

    probe = _probe_script(tmp_path, call, vault)
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    env["CAPTURE_KEEP"] = "999999999"
    env["CAPTURE_GC_GUARD_LOG"] = str(gc_guard_log)
    env["CAPTURE_VAULT_GLOBAL_LOCK"] = str(lock_path)
    try:
        completed = subprocess.run(
            ["bash", str(probe)],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"{call} did not return within {timeout}s against a service that "
            f"never answers, with sleep stubbed to a no-op. The readiness wait "
            f"is unbounded: it converts a failed restart into a hung capture, "
            f"which is worse than the failed steps it was meant to prevent."
        ) from exc
    assert completed.returncode == expected_returncode, (
        f"capture probe exited {completed.returncode}, expected "
        f"{expected_returncode}:\n{completed.stdout}{completed.stderr}"
    )
    assert gc_guard_log.is_file(), (
        "the probe did not route bd_test_root_gc through its inert sandbox "
        f"interpreter:\n{completed.stdout}{completed.stderr}"
    )
    assert "toolchain/bin/bd-gc --apply --older-than 1440 --only classified" in (
        gc_guard_log.read_text(encoding="utf-8")
    )
    assert "pruned " not in completed.stdout + completed.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    return completed, calls


def _said_by_the_wait(completed) -> str:
    """Only the output produced from the call onwards, on both streams."""
    spoken = []
    for stream in (completed.stdout, completed.stderr):
        assert PROBE_MARK in stream, (
            f"the probe marker never reached one of the streams, so this gate "
            f"cannot tell the wait's output from capture.sh's own. Unknown is "
            f"a third state and it fails.\n{stream}"
        )
        spoken.append(stream.split(PROBE_MARK, 1)[1])
    return "".join(spoken)


def test_the_teardown_waits_for_the_app_to_serve_before_steps_7_to_9():
    """The restart must be followed by a readiness wait, not by nothing."""
    name, text = _readiness_wait(_capture_body())
    assert name and text


def test_the_readiness_probe_uses_the_origin_the_guarded_steps_use():
    """Probing a different origin certifies a socket the steps never reach.

    Every instance has a distinct origin. A wait that retained the old fixed
    origin would certify a peer while the guarded probes failed against this
    run -- a gate whose denominator is not its subject.
    """
    body = _capture_body()
    assert '"$CAPTURE_APP_ORIGIN/api/dev/$route"' in body, (
        "capture.sh no longer routes /api/dev/* through its selected origin"
    )
    _name, text = _readiness_wait(body)
    assert "CAPTURE_APP_ORIGIN" in text or "CAPTURE_READY_URL" in text, (
        "steps [7]/[9] use CAPTURE_APP_ORIGIN but the readiness wait does not; it "
        f"would certify an origin those steps never use.\n{text}"
    )


def test_the_readiness_wait_is_bounded_and_says_so_when_it_times_out(tmp_path):
    """Bounded, and never silent about giving up.

    An unbounded wait turns a failed restart into a hang. A bounded wait that
    returned quietly would be worse than none at all: the capture would carry
    on into steps [7]-[9] reporting nothing about an app it never saw serving.
    Unknown is a third state, and it fails (CLAUDE.md 0).
    """
    name, _text = _readiness_wait(_capture_body())
    completed, calls = _run_probe(tmp_path, call=name, curl_exit=7)

    attempts = [line for line in calls if line.startswith("curl")]
    sleeps = [float(line.split()[1]) for line in calls
              if line.startswith("sleep")]
    assert len(attempts) >= 2, (
        f"the readiness wait made {len(attempts)} probe(s) against a service "
        f"that never answered; it is not polling. calls={calls}"
    )
    budget = sum(sleeps)
    assert 0 < budget <= 120, (
        f"the readiness wait's total sleep budget is {budget}s over "
        f"{len(attempts)} attempts. Zero means it never waits between probes; "
        f"more than 120s means a stalled restart holds the capture far longer "
        f"than the ~3s waitress actually needs."
    )
    spoken = _said_by_the_wait(completed)
    assert re.search(r"(?i)warn|timed out|timeout|did not|no answer|never|"
                     r"not ready|not known", spoken), (
        f"the readiness wait gave up silently after {len(attempts)} attempts. "
        f"Steps [7]-[9] then run against an app nothing has seen serving, and "
        f"the operator gets `curl: (7)` with no explanation.\n"
        f"--- everything the wait said ---\n{spoken!r}"
    )


def test_the_readiness_wait_returns_as_soon_as_the_app_answers(tmp_path):
    """A fixed `sleep 5` would pass the gate above and waste the difference."""
    name, _text = _readiness_wait(_capture_body())
    completed, calls = _run_probe(tmp_path, call=name, curl_exit=0)

    attempts = [line for line in calls if line.startswith("curl")]
    sleeps = [line for line in calls if line.startswith("sleep")]
    assert len(attempts) == 1, (
        f"an app answering on the first probe was asked {len(attempts)} "
        f"times. calls={calls}"
    )
    assert not sleeps, (
        f"the readiness wait slept even though the app answered immediately: "
        f"{sleeps}. That is a fixed delay wearing a poll's clothes.\n"
        f"{completed.stdout}"
    )


def test_the_readiness_probe_owns_its_singleton_inside_a_parent_capture(
    tmp_path,
):
    """A synthetic capture must not share its parent's singleton.

    capture.sh holds one process-wide lock for the live service, drop-in and
    ports.  This probe executes the real script head inside pytest; when pytest
    itself is a capture stage, the unisolated probe refuses at exit 73 before
    reaching the readiness subject. Hold a real outer lock, pass that path to
    a refusal control, then prove the normal helper creates its owner-only lock
    below ``tmp_path``.
    """
    outer_dir = tmp_path / "outer-capture"
    outer_dir.mkdir(mode=0o700)
    outer_lock = outer_dir / "capture-vault.lock"
    outer_lock.touch(mode=0o600)
    outer_lock.chmod(0o600)

    with outer_lock.open("r+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contention = subprocess.run(
            ["flock", "-n", str(outer_lock), "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert contention.returncode == 1, (
            "the outer lock was not actually held, so this test did not "
            "reproduce a probe nested inside a live capture"
        )
        name, _text = _readiness_wait(_capture_body())
        inherited = tmp_path / "inherited"
        inherited.mkdir()
        refused, _calls = _run_probe(
            inherited,
            call=name,
            curl_exit=0,
            lock_override=outer_lock,
            expected_returncode=73,
        )
        assert refused.returncode == 73, (
            "the control probe did not inherit and refuse the live capture's "
            f"contended singleton:\n{refused.stdout}{refused.stderr}"
        )
        assert "another capture owns the singleton" in refused.stderr
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        completed, calls = _run_probe(isolated, call=name, curl_exit=0)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "command not found" not in completed.stderr
    assert calls == ["curl"], calls
    scratch_lock = isolated / "capture-lock" / "capture-vault.lock"
    assert scratch_lock.is_file(), (
        f"the nested probe did not create its singleton under tmp_path: "
        f"{scratch_lock}"
    )
    assert scratch_lock.stat().st_mode & 0o777 == 0o600
    holder_pid = scratch_lock.read_text(encoding="utf-8").split()[0]
    assert holder_pid.isdecimal(), scratch_lock.read_text(encoding="utf-8")


def test_a_capture_that_never_enabled_the_vault_waits_for_nothing(tmp_path):
    """No password, or no TTY, means no drop-in, no restart -- and no delay.

    cleanup_capture_vault is a no-op in that case, so a readiness wait reached
    from anywhere other than inside its restart branch would add up to the full
    ceiling to every unattended capture, for a service it never touched.
    """
    completed, calls = _run_probe(
        tmp_path, call="cleanup_capture_vault", vault="0", curl_exit=7)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert calls == [], (
        f"cleanup_capture_vault did work with CAPTURE_VAULT=0: {calls}. "
        f"Nothing was restarted, so nothing may be waited on."
    )


def test_a_readiness_timeout_reaches_the_verdict():
    """Saying it on stderr is not enough; the verdict has to see it.

    capture.sh deliberately never aborts mid-run (`don't set -e: we want all 9
    steps to run even if one errors`), so the only way an unknown can fail is
    by reaching tools/capture_verdict.py as a stage exit. A run whose service
    never came back must not be able to print PASS.
    """
    body = _capture_body()
    _name, text = _readiness_wait(body)
    assigned = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", text, re.M))
    verdict_at = body.find("tools/capture_verdict.py")
    assert verdict_at != -1, "capture.sh no longer calls capture_verdict.py"
    stages = re.findall(r'--stage-exit\s+"([^"]+)"', body[verdict_at:])
    referenced = {
        name
        for stage in stages
        for name in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", stage)
    }
    assert assigned & referenced, (
        f"the readiness wait sets {sorted(assigned)} but the verdict only "
        f"reads {sorted(referenced)}. A timeout would warn on stderr and the "
        f"capture could still print CAPTURE VERDICT: PASS -- the one place the "
        f"operator actually reads."
    )
