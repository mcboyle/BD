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


def _load_module(*, verify_provenance: bool = False):
    spec = importlib.util.spec_from_file_location("bd_cut_quality", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # A parser mutant necessarily changes this tracked consumer's live blob.
    # Keep the outer self-tamper guard from masking the inner invariant under
    # mutation; the dedicated consumer-tamper tests replace this seam and still
    # prove the production guard directly.
    real_file_sha256 = module.file_sha256
    expected_self = _policy().get("trusted_consumers", {}).get(
        "toolchain/bin/bd_cut_quality.py",
    )
    if expected_self:
        module.file_sha256 = lambda subject: (
            expected_self if Path(subject).resolve() == MODULE.resolve()
            else real_file_sha256(Path(subject))
        )
    if not verify_provenance:
        # Most tests below isolate the receipt parser's individual structural
        # refusals.  Provenance replay has dedicated executable controls below;
        # do not make every parser unit manufacture a second evidence graph.
        module._verify_evidence_provenance = lambda *args, **kwargs: None
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
    inner = {
        "schema": "cut-quality-permit/1",
        "permit_id": _sha_bytes(_canonical(payload)),
        "payload": payload,
    }
    policy = _policy()
    active = policy.get("active_checker")
    if active is None:
        # The RED parent speaks permit/1 directly.  Keep parser tests capable
        # of reaching the exact payload invariant they name; receipt/2 tests
        # remain RED because the production consumer cannot unwrap them yet.
        path.write_text(json.dumps(inner, indent=2) + "\n", encoding="utf-8")
        return inner
    provenance = {
        "checker": {
            "path": "/test-fixture/cut-acceptance-preflight",
            "schema": active["schema"], "sha256": active["sha256"],
        },
        "matrix": {"path": str(POLICY), "sha256": _sha_bytes(POLICY.read_bytes())},
    }
    subject = {
        "schema": "cut-quality-receipt/2",
        "provenance": provenance,
        "inner_receipt": inner,
    }
    value = {**subject, "receipt_id": _sha_bytes(_canonical(subject))}
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def _inner(value: dict) -> dict:
    return value.get("inner_receipt", value)


def _refresh(value: dict) -> None:
    inner = _inner(value)
    inner["permit_id"] = _sha_bytes(_canonical(inner["payload"]))
    if "inner_receipt" not in value:
        return
    subject = {key: value[key] for key in ("schema", "provenance", "inner_receipt")}
    value["receipt_id"] = _sha_bytes(_canonical(subject))


def _refusal(exc: pytest.ExceptionInfo) -> str:
    return getattr(exc.value, "code", "")


def test_policy_is_strict_and_bootstrapped_from_v1():
    policy = _policy()
    assert policy["schema"] == "cut-quality-policy/2"
    assert policy["stage_order"] == [
        "pre-implementation", "pre-review", "pre-floor", "pre-fleet",
        "pre-merge",
    ]
    assert policy["trusted_validators"][0] == {
        "schema": "cut-acceptance-preflight/1",
        "sha256": "6c06529b9d78e7cd2b662be89bd37f8ea48e5b6392311044cb3e0bbc31739bac",
    }
    assert policy["active_checker"] == {
        **policy["trusted_validators"][-1],
        "resolver_env": "BD_CUT_QUALITY_VALIDATOR",
    }
    expected_edges = [
        (older["sha256"], newer["sha256"])
        for older, newer in zip(
            policy["trusted_validators"], policy["trusted_validators"][1:],
            strict=False,
        )
    ]
    transitions = policy["transitions"]
    assert [
        (transition["from_sha256"], transition["to_sha256"])
        for transition in transitions
    ] == expected_edges
    bootstrap_hashes = {
        row["sha256"] for row in policy["trusted_validators"]
    } | {
        transition[key]
        for transition in transitions
        for key in ("artifact_sha256", "review_sha256")
    }
    assert all(len(value) == 64 and len(set(value)) > 1
               for value in bootstrap_hashes)


def test_policy_refuses_a_disconnected_validator_transition_chain(tmp_path: Path):
    module = _load_module()
    policy = _policy()
    v3 = hashlib.sha256(b"active-v3-validator").hexdigest()
    subject = hashlib.sha256(b"reviewed-v2-v3-transition").hexdigest()
    policy["trusted_validators"].append({
        "schema": "cut-acceptance-preflight/3", "sha256": v3,
    })
    policy["active_checker"] = {
        **policy["trusted_validators"][-1],
        "resolver_env": "BD_CUT_QUALITY_VALIDATOR",
    }
    policy["transitions"].append({
        "from_sha256": policy["trusted_validators"][0]["sha256"],
        "to_sha256": v3,
        "artifact_sha256": subject,
        "review_sha256": subject,
    })
    path = tmp_path / "cut_quality_policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(module.PermitRefusal) as exc:
        module._validate_policy(path, "pre-floor", tmp_path / "permit.json")
    assert exc.value.code == "CQ-POLICY-MALFORMED"
    assert "adjacent transition chain" in exc.value.invariant


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
    _inner(value)["payload"]["policy_sha256"] = _sha_bytes(policy.read_bytes())
    _inner(value)["payload"]["tool"] = bad_policy["trusted_validators"][-1]
    _refresh(value)
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
        "schema": "cut-quality-policy/2", "stage_order": list(module.STAGES),
        "trusted_validators": [
            {"schema": "cut-acceptance-preflight/1", "sha256": v1},
            {"schema": "cut-acceptance-preflight/2", "sha256": v2},
        ],
        "active_checker": {
            "schema": "cut-acceptance-preflight/2", "sha256": v2,
            "resolver_env": "BD_CUT_QUALITY_VALIDATOR",
        },
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
    _inner(value)["payload"]["policy_sha256"] = _sha_bytes(policy.read_bytes())
    _inner(value)["payload"]["tool"] = {
        "schema": "cut-acceptance-preflight/1", "sha256": v1,
    }
    _refresh(value)
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
    assert got["receipt_id"] == value["receipt_id"]


def test_legacy_unwrapped_permit_refuses(repo: Path, tmp_path: Path):
    module = _load_module(verify_provenance=True)
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    path.write_text(json.dumps(_inner(value)), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert exc.value.code == "CQ-RECEIPT-SCHEMA"


def test_receipt_id_binds_provenance_and_inner_permit(repo: Path, tmp_path: Path):
    module = _load_module(verify_provenance=True)
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    value["provenance"]["matrix"]["sha256"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert exc.value.code == "CQ-RECEIPT-ID-MISMATCH"


def _provenance_fixture(tmp_path: Path, permit: dict) -> tuple[Path, Path, dict]:
    validator = tmp_path / "approved-checker.py"
    validator.write_text(
        """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--repo")
parser.add_argument("--matrix", required=True)
parser.add_argument("--stage", required=True)
parser.add_argument("--emit-permit", required=True)
args = parser.parse_args()
matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
if matrix.get("mutate_repo"):
    (Path(args.repo) / "changed-during-validator.txt").write_text(
        "validator raced the protected boundary\\n", encoding="utf-8"
    )
Path(args.emit_permit).write_text(
    json.dumps(matrix["permit"], sort_keys=True) + "\\n", encoding="utf-8"
)
""",
        encoding="utf-8",
    )
    validator.chmod(0o700)
    python = Path(sys.executable).resolve()
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({
        "environment": {
            "python": str(python), "executable_sha256": _sha_bytes(python.read_bytes()),
        },
        "permit": _inner(permit),
    }), encoding="utf-8")
    checker = {
        "path": str(validator),
        "schema": "cut-acceptance-preflight/test",
        "sha256": _sha_bytes(validator.read_bytes()),
    }
    permit["provenance"] = {
        "checker": checker,
        "matrix": {"path": str(matrix), "sha256": _sha_bytes(matrix.read_bytes())},
    }
    _refresh(permit)
    policy = {
        "active_checker": {**checker, "resolver_env": "BD_CUT_QUALITY_VALIDATOR"},
    }
    policy["active_checker"].pop("path")
    return validator, matrix, policy


def test_pinned_validator_replay_accepts_its_reproduced_receipt(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    module._verify_evidence_provenance(
        repo, _inner(value), value["provenance"], policy, "pre-floor", permit_path,
    )


def test_pinned_validator_replay_accepts_exact_v3_environment_schema(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    python = Path(sys.executable).resolve()
    matrix_value["environment"] = {
        "schema": "cut-local-environment/3",
        "python": str(python),
        "python_sha256": _sha_bytes(python.read_bytes()),
    }
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    value["provenance"]["matrix"]["sha256"] = _sha_bytes(matrix.read_bytes())
    _refresh(value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    module._verify_evidence_provenance(
        repo, _inner(value), value["provenance"], policy, "pre-floor", permit_path,
    )


def test_pinned_validator_replay_refuses_mixed_environment_schema(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    python = Path(sys.executable).resolve()
    digest = _sha_bytes(python.read_bytes())
    matrix_value["environment"] = {
        "schema": "cut-local-environment/3",
        "python": str(python),
        "python_sha256": digest,
        "executable_sha256": digest,
    }
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    value["provenance"]["matrix"]["sha256"] = _sha_bytes(matrix.read_bytes())
    _refresh(value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy, "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-MATRIX-STALE"


def test_full_consumer_accepts_when_every_stable_replay_claim_matches(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    supplied = module._stable_issuer_payload(_inner(authoritative)["payload"])
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    assert module._stable_issuer_payload(_inner(accepted)["payload"]) == supplied
    assert accepted["receipt_id"] == authoritative["receipt_id"]


def test_full_consumer_accepts_receipt_pinned_to_exact_matrix_bytes(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    assert matrix.is_file() and not matrix.is_symlink()
    assert authoritative["provenance"]["matrix"]["sha256"] == _sha_bytes(
        matrix.read_bytes()
    )
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    assert accepted["receipt_id"] == authoritative["receipt_id"]


def test_arbitrary_valid_hashes_refuse_when_validator_does_not_reproduce_them(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    fabricated = _permit(repo, permit_path, "pre-floor")
    reproduced = json.loads(json.dumps(fabricated))
    _inner(reproduced)["payload"]["artifact_hashes"]["red"] = ["b" * 64]
    _refresh(reproduced)
    validator, matrix, policy = _provenance_fixture(tmp_path, reproduced)
    fabricated["provenance"] = json.loads(json.dumps(reproduced["provenance"]))
    _refresh(fabricated)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(fabricated), fabricated["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-PROVENANCE-MISMATCH", exc.value.as_result()


def _full_consumer_fixture(repo: Path, tmp_path: Path):
    """Build one complete synthetic trust root around the public consumer."""
    trusted_root = tmp_path / "trusted-runtime"
    module_path = trusted_root / "toolchain" / "bin" / "bd_cut_quality.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(MODULE.read_bytes())
    spec = importlib.util.spec_from_file_location("full_provenance_consumer", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt_path = tmp_path / "receipt.json"
    authoritative = _permit(repo, receipt_path, "pre-floor")
    validator, matrix, _minimal = _provenance_fixture(tmp_path, authoritative)
    checker_sha = _sha_bytes(validator.read_bytes())
    policy_value = _policy()
    policy_value["trusted_validators"][-1] = {
        "schema": "cut-acceptance-preflight/test", "sha256": checker_sha,
    }
    policy_value["active_checker"] = {
        **policy_value["trusted_validators"][-1],
        "resolver_env": "BD_CUT_QUALITY_VALIDATOR",
    }
    policy_value["transitions"][-1]["to_sha256"] = checker_sha
    policy_value["trusted_consumers"] = {
        "toolchain/bin/bd_cut_quality.py": _sha_bytes(module_path.read_bytes()),
    }
    policy_path = trusted_root / "toolchain" / "cut_quality_policy.json"
    policy_path.write_text(json.dumps(policy_value), encoding="utf-8")
    _inner(authoritative)["payload"]["policy_sha256"] = _sha_bytes(
        policy_path.read_bytes()
    )
    _inner(authoritative)["payload"]["tool"] = policy_value["trusted_validators"][-1]
    _refresh(authoritative)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["permit"] = _inner(authoritative)
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    authoritative["provenance"]["matrix"]["sha256"] = _sha_bytes(matrix.read_bytes())
    _refresh(authoritative)
    return (module, module_path, receipt_path, authoritative, validator,
            matrix, policy_path)


def _resync_full_fixture(authoritative: dict, matrix: Path,
                         policy_path: Path) -> None:
    """Rebind one controlled fixture after an independently chosen input edit."""
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    _inner(authoritative)["payload"]["policy_sha256"] = _sha_bytes(
        policy_path.read_bytes()
    )
    _inner(authoritative)["payload"]["tool"] = policy["trusted_validators"][-1]
    _refresh(authoritative)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["permit"] = _inner(authoritative)
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    authoritative["provenance"]["matrix"]["sha256"] = _sha_bytes(
        matrix.read_bytes()
    )
    _refresh(authoritative)


def test_full_consumer_accepts_replayed_receipt_and_authorizes_one_safe_action(
        repo: Path, tmp_path: Path, monkeypatch):
    """A complete replayed receipt reaches exactly one action sentinel."""
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    actions = []

    def representative_protected_action():
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
        actions.append("bd-band-safe-action-sentinel")

    representative_protected_action()
    assert actions == ["bd-band-safe-action-sentinel"]


def test_full_consumer_accepts_receipt_at_its_exact_declared_stage(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    actions = []
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    actions.append("safe-action")
    assert _inner(accepted)["payload"]["stage"] == "pre-floor"
    assert accepted["receipt_id"] == authoritative["receipt_id"]
    assert actions == ["safe-action"]


def test_full_consumer_accepts_exact_transition_reviewed_consumer_blob(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert module_path.is_file() and not module_path.is_symlink()
    assert module.file_sha256(module_path) == policy["trusted_consumers"][
        "toolchain/bin/bd_cut_quality.py"
    ]
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    assert accepted["receipt_id"] == authoritative["receipt_id"]


def test_full_consumer_refuses_unlisted_regular_consumer(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    unlisted = module_path.parent / "unreviewed_consumer.py"
    unlisted.write_text("# deliberately absent from trusted_consumers\n",
                        encoding="utf-8")
    assert unlisted.is_file() and not unlisted.is_symlink()
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=unlisted, now=1_800_000_000,
        )
    assert exc.value.code == "CQ-CONSUMER-TAMPER"


def test_full_consumer_refuses_listed_nonregular_consumer(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    nonregular = module_path.parent / "nonregular-consumer"
    nonregular.mkdir()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["trusted_consumers"][
        "toolchain/bin/nonregular-consumer"
    ] = "0" * 64
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    _resync_full_fixture(authoritative, matrix, policy_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=nonregular, now=1_800_000_000,
        )
    assert exc.value.code == "CQ-CONSUMER-TAMPER"


def test_combined_issuer_wraps_validator_output_for_public_consumer(
        repo: Path, tmp_path: Path, monkeypatch):
    """The advertised recovery path must produce a usable v2 receipt, not v1."""
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    monkeypatch.setenv("BD_CUT_QUALITY_MATRIX", str(matrix))
    assert module.main([
        "--issue-receipt", "--repo", str(repo), "--stage", "pre-floor",
        "--out", str(receipt_path), "--policy", str(policy_path),
    ]) == 0
    observed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert observed["schema"] == "cut-quality-receipt/2"
    assert observed["inner_receipt"] == _inner(authoritative)
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    assert accepted["receipt_id"] == observed["receipt_id"]


def test_combined_issuer_and_replay_accept_exact_v3_environment_schema(
        repo: Path, tmp_path: Path, monkeypatch):
    """Both public boundaries must consume the validator-v3 interpreter pin."""
    (module, module_path, receipt_path, _authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    python = Path(sys.executable).resolve()
    matrix_value["environment"] = {
        "schema": "cut-local-environment/3",
        "python": str(python),
        "python_sha256": _sha_bytes(python.read_bytes()),
    }
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    issued = module.issue_receipt(
        repo, matrix, validator, "pre-floor", receipt_path,
        policy_path=policy_path,
    )
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )

    assert accepted["receipt_id"] == issued["receipt_id"]


def test_full_consumer_refuses_wrapped_placeholder_claims_before_authorization(
        repo: Path, tmp_path: Path, monkeypatch):
    """The public consumer, not only its helper, must execute replay."""
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    fabricated = json.loads(json.dumps(authoritative))
    _inner(fabricated)["payload"]["artifact_hashes"] = {
        label: ["d" * 64] for label in
        ("red", "green", "mutation", "regeneration", "review")
    }
    _refresh(fabricated)
    receipt_path.write_text(json.dumps(fabricated), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
    assert exc.value.code == "CQ-PROVENANCE-MISMATCH", exc.value.as_result()


def test_full_consumer_refuses_a_validator_mismatched_delivery_claim(
        repo: Path, tmp_path: Path, monkeypatch):
    """One plausible non-artifact claim mismatch still requires exact replay."""
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    supplied = json.loads(json.dumps(authoritative))
    _inner(supplied)["payload"]["delivery_sha256"] = "e" * 64
    _refresh(supplied)
    receipt_path.write_text(json.dumps(supplied), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
    assert exc.value.code == "CQ-PROVENANCE-MISMATCH", exc.value.as_result()


def test_full_consumer_refuses_if_repo_changes_during_validator_replay(
        repo: Path, tmp_path: Path, monkeypatch):
    """The replay boundary must finish by remeasuring exact repository state."""
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["mutate_repo"] = True
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    authoritative["provenance"]["matrix"]["sha256"] = _sha_bytes(
        matrix.read_bytes()
    )
    _refresh(authoritative)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
    assert exc.value.code == "CQ-WORKTREE-DIRTY", exc.value.as_result()


def test_missing_matrix_refuses_before_validator_result_is_trusted(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    matrix.unlink()

    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-EVIDENCE-UNVERIFIABLE"


def test_validator_bytes_must_equal_the_policy_pin(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    policy["active_checker"]["sha256"] = "f" * 64
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-VALIDATOR-TAMPER"


def test_matrix_interpreter_bytes_must_equal_the_environment_pin(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["environment"]["executable_sha256"] = "f" * 64
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    value["provenance"]["matrix"]["sha256"] = _sha_bytes(matrix.read_bytes())
    _refresh(value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))

    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-ENVIRONMENT-MISMATCH", exc.value.as_result()


def test_checker_resolver_must_equal_receipt_path(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    _validator, _matrix, policy = _provenance_fixture(tmp_path, value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(tmp_path / "other.py"))
    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-VALIDATOR-MISSING"


def test_matrix_digest_drift_refuses_before_checker_invocation(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_module(verify_provenance=True)
    permit_path = tmp_path / "permit.json"
    value = _permit(repo, permit_path, "pre-floor")
    validator, matrix, policy = _provenance_fixture(tmp_path, value)
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    matrix.write_text(matrix.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module._verify_evidence_provenance(
            repo, _inner(value), value["provenance"], policy,
            "pre-floor", permit_path,
        )
    assert exc.value.code == "CQ-MATRIX-STALE"


def test_canonical_replay_projection_excludes_only_issue_window(repo: Path):
    module = _load_module(verify_provenance=True)
    payload = _payload(repo, "pre-floor")
    clock_only = json.loads(json.dumps(payload))
    clock_only["issued_at"] += 1
    clock_only["expires_at"] += 1
    assert module._stable_issuer_payload(payload) == module._stable_issuer_payload(clock_only)
    for key in sorted(set(payload) - {"issued_at", "expires_at"}):
        changed = json.loads(json.dumps(payload))
        changed[key] = None
        assert module._stable_issuer_payload(payload) != module._stable_issuer_payload(changed), key


def test_wrapper_producer_binds_checker_matrix_and_inner_permit(
        repo: Path, tmp_path: Path):
    module = _load_module(verify_provenance=True)
    scratch_receipt = tmp_path / "scratch-receipt.json"
    value = _permit(repo, scratch_receipt, "pre-floor")
    validator, matrix, _minimal = _provenance_fixture(tmp_path, value)
    inner_path = tmp_path / "inner.json"
    inner_path.write_text(json.dumps(_inner(value)), encoding="utf-8")
    policy_value = _policy()
    checker_sha = _sha_bytes(validator.read_bytes())
    policy_value["trusted_validators"][-1] = {
        "schema": "cut-acceptance-preflight/test", "sha256": checker_sha,
    }
    policy_value["active_checker"] = {
        **policy_value["trusted_validators"][-1],
        "resolver_env": "BD_CUT_QUALITY_VALIDATOR",
    }
    policy_value["transitions"][-1]["to_sha256"] = checker_sha
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy_value), encoding="utf-8")
    output = tmp_path / "wrapped.json"

    got = module.wrap_issuer_permit(
        inner_path, matrix, validator, output, policy_path=policy_path,
    )
    assert json.loads(output.read_text(encoding="utf-8")) == got
    assert got["provenance"]["checker"]["sha256"] == checker_sha
    assert got["provenance"]["matrix"]["sha256"] == _sha_bytes(matrix.read_bytes())
    subject = {key: got[key] for key in ("schema", "provenance", "inner_receipt")}
    assert got["receipt_id"] == _sha_bytes(_canonical(subject))


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
    _inner(value)["payload"]["identity"]["base_sha"] = nonancestor
    _refresh(value)
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
    _inner(value)["payload"].update({
        "source_obligations_sha256": "8" * 64,
        "floor_selection_sha256": "9" * 64,
        "delivery_sha256": "a" * 64,
        "delivery_classification": "non-runtime",
    })
    _inner(value)["payload"]["invalidators"] = [
        "identity-change", "policy-change", "tool-trust-change",
        "environment-change", "source-obligation-change",
        "floor-selection-change", "delivery-change", "artifact-change", "expiry",
    ]
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    got = module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                                 now=1_800_000_000)
    assert got["receipt_id"] == value["receipt_id"]


def test_full_consumer_accepts_unchanged_declared_runtime_input(
        repo: Path, tmp_path: Path, monkeypatch):
    (repo / ".gitignore").write_text("runtime-input.dat\n", encoding="utf-8")
    runtime_input = repo / "runtime-input.dat"
    runtime_input.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "declare ignored prerequisite")
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    expected = _sha_bytes(runtime_input.read_bytes())
    _inner(authoritative)["payload"]["runtime_inputs"] = [{
        "path": "runtime-input.dat", "sha256": expected,
    }]
    _resync_full_fixture(authoritative, matrix, policy_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    assert runtime_input.is_file() and not runtime_input.is_symlink()
    assert _sha_bytes(runtime_input.read_bytes()) == expected
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    accepted = module.validate_permit(
        repo, receipt_path, "pre-floor", policy_path=policy_path,
        consumer_path=module_path, now=1_800_000_000,
    )
    assert accepted["receipt_id"] == authoritative["receipt_id"]


def test_repository_submodule_identity_mismatch_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "permit.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["repository"]["submodules_sha256"] = "0" * 64
    _refresh(value)
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


def test_relevant_ignored_runtime_input_change_refuses(
        repo: Path, tmp_path: Path, monkeypatch):
    (repo / ".gitignore").write_text("runtime-input.dat\n", encoding="utf-8")
    (repo / "runtime-input.dat").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "declare ignored prerequisite")
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    _inner(authoritative)["payload"]["runtime_inputs"] = [{
        "path": "runtime-input.dat",
        "sha256": _sha_bytes((repo / "runtime-input.dat").read_bytes()),
    }]
    _refresh(authoritative)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["permit"] = _inner(authoritative)
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    authoritative["provenance"]["matrix"]["sha256"] = _sha_bytes(
        matrix.read_bytes()
    )
    _refresh(authoritative)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    (repo / "runtime-input.dat").write_text("two\n", encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
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
    (module, module_path, receipt_path, authoritative, validator,
     _matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    real_sha = module.file_sha256

    def tampered_sha(subject):
        if Path(subject).resolve() == module_path.resolve():
            return "0" * 64
        return real_sha(Path(subject))

    monkeypatch.setattr(module, "file_sha256", tampered_sha)
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            now=1_800_000_000, consumer_path=module_path,
        )
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
    assert _inner(got)["payload"]["identity"]["kind"] == "dirty-snapshot/1"


def test_preimplementation_receipt_allows_not_yet_available_artifacts(
        repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "receipt.json"
    value = _permit(repo, path, "pre-implementation", dirty_snapshot=True)
    _inner(value)["payload"]["artifact_hashes"].update({
        "green": [], "mutation": [], "regeneration": [], "review": [],
    })
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    module.validate_permit(repo, path, "pre-implementation", policy_path=POLICY,
                           now=1_800_000_000)


def _assert_artifact_class_required(repo: Path, tmp_path: Path, *,
                                    stage: str, label: str) -> None:
    module = _load_module()
    path = tmp_path / f"{stage}-{label}.json"
    value = _permit(
        repo, path, stage, dirty_snapshot=(stage == "pre-implementation"),
    )
    _inner(value)["payload"]["artifact_hashes"][label] = []
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, stage, policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-PERMIT-SCHEMA"


def test_preimplementation_requires_red_artifact(repo: Path, tmp_path: Path):
    _assert_artifact_class_required(
        repo, tmp_path, stage="pre-implementation", label="red",
    )


def test_prereview_requires_green_artifact(repo: Path, tmp_path: Path):
    _assert_artifact_class_required(repo, tmp_path, stage="pre-review", label="green")


def test_prereview_requires_mutation_artifact(repo: Path, tmp_path: Path):
    _assert_artifact_class_required(
        repo, tmp_path, stage="pre-review", label="mutation",
    )


def test_prereview_requires_regeneration_artifact(repo: Path, tmp_path: Path):
    _assert_artifact_class_required(
        repo, tmp_path, stage="pre-review", label="regeneration",
    )


def test_final_stage_requires_review_artifact(repo: Path, tmp_path: Path):
    _assert_artifact_class_required(repo, tmp_path, stage="pre-floor", label="review")


def test_prereview_accepts_complete_artifacts(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "pre-review.json"
    _permit(repo, path, "pre-review")
    module.validate_permit(
        repo, path, "pre-review", policy_path=POLICY, now=1_800_000_000,
    )


def test_candidate_sha_mismatch_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "wrong-sha.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["identity"]["candidate_sha"] = "f" * 40
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-CANDIDATE-STALE"


def test_candidate_tree_mismatch_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "wrong-tree.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["identity"]["candidate_tree"] = "e" * 40
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-CANDIDATE-STALE"


def test_missing_invalidator_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "missing-invalidator.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["invalidators"].remove("expiry")
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-PERMIT-SCHEMA"


def test_nonpositive_permit_window_refuses_even_before_expiry(
        repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "nonpositive-window.json"
    value = _permit(repo, path, "pre-floor")
    payload = _inner(value)["payload"]
    payload["issued_at"] = 1_800_000_000
    payload["expires_at"] = 1_800_000_000
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_799_999_999,
        )
    assert _refusal(exc) == "CQ-PERMIT-EXPIRED"


def test_future_issued_permit_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "future-issued.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["issued_at"] = 1_800_000_301
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-PERMIT-NOT-YET-VALID"


def test_expired_now_permit_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "expired-now.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["expires_at"] = 1_799_999_999
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-PERMIT-EXPIRED"


def test_policy_sha_mismatch_refuses(repo: Path, tmp_path: Path):
    module = _load_module()
    path = tmp_path / "wrong-policy-sha.json"
    value = _permit(repo, path, "pre-floor")
    _inner(value)["payload"]["policy_sha256"] = "9" * 64
    _refresh(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, path, "pre-floor", policy_path=POLICY, now=1_800_000_000,
        )
    assert _refusal(exc) == "CQ-PERMIT-POLICY-STALE"


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
        (lambda value: _inner(value).update(permit_id="0" * 64),
         "CQ-PERMIT-ID-MISMATCH"),
        (lambda value: _inner(value)["payload"].update(expires_at=1_700_000_001),
         "CQ-PERMIT-EXPIRED"),
        (lambda value: _inner(value)["payload"].update(issued_at=1_800_000_301),
         "CQ-PERMIT-NOT-YET-VALID"),
        (lambda value: _inner(value)["payload"]["tool"].update(sha256="9" * 64),
         "CQ-PERMIT-UNTRUSTED-TOOL"),
        (lambda value: _inner(value)["payload"].update(policy_sha256="9" * 64),
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
        _refresh(value)
    else:
        subject = {key: value[key]
                   for key in ("schema", "provenance", "inner_receipt")}
        value["receipt_id"] = _sha_bytes(_canonical(subject))
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(repo, path, "pre-floor", policy_path=POLICY,
                               now=1_800_000_000)
    assert _refusal(exc) == code


def test_earlier_stage_does_not_authorize_later_action(
        repo: Path, tmp_path: Path, monkeypatch):
    (module, module_path, receipt_path, authoritative, validator,
     matrix, policy_path) = _full_consumer_fixture(repo, tmp_path)
    _inner(authoritative)["payload"]["stage"] = "pre-review"
    _refresh(authoritative)
    matrix_value = json.loads(matrix.read_text(encoding="utf-8"))
    matrix_value["permit"] = _inner(authoritative)
    matrix.write_text(json.dumps(matrix_value), encoding="utf-8")
    authoritative["provenance"]["matrix"]["sha256"] = _sha_bytes(
        matrix.read_bytes()
    )
    _refresh(authoritative)
    receipt_path.write_text(json.dumps(authoritative), encoding="utf-8")
    monkeypatch.setenv("BD_CUT_QUALITY_VALIDATOR", str(validator))
    with pytest.raises(module.PermitRefusal) as exc:
        module.validate_permit(
            repo, receipt_path, "pre-floor", policy_path=policy_path,
            consumer_path=module_path, now=1_800_000_000,
        )
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


def test_bd_band_initial_guard_precedes_target_validation(repo: Path, monkeypatch):
    module = _load_script("bd-band")
    monkeypatch.setattr(module.cut_quality, "enforce", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        module, "_load_bandcheck",
        lambda: (_ for _ in ()).throw(
            AssertionError("target validation ran before initial permit refusal")
        ),
    )
    assert module.main([
        "tests/test_tiny.py", "--work", str(repo),
    ]) == 2


def test_bd_parband_initial_guard_precedes_target_validation(repo: Path, monkeypatch):
    module = _load_script("bd-parband")
    monkeypatch.setattr(module.cut_quality, "enforce", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        module.sec, "missing_suite_reason",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("target validation ran before initial permit refusal")
        ),
    )
    assert module.main(["tests/test_tiny.py", "--work", str(repo)]) == 2


def test_ci_initial_guard_precedes_verdict(repo: Path, monkeypatch):
    module = _load_script("bd-ci-verdict")
    monkeypatch.setattr(module.cut_quality, "enforce", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        module, "main_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("verdict ran before initial permit refusal")
        ),
    )
    assert module.main(["123", "--repo", str(repo)]) == 2


def test_fleet_initial_guard_precedes_inventory_or_host_resolution(
        tmp_path: Path, monkeypatch):
    module = _load_script("bd-fleet-run")
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.1\n", encoding="utf-8")
    monkeypatch.setattr(module.cut_quality, "enforce", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        module, "_load_bd_fleet",
        lambda: (_ for _ in ()).throw(
            AssertionError("inventory loaded before initial permit refusal")
        ),
    )
    assert module.main([
        "--hosts", str(hosts), "--root", str(tmp_path / "artifacts"),
        "--execute", "--", "/bin/hostname",
    ]) == 2


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

    def bomb_regen(*args, **kwargs):
        raise AssertionError(
            "bd-cut continued after final permit recheck was removed"
        )

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    monkeypatch.setattr(module, "regen_order", bomb_regen)
    out = tmp_path / "out"
    before = (repo / "bulk_downloader" / "__init__.py").read_bytes()
    rc = module.main(["--work", str(repo), "--out", str(out), "--no-gate",
                      "--no-build"])
    assert rc == 2
    assert len(calls) == 2
    assert calls[0][0][2] == calls[1][0][2] == "pre-implementation"
    assert (repo / "bulk_downloader" / "__init__.py").read_bytes() == before


def test_bd_cut_detach_rechecks_before_child_launch(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_script("bd-cut")
    calls = []

    def staged_enforce(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) == 1

    def bomb_launch(*args, **kwargs):
        raise AssertionError("detached child launched after the receipt went stale")

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    monkeypatch.setattr(module.subprocess, "run", bomb_launch)
    out = tmp_path / "out"
    rc = module.main([
        "--work", str(repo), "--out", str(out), "--no-gate", "--no-build",
        "--detach",
    ])
    assert rc == 2
    assert len(calls) == 2
    assert calls[0][0][2] == calls[1][0][2] == "pre-implementation"


def test_bd_cut_valid_receipt_reaches_post_recheck_boundary(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_script("bd-cut")
    calls = []
    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        module, "precut",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("precut reached")
        ),
    )
    with pytest.raises(AssertionError, match="precut reached"):
        module.main([
            "--work", str(repo), "--out", str(tmp_path / "out"),
            "--no-gate", "--no-build",
        ])


def test_bd_cut_valid_receipt_reaches_fake_detached_child(
        repo: Path, tmp_path: Path, monkeypatch):
    module = _load_script("bd-cut")
    calls = []
    launches = []
    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda argv, **kwargs: launches.append(list(argv)) or
        subprocess.CompletedProcess(argv, 0, "", ""),
    )
    rc = module.main([
        "--work", str(repo), "--out", str(tmp_path / "out"),
        "--no-gate", "--no-build", "--detach",
    ])
    assert rc == 0
    assert len(launches) == 2


def test_bd_band_rechecks_before_pytest(repo: Path, monkeypatch):
    module = _load_script("bd-band")
    calls = []

    def staged_enforce(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) == 1

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    monkeypatch.setattr(
        module, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pytest launched after the permit went stale")
        ),
    )
    rc = module.main([
        "tests/test_tiny.py", "--skip-bandcheck", "--work", str(repo),
    ])
    assert rc == 2
    assert len(calls) == 2


def test_bd_band_valid_permit_reaches_fake_pytest(repo: Path, monkeypatch):
    module = _load_script("bd-band")
    calls = []
    launches = []
    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(module.sec, "resolve_test_interpreter", lambda work: sys.executable)
    monkeypatch.setattr(
        module, "run",
        lambda argv, **kwargs: launches.append(list(argv)) or
        subprocess.CompletedProcess(argv, 0, "1 passed\n", ""),
    )
    rc = module.main([
        "tests/test_tiny.py", "--skip-bandcheck", "--work", str(repo),
    ])
    assert rc == 0
    assert launches and launches[0][:3] == [sys.executable, "-m", "pytest"]


def test_bd_parband_rechecks_before_executor(repo: Path, monkeypatch):
    module = _load_script("bd-parband")
    calls = []

    def staged_enforce(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) == 1

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    monkeypatch.setattr(
        module.cf, "ThreadPoolExecutor",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("executor constructed after the permit went stale")
        ),
    )
    rc = module.main(["tests/test_tiny.py", "--work", str(repo)])
    assert rc == 2
    assert len(calls) == 2


def test_bd_parband_valid_permit_reaches_fake_executor(repo: Path, monkeypatch):
    module = _load_script("bd-parband")
    calls = []
    entered = []

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            entered.append((args, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, *args, **kwargs):
            raise AssertionError("executor construction, not task completion, is the control")

    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(module.cf, "ThreadPoolExecutor", FakeExecutor)
    with pytest.raises(AssertionError, match="executor construction"):
        module.main(["tests/test_tiny.py", "--work", str(repo)])
    assert len(entered) == 1


def test_fleet_rechecks_before_artifact_or_dispatch(tmp_path: Path, monkeypatch):
    module = _load_script("bd-fleet-run")
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.1\n", encoding="utf-8")
    root = tmp_path / "artifacts"
    calls = []

    def staged_enforce(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) == 1

    class BombRunner:
        name = "must-not-run"

        def run(self, *args, **kwargs):
            raise AssertionError("fleet dispatched after the permit went stale")

    class FakeProbe:
        name = "fake"

        def local_head(self):
            return "fake"

    monkeypatch.setattr(module.cut_quality, "enforce", staged_enforce)
    rc = module.main([
        "--hosts", str(hosts), "--root", str(root), "--execute", "--", "true",
    ], runner=BombRunner(), probe=FakeProbe())
    assert rc == 2
    assert len(calls) == 2
    assert not root.exists()


def test_fleet_valid_permit_reaches_fake_runner(tmp_path: Path, monkeypatch):
    module = _load_script("bd-fleet-run")
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.1\n", encoding="utf-8")
    root = tmp_path / "artifacts"
    calls = []

    class FakeRunner:
        name = "fake"
        calls = []

        def run(self, argv, log_path, timeout):
            self.calls.append((list(argv), log_path, timeout))
            raise AssertionError("fake runner reached")

    class FakeProbe:
        name = "fake"

        def local_head(self):
            return "fake"

    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    runner = FakeRunner()
    rc = module.main([
        "--hosts", str(hosts), "--root", str(root), "--execute",
        "--no-record-commit", "--", "/bin/hostname",
    ], runner=runner, probe=FakeProbe())
    assert rc == 1
    assert len(runner.calls) == 1
    assert root.exists()


def test_ci_valid_permit_reaches_fake_verdict(repo: Path, monkeypatch):
    module = _load_script("bd-ci-verdict")
    permits = []
    verdicts = []
    monkeypatch.setattr(
        module.cut_quality, "enforce",
        lambda *args, **kwargs: permits.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        module, "main_verdict",
        lambda args: verdicts.append(args.pr) or 17,
    )
    rc = module.main(["123", "--repo", str(repo)])
    assert rc == 17
    assert verdicts == ["123"]


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


@pytest.mark.parametrize("tool", [
    "bd-cut", "bd-band", "bd-parband", "bd-fleet-run", "bd-ci-verdict",
])
def test_help_modes_are_permit_free(tool: str):
    result = _run(tool, "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CQ-PERMIT-" not in (result.stdout + result.stderr)


@pytest.mark.parametrize("tool", [
    "bd-cut", "bd-band", "bd-parband", "bd-fleet-run", "bd-ci-verdict",
])
def test_each_launcher_imports_the_sibling_pinned_parser(
        tool: str, tmp_path: Path):
    (tmp_path / "bd_cut_quality.py").write_text(
        'raise RuntimeError("hostile PYTHONPATH parser loaded")\n', encoding="utf-8",
    )
    code = (
        "import importlib.machinery, importlib.util, pathlib; "
        f"p=pathlib.Path({str(BIN / tool)!r}); "
        "s=importlib.util.spec_from_loader('isolated_launcher', "
        "importlib.machinery.SourceFileLoader('isolated_launcher', str(p))); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "print(pathlib.Path(m.cut_quality.__file__).resolve())"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=60, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(MODULE.resolve())


def test_parband_and_ci_expose_no_permit_bypass_flag():
    import ast

    flags = set()
    for tool in ("bd-parband", "bd-ci-verdict"):
        tree = ast.parse((BIN / tool).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            flags.update(
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.startswith("--")
            )
    bypass = {
        flag for flag in flags
        if flag == "--force" or (
            any(word in flag for word in ("permit", "gate", "quality"))
            and any(word in flag for word in ("no-", "skip", "disable", "bypass"))
        )
    }
    assert bypass == set()


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
    assert "--issue-receipt" in value["rerun_argv"]
    assert "cut-acceptance-preflight" not in " ".join(value["rerun_argv"])
    assert value["rerun_argv"][-2:] == ["--out", value["permit_path"]]
