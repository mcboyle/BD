"""Tests for NEW-1 — login-flow / macro plaintext-password elimination.

Approach (c): the recorder NEVER stores a password. At record time a
password-field 'type' action's text is replaced with VAULT_MARKER; at
replay the real password is resolved from the vault keyed by site_id, on
a copy, so the macro file never contains the secret in any form.

Covers:
  * record_macro scrubs password actions (by precise selector and by an
    explicit secret flag), is idempotent, and does NOT false-positive on
    non-password fields (which would itself leak the credential);
  * replay substitution on both executors (macro_recorder.replay_macro
    and macro_replay.replay_on_page);
  * loud failure when a marker is present but the vault has no password
    (no bogus value, no marker, typed);
  * end-to-end: a recorded password never appears on disk;
  * scrub_stored_passwords() migrates pre-existing plaintext macros.

Plain functions + context managers (no local fixtures) for runner +
real-pytest compatibility. A fake page records what gets typed.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import tempfile

from bulk_downloader import macro_recorder as mr
from bulk_downloader import macro_replay as mp


# ─── helpers ─────────────────────────────────────────────────────────

@contextmanager
def _macro_home():
    """Isolate the macros/ dir via BD_INSTALL_DIR."""
    prior = os.environ.get("BD_INSTALL_DIR")
    with tempfile.TemporaryDirectory() as td:
        os.environ["BD_INSTALL_DIR"] = td
        try:
            yield Path(td) / "macros"
        finally:
            if prior is None:
                os.environ.pop("BD_INSTALL_DIR", None)
            else:
                os.environ["BD_INSTALL_DIR"] = prior


@contextmanager
def _vault(password):
    """Monkeypatch the secret resolver to return `password` (or None).

    Patches the resolver on EVERY reachable macro_recorder module object:
    the one bound at import, ``sys.modules[...]``, and the
    ``bulk_downloader.macro_recorder`` package attribute. Neighbor macro
    tests delete+reimport macro_recorder (the blessed sys.modules wipe)
    and their teardown restores sys.modules but NOT the package attribute
    — so ``from . import macro_recorder`` inside replay_on_page can land
    on a stale object that sys.modules-only patching misses. Patching all
    reachable objects keeps this test robust to neighbor ordering.
    """
    import sys
    targets, seen = [], set()
    pkg = sys.modules.get("bulk_downloader")
    candidates = [mr, sys.modules.get("bulk_downloader.macro_recorder"),
                  getattr(pkg, "macro_recorder", None) if pkg else None]
    for m in candidates:
        if m is not None and id(m) not in seen:
            seen.add(id(m))
            targets.append(m)
    origs = [(m, m._resolve_secret_for) for m in targets]
    for m in targets:
        m._resolve_secret_for = lambda site_id, _p=password: _p
    try:
        yield
    finally:
        for m, o in origs:
            m._resolve_secret_for = o


class _FakeLocator:
    def __init__(self, page, sel):
        self._page, self._sel = page, sel
        self.first = self

    def fill(self, text, **kw):
        self._page.typed.append((self._sel, text))

    def type(self, text, **kw):
        self._page.typed.append((self._sel, text))

    def click(self, **kw):
        pass

    def wait_for(self, **kw):
        pass


class _FakePage:
    """Supports both executor styles: page.locator(sel).first.fill (the
    macro_recorder path) and page.fill(sel, text) (the macro_replay path)."""
    def __init__(self):
        self.typed = []          # list of (selector, text)

    def locator(self, sel):
        return _FakeLocator(self, sel)

    def fill(self, sel, text, **kw):
        self.typed.append((sel, text))

    def type(self, sel, text, **kw):
        self.typed.append((sel, text))

    def evaluate(self, *a, **k):
        pass


# ─── record-time scrub ───────────────────────────────────────────────

def test_record_scrubs_password_by_selector():
    with _macro_home():
        actions = [{"kind": "type", "selector": "input[type='password']",
                    "text": "hunter2-real"}]
        r = mr.record_macro("acme", "login", actions, tags=["login_flow"])
        assert r["ok"], r
        bundle = mr.get_macro("acme", "login")
        a = bundle["actions"][0]
        assert a["text"] == mr.VAULT_MARKER
        assert a["secret"] is True
        # and nothing plaintext on disk
        blob = Path(r["path"]).read_text(encoding="utf-8")
        assert "hunter2-real" not in blob


def test_record_scrubs_by_explicit_secret_flag():
    with _macro_home():
        # id-based selector the heuristic alone wouldn't catch — the flag does
        actions = [{"kind": "type", "selector": "#pw", "text": "secretpw",
                    "secret": True}]
        mr.record_macro("acme", "flow", actions)
        a = mr.get_macro("acme", "flow")["actions"][0]
        assert a["text"] == mr.VAULT_MARKER and a["secret"] is True


def test_record_does_not_touch_non_password_fields():
    with _macro_home():
        actions = [
            {"kind": "type", "selector": "input[name='q']", "text": "search term"},
            {"kind": "type", "selector": "#passport", "text": "AB1234567"},  # not a pw
            {"kind": "click", "selector": "#go"},
        ]
        mr.record_macro("acme", "search", actions)
        got = mr.get_macro("acme", "search")["actions"]
        assert got[0]["text"] == "search term"        # untouched
        assert got[1]["text"] == "AB1234567"          # 'passport' != password
        assert "secret" not in got[0]


def test_scrub_is_idempotent():
    actions = [{"kind": "type", "selector": "input[type='password']",
                "text": mr.VAULT_MARKER}]
    scrubbed, n = mr._scrub_secret_actions(actions)
    assert n == 0                                     # already a marker
    assert scrubbed[0]["text"] == mr.VAULT_MARKER
    assert scrubbed[0]["secret"] is True              # flag normalised


# ─── replay substitution: macro_recorder.replay_macro ────────────────

def test_replay_macro_substitutes_marker_from_vault():
    page = _FakePage()
    macro = {"actions": [{"kind": "type", "selector": "#pw",
                          "text": mr.VAULT_MARKER, "secret": True}]}
    with _vault("VAULT-PASSWORD"):
        res = mr.replay_macro(page, macro, site_id="acme", name="login")
    assert res["ok"], res
    assert page.typed == [("#pw", "VAULT-PASSWORD")]   # real value typed
    # the marker literal was never typed
    assert all(t != mr.VAULT_MARKER for _, t in page.typed)


def test_replay_macro_fails_loud_when_vault_empty():
    page = _FakePage()
    macro = {"actions": [{"kind": "type", "selector": "#pw",
                          "text": mr.VAULT_MARKER, "secret": True}]}
    with _vault(None):
        res = mr.replay_macro(page, macro, site_id="acme", name="login",
                              strict=True)
    assert res["ok"] is False
    assert res["failed_at"] == 0
    assert "no vault password" in res["error"]
    assert page.typed == []                            # nothing typed at all


def test_replay_macro_leaves_normal_type_actions_alone():
    page = _FakePage()
    macro = {"actions": [{"kind": "type", "selector": "#u", "text": "alice"}]}
    with _vault("UNUSED"):
        res = mr.replay_macro(page, macro, site_id="acme", name="f")
    assert res["ok"]
    assert page.typed == [("#u", "alice")]


# ─── replay substitution: macro_replay.replay_on_page ────────────────

def test_replay_on_page_substitutes_marker():
    page = _FakePage()
    actions = [{"kind": "type", "selector": "#pw",
                "text": mr.VAULT_MARKER, "secret": True}]
    with _vault("OTHER-VAULT-PW"):
        res = mp.replay_on_page(page, actions, site_id="acme")
    assert res["ok"], res
    assert page.typed == [("#pw", "OTHER-VAULT-PW")]


def test_replay_on_page_fails_loud_when_vault_empty():
    page = _FakePage()
    actions = [{"kind": "type", "selector": "#pw",
                "text": mr.VAULT_MARKER, "secret": True}]
    with _vault(None):
        res = mp.replay_on_page(page, actions, site_id="acme")
    assert res["ok"] is False
    assert "no vault password" in (res["error"] or "")
    assert page.typed == []


# ─── migration of pre-existing plaintext macros ──────────────────────

def test_migration_scrubs_existing_plaintext_files():
    with _macro_home() as macro_dir:
        macro_dir.mkdir(parents=True, exist_ok=True)
        # Write a legacy macro with a plaintext password straight to disk
        legacy = {
            "site_id": "acme", "name": "login",
            "actions": [
                {"kind": "type", "selector": "#u", "text": "alice"},
                {"kind": "type", "selector": "input[type='password']",
                 "text": "OLD-PLAINTEXT-PW"},
            ],
            "metadata": {"tags": ["login_flow"]},
        }
        p = macro_dir / "acme_login.json"
        p.write_text(json.dumps(legacy), encoding="utf-8")

        out = mr.scrub_stored_passwords()
        assert out["modified"] == 1
        assert str(p) in out["files"]

        after = json.loads(p.read_text(encoding="utf-8"))
        pw_action = after["actions"][1]
        assert pw_action["text"] == mr.VAULT_MARKER
        assert "OLD-PLAINTEXT-PW" not in p.read_text(encoding="utf-8")
        assert after["actions"][0]["text"] == "alice"   # username untouched


def test_migration_noop_when_nothing_to_scrub():
    with _macro_home() as macro_dir:
        macro_dir.mkdir(parents=True, exist_ok=True)
        clean = {"site_id": "acme", "name": "x",
                 "actions": [{"kind": "click", "selector": "#go"}],
                 "metadata": {}}
        (macro_dir / "acme_x.json").write_text(json.dumps(clean), encoding="utf-8")
        out = mr.scrub_stored_passwords()
        assert out["scanned"] == 1 and out["modified"] == 0
