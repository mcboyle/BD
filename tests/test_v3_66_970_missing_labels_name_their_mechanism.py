"""The two on-screen "missing" figures must say which question they answer.

@970, item 12. Four producers in this tree answer to the word "missing" and
they answer DIFFERENT questions. Two reach an operator:

  * `app_widgets_api._collect_library_data` COUNTS `file_exists=0` on the
    `library` table -- a flag a scanner wrote at some earlier time -- and drives
    a widget on Home and SiteDetail;
  * `library_final.missing_from_disk_scan` walks `history` and STATS each file
    live, driving the Library route's audit line.

They render on disjoint routes, so they cannot appear under one label on one
screen -- measured at v3.66.967, and that is why item 12 is a rename rather than
a product call. But "Missing files" and "missing from disk" read as synonyms to
anyone who sees both across two routes, and nothing said which was cached.

The load-bearing test here is the SINGLE-SOURCE one. `lib_missing_extra` had two
independently-maintained copies of the same string in one module, which is the
shape CLAUDE.md calls a denominator that drifts -- and the copy nobody updates
is the one that ships. A label test alone would pass while the two producers
silently diverged.
"""

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
WIDGETS = REPO / "bulk_downloader" / "app_widgets_api.py"
CATALOG = REPO / "frontend" / "src" / "lib" / "widgetCatalog.ts"
LIBRARY_ROUTE = REPO / "frontend" / "src" / "routes" / "Library.tsx"

_KEY = "lib_missing_extra"
_HELPER = "_missing_extra"


def _extra_write_sites():
    """Every place `lib_missing_extra` is given a value, by AST.

    Both syntactic forms are in the denominator on purpose: one producer builds
    a dict literal and the other assigns into `out[...]`. A predicate covering
    only one of them would report a single write site and certify agreement
    between a set of one.
    """
    tree = ast.parse(WIDGETS.read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == _KEY:
                    sites.append(("dict-literal", k.lineno, v))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == _KEY):
                    sites.append(("subscript", t.lineno, node.value))
    return sites


def test_every_lib_missing_extra_producer_uses_ONE_helper():
    """THE LOAD-BEARING ASSERTION: two hand-maintained copies is the defect."""
    sites = _extra_write_sites()
    assert len(sites) >= 2, (
        "BD-GATE-UNRUNNABLE: found %d write site(s) for %r in %s. This test "
        "exists to prove SEVERAL producers agree; over a set of one it would "
        "pass by having nothing to compare, which is a gate certifying an "
        "empty denominator." % (len(sites), _KEY, WIDGETS.name))
    bad = [(form, line, ast.dump(v)[:80]) for form, line, v in sites
           if not (isinstance(v, ast.Call)
                   and isinstance(v.func, ast.Name)
                   and v.func.id == _HELPER)]
    assert not bad, (
        "%d of %d %r producers do not route through %s(), so the string is "
        "maintained in more than one place and the copy nobody updates is the "
        "one that ships: %r" % (len(bad), len(sites), _KEY, _HELPER, bad))


def test_the_extra_line_carries_the_SCOPE():
    """The same widget renders global on Home and site-scoped on SiteDetail.

    The number alone cannot tell an operator which one is on screen, so the
    sub-line has to say.
    """
    import importlib
    mod = importlib.import_module("bulk_downloader.app_widgets_api")
    helper = getattr(mod, _HELPER, None)
    assert helper is not None, (
        "%s() does not exist, so nothing can carry the scope" % _HELPER)
    scoped = helper(3, "wow")
    globalish = helper(3, None)
    assert "wow" in scoped, (
        "a site-scoped extra line did not name the site: %r" % scoped)
    assert scoped != globalish, (
        "the site-scoped and global forms are identical (%r), so the line "
        "cannot tell an operator which scope produced the number" % scoped)
    # and the zero case must still be scoped -- "all present" is exactly the
    # reading an operator would over-trust if it silently meant one site.
    assert helper(0, "wow") != helper(0, None), (
        "the zero case is unscoped, so 'all present' for ONE site is "
        "indistinguishable from 'all present' everywhere")


def test_the_widget_does_not_claim_a_DISK_check():
    """It is the index's cached flag. Saying "from disk" makes it the other one."""
    src = CATALOG.read_text(encoding="utf-8")
    # Cut on STRUCTURE, not a fixed width: the catalog is a list of sibling
    # object literals, so the entry ends where the next one begins. A
    # src[start:start+N] window silently swallows or truncates the moment
    # anything above it grows, which is the failure mode
    # test_source_windows_do_not_shift ratchets against -- and it caught this
    # test's first draft doing exactly that.
    start = src.index('{ id: "lib_missing"')
    end = src.index('{ id: "', start + 1)
    block = src[start:end]
    low = block.lower()
    assert "from disk" not in low, (
        "the index-flag widget describes itself as a disk check, which is the "
        "OTHER producer's question: %r" % block[:300])
    assert "index" in low, (
        "the widget does not say it is the library INDEX's cached flag, so it "
        "stays confusable with the Library route's live stat: %r" % block[:300])


def test_the_library_route_names_what_it_STATTED():
    """"missing from disk" is meaningless without the directory it walked.

    0 because everything is present and 0 because nothing resolved are the same
    glyph; naming the audited directory is what separates them.
    """
    src = LIBRARY_ROUTE.read_text(encoding="utf-8")
    assert "missing from disk" in src, (
        "the Library route no longer carries the live-stat label at all")
    i = src.index("missing from disk")
    # Structural again: the audit figures live in one <p>, so its closing tag is
    # the boundary. index() raises if the shape changed, which is a loud failure
    # rather than a window that quietly stops covering its subject.
    window = src[i:src.index("</p>", i)]
    assert "auditDir" in window, (
        "the live-stat figure does not name the directory it walked, so an "
        "operator cannot tell 'nothing is missing' from 'nothing resolved': %r"
        % window[:200])
