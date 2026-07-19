"""test_provider_resolve_surface_lock.py -- attribute-surface guard for the
provider_resolve -> provider_resolve_impl package split (DECOMP-LEAF cut 6, final)."""
from bulk_downloader import provider_resolve as pr

PUBLIC = {"build_signing_callback", "resolve_provider_embed", "resolve_vimeo", "resolve_wistia",
          "resolve_jwplayer", "resolve_brightcove", "resolve_youtube"}
# tests monkeypatch these on the shim; they must stay attributes that callers honor at call time
MONKEYPATCHED = {"_default_http_get", "_now", "_yt_cipher_ytdlp_path", "_is_safe_public_host",
                 "_decipher_signed_formats", "_YT_CIPHER_YTDLP_PATH_CACHE"}
ALIASES = {"HttpGet", "CacheWrite"}


def test_public_surface_present():
    assert not (PUBLIC - set(dir(pr))), f"dropped: {sorted(PUBLIC - set(dir(pr)))}"


def test_monkeypatch_targets_present():
    assert not (MONKEYPATCHED - set(dir(pr))), f"dropped monkeypatch targets: {sorted(MONKEYPATCHED - set(dir(pr)))}"


def test_all_matches_original():
    assert pr.__all__ == ["HttpGet", "CacheWrite", "DEFAULT_CACHE_TTL_SECONDS", "SSRFBlocked",
                          "resolve_provider_embed", "resolve_vimeo", "resolve_youtube",
                          "resolve_brightcove", "resolve_wistia", "resolve_jwplayer",
                          "build_signing_callback"]


def test_aliases_and_class_present():
    assert ALIASES <= set(dir(pr)) and hasattr(pr, "SSRFBlocked")
    assert sorted(pr._RESOLVERS) == ["brightcove", "jwplayer", "vimeo", "wistia", "youtube"]


def test_default_http_get_monkeypatch_is_honored():
    # the core H-07 contract: rebinding pr._default_http_get is seen by resolve_provider_embed
    calls = []
    def fail(url):
        calls.append(url); raise ConnectionError("net")
    orig = pr._default_http_get
    pr._default_http_get = fail
    try:
        pr.resolve_provider_embed({"provider": "vimeo",
                                   "embed_url": "https://player.vimeo.com/video/1",
                                   "ids": {"clip_id": "1"}})
    except Exception:
        pass
    finally:
        pr._default_http_get = orig
    assert calls, "resolve_provider_embed did not honor the pr._default_http_get monkeypatch"


def test_each_submodule_imports():
    import importlib
    for mod in ("_common", "vimeo", "wistia", "jwplayer", "brightcove", "youtube", "dispatch"):
        importlib.import_module(f"bulk_downloader.provider_resolve_impl.{mod}")
