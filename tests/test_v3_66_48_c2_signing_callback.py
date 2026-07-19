"""C2 — JWPlayer signing_callback wiring.

v3.66.23 added a ``signing_callback`` hook on the self-hosted JWPlayer
resolver: if an embed dict carries ``signing_callback``, the resolver
delegates the fetch to it (the integration hook for operators with
legitimate credentials). What was missing was a way to supply it from a
per-site config and attach it at resolution time.

Honest scope note (verified against v3.66.47 source): provider
resolution itself is opt-in — it only runs when a caller passes
``resolve_providers=True`` to ``deep_detect`` / ``deep_detect_live``, and
NO default caller (runner fallback, /api/dev/deep_detect) does today. So
this wiring is inert until provider resolution is also enabled; these
tests therefore drive the resolution path directly with
``resolve_providers=True``, which is exactly how a caller that opts in
would reach it.

Two pieces:
  1. ``provider_resolve.build_signing_callback(config)`` — imports a
     per-site-config-named module/function, fail-safe to None.
  2. ``deep_detect`` attaches a supplied ``signing_callback`` to JWPlayer
     embeds (and only those) before resolution; ``deep_detect_live``
     threads the param through.
"""
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.bd_module_wipe


# ── 1. build_signing_callback ─────────────────────────────────────────


class TestBuildSigningCallback:
    def _build(self, **cfg):
        from bulk_downloader.provider_resolve import build_signing_callback
        return build_signing_callback(cfg)

    def test_valid_module_and_function_returns_callable(self):
        # Use a real stdlib callable as the target.
        cb = self._build(signing_callback_module="json",
                         signing_callback_function="dumps")
        import json
        assert cb is json.dumps
        assert callable(cb)

    def test_missing_module_field_returns_none(self):
        assert self._build(signing_callback_function="dumps") is None

    def test_missing_function_field_returns_none(self):
        assert self._build(signing_callback_module="json") is None

    def test_empty_strings_return_none(self):
        assert self._build(signing_callback_module="  ",
                           signing_callback_function="dumps") is None
        assert self._build(signing_callback_module="json",
                           signing_callback_function="") is None

    def test_nonexistent_module_returns_none(self):
        assert self._build(
            signing_callback_module="bulk_downloader._no_such_mod_xyz",
            signing_callback_function="f") is None

    def test_nonexistent_function_returns_none(self):
        assert self._build(signing_callback_module="json",
                           signing_callback_function="not_a_real_attr") is None

    def test_non_callable_attribute_returns_none(self):
        # json.__doc__ exists but is a str, not callable.
        assert self._build(signing_callback_module="json",
                           signing_callback_function="__doc__") is None

    def test_non_dict_config_returns_none(self):
        from bulk_downloader.provider_resolve import build_signing_callback
        assert build_signing_callback(None) is None
        assert build_signing_callback("nope") is None
        assert build_signing_callback(["a"]) is None

    def test_in_public_api(self):
        from bulk_downloader import provider_resolve
        assert "build_signing_callback" in provider_resolve.__all__


# ── 2. deep_detect attaches the callback to JWPlayer embeds only ──────


def _marker():
    """A sentinel callable to thread through as the signing_callback."""
    def _cb(url):
        return (200, {}, b"")
    return _cb


class TestDeepDetectAttachesSigningCallback:
    HTML = "<html><body>" + "x" * 300 + "</body></html>"

    def _run(self, signing_callback):
        """Drive deep_detect with two embeds (jwplayer + vimeo) and
        capture what resolve_provider_embed receives for each."""
        from bulk_downloader import deep_detect, provider_resolve

        jw = {"provider": "jwplayer", "ids": {"media_id": "abc123"}}
        vm = {"provider": "vimeo", "ids": {"video_id": "987"}}
        embeds = [jw, vm]
        captured = []

        def fake_extract(html, base_url=""):
            return embeds

        def fake_resolve(e, *, http_get=None, site_memory=None):
            captured.append(e)
            return [], None

        # Post-split, deep_detect() resolves extract_provider_embeds in its own
        # (orchestrate) namespace, so patch the use site rather than the package
        # re-export. Falls back to the module itself when deep_detect is a monolith.
        _owner = getattr(deep_detect, "orchestrate", deep_detect)
        with patch.object(_owner, "extract_provider_embeds", fake_extract), \
                patch.object(provider_resolve, "resolve_provider_embed",
                             fake_resolve):
            deep_detect.deep_detect(
                self.HTML,
                base_url="https://x.test/",
                resolve_providers=True,
                http_get=lambda u: (200, {}, b""),
                signing_callback=signing_callback,
            )
        return jw, vm, captured

    def test_jwplayer_embed_gets_the_callback(self):
        cb = _marker()
        jw, vm, captured = self._run(cb)
        # resolve_provider_embed was called for both embeds.
        assert len(captured) == 2
        cap_jw = next(c for c in captured if c.get("provider") == "jwplayer")
        cap_vm = next(c for c in captured if c.get("provider") == "vimeo")
        assert cap_jw.get("signing_callback") is cb
        # Non-jwplayer providers never receive it.
        assert "signing_callback" not in cap_vm

    def test_original_embed_dict_not_mutated(self):
        cb = _marker()
        jw, vm, captured = self._run(cb)
        # The attach uses a shallow copy so the outcome-row reference to
        # the original embed stays clean.
        assert "signing_callback" not in jw
        assert "signing_callback" not in vm

    def test_none_callback_attaches_nothing(self):
        jw, vm, captured = self._run(None)
        for c in captured:
            assert "signing_callback" not in c


# ── 3. deep_detect_live threads the param through ─────────────────────


class TestDeepDetectLivePassesThrough:
    def test_live_forwards_signing_callback_to_deep_detect(self):
        from bulk_downloader import deep_detect
        cb = _marker()
        captured = {}

        # Minimal report shape so deep_detect_live's post-processing
        # doesn't trip on missing keys.
        def fake_dd(html, **kwargs):
            captured["signing_callback"] = kwargs.get("signing_callback")
            return {
                "buckets": {"accepted": [], "best": None, "rejected": [],
                            "rejected_raw": [], "warnings": [], "counts": {}},
                "provider_resolutions": [], "warnings": [], "blockers": {},
                "source_breakdown": {},
            }

        # deep_detect_live() resolves deep_detect in its own (orchestrate) namespace
        # post-split, so patch the use site rather than the package re-export.
        _owner = getattr(deep_detect, "orchestrate", deep_detect)
        with patch.object(_owner, "deep_detect", fake_dd):
            deep_detect.deep_detect_live(
                "<html></html>",
                base_url="https://x.test/",
                resolve_providers=True,
                http_get=lambda u: (200, {}, b""),
                signing_callback=cb,
                max_probes=0,
            )
        assert captured.get("signing_callback") is cb
