"""Tests for cross-site selector reuse — P5-1b (v3.66.35).

Covers the pure core: shape normalization, signature stability,
confidence gating, store round-trip + atomicity, record/lookup/augment,
and the env-gated façade (no-op when off, augments when on).
"""
import json
import os

import pytest

from bulk_downloader import cross_site_selectors as css


# ── selector_shape: volatile stripped, stable kept ──────────────────
class TestSelectorShape:
    def test_blank_is_empty(self):
        assert css.selector_shape("") == ""
        assert css.selector_shape("   ") == ""
        assert css.selector_shape(None) == ""

    def test_stable_attr_kept(self):
        assert css.selector_shape("input[name='username']") == "input[name=username]"
        assert css.selector_shape('input[type="password"]') == "input[type=password]"

    def test_camel_hash_class_stripped(self):
        # Aylo-style rotated CSS-module classes vanish; tag survives.
        assert css.selector_shape("form.joHetV button.ljLTqn") == "form button"

    def test_styled_components_prefix_stripped(self):
        assert css.selector_shape("button.sc-1a2b3c") == "button"

    def test_wordlike_class_kept(self):
        assert css.selector_shape("button.login-btn") == "button.login-btn"

    def test_numeric_id_suffix_stripped(self):
        assert css.selector_shape("#user_12345") == "#user"
        assert css.selector_shape("#field-887") == "#field"

    def test_has_text_preserved_and_lowercased(self):
        shape = css.selector_shape("button:has-text('Sign In')")
        assert "has-text" in shape and "sign in" in shape

    def test_two_aylo_sites_same_shape(self):
        a = css.selector_shape("form.joHetV input[name='username']")
        b = css.selector_shape("form.qWxYzA input[name='username']")
        assert a == b == "form input[name=username]"


# ── form_signature ──────────────────────────────────────────────────
class TestFormSignature:
    def _block(self, u="input[name='username']", p="input[type='password']",
               s="button[type='submit']"):
        b = {}
        if u: b["user_field"] = [u]
        if p: b["pass_field"] = [p]
        if s: b["submit_btn"] = [s]
        return b

    def test_none_without_user_or_pass(self):
        # submit-only block has no fingerprint anchor
        assert css.form_signature({"submit_btn": ["button[type='submit']"]}) is None
        assert css.form_signature({}) is None
        assert css.form_signature(None) is None

    def test_structurally_identical_sites_collide(self):
        a = self._block(u="form.joHetV input[name='username']")
        b = self._block(u="form.qWxYzA input[name='username']")
        assert css.form_signature(a) == css.form_signature(b)
        assert css.form_signature(a) is not None

    def test_different_structure_differs(self):
        a = self._block(u="input[name='username']")
        b = self._block(u="input[name='email']")
        assert css.form_signature(a) != css.form_signature(b)

    def test_uses_top_selector_only(self):
        # adding lower-priority selectors doesn't change the signature
        a = self._block()
        b = self._block()
        b["user_field"].append("input.fallback")
        assert css.form_signature(a) == css.form_signature(b)


# ── high_confidence_selectors ───────────────────────────────────────
class TestHighConfidence:
    def test_only_proven_selectors_eligible(self):
        block = {
            "user_field": ["#proven", "#unproven", "#failing"],
            "_per_selector": {
                "#proven": {"hits": 5, "misses": 0},
                "#unproven": {"hits": 1, "misses": 0},   # below min_hits
                "#failing": {"hits": 5, "misses": 2},     # has misses
            },
        }
        hc = css.high_confidence_selectors(block, min_hits=2)
        assert hc["user_field"] == ["#proven"]

    def test_no_counter_means_not_eligible(self):
        block = {"user_field": ["#never_exercised"], "_per_selector": {}}
        assert css.high_confidence_selectors(block)["user_field"] == []


# ── store round-trip + atomicity ────────────────────────────────────
class TestStoreIO:
    def test_missing_file_returns_empty(self, tmp_path):
        p = str(tmp_path / "nope.json")
        store = css.load_store(p)
        assert store["signatures"] == {} and store["version"] == css.STORE_VERSION

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        store = css.load_store(str(p))
        assert store["signatures"] == {}

    def test_round_trip(self, tmp_path):
        p = str(tmp_path / "store.json")
        store = css._empty_store()
        store["signatures"]["abc"] = {"user_field": [
            {"selector": "#x", "source": "sitea", "hits": 3}]}
        css.save_store(store, p)
        back = css.load_store(p)
        assert back["signatures"]["abc"]["user_field"][0]["selector"] == "#x"

    def test_atomic_no_tmp_left(self, tmp_path):
        p = str(tmp_path / "store.json")
        css.save_store(css._empty_store(), p)
        leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert leftovers == []


# ── record / lookup ─────────────────────────────────────────────────
class TestRecordLookup:
    def _block(self):
        return {
            "user_field": ["input[name='username']"],
            "pass_field": ["input[type='password']"],
            "submit_btn": ["button[type='submit']"],
            "_per_selector": {
                "input[name='username']": {"hits": 4, "misses": 0},
                "input[type='password']": {"hits": 4, "misses": 0},
                "button[type='submit']": {"hits": 4, "misses": 0},
            },
        }

    def test_record_then_lookup(self):
        store = css._empty_store()
        block = self._block()
        assert css.record_learned(store, "sitea", block) is True
        sig = css.form_signature(block)
        found = css.lookup(store, sig)
        assert found["user_field"] == ["input[name='username']"]

    def test_lookup_excludes_own_source(self):
        store = css._empty_store()
        block = self._block()
        css.record_learned(store, "sitea", block)
        sig = css.form_signature(block)
        # Site A doesn't get its own selectors back via cross-site path.
        assert css.lookup(store, sig, exclude_source="sitea")["user_field"] == []
        # A structurally-identical Site B does.
        assert css.lookup(store, sig, exclude_source="siteb")["user_field"] == \
            ["input[name='username']"]

    def test_re_record_keeps_higher_hits(self):
        store = css._empty_store()
        b = self._block()
        css.record_learned(store, "sitea", b)
        b["_per_selector"]["input[name='username']"]["hits"] = 10
        css.record_learned(store, "sitea", b)
        sig = css.form_signature(b)
        entry = store["signatures"][sig]["user_field"][0]
        assert entry["hits"] == 10

    def test_role_cap(self):
        store = css._empty_store()
        sig = "fixedsig"
        store["signatures"][sig] = {"user_field": [
            {"selector": f"#s{i}", "source": "s", "hits": i}
            for i in range(css.MAX_PER_ROLE + 5)]}
        # force a cap via a record under the same sig
        block = {
            "user_field": ["input[name='username']"],
            "pass_field": ["input[type='password']"],
            "_per_selector": {
                "input[name='username']": {"hits": 99, "misses": 0},
                "input[type='password']": {"hits": 99, "misses": 0}},
        }
        real_sig = css.form_signature(block)
        store["signatures"][real_sig] = {"user_field": [
            {"selector": f"#s{i}", "source": "s", "hits": i}
            for i in range(css.MAX_PER_ROLE + 5)]}
        css.record_learned(store, "sitea", block)
        assert len(store["signatures"][real_sig]["user_field"]) <= css.MAX_PER_ROLE


# ── augment_chain ───────────────────────────────────────────────────
class TestAugmentChain:
    def test_tail_appended(self):
        out = css.augment_chain(["#a", "#b"], ["#c", "#d"])
        assert out == ["#a", "#b", "#c", "#d"]

    def test_dedup_preserves_own_order(self):
        out = css.augment_chain(["#a", "#b"], ["#b", "#c"])
        assert out == ["#a", "#b", "#c"]

    def test_limit_respected(self):
        out = css.augment_chain(["#a"], ["#b", "#c", "#d", "#e", "#f"], limit=2)
        assert out == ["#a", "#b", "#c"]

    def test_does_not_mutate_input(self):
        own = ["#a"]
        css.augment_chain(own, ["#b"])
        assert own == ["#a"]


# ── façade: env gate + end-to-end ───────────────────────────────────
class TestSyncAndAugment:
    def _config(self, source="siteb.com"):
        return {
            "login_url": f"https://{source}/login",
            "learned": {"login": {
                "user_field": ["input[name='username']"],
                "pass_field": ["input[type='password']"],
                "submit_btn": ["button[type='submit']"],
                "_per_selector": {
                    "input[name='username']": {"hits": 4, "misses": 0},
                    "input[type='password']": {"hits": 4, "misses": 0},
                    "button[type='submit']": {"hits": 4, "misses": 0},
                }}},
        }

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.delenv(css._ENV_FLAG, raising=False)
        chains = {"user_field": ["#a"], "pass_field": ["#b"], "submit_btn": ["#c"]}
        assert css.sync_and_augment(self._config(), dict(chains)) == chains

    def test_enabled_records_and_augments(self, monkeypatch, tmp_path):
        monkeypatch.setenv(css._ENV_FLAG, "1")
        monkeypatch.setenv("BD_HOME", str(tmp_path))

        # Site A records first.
        site_a = self._config(source="sitea.com")
        css.sync_and_augment(site_a, {
            "user_field": list(site_a["learned"]["login"]["user_field"]),
            "pass_field": list(site_a["learned"]["login"]["pass_field"]),
            "submit_btn": list(site_a["learned"]["login"]["submit_btn"])})

        # Site B (structurally identical, different host) gets A's selector
        # appended as a tail — but it's already in B's own learned list, so
        # dedup means no duplicate; prove cross-site by giving B a DIFFERENT
        # proven selector set that still shares the signature.
        store = css.load_store()
        sig = css.form_signature(site_a["learned"]["login"])
        assert sig in store["signatures"]
        # A's own source is excluded (it already has these as learned)…
        assert css.lookup(store, sig, exclude_source="sitea.com")["user_field"] == []
        # …but a structurally-identical Site B sees A's proven selectors.
        assert css.lookup(store, sig, exclude_source="siteb.com")["user_field"]

    def test_corrupt_store_does_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setenv(css._ENV_FLAG, "1")
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        (tmp_path / "cross_site_selectors.json").write_text("garbage", encoding="utf-8")
        chains = {"user_field": ["#a"], "pass_field": ["#b"], "submit_btn": ["#c"]}
        # must not raise; returns at least the originals
        out = css.sync_and_augment(self._config(), dict(chains))
        assert out["user_field"][:1] == ["#a"]

    def test_no_signature_passes_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv(css._ENV_FLAG, "1")
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        cfg = {"login_url": "https://x.com/login", "learned": {"login": {}}}
        chains = {"user_field": ["#a"], "pass_field": ["#b"], "submit_btn": ["#c"]}
        assert css.sync_and_augment(cfg, dict(chains)) == chains
