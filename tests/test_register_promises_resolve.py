"""A finding is a numbered item in the register's inventory, or it does not exist.

THE FAILURE THIS EXISTS TO STOP, from 15.62. Four order-dependent band failures
were proven pre-existing and this register was TOLD they had been written down.
They had not; a grep for them returned zero. A finding that lives only in a
conversation is lost at the next context boundary, and no gate could see it --
`bd-freshcheck` and `bd-doc-truth` both ask whether a cited PATH resolves, never
whether a promise was kept, so a prose promise is unfalsifiable by construction.

The mechanizable half is a set comparison, and it needs a machine-readable
declaration to compare against. Hence the ITEM LEDGER block that a session-close
section carries: prose like "recorded as" or "filed as" is no longer an accepted
form, because prose is what failed.

CHECKED BOTH WAYS, because either direction alone is a gate that cannot see its
subject:

  A. Every number a ledger declares must resolve to a numbered entry in the
     inventory. Catches a close section promising about an item nobody filed --
     15.68 itself names "the register-promise gate" with no number, which is
     the exact form 15.62 said must stop.
  B. Every inventory entry must be ACCOUNTED FOR -- closed in the inventory
     text, or declared open or closed by the newest session close. Catches the
     worse direction: an open item silently dropped from the close, which reads
     as finished and is simply forgotten.

Direction B is baselined, not enforced retroactively. The items it cannot
account for today are frozen in _UNACCOUNTED and may only shrink; adjudicating
one means giving it a status in the ledger and removing it here. A stale entry
fails, so the list cannot outlive the gap it records.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_REGISTER = _REPO / "project-knowledge" / "SESSION_CARRY.md"

_HEADING = re.compile(r'^### 15\.(\d+) \| (.*)$')
_ENTRY = re.compile(r'^\s{0,2}(\d{1,3})\.\s+\*\*(.+)')
_LEDGER_ROW = re.compile(r'^(OPEN|CLOSED):\s*(.*)$')

# The section holding the canonical numbered inventory.
_INVENTORY_SECTION = 36

# Inventory entries the newest session close does not account for, frozen at
# v3.66.955. Each is an item whose status is not stated anywhere a machine can
# read it -- NOT a claim that it is open or closed. Remove an entry by giving
# the item a status in the ledger; test_no_unaccounted_entry_is_stale fails if
# one stops being unaccounted, so this cannot quietly become permanent.
_UNACCOUNTED: frozenset[int] = frozenset({
    1,   # 7b -- name the twelve retired tools; unrecoverable from the tree
    2,   # item 9 -- capture.sh commit identity; release gate, needs a GO
    8,   # batch B remainder -- bd-opv, bd-env-report-check, bd-equiv
    11,  # repo-root .db-wal writer; 15.68 closed its DENOMINATOR question only
    13,  # item 15 -- bd-state reachable only through build_session_pack.py
    16,  # 7a -- retirement completion, three pre-@858 tools survive as prose
    20,  # import-graph gate blind to tests/ edges; widened at @889, unrecorded here
    23,  # the capture gap at 885/886
    29,  # the archive sequence -- 15.68 closed its DATABASE RECOVERY part only
    30,  # the launcher Stop hook's advice; no .githooks/pre-push exists
})


def _sections() -> list[tuple[int, str, list[str]]]:
    """[(number, title, body lines)] for every ### 15.N section."""
    lines = _REGISTER.read_text(encoding="utf-8").splitlines()
    marks = [(i, m) for i, l in enumerate(lines) if (m := _HEADING.match(l))]
    out = []
    for idx, (i, m) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        out.append((int(m.group(1)), m.group(2), lines[i + 1:end]))
    return out


def _inventory() -> tuple[set[int], set[int]]:
    """(every numbered entry, those the inventory text itself marks CLOSED)."""
    body = next(b for n, _, b in _sections() if n == _INVENTORY_SECTION)
    items, closed = set(), set()
    for line in body:
        m = _ENTRY.match(line)
        if not m:
            continue
        num, text = int(m.group(1)), m.group(2)
        items.add(num)
        if re.match(r'CLOSED\b', text.strip('* ')):
            closed.add(num)
    return items, closed


def _session_closes() -> list[tuple[int, str, list[str]]]:
    return sorted((s for s in _sections()
                   if re.search(r'session close', s[1], re.I)),
                  key=lambda s: s[0])


def _ledger(body: list[str]) -> dict[str, set[int]] | None:
    """Parse an ITEM LEDGER block. None when the section carries none.

    Numbers only; a sub-letter is normalised to its parent, so 12(c) counts as
    item 12. Bare prose in a row -- an item named without a number -- is
    returned separately rather than dropped, because dropping it is how an
    unnumbered promise would pass direction A unnoticed.
    """
    try:
        start = next(i for i, l in enumerate(body) if l.strip().startswith("ITEM LEDGER"))
    except StopIteration:
        return None
    rows: dict[str, set[int]] = {"OPEN": set(), "CLOSED": set()}
    unnumbered: list[str] = []
    key = None
    for line in body[start + 1:]:
        if line.startswith("```") or line.strip().startswith("### "):
            break
        # A ledger is a CONTIGUOUS block: the first blank line after it ends
        # it. Without this the indented-continuation rule below swallowed the
        # whole section, and prose reading "50-deep graft" parsed as item 50 --
        # caught by direction A on this file's own 15.69, which is the gate
        # finding a defect in its own parser on real data rather than on a
        # fixture.
        if key is not None and not line.strip():
            break
        m = _LEDGER_ROW.match(line.strip())
        if m:
            key, rest = m.group(1), m.group(2)
        elif key and line.startswith(" ") and line.strip():
            rest = line.strip()
        else:
            continue
        for tok in (t.strip() for t in rest.split(",") if t.strip()):
            g = re.match(r'^(\d{1,3})', tok)
            if g:
                rows[key].add(int(g.group(1)))
            else:
                unnumbered.append(tok)
    rows["UNNUMBERED"] = set()
    rows["_unnumbered_text"] = unnumbered          # type: ignore[assignment]
    return rows


def audit(items: set[int], closed_in_inventory: set[int],
          led: dict, baseline: frozenset[int]) -> dict[str, list]:
    """The whole comparison, as data. Pure so the synthetic cases below can
    reach every failing branch -- on the real register most of these are empty,
    and an assertion whose failing branch no test executes proves nothing."""
    declared = led["OPEN"] | led["CLOSED"]
    accounted = closed_in_inventory | declared | baseline
    return {
        "unknown": sorted(declared - items),
        "prose": list(led.get("_unnumbered_text", [])),
        "missing": sorted(items - accounted),
        "stale": sorted(baseline & (closed_in_inventory | declared)),
        "ghosts": sorted(baseline - items),
    }


def test_direction_a_fires_on_an_item_that_is_not_in_the_inventory():
    r = audit({1, 2}, set(), {"OPEN": {1, 99}, "CLOSED": set()}, frozenset())
    assert r["unknown"] == [99]


def test_direction_a_fires_on_an_unnumbered_promise():
    led = {"OPEN": {1}, "CLOSED": set(), "_unnumbered_text": ["the promise gate"]}
    assert audit({1}, set(), led, frozenset())["prose"] == ["the promise gate"]


def test_direction_b_fires_on_an_item_no_close_accounts_for():
    r = audit({1, 2, 3}, {1}, {"OPEN": {2}, "CLOSED": set()}, frozenset())
    assert r["missing"] == [3]


def test_the_baseline_cannot_outlive_the_gap_it_records():
    r = audit({1, 2}, set(), {"OPEN": {2}, "CLOSED": set()}, frozenset({2}))
    assert r["stale"] == [2]
    assert audit({1}, set(), {"OPEN": set(), "CLOSED": set()},
                 frozenset({77}))["ghosts"] == [77]


def test_the_ledger_parser_reads_a_real_block():
    body = ["", "ITEM LEDGER -- machine-checked", "OPEN:   3, 12(c), 17",
            "CLOSED: 5, 7", "", "prose after the block"]
    led = _ledger(body)
    assert led is not None
    assert led["OPEN"] == {3, 12, 17} and led["CLOSED"] == {5, 7}
    assert _ledger(["no ledger here"]) is None


def test_the_parser_itself_surfaces_an_unnumbered_row():
    """Driven through _ledger, not a hand-built dict.

    The dict-driven case above cannot see the parser DROPPING a prose row --
    a mutation proved exactly that escape. An unnumbered entry must survive
    parsing, or direction A never gets the chance to reject it.
    """
    led = _ledger(["ITEM LEDGER", "OPEN: 3, the register-promise gate, 17"])
    assert led is not None
    assert led["OPEN"] == {3, 17}
    assert led["_unnumbered_text"] == ["the register-promise gate"]


def test_the_predicate_excludes_sections_that_merely_close_at_a_sha():
    """`close at <sha>` names a commit, not an item declaration.

    Seventeen sections carry 'close' in that sense. Folding them in would
    demand a ledger from sections that legitimately have none -- and the
    newest section overall happens to carry one, so the over-broad predicate
    passes every other assertion here. A mutation proved that escape.
    """
    closes = {n for n, _, _ in _session_closes()}
    assert 68 in closes, "15.68 is a session close and must be included"
    for n in (67, 66, 65, 64, 63, 62, 60, 59):
        assert n not in closes, (
            f"15.{n} says 'close at <sha>' -- a commit, not an item set -- and "
            f"must not be treated as a session close")


def test_the_newest_session_close_carries_a_ledger():
    closes = _session_closes()
    assert closes, (
        "BD-GATE-UNRUNNABLE: no section title matches 'session close', so this "
        "gate has no subject and its clean verdict would mean nothing")
    num, title, body = closes[-1]
    assert _ledger(body) is not None, (
        f"the newest session close (15.{num}) carries no ITEM LEDGER block. A "
        f"close section declares its items in a form a machine can read; prose "
        f"is the form that failed. Title: {title[:60]}")


def test_every_declared_item_resolves_to_a_numbered_entry():
    """Direction A."""
    items, _ = _inventory()
    assert items, (
        f"BD-GATE-UNRUNNABLE: section 15.{_INVENTORY_SECTION} yielded zero "
        f"numbered entries -- the denominator is empty")
    for num, title, body in _session_closes():
        led = _ledger(body)
        if led is None:
            continue
        declared = led["OPEN"] | led["CLOSED"]
        unknown = sorted(declared - items)
        assert not unknown, (
            f"15.{num}'s ledger names item(s) {unknown} that do not exist in "
            f"the 15.{_INVENTORY_SECTION} inventory")
        prose = led["_unnumbered_text"]                # type: ignore[index]
        assert not prose, (
            f"15.{num}'s ledger names {prose} without a number. A finding is a "
            f"numbered item in the inventory or it does not exist -- file it, "
            f"then cite the number")


def _closed_ever() -> set[int]:
    """Every item any ledger has declared CLOSED.

    A close is PERMANENT. Reading only the newest ledger would make the eleven
    items 15.68 closed read as unaccounted the moment a newer close existed --
    the gate would manufacture a gap by the act of writing the next session's
    section. Found while writing 15.69, one cut after the gate shipped.
    """
    out: set[int] = set()
    for _n, _t, body in _session_closes():
        led = _ledger(body)
        if led is not None:
            out |= led["CLOSED"]
    return out


def test_a_close_is_permanent_and_does_not_reopen():
    """The property the accumulator exists for, asserted over the real register.

    15.68 declared items closed; a later session close must not make them
    unaccounted. Without this, direction B fails on a register that is correct.
    """
    closes = _session_closes()
    assert len(closes) >= 2, "needs at least two session closes to be meaningful"
    older = _ledger(closes[-2][2]) or {"CLOSED": set()}
    if older["CLOSED"]:
        assert older["CLOSED"] <= _closed_ever(), (
            "an older ledger's CLOSED set is not carried forward")


def test_every_inventory_entry_is_accounted_for():
    """Direction B: nothing open may silently vanish from the close."""
    items, closed_in_inventory = _inventory()
    closes = _session_closes()
    assert closes, "BD-GATE-UNRUNNABLE: no session-close section"
    led = _ledger(closes[-1][2])
    assert led is not None, "the newest session close carries no ledger"
    accounted = closed_in_inventory | led["OPEN"] | _closed_ever() | _UNACCOUNTED
    missing = sorted(items - accounted)
    assert not missing, (
        f"{len(missing)} inventory entr(ies) are accounted for nowhere -- not "
        f"closed in the inventory text, not declared by 15.{closes[-1][0]}: "
        f"{missing}. Give each a status in the ledger, or add it to "
        f"_UNACCOUNTED with the measurement that put it there")


def test_no_unaccounted_entry_is_stale():
    """The baseline may only shrink."""
    items, closed_in_inventory = _inventory()
    led = _ledger(_session_closes()[-1][2])
    assert led is not None
    now_accounted = closed_in_inventory | led["OPEN"] | _closed_ever()
    stale = sorted(_UNACCOUNTED & now_accounted)
    assert not stale, (
        f"item(s) {stale} are declared in the ledger AND still listed as "
        f"unaccounted -- remove them from _UNACCOUNTED in the same cut")
    ghosts = sorted(_UNACCOUNTED - items)
    assert not ghosts, (
        f"_UNACCOUNTED names item(s) {ghosts} that are not in the inventory")


def test_the_ledger_block_ends_at_the_first_blank_line():
    """Prose after a ledger is prose, not more ledger.

    The continuation rule that lets a long OPEN row wrap once swallowed the
    entire section, so a sentence about a "50-deep graft" was read as a
    declaration about item 50. Direction A caught it on the real register.
    """
    led = _ledger([
        "ITEM LEDGER", "OPEN:   3, 12", "CLOSED: 5",
        "",
        "  50-deep graft, so the two chains did not overlap.",
        "  99 bottles, and a wrapped prose line.",
    ])
    assert led is not None
    assert led["OPEN"] == {3, 12} and led["CLOSED"] == {5}
    assert not led["_unnumbered_text"], led["_unnumbered_text"]


def test_a_wrapped_ledger_row_is_still_read():
    """The continuation rule must survive the fix -- a long row may wrap."""
    led = _ledger(["ITEM LEDGER", "OPEN:   3, 12,", "        17, 31", "CLOSED: 5"])
    assert led is not None
    assert led["OPEN"] == {3, 12, 17, 31}
