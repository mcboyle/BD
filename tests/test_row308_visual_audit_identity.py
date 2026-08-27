"""Row 308: visual-audit artifacts carry the capture's real release identity.

The navigator and montage are evidence builders.  A current label derived only
from the checkout would let an old capture masquerade as current, so their
population is the complete non-empty capture manifest: every row must carry one
identity, and that identity must equal the independently parsed release source.
"""
from __future__ import annotations

import ast
import builtins
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import sys

from PIL import Image, ImageDraw
import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_PK = _REPO / "project-knowledge"
_BUILDERS = (
    _PK / "build_navigator.py",
    _PK / "build_montage.py",
)
_VERSION_KEY = "capture_release_version"
_RELEASE_LITERAL = re.compile(r"\bv\d+\.\d+\.\d+\b")


def _identity_module():
    path = _PK / "visual_audit_identity.py"
    spec = importlib.util.spec_from_file_location("row308_visual_audit_identity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_contract_module():
    path = _PK / "capture_manifest_contract.py"
    name = "row308_capture_manifest_contract"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved_modules = {name: sys.modules[name]} if name in sys.modules else {}
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
    return module


def _release_version() -> str:
    source = (_REPO / "bulk_downloader" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assignments = []
    for statement in ast.parse(source).body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            value = statement.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                assignments.append(value.value)
    assert len(assignments) == 1, (
        "release precondition failed: expected exactly one literal __version__, "
        f"found {assignments!r}"
    )
    version = assignments[0]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    return version


def _literal_hits(sources: dict[str, str]) -> list[str]:
    hits = []
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            hits.extend(
                f"{name}:{node.lineno}:{match.group(0)}"
                for match in _RELEASE_LITERAL.finditer(node.value)
            )
    return sorted(hits)


def _assert_no_literal_hits(sources: dict[str, str]) -> None:
    hits = _literal_hits(sources)
    assert not hits, f"hardcoded visual-audit release literal(s): {hits!r}"


def _capture_fixture(tmp_path: Path, *, identity: object) -> Path:
    capture = tmp_path / "capture"
    contract = _manifest_contract_module()
    rows = []
    for view in contract.EXPECTED_VIEWS:
        for theme in contract.THEMES:
            relative = f"{theme}/{view.filename}"
            target = capture / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            colour = (240, 240, 240) if theme == "light" else (20, 20, 20)
            Image.new("RGB", (16, 12), colour).save(target)
            rows.append(
                {
                    "cat": view.cat,
                    "route": view.route,
                    "label": view.label,
                    "theme": theme,
                    "status": "ok",
                    "file": relative,
                    "head": view.label,
                    "h": 12,
                    "err": 0,
                    "ox": False,
                    _VERSION_KEY: identity,
                }
            )
    (capture / "manifest.json").write_text(
        json.dumps(rows), encoding="utf-8"
    )

    # Preconditions: both builders receive row 309's exact valid population.
    assert len(contract.EXPECTED_VIEWS) == 52
    assert contract.THEMES == ("light", "dark")
    assert len(contract.expected_manifest_keys()) == 104
    assert len(rows) == 104
    assert Counter(row["theme"] for row in rows) == {"light": 52, "dark": 52}
    assert Counter(row["cat"] for row in rows) == {
        "nav": 58,
        "drillin": 10,
        "subtab": 24,
        "popup": 10,
        "cockpit": 2,
    }
    assert {
        (row["cat"], row["route"], row["theme"])
        for row in rows
    } == contract.expected_manifest_keys()
    assert sum((capture / row["file"]).is_file() for row in rows) == 104
    assert contract.validate_manifest(rows) is rows
    return capture


def _redirect_outputs(monkeypatch: pytest.MonkeyPatch, output: Path) -> None:
    output.mkdir()
    real_open = builtins.open
    real_getsize = os.path.getsize
    external = Path("/mnt/user-data/outputs")

    def redirected(path, *args, **kwargs):
        try:
            candidate = Path(os.fspath(path))
        except TypeError:
            return real_open(path, *args, **kwargs)
        if candidate == external or external in candidate.parents:
            candidate = output / candidate.name
        return real_open(candidate, *args, **kwargs)

    def redirected_getsize(path):
        candidate = Path(os.fspath(path))
        if candidate == external or external in candidate.parents:
            candidate = output / candidate.name
        return real_getsize(candidate)

    monkeypatch.setattr(builtins, "open", redirected)
    monkeypatch.setattr(os.path, "getsize", redirected_getsize)


def _run_builder(
    builder: Path,
    capture: Path,
    output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    labels = []
    real_draw = ImageDraw.Draw

    class RecordingDraw:
        def __init__(self, image):
            self._delegate = real_draw(image)

        def text(self, xy, value, *args, **kwargs):
            labels.append(value)
            return self._delegate.text(xy, value, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    monkeypatch.setenv("BD_CAPTURE_DIR", str(capture))
    monkeypatch.syspath_prepend(str(_PK))
    _redirect_outputs(monkeypatch, output)
    monkeypatch.setattr(ImageDraw, "Draw", RecordingDraw)
    runpy.run_path(str(builder), run_name="__main__")
    return labels


def test_no_builder_hardcodes_a_release_literal() -> None:
    assert len(_BUILDERS) == 2
    assert sum(path.is_file() for path in _BUILDERS) == 2
    _assert_no_literal_hits(
        {path.name: path.read_text(encoding="utf-8") for path in _BUILDERS}
    )


def test_literal_census_negative_control_fires_once() -> None:
    planted = {'planted.py': 'LABEL = "v9.8.7 · light"\n'}
    hits = _literal_hits(planted)
    assert hits == ["planted.py:1:v9.8.7"]
    with pytest.raises(AssertionError, match="hardcoded visual-audit release literal") as caught:
        _assert_no_literal_hits(planted)
    assert "planted.py:1:v9.8.7" in str(caught.value)


def test_capture_producer_measures_identity_at_both_boundaries() -> None:
    source = (_PK / "capture_all.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = Counter(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    assert calls["health_release_version"] == 2
    assert calls["stamp_manifest"] == 1


def test_health_identity_fires_once_and_stamps_every_nonzero_row() -> None:
    module = _identity_module()
    version = _release_version()
    fired = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"ok": True, "version": version}).encode()

    def opener(url, *, timeout):
        fired.append((url, timeout))
        return Response()

    observed = module.health_release_version(
        "http://127.0.0.1:5599/", _REPO, opener=opener
    )
    assert fired == [("http://127.0.0.1:5599/api/health", 5.0)]
    assert observed == version

    rows = [{"row": 0}, {"row": 1}, {"row": 2}]
    assert len(rows) == 3
    module.stamp_manifest(rows, observed)
    assert [row[_VERSION_KEY] for row in rows] == [version, version, version]
    assert module.validate_manifest_release(rows, _REPO) == version


def test_health_mismatch_and_unavailable_measurement_are_unknown() -> None:
    module = _identity_module()
    fired = []

    class MismatchResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true, "version": "9.8.7"}'

    def mismatch(url, *, timeout):
        fired.append(("mismatch", url, timeout))
        return MismatchResponse()

    with pytest.raises(RuntimeError, match="UNKNOWN: capture release identity mismatch"):
        module.health_release_version("http://127.0.0.1:5599", _REPO, opener=mismatch)
    assert fired == [("mismatch", "http://127.0.0.1:5599/api/health", 5.0)]

    def unavailable(url, *, timeout):
        fired.append(("unavailable", url, timeout))
        raise OSError("planted health outage")

    with pytest.raises(RuntimeError, match="UNKNOWN: capture release identity unavailable"):
        module.health_release_version("http://127.0.0.1:5599", _REPO, opener=unavailable)
    assert fired[-1] == (
        "unavailable",
        "http://127.0.0.1:5599/api/health",
        5.0,
    )
    assert len(fired) == 2


def test_zero_or_unshaped_manifest_is_unknown_not_ok() -> None:
    module = _identity_module()
    version = _release_version()
    fired = []

    for operation, subject, reason in (
        (module.stamp_manifest, [], "unavailable"),
        (module.validate_manifest_release, [], "unavailable"),
        (module.validate_manifest_release, {}, "malformed"),
    ):
        with pytest.raises(
            RuntimeError, match=rf"UNKNOWN: capture release identity {reason}"
        ):
            operation(subject, version if operation is module.stamp_manifest else _REPO)
        fired.append((operation.__name__, reason))

    assert fired == [
        ("stamp_manifest", "unavailable"),
        ("validate_manifest_release", "unavailable"),
        ("validate_manifest_release", "malformed"),
    ]


def test_both_builders_emit_the_validated_capture_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _release_version()
    capture = _capture_fixture(tmp_path, identity=version)

    nav_out = tmp_path / "navigator-output"
    nav_labels = _run_builder(_BUILDERS[0], capture, nav_out, monkeypatch)
    assert nav_labels == []
    navigator = nav_out / "functional.html"
    assert navigator.is_file() and navigator.stat().st_size > 0
    html = navigator.read_text(encoding="utf-8")
    assert html.count(f"v{version}") == 2, (
        "navigator did not render the independently parsed capture/release identity"
    )

    monkeypatch.undo()
    montage_out = tmp_path / "montage-output"
    labels = _run_builder(_BUILDERS[1], capture, montage_out, monkeypatch)
    version_labels = [label for label in labels if isinstance(label, str) and label.startswith("v")]
    assert Counter(version_labels) == {
        f"v{version} · light": 2,
        f"v{version} · dark": 2,
    }
    expected = {
        "montage_light_navtabs.png",
        "montage_light_subtabs.png",
        "montage_dark_navtabs.png",
        "montage_dark_subtabs.png",
    }
    produced = {path.name for path in montage_out.glob("*.png") if path.stat().st_size > 0}
    assert produced == expected


def test_builders_refuse_unknown_capture_identity_before_emitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = (
        (None, "unavailable"),
        ("not-a-version", "malformed"),
        ("9.8.7", "mismatch"),
    )
    expected = [
        (builder.stem, reason)
        for builder in _BUILDERS
        for _identity, reason in identities
    ]
    refused = []
    wrong_errors = []
    emitted = []
    for builder in _BUILDERS:
        for identity, reason in identities:
            case_root = tmp_path / f"{builder.stem}-{reason}"
            capture = _capture_fixture(case_root, identity=identity)
            output = case_root / "output"
            try:
                _run_builder(builder, capture, output, monkeypatch)
            except RuntimeError as exc:
                if f"UNKNOWN: capture release identity {reason}" in str(exc):
                    refused.append((builder.stem, reason))
                else:
                    wrong_errors.append((builder.stem, reason, str(exc)))
            finally:
                evidence = sorted(path.name for path in output.iterdir())
                if evidence:
                    emitted.append((builder.stem, reason, evidence))
                monkeypatch.undo()

    assert refused == expected
    assert wrong_errors == []
    assert emitted == [], "a refused identity still emitted audit evidence"


def test_identity_refusal_precedes_population_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = []
    emitted = []
    for builder in _BUILDERS:
        case_root = tmp_path / builder.stem
        capture = _capture_fixture(case_root, identity=_release_version())
        manifest_path = capture / "manifest.json"
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows[0][_VERSION_KEY] = "9.8.7"
        removed = rows.pop()
        manifest_path.write_text(json.dumps(rows), encoding="utf-8")

        assert len(rows) == 103
        assert removed[_VERSION_KEY] == _release_version()
        assert {row[_VERSION_KEY] for row in rows} == {_release_version(), "9.8.7"}
        output = case_root / "output"
        try:
            _run_builder(builder, capture, output, monkeypatch)
        except SystemExit as exc:
            refused.append((builder.stem, "exit", exc.code))
        except RuntimeError as exc:
            refused.append((builder.stem, "error", str(exc)))
        finally:
            if list(output.iterdir()):
                emitted.append(builder.stem)
            monkeypatch.undo()

    assert [name for name, _kind, _error in refused] == [
        path.stem for path in _BUILDERS
    ]
    assert all(
        kind == "error" and "UNKNOWN: capture release identity mismatch" in error
        for _name, kind, error in refused
    )
    assert emitted == []


def test_builders_refuse_incomplete_population_before_emitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = []
    emitted = []
    for builder in _BUILDERS:
        case_root = tmp_path / builder.stem
        capture = _capture_fixture(case_root, identity=_release_version())
        manifest_path = capture / "manifest.json"
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        removed = rows.pop()
        manifest_path.write_text(json.dumps(rows), encoding="utf-8")

        contract = _manifest_contract_module()
        assert len(rows) == 103
        assert (removed["cat"], removed["route"], removed["theme"]) not in {
            (row["cat"], row["route"], row["theme"])
            for row in rows
        }
        with pytest.raises(contract.ManifestContractError, match="missing 1 expected row"):
            contract.validate_manifest(rows)

        output = case_root / "output"
        try:
            _run_builder(builder, capture, output, monkeypatch)
        except SystemExit as exc:
            refused.append((builder.stem, "exit", exc.code))
        except RuntimeError as exc:
            refused.append((builder.stem, "error", str(exc)))
        finally:
            if list(output.iterdir()):
                emitted.append(builder.stem)
            monkeypatch.undo()

    assert len(refused) == 2
    assert refused[0] == ("build_navigator", "exit", 2)
    assert refused[1][:2] == ("build_montage", "error")
    assert "missing 1 expected row" in refused[1][2]
    assert emitted == []


@pytest.mark.parametrize("builder", _BUILDERS, ids=lambda path: path.stem)
def test_builders_validate_the_complete_manifest_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builder: Path,
) -> None:
    capture = _capture_fixture(tmp_path, identity=_release_version())
    manifest_path = capture / "manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) == 104 and rows[-1][_VERSION_KEY] == _release_version()
    rows[-1][_VERSION_KEY] = "9.8.7"
    manifest_path.write_text(json.dumps(rows), encoding="utf-8")

    output = tmp_path / f"output-{builder.stem}-mixed"
    with pytest.raises(RuntimeError, match="UNKNOWN: capture release identity mismatch"):
        _run_builder(builder, capture, output, monkeypatch)
    assert list(output.iterdir()) == []


def test_transform_control_imports_identity_helper_without_judging_behavior() -> None:
    module = _identity_module()
    assert callable(module.validate_manifest_release)
