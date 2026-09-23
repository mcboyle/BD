"""Row 1031 -- the accessible confirmation checkbox selector.

Three modules used to roll their own checkbox locator (``interstitial``,
``runner_challenge``, ``login_impl.submit``) and all three wrote CSS over a native
``<input type="checkbox">``. A page that renders its confirmation box the accessible way --
``<span role="checkbox" aria-checked="true">``, or an ``role="switch"`` toggle -- is invisible to
every one of them, because an ARIA widget is not an ``<input>`` and has no ``:checked`` state.

Where that matters most is money. ``interstitial._prechecked_billing_consent`` (row 762) refuses a
gate click when recurring-charge language sits beside a CHECKED consent box, and fails closed with
``unknown`` when the box cannot be measured -- unavailable evidence is never permission to spend.
An ARIA consent box defeated that by a worse route than ``unknown``: the native selector matched
nothing, so the page was reported CLEAR, as though the box had been read and found empty.

So the selector here is not a convenience. :data:`CHECKED_CONFIRMATION_SELECTOR` is the set of
shapes that mean "this confirmation is already ticked"; ``interstitial`` counts consent boxes with it.
"""
from __future__ import annotations

#: The shapes that mean "already ticked", each a portable CSS selector so they can be joined into
#: one group and handed to any engine's ``locator``/``querySelectorAll``. ``role="switch"`` and
#: ``role="menuitemcheckbox"`` are here because ARIA defines both as checkbox-family widgets
#: carrying ``aria-checked``: a consent toggle drawn as a switch is the same hazard.
CHECKED_CONFIRMATION_SELECTORS = (
    "input[type='checkbox']:checked",
    "[role='checkbox'][aria-checked='true']",
    "[role='switch'][aria-checked='true']",
    "[role='menuitemcheckbox'][aria-checked='true']",
)

#: The same set as one CSS group, which is what a caller actually passes to ``page.locator``.
CHECKED_CONFIRMATION_SELECTOR = ", ".join(CHECKED_CONFIRMATION_SELECTORS)
