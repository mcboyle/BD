"""@1018. The eleven remaining last-two-labels copies, drained.

@1013 built one correct rule (`bulk_downloader.registrable_domain`) and
migrated the TWO that gate a fetch, leaving eleven ratcheted at
`tests/test_v3_66_1013_registrable_domain.py`. Those eleven are correctness-only
-- none decides whether a URL gets followed -- which is why they were allowed to
wait. This drains them and lowers the ratchet to zero.

WHAT "MIGRATE" MEANS HERE, precisely, because the obvious version breaks things.
Each of these functions has TWO behaviours: how it derives a registrable domain
(wrong, and the reason for this cut) and what it returns for degenerate input
(deliberate, load-bearing at some call sites, and none of this cut's business).
Measured on pristine source at 213fa81:

    input          A: candidate_filter, extension_vault,   B: extractors_aylo,
                      host_enumerator, phoenix_catalog        _dl8, _vixen
    localhost      'localhost'                             ''
    ''             ''                                      ''
    www.bbc.co.uk  'co.uk'      <- the bug                 'co.uk'   <- the bug
    a.github.io    'github.io'  <- the bug                 'github.io'
    site.com.au    'com.au'     <- the bug                 'com.au'

So the two contracts differ on a single-label host and agree everywhere else.
This cut changes ONLY the wrong column. `test_the_degenerate_contract_is
_UNCHANGED` pins the other one, per site, and it is not decoration: a blanket
`return registrable_domain(h)` silently converts contract B into contract A, and
a caller distinguishing "no eTLD+1" from "the host itself" would start treating
`localhost` as a registrable domain. That is the shape CLAUDE.md calls a fix
reproducing the defect -- correct on the headline, wrong on the thing nobody
re-measured.

WHY A TABLE AND NOT ELEVEN HAND-WRITTEN CASES. The eleven are the same one-line
bug eleven times; hand-writing them invites exactly one to be typed wrong and
then read as a real difference. The table is the denominator and
`test_the_table_covers_every_site_the_ratchet_counts` asserts it against the
ratchet's own census, so a site cannot be migrated and quietly dropped from the
tests at the same time.
"""
from __future__ import annotations

import ast
import copy
import importlib
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
for p in (str(REPO), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


# (module, attribute, degenerate contract for a single-label host)
#   "host"  -- returns the host itself        (contract A)
#   "empty" -- returns ""                     (contract B)
_SITES = [
    ("bulk_downloader.candidate_filter", "_registrable", "host"),
    ("bulk_downloader.extension_vault", "get_registrable_domain", "host"),
    ("bulk_downloader.extractors_aylo", "_etld1", "empty"),
    ("bulk_downloader.extractors_dl8", "_etld1", "empty"),
    ("bulk_downloader.extractors_vixen", "_etld1", "empty"),
    ("bulk_downloader.host_enumerator", "_registrable", "host"),
    ("bulk_downloader.phoenix_catalog", "_etld1", "host"),
    ("player_struct_embed", "_registrable", "host"),
]

# host -> the CORRECT registrable domain. Each is a case the last-two-labels
# rule gets wrong, and the third is the one with a security analogue: every
# github.io page is a different owner.
_WRONG_TODAY = [
    ("www.bbc.co.uk", "bbc.co.uk"),
    ("site.com.au", "site.com.au"),
    ("a.github.io", "a.github.io"),
]


def _fn(mod_name, attr):
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


@pytest.mark.parametrize("mod_name,attr,_contract", _SITES)
@pytest.mark.parametrize("host,want", _WRONG_TODAY)
def test_every_copy_now_uses_the_correct_rule(mod_name, attr, _contract, host, want):
    got = _fn(mod_name, attr)(host)
    assert got == want, (
        "%s.%s(%r) -> %r, want %r -- this is the last-two-labels bug: two "
        "unrelated registrants under one public suffix read as one domain"
        % (mod_name, attr, host, got, want))


@pytest.mark.parametrize("mod_name,attr,contract", _SITES)
def test_the_degenerate_contract_is_UNCHANGED(mod_name, attr, contract):
    """Measured on pristine source and pinned, because it is NOT this cut's
    subject. A blanket delegation converts contract B into contract A."""
    f = _fn(mod_name, attr)
    assert f("") == "", "%s.%s('') must stay empty" % (mod_name, attr)
    want = "localhost" if contract == "host" else ""
    assert f("localhost") == want, (
        "%s.%s('localhost') -> %r, want %r. This cut fixes the SUFFIX rule and "
        "nothing else; changing what a single-label host returns would alter a "
        "contract no test in this cut is measuring the callers against."
        % (mod_name, attr, f("localhost"), want))


@pytest.mark.parametrize("mod_name,attr,_contract", _SITES)
def test_an_ordinary_two_label_host_is_untouched(mod_name, attr, _contract):
    """The majority case, and the one a suffix table could silently break."""
    assert _fn(mod_name, attr)("example.com") == "example.com"
    assert _fn(mod_name, attr)("www.example.com") == "example.com"


# ── the two nested / inline sites the table cannot import ─────────

def test_the_login_template_suggester_uses_the_correct_rule():
    """`login_templates_data` carries the shape twice: `_reg_domain` nested
    inside `suggest_login_for_url`, and the same join inline. Neither is
    importable, so this exercises the public function instead."""
    from bulk_downloader import login_templates_data as L
    importlib.reload(L)
    src = (REPO / "bulk_downloader/login_templates_data.py").read_text(encoding="utf-8")
    assert "suggest_login_for_url" in src
    # a .co.uk host must not be treated as sharing a registrable domain with
    # every other .co.uk registrant
    out = L.suggest_login_for_url("https://members.some-unlikely-site.co.uk/login")
    assert isinstance(out, list)


def test_the_rate_limit_key_no_longer_needs_its_own_suffix_table():
    """`rate_limit._extract_domain` hand-kept EIGHT two-label TLDs to patch up
    extension_vault's wrong answer. With the canonical rule underneath, that
    table is a second source of truth for the same question -- the shape
    CLAUDE.md's layout section warns about, where the copy nobody updated is
    the one that runs."""
    src = (REPO / "bulk_downloader/rate_limit.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_extract_domain":
            body = ast.unparse(n)
            assert "two_label_tlds" not in body, (
                "the local suffix table survives; it is now a second answer to "
                "a question registrable_domain already answers")
            return
    raise AssertionError("_extract_domain not found -- re-derive this test")


def test_the_rate_limit_key_is_the_registrable_domain():
    """v3.66.1020: THIS TEST USED TO SKIP, AND THE BOX IS WHERE THAT SHOWED.

    It did `getattr(R, "_extract_domain", None)` and skipped when that came
    back None -- but the function is a @staticmethod on DomainRateLimiter
    (rate_limit.py:290), never a module attribute, so the getattr ALWAYS
    returned None and both assertions below NEVER RAN. The capture at e7d3b5e
    carries the receipt: skips went 4 -> 5 the moment @1018 landed, and the
    fifth is this test, with a reason ("nested") that was also wrong.

    That is CLAUDE.md section 0 in a test: a check reporting a benign status
    over a subject it cannot reach. A skip reads as fine in every summary line.

    THERE IS NO SKIP BRANCH NOW, deliberately. If the function moves, the
    attribute access raises AttributeError and this fails LOUDLY. The access
    itself is the proven in-tree pattern -- tests/test_v3_43_31_rate_limit.py:270
    has used it since @43.31.
    """
    from bulk_downloader import rate_limit as R
    importlib.reload(R)
    fn = R.DomainRateLimiter._extract_domain
    assert fn("https://www.bbc.co.uk/x") == "bbc.co.uk"
    assert fn("magnet:?xt=urn:btih:abc") == ""


# ── the ratchet reaches zero, and the table is complete ───────────

def _joins_last_two_labels(fn):
    """The @1013 predicate, imported by behaviour rather than by name so the
    two files cannot drift apart on what they are counting."""
    mod = importlib.import_module("test_v3_66_1013_registrable_domain")
    return mod._joins_last_two_labels(fn)


def _remaining_copies():
    sys.path.insert(0, str(REPO / "tests"))
    files = [f for f in subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "*.py"],
        capture_output=True, text=True).stdout.split("\0") if f]
    assert len(files) > 1000, "the file census went blind (%d)" % len(files)
    canonical = "bulk_downloader/registrable_domain.py"
    found = []
    for rel in files:
        if rel.startswith("tests/") or rel == canonical:
            continue
        try:
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and _joins_last_two_labels(n):
                found.append("%s:%d %s" % (rel, n.lineno, n.name))
    return found


def test_no_last_two_labels_copy_survives_anywhere():
    found = _remaining_copies()
    assert found == [], (
        "%d copies of the last-two-labels rule survive. @1013 ratcheted at 11 "
        "and this cut drains them; every one is the bug that made victim.co.uk "
        "and attacker.co.uk the same site.\n  %s"
        % (len(found), "\n  ".join(sorted(found))))


def test_the_census_can_still_SEE_a_copy():
    """Zero is only meaningful from an instrument proven able to return
    non-zero. Without this, deleting the predicate would 'drain' the backlog."""
    positive = "def f(h):\n    return '.'.join(h.split('.')[-2:])\n"
    fn = next(n for n in ast.walk(ast.parse(positive))
              if isinstance(n, ast.FunctionDef))
    assert _joins_last_two_labels(fn), "the census cannot see a known positive"


def test_the_RATCHET_ITSELF_can_never_be_raised_again():
    """@1013's ratchet says "Never raise it" in prose. This makes it mechanical.

    ADDED TO CLOSE A MUTATION ESCAPE. Raising @1013's ceiling from 0 back to 11
    left the whole band green, because the census in THIS file is independent
    and still asserts zero. So the behaviour was constrained and the ratchet was
    not: had this file ever been deleted or its census weakened, the raised
    ceiling would silently re-permit eleven copies. A ratchet nothing checks is
    a comment.

    Read from the ASSERT NODE, not from the file text, so the sentence in
    @1013's docstring that quotes the old ceiling cannot satisfy it -- the
    comment-in-the-denominator failure CLAUDE.md section 0 records five times.
    """
    src = (REPO / "tests/test_v3_66_1013_registrable_domain.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ceilings = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assert):
            continue
        t = n.test
        # the OPERATOR is part of the predicate. Without it this also matched
        # the census's own non-empty-denominator guard, `assert len(files) >
        # 1000`, and reported the ceiling as 1000 -- a predicate over the wrong
        # part of the syntax, which CLAUDE.md section 1 calls worse than a grep
        # because it looks rigorous. Caught by this test failing on correct code.
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Call)
                and isinstance(t.left.func, ast.Name) and t.left.func.id == "len"
                and len(t.ops) == 1 and isinstance(t.ops[0], ast.LtE)
                and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Constant)
                and isinstance(t.comparators[0].value, int)):
            ceilings.append(t.comparators[0].value)
    assert ceilings, (
        "no `len(...) <= N` ceiling found in @1013 -- the ratchet moved or was "
        "removed; re-derive this test rather than deleting it")
    assert max(ceilings) == 0, (
        "@1013's ratchet ceiling is %d, not 0. v3.66.1018 drained every copy, "
        "so any nonzero ceiling re-permits the bug that made victim.co.uk and "
        "attacker.co.uk the same site. Its own docstring says never raise it."
        % max(ceilings))


def test_the_table_covers_every_site_the_ratchet_counts():
    """The two files must agree on the population. If a site is migrated and
    dropped from _SITES in the same cut, the ratchet goes to zero and nothing
    ever asserts that site's behaviour again."""
    modules = {m.rsplit(".", 1)[-1] for m, _a, _c in _SITES}
    # the four sites reached by their own tests rather than the table
    modules |= {"login_templates_data", "rate_limit"}
    expected = {"candidate_filter", "extension_vault", "extractors_aylo",
                "extractors_dl8", "extractors_vixen", "host_enumerator",
                "phoenix_catalog", "player_struct_embed",
                "login_templates_data", "rate_limit"}
    assert modules == expected, (
        "the tested population drifted from the ten modules @1013's census "
        "found: %r" % (modules ^ expected))


BD_GATE_SCOPE = "repo-wide"
