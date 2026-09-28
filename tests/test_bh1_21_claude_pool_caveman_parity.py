"""bh1-21 (BH-bd-agy-audit-1-016): Claude pool B settings.json lacks the caveman hooks pool A runs.

Pool A (~/.claude/settings.json) registers caveman-proxy on 10 hook events and caveman shrink-hook on
PreToolUse; pool B (~/.claude-b/settings.json) carried caveman-proxy on only 4 of them (rule 54 parity).
The candidate is pool B's settings.json with A's missing caveman blocks inserted and nothing else changed.

The candidate lives outside the repo (harness-cut shape, O1045). Opt in with
BD_BH1_21_CANDIDATE=<dir holding settings-b.json (candidate), settings-b.json.orig (pool B as built
against) and settings-a.json (pool A reference)>. Hermetic: no live settings file is read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH1_21_CANDIDATE", "")
candidate_only = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _commands(block):
    return [h.get("command", "") for h in block.get("hooks", [])]


def caveman_map(settings):
    """{event: sorted caveman hook commands} for every event that registers one."""
    out = {}
    for event, blocks in settings.get("hooks", {}).items():
        cmds = sorted(c for b in blocks for c in _commands(b) if "caveman" in c)
        if cmds:
            out[event] = cmds
    return out


def without_caveman(settings):
    """The settings with every caveman-only hook block removed (everything else untouched)."""
    s = json.loads(json.dumps(settings))
    for event, blocks in s.get("hooks", {}).items():
        s["hooks"][event] = [b for b in blocks if not any("caveman" in c for c in _commands(b))]
    return s


PROXY = "'/x/caveman-proxy' native-hook claude"
OTHER = "/x/bd-turn-hook.sh"


def _settings(events):
    return {"hooks": {e: [{"hooks": [{"type": "command", "command": c}]} for c in cmds] for e, cmds in events.items()}}


def test_caveman_map_sees_a_missing_event_and_ignores_other_hooks():
    a = _settings({"Stop": [OTHER, PROXY], "PreToolUse": [PROXY]})
    b = _settings({"Stop": [OTHER], "PreToolUse": [PROXY]})
    assert caveman_map(a) == {"Stop": [PROXY], "PreToolUse": [PROXY]}
    assert caveman_map(b) == {"PreToolUse": [PROXY]}
    assert caveman_map(a) != caveman_map(b)
    assert caveman_map(_settings({"Stop": [OTHER]})) == {}
    assert without_caveman(a) == without_caveman(b)


def _load(name):
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"bh1-21: named candidate file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@candidate_only
def test_reference_pool_a_registers_caveman_hooks():
    # positive control: the reference is not empty, so an equal map below is not 0 == 0
    ref = caveman_map(_load("settings-a.json"))
    assert len(ref) >= 6, ref


@candidate_only
def test_candidate_pool_b_registers_the_same_caveman_hooks_as_pool_a():
    assert caveman_map(_load("settings-b.json")) == caveman_map(_load("settings-a.json"))


@candidate_only
def test_candidate_changes_nothing_but_caveman_blocks():
    assert without_caveman(_load("settings-b.json")) == without_caveman(_load("settings-b.json.orig"))


@candidate_only
def test_candidate_registers_each_caveman_command_once_per_event():
    for event, cmds in caveman_map(_load("settings-b.json")).items():
        assert len(cmds) == len(set(cmds)), (event, cmds)
