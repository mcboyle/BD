"""bd-census-delta re-sums a census pin across a train's members, NAMED.

T281i: two cuts each added one structured-reason consumer; each was green on its
own base, the train measured 41 against a pin of 39 and an integrator re-pinned it
by hand. These tests build that train from synthetic members (hermetic `git init`
trees carrying a copy of the real pinned test, so the measurer is the real census)
and hold the tool to: the re-sum is +2 and names both members; a second run changes
nothing; a member that removes a consumer is -1; a live text-decision escape, a
drifted hand-maintained input pin, or a measurer that cannot look is REFUSED with
the head file untouched; and the real tree's pin is derivable (in sync).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain/bin/bd-census-delta"
_PIN_REL = "tests/test_rowssrf_loopback_reason_is_structured.py"
_CENSUS = "rowssrf-consumers"
_GUARD = "_is_safe_public_host"
_CLASSIFY = "_classify_ip"


def _consumer(n: int, *, symbol: str = _GUARD, text_decision: bool = False) -> str:
    """A module with n boolean-shaped consumers (or one deciding on the text)."""
    lines = [f"from bulk_downloader.provider_resolve_impl._common import {symbol}", ""]
    for i in range(n):
        decision = ('"loopback" in str(reason)' if text_decision else "not ok")
        lines += ["", f"def guard_{i}(host):",
                  f"    ok, reason = {symbol}(host)",
                  f"    if {decision}:",
                  "        raise ValueError(str(reason))",
                  "    return host", ""]
    return "\n".join(lines)


def _pin_text(consumers: str, noncanonical: str = "{}") -> str:
    """The real pinned test with both pin literals replaced by the given text."""
    raw = (_REPO / _PIN_REL).read_bytes()
    starts = [0] + [i + 1 for i, b in enumerate(raw) if b == 0x0A]
    tree = ast.parse(raw)
    spans = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            text = {"_EXPECTED_RUNTIME_CONSUMERS": consumers,
                    "_NONCANONICAL_SAME_NAME_CALLS": noncanonical}.get(node.targets[0].id)
            if text is not None:
                value = node.value
                spans.append((starts[value.lineno - 1] + value.col_offset,
                              starts[value.end_lineno - 1] + value.end_col_offset,
                              text.encode()))
    assert len(spans) == 2, "fixture: both pin literals must be found in the real file"
    for start, end, text in sorted(spans, reverse=True):
        raw = raw[:start] + text + raw[end:]
    return raw.decode("utf-8")


_BASE_PIN = (
    '{\n'
    '    # hand-audited base consumer\n'
    '    "bulk_downloader/base_site.py": {"_is_safe_public_host": 1},\n'
    '}')


def _tree(root: Path, name: str, files: dict, pin: str = _BASE_PIN,
          noncanonical: str = "{}") -> Path:
    tree = root / name
    for rel, text in {**files, _PIN_REL: _pin_text(pin, noncanonical)}.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tree)], check=True, env=_env(root))
    subprocess.run(["git", "-C", str(tree), "add", "--", "bulk_downloader", "tests"],
                   check=True, env=_env(root))
    return tree


def _env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    env["LC_ALL"] = "C"
    return env


def _run(root: Path, *args: str):
    result = subprocess.run(
        [sys.executable, "-B", str(_TOOL), "--census", _CENSUS, *args],
        cwd=_REPO, env=_env(root), text=True, capture_output=True, timeout=180)
    return result.returncode, result.stdout + result.stderr


def _pin_of(tree: Path, name: str = "_EXPECTED_RUNTIME_CONSUMERS") -> dict:
    module = ast.parse((tree / _PIN_REL).read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in module.body
                if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


_BASE_FILES = {"bulk_downloader/base_site.py": _consumer(1)}


def test_two_members_each_adding_one_consumer_resum_to_plus_two_named(tmp_path):
    base = _tree(tmp_path, "base", _BASE_FILES)
    member_a = _tree(tmp_path, "member_a",
                     {**_BASE_FILES, "bulk_downloader/site_a.py": _consumer(1)})
    member_b = _tree(tmp_path, "member_b",
                     {**_BASE_FILES, "bulk_downloader/site_b.py": _consumer(1)})
    head = _tree(tmp_path, "head", {**_BASE_FILES,
                                    "bulk_downloader/site_a.py": _consumer(1),
                                    "bulk_downloader/site_b.py": _consumer(1)})
    members = ["--base", str(base), "--member", f"cut-a={member_a}",
               "--member", f"cut-b={member_b}", "--head", str(head)]

    # The train as merged: pin 1, tree 3 -- the 39 -> 41 shape. --check reports,
    # never writes.
    before = (head / _PIN_REL).read_bytes()
    rc, out = _run(tmp_path, *members, "--check")
    assert rc == 1, out
    assert "measured_total=3 pinned_total=1" in out, out
    assert (head / _PIN_REL).read_bytes() == before

    rc, out = _run(tmp_path, *members)
    assert rc == 1, out
    assert "MEMBER cut-a delta=+1 bulk_downloader/site_a.py::_is_safe_public_host +1" in out
    assert "MEMBER cut-b delta=+1 bulk_downloader/site_b.py::_is_safe_public_host +1" in out
    assert "SUM base=1 members=+2 -> 3; head measures 3\n" in out, out
    assert ("['bulk_downloader/site_a.py']['_is_safe_public_host'] 0 -> 1 (cut-a +1)"
            in out), out
    assert ("['bulk_downloader/site_b.py']['_is_safe_public_host'] 0 -> 1 (cut-b +1)"
            in out), out
    assert "WROTE 2 pin(s)" in out
    assert _pin_of(head) == {
        "bulk_downloader/base_site.py": {_GUARD: 1},
        "bulk_downloader/site_a.py": {_GUARD: 1},
        "bulk_downloader/site_b.py": {_GUARD: 1},
    }
    written = (head / _PIN_REL).read_text(encoding="utf-8")
    assert "# hand-audited base consumer" in written
    assert "# bd-census-delta: _is_safe_public_host cut-a +1\n" in written

    # Idempotent: the re-pinned head is in sync and its bytes do not move.
    after = (head / _PIN_REL).read_bytes()
    rc, out = _run(tmp_path, *members)
    assert rc == 0, out
    assert "IN-SYNC" in out and "total=3" in out, out
    assert (head / _PIN_REL).read_bytes() == after


def test_member_removing_one_consumer_is_minus_one_named(tmp_path):
    """Positive control on the other sign: the re-sum goes DOWN, by name."""
    pin = ('{\n'
           '    "bulk_downloader/base_site.py": {"_is_safe_public_host": 2},\n'
           '    # ROW X. audit text for the site that is going away\n'
           '    # (two lines of it)\n'
           '    "bulk_downloader/gone.py": {"_is_safe_public_host": 1},\n'
           '}')
    files = {"bulk_downloader/base_site.py": _consumer(2),
             "bulk_downloader/gone.py": _consumer(1)}
    base = _tree(tmp_path, "base", files, pin)
    shrink = {"bulk_downloader/base_site.py": _consumer(1),
              "bulk_downloader/gone.py": _consumer(1)}
    member_rm = _tree(tmp_path, "member_rm", {"bulk_downloader/base_site.py": _consumer(2)}, pin)
    member_shrink = _tree(tmp_path, "member_shrink", shrink, pin)
    head = _tree(tmp_path, "head", {"bulk_downloader/base_site.py": _consumer(1)}, pin)

    rc, out = _run(tmp_path, "--base", str(base), "--member", f"rm={member_rm}",
                   "--member", f"shrink={member_shrink}", "--head", str(head))
    assert rc == 1, out
    assert "MEMBER rm delta=-1 bulk_downloader/gone.py::_is_safe_public_host -1" in out
    assert "MEMBER shrink delta=-1" in out
    assert "SUM base=3 members=-2 -> 1; head measures 1\n" in out, out
    assert "['bulk_downloader/gone.py']['_is_safe_public_host'] 1 -> 0 (rm -1)" in out
    assert "['bulk_downloader/base_site.py']['_is_safe_public_host'] 2 -> 1 (shrink -1)" in out
    assert _pin_of(head) == {"bulk_downloader/base_site.py": {_GUARD: 1}}
    written = (head / _PIN_REL).read_text(encoding="utf-8")
    assert "ROW X." not in written and "(two lines of it)" not in written


def test_new_symbol_in_a_pinned_path_regenerates_that_entry(tmp_path):
    files = {"bulk_downloader/base_site.py": _consumer(1) + "\n" + _consumer(
        1, symbol=_CLASSIFY).replace("guard_0", "classify_0")}
    head = _tree(tmp_path, "head", files)
    rc, out = _run(tmp_path, "--head", str(head))
    assert rc == 1, out
    assert "['_classify_ip'] 0 -> 1 (unattributed +1)" in out, out
    assert _pin_of(head) == {"bulk_downloader/base_site.py": {_GUARD: 1, _CLASSIFY: 1}}
    rc, out = _run(tmp_path, "--head", str(head))
    assert rc == 0, out


def test_a_live_text_decision_is_refused_and_the_head_is_untouched(tmp_path):
    head = _tree(tmp_path, "head", {**_BASE_FILES, "bulk_downloader/bad.py":
                                    _consumer(1, text_decision=True)})
    before = (head / _PIN_REL).read_bytes()
    rc, out = _run(tmp_path, "--head", str(head))
    assert rc == 2, out
    assert "COULD-NOT-LOOK" in out and "escapes" in out, out
    assert "bulk_downloader/bad.py:" in out, out
    assert (head / _PIN_REL).read_bytes() == before


def test_a_drifted_hand_maintained_input_pin_is_refused(tmp_path):
    head = _tree(tmp_path, "head", _BASE_FILES,
                 noncanonical='{"toolchain/bin/absent.py": {"_classify_ip": 3}}')
    before = (head / _PIN_REL).read_bytes()
    rc, out = _run(tmp_path, "--head", str(head))
    assert rc == 2, out
    assert "_NONCANONICAL_SAME_NAME_CALLS" in out and "by hand" in out, out
    assert (head / _PIN_REL).read_bytes() == before


def test_a_measurer_that_cannot_look_is_could_not_look_never_zero(tmp_path):
    head = tmp_path / "not-a-repo"
    (head / "tests").mkdir(parents=True)
    (head / _PIN_REL).write_text(_pin_text(_BASE_PIN), encoding="utf-8")
    rc, out = _run(tmp_path, "--head", str(head))
    assert rc == 2, out
    assert "COULD-NOT-LOOK" in out and "UNKNOWN" in out, out
    assert "IN-SYNC" not in out


def test_member_without_base_is_an_argument_error(tmp_path):
    rc, out = _run(tmp_path, "--head", str(tmp_path), "--member", f"x={tmp_path}")
    assert rc == 2 and "--member needs --base" in out, out


def test_the_real_tree_pin_is_derivable_by_the_tool(tmp_path):
    rc, out = _run(tmp_path, "--head", str(_REPO), "--check")
    assert rc == 0, out
    assert "IN-SYNC tests/test_rowssrf_loopback_reason_is_structured.py" in out, out
