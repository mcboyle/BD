"""bd-wacz-corpus: derivative markers appear in more forms than a dot prefix.

@983, item 44's parent class. @978 fixed `_is_redacted` testing
`endswith(".redacted.wacz")`, which classified 197 of 601 redacted files as RAW
captures because a copy suffix moved the marker off the end. The marker set it
left behind is dot-prefixed -- `.redacted`, `.scrubbed` -- and the box's corpus
also uses UNDERSCORE forms with a profile qualifier.

MEASURED on the operator's real file list, 742 wacz under `captures/`. Seven
names carry a form the dot-prefixed set cannot see:

    bang247_redacted_safe.wacz        cap_f78f_redacted_safe.wacz
    bang247_redacted_strict.wacz      shaka.redacted_2.wacz
    cap_355b_redacted_safe.wacz       wow247_redacted_strict.wacz
                                      wow248_redacted_safe.wacz

Six read as RAW captures; the seventh bases to the fragment `shaka_2`, so it can
never pair with `shaka`. Every fixture below is one of those real names -- the
same class has now escaped synthetic fixtures twice, which is the whole argument
for not inventing an eighth.

WHY SIX FILES OF 742 IS WORTH A CUT. It is 0.8% and the count is not the point:
the defect INVERTS A FINDING. `--dupes` reports a derivative byte-identical to
its source as a no-op redaction -- @971 established that path could pass binary
members through untouched -- and that branch requires `_is_redacted` to be true
for a member of the hash group. While these six read as raw, a failed scrub
among them is reported as reclaimable disk instead of as evidence that redaction
never happened, which invites deleting the evidence.
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-wacz-corpus"

# The operator's real names, 2026-08-09, not invented shapes.
_UNDERSCORE_FORMS = {
    "bang247_redacted_safe.wacz": "bang247",
    "bang247_redacted_strict.wacz": "bang247",
    "cap_355b_redacted_safe.wacz": "cap_355b",
    "cap_f78f_redacted_safe.wacz": "cap_f78f",
    "wow247_redacted_strict.wacz": "wow247",
    "wow248_redacted_safe.wacz": "wow248",
}
_DOT_QUALIFIED = {"shaka.redacted_2.wacz": "shaka"}


def _tool():
    loader = importlib.machinery.SourceFileLoader("bd_wacz_corpus_marker_test",
                                                  str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _p(name):
    return pathlib.Path("/corpus") / name


def _wacz(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive/data.warc", payload)
        z.writestr("datapackage.json", b"{}")
    return path


def test_UNDERSCORE_derivative_forms_are_not_read_as_RAW_captures():
    """The six. A derivative counted as a raw capture is a phantom capture: it
    inflates the corpus's raw population and its source loses a twin."""
    m = _tool()
    raw = [n for n in _UNDERSCORE_FORMS if not m._is_redacted(_p(n))]
    assert not raw, (
        "%d of %d real underscore-form derivatives classified as RAW captures: "
        "%r" % (len(raw), len(_UNDERSCORE_FORMS), sorted(raw)))


def test_an_UNDERSCORE_derivative_bases_to_its_SOURCE():
    m = _tool()
    wrong = {n: m._base(_p(n)) for n, want in _UNDERSCORE_FORMS.items()
             if m._base(_p(n)) != want}
    assert not wrong, (
        "these did not resolve to the source capture they derive from: %r"
        % wrong)


def test_a_QUALIFIED_dot_marker_does_not_leave_a_FRAGMENT():
    """`shaka.redacted_2` currently bases to `shaka_2` -- the marker is stripped
    and its qualifier is left welded to the stem, so the derivative can never
    pair with `shaka`. A half-stripped base is worse than an unstripped one: it
    matches nothing and looks like a real name."""
    m = _tool()
    for n, want in _DOT_QUALIFIED.items():
        got = m._base(_p(n))
        assert got == want, (
            "%s based to %r, leaving the marker's qualifier welded to the stem; "
            "expected %r" % (n, got, want))


def test_TWO_REDACTION_PROFILES_of_one_source_are_ONE_family():
    """`bang247` carries both `_redacted_safe` and `_redacted_strict`. They are
    two profiles of one capture, not two captures, so a template merge must see
    ONE source -- the distinction `--hosts` counts as `sources`."""
    m = _tool()
    names = ["bang247.wacz", "bang247_redacted_safe.wacz",
             "bang247_redacted_strict.wacz"]
    bases = {m._base(_p(n)) for n in names}
    assert bases == {"bang247"}, (
        "two redaction profiles of one source did not collapse to one family: "
        "%r" % sorted(bases))


def test_the_strip_does_not_EAT_the_source_names_own_underscores():
    """The over-stripping direction, and it is the one a greedy fix breaks.
    `cap_355b_redacted_safe` must lose only `_redacted_safe`; a rule that ate
    everything from the first underscore would return `cap`, silently merging
    every `cap_*` capture into one bucket."""
    m = _tool()
    assert m._base(_p("cap_355b_redacted_safe.wacz")) == "cap_355b"
    assert m._base(_p("cap_f78f_redacted_safe.wacz")) == "cap_f78f"


def test_a_HYPHEN_indexed_capture_is_NOT_treated_as_a_derivative():
    """Over-sensitivity guard. `wowza` and `wowza-1` are DIFFERENT captures --
    measured on the box, 1,553,393 vs 1,532,106 bytes -- so collapsing them
    would delete a real capture from the corpus's count and understate a site's
    merge inputs. `-1` is an index, not a marker."""
    m = _tool()
    assert m._is_redacted(_p("wowza-1.wacz")) is False
    assert m._base(_p("wowza-1.wacz")) == "wowza-1"
    assert m._base(_p("wow259-2.wacz")) == "wow259-2"


def test_a_stem_that_BEGINS_with_a_marker_word_is_not_a_derivative():
    """A marker needs a separator before it. Without that requirement a capture
    of a site whose name merely starts with the word would be reclassified, and
    its base would be the empty string."""
    m = _tool()
    assert m._is_redacted(_p("redacted_site.wacz")) is False
    assert m._base(_p("redacted_site.wacz")) == "redacted_site"


def test_the_FORMS_ALREADY_SUPPORTED_still_work():
    """The regression direction. @978 paid for these; a rewrite that fixes the
    underscore forms and loses the dot forms is a net loss over the corpus,
    where `.redacted` outnumbers the underscore forms 329 to 6."""
    m = _tool()
    for n in ("x.redacted.wacz", "x.scrubbed.wacz", "x.redacted.scrubbed.wacz",
              "x.redacted (2).wacz", "x.scrubbed (dup1).wacz"):
        assert m._is_redacted(_p(n)) is True, n
        assert m._base(_p(n)) == "x", (n, m._base(_p(n)))
    assert m._is_redacted(_p("x.wacz")) is False
    assert m._base(_p("x.wacz")) == "x"


def test_a_NOOP_SCRUB_in_an_UNDERSCORE_FORM_is_a_FINDING_not_reclaimable_disk(tmp_path):
    """The consequence that makes six files worth a cut, end to end.

    A derivative byte-identical to its source means the scrubber returned its
    input. While the underscore forms read as RAW, the pair looks like two
    ordinary duplicate files and `--dupes` offers the bytes back as reclaimable
    -- which invites deleting the evidence that redaction never happened.
    """
    same = b"WARC/1.1\r\n\r\n" + b"z" * 4000
    _wacz(tmp_path / "wow248.wacz", same)
    _wacz(tmp_path / "wow248_redacted_safe.wacz", same)
    assert (hashlib.sha256((tmp_path / "wow248.wacz").read_bytes()).digest()
            == hashlib.sha256(
                (tmp_path / "wow248_redacted_safe.wacz").read_bytes()).digest()), (
        "the fixture is not byte-identical, so this test cannot observe its "
        "subject")

    r = subprocess.run([sys.executable, str(TOOL), "--root", str(tmp_path),
                        "--dupes", "--json"],
                       capture_output=True, text=True, timeout=300)
    d = json.loads(r.stdout)["modes"]["dupes"]
    assert d["noop_derivatives"] == 1, (
        "a byte-identical underscore-form derivative was not reported as a "
        "no-op redaction: %r" % d)
    assert d["reclaimable_bytes"] == 0, (
        "the failed scrub was offered back as reclaimable disk (%d bytes) -- "
        "that is the evidence, not a duplicate" % d["reclaimable_bytes"])
    assert r.returncode == 1, (
        "a no-op redaction is a FINDING; exit was %d" % r.returncode)
