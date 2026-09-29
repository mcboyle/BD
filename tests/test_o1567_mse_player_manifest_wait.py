"""O1567 fx-hustler-mse-manifest-wait: an MSE player's manifest can land late.

Live (wrk-191, hustlerunlimited + o1513-a6-hustler1-live, 21:4x-21:5xZ): the
dacast player binds a blob: (MediaSource) src first and requests its
".../<uuid>.ism/.m3u8?context=..." master a moment later. The page-media read
ran in between -- "spa-api: no download-like options in 0 captured API
record(s) and 1 page media URL(s)" -- and the scene failed "[page_shape] No
download button found"; the runner's own watcher logged "hls manifest detected"
for that master right after (shots/hustler1-aip6-fail-page.png). The same
scene downloaded when the read happened to come later (20:29Z, 21:45Z).
"""
BD_GATE_SCOPE = "module"

from bulk_downloader import spa_media_extract as _spa

MASTER = "https://cdn-01.vod.adaptive.dacast.com/usp/ab.ism/.m3u8?context=x"


class _Page:
    url = "https://hustlerunlimited.com/videos/all-in-pleasure-6/"

    def __init__(self, late_after):
        self.reads = 0
        self.late_after = late_after

    def evaluate(self, js, *a):
        if js == _spa.PAGE_MEDIA_JS:
            self.reads += 1
            out = ["blob:https://hustlerunlimited.com/1f2e"]
            if self.reads > self.late_after:
                out.append(MASTER)
            return out
        return []

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return "<html><video></video></html>"


def _runner(monkeypatch):
    from bulk_downloader import runner_extractors as rx

    class R(rx.ExtractorsMixin):
        site_id = "t"
        config = {"name": "t"}
        _spa_api_capture = None

        def log_event(self, *a, **k):
            pass
    seen = []

    def fake_candidates(page_url, media):
        seen.append(list(media))
        return []
    monkeypatch.setattr(_spa, "page_media_candidates", fake_candidates)
    monkeypatch.setattr(_spa, "embed_frame_candidates", lambda *a, **k: [])
    monkeypatch.setattr(_spa, "third_party_frame_hosts", lambda *a, **k: [])
    return R(), seen


def test_late_master_after_blob_src_joins_the_population(monkeypatch):
    r, seen = _runner(monkeypatch)
    page = _Page(late_after=3)
    r._try_spa_api_media_extractor(page.url, page)
    assert seen, "fixture broken: page media never consulted"
    assert any(MASTER in m for m in seen), (
        f"MSE player's late master never read ({page.reads} page-media read(s))")


def test_negative_control_no_blob_player_is_read_once(monkeypatch):
    r, seen = _runner(monkeypatch)

    class P(_Page):
        def evaluate(self, js, *a):
            if js == _spa.PAGE_MEDIA_JS:
                self.reads += 1
                return []
            return []
    page = P(late_after=0)
    r._try_spa_api_media_extractor(page.url, page)
    assert page.reads == 1, f"a page with no MSE player was polled {page.reads}x"
