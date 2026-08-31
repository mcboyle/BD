"""Row 357 -- mutation anchors do not freeze values that are re-derived.

Syntax cannot tell whether ``TIMEOUT = 149`` is fixed policy or the rounded
result of a measurement.  This gate therefore does not pretend that it can.
It combines an immutable, completely audited adoption census with narrow
producer records for the values proven to be derived.  A new value-bearing
anchor outside those two populations is UNKNOWN, never silently OK.

The adoption Git tree is deliberately immutable rather than a list authors can
append to.  It names the tree this gate SHIPS into, not the tree it was drafted
against; the single advance from draft to ship is itself measured, by
``test_the_adoption_pin_advanced_only_by_audited_addition``, which refuses any
advance that is not pure addition or that would absorb an anchor over a
registered derived value.  A legitimate new fixed literal can be admitted only
through the reasoned stable-value exception registry, whose exact size is
separately ratcheted.  The registry is empty at adoption.

Honest limit: no text-only gate can see a future tool begin deriving an
unchanged, previously fixed value if neither the anchor nor its source site
changes.  Such a semantic change must add producer evidence here during the
producer's review.  The value-bearing rule is intentionally conservative for
numbers, booleans, quoted values, assignments/comparisons, versions, digests,
and spelled counts; an unrecognised value format may look structural until
that producer is registered.  Diverse alternate-value probes catch literal and
small-enumeration regexes, but finite samples cannot prove a regex against
every possible future value.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import subprocess
import tarfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
# ADOPTION IS THE TREE THIS GATE SHIPS INTO, and it is frozen from then on.
# Moving this pin absorbs a whole population without a per-anchor reason, so it
# is legitimate exactly once: while row 357 is still unmerged and main is still
# moving underneath it. `72ae230a` (merged 4d636df) was the tree when this file
# was first drafted; rows 356, 362 and 363 merged afterwards and added 7 spec
# files / 77 anchors that no producer re-derives -- audited one by one before
# this pin advanced, and absorbed here rather than laundered through 77
# reasonless stable-value exceptions. AFTER THIS CUT MERGES, ADVANCING THIS PIN
# IS LAUNDERING: a new anchor earns its class from a producer record or from a
# reasoned stable-value exception, never from a wider census.
_ADOPTION_TREE = "67e84b316e399cda474b2c46e9a396b7cbf12bdd"
_ADOPTION_COUNTS = {
    "specs": 212,
    "mutants": 1169,
    "old": 1166,
    "old_regex": 3,
}

# The tree this file was drafted against, kept so the pin advance above is
# MEASURABLE rather than asserted: the difference between the two trees must be
# pure addition, and every absorbed anchor must be provably not fragile.
_PREADOPTION_TREE = "72ae230a932cdd96ebd1c6d6e4c516697435fcc2"
_ABSORBED_SPECS = (
    "tests/mutants/row356_cookie_quality_unknown.json",
    "tests/mutants/row356_cookie_quality_unknown_transform_control.json",
    "tests/mutants/row362_template_resolution_truth.json",
    "tests/mutants/row362_template_resolution_truth_transform_control.json",
    "tests/mutants/row363_affordance_learning.json",
    "tests/mutants/row363_affordance_learning_hardening.json",
    "tests/mutants/row363_affordance_learning_transform_control.json",
)
_ABSORBED_ANCHORS = 77


class State(str, Enum):
    STABLE = "STABLE"
    FRAGILE = "FRAGILE"
    UNKNOWN = "UNKNOWN"


class UnknownEvidence(RuntimeError):
    """The available evidence cannot support STABLE or FRAGILE."""


@dataclass(frozen=True)
class Anchor:
    spec: str
    label: str
    file: str
    field: str
    text: str
    new: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "spec": self.spec,
                "label": self.label,
                "file": self.file,
                "field": self.field,
                "text": self.text,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Audit:
    anchor: Anchor
    state: State
    compliant: bool
    detail: str


@dataclass(frozen=True)
class FragileRule:
    spec: str
    label_prefix: str
    producer_file: str
    producer_regex: str
    value_regex: str
    reason: str


@dataclass(frozen=True)
class ResolvedSite:
    rule: FragileRule
    file: str
    value_spans: tuple[tuple[int, int], ...]
    site_line: int
    producer_line: int


@dataclass(frozen=True)
class StableValueException:
    reason: str
    evidence_file: str
    evidence_regex: str


# A fixed literal is not fragile merely because it is numeric.  New fixed
# literals that the immutable adoption census cannot know belong here with
# reviewable evidence.  Exact equality makes changing the ratchet a separate,
# visible act; an entry cannot grow the population by itself.
_STABLE_VALUE_EXCEPTIONS: dict[str, StableValueException] = {}
_STABLE_VALUE_EXCEPTION_MAX = 0


def _family(
    spec: str,
    prefixes: tuple[str, ...],
    producer_file: str,
    producer_regex: str,
    value_regex: str,
    reason: str,
) -> tuple[FragileRule, ...]:
    return tuple(
        FragileRule(
            spec,
            prefix,
            producer_file,
            producer_regex,
            value_regex,
            reason,
        )
        for prefix in prefixes
    )


_VITEST_PRODUCER = r"(?m)^    derived = math\.ceil\(_VITEST_LOADED_WORST_MS \* 1\.5\)$"
_ROW338_PRODUCER = r"(?m)^_ROW_338_MEASUREMENTS = \($"
_HUNT = "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py"

_FRAGILE_RULES = (
    *_family(
        "tests/mutants/row281_ui_wrapper_delegation.json",
        tuple(f"M{i} " for i in range(1, 6)),
        "tests/frontend_vitest.py",
        r"(?m)^    assert passed == collected == expected_tests, \($",
        r"(?<=expected_tests=)[0-9]+",
        "run_vitest parses the live Vitest receipt and reconciles its test count",
    ),
    *_family(
        "tests/mutants/row281_ui_wrapper_delegation_transform_control.json",
        ("TC1 ",),
        "tests/frontend_vitest.py",
        r"(?m)^    assert passed == collected == expected_tests, \($",
        r"(?<=expected_tests=)[0-9]+",
        "the transform duplicates a wrapper count derived from the Vitest receipt",
    ),
    *_family(
        "tests/mutants/row297_real_corpus_credential_denominator.json",
        ("M6 ",),
        "tests/test_ct1_corpus_validation.py",
        r"(?m)^def _credential_census\(\) -> dict\[str, int\]:$",
        r"(?<=\": )[0-9]+",
        "_credential_census derives all six metrics from the live fixture corpus",
    ),
    *_family(
        "tests/mutants/row325_forward_deadline_population.json",
        ("M5 ",),
        _HUNT,
        r"(?m)^_W1_PARTIAL_FRAME_LOADED_WAIT_S = \(",
        r"(?<=reap_seconds=)[0-9]+",
        "the partial-frame deadline is the integral ceiling over loaded arrivals",
    ),
    *_family(
        "tests/mutants/row329_vitest_timeout.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "row 339 derives the Vitest wall from the loaded worst case",
    ),
    *_family(
        "tests/mutants/row329_vitest_timeout_transform_control.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "the transform duplicates the measurement-derived Vitest wall",
    ),
    *_family(
        "tests/mutants/row338_inner_bounds.json",
        tuple(f"M{i:02d} " for i in range(1, 21)),
        "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
        _ROW338_PRODUCER,
        r"(?<=timeout=)[0-9]+(?=[),])",
        "row 338 derives each inner wall from its measured call-site cost",
    ),
    *_family(
        "tests/mutants/row338_inner_bounds_transform_control.json",
        ("CONTROL ",),
        "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
        _ROW338_PRODUCER,
        r"(?<=timeout=)[0-9]+(?=[),])",
        "the transform duplicates row 338's measured tool-smoke wall",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "row 339 derives the Vitest wall from the loaded worst case",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds.json",
        ("M2 ",),
        "tools/verify_release.py",
        r"(?m)^# ended at 16\.43: ceil\(99\.06s \* 1\.5\) = 149s ",
        r"(?<=_STANDARD_TEST_FILE_TIMEOUT_S = )[0-9]+",
        "verify_release derives the standard-file wall from its loaded worst case",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds_transform_control.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "the transform duplicates row 339's measurement-derived Vitest wall",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds_transform_control.json",
        ("M2 ",),
        "tools/verify_release.py",
        r"(?m)^# ended at 16\.43: ceil\(99\.06s \* 1\.5\) = 149s ",
        r"(?<=_STANDARD_TEST_FILE_TIMEOUT_S = )[0-9]+",
        "the transform duplicates row 339's measurement-derived verifier wall",
    ),
    # Row 531 (v3.66.1381) retired row348's M4 rather than re-pointing it.
    # M4 set _EXPECTED_DECLARED_GATE_COUNT to a stale value, and it was catchable
    # only because a hand-maintained literal can be wrong about the population by
    # itself. That literal is gone: the expectation is now derived from the
    # declared set. A mutant aimed at the derivation leaves a consistent tree
    # consistent and ESCAPES, which would be a false negative dressed as a
    # mutant, so the honest move is to stop claiming the coverage. row348's M1,
    # M2 and M3 still sever scope, declaration and shard by making the TREE
    # inconsistent, which is what that spec is for.
    *_family(
        "tests/mutants/v3_66_1111_capture_stage_cap.json",
        ("the default cap ",),
        "scripts/lib/heartbeat.sh",
        r"(?m)^# Default 5400 \(90 min\) is ~17x the slowest lane measured",
        r"(?<=CAPTURE_STAGE_CAP:=)[0-9]+",
        "the capture cap is derived from the measured slowest fleet lane",
    ),
    *_family(
        "tests/mutants/v3_66_1204_shared_state_attribution.json",
        ("M15 ",),
        "tests/test_v3_66_1046_gates_for_this_sessions_shapes.py",
        r"(?m)^_SUITE_BASELINE_S = \{$",
        r"(?:(?<=\": )|(?<=# ))[0-9]+",
        "the suite duration and test-count comment are live census results",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M1 ",),
        _HUNT,
        r"(?m)^# _CONTENTION_FACTOR IS MEASURED, NOT CHOSEN\.",
        r"(?<=_CONTENTION_FACTOR = )[0-9]+\.[0-9]+",
        "the factor comes from the three-copy contention measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M2 ",),
        "project-knowledge/BUDGET_RATCHET.json",
        r'(?m)^ "governing_bound_s": 240,$',
        r"(?<=_GOVERNING_BOUND_S = )[0-9]+\.[0-9]+",
        "the governing bound is synchronized with the independent budget ratchet",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M3 ",),
        _HUNT,
        r"(?m)^    derived = math\.ceil\(measured \* _CONTENTION_FACTOR\)$",
        r"(?<=\()[0-9]+\.[0-9]+(?=,)",
        "the table cost is policed against a live elapsed measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M4 ",),
        _HUNT,
        r"(?m)^    assert _MIN_BUDGET_S >= 30\.0, \($",
        r"(?<=_MIN_BUDGET_S = )[0-9]+\.[0-9]+",
        "the floor is pinned by the measured scheduling-stall failure shape",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M1 ",),
        "toolchain/bin/bd-wedge-hunt",
        r"(?m)^# 15 is DERIVED, not chosen: SIGINT-delivered to runner-exited measured$",
        r"(?<=W1_CLEANUP_SECONDS=)[0-9]+",
        "the cleanup wall is ceil(2.4737 seconds times the measured stretch)",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M2 ",),
        _HUNT,
        r"(?m)^#: Named separately from `_CONTENTION_FACTOR` so that moving one cannot$",
        r"(?<=_W1_RUNNER_STRETCH_FACTOR = )[0-9]+\.[0-9]+",
        "the runner stretch is derived from the measured contention maximum",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M3 ",),
        _HUNT,
        r"(?m)^#: SIGINT delivered -> runner exited, for the shape this cut is about: a$",
        r"(?<=_W1_SETTLEMENT_MEASURED_S = )[0-9]+\.[0-9]+",
        "the settlement cost is a direct repeated measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M4 ",),
        _HUNT,
        r"(?m)^    assert _W1_RUNNER_RESERVE_S >= reserve \* 0\.2, \($",
        r"(?<=_W1_RUNNER_RESERVE_S = )[0-9]+\.[0-9]+",
        "the reserve is derived from the production cleanup reserve and cap",
    ),
    *_family(
        "tests/mutants/v3_66_1239_precut_underived_gates.json",
        ("M4 ",),
        "toolchain/bin/bd-precut",
        r"(?m)^    _UNDERIVED_GATES = \[$",
        r"(?<=none of the )[a-z]+(?:-[a-z]+)*(?= are present)",
        "the English count is manually re-derived from _UNDERIVED_GATES",
    ),
    *_family(
        "tests/mutants/v3_66_1241_owner_observation_deadline.json",
        ("M4 ",),
        "toolchain/bin/bd-wedge-hunt",
        r"(?m)^# THE FLOOR IS DERIVED, NOT CHOSEN\. One complete observation spawn --$",
        r"(?<=W1_OWNER_OBSERVATION_SECONDS=)[0-9]+",
        "the owner-observation floor is derived from measurement and lifecycle cap",
    ),
    *_family(
        "tests/mutants/v3_66_1241_owner_observation_deadline.json",
        ("M5 ",),
        _HUNT,
        r"(?m)^#: ONE COMPLETE OWNER OBSERVATION, measured as the runner actually drives$",
        r"(?<=_W1_OBSERVATION_MEASURED_S = )[0-9]+\.[0-9]+",
        "the observation input is a direct repeated measurement",
    ),
)


_VALUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_$])(?:0[xX][0-9A-Fa-f_]+|[0-9][0-9_]*(?:\.[0-9]+)?)"
    r"(?![A-Za-z0-9_$])"
    r"|\b(?:True|False|None|true|false|null)\b"
    r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b"
    r"|\b[0-9a-fA-F]{16,}\b"
)
_QUOTED_VALUE = re.compile(
    r"(?:[rubfRUBF]{0,2})?(?:\"[^\"\n]*\"|'[^'\n]*')"
)
_VALUE_OPERATOR = re.compile(r"=")


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _one_regex(pattern: str, source: str, subject: str) -> re.Match[str]:
    try:
        matches = list(re.finditer(pattern, source))
    except re.error as exc:
        raise UnknownEvidence(f"UNKNOWN: invalid {subject} regex: {exc}") from exc
    if len(matches) != 1:
        raise UnknownEvidence(
            f"UNKNOWN: {subject} resolves {len(matches)} times, expected exactly 1"
        )
    return matches[0]


def _anchors(documents: dict[str, dict]) -> list[Anchor]:
    found: list[Anchor] = []
    for spec, document in sorted(documents.items()):
        mutants = document.get("mutants")
        if not isinstance(mutants, list) or not mutants:
            raise UnknownEvidence(f"UNKNOWN: {spec} has no mutant denominator")
        labels: set[str] = set()
        for mutant in mutants:
            if not isinstance(mutant, dict):
                raise UnknownEvidence(f"UNKNOWN: {spec} has a non-object mutant")
            label = mutant.get("label")
            if not isinstance(label, str) or not label or label in labels:
                raise UnknownEvidence(
                    f"UNKNOWN: {spec} has a missing or duplicate label {label!r}"
                )
            labels.add(label)
            fields = {"old", "old_regex"} & set(mutant)
            if len(fields) != 1:
                raise UnknownEvidence(
                    f"UNKNOWN: {spec}::{label} has {sorted(fields)} anchor fields"
                )
            field = next(iter(fields))
            if not all(
                isinstance(mutant.get(key), str) and mutant[key]
                for key in ("file", field, "new")
            ):
                raise UnknownEvidence(f"UNKNOWN: {spec}::{label} has invalid text")
            found.append(
                Anchor(
                    spec,
                    label,
                    mutant["file"],
                    field,
                    mutant[field],
                    mutant["new"],
                )
            )
    return found


def _tree_documents(repo: Path, tree: str) -> dict[str, dict]:
    run = subprocess.run(
        ["git", "archive", "--format=tar", tree, "tests/mutants"],
        cwd=repo,
        capture_output=True,
    )
    if run.returncode:
        raise UnknownEvidence(
            "UNKNOWN: immutable row357 adoption tree is unavailable: "
            + run.stderr.decode("utf-8", "replace")[-500:]
        )
    documents: dict[str, dict] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(run.stdout), mode="r:") as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith(".json"):
                    continue
                if (
                    not member.name.startswith("tests/mutants/")
                    or ".." in Path(member.name).parts
                    or member.name in documents
                ):
                    raise UnknownEvidence(
                        f"UNKNOWN: malformed adoption member {member.name!r}"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise UnknownEvidence(
                        f"UNKNOWN: unreadable adoption member {member.name}"
                    )
                documents[member.name] = json.loads(stream.read())
    except (tarfile.TarError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnknownEvidence(f"UNKNOWN: unreadable adoption census: {exc}") from exc
    return documents


def _adoption_documents(repo: Path = _REPO) -> dict[str, dict]:
    documents = _tree_documents(repo, _ADOPTION_TREE)
    anchors = _anchors(documents)
    observed = {
        "specs": len(documents),
        "mutants": len(anchors),
        "old": sum(anchor.field == "old" for anchor in anchors),
        "old_regex": sum(anchor.field == "old_regex" for anchor in anchors),
    }
    if observed != _ADOPTION_COUNTS:
        raise UnknownEvidence(
            f"UNKNOWN: partial adoption census {observed} != {_ADOPTION_COUNTS}"
        )
    return documents


def _current_documents(repo: Path = _REPO) -> dict[str, dict]:
    run = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests/mutants/*.json"],
        cwd=repo,
        capture_output=True,
    )
    if run.returncode:
        raise UnknownEvidence(
            "UNKNOWN: tracked mutation population cannot be enumerated: "
            + run.stderr.decode("utf-8", "replace")[-500:]
        )
    raw_paths = [item for item in run.stdout.split(b"\0") if item]
    try:
        paths = [item.decode("utf-8") for item in raw_paths]
    except UnicodeDecodeError as exc:
        raise UnknownEvidence("UNKNOWN: a tracked spec path is not UTF-8") from exc
    if not paths or len(paths) != len(set(paths)):
        raise UnknownEvidence(
            f"UNKNOWN: invalid tracked mutation denominator ({len(paths)} paths)"
        )
    documents: dict[str, dict] = {}
    try:
        for rel in sorted(paths):
            documents[rel] = json.loads((repo / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnknownEvidence(f"UNKNOWN: current mutation census is unreadable: {exc}") from exc
    return documents


def _anchor_span(anchor: Anchor, source: str) -> tuple[int, int]:
    if anchor.field == "old_regex":
        return _one_regex(
            anchor.text,
            source,
            f"{anchor.spec}::{anchor.label} old_regex anchor",
        ).span()
    count = source.count(anchor.text)
    if count != 1:
        raise UnknownEvidence(
            f"UNKNOWN: {anchor.spec}::{anchor.label} literal anchor occurs {count} times"
        )
    start = source.find(anchor.text)
    return start, start + len(anchor.text)


def _rule_anchor(rule: FragileRule, anchors: list[Anchor]) -> Anchor:
    matches = [
        anchor
        for anchor in anchors
        if anchor.spec == rule.spec and anchor.label.startswith(rule.label_prefix)
    ]
    if len(matches) != 1:
        raise UnknownEvidence(
            "UNKNOWN: fragile registry key "
            f"{rule.spec}::{rule.label_prefix!r} resolves {len(matches)} times"
        )
    return matches[0]


def _alternates(anchor: Anchor, value: str) -> tuple[str, ...]:
    """Return shape-distinct probes a value-generic regex must accept.

    Hash-derived probes avoid a tiny public sentinel list that a literal
    alternation could accidentally satisfy. This is still a finite
    metamorphic check, not a proof over the regex language.
    """
    digest = hashlib.sha256(f"{anchor.fingerprint}:{value}".encode("utf-8")).digest()
    if re.fullmatch(r"[a-z]+(?:-[a-z]+)*", value):
        candidates = (
            "zero",
            "nine",
            "twenty-one",
            "nine-hundred-ninety-nine",
        )
        return tuple(item for item in candidates if item != value)
    if "_" in value:
        candidates = (
            "0",
            "42",
            "1_234",
            f"{int.from_bytes(digest[:4], 'big'):_}",
            "987_654_321",
        )
        return tuple(item for item in candidates if item != value)
    if "." in value:
        candidates = (
            "0.1",
            "12.345678",
            f"{int.from_bytes(digest[:3], 'big')}.{int.from_bytes(digest[3:6], 'big')}",
            "987654321.0",
        )
        return tuple(item for item in candidates if item != value)
    candidates = (
        "0",
        "7",
        "42",
        str(int.from_bytes(digest[:4], "big")),
        "987654321",
    )
    return tuple(item for item in candidates if item != value)


def _prove_regex_value_generic(
    anchor: Anchor,
    source: str,
    anchor_span: tuple[int, int],
    value_spans: tuple[tuple[int, int], ...],
) -> None:
    for value_span in value_spans:
        if not (
            anchor_span[0] <= value_span[0]
            and value_span[1] <= anchor_span[1]
        ):
            raise UnknownEvidence(
                "UNKNOWN: regex anchor only partially covers the derived value for "
                f"{anchor.spec}::{anchor.label}"
            )
        current = source[value_span[0] : value_span[1]]
        probes = _alternates(anchor, current)
        if len(probes) < 3 or len(set(probes)) != len(probes):
            raise UnknownEvidence(
                f"UNKNOWN: insufficient alternate-value probes for {current!r}"
            )
        for replacement in probes:
            changed = source[: value_span[0]] + replacement + source[value_span[1] :]
            changed_match = _one_regex(
                anchor.text,
                changed,
                f"{anchor.spec}::{anchor.label} after derived value {replacement!r}",
            )
            expected = (
                anchor_span[0],
                anchor_span[1] + len(replacement) - len(current),
            )
            if changed_match.span() != expected:
                raise UnknownEvidence(
                    "UNKNOWN: regex anchor's sole alternate match changed semantic "
                    f"site for {anchor.spec}::{anchor.label}: "
                    f"{changed_match.span()} != {expected}"
                )


def _read_source(repo: Path, rel: str, cache: dict[str, str]) -> str:
    if rel not in cache:
        try:
            cache[rel] = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise UnknownEvidence(f"UNKNOWN: cannot read {rel}: {exc}") from exc
    return cache[rel]


def _resolve_rule(
    repo: Path,
    rule: FragileRule,
    anchors: list[Anchor],
    cache: dict[str, str],
) -> ResolvedSite:
    anchor = _rule_anchor(rule, anchors)
    source = _read_source(repo, anchor.file, cache)
    site_pattern = anchor.text if anchor.field == "old_regex" else re.escape(anchor.text)
    site = _one_regex(site_pattern, source, f"derived site {rule.spec}::{anchor.label}")
    site_text = site.group(0)
    try:
        values = list(re.finditer(rule.value_regex, site_text))
    except re.error as exc:
        raise UnknownEvidence(
            f"UNKNOWN: invalid value selector for {rule.spec}::{anchor.label}: {exc}"
        ) from exc
    if not values:
        raise UnknownEvidence(
            f"UNKNOWN: derived site {rule.spec}::{anchor.label} exposes zero values"
        )
    value_spans = tuple(
        (site.start() + value.start(), site.start() + value.end()) for value in values
    )

    producer_source = _read_source(repo, rule.producer_file, cache)
    producer = _one_regex(
        rule.producer_regex,
        producer_source,
        f"producer for {rule.spec}::{anchor.label}",
    )

    return ResolvedSite(
        rule,
        anchor.file,
        value_spans,
        _line(source, site.start()),
        _line(producer_source, producer.start()),
    )


def _validate_exceptions(
    repo: Path,
    anchors: list[Anchor],
    exceptions: dict[str, StableValueException],
    expected_size: int,
    cache: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    if len(exceptions) != expected_size:
        errors.append(
            "UNKNOWN: stable-value exception population changed without its exact "
            f"ratchet ({len(exceptions)} != {expected_size})"
        )
    current = {anchor.fingerprint for anchor in anchors}
    for fingerprint, exception in sorted(exceptions.items()):
        if fingerprint not in current:
            errors.append(f"UNKNOWN: orphan stable-value exception {fingerprint}")
            continue
        if not exception.reason.strip():
            errors.append(f"UNKNOWN: stable-value exception {fingerprint} has no reason")
        try:
            evidence = _read_source(repo, exception.evidence_file, cache)
            _one_regex(
                exception.evidence_regex,
                evidence,
                f"stable-value evidence {fingerprint}",
            )
        except UnknownEvidence as exc:
            errors.append(str(exc))
    return errors


def _validate_rules(rules: tuple[FragileRule, ...]) -> list[str]:
    errors: list[str] = []
    keys = [(rule.spec, rule.label_prefix) for rule in rules]
    if len(keys) != len(set(keys)):
        errors.append("UNKNOWN: duplicate fragile producer registry key")
    for rule in rules:
        fields = {
            "spec": rule.spec,
            "label prefix": rule.label_prefix,
            "producer file": rule.producer_file,
            "producer regex": rule.producer_regex,
            "value selector": rule.value_regex,
            "reason": rule.reason,
        }
        missing = [name for name, value in fields.items() if not value.strip()]
        if missing:
            errors.append(
                "UNKNOWN: fragile producer registry entry "
                f"{rule.spec}::{rule.label_prefix!r} lacks {', '.join(missing)}"
            )
    return errors


def _audit_anchor(
    anchor: Anchor,
    source: str,
    adoption: frozenset[str],
    sites: tuple[ResolvedSite, ...],
    exceptions: dict[str, StableValueException],
) -> Audit:
    try:
        span = _anchor_span(anchor, source)
    except UnknownEvidence as exc:
        return Audit(anchor, State.UNKNOWN, False, str(exc))

    overlaps = [
        site
        for site in sites
        if site.file == anchor.file
        and any(_overlaps(span, value_span) for value_span in site.value_spans)
    ]
    # Duplicate transform-control records can name the same physical site.
    unique: dict[tuple[str, tuple[tuple[int, int], ...]], ResolvedSite] = {
        (site.file, site.value_spans): site for site in overlaps
    }
    if len(unique) > 1:
        return Audit(
            anchor,
            State.UNKNOWN,
            False,
            "UNKNOWN MUTANT ANCHOR REFUSED: anchor overlaps multiple derived sites: "
            f"{anchor.spec}::{anchor.label}",
        )
    if unique:
        site = next(iter(unique.values()))
        where = f"{site.file}:{site.site_line}"
        producer = f"{site.rule.producer_file}:{site.producer_line}"
        if anchor.field == "old":
            return Audit(
                anchor,
                State.FRAGILE,
                False,
                "FRAGILE MUTANT ANCHOR REFUSED: "
                f"{anchor.spec}::{anchor.label} uses literal old at {where}; "
                f"re-derived by {producer} ({site.rule.reason}); use old_regex",
            )
        try:
            _prove_regex_value_generic(anchor, source, span, site.value_spans)
        except UnknownEvidence as exc:
            return Audit(
                anchor,
                State.UNKNOWN,
                False,
                "UNKNOWN MUTANT ANCHOR REFUSED: " + str(exc),
            )
        return Audit(
            anchor,
            State.FRAGILE,
            True,
            f"regex-anchored derived value at {where}; producer {producer}",
        )

    if anchor.fingerprint in adoption:
        return Audit(
            anchor,
            State.STABLE,
            True,
            "unchanged member of the immutable row357 audited census",
        )

    matched = source[span[0] : span[1]]
    if not (
        _VALUE_TOKEN.search(matched)
        or _QUOTED_VALUE.search(matched)
        or _VALUE_OPERATOR.search(matched)
    ):
        return Audit(
            anchor,
            State.STABLE,
            True,
            "new anchor is structural and contains no value-bearing token",
        )
    if anchor.fingerprint in exceptions:
        return Audit(
            anchor,
            State.STABLE,
            True,
            exceptions[anchor.fingerprint].reason,
        )
    return Audit(
        anchor,
        State.UNKNOWN,
        False,
        "UNKNOWN MUTANT ANCHOR REFUSED: "
        f"{anchor.spec}::{anchor.label} contains value-bearing source text, "
        "but no producer or audited stable exception establishes its class",
    )


def _audit_documents(
    repo: Path,
    documents: dict[str, dict],
    adoption: frozenset[str],
    rules: tuple[FragileRule, ...],
    exceptions: dict[str, StableValueException] | None = None,
    exception_max: int = 0,
) -> tuple[list[Audit], list[str]]:
    exceptions = exceptions or {}
    try:
        anchors = _anchors(documents)
    except UnknownEvidence as exc:
        return [], [str(exc)]
    cache: dict[str, str] = {}
    errors = _validate_rules(rules)
    sites: list[ResolvedSite] = []
    for rule in rules:
        try:
            sites.append(_resolve_rule(repo, rule, anchors, cache))
        except UnknownEvidence as exc:
            errors.append(str(exc))
    errors.extend(
        _validate_exceptions(repo, anchors, exceptions, exception_max, cache)
    )
    audits: list[Audit] = []
    for anchor in anchors:
        try:
            source = _read_source(repo, anchor.file, cache)
        except UnknownEvidence as exc:
            audits.append(Audit(anchor, State.UNKNOWN, False, str(exc)))
            continue
        audits.append(
            _audit_anchor(anchor, source, adoption, tuple(sites), exceptions)
        )
    if len(audits) != len(anchors) or not audits:
        errors.append(
            "UNKNOWN: anchor audit did not reconcile its nonzero denominator "
            f"({len(audits)} of {len(anchors)})"
        )
    return audits, errors


def _assert_compliant(audits: list[Audit], errors: list[str]) -> None:
    blocked = [audit.detail for audit in audits if not audit.compliant]
    assert not errors and not blocked, "\n".join([*errors, *blocked])


def _synthetic_document(anchor_field: str, anchor: str, *, label: str) -> dict:
    return {
        "mutants": [
            {
                "label": label,
                "file": "settings.py",
                anchor_field: anchor,
                "new": "TIMEOUT_S = 5",
            }
        ]
    }


def test_a_literal_over_a_registered_derived_value_is_refused(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text(
        "# measured worst case times headroom\nMEASURED_TIMEOUT = 149\n",
        encoding="utf-8",
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "TIMEOUT_S = 149", label="M1 measured timeout"
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^# measured worst case times headroom$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "the measurement producer recomputes this timeout",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert len(audits) == 1 and audits[0].state is State.FRAGILE
    with pytest.raises(AssertionError, match="FRAGILE MUTANT ANCHOR REFUSED"):
        _assert_compliant(audits, errors)


def test_a_structural_anchor_passes_as_the_negative_control(tmp_path):
    (tmp_path / "settings.py").write_text(
        "def stable_name():\n    return object()\n", encoding="utf-8"
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "def stable_name():", label="M1 stable function scope"
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    _assert_compliant(audits, errors)
    assert [audit.state for audit in audits] == [State.STABLE]


def test_an_unclassified_value_is_UNKNOWN_and_never_OK(tmp_path):
    (tmp_path / "settings.py").write_text("LIMIT = 7\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "LIMIT = 7", label="M1 unexplained limit"
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert not audits[0].compliant
    assert "UNKNOWN MUTANT ANCHOR REFUSED" in audits[0].detail


def test_a_fixed_literal_needs_reasoned_evidence_and_a_visible_ratchet(tmp_path):
    (tmp_path / "settings.py").write_text("PROTOCOL_PORT = 8899\n", encoding="utf-8")
    (tmp_path / "contract.md").write_text(
        "The fixture protocol identity is the fixed local port 8899.\n",
        encoding="utf-8",
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "PROTOCOL_PORT = 8899", label="M1 fixed fixture identity"
        )
    }
    anchor = _anchors(documents)[0]
    exceptions = {
        anchor.fingerprint: StableValueException(
            "8899 is a fixed fixture protocol identity, not measured output",
            "contract.md",
            r"(?m)^The fixture protocol identity is the fixed local port 8899\.$",
        )
    }
    audits, errors = _audit_documents(
        tmp_path, documents, frozenset(), (), exceptions, exception_max=1
    )
    _assert_compliant(audits, errors)
    assert audits[0].state is State.STABLE

    _audits, stale_ratchet = _audit_documents(
        tmp_path, documents, frozenset(), (), exceptions, exception_max=0
    )
    assert any("ratchet" in error for error in stale_ratchet)


def test_a_regex_must_survive_a_different_derived_value(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old_regex", "TIMEOUT_S = 149", label="M1 fake regex"
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert "after derived value" in audits[0].detail
    with pytest.raises(AssertionError, match="UNKNOWN"):
        _assert_compliant(audits, errors)


def test_every_regex_over_a_known_site_faces_the_alternate_proof(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": {
            "mutants": [
                {
                    "label": "M1 registered generic regex",
                    "file": "settings.py",
                    "old_regex": r"TIMEOUT_S = [0-9]+",
                    "new": "TIMEOUT_S = 5",
                },
                {
                    "label": "M2 unregistered literal regex",
                    "file": "settings.py",
                    "old_regex": "TIMEOUT_S = 149",
                    "new": "TIMEOUT_S = 6",
                },
            ]
        }
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert audits[0].state is State.FRAGILE and audits[0].compliant
    assert audits[1].state is State.UNKNOWN and not audits[1].compliant
    assert "after derived value" in audits[1].detail


def test_literal_enumeration_is_not_a_value_generic_regex(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old_regex",
            r"TIMEOUT_S = (?:149|0|7|42|987654321)",
            label="M1 enumerated probes",
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert "after derived value" in audits[0].detail


@pytest.mark.parametrize(
    "source_text",
    [
        'BUILD_VERSION = "release-candidate"',
        'EXPECTED_GATES = {"alpha", "beta"}',
        "MODE = release_candidate",
    ],
)
def test_unexplained_string_or_rhs_values_are_UNKNOWN(tmp_path, source_text):
    (tmp_path / "settings.py").write_text(source_text + "\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old",
            source_text,
            label="M1 unexplained string or RHS value",
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert not audits[0].compliant
    assert "UNKNOWN MUTANT ANCHOR REFUSED" in audits[0].detail


def test_the_immutable_adoption_population_is_complete():
    documents = _adoption_documents()
    anchors = _anchors(documents)
    assert len(documents) == 212
    assert len(anchors) == 1169
    assert sum(anchor.field == "old_regex" for anchor in anchors) == 3


def test_the_adoption_pin_advanced_only_by_audited_addition():
    """The one legitimate pin move is proved, not asserted.

    Absorbing a population without a per-anchor reason is only honest while
    this gate is unmerged AND the absorbed anchors are provably outside every
    registered derived-value site.  Both halves are measured here, so a later
    author cannot quietly widen the census past a fragile anchor: the addition
    must be pure, its size exact, and no absorbed anchor may resolve FRAGILE.
    """
    before = _tree_documents(_REPO, _PREADOPTION_TREE)
    after = _adoption_documents()
    assert before and after

    shared = sorted(set(before) & set(after))
    assert len(shared) == len(before), "the pin advance dropped an audited spec"
    for spec in shared:
        assert before[spec] == after[spec], (
            f"{spec} changed under the adoption pin advance; a modified spec is "
            "not an audited addition"
        )
    added = sorted(set(after) - set(before))
    assert added == sorted(_ABSORBED_SPECS), added

    before_fingerprints = {anchor.fingerprint for anchor in _anchors(before)}
    absorbed = [
        anchor
        for anchor in _anchors(after)
        if anchor.fingerprint not in before_fingerprints
    ]
    assert len(absorbed) == _ABSORBED_ANCHORS, len(absorbed)
    assert {anchor.spec for anchor in absorbed} == set(_ABSORBED_SPECS)

    # Audit the live tree with an EMPTY census so absorption cannot mask a
    # producer overlap, then read only the absorbed anchors' verdicts.
    audits, errors = _audit_documents(
        _REPO,
        _current_documents(),
        frozenset(),
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    assert not errors, errors
    absorbed_fingerprints = {anchor.fingerprint for anchor in absorbed}
    judged = [
        audit
        for audit in audits
        if audit.anchor.fingerprint in absorbed_fingerprints
    ]
    assert len(judged) == _ABSORBED_ANCHORS, len(judged)
    fragile = [audit for audit in judged if audit.state is State.FRAGILE]
    assert not fragile, [audit.detail for audit in fragile]
    # The absorption is load-bearing: without it these anchors are not silently
    # OK.  A census that absorbed only already-structural anchors would prove
    # nothing about the rule it is standing in for.
    unknown = [audit for audit in judged if audit.state is State.UNKNOWN]
    assert len(unknown) == 69, len(unknown)


def test_an_unavailable_adoption_tree_is_UNKNOWN(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(UnknownEvidence, match="UNKNOWN: immutable row357 adoption"):
        _adoption_documents(tmp_path)


def test_the_preconversion_literal_population_is_RED():
    """Replay the original anchor forms without writing any subject file."""
    adoption_documents = _adoption_documents()
    adoption_anchors = _anchors(adoption_documents)
    replay = copy.deepcopy(_current_documents())
    converted = 0
    for rule in _FRAGILE_RULES:
        historical = _rule_anchor(rule, adoption_anchors)
        if historical.field == "old_regex":
            continue  # rows 329 and 348 were already converted before row 357
        candidates = [
            mutant
            for mutant in replay[rule.spec]["mutants"]
            if mutant["label"].startswith(rule.label_prefix)
        ]
        assert len(candidates) == 1, (rule.spec, rule.label_prefix)
        mutant = candidates[0]
        pattern = mutant.pop("old_regex")
        source = (_REPO / mutant["file"]).read_text(encoding="utf-8")
        mutant["old"] = _one_regex(pattern, source, "preconversion replay").group(0)
        converted += 1
    assert converted == 46

    adoption = frozenset(anchor.fingerprint for anchor in adoption_anchors)
    audits, errors = _audit_documents(
        _REPO,
        replay,
        adoption,
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    assert not errors
    refused = [
        audit
        for audit in audits
        if audit.state is State.FRAGILE and not audit.compliant
    ]
    assert len(refused) == 46
    assert all(
        audit.detail.startswith("FRAGILE MUTANT ANCHOR REFUSED:")
        for audit in refused
    )
    with pytest.raises(AssertionError, match="FRAGILE MUTANT ANCHOR REFUSED"):
        _assert_compliant(audits, errors)


def test_every_tracked_mutant_anchor_has_an_honest_classification():
    adoption_documents = _adoption_documents()
    adoption = frozenset(
        anchor.fingerprint for anchor in _anchors(adoption_documents)
    )
    current = _current_documents()
    audits, errors = _audit_documents(
        _REPO,
        current,
        adoption,
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    # 49 -> 48 at row 531: row348::M4 retired with its subject, not dropped
    # silently. See the comment beside the removed _family entry above.
    assert len(_FRAGILE_RULES) == 48, "the measured fragile denominator drifted"
    _assert_compliant(audits, errors)
    assert sum(audit.state is State.FRAGILE for audit in audits) >= 48
    assert all(audit.state is not State.UNKNOWN for audit in audits)


def _base_mutant(documents: dict[str, dict], anchor: Anchor) -> dict:
    matches = [
        mutant
        for mutant in documents[anchor.spec]["mutants"]
        if mutant["label"] == anchor.label and mutant["file"] == anchor.file
    ]
    assert len(matches) == 1, (anchor.spec, anchor.label)
    return matches[0]


def _semantic_intent(rule: FragileRule, before: str, replacement: str) -> None:
    spec = Path(rule.spec).name
    if spec.startswith("row281_"):
        assert "receipt = run_vitest" in before and "receipt = None" in replacement
    elif spec.startswith("row297_"):
        assert "assert metrics ==" in before and "assert set(metrics)" in replacement
    elif spec.startswith("row325_"):
        assert "reap_seconds=9" in before and "reap_seconds=3" in replacement
    elif spec.startswith("row329_"):
        assert "13_262" in before and "5_000" in replacement
    elif spec.startswith("row338_"):
        old = int(re.search(rule.value_regex, before).group().replace("_", ""))
        new = int(re.search(r"(?<=timeout=)[0-9]+(?=[),])", replacement).group())
        assert new > old
    elif spec.startswith("row339_"):
        old = int(re.search(rule.value_regex, before).group().replace("_", ""))
        numbers = [int(value.replace("_", "")) for value in re.findall(r"[0-9][0-9_]*", replacement)]
        assert numbers and numbers[-1] < old
    # row348_ had a branch here for M4; the mutant was retired at row 531 with
    # the literal it severed, so no rule for that spec reaches this function.
    elif spec.startswith("v3_66_1111_"):
        assert "5400" in before and "200" in replacement
    elif spec.startswith("v3_66_1204_"):
        assert "test_v3_66_1054" in before and "omitted" in replacement
    elif spec.startswith("v3_66_1226_"):
        assert replacement in {
            "_CONTENTION_FACTOR = 1.0",
            "_GOVERNING_BOUND_S = 600.0",
            '"registration_receipt_drift_before_go_refuses_release/wait":                 (0.5, 7),',
            "_MIN_BUDGET_S = 0.0",
        }
    elif spec.startswith("v3_66_1231_"):
        assert replacement in {
            "W1_CLEANUP_SECONDS=$W1_GATE_SECONDS",
            "_W1_RUNNER_STRETCH_FACTOR = 1.0",
            "_W1_SETTLEMENT_MEASURED_S = 0.1",
            "_W1_RUNNER_RESERVE_S = 0.0",
        }
    elif spec.startswith("v3_66_1239_"):
        assert "unknown.append" in before and "pass  # nothing to report" in replacement
    elif spec.startswith("v3_66_1241_"):
        assert replacement in {
            "W1_OWNER_OBSERVATION_SECONDS=1\n",
            "_W1_OBSERVATION_MEASURED_S = 0.0300\n",
        }
    else:  # pragma: no cover - a registry addition must add its semantic proof
        raise AssertionError(f"no intent proof for {spec}")


def test_every_converted_spec_resolves_once_and_preserves_its_original_intent():
    current_documents = _current_documents()
    current_anchors = _anchors(current_documents)
    adoption_documents = _adoption_documents()
    for rule in _FRAGILE_RULES:
        anchor = _rule_anchor(rule, current_anchors)
        assert anchor.field == "old_regex", (
            f"{anchor.spec}::{anchor.label} remains a literal fragile anchor"
        )
        source = (_REPO / anchor.file).read_text(encoding="utf-8")
        match = _one_regex(anchor.text, source, f"converted {anchor.spec}::{anchor.label}")
        before = match.group(0)
        base = _base_mutant(adoption_documents, anchor)
        base_fields = set(base) & {"old", "old_regex"}
        assert len(base_fields) == 1
        base_text = base[next(iter(base_fields))]
        assert before.endswith("\n") == base_text.endswith("\n"), (
            f"{anchor.spec}::{anchor.label} changed its historical newline boundary"
        )
        assert anchor.new == base["new"], (
            f"{anchor.spec}::{anchor.label} changed its original literal mutation"
        )
        mutated = source[: match.start()] + anchor.new + source[match.end() :]
        assert mutated != source
        assert mutated[match.start() : match.start() + len(anchor.new)] == anchor.new
        assert anchor.new != before
        _semantic_intent(rule, before, anchor.new)
