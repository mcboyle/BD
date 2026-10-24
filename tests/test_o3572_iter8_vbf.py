"""Behavioral controls for the O3572 iter8 harness candidate."""
import importlib
import os
import socket
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
_CAND_ENV = os.environ.get("BD_ITER8_VBF_CANDIDATE", "")
CANDIDATE = Path(_CAND_ENV) if _CAND_ENV else Path("/nonexistent")
pytestmark = pytest.mark.skipif(
    not _CAND_ENV or not (CANDIDATE / "arm.py").is_file() or socket.gethostname() != "wrk-test04",
    reason="candidate opt-in on wrk-test04 (.82) required (set BD_ITER8_VBF_CANDIDATE on wrk-test04)",
)

@pytest.fixture(scope="module")
def arm():
    sys.path.insert(0, str(CANDIDATE))
    try:
        yield importlib.import_module("arm")
    finally:
        sys.path.remove(str(CANDIDATE))

def test_sameout_is_never_refute(arm):
    ob = {"source": "model", "primitive": "exit_code", "B": "FAIL", "P": "FAIL", "P2": "FAIL", "expected_base": "ANY"}
    cls = arm.D.classify(ob)
    assert not arm.D.is_refute_evidence(ob, cls, "strict"), "VBF_SAMEOUT_FALSE_REFUTE: BASE=FAIL PATCHED=FAIL must not refute"

def test_real_regression_still_refutes(arm):
    ob = {"source": "model", "primitive": "exit_code", "B": "PASS", "P": "FAIL", "P2": "FAIL", "expected_base": "ANY"}
    assert arm.D.is_refute_evidence(ob, arm.D.classify(ob), "strict")

@pytest.mark.parametrize("b,p", [(b,p) for b in ("PASS", "FAIL", "ERROR", None) for p in ("PASS", "FAIL", "ERROR", None)])
def test_differential_refute_denominator(arm, b, p):
    ob = {"source": "model", "primitive": "exit_code", "B": b, "P": p, "P2": p, "expected_base": "ANY"}
    actual = arm.D.is_refute_evidence(ob, arm.D.classify(ob), "strict")
    assert actual == (b == "PASS" and p == "FAIL")
    if b == p == "FAIL":
        assert arm.D.classify(ob) == "PRE_EXISTING_OR_BROKEN_PROBE", "VBF_DIFFERENTIAL_CLASS"


def test_flaky_or_infrastructure_failures_never_refute(arm):
    for ob in (
        {"B": "PASS", "P": "FAIL", "P2": "PASS"},
        {"B": "PASS", "P": "ERROR", "res_P": {"observed": {"env_gap": True}}},
        {"B": "PASS", "P": "FAIL", "unsupported_reason": ["bad plan"]},
    ):
        assert not arm.D.is_refute_evidence(ob, arm.D.classify(ob), "strict")


def test_ast_context_exact_source_and_bound(arm, tmp_path):
    b, p = tmp_path / "b", tmp_path / "p"
    b.mkdir(); p.mkdir()
    (b / "target.py").write_text("import math\ndef f(x: int = 2):\n    return x\n")
    (p / "target.py").write_text("import math\ndef f(x: int = 2):\n    return x + 1\n")
    text = arm.ASTCTX.header([("M", "target.py")], str(b), str(p))
    assert "def f(x: int = 2):" in text and "import math" in text
    assert "return x + 1" in text and "B target.py" in text and "P target.py" in text
    assert len(text.encode()) < 600
    (b / "huge.py").write_text("def giant():\n" + "    pass\n" * 300)
    text = arm.ASTCTX.header([("M", "huge.py")], str(b), str(p))
    assert "def giant():" in text and len(text.encode()) < 600


def test_ast_context_does_not_open_escape_or_forbidden(arm, tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    outside = tmp_path / "outside.py"; outside.write_text("SECRET = 42")
    (root / "escape.py").symlink_to(outside)
    forbidden = root / "labels.py"; forbidden.write_text("SECRET = 42")
    opened = []
    original = Path.open
    def spy(self, *a, **kw):
        opened.append(self)
        return original(self, *a, **kw)
    monkeypatch.setattr(Path, "open", spy)
    text = arm.ASTCTX.header([("M", "escape.py"), ("M", "labels.py"), ("M", "../outside.py")], root, root)
    assert "SECRET" not in text and opened == []


def _plan(code, *, probe=False, expected="PASS"):
    return {"criteria": [{"id": "C1", "text": "preserve target behavior"}], "obligations": [{
        "id": "O1", "covers": ["C1"], "invariant": "target value", "primitive": "exit_code",
        "anchor": "base", "env_precondition": "target.py exists", "minimal_input": "f(2)",
        "expected_contract": "target value unchanged", "probe": probe,
        "args": {"argv": ["python3", "-c", code], "expect_rc": 0},
        "expected_base": expected, "critical": True}]}


def _run(arm, tmp_path, monkeypatch, plans, *, base_value=2, patched_value=3):
    import json
    b, p, cases = (tmp_path / n for n in ("base", "patched", "cases"))
    for d in (b,p,cases): d.mkdir()
    for tree, value in ((b,base_value),(p,patched_value)):
        (tree / "target.py").write_text(f"def f(x):\n    return {value}\n")
    cut = "SECRET-CUT-123"
    case = cases / cut; case.mkdir()
    for name, body in (("ORDER.md", f"{cut}: preserve f(2)"), ("DONE.md", f"{cut}: changed"), ("diff.patch", f"diff {cut} target.py")):
        (case / name).write_text(body)
    calls = []
    def complete(**kw):
        calls.append(kw)
        assert len(calls) <= len(plans), "VBF_UNBOUNDED_MODEL_CALLS"
        plan = plans[len(calls)-1]
        text = json.dumps(plan) if isinstance(plan, dict) else plan
        return {"text": text, "key": "synthetic", "cached": False, "usage": {}, "error": None}
    monkeypatch.setattr(arm.llm, "complete", complete)
    rec = {"cut_id": cut, "changed": [("M", "target.py")], "snap_base": str(b), "snap_patched": str(p), "owed_tests": []}
    cfg = {**arm.FROZEN, "offline": True, "plan_only": False, "snap_root": None, "weights": "synthetic", "timeout": 10}
    ev = arm.run_cut(rec, str(cases), cfg, arm.Ctx(timeout_s=10), None)
    (tmp_path / "execution.json").write_text(json.dumps(ev, indent=1))
    return ev, calls


def test_end_to_end_real_regression_refutes_without_reask(arm, tmp_path, monkeypatch):
    ev, calls = _run(arm, tmp_path, monkeypatch, [_plan("import target; assert target.f(2) == 2")])
    assert ev["verdict"] == "REFUTE", ev
    assert len(calls) == 1 and ev["vbf"]["stage2"] is None
    assert ev["vbf"]["stage15"]["attempts"] == 0
    assert all("SECRET-CUT-123" not in c["user"] for c in calls)
    assert "CAND-" in calls[0]["user"] and "AST CONTEXT" in calls[0]["user"]


def test_end_to_end_sameout_forces_stage2_without_refute(arm, tmp_path, monkeypatch):
    plan = _plan("import target; assert target.f(2) == 0")
    ev, calls = _run(arm, tmp_path, monkeypatch, [plan,plan])
    assert ev["verdict"] == "ESCALATE", ev
    assert len(calls) == 2 and "STAGE-2 RE-ASK" in calls[1]["user"], "VBF_STAGE2_NOT_REASKED"
    assert ev["vbf"]["stage15"]["attempts"] == 0
    assert ev["obligations"][0]["class"] == "PRE_EXISTING_OR_BROKEN_PROBE"


def test_import_error_repaired_once_before_freeze(arm, tmp_path, monkeypatch):
    bad = _plan("def outer():\n    inner()\ndef inner():\n    from target import missing\n    assert missing(2) == 2\nouter()")
    good = _plan("def outer():\n    inner()\ndef inner():\n    from target import f\n    assert f(2) == 2\nouter()")
    ev, calls = _run(arm, tmp_path, monkeypatch, [bad,good])
    assert ev["verdict"] == "REFUTE", ev
    repair = ev["vbf"]["stage15"]
    assert len(calls) == 2 and repair["attempts"] == 1 and repair["adopted"]
    assert calls[1]["max_tokens"] == 1024 and calls[1]["temperature"] == 0.0, "VBF_REPAIR_NOT_BOUNDED"
    assert calls[1]["seed"] == 0 and calls[1]["retries"] == 0
    trace = repair["failures"][0]["traceback"]
    assert "ImportError:" in trace and len(trace.splitlines()) == 5
    assert repair["frozen_sha256"] == arm.VBF.digest(ev["obligations"][:1])


def test_repair_cannot_discard_obligation(arm, tmp_path, monkeypatch):
    bad = _plan("from target import missing; assert missing(2) == 2")
    weakened = _plan("import target; assert target.f(2) == 2")
    weakened["obligations"][0]["critical"] = False
    ev, calls = _run(arm, tmp_path, monkeypatch, [bad,weakened,bad])
    assert ev["verdict"] == "ESCALATE" and len(calls) == 3
    assert ev["vbf"]["stage15"]["rejected"] == "VBF_REPAIR_CONTRACT_CHANGED"


def test_stage2_adds_differential_refutation(arm, tmp_path, monkeypatch):
    first = _plan("import target; assert target.f(2) == 3", probe=True, expected="FAIL")
    second = _plan("import target; assert target.f(2) == 2")
    ev, calls = _run(arm, tmp_path, monkeypatch, [first,second])
    assert ev["verdict"] == "REFUTE", ev
    assert len(calls) == 2 and ev["refute_ids"] == ["VBF2-0-O1"]


def test_broken_stage2_preserves_qualified_stage1_board(arm, tmp_path, monkeypatch):
    import copy
    first = _plan("import target; assert target.f(2) == 3", probe=True, expected="FAIL")
    coverage = copy.deepcopy(first["obligations"][0])
    coverage.update(id="O2", probe=False)
    coverage["args"]["argv"].append("target.py")
    first["obligations"].append(coverage)
    ev, calls = _run(arm, tmp_path, monkeypatch, [first,"malformed"])
    prior = [copy.deepcopy(o) for o in ev["obligations"] if o["id"] != "VBF-STAGE2-UNRESOLVED"]
    assert arm.D.decide(prior, first["criteria"], [("M", "target.py")])["verdict"] == "BOARD", "VBF_STAGE1_NOT_QUALIFIED"
    assert ev["verdict"] == "BOARD" and len(calls) == 2, "VBF_STAGE2_DESTROYED_VALID_BOARD"
    assert ev["vbf"]["stage2"]["problems"]
    assert not any(o["id"] == "VBF-STAGE2-UNRESOLVED" for o in ev["obligations"])


def test_repair_refuses_wrong_host(arm, monkeypatch):
    monkeypatch.setattr(arm.VBF.subprocess, "check_output", lambda *a, **k: "10.0.70.186")
    with pytest.raises(RuntimeError, match="VBF_HOST_REQUIRED"):
        arm.VBF.require_host()


@pytest.mark.parametrize("field", ["invariant", "env_precondition", "minimal_input", "expected_contract"])
def test_repair_preserves_semantic_contract(arm, field):
    import json
    from types import SimpleNamespace
    original = _plan("from target import missing")
    criteria, obs, problems = arm.schema.normalise(original)
    assert not problems
    weakened = _plan("import target")
    weakened["obligations"][0][field] = "changed contract"
    calls = []
    def complete(**kw):
        calls.append(kw)
        return {"text": json.dumps(weakened), "error": None}
    def execute(*args):
        return {"status": "ERROR", "observed": {"rc": 1, "stderr_tail": "ImportError: missing"}, "error_origin": "plan"}
    _, info = arm.VBF.run(criteria, obs, "unused-base", "unused-patched", SimpleNamespace(),
                           {**arm.FROZEN, "offline": True, "timeout": 10}, "system", "user", complete, arm.schema.extract_json,
                           arm.schema.normalise, lambda *args, **kw: None, execute)
    assert len(calls) == 1
    assert info.get("rejected") == "VBF_REPAIR_CONTRACT_CHANGED", "VBF_REPAIR_WEAKENED_CONTRACT"


@pytest.mark.parametrize("base", ["FAIL", "ERROR"])
def test_harness_untouched_red_remains_nonblocking(arm, base):
    ob = {"source": "harness", "primitive": "test_run", "B": base, "P": "FAIL", "P2": "FAIL", "patch_touched": False}
    assert arm.D.classify(ob) == arm.D.PRE_EXISTING, "VBF_HARNESS_PREEXISTING_BLOCKED"


def test_stage2_unanchored_check_never_refutes(arm, tmp_path, monkeypatch):
    first = _plan("import target; assert target.f(2) == 3", probe=True, expected="FAIL")
    second = _plan("import target; assert target.f(2) == 2")
    second["obligations"][0]["anchor"] = "diff_literal"
    ev, _ = _run(arm, tmp_path, monkeypatch, [first, second])
    assert ev["verdict"] != "REFUTE", "VBF_STAGE2_UNANCHORED_REFUTE"


def _repair(arm, original, repaired, *, stdout=False, taint=False):
    import json
    from types import SimpleNamespace
    criteria, obs, _ = arm.schema.normalise(original)
    if taint: obs[0]["errrepair"] = ["R-FIXTURE"]
    calls = []
    def complete(**kw):
        calls.append(kw)
        return {"text": json.dumps(repaired), "error": None}
    def execute(*args):
        stream = "a\nb\nc\nd\nE   ImportError: missing\nsummary\n" if stdout else "a\nb\nc\nd\nImportError: missing"
        return {"status": "ERROR", "observed": {"rc": 1, "stdout_tail" if stdout else "stderr_tail": stream}, "error_origin": "plan"}
    def validate(rows, base, patched, trusted=None):
        if trusted is not None:
            arm.errrepair.reenter(trusted, rows, base, patched)
    rows, info = arm.VBF.run(criteria, obs, "unused-base", "unused-patched", SimpleNamespace(),
        {**arm.FROZEN, "offline": True, "timeout": 10}, "system", "user", complete,
        arm.schema.extract_json, arm.schema.normalise, validate, execute)
    return rows, info, calls


def test_pytest_stdout_exception_is_repaired(arm):
    original = _plan("from target import missing; assert missing(2) == 2")
    repaired = _plan("from target import f; assert f(2) == 2")
    _, info, calls = _repair(arm, original, repaired, stdout=True)
    assert info["attempts"] == 1 and info["adopted"], "VBF_PYTEST_STDOUT_IGNORED"
    assert info["failures"][0]["traceback"] == "a\nb\nc\nd\nE   ImportError: missing"
    assert "CANONICAL_PLAN" in calls[0]["user"] and "preserve target behavior" in calls[0]["user"], "VBF_REPAIR_PLAN_MISSING"


@pytest.mark.parametrize("weakening", ["expect_rc", "primitive", "assertion", "invocation", "literal"])
def test_repair_preserves_executable_contract(arm, weakening):
    import copy
    original = _plan("from target import missing\ndef check():\n    assert missing(2) == 2\ncheck()")
    repaired = copy.deepcopy(original)
    ob = repaired["obligations"][0]
    ob["args"]["argv"][-1] = ob["args"]["argv"][-1].replace("missing", "f")
    if weakening == "expect_rc": ob["args"]["expect_rc"] = 1
    elif weakening == "primitive": ob["primitive"] = "shell_snippet"
    elif weakening == "assertion": ob["args"]["argv"][-1] = "from target import f\ndef check():\n    pass\ncheck()"
    elif weakening == "invocation": ob["args"]["argv"][-1] = "from target import f\ndef check():\n    assert f(2) == 2"
    elif weakening == "literal": ob["args"]["argv"][-1] = ob["args"]["argv"][-1].replace("== 2", "== 3")
    _, info, _ = _repair(arm, original, repaired)
    assert not info["adopted"] and info.get("rejected") == "VBF_REPAIR_EXECUTABLE_CHANGED", "VBF_REPAIR_EXECUTABLE_WEAKENED"


def test_repair_inherits_trusted_taint(arm):
    original = _plan("from target import missing; assert missing(2) == 2")
    repaired = _plan("from target import f; assert f(2) == 2")
    rows, info, _ = _repair(arm, original, repaired, taint=True)
    assert info["adopted"] and arm.D.is_tainted(rows[0]), "VBF_REPAIR_TAINT_LAUNDERED"


def test_stage2_receives_previous_canonical_plan(arm, tmp_path, monkeypatch):
    plan = _plan("import target; assert target.f(2) == 0")
    _, calls = _run(arm, tmp_path, monkeypatch, [plan, plan])
    assert "CANONICAL_PLAN" in calls[1]["user"] and "preserve target behavior" in calls[1]["user"], "VBF_STAGE2_PLAN_MISSING"


def test_pytest_primitive_retains_exception_streams(arm, tmp_path):
    import importlib
    from types import SimpleNamespace
    prim = importlib.import_module("lib.prims.test_run.prim")
    out = b"a\nb\nc\nd\nE   ImportError: missing\n1 error in 0.01s\n"
    ctx = SimpleNamespace(python="python3", timeout_s=10, venv=None,
        run=lambda *a, **k: SimpleNamespace(stdout=out, stderr=(prim._START+"\n").encode(), rc=2,
                                           timed_out=False, wall_s=.01, sandbox_error=None))
    result = prim.check({"runner": "pytest", "paths": ["test_probe.py"],
                         "files": {"test_probe.py": "def test_probe(): pass"}}, str(tmp_path), ctx)
    assert result["observed"].get("stdout_tail") == out.decode(), "VBF_PYTEST_STREAM_LOST"
    assert prim._START not in result["observed"]["stderr_tail"]


def test_pytest_fixture_import_repair_executes_before_freeze(arm, tmp_path, monkeypatch):
    import copy
    bad = _plan("unused")
    ob = bad["obligations"][0]
    ob["primitive"] = "test_run"
    ob["args"] = {"runner": "pytest", "paths": ["test_vbf_probe.py"], "expect": "pass",
        "files": {"test_vbf_probe.py": "from target import missing\ndef test_value():\n    assert missing(2) == 2\n"}}
    good = copy.deepcopy(bad)
    good["obligations"][0]["args"]["files"]["test_vbf_probe.py"] = "from target import f\ndef test_value():\n    assert f(2) == 2\n"
    ev, calls = _run(arm, tmp_path, monkeypatch, [bad, good])
    assert ev["vbf"]["stage15"]["adopted"] and len(calls) == 2, "VBF_PYTEST_REPAIR_NOT_EXECUTED"
    assert ev["verdict"] == "REFUTE", ev


def test_repair_cannot_alias_expected_to_actual(arm):
    original = _plan("from target import missing; actual=missing(2); expected=2; assert actual == expected")
    repaired = _plan("from target import f; actual=f(2); expected=2; assert actual == actual")
    _, info, _ = _repair(arm, original, repaired)
    assert not info["adopted"], "VBF_REPAIR_REFERENCE_ALIAS"


def test_repair_cannot_redirect_check_to_noop(arm):
    original = _plan("from target import missing\ndef check():\n    assert missing(2) == 2\ndef noop():\n    pass\ncheck()")
    repaired = _plan("from target import f\ndef check():\n    assert f(2) == 2\ndef noop():\n    pass\nnoop()")
    _, info, _ = _repair(arm, original, repaired)
    assert not info["adopted"], "VBF_REPAIR_CHECK_REDIRECTED"
