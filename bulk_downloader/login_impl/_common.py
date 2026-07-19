"""login_impl._common -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import time


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


def _try_fill(page,selectors,value,what):
    """Walk the candidate list; fill the first visible element, return
    (True, used_selector). On total failure return (False, summary_error)
    that lists which selectors were tried.

    Phase 15.5 — Human-like typing: instead of `loc.fill(value)` (which
    sets the value via DOM in one shot — no keyboard events, trivially
    detectable), we focus the field then call `page.keyboard.type` with
    a randomized 50-150ms delay between keystrokes. Real browsers emit
    keydown/keypress/input/keyup for each character; bots that DOM-set
    don't. Many enterprise WAFs key on this gap exclusively."""
    import random
    tried=[]
    for sel in selectors:
        if not sel: continue
        tried.append(sel)
        try:
            loc=page.locator(sel).first
            loc.wait_for(state="visible",timeout=2500)
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
        try:
            loc=page.locator(sel).first
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
