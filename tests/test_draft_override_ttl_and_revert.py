"""#3 (Addendum A2/A3): the persisted draft-test override must expire, and an
explicit clear must revert exactly the login selectors it seeded.

A2 -- ``draft_test_override`` rides _save_sites_config and persists across
restarts; it is the only path by which an UNREVIEWED draft drives real
downloads. Past ``_DRAFT_OVERRIDE_TTL_SECONDS`` it must be treated as inert so a
forgotten override stops driving the site.

A3 -- setting the override seeds login selectors into the config
(preserve-if-present); clearing it must remove exactly those (and only where
still unchanged), not leave the draft's selectors permanently in the live config.
"""

from __future__ import annotations

import time

from bulk_downloader.runner_teach import (
    TeachMixin,
    _draft_override_is_fresh,
    _DRAFT_OVERRIDE_TTL_SECONDS,
)
from bulk_downloader.capture_login_wire import (
    apply_draft_login_selectors,
    revert_seeded_login,
)


class _Stub:
    """Minimal stand-in carrying just the ``config`` the methods read."""

    def __init__(self, config):
        self.config = config


def _override(set_at):
    return {"draft_test_override": {"template": {"host": "x", "selectors": {}},
                                    "set_at": set_at}}


def test_fresh_override_is_used():
    s = _Stub(_override(int(time.time())))
    assert TeachMixin._draft_override_template(s) == {"host": "x", "selectors": {}}


def test_expired_override_is_inert():
    old = int(time.time()) - _DRAFT_OVERRIDE_TTL_SECONDS - 60
    s = _Stub(_override(old))
    # Pristine returned the template regardless of age; the TTL makes it None.
    assert TeachMixin._draft_override_template(s) is None
    # And an expired override must not suppress persistence either.
    assert TeachMixin._override_suppresses_persist(s) is False


def test_missing_set_at_fails_open_to_fresh():
    s = _Stub({"draft_test_override": {"template": {"host": "x"}}})  # no set_at
    assert TeachMixin._draft_override_template(s) == {"host": "x"}


def test_freshness_helper_boundaries():
    now = 1_000_000
    assert _draft_override_is_fresh({"template": {"a": 1}, "set_at": now}, now=now)
    assert _draft_override_is_fresh(
        {"template": {"a": 1}, "set_at": now - _DRAFT_OVERRIDE_TTL_SECONDS}, now=now)
    assert not _draft_override_is_fresh(
        {"template": {"a": 1}, "set_at": now - _DRAFT_OVERRIDE_TTL_SECONDS - 1},
        now=now)
    assert not _draft_override_is_fresh({"template": {}}, now=now)  # empty template
    assert not _draft_override_is_fresh(None, now=now)


def test_clear_reverts_seeded_login_only_if_unchanged():
    # Set: seed login selectors into a config that had none.
    cfg = {}
    login = {"email": "#user", "password": "#pass", "submit": "#go"}
    seeded_keys = apply_draft_login_selectors(cfg, login)
    assert seeded_keys, "precondition: selectors were seeded"
    seeded_map = {k: cfg[k] for k in seeded_keys}

    # Operator manually changed ONE of the seeded selectors after the fact.
    changed_key = seeded_keys[0]
    cfg[changed_key] = "#operator-changed"

    removed = revert_seeded_login(cfg, seeded_map)

    # The manually-changed key is preserved; the untouched ones are reverted.
    assert changed_key not in removed
    assert cfg[changed_key] == "#operator-changed"
    for k in seeded_keys[1:]:
        assert k not in cfg
        assert k in removed


def test_revert_tolerates_non_dict():
    assert revert_seeded_login({}, None) == []
    assert revert_seeded_login({}, "nope") == []
