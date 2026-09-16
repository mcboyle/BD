"""Row 717: a fresh host cannot obtain the cut-quality checker from any tracked doc.

The policy pins an active_checker by sha256 and resolves it through an environment
variable, but docs/repo/FRESH_HOST_BRINGUP.md never named either, so the only way a
new box learned of them was a fleet patch -- which protects only the hosts that were
up when it ran. These assertions read the tracked policy and the tracked tool rather
than a copied constant, so the doc cannot silently fall out of step with them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Binds a tracked document against the tracked policy and the tracked launcher;
# its subject is repository-level bring-up state, not a single product module.
BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "repo" / "FRESH_HOST_BRINGUP.md"
POLICY = REPO / "toolchain" / "cut_quality_policy.json"
TOOL = REPO / "toolchain" / "bin" / "bd_cut_quality.py"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def test_active_checker_schema_and_sha256_are_documented(doc_text, policy):
    active = policy["active_checker"]
    assert active["schema"] in doc_text, (
        f"FRESH_HOST_BRINGUP.md does not name the active_checker schema "
        f"{active['schema']!r} pinned by {POLICY.name}"
    )
    assert active["sha256"] in doc_text, (
        f"FRESH_HOST_BRINGUP.md does not carry the active_checker sha256 "
        f"{active['sha256']} -- a fresh host cannot verify the file it obtains"
    )


def test_every_env_var_the_tool_reads_is_documented(doc_text):
    read_by_tool = set(
        re.findall(r"BD_CUT_QUALITY[A-Z_]*", TOOL.read_text(encoding="utf-8"))
    )
    assert read_by_tool, "probe found no BD_CUT_QUALITY* names in the tool at all"
    undocumented = sorted(name for name in read_by_tool if name not in doc_text)
    assert not undocumented, (
        f"{TOOL.name} reads these but FRESH_HOST_BRINGUP.md never names them: "
        f"{undocumented}"
    )


def _stanza_naming(doc_text: str, needle: str) -> str:
    """The '## ' section containing `needle`.

    Selected by splitting on headings rather than by arithmetic around str.index,
    because a fixed source window must be a literal slice: tools/build_source_window_hashes.py
    hashes the window without executing the test, so a slice over a runtime variable
    fails closed at regen (BOUNCE-717-source-window-not-fixed-intB-2308Z.md).
    """
    for stanza in doc_text.split("\n## "):
        if needle in stanza:
            return stanza
    return ""


def test_the_matrix_variable_is_documented_as_real_not_nonexistent(doc_text):
    """Row 717 was filed when BD_CUT_QUALITY_MATRIX was absent from the candidate.

    It is real on this base, so the row's "document if real, otherwise mark
    nonexistent" branch resolves to documenting it; this pins which branch was taken
    so a later edit cannot quietly downgrade it back to a nonexistence note.
    """
    assert "BD_CUT_QUALITY_MATRIX" in TOOL.read_text(encoding="utf-8")
    assert "BD_CUT_QUALITY_MATRIX" in doc_text
    stanza = _stanza_naming(doc_text, "BD_CUT_QUALITY_MATRIX").lower()
    assert "nonexistent" not in stanza, (
        "BD_CUT_QUALITY_MATRIX is read by the tool, so its section must not call it "
        "nonexistent"
    )
    assert "real and required" in stanza, (
        "the section must state positively that BD_CUT_QUALITY_MATRIX is real; the "
        "absence of the word 'nonexistent' alone would also pass on an empty stanza"
    )


def test_the_doc_states_where_a_fresh_host_obtains_the_checker(doc_text, policy):
    """The row's actual complaint: no tracked document says where the file comes from."""
    sha = policy["active_checker"]["sha256"]
    stanza = _stanza_naming(doc_text, sha)
    assert stanza, f"no FRESH_HOST_BRINGUP.md section names the active_checker sha256 {sha}"
    assert "BD_CUT_QUALITY_VALIDATOR" in stanza, (
        "the sha256 and the variable that resolves it must be documented together"
    )
