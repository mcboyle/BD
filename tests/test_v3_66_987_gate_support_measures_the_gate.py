"""bd-wacz-corpus --templates: reliability must be measured on the gate's own clauses.

@987. The v3.66.984 report told the operator that 84 of 158 sites were
"corroborated" while only 71 were green and 71 read "unknown". The arithmetic
never reconciled, and the reason is section 0: `_gate_support` asked a question
over a denominator that structurally excluded its subject.

MEASURED AT 07c05f2, by running the real pipeline rather than reading it:

  merge_drafts writes selector_support keyed by RAW draft leaf names --
    download.button_hint, download.row_selectors, login.*
  _GATE_KEYS looked for
    download.trigger, download.row_selectors, download.button
  intersection on a text-hint site: download.row_selectors ONLY

Three consequences, each a test below:

1. A site whose only download evidence is `button_hint` scored gate_support
   None -> reliability "unknown", though its trigger was corroborated across
   every capture. `download.button` is emitted by NOTHING; `download.button_hint`
   was missing from the tuple. That is the unknown-71 mechanism.

2. A site whose inline rows normalize DISCARDS (not modal-scoped) scored
   "corroborated" off `download.row_selectors` support, while assess on the same
   merged draft reported row_selectors_count 0. Reliability graded over a
   selector the gate never sees.

3. Grading the VOTE WINNER is wrong even when the key is right. Measured:
   drafts {button_hint:"X"}, {trigger:"Y"}, {trigger:"Y"} vote Y at 2 of 3, but
   template_normalize._map_selectors:102 gives the hint PRECEDENCE, so the
   shipped template carries X. "corroborated on Y" describes a value the
   template does not contain.

THE FIX MUST NOT REPRODUCE THE SHAPE OF THE DEFECT, and a wider key tuple would.
Any hand-written raw-key -> clause map is `_GATE_KEYS` with more entries: it
cannot see a raw key `build_template` grows tomorrow, and a test exercising the
map over the spellings it already knows about passes either way. So support is
voted over the OUTPUT of `normalize_draft` -- the same function that produces the
gate's input -- and attributed to values READ OUT of normalize(merged).
`test_a_FUTURE_raw_key_is_VISIBLE_rather_than_vanishing` is the test no key map
can pass.

PRESENT-IN-MERGED IS NECESSARY AND NOT SUFFICIENT. Measured: merge stringifies a
list-valued leaf (defect G), and the resulting JSON text
'["[role=dialog] a.dl"]' *passes* `_is_modal_scoped` -- it contains the literal
`[role=dialog]` -- so it survives normalize and assess reports
row_selectors_count 1, promotion_ready True. Defect G does not merely corrupt a
selector, it MANUFACTURES A GREEN. Hence the artifact test, and hence the
over-sensitivity guard beside it: a fix that flags every JSON-parsable selector
would destroy real templates, so the flag requires BOTH that it parses to a
non-scalar AND that its exact-match support is zero.
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"
MERGE_TOOL = REPO / "toolchain" / "bin" / "bd-template-merge"

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


CORP = _load("bd_wacz_corpus_987", TOOL)
MERGE = _load("bd_template_merge_987", MERGE_TOOL).merge_drafts


def _draft(download, host="h.example.org"):
    """A raw draft in build_template's own shape."""
    return {
        "schema_version": "1", "host": host, "match": {"hosts": [host]},
        "selectors": {"download": dict(download),
                      "login": {"email": "#e", "password": "#p",
                                "submit": "#s"}},
        "resolution_priority": [1080, 720],
        "network_patterns": ["https://cdn.example.org/{resolution}.mp4"],
        "source": {"capture_file": "c.wacz"},
    }


def _support(raws):
    """Run the REAL pipeline and return (gate_support, normalized merged).

    Never a hand-built stand-in: the whole defect was a function reading a
    different object from the one the gate judges, so a fixture that skipped
    merge or normalize could not express it.
    """
    normed = [normalize_draft(r) for r in raws]
    merged = MERGE(raws, [str(i) for i in range(len(raws))]) if len(raws) >= 2 else None
    nm = normalize_draft(merged) if merged is not None else None
    return CORP._gate_support(nm, normed, raws), nm


# --------------------------------------------------------------------------
# 1. The two halves of the measured defect.
# --------------------------------------------------------------------------

def test_a_DISCARDED_row_selector_is_never_reported_as_corroborated():
    """A row selector the normalizer discards.

    The fixture was the inline-panel shape (`.download-block a.dl`) until @989
    made that a KEPT selector -- a download affordance now counts as scoping.
    It is page junk now (`li.swiper-slide`, taken from the measured corpus),
    because the test's subject is "a row the gate never sees", not any
    particular selector.

    RED today: `_gate_support` returns download.row_selectors support 2 of 2 and
    `_reliability` calls it corroborated, while assess on the same merged draft
    reports row_selectors_count 0 because normalize dropped it for not being
    modal-scoped."""
    raws = [_draft({"button_hint": "text=/Download/i",
                    "row_selectors": ["li.swiper-slide"]})] * 2
    gs, nm = _support(raws)
    a = assess(nm, source="MERGED")

    assert a["row_selectors_count"] == 0, (
        "fixture drifted -- normalize was supposed to DROP the inline row")
    rows = gs["clauses"]["row_selectors"]["merged_rows"]
    assert rows == [], (
        "a row selector the gate discarded is being reported as merged "
        "evidence: %r" % rows)
    assert CORP._reliability(len(raws), gs) == "corroborated", (
        "the site IS corroborated -- on its trigger. Reporting `unknown` here "
        "would be the over-sensitive failure, not a fix.")
    assert gs["best"]["clause"] == "trigger", (
        "reliability must be graded on the clause the gate actually sees, "
        "got %r" % (gs["best"],))


def test_a_TEXT_HINT_only_site_is_corroborated_not_unknown():
    """The unknown-71 mechanism. RED today: `download.button_hint` is absent
    from `_GATE_KEYS`, so gate_support is None and reliability is "unknown"
    for a trigger every capture agreed on."""
    raws = [_draft({"button_hint": "text=/Download/i"})] * 3
    gs, _nm = _support(raws)
    assert gs["best"] is not None, "no gate evidence found for a unanimous hint"
    assert gs["best"]["support"] == 3 and gs["best"]["of"] == 3
    assert CORP._reliability(len(raws), gs) == "corroborated"


def test_reliability_grades_the_SHIPPED_value_not_the_vote_winner():
    """THE LOAD-BEARING TEST. Measured: the raw vote makes Y the winner at 2 of
    3, but `_map_selectors:102` gives button_hint PRECEDENCE, so the merged
    template ships X at support 1.

    RED today: `_GATE_KEYS` contains `download.trigger`, so `_gate_support`
    reports Y at support 2 -> "corroborated" about a value the template does not
    carry. Two of the three candidate designs for this fix failed exactly here,
    which is why it is pinned."""
    raws = [_draft({"button_hint": "X"}),
            _draft({"trigger": "Y"}), _draft({"trigger": "Y"})]
    gs, nm = _support(raws)

    assert nm["selectors"]["download"]["trigger"] == "X", (
        "fixture drifted -- normalize no longer prefers the hint")
    trig = gs["clauses"]["trigger"]
    assert trig["merged_value"] == "X", (
        "graded the vote winner rather than the shipped value: %r" % trig)
    assert (trig["support"], trig["disagree"], trig["absent"]) == (1, 2, 0)
    assert CORP._reliability(len(raws), gs) == "single_witness", (
        "a value one capture in three supports is not corroboration")
    assert any(c["value"] == "Y" and c["support"] == 2
               for c in trig["candidates"]), (
        "the runner-up must stay VISIBLE -- it is the two-page-shapes signal")


# --------------------------------------------------------------------------
# 2. The distinction that makes the operator's 68 sites actionable.
# --------------------------------------------------------------------------

def test_TWO_PAGE_SHAPES_is_distinguishable_from_INCOMPLETE_CAPTURES():
    """5 of 9 is two opposite situations and the old report printed one number
    for both: the other four captures carried a DIFFERENT value (two page
    shapes -- the template silently fails on the minority) or NO value (the
    captures never reached the download control -- the template is fine).

    RED today: neither `disagree` nor `absent` exists; both fixtures produce an
    indistinguishable {"support": 2, "of": 3}."""
    gap, _ = _support([_draft({"trigger": "A"}), _draft({"trigger": "A"}),
                       _draft({})])
    conflict, _ = _support([_draft({"trigger": "A"}), _draft({"trigger": "A"}),
                            _draft({"trigger": "B"})])

    g, c = gap["clauses"]["trigger"], conflict["clauses"]["trigger"]
    assert (g["support"], g["disagree"], g["absent"]) == (2, 0, 1), g
    assert (c["support"], c["disagree"], c["absent"]) == (2, 1, 0), c
    assert gap["capture_gaps"]["no_download_evidence"] == 1
    assert conflict["capture_gaps"]["no_download_evidence"] == 0

    for name, block in (("gap", gap), ("conflict", conflict)):
        t = block["clauses"]["trigger"]
        assert t["support"] + t["disagree"] + t["absent"] == block["of"], (
            "%s: the identity that makes the three numbers a partition does "
            "not hold: %r of %r" % (name, t, block["of"]))


def test_a_DISCARDED_control_is_counted_separately_from_a_MISSING_one():
    """The number that decides whether the operator re-captures 77 sites or
    fixes a normalizer rule. A capture whose download control was EXTRACTED and
    then dropped by the pipeline is not the same as one that never had it.

    RED today: no such counter exists, which is why 77 sites were reported to
    the operator as a capture-side gap -- advice that was retracted."""
    dropped, _ = _support([_draft({"row_selectors": ["li.swiper-slide"]})] * 2)
    absent, _ = _support([_draft({})] * 2)

    assert dropped["capture_gaps"]["evidence_dropped_by_pipeline"] == 2
    assert dropped["capture_gaps"]["no_download_evidence"] == 0
    assert absent["capture_gaps"]["no_download_evidence"] == 2
    assert absent["capture_gaps"]["evidence_dropped_by_pipeline"] == 0

    rows = dropped["capture_gaps"]["dropped_rows"]
    assert [r["value"] for r in rows] == ["li.swiper-slide"], rows
    assert rows[0]["supported_by"] == 2 and rows[0]["of"] == 2, (
        "the discarded control's own support is the actionable part -- 'the "
        "control we threw away was corroborated 2 of 2': %r" % rows)


def test_a_FUTURE_raw_key_is_VISIBLE_rather_than_vanishing():
    """THE TEST NO KEY MAP CAN PASS, and the reason the fix votes over
    normalize's output instead of mapping raw names.

    `_GATE_KEYS` could not see `download.button_hint`; a wider tuple simply
    cannot see whatever `build_template` adds next. A novel leaf must land
    somewhere a human will read, BY NAME."""
    gs, _ = _support([_draft({"frob": "x"})] * 2)
    assert gs["gate_visible"] is False
    assert gs["capture_gaps"]["dropped_leaves"] == {"frob": 2}, (
        "a raw download leaf the pipeline does not map vanished without "
        "record: %r" % gs["capture_gaps"])
    assert CORP._reliability(2, gs) == "unknown"


# --------------------------------------------------------------------------
# 3. Defect G, and the over-sensitivity guard that must ship beside it.
# --------------------------------------------------------------------------

def test_a_STRINGIFIED_merge_artifact_is_not_graded_as_evidence():
    """Measured: merge json.dumps a list-valued leaf, and the resulting text
    '["[role=dialog] a.dl"]' PASSES `_is_modal_scoped` because it contains the
    literal `[role=dialog]`. It survives normalize and assess reports
    row_selectors_count 1, promotion_ready True -- so the artifact does not
    merely corrupt a selector, it manufactures a green.

    RED today: reported as download.row_selectors support 2 -> corroborated."""
    raws = [_draft({"row_selectors": ["[role=dialog] a.dl"]})] * 2
    normed = [normalize_draft(r) for r in raws]
    nm = normalize_draft(MERGE(raws, ["a", "b"]))
    # @988 FIXED the producer: `merge_drafts` no longer stringifies a list leaf,
    # so this artifact can no longer arrive from a merge. It is injected
    # directly instead, because a guard that can only be reached through the
    # bug it guards against dies the moment that bug is fixed -- and this one
    # still has to hold for a hand-authored draft, a pre-@988 draft on disk, or
    # a future producer that reintroduces the shape.
    nm["selectors"]["download"]["row_selectors"] = ['["[role=dialog] a.dl"]']
    gs = CORP._gate_support(nm, normed, raws)

    assert assess(nm)["row_selectors_count"] == 1, "fixture drifted"
    row = gs["clauses"]["row_selectors"]["merged_rows"][0]
    assert row["stringified_artifact"] is True, (
        "JSON text is being presented as a usable selector: %r" % row)
    assert "value" not in row, (
        "an artifact must never sit under a key a human would paste as CSS")
    assert json.loads(row["value_json"]) == row["value_parsed"]
    assert gs["best"] is None, (
        "the artifact was graded as gate evidence: %r" % gs["best"])
    assert CORP._reliability(len(raws), gs) == "unknown"
    assert any(c["value"] == "[role=dialog] a.dl" and c["support"] == 2
               for c in gs["clauses"]["row_selectors"]["candidates"]), (
        "the REAL per-capture row must stay visible beside the artifact")


def test_a_REAL_selector_that_parses_as_JSON_is_not_flagged():
    """The other direction, in the same battery so a fix that flags everything
    cannot pass. Over-sensitivity is a soundness bug: a gate that cries wolf
    gets switched off. The flag needs BOTH conditions -- parses to a non-scalar
    AND exact-match support of zero.

    THE FIRST VERSION OF THIS TEST WAS VACUOUS AND `bd-mutate` CAUGHT IT, NOT
    REVIEW. It used '[role=dialog] a.dl', which is not valid JSON, so `is_json`
    was False and the mutant that drops the support condition changed nothing.
    The test named the condition it existed to pin and never exercised it -- a
    harness that cannot represent the failure it is written for. The value below
    genuinely parses to a list AND is carried by every capture, which is the
    only shape that separates the two conditions."""
    weird = '["div.modal a.dl"]'          # valid JSON, and really in the drafts
    assert isinstance(json.loads(weird), list), "fixture is not JSON after all"
    raws = [_draft({"row_selectors": [weird]}), _draft({"row_selectors": [weird]})]
    normed = [normalize_draft(r) for r in raws]
    nm = normalize_draft(MERGE(raws, ["a", "b"]))
    for d in normed + [nm]:
        d["selectors"]["download"]["row_selectors"] = [weird]

    gs = CORP._gate_support(nm, normed, raws)
    row = gs["clauses"]["row_selectors"]["merged_rows"][0]
    assert row["stringified_artifact"] is False, (
        "a selector every capture carries was flagged as a merge artifact "
        "purely for parsing as JSON: %r" % row)
    assert row["value"] == weird and row["support"] == 2
    assert CORP._reliability(2, gs) == "corroborated"


def test_UNANIMOUS_captures_report_no_gaps_and_stay_corroborated():
    """The general over-sensitivity guard. A fix that answers "unknown" to
    everything passes every escape test above and destroys the tool."""
    raws = [_draft({"trigger": "button.dl", "row_selectors": ["div.modal a.dl"]})] * 4
    gs, _ = _support(raws)
    assert CORP._reliability(4, gs) == "corroborated"
    assert gs["gate_visible"] is True
    assert gs["capture_gaps"]["no_download_evidence"] == 0
    assert gs["capture_gaps"]["evidence_dropped_by_pipeline"] == 0
    assert gs["capture_gaps"]["dropped_leaves"] == {}
    assert gs["capture_gaps"]["dropped_rows"] == []
    t = gs["clauses"]["trigger"]
    assert (t["support"], t["disagree"], t["absent"]) == (4, 0, 0)


# --------------------------------------------------------------------------
# 4. Third states. `unknown` must be distinguishable from `no evidence`.
# --------------------------------------------------------------------------

def test_the_block_is_never_None_and_says_WHY_it_cannot_grade():
    """A single-capture site and a site whose merge failed both scored a bare
    None, so the report could not tell "nothing to corroborate against" from
    "the merge tool was unavailable". Unknown is a third state; an unlabelled
    one is not actionable."""
    one, _ = _support([_draft({"trigger": "button.dl"})])
    assert one is not None and one["reason"] == "single_draft"
    assert one["best"] is None and CORP._reliability(1, one) == "unknown"
    assert one["of"] == 1

    none = CORP._gate_support(None, [], [])
    assert none["reason"] == "no_drafts"
    assert CORP._reliability(0, none) == "unknown"


def test_DENOMINATOR_is_carried_in_band_with_the_number():
    """'corroborated 2 of 2' on a nine-capture site whose other seven archives
    were unbuildable reads far stronger than the evidence. Section 1: say which
    denominator a count is over, in the same breath as the count."""
    raws = [_draft({"trigger": "button.dl"})] * 2
    gs = CORP._gate_support(normalize_draft(MERGE(raws, ["a", "b"])),
                            [normalize_draft(r) for r in raws], raws,
                            captures=9, unbuildable=7)
    assert gs["of"] == 2 and gs["captures"] == 9 and gs["unbuildable"] == 7
    assert gs["denominator_narrowed"] is True, (
        "seven of nine captures produced no draft and the block does not say so")


# --------------------------------------------------------------------------
# 5. The gate's own boolean, rather than a second definition of it.
# --------------------------------------------------------------------------

def test_a_leaf_the_pipeline_KEPT_is_not_counted_as_one_it_DROPPED():
    """Adversarial review caught this, and it is the retracted-77 shape
    inverted. `api_template` is a real leaf (`build_template_from_wacz:1749`)
    that SURVIVES normalize verbatim -- it is simply not a gate clause. A
    predicate of "raw leaves exist AND the gate clauses are empty" counts it as
    discarded, so the rollup would tell the operator to go and look at the
    normalizer for a site where the normalizer dropped nothing at all.

    Dropped means: this leaf's value is nowhere in the normalized draft."""
    kept, _ = _support([_draft({"api_template": "https://api.example.org/dl/{id}"})] * 2)
    assert kept["capture_gaps"]["evidence_dropped_by_pipeline"] == 0, (
        "a leaf normalize carried through was counted as dropped: %r"
        % kept["capture_gaps"])
    assert kept["capture_gaps"]["dropped_leaves"] == {}
    # It is still not gate evidence -- that part was right.
    assert kept["gate_visible"] is False

    # And the genuinely-dropped case must keep working, or this "fix" is just
    # a counter that never fires.
    gone, _ = _support([_draft({"row_selectors": ["li.swiper-slide"]})] * 2)
    assert gone["capture_gaps"]["evidence_dropped_by_pipeline"] == 2
    assert gone["capture_gaps"]["dropped_leaves"] == {"row_selectors": 2}


def test_the_ROLLUP_does_not_blame_the_normalizer_for_a_leaf_it_kept():
    """The operator-facing half of the same defect, driven through the REAL
    pipeline rather than a hand-built gaps dict.

    The first version of this test built the counters by hand and passed the
    moment it was written -- it could not see the defect, because the defect is
    in how those counters are DERIVED. That is the same blindness the cut is
    about, in the test written to prove the cut."""
    kept, _ = _support([_draft({"api_template": "https://api.example.org/dl/{id}"})] * 2)
    roll = CORP._gate_blocked_rollup(
        [{"blocking": ["gate_selector"], "gate_support": kept}])
    assert roll["by_cause"]["download_control_discarded_by_normalizer"] == 0, (
        "a site whose only leaf normalize KEPT was reported as a normalizer "
        "question rather than a capture that never reached a download "
        "control: %r" % roll)
    assert roll["by_cause"]["other"] == 1, (
        "an api_template-only site is neither a discard nor an empty capture: "
        "the leaf survived and simply is not a gate clause. `other` is the "
        "honest bucket, and deciding otherwise would mean re-deriving which "
        "leaves feed the gate -- the key map this cut exists to remove: %r"
        % roll)

    # The genuinely-discarded site must still be blamed on the normalizer, or
    # this fix has simply turned the counter off.
    gone, _ = _support([_draft({"row_selectors": ["li.swiper-slide"]})] * 2)
    roll2 = CORP._gate_blocked_rollup(
        [{"blocking": ["gate_selector"], "gate_support": gone}])
    assert roll2["by_cause"]["download_control_discarded_by_normalizer"] == 1


def test_a_CANDIDATE_can_never_out_support_its_own_denominator():
    """`normalize_draft` does not dedupe row_selectors, and the vote counted
    OCCURRENCES rather than captures -- so one draft listing the same row three
    times printed support 4 of 2. A ratio above 1 in the field the honeypot
    reading depends on is worse than no field."""
    raws = [_draft({"row_selectors": ["div.modal a.dl"] * 3}),
            _draft({"row_selectors": ["div.modal a.dl"]})]
    gs, _ = _support(raws)
    for c in gs["clauses"]["row_selectors"]["candidates"]:
        assert c["support"] <= c["of"], (
            "support exceeds its denominator: %r" % c)
    assert gs["clauses"]["row_selectors"]["candidates"][0]["support"] == 2


def test_UNANIMOUS_captures_are_not_reported_as_DISAGREEING_when_no_merge_shipped():
    """When the merge is unavailable there is no shipped value to agree with,
    and counting every capture as `disagree` reads as "two page shapes" -- the
    exact conclusion the counter exists to support. Nothing shipped is its own
    state."""
    normed = [normalize_draft(_draft({"trigger": "button.dl"}))] * 3
    gs = CORP._gate_support(None, normed, [_draft({"trigger": "button.dl"})] * 3)
    t = gs["clauses"]["trigger"]
    assert gs["reason"] == "merge_unavailable"
    assert t["disagree"] == 0, (
        "three captures that agree were reported as disagreeing: %r" % t)
    assert t["unattributed"] == 3
    assert CORP._partition_ok(t, 3), t


def test_an_ARTIFACT_ONLY_site_says_so_rather_than_claiming_NO_evidence():
    """`merge_drafts` stringifies EVERY list leaf, so a row-only site's merged
    rows are always an artifact and `best` is always None. Reporting that as
    "no gate visible evidence" is affirmatively false -- the evidence is
    unanimous and sits in `candidates` two lines below. Reptyle's modal shape is
    exactly this population."""
    raws = [_draft({"row_selectors": ["[role=dialog] a.dl"]})] * 3
    normed = [normalize_draft(r) for r in raws]
    nm = normalize_draft(MERGE(raws, ["a", "b", "c"]))
    # Injected rather than merged, for the reason given in the artifact test
    # above: @988 fixed the producer and the guard has to outlive it.
    nm["selectors"]["download"]["row_selectors"] = ['["[role=dialog] a.dl"]']
    gs = CORP._gate_support(nm, normed, raws)
    assert gs["reason"] == "merge_artifact_only", (
        "the reason names the wrong cause: %r" % gs["reason"])
    assert gs["clauses"]["row_selectors"]["candidates"][0]["support"] == 3, (
        "the unanimous evidence the reason denies must still be visible")
    assert CORP._reliability(3, gs) == "unknown"

    # The other direction: a site that genuinely has nothing must still say so.
    none, _ = _support([_draft({"frob": "x"})] * 2)
    assert none["reason"] == "no_gate_visible_evidence"


def test_the_PARTITION_check_is_driven_directly_in_both_directions():
    """@984 learned this the expensive way: a guard today's control flow cannot
    violate is asserted by the code and constrained by nothing, so its mutant
    escapes a green band. `_gate_support` derives all three counters itself and
    cannot currently produce an inconsistent set -- which is exactly why the
    check is extracted and driven here rather than trusted in place."""
    assert CORP._partition_ok({"support": 2, "disagree": 1, "absent": 0}, 3)
    assert CORP._partition_ok({"support": 0, "disagree": 0, "absent": 4}, 4)
    # The other direction, or a check that returned True always would pass the
    # assertions above and guard nothing.
    assert not CORP._partition_ok({"support": 2, "disagree": 1, "absent": 1}, 3)
    assert not CORP._partition_ok({"support": 1, "disagree": 0, "absent": 0}, 3)


def test_the_ROLLUP_decomposes_gate_selector_blocked_by_CAUSE():
    """The operator was told "77 of 82 sites are blocked on gate_selector" and,
    on the strength of it, that those 77 needed re-capturing. That advice was
    retracted: the captures contain the download control and the normalizer
    discards it for not being modal-scoped. A bare count cannot tell those
    apart, so it can only ever produce advice like that.

    RED today: `blocking` carries the bare string and nothing aggregates it."""
    def site(gaps, blocking=("gate_selector",), of=2):
        return {"blocking": list(blocking),
                "gate_support": {"of": of, "capture_gaps": dict(
                    {"no_download_evidence": 0, "evidence_dropped_by_pipeline": 0,
                     "dropped_leaves": {}, "dropped_rows": []}, **gaps)}}

    sites = [
        site({"evidence_dropped_by_pipeline": 2,
              "dropped_rows": [{"value": "a.dl", "supported_by": 2, "of": 2}]}),
        site({"no_download_evidence": 2}),
        site({}),                                    # neither -> the residual
        site({"no_download_evidence": 2}, blocking=("resolutions",)),
    ]
    roll = CORP._gate_blocked_rollup(sites)

    assert roll["sites"] == 3, (
        "a site blocked on a DIFFERENT clause was counted: %r" % roll)
    by = roll["by_cause"]
    assert by["download_control_discarded_by_normalizer"] == 1
    assert by["no_download_evidence_in_any_draft"] == 1
    assert by["other"] == 1
    assert sum(by.values()) == roll["sites"], (
        "the causes must PARTITION the blocked sites -- a bucket that does not "
        "sum is a count nobody can act on: %r" % roll)


def test_assess_EXPOSES_the_gate_boolean_it_already_computes():
    """`assess` computes gate_selector = trigger|rows|button and returns
    neither it nor `button`. `_blocking` therefore re-derived the gate from
    two of its three clauses and agreed only by the coincidence that normalize
    collapses button into trigger. Measured -- so this is a blindness that is
    currently harmless, and would become wrong silently."""
    def t(dl):
        return {"selectors": {"download": dl}, "resolutions": [1080]}

    assert assess(t({"trigger": "a"}))["gate_selector"] is True
    assert assess(t({"row_selectors": ["div.modal a"]}))["gate_selector"] is True
    assert assess(t({"button": "b"}))["gate_selector"] is True
    assert assess(t({}))["gate_selector"] is False
    for dl in ({"trigger": "a"}, {"button": "b"}, {}):
        a = assess(t(dl))
        assert a["gate_selector"] == bool(
            a["download_trigger"] or a["row_selectors_count"]
            or (dl.get("button") or "")), dl


def test_blocking_READS_the_gate_and_fails_LOUDLY_when_it_cannot():
    """Both directions. The button-only template is the case where the old
    re-derivation disagreed with the gate; the missing-key case is the one a
    silent fallback would paper over."""
    button_only = assess({"selectors": {"download": {"button": "b"}},
                          "resolutions": [1080]})
    assert button_only["promotion_ready"] is True, "fixture drifted"
    assert "gate_selector" not in CORP._blocking(button_only), (
        "reported the gate clause as blocking on a template the gate passes")

    assert CORP._blocking({"resolutions": [1080]}) == ["gate_unassessed"], (
        "an assessment carrying no gate verdict must fail as UNKNOWN rather "
        "than assert a confident 'gate_selector'")
    assert CORP._blocking({"gate_selector": True, "resolutions": [1080]}) == []
    assert CORP._blocking({"gate_selector": False,
                           "resolutions": [1080]}) == ["gate_selector"]
    assert CORP._blocking(None) == ["no_draft"]
