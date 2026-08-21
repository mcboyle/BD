"""Shared, fail-closed consumer for content-addressed cut-quality permits.

This module is intentionally stdlib-only and lives beside its five callers so
they cannot grow independent JSON/stage/trust parsers.  It verifies that a
previously minted receipt authorizes the exact action state, then replays the
policy-pinned validator over the content-addressed evidence matrix at the last
boundary before execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


RECEIPT_SCHEMA = "cut-quality-receipt/2"
PERMIT_SCHEMA = "cut-quality-permit/1"
POLICY_SCHEMA = "cut-quality-policy/2"
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
            expected={"schema": RECEIPT_SCHEMA if label == "permit" else POLICY_SCHEMA},
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
            "CQ-RECEIPT-SCHEMA" if label == "receipt" else "CQ-PERMIT-SCHEMA",
            f"{label} must have the exact supported fields",
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
    required = {"schema", "stage_order", "trusted_validators", "active_checker",
                "trusted_consumers", "transitions"}
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
    active_checker = policy["active_checker"]
    if (not isinstance(active_checker, dict)
            or set(active_checker) != {"schema", "sha256", "resolver_env"}
            or active_checker.get("resolver_env") != "BD_CUT_QUALITY_VALIDATOR"
            or {key: active_checker.get(key) for key in ("schema", "sha256")}
            != validators[-1]):
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED",
            "active checker must exactly select the final trusted validator",
            expected={**validators[-1],
                      "resolver_env": "BD_CUT_QUALITY_VALIDATOR"},
            observed=active_checker, stage=stage, permit_path=permit_path,
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
    expected_edges = [
        (older["sha256"], newer["sha256"])
        for older, newer in zip(validators, validators[1:], strict=False)
    ]
    observed_edges = [
        (row["from_sha256"], row["to_sha256"])
        for row in transitions
    ]
    if observed_edges != expected_edges:
        raise PermitRefusal(
            "CQ-POLICY-MALFORMED",
            "trusted validators require one ordered adjacent transition chain",
            expected=expected_edges, observed=observed_edges,
            stage=stage, permit_path=permit_path,
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


def _read_provenance_input(raw_path: str | None, label: str, stage: str,
                           permit_path: Path, *, expected_sha256: str | None = None,
                           mismatch_code: str = "CQ-VALIDATOR-TAMPER"
                           ) -> tuple[Path, bytes, str]:
    """Copy one canonical regular authority input before it can be evaluated.

    A caller-selected path and digest are not provenance.  This helper only
    establishes stable bytes for the independently pinned validator below; the
    validator remains responsible for interpreting the matrix and its complete
    evidence graph.
    """
    if not raw_path:
        raise PermitRefusal(
            "CQ-EVIDENCE-UNVERIFIABLE",
            f"{label} is required to revalidate permit provenance",
            expected={"environment": label, "absolute_regular_path": True},
            observed={"environment": label, "value": raw_path},
            stage=stage, permit_path=permit_path,
        )
    supplied = Path(raw_path).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise PermitRefusal(
            "CQ-EVIDENCE-UNVERIFIABLE",
            f"{label} must resolve to a readable authority input",
            expected={"path": str(supplied), "readable": True},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=permit_path,
        ) from exc
    if not supplied.is_absolute() or supplied != resolved:
        raise PermitRefusal(
            "CQ-EVIDENCE-UNVERIFIABLE",
            f"{label} must be an absolute canonical non-symlink path",
            expected={"path": str(resolved)}, observed={"path": str(supplied)},
            stage=stage, permit_path=permit_path,
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                contents = stream.read()
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise PermitRefusal(
            "CQ-EVIDENCE-UNVERIFIABLE",
            f"{label} must remain a readable regular non-symlink file",
            expected={"path": str(resolved), "regular": True},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=permit_path,
        ) from exc
    digest = hashlib.sha256(contents).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise PermitRefusal(
            mismatch_code,
            f"{label} must equal its content-addressed authority bytes",
            expected={"path": str(resolved), "sha256": expected_sha256},
            observed={"sha256": digest}, stage=stage, permit_path=permit_path,
        )
    return resolved, contents, digest


def _write_private(path: Path, contents: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _stable_issuer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove only the validator's issue-window clock from a fresh receipt."""
    return {key: payload[key] for key in sorted(payload)
            if key not in {"issued_at", "expires_at"}}


def _verify_evidence_provenance(repo: Path, permit: dict[str, Any],
                                provenance: dict[str, Any],
                                policy: dict[str, Any], stage: str,
                                permit_path: Path) -> None:
    """Require the policy-pinned validator to reproduce this exact receipt.

    The permit's own digest detects accidental receipt edits.  Authorization
    comes from this independent replay: the exact approved validator must
    accept the current matrix/evidence graph and reproduce every stable permit
    field for the current repository.  This closes the former all-placeholder
    digest escape without duplicating validator semantics in each consumer.
    """
    if not isinstance(provenance, dict) or set(provenance) != {"checker", "matrix"}:
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "receipt provenance requires checker and matrix",
            expected=["checker", "matrix"], observed=provenance,
            stage=stage, permit_path=permit_path,
        )
    checker = provenance["checker"]
    matrix = provenance["matrix"]
    if (not isinstance(checker, dict)
            or set(checker) != {"path", "schema", "sha256"}
            or not isinstance(matrix, dict)
            or set(matrix) != {"path", "sha256"}):
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "receipt provenance fields must be exact",
            expected={"checker": ["path", "schema", "sha256"],
                      "matrix": ["path", "sha256"]},
            observed=provenance, stage=stage, permit_path=permit_path,
        )
    active = policy["active_checker"]
    if ({key: checker.get(key) for key in ("schema", "sha256")}
            != {key: active[key] for key in ("schema", "sha256")}):
        raise PermitRefusal(
            "CQ-VALIDATOR-TAMPER", "receipt checker is not the active policy authority",
            expected={key: active[key] for key in ("schema", "sha256")},
            observed={key: checker.get(key) for key in ("schema", "sha256")},
            stage=stage, permit_path=permit_path,
        )
    configured = os.environ.get(active["resolver_env"])
    if configured != checker.get("path"):
        raise PermitRefusal(
            "CQ-VALIDATOR-MISSING", "configured checker path must equal receipt provenance",
            expected={active["resolver_env"]: checker.get("path")},
            observed={active["resolver_env"]: configured},
            stage=stage, permit_path=permit_path,
        )
    _, validator_bytes, validator_sha = _read_provenance_input(
        checker.get("path"), "receipt.provenance.checker.path", stage, permit_path,
        expected_sha256=checker.get("sha256"),
    )
    matrix_path, matrix_bytes, matrix_sha = _read_provenance_input(
        matrix.get("path"), "receipt.provenance.matrix.path", stage, permit_path,
        expected_sha256=matrix.get("sha256"), mismatch_code="CQ-MATRIX-STALE",
    )
    try:
        matrix_value = json.loads(matrix_bytes.decode("utf-8"),
                                  object_pairs_hook=_duplicate_key)
        environment = matrix_value["environment"]
        python_text = environment["python"]
        python_sha = environment["executable_sha256"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, PermitRefusal) as exc:
        raise PermitRefusal(
            "CQ-MATRIX-STALE", "matrix must expose one exact approved interpreter",
            expected={"environment": ["python", "executable_sha256"]},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=permit_path,
        ) from exc
    python_path, _, _ = _read_provenance_input(
        python_text, "matrix.environment.python", stage, permit_path,
        expected_sha256=python_sha, mismatch_code="CQ-ENVIRONMENT-MISMATCH",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="bd-cut-quality-verify-") as temp_text:
            temp_root = Path(temp_text)
            os.chmod(temp_root, 0o700)
            validator_copy = temp_root / "validator.py"
            matrix_copy = temp_root / "matrix.json"
            emitted = temp_root / "permit.json"
            _write_private(validator_copy, validator_bytes, 0o700)
            _write_private(matrix_copy, matrix_bytes, 0o600)
            command = [
                str(python_path), "-I", "-B", str(validator_copy),
                "--repo", str(repo), "--matrix", str(matrix_copy),
                "--stage", stage, "--emit-permit", str(emitted),
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=180,
                check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if completed.returncode != 0:
                raise PermitRefusal(
                    "CQ-EVIDENCE-UNVERIFIABLE",
                    "the active validator did not accept the current evidence matrix",
                    expected={"exit": 0, "validator_sha256": validator_sha,
                              "matrix_sha256": matrix_sha},
                    observed={"exit": completed.returncode,
                              "stderr": completed.stderr[-1000:]},
                    stage=stage, permit_path=permit_path,
                )
            fresh = _load_json(emitted, "permit", stage)
    except PermitRefusal:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PermitRefusal(
            "CQ-VALIDATOR-HOLD",
            "the active validator could not reproduce the permit",
            expected={"validator_sha256": validator_sha,
                      "matrix_path": str(matrix_path), "exit": 0},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=permit_path,
        ) from exc
    if (set(fresh) != {"schema", "permit_id", "payload"}
            or fresh.get("schema") != PERMIT_SCHEMA
            or not isinstance(fresh.get("payload"), dict)
            or fresh.get("permit_id") != canonical_sha256(fresh.get("payload"))
            or _stable_issuer_payload(fresh["payload"])
            != _stable_issuer_payload(permit["payload"])):
        raise PermitRefusal(
            "CQ-PROVENANCE-MISMATCH",
            "the active validator did not reproduce the supplied permit",
            expected={"stable_payload_sha256": canonical_sha256(
                _stable_issuer_payload(fresh.get("payload", {}))),
                "validator_sha256": validator_sha, "matrix_sha256": matrix_sha},
            observed={"stable_payload_sha256": canonical_sha256(
                _stable_issuer_payload(permit["payload"]))},
            stage=stage, permit_path=permit_path,
        )


def wrap_issuer_permit(inner_path: Path | str, matrix_path: Path | str,
                       validator_path: Path | str, output_path: Path | str,
                       *, policy_path: Path | str | None = None) -> dict[str, Any]:
    """Content-address one validator-issued v1 permit with its replay inputs."""
    inner_path = Path(inner_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().absolute()
    inner = _load_json(inner_path, "issuer permit")
    if (set(inner) != {"schema", "permit_id", "payload"}
            or inner.get("schema") != PERMIT_SCHEMA
            or not isinstance(inner.get("payload"), dict)
            or inner.get("permit_id") != canonical_sha256(inner.get("payload"))):
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "only a complete content-addressed v1 permit can be wrapped",
            expected={"schema": PERMIT_SCHEMA, "valid_permit_id": True},
            observed={"schema": inner.get("schema")}, permit_path=output_path,
        )
    stage = inner["payload"].get("stage")
    if stage not in STAGES:
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "inner permit stage must be supported",
            expected=list(STAGES), observed=stage, permit_path=output_path,
        )
    policy_file = Path(policy_path or DEFAULT_POLICY).resolve()
    policy = _validate_policy(policy_file, stage, output_path)
    checker_path, _, checker_sha = _read_provenance_input(
        str(Path(validator_path).expanduser().absolute()),
        "validator_path", stage, output_path,
        expected_sha256=policy["active_checker"]["sha256"],
    )
    matrix_file, _, matrix_sha = _read_provenance_input(
        str(Path(matrix_path).expanduser().absolute()),
        "matrix_path", stage, output_path,
    )
    provenance = {
        "checker": {
            "path": str(checker_path),
            "schema": policy["active_checker"]["schema"],
            "sha256": checker_sha,
        },
        "matrix": {"path": str(matrix_file), "sha256": matrix_sha},
    }
    subject = {
        "schema": RECEIPT_SCHEMA,
        "provenance": provenance,
        "inner_receipt": inner,
    }
    receipt = {**subject, "receipt_id": canonical_sha256(subject)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + f".tmp.{os.getpid()}")
    data = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        _write_private(temporary, data, 0o600)
        os.replace(temporary, output_path)
        directory = os.open(output_path.parent,
                            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return receipt


def issue_receipt(repo: Path | str, matrix_path: Path | str,
                  validator_path: Path | str, stage: str,
                  output_path: Path | str,
                  *, policy_path: Path | str | None = None) -> dict[str, Any]:
    """Run the approved issuer and atomically wrap its v1 output as receipt v2."""
    repo = Path(repo).expanduser().resolve()
    output_path = Path(output_path).expanduser().absolute()
    matrix_path = Path(matrix_path).expanduser().absolute()
    validator_path = Path(validator_path).expanduser().absolute()
    if stage not in STAGES:
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "receipt stage must be supported",
            expected=list(STAGES), observed=stage, permit_path=output_path,
        )
    policy_file = Path(policy_path or DEFAULT_POLICY).resolve()
    policy = _validate_policy(policy_file, stage, output_path)
    _, matrix_bytes, _ = _read_provenance_input(
        str(matrix_path), "matrix_path", stage, output_path,
    )
    try:
        matrix_value = json.loads(matrix_bytes.decode("utf-8"),
                                  object_pairs_hook=_duplicate_key)
        environment = matrix_value["environment"]
        python_text = environment["python"]
        python_sha = environment["executable_sha256"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError,
            PermitRefusal) as exc:
        raise PermitRefusal(
            "CQ-MATRIX-STALE", "matrix must expose one exact approved interpreter",
            expected={"environment": ["python", "executable_sha256"]},
            observed={"error": f"{type(exc).__name__}: {exc}"},
            stage=stage, permit_path=output_path,
        ) from exc
    python_path, _, _ = _read_provenance_input(
        python_text, "matrix.environment.python", stage, output_path,
        expected_sha256=python_sha, mismatch_code="CQ-ENVIRONMENT-MISMATCH",
    )
    _, validator_bytes, validator_sha = _read_provenance_input(
        str(validator_path), "validator_path", stage, output_path,
        expected_sha256=policy["active_checker"]["sha256"],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bd-cut-quality-issue-") as temp_text:
        private_root = Path(temp_text)
        private_validator = private_root / "validator.py"
        private_matrix = private_root / "matrix.json"
        inner_path = private_root / "inner-permit.json"
        _write_private(private_validator, validator_bytes, 0o700)
        _write_private(private_matrix, matrix_bytes, 0o600)
        completed = subprocess.run(
            [str(python_path), "-I", "-B", str(private_validator),
             "--repo", str(repo), "--matrix", str(private_matrix),
             "--stage", stage, "--emit-permit", str(inner_path)],
            capture_output=True, text=True, timeout=180, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if completed.returncode != 0:
            raise PermitRefusal(
                "CQ-EVIDENCE-UNVERIFIABLE",
                "the approved validator could not issue the receipt",
                expected={"exit": 0, "validator_sha256": validator_sha},
                observed={"exit": completed.returncode,
                          "stderr": completed.stderr[-1000:]},
                stage=stage, permit_path=output_path,
            )
        return wrap_issuer_permit(
            inner_path, matrix_path, validator_path, output_path,
            policy_path=policy_file,
        )


def _validate_repository_identity(repo: Path, identity: dict[str, Any],
                                  required_stage: str,
                                  permit_path: Path) -> None:
    """Measure the exact authorized state at either side of validator replay."""
    if not isinstance(identity, dict) or "kind" not in identity:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "identity must be an exact typed object",
            expected={"kind": "dirty-snapshot/1 or final-candidate/1"},
            observed=identity, stage=required_stage, permit_path=permit_path,
        )
    if required_stage == "pre-implementation":
        if (set(identity) != {"kind", "snapshot"}
                or identity["kind"] != "dirty-snapshot/1"):
            raise PermitRefusal(
                "CQ-PERMIT-IDENTITY-KIND",
                "bd-cut pre-implementation requires a non-circular dirty snapshot",
                expected={"kind": "dirty-snapshot/1"}, observed=identity,
                stage=required_stage, permit_path=permit_path,
            )
        observed_snapshot = snapshot_identity(repo)
        if identity["snapshot"] != observed_snapshot:
            raise PermitRefusal(
                "CQ-SNAPSHOT-STALE", "bd-cut input state changed after receipt issuance",
                expected=identity["snapshot"], observed=observed_snapshot,
                stage=required_stage, permit_path=permit_path,
                corrective_action="return to pre-implementation review for the new snapshot",
            )
        return

    expected_keys = {"kind", "base_sha", "candidate_sha", "candidate_tree"}
    if set(identity) != expected_keys or identity["kind"] != "final-candidate/1":
        raise PermitRefusal(
            "CQ-PERMIT-IDENTITY-KIND",
            "post-finalization actions require a clean commit/tree",
            expected={"kind": "final-candidate/1", "fields": sorted(expected_keys)},
            observed=identity, stage=required_stage, permit_path=permit_path,
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
            stage=required_stage, permit_path=permit_path,
        )
    if identity["candidate_sha"] != head or identity["candidate_tree"] != tree:
        raise PermitRefusal(
            "CQ-CANDIDATE-STALE", "permit candidate must equal live HEAD and tree",
            expected={"candidate_sha": identity["candidate_sha"],
                      "candidate_tree": identity["candidate_tree"]},
            observed={"candidate_sha": head, "candidate_tree": tree},
            stage=required_stage, permit_path=permit_path,
        )
    if COMMIT_RE.fullmatch(str(identity["base_sha"])) is None:
        raise PermitRefusal(
            "CQ-PERMIT-SCHEMA", "base_sha must be an exact commit identity",
            expected="[0-9a-f]{40,64}", observed=identity["base_sha"],
            stage=required_stage, permit_path=permit_path,
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
            stage=required_stage, permit_path=permit_path,
        )


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
    receipt = _load_json(path, "permit", required_stage)
    _exact(receipt, {"schema", "receipt_id", "provenance", "inner_receipt"},
           set(), "receipt", required_stage, path)
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "receipt schema must be the supported version",
            expected={"schema": RECEIPT_SCHEMA},
            observed={"schema": receipt["schema"]},
            stage=required_stage, permit_path=path,
        )
    receipt_id = _sha(receipt["receipt_id"], "receipt_id", required_stage, path)
    receipt_subject = {key: receipt[key]
                       for key in ("schema", "provenance", "inner_receipt")}
    observed_receipt_id = canonical_sha256(receipt_subject)
    if receipt_id != observed_receipt_id:
        raise PermitRefusal(
            "CQ-RECEIPT-ID-MISMATCH",
            "receipt ID must address provenance and the complete inner permit",
            expected={"receipt_id": observed_receipt_id},
            observed={"receipt_id": receipt_id},
            stage=required_stage, permit_path=path,
        )
    permit = receipt["inner_receipt"]
    if not isinstance(permit, dict):
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "inner receipt must be an object",
            expected="object", observed=type(permit).__name__,
            stage=required_stage, permit_path=path,
        )
    _exact(permit, {"schema", "permit_id", "payload"}, set(), "inner_receipt",
           required_stage, path)
    if permit["schema"] != PERMIT_SCHEMA:
        raise PermitRefusal(
            "CQ-RECEIPT-SCHEMA", "inner permit schema must be the supported version",
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
    if payload["expires_at"] <= current:
        raise PermitRefusal(
            "CQ-PERMIT-EXPIRED", "permit must be inside a positive validity window",
            expected={"expires_at_after": max(current, payload["issued_at"])},
            observed={"issued_at": payload["issued_at"],
                      "expires_at": payload["expires_at"], "now": current},
            stage=required_stage, permit_path=path,
        )
    if payload["expires_at"] <= payload["issued_at"]:
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
    if supplied_repo != repo:
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
        missing_or_nonregular = expected_consumer is None or not regular
        digest_mismatch = observed_consumer != expected_consumer
        if missing_or_nonregular or digest_mismatch:
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
    _validate_repository_identity(repo, identity, required_stage, path)
    _verify_evidence_provenance(
        repo, permit, receipt["provenance"], policy, required_stage, path,
    )
    _validate_repository_identity(repo, identity, required_stage, path)
    return receipt


def enforce(repo: Path | str, permit_path: Path | str | None,
            required_stage: str, *, policy_path: Path | str | None = None,
            consumer_path: Path | str | None = None) -> bool:
    """Validate or emit one stable JSON refusal.  Never raises to a CLI caller."""
    try:
        validate_permit(repo, permit_path, required_stage, policy_path=policy_path,
                        consumer_path=consumer_path)
        return True
    except PermitRefusal as exc:
        # The consumer owns the recovery contract.  Callers formerly supplied
        # a raw v1 issuer command, which could never satisfy receipt-v2 parsing.
        # Centralizing this argv prevents protected launchers from drifting
        # back to a mechanically unusable recovery path.
        exc.rerun_argv = [
            sys.executable, str(Path(__file__).resolve()), "--issue-receipt",
            "--repo", str(Path(repo).expanduser().absolute()),
            "--stage", required_stage,
            "--out", exc.permit_path or str(resolve_permit_path(
                Path(repo).expanduser().absolute(), required_stage, permit_path,
            )),
        ]
        emit_refusal(exc)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="issue or wrap one replay-verifiable cut-quality receipt",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--issue-receipt", action="store_true")
    mode.add_argument("--wrap-inner-permit")
    parser.add_argument("--repo")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--matrix")
    parser.add_argument("--validator")
    parser.add_argument("--out", required=True)
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    args = parser.parse_args(argv)
    try:
        matrix = args.matrix or os.environ.get("BD_CUT_QUALITY_MATRIX")
        validator = args.validator or os.environ.get("BD_CUT_QUALITY_VALIDATOR")
        if not matrix or not validator:
            raise PermitRefusal(
                "CQ-VALIDATOR-MISSING",
                "receipt issuance requires exact matrix and validator paths",
                expected={"BD_CUT_QUALITY_MATRIX": "path",
                          "BD_CUT_QUALITY_VALIDATOR": "path"},
                observed={"matrix": matrix, "validator": validator},
                permit_path=Path(args.out),
            )
        if args.issue_receipt:
            if not args.repo or not args.stage:
                raise PermitRefusal(
                    "CQ-RECEIPT-SCHEMA",
                    "--issue-receipt requires --repo and --stage",
                    expected=["--repo", "--stage"],
                    observed={"repo": args.repo, "stage": args.stage},
                    permit_path=Path(args.out),
                )
            receipt = issue_receipt(
                args.repo, matrix, validator, args.stage, args.out,
                policy_path=args.policy,
            )
        else:
            receipt = wrap_issuer_permit(
                args.wrap_inner_permit, matrix, validator, args.out,
                policy_path=args.policy,
            )
    except PermitRefusal as exc:
        emit_refusal(exc)
        return 2
    print(json.dumps({"schema": RECEIPT_SCHEMA,
                      "receipt_id": receipt["receipt_id"],
                      "path": str(Path(args.out).expanduser().absolute())},
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
