"""capture.sh must request every seeding mode tools/live_seed.py offers.

THE DEFECT. capture.sh invoked the seeder as:

    venv/bin/python tools/live_seed.py --seed --start --start-timeout 180 \
      --login --count 3 $_seed_force

`--vpn-tunnel` is absent, and `--seed` does not imply it -- live_seed.py's
main() branches on `args.vpn_tunnel` separately. So the synthetic VPN tunnel
added for L30 was never created on a capture host, and L30 reported

    WARN  L30  vpn-tunnel-inventory-consistent
              no VPN tunnels configured - nothing to verify

on the box on 2026-07-29. That WARN is truthful. There was nothing to verify,
because nothing ever asked for the thing L30 checks.

WHY NO GATE CAUGHT IT. tests/test_l30_seeded_tunnel_integration.py is 353 lines
across 10 cases and exercises seed_vpn_tunnel() thoroughly: inertness, restart
survival, teardown from both stores, refusal on a broken config, and that an
operator tunnel is left alone. Every one of them calls the seeder directly.
Not one asserts that anything ever calls it. Measured before this file existed,
`grep -- "--vpn-tunnel" tests/ capture.sh` returned nothing at all.

A capability can be built, and gated, and never wired -- and a test suite whose
denominator is the function will certify it the whole time. That is CLAUDE.md
section 0: the gate could not see the thing it was asked about, so it reported
OK.

THE DENOMINATOR HERE IS THE SEEDER'S OWN MODE GUARD. live_seed.py's main()
refuses to run without a mode:

    if not args.seed and not args.teardown and not args.login \
            and not args.vpn_tunnel:
        parser.error("choose --seed, --login, --vpn-tunnel or --teardown")

Those attribute names ARE the set of modes, and they are read from the source by
AST rather than copied here -- a copied list is a second denominator that keeps
passing after someone adds a mode. Adding a seeding mode to the seeder now
requires either wiring it into capture.sh or recording an explicit exclusion,
instead of it silently never running.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"
SEEDER = REPO_ROOT / "tools" / "live_seed.py"

# `--teardown` is the inverse operation, not a seeding mode: capture.sh invokes
# it from cleanup_live_seed on the normal path and from the EXIT trap. It is
# asserted separately below rather than being required in the seeding call.
NOT_A_SEEDING_MODE = {"teardown"}

# Modes deliberately not requested on a capture host. Each entry must carry a
# reason. Empty today -- the point of this file is that adding to it is a
# visible decision rather than an omission nobody sees.
DELIBERATELY_EXCLUDED: dict[str, str] = {}


def _flag(attr: str) -> str:
    return "--" + attr.replace("_", "-")


@pytest.fixture(scope="module")
def seeding_modes() -> list[str]:
    """Mode names from the seeder's own 'choose a mode' guard, via AST."""
    if not SEEDER.is_file():
        pytest.fail(f"{SEEDER} not found; this gate cannot verify its subject")
    tree = ast.parse(SEEDER.read_text(encoding="utf-8"))
    modes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        calls_parser_error = any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "error"
            for sub in ast.walk(node)
        )
        if not calls_parser_error:
            continue
        for sub in ast.walk(node.test):
            if (isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "args"
                    and sub.attr not in modes):
                modes.append(sub.attr)
    return modes


@pytest.fixture(scope="module")
def capture_body() -> str:
    if not CAPTURE_SH.is_file():
        pytest.fail(f"{CAPTURE_SH} not found; this gate cannot verify its subject")
    # Resolve backslash continuations: the seeder invocation spans lines, and a
    # line-at-a-time reader would see only part of its own subject.
    return re.sub(r"\\\n\s*", " ", CAPTURE_SH.read_text(encoding="utf-8"))


def _seeder_invocations(body: str) -> list[str]:
    """Lines that actually RUN the seeder, not lines that mention it.

    The first version of this matched any line containing the seeder's path,
    which swept in four prose comments. That makes the gate permissive in
    exactly the wrong direction: a flag written only in a comment would satisfy
    it while nothing on the box ever ran. The predicate is now the interpreter
    invocation, and comment lines are dropped outright.
    """
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "tools/live_seed.py" not in stripped:
            continue
        # An invocation names the interpreter; a conditional file test does not.
        if "venv/bin/python" not in stripped:
            continue
        out.append(stripped)
    return out


def _seeding_invocation(body: str) -> str:
    calls = [ln for ln in _seeder_invocations(body) if "--teardown" not in ln]
    if not calls:
        pytest.fail(
            "capture.sh contains no non-teardown tools/live_seed.py invocation; "
            "the shape this gate reads has changed and it cannot answer"
        )
    return " ".join(calls)


# ── denominator canaries ─────────────────────────────────────────────────────

def test_the_seeder_declares_modes(seeding_modes):
    """An empty mode set makes every assertion below vacuously true."""
    assert seeding_modes, (
        "no `args.<mode>` names were derived from live_seed.py's mode guard, so "
        "the checks below would pass over an empty set. Either main()'s "
        "'choose a mode' guard moved, or the AST predicate no longer matches it."
    )


def test_capture_sh_invokes_the_seeder(capture_body):
    assert _seeder_invocations(capture_body), (
        "capture.sh never invokes tools/live_seed.py; nothing below is meaningful"
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_capture_requests_every_seeding_mode(capture_body, seeding_modes):
    """A mode that is never requested has never run on a capture host."""
    call = _seeding_invocation(capture_body)
    wanted = [m for m in seeding_modes if m not in NOT_A_SEEDING_MODE]
    missing = [_flag(m) for m in wanted
               if _flag(m) not in call and m not in DELIBERATELY_EXCLUDED]
    assert not missing, (
        f"capture.sh's seeder invocation omits {len(missing)} seeding mode(s): "
        f"{missing}\n\n"
        f"invocation: {call}\n"
        f"modes declared by live_seed.py: {sorted(wanted)}\n\n"
        f"State that BD is not exercised is state no live check can observe. "
        f"Either add the flag, or add the mode to DELIBERATELY_EXCLUDED in this "
        f"file with the reason it must not run on a capture host."
    )


def test_every_exclusion_carries_a_reason():
    """An exclusion list without reasons becomes a place to hide omissions."""
    unexplained = [m for m, why in DELIBERATELY_EXCLUDED.items()
                   if not str(why).strip()]
    assert not unexplained, (
        f"seeding mode(s) excluded with no stated reason: {unexplained}"
    )


def test_excluded_modes_are_real_modes(seeding_modes):
    """A stale exclusion silences a mode that no longer exists by that name."""
    unknown = [m for m in DELIBERATELY_EXCLUDED if m not in seeding_modes]
    assert not unknown, (
        f"DELIBERATELY_EXCLUDED names mode(s) the seeder does not declare: "
        f"{unknown}. A renamed mode would be silently unrequested again."
    )


def test_capture_tears_down_what_it_seeds(capture_body):
    """Seeding without teardown strands synthetic state on the operator's box."""
    assert any("--teardown" in ln for ln in _seeder_invocations(capture_body)), (
        "capture.sh invokes the seeder but never with --teardown, so synthetic "
        "state would be left behind on the box after the capture finishes."
    )
