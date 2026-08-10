"""@1002 (item C). `text=/Download/i` could go green on a HEADING.

`tools/build_template_from_wacz.py::_html_selectors` emitted the download
button hint from `elif _has(r'>\\s*Download\\s*<', html)`. That pattern matches
ANY element whose text is exactly "Download" -- `<h3>Download</h3>` included --
and the emitted hint `text=/Download/i` is UNSCOPED, so at runtime it resolves
to the heading even when a real button exists on the page. Measured on a VIP4K
reconstruction: `promotion_ready` True on a trigger that clicks a title.

BOTH HALVES ARE NEEDED and this file pins both:

  1. emission is gated on an INTERACTIVE element carrying the text, so a
     heading emits nothing;
  2. the hint is SCOPED (`a:has-text(...)` / `button:has-text(...)`), so even
     when a real control exists the selector cannot drift onto a heading that
     happens to share the word.

Fixing only (1) still leaves an unscoped selector that a later heading can
capture; fixing only (2) still emits a hint for a page with no control at all.

THE COST, STATED HONESTLY. A site whose download control is a `<div>`/`<span>`
with a JS click handler now emits no hint and reads not_green. That is a real
loss. It is the right trade because the alternative is a FALSE GREEN -- a
template that promotes on a selector which clicks a title -- and a false green
sends a worker at a page that will never download.
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "build_template_from_wacz.py"


@pytest.fixture(scope="module")
def mod():
    loader = SourceFileLoader("bd_build_template_c", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _dl(mod, html):
    return (mod._html_selectors(html) or {}).get("download", {}).get("button_hint")


# ---------------------------------------------------------------------------
# The defect: a non-interactive element must not produce a hint.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("html", [
    "<h3>Download</h3>",
    "<h1> Download </h1>",
    "<div class='section-title'>Download</div>",
    "<span>Download</span>",
    "<p>Download</p>",
    "<td>Download</td>",
    "<label>Download</label>",
])
def test_a_non_interactive_element_emits_NO_download_hint(mod, html):
    """RED before this cut: every one of these produced `text=/Download/i`,
    which at runtime clicks the heading."""
    assert _dl(mod, html) is None, (
        "a non-interactive element still emits a download hint: %r" % html)


# ---------------------------------------------------------------------------
# The other direction. A test that only proves the tool emits LESS is not a
# test -- it passes trivially for a tool that emits nothing at all.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("html,expect_tag", [
    ('<a href="/dl/1">Download</a>', "a"),
    ('<a href="/dl/1"> Download </a>', "a"),
    ('<a href="/dl/1"><span>Download</span></a>', "a"),
    ('<button type="button">Download</button>', "button"),
    ('<button><i class="ico"></i> Download</button>', "button"),
])
def test_an_interactive_element_still_emits_a_hint(mod, html, expect_tag):
    got = _dl(mod, html)
    assert got, "a real %s control emitted no hint: %r" % (expect_tag, html)
    assert expect_tag in got, "hint does not name the matched element: %r" % got


def test_the_hint_is_SCOPED_never_a_bare_text_selector(mod):
    """The second half. An unscoped `text=/Download/i` resolves to whatever
    carries the word -- gating emission does not stop it drifting onto a
    heading that appears later on the same page."""
    for html in ('<a href="/dl">Download</a>',
                 "<button>Download</button>",
                 '<h3>Download</h3><a href="/dl">Download</a>'):
        got = _dl(mod, html)
        assert got, html
        assert not got.startswith("text="), (
            "hint is unscoped and can resolve to any element: %r" % got)
        assert ":has-text(" in got, "hint is not text-scoped to a tag: %r" % got


def test_a_heading_ALONGSIDE_a_real_control_still_emits_the_control(mod):
    """The realistic page, and the one the VIP4K reconstruction actually had:
    a section heading AND a real button. The hint must describe the button."""
    html = '<h2>Download</h2><div class="row"><button>Download</button></div>'
    got = _dl(mod, html)
    assert got and "button" in got, got
    assert not got.startswith("text="), got


# ---------------------------------------------------------------------------
# The higher-priority branches are untouched -- this cut narrows ONE elif.
# ---------------------------------------------------------------------------

def test_aria_label_and_title_branches_are_unchanged(mod):
    assert _dl(mod, '<a aria-label="Download video" href="/x">x</a>') == \
        '[aria-label*="Download" i]'
    assert _dl(mod, '<a title="Download this" href="/x">x</a>') == \
        '[title*="Download" i]'


def test_a_page_with_no_download_affordance_emits_nothing(mod):
    assert _dl(mod, "<div>Watch now</div><h3>Comments</h3>") is None


def test_the_emitted_hint_is_a_form_the_selector_linter_accepts(mod):
    """The register measured `a:has-text("Download")`, `button:has-text(...)`
    and the comma form as accepted for both the trigger and row roles -- so the
    scoped form was available. Pin that the tool emits one of those, not some
    third spelling the linter would reject at promotion time."""
    accepted = {
        'a:has-text("Download")',
        'button:has-text("Download")',
        'a:has-text("Download"), button:has-text("Download")',
    }
    for html in ('<a href="/d">Download</a>', "<button>Download</button>",
                 '<a href="/d">Download</a><button>Download</button>'):
        got = _dl(mod, html)
        assert got in accepted, "unexpected hint spelling %r for %r" % (got, html)
