"""Both provisioning paths must install the same optional capabilities.

@1064, backlog row 96. MEASURED at v3.66.1062: test4 alone had PostgreSQL +
MOD3_PG_TEST_DSN and bd_dev_inspect, so it ran 21 tests the other three hosts
SKIPPED -- 15722 pass / 5 skip against 15701 / 26 in the same capture round.
Nothing in scripts/provision_test_host.sh installed either.

THE GAP WAS NOT "NOBODY WROTE IT". `scripts/cloud-setup.sh` already had both,
as `mod3_pg_provision` and `bd_dev_inspect_provision`. The two provisioning
paths had simply diverged, so which capabilities a host ends up with depends on
WHICH SCRIPT built it -- and a capture on the poorer host goes green by
SKIPPING what is missing, which is section 0's blind gate at fleet scale.

WHY A SHARED LIBRARY RATHER THAN A SECOND COPY. CLAUDE.md section 5 records the
same shape for system packages: "three copies is a denominator that drifts, and
the copy nobody updated is the one the box runs". `scripts/lib/system_deps.sh`
is the fix that worked there and the precedent followed here.

THESE ARE OPTIONAL CAPABILITIES, NOT CORE. A host that cannot install postgres
must WARN -- visibly, in the verdict -- rather than fail provisioning or, worse,
skip 21 tests silently. The provisioner's own header says it: "a WARN is a
capability you do NOT have, never a pass".
"""

from pathlib import Path

import pytest

from shell_source import shell_code_only

_REPO = Path(__file__).resolve().parent.parent

# Its subject is that three provisioning entry points agree with one shared
# library -- an invariant over the tree's scripts, not over any module.
BD_GATE_SCOPE = "repo-wide"
_LIB = _REPO / "scripts" / "lib" / "dev_capabilities.sh"
_CLOUD = _REPO / "scripts" / "cloud-setup.sh"
_HOST = _REPO / "scripts" / "provision_test_host.sh"

_FUNCS = ("bd_mod3_pg_provision", "bd_dev_inspect_provision")


def test_the_shared_library_exists():
    assert _LIB.exists(), (
        f"{_LIB.relative_to(_REPO)} is missing. The capabilities live in "
        f"cloud-setup.sh only, so a host built by provision_test_host.sh "
        f"silently skips 21 tests."
    )


@pytest.fixture(scope="module")
def lib_code():
    if not _LIB.exists():
        pytest.fail("the shared library does not exist yet")
    return shell_code_only(_LIB)


@pytest.mark.parametrize("fn", _FUNCS)
def test_the_library_defines_the_capability(lib_code, fn):
    assert f"{fn}()" in lib_code, (
        f"{fn} is not defined in the shared library"
    )


@pytest.mark.parametrize("script", ["cloud-setup.sh", "provision_test_host.sh"])
def test_both_paths_source_the_library(script):
    code = shell_code_only(_REPO / "scripts" / script)
    assert len(code) > 2000, f"{script} stripped to {len(code)} chars"
    # ASSERT THE SOURCING CONSTRUCT, NOT THE PATH STRING. A mutant replacing the
    # real `. <lib>` with `true` ESCAPED an `in code` check, because the path
    # also appears in the `[ ! -r <lib> ]` readability guard beside it.
    # Mentioning a file is not loading it -- section 0, in this test.
    import re
    assert re.search(r'^\s*(\.|source)\s+.*dev_capabilities\.sh', code, re.M), (
        f"{script} never SOURCES scripts/lib/dev_capabilities.sh (it may only "
        f"mention the path), so the two provisioning paths can drift again -- "
        f"which is the defect, not the missing steps"
    )


@pytest.mark.parametrize("script", ["cloud-setup.sh", "provision_test_host.sh"])
@pytest.mark.parametrize("fn", _FUNCS)
def test_neither_script_redefines_the_capability(script, fn):
    """THE ANTI-DRIFT PROPERTY, and the whole reason this is a library.

    A second definition is not a harmless duplicate: it is the copy nobody
    updates, and it is the one that host runs.
    """
    code = shell_code_only(_REPO / "scripts" / script)
    assert f"{fn}()" not in code, (
        f"{script} defines {fn} itself instead of sourcing it -- a second "
        f"definition is the copy that drifts"
    )


@pytest.mark.parametrize("fn", _FUNCS)
def test_the_host_provisioner_actually_invokes_it(fn):
    """Sourcing is not calling. A library that nothing invokes provisions
    nothing, and the verdict would still read READY."""
    code = shell_code_only(_HOST)
    assert fn in code, (
        f"provision_test_host.sh never invokes {fn}; it would source the "
        f"library and install nothing, and the verdict would still say READY"
    )


def test_the_capabilities_are_optional_not_core():
    """A box that cannot install postgres must WARN, not FAIL.

    The over-sensitivity control: making these `core` would turn a missing
    optional capability into a failed provision, which is the gate-cries-wolf
    defect. It must be visible and non-blocking.
    """
    # JOIN LINE CONTINUATIONS FIRST. A `run_step ... optional \` whose command
    # sits on the next line puts the kind and the function name on DIFFERENT
    # lines, so a per-line predicate fails a CORRECT implementation for its
    # FORM -- CLAUDE.md section 0's shell-construct trap, which is why
    # shell_source exists. This test hit it on its own first run.
    code = shell_code_only(_HOST).replace("\\\n", " ")
    for fn in _FUNCS:
        line = next((l for l in code.splitlines() if fn in l and "run_step" in l), None)
        assert line, (
            f"no run_step invocation found for {fn} -- sourcing a library and "
            f"never calling it provisions nothing while the verdict still reads READY"
        )
        assert " optional " in line, (
            f"{fn} is wired as core, so a host that cannot install it FAILS "
            f"provisioning instead of warning: {line.strip()!r}"
        )


# ── the capability must be visible to a NON-INTERACTIVE capture ────────
#
# Installing postgres without this is a fix that does not work. MEASURED on
# test4 2026-08-12, same box, same commit, only the launch method differing:
# an interactive capture ran 15722 pass / 5 skip; a scripted one ran 15712 / 23.
# The 18 mod3 tests were skipping because MOD3_PG_TEST_DSN was exported only
# from ~/.bashrc, BELOW its `case $- in *i*) ;; *) return;;` guard. The
# capability was present on the box and the gate could not see it.

_CAPTURE = _REPO / "capture.sh"
_ENV_FILE = ".config/bd/mod3.env"


def test_the_provisioner_persists_the_dsn_to_a_file():
    """~/.bashrc is not a delivery mechanism for an automated run."""
    code = shell_code_only(_LIB)
    assert _ENV_FILE in code, (
        f"bd_mod3_pg_provision does not write {_ENV_FILE}, so only an "
        f"INTERACTIVE shell would ever see MOD3_PG_TEST_DSN and a scripted "
        f"capture skips 18 tests while reporting PASS"
    )


def test_capture_sources_the_capability_env():
    """The gate must read it, or persisting it changes nothing."""
    code = shell_code_only(_CAPTURE)
    assert _ENV_FILE in code, (
        f"capture.sh never sources {_ENV_FILE}; the DSN would be written and "
        f"never read, and the mod3 suites keep skipping"
    )
    import re
    assert re.search(r'^\s*(\.|source)\s+.*mod3\.env', code, re.M), (
        "capture.sh mentions the env file but does not SOURCE it -- mentioning "
        "a path is not loading it"
    )


def test_capture_says_so_when_the_capability_is_absent():
    """Section 0: absent must be VISIBLE, never silent.

    The state that caused this row is not 'postgres missing' -- it is 'postgres
    missing and nothing said so'. A capture without the env file must announce
    that the mod3 suites will skip.
    """
    from shell_source import if_blocks_containing
    code = shell_code_only(_CAPTURE)
    assert _ENV_FILE in code, "no capability-env block to inspect"
    # CUT ON STRUCTURE, NOT A FIXED WIDTH. The first version sliced
    # code[i-400:i+700] and test_source_windows_do_not_shift caught it on the
    # band, correctly: a fixed window stops covering its subject the moment
    # anything is added above it, silently. shell_source exists for this.
    blocks = if_blocks_containing(code, _ENV_FILE)
    assert blocks, f"no enclosing if-construct reaches {_ENV_FILE}"
    block = "\n".join(blocks)
    assert "SKIP" in block.upper(), (
        "the absent branch does not announce that the mod3 suites will skip, "
        "so a capture on a host without the capability looks identical to one "
        "with it"
    )


# ── an unattended capture must reach the same checks as an attended one ──
#
# Two capabilities were invisible to a scripted capture for the same reason:
# the mod3 DSN lived below ~/.bashrc's non-interactive guard, and the capture
# vault gated solely on `[ -t 0 ]`. Both left the box HOLDING the capability
# while the gate measuring it could not see it, and both still reported PASS.

def test_the_vault_has_a_non_interactive_path():
    code = shell_code_only(_CAPTURE)
    assert "CAPTURE_VAULT_PW" in code, "no capture-vault password variable at all"
    tty_gate = [l for l in code.splitlines() if "-t 0" in l]
    assert tty_gate, "no TTY test found -- has the vault block moved?"
    # The env branch must be reachable WITHOUT a TTY, i.e. it must be tested
    # before (or independently of) the `[ -t 0 ]` arm.
    i_env = code.find('"${CAPTURE_VAULT_PW:-}"')
    i_tty = code.find("-t 0")
    assert i_env != -1, (
        "capture.sh has no CAPTURE_VAULT_PW branch, so an ssh/nohup/systemd "
        "capture always skips L6/L8 while an attended one runs them -- two "
        "different denominators on the same host"
    )
    assert i_env < i_tty, (
        "the CAPTURE_VAULT_PW branch is tested AFTER the TTY branch, so an "
        "unattended run still cannot reach it"
    )


def test_no_credential_literal_is_committed():
    """Section 7: a file that names a credential becomes a place it lives.

    The unattended path takes the password from the environment. Defaulting it
    to a literal here would put a credential in the repo and in every PR range
    gitleaks scans -- and the value being trivial does not change where it now
    lives.
    """
    import re
    from shell_source import shell_code_only as _code_only
    # COMMENT-STRIPPED, AND MATCHING AN ASSIGNMENT -- NOT A MENTION. The first
    # version of this regex matched the message string "no CAPTURE_VAULT_PW:
    # capture vault skipped", i.e. its own prose, and failed a correct file.
    # Section 0: an assertion that cannot tell prose from code.
    code = _code_only(_CAPTURE)
    bad = re.findall(r'CAPTURE_VAULT_PW=[^\s"\')]+|CAPTURE_VAULT_PW:-[^}"\s]+', code)
    assert not bad, (
        f"capture.sh appears to hardcode a vault password default: {bad[:2]}. "
        f"Take it from the environment; do not write one down -- the value "
        f"being a dummy does not change the fact that the repo now carries it."
    )


# ── the DONE path must persist too ──────────────────────────────────────
#
# THIS TEST EXISTS BECAUSE A DEFECT SHIPPED. bd_mod3_pg_provision's early
# return -- "a server already answering the DSN is the DONE state" -- returned
# BEFORE writing the env file, so on exactly the hosts where postgres already
# worked the DSN was never persisted and a scripted capture still skipped 18
# tests. Measured on test4 @1064: mod3_exit=0 with env_file=ABSENT. An exit
# code is not evidence that the side effect happened.

def test_the_already_serving_path_still_persists_the_dsn():
    from shell_source import shell_code_only as _co
    code = _co(_LIB)
    lines = code.splitlines()
    start = next(i for i, l in enumerate(lines) if "bd_mod3_pg_provision()" in l)
    early = next(i for i in range(start, len(lines))
                 if "already serving the DSN" in lines[i])
    # Walk back to the top of that if-branch and forward to its return.
    branch = "\n".join(lines[start:early + 1])
    assert "bd_mod3_env_persist" in branch, (
        "the already-serving early return does not persist the DSN, so a host "
        "whose postgres already works never gets ~/.config/bd/mod3.env and a "
        "scripted capture keeps skipping the mod3 suites while exiting 0"
    )


def test_persisting_is_one_function_not_two_copies():
    """One writer, so the two paths cannot disagree about the file."""
    from shell_source import shell_code_only as _co
    code = _co(_LIB)
    assert code.count("bd_mod3_env_persist(){") == 1, "persist helper defined twice"
    assert code.count("mod3.env") == 1, (
        "the env-file path is written in more than one place -- a second copy "
        "is the one that drifts"
    )


def _vault_block() -> str:
    """The vault construct, cut on STRUCTURE: assignment to its closing `fi`."""
    lines = _CAPTURE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("CAPTURE_VAULT=0"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip() == "fi")
    return "\n".join(lines[start:end + 1]) + "\n"


def test_an_inherited_vault_password_is_honoured_when_executed(tmp_path):
    """RUN IT. THIS TEST EXISTS BECAUSE A DEFECT SHIPPED PAST SOURCE READING.

    @1065 added the unattended branch and every assertion here read capture.sh's
    TEXT: the branch existed, it was ordered before the TTY arm, no literal was
    committed. All true, and the feature did not work -- a bare
    `CAPTURE_VAULT_PW=""` ran ABOVE it and wiped the inherited value. Measured on
    test5: the run printed "no TTY and no CAPTURE_VAULT_PW" with the variable set
    in its own environment.

    Initialising a variable and honouring an inherited one are different
    operations, and only execution can tell them apart.
    """
    import os
    import subprocess
    block = _vault_block()
    # Precondition: the harness built the shape (section 6).
    assert "CAPTURE_VAULT_PW" in block and block.count("fi") >= 1, block[:200]
    syn = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
    assert syn.returncode == 0, syn.stderr

    real_lock = Path("/tmp/bd_capture_vault.lock")
    before = ((real_lock.stat().st_dev, real_lock.stat().st_ino,
               real_lock.stat().st_mtime_ns) if real_lock.exists() else None)
    env = {k: v for k, v in os.environ.items() if k != "CAPTURE_VAULT_PW"}
    env["CAPTURE_VAULT_GLOBAL_LOCK"] = str(tmp_path / "vault-global.lock")
    on = subprocess.run(["bash", "-s"], input=block, text=True, capture_output=True,
                        timeout=60,
                        env={**env, "CAPTURE_VAULT_PW": "unit-test-value"})
    assert "ENABLED" in on.stdout, (
        f"an inherited CAPTURE_VAULT_PW did not enable the vault -- something "
        f"above the branch clobbers it. Output: {on.stdout.strip()[:200]}"
    )

    off = subprocess.run(["bash", "-s"], input=block, text=True, capture_output=True,
                         timeout=60, env=env)
    assert "ENABLED" not in off.stdout, (
        f"the vault enabled itself with no password set -- the default must be "
        f"unchanged. Output: {off.stdout.strip()[:200]}"
    )
    assert "skip" in off.stdout.lower(), off.stdout.strip()[:200]
    after = ((real_lock.stat().st_dev, real_lock.stat().st_ino,
              real_lock.stat().st_mtime_ns) if real_lock.exists() else None)
    assert after == before, "executed unit block touched the real /tmp lock"
