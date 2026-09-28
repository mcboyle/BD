"""tests/test_eff_p2_floors.py -- Regression tests for PREFIX-BATCH-1 (eff-p2).

Covers:
- PROPOSAL-bd-agy-trainer-1-001: FLEET_RULE-FLOOR.md compression + Tier A verbatim compliance.
- PROPOSAL-bd-agy-trainer-1-003: _COMMON.txt dead directive purge. CLAUDE-FLOOR.md is carried byte-for-byte
  from live: its compression dropped binding clauses (eff-p2 G5 REFUTE, bd-worker-A1-A).
- CO-CUT CACHE-011: Integration with bd-floor-check.py (Tier A 0 stale, 0 foreign; SHA match).
- RULING-EFF-P2-REFUTE (bd-pm-B, O1481/O1466): rule 56 verbatim; no floor rule id collides with SOURCE.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_DIR = os.environ.get("BD_EFF_P2_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE_DIR, reason="candidate opt-in required")


def _get_path(name: str) -> Path:
    p = Path(CANDIDATE_DIR)
    if p.is_dir():
        target = p / name
    else:
        target = p.parent / name
    assert target.exists(), f"Candidate file {target} does not exist"
    return target


def test_candidates_exist_and_sizes() -> None:
    """Candidates exist and meet compression size ceilings."""
    fleet_floor = _get_path("FLEET_RULE-FLOOR.md").read_bytes()
    claude_floor = _get_path("CLAUDE-FLOOR.md").read_bytes()
    common_txt = _get_path("_COMMON.txt").read_bytes()

    assert len(fleet_floor) > 0
    assert len(claude_floor) > 0
    assert len(common_txt) > 0

    # Compression checks
    assert len(fleet_floor) <= 9000, (
        f"FLEET_RULE-FLOOR.md must be <=9000 B, got {len(fleet_floor)}"
    )
    assert len(common_txt) <= 1800, (
        f"_COMMON.txt must be <=1800 B, got {len(common_txt)}"
    )


def test_fleet_floor_tier_a_verbatim_compliance() -> None:
    """Tier A numbered rules must match FLEET_RULE.md character-for-character."""
    floor_text = _get_path("FLEET_RULE-FLOOR.md").read_text(encoding="utf-8")
    src_path = Path("/home/mboyle/bd-persist/FLEET_RULE.md")
    assert src_path.exists(), "SOURCE FLEET_RULE.md missing"
    src_text = src_path.read_text(encoding="utf-8")
    src_stripped = {ln.strip() for ln in src_text.splitlines()}

    # Extract lines in Tier A
    assert "## TIER A" in floor_text
    tier_a_lines: list[str] = []
    in_tier_a = False
    for line in floor_text.splitlines():
        if line.startswith("## TIER A"):
            in_tier_a = True
            continue
        if in_tier_a and line.startswith("## "):
            break
        if in_tier_a:
            tier_a_lines.append(line)

    head_re = re.compile(r"^\s?(\d+[a-z]?)\.\s")
    tier_a_ids: list[str] = []
    for line in tier_a_lines:
        m = head_re.match(line)
        if m:
            tier_a_ids.append(m.group(1))
            assert line.strip() in src_stripped, (
                f"Tier A rule {m.group(1)} line not verbatim from source: {line!r}"
            )

    # O1489: rule 21 is retired; Tier A carries SOURCE's retirement line in its place.
    expected_ids = {"22", "26", "15", "16", "1", "6", "7", "24", "27"}
    assert set(tier_a_ids) == expected_ids, (
        f"Tier A must contain exactly {expected_ids}, got {set(tier_a_ids)}"
    )
    retired = [ln for ln in tier_a_lines if ln.startswith("(21 RETIRED")]
    assert len(retired) == 1 and retired[0].strip() in src_stripped, (
        f"Tier A must carry SOURCE's rule-21 retirement line verbatim, got {retired}"
    )


def test_fleet_floor_sha256_header_matches_source() -> None:
    """Header SHA256 must match current FLEET_RULE.md hash."""
    floor_text = _get_path("FLEET_RULE-FLOOR.md").read_text(encoding="utf-8")
    src_bytes = Path("/home/mboyle/bd-persist/FLEET_RULE.md").read_bytes()
    expected_sha = hashlib.sha256(src_bytes).hexdigest()

    m = re.search(r"^SHA256:\s*([0-9a-f]{64})$", floor_text, re.MULTILINE)
    assert m, "Missing valid SHA256 header in FLEET_RULE-FLOOR.md"
    assert m.group(1) == expected_sha, (
        f"SHA256 header {m.group(1)} does not match source sha {expected_sha}"
    )


SOURCE = Path("/home/mboyle/bd-persist/FLEET_RULE.md")
# A rule is DEFINED by a line of one of these shapes; group 1 = id, group 2 = the text that follows it.
_RULE_DEF = re.compile(
    r"^(?: ?(\d+[a-z]?)\. (.*)|## Rule (\d+): (.*)|\* \(RULE (\d+)\) (.*)|Rule (\d+) \((.*?)\))"
)


def _rule_defs(text: str) -> dict[str, list[str]]:
    """id -> texts; a definition's text includes its indented continuation lines."""
    defs: dict[str, list[str]] = {}
    last: list[str] | None = None
    for line in text.splitlines():
        m = _RULE_DEF.match(line)
        if m:
            rid, body = [g for g in m.groups() if g is not None]
            defs.setdefault(rid, []).append(body)
            last = defs[rid]
        elif last is not None and line.startswith("    ") and line.strip():
            last[-1] += " " + line.strip()
        else:
            last = None
    return defs


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4}


def test_fleet_floor_rule56_operator_authority_verbatim() -> None:
    """RULING-EFF-P2-REFUTE: a compression cut never removes a floor rule; rule 56 is carried byte-for-byte."""
    floor_lines = (
        _get_path("FLEET_RULE-FLOOR.md").read_text(encoding="utf-8").splitlines()
    )
    src56 = [
        ln
        for ln in SOURCE.read_text(encoding="utf-8").splitlines()
        if ln.startswith("56. ")
    ]
    assert len(src56) == 1, f"SOURCE must define rule 56 once, found {len(src56)}"
    assert src56[0] in floor_lines, (
        "rule 56 (Operator Authority) missing or not verbatim from SOURCE"
    )


def test_fleet_floor_every_rule_id_exists_in_source_with_matching_title() -> None:
    """No rule number in the floor may name a different rule than SOURCE (O1466: 58-60, 62-83 retired).

    A floor line is a verbatim copy (Tier A), a compressed handle (Tier B) or an index entry; either way its id must
    be a SOURCE id and its text must share >= 2 keywords with SOURCE's text for that id.
    """
    src = _rule_defs(SOURCE.read_text(encoding="utf-8"))
    floor = _rule_defs(_get_path("FLEET_RULE-FLOOR.md").read_text(encoding="utf-8"))
    assert len(floor) >= 20, (
        f"probe control: only {len(floor)} rule ids parsed from the floor"
    )
    bad = []
    for rid, bodies in floor.items():
        for body in bodies:
            if rid not in src:
                bad.append(f"{rid}: not in SOURCE ({body[:40]!r})")
            elif not any(len(_keywords(body) & _keywords(s)) >= 2 for s in src[rid]):
                bad.append(f"{rid}: {body[:40]!r} != SOURCE {src[rid][0][:40]!r}")
    assert not bad, "floor rule ids collide with SOURCE:\n" + "\n".join(bad)


def test_fleet_floor_no_larger_than_live_floor() -> None:
    """A compression cut may not grow the prefix it compresses."""
    live = Path("/home/mboyle/bd-persist/FLEET_RULE-FLOOR.md")
    cand = _get_path("FLEET_RULE-FLOOR.md")
    assert cand.stat().st_size <= live.stat().st_size, (
        f"candidate {cand.stat().st_size} B > live {live.stat().st_size} B"
    )


def test_claude_floor_sections_and_invariants() -> None:
    """CLAUDE-FLOOR.md must preserve all A1-A8 sections and core invariant commands."""
    claude_text = _get_path("CLAUDE-FLOOR.md").read_text(encoding="utf-8")
    for sec in [
        "A1 IDENTITY",
        "A2 AUTHORITY",
        "A3 LIFECYCLE",
        "A4 GIT",
        "A5 VERIFY",
        "A6 RELEASE",
        "A7 INVARIANTS",
        "A8 TOOLS",
    ]:
        assert sec in claude_text, f"Missing section {sec} in CLAUDE-FLOOR.md"

    # Critical tokens preserved
    assert "--timeout=240" in claude_text
    assert "bd-mutate" in claude_text
    assert "IMPROVEMENT_BACKLOG.md" in claude_text
    assert "deploy.sh" in claude_text


def test_claude_floor_is_live_floor_byte_for_byte() -> None:
    """No CLAUDE-FLOOR.md clause is lost: the candidate is the live floor, not a compression of it."""
    live = Path("/home/mboyle/bd-persist/CLAUDE-FLOOR.md").read_bytes()
    assert b"A1 IDENTITY" in live, "probe control: live CLAUDE-FLOOR.md unreadable"
    assert _get_path("CLAUDE-FLOOR.md").read_bytes() == live, (
        "candidate CLAUDE-FLOOR.md differs from live; its compression dropped binding clauses"
    )


def test_common_txt_purges_obsolete_and_cites_o1212() -> None:
    """_COMMON.txt must purge obsolete directives, cite O1212 (not retired rule 21) and bd-say routing."""
    common_text = _get_path("_COMMON.txt").read_text(encoding="utf-8")
    assert "test2 is HANDS OFF" not in common_text, (
        "Obsolete 'test2 is HANDS OFF' must be purged"
    )
    assert "bd-fable-b" not in common_text, "Obsolete 'bd-fable-b' must be purged"
    # O1489: FLEET_RULE.md carries no rule 21; a "Rule 21" cite resolves to a wrong neighbour.
    assert "Rule 21" not in common_text, "_COMMON.txt cites retired rule 21 (O1489)"
    assert "O1212" in common_text, "_COMMON.txt must cite O1212 for host access"
    assert "bd-sentinel-A" in common_text
    assert "bd-pm-A" in common_text
