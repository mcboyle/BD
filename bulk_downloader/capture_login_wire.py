"""Wire a /capture draft login block into the live-login config — v3.66.289.

NON-guard, pure, import-light (no Flask, no I/O, no module-level work).

Background. The /capture SPA login pickers write a DRAFT template login block
under the canonical single-credential-slot schema produced by
``tools/build_template_from_wacz`` :

    {"email": "<credential-field selector>",   # holds a username OR an email
     "password": "<password-field selector>",
     "submit": "<submit-control selector>"}

The LIVE login (``bulk_downloader.login.do_login``) reads an entirely different
set of keys off the *site config*: ``user_field`` / ``pass_field`` /
``submit_btn`` (plus the ``learned.login`` lists populated by the teach flow).
Nothing connected the two, so selectors an operator carefully picked in the
``/capture`` workflow never drove the actual login — the verification login
fell through to the 154-entry fallback list instead.

:func:`apply_draft_login_selectors` is the bridge. It is applied at the point
where a draft becomes operative for login (the ``test_extract`` draft override),
so the OPV verification run logs in via the picked selectors.

PRESERVE-IF-PRESENT. It only fills a config key that is currently empty/blank.
A manually-set value or a teach-learned selector already on the config is never
overwritten — the picked selector is a *fallback seed*, not an override.
"""
from __future__ import annotations

from typing import Any, Dict, List

# draft-login key -> live-login config key. ``email`` is the canonical
# credential slot (username OR email), matching build_template_from_wacz.
_LOGIN_KEY_MAP = (
    ("email", "user_field"),
    ("password", "pass_field"),
    ("submit", "submit_btn"),
)


def _blank(v: Any) -> bool:
    """True when a config value should be treated as empty (missing, None, or
    whitespace-only)."""
    return not (isinstance(v, str) and v.strip())


def apply_draft_login_selectors(cfg: Dict[str, Any],
                                login_block: Any) -> List[str]:
    """Map a draft login block onto ``cfg``'s live-login selector keys.

    Parameters
    ----------
    cfg : dict
        The site config to seed (mutated in place).
    login_block : dict-like
        A draft template's ``login`` block: ``{email?, password?, submit?}``.
        Non-dict input is tolerated (returns ``[]`` and leaves ``cfg`` untouched).

    Returns
    -------
    list[str]
        The config keys that were filled this call (empty when nothing was
        eligible — e.g. all targets already set, or no usable source selectors).
    """
    filled: List[str] = []
    if not isinstance(login_block, dict):
        return filled
    for src, dst in _LOGIN_KEY_MAP:
        sel = login_block.get(src)
        if not (isinstance(sel, str) and sel.strip()):
            continue                      # no usable source selector
        if not _blank(cfg.get(dst)):
            continue                      # preserve operator/teach value
        cfg[dst] = sel.strip()
        filled.append(dst)
    return filled


def revert_seeded_login(cfg: Dict[str, Any], seeded_login: Any) -> List[str]:
    """Undo the login selectors a draft-test override seeded into ``cfg``.

    ``seeded_login`` is the ``{key: value}`` map recorded when the override was
    set (the keys ``apply_draft_login_selectors`` filled). A key is removed only
    when ``cfg``'s current value still equals the seeded value -- a value the
    operator changed since is preserved. Returns the keys actually removed.
    Tolerates a non-dict ``seeded_login`` (returns ``[]``).
    """
    removed: List[str] = []
    if not isinstance(seeded_login, dict):
        return removed
    for k, v in seeded_login.items():
        if cfg.get(k) == v:
            cfg.pop(k, None)
            removed.append(k)
    return removed


__all__ = ["apply_draft_login_selectors", "revert_seeded_login"]
