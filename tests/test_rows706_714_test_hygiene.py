"""Rows 706/714: DNS-free fail-closed tests and tolerant manifest readers."""
from __future__ import annotations

import ast
from collections import Counter
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from packaging.requirements import InvalidRequirement
import pytest

from tests import test_pytest_runtime_requirement as runtime_reader
from tests import test_row360_turnstile_bypass_is_installed as row360_reader
from tests import test_v3_66_24_phase4_ssrf_hardening as ssrf_tests
from tests import test_v3_66_653_dep_freshness as dep_reader
from tests import test_v3_66_896_requirements_compare_specifiers as compare_reader


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO / "requirements.txt"
_INVALID_SUFFIX = ".in" + "valid"

_READER_CALLS = {
    "tests/test_pytest_runtime_requirement.py::"
    "test_pytest_is_installed_by_core_requirements":
        runtime_reader.test_pytest_is_installed_by_core_requirements,
    "tests/test_row360_turnstile_bypass_is_installed.py::"
    "test_the_core_manifest_installs_the_turnstile_fetcher_extra":
        row360_reader.test_the_core_manifest_installs_the_turnstile_fetcher_extra,
    "tests/test_v3_66_653_dep_freshness.py::"
    "test_lxml_and_cssselect_are_declared_in_the_core_manifest":
        dep_reader.test_lxml_and_cssselect_are_declared_in_the_core_manifest,
    "tests/test_v3_66_896_requirements_compare_specifiers.py::"
    "test_a_manifest_of_every_installed_version_is_satisfied":
        compare_reader.test_a_manifest_of_every_installed_version_is_satisfied,
    "tests/test_v3_66_896_requirements_compare_specifiers.py::"
    "test_packaging_is_declared_not_merely_transitive":
        compare_reader.test_packaging_is_declared_not_merely_transitive,
}


def _tracked_test_sources(repo: Path) -> dict[str, str]:
    try:
        done = subprocess.run(
            ["git", "ls-files", "--", "tests/test*.py"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AssertionError(f"UNKNOWN: cannot enumerate tests: {exc}") from exc
    if done.returncode != 0:
        raise AssertionError(
            f"UNKNOWN: git could not enumerate tests: {done.stderr.strip()}")
    paths = done.stdout.splitlines()
    assert paths, "UNKNOWN: tracked test denominator is empty"
    sources = {}
    for rel in paths:
        try:
            sources[rel] = (repo / rel).read_text(encoding="utf-8")
        except OSError as exc:
            raise AssertionError(f"UNKNOWN: cannot read {rel}: {exc}") from exc
    return sources


def _test_functions(path: str, source: str):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AssertionError(f"UNKNOWN: cannot parse {path}: {exc}") from exc

    found = []

    class Visitor(ast.NodeVisitor):
        classes: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.classes.append(node.name)
            self.generic_visit(node)
            self.classes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name.startswith("test_"):
                qualname = "::".join((*self.classes, node.name))
                found.append((node, f"{path}::{qualname}"))
            else:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return found


def _manifest_reader_sites(repo: Path) -> tuple[str, ...]:
    readers = []
    for path, source in _tracked_test_sources(repo).items():
        for node, nodeid in _test_functions(path, source):
            strings = {
                item.value for item in ast.walk(node)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            attrs = {
                item.attr for item in ast.walk(node)
                if isinstance(item, ast.Attribute)
            }
            if "requirements.txt" in strings and {"read_text", "splitlines"} <= attrs:
                readers.append(nodeid)
    assert readers, "UNKNOWN: requirements reader denominator is empty"
    return tuple(sorted(readers))


def _dns_adjacent_sites(repo: Path) -> tuple[tuple[str, str], ...]:
    sites = []
    for path, source in _tracked_test_sources(repo).items():
        for node, nodeid in _test_functions(path, source):
            identifiers = {
                item.id for item in ast.walk(node) if isinstance(item, ast.Name)
            } | {
                item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)
            }
            adjacent = any(
                marker in name.lower()
                for name in identifiers
                for marker in ("resolv", "socket", "connect", "client")
            ) or "_is_safe_public_host" in identifiers
            if not adjacent:
                continue
            for item in ast.walk(node):
                if (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                    and _INVALID_SUFFIX in item.value
                    and "\n" not in item.value
                ):
                    sites.append((f"{path}:{item.lineno}:{nodeid}", nodeid))
    assert sites, "UNKNOWN: resolver-adjacent hostname denominator is empty"
    return tuple(sorted(sites))


def _install_dns_tripwire(tmp_path: Path) -> tuple[Path, Path]:
    sink = tmp_path / "dns.jsonl"
    hook = tmp_path / "sitecustomize.py"
    hook.write_text(
        """import json
import os
import socket

_real = socket.getaddrinfo
_suffix = ".in" + "valid"
_sink = os.environ["ROW706_DNS_SINK"]

def guarded(host, *args, **kwargs):
    if isinstance(host, str) and host.endswith(_suffix):
        nodeid = os.environ.get("PYTEST_CURRENT_TEST", "UNKNOWN").split(" (", 1)[0]
        with open(_sink, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"nodeid": nodeid, "host": host}) + "\\n")
        raise socket.gaierror(-2, "row706 DNS tripwire")
    return _real(host, *args, **kwargs)

socket.getaddrinfo = guarded
""",
        encoding="utf-8",
    )
    return hook, sink


def _tripwire_env(hook: Path, sink: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ROW706_DNS_SINK"] = str(sink)
    env["PYTHONPATH"] = os.pathsep.join((str(hook.parent), str(_REPO)))
    return env


def _manifest_with_inline_comments(text: str) -> str:
    lines = []
    comment_index = 0
    for raw in text.splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            separator = "\t" if comment_index % 2 else "  "
            kind = "tab" if separator == "\t" else "space"
            raw += f"{separator}# row714 {kind} inline comment # trailing prose"
            comment_index += 1
        lines.append(raw)
    return "\n".join(lines) + "\n"


def _replace_manifest_read(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    original = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.resolve() == _MANIFEST.resolve():
            return body
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_row706_fail_closed_probe_never_reaches_the_live_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    checks: list[str] = []
    resolves: list[object] = []
    real_check = ssrf_tests._is_safe_public_host

    def refuse(host: str, *args: object, **kwargs: object) -> object:
        calls.append(host)
        raise socket.gaierror(-2, "row706 resolver tripwire")

    def counted_check(host: str):
        checks.append(host)
        resolve = socket.getaddrinfo
        result = real_check(host)
        resolves.append(resolve)
        return result

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(ssrf_tests, "_is_safe_public_host", counted_check)
    ssrf_tests.TestIsSafePublicHost().test_unresolvable_hostname_blocked_fail_closed()

    assert checks == ["this-host-does-not-exist.invalid"]
    assert len(resolves) == 1
    resolve = resolves[0]
    assert resolve.call_count == 1, f"resolver call count drifted: expected 1, observed {resolve.call_count}"
    assert calls == [], f"fail-closed test reached resolver {len(calls)} time(s): {calls}"


def test_row706_every_resolver_adjacent_site_runs_under_the_dns_tripwire(
    tmp_path: Path,
) -> None:
    sites = _dns_adjacent_sites(_REPO)
    nodeids = tuple(sorted({nodeid for _, nodeid in sites}))
    assert len(sites) == 20 and len(nodeids) == 14, (
        "UNKNOWN: resolver-adjacent census drifted: "
        "expected sites=20 nodeids=14; "
        f"observed sites={len(sites)} nodeids={len(nodeids)}"
    )
    hook, sink = _install_dns_tripwire(tmp_path)
    env = _tripwire_env(hook, sink)

    control = subprocess.run(
        [sys.executable, "-c", "import socket; socket.getaddrinfo('probe.' + 'invalid', 443)"],
        cwd=_REPO,
        env={**env, "PYTEST_CURRENT_TEST": "row706-control (call)"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert control.returncode != 0
    control_rows = [] if not sink.exists() else [
        json.loads(line) for line in sink.read_text().splitlines()
    ]
    assert control_rows == [{"nodeid": "row706-control", "host": "probe.invalid"}]
    sink.unlink()

    run = subprocess.run(
        [sys.executable, "-m", "pytest", *nodeids, "-q", "-p", "no:randomly"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, (
        "UNKNOWN: resolver-adjacent test run was not measurable: "
        f"rc={run.returncode} stdout={run.stdout[-1000:]!r} stderr={run.stderr[-1000:]!r}"
    )
    rows = [] if not sink.exists() else [
        json.loads(line) for line in sink.read_text().splitlines()
    ]
    counts = Counter(row["nodeid"] for row in rows)
    report = [f"{identity}={counts[nodeid]}" for identity, nodeid in sites]
    assert len(report) == len(sites) > 0
    assert sum(counts.values()) == 0, "DNS attempt count per site: " + ", ".join(report)


def test_row706_narrowed_census_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sites = _dns_adjacent_sites(_REPO)
    nodeids = {nodeid for _, nodeid in sites}
    assert len(sites) == 20 and len(nodeids) == 14
    frequencies = Counter(nodeid for _, nodeid in sites)
    unique_nodeid = next(
        nodeid for nodeid, count in frequencies.items() if count == 1)
    narrowed = tuple(site for site in sites if site[1] != unique_nodeid)
    assert len(narrowed) == 19
    assert len({nodeid for _, nodeid in narrowed}) == 13
    monkeypatch.setattr(
        sys.modules[__name__], "_dns_adjacent_sites", lambda repo: narrowed)

    with pytest.raises(
        AssertionError,
        match=(
            "UNKNOWN: resolver-adjacent census drifted: "
            "expected sites=20 nodeids=14; observed sites=19 nodeids=13"
        ),
    ):
        test_row706_every_resolver_adjacent_site_runs_under_the_dns_tripwire(
            tmp_path)


def test_row706_gate_owns_the_resolver_call_count_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DoubleCountResolver:
        call_count = 2

        def __call__(self, *args: object, **kwargs: object) -> object:
            raise socket.gaierror(-2, "row706 resolver tripwire")

    double_resolver = DoubleCountResolver()

    def subject_without_a_private_witness(_self: object) -> None:
        with ssrf_tests.mock.patch("socket.getaddrinfo", new=double_resolver):
            ok, reason = ssrf_tests._is_safe_public_host(
                "this-host-does-not-exist" + _INVALID_SUFFIX)
        assert not ok
        assert "DNS" in reason or "resolution" in reason.lower()

    monkeypatch.setattr(
        ssrf_tests.TestIsSafePublicHost,
        "test_unresolvable_hostname_blocked_fail_closed",
        subject_without_a_private_witness,
    )

    with pytest.raises(
        AssertionError,
        match="resolver call count drifted: expected 1, observed 2",
    ):
        test_row706_fail_closed_probe_never_reaches_the_live_resolver(monkeypatch)
    assert double_resolver.call_count == 2


def test_row714_row360_reader_tolerates_an_inline_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _MANIFEST.read_text(encoding="utf-8")
    body = _manifest_with_inline_comments(original)
    assert body.count("# row714 space inline comment") > 0
    assert body.count("\t# row714 tab inline comment") > 0
    _replace_manifest_read(monkeypatch, body)

    row360_reader.test_the_core_manifest_installs_the_turnstile_fetcher_extra()


def test_row714_runtime_reader_tolerates_an_inline_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _MANIFEST.read_text(encoding="utf-8")
    body = _manifest_with_inline_comments(original)
    assert body.count("# row714 space inline comment") > 0
    assert body.count("\t# row714 tab inline comment") > 0
    _replace_manifest_read(monkeypatch, body)

    runtime_reader.test_pytest_is_installed_by_core_requirements()


def test_row714_every_tree_derived_manifest_reader_tolerates_inline_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readers = _manifest_reader_sites(_REPO)
    assert set(readers) == set(_READER_CALLS), (
        f"UNKNOWN: requirements reader execution map drifted: {readers}")
    body = _manifest_with_inline_comments(_MANIFEST.read_text(encoding="utf-8"))
    expected_comments = sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
    )
    space_comments = body.count("# row714 space inline comment")
    tab_comments = body.count("\t# row714 tab inline comment")
    assert space_comments > 0 and tab_comments > 0
    assert space_comments + tab_comments == expected_comments > 0
    _replace_manifest_read(monkeypatch, body)

    fired = 0
    for nodeid in readers:
        _READER_CALLS[nodeid]()
        fired += 1
    assert fired == len(readers) == 5


def test_tree_measurement_failures_are_unknown(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="UNKNOWN: git could not enumerate tests"):
        _tracked_test_sources(tmp_path)


def test_transform_control_only_imports_the_reader_modules() -> None:
    assert row360_reader.__name__.endswith("test_row360_turnstile_bypass_is_installed")
    assert runtime_reader.__name__.endswith("test_pytest_runtime_requirement")


@pytest.mark.parametrize(
    "reader",
    (
        row360_reader.test_the_core_manifest_installs_the_turnstile_fetcher_extra,
        runtime_reader.test_pytest_is_installed_by_core_requirements,
    ),
)
def test_row714_malformed_requirement_still_fails(
    monkeypatch: pytest.MonkeyPatch,
    reader,
) -> None:
    body = _MANIFEST.read_text(encoding="utf-8") + "not a requirement ???\n"
    _replace_manifest_read(monkeypatch, body)

    with pytest.raises(InvalidRequirement):
        reader()
