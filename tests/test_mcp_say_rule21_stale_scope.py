"""P2 mcp-say-rule21-stale: bd-mcp say() still refused any text naming test2 as "operator-only (FLEET_RULE 21)".

FLEET_RULE 21 was retired in full by the operator (O1212), so test2 / 10.0.70.95 / bd-capture-test2 are ordinary
words again. The sender-identity guard on say() stays.

Executes the candidate's real say/role_state_set/_refuse_forbidden source (extracted by AST, decorators dropped)
with _run and _log mocked: nothing is sent, no bd-say, no network.
Opt-in: BD_MCP_SAY_RULE21_CANDIDATE=<absolute path to a bd-mcp server.py>.
"""
import ast
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_MCP_SAY_RULE21_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
TEST2_TEXT = "O1508 rerun on test2 http://10.0.70.95:5555 via bd-capture-test2: /home/x/F.md"


@pytest.fixture
def mod():
    path = Path(CANDIDATE)
    assert path.is_absolute() and path.is_file(), f"candidate missing: {path}"
    tree = ast.parse(path.read_text(), filename=str(path))
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"say", "role_state_set", "_refuse_forbidden"}:
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FORBIDDEN" for t in node.targets):
            body.append(node)
    assert {"say", "role_state_set"} <= {n.name for n in body if isinstance(n, ast.FunctionDef)}, "candidate lacks say"
    Field = pytest.importorskip("pydantic").Field

    run = Mock(return_value={"rc": 0, "out": "sent", "err": ""})
    ns = {"os": os, "Field": Field, "HOME": Path("/nonexistent-home"), "HARNESS": Path("/nonexistent-harness"),
          "_run": run, "_log": Mock()}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)
    return ns, run


@pytest.mark.parametrize("target,text", [("bd-pm-C-B", TEST2_TEXT), ("bd-capture-test2", "status?")])
def test_say_naming_test2_is_sent_not_refused(mod, target, text):
    ns, run = mod
    r = ns["say"](target=target, text=text, kind="", from_seat="bd-worker-Z9", sender="")
    assert run.call_count == 1, "P2 rule21: say() refused a test2 message; rule 21 is retired (O1212)"
    argv = run.call_args.args[0]
    assert argv[-2:] == [target, text] and r["rc"] == 0, f"P2 rule21: wrong bd-say argv {argv}"
    assert run.call_args.kwargs["env"]["BD_SEAT"] == "bd-worker-Z9"


def test_role_state_naming_test2_is_recorded(mod):
    ns, run = mod
    ns["role_state_set"](role="worker", seat="bd-worker-Z9", text=TEST2_TEXT)
    assert run.call_count == 1, "P2 rule21: role_state_set() refused a test2 state line (O1212)"


def test_sender_identity_guard_is_kept(mod, monkeypatch):
    ns, run = mod
    monkeypatch.delenv("BD_SEAT", raising=False)
    with pytest.raises(ValueError, match="sender identity required"):
        ns["say"](target="bd-pm-C-B", text="x", kind="", from_seat="", sender="")
    assert run.call_count == 0, "the missing-sender refusal must stop the send"


def test_no_rule21_refusal_text_remains():
    src = Path(CANDIDATE).read_text()
    assert "operator-only (FLEET_RULE 21)" not in src, "P2 rule21: the retired rule-21 refusal message is still in bd-mcp"
