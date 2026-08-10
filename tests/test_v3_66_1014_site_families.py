"""@1014. A labelled CANDIDATE tier grouping exact-host buckets into site families.

WHAT IT IS FOR -- 15.74 finding A, "Grouping splits every site". `_place_by_host`
buckets on the EXACT hostname, and five of the operator's seven sites span a
login host and a content host: `auth.wowgirls.com`/`venus.wowgirls.com`,
`vip4k.com`/`members.vip4k.com`, `auth.reptyle.com`/`app.reptyle.com`,
`bangbros.com`/`site-ma.bangbros.com`. The box data shows the split directly --
`auth.reptyle.com cap=5 trigger 5/5` beside `app.reptyle.com cap=62 trigger
34/62`. **A login-host bucket reporting green is a FALSE GREEN**, because login
selectors are scored and never gated.

THE DESIGN IS 15.74'S, NOT A NEW ONE, and its constraints are the point:

  * do NOT re-key `_place_by_host`;
  * keep the exact host as the MERGE unit -- bd-template-merge's single-host
    guard is correct for drafts, and cross-host merging is not what this fixes;
  * families are a CANDIDATE tier: a proposal a human confirms, never a silent
    regrouping.

WHY THE SIGNAL IS THE REGISTRABLE DOMAIN. The obvious key -- the operator's
filename stem -- is a guess, and `bd-wacz-corpus` states "never a guess" as a
design rule, keying its UNKNOWN bucket apart precisely so a stem cannot be
laundered into a measured group. The registrable domain is derived from the host
the ARCHIVE reports, so it inherits that authority. Measured against the
operator's real corpus listing, the siteid alternative is refuted: the
`{host}_{siteid}_{YYYYMMDD}` convention appears on 2 sources of roughly 600
(`auth.reptyle.com_0b60f1ec_...`, `pexels.com_1a820331_...`); everything else is
a nickname (`beeg.wacz`, `bitmovin1.redacted.wacz`, `capture (2).wacz`).

AND THE DOMAIN RULE HAD TO BE FIXED FIRST. @1013 replaced the naive
last-two-labels rule, which put `www.bbc.co.uk` in a family called `co.uk` --
one that would then swallow every other `.co.uk` host in the corpus. The
operator's corpus contains bbc captures, so that was not hypothetical.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _corpus():
    """Load the extensionless bd-wacz-corpus by PATH.

    BY PATH, and that is not incidental: an `import` of a name inside
    `toolchain/bin` reads to the dependency-freshness gate as an undeclared
    third-party requirement -- measured at v3.66.997, where exactly that turned
    CI red. A SourceFileLoader has no such side effect.
    """
    p = REPO / "toolchain" / "bin" / "bd-wacz-corpus"
    assert p.is_file(), p
    spec = importlib.util.spec_from_loader(
        "bd_wacz_corpus_under_test",
        importlib.machinery.SourceFileLoader("bd_wacz_corpus_under_test", str(p)))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bd_wacz_corpus_under_test", mod)
    spec.loader.exec_module(mod)
    return mod


def _groups(*specs):
    """Minimal group dicts of the shape mode_hosts emits."""
    out = []
    for host, captures, methods in specs:
        out.append({"host": host, "captures": captures, "sources": captures,
                    "merge_candidate": captures > 1, "methods": list(methods),
                    "files": []})
    return out


# ── the tier exists and groups what it should ─────────────────────

def test_the_helper_exists():
    assert hasattr(_corpus(), "site_families"), "site_families is missing"


def test_the_operators_real_splits_are_grouped():
    fams = _corpus().site_families(_groups(
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 62, ["archive"]),
        ("vip4k.com", 3, ["archive"]),
        ("members.vip4k.com", 9, ["archive"]),
        ("auth.reptyle.com", 5, ["filename"]),
        ("app.reptyle.com", 62, ["archive"]),
    ))
    by = {f["family"]: sorted(f["hosts"]) for f in fams}
    assert by["wowgirls.com"] == ["auth.wowgirls.com", "venus.wowgirls.com"]
    assert by["vip4k.com"] == ["members.vip4k.com", "vip4k.com"]
    assert by["reptyle.com"] == ["app.reptyle.com", "auth.reptyle.com"]


def test_a_LONE_host_is_not_a_family():
    """A family of one proposes nothing and would bury the real ones. The
    operator reads this list to confirm groupings; every row must be a claim."""
    fams = _corpus().site_families(_groups(
        ("beeg.com", 4, ["archive"]),
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 6, ["archive"]),
    ))
    assert [f["family"] for f in fams] == ["wowgirls.com"]


def test_UNKNOWN_buckets_never_enter_a_family():
    """`_place_by_host` keys unknowns apart on purpose, so that "a stem that
    happens to look like a host can never be laundered into a measured group".
    A family built from an unresolved stem would launder it right back."""
    fams = _corpus().site_families(_groups(
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 6, ["archive"]),
        ("wowgirls.com", 2, ["unknown"]),
    ))
    assert len(fams) == 1
    assert "wowgirls.com" not in fams[0]["hosts"], (
        "an UNKNOWN bucket was grouped into a measured family")
    assert sorted(fams[0]["hosts"]) == ["auth.wowgirls.com", "venus.wowgirls.com"]


def test_the_multi_part_suffix_case_v3_66_1013_had_to_fix_first():
    """Under the pre-@1013 rule these three collapse into one `co.uk` family --
    two unrelated registrants proposed as one site."""
    fams = _corpus().site_families(_groups(
        ("www.bbc.co.uk", 3, ["archive"]),
        ("news.bbc.co.uk", 2, ["archive"]),
        ("www.guardian.co.uk", 4, ["archive"]),
    ))
    by = {f["family"]: sorted(f["hosts"]) for f in fams}
    assert "co.uk" not in by, "unrelated .co.uk registrants were grouped"
    assert by["bbc.co.uk"] == ["news.bbc.co.uk", "www.bbc.co.uk"]


# ── it is a CANDIDATE, and says so ────────────────────────────────

def test_every_family_is_labelled_a_candidate():
    """The whole safety property. A family is a proposal for a human, and a
    consumer that cannot tell it from a measured grouping will treat it as one.
    """
    fams = _corpus().site_families(_groups(
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 6, ["archive"])))
    assert fams and all(f.get("tier") == "candidate" for f in fams), fams
    assert all(f.get("basis") == "registrable_domain" for f in fams), (
        "a family that does not say how it was derived cannot be reviewed")


def test_a_family_carries_the_evidence_a_reviewer_needs():
    """Capture counts per host, because that is how the operator recognises the
    login/content split -- `auth.reptyle.com cap=5` beside
    `app.reptyle.com cap=62` is the tell."""
    fams = _corpus().site_families(_groups(
        ("auth.reptyle.com", 5, ["filename"]),
        ("app.reptyle.com", 62, ["archive"])))
    f = fams[0]
    assert f["captures"] == {"auth.reptyle.com": 5, "app.reptyle.com": 62}


def test_the_existing_host_groups_are_NOT_mutated():
    """15.74: do not re-key _place_by_host. The exact host stays the merge unit,
    so this tier must be additive -- computing it may not disturb its input."""
    groups = _groups(("auth.wowgirls.com", 5, ["archive"]),
                     ("venus.wowgirls.com", 6, ["archive"]))
    before = [dict(g) for g in groups]
    _corpus().site_families(groups)
    assert groups == before, "site_families mutated the host groups"


def test_it_is_ADDITIVE_in_the_mode_output():
    """The report gains a key; nothing it already published changes shape."""
    mod = _corpus()
    import inspect
    src = inspect.getsource(mod.mode_hosts)
    assert '"site_families"' in src, (
        "mode_hosts does not publish the tier, so nothing can consume it")
    for kept in ('"hosts"', '"groups"', '"merge_candidates"'):
        assert kept in src, "mode_hosts stopped publishing %s" % kept


# ── degenerate input ──────────────────────────────────────────────

@pytest.mark.parametrize("groups", [[], None])
def test_no_groups_is_no_families_not_a_crash(groups):
    assert _corpus().site_families(groups) == []


def test_a_host_with_no_registrable_domain_is_skipped_not_grouped():
    """`localhost` and a bare label have no registrant. Grouping on "" would
    put every one of them in a single nameless family."""
    fams = _corpus().site_families(_groups(
        ("localhost", 2, ["archive"]),
        ("", 1, ["archive"]),
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 6, ["archive"])))
    assert [f["family"] for f in fams] == ["wowgirls.com"]


def test_TWO_hosts_with_no_registrant_do_not_form_a_NAMELESS_family():
    """CLOSES A MEASURED MUTATION ESCAPE. Deleting the no-registrant guard left
    the band green, because the case above has ONE `localhost` and ONE `""` --
    each a singleton, so the "a family needs two hosts" filter dropped them for
    an unrelated reason and the guard never had to work.

    Two hosts that both reduce to nothing is where it bites: they collide into
    one family keyed on the empty string, and every host BD cannot resolve a
    registrant for would be proposed as one site.

    `"."` and `".."` rather than `""` and `"."`, because an EMPTY host is
    already dropped by a different guard -- so the first version of this test
    still escaped the mutant, having exercised the wrong one of two guards that
    both happen to produce the right answer."""
    fams = _corpus().site_families(_groups(
        (".", 3, ["archive"]),
        ("..", 4, ["archive"]),
        ("auth.wowgirls.com", 5, ["archive"]),
        ("venus.wowgirls.com", 6, ["archive"])))
    assert [f["family"] for f in fams] == ["wowgirls.com"], (
        "a family with no registrable domain was proposed: %r"
        % [f["family"] for f in fams])
