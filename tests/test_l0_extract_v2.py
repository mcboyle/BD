"""Focused regression coverage for the schema-2 L0 extractor."""

from __future__ import annotations

import importlib
import json
import ntpath
import os
import posixpath
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools import l0_extract


ROOT = Path(__file__).resolve().parent.parent


SOURCE = '''
@login_required
def sample(a: int, /, b="x", *items, enabled: bool = False, **opts) -> dict:
    current = app.config.get("LIMIT")
    app.config["LIMIT"] = b
    with state_lock:
        metrics.increment("sample.calls")
    if enabled:
        raise ValueError("bad")
    return {"value": a}
'''


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path: Path, source: str = SOURCE) -> Path:
    root = tmp_path / "repo"
    module = root / "bulk_downloader" / "sample.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "bulk_downloader/sample.py", "README.md")
    return root


def build_fixture_graph(tmp_path: Path, source: str = SOURCE) -> Path:
    root = _make_repo(tmp_path, source)
    db = tmp_path / "KNOWLEDGE_GRAPH.db"
    l0_extract.build_db(root, db)
    return db


def function_meta(db: Path, qualname: str) -> dict[str, object]:
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT meta_json FROM nodes "
            "WHERE kind = 'function' AND qualname = ? ORDER BY id",
            (qualname,),
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


def graph_meta(db: Path) -> dict[str, str]:
    with sqlite3.connect(db) as connection:
        return dict(connection.execute("SELECT k, v FROM meta"))


def direct_fields(
    records: list[dict[str, object]], *fields: str
) -> list[dict[str, object]]:
    return [
        {field: record[field] for field in fields}
        for record in records
    ]


def test_l0_records_signature_contract_config_lock_metric_and_auth(tmp_path):
    db = build_fixture_graph(tmp_path, SOURCE)
    meta = function_meta(db, "sample")

    assert [p["kind"] for p in meta["parameters"]] == [
        "positional_only",
        "positional_or_keyword",
        "var_positional",
        "keyword_only",
        "var_keyword",
    ]
    assert meta["parameters"] == [
        {
            "name": "a",
            "kind": "positional_only",
            "default": None,
            "annotation": "int",
        },
        {
            "name": "b",
            "kind": "positional_or_keyword",
            "default": '"x"',
            "annotation": None,
        },
        {
            "name": "items",
            "kind": "var_positional",
            "default": None,
            "annotation": None,
        },
        {
            "name": "enabled",
            "kind": "keyword_only",
            "default": "False",
            "annotation": "bool",
        },
        {
            "name": "opts",
            "kind": "var_keyword",
            "default": None,
            "annotation": None,
        },
    ]
    assert meta["args"] == ["a", "b", "items", "enabled", "opts"]
    assert meta["has_kwargs"] is True
    assert meta["returns"]["annotation"] == "dict"
    assert meta["returns"]["has_value"] is True
    assert meta["returns"]["has_none"] is False
    assert meta["returns"]["has_bare"] is False
    assert meta["returns"]["shapes"] == ["dict"]
    assert meta["raises"] == ["ValueError"]
    assert meta["decorators"] == ["login_required"]
    assert meta["auth_calls"] == []
    assert meta["config_reads"] == [{"key": "LIMIT", "at": 4}]
    assert meta["config_writes"] == [{"key": "LIMIT", "at": 5}]
    assert direct_fields(
        meta["concurrency_ops"], "kind", "name", "operation", "at"
    ) == [{
        "kind": "lock",
        "name": "state_lock",
        "operation": "context",
        "at": 6,
    }]
    assert direct_fields(
        meta["metric_emits"], "name", "operation", "at"
    ) == [{
        "name": "sample.calls",
        "operation": "increment",
        "at": 7,
    }]


def test_return_shapes_raise_names_and_manual_auth_calls_are_normalized(tmp_path):
    source = '''\
def varied(
    value: list[int] | None,
    fallback="Bearer raw-default-secret",
) -> tuple[int, dict] | None:
    authorize_request("raw-secret-must-not-be-stored")
    if value is None:
        return None
    if not value:
        return
    if value[0] < 0:
        raise errors.DomainError("sensitive body")
    if value[0] == 0:
        return (0, {})
    return build_result(value)
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "varied")

    assert meta["parameters"][1]["default"] == "<redacted>"
    assert meta["returns"] == {
        "annotation": "tuple[int, dict] | None",
        "has_value": True,
        "has_none": True,
        "has_bare": True,
        "shapes": ["call", "tuple"],
    }
    assert meta["raises"] == ["errors.DomainError"]
    assert direct_fields(meta["auth_calls"], "name", "at") == [{
        "name": "authorize_request",
        "at": 5,
    }]
    assert meta["calls"] == [
        {"name": "authorize_request", "at": 5},
        {"name": "errors.DomainError", "at": 11},
        {"name": "build_result", "at": 14},
    ]
    serialized = json.dumps(meta, sort_keys=True)
    assert "raw-default-secret" not in serialized
    assert "raw-secret-must-not-be-stored" not in serialized
    assert "sensitive body" not in serialized


def test_config_environment_concurrency_and_metric_recognizers(tmp_path):
    source = '''\
async def surfaces(queue, worker_thread, child_process):
    token = os.environ.get("API_TOKEN", "literal-secret-value")
    timeout = settings.get("TIMEOUT")
    settings.set("TIMEOUT", 5)
    os.environ["MODE"] = "safe"
    lock.acquire()
    lock.release()
    threading.Thread(target=run)
    multiprocessing.Process(target=run)
    asyncio.create_task(run())
    queue.put("item")
    scheduler.add_job(run)
    statsd.incr("jobs.started")
    histogram.observe(3)
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "surfaces")

    assert meta["config_reads"] == [
        {"key": "API_TOKEN", "at": 2},
        {"key": "TIMEOUT", "at": 3},
    ]
    assert meta["config_writes"] == [
        {"key": "TIMEOUT", "at": 4},
        {"key": "MODE", "at": 5},
    ]
    assert {item["kind"] for item in meta["concurrency_ops"]} == {
        "async",
        "lock",
        "process",
        "queue",
        "scheduler",
        "thread",
    }
    assert direct_fields(
        meta["metric_emits"], "name", "operation", "at"
    ) == [
        {"name": "jobs.started", "operation": "incr", "at": 13},
        {"name": "histogram", "operation": "observe", "at": 14},
    ]
    serialized = json.dumps(meta, sort_keys=True)
    assert "literal-secret-value" not in serialized


def test_unresolved_call_details_are_lossless_and_line_sorted(tmp_path):
    source = '''\
def caller():
    zeta()
    service.alpha()
    zeta()
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "caller")

    assert meta["unresolved_calls"] == [
        {
            "from": "bulk_downloader/sample.py::caller",
            "name": "zeta",
            "at": 2,
        },
        {
            "from": "bulk_downloader/sample.py::caller",
            "name": "service.alpha",
            "at": 3,
        },
        {
            "from": "bulk_downloader/sample.py::caller",
            "name": "zeta",
            "at": 4,
        },
    ]
    with sqlite3.connect(db) as connection:
        edges = connection.execute(
            "SELECT dst, meta_json FROM edges "
            "WHERE kind = 'call' ORDER BY json_extract(meta_json, '$.at'), dst"
        ).fetchall()
    assert edges == [
        ("zeta", '{"at": 2}'),
        ("service.alpha", '{"at": 3}'),
        ("zeta", '{"at": 4}'),
    ]


def test_duplicate_qualnames_keep_distinct_nodes(tmp_path):
    source = '''\
if FLAG:
    def duplicate():
        return 1
else:
    def duplicate():
        return 2
'''
    db = build_fixture_graph(tmp_path, source)

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT id, qualname FROM nodes "
            "WHERE kind = 'function' ORDER BY id"
        ).fetchall()
    assert rows == [
        ("bulk_downloader/sample.py::duplicate", "duplicate"),
        ("bulk_downloader/sample.py::duplicate#5", "duplicate"),
    ]


def test_same_named_methods_include_their_class_scope(tmp_path):
    source = '''\
class A:
    def run(self):
        return "a"

class B:
    def run(self):
        return "b"
'''
    db = build_fixture_graph(tmp_path, source)

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT id, qualname FROM nodes "
            "WHERE kind = 'function' ORDER BY qualname"
        ).fetchall()
    assert rows == [
        ("bulk_downloader/sample.py::A.run", "A.run"),
        ("bulk_downloader/sample.py::B.run", "B.run"),
    ]


def test_methods_include_all_nested_class_scopes(tmp_path):
    source = '''\
class Outer:
    class Inner:
        def run(self):
            return "nested"
'''
    db = build_fixture_graph(tmp_path, source)

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT id, qualname FROM nodes WHERE kind = 'function'"
        ).fetchall()
    assert rows == [
        (
            "bulk_downloader/sample.py::Outer.Inner.run",
            "Outer.Inner.run",
        ),
    ]


def test_typescript_facts_remain_compatible(tmp_path):
    root = _make_repo(tmp_path, "def python_only():\n    return 1\n")
    frontend = root / "frontend" / "src" / "sample.tsx"
    frontend.parent.mkdir(parents=True)
    frontend.write_text(
        "export function View() { return fetch('/safe'); }\n"
        "export const TOKEN_LABEL = 'token';\n",
        encoding="utf-8",
    )
    _git(root, "add", "frontend/src/sample.tsx")
    db = tmp_path / "graph.db"

    l0_extract.build_db(root, db)

    with sqlite3.connect(db) as connection:
        raw = connection.execute(
            "SELECT meta_json FROM nodes WHERE id = ?",
            ("frontend/src/sample.tsx",),
        ).fetchone()[0]
    assert json.loads(raw) == {
        "exports": ["TOKEN_LABEL", "View"],
        "fetch_calls": 1,
        "secrets": ["TOKEN_LABEL"],
    }


def test_graph_metadata_binds_dirty_tracked_tree_and_production_inputs(tmp_path):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    l0_extract.build_db(root, first_db)
    first = graph_meta(first_db)

    (root / "README.md").write_text("dirty fixture\n", encoding="utf-8")
    l0_extract.build_db(root, second_db)
    second = graph_meta(second_db)

    assert first["schema"] == "2"
    assert first["schema_name"] == "knowledge_graph"
    assert first["schema_version"] == "2"
    assert len(first["source_sha"]) == 64
    assert first["source_sha"] != second["source_sha"]
    assert json.loads(first["input_hashes"]) == json.loads(second["input_hashes"])
    assert set(json.loads(first["input_hashes"])) == {
        "bulk_downloader/sample.py",
    }


def test_failed_atomic_replace_preserves_valid_prior_database(tmp_path, monkeypatch):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    db = tmp_path / "graph.db"
    l0_extract.build_db(root, db)
    before = db.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(l0_extract.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        l0_extract.build_db(root, db)

    assert db.read_bytes() == before
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert list(tmp_path.glob(".*graph.db*.tmp")) == []


def test_snapshot_validation_failure_preserves_valid_prior_database(tmp_path):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    db = tmp_path / "graph.db"
    l0_extract.build_db(root, db)
    before = db.read_bytes()
    outside = tmp_path / "outside.py"
    outside.write_text("def escaped():\n    return 1\n", encoding="utf-8")
    link = root / "bulk_downloader" / "escape.py"
    link.symlink_to(outside)
    _git(root, "add", "bulk_downloader/escape.py")

    with pytest.raises(ValueError, match="outside repository"):
        l0_extract.build_db(root, db)

    assert db.read_bytes() == before


def test_cli_defaults_are_derived_from_discovered_repository_root(
    tmp_path, monkeypatch
):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    monkeypatch.chdir(root / "bulk_downloader")
    captured = {}

    def fake_build_db(actual_root, actual_db):
        captured["root"] = Path(actual_root)
        captured["db"] = Path(actual_db)
        return {"files": 0}

    monkeypatch.setattr(l0_extract, "build_db", fake_build_db)

    assert l0_extract.main([]) == 0
    assert captured == {
        "root": root.resolve(),
        "db": root.resolve() / "artifacts" / "KNOWLEDGE_GRAPH.db",
    }


def test_snapshot_import_is_lazy_and_l0_can_import_after_snapshot():
    script = """\
import importlib
import sys

for name in ("tools.l0_extract", "tools.code_intelligence.snapshot"):
    sys.modules.pop(name, None)
snapshot = importlib.import_module("tools.code_intelligence.snapshot")
assert "tools.l0_extract" not in sys.modules
l0_extract = importlib.import_module("tools.l0_extract")
importlib.reload(l0_extract)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_annotations_redact_sensitive_string_and_bytes_literals(tmp_path):
    source = '''\
from typing import Annotated, Literal

def annotated(
    value: Literal["Bearer annotation-token", "safe"],
    metadata: Annotated[int, "password=annotation-secret"],
    payload: Literal[b"private-key-bytes"],
    forward: "Annotated[SafeType, 'api_token=forward-secret']",
) -> Annotated[Literal["cookie=return-secret"], "safe"]:
    return None
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "annotated")
    annotations = [
        parameter["annotation"] for parameter in meta["parameters"]
    ]

    assert annotations[0] == "Literal['<redacted>', 'safe']"
    assert annotations[1] == "Annotated[int, '<redacted>']"
    assert annotations[2] == "Literal[b'<redacted>']"
    assert "SafeType" in annotations[3]
    assert "<redacted>" in annotations[3]
    assert meta["returns"]["annotation"] == (
        "Annotated[Literal['<redacted>'], 'safe']"
    )
    serialized = json.dumps(meta, sort_keys=True)
    for secret in (
        "annotation-token",
        "annotation-secret",
        "private-key-bytes",
        "forward-secret",
        "return-secret",
    ):
        assert secret not in serialized


def test_secret_named_defaults_preserve_safe_non_string_constants(tmp_path):
    source = '''\
def defaults(
    api_token=None,
    password=False,
    otp=7,
    credential=3.5,
    secret_blob=b"raw-secret-bytes",
    token_text="opaque",
):
    return None
'''
    db = build_fixture_graph(tmp_path, source)
    parameters = function_meta(db, "defaults")["parameters"]

    assert [parameter["default"] for parameter in parameters] == [
        "None",
        "False",
        "7",
        "3.5",
        "<redacted>",
        "<redacted>",
    ]
    assert "raw-secret-bytes" not in json.dumps(parameters, sort_keys=True)


def test_config_augassign_reads_and_writes_and_update_keywords_are_writes(
    tmp_path,
):
    source = '''\
def update_config():
    app.config["LIMIT"] += 1
    config.update(TIMEOUT=5, MODE="safe")
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "update_config")

    assert meta["config_reads"] == [{"key": "LIMIT", "at": 2}]
    assert meta["config_writes"] == [
        {"key": "LIMIT", "at": 2},
        {"key": "MODE", "at": 3},
        {"key": "TIMEOUT", "at": 3},
    ]


def test_heuristic_auth_concurrency_and_metrics_are_labeled(tmp_path):
    source = '''\
def labeled():
    authorize_request()
    with state_lock:
        metrics.increment("labeled.calls")
'''
    db = build_fixture_graph(tmp_path, source)
    meta = function_meta(db, "labeled")

    for field in ("auth_calls", "concurrency_ops", "metric_emits"):
        assert len(meta[field]) == 1
        assert meta[field][0]["method"] == "name_substring"
        confidence = meta[field][0]["confidence"]
        assert isinstance(confidence, (int, float))
        assert 0.0 < confidence < 1.0


def test_sink_classifications_are_labeled_without_changing_legacy_fields(
    tmp_path,
):
    source = '''\
def classified_sinks(user):
    subprocess.run(["echo"], shell=True)
    requests.get("https://example.invalid")
    os.path.join("root", "child")
    redact_value(user)
    cursor.execute("SELECT 1")
    query = f"SELECT * FROM records WHERE id = {user}"
'''
    db = build_fixture_graph(tmp_path, source)
    sinks = function_meta(db, "classified_sinks")["sinks"]

    assert [
        {
            key: value
            for key, value in sink.items()
            if key in {"kind", "at", "shell"}
        }
        for sink in sinks
    ] == [
        {"kind": "subprocess", "at": 2, "shell": True},
        {"kind": "fetch", "at": 3},
        {"kind": "path", "at": 4},
        {"kind": "redaction", "at": 5},
        {"kind": "sql", "at": 6},
        {"kind": "sql_fstring", "at": 7},
    ]
    for sink in sinks:
        assert sink["method"] == "name_substring"
        confidence = sink["confidence"]
        assert isinstance(confidence, (int, float))
        assert 0.0 < confidence < 1.0


def test_nested_declaration_calls_belong_to_enclosing_function_only(tmp_path):
    source = '''\
def outer():
    @authorize_request(factory())
    def inner(x=metrics.increment("decl.calls")):
        hidden_body()
    class Child(base_factory(), metaclass=meta_factory()):
        class_hidden()
    return inner
'''
    db = build_fixture_graph(tmp_path, source)
    outer = function_meta(db, "outer")
    inner = function_meta(db, "outer.inner")
    outer_names = [call["name"] for call in outer["calls"]]

    assert outer_names == [
        "authorize_request",
        "factory",
        "metrics.increment",
        "base_factory",
        "meta_factory",
    ]
    assert "hidden_body" not in outer_names
    assert "class_hidden" not in outer_names
    assert direct_fields(outer["auth_calls"], "name", "at") == [
        {"name": "authorize_request", "at": 2}
    ]
    assert direct_fields(
        outer["metric_emits"], "name", "operation", "at"
    ) == [
        {"name": "decl.calls", "operation": "increment", "at": 3}
    ]
    assert [call["name"] for call in inner["calls"]] == ["hidden_body"]


def test_lambda_default_calls_and_metrics_belong_to_enclosing_function(
    tmp_path,
):
    source = '''\
def outer():
    callback = lambda value=prepare(
        metrics.increment("lambda.defaults")
    ): hidden_lambda_body()
    return callback
'''
    db = build_fixture_graph(tmp_path, source)
    outer = function_meta(db, "outer")

    assert [call["name"] for call in outer["calls"]] == [
        "prepare",
        "metrics.increment",
    ]
    assert direct_fields(
        outer["metric_emits"], "name", "operation", "at"
    ) == [{
        "name": "lambda.defaults",
        "operation": "increment",
        "at": 3,
    }]
    assert "hidden_lambda_body" not in json.dumps(outer, sort_keys=True)


def test_repository_relative_paths_are_posix_on_windows_only():
    windows_path = l0_extract._repository_relative_path(
        r"C:\repo\bulk_downloader\literal\module.py",
        r"C:\repo",
        path_module=ntpath,
    )
    posix_path = l0_extract._repository_relative_path(
        "/repo/bulk_downloader/literal\\name.py",
        "/repo",
        path_module=posixpath,
    )

    assert windows_path == "bulk_downloader/literal/module.py"
    assert posix_path == "bulk_downloader/literal\\name.py"


def _copy_foundation_package(root: Path) -> None:
    destination = root / "tools" / "code_intelligence"
    shutil.copytree(
        ROOT / "tools" / "code_intelligence",
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _git(root, "add", "tools/code_intelligence")


def _standalone_l0(tmp_path: Path) -> Path:
    standalone = tmp_path / "standalone" / "l0_extract.py"
    standalone.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "l0_extract.py", standalone)
    return standalone


def _outside_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def test_git_root_loads_foundation_when_repo_is_outside_sys_path(tmp_path):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    _copy_foundation_package(root)
    standalone = _standalone_l0(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    db = tmp_path / "portable.db"

    result = subprocess.run(
        [
            sys.executable,
            str(standalone),
            "--root",
            str(root),
            "--db",
            str(db),
        ],
        cwd=outside,
        env=_outside_environment(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    metadata = graph_meta(db)
    from tools.code_intelligence.snapshot import build_snapshot

    assert metadata["source_binding"] == "tracked_tree"
    assert metadata["source_sha"] == build_snapshot(root).source_sha


def test_git_root_without_available_foundation_fails_instead_of_fallback(
    tmp_path,
):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    standalone = _standalone_l0(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    db = tmp_path / "must-not-exist.db"

    result = subprocess.run(
        [
            sys.executable,
            str(standalone),
            "--root",
            str(root),
            "--db",
            str(db),
        ],
        cwd=outside,
        env=_outside_environment(),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "foundation snapshot" in (result.stdout + result.stderr).lower()
    assert not db.exists()


def test_non_git_fallback_is_explicitly_marked(tmp_path):
    root = tmp_path / "legacy"
    module = root / "bulk_downloader" / "sample.py"
    module.parent.mkdir(parents=True)
    module.write_text("def answer():\n    return 42\n", encoding="utf-8")
    db = tmp_path / "legacy.db"

    l0_extract.build_db(root, db)

    assert graph_meta(db)["source_binding"] == "legacy_non_git_fallback"


def test_untracked_production_input_fails_and_preserves_prior_database(
    tmp_path,
):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    db = tmp_path / "graph.db"
    l0_extract.build_db(root, db)
    before = db.read_bytes()
    (root / "bulk_downloader" / "untracked.py").write_text(
        "def untracked():\n    return 1\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="untracked production input"):
        l0_extract.build_db(root, db)

    assert db.read_bytes() == before


def test_production_mutation_after_snapshot_preserves_prior_database(
    tmp_path, monkeypatch
):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    module = root / "bulk_downloader" / "sample.py"
    db = tmp_path / "graph.db"
    l0_extract.build_db(root, db)
    before = db.read_bytes()
    original = l0_extract._tracked_snapshot

    def snapshot_then_mutate(actual_root, files):
        snapshot = original(actual_root, files)
        module.write_text("def answer():\n    return 43\n", encoding="utf-8")
        return snapshot

    monkeypatch.setattr(l0_extract, "_tracked_snapshot", snapshot_then_mutate)

    with pytest.raises(RuntimeError, match="changed after snapshot"):
        l0_extract.build_db(root, db)

    assert db.read_bytes() == before


def test_production_mutation_after_parse_preserves_prior_database(
    tmp_path, monkeypatch
):
    root = _make_repo(tmp_path, "def answer():\n    return 42\n")
    module = root / "bulk_downloader" / "sample.py"
    db = tmp_path / "graph.db"
    l0_extract.build_db(root, db)
    before = db.read_bytes()
    original = l0_extract._populate_database

    def populate_then_mutate(actual_root, database, files, snapshot):
        stats = original(actual_root, database, files, snapshot)
        module.write_text("def answer():\n    return 44\n", encoding="utf-8")
        return stats

    monkeypatch.setattr(
        l0_extract, "_populate_database", populate_then_mutate
    )

    with pytest.raises(RuntimeError, match="source tree changed"):
        l0_extract.build_db(root, db)

    assert db.read_bytes() == before
