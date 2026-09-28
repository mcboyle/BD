"""BH2-18 + findings/HIGH-AGY-HOOKS-UNPARSED-bd-worker-B2-B.md: AGY never loaded harness/agy-hooks.json.

AGY's hooks.json is a map of NAMED hooks ({"<name>": {"PreToolUse": [...]}}); the harness file had a top-level
"PreToolUse" array, which AGY rejects whole ("cannot unmarshal array into Go struct field .PreToolUse of type
jsonhook.JSONHookSpec", every AGY seat's cli log), so no fleet guard ever ran on an AGY seat, and
bd-verdict-write-hook.sh was not registered for AGY at all. RULING-BH2-15-18 / RULING-AGY-HOOKS (bd-pm-C-B): convert
to the named form and register the verdict hook on AGY's PostToolUse, whose payload (measured, live agy -p probe)
is {"toolCall": {"name", "args": {"TargetFile", ...}}, "stepIdx", "error", ...} and whose stdout must be {}.

The harness is deployed from bd-persist, not this repo: BD_BH2_18_CANDIDATE_DIR is the absolute path of the
candidate directory (agy-hooks.json, gemini-config-hooks.json, bd-verdict-write-hook.sh). Hermetic: every hook run
uses tmp log/bus/say/reaper seams and tmp verdict files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_DIR = os.environ.get("BD_BH2_18_CANDIDATE_DIR", "")
pytestmark = pytest.mark.skipif(not CANDIDATE_DIR, reason="candidate opt-in required")

LIVE_HOOK = "/home/mboyle/bd-persist/harness/bd-verdict-write-hook.sh"
EVENTS = {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"}
GOOD_VERDICT = (
    "VERDICT: BOARD\nINDEX-TREE: " + "a" * 40 + "\nPATCH-SHA256: " + "b" * 64 + "\n"
)
AGY_WRITE_TOOLS = (
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
)


def _cand(name: str) -> Path:
    p = Path(CANDIDATE_DIR) / name
    assert p.is_file(), f"candidate missing: {p}"
    return p


def agy_schema_errors(doc: object) -> list[str]:
    """The AGY hooks.json schema (builtin skills/agy-customizations/docs/hooks.md, "File Format")."""
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    errs = []
    for name, spec in doc.items():
        if not isinstance(spec, dict):
            errs.append(f"hook {name!r}: spec is {type(spec).__name__}, not an object")
            continue
        for ev, groups in spec.items():
            if ev == "enabled":
                continue
            if ev not in EVENTS or not isinstance(groups, list):
                errs.append(f"hook {name!r}: bad event {ev!r}")
                continue
            for g in groups:
                if ev in ("PreToolUse", "PostToolUse"):
                    hs = g.get("hooks") if isinstance(g, dict) else None
                    if (
                        not isinstance(g.get("matcher"), str)
                        or not isinstance(hs, list)
                        or not hs
                    ):
                        errs.append(f"hook {name!r} {ev}: group lacks matcher/hooks")
                        continue
                    if any(
                        not isinstance(h, dict) or not isinstance(h.get("command"), str)
                        for h in hs
                    ):
                        errs.append(f"hook {name!r} {ev}: handler lacks command")
    return errs


def _commands(doc: dict, event: str) -> list[tuple[str, str]]:
    return [
        (g["matcher"], h["command"])
        for spec in doc.values()
        for g in spec.get(event, [])
        for h in g["hooks"]
    ]


def test_schema_positive_control_plugin_file_agy_loads() -> None:
    # Measured: AGY loads this file (its PostToolUse hook ran in cli-20260928_185238.log).
    doc = json.loads(
        Path("/home/mboyle/bd-persist/plugins/bd-cut-hygiene/hooks.json").read_text()
    )
    assert agy_schema_errors(doc) == []


def test_schema_negative_control_array_form_is_rejected() -> None:
    assert agy_schema_errors(
        {"PreToolUse": [{"matcher": "x", "hooks": [{"command": "c"}]}]}
    )


@pytest.mark.parametrize("name", ["agy-hooks.json", "gemini-config-hooks.json"])
def test_candidate_hooks_json_is_agy_named_hook_form(name: str) -> None:
    errs = agy_schema_errors(json.loads(_cand(name).read_text()))
    assert errs == [], f"AGY_HOOKS_UNPARSED: {name}: {errs}"


def test_candidate_keeps_every_live_guard() -> None:
    orig = json.loads(_cand("agy-hooks.json.orig").read_text())
    live_pre = [
        (g["matcher"], h["command"]) for g in orig["PreToolUse"] for h in g["hooks"]
    ]
    assert (
        len(live_pre) == 5
    )  # fixture has data: 4 write/run guards + output-bound on view_file
    new = json.loads(_cand("agy-hooks.json").read_text())
    assert agy_schema_errors(new) == [], "AGY_HOOKS_UNPARSED: no named hooks to compare"
    assert _commands(new, "PreToolUse") == live_pre
    g_orig = json.loads(_cand("gemini-config-hooks.json.orig").read_text())[
        "PreToolUse"
    ]
    g_new = json.loads(_cand("gemini-config-hooks.json").read_text())
    assert [(h["matcher"], h["command"]) for h in g_orig] == _commands(
        g_new, "PreToolUse"
    )


def _post_command() -> str:
    doc = json.loads(_cand("agy-hooks.json").read_text())
    posts = _commands(doc, "PostToolUse")
    hits = [(m, c) for m, c in posts if LIVE_HOOK in c]
    assert len(hits) == 1, (
        f"AGY_VERDICT_HOOK_UNREGISTERED: PostToolUse commands {posts}"
    )
    m, c = hits[0]
    for tool in AGY_WRITE_TOOLS:
        assert re.fullmatch(m, tool) or re.search(m, tool), (
            f"matcher {m!r} misses {tool}"
        )
    assert not re.fullmatch(m, "view_file") and not re.fullmatch(m, "run_command")
    return c.replace(LIVE_HOOK, str(_cand("bd-verdict-write-hook.sh")))


def _run(cmd: list[str], event: dict, tmp: Path) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BD_SEAT", "BD_VERDICT_WORKTREE")
    }
    env.update(
        LC_ALL="C",
        BD_VERDICT_LINT_LOG=str(tmp / "lint.log"),
        BD_BUS_DB_PATH=str(tmp / "bus.db"),
        BD_SAY_CMD="/bin/true",
        BD_SCRATCH_REAPER_SH="/bin/true",
        BD_SEAT="bd-worker-test-bh2-18",
    )
    return subprocess.run(
        cmd,
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def _agy_event(path: Path) -> dict:
    return {
        "conversationId": "c",
        "stepIdx": 2,
        "error": "",
        "toolCall": {
            "name": "write_to_file",
            "args": {"TargetFile": str(path), "CodeContent": "x"},
        },
    }


def test_agy_post_write_of_bad_verdict_warns_and_prints_empty_json(
    tmp_path: Path,
) -> None:
    v = tmp_path / "cut" / ".review" / "VERDICT-correctness-x.md"
    v.parent.mkdir(parents=True)
    v.write_text("VERDICT: REFUTED\nbody\n")
    res = _run(["sh", "-c", _post_command()], _agy_event(v), tmp_path)
    assert res.returncode == 0, res
    assert json.loads(res.stdout) == {}, (
        f"AGY PostToolUse stdout must be {{}}: {res.stdout!r}"
    )
    log = (
        (tmp_path / "lint.log").read_text() if (tmp_path / "lint.log").exists() else ""
    )
    assert "VERDICTLINT-WARN" in log and str(v) in log, (
        f"AGY_VERDICT_HOOK_BLIND: an AGY verdict write was not linted: log={log!r} stderr={res.stderr!r}"
    )


def test_agy_hook_reads_toolcall_targetfile(tmp_path: Path) -> None:
    v = tmp_path / "cut" / ".review" / "VERDICT-shape-y.md"
    v.parent.mkdir(parents=True)
    v.write_text("VERDICT: REFUTED\n")
    res = _run(
        ["bash", str(_cand("bd-verdict-write-hook.sh"))], _agy_event(v), tmp_path
    )
    assert res.returncode == 0
    assert "verdict-write WARN" in res.stdout and str(v) in res.stdout, (
        f"AGY_VERDICT_HOOK_BLIND: {res.stdout!r}"
    )


def test_control_claude_shape_still_warns(tmp_path: Path) -> None:
    v = tmp_path / "cut" / ".review" / "VERDICT-correctness-z.md"
    v.parent.mkdir(parents=True)
    v.write_text("VERDICT: REFUTED\n")
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(v)}}
    res = _run(["bash", str(_cand("bd-verdict-write-hook.sh"))], ev, tmp_path)
    assert res.returncode == 0 and "verdict-write WARN" in res.stdout


def test_control_valid_verdict_and_non_verdict_are_silent(tmp_path: Path) -> None:
    good = tmp_path / "cut" / ".review" / "VERDICT-correctness-ok.md"
    good.parent.mkdir(parents=True)
    good.write_text(GOOD_VERDICT)
    other = tmp_path / "notes.md"
    other.write_text("VERDICT: REFUTED\n")
    hook = ["bash", str(_cand("bd-verdict-write-hook.sh"))]
    for p in (good, other):
        for ev in (
            _agy_event(p),
            {"tool_name": "Write", "tool_input": {"file_path": str(p)}},
        ):
            res = _run(hook, ev, tmp_path)
            assert res.returncode == 0 and "verdict-write WARN" not in res.stdout, (
                res.stdout
            )
    assert not (tmp_path / "lint.log").exists()


def test_agy_post_command_on_valid_verdict_prints_only_empty_json(
    tmp_path: Path,
) -> None:
    good = tmp_path / "cut" / ".review" / "VERDICT-correctness-ok.md"
    good.parent.mkdir(parents=True)
    good.write_text(GOOD_VERDICT)
    res = _run(["sh", "-c", _post_command()], _agy_event(good), tmp_path)
    assert res.returncode == 0 and json.loads(res.stdout) == {}
    assert "verdict-write WARN" not in res.stderr, res.stderr
