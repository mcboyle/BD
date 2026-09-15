"""login_impl._common -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import time


def _selector_text(raw):
    """Return the CSS selector carried by a plain or structured chain step."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("selector") or "").strip()
    return ""


def _first_positive_size_match(page, selector):
    """Return the first visible, positive-size match for ``selector``.

    Login pages often keep desktop and mobile controls in the DOM together.
    Resolving ``.first`` can therefore select a hidden mobile control even
    though a later desktop match is clickable.  Presence alone is also not a
    useful signal for modal login fields: a complete ``display:none`` form is
    present but has no usable box.
    """
    try:
        matches = page.locator(selector)
        count = matches.count()
    except Exception:
        return None
    for index in range(count):
        try:
            match = matches.nth(index)
            if not match.is_visible():
                continue
            box = match.bounding_box()
            if (box and box.get("width", 0) > 0
                    and box.get("height", 0) > 0):
                return match
        except Exception:
            continue
    return None


def _fire_login_trigger_if_needed(page, login_trigger, username_selectors):
    """Reveal a configured modal login form when no username field is usable.

    Returns ``(needed, fired, detail)``.  Empty/missing/non-string trigger
    values return immediately without touching the page, preserving the
    historical path for sites that do not opt in.
    """
    if not isinstance(login_trigger, str):
        return False, False, ""
    trigger = login_trigger.strip()
    if not trigger:
        return False, False, ""

    for raw_selector in username_selectors:
        selector = _selector_text(raw_selector)
        if selector and _first_positive_size_match(page, selector) is not None:
            return False, False, "username field is already visible"

    visible_trigger = _first_positive_size_match(page, trigger)
    if visible_trigger is None:
        return True, False, f"configured trigger [{trigger}] has no visible match"
    try:
        visible_trigger.click(timeout=2500)
        return True, True, f"clicked visible trigger [{trigger}]"
    except Exception as exc:
        return True, False, (
            f"could not click visible trigger [{trigger}]: {str(exc)[:120]}"
        )


def _all_visible(page,selectors):
    """Return the first visible match from the candidate list. Tries each
    selector with a short wait_for; fast-fails so we can move on. None
    returned if nothing matches."""
    for sel in selectors:
        if not sel: continue
        try:
            loc=page.locator(sel).first
            loc.wait_for(state="visible",timeout=2500)
            return loc,sel
        except Exception: continue
    return None,None


_MATCH_WAIT_MS = 2500
# Row 722: how many matches of one click selector are walked for a visible one.
_CLICK_WALK_LIMIT = 6


def _wait_attached(loc):
    """Wait for ATTACHMENT. The only thing attached may be the decoy, and
    we still need it in order to walk past it to the real field behind."""
    loc.wait_for(state="attached", timeout=_MATCH_WAIT_MS)


def _wait_visible(loc):
    """Wait for VISIBILITY, which is what a field must be before we fill
    it -- but never what a whole selector's match set is judged on."""
    loc.wait_for(state="visible", timeout=_MATCH_WAIT_MS)


def _signal_tabindex(loc):
    """For a form input ``tabindex="-1"`` is a STANDALONE trap signal."""
    if loc.get_attribute("tabindex") == "-1":
        return "tabindex=-1"
    return ""


def _signal_aria_hidden(loc):
    """A field announced as hidden to assistive tech is not for a human."""
    if (loc.get_attribute("aria-hidden") or "").lower() == "true":
        return 'aria-hidden="true"'
    return ""


def _signal_css_hidden(loc):
    """The SHIPPED style vocabulary, consulted rather than restated."""
    from ..deep_detect.login import HONEYPOT_CSS_HIDDEN
    # Same normalisation as deep_detect.login._is_visible_input: the
    # vocabulary is stored whitespace-stripped and lowercase.
    style = (loc.get_attribute("style") or "").lower().replace(" ", "")
    for pat in HONEYPOT_CSS_HIDDEN:
        if pat in style:
            return f"style~{pat}"
    return ""


def _signal_hidden_attr(loc):
    """The ``hidden`` attribute: no shipped equivalent, browser-only fact."""
    if loc.get_attribute("hidden") is not None:
        return "hidden-attr"
    return ""


def _signal_type_hidden(loc):
    """``type=hidden``: likewise browser-only."""
    if (loc.get_attribute("type") or "").lower() == "hidden":
        return "type=hidden"
    return ""


def _signal_offscreen_box(loc):
    """A negative bounding box -- the ``left:-9999px`` decoy whose inline
    style was moved to a stylesheet, so only layout can still see it."""
    box = loc.bounding_box()
    if box and (box.get("x", 0) < 0 or box.get("y", 0) < 0):
        return "off-screen box"
    return ""


# Row 770: THE SIGNAL SET IS A MEMBERSHIP, not a run of inline branches.
# Membership order is the reported-reason precedence and is part of the
# contract the row's tests assert; adding a signal is adding one member.
# Each member returns the REASON STRING it fires on, or "" for "not mine",
# and each is dispatched under its own fail-open guard below, so one
# uninspectable signal cannot cost the caller the other five.
_HONEYPOT_SIGNALS = (
    _signal_tabindex,
    _signal_aria_hidden,
    _signal_css_hidden,
    _signal_hidden_attr,
    _signal_type_hidden,
    _signal_offscreen_box,
)


def _is_honeypot_field(loc):
    """Return ``(is_decoy, reason)`` for a Playwright input locator.

    Row 770: real sites plant a spare ``input[type=text]`` ahead of the real
    field in DOM order specifically to catch scripts that grab the first
    match instead of the field a human would actually see and use.

    This is the PLAYWRIGHT-side counterpart of the SHIPPED input rule in
    ``deep_detect.login._is_visible_input`` — it reuses that module's
    ``HONEYPOT_CSS_HIDDEN`` vocabulary rather than restating one, so an
    addition there propagates here. The signal set is deliberately the
    input-side set: for form inputs ``tabindex="-1"`` is a STANDALONE
    trigger (``dom_honeypot``'s module docstring pins this, and contrasts
    it with links, where it is only contributing).

    What is deliberately NOT copied from the shipped rule is
    ``HONEYPOT_NAMES``: ``_input_is_honeypot`` fires a suspicious name only
    on a field that is ALREADY hidden, and every such field is caught here
    by a hiding signal. Firing on the name alone would reject a legitimate
    visible field (a real "company" or "website" input on a signup form).

    Two signals have no shipped equivalent because they are layout facts
    only a live browser can see: an off-screen bounding box (negative x/y —
    the ``left:-9999px`` decoy whose inline style was moved to a
    stylesheet), and the ``hidden`` attribute / ``type=hidden``.

    THE EVASION SURFACE — what this ENUMERATION DOES NOT CATCH. The six
    signals are an enumeration of spellings, not a visibility oracle, so a
    decoy spelled outside them is FILLED, silently, with ``skipped`` empty
    and no diagnostic. Measured against the shipped code, not assumed;
    ``tests/test_login_honeypot.py`` ships a fixture per case asserting the
    decoy IS filled today, so widening a signal breaks that fixture and the
    enumeration's edge cannot drift unrecorded:

    * HIDING BORNE BY A CLASS OR STYLESHEET, with no inline ``style``
      attribute. ``_signal_css_hidden`` reads ``get_attribute("style")`` —
      the inline attribute only — so ``opacity:0``, ``height:0`` or
      ``clip-path`` applied through a class leaves a positive, non-negative
      box and fires nothing. This is the widest gap of the three: the
      shipped rule at ``deep_detect.login`` also reads class and id hints,
      and this Playwright-side rule does not.
    * A ZERO-SIZE BOX AT NON-NEGATIVE COORDINATES — ``0x0`` at the origin,
      or ``1x1`` at positive x/y. ``_signal_offscreen_box`` tests the SIGN
      of x/y, never the extent, so only a decoy pushed to negative
      coordinates is caught.
    * A ``tabindex`` OTHER THAN ``"-1"`` — ``"-2"``, or any other negative
      value. The comparison is against the literal ``"-1"`` that the sites
      in the fixtures actually ship, not against "negative".

    Closing any of these is a row of its own with its own controls: each
    widening trades a caught decoy against the risk of rejecting a real
    field, which is the failure mode the fail-open below exists for.

    Fail-open on any introspection error: a field we cannot inspect must
    not be treated as a trap, or a real field would go unfilled.
    """
    for signal in _HONEYPOT_SIGNALS:
        try:
            why = signal(loc)
        except Exception:
            continue
        if why:
            return True, why
    return False, ""


def _try_fill(page,selectors,value,what):
    """Walk the candidate list; fill the first visible, non-honeypot
    element, return (True, used_selector). On total failure return
    (False, summary_error) that lists which selectors were tried.

    Row 770: a selector can match MORE than one element in DOM order — a
    decoy field planted ahead of the real one — so each selector's full
    match set is walked (not just `.first`), skipping any match that
    trips ``_is_honeypot_field``.

    Phase 15.5 — Human-like typing: instead of `loc.fill(value)` (which
    sets the value via DOM in one shot — no keyboard events, trivially
    detectable), we focus the field then call `page.keyboard.type` with
    a randomized 50-150ms delay between keystrokes. Real browsers emit
    keydown/keypress/input/keyup for each character; bots that DOM-set
    don't. Many enterprise WAFs key on this gap exclusively."""
    import random
    tried=[]
    skipped=[]
    for sel in selectors:
        if not sel: continue
        tried.append(sel)
        try:
            matches=page.locator(sel)
            # Row 770: the pre-fix code resolved `.first` and then waited on
            # IT for 2500ms, so a form that had not rendered yet still got
            # filled. `count()` resolves IMMEDIATELY and would return 0 for
            # exactly that page, silently skipping the selector -- so the
            # wait is kept, on ATTACHMENT rather than visibility: the only
            # thing attached may be the decoy, and we still need to see it
            # in order to walk past it to the real field behind it.
            _wait_attached(matches.first)
            count=matches.count()
        except Exception: continue
        for idx in range(count):
            try:
                loc=matches.nth(idx)
                decoy,why=_is_honeypot_field(loc)
                if decoy:
                    skipped.append(f"{sel}[{idx}]:{why}")
                    continue
                _wait_visible(loc)
                # Clear any existing value, then click to focus, then type.
                # We use loc.fill('') for the clear (DOM-set is fine for blanking)
                # and only switch to keyboard.type for the new value.
                try: loc.fill('')
                except Exception: pass
                try: loc.click(timeout=1500)
                except Exception:
                    # Fallback: just call fill if click is intercepted (e.g.
                    # sneaky overlay). Less stealthy but the login still works.
                    loc.fill(value)
                    return True,sel
                # Type with per-character delay drawn from a uniform distribution.
                # Real human typing has ~80-200ms gaps; we land in the middle of
                # that range. v3.65.2: keyboard.type's `delay` parameter is sampled
                # ONCE per call and reused between every keystroke — so a single
                # call with delay=random.uniform(50,150) produces a perfectly
                # periodic signal (e.g. exactly 87ms between every char), which
                # a WAF looking at keystroke variance scores as MORE bot-like
                # than no delay at all. Loop one char at a time, fresh sample
                # each iteration, so the inter-keystroke gaps are actually
                # non-uniform across the value.
                for ch in value:
                    page.keyboard.type(ch, delay=random.uniform(50, 150))
                return True,sel
            except Exception: continue
    # Row 770: "nothing matched" and "every match was a decoy" are opposite
    # situations -- the first wants a better selector list, the second says
    # the filter is doing its job (or is over-firing) -- and the pre-fix
    # message collapsed them into one string. Name the decoys and why.
    if skipped:
        return False,(f"could not fill {what}; tried {len(tried)} selectors, "
                      f"skipped {len(skipped)} honeypot field(s): "
                      f"{', '.join(skipped[:5])}")
    return False,f"could not fill {what}; tried {len(tried)} selectors"


def _try_click(page,selectors,what):
    """Same pattern as _try_fill but for clicks. Force=True is used as a
    last-resort attempt because some sites have invisible overlays that
    intercept clicks; force=True ignores actionability checks.

    Per-selector timeout is intentionally short (400ms) so that walking
    a 50+ selector list in 30s, not 5+ minutes. The selectors that DO
    match return immediately; the ones that don't fail fast.

    Phase 15.6 — Human mouse motion: before clicking we move the cursor
    along a bezier-ish path to the target instead of teleporting it. Real
    users have curved trajectories with overshoot and slight wobble; bots
    that call element.click() never move the mouse at all. CF Bot
    Management scores this heavily."""
    tried=[]
    for sel in selectors:
        if not sel: continue
        tried.append(sel)
        # Row 722 (kink.com): a selector's FIRST match can be the button of a
        # hidden duplicate form (a display:none login modal ahead of the page
        # form in DOM order). `.first` never becomes visible, so the whole
        # selector used to be skipped and the visible button behind it was
        # never clicked. Walk the match set, as _try_fill does, and click the
        # first match that is visible. Bounded so a broad selector cannot
        # spend 400ms on every button of a page.
        matches=page.locator(sel)
        try: count=max(1,min(matches.count(),_CLICK_WALK_LIMIT))
        except Exception: count=1
        for idx in range(count):
            try:
                loc=matches.first if idx==0 else matches.nth(idx)
                loc.wait_for(state="visible",timeout=400)
                try: _human_move_to(page, loc)
                except Exception: pass
                loc.click(timeout=2000)
                return True,sel
            except Exception: continue
    # Force-click fallback — same selectors but with force=True
    for sel in selectors:
        if not sel: continue
        try:
            loc=page.locator(sel).first
            loc.wait_for(state="attached",timeout=300)
            try: _human_move_to(page, loc)
            except Exception: pass
            loc.click(force=True,timeout=2000)
            return True,sel+" (forced)"
        except Exception: continue
    return False,f"could not click {what}; tried {len(tried)} selectors"


def _human_move_to(page, locator):
    """Phase 15.6: move the mouse to the locator's center along a curved
    path with slight wobble + a final ~5px overshoot-and-correct.

    The curve is approximated as 12-18 short steps rather than a true
    bezier evaluation — perceptually identical to bot detectors and 10x
    cheaper. Each step has a 5-15ms gap so the mouse appears to traverse
    the screen instead of teleporting. Total duration ~150-300ms which
    matches the timing of a relaxed pointer move.

    If the locator can't be measured (e.g. detached), this silently
    returns without moving — caller's click() will still succeed via
    Playwright's own pre-click pointer move."""
    import math, random as _rnd
    box = locator.bounding_box()
    if not box: return
    target_x = box["x"] + box["width"] / 2 + _rnd.uniform(-3, 3)
    target_y = box["y"] + box["height"] / 2 + _rnd.uniform(-2, 2)
    # Start position: jitter a bit from where Playwright thinks the mouse
    # is. We don't have a great way to read the current position, so we
    # synthesize a plausible "previous" location offset from the target.
    start_x = target_x + _rnd.uniform(-200, 200)
    start_y = target_y + _rnd.uniform(-150, 150)
    steps = _rnd.randint(12, 18)
    # Quadratic bezier with a single control point offset perpendicularly
    # to the direct line. This produces the slight curve human pointers
    # naturally have.
    dx, dy = target_x - start_x, target_y - start_y
    length = math.hypot(dx, dy) or 1
    # Perpendicular vector, scaled by 8-25% of total distance
    perp_x = -dy / length * length * _rnd.uniform(0.08, 0.25)
    perp_y =  dx / length * length * _rnd.uniform(0.08, 0.25)
    # Random sign so the curve goes either side of the line
    if _rnd.random() < 0.5:
        perp_x, perp_y = -perp_x, -perp_y
    ctrl_x = (start_x + target_x) / 2 + perp_x
    ctrl_y = (start_y + target_y) / 2 + perp_y
    for i in range(1, steps + 1):
        t = i / steps
        # Quadratic bezier at parameter t
        bx = (1-t)*(1-t)*start_x + 2*(1-t)*t*ctrl_x + t*t*target_x
        by = (1-t)*(1-t)*start_y + 2*(1-t)*t*ctrl_y + t*t*target_y
        # Tiny per-step wobble
        bx += _rnd.uniform(-1, 1)
        by += _rnd.uniform(-1, 1)
        try: page.mouse.move(bx, by)
        except Exception: return
        time.sleep(_rnd.uniform(0.005, 0.018))
    # Slight overshoot then correct — distinctive human signature
    try:
        page.mouse.move(target_x + _rnd.uniform(2, 6), target_y + _rnd.uniform(2, 6))
        time.sleep(_rnd.uniform(0.02, 0.05))
        page.mouse.move(target_x, target_y)
    except Exception: pass


_LOGIN_CSS_SAFE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _css_escape_for_id(s):
    if not s:
        return ""
    if _LOGIN_CSS_SAFE.match(s):
        return s
    import re as _re
    return _re.sub(r"([^A-Za-z0-9_-])", r"\\\1", s)


def _ms_since(t):
    import time
    return int((time.time() - t) * 1000)
