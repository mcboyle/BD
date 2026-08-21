"""Fast synthetic contracts for exact-state cut-quality permits.

These tests never run a product suite, contact a host, ask GitHub, build a
release, or mutate the checkout under test.  They exercise the shared parser
and the five canonical action boundaries with scratch repositories only.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


# This suite binds policy, every protected launcher, and repository identity;
# its safety subject is the tree rather than a single product module.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "toolchain" / "bin"
POLICY = ROOT / "toolchain" / "cut_quality_policy.json"
MODULE = BIN / "bd_cut_quality.py"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True,
    )
    return result.stdout.strip()


def _load_module():
    spec = importlib.util.spec_from_file_location("bd_cut_quality", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_script(name: str):
    path = BIN / name
    loader_name = "permit_test_" + name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(loader_name, path)
    if spec is None or spec.loader is None:
        loader = importlib.machinery.SourceFileLoader(loader_name, str(path))
        spec = importlib.util.spec_from_loader(loader_name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / "tests").mkdir(parents=True)
    (path / "bulk_downloader").mkdir()
    (path / "tests" / "test_tiny.py").write_text(
        "def test_tiny():\n    assert True\n", encoding="utf-8"
    )
    (path / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n', encoding="utf-8"
    )
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "permit@example.invalid")
    _git(path, "config", "user.name", "Permit Test")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "candidate")
    return path


def _policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _payload(repo: Path, stage: str, *, dirty_snapshot: bool = False) -> dict:
    module = _load_module()
    policy_sha = _sha_bytes(POLICY.read_bytes())
    trusted = _policy()["trusted_validators"][-1]
    if dirty_snapshot:
        identity = {
            "kind": "dirty-snapshot/1",
            "snapshot": module.snapshot_identity(repo),
        }
    else:
        identity = {
            "kind": "final-candidate/1",
            "base_sha": _git(repo, "rev-parse", "HEAD"),
            "candidate_sha": _git(repo, "rev-parse", "HEAD"),
            "candidate_tree": _git(repo, "rev-parse", "HEAD^{tree}"),
        }
    digest = "1" * 64
    return {
        "stage": stage,
        "identity": identity,
        "requirements_sha256": "2" * 64,
        "contract_sha256": "3" * 64,
        "tool": {
            "schema": trusted["schema"],
            "sha256": trusted["sha256"],
        },
        "policy_sha256": policy_sha,
        "environment_sha256": "4" * 64,
        "source_obligations_sha256": "8" * 64,
        "floor_selection_sha256": "9" * 64,
        "delivery_sha256": "a" * 64,
        "delivery_classification": "non-runtime",
        "repository": _repository_contract(repo),
        "runtime_inputs": [{
            "path": "tests/test_tiny.py",
            "sha256": _sha_bytes((repo / "tests" / "test_tiny.py").read_bytes()),
        }],
        "risk_sha256": "5" * 64,
        "audit_sha256": "6" * 64,
        "evidence_graph_root": "7" * 64,
        "artifact_hashes": {
            "red": [digest],
            "green": [digest],
            "mutation": [digest],
            "regeneration": [digest],
            "review": [digest],
        },
        "issued_at": 1_700_000_000,
        "expires_at": 4_000_000_000,
        "invalidators": [
            "identity-change", "policy-change", "tool-trust-change",
            "environment-change", "source-obligation-change",
            "floor-selection-change", "delivery-change", "artifact-change", "expiry",
        ],
    }


def _repository_contract(repo: Path) -> dict:
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    submodules = subprocess.run(
        ["git", "-C", str(repo), "submodule", "status", "--recursive"],
        capture_output=True, check=True,
    ).stdout
    return {
        "realpath": str(repo.resolve()),
        "git_common_dir_realpath": str(common.resolve()),
        "submodules_sha256": _sha_bytes(submodules),
    }


def _permit(repo: Path, path: Path, stage: str,
            *, dirty_snapshot: bool = False) -> dict:
    payload = _payload(repo, stage, dirty_snapshot=dirty_snapshot)
    value = {
        "schema": "cut-quality-permit/1",
        "permit_id": _sha_bytes(_canonical(payload)),
        "payload": payload,
    }
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def _refusal(exc: pytest.ExceptionInfo) -> str:
    return getattr(exc.value, "code", "")


def test_policy_is_strict_and_bootstrapped_from_v1():
    policy = _policy()
    assert policy["schema"] == "cut-quality-policy/1"
    assert policy["stage_order"] == [
        "pre-implementation", "pre-review", "pre-floor", "pre-fleet",
        "pre-merge",
    ]
    assert policy["trusted_validators"][0] == {
        "schema": "cut-acceptance-preflight/1",
        "sha256": "6c06529b9d78e7cd2b662be89bd37f8ea48e5b6392311044cb3e0bbc31739bac",
    }
    transition = policy["transitions"][0]
    assert transition["from_sha256"] == policy["trusted_validators"][0]["sha256"]
    assert transition["to_sha256"] == policy["trusted_validators"][-1]["sha256"]
    bootstrap_hashes = {
        policy["trusted_validators"][-1]["sha256"],
        transition["artifact_sha256"], transition["review_sha256"],
    }
    assert all(len(value) == 64 and len(set(value)) > 1
               for value in bootstrap_hashes)


def test_placeholder_or_unbound_transition_policy_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    bad_policy = _policy()
    bad_policy["trusted_validators"][-1]["sha256"] = "a" * 64
    bad_policy["transitions"][0].update({
        "to_sha256": "a" * 64,
        "artifact_sha256": "b" * 64,
        "review_sha256": "c" * 64,
    })
    policy = tmp_path / "toolchain" / "cut_quality_policy.json"
    policy.parent.mkdir()
    policy.write_text(json.dumps(bad_policy), encoding="utf-8")
    permit = tmp_path / "permit.json"
    value = _permit(repo, permit, "pre-floor")
    value["payload"]["policy_sha256"] = _sha_bytes(policy.read_bytes())
    value["payload"]["tool"] = bad_policy["trusted_validators"][-1]
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    permit.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, permit, "pre-floor", policy_path=policy,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-POLICY-MALFORMED"


def test_predecessor_trust_root_cannot_issue_action_permit(repo: Path,
                                                            tmp_path: Path):
    root = tmp_path / "trusted-runtime"
    module_path = root / "toolchain" / "bin" / "bd_cut_quality.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(MODULE.read_bytes())
    spec = importlib.util.spec_from_file_location("bd_cut_quality_predecessor", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    v1 = "6c06529b9d78e7cd2b662be89bd37f8ea48e5b6392311044cb3e0bbc31739bac"
    v2 = hashlib.sha256(b"active-v2-validator").hexdigest()
    subject = hashlib.sha256(b"reviewed-transition-subject").hexdigest()
    policy = root / "toolchain" / "cut_quality_policy.json"
    policy.write_text(json.dumps({
        "schema": "cut-quality-policy/1", "stage_order": list(module.STAGES),
        "trusted_validators": [
            {"schema": "cut-acceptance-preflight/1", "sha256": v1},
            {"schema": "cut-acceptance-preflight/2", "sha256": v2},
        ],
        "trusted_consumers": {
            "toolchain/bin/bd_cut_quality.py": _sha_bytes(module_path.read_bytes()),
        },
        "transitions": [{
            "from_sha256": v1, "to_sha256": v2,
            "artifact_sha256": subject, "review_sha256": subject,
        }],
    }), encoding="utf-8")
    permit = tmp_path / "permit.json"
    value = _permit(repo, permit, "pre-floor")
    value["payload"]["policy_sha256"] = _sha_bytes(policy.read_bytes())
    value["payload"]["tool"] = {
        "schema": "cut-acceptance-preflight/1", "sha256": v1,
    }
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    permit.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, permit, "pre-floor", policy_path=policy,
                               now=1_800_000_000)
    assert exc.value.code == "CQ-PERMIT-UNTRUSTED-TOOL"


def test_policy_binds_every_protected_consumer_blob():
    policy = _policy()
    expected = {
        "toolchain/bin/bd-cut", "toolchain/bin/bd-band",
        "toolchain/bin/bd-parband", "toolchain/bin/bd-fleet-run",
        "toolchain/bin/bd-ci-verdict", "toolchain/bin/bd_cut_quality.py",
    }
    assert set(policy["trusted_consumers"]) == expected
    for rel, digest in policy["trusted_consumers"].items():
        assert digest == _sha_bytes((ROOT / rel).read_bytes())


def test_valid_final_candidate_permit(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    got = module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                                 now=1_800_000_000)
    assert got["permit_id"] == value["permit_id"]


def test_nonancestor_base_refuses_with_distinctive_code(repo: Path,
                                                         tmp_path: Path):
    """A real side-branch base cannot authorize the live candidate."""
    module = _load_module()
    starting_branch = _git(repo, "branch", "--show-current")
    _git(repo, "checkout", "-q", "-b", "nonancestor-base")
    (repo / "side-only.txt").write_text("not in candidate\n", encoding="utf-8")
    _git(repo, "add", "side-only.txt")
    _git(repo, "commit", "-q", "-m", "side-only base")
    nonancestor = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", starting_branch)

    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["payload"]["identity"]["base_sha"] = nonancestor
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-BASE-STALE"
    refusal = exc.value.as_result()
    assert refusal["status"] == "REFUSED"
    assert refusal["code"] == "CQ-BASE-STALE"
    assert refusal["expected"] == {
        "ancestor": nonancestor,
        "candidate": _git(repo, "rev-parse", "HEAD"),
    }
    assert refusal["observed"] == {"merge_base_exit": 1}


def test_actual_v2_producer_semantic_fields_are_accepted(repo: Path, tmp_path: Path):
    """Producer/consumer share the full semantic invalidation contract."""
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["payload"].update({
        "source_obligations_sha256": "8" * 64,
        "floor_selection_sha256": "9" * 64,
        "delivery_sha256": "a" * 64,
        "delivery_classification": "non-runtime",
    })
    value["payload"]["invalidators"] = [
        "identity-change", "policy-change", "tool-trust-change",
        "environment-change", "source-obligation-change",
        "floor-selection-change", "delivery-change", "artifact-change", "expiry",
    ]
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    got = module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                                 now=1_800_000_000)
    assert got["permit_id"] == value["permit_id"]


def test_repository_and_runtime_inputs_are_exact(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["payload"]["repository"] = _repository_contract(repo)
    value["payload"]["runtime_inputs"] = [{
        "path": "tests/test_tiny.py",
        "sha256": _sha_bytes((repo / "tests" / "test_tiny.py").read_bytes()),
    }]
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    got = module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                                 now=1_800_000_000)
    assert got["permit_id"] == value["permit_id"]


def test_repository_submodule_identity_mismatch_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["payload"]["repository"]["submodules_sha256"] = "0" * 64
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-REPO-IDENTITY"


def test_symlink_repository_argument_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    link = tmp_path / "repo-link"
    link.symlink_to(repo, target_is_directory=True)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(link, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-REPO-IDENTITY"


def test_runtime_input_symlink_swap_refuses_before_action(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    target = repo / "tests" / "test_tiny.py"
    original = tmp_path / "original.py"
    original.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(original)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-RUNTIME-INPUT-STALE"


def test_relevant_ignored_runtime_input_change_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    (repo / ".gitignore").write_text("runtime-input.dat\n", encoding="utf-8")
    (repo / "runtime-input.dat").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "declare ignored prerequisite")
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["payload"]["runtime_inputs"] = [{
        "path": "runtime-input.dat",
        "sha256": _sha_bytes((repo / "runtime-input.dat").read_bytes()),
    }]
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    (repo / "runtime-input.dat").write_text("two\n", encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-RUNTIME-INPUT-STALE"


def test_repository_rename_invalidates_exact_path_contract(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    renamed = tmp_path / "renamed-repo"
    repo.rename(renamed)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(renamed, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-REPO-IDENTITY"


def test_live_consumer_blob_mismatch_refuses(repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    real_sha = module.file_sha256

    def tampered_sha(subject):
        if Path(subject).resolve() == MODULE.resolve():
            return "0" * 64
        return real_sha(Path(subject))

    monkeypatch.setattr(module, "file_sha256", tampered_sha)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000, consumer_path=MODULE)
    assert _refusal(exc) == "CQ-CONSUMER-TAMPER"


def test_valid_dirty_snapshot_receipt(repo: Path, tmp_path: Path):
    module = _load_module()
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "0.0.1"\n', encoding="utf-8"
    )
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    path = tmp_path / "receipt.json"
    _permit(repo, path, "pre-implementation", dirty_snapshot=True)
    got = module.validate_permit(
        repo, path, "pre-implementation", policy_path=POLICY,
        now=1_800_000_000,
    )
    assert got["payload"]["identity"]["kind"] == "dirty-snapshot/1"


def test_preimplementation_receipt_allows_not_yet_available_artifacts(
        repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "receipt.json"
    value = _permit(repo, path, "pre-implementation", dirty_snapshot=True)
    value["payload"]["artifact_hashes"].update({
        "green": [], "mutation": [], "regeneration": [], "review": [],
    })
    value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    module.validate_permit(repo, path, "pre-implementation", policy_path=POLICY,
                           now=1_800_000_000)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda repo, path: path.unlink(), "CQ-PERMIT-MISSING"),
        (lambda repo, path: path.write_text("{", encoding="utf-8"),
         "CQ-PERMIT-MALFORMED"),
        (lambda repo, path: path.write_text(
            '{"schema":"cut-quality-permit/1","schema":"x"}',
            encoding="utf-8"), "CQ-JSON-DUPLICATE-KEY"),
    ],
)
def test_parse_refusals(repo: Path, tmp_path: Path, mutator, code: str):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    mutator(repo, path)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == code
    result = exc.value.as_result()
    assert result["status"] == "REFUSED"
    assert result["invalidated_downstream"]
    assert result["corrective_action"]
    assert result["rerun_argv"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.update(permit_id="0" * 64),
         "CQ-PERMIT-ID-MISMATCH"),
        (lambda value: value["payload"].update(expires_at=1_700_000_001),
         "CQ-PERMIT-EXPIRED"),
        (lambda value: value["payload"].update(issued_at=1_800_000_301),
         "CQ-PERMIT-NOT-YET-VALID"),
        (lambda value: value["payload"]["tool"].update(sha256="9" * 64),
         "CQ-PERMIT-UNTRUSTED-TOOL"),
        (lambda value: value["payload"].update(policy_sha256="9" * 64),
         "CQ-PERMIT-POLICY-STALE"),
    ],
)
def test_content_and_trust_refusals(repo: Path, tmp_path: Path, mutation,
                                    code: str):
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    mutation(value)
    if code != "CQ-PERMIT-ID-MISMATCH":
        value["permit_id"] = _sha_bytes(_canonical(value["payload"]))
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == code


def test_earlier_stage_does_not_authorize_later_action(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-review")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-PERMIT-STAGE"


def test_final_candidate_permit_rejects_dirty_tree(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    _permit(repo, path, "pre-floor")
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8"
    )
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == "CQ-WORKTREE-DIRTY"


def test_dirty_snapshot_receipt_rejects_second_edit(repo: Path, tmp_path: Path):
    module = _load_module()
    dirty = repo / "bulk_downloader" / "__init__.py"
    dirty.write_text('__version__ = "0.0.1"\n', encoding="utf-8")
    path = tmp_path / "receipt.json"
    _permit(repo, path, "pre-implementation", dirty_snapshot=True)
    dirty.write_text('__version__ = "0.0.2"\n', encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-implementation", policy_path=POLICY,
            now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-SNAPSHOT-STALE"


def test_dirty_snapshot_includes_untracked_content(repo: Path, tmp_path: Path):
    module = _load_module()
    extra = repo / "scratch.txt"
    extra.write_text("one\n", encoding="utf-8")
    before = module.snapshot_identity(repo)
    extra.write_text("two\n", encoding="utf-8")
    after = module.snapshot_identity(repo)
    assert before["status_sha256"] == after["status_sha256"]
    assert before["untracked_sha256"] != after["untracked_sha256"]
    assert before["snapshot_sha256"] != after["snapshot_sha256"]


def _run(tool: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BIN / tool), *args], capture_output=True, text=True,
        timeout=60, env=env,
    )


@pytest.mark.parametrize("tool", ["bd-band", "bd-parband", "bd-ci-verdict"])
def test_protected_execution_surfaces_refuse_before_action_import_level(
        tool: str, repo: Path, monkeypatch, capsys):
    """Negative launch tests cannot instantiate a real action subprocess."""
    module = _load_script(tool)
    calls = []

    def bomb_action(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("protected action subprocess constructed before permit")

    if tool == "bd-band":
        monkeypatch.setattr(module, "run", bomb_action)
        argv = ["tests/absent.py", "--skip-bandcheck", "--work", str(repo)]
    elif tool == "bd-parband":
        monkeypatch.setattr(module, "run_one", bomb_action)
        argv = ["tests/absent.py", "--work", str(repo)]
    else:
        monkeypatch.setattr(module, "main_verdict", bomb_action)
        argv = ["123", "--gh", "/definitely/absent", "--repo", str(repo)]
    rc = module.main(argv)
    output = capsys.readouterr()
    assert rc == 2, output.out + output.err
    assert "CQ-PERMIT-MISSING" in output.out + output.err
    assert calls == []


def test_every_launcher_negative_is_hermetic_and_non_networked_source_contract():
    """Regression for the unsafe argparse-REMAINDER RED harness incident."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "test_protected_execution_surfaces_refuse_before_action_import_level" in source
    assert "class BombRunner" in source and "class FakeProbe" in source
    assert "192.0.2.1" in source  # documentation-only TEST-NET address
    assert '"--execute", "--", "true"' in source


def test_fleet_execute_refuses_before_runner_or_host_contact(tmp_path: Path):
    module = _load_script("bd-fleet-run")
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.1\n", encoding="utf-8")
    root = tmp_path / "artifacts"

    class BombRunner:
        name = "must-not-run"
        calls = []

        def run(self, argv, log_path, timeout):
            self.calls.append(list(argv))
            raise AssertionError("permit refusal happened after execution")

    class FakeProbe:
        name = "fake"

        def local_head(self):
            return "fake"

    runner = BombRunner()
    rc = module.main(
        ["--hosts", str(hosts), "--root", str(root), "--execute", "--", "true"],
        runner=runner, probe=FakeProbe(),
    )
    assert rc == 2
    assert runner.calls == []
    assert not root.exists()


def test_bd_cut_no_gate_cannot_bypass_missing_snapshot_receipt(
        repo: Path, tmp_path: Path, capsys):
    module = _load_script("bd-cut")
    out = tmp_path / "out"
    before = (repo / "bulk_downloader" / "__init__.py").read_bytes()
    rc = module.main(["--work", str(repo), "--out", str(out), "--no-gate",
                      "--no-build"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "CQ-PERMIT-MISSING" in captured.err
    assert not out.exists()
    assert (repo / "bulk_downloader" / "__init__.py").read_bytes() == before


def test_bd_cut_rechecks_snapshot_at_last_prework_boundary(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_script("bd-cut")
    calls = []

    def staged_enforce(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) == 1

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    out = tmp_path / "out"
    before = (repo / "bulk_downloader" / "__init__.py").read_bytes()
    rc = module.main(["--work", str(repo), "--out", str(out), "--no-gate",
                      "--no-build"])
    assert rc == 2
    assert len(calls) == 2
    assert calls[0][0][2] == calls[1][0][2] == "pre-implementation"
    assert (repo / "bulk_downloader" / "__init__.py").read_bytes() == before


def test_bd_cut_plan_is_permit_free(repo: Path, monkeypatch):
    module = _load_script("bd-cut")
    seen = []
    monkeypatch.setattr(module, "plan", lambda work, version: seen.append((work, version)) or 0)
    rc = module.main(["--work", str(repo), "--plan"])
    assert rc == 0
    assert seen == [(str(repo.resolve()), None)]


@pytest.mark.parametrize("tool", [
    "bd-cut", "bd-band", "bd-parband", "bd-fleet-run", "bd-ci-verdict",
])
def test_selftest_modes_do_not_require_permit(tool: str):
    # A selftest may fail for an independently diagnosed local prerequisite;
    # this contract is specifically that permit enforcement does not intercept.
    result = _run(tool, "--selftest")
    assert "CQ-PERMIT-" not in (result.stdout + result.stderr)


def test_fleet_plan_mode_does_not_require_permit(tmp_path: Path):
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.1\n", encoding="utf-8")
    result = _run("bd-fleet-run", "--hosts", str(hosts), "--", "true")
    assert "CQ-PERMIT-" not in (result.stdout + result.stderr)


def test_refusal_json_is_stable_from_cli(repo: Path):
    result = _run("bd-band", "tests/absent.py", "--skip-bandcheck", "--work",
                  str(repo))
    documents = [line for line in result.stderr.splitlines()
                 if line.startswith("{\"code\":")]
    assert documents, result.stderr
    value = json.loads(documents[-1])
    assert value["schema"] == "cut-quality-permit-refusal/1"
    assert value["code"] == "CQ-PERMIT-MISSING"
    assert value["expected"]["stage"] == "pre-floor"
    assert value["invalidated_downstream"] == ["pre-floor", "pre-fleet", "pre-merge"]
