"""Row 988: Terminal Visual Artifact & Schema Drift Diff Inspector.

Provides inspection, anomaly detection, visual artifact diffing (content vs
formatting-only styling), and hierarchical schema drift detection at any depth
for terminal UI frames, streaming telemetry, and CLI outputs.

PAYLOAD RULE (extract_json_payloads; nothing else decides what is compared):
scan the ANSI-stripped frame once. Depth 0 is plain text ('"' is not special
there); '{' or '[' at depth 0 opens a group. Inside a group JSON strings apply
('"' opens one, a backslash escapes the next char, the next unescaped '"' ends
it; brackets in strings are ignored) and a closer must match the innermost
opener. UNPARSABLE if: a '}'/']' at depth 0; a closer of the wrong type; the
frame ends inside a group or a string; a group json.loads (strict) rejects
that is not a plain label (no '"', no nested '{'/'[': [INFO] [12:34:56] [1/3]
{ok}; labels are text); a group json.loads cannot hold (too deep, integer too
long); a group right after '"' and ':' at depth 0, whitespace allowed around
the ':' (a member value: a window into a payload). Otherwise every depth-0
group that decodes is a payload, in frame order; nothing below depth 0 is a
candidate.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

# Standard ANSI escape sequence matching OSC, CSI, and standard single-character codes
ANSI_ESCAPE_RE = re.compile(
    r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)|\x1B\[[0-?]*[ -/]*[@-~]|\x1B[@-Z\\-_]"
)
ANSI_RESET_RE = re.compile(r"\x1B\[(?:0;?0?|00?|39|49)?m")
# At depth 0 only brackets matter.
_BRACKET_RE = re.compile(r"[{}\[\]]")
# Inside a group: a whole JSON string, a '"' whose string the frame never ends,
# or a bracket.
_GROUP_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|"|[{}\[\]]', re.DOTALL)
_OPENER_OF = {"}": "{", "]": "["}


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences (CSI, OSC, and standard controls) from the string."""
    return ANSI_ESCAPE_RE.sub("", text)


def _is_member_value(text: str, start: int) -> bool:
    """True when the depth-0 text before the group at ``start`` ends with
    ``"`` and ``:`` (whitespace allowed): ``"job": {...}``."""
    j = start - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    if j < 0 or text[j] != ":":
        return False
    j -= 1
    while j >= 0 and text[j].isspace():
        j -= 1
    return j >= 0 and text[j] == '"'


def _is_plain_label(group: str) -> bool:
    """True for a group with no ``"`` and no nested ``{``/``[``: ``[INFO]``."""
    return '"' not in group and not _BRACKET_RE.search(group, 1, len(group) - 1)


def extract_json_payloads(text: str) -> tuple[str, list]:
    """Every JSON payload in a terminal frame, by the PAYLOAD RULE in the
    module docstring: ``("found", [payload, ...])`` in frame order;
    ``("unparsable", [])`` when the rule says so; ``("absent", [])`` when the
    frame is text (plain labels included) and nothing else.

    ANSI sequences go first: an ESC byte can never occur inside valid JSON, so
    stripping them only rescues colourised payloads. A value nested in a group
    belongs to that group; it is never tried on its own, so a broken or cut
    off payload can only make the frame unparsable, never pass one of its
    values off as the payload. What the rule cannot see: a frame cut at both
    ends that shows only whole values from inside a larger payload, none of
    them a member value (a window onto array elements), reads as payloads of
    their own.
    """
    clean = strip_ansi(text)
    payloads: list = []
    stack: List[str] = []  # open brackets of the current depth-0 group
    start = pos = 0  # where that group opened; where the scan goes on
    while True:
        m = (_GROUP_TOKEN_RE if stack else _BRACKET_RE).search(clean, pos)
        if m is None:
            break
        tok, pos = m.group(), m.end()
        if tok[0] == '"':
            if len(tok) == 1:  # a string the frame never ends
                return "unparsable", []
            continue  # a whole string: its brackets are string content
        if tok in "{[":
            if not stack:
                start = m.start()
                if _is_member_value(clean, start):
                    return "unparsable", []
            stack.append(tok)
            continue
        if not stack or stack.pop() != _OPENER_OF[tok]:
            return "unparsable", []  # a closer at depth 0, or of the wrong type
        if stack:
            continue
        group = clean[start:pos]
        try:
            payloads.append(json.loads(group))
        except json.JSONDecodeError:
            if not _is_plain_label(group):
                return "unparsable", []
        except (ValueError, RecursionError):
            return "unparsable", []  # JSON the decoder cannot hold
    if stack:
        return "unparsable", []  # the frame ends inside a group
    return ("found", payloads) if payloads else ("absent", [])


@dataclass
class VisualAnomaly:
    """Represents a visual rendering defect or unexpected sequence in terminal output."""

    line_no: int
    anomaly_type: str
    description: str
    snippet: str


@dataclass
class TerminalVisualArtifact:
    """Encapsulates a parsed terminal visual output snapshot or frame."""

    raw: str
    clean_text: str
    lines: List[str]
    raw_lines: List[str]
    line_count: int
    max_width: int
    has_ansi: bool
    anomalies: List[VisualAnomaly] = field(default_factory=list)


@dataclass
class DiffLine:
    """One line within a visual artifact diff."""

    line_no: int
    diff_type: str  # "ADD", "REMOVE", "CHANGE", "SAME"
    expected: Optional[str]
    actual: Optional[str]
    formatting_changed: bool = False


@dataclass
class VisualArtifactDiff:
    """Result of diffing two terminal visual artifacts."""

    is_drifted: bool
    content_drift: bool
    formatting_only_drift: bool
    diff_lines: List[DiffLine] = field(default_factory=list)

    def render_diff(self, colorize: bool = False) -> str:
        """Render diff lines in unified-diff format."""
        out: List[str] = []
        for dl in self.diff_lines:
            if dl.diff_type == "SAME":
                out.append(f"  {dl.actual}")
            elif dl.diff_type == "REMOVE":
                tag = "\033[31m-\033[0m" if colorize else "-"
                out.append(f"{tag} {dl.expected}")
            elif dl.diff_type == "ADD":
                tag = "\033[32m+\033[0m" if colorize else "+"
                out.append(f"{tag} {dl.actual}")
            elif dl.diff_type == "CHANGE":
                if dl.expected is not None:
                    tag = "\033[31m-\033[0m" if colorize else "-"
                    out.append(f"{tag} {dl.expected}")
                if dl.actual is not None:
                    tag = "\033[32m+\033[0m" if colorize else "+"
                    out.append(f"{tag} {dl.actual}")
        return "\n".join(out)


@dataclass
class SchemaDriftItem:
    """Single observed drift within a structured schema or payload."""

    path: str
    drift_type: str  # "FIELD_ADDED", "FIELD_REMOVED", "TYPE_CHANGED", "VALUE_DRIFT", "ELEMENT_ADDED", "ELEMENT_REMOVED"
    baseline_value: Any = None
    target_value: Any = None
    message: str = ""


@dataclass
class SchemaDriftReport:
    """Collection of schema drift findings between baseline and target payloads."""

    has_drift: bool
    drift_count: int
    drifts: List[SchemaDriftItem] = field(default_factory=list)

    def render_report(self) -> str:
        """Render a readable plain-text summary of schema drift findings."""
        if not self.has_drift or not self.drifts:
            return "Schema Drift Report: No drift detected."

        lines = [f"Schema Drift Report: {len(self.drifts)} drifts detected:"]
        for d in self.drifts:
            if d.drift_type == "FIELD_ADDED":
                lines.append(f"  [+] FIELD_ADDED   at '{d.path}' -> {repr(d.target_value)}")
            elif d.drift_type == "FIELD_REMOVED":
                lines.append(f"  [-] FIELD_REMOVED at '{d.path}' (was {repr(d.baseline_value)})")
            elif d.drift_type == "TYPE_CHANGED":
                b_type = type(d.baseline_value).__name__
                t_type = type(d.target_value).__name__
                lines.append(f"  [~] TYPE_CHANGED  at '{d.path}' from {b_type} to {t_type}")
            elif d.drift_type == "VALUE_DRIFT":
                lines.append(f"  [~] VALUE_DRIFT   at '{d.path}': {repr(d.baseline_value)} -> {repr(d.target_value)}")
            else:
                lines.append(f"  [*] {d.drift_type} at '{d.path}'")
        return "\n".join(lines)


@dataclass
class TerminalInspectionReport:
    """Unified inspection combining visual artifact parsing, diffing, and schema drift.

    ``schema_status`` says why ``schema_drift`` is or is not set (payloads as
    found by ``extract_json_payloads``): ``"not_requested"`` (no baseline
    frame); ``"unparsable"`` (a frame is unparsable by the PAYLOAD RULE: a
    broken or cut-off payload); ``"no_payload"`` (otherwise, a frame holds no
    payload, only text and plain labels); ``"payload_count_changed"`` (both
    hold payloads, but not the same number, so none are paired up);
    ``"compared"`` (both hold N payloads,
    compared pairwise in frame order; ``has_drift`` False when all match; with
    N == 1 drift paths are the payload's own, with N > 1 they start with the
    payload's index, ``payload[i]``).
    ``schema_drift`` is None for every status except ``"compared"``.
    """

    visual_artifact: TerminalVisualArtifact
    visual_diff: Optional[VisualArtifactDiff] = None
    schema_drift: Optional[SchemaDriftReport] = None
    schema_status: str = "not_requested"


def inspect_visual_artifact(
    text: str, max_allowed_width: Optional[int] = None
) -> TerminalVisualArtifact:
    """Parse raw terminal text, extract clean lines, and identify visual anomalies."""
    norm_text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = norm_text.split("\n")
    clean_lines = [strip_ansi(line) for line in raw_lines]
    clean_text = "\n".join(clean_lines)

    has_ansi = bool(ANSI_ESCAPE_RE.search(text))
    max_width = max((len(line) for line in clean_lines), default=0)
    anomalies: List[VisualAnomaly] = []

    for idx, (raw_l, clean_l) in enumerate(zip(raw_lines, clean_lines), start=1):
        # 1. Unclosed ANSI sequences (color opened without reset on the line)
        opens = []
        resets = []
        for m in re.finditer(r"\x1B\[[0-9;]*m", raw_l):
            if ANSI_RESET_RE.fullmatch(m.group(0)):
                resets.append(m.start())
            else:
                opens.append(m.start())
        if opens:
            last_open = max(opens)
            resets_after = [r for r in resets if r > last_open]
            if not resets_after:
                anomalies.append(
                    VisualAnomaly(
                        line_no=idx,
                        anomaly_type="UNCLOSED_ANSI",
                        description=f"Line {idx} opens an ANSI style without a reset before line end",
                        snippet=raw_l[:60],
                    )
                )

        # 2. Raw control characters (bell, null, unexpected ASCII controls < 32 excluding \t, \n)
        for char in clean_l:
            if ord(char) < 32 and char not in ("\t", "\n"):
                anomalies.append(
                    VisualAnomaly(
                        line_no=idx,
                        anomaly_type="RAW_CONTROL_CHAR",
                        description=f"Raw non-printable control char (hex {ord(char):#02x}) at line {idx}",
                        snippet=repr(clean_l[:60]),
                    )
                )
                break

        # 3. Line width overflow
        if max_allowed_width is not None and len(clean_l) > max_allowed_width:
            anomalies.append(
                VisualAnomaly(
                    line_no=idx,
                    anomaly_type="LINE_OVERFLOW",
                    description=f"Visual line width ({len(clean_l)}) exceeds limit ({max_allowed_width})",
                    snippet=clean_l[:60],
                )
            )

    return TerminalVisualArtifact(
        raw=text,
        clean_text=clean_text,
        lines=clean_lines,
        raw_lines=raw_lines,
        line_count=len(clean_lines),
        max_width=max_width,
        has_ansi=has_ansi,
        anomalies=anomalies,
    )


def diff_visual_artifacts(
    expected: str | TerminalVisualArtifact,
    actual: str | TerminalVisualArtifact,
) -> VisualArtifactDiff:
    """Compare two visual artifacts to identify content drift and formatting-only styling drift."""
    art_expected = (
        expected
        if isinstance(expected, TerminalVisualArtifact)
        else inspect_visual_artifact(expected)
    )
    art_actual = (
        actual
        if isinstance(actual, TerminalVisualArtifact)
        else inspect_visual_artifact(actual)
    )

    clean_match = art_expected.clean_text == art_actual.clean_text
    raw_match = art_expected.raw == art_actual.raw

    if raw_match:
        return VisualArtifactDiff(
            is_drifted=False,
            content_drift=False,
            formatting_only_drift=False,
            diff_lines=[
                DiffLine(line_no=i, diff_type="SAME", expected=l, actual=l)
                for i, l in enumerate(art_actual.lines, start=1)
            ],
        )

    content_drift = not clean_match
    formatting_only_drift = clean_match and not raw_match

    # Generate line diffs
    diff_lines: List[DiffLine] = []
    matcher = difflib.SequenceMatcher(None, art_expected.lines, art_actual.lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                exp_raw = art_expected.raw_lines[i1 + offset]
                act_raw = art_actual.raw_lines[j1 + offset]
                fmt_changed = exp_raw != act_raw
                diff_lines.append(
                    DiffLine(
                        line_no=j1 + offset + 1,
                        diff_type="SAME",
                        expected=art_expected.lines[i1 + offset],
                        actual=art_actual.lines[j1 + offset],
                        formatting_changed=fmt_changed,
                    )
                )
        elif tag == "delete":
            for offset in range(i2 - i1):
                diff_lines.append(
                    DiffLine(
                        line_no=i1 + offset + 1,
                        diff_type="REMOVE",
                        expected=art_expected.lines[i1 + offset],
                        actual=None,
                    )
                )
        elif tag == "insert":
            for offset in range(j2 - j1):
                diff_lines.append(
                    DiffLine(
                        line_no=j1 + offset + 1,
                        diff_type="ADD",
                        expected=None,
                        actual=art_actual.lines[j1 + offset],
                    )
                )
        elif tag == "replace":
            for offset in range(max(i2 - i1, j2 - j1)):
                exp = art_expected.lines[i1 + offset] if i1 + offset < i2 else None
                act = art_actual.lines[j1 + offset] if j1 + offset < j2 else None
                diff_lines.append(
                    DiffLine(
                        line_no=j1 + min(offset, j2 - j1 - 1) + 1,
                        diff_type="CHANGE",
                        expected=exp,
                        actual=act,
                    )
                )

    return VisualArtifactDiff(
        is_drifted=True,
        content_drift=content_drift,
        formatting_only_drift=formatting_only_drift,
        diff_lines=diff_lines,
    )


def inspect_schema_drift(
    baseline: Any, target: Any, path: str = ""
) -> SchemaDriftReport:
    """Inspect structural and type drift between baseline and target schemas/dictionaries.

    Findings come depth-first: at a dict, its added then removed fields (each
    sorted), then each common field's findings in key order; at a list, each
    common element's findings in index order, then its added or removed
    elements. The walk keeps its own stack, so a deeply nested payload cannot
    raise RecursionError. Two NaN floats count as unchanged (NaN != NaN).
    """
    drifts: List[SchemaDriftItem] = []

    def _format_path(prefix: str, key: str | int) -> str:
        if isinstance(key, int):
            return f"{prefix}[{key}]" if prefix else f"[{key}]"
        return f"{prefix}.{key}" if prefix else str(key)

    # ("cmp", b, t, path) compares two values; ("emit", item) records a finding
    # that must follow everything pushed after it (a list's tail elements).
    stack: List[tuple] = [("cmp", baseline, target, path)]
    while stack:
        work = stack.pop()
        if work[0] == "emit":
            drifts.append(work[1])
            continue
        _, b, t, curr_path = work
        if type(b) is not type(t):
            drifts.append(
                SchemaDriftItem(
                    path=curr_path,
                    drift_type="TYPE_CHANGED",
                    baseline_value=b,
                    target_value=t,
                    message=f"Type drifted from {type(b).__name__} to {type(t).__name__}",
                )
            )
            continue

        if isinstance(b, dict):
            b_keys = set(b.keys())
            t_keys = set(t.keys())

            # Added fields
            for k in sorted(t_keys - b_keys):
                p = _format_path(curr_path, k)
                drifts.append(
                    SchemaDriftItem(
                        path=p,
                        drift_type="FIELD_ADDED",
                        baseline_value=None,
                        target_value=t[k],
                        message=f"Field '{p}' added to schema",
                    )
                )

            # Removed fields
            for k in sorted(b_keys - t_keys):
                p = _format_path(curr_path, k)
                drifts.append(
                    SchemaDriftItem(
                        path=p,
                        drift_type="FIELD_REMOVED",
                        baseline_value=b[k],
                        target_value=None,
                        message=f"Field '{p}' removed from schema",
                    )
                )

            # Common fields: pushed in reverse so they are compared in key order
            for k in sorted(b_keys & t_keys, reverse=True):
                stack.append(("cmp", b[k], t[k], _format_path(curr_path, k)))

        elif isinstance(b, list):
            common_len = min(len(b), len(t))
            tail: List[SchemaDriftItem] = []
            for i in range(common_len, len(t)):
                p = _format_path(curr_path, i)
                tail.append(
                    SchemaDriftItem(
                        path=p,
                        drift_type="ELEMENT_ADDED",
                        baseline_value=None,
                        target_value=t[i],
                        message=f"Element added at index {i}",
                    )
                )
            for i in range(common_len, len(b)):
                p = _format_path(curr_path, i)
                tail.append(
                    SchemaDriftItem(
                        path=p,
                        drift_type="ELEMENT_REMOVED",
                        baseline_value=b[i],
                        target_value=None,
                        message=f"Element removed at index {i}",
                    )
                )
            # The tail is recorded after every common element's findings.
            for item in reversed(tail):
                stack.append(("emit", item))
            for i in reversed(range(common_len)):
                stack.append(("cmp", b[i], t[i], _format_path(curr_path, i)))

        elif b != t and not (isinstance(b, float) and math.isnan(b) and math.isnan(t)):
            drifts.append(
                SchemaDriftItem(
                    path=curr_path,
                    drift_type="VALUE_DRIFT",
                    baseline_value=b,
                    target_value=t,
                    message=f"Value changed from {repr(b)} to {repr(t)}",
                )
            )

    return SchemaDriftReport(
        has_drift=len(drifts) > 0,
        drift_count=len(drifts),
        drifts=drifts,
    )


class TerminalDriftInspector:
    """Unified engine to inspect terminal UI visual frames and embedded schema payloads."""

    def __init__(self, max_allowed_width: Optional[int] = 120):
        self.max_allowed_width = max_allowed_width

    def inspect_terminal_frame(
        self,
        frame_text: str,
        baseline_frame_text: Optional[str] = None,
    ) -> TerminalInspectionReport:
        """Inspect a single frame and optionally diff against a baseline frame."""
        artifact = inspect_visual_artifact(
            frame_text, max_allowed_width=self.max_allowed_width
        )

        visual_diff: Optional[VisualArtifactDiff] = None
        schema_drift: Optional[SchemaDriftReport] = None
        schema_status = "not_requested"

        if baseline_frame_text is not None:
            visual_diff = diff_visual_artifacts(baseline_frame_text, frame_text)

            # Every JSON payload embedded in each frame, compared pairwise in frame order
            base_state, base_payloads = extract_json_payloads(baseline_frame_text)
            act_state, act_payloads = extract_json_payloads(frame_text)
            if "unparsable" in (base_state, act_state):
                schema_status = "unparsable"
            elif "absent" in (base_state, act_state):
                schema_status = "no_payload"
            elif len(base_payloads) != len(act_payloads):
                schema_status = "payload_count_changed"
            else:
                if len(act_payloads) == 1:
                    schema_drift = inspect_schema_drift(base_payloads[0], act_payloads[0])
                else:
                    schema_drift = inspect_schema_drift(
                        base_payloads, act_payloads, path="payload"
                    )
                schema_status = "compared"

        return TerminalInspectionReport(
            visual_artifact=artifact,
            visual_diff=visual_diff,
            schema_drift=schema_drift,
            schema_status=schema_status,
        )
