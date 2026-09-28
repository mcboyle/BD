"""bd-precut --gate declared import edges reconciliation tests (H149).

Closes HARNESS_BACKLOG.md L3982 (H149):
  bd-precut --gate applies the SAME verbatim declared-edge comparison as
  bd-collect-gate.sh and prints
  `RESULT: cut-ready -- N declared import edge(s) owed to the integrator`
  when every reported edge is quoted verbatim under
  `OWED TO THE INTEGRATOR -- NEW IMPORT EDGES`; with one edge undeclared it stays
  NOT CUT-READY and names the COUPLING.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"


def _load_precut():
    loader = SourceFileLoader("bd_precut_h149", str(PRECUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, PRECUT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def precut():
    return _load_precut()


# The ten edges from row 392 (RULING-row392-precut-vs-import-gate-is-not-a-collision-declare-the-ten-edges-verbatim-ADJ-A-20260907T1604Z.md)
ROW392_EDGES = [
    "bulk_downloader/login_impl/submit.py -> bulk_downloader/login_templates_data.py",
    "bulk_downloader/login_impl/submit.py -> bulk_downloader/scrapling_adapter.py",
    "bulk_downloader/runner_auth.py -> bulk_downloader/login_templates_data.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/cloak.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/learn.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/login.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/login_templates_data.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/runner_auth.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/scrapling_adapter.py",
    "tests/test_row392_measured_login_forms.py -> bulk_downloader/session_keeper.py",
]


def _make_sample_junit(edges):
    lines = ["AssertionError: NEW import edge(s) not in the frozen baseline:"]
    for e in edges:
        lines.append(f"  {e}")
    msg = "\n".join(lines)
    # Pytest formatted failure in JUnit XML
    return f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" tests="1">
<testcase classname="tests.test_import_graph_no_new_edges" name="test_no_new_edges" file="tests/test_import_graph_no_new_edges.py">
  <failure message="NEW import edge(s)">
def test_no_new_edges():
&gt;   assert not new
{msg}
  </failure>
</testcase>
</testsuite></testsuites>
"""


def test_denominators():
    """H149: assert both denominators from HARNESS_BACKLOG.md L3999."""
    text = PRECUT.read_text(encoding="utf-8")
    assert text.count("no_new_edges") >= 2, "no_new_edges must be in _UNDERIVED_GATES"
    assert text.count("OWED TO THE INTEGRATOR") >= 1, (
        "OWED TO THE INTEGRATOR must be present in bd-precut"
    )


def test_extract_reported_import_edges(precut, tmp_path):
    """Verify edges are extracted from JUnit XML for test_import_graph_no_new_edges."""
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit(ROW392_EDGES), encoding="utf-8")

    extracted = precut._extract_reported_import_edges(str(junit))
    assert extracted == sorted(ROW392_EDGES)


def test_control_1_row392_ten_edges_declared_verbatim_clears(precut, tmp_path):
    """Control (1): row392's tree with the ten edges declared verbatim -> cut-ready."""
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit(ROW392_EDGES), encoding="utf-8")

    done_md = tmp_path / "DONE.md"
    done_content = [
        "# Task DONE",
        "",
        "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES",
        "",
    ]
    for e in ROW392_EDGES:
        done_content.append(e)
        done_content.append("WHY: required for row392 login measurement")
    done_content.extend(["", "## NEXT SECTION", "Other content"])
    done_md.write_text("\n".join(done_content), encoding="utf-8")

    cleared, msg, count = precut._check_declared_import_edges(str(junit), str(tmp_path))
    assert cleared is True
    assert msg is None
    assert count == 10


def test_control_2_row392_one_line_paraphrased_refuses_naming_coupling(
    precut, tmp_path
):
    """Control (2): row392's tree with one line paraphrased -> NOT CUT-READY naming COUPLING."""
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit(ROW392_EDGES), encoding="utf-8")

    # Paraphrase or abbreviate the last edge (the exact defect row392 shipped)
    done_md = tmp_path / "DONE.md"
    done_content = [
        "# Task DONE",
        "",
        "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES",
        "",
    ]
    for e in ROW392_EDGES[:-1]:
        done_content.append(e)
        done_content.append("WHY: required for row392 login measurement")
    # Paraphrased edge (does not match character for character)
    done_content.append("tests/... -> bulk_downloader/session_keeper.py (paraphrased)")
    done_md.write_text("\n".join(done_content), encoding="utf-8")

    cleared, msg, _count = precut._check_declared_import_edges(
        str(junit), str(tmp_path)
    )
    assert cleared is False
    assert "UNDECLARED IMPORT COUPLING" in msg
    assert ROW392_EDGES[-1] in msg
    assert "The ruling requires them copied VERBATIM" in msg


def test_missing_heading_fails(precut, tmp_path):
    """Edges present in DONE.md but without heading -> fails naming COUPLING."""
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit(ROW392_EDGES[:2]), encoding="utf-8")

    done_md = tmp_path / "DONE.md"
    # Edges written without the required heading
    done_md.write_text("\n".join(ROW392_EDGES[:2]), encoding="utf-8")

    cleared, msg, _count = precut._check_declared_import_edges(
        str(junit), str(tmp_path)
    )
    assert cleared is False
    assert "UNDECLARED IMPORT COUPLING" in msg


def test_missing_done_md_fails(precut, tmp_path):
    """No DONE.md present in root -> fails naming COUPLING."""
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit(ROW392_EDGES[:1]), encoding="utf-8")

    cleared, msg, _count = precut._check_declared_import_edges(
        str(junit), str(tmp_path)
    )
    assert cleared is False
    assert "UNDECLARED IMPORT COUPLING" in msg
    assert ROW392_EDGES[0] in msg


def test_e2e_declared_edges_clears_precut_gate(precut, monkeypatch, tmp_path, capsys):
    """End-to-end: bd-precut --gate prints RESULT: cut-ready -- N declared import edge(s) owed to the integrator."""
    junit_xml = _make_sample_junit(ROW392_EDGES)

    class _Completed:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(argv, **kwargs):
        for a in argv:
            if str(a).startswith("--junitxml="):
                junit_path = a.split("=", 1)[1]
                pathlib.Path(junit_path).write_text(junit_xml, encoding="utf-8")
        env = kwargs.get("env") or {}
        inflight = env.get("BD_PRECUT_INFLIGHT_LOG")
        if inflight:
            pathlib.Path(inflight).write_text(
                "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
                encoding="utf-8",
            )
        return _Completed(1)

    monkeypatch.setattr(precut.subprocess, "run", fake_run)
    done_md = tmp_path / "DONE.md"
    done_content = [
        "# Task DONE",
        "",
        "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES",
        "",
    ]
    for e in ROW392_EDGES:
        done_content.append(e)
        done_content.append("WHY: required for row392 login measurement")
    done_md.write_text("\n".join(done_content), encoding="utf-8")

    rc, detail = precut._run_underived_gates(
        str(tmp_path),
        [("tests/test_import_graph_no_new_edges.py", "imports")],
        dict(os.environ),
    )
    assert rc == 0
    assert detail == "declared-edges-cleared:10"


def test_e2e_undeclared_edges_refuses_naming_coupling(
    precut, monkeypatch, tmp_path, capsys
):
    """End-to-end: bd-precut --gate refuses with UNDECLARED IMPORT COUPLING on paraphrase."""
    junit_xml = _make_sample_junit(ROW392_EDGES)

    class _Completed:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(argv, **kwargs):
        for a in argv:
            if str(a).startswith("--junitxml="):
                junit_path = a.split("=", 1)[1]
                pathlib.Path(junit_path).write_text(junit_xml, encoding="utf-8")
        env = kwargs.get("env") or {}
        inflight = env.get("BD_PRECUT_INFLIGHT_LOG")
        if inflight:
            pathlib.Path(inflight).write_text(
                "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
                encoding="utf-8",
            )
        return _Completed(1)

    monkeypatch.setattr(precut.subprocess, "run", fake_run)
    done_md = tmp_path / "DONE.md"
    done_content = [
        "# Task DONE",
        "",
        "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES",
        "",
    ]
    for e in ROW392_EDGES[:-1]:
        done_content.append(e)
        done_content.append("WHY: required for row392 login measurement")
    done_content.append("paraphrased edge")
    done_md.write_text("\n".join(done_content), encoding="utf-8")

    rc, detail = precut._run_underived_gates(
        str(tmp_path),
        [("tests/test_import_graph_no_new_edges.py", "imports")],
        dict(os.environ),
    )
    assert rc == 1
    assert "UNDECLARED IMPORT COUPLING" in detail
    assert ROW392_EDGES[-1] in detail


def test_a_longer_edge_containing_the_reported_one_does_not_declare_it(
    precut, tmp_path
):
    """VERBATIM is the whole edge, not a substring: a line for a different (longer) path
    that happens to contain the reported edge declares nothing."""
    edge = ROW392_EDGES[0]
    junit = tmp_path / "underived.xml"
    junit.write_text(_make_sample_junit([edge]), encoding="utf-8")
    heading = "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES"
    for decoy in ("x" + edge, edge + ".bak", "vendor/" + edge):
        (tmp_path / "DONE.md").write_text(f"{heading}\n{decoy}\n", encoding="utf-8")
        cleared, msg, _ = precut._check_declared_import_edges(str(junit), str(tmp_path))
        assert cleared is False and edge in msg, decoy
    for ok in (edge, f"- `{edge}`: WHY row392", f"* {edge} (WHY: row392)"):
        (tmp_path / "DONE.md").write_text(f"{heading}\n{ok}\n", encoding="utf-8")
        assert (
            precut._check_declared_import_edges(str(junit), str(tmp_path))[0] is True
        ), ok


def test_edges_are_read_from_a_junit_real_pytest_wrote(precut, tmp_path):
    """The extractor reads the XML pytest itself writes (escaping, `E` prefixes), for a
    failure whose message is built exactly as tests/test_import_graph_no_new_edges.py builds it."""
    edges = ROW392_EDGES[:3]
    src = tmp_path / "tests" / "test_import_graph_no_new_edges.py"
    src.parent.mkdir()
    src.write_text(
        f"NEW = {edges!r}\n\n\ndef test_no_new_edges():\n"
        "    new = [tuple(e.split(' -> ')) for e in NEW]\n"
        "    assert not new, (\n"
        "        'NEW import edge(s) not in the frozen baseline <&> -- declare it in DONE.md:\\n  '\n"
        "        + '\\n  '.join(f'{s} -> {d}' for s, d in new)\n"
        "    )\n",
        encoding="utf-8",
    )
    junit = tmp_path / "underived.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         "--rootdir", str(tmp_path), "--junitxml", str(junit), str(src)],
        cwd=tmp_path, env={**os.environ, "LC_ALL": "C"},
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert proc.returncode == 1 and junit.is_file(), proc.stdout + proc.stderr

    assert precut._extract_reported_import_edges(str(junit)) == sorted(edges)


def test_declared_edges_never_absorb_an_unknown_into_cut_ready(
    precut, monkeypatch, tmp_path, capsys
):
    """main(): cleared edges are reported beside what RAN; a check that did not run stays
    UNKNOWN (the census below reports an error), never a bare `cut-ready`."""
    root = tmp_path / "wt"
    (root / "tests").mkdir(parents=True)
    (root / "bulk_downloader").mkdir()
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.1"\n')
    # the release trio agrees, so the only thing between this tree and cut-ready is the census
    (root / "tests" / "test_settings_center_slice4.py").write_text(
        'assert __version__ == "3.66.1"\n'
    )
    (root / "CHANGELOG.md").write_text("## v3.66.1\n\nrelease\n")
    (root / "PIN_INDEX.json").write_text('{"version": "3.66.1"}\n')
    (root / "tests" / "test_import_graph_no_new_edges.py").write_text(
        "def test_x():\n    pass\n"
    )
    (root / "venv").symlink_to(pathlib.Path(sys.prefix), target_is_directory=True)
    monkeypatch.setattr(
        precut.subprocess, "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", ""),
    )  # fmt: skip
    monkeypatch.setattr(
        precut, "_run_underived_gates", lambda *a, **k: (0, "declared-edges-cleared:3")
    )
    monkeypatch.setattr(precut, "_auto_baseline", lambda _r: None, raising=False)
    monkeypatch.setattr(
        precut,
        "_derive_baseline",
        lambda _r, _t: (None, "test baseline"),
        raising=False,
    )
    monkeypatch.setattr(
        precut,
        "_repo_wide_marker_census",
        lambda _r: {"error": "test census"},
        raising=False,
    )
    for helper in (
        "_print_underived_failures",
        "_print_inflight",
        "_preserve_underived_evidence",
    ):
        monkeypatch.setattr(precut, helper, lambda *a, **k: None, raising=False)
    monkeypatch.delenv("BD_PRECUT_FAST", raising=False)

    rc = precut.main(["--root", str(root), "--gate"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "RESULT: cut-ready -- 3 declared" not in out, out
    assert re.search(
        r"^RESULT: cut-ready for what RAN \(.*3 declared import edge\(s\) owed to the "
        r"integrator\) -- \d+ check\(s\) NOT RUN",
        out,
        re.MULTILINE,
    ), out


# ---- H149 fix-forward (VERDICT-shape-bd-review-shape-A1-A.md R1/R2 + m4/m5 gaps) ----------------------------------


def _other_gate_case(name="test_other_gate_is_red"):
    return (
        f'<testcase classname="tests.test_other_gate" name="{name}" file="tests/test_other_gate.py">'
        '<failure message="other gate failed">AssertionError: other gate failed</failure></testcase>'
    )


def _junit(edges_text, extra_cases=""):
    """A JUnit record whose import-gate failure carries `edges_text`, plus optional other testcases."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" tests="2">
<testcase classname="tests.test_import_graph_no_new_edges" name="test_no_new_edges" file="tests/test_import_graph_no_new_edges.py">
  <failure message="NEW import edge(s)">AssertionError: NEW import edge(s) not in the frozen baseline:
{edges_text}</failure>
</testcase>{extra_cases}
</testsuite></testsuites>
"""


def _run_gate(
    precut, monkeypatch, tmp_path, junit_xml, pytest_rc, declared=ROW392_EDGES
):
    class _Completed:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(argv, **kwargs):
        for a in argv:
            if str(a).startswith("--junitxml="):
                pathlib.Path(a.split("=", 1)[1]).write_text(junit_xml, encoding="utf-8")
        inflight = (kwargs.get("env") or {}).get("BD_PRECUT_INFLIGHT_LOG")
        if inflight:
            pathlib.Path(inflight).write_text(
                "START tests/a.py::test_one\nFINISH tests/a.py::test_one\n",
                encoding="utf-8",
            )
        return _Completed(pytest_rc)

    monkeypatch.setattr(precut.subprocess, "run", fake_run)
    lines = ["# DONE", "", "## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES", ""]
    for e in declared:
        lines += [e, "WHY: fixture"]
    (tmp_path / "DONE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return precut._run_underived_gates(
        str(tmp_path),
        [
            ("tests/test_import_graph_no_new_edges.py", "imports"),
            ("tests/test_other_gate.py", "other gate"),
        ],
        dict(os.environ),
    )


EDGES_TEXT = "\n".join("  " + e for e in ROW392_EDGES)


def test_control_completed_run_with_only_declared_edges_is_cleared(
    precut, monkeypatch, tmp_path
):
    rc, detail = _run_gate(precut, monkeypatch, tmp_path, _junit(EDGES_TEXT), 1)
    assert (rc, detail) == (0, "declared-edges-cleared:10")


@pytest.mark.parametrize("pytest_rc", [2, 3, 4, 5])
def test_an_incomplete_run_is_never_cleared_by_declared_edges(
    precut, monkeypatch, tmp_path, pytest_rc
):
    # R1: an interrupted run's JUnit names only the import failure; the gates after it never ran.
    rc, detail = _run_gate(precut, monkeypatch, tmp_path, _junit(EDGES_TEXT), pytest_rc)
    assert rc == pytest_rc, detail
    assert "declared-edges-cleared" not in detail


def test_declared_edges_clear_the_import_gate_and_nothing_else(
    precut, monkeypatch, tmp_path
):
    # R2(a): the ruling's "and for nothing else" -- another gate's failure keeps the cut red and is named.
    rc, detail = _run_gate(
        precut, monkeypatch, tmp_path, _junit(EDGES_TEXT, _other_gate_case()), 1
    )
    assert rc == 1, detail
    assert "test_other_gate_is_red" in detail
    assert "declared-edges-cleared" not in detail


def test_an_import_failure_with_no_extractable_edge_is_not_cleared(
    precut, monkeypatch, tmp_path
):
    # R2(b): the gate's message shape drifted, so 0 edges are read -- that is not "all edges declared".
    junit_xml = _junit(
        "  (the frozen-baseline message changed shape: no a.py -> b.py lines)"
    )
    rc, detail = _run_gate(precut, monkeypatch, tmp_path, junit_xml, 1)
    assert rc == 1, detail
    assert "declared-edges-cleared" not in detail


def _done_with(tmp_path, text):
    (tmp_path / "DONE.md").write_text(text, encoding="utf-8")


def test_an_edge_outside_the_owed_section_does_not_declare_it(precut, tmp_path):
    # m4: the heading is present, but the edge appears only ABOVE it.
    junit = tmp_path / "j.xml"
    junit.write_text(_junit("  " + ROW392_EDGES[0]), encoding="utf-8")
    _done_with(
        tmp_path,
        f"# DONE\nnote: {ROW392_EDGES[0]}\n\n## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES\n\nnone\n",
    )
    cleared, msg, _ = precut._check_declared_import_edges(str(junit), str(tmp_path))
    assert not cleared and ROW392_EDGES[0] in msg


def test_an_edge_under_a_later_heading_does_not_declare_it(precut, tmp_path):
    # m5: the owed section ends at the next heading.
    junit = tmp_path / "j.xml"
    junit.write_text(_junit("  " + ROW392_EDGES[0]), encoding="utf-8")
    _done_with(
        tmp_path,
        f"# DONE\n## OWED TO THE INTEGRATOR -- NEW IMPORT EDGES\n\nnone\n\n## NOTES\n{ROW392_EDGES[0]}\n",
    )
    cleared, msg, _ = precut._check_declared_import_edges(str(junit), str(tmp_path))
    assert not cleared and ROW392_EDGES[0] in msg
