"""Shared, fail-closed consumer for content-addressed cut-quality permits.

This module is intentionally stdlib-only and lives beside its five callers so
they cannot grow independent JSON/stage/trust parsers.  It does not mint trust
or run quality checks.  It verifies that a previously minted permit authorizes
the exact action state at the last boundary before execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PERMIT_SCHEMA = "cut-quality-permit/1"
POLICY_SCHEMA = "cut-quality-policy/1"
REFUSAL_SCHEMA = "cut-quality-permit-refusal/1"
STAGES = (
    "pre-implementation", "pre-review", "pre-floor", "pre-fleet",
    "pre-merge",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "cut_quality_policy.json"
FINAL_INVALIDATORS = {
    "identity-change", "policy-change", "tool-trust-change",
    "environment-change", "source-obligation-change", "floor-selection-change",
    "delivery-change", "artifact-change", "expiry",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class PermitRefusal(ValueError):
    """A stable refusal with enough structured detail for a mechanical retry."""

    def __init__(self, code: str, invariant: str, *, expected: Any = None,
                 observed: Any = None, stage: str | None = None,
                 permit_path: Path | None = None,
                 corrective_action: str | None = None,
                 rerun_argv: list[str] | None = None):
        super().__init__(f"{code}: {invariant}")
        self.code = code
        self.invariant = invariant
        self.expected = expected
        self.observed = observed
        self.stage = stage
        self.permit_path = str(permit_path) if permit_path is not None else None
        self.corrective_action = corrective_action or (
            "regenerate the required permit from unchanged exact evidence"
        )
        self.rerun_argv = rerun_argv or [
            "cut-acceptance-preflight", "--stage", stage or "<stage>",
            "--emit-permit", self.permit_path or "<permit-path>",
        ]

    def as_result(self) -> dict[str, Any]:
        try:
            start = STAGES.index(self.stage) if self.stage in STAGES else 0
        except ValueError:  # defensive; callers cannot supply an unknown stage
            start = 0
        return {
            "schema": REFUSAL_SCHEMA,
            "code": self.code,
            "status": "REFUSED",
            "invariant": self.invariant,
            "expected": self.expected,
            "observed": self.observed,
            "permit_path": self.permit_path,
            "invalidated_downstream": list(STAGES[start:]),
            "corrective_action": self.corrective_action,
            "rerun_argv": self.rerun_argv,
        }


def emit_refusal(refusal: PermitRefusal, stream=None) -> None:
    stream = stream or sys.stderr
    stream.write(json.dumps(refusal.as_result(), sort_keys=True,
                            separators=(",", ":")) + "\n")


def _duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PermitRefusal(
                "CQ-JSON-DUPLICATE-KEY", "JSON objects must have unique keys",
                expected={"unique": True}, observed={"duplicate": key},
            )
        result[key] = value
    return result


def _load_json(path: Path, label: str, stage: str | None = None) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        code = "CQ-PERMIT-MISSING" if label == "permit" else "CQ-POLICY-MISSING"
        raise PermitRefusal(
            code, f"{label} must exist and be readable",
            expected={"path": str(path), "readable": True,
                      **({"stage": stage} if label == "permit" else {})},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=path if label == "permit" else None,
        ) from exc
    try:
        value = json.loads(text, object_pairs_hook=_duplicate_key)
    except PermitRefusal as exc:
        exc.stage = stage
        exc.permit_path = str(path) if label == "permit" else exc.permit_path
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        code = "CQ-PERMIT-MALFORMED" if label == "permit" else "CQ-POLICY-MALFORMED"
        raise PermitRefusal(
            code, f"{label} must be one complete JSON object",
            expected={"schema": PERMIT_SCHEMA if label == "permit" else POLICY_SCHEMA},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=path if label == "permit" else None,
        ) from exc
    if not isinstance(value, dict):
        code = "CQ-PERMIT-MALFORMED" if label == "permit" else "CQ-POLICY-MALFORMED"
        raise PermitRefusal(
            code, f"{label} root must be an object", expected="object",
            observed=type(value).__name__, stage=stage,
            permit_path=path if label == "permit" else None,
        )
    return value


def _exact(value: dict[str, Any], required: set[str], optional: set[str],
           label: str, stage: str, path: Path) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", f"{label} must have the exact supported fields",
            expected={"required": sorted(required), "optional": sorted(optional)},
            observed={"missing": missing, "unknown": unknown}, stage=stage,
            permit_path=path,
        )


def _sha(value: Any, label: str, stage: str, path: Path) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", f"{label} must be a lowercase SHA-256",
            expected={label: "[0-9a-f]{64}"}, observed={label: value},
            stage=stage, permit_path=path,
        )
    return value


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True,
            text=not binary, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PermitRefusal(
            "CQ-GIT-UNEVALUABLE", "exact repository identity must be measurable",
            expected={"git": list(args), "exit": 0},
            observed={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise PermitRefusal(
            "CQ-GIT-UNEVALUABLE", "exact repository identity must be measurable",
            expected={"git": list(args), "exit": 0},
            observed={"exit": result.returncode, "stderr": (stderr or "")[-500:]},
        )
    return result.stdout if binary else result.stdout.strip()


def snapshot_identity(repo: Path | str) -> dict[str, Any]:
    """Hash the complete dirty input state without writing a Git object.

    Status alone misses a second edit to an already-dirty path.  The identity
    therefore includes the HEAD, exact index entries, full tracked diff bytes,
    and path/content/mode/size identities for every untracked file.
    """
    repo = Path(repo).resolve()
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    status = _git(repo, "status", "--porcelain=v1", "-z",
                  "--untracked-files=all", binary=True)
    index = _git(repo, "ls-files", "--stage", "-z", binary=True)
    diff = _git(repo, "diff", "--binary", "HEAD", "--", binary=True)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z",
                     binary=True)
    assert isinstance(status, bytes) and isinstance(index, bytes)
    assert isinstance(diff, bytes) and isinstance(untracked, bytes)
    rows = []
    for raw in sorted(part for part in untracked.split(b"\0") if part):
        rel = raw.decode("utf-8", "surrogateescape")
        path = repo / rel
        try:
            stat = path.lstat()
            if not path.is_file() or path.is_symlink():
                digest = "non-regular"
            else:
                digest = file_sha256(path)
            rows.append({
                "path_hex": raw.hex(), "mode": stat.st_mode & 0o7777,
                "size": stat.st_size, "sha256": digest,
            })
        except OSError as exc:
            rows.append({"path_hex": raw.hex(), "error": type(exc).__name__})
    value = {
        "head_sha": head,
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_sha256": canonical_sha256(rows),
        "untracked_count": len(rows),
    }
    value["snapshot_sha256"] = canonical_sha256(value)
    return value


def _default_permit_path(repo: Path, stage: str) -> Path:
    explicit = os.environ.get("BD_CUT_QUALITY_PERMIT")
    if explicit:
        return Path(explicit).expanduser()
    state = os.environ.get("BD_CUT_QUALITY_STATE")
    if state:
        root = Path(state).expanduser()
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        root = (Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state")
        root = root / "bulkdownloader" / "cut-quality"
    try:
        head = str(_git(repo, "rev-parse", "--verify", "HEAD^{commit}"))
    except PermitRefusal:
        head = "unknown"
    return root / f"{head}-{stage}.json"


def resolve_permit_path(repo: Path | str, stage: str,
                        supplied: Path | str | None) -> Path:
    return (Path(supplied).expanduser() if supplied else
            _default_permit_path(Path(repo).resolve(), stage)).resolve()


def add_permit_argument(parser, *, help_suffix: str = "") -> None:
    parser.add_argument(
        "--cut-quality-permit", metavar="JSON",
        help="exact-state permit (default: BD_CUT_QUALITY_PERMIT or the "
             "candidate/stage path under the cut-quality state root)" + help_suffix,
    )


def _validate_policy(path: Path, stage: str, permit_path: Path) -> dict[str, Any]:
    policy = _load_json(path, "policy", stage)
    required = {"schema", "stage_order", "trusted_validators", "trusted_consumers",
                "transitions"}
    if set(policy) != required or policy.get("schema") != POLICY_SCHEMA:
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED", "trust policy must have the exact supported schema",
            expected={"schema": POLICY_SCHEMA, "fields": sorted(required)},
            observed={"schema": policy.get("schema"), "fields": sorted(policy)},
            stage=stage, permit_path=permit_path,
        )
    if policy["stage_order"] != list(STAGES):
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED", "stage ordering is a fixed monotonic authority",
            expected=list(STAGES), observed=policy["stage_order"], stage=stage,
            permit_path=permit_path,
        )
    validators = policy["trusted_validators"]
    if not isinstance(validators, list) or not validators:
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED", "policy must name at least one exact trusted validator",
            expected="nonempty list", observed=validators, stage=stage,
            permit_path=permit_path,
        )
    for row in validators:
        if (not isinstance(row, dict) or set(row) != {"schema", "sha256"}
                or not isinstance(row.get("schema"), str)
                or SHA256_RE.fullmatch(str(row.get("sha256"))) is None):
            raise PermitRefusal(
                "CQ-POLICY-MALFORMED", "trusted validators require exact schema/hash pairs",
                expected={"schema": "string", "sha256": "[0-9a-f]{64}"},
                observed=row, stage=stage, permit_path=permit_path,
            )
    transitions = policy["transitions"]
    if not isinstance(transitions, list):
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED", "trust transitions must be a list",
            expected="list", observed=type(transitions).__name__, stage=stage,
            permit_path=permit_path,
        )
    expected_keys = {"from_sha256", "to_sha256", "artifact_sha256", "review_sha256"}
    validator_hashes = {row["sha256"] for row in validators}
    for row in transitions:
        if not isinstance(row, dict) or set(row) != expected_keys or any(
                SHA256_RE.fullmatch(str(row.get(key))) is None for key in expected_keys):
            raise PermitRefusal(
                "CQ-POLICY-MALFORMED", "trust transitions require four exact hashes",
                expected=sorted(expected_keys), observed=row, stage=stage,
                permit_path=permit_path,
            )
        transition_hashes = [row[key] for key in expected_keys]
        if (any(len(set(value)) == 1 for value in transition_hashes)
                or row["from_sha256"] == row["to_sha256"]
                or row["from_sha256"] not in validator_hashes
                or row["to_sha256"] not in validator_hashes
                or row["artifact_sha256"] != row["review_sha256"]):
            raise PermitRefusal(
                "CQ-POLICY-MALFORMED",
                "trust transition must bind two trusted tools to one reviewed subject",
                expected={"from_and_to_trusted": True, "distinct_tools": True,
                          "artifact_equals_review_subject": True,
                          "placeholder_hashes": False},
                observed=row, stage=stage, permit_path=permit_path,
            )
    consumers = policy["trusted_consumers"]
    if not isinstance(consumers, dict) or not consumers:
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED", "policy must bind every protected consumer blob",
            expected="nonempty path-to-SHA mapping", observed=consumers,
            stage=stage, permit_path=permit_path,
        )
    for rel, digest in consumers.items():
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or ".." in Path(rel).parts
                or SHA256_RE.fullmatch(str(digest)) is None):
            raise PermitRefusal(
                "CQ-POLICY-MALFORMED",
                "trusted consumers require safe relative paths and exact hashes",
                expected={"path": "safe relative", "sha256": "[0-9a-f]{64}"},
                observed={rel: digest}, stage=stage, permit_path=permit_path,
            )
    return policy


def validate_permit(repo: Path | str, permit_path: Path | str | None,
                    required_stage: str, *, policy_path: Path | str | None = None,
                    now: int | None = None,
                    consumer_path: Path | str | None = None) -> dict[str, Any]:
    supplied_repo = Path(repo).expanduser().absolute()
    repo = supplied_repo.resolve()
    if required_stage not in STAGES:
        raise ValueError(f"unknown required stage: {required_stage}")
    path = resolve_permit_path(repo, required_stage, permit_path)
    policy_file = Path(policy_path or DEFAULT_POLICY).resolve()
    permit = _load_json(path, "permit", required_stage)
    _exact(permit, {"schema", "permit_id", "payload"}, set(), "permit",
           required_stage, path)
    if permit["schema"] != PERMIT_SCHEMA:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "permit schema must be the supported version",
            expected={"schema": PERMIT_SCHEMA}, observed={"schema": permit["schema"]},
            stage=required_stage, permit_path=path,
        )
    payload = permit["payload"]
    if not isinstance(payload, dict):
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "permit payload must be an object",
            expected="object", observed=type(payload).__name__, stage=required_stage,
            permit_path=path,
        )
    required = {
        "stage", "identity", "requirements_sha256", "contract_sha256", "tool",
        "policy_sha256", "environment_sha256", "source_obligations_sha256",
        "floor_selection_sha256", "delivery_sha256", "delivery_classification",
        "risk_sha256", "audit_sha256",
        "evidence_graph_root", "artifact_hashes", "issued_at", "expires_at",
        "invalidators", "repository", "runtime_inputs",
    }
    _exact(payload, required, set(), "permit.payload", required_stage, path)
    permit_id = _sha(permit["permit_id"], "permit_id", required_stage, path)
    observed_id = canonical_sha256(payload)
    if permit_id != observed_id:
        raise PermitRefusal(
            "CQ-PERMIT-ID-MISMATCH", "permit ID must address its complete payload",
            expected={"permit_id": observed_id}, observed={"permit_id": permit_id},
            stage=required_stage, permit_path=path,
        )
    if payload["stage"] != required_stage:
        raise PermitRefusal(
            "CQ-PERMIT-STAGE", "an action requires its exact stage permit",
            expected={"stage": required_stage}, observed={"stage": payload["stage"]},
            stage=required_stage, permit_path=path,
        )
    current = int(time.time()) if now is None else int(now)
    if not isinstance(payload["issued_at"], int) or not isinstance(payload["expires_at"], int):
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "permit times must be integer epoch seconds",
            expected="integers", observed={"issued_at": payload["issued_at"],
                                           "expires_at": payload["expires_at"]},
            stage=required_stage, permit_path=path,
        )
    if payload["issued_at"] > current + 300:
        raise PermitRefusal(
            "CQ-PERMIT-NOT-YET-VALID", "permit issue time cannot be in the future",
            expected={"issued_at_max": current + 300},
            observed={"issued_at": payload["issued_at"]}, stage=required_stage,
            permit_path=path,
        )
    if payload["expires_at"] <= current or payload["expires_at"] <= payload["issued_at"]:
        raise PermitRefusal(
            "CQ-PERMIT-EXPIRED", "permit must be inside a positive validity window",
            expected={"expires_at_after": max(current, payload["issued_at"])},
            observed={"issued_at": payload["issued_at"],
                      "expires_at": payload["expires_at"], "now": current},
            stage=required_stage, permit_path=path,
        )
    if set(payload["invalidators"]) != FINAL_INVALIDATORS:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "all invalidation rules must be explicit",
            expected=sorted(FINAL_INVALIDATORS), observed=payload["invalidators"],
            stage=required_stage, permit_path=path,
        )
    for label in (
        "requirements_sha256", "contract_sha256", "policy_sha256",
        "environment_sha256", "source_obligations_sha256",
        "floor_selection_sha256", "delivery_sha256", "risk_sha256", "audit_sha256",
        "evidence_graph_root",
    ):
        _sha(payload[label], label, required_stage, path)
    if payload["delivery_classification"] not in {"runtime", "non-runtime"}:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "delivery classification is a closed vocabulary",
            expected=["non-runtime", "runtime"],
            observed=payload["delivery_classification"], stage=required_stage,
            permit_path=path,
        )
    repository = payload["repository"]
    repository_keys = {"realpath", "git_common_dir_realpath", "submodules_sha256"}
    if not isinstance(repository, dict) or set(repository) != repository_keys:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "repository identity requires exact path/submodule fields",
            expected=sorted(repository_keys), observed=repository,
            stage=required_stage, permit_path=path,
        )
    if supplied_repo != repo or supplied_repo.is_symlink():
        raise PermitRefusal(
            "CQ-REPO-IDENTITY", "repository argument must be its canonical non-symlink path",
            expected={"realpath": str(repo)}, observed={"supplied": str(supplied_repo)},
            stage=required_stage, permit_path=path,
        )
    common_value = Path(str(_git(repo, "rev-parse", "--git-common-dir")))
    if not common_value.is_absolute():
        common_value = repo / common_value
    submodules = _git(repo, "submodule", "status", "--recursive", binary=True)
    assert isinstance(submodules, bytes)
    observed_repository = {
        "realpath": str(repo),
        "git_common_dir_realpath": str(common_value.resolve()),
        "submodules_sha256": hashlib.sha256(submodules).hexdigest(),
    }
    if repository != observed_repository:
        raise PermitRefusal(
            "CQ-REPO-IDENTITY", "repository path/common-dir/submodule identity is stale",
            expected=repository, observed=observed_repository,
            stage=required_stage, permit_path=path,
        )
    runtime_inputs = payload["runtime_inputs"]
    if not isinstance(runtime_inputs, list) or not runtime_inputs:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "runtime input denominator must be nonempty",
            expected="nonempty list", observed=runtime_inputs,
            stage=required_stage, permit_path=path,
        )
    seen_inputs: set[str] = set()
    for index, row in enumerate(runtime_inputs):
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise PermitRefusal(
                "CQ-PERMIT-SCHEMA", "runtime inputs require exact path/hash fields",
                expected={"path": "repo-relative", "sha256": "[0-9a-f]{64}"},
                observed=row, stage=required_stage, permit_path=path,
            )
        rel = row.get("path")
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or ".." in Path(rel).parts or rel in seen_inputs):
            raise PermitRefusal(
                "CQ-RUNTIME-INPUT-STALE",
                "runtime input paths must be unique and repository-relative",
                expected="unique safe relative path", observed=rel,
                stage=required_stage, permit_path=path,
            )
        seen_inputs.add(rel)
        expected_input = _sha(row.get("sha256"), f"runtime_inputs[{index}].sha256",
                              required_stage, path)
        live = repo / rel
        try:
            relative_parts = Path(rel).parts
            current = repo
            symlink_component = False
            for component in relative_parts:
                current = current / component
                if current.is_symlink():
                    symlink_component = True
                    break
            regular = live.is_file() and not symlink_component
            observed_input = file_sha256(live) if regular else None
        except OSError:
            observed_input = None
            regular = False
        if not regular or observed_input != expected_input:
            raise PermitRefusal(
                "CQ-RUNTIME-INPUT-STALE",
                "runtime input must remain an exact regular non-symlink file",
                expected={"path": rel, "sha256": expected_input, "regular": True},
                observed={"sha256": observed_input, "regular": regular},
                stage=required_stage, permit_path=path,
            )
    artifacts = payload["artifact_hashes"]
    artifact_keys = {"red", "green", "mutation", "regeneration", "review"}
    if not isinstance(artifacts, dict) or set(artifacts) != artifact_keys:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "artifact hash classes are exact and non-optional",
            expected=sorted(artifact_keys),
            observed=sorted(artifacts) if isinstance(artifacts, dict) else artifacts,
            stage=required_stage, permit_path=path,
        )
    required_nonempty = ({"red"} if required_stage == "pre-implementation" else
                         {"red", "green", "mutation", "regeneration"}
                         if required_stage == "pre-review" else artifact_keys)
    for label, hashes in artifacts.items():
        if not isinstance(hashes, list) or (label in required_nonempty and not hashes):
            raise PermitRefusal(
                "CQ-PERMIT-SCHEMA",
                f"artifact class {label} has the wrong stage denominator",
                expected=("nonempty SHA-256 list" if label in required_nonempty
                          else "SHA-256 list, possibly empty before evidence exists"),
                observed=hashes,
                stage=required_stage, permit_path=path,
            )
        for digest in hashes:
            _sha(digest, f"artifact_hashes.{label}", required_stage, path)

    policy = _validate_policy(policy_file, required_stage, path)
    policy_sha = file_sha256(policy_file)
    if payload["policy_sha256"] != policy_sha:
        raise PermitRefusal(
            "CQ-PERMIT-POLICY-STALE", "permit must bind the live trust policy bytes",
            expected={"policy_sha256": policy_sha},
            observed={"policy_sha256": payload["policy_sha256"]},
            stage=required_stage, permit_path=path,
        )
    policy_root = policy_file.parent.parent.resolve()
    protected = [Path(__file__).resolve()]
    if consumer_path is not None:
        protected.append(Path(consumer_path).resolve())
    for protected_path in protected:
        try:
            rel = protected_path.relative_to(policy_root).as_posix()
        except ValueError as exc:
            raise PermitRefusal(
                "CQ-CONSUMER-TAMPER",
                "permit consumer must live under the trust-policy repository root",
                expected={"root": str(policy_root)}, observed=str(protected_path),
                stage=required_stage, permit_path=path,
            ) from exc
        expected_consumer = policy["trusted_consumers"].get(rel)
        try:
            regular = protected_path.is_file() and not protected_path.is_symlink()
            observed_consumer = file_sha256(protected_path) if regular else None
        except OSError:
            regular = False
            observed_consumer = None
        if (expected_consumer is None or not regular
                or observed_consumer != expected_consumer):
            raise PermitRefusal(
                "CQ-CONSUMER-TAMPER",
                "permit consumer bytes are not the transition-reviewed trusted blob",
                expected={"path": rel, "sha256": expected_consumer, "regular": True},
                observed={"sha256": observed_consumer, "regular": regular},
                stage=required_stage, permit_path=path,
            )
    tool = payload["tool"]
    if not isinstance(tool, dict) or set(tool) != {"schema", "sha256"}:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "tool identity requires exact schema/hash fields",
            expected={"schema": "string", "sha256": "[0-9a-f]{64}"},
            observed=tool, stage=required_stage, permit_path=path,
        )
    _sha(tool.get("sha256"), "tool.sha256", required_stage, path)
    active = policy["trusted_validators"][-1]
    active_identity = (active["schema"], active["sha256"])
    if (tool.get("schema"), tool.get("sha256")) != active_identity:
        raise PermitRefusal(
            "CQ-PERMIT-UNTRUSTED-TOOL",
            "permit producer must be the exact active validator, not a transition root",
            expected={"active": active},
            observed=tool, stage=required_stage, permit_path=path,
        )

    identity = payload["identity"]
    if not isinstance(identity, dict) or "kind" not in identity:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "identity must be an exact typed object",
            expected={"kind": "dirty-snapshot/1 or final-candidate/1"},
            observed=identity, stage=required_stage, permit_path=path,
        )
    if required_stage == "pre-implementation":
        if set(identity) != {"kind", "snapshot"} or identity["kind"] != "dirty-snapshot/1":
            raise PermitRefusal(
                "CQ-PERMIT-IDENTITY-KIND",
                "bd-cut pre-implementation requires a non-circular dirty snapshot",
                expected={"kind": "dirty-snapshot/1"}, observed=identity,
                stage=required_stage, permit_path=path,
            )
        observed_snapshot = snapshot_identity(repo)
        if identity["snapshot"] != observed_snapshot:
            raise PermitRefusal(
                "CQ-SNAPSHOT-STALE", "bd-cut input state changed after receipt issuance",
                expected=identity["snapshot"], observed=observed_snapshot,
                stage=required_stage, permit_path=path,
                corrective_action="return to pre-implementation review for the new snapshot",
            )
    else:
        expected_keys = {"kind", "base_sha", "candidate_sha", "candidate_tree"}
        if set(identity) != expected_keys or identity["kind"] != "final-candidate/1":
            raise PermitRefusal(
                "CQ-PERMIT-IDENTITY-KIND", "post-finalization actions require a clean commit/tree",
                expected={"kind": "final-candidate/1", "fields": sorted(expected_keys)},
                observed=identity, stage=required_stage, permit_path=path,
            )
        head = str(_git(repo, "rev-parse", "--verify", "HEAD^{commit}"))
        tree = str(_git(repo, "rev-parse", "--verify", "HEAD^{tree}"))
        status = _git(repo, "status", "--porcelain=v1", "-z",
                      "--untracked-files=all", binary=True)
        assert isinstance(status, bytes)
        if status:
            raise PermitRefusal(
                "CQ-WORKTREE-DIRTY", "final-candidate permit requires a clean index/worktree",
                expected={"dirty": False},
                observed={"dirty": True,
                          "status_sha256": hashlib.sha256(status).hexdigest()},
                stage=required_stage, permit_path=path,
            )
        if identity["candidate_sha"] != head or identity["candidate_tree"] != tree:
            raise PermitRefusal(
                "CQ-CANDIDATE-STALE", "permit candidate must equal live HEAD and tree",
                expected={"candidate_sha": identity["candidate_sha"],
                          "candidate_tree": identity["candidate_tree"]},
                observed={"candidate_sha": head, "candidate_tree": tree},
                stage=required_stage, permit_path=path,
            )
        if COMMIT_RE.fullmatch(str(identity["base_sha"])) is None:
            raise PermitRefusal(
                "CQ-PERMIT-SCHEMA", "base_sha must be an exact commit identity",
                expected="[0-9a-f]{40,64}", observed=identity["base_sha"],
                stage=required_stage, permit_path=path,
            )
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor",
             identity["base_sha"], head], capture_output=True, timeout=60,
        )
        if ancestor.returncode != 0:
            raise PermitRefusal(
                "CQ-BASE-STALE", "permit base must be an ancestor of live candidate",
                expected={"ancestor": identity["base_sha"], "candidate": head},
                observed={"merge_base_exit": ancestor.returncode},
                stage=required_stage, permit_path=path,
            )
    return permit


def enforce(repo: Path | str, permit_path: Path | str | None,
            required_stage: str, *, policy_path: Path | str | None = None,
            rerun_argv: list[str] | None = None,
            consumer_path: Path | str | None = None) -> bool:
    """Validate or emit one stable JSON refusal.  Never raises to a CLI caller."""
    try:
        validate_permit(repo, permit_path, required_stage, policy_path=policy_path,
                        consumer_path=consumer_path)
        return True
    except PermitRefusal as exc:
        if rerun_argv:
            exc.rerun_argv = list(rerun_argv)
        emit_refusal(exc)
        return False
