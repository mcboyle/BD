"""The newest CHANGELOG release entry must not contain train placeholders."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

# The accepted separator is derived from the WRITERS, not from the sample in
# front of us: toolchain/bin/bd-cut cut_release emits " -- " and
# toolchain/bin/bd-bump emits " - ".  A gate whose grammar is narrower than its
# producer's judges the wrong entry.  Horizontal whitespace only -- \s would
# cross newlines and let a title-less "## v1.2.3 --" header absorb the entry
# below it, which is a fail-open in the opposite direction.
_SEPARATOR = r"[ \t]*-{1,2}[ \t]+"
_VERSION = r"## v\d+\.\d+\.\d+"
_RELEASE_HEADER = re.compile(
    r"(?m)^" + _VERSION + r"(?:" + _SEPARATOR + r"[^\n]+)?$"
)
_RELEASE_TITLE = re.compile(
    r"^" + _VERSION + _SEPARATOR + r"(?P<text>.*)$"
)
_BULLET_START = re.compile(r"^[ \t]*[-*+][ \t]+(?P<text>.*)$")
# The BROAD population of version headings, independent of the separator
# grammar above: it is what makes a SKIPPED newest header measurable.
_VERSION_HEADING = re.compile(r"(?m)^##\s*v\d")
_MECHANISM_MARKER = re.compile(
    r"##[ \t]+MECHANISM(?=[ \t:(]|$)", re.MULTILINE
)
_TEXT_PLACEHOLDERS = ("DRAFT:", "EDIT THIS TITLE")
_ALL_PLACEHOLDERS = (*_TEXT_PLACEHOLDERS, "## MECHANISM")

_SHIPPED_DRAFT_ENTRY = """\
## v3.66.1457 - DRAFT: 1 reviewed patches (EDIT THIS TITLE AND THESE BULLETS BEFORE COMMITTING)

Train: 1 refute-first-reviewed worker patches.

- W4-LOGINSESSION: ## MECHANISM (measured on a local fixture host through BD's real carry chain) brazzers logs in on `site-ma.brazzers.com`; the scene lives on `www.brazzers.com` -- SIBLING subdomains. A session cookie the login host issues host-only (no Domain attribute) is never sent to a sibling host: RFC 6265 5.1.

"""

_CLEAN_ENTRY = """\
## v3.66.1457 - sibling-host sessions keep their cookie scope

Train: 1 refute-first-reviewed worker patch.

- W4-LOGINSESSION: A host-only login cookie is not sent to sibling hosts, so the downloader carries the authenticated session through the supported login flow.

"""

_QUOTED_MARKERS_ENTRY = """\
## v3.66.1462 - fourteen rows filed, and two release notes say what they should have said

Train: the correction documents the previously shipped placeholder without publishing it as structure.

- One release note carried its own placeholder -- "DRAFT: 1 reviewed patches (EDIT THIS TITLE AND THESE BULLETS BEFORE COMMITTING)" -- as its title.
- The bullet scanner rejects the quoted "## MECHANISM" heading marker.

"""

_BARE_HEADER_ENTRY = """\
## v3.66.1107

Legacy untitled release entry remains measurable.

"""

_STRUCTURAL_MARKER_CASES = (
    pytest.param(
        "title",
        "DRAFT:",
        "## v3.66.2000 - DRAFT: replacement title\n\nClean body.\n",
        id="title-draft",
    ),
    pytest.param(
        "title",
        "DRAFT:",
        "## v3.66.2000 -  DRAFT: replacement title\n\nClean body.\n",
        id="title-two-space-draft",
    ),
    pytest.param(
        "bullet",
        "DRAFT:",
        "## v3.66.2000 - clean title\n\n- DRAFT: replacement bullet\n",
        id="bullet-draft",
    ),
    pytest.param(
        "paragraph",
        "DRAFT:",
        "## v3.66.2000 - clean title\n\nDRAFT: replacement paragraph\n",
        id="paragraph-draft",
    ),
    pytest.param(
        "title",
        "EDIT THIS TITLE",
        "## v3.66.2000 - EDIT THIS TITLE before release\n\nClean body.\n",
        id="title-edit-title",
    ),
    pytest.param(
        "bullet",
        "EDIT THIS TITLE",
        "## v3.66.2000 - clean title\n\n* EDIT THIS TITLE before release\n",
        id="bullet-edit-title",
    ),
    pytest.param(
        "paragraph",
        "EDIT THIS TITLE",
        "## v3.66.2000 - clean title\n\nEDIT THIS TITLE before release\n",
        id="paragraph-edit-title",
    ),
    pytest.param(
        "title",
        "## MECHANISM",
        "## v3.66.2000 - ## MECHANISM\n\nClean body.\n",
        id="title-mechanism",
    ),
    pytest.param(
        "bullet",
        "## MECHANISM",
        "## v3.66.2000 - clean title\n\n+ ## MECHANISM placeholder\n",
        id="bullet-mechanism",
    ),
    pytest.param(
        "paragraph",
        "## MECHANISM",
        "## v3.66.2000 - clean title\n\n## MECHANISM\n\nClean later paragraph.\n",
        id="paragraph-mechanism",
    ),
)

# Every separator the WRITERS emit, derived from the producers rather than from
# the sample: toolchain/bin/bd-cut cut_release writes " -- " and
# toolchain/bin/bd-bump writes " - ".  Each case stacks the malformed newest
# entry ABOVE a clean second entry, because a single-entry fixture correctly
# returns UNKNOWN and would pass while the silent-next defect is fully alive.
_SEPARATOR_ESCAPE_CASES = (
    pytest.param(
        "-\t",
        "## v3.66.2000 -\tDRAFT: replacement title\n\nClean body.\n",
        "## v3.66.2000 -\tDRAFT: replacement title",
        "DRAFT:",
        id="hyphen-tab",
    ),
    pytest.param(
        "  - ",
        "## v3.66.2000  - DRAFT: replacement title\n\nClean body.\n",
        "## v3.66.2000  - DRAFT: replacement title",
        "DRAFT:",
        id="two-space-hyphen",
    ),
    pytest.param(
        " -- ",
        "## v3.66.2000 -- DRAFT: replacement title\n\nClean body.\n",
        "## v3.66.2000 -- DRAFT: replacement title",
        "DRAFT:",
        id="double-hyphen",
    ),
)

# Newest headings that NO producer emits.  Widening the separator grammar must
# not silently absorb them either: an unidentifiable newest header is UNKNOWN.
_UNRECOGNISED_NEWEST_CASES = (
    pytest.param(
        "## v3.66.2000 DRAFT: replacement title\n\nClean body.\n",
        id="no-separator",
    ),
    pytest.param(
        "## v3.66.2000 --- DRAFT: replacement title\n\nClean body.\n",
        id="triple-hyphen",
    ),
)

_CLEAN_DOUBLE_HYPHEN_ENTRY = """\
## v3.66.2001 -- the newest entry uses the separator bd-cut writes

Train: 1 reviewed worker patch.

- W6-DRAFTGATEF: the newest release header is identified by every separator its producers emit.

"""

_TITLE_PROSE_ENTRY = """\
## v3.66.2000 - release notes quote "DRAFT:" and EDIT THIS TITLE as examples

Clean body.

"""

_BULLET_PROSE_ENTRY = """\
## v3.66.2000 - release notes explain the mechanism marker

- W4: ## MECHANISM is the placeholder that the gate rejects at a structural start.

"""


def _newest_release_entry(changelog: str) -> str:
    matches = list(_RELEASE_HEADER.finditer(changelog))
    assert matches, "CHANGELOG measurement UNKNOWN: no release entry was found"
    start = matches[0].start()
    # A version heading ABOVE the first recognised entry means the newest entry
    # was not identified.  Silently judging the next one is the fail-open this
    # gate exists to prevent, so the measurement is UNKNOWN (A2), never OK.
    skipped = _VERSION_HEADING.search(changelog, 0, start)
    if skipped is not None:
        raise AssertionError(
            "CHANGELOG measurement UNKNOWN: an unrecognised release heading "
            "precedes the newest identified entry: "
            f"{changelog[skipped.start():].splitlines()[0]!r}"
        )
    end = matches[1].start() if len(matches) > 1 else len(changelog)
    return changelog[start:end]


def _entry_title(entry: str) -> str:
    entry_title, *_ = entry.splitlines()
    return entry_title


def _structural_starts(entry: str) -> list[tuple[str, str]]:
    lines = entry.splitlines()
    starts: list[tuple[str, str]] = []
    title_match = _RELEASE_TITLE.fullmatch(_entry_title(entry))
    if title_match is not None:
        starts.append(("title", title_match.group("text")))

    previous_blank = True
    for line in lines[1:]:
        bullet_match = _BULLET_START.fullmatch(line)
        if bullet_match is not None:
            starts.append(("bullet", bullet_match.group("text")))
        elif previous_blank and line.strip():
            starts.append(("paragraph", line.lstrip(" \t")))
        previous_blank = not line.strip()
    return starts


def _marker_at_start(text: str) -> str | None:
    text = text.lstrip(" \t")
    for marker in _TEXT_PLACEHOLDERS:
        if text.startswith(marker):
            return marker
    if _MECHANISM_MARKER.match(text):
        return "## MECHANISM"
    return None


def _placeholder_markers(entry: str) -> list[str]:
    anchored = [
        marker
        for _, text in _structural_starts(entry)
        if (marker := _marker_at_start(text)) is not None
    ]
    if not anchored:
        return []

    markers = [marker for marker in _TEXT_PLACEHOLDERS if marker in entry]
    if _MECHANISM_MARKER.search(entry):
        markers.append("## MECHANISM")
    return markers


def _assert_changelog_ready(changelog: Path) -> str:
    entry = _newest_release_entry(changelog.read_text(encoding="utf-8"))
    markers = _placeholder_markers(entry)
    assert not markers, (
        f"newest CHANGELOG entry contains draft placeholder marker(s): {markers}"
    )
    return entry


def test_shipped_draft_entry_is_refused(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_SHIPPED_DRAFT_ENTRY, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == _SHIPPED_DRAFT_ENTRY

    entry = _newest_release_entry(changelog.read_text(encoding="utf-8"))
    assert entry.count("\n## v") == 0
    markers = _placeholder_markers(entry)
    assert len(markers) == 3, "the defective fixture did not fire all three markers"
    assert markers == ["DRAFT:", "EDIT THIS TITLE", "## MECHANISM"]
    with pytest.raises(AssertionError, match="draft placeholder marker") as refused:
        _assert_changelog_ready(changelog)
    assert "DRAFT:" in str(refused.value)
    assert "EDIT THIS TITLE" in str(refused.value)
    assert "## MECHANISM" in str(refused.value)


def test_corrected_entry_is_accepted(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_CLEAN_ENTRY, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == _CLEAN_ENTRY

    entry = _assert_changelog_ready(changelog)
    assert entry == _CLEAN_ENTRY
    assert _placeholder_markers(entry) == []


def test_only_the_newest_release_entry_is_judged(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    text = _CLEAN_ENTRY + "\n" + _SHIPPED_DRAFT_ENTRY
    changelog.write_text(text, encoding="utf-8")
    assert len(_RELEASE_HEADER.findall(text)) == 2

    entry = _assert_changelog_ready(changelog)
    assert entry == _CLEAN_ENTRY + "\n"
    assert _placeholder_markers(entry) == []
    assert len(_placeholder_markers(_newest_release_entry(_SHIPPED_DRAFT_ENTRY))) == 3


def test_quoted_placeholder_names_in_newest_body_are_accepted(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_QUOTED_MARKERS_ENTRY, encoding="utf-8")
    entry = _newest_release_entry(changelog.read_text(encoding="utf-8"))
    title, body = entry.split("\n", 1)
    assert title == "## v3.66.1462 - fourteen rows filed, and two release notes say what they should have said"
    assert [body.count(marker) for marker in (*_TEXT_PLACEHOLDERS, "## MECHANISM")] == [1, 1, 1]
    assert _structural_starts(entry) == [
        ("title", "fourteen rows filed, and two release notes say what they should have said"),
        ("paragraph", "Train: the correction documents the previously shipped placeholder without publishing it as structure."),
        ("bullet", 'One release note carried its own placeholder -- "DRAFT: 1 reviewed patches (EDIT THIS TITLE AND THESE BULLETS BEFORE COMMITTING)" -- as its title.'),
        ("bullet", 'The bullet scanner rejects the quoted "## MECHANISM" heading marker.'),
    ]

    assert _assert_changelog_ready(changelog) == _QUOTED_MARKERS_ENTRY


@pytest.mark.parametrize(
    ("position", "marker", "entry"),
    _STRUCTURAL_MARKER_CASES,
)
def test_each_marker_is_refused_at_each_structural_start(
    tmp_path: Path,
    position: str,
    marker: str,
    entry: str,
) -> None:
    assert len(_STRUCTURAL_MARKER_CASES) == 10
    assert {
        (case.values[0], case.values[1]) for case in _STRUCTURAL_MARKER_CASES
    } == {
        (position_name, marker_name)
        for position_name in ("title", "bullet", "paragraph")
        for marker_name in (*_TEXT_PLACEHOLDERS, "## MECHANISM")
    }
    assert entry.count(marker) == 1
    lines = entry.splitlines()
    if position == "title":
        title_prefix = "## v3.66.2000 - "
        assert lines[0].startswith(title_prefix)
        assert lines[0][len(title_prefix):].lstrip(" \t").startswith(marker)
    elif position == "bullet":
        assert re.match(r"^[ \t]*[-*+][ \t]+", lines[2])
        assert re.sub(r"^[ \t]*[-*+][ \t]+", "", lines[2]).startswith(marker)
    else:
        assert position == "paragraph"
        assert lines[1] == ""
        assert lines[2].startswith(marker)

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(entry, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == entry

    assert _placeholder_markers(_newest_release_entry(entry)) == [marker]
    with pytest.raises(AssertionError, match="draft placeholder marker") as refused:
        _assert_changelog_ready(changelog)
    assert marker in str(refused.value)


_HARDENED_MARKER_CASES = (
        pytest.param(
            "two-space title",
            "DRAFT:",
            "## v3.66.2000 -  DRAFT: replacement title\n\nClean body.\n",
            "## v3.66.2000 -  DRAFT: replacement title",
            id="title-two-spaces",
        ),
        pytest.param(
            "tab title",
            "DRAFT:",
            "## v3.66.2000 - \tDRAFT: replacement title\n\nClean body.\n",
            "## v3.66.2000 - \tDRAFT: replacement title",
            id="title-tab",
        ),
        pytest.param(
            "two-space mechanism title",
            "## MECHANISM",
            "## v3.66.2000 -  ## MECHANISM\n\nClean body.\n",
            "## v3.66.2000 -  ## MECHANISM",
            id="title-two-space-mechanism",
        ),
        pytest.param(
            "first body paragraph",
            "DRAFT:",
            "## v3.66.2000 - clean title\nDRAFT: replacement paragraph\n",
            "DRAFT: replacement paragraph",
            id="paragraph-directly-under-title",
        ),
)


@pytest.mark.parametrize(
    ("case_name", "marker", "entry", "expected_marker_line"),
    _HARDENED_MARKER_CASES,
)
def test_whitespace_and_first_body_line_markers_are_refused(
    tmp_path: Path,
    case_name: str,
    marker: str,
    entry: str,
    expected_marker_line: str,
) -> None:
    assert len(entry.splitlines()) >= 2
    assert expected_marker_line in entry.splitlines()
    assert entry.count(marker) == 1

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(entry, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == entry

    detected = _placeholder_markers(_newest_release_entry(entry))
    assert detected == [marker], f"{case_name} placeholder was not judged: {detected}"
    with pytest.raises(AssertionError, match="draft placeholder marker"):
        _assert_changelog_ready(changelog)


def test_all_distinct_structural_marker_sites_are_judged() -> None:
    sites = {
        case.values[2]: (case.id, case.values[1])
        for case in (*_STRUCTURAL_MARKER_CASES, *_HARDENED_MARKER_CASES)
    }
    # Each separator site is measured STACKED above a clean second entry: a
    # single-entry site returns UNKNOWN and would report a false escape.
    sites.update(
        {
            case.values[1] + "\n" + _CLEAN_ENTRY: (case.id, case.values[3])
            for case in _SEPARATOR_ESCAPE_CASES
        }
    )
    assert len(sites) == 16

    judged = {
        entry: _placeholder_markers(_newest_release_entry(entry))
        for entry in sites
    }
    escapes = [sites[entry][0] for entry, markers in judged.items() if not markers]
    assert len(judged) == 16
    assert escapes == []


def test_markers_inside_title_prose_are_accepted(tmp_path: Path) -> None:
    title = _TITLE_PROSE_ENTRY.splitlines()[0]
    assert title.startswith("## v3.66.2000 - release notes")
    assert title.count("DRAFT:") == 1
    assert title.count("EDIT THIS TITLE") == 1

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_TITLE_PROSE_ENTRY, encoding="utf-8")

    assert _assert_changelog_ready(changelog) == _TITLE_PROSE_ENTRY
    assert _placeholder_markers(_TITLE_PROSE_ENTRY) == []


def test_unquoted_marker_inside_bullet_prose_is_accepted(tmp_path: Path) -> None:
    bullet = _BULLET_PROSE_ENTRY.splitlines()[2]
    assert bullet.startswith("- W4: ")
    assert bullet.count("## MECHANISM") == 1

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_BULLET_PROSE_ENTRY, encoding="utf-8")

    assert _assert_changelog_ready(changelog) == _BULLET_PROSE_ENTRY
    assert _placeholder_markers(_BULLET_PROSE_ENTRY) == []


def test_bare_newest_release_header_is_not_skipped() -> None:
    text = _BARE_HEADER_ENTRY + _CLEAN_ENTRY
    assert text.count("\n## v") == 1

    entry = _newest_release_entry(text)

    assert entry == _BARE_HEADER_ENTRY


@pytest.mark.parametrize(
    ("separator", "newest_entry", "newest_title", "marker"),
    _SEPARATOR_ESCAPE_CASES,
)
def test_every_producer_separator_is_judged_above_a_clean_entry(
    tmp_path: Path,
    separator: str,
    newest_entry: str,
    newest_title: str,
    marker: str,
) -> None:
    text = newest_entry + "\n" + _CLEAN_ENTRY

    # PRECONDITION: the fixture really stacked TWO entries, malformed first.
    assert len(_VERSION_HEADING.findall(text)) == 2
    assert text.splitlines()[0] == newest_title
    assert separator in newest_title
    assert newest_entry.count(marker) == 1
    assert text.endswith(_CLEAN_ENTRY)
    assert _placeholder_markers(_newest_release_entry(_CLEAN_ENTRY)) == []

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == text

    entry = _newest_release_entry(text)
    assert entry.splitlines()[0] == newest_title, (
        f"the gate judged the wrong entry: newest header {newest_title!r} "
        f"was skipped; judged {entry.splitlines()[0]!r}"
    )
    assert len(_RELEASE_HEADER.findall(text)) == 2
    assert _placeholder_markers(entry) == [marker]
    with pytest.raises(AssertionError, match="draft placeholder marker") as refused:
        _assert_changelog_ready(changelog)
    assert marker in str(refused.value)


@pytest.mark.parametrize("newest_entry", _UNRECOGNISED_NEWEST_CASES)
def test_unrecognised_newest_header_is_unknown(
    tmp_path: Path,
    newest_entry: str,
) -> None:
    text = newest_entry + "\n" + _CLEAN_ENTRY

    # PRECONDITION: two headings present, but only the OLDER one is recognised
    # by the separator grammar -- exactly the shape that silently fell through.
    assert len(_VERSION_HEADING.findall(text)) == 2
    assert len(_RELEASE_HEADER.findall(text)) == 1
    assert text.splitlines()[0] == newest_entry.splitlines()[0]
    assert _placeholder_markers(_newest_release_entry(_CLEAN_ENTRY)) == []

    # DECIDING DETAIL: with a SINGLE entry the same input already returns
    # UNKNOWN, so a one-entry fixture cannot see this defect at all.
    with pytest.raises(AssertionError, match="no release entry was found"):
        _newest_release_entry(newest_entry)

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == text

    try:
        entry = _newest_release_entry(text)
    except AssertionError as unknown:
        assert "measurement UNKNOWN" in str(unknown)
        assert "v3.66.2000" in str(unknown)
    else:
        pytest.fail(
            "the newest release header was skipped silently; the gate judged "
            f"{entry.splitlines()[0]!r}"
        )

    with pytest.raises(AssertionError, match="measurement UNKNOWN"):
        _assert_changelog_ready(changelog)


def test_clean_double_hyphen_newest_entry_is_accepted(tmp_path: Path) -> None:
    """Negative control: widening must not make a clean newest entry refuse."""
    text = _CLEAN_DOUBLE_HYPHEN_ENTRY + "\n" + _SHIPPED_DRAFT_ENTRY
    assert len(_VERSION_HEADING.findall(text)) == 2
    assert len(_placeholder_markers(_newest_release_entry(_SHIPPED_DRAFT_ENTRY))) == 3

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8")
    assert changelog.read_text(encoding="utf-8") == text

    entry = _assert_changelog_ready(changelog)
    assert entry == _CLEAN_DOUBLE_HYPHEN_ENTRY + "\n"
    assert _structural_starts(entry)[0] == (
        "title",
        "the newest entry uses the separator bd-cut writes",
    )
    assert _placeholder_markers(entry) == []


def test_prose_above_the_newest_entry_is_not_a_skipped_header(
    tmp_path: Path,
) -> None:
    """Negative control: the skipped-header check must not fire on a preamble."""
    preamble = "# Changelog\n\nAll notable changes are recorded here.\n\n"
    text = preamble + _CLEAN_ENTRY + "\n" + _SHIPPED_DRAFT_ENTRY
    assert len(_VERSION_HEADING.findall(text)) == 2
    assert _VERSION_HEADING.search(text).start() == len(preamble)
    assert _RELEASE_HEADER.search(text).start() == len(preamble)

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8")

    entry = _assert_changelog_ready(changelog)
    assert entry == _CLEAN_ENTRY + "\n"
    assert _placeholder_markers(entry) == []


def test_missing_release_entry_is_unknown() -> None:
    with pytest.raises(AssertionError, match="measurement UNKNOWN"):
        _newest_release_entry("# Changelog\n\nNo releases measured.\n")


def test_real_changelog_newest_entry_is_ready() -> None:
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md measurement UNKNOWN: file unavailable"
    text = changelog.read_text(encoding="utf-8")
    matches = list(_RELEASE_HEADER.finditer(text))
    assert matches, "the real CHANGELOG.md has a zero release-entry denominator"

    entry = _assert_changelog_ready(changelog)
    assert entry.startswith(matches[0].group(0) + "\n")
    assert _placeholder_markers(entry) == []


def test_transform_control_imports_without_asserting_placeholder_behavior() -> None:
    """Mutation control: importing this gate alone is not a verdict."""
    importlib.import_module(__name__)
