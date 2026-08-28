"""v3.66.1174: reviewed defect suppressions are exact, stale, and auditable.

The detector must still run.  A suppression is a post-detection decision bound
to one detector, one tracked production path, and one normalized AST node.  It
must not become a filename-wide ignore or a second detector registry.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "project-knowledge" / "DEFECT_PATTERN_SUPPRESSIONS.json"
SCANNERS = (
    ROOT / "toolchain" / "bin" / "bd-defect-scan",
    ROOT / "tools" / "defect_patterns.py",
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _handler_fingerprint(source: str, *, qualname: str = "endpoint", index: int = 0) -> str:
    """Hash DP, path, owner, protected try body, and normalized handler AST."""
    tree = ast.parse(source)
    tries = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    owner = tries[index]
    protected = ast.Module(body=owner.body, type_ignores=[])
    protected_ast = ast.dump(protected, annotate_fields=True, include_attributes=False)
    handler_ast = ast.dump(owner.handlers[0], annotate_fields=True, include_attributes=False)
    identity = "\0".join(("DP-13", "bulk_downloader/probe.py", qualname,
                           protected_ast, handler_ast))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _source(exception: str = "Exception", *, formatted: bool = False,
            function: str = "endpoint") -> str:
    if formatted:
        return (
            f"def {function}():\n"
            "    # formatting and comments are not semantics\n"
            "    try:\n"
            "        risky()\n"
            f"    except {exception}:\n"
            "        pass\n"
        )
    return (
        f"def {function}():\n"
        "    try:\n"
        "        risky()\n"
        f"    except {exception}:\n"
        "        pass\n"
    )


def _make_tree(tmp_path: Path, source: str | None = None) -> tuple[Path, Path]:
    root = tmp_path / "subject"
    target = root / "bulk_downloader" / "probe.py"
    target.parent.mkdir(parents=True)
    target.write_text(source if source is not None else _source(), encoding="utf-8")
    (root / "project-knowledge").mkdir()
    _git(root, "init", "-q")
    _git(root, "add", ".")
    return root, target


def _row(source: str, *, qualname: str = "endpoint", **updates) -> dict:
    row = {
        "dp": "DP-13",
        "path": "bulk_downloader/probe.py",
        "fingerprint": _handler_fingerprint(source, qualname=qualname),
        "rationale": "Reviewed fixture: swallowed exception is intentional here.",
    }
    row.update(updates)
    return row


def _write_authority(root: Path, rows: list[dict], *, raw: str | None = None) -> None:
    path = root / "project-knowledge" / "DEFECT_PATTERN_SUPPRESSIONS.json"
    path.write_text(raw if raw is not None else json.dumps({
        "schema": "bd-defect-suppressions/v1", "entries": rows,
    }), encoding="utf-8")
    _git(root, "add", ".")


def _run(scanner: Path, root: Path, *, home: Path | None = None,
         extra_env: dict[str, str] | None = None):
    env = dict(os.environ, BDTOOLS_CACHE="1")
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
        env.update(HOME=str(home), XDG_CACHE_HOME=str(home / "cache"))
    if extra_env is not None:
        env.update(extra_env)
    command = [sys.executable, str(scanner), "--scan", str(root)]
    if scanner.name == "bd-defect-scan":
        command.append("--json")
    cp = subprocess.run(
        command,
        text=True, capture_output=True, env=env, timeout=60,
    )
    payload = None
    try:
        payload = json.loads(cp.stdout)
    except json.JSONDecodeError:
        pass
    return cp, payload


def _fault_hook(tmp_path: Path, *, mode: str, target: Path) -> tuple[dict[str, str], Path]:
    """Install a subprocess hook at the real filesystem boundary.

    ``delete-after-walk`` lets the real walk enumerate ``target`` before
    deleting it. ``deny-open`` leaves the real file in place and raises EACCES
    only when the scanner opens that exact path. Both record every firing so a
    test cannot pass through an unexercised fault seam.
    """
    hook_dir = tmp_path / ("hook-" + mode)
    hook_dir.mkdir()
    record = tmp_path / (mode + ".record")
    hook_dir.joinpath("sitecustomize.py").write_text(
        '''\
import builtins
import errno
import os

_mode = os.environ["ROW328_FAULT_MODE"]
_target = os.path.realpath(os.environ["ROW328_FAULT_TARGET"])
_record = os.environ["ROW328_FAULT_RECORD"]
_real_open = builtins.open


def _same_path(path):
    try:
        return os.path.realpath(os.fspath(path)) == _target
    except TypeError:
        return False


def _mark(label):
    with _real_open(_record, "a", encoding="utf-8") as stream:
        stream.write(label + ":" + _target + "\\n")


if _mode == "delete-after-walk":
    _real_walk = os.walk
    _fired = False

    def _racing_walk(*args, **kwargs):
        global _fired
        for dirpath, dirnames, filenames in _real_walk(*args, **kwargs):
            listed = [os.path.join(dirpath, name) for name in filenames]
            if not _fired and any(_same_path(path) for path in listed):
                if not os.path.isfile(_target):
                    raise RuntimeError("row328 race target was not a real file")
                os.unlink(_target)
                _mark("deleted-after-walk")
                _fired = True
            yield dirpath, dirnames, filenames

    os.walk = _racing_walk
elif _mode == "deny-open":
    def _denied_open(path, *args, **kwargs):
        if _same_path(path):
            if not os.path.isfile(_target):
                raise RuntimeError("row328 unreadable target is not present")
            _mark("denied-open")
            raise PermissionError(errno.EACCES, "row328 forced unreadable", _target)
        return _real_open(path, *args, **kwargs)

    builtins.open = _denied_open
else:
    raise RuntimeError("unknown row328 fault mode: " + _mode)
''',
        encoding="utf-8",
    )
    pythonpath = str(hook_dir)
    if os.environ.get("PYTHONPATH"):
        pythonpath += os.pathsep + os.environ["PYTHONPATH"]
    return ({
        "BDTOOLS_CACHE": "0",
        "PYTHONPATH": pythonpath,
        "ROW328_FAULT_MODE": mode,
        "ROW328_FAULT_TARGET": str(target),
        "ROW328_FAULT_RECORD": str(record),
    }, record)


def _assert_valid_payload(payload: dict, *, raw: int, visible: int, suppressed: int,
                          entries: int) -> None:
    assert payload["raw_total_findings"] == raw
    assert payload["total_findings"] == visible
    assert sum(len(rows) for rows in payload["suppressed_findings"].values()) == suppressed
    assert payload["suppression_entries"] == entries
    assert sum(len(rows) for rows in payload["findings"].values()) == visible
    assert sum(payload["raw_by_dp"].values()) == raw


def test_the_canonical_authority_exists_and_has_the_exact_schema() -> None:
    assert AUTHORITY.is_file(), "the canonical suppression authority is absent"
    payload = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    assert set(payload) == {"schema", "entries"}
    assert payload["schema"] == "bd-defect-suppressions/v1"
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) == 12
    assert all(set(row) == {"dp", "path", "fingerprint", "rationale"}
               for row in payload["entries"])
    assert all(len(row["fingerprint"]) == 64
               and set(row["fingerprint"]) <= set("0123456789abcdef")
               and row["rationale"].strip() == row["rationale"]
               and row["rationale"] for row in payload["entries"])
    identities = {(row["dp"], row["path"], row["fingerprint"])
                  for row in payload["entries"]}
    assert len(identities) == 12


def test_canonical_authority_matches_the_exact_current_tree_findings() -> None:
    """Unique-looking hashes are not evidence: both real scanners must apply them."""
    authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    expected = {
        (row["dp"], row["path"], row["fingerprint"])
        for row in authority["entries"]
    }
    assert len(expected) == 12

    for scanner in SCANNERS:
        cp, payload = _run(scanner, ROOT)
        assert cp.returncode == 0, (scanner, cp.stderr, cp.stdout[-2000:])
        assert payload is not None
        actual = {
            (finding["dp"], path, finding["fingerprint"])
            for path, findings in payload["suppressed_findings"].items()
            for finding in findings
        }
        assert actual == expected
        assert payload["suppression_entries"] == 12
        assert payload["suppression_errors"] == []
        assert payload["suppressed_total_findings"] == 12
        assert payload["raw_total_findings"] == payload["total_findings"] + 12
        assert payload["raw_by_dp"]["DP-13"] == payload["by_dp"]["DP-13"] + 12


def test_a_source_file_vanishing_after_enumeration_is_counted_exactly(
    tmp_path: Path,
) -> None:
    root, stable = _make_tree(tmp_path, "stable_value = 1\n")
    vanishing = stable.with_name("vanishing.py")
    vanishing.write_text("vanishing_value = 1\n", encoding="utf-8")
    _write_authority(root, [])
    fault_env, record = _fault_hook(
        tmp_path, mode="delete-after-walk", target=vanishing
    )

    cp, payload = _run(SCANNERS[0], root, extra_env=fault_env)

    assert record.read_text(encoding="utf-8").splitlines() == [
        "deleted-after-walk:" + str(vanishing.resolve())
    ]
    assert not vanishing.exists()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert payload is not None
    assert payload["files_enumerated"] == 2
    assert payload["files_scanned"] == 1
    assert payload["files_vanished"] == 1
    assert payload["total_findings"] == 0


def test_an_unreadable_present_source_remains_unknown_and_refuses(
    tmp_path: Path,
) -> None:
    root, stable = _make_tree(tmp_path, "stable_value = 1\n")
    unreadable = stable.with_name("unreadable.py")
    unreadable.write_text("unreadable_value = 1\n", encoding="utf-8")
    _write_authority(root, [])
    fault_env, record = _fault_hook(tmp_path, mode="deny-open", target=unreadable)

    cp, payload = _run(SCANNERS[0], root, extra_env=fault_env)

    assert unreadable.is_file(), "the negative control must remain present"
    assert record.read_text(encoding="utf-8").splitlines() == [
        "denied-open:" + str(unreadable.resolve())
    ]
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert payload is None
    assert "CANNOT-EVALUATE" in cp.stderr
    assert "UNREADABLE" in cp.stderr
    assert str(unreadable) in cp.stderr
    assert "VANISHED" not in cp.stderr


def test_a_real_finding_in_a_real_file_is_still_reported(tmp_path: Path) -> None:
    root, target = _make_tree(tmp_path, _source())
    _write_authority(root, [])

    cp, payload = _run(SCANNERS[0], root, extra_env={"BDTOOLS_CACHE": "0"})

    assert target.is_file()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert payload is not None
    assert payload["files_enumerated"] == 1
    assert payload["files_scanned"] == 1
    assert payload["files_vanished"] == 0
    assert payload["total_findings"] == 1
    assert payload["raw_total_findings"] == 1
    assert [finding["dp"] for finding in
            payload["findings"]["bulk_downloader/probe.py"]] == ["DP-13"]


def test_chromium_profile_markers_exclude_generated_profile_data(
    tmp_path: Path,
) -> None:
    root, stable = _make_tree(tmp_path, "stable_value = 1\n")
    profile = root / "tools" / "browser-runtime-data"
    profile.mkdir(parents=True)
    profile.joinpath("Local State").write_text("{}\n", encoding="utf-8")
    profile.joinpath("payload.py").write_text(_source(), encoding="utf-8")
    profile.joinpath("SingletonLock").symlink_to("browser-host-12345")
    _write_authority(root, [])

    cp, payload = _run(SCANNERS[0], root, extra_env={"BDTOOLS_CACHE": "0"})

    assert stable.is_file()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert payload is not None
    assert payload["files_enumerated"] == 1
    assert payload["files_scanned"] == 1
    assert payload["files_vanished"] == 0
    assert payload["total_findings"] == 0
    assert "tools/browser-runtime-data/payload.py" not in payload["findings"]


def test_transform_control_runs_a_stable_scan_without_exercising_the_race(
    tmp_path: Path,
) -> None:
    root, stable = _make_tree(tmp_path, "stable_value = 1\n")
    _write_authority(root, [])

    cp, payload = _run(SCANNERS[0], root, extra_env={"BDTOOLS_CACHE": "0"})

    assert stable.is_file()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert payload is not None
    assert payload["files_scanned"] == 1
    assert payload["total_findings"] == 0


def test_one_node_is_suppressed_while_an_adjacent_finding_stays_visible(tmp_path: Path) -> None:
    source = _source(function="endpoint") + "\n" + _source(function="other_endpoint")
    root, _ = _make_tree(tmp_path, source)
    _write_authority(root, [_row(source, qualname="endpoint")])
    for scanner in SCANNERS:
        cp, payload = _run(scanner, root)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        _assert_valid_payload(payload, raw=2, visible=1, suppressed=1, entries=1)
        suppressed_rows = payload["suppressed_findings"]["bulk_downloader/probe.py"]
        assert suppressed_rows[0]["dp"] == "DP-13"


def test_formatting_only_change_keeps_the_review_valid(tmp_path: Path) -> None:
    original = _source()
    formatted = _source(formatted=True)
    assert _handler_fingerprint(original) == _handler_fingerprint(formatted)
    root, _ = _make_tree(tmp_path, formatted)
    _write_authority(root, [_row(original)])
    for scanner in SCANNERS:
        cp, payload = _run(scanner, root)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        _assert_valid_payload(payload, raw=1, visible=0, suppressed=1, entries=1)


def test_semantic_change_makes_the_finding_reappear_and_the_entry_stale(tmp_path: Path) -> None:
    original = _source("Exception")
    changed = _source("ValueError")
    assert _handler_fingerprint(original) != _handler_fingerprint(changed)
    root, _ = _make_tree(tmp_path, changed)
    _write_authority(root, [_row(original)])
    for scanner in SCANNERS:
        cp, payload = _run(scanner, root)
        assert cp.returncode != 0
        assert payload is not None, cp.stdout + cp.stderr
        _assert_valid_payload(payload, raw=1, visible=1, suppressed=0, entries=1)
        assert payload["suppression_errors"]
        assert "stale" in json.dumps(payload["suppression_errors"]).lower()


def test_identical_nodes_are_ambiguous_and_never_both_suppressed(tmp_path: Path) -> None:
    one = _source(function="endpoint")
    source = one + "\n" + one
    root, _ = _make_tree(tmp_path, source)
    _write_authority(root, [_row(source, qualname="endpoint")])
    for scanner in SCANNERS:
        cp, payload = _run(scanner, root)
        assert cp.returncode != 0
        assert payload is not None, cp.stdout + cp.stderr
        _assert_valid_payload(payload, raw=2, visible=2, suppressed=0, entries=1)
        assert "ambiguous" in json.dumps(payload["suppression_errors"]).lower()


@pytest.mark.parametrize("case", [
    "malformed-json", "duplicate-json-key", "wrong-schema", "extra-top-key",
    "missing-field", "extra-row-key", "unknown-detector", "absolute-path",
    "parent-path", "bad-digest", "blank-rationale", "duplicate-row",
    "missing-target", "syntax-error-target", "symlink-target",
])
def test_invalid_authority_fails_closed(case: str, tmp_path: Path) -> None:
    source = _source()
    root, target = _make_tree(tmp_path, source)
    row = _row(source)
    raw = None
    rows = [row]
    top = {"schema": "bd-defect-suppressions/v1", "entries": rows}
    if case == "malformed-json": raw = "{"
    elif case == "duplicate-json-key":
        raw = ('{"schema":"bd-defect-suppressions/v1",'
               '"schema":"bd-defect-suppressions/v1","entries":[]}')
    elif case == "wrong-schema": top["schema"] = "bd-defect-suppressions/v2"
    elif case == "extra-top-key": top["unexpected"] = True
    elif case == "missing-field": row.pop("rationale")
    elif case == "extra-row-key": row["line"] = 2
    elif case == "unknown-detector": row["dp"] = "DP-99"
    elif case == "absolute-path": row["path"] = str(target)
    elif case == "parent-path": row["path"] = "../probe.py"
    elif case == "bad-digest": row["fingerprint"] = "0" * 63
    elif case == "blank-rationale": row["rationale"] = "  "
    elif case == "duplicate-row": rows.append(dict(row))
    elif case == "missing-target": row["path"] = "bulk_downloader/missing.py"
    elif case == "syntax-error-target": target.write_text("def broken(:\n", encoding="utf-8")
    elif case == "symlink-target":
        real = target.with_name("real.py")
        target.rename(real)
        target.symlink_to(real.name)
    if raw is None:
        raw = json.dumps(top)
    _write_authority(root, rows, raw=raw)
    for scanner in SCANNERS:
        cp, _payload = _run(scanner, root)
        assert cp.returncode != 0, f"{scanner.name} accepted {case}: {cp.stdout}"
        assert "suppression" in (cp.stdout + cp.stderr).lower()


def test_cached_values_remain_raw_when_only_suppression_authority_changes(tmp_path: Path) -> None:
    source = _source()
    root, _ = _make_tree(tmp_path, source)
    home = tmp_path / "home"
    _write_authority(root, [])
    first, before = _run(SCANNERS[0], root, home=home)
    assert first.returncode == 0
    _assert_valid_payload(before, raw=1, visible=1, suppressed=0, entries=0)
    _write_authority(root, [_row(source)])
    second, after = _run(SCANNERS[0], root, home=home)
    assert second.returncode == 0, second.stdout + second.stderr
    _assert_valid_payload(after, raw=1, visible=0, suppressed=1, entries=1)
    assert before["raw_by_dp"] == after["raw_by_dp"]


def test_the_two_entry_points_return_identical_suppression_evidence(tmp_path: Path) -> None:
    source = _source()
    root, _ = _make_tree(tmp_path, source)
    _write_authority(root, [_row(source)])
    evidence_keys = {
        "schema", "raw_total_findings", "raw_by_dp", "total_findings", "by_dp",
        "findings", "suppressed_findings", "suppression_entries",
        "suppression_errors", "files_enumerated", "files_scanned",
        "files_vanished",
    }
    results = []
    for scanner in SCANNERS:
        cp, payload = _run(scanner, root)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        results.append({key: payload[key] for key in evidence_keys})
    assert results[0] == results[1]
