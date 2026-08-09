"""bd-template-merge: a list-valued selector must survive the merge as a LIST.

@988, closing finding G. `merge_drafts` cannot use a list as a dict key, so it
json.dumps a non-scalar leaf in order to vote on it -- and then writes the
winning TEXT straight back into the canonical slot without ever parsing it back.

The result is not merely corrupt, it is corrupt AND PROMOTABLE. Measured on the
real pipeline at v3.66.987:

    merged rows raw   : '["[role=dialog] a.dl"]'   (a str, not a list)
    _is_modal_scoped  : True        <- the JSON text CONTAINS "[role=dialog]"
    normalized rows   : ["[\\"[role=dialog] a.dl\\"]"]
    row_selectors_count: 1   promotion_ready: True

So the artifact passes modal-scoping for the wrong reason, survives normalize,
and reaches the promote gate. As a CSS selector it matches nothing. A template
promoted from it would click nothing at runtime, and nothing downstream says so.

WHAT IT COSTS, measured on the operator's corpus (742 captures / 158 sites) at
v3.66.987: SIXTEEN sites report reliability `unknown` with reason
`merge_artifact_only` -- every one of them `green_from_one`, every one of them
modal-shaped (`.modal a.inject-url`, `[role="dialog"] a[role="button"]`,
`.drawer ...`), and fifteen of the sixteen with FULL support (4/4, 3/3, 2/2).
They are unknown solely because the merged row is the artifact rather than the
row every capture agreed on.

THE FIX MUST NOT RE-DETECT. Parsing the winner back "if it looks like JSON"
cannot tell an encoded list from a hand-written selector that happens to be
valid JSON, and guessing wrong writes garbage into the canonical slot -- the
same defect wearing the other hat. The encoding is RECORDED at vote time
instead, and `test_a_STRING_that_happens_to_be_valid_JSON_is_left_alone` is the
direction that pins it.
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MERGE_TOOL = REPO / "toolchain" / "bin" / "bd-template-merge"
CORPUS_TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

for _d in (str(REPO), str(REPO / "tools")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from bulk_downloader.template_normalize import normalize_draft   # noqa: E402
from template_inventory import assess                            # noqa: E402


def _load(name, path):
    ld = importlib.machinery.SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(ld.name, ld))
    ld.exec_module(mod)
    return mod


MERGE = _load("bd_template_merge_988", MERGE_TOOL)
CORP = _load("bd_wacz_corpus_988", CORPUS_TOOL)


def _draft(download, host="h.example.org"):
    return {
        "schema_version": "1", "host": host, "match": {"hosts": [host]},
        "selectors": {"download": dict(download),
                      "login": {"email": "#e", "password": "#p", "submit": "#s"}},
        "resolution_priority": [1080, 720],
        "network_patterns": ["https://cdn.example.org/{resolution}.mp4"],
        "source": {"capture_file": "c.wacz"},
    }


def _merge(drafts):
    return MERGE.merge_drafts(drafts, [str(i) for i in range(len(drafts))])


ROW = "[role=dialog] a.dl"


def test_a_LIST_leaf_lands_in_the_canonical_slot_as_a_LIST():
    """The defect itself. RED today: the slot holds the JSON TEXT of the list."""
    merged = _merge([_draft({"row_selectors": [ROW]})] * 2)
    rows = merged["selectors"]["download"]["row_selectors"]
    assert isinstance(rows, list), (
        "a list-valued selector was written back as %s: %r"
        % (type(rows).__name__, rows))
    assert rows == [ROW]


def test_the_SUPPORT_block_reports_the_real_value_not_its_encoding():
    """A human reading `selector_support` is reading it to decide what the
    template should contain. JSON text there is unusable for that."""
    merged = _merge([_draft({"row_selectors": [ROW]})] * 3)
    sup = merged["merge"]["selector_support"]["download.row_selectors"]
    assert sup[0]["value"] == [ROW], (
        "support reports the encoding rather than the selector: %r" % sup[0])
    assert sup[0]["support"] == 3 and sup[0]["of"] == 3


def test_the_MERGED_template_is_promotable_for_a_REAL_selector():
    """End to end through the pipeline that actually judges it. RED today: it
    is promotable, but on '["[role=dialog] a.dl"]' -- a green manufactured from
    JSON text, which is worse than not being promotable at all."""
    merged = _merge([_draft({"row_selectors": [ROW]})] * 2)
    nm = normalize_draft(merged)
    a = assess(nm, source="MERGED")
    rows = nm["selectors"]["download"]["row_selectors"]
    assert rows == [ROW], (
        "the normalized template carries a selector no browser can use: %r" % rows)
    assert a["promotion_ready"] is True
    assert a["row_selectors_count"] == 1


def test_a_STRING_that_happens_to_be_valid_JSON_is_left_alone():
    """THE OVER-SENSITIVITY DIRECTION, and the reason the fix records the
    encoding rather than re-detecting it.

    A fix that parses the winner back whenever it "looks like JSON" would turn
    this hand-written selector into a list. That is the same defect with the
    sign flipped, and it would be far harder to notice because the value would
    still look plausible."""
    weird = '["div.modal a.dl"]'          # a STRING leaf, valid JSON by accident
    assert isinstance(json.loads(weird), list), "fixture is not JSON after all"
    merged = _merge([_draft({"trigger": weird})] * 2)
    trig = merged["selectors"]["download"]["trigger"]
    assert trig == weird and isinstance(trig, str), (
        "a hand-written string leaf was decoded into %s: %r"
        % (type(trig).__name__, trig))
    sup = merged["merge"]["selector_support"]["download.trigger"]
    assert sup[0]["value"] == weird


def test_two_DIFFERENT_lists_still_vote_and_the_majority_wins():
    """The merge's whole purpose has to survive the fix. A list leaf must still
    rank, and the runner-up must still be retained with its denominator --
    keep-all is the documented semantics."""
    a, b = [".modal a.one"], [".modal a.two"]
    merged = _merge([_draft({"row_selectors": a}), _draft({"row_selectors": a}),
                     _draft({"row_selectors": b})])
    assert merged["selectors"]["download"]["row_selectors"] == a
    sup = merged["merge"]["selector_support"]["download.row_selectors"]
    assert [(e["value"], e["support"]) for e in sup] == [(a, 2), (b, 1)], sup


def test_a_TIE_on_a_list_leaf_reports_real_values():
    """`ties` is read when the captures genuinely disagree with no majority --
    the worst case for a gate-critical selector, and the one where an unusable
    value in the report costs the most."""
    a, b = [".modal a.one"], [".modal a.two"]
    merged = _merge([_draft({"row_selectors": a}), _draft({"row_selectors": b})])
    ties = [t for t in merged["merge"]["ties"]
            if t["key"] == "download.row_selectors"]
    assert ties, "a 1-1 split on a list leaf produced no tie record"
    assert sorted(ties[0]["values"], key=json.dumps) == sorted([a, b], key=json.dumps), (
        "the tie reports encodings rather than selectors: %r" % ties[0])


def test_the_SIXTEEN_artifact_only_sites_become_gradable():
    """The corpus payoff, driven through the same path `--templates` uses.

    Measured at v3.66.987: 16 sites sat at reliability `unknown` /
    `merge_artifact_only` purely because the merged row was the artifact. With
    the merge fixed, a unanimous modal row is what the gate judges and what
    reliability grades."""
    raws = [_draft({"row_selectors": [ROW]})] * 3
    normed = [normalize_draft(r) for r in raws]
    nm = normalize_draft(_merge(raws))
    gs = CORP._gate_support(nm, normed, raws)
    assert gs["reason"] is None, (
        "still ungradable after the merge fix: %r" % gs["reason"])
    assert gs["best"] == {"clause": "row_selectors", "value": ROW,
                          "support": 3, "of": 3}, gs["best"]
    assert CORP._reliability(3, gs) == "corroborated"
    row = gs["clauses"]["row_selectors"]["merged_rows"][0]
    assert row["stringified_artifact"] is False and row["value"] == ROW


def test_FIRST_APPEARANCE_still_wins_among_equal_values():
    """Adversarial review caught a regression I introduced. `isinstance(True, int)`
    is True in Python, so ("scalar", True) and ("scalar", 1) are the SAME dict
    key -- and storing the original with plain assignment let the LAST draft's
    representative win. The tool's documented tie-break is FIRST APPEARANCE
    (`_rank`), and the canonical slot must not quietly disagree with it: a
    resolution written as 1080 in draft one and 1080.0 in draft two would flip
    type on merge, depending only on draft order."""
    a = _merge([_draft({"trigger": True}), _draft({"trigger": 1})])
    b = _merge([_draft({"trigger": 1}), _draft({"trigger": True})])
    assert a["selectors"]["download"]["trigger"] is True, (
        "the second draft's representative overwrote the first's: %r"
        % a["selectors"]["download"]["trigger"])
    assert b["selectors"]["download"]["trigger"] == 1 and \
        b["selectors"]["download"]["trigger"] is not True
    # And they still count as ONE value, which is what made them collide.
    assert a["merge"]["selector_support"]["download.trigger"][0]["support"] == 2


def test_the_MERGED_output_does_not_ALIAS_an_input_drafts_list():
    """Latent, and new: the old code manufactured a fresh string for every
    non-scalar, so the merged template could not share an object with an input.
    `mode_templates` reads the raw drafts AFTER normalizing the merge, in the
    same run -- an in-place mutation downstream would rewrite its own inputs."""
    d1, d2 = _draft({"row_selectors": [ROW]}), _draft({"row_selectors": [ROW]})
    merged = _merge([d1, d2])
    merged["selectors"]["download"]["row_selectors"].append("INJECTED")
    assert d2["selectors"]["download"]["row_selectors"] == [ROW], (
        "mutating the merged template rewrote an input draft: %r"
        % d2["selectors"]["download"]["row_selectors"])
    assert merged["merge"]["selector_support"]["download.row_selectors"][0]["value"] \
        == [ROW], "the support entry aliases the canonical slot"
    # The support entry is a SECOND way into the same object, and a deepcopy at
    # the write site alone leaves it aliased -- which is exactly how this
    # escaped the first battery.
    merged["merge"]["selector_support"]["download.row_selectors"][0]["value"] \
        .append("INJECTED-VIA-SUPPORT")
    assert d1["selectors"]["download"]["row_selectors"] == [ROW], (
        "mutating the SUPPORT entry rewrote an input draft: %r"
        % d1["selectors"]["download"]["row_selectors"])


def test_a_GREEN_site_is_never_also_reported_BLOCKED_on_the_gate():
    """Two sites on the operator's corpus printed `green_from_one` beside
    `blocking: ['gate_selector']` -- `blocking` described the MERGED draft while
    the verdict described the best single capture. One row cannot answer the
    same question both ways, and it inflated the blocked rollup by two."""
    green = assess({"selectors": {"download": {"trigger": "a.dl"}},
                    "resolutions": [1080]}, source="one.wacz")
    merged_bad = assess({"selectors": {"download": {}},
                         "resolutions": [1080]}, source="MERGED")
    assert green["promotion_ready"] is True and merged_bad["promotion_ready"] is False
    assert CORP._verdict_blocking("green_from_one", green, merged_bad, green) == [], (
        "a site whose verdict came from a green capture reported that capture's "
        "gate clause as blocking")
    assert CORP._verdict_blocking("not_green", None, merged_bad, green) == \
        CORP._blocking(merged_bad), (
        "a not-green site must still report the merged draft's blockers")

    # DRIVEN DIRECTLY, because the call site cannot reach it: `ready` is
    # non-empty only when the verdict IS green, so a mutant deleting the
    # verdict half of the guard changes nothing observable and escaped the
    # battery. Same shape as @984's `_reliability` n<2 guard -- a rule asserted
    # by the code and constrained by nothing is not a rule.
    assert CORP._verdict_blocking("not_green", green, merged_bad, green) == \
        CORP._blocking(merged_bad), (
        "handed a ready assessment, a NOT-green verdict described it anyway -- "
        "blocking must describe the thing the verdict is about")
