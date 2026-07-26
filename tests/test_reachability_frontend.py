"""Contract tests for the evidence-preserving reachability frontend."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from tools.code_intelligence.artifacts import artifact_hash
from tools.code_intelligence.reachability_service import (
    ProbeObservation,
    _call_paths,
    _load_deferrals,
    analyze_reachability,
    classify_route,
    run_reachability_cli,
    validate_reachability_artifact,
)
from tools.code_intelligence.results import ResultState
from tools.code_intelligence.schemas import SchemaError, make_envelope, validate_envelope


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "reachability.py"
HEX64 = "a" * 64


class _SpoofedSafeExceptionName(str):
    def __hash__(self) -> int:
        return hash("RuntimeError")

    def __eq__(self, other: object) -> bool:
        return other == "RuntimeError"


class _NeverUnequalExceptionName(str):
    def __ne__(self, other: object) -> bool:
        return False


def _obs(
    status: int | None,
    location: str | None = None,
    exception: str | None = None,
) -> ProbeObservation:
    return ProbeObservation(status, location, exception)


def test_public_route_requires_unauthenticated_success() -> None:
    row = classify_route(
        rule="/public",
        methods=("GET",),
        unauthenticated=_obs(200),
        authenticated=None,
        auth_gate_facts=(),
        operator_wiring="spa",
        navigation="linked",
        call_paths=(),
    )

    assert row["classification"] == "public"
    assert row["confidence"] == "high"
    assert row["evidence"]["operator_wiring"] == "spa"


def test_authenticated_route_requires_auth_delta() -> None:
    row = classify_route(
        rule="/private",
        methods=("GET",),
        unauthenticated=_obs(302, "/login"),
        authenticated=_obs(200),
        auth_gate_facts=("login_required",),
        operator_wiring="spa",
        navigation="linked",
        call_paths=(),
    )

    assert row["classification"] == "authenticated"
    assert row["confidence"] == "high"


def test_internal_is_not_inferred_from_dark_operator_wiring_alone() -> None:
    row = classify_route(
        rule="/api/internal",
        methods=("POST",),
        unauthenticated=_obs(403),
        authenticated=_obs(403),
        auth_gate_facts=(),
        operator_wiring="dark",
        navigation=None,
        call_paths=(),
    )

    assert row["classification"] == "unknown"
    assert row["evidence"]["operator_wiring"] == "dark"


def test_probe_exception_is_unknown() -> None:
    row = classify_route(
        rule="/broken",
        methods=("GET",),
        unauthenticated=_obs(None, exception="RuntimeError"),
        authenticated=None,
        auth_gate_facts=(),
        operator_wiring=None,
        navigation=None,
        call_paths=(),
    )

    assert row["classification"] == "unknown"
    assert row["confidence"] == "low"


def test_call_paths_never_exceed_artifact_node_limit() -> None:
    nodes = ["fixture.py::endpoint", *(f"fixture.py::step_{index}" for index in range(1, 10))]
    call_graph = {
        "nodes": nodes,
        "edges": [
            {"from": source, "to": target}
            for source, target in zip(nodes[:-1], nodes[1:], strict=True)
        ],
    }

    paths = _call_paths(call_graph, "endpoint")

    assert max(map(len, paths)) == 8
    assert all(len(path) <= 8 for path in paths)


def test_duplicate_normalized_deferrals_are_emitted_once(tmp_path: Path) -> None:
    source = tmp_path / "deferrals.json"
    source.write_text(
        json.dumps(
            {
                "deferrals": {
                    "D-1": {"finding": "same", "status": "deferred"},
                    "D-2": {"finding": "same", "status": "deferred"},
                }
            }
        ),
        encoding="utf-8",
    )

    _loaded, summaries = _load_deferrals(source)

    assert summaries == [{"finding": "same", "status": "deferred"}]


def test_cli_help_is_lazy_and_portable_outside_repository(tmp_path: Path) -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0
    for option in (
        "--app",
        "--authenticated-fixture",
        "--security-surface",
        "--call-graph",
        "--deferrals",
        "--timeout",
        "--out",
        "--check",
        "--gate",
        "--json",
    ):
        assert option in run.stdout
    assert run.stderr == ""
    assert "bulk_downloader.app" not in sys.modules


def _projection(path: Path, name: str, **fields: object) -> None:
    payload = {
        **make_envelope(name, 1, HEX64, "fixture-1", {"fixture": HEX64}),
        **fields,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    security = tmp_path / "SECURITY_SURFACE.json"
    call_graph = tmp_path / "CALL_GRAPH.json"
    _projection(
        security,
        "security_surface",
        auth_gates=[
            {
                "name": "private",
                "at": "reachability_fixture_app.py:1-2",
                "decorators": ["login_required"],
                "path": "reachability_fixture_app.py",
                "function": "reachability_fixture_app.py::private",
                "method": "name_substring",
                "confidence": 0.6,
                "reason": "auth_decorator_name",
                "source": {"decorators": ["login_required"]},
            },
            {
                "name": "internal",
                "at": "reachability_fixture_app.py:3-4",
                "decorators": ["admin_only"],
                "path": "reachability_fixture_app.py",
                "function": "reachability_fixture_app.py::internal",
                "method": "name_substring",
                "confidence": 0.6,
                "reason": "auth_decorator_name",
                "source": {"decorators": ["admin_only"]},
            },
        ],
        secret_sites=[],
        sql_sites=[],
        subprocess_sites=[],
        path_sinks=[],
        totals={},
    )
    _projection(
        call_graph,
        "call_graph",
        nodes=[
            "reachability_fixture_app.py::private",
            "reachability_fixture_app.py::helper",
        ],
        edges=[
            {
                "from": "reachability_fixture_app.py::private",
                "to": "reachability_fixture_app.py::helper",
                "kind": "route_handler",
                "reason": "exact_qualified",
                "confidence": 1.0,
            }
        ],
        unresolved=[],
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return security, call_graph


def _app_module(root: Path, source: str) -> str:
    module = root / "reachability_fixture_app.py"
    module.write_text(source, encoding="utf-8")
    return "reachability_fixture_app:app"


def _analyze(
    tmp_path: Path,
    *,
    app_target: str,
    authenticated_fixture: str | None = None,
    timeout: float = 5.0,
):
    security, call_graph = _inputs(tmp_path)
    deferrals = tmp_path / "REACHABILITY_DEFERRALS.json"
    deferrals.write_text(
        json.dumps(
            {
                "deferrals": {
                    "D-1": {
                        "rule": "/private",
                        "status": "deferred",
                        "finding": "D-1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return analyze_reachability(
        app_target=app_target,
        repo_root=tmp_path,
        security_surface_path=security,
        call_graph_path=call_graph,
        deferrals_path=deferrals,
        authenticated_fixture=authenticated_fixture,
        timeout_seconds=timeout,
    )


def test_analyze_loads_app_and_auth_fixture_only_in_bounded_child(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "imported.marker"
    target = _app_module(
        tmp_path,
        f"""\
from pathlib import Path
from flask import Flask, redirect, request
Path({str(marker)!r}).write_text("child", encoding="utf-8")
app = Flask(__name__, static_folder=None)

@app.get("/public")
def public():
    return "ok"

@app.get("/private")
def private():
    return ("ok", 200) if request.headers.get("X-Auth") == "yes" else ("no", 401)

@app.post("/internal")
def internal():
    return ("no", 403)

@app.get("/gone")
def gone():
    return ("gone", 404)

@app.get("/redirect")
def redirected():
    return redirect("https://user:password@example.test/login?token=raw-secret")

@app.get("/magic")
def magic():
    return redirect("/magic-login/path-secret-value")

def authenticated(client):
    client.environ_base["HTTP_X_AUTH"] = "yes"
    return client
""",
    )

    assert not marker.exists()
    assert "reachability_fixture_app" not in sys.modules
    result, artifact = _analyze(
        tmp_path,
        app_target=target,
        authenticated_fixture="reachability_fixture_app:authenticated",
    )

    assert marker.read_text(encoding="utf-8") == "child"
    assert "reachability_fixture_app" not in sys.modules
    assert result.state is ResultState.ADVISORY
    validate_envelope(artifact, "bd.reachability")
    assert set(artifact["input_hashes"]) == {
        "app_source",
        "authenticated_fixture_source",
        "call_graph",
        "deferrals",
        "security_surface",
        "tracked_tree",
    }
    assert artifact["source_sha"] != HEX64
    rows = {
        (row["rule"], tuple(row["methods"])): row
        for row in artifact["routes"]
    }
    assert rows[("/public", ("GET",))]["classification"] == "public"
    assert rows[("/private", ("GET",))]["classification"] == "authenticated"
    assert rows[("/internal", ("POST",))]["classification"] == "unknown"
    assert rows[("/gone", ("GET",))]["classification"] == "unknown"
    redirect_location = rows[("/redirect", ("GET",))]["evidence"]["auth_probe"][
        "unauthenticated"
    ]["location"]
    assert redirect_location == "/login"
    magic_location = rows[("/magic", ("GET",))]["evidence"]["auth_probe"][
        "unauthenticated"
    ]["location"]
    assert magic_location == "/<redacted>"
    private_evidence = rows[("/private", ("GET",))]["evidence"]
    assert private_evidence["auth_gate_facts"] == [
        "reachability_fixture_app.py::private:name_substring"
    ]
    assert private_evidence["call_paths"] == [
        [
            "reachability_fixture_app.py::private",
            "reachability_fixture_app.py::helper",
        ]
    ]
    assert private_evidence["deferrals"] == []
    assert artifact["adapter_status"]["deferrals"] == "available_unmapped"
    assert artifact["global_evidence"]["deferrals"] == [
        {"finding": "D-1", "status": "deferred"}
    ]
    assert artifact["summary"]["deferrals"] == 1
    rendered = json.dumps(artifact, sort_keys=True)
    assert "password" not in rendered
    assert "raw-secret" not in rendered
    assert "path-secret-value" not in rendered


@pytest.mark.parametrize(
    ("target", "summary"),
    [
        ("missing-colon", "app target invalid"),
        ("module:", "app target invalid"),
        (":attribute", "app target invalid"),
    ],
)
def test_malformed_app_target_is_a_controlled_error(
    tmp_path: Path, target: str, summary: str
) -> None:
    result, artifact = _analyze(tmp_path, app_target=target)

    assert result.state is ResultState.ERROR
    assert result.summary == summary
    assert artifact == {}


def test_invalid_projection_is_rejected_before_app_import(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-import"
    target = _app_module(
        tmp_path,
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\napp = object()\n",
    )
    security, call_graph = _inputs(tmp_path)
    security.write_text('{"schema_name":"security_surface"}', encoding="utf-8")

    result, artifact = analyze_reachability(
        app_target=target,
        repo_root=tmp_path,
        security_surface_path=security,
        call_graph_path=call_graph,
        deferrals_path=None,
        authenticated_fixture=None,
        timeout_seconds=1.0,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "security surface invalid"
    assert result.evidence == {"stage": "security_surface"}
    assert artifact == {}
    assert not marker.exists()


def test_timeout_and_child_crash_are_explicit_non_pass_states(
    tmp_path: Path,
) -> None:
    slow = _app_module(
        tmp_path,
        "import time\ntime.sleep(10)\napp = object()\n",
    )
    timed, timed_artifact = _analyze(
        tmp_path,
        app_target=slow,
        timeout=0.05,
    )
    assert timed.state is ResultState.TIMEOUT
    assert timed.summary == "probe exceeded timeout"
    assert timed_artifact == {}

    crash_dir = tmp_path / "crash"
    crash_dir.mkdir()
    crash = _app_module(
        crash_dir,
        "import os\nos._exit(17)\n",
    )
    crashed, crashed_artifact = _analyze(crash_dir, app_target=crash)
    assert crashed.state is ResultState.ERROR
    assert crashed.summary == "probe child failed"
    assert crashed_artifact == {}


def _cli_args(
    tmp_path: Path,
    *,
    app: str,
    check: Path | None = None,
    gate: bool = False,
) -> argparse.Namespace:
    security, call_graph = _inputs(tmp_path)
    return argparse.Namespace(
        app=app,
        authenticated_fixture=None,
        security_surface=security,
        call_graph=call_graph,
        deferrals=None,
        timeout=5.0,
        out=tmp_path / "REACHABILITY.json",
        check=check,
        gate=gate,
        json=False,
    )


def test_cli_atomically_writes_checks_and_gates_only_privilege_unknowns(
    tmp_path: Path,
) -> None:
    target = _app_module(
        tmp_path,
        """\
from flask import Flask
app = Flask(__name__, static_folder=None)
@app.get("/private")
def private():
    return ("no", 401)
""",
    )
    args = _cli_args(tmp_path, app=target)
    advisory = run_reachability_cli(args=args, repo_root=tmp_path)
    payload = json.loads(args.out.read_text(encoding="utf-8"))

    assert advisory.state is ResultState.ADVISORY
    assert payload["summary"]["privilege_unknown"] == 1
    check = tmp_path / "expected.json"
    check.write_bytes(args.out.read_bytes())
    matching_args = argparse.Namespace(**vars(args))
    matching_args.check = check
    matching = run_reachability_cli(args=matching_args, repo_root=tmp_path)
    assert matching.state is ResultState.ADVISORY
    assert artifact_hash(
        json.loads(args.out.read_text(encoding="utf-8"))
    ) == artifact_hash(json.loads(check.read_text(encoding="utf-8")))

    gated_args = argparse.Namespace(**vars(args))
    gated_args.gate = True
    gated = run_reachability_cli(args=gated_args, repo_root=tmp_path)
    assert gated.state is ResultState.FAIL
    assert gated.summary == "privilege reachability unknown"


def test_malformed_check_preserves_existing_output(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    check = tmp_path / "bad-check.json"
    check.write_text('{"schema_name":"bd.reachability"}', encoding="utf-8")
    args = _cli_args(tmp_path, app=target, check=check)
    args.out.write_text('{"preserved":true}\n', encoding="utf-8")

    result = run_reachability_cli(args=args, repo_root=tmp_path)

    assert result.state is ResultState.ERROR
    assert result.summary == "reachability check invalid"
    assert args.out.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_output_alias_to_projection_is_rejected(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    args = _cli_args(tmp_path, app=target)
    args.out = args.security_surface
    original = args.security_surface.read_bytes()

    result = run_reachability_cli(args=args, repo_root=tmp_path)

    assert result.state is ResultState.ERROR
    assert result.summary == "reachability artifact path invalid"
    assert args.security_surface.read_bytes() == original


def test_broken_output_symlink_is_rejected_without_replacement(
    tmp_path: Path,
) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    args = _cli_args(tmp_path, app=target)
    destination = tmp_path / "missing-target.json"
    try:
        args.out.symlink_to(destination)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    result = run_reachability_cli(args=args, repo_root=tmp_path)

    assert result.state is ResultState.ERROR
    assert result.summary == "reachability artifact path invalid"
    assert args.out.is_symlink()
    assert not destination.exists()


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_name":"security_surface","schema_name":"duplicate"}',
        '{"schema_name":"security_surface","value":NaN}',
        "[]",
    ],
)
def test_malformed_duplicate_and_nonfinite_projections_are_controlled(
    tmp_path: Path,
    raw: str,
) -> None:
    marker = tmp_path / "must-not-import"
    target = _app_module(
        tmp_path,
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\napp = object()\n",
    )
    security, call_graph = _inputs(tmp_path)
    security.write_text(raw, encoding="utf-8")

    result, artifact = analyze_reachability(
        app_target=target,
        repo_root=tmp_path,
        security_surface_path=security,
        call_graph_path=call_graph,
        deferrals_path=None,
        authenticated_fixture=None,
        timeout_seconds=1.0,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "security surface invalid"
    assert artifact == {}
    assert not marker.exists()
    rendered = json.dumps(asdict(result), sort_keys=True)
    assert "duplicate" not in rendered
    assert "NaN" not in rendered


def test_projection_symlink_is_rejected_before_target_read(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "raise RuntimeError('must not import')\n",
    )
    security, call_graph = _inputs(tmp_path)
    linked = tmp_path / "linked-security.json"
    try:
        linked.symlink_to(security)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    result, artifact = analyze_reachability(
        app_target=target,
        repo_root=tmp_path,
        security_surface_path=linked,
        call_graph_path=call_graph,
        deferrals_path=None,
        authenticated_fixture=None,
        timeout_seconds=1.0,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "security surface invalid"
    assert artifact == {}


def test_mutating_and_parameterized_routes_are_enumerated_not_executed(
    tmp_path: Path,
) -> None:
    mutation_marker = tmp_path / "mutation.marker"
    parameter_marker = tmp_path / "parameter.marker"
    target = _app_module(
        tmp_path,
        f"""\
from pathlib import Path
from flask import Flask
app = Flask(__name__, static_folder=None)
@app.post("/mutate")
def mutate():
    Path({str(mutation_marker)!r}).touch()
    return "changed"
@app.get("/item/<item_id>")
def item(item_id):
    Path({str(parameter_marker)!r}).touch()
    return item_id
""",
    )

    result, artifact = _analyze(tmp_path, app_target=target)
    rows = {row["rule"]: row for row in artifact["routes"]}

    assert result.state is ResultState.ADVISORY
    assert rows["/mutate"]["evidence"]["auth_probe"]["unauthenticated"][
        "exception"
    ] == "UnsafeMethodNotProbed"
    assert rows["/item/<item_id>"]["evidence"]["auth_probe"][
        "unauthenticated"
    ]["exception"] == "RouteParametersUnresolved"
    assert not mutation_marker.exists()
    assert not parameter_marker.exists()


def test_probe_exception_messages_and_response_bodies_are_never_stored(
    tmp_path: Path,
) -> None:
    secret = "Bearer-ultra-private-value"
    target = _app_module(
        tmp_path,
        f"""\
from flask import Flask
app = Flask(__name__, static_folder=None)
app.testing = True
@app.get("/broken")
def broken():
    raise RuntimeError({secret!r})
@app.get("/body")
def body():
    return {secret!r}
""",
    )

    result, artifact = _analyze(tmp_path, app_target=target)
    rendered = json.dumps(
        {"result": asdict(result), "artifact": artifact},
        sort_keys=True,
        default=lambda value: value.value,
    )

    assert result.state is ResultState.ADVISORY
    assert secret not in rendered
    broken = next(
        row for row in artifact["routes"] if row["rule"] == "/broken"
    )
    assert broken["evidence"]["auth_probe"]["unauthenticated"] == {
        "status": None,
        "location": None,
        "exception": "RuntimeError",
    }


def test_sensitive_custom_route_exception_name_is_sanitized(
    tmp_path: Path,
) -> None:
    sensitive_name = "Bearer_ultra_private_value"
    target = _app_module(
        tmp_path,
        f"""\
from flask import Flask
app = Flask(__name__, static_folder=None)
app.testing = True
class {sensitive_name}(Exception):
    pass
@app.get("/broken")
def broken():
    raise {sensitive_name}()
""",
    )

    result, artifact = _analyze(tmp_path, app_target=target)
    rendered = json.dumps(
        {"result": asdict(result), "artifact": artifact},
        sort_keys=True,
        default=lambda value: value.value,
    )

    assert result.state is ResultState.ADVISORY
    broken = next(
        row for row in artifact["routes"] if row["rule"] == "/broken"
    )
    assert broken["evidence"]["auth_probe"]["unauthenticated"] == {
        "status": None,
        "location": None,
        "exception": "ProbeError",
    }
    assert sensitive_name not in rendered


def test_spoofed_str_subclass_exception_name_is_canonicalized(
    tmp_path: Path,
) -> None:
    sensitive_name = "Bearer_ultra_private_value"
    target = _app_module(
        tmp_path,
        f"""\
from flask import Flask
from {__name__} import _SpoofedSafeExceptionName
app = Flask(__name__, static_folder=None)
app.testing = True
spoofed_name = _SpoofedSafeExceptionName({sensitive_name!r})
class CustomError(Exception):
    pass
CustomError.__name__ = spoofed_name
assert type(CustomError()).__name__ is spoofed_name
@app.get("/broken")
def broken():
    raise CustomError()
""",
    )

    result, artifact = _analyze(tmp_path, app_target=target)
    broken = next(
        row for row in artifact["routes"] if row["rule"] == "/broken"
    )
    exception = broken["evidence"]["auth_probe"]["unauthenticated"][
        "exception"
    ]
    rendered = json.dumps(
        {"result": asdict(result), "artifact": artifact},
        sort_keys=True,
        default=lambda value: value.value,
    )

    assert result.state is ResultState.ADVISORY
    assert type(exception) is str
    assert exception == "ProbeError"
    assert sensitive_name not in rendered


def test_sensitive_custom_child_exception_name_is_sanitized(
    tmp_path: Path,
) -> None:
    sensitive_name = "Bearer_ultra_private_value"
    target = _app_module(
        tmp_path,
        f"""\
class {sensitive_name}(Exception):
    pass
raise {sensitive_name}()
""",
    )

    result, artifact = _analyze(tmp_path, app_target=target)
    rendered = json.dumps(asdict(result), sort_keys=True, default=str)

    assert result.state is ResultState.ERROR
    assert result.summary == "probe child failed"
    assert result.evidence == {
        "stage": "probe_child",
        "exception": "ProbeError",
    }
    assert artifact == {}
    assert sensitive_name not in rendered


def test_strict_check_rejects_unknown_fields(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    args = _cli_args(tmp_path, app=target)
    first = run_reachability_cli(args=args, repo_root=tmp_path)
    assert first.state is ResultState.ADVISORY
    payload = json.loads(args.out.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    check = tmp_path / "strict-check.json"
    check.write_text(json.dumps(payload), encoding="utf-8")
    args.check = check
    args.out.write_text('{"preserved":true}\n', encoding="utf-8")

    result = run_reachability_cli(args=args, repo_root=tmp_path)

    assert result.state is ResultState.ERROR
    assert result.summary == "reachability check invalid"
    assert args.out.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_app_target_must_resolve_to_source_inside_repository(
    tmp_path: Path,
) -> None:
    result, artifact = _analyze(
        tmp_path,
        app_target="installed_external_module:app",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "app target invalid"
    assert result.evidence == {"stage": "app_target"}
    assert artifact == {}


def test_output_cannot_alias_target_source(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    args = _cli_args(tmp_path, app=target)
    source = tmp_path / "reachability_fixture_app.py"
    args.out = source
    original = source.read_bytes()

    result = run_reachability_cli(args=args, repo_root=tmp_path)

    assert result.state is ResultState.ERROR
    assert result.summary == "reachability artifact path invalid"
    assert source.read_bytes() == original


def test_artifact_requires_exact_source_input_hash_keys(tmp_path: Path) -> None:
    target = _app_module(
        tmp_path,
        "from flask import Flask\napp = Flask(__name__, static_folder=None)\n",
    )
    result, artifact = _analyze(tmp_path, app_target=target)
    assert result.state is ResultState.ADVISORY
    artifact["input_hashes"].pop("app_source")

    with pytest.raises(SchemaError, match="input hash"):
        validate_reachability_artifact(artifact)


def test_strict_artifact_rejects_spoofed_exception_str_subclass(
    tmp_path: Path,
) -> None:
    target = _app_module(
        tmp_path,
        """\
from flask import Flask
app = Flask(__name__, static_folder=None)
@app.get("/public")
def public():
    return "ok"
""",
    )
    result, artifact = _analyze(tmp_path, app_target=target)
    assert result.state is ResultState.ADVISORY
    sensitive_name = "Bearer_ultra_private_value"
    artifact["routes"][0]["evidence"]["auth_probe"]["unauthenticated"][
        "exception"
    ] = _NeverUnequalExceptionName(sensitive_name)
    assert sensitive_name in json.dumps(artifact)

    with pytest.raises(SchemaError, match="observation"):
        validate_reachability_artifact(artifact)


def test_projection_larger_than_resource_budget_is_rejected(
    tmp_path: Path,
) -> None:
    target = _app_module(
        tmp_path,
        "raise RuntimeError('must not import')\n",
    )
    security, call_graph = _inputs(tmp_path)
    security.write_bytes(b" " * (16 * 1024 * 1024 + 1))

    result, artifact = analyze_reachability(
        app_target=target,
        repo_root=tmp_path,
        security_surface_path=security,
        call_graph_path=call_graph,
        deferrals_path=None,
        authenticated_fixture=None,
        timeout_seconds=1.0,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "security surface invalid"
    assert artifact == {}
