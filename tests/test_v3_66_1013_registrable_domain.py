"""@1013. One correct registrable-domain rule, replacing thirteen wrong ones.

MEASURED, and the census is the first finding. A grep for the FUNCTION NAMES
(`_registrable`, `_etld1`, `get_registrable_domain`) found nine. An AST census
matching on the SHAPE -- any function joining the last two labels of a
`split(".")` -- found THIRTEEN, over 2164 tracked `.py` files. The four the name
grep could not see are `login_templates_data.suggest_login_for_url`,
`login_templates_data._reg_domain`, `rate_limit._extract_domain`, and the pair
of `_is_same_etld1` predicates. CLAUDE.md section 1, exactly: the instrument
fixes the denominator, and a name is not one.

ALL THIRTEEN ARE THE SAME ONE-LINE BUG: `".".join(host.split(".")[-2:])`. That
is correct for `members.vip4k.com` and wrong for every multi-part public suffix.
Measured on five implementations before any change -- all five agreed, and all
five returned `co.uk` for `www.bbc.co.uk`.

IT IS NOT COSMETIC. Two of the thirteen are same-site PREDICATES gating whether
a URL found in a listing or a search result gets followed
(`playlist_extractor.py:328`, `search_extractor.py:307`). Measured against the
shipped code:

    same-site?  True   https://victim.co.uk/list  vs  https://attacker.co.uk/x
    same-site?  True   https://a.github.io/list   vs  https://b.github.io/x
    same-site?  True   https://site.com.au/list   vs  https://evil.com.au/x

Every GitHub Pages site is "the same site" as every other one, and any two
`.co.uk` registrants are too. A same-site check that says yes to unrelated
registrants is a scope escape in a fetch decision, so the security call sites
migrate first and the rest follow.

WHY A CURATED SUFFIX SET AND NOT A LIBRARY. `tldextract`, `publicsuffix2` and
`publicsuffixlist` are all absent, and adding one would put a new runtime
dependency on the deploy box, in requirements.txt, and through the dep-freshness
gate -- for a rule whose entire job here is to be right about a few dozen
suffixes. The set below is explicit, testable, and carries its own
"what happens to a suffix we do not know" answer. THE HONEST LIMIT IS STATED
RATHER THAN IMPLIED: an unknown multi-part suffix still degrades to the old
last-two-labels answer, which is no worse than today and is not correct. That is
recorded here, not hidden in a docstring nobody reads.
"""
from __future__ import annotations

import ast
import copy
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _rd():
    from bulk_downloader import registrable_domain as rd
    return rd


# ── the rule itself ───────────────────────────────────────────────

@pytest.mark.parametrize("host,want", [
    # the ordinary case the old rule got right, which must not regress
    ("members.vip4k.com", "vip4k.com"),
    ("auth.wowgirls.com", "wowgirls.com"),
    ("venus.wowgirls.com", "wowgirls.com"),
    ("site-ma.bangbros.com", "bangbros.com"),
    ("vip4k.com", "vip4k.com"),
    # the multi-part suffixes the old rule got wrong
    ("www.bbc.co.uk", "bbc.co.uk"),
    ("bbc.co.uk", "bbc.co.uk"),
    ("site.com.au", "site.com.au"),
    ("a.github.io", "a.github.io"),
    ("shop.co.jp", "shop.co.jp"),
])
def test_the_registrable_domain_is_correct(host, want):
    assert _rd().registrable_domain(host) == want


@pytest.mark.parametrize("host", ["", None, ".", "localhost", "..", "a."])
def test_degenerate_input_never_raises(host):
    """These reach the rule from urlparse().hostname on malformed URLs. A
    same-site predicate that raises is a same-site predicate that fails open in
    whatever except: clause encloses it."""
    got = _rd().registrable_domain(host)
    assert isinstance(got, str)


def test_a_bare_public_suffix_is_not_a_registrable_domain():
    """`co.uk` alone has no registrant. Returning it as though it were one is
    how the old rule made two strangers the same site."""
    rd = _rd()
    assert rd.registrable_domain("co.uk") == "co.uk"
    assert rd.is_public_suffix("co.uk") is True
    assert rd.is_public_suffix("bbc.co.uk") is False


def test_an_UNKNOWN_multi_part_suffix_degrades_and_SAYS_SO():
    """The stated limit, asserted so it stays true. A curated set cannot know
    every suffix; what it must not do is pretend otherwise."""
    rd = _rd()
    assert rd.registrable_domain("a.b.invalidsuffixtld") == "b.invalidsuffixtld"
    assert rd.is_known_suffix("co.uk") is True
    assert rd.is_known_suffix("invalidsuffixtld") is False


# ── the same-site predicate, which is the security half ───────────

@pytest.mark.parametrize("a,b", [
    ("https://victim.co.uk/list", "https://attacker.co.uk/x"),
    ("https://a.github.io/list", "https://b.github.io/x"),
    ("https://site.com.au/list", "https://evil.com.au/x"),
])
def test_unrelated_registrants_are_NOT_same_site(a, b):
    """RED on pristine source: the shipped `_is_same_etld1` returns True for
    every one of these."""
    assert _rd().same_site(a, b) is False, (a, b)


@pytest.mark.parametrize("a,b", [
    ("https://members.vip4k.com/l", "https://vip4k.com/x"),
    ("https://auth.wowgirls.com/l", "https://venus.wowgirls.com/x"),
    ("https://www.bbc.co.uk/a", "https://news.bbc.co.uk/b"),
])
def test_the_SAME_registrant_still_is_same_site(a, b):
    """THE OTHER DIRECTION, and the one that breaks downloads if it is wrong.
    A predicate that returns False for everything satisfies the security test
    above and stops every listing crawl from following anything."""
    assert _rd().same_site(a, b) is True, (a, b)


def test_two_urls_on_a_BARE_public_suffix_are_not_same_site():
    """CLOSES A MEASURED MUTATION ESCAPE. Deleting the is_public_suffix guard in
    same_site() left the whole band green, because for victim.co.uk vs
    attacker.co.uk the registrable domains already differ and the guard never
    fires. It is load-bearing only when BOTH hosts are themselves bare public
    suffixes -- where the domains are equal and there is no registrant to be
    equal ABOUT. Unusual input, which is exactly the kind a fetch predicate must
    fail closed on.
    """
    rd = _rd()
    assert rd.same_site("https://co.uk/a", "https://co.uk/b") is False
    assert rd.same_site("https://github.io/a", "https://github.io/b") is False


def test_same_site_is_false_on_garbage_not_true():
    """Fail CLOSED. An unparseable URL is not a same-site match."""
    rd = _rd()
    assert rd.same_site("", "https://x.com/") is False
    assert rd.same_site("not a url", "also not") is False


# ── the security call sites actually use it ───────────────────────

def _joins_last_two_labels(fn: ast.FunctionDef) -> bool:
    """Whether `fn`'s CODE computes a domain by joining the last two labels.

    DOCSTRINGS AND COMMENTS ARE STRIPPED FIRST, and that is not tidiness. The
    first version of this predicate unparsed the node whole, so the docstring
    this cut added to each migrated function -- which necessarily describes the
    pattern it removed -- matched, and both call sites reported UNMIGRATED after
    being correctly migrated. CLAUDE.md section 0 records four separate cuts
    where an assertion could not tell prose from code, and this is the fifth:
    "Explaining a removal by naming the removed thing recreates it."

    `ast.unparse` drops comments already; docstrings survive as expression
    statements and have to be removed by hand.
    """
    stripped = copy.deepcopy(fn)
    for node in ast.walk(stripped):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(stripped)
    return ("split('.')" in code or 'split(".")' in code) and "[-2:]" in code


_SECURITY_SITES = ("bulk_downloader/playlist_extractor.py",
                   "bulk_downloader/search_extractor.py")


def test_the_security_call_sites_no_longer_join_the_last_two_labels():
    """The subject is the SHAPE, not the name -- renaming the old helper would
    satisfy a name-based check while leaving the bug."""
    offenders = []
    for rel in _SECURITY_SITES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and _joins_last_two_labels(n):
                offenders.append("%s:%d %s" % (rel, n.lineno, n.name))
    assert not offenders, (
        "these still compute a registrable domain by joining the last two "
        "labels: %r" % offenders)


def test_the_shape_census_can_still_SEE_the_pattern():
    """Non-empty denominator for the census below. A scan that matches nothing
    would report 'no remaining copies' truthfully and uselessly, so it is
    proven able to find the shape in a known-positive sample first."""
    positive = "def f(h):\n    return '.'.join(h.split('.')[-2:])\n"
    fn = next(n for n in ast.walk(ast.parse(positive))
              if isinstance(n, ast.FunctionDef))
    assert _joins_last_two_labels(fn), "the predicate cannot see a known positive"

    # AND THE NEGATIVE, which is the half that was broken: a function whose
    # DOCSTRING describes the pattern while its code does not use it.
    prose = ('def f(h):\n'
             '    """Was computed by joining the last two labels of h.split(\'.\')[-2:]."""\n'
             '    from .registrable_domain import registrable_domain\n'
             '    return registrable_domain(h)\n')
    fn2 = next(n for n in ast.walk(ast.parse(prose))
               if isinstance(n, ast.FunctionDef))
    assert not _joins_last_two_labels(fn2), (
        "a docstring describing the removed pattern reads as the pattern")


def test_the_remaining_copies_are_COUNTED_so_the_backlog_cannot_grow():
    """A RATCHET, not a clean bill of health.

    Thirteen copies existed at @1013 and that cut migrated the two that gate a
    fetch. Asserting zero would have failed honestly and blocked the security
    fix behind eleven unrelated edits across extractors, rate limiting and login
    suggestion -- each of which deserved its own band. Asserting a CEILING let
    the rest be drained later while making a fourteenth impossible.

    v3.66.1018 drained the remaining eleven, so the ceiling is now ZERO and this
    is a clean bill of health rather than a ratchet. It stays written as a
    ceiling because that is what makes it one-directional: a fourteenth copy
    fails here whether or not anyone remembers this file exists.

    Lower this when a cut converts some. Never raise it.
    """
    files = [f for f in subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "*.py"],
        capture_output=True, text=True).stdout.split("\0") if f]
    assert len(files) > 1000, "the file census went blind (%d)" % len(files)
    # THE CANONICAL MODULE IS NOT A COPY. registrable_domain() ends with the
    # last-two-labels join as its DOCUMENTED fallback for a suffix the curated
    # set does not know -- that degradation is asserted by
    # test_an_UNKNOWN_multi_part_suffix_degrades_and_SAYS_SO. Counting the
    # definition as one of the copies it exists to replace would make the
    # ratchet unsatisfiable.
    #
    # ONE exemption, asserted to BE one: a list of paths is how an exemption
    # quietly grows into a second implementation nobody counts.
    _CANONICAL = "bulk_downloader/registrable_domain.py"
    assert (REPO / _CANONICAL).is_file(), _CANONICAL

    found = []
    for rel in files:
        if rel.startswith("tests/"):
            continue          # this file quotes the shape in its own prose
        if rel == _CANONICAL:
            continue
        try:
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8",
                                                    errors="replace"))
        except (SyntaxError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and _joins_last_two_labels(n):
                found.append("%s:%d %s" % (rel, n.lineno, n.name))
    assert len(found) <= 0, (
        "last-two-labels copies rose to %d, above the ratchet of 0. Every one "
        "of these is the bug that made victim.co.uk and attacker.co.uk the "
        "same site. Use bulk_downloader.registrable_domain.\n  %s"
        % (len(found), "\n  ".join(sorted(found))))
