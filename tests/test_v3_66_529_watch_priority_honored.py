"""v3.66.529 -- THC: watch-folder priority must be honored, not silently dropped.

The bug: process_file() called ``runner.load_urls(urls, url_priorities=...)`` but
load_urls' real signature is ``load_urls(urls, dedupe=True, folder_scan=False)`` --
no ``url_priorities`` param. So the call ALWAYS raised TypeError, the ``except
TypeError`` fell back to ``load_urls(urls)`` WITHOUT priority, and the configured
watch_url_priority was silently dropped on every imported file.

The fix loads the URLs then calls ``runner.bulk_priority(norm_urls, priority)``
(a real method on the runner that tags the in-memory jobs, queue ordering, and db
rows). bulk_priority is a method on the passed runner, so no new import edge.

RED-first via a fake runner whose load_urls mirrors the REAL signature (so the
url_priorities kwarg TypeErrors exactly as in production). Zero-arg fns + tempfile;
runs under the custom runner and pytest.
"""
import tempfile
from pathlib import Path

from bulk_downloader.watch_folder import process_file


class _FakeRunner:
    """Mirrors the real SiteRunner contract the watch folder depends on."""
    site_id = "s1"

    def __init__(self):
        self.loaded = None
        self.priority_calls = []

    # REAL signature -- a url_priorities= kwarg must raise TypeError, like production
    def load_urls(self, urls, dedupe=True, folder_scan=False):
        self.loaded = list(urls)
        return {"added": len(urls)}

    def bulk_priority(self, urls, priority):
        self.priority_calls.append((list(urls), priority))
        return len(urls)


def _write_watch_file(tmp, lines):
    f = Path(tmp) / "batch.txt"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def test_high_priority_is_applied_to_loaded_urls():
    tmp = tempfile.mkdtemp(prefix="bd_wf_thc_")
    urls = ["https://example.com/a", "https://example.com/b"]
    f = _write_watch_file(tmp, urls)
    r = _FakeRunner()

    res = process_file(f, r, priority="high")

    assert res["ok"] is True, res
    assert r.loaded == urls, ("urls must reach load_urls", r.loaded)
    # THE REGRESSION: priority must actually be applied (was silently dropped).
    assert r.priority_calls == [(urls, "high")], (
        "watch-folder priority was not honored (THC: load_urls url_priorities kwarg "
        "TypeError'd and the priority was dropped)", r.priority_calls)


def test_normal_priority_does_not_call_bulk_priority():
    tmp = tempfile.mkdtemp(prefix="bd_wf_thc_n_")
    f = _write_watch_file(tmp, ["https://example.com/x"])
    r = _FakeRunner()

    res = process_file(f, r, priority="normal")

    assert res["ok"] is True, res
    assert r.priority_calls == [], ("normal priority must not tag rows", r.priority_calls)


def test_tab_header_url_is_normalized_before_priority_tag():
    """A tab-separated header suffix is stripped (mirroring load_urls) so the
    queued job key matches when bulk_priority tags it."""
    tmp = tempfile.mkdtemp(prefix="bd_wf_thc_t_")
    f = _write_watch_file(tmp, ["https://example.com/v.mp4\tReferer=https://example.com"])
    r = _FakeRunner()

    res = process_file(f, r, priority="high")

    assert res["ok"] is True, res
    assert r.priority_calls == [(["https://example.com/v.mp4"], "high")], r.priority_calls
