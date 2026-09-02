"""Tests for the release-hygiene tools wired into build_release.py (Wave 5):
scan_version_pins, check_frontend_present, diff_release_zips.

Synthetic zips/trees only — no real artifacts. Each tool's pass AND fail
(teeth) path is exercised.

Zero-arg test functions; repo root from __file__ (run_tests.py convention).
"""
import os
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import scan_version_pins as SVP  # noqa: E402
import check_frontend_present as CFP  # noqa: E402
import diff_release_zips as DRZ  # noqa: E402


def _mkzip(files: dict) -> str:
    d = Path(tempfile.mkdtemp(prefix="hyg_"))
    z = d / "rel.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return str(z)


def _mktree(rel_files: dict) -> str:
    root = tempfile.mkdtemp(prefix="hygtree_")
    for rel, data in rel_files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
    return root


def _tracked_synthetic_corpus_wacz() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z", "--",
         "tests/capture_corpus_synthetic"],
        capture_output=True,
        check=True,
    )
    return {
        path for path in result.stdout.decode("utf-8").split("\0")
        if path.endswith(".wacz")
    }


def _run_synthetic_with_custom_runner(tmp_path: Path, source: str):
    test_file = tmp_path / "test_synthetic_runner_case.py"
    test_file.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_REPO / "run_tests.py"), str(test_file)],
        cwd=_REPO,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_custom_runner_keeps_a_single_list_parameter_as_one_value(tmp_path):
    result = _run_synthetic_with_custom_runner(
        tmp_path,
        """
import pytest

@pytest.mark.parametrize("payload", [["left", "right"]])
def test_list_payload(payload):
    assert payload == ["left", "right"]
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total: 1 | Passed: 1 | Failed: 0" in result.stdout


def test_custom_runner_spawn_can_import_the_loaded_test_module(tmp_path):
    result = _run_synthetic_with_custom_runner(
        tmp_path,
        """
import multiprocessing

def _send_result(connection):
    connection.send("spawn-ok")
    connection.close()

def test_spawn_import():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_send_result, args=(child,))
    process.start()
    child.close()
    assert parent.recv() == "spawn-ok"
    process.join(timeout=10)
    assert process.exitcode == 0
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total: 1 | Passed: 1 | Failed: 0" in result.stdout


def test_custom_runner_runs_setup_function_before_each_test(tmp_path):
    result = _run_synthetic_with_custom_runner(
        tmp_path,
        """
state = []

def setup_function():
    state.clear()

def test_first_dirties_module_state():
    state.append("dirty")

def test_second_starts_clean():
    assert state == []
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total: 2 | Passed: 2 | Failed: 0" in result.stdout


# ── scan_version_pins ──────────────────────────────────────────────
def test_version_pin_matches_expected():
    root = _mktree({"tests/test_x.py": 'assert __version__ == "3.66.169"\n'})
    hard, _soft = SVP.scan_test_pins(root, "3.66.169")
    assert hard == [], hard


def test_version_pin_mismatch_flagged():
    root = _mktree({"tests/test_x.py": 'assert __version__ == "3.66.168"\n'})
    hard, _soft = SVP.scan_test_pins(root, "3.66.169")
    assert len(hard) == 1 and hard[0][2] == "3.66.168", hard


# ── check_frontend_present ─────────────────────────────────────────
def _critical_files():
    return {p: "x" for p in CFP.CRITICAL_FRONTEND}


def test_frontend_required_ok():
    z = _mkzip({**_critical_files(), "bulk_downloader/__init__.py": "v"})
    assert CFP.required_present(z) == []


def test_frontend_required_detects_absent_and_empty():
    files = _critical_files()
    files["frontend/src/App.tsx"] = ""          # empty
    del files["frontend/dist/index.html"]         # absent
    z = _mkzip(files)
    bad = CFP.required_present(z)
    assert any("App.tsx" in b and "empty" in b for b in bad), bad
    assert any("index.html" in b and "absent" in b for b in bad), bad


def test_frontend_compare_detects_drop_and_change():
    base = _mkzip({"frontend/src/App.tsx": "v1", "frontend/dist/index.html": "h"})
    cand = _mkzip({"frontend/src/App.tsx": "v2"})  # index.html dropped, App changed
    res = CFP.compare(base, cand)
    assert "frontend/dist/index.html" in res["missing"], res
    assert "frontend/src/App.tsx" in res["changed"], res


# ── diff_release_zips ──────────────────────────────────────────────
def test_diff_added_changed_removed():
    old = _mkzip({"a.py": "1", "b.py": "1"})
    new = _mkzip({"a.py": "2", "c.py": "1"})  # a changed, b removed, c added
    d = DRZ.diff(old, new)
    assert d["changed"] == ["a.py"], d["changed"]
    assert d["removed"] == ["b.py"], d["removed"]
    assert d["added"] == ["c.py"], d["added"]


def test_diff_flags_forbidden_artifacts():
    new = _mkzip({"x.py": "1", "bulk_downloader/__pycache__/x.pyc": "z",
                  "data/site.wacz": "w"})
    bad = DRZ.forbidden_artifacts(list(__import__("zipfile").ZipFile(new).namelist()))
    assert any("__pycache__" in b for b in bad), bad
    assert any(b.endswith(".wacz") for b in bad), bad


def test_diff_allows_only_declared_synthetic_capture_corpus_wacz():
    tracked = _tracked_synthetic_corpus_wacz()
    assert len(tracked) == 20, tracked
    assert DRZ.forbidden_artifacts(sorted(tracked)) == []
    bad = set(DRZ.forbidden_artifacts([
        "tests/fixtures/site.redacted.wacz",
        "tests/capture_corpus_syntheticish/site.wacz",
        "tests/capture_corpus/site.wacz",
        "tests/fixtures/site.wacz",
        "captures/site.wacz",
    ]))
    assert bad == {
        "tests/capture_corpus_syntheticish/site.wacz",
        "tests/capture_corpus/site.wacz",
        "tests/fixtures/site.wacz",
        "captures/site.wacz",
    }


def test_undeclared_capture_corpus_wacz_is_not_exempt_by_its_directory():
    """A prefix is not per-member release-hygiene evidence."""
    planted = "tests/capture_corpus_synthetic/members_real_session.wacz"
    tracked = _tracked_synthetic_corpus_wacz()
    assert len(tracked) == 20, tracked
    assert planted.startswith("tests/capture_corpus_synthetic/")
    assert planted.endswith(".wacz")
    assert ".redacted." not in planted
    assert planted not in tracked
    findings = DRZ.forbidden_artifacts([planted])
    assert findings == [planted], (
        "an undeclared capture corpus member was exempted by its directory prefix; "
        f"the production forbidden-artifact seam returned {findings!r}"
    )


def test_capture_corpus_declaration_matches_the_tracked_wacz_denominator():
    manifest = json.loads(
        (_REPO / "tests/capture_corpus_synthetic/datapackage.json").read_text(
            encoding="utf-8"
        )
    )
    resources = manifest.get("resources")
    assert isinstance(resources, list), resources
    declared = {resource.get("path") for resource in resources if isinstance(resource, dict)}
    tracked = _tracked_synthetic_corpus_wacz()
    assert len(tracked) == 20, tracked
    assert declared == tracked, (
        "the declaration must name every tracked synthetic capture member exactly; "
        f"undeclared={sorted(tracked - declared)!r}; stale={sorted(declared - tracked)!r}"
    )


def test_unavailable_capture_corpus_declaration_fails_closed(tmp_path, monkeypatch):
    member = "tests/capture_corpus_synthetic/cap.wacz"
    tracked = _tracked_synthetic_corpus_wacz()
    assert len(tracked) == 20, tracked
    assert member in tracked
    assert DRZ.forbidden_artifacts([member]) == []

    controls_fired = 0
    for label, contents in (("missing", None), ("malformed", "{not json")):
        manifest = tmp_path / f"{label}.json"
        if contents is not None:
            manifest.write_text(contents, encoding="utf-8")
        monkeypatch.setattr(DRZ, "_SYNTHETIC_CAPTURE_MANIFEST", manifest)
        findings = DRZ.forbidden_artifacts([member])
        controls_fired += 1
        assert findings == [member], (
            f"the {label} capture-corpus declaration failed open: {findings!r}"
        )
    assert controls_fired == 2


def test_diff_flags_frontend_drop():
    old = _mkzip({"frontend/dist/index.html": "h", "x.py": "1"})
    new = _mkzip({"x.py": "1"})
    d = DRZ.diff(old, new)
    assert d["frontend_dropped"] == ["frontend/dist/index.html"], d


def test_diff_version_and_changelog_extraction():
    z = _mkzip({"bulk_downloader/__init__.py": '__version__ = "3.66.169"\n',
                "CHANGELOG.md": "# Changelog\n\n## v3.66.169 — x\n"})
    assert DRZ.version_of(z) == "3.66.169"
    assert DRZ.changelog_top(z) == "3.66.169"


def _git_blob(revision: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=_REPO,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, (
        f"the tracked RED input {revision}:{path} is unavailable: "
        f"{result.stderr.strip()}"
    )
    assert result.stdout, f"the tracked RED input {revision}:{path} is empty"
    return result.stdout


def test_release_diff_rejects_bfbc073_headerless_duplicate(capsys):
    """The real v3.66.1240 artifact must not be judged from its first header.

    ``bfbc073`` prepended a complete draft between the stable preamble and its
    first level-two header, then prepended the headed entry too.  The release
    diff gate saw one matching v3.66.1240 header and returned OK because every
    line above that header was outside its denominator.
    """
    defect = _git_blob("bfbc073", "CHANGELOG.md")
    defect_lines = defect.splitlines()
    headers = [
        (line_number, line)
        for line_number, line in enumerate(defect_lines, start=1)
        if line.startswith("## ")
    ]
    current_headers = [item for item in headers if "v3.66.1240" in item[1]]
    prefix_nonblank = [
        line_number
        for line_number, line in enumerate(defect_lines[5:headers[0][0] - 1], start=6)
        if line.strip()
    ]

    # Preconditions are facts about the filed commit, not assumptions derived
    # from the gate: one headed entry, a 44-line headerless draft at 7-50, and
    # the characteristic first bullet present in both drafts.
    assert defect_lines[:5] == [
        "# Changelog",
        "",
        "Versioning is loose — pre-3.43 was unstructured, 3.43+ is grouped by",
        "phase number. Notes here cover recent releases. The former pre-v3.46",
        "archive is not present in this repository; consult source-control history.",
    ]
    assert headers[0] == (
        52,
        "## v3.66.1240 - the supervisor form shows what the server actually has",
    )
    assert current_headers == [headers[0]], current_headers
    assert len(prefix_nonblank) == 44, prefix_nonblank
    assert prefix_nonblank == list(range(7, 51)), prefix_nonblank
    assert defect.count(
        "- THREE MORE UNSEEDED FIELDS, THE OTHER HALF OF ROW 238'S CENSUS."
    ) == 2

    baseline = _mkzip({
        "bulk_downloader/__init__.py": _git_blob(
            "bfbc073^", "bulk_downloader/__init__.py"
        ),
        "CHANGELOG.md": _git_blob("bfbc073^", "CHANGELOG.md"),
    })
    candidate = _mkzip({
        "bulk_downloader/__init__.py": _git_blob(
            "bfbc073", "bulk_downloader/__init__.py"
        ),
        "CHANGELOG.md": defect,
    })

    result = DRZ._run(["--old", baseline, "--new", candidate])
    output = capsys.readouterr().out
    assert result == 1, (
        "bfbc073's real headerless duplicate returned OK; the gate still "
        f"excludes lines 7-50 above the first header\n{output}"
    )
    assert "CHANGELOG ORPHAN PROSE" in output, output
    assert "lines 7-50" in output, output


_INITIAL_IMPORT = "2c1e4dd9ab851d6f00f5380e9883a11f04cd682f"
_KNOWN_BAD_FIRST_PARENT_STATES = {
    # Merge PR #505 carried bfbc073's headerless draft onto main. Its repaired
    # child is expected to pass; the real topic commit is the RED input above.
    "3372dbeb15aeb54a7b4d2adc0b78df081710fc2e",
}
_MINIMUM_FIRST_PARENT_CHANGELOG_STATES = 395


def _changelog_through_first_header(revision: str) -> str:
    """Read exactly the layout denominator, without materialising a 2.7MB blob."""
    process = subprocess.Popen(
        ["git", "show", f"{revision}:CHANGELOG.md"],
        cwd=_REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    assert process.stdout is not None
    lines = []
    for line in process.stdout:
        lines.append(line)
        if line.startswith("## "):
            break
    process.stdout.close()
    assert process.stderr is not None
    error = process.stderr.read()
    process.stderr.close()
    process.wait(timeout=30)
    assert lines and lines[-1].startswith("## "), (
        f"{revision}:CHANGELOG.md did not yield a first level-two header: {error}"
    )
    return "".join(lines)


def test_every_expected_historical_changelog_layout_passes():
    """No overcorrection across the complete, explicitly bounded history.

    The accepted population is every first-parent CHANGELOG-changing revision
    reachable from this candidate, minus the one explicitly named known-bad
    merge. This revision-graph boundary is independent of the CHANGELOG text,
    grows with later release cuts, and fails closed on truncated history.
    """
    result = subprocess.run(
        [
            "git", "rev-list", "--first-parent", "--reverse",
            "HEAD", "--", "CHANGELOG.md",
        ],
        cwd=_REPO,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    main_revisions = result.stdout.splitlines()
    assert len(main_revisions) >= _MINIMUM_FIRST_PARENT_CHANGELOG_STATES, (
        "the historical denominator is truncated: expected at least %d "
        "first-parent CHANGELOG states, collected %d"
        % (_MINIMUM_FIRST_PARENT_CHANGELOG_STATES, len(main_revisions))
    )
    assert main_revisions[0] == _INITIAL_IMPORT
    assert _KNOWN_BAD_FIRST_PARENT_STATES <= set(main_revisions), (
        "the explicit known-bad exclusions are absent from the collected history"
    )

    accepted_revisions = [
        revision for revision in main_revisions
        if revision not in _KNOWN_BAD_FIRST_PARENT_STATES
    ]
    assert len(accepted_revisions) == (
        len(main_revisions) - len(_KNOWN_BAD_FIRST_PARENT_STATES)
    )
    assert len(accepted_revisions) == len(set(accepted_revisions))
    assert accepted_revisions
    failures = []
    for revision in accepted_revisions:
        prefix = _changelog_through_first_header(revision)
        layout = DRZ.changelog_preamble_layout(prefix)
        if layout["status"] != "ok":
            failures.append((revision, layout))
    assert not failures, (
        "the preamble-region gate rejected %d of %d explicitly accepted "
        "historical CHANGELOG states: %r"
        % (len(failures), len(accepted_revisions), failures[:10])
    )


def test_unmeasurable_changelog_layout_is_UNKNOWN_not_OK(capsys):
    baseline = _mkzip({
        "bulk_downloader/__init__.py": '__version__ = "1.0.0"\n',
        "CHANGELOG.md": "# Changelog\n\npreamble\n\n## v1.0.0\n",
    })
    candidate = _mkzip({
        "bulk_downloader/__init__.py": '__version__ = "1.0.1"\n',
        "CHANGELOG.md": "## v1.0.1\n\nbody\n",
    })
    result = DRZ._run(["--old", baseline, "--new", candidate])
    output = capsys.readouterr().out
    assert result == 2, output
    assert "CHANGELOG LAYOUT UNKNOWN" in output, output
    assert "RELEASE DIFF GATE: UNKNOWN" in output, output
    assert "RELEASE DIFF GATE: OK" not in output, output


def test_a_candidate_cannot_redefine_an_orphan_block_as_its_preamble():
    forged = (
        "# Changelog\n\n"
        "- orphan draft posing as the document preamble\n\n"
        "## v1.0.1\n\nbody\n"
    )
    layout = DRZ.changelog_preamble_layout(forged)
    assert layout["status"] == "unknown", layout
    assert layout["offending_lines"] == [], layout
    assert "explicit prose preamble is not recognized" in layout["reason"]

    shifted_clean = (
        "# Changelog\n\n\n\n"
        "Versioning is loose — pre-3.43 was unstructured, 3.43+ is grouped by\n"
        "phase number. Notes here cover recent releases. The former pre-v3.46\n"
        "archive is not present in this repository; consult source-control history.\n"
        "\n## v1.0.1\n\nbody\n"
    )
    clean_layout = DRZ.changelog_preamble_layout(shifted_clean)
    assert clean_layout["status"] == "ok", clean_layout
    assert clean_layout["preamble_end_line"] == 7, clean_layout
    assert clean_layout["first_header_line"] == 9, clean_layout


def test_transform_control_only_imports_the_gate_module():
    """Mutation transform control: importability is not behaviour evidence."""
    __import__("diff_release_zips")


# ── the current version appears ONCE as a changelog header ────────
#
# @1009. v3.66.1008 shipped its entry TWICE. The prepend script verified ASCII
# with a read-back AFTER writing, so a failed check left the file mutated, and
# the corrected re-run prepended a second copy to an already-prepended file.
#
# Nothing caught it. Both CHANGELOG checks in .github/workflows/ci.yml resolve
# `hdr[0]` -- the FIRST '## ' header -- and assert over that entry alone, so a
# duplicate anywhere below is structurally outside their denominator. They
# reported ASCII-clean and version-coherent, truthfully, about one of the two.
#
# SCOPED TO THE CURRENT VERSION, NOT TO EVERY HEADER, and that is measured
# rather than cautious: the changelog already carries `## v3.49.0 - 2026-05-15`
# twice, from long before this cut (2 of 1001 headers at @1009). A blanket
# uniqueness gate would fail on history nobody intends to rewrite, get switched
# off, and take the useful half with it -- CLAUDE.md section 0's
# over-sensitivity failure, which is a soundness bug and not a safe default.

def _repo_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent


def _changelog_headers(text):
    return [l for l in text.splitlines() if l.startswith("## ")]


def test_the_header_scan_can_see_the_changelog():
    """Non-empty denominator, asserted before the verdict below. A parse that
    found no headers would report "the version appears once" just as
    truthfully, over nothing."""
    text = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    assert len(_changelog_headers(text)) > 100, "the header scan went blind"


def test_the_current_changelog_has_only_blanks_before_its_first_header():
    """This is the exact-head gate; the real-commit test above proves its teeth."""
    text = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    layout = DRZ.changelog_preamble_layout(text)
    assert layout["status"] != "unknown", (
        "CHANGELOG preamble/header denominator is UNKNOWN: %s" % layout["reason"]
    )
    assert layout["preamble_end_line"] > 0
    assert layout["first_header_line"] > layout["preamble_end_line"]
    assert layout["status"] == "ok", (
        "CHANGELOG ORPHAN PROSE at lines %s: nonblank content appears after "
        "the preamble and before the first level-two header"
        % DRZ._line_ranges(layout["offending_lines"])
    )


def test_the_current_version_has_exactly_one_changelog_entry():
    import re
    root = _repo_root()
    v = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (root / "bulk_downloader" / "__init__.py").read_text(
                      encoding="utf-8")).group(1)
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    hits = [h for h in _changelog_headers(text) if v in h]
    assert len(hits) == 1, (
        "v%s has %d changelog entries, expected exactly 1: %r\n"
        "A prepend that ran twice is the way this happens; ci.yml's two "
        "CHANGELOG checks read only the first header and cannot see it."
        % (v, len(hits), hits))


def test_the_gate_FIRES_on_a_duplicate_and_not_on_a_single_entry():
    """Both directions. A gate that only ever passes is not a gate, and one
    that fires on the correct shape would be switched off."""
    def hits(text, v):
        return [h for h in _changelog_headers(text) if v in h]
    one = "# Changelog\n\n## v9.9.9\n\nx\n\n## v9.9.8\n\ny\n"
    two = "# Changelog\n\n## v9.9.9\n\nx\n\n## v9.9.9\n\nx\n\n## v9.9.8\n\ny\n"
    assert len(hits(one, "9.9.9")) == 1
    assert len(hits(two, "9.9.9")) == 2
