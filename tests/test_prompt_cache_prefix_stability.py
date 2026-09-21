"""Row 961 -- prompt-cache prefix lock and volatile-tail isolation.

O586: 98% of fleet tokens are per-turn context re-reads. A provider can only reuse a KV block if the
prompt PREFIX is byte-identical; substituting a seat name into the role body makes every seat of a
role a cache miss. Row 864 froze the prefix in bd-launch-role.sh; this gate holds the same invariant
for the other two launchers.

Hermetic: fixture role prompts, fixture codex agent TOML, fixture output dirs, mock say. No seat is
launched -- the launchers are called with --emit-prompt, which assembles the prompt and exits.

TWO THINGS THIS GATE LEARNED FROM ITS OWN REFUTE (bd-cx-lens-1, 2026-09-21T14:26Z):

E1 -- EXECUTE THE ROLE YOU NAME. The first version hardcoded the role argument `worker` in emit()
while the real-prompt tests iterated five role prompt FILES, so every emission assembled the worker
body and the other four roles were never run. It then compared the emitted length against a
DIFFERENT role's file length, which is not a check that the right body was selected. A seat-name
leak that fires only for a non-worker role left all seven tests green. Now the role is passed
through, and each emission is pinned to its OWN body by content: a distinctive line of that role's
prompt must be in the prefix, and a distinctive line of another role's prompt must not.

E2 -- HOST PATHS ARE AN OPT-IN, NEVER A DEFAULT. The first version defaulted to untracked host
paths (harness-work/FIX/<slug>/, bd-persist/harness, bd-persist/role-prompts). On a host without
them `bash <missing>` returned rc 127 and the gate FAILED where it had measured nothing. The rule
here, in all three cases: unset -> SKIP and say what to set; set but missing -> FAIL, because a
supplied path that is not there is a broken run and not an absent dependency. test_optin_contract
below holds that rule itself and needs no host path at all, so this module is never vacuous.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

# SYN-72 / O1045 / O1066b: harness CANDIDATES are never staged into the product repo. They live in
# harness-work/FIX/<slug>/; only this gate rides the cut. There is deliberately NO default: see E2.
FIX_HOME_VAR = "BD_ROW961_FIX"
LAUNCHER_FILES = {"agy": "bd-launch-agy.sh", "codex": "bd-launch-codex-role.sh"}
# BD_TEST_AGY / BD_TEST_CODEX point the gate at a pre-row-961 build; the negative control
# in prove.sh uses them to show this gate can return the other answer (R-NEG, O46).
LAUNCHER_OVERRIDE = {"agy": "BD_TEST_AGY", "codex": "BD_TEST_CODEX"}
HARNESS_VAR = "BD_ROW961_HARNESS"          # the live bd-persist/harness dir, for the census
PROMPTS_VAR = "BD_ROW961_ROLE_PROMPTS"     # the live bd-persist/role-prompts dir
TAIL_MARKER = "# ---- VOLATILE TAIL: SEAT IDENTITY & DYNAMIC POINTERS (ROW 864) ----"
# 4000 tokens of English is ~16000 characters; acceptance 1 is stated in tokens, measured in bytes.
# THIS THRESHOLD IS A PROPERTY OF THE FIXTURE, NOT OF THE FLEET. _body(400) is deliberately long
# enough to cover it; the real role prompts are not. Measured over the whole of role-prompts/ at
# 2026-09-21: 100 files, min 305 / median 1733 / max 5020 bytes, and 0 of 100 reach 16000. The
# emitted prefixes for eight sampled roles ran 1040-2826 bytes (~260-706 tokens). So the length
# assertion below proves the launcher does not TRUNCATE a long body; it does not, and cannot,
# prove the shipped fleet prompt reaches the first 4000 tokens. test_real_role_prompts_* measures
# the shipped ones and asserts the invariant that is actually deliverable there: stability.
PREFIX_BYTES_FOR_4000_TOKENS = 16000
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
ROLES_SAMPLED = 5
AGY_FAMILY = "flash"   # the family argument emit() passes to bd-launch-agy.sh


def optional_path(var: str) -> Path | None:
    """E2: unset -> None (caller skips); set but missing -> FileNotFoundError (the run is broken)."""
    raw = os.environ.get(var)
    if raw is None or raw.strip() == "":
        return None
    p = Path(raw)
    if not p.exists():
        raise FileNotFoundError(
            f"{var}={raw} was supplied but does not exist; a supplied path that is not there is a "
            f"broken run, not an absent dependency")
    return p


def required_dir(var: str, what: str) -> Path:
    p = optional_path(var)
    if p is None:
        pytest.skip(f"{var} is not set: this check needs {what}; set {var} to opt in")
    if not p.is_dir():
        pytest.fail(f"{var}={p} is not a directory")
    return p


def launcher(kind: str) -> Path:
    """The launcher candidate under test, by explicit opt-in only."""
    override = optional_path(LAUNCHER_OVERRIDE[kind])
    if override is not None:
        return override
    home = required_dir(FIX_HOME_VAR, f"the row-961 launcher candidates ({LAUNCHER_FILES[kind]})")
    p = home / LAUNCHER_FILES[kind]
    if not p.is_file():
        pytest.fail(f"{FIX_HOME_VAR}={home} was supplied but carries no {LAUNCHER_FILES[kind]}")
    return p


def _body(n: int = 400, role: str = "<role>") -> str:
    """A role prompt long enough that its prefix covers the first 4000 tokens."""
    line = ("Standing rule: read the slice, never the file; a probe that cannot say no is not a probe. "
            "Your handoff is handoff-NAME.md and your compact file is compact-NAME.md.\n")
    return f"YOU ARE bd-{role}. You build ONE cut.\n" + line * n


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    prompts = tmp_path / "role-prompts"; prompts.mkdir()
    (prompts / "worker.prompt").write_text(_body())
    out = tmp_path / "role-systemprompts"; out.mkdir()
    say = tmp_path / "say.sh"; say.write_text("#!/bin/bash\nexit 0\n"); say.chmod(0o755)
    codex_home = tmp_path / "codex"; (codex_home / "agents").mkdir(parents=True)
    (codex_home / "agents" / "bd-worker.toml").write_text(
        'developer_instructions = """\n' + _body() + '"""\n')
    (codex_home / "bd-worker.config.toml").write_text('model = "gpt-5"\n')
    e = dict(os.environ)
    e.update(
        BD_ROLE_PROMPT_DIR=str(prompts),
        BD_ROLE_SPROMPT_DIR=str(out),
        BD_AGY_WORKDIR=str(tmp_path / "wd"),
        CODEX_HOME=str(codex_home),
        BD_CX_SAY=str(say),
        BD_LAUNCH_ALLOW_DUP="1",
    )
    return e


def emit(script: Path, seat: str, env: dict, role: str = "worker") -> str:
    """Assemble one seat's system prompt via --emit-prompt; returns its text.

    E1: the ROLE is an argument. Hardcoding it here is what let a non-worker defect escape.
    """
    if script.name.startswith("bd-launch-agy"):
        cmd = ["bash", str(script), role, seat, "flash", "--emit-prompt"]
    else:
        cmd = ["bash", str(script), role, seat, "--emit-prompt"]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{script.name} --emit-prompt role={role} rc={r.returncode}: {r.stderr[-800:]}"
    path = Path(r.stdout.strip().splitlines()[-1])
    assert path.is_file(), f"{script.name} did not print a written prompt path: {r.stdout!r}"
    return path.read_text()


def split(text: str) -> tuple[str, str]:
    assert TAIL_MARKER in text, "no volatile-tail marker: prefix and tail are not separated"
    head, tail = text.split(TAIL_MARKER, 1)
    return head, tail


def _lines(prompt: Path) -> set[str]:
    return {l.strip() for l in prompt.read_text(errors="replace").splitlines() if len(l.strip()) > 40}


def resolve_prompt(prompts: Path, role: str, family: str = AGY_FAMILY) -> Path | None:
    """The prompt file bd-launch-agy.sh WILL select, in its own documented search order.

    The gate has to mirror the launcher's resolution rather than assume role.prompt: for role
    `adjudicator` at family `flash` the launcher picks agy-adjudicator.prompt, so demanding
    adjudicator.prompt's content back would fail a launcher that is behaving exactly as specified.
    Measured on the live prompt dir: `adjudicator` and `agy-adjudicator` both resolve to
    agy-adjudicator.prompt (there is no agy-agy-adjudicator.prompt), and they do emit the same
    prefix -- which is correct, and is why distinctness below is asserted per RESOLVED FILE.
    """
    for cand in (prompts / f"agy-{role}-{family}.prompt", prompts / f"agy-{role}.prompt",
                 prompts / f"{role}-{family}.prompt", prompts / f"{role}.prompt"):
        if cand.is_file():
            return cand
    return None


def unique_line(prompt: Path, others: list[Path]) -> str | None:
    """The longest line of THIS prompt file that appears in none of `others`.

    The plain longest line will not do: the role prompts share a common floor, and advisor-b's
    longest line is also inside adjudicator's. A shared line proves nothing about which body was
    assembled, in either direction. Only a line unique within the resolved set discriminates.
    """
    mine = _lines(prompt)
    for other in others:
        mine -= _lines(other)
    return max(mine, key=len) if mine else None


def test_optin_contract_is_skip_when_absent_and_fail_when_supplied_missing(tmp_path, monkeypatch):
    """E2, held on the rule itself: this needs no host path, so the module is never vacuous."""
    monkeypatch.delenv(FIX_HOME_VAR, raising=False)
    assert optional_path(FIX_HOME_VAR) is None, "unset must read as absent, not as a default path"
    monkeypatch.setenv(FIX_HOME_VAR, str(tmp_path / "definitely-not-here"))
    with pytest.raises(FileNotFoundError):
        optional_path(FIX_HOME_VAR)
    monkeypatch.setenv(FIX_HOME_VAR, str(tmp_path))
    assert optional_path(FIX_HOME_VAR) == tmp_path, "a supplied, existing path must be used"


@pytest.mark.parametrize("kind", ["agy", "codex"])
def test_prefix_is_byte_identical_across_seats_of_one_role(kind, env):
    """Acceptance 1: the frozen prefix is byte-for-byte the same for two seats of the same role."""
    script = launcher(kind)
    a = split(emit(script, "bd-seat-alpha", env))[0]
    b = split(emit(script, "bd-seat-bravo", env))[0]
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest(), (
        f"{script.name}: prefix differs between seats -- every seat of this role is a cache miss")
    assert len(a.encode()) >= PREFIX_BYTES_FOR_4000_TOKENS, (
        f"{script.name}: frozen prefix is only {len(a.encode())} bytes, short of the first "
        f"{PREFIX_BYTES_FOR_4000_TOKENS} bytes (~4000 tokens) the acceptance names")


@pytest.mark.parametrize("kind", ["agy", "codex"])
def test_volatile_data_is_confined_to_the_tail(kind, env):
    """Acceptance 3: seat identity and dynamic pointers appear only after the tail marker."""
    script = launcher(kind)
    seat = "bd-seat-charlie"
    head, tail = split(emit(script, seat, env))
    assert seat not in head, f"{script.name}: seat name leaked into the frozen prefix"
    assert seat in tail, f"{script.name}: seat name is missing from the tail -- seat is not identified"
    assert not TIMESTAMP_RE.search(head), f"{script.name}: a timestamp leaked into the frozen prefix"


def test_every_launcher_that_writes_a_system_prompt_carries_the_tail_marker():
    """Denominator from the filesystem, not from a handed list (FLEET_RULE 8)."""
    harness = required_dir(HARNESS_VAR, "the live launcher directory to count writers in")
    home = required_dir(FIX_HOME_VAR, "the row-961 launcher candidates")
    writers = sorted(p for p in harness.glob("bd-launch-*.sh")
                     if "role-systemprompts" in p.read_text(errors="replace"))
    assert writers, "positive control failed: no launcher writes role-systemprompts at all"
    cut_names = set(LAUNCHER_FILES.values())
    missing = []
    for p in writers:
        text = (home / p.name).read_text(errors="replace") if p.name in cut_names else p.read_text(errors="replace")
        if TAIL_MARKER not in text:
            missing.append(p.name)
    assert not missing, f"{len(missing)}/{len(writers)} launchers have no frozen-prefix marker: {missing}"


def _sampled_roles() -> tuple[Path, list[str]]:
    prompts = required_dir(PROMPTS_VAR, "the shipped role prompts to census")
    real = sorted(prompts.glob("*.prompt"))
    assert real, f"positive control failed: {prompts} carries no *.prompt to measure"
    return prompts, [p.stem for p in real[:ROLES_SAMPLED]]


def test_real_role_prompts_emit_a_stable_prefix(tmp_path, env):
    """Census over the real role-prompts directory (read-only) -- the prompts the fleet ships.

    E1: each role is EXECUTED AS ITSELF. The refuted version passed the literal role `worker` for
    all five, so a seat-name leak in any other role's body could not be seen. It can be seen here.
    """
    script = launcher("agy")
    prompts, roles = _sampled_roles()
    env = dict(env, BD_ROLE_PROMPT_DIR=str(prompts))
    for i, role in enumerate(roles):
        a = split(emit(script, f"bd-{role}-alpha", dict(env, BD_AGY_WORKDIR=str(tmp_path / f"a{i}")), role))[0]
        b = split(emit(script, f"bd-{role}-bravo", dict(env, BD_AGY_WORKDIR=str(tmp_path / f"b{i}")), role))[0]
        assert a == b, f"{role}: real role prompt does not emit a byte-identical prefix"
        assert f"bd-{role}-alpha" not in a, f"{role}: seat name leaked into the frozen prefix"
    assert len(roles) == min(ROLES_SAMPLED, len(roles))


def test_real_role_prompt_prefix_carries_the_body_the_launcher_selected(tmp_path, env):
    """The emission must carry THE BODY IT RESOLVED TO, not merely a body of the right length.

    Stability alone can be satisfied by a prefix that has collapsed to a constant, and a length
    comparison can be satisfied by another role's body -- which is exactly how the refuted version
    passed while emitting `worker` for all five roles. Content decides, against the file the
    launcher's own search order picks, and two roles resolving to DIFFERENT files must not emit
    the same prefix.
    """
    script = launcher("agy")
    prompts, roles = _sampled_roles()
    env = dict(env, BD_ROLE_PROMPT_DIR=str(prompts))
    resolved = {r: resolve_prompt(prompts, r) for r in roles}
    files = sorted({f for f in resolved.values() if f is not None})
    fingerprint = {f: unique_line(f, [g for g in files if g != f]) for f in files}
    wrong_body, short, checked, heads = [], [], 0, {}
    for i, role in enumerate(roles):
        mine = resolved[role]
        if mine is None or fingerprint[mine] is None:
            continue
        other = next((f for f in files if f != mine and fingerprint[f]), None)
        if other is None:
            continue
        head = split(emit(script, f"bd-{role}-probe",
                          dict(env, BD_AGY_WORKDIR=str(tmp_path / f"p{i}")), role))[0]
        heads[role] = head
        # CONTENT IS CHECKED IN THE FROZEN PREFIX, NOT ANYWHERE IN THE EMISSION (row961 E3,
        # bd-cx-worker-3). Matching across head+tail passes a launcher that MOVES the role body
        # after the tail marker, which destroys the very prefix this row exists to freeze -- a body
        # in the volatile tail is re-sent uncached every turn. An earlier draft matched the whole
        # text because adjudicator.prompt's unique line was in neither half; that was the ROLE
        # RESOLUTION bug, not a relocation, and resolve_prompt closed it: all five sampled roles
        # carry their RESOLVED file's unique line in the HEAD. prove.sh pins the other direction
        # with a launcher that emits the body after the marker.
        if fingerprint[mine] not in head:
            wrong_body.append((role, f"the line unique to {mine.name} is absent from its FROZEN PREFIX"))
        if fingerprint[other] in head:
            wrong_body.append((role, f"carries {other.name}'s unique line instead of {mine.name}'s"))
        if len(head.encode()) < len(mine.read_bytes()):
            short.append((role, len(head.encode()), len(mine.read_bytes())))
        checked += 1
    assert checked, "positive control failed: no resolved prompt had a unique line; nothing measured"
    assert not wrong_body, f"launcher assembled the wrong role body: {wrong_body}"
    assert not short, f"emitted prefix is shorter than the prompt it resolved to: {short}"
    # A prefix that had collapsed to one constant would satisfy every stability check in this file
    # and be worthless. Two roles share a prefix ONLY if they resolve to the same prompt file.
    collapsed = [(x, y) for x in heads for y in heads
                 if x < y and heads[x] == heads[y] and resolved[x] != resolved[y]]
    assert not collapsed, f"roles with different resolved prompts emit an identical prefix: {collapsed}"
