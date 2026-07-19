"""PLUGIN-DISPATCH -- wire the plugin ``@extractor`` registry into the capture
flow (the prerequisite that makes GH-2's yt-dlp adapter, and every existing
exec/node/py-bridge plugin extractor, actually run).

RED-first. On pristine v3.66.690:
  * ``ExtractorsMixin`` has no ``_try_plugin_extractor`` -> the dispatch unit
    tests RED (AttributeError).
  * ``runner.py``'s per-URL dispatch chain never calls ``_try_plugin_extractor``
    / consults ``plugins.get_extractor`` -> the structural guard RED.

Finding this cut fixes: ``plugins.register_extractor`` populates ``_extractors``
and ``list_extractors`` READS it (status/blast-radius), but nothing ever invoked
a registered extractor during capture -- ``get_extractor`` had zero callers. So
the whole ``@extractor`` mechanism was registration-only. This wires the
dispatch: a registered ``@extractor`` for the current site is tried before the
browser path; a ``{"video_url"}`` result is downloaded via the existing
``_do_direct_http_download``; ``{}``/``None``/no video_url falls through
unchanged. Gate is simply "an extractor is registered for this site" -- naturally
opt-in, no config flag. (HLS results are deferred -> fall through for now.)
"""
import os
import types


def _fake_runner(site_id, config, dl_calls):
    def _ddhd(page_url, file_url, output_path, referer=""):
        dl_calls.append({"file_url": file_url, "output_path": output_path})
        try:
            open(output_path, "w").close()   # simulate a written file
        except Exception:
            pass
        return True
    return types.SimpleNamespace(
        site_id=site_id, config=config,
        _do_direct_http_download=_ddhd,
        _update_job=lambda *a, **k: None,
        log_event=lambda *a, **k: None,
    )


def _call(fake_self, url):
    from bulk_downloader.runner_extractors import ExtractorsMixin
    return ExtractorsMixin._try_plugin_extractor(fake_self, url)


def test_no_registered_extractor_falls_through(tmp_path):
    from bulk_downloader import plugins as P
    P.unregister_extractor("siteX")
    calls = []
    fs = _fake_runner("siteX", {"download_dir": str(tmp_path)}, calls)
    assert _call(fs, "https://ex/v") is False
    assert calls == [], "no registered extractor -> no download attempted"


def test_extractor_video_url_downloads(tmp_path):
    from bulk_downloader import plugins as P
    P.register_extractor("siteY", lambda u, ctx: {"video_url": "https://cdn/x.mp4",
                                                  "title": "clip"})
    try:
        calls = []
        fs = _fake_runner("siteY", {"download_dir": str(tmp_path)}, calls)
        ok = _call(fs, "https://ex/v")
        assert ok is True
        assert len(calls) == 1 and calls[0]["file_url"] == "https://cdn/x.mp4", calls
    finally:
        P.unregister_extractor("siteY")


def test_extractor_receives_url_and_context(tmp_path):
    from bulk_downloader import plugins as P
    seen = {}

    def _ex(u, ctx):
        seen["url"] = u
        seen["ctx"] = ctx
        return {"video_url": "https://cdn/y.mp4"}
    P.register_extractor("siteC", _ex)
    try:
        fs = _fake_runner("siteC", {"download_dir": str(tmp_path), "name": "Site C"}, [])
        _call(fs, "https://ex/watch")
        assert seen["url"] == "https://ex/watch"
        assert seen["ctx"].get("site_id") == "siteC"
    finally:
        P.unregister_extractor("siteC")


def test_empty_result_falls_through(tmp_path):
    from bulk_downloader import plugins as P
    P.register_extractor("siteZ", lambda u, ctx: {})
    try:
        calls = []
        fs = _fake_runner("siteZ", {"download_dir": str(tmp_path)}, calls)
        assert _call(fs, "https://ex/v") is False
        assert calls == []
    finally:
        P.unregister_extractor("siteZ")


def test_none_result_falls_through(tmp_path):
    from bulk_downloader import plugins as P
    P.register_extractor("siteN", lambda u, ctx: None)
    try:
        fs = _fake_runner("siteN", {"download_dir": str(tmp_path)}, [])
        assert _call(fs, "https://ex/v") is False
    finally:
        P.unregister_extractor("siteN")


def test_is_hls_result_falls_through_for_now(tmp_path):
    from bulk_downloader import plugins as P
    P.register_extractor("siteH", lambda u, ctx: {"video_url": "https://cdn/x.m3u8",
                                                  "is_hls": True})
    try:
        calls = []
        fs = _fake_runner("siteH", {"download_dir": str(tmp_path)}, calls)
        assert _call(fs, "https://ex/v") is False, "HLS via plugin extractor is deferred"
        assert calls == []
    finally:
        P.unregister_extractor("siteH")


def test_extractor_exception_is_guarded(tmp_path):
    from bulk_downloader import plugins as P

    def _boom(u, ctx):
        raise RuntimeError("nope")
    P.register_extractor("siteE", _boom)
    try:
        fs = _fake_runner("siteE", {"download_dir": str(tmp_path)}, [])
        assert _call(fs, "https://ex/v") is False
    finally:
        P.unregister_extractor("siteE")


def test_dispatch_chain_wires_plugin_extractor():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    src = open(os.path.join(root, "bulk_downloader", "runner.py"), encoding="utf-8").read()
    assert "_try_plugin_extractor" in src, \
        "the per-URL dispatch chain must call _try_plugin_extractor"
