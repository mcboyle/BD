"""Two agent-facing docs must not both assert the environment.

`CLAUDE.md` and `CODEX_HANDOFF.md` are read by different agents working the same
tree. Whenever both stated an environment fact, they were free to disagree --
and they did: while CLAUDE.md said the interpreter is `venv/bin/python`,
CODEX_HANDOFF.md issued 14 commands against `.venv/bin/python`, which exits 127
here. A session followed the wrong one and reported seven failures that did not
exist.

The fix is not to keep them in sync by hand. Hand-synced duplicates are the
`bd-guardcheck` defect: one copy gets repaired, the other keeps shipping, and
nothing compares them. So CODEX_HANDOFF.md states NO environment facts at all
and points at CLAUDE.md instead.

This gate holds that line. It is deliberately narrow: the handoff may freely
describe tasks, decisions, evidence and history. What it may not do is make a
claim about the interpreter, the deploy mechanism, or the repository location
that could drift out of step with the contract.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HANDOFF = REPO_ROOT / "CODEX_HANDOFF.md"
CONTRACT = REPO_ROOT / "CLAUDE.md"

# Facts CLAUDE.md owns. Each is (label, pattern, why it must not be restated).
OWNED = (
    (
        "the interpreter",
        r"\.venv/bin/python",
        "CLAUDE.md section 5 owns the interpreter. `.venv` does not exist here; a "
        "command naming it exits 127 and the caller silently falls back to a 3.11 "
        "without project dependencies.",
    ),
    (
        "the repository location",
        r"/root/BulkDownloader-main",
        "the checkout path belongs to whoever is running, not to a document.",
    ),
    (
        "the deploy mechanism",
        r"unzip\s+-o",
        "CLAUDE.md section 7 owns the deploy model. The box deploys via git.",
    ),
    (
        "index-preservation policy",
        r"[Dd]o not reset, checkout",
        "this protected a git index that no longer exists, in a repository whose "
        "deploy is `git reset --hard`. It is now actively wrong.",
    ),
)


def _executable_lines(text: str) -> list[tuple[int, str]]:
    """Lines that could be pasted and run: fenced code, or a bullet command.

    Prose that NAMES a dead path while explaining that it is dead is fine and
    necessary -- the handoff has to say what was removed and why. Three gates
    written earlier in this session first failed by grepping whole files and
    flagging their own explanatory comments. The subject here is a runnable
    instruction, so the denominator is code, not narrative.
    """
    out: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append((lineno, line))
    return out


@pytest.mark.parametrize("label,pattern,why", OWNED, ids=[o[0] for o in OWNED])
def test_the_handoff_does_not_restate_an_owned_fact(label: str, pattern: str, why: str):
    text = HANDOFF.read_text(encoding="utf-8")
    offenders = [
        f"{lineno}: {line.strip()[:100]}"
        for lineno, line in _executable_lines(text)
        if re.search(pattern, line)
    ]
    assert not offenders, (
        f"CODEX_HANDOFF.md issues a runnable command asserting {label}:\n  "
        + "\n  ".join(offenders)
        + f"\n\n{why}\n\nState it in CLAUDE.md and point here, or the two "
          f"documents drift and the next agent follows whichever it read first."
    )


def test_the_handoff_says_which_document_wins():
    """A pointer only helps if it establishes precedence.

    Without it, an agent finding a conflict has no rule for resolving it and
    will reasonably prefer the more specific-looking document -- which is the
    stale one.
    """
    text = HANDOFF.read_text(encoding="utf-8")
    assert "CLAUDE.md" in text, "CODEX_HANDOFF.md never mentions CLAUDE.md"
    # `` ` `` is optional: the doc writes CLAUDE.md in backticks, so a pattern
    # demanding whitespace immediately after the name matches nothing. The
    # subject is "does it name a winner", not "does it use one exact phrasing".
    precedence = re.search(
        r"`?CLAUDE\.md`?\s+(is\s+)?(authoritative|wins)", text, re.I
    )
    assert precedence, (
        "CODEX_HANDOFF.md mentions CLAUDE.md but does not say it takes "
        "precedence. Name the winner explicitly; an agent that finds a conflict "
        "needs a rule, not a cross-reference."
    )


def test_the_contract_still_owns_what_the_handoff_defers_on():
    """The deferral is only safe while CLAUDE.md actually carries the facts.

    If someone trims CLAUDE.md, this handoff silently points at nothing --
    a pointer to a fact that no longer exists reads exactly like a fact.
    """
    contract = CONTRACT.read_text(encoding="utf-8")
    missing = []
    if "venv/bin/python" not in contract:
        missing.append("the interpreter (venv/bin/python)")
    if not re.search(r"git\s+reset\s+--hard", contract):
        missing.append("the git deploy model")
    assert not missing, (
        "CLAUDE.md no longer states: " + ", ".join(missing) + ".\n"
        "CODEX_HANDOFF.md defers to it for exactly these, so trimming them "
        "leaves both documents silent and the next agent guessing."
    )
