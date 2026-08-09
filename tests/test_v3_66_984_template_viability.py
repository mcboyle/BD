"""bd-wacz-corpus --templates: which sites can produce a GREEN template, from
one capture or from a combination of that site's captures.

@984, the operator's question: "can any of the files, or a combination of the
same site captures, make a green and reliable template?"

GREEN IS NOT DEFINED HERE. It is already defined three times over --
`tools/promote_template.py` is the gate, and `template_inventory.assess()`
mirrors it deliberately "so the numbers can't diverge from reality". This mode
RUNS that predicate; it does not restate it. A fourth definition of green is how
a tool starts disagreeing with the gate it exists to predict.

    gate_selector    = download.trigger OR row_selectors OR download.button
    promotion_ready  = gate_selector AND resolutions AND no blocked terms

RELIABLE IS SUPPORT ACROSS A SITE'S CAPTURES -- the operator's definition, taken
verbatim. `bd-template-merge.merge_drafts` already records it with denominators
("2 of 3"), so this mode reads that rather than inventing a score.

    corroborated    the gate selector is backed by MORE THAN ONE capture
    single_witness  exactly one capture saw it and the others did not
    unknown         one capture in the group, so there is nothing to corroborate

THE THIRD STATE IS LOAD-BEARING. A lone capture reports support 1 of 1, which is
arithmetically indistinguishable from a selector every capture agrees on --
`bd-template-merge`'s own docstring refuses to merge one draft for exactly this
reason. Reporting a single-capture site as 100% reliable would be a gate
reporting clean over a denominator that cannot contain its subject.

Reliability is read from the GATE-CRITICAL keys only. An average over all
selectors would let a well-corroborated login mask a one-vote download trigger,
and the trigger is the thing the template lives or dies on.
"""

import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

_LOGIN = ('<input id="user-email"><input id="password">'
          '<button type="submit">Go</button>')
_TRIGGER = '<a class="download-btn" href="/dl" download>Download</a>'


def _cap(path, url, html, resolutions=None):
    """A capture whose resolutions arrive the way a REAL one's do: off the
    NETWORK LOG. An earlier draft of this helper set a top-level `resolutions`
    key, which `build_template` never reads -- the fixture was describing a
    capture shape that does not exist."""
    net = [{"url": "https://cdn.example.org/v/%dp/x.mp4" % r, "status": 200,
            "method": "GET"} for r in (resolutions or [])]
    cap = {"url": url, "captured_at": "2026-06-29T00:00:00Z",
           "dom_log": [{"type": "full_snapshot", "html": html}],
           "network_log": net}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive/capture.json", json.dumps(cap))
        z.writestr("pages/pages.jsonl",
                   json.dumps({"format": "json-pages-1.0"}) + "\n"
                   + json.dumps({"id": "page-0", "url": url}) + "\n")
    return path


def _run(root, *extra):
    r = subprocess.run([sys.executable, str(TOOL), "--root", str(root),
                        "--templates", "--json", *extra],
                       capture_output=True, text=True, timeout=900)
    assert r.stdout.strip(), "no stdout: rc=%d %s" % (r.returncode, r.stderr[-500:])
    out = json.loads(r.stdout)
    assert "templates" in out.get("modes", {}), (
        "--templates produced no `templates` mode: %r" % sorted(out.get("modes", {})))
    return r.returncode, out["modes"]["templates"]


def _by_host(mode):
    return {s["host"]: s for s in mode["sites"]}


def _tool_module():
    """Load the extensionless tool so its pure rules can be driven directly."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_wacz_corpus_tpl", str(TOOL))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def test_a_site_GREEN_FROM_ONE_capture_names_that_capture(tmp_path):
    """The simplest useful answer: this one file is already enough."""
    _cap(tmp_path / "a.wacz", "https://solo.example.org/s/1",
         _LOGIN + _TRIGGER, resolutions=[1080, 720])
    rc, t = _run(tmp_path)
    s = _by_host(t)["solo.example.org"]
    assert s["verdict"] == "green_from_one", s
    assert s["green_capture"] == "a.wacz", s
    assert s["best_single_score"] > 0, s


def test_a_site_GREEN_ONLY_MERGED_says_so_and_names_the_inputs(tmp_path):
    """The operator's actual question. Neither capture is enough alone; together
    they are. A tool that only scored single captures would report this site as
    hopeless, which is the answer that costs a usable template."""
    # Each capture is missing a DIFFERENT gate clause, which is the only way to
    # construct this case: x1 has the gate selector and no resolutions, x2 has
    # resolutions and no gate selector. Neither passes; the union does.
    # (`merge_drafts` unions `resolution_priority` across drafts, so the
    # resolution x2 contributes really does reach the merged draft -- verified
    # by reading the merge, not assumed from its name.)
    _cap(tmp_path / "x1.wacz", "https://combo.example.org/s/1", _TRIGGER)
    _cap(tmp_path / "x2.wacz", "https://combo.example.org/s/2",
         _LOGIN, resolutions=[1080])
    rc, t = _run(tmp_path)
    s = _by_host(t)["combo.example.org"]
    assert s["verdict"] == "green_only_merged", s
    assert s["green_capture"] is None, s
    assert sorted(s["merge_inputs"]) == ["x1.wacz", "x2.wacz"], s
    assert s["merged_ready"] is True and s["best_single_ready"] is False, s


def test_a_site_that_CANNOT_be_green_says_WHY_in_the_GATES_OWN_TERMS(tmp_path):
    """Not "low score" -- which of the gate's clauses failed. A reader has to be
    able to act on it, and "missing resolutions" is actionable where "42/100"
    is not."""
    _cap(tmp_path / "n1.wacz", "https://nope.example.org/s/1", _LOGIN)
    _cap(tmp_path / "n2.wacz", "https://nope.example.org/s/2", _LOGIN)
    rc, t = _run(tmp_path)
    s = _by_host(t)["nope.example.org"]
    assert s["verdict"] == "not_green", s
    assert "gate_selector" in s["blocking"], (
        "the failing gate clause was not named: %r" % s["blocking"])
    assert "resolutions" in s["blocking"], s


def test_RELIABILITY_is_SUPPORT_across_the_sites_captures(tmp_path):
    """The operator's definition, verbatim. Two captures that BOTH carry the
    gate selector is corroboration; the number and its denominator both travel."""
    for i in (1, 2):
        _cap(tmp_path / ("r%d.wacz" % i), "https://corro.example.org/s/%d" % i,
             _TRIGGER, resolutions=[1080])
    rc, t = _run(tmp_path)
    s = _by_host(t)["corro.example.org"]
    assert s["reliability"] == "corroborated", s
    g = s["gate_support"]
    assert g["support"] == 2 and g["of"] == 2, (
        "support must carry its denominator, never a bare count: %r" % g)


def test_a_ONE_VOTE_gate_selector_is_a_SINGLE_WITNESS_not_corroboration(tmp_path):
    """The distinction that makes the word mean something. The merge is green,
    but the selector it is green ON was seen by one capture of three -- exactly
    the "one-off produced by a broken capture" bd-template-merge was designed to
    rank rather than hide."""
    _cap(tmp_path / "w1.wacz", "https://lone.example.org/s/1",
         _TRIGGER, resolutions=[1080])
    for i in (2, 3):
        _cap(tmp_path / ("w%d.wacz" % i), "https://lone.example.org/s/%d" % i,
             _LOGIN, resolutions=[1080])
    rc, t = _run(tmp_path)
    s = _by_host(t)["lone.example.org"]
    assert s["reliability"] == "single_witness", s
    assert s["gate_support"]["support"] == 1 and s["gate_support"]["of"] == 3, s


def test_a_SINGLE_CAPTURE_site_reports_reliability_UNKNOWN_not_perfect(tmp_path):
    """THE LOAD-BEARING ASSERTION, and the one a plausible implementation gets
    wrong. One capture yields support 1 of 1, which is arithmetically identical
    to a selector every capture agrees on. Calling that corroborated is a
    verdict over a denominator that cannot contain its subject -- and
    bd-template-merge refuses to merge one draft for this exact reason."""
    _cap(tmp_path / "one.wacz", "https://alone.example.org/s/1",
         _TRIGGER, resolutions=[1080])
    rc, t = _run(tmp_path)
    s = _by_host(t)["alone.example.org"]
    assert s["verdict"] == "green_from_one", s
    assert s["reliability"] == "unknown", (
        "a lone capture reported reliability %r -- 1 of 1 is indistinguishable "
        "from a universal and must not read as corroboration" % s["reliability"])
    assert s["captures"] == 1, s


def test_an_UNBUILDABLE_capture_is_its_own_state_not_a_failed_template(tmp_path):
    """An archive that yields no draft answers nothing about the site. Folding it
    into `not_green` would report a measurement where none was taken."""
    (tmp_path / "broken.wacz").write_bytes(b"PK\x03\x04 not a zip")
    rc, t = _run(tmp_path)
    s = _by_host(t)["broken"]
    assert s["verdict"] == "unbuildable", s
    assert s["drafts_built"] == 0 and s["captures"] == 1, s
    assert t["unbuildable_captures"] == 1, t


def test_the_mode_ACCOUNTS_for_every_capture_and_states_its_denominator(tmp_path):
    """The non-empty-denominator assertion, written before the verdict."""
    _cap(tmp_path / "a.wacz", "https://one.example.org/s/1",
         _TRIGGER, resolutions=[1080])
    _cap(tmp_path / "b.wacz", "https://two.example.org/s/1", _LOGIN)
    (tmp_path / "c.wacz").write_bytes(b"PK\x03\x04 nope")
    rc, t = _run(tmp_path)
    assert t["examined"] == 3, t
    assert sum(s["captures"] for s in t["sites"]) == t["examined"], (
        "the per-site rows do not account for every capture examined: %r" % t)
    assert sum(t["verdicts"].values()) == len(t["sites"]), t
    assert t["denominator"], "the mode states no denominator"


def test_GREEN_is_the_PROMOTE_GATES_verdict_not_a_second_definition(tmp_path):
    """Asserted against the real predicate rather than by reading the code.

    `template_inventory.assess` mirrors `promote_template.py`. This test runs
    BOTH -- the mode, and `assess` directly on a draft built from the same
    capture -- and requires them to agree. If a future edit invents a local
    notion of green, the two diverge and this fails.
    """
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "tools"))
    from build_template_from_wacz import build_template
    from template_inventory import assess
    from bulk_downloader.template_normalize import normalize_draft

    p = _cap(tmp_path / "a.wacz", "https://truth.example.org/s/1",
             _LOGIN + _TRIGGER, resolutions=[1080])
    # THE FULL pipeline: build -> normalize -> assess. An earlier version of this
    # test called assess(build_template(p)) -- skipping normalize, exactly as the
    # mode did -- so both sides carried the same error and agreed on False. A
    # comparison whose two halves share a defect proves the defect, not the code.
    # MEASURED: assess(raw) is False and assess(normalize(raw)) is True for this
    # same capture, because the builder emits `resolution_priority` and the
    # promote-gate mirror reads `resolutions`.
    canonical = assess(normalize_draft(build_template(p)))["promotion_ready"]
    assert canonical is True, (
        "the canonical pipeline did not produce a promotable draft, so the "
        "comparison below would hold for the wrong reason")
    rc, t = _run(tmp_path)
    s = _by_host(t)["truth.example.org"]
    assert s["best_single_ready"] is canonical, (
        "the mode says %r and the real promote-gate mirror says %r"
        % (s["best_single_ready"], canonical))


def test_the_mode_says_so_when_the_TEMPLATE_BUILDER_is_unavailable(tmp_path):
    """Same discipline as `--hosts`' filename parser. The builder is imported
    from `tools/`, not vendored, so a rename makes this mode unable to answer --
    at which point it must report that, not grade every site `not_green`."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_wacz_corpus_tpl_test",
                                                  str(TOOL))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)

    p = _cap(tmp_path / "a.wacz", "https://t.example.org/s/1",
             _LOGIN + _TRIGGER, resolutions=[1080])
    live = mod.mode_templates([p])
    assert "build_template" in live["template_builder"], live
    assert live["verdicts"].get("green_from_one") == 1, (
        "the real builder produced no green site, so the negative half below "
        "would pass for the wrong reason: %r" % live)

    mod._BUILDER_CACHE = (None, None, "UNAVAILABLE: forced by test")
    dead = mod.mode_templates([p])
    assert dead["template_builder"].startswith("UNAVAILABLE"), dead
    assert dead["verdicts"].get("green_from_one", 0) == 0, (
        "a verdict was reported by a mode that could not build a draft: %r"
        % dead)


def test_a_LONE_DRAFT_is_never_corroboration_however_high_its_support_reads():
    """Closing a mutation ESCAPE, and the escape is the interesting part.

    The n<2 guard used to sit inside `mode_templates`, where it was
    UNREACHABLE: a single-capture site never produces a merged draft, so the
    `gate_support is None` branch answered first. Deleting the guard changed
    nothing observable and the battery's mutant escaped a green band -- the rule
    was asserted by the code and constrained by nothing.

    Driven directly, both directions. The n=1 case is handed a support of 2,
    which today's control flow cannot produce; that is the point. If a future
    change ever merges a lone draft, the rule must still refuse to call its
    support corroboration rather than inherit the answer from an accident of
    ordering.
    """
    m = _tool_module()
    assert m._reliability(1, {"support": 2, "of": 2}) == "unknown", (
        "a lone draft was graded on support that cannot mean what it says")
    assert m._reliability(1, None) == "unknown"
    # The other direction, or a rule that returned "unknown" always would pass
    # the assertions above and destroy the mode.
    assert m._reliability(3, {"support": 2, "of": 3}) == "corroborated"
    assert m._reliability(3, {"support": 1, "of": 3}) == "single_witness"
    assert m._reliability(2, None) == "unknown"


def test_it_never_writes_a_template_or_touches_the_corpus(tmp_path):
    """It scores drafts in memory. Promotion stays the operator's."""
    import hashlib
    p = _cap(tmp_path / "a.wacz", "https://ro.example.org/s/1",
             _TRIGGER, resolutions=[1080])
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    listing = sorted(x.name for x in tmp_path.iterdir())
    rc, _t = _run(tmp_path)
    assert rc in (0, 1), rc
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == listing, (
        "the mode wrote into the corpus directory")
