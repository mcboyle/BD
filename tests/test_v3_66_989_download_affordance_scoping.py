"""template_normalize: a download AFFORDANCE counts as scoping, not just a modal.

@989, closing finding B. `_map_selectors` keeps a row selector only when
`_is_modal_scoped` matches -- `[role=dialog]`, `aria-modal`, `.modal`,
`.ant-modal`, `.MuiDialog`, `.drawer`, `.popover`, `.dialog`. Everything else is
dropped with a warning.

THE RULE IS NOT WRONG AND MUST NOT BE DELETED. An unscoped row selector matching
every anchor on a page is exactly how a honeypot gets into a template, and the
measured corpus shows the rule earning its keep: of 143 dropped row selectors,
most are `a:nth-child(31)`, `li.theo-menu-item`, `span.screen-reader-text`,
`a.nav__link`, `span.title` -- junk the row heuristic scraped off the page.

BUT 44 OF THOSE 143 ARE REAL DOWNLOAD CONTROLS, and they are the operator's own
member sites. Measured on the 742-capture corpus at v3.66.988:

    auth.wowgirls.com          a.ct_dl_button                        30/39
    vip4k.com                  a.download__item                       7/8
    ultrafilms.com             div.clickable.download-button...       7/21
    members.nubiles-porn.com   a.dropdown-downloads-link              5/9
    members.dfxtra.com         a.Link...VideoJSPlayer-DownloadOption   5/9
    members.teenmegaworld.net  a.d-flex.download-element              4/6

`a.ct_dl_button[data-framerate]` is the selector `build_template_from_wacz`'s own
comment names as the wowgirls inline download anchor. It is corroborated in 30 of
39 captures and discarded every time.

WHY IT MATTERS MORE THAN THE VERDICT SUGGESTS. Those sites are already GREEN --
on their trigger. The rows being discarded are the per-RESOLUTION links. The
trigger opens the download UI; the rows are how you pick 1080p over 4K. So the
operator's fourth step, "select the highest resolution", is the step this rule
silently removes, on sites the report calls green.

THE WIDENING IS SEMANTIC, NOT STRUCTURAL. "Any container-scoped selector" would
re-admit `li.swiper-slide` and `a.nav__link`. What separates the two populations
in the measured data is that a real control is a CLICK TARGET carrying a
DOWNLOAD token. Both halves are required, and the tests below pin both
directions -- a `span` bearing the word download is a label, not a control, and
an anchor with no download token is a nav link.

THE HONEYPOT TRADE IS REAL AND IS STATED. A decoy named `a.download-link` is now
admissible where the modal rule refused it. That resistance is not recovered
here; it is recovered at the corpus level, where a decoy that varies per
page-load reads support 1 of N against the real control's N of N
(verified at v3.66.987). Admitted-by-affordance rows are therefore RECORDED in
the draft's warnings, so a reviewer can see which rows were not modal-scoped.
"""

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bulk_downloader.template_normalize import (      # noqa: E402
    _is_download_affordance, normalize_draft)


# Measured on the operator's corpus. Left as data rather than prose so the
# discriminator is checked against the real population, not against an example.
REAL_CONTROLS = [
    "a.ct_dl_button",
    "a.ct_dl_button[data-framerate]",
    "a.download__item",
    "a.dropdown-downloads-link",
    "div.clickable.download-button.video-download-button",
    "div.cuts-button.cuts-button-download-selected",
    "a.d-flex.download-element",
    "a.Link.Link.VideoJSPlayer-DownloadOption-Link",
    "a.video-quality-dropdown-item",
]

JUNK = [
    "a:nth-child(31)",
    "a.nav__link",
    "a.mnav__menu__link",
    "li.theo-menu-item.vjs-menu-item",
    "span.title",
    "span.screen-reader-text",
    "span.tw-pointer-events-none.tw-text-body-16",
    "span.video-hd-mark",
    "span.ca-applied",
    "div.tw-overflow-hidden.tw-text-body-14",
    # Labels that CARRY a download word but are not click targets. These are the
    # ones a token-only rule would wrongly admit.
    "span.download-label",
    "span.download__quality",
    "div.edge-download-item-dimensions",
]


def test_the_REAL_download_controls_are_recognised():
    for sel in REAL_CONTROLS:
        assert _is_download_affordance(sel), (
            "a measured download control was not recognised: %r" % sel)


def test_the_JUNK_the_modal_rule_correctly_rejected_stays_rejected():
    """The over-sensitivity direction, and the whole reason this is a semantic
    widening rather than 'container scoping counts'. A rule that admitted these
    would put a nav link in a template."""
    for sel in JUNK:
        assert not _is_download_affordance(sel), (
            "a rule meant to admit download controls also admits page junk, "
            "which is how a honeypot gets in: %r" % sel)


def _draft(rows):
    return {"schema_version": "1", "host": "h.example.org",
            "match": {"hosts": ["h.example.org"]},
            "selectors": {"download": {"trigger": "button.dl",
                                       "row_selectors": list(rows)},
                          "login": {"email": "#e", "password": "#p",
                                    "submit": "#s"}},
            "resolution_priority": [1080],
            "network_patterns": ["https://cdn.example.org/{resolution}.mp4"],
            "source": {"capture_file": "c.wacz"}}


def test_an_INLINE_download_panel_survives_normalize():
    """The wowgirls / vip4k / ultrafilms shape, end to end."""
    nd = normalize_draft(_draft(["a.ct_dl_button[data-framerate]"]))
    assert nd["selectors"]["download"]["row_selectors"] == \
        ["a.ct_dl_button[data-framerate]"], (
        "the inline download anchor was still discarded: %r"
        % nd["selectors"]["download"].get("row_selectors"))


def test_a_MODAL_row_still_survives_and_page_junk_still_does_not():
    """Both directions in one draft, because a widening that lost the modal case
    or kept the junk would pass a one-sided test."""
    nd = normalize_draft(_draft(["div.modal a.dl", "a.nav__link",
                                 "span.screen-reader-text"]))
    assert nd["selectors"]["download"]["row_selectors"] == ["div.modal a.dl"]
    dropped = [w for w in nd["warnings"] if "dropped row selector" in w]
    assert len(dropped) == 2, (
        "page junk was admitted alongside the modal row: %r" % nd["warnings"])


def test_a_row_kept_by_AFFORDANCE_is_RECORDED_as_not_modal_scoped():
    """The audit trail that pays for the honeypot resistance being traded away.
    A reviewer must be able to see which rows were admitted on their name rather
    than on their scope."""
    nd = normalize_draft(_draft(["a.ct_dl_button", "div.modal a.dl"]))
    noted = [w for w in nd["warnings"] if "download affordance" in w]
    assert any("a.ct_dl_button" in w for w in noted), (
        "a row admitted by affordance was not recorded: %r" % nd["warnings"])
    assert not any("div.modal a.dl" in w for w in noted), (
        "a genuinely modal-scoped row was labelled as an affordance admission")


def test_a_LINT_BLOCKED_generic_is_still_refused_after_the_widening():
    """The widening must not have opened a path around the linter.

    A NOTE ON WHAT THIS DOES AND DOES NOT PROVE. The code puts the lint check
    FIRST, ahead of both scope tests, so a blocked selector can never be
    admitted on its name. That ordering is currently DEFENSIVE rather than
    load-bearing: measured, the two predicates do not overlap on any input --
    the linter blocks bare generics (`*`, `a`, `body a`, `a[href]`, `button`)
    and the affordance rule requires a download token, which makes a selector
    non-generic. So no fixture can distinguish lint-first from lint-last today,
    and this test does not claim to. It pins the reachable half: the widening
    did not start admitting generics.

    Stated rather than dressed up as a passing ordering test, because a test
    that cannot fail for the reason it names is the thing this repo keeps
    getting caught by."""
    nd = normalize_draft(_draft(["*", "body a", "a[href]"]))
    assert not nd["selectors"].get("download", {}).get("row_selectors"), (
        "a lint-blocked generic reached the template: %r"
        % nd["selectors"].get("download"))
    assert not _is_download_affordance("*") and not _is_download_affordance("body a")
