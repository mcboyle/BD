"""Virtualized catalog traversal contracts (row 911)."""

from contextlib import contextmanager
import threading
import time

from bulk_downloader.runner_browser import BrowserMixin
from bulk_downloader import runner_browser
from bulk_downloader.runner import SiteRunner
from bulk_downloader import runner as runner_module
from bulk_downloader import cloak


BD_GATE_SCOPE = "repo-wide"


class _VirtualizedCatalogPage:
    """Scripted page: each `windows` entry is one snapshot the collector sees."""

    def __init__(self, windows, viewport=720):
        self.windows = list(windows)
        self.viewport = viewport
        self.evaluate_calls = 0
        self.scroll_calls = 0
        self.scroll_steps = []
        self.settle_calls = 0
        self.settle_ms = []

    def evaluate(self, script, arg=None):
        if script is runner_browser._VIRTUAL_SCROLL_BY_JS:
            selector, step = arg
            assert selector == ".catalog-item"
            self.scroll_calls += 1
            self.scroll_steps.append(step)
            return None
        assert script is runner_browser._VIRTUAL_SCROLL_MEDIA_JS and arg == ".catalog-item"
        self.evaluate_calls += 1
        window = self.windows.pop(0) if self.windows else {"urls": [], "at_end": True}
        return {"viewport": self.viewport, **window}

    def wait_for_timeout(self, milliseconds):
        self.settle_calls += 1
        self.settle_ms.append(milliseconds)


def test_virtualized_catalog_accumulates_media_before_unmounting():
    page = _VirtualizedCatalogPage([
        {"urls": ["https://media.example/1.mp4", "https://media.example/2.mp4"], "scroll_y": 0},
        {"urls": ["https://media.example/2.mp4", "https://media.example/3.mp4"], "scroll_y": 540},
        {"urls": ["https://media.example/4.mp4"], "scroll_y": 1080, "at_end": True},
    ])

    urls = BrowserMixin()._collect_virtualized_media_urls(page, ".catalog-item")

    assert urls == [
        "https://media.example/1.mp4", "https://media.example/2.mp4",
        "https://media.example/3.mp4", "https://media.example/4.mp4",
    ]
    # Two scrolled windows, one end window, then three empty end snapshots
    # (the bounded wait for an infinite-scroll catalog that might still
    # append) -- no scroll once the end is reached.
    assert page.evaluate_calls == 6
    assert page.scroll_calls == 2
    assert page.settle_calls == 5


def test_scroll_step_stays_inside_the_viewport():
    """A step larger than the viewport leaves a band no virtualizer renders."""
    page = _VirtualizedCatalogPage([
        {"urls": ["https://media.example/1.mp4"], "scroll_y": 0},
        {"urls": [], "scroll_y": 540, "at_end": True},
    ], viewport=720)

    urls = BrowserMixin()._collect_virtualized_media_urls(page, ".catalog-item")

    assert urls == ["https://media.example/1.mp4"]
    assert page.scroll_steps == [540]
    assert BrowserMixin._virtual_scroll_step(0) == 400
    assert BrowserMixin._virtual_scroll_step(None) == 400
    assert BrowserMixin._virtual_scroll_step("bogus") == 400
    assert BrowserMixin._virtual_scroll_step(80) == 100
    assert BrowserMixin._virtual_scroll_step(600) == 450


def test_infinite_scroll_catalog_is_polled_at_the_end_until_it_stops_growing():
    """E2 (headless Chromium, row 911 review): the first at_end snapshot saw 20 of
    100 items because the site appends its next batch after the bottom is
    reached. at_end is a reason to wait, not to stop."""
    page = _VirtualizedCatalogPage([
        {"urls": ["https://media.example/1.mp4"], "scroll_y": 0, "at_end": True},
        {"urls": [], "scroll_y": 0, "at_end": True},              # still loading
        {"urls": ["https://media.example/2.mp4"], "scroll_y": 0},  # appended: not at end
        {"urls": ["https://media.example/3.mp4"], "scroll_y": 540, "at_end": True},
    ])

    urls = BrowserMixin()._collect_virtualized_media_urls(
        page, ".catalog-item", idle_rounds_to_stop=2, settle_ms=7, end_settle_ms=11)

    assert urls == ["https://media.example/1.mp4", "https://media.example/2.mp4",
                    "https://media.example/3.mp4"]
    assert page.scroll_calls == 1
    # end poll, end poll, scroll settle, end poll, then two empty end polls -> stop
    assert page.settle_ms == [11, 11, 7, 11, 11]


def test_virtualized_catalog_stops_when_scrolling_no_longer_moves_the_catalog():
    """A page that ignores scrolling never reaches at_end and never moves:
    bounded stop, not max_rounds."""
    page = _VirtualizedCatalogPage([{"urls": [], "scroll_y": 0}] * 50)

    urls = BrowserMixin()._collect_virtualized_media_urls(
        page, ".catalog-item", max_rounds=40, idle_rounds_to_stop=2)

    assert urls == []
    assert page.evaluate_calls == 3
    assert page.scroll_calls == page.settle_calls == 2


def test_empty_spacer_rows_do_not_end_the_traversal_while_the_catalog_still_moves():
    page = _VirtualizedCatalogPage([
        {"urls": ["https://media.example/1.mp4"], "scroll_y": 0},
        {"urls": [], "scroll_y": 540},
        {"urls": [], "scroll_y": 1080},
        {"urls": [], "scroll_y": 1620},
        {"urls": [], "scroll_y": 2160},
        {"urls": ["https://media.example/2.mp4"], "scroll_y": 2700, "at_end": True},
    ])

    urls = BrowserMixin()._collect_virtualized_media_urls(
        page, ".catalog-item", idle_rounds_to_stop=2)

    assert urls == ["https://media.example/1.mp4", "https://media.example/2.mp4"]
    assert page.scroll_calls == 5


def test_wall_clock_budget_bounds_a_catalog_that_never_stops_growing():
    class _Clock:
        now = 0.0

    page = _VirtualizedCatalogPage(
        [{"urls": [f"https://media.example/{i}.mp4"], "scroll_y": i * 540} for i in range(1000)])
    page.wait_for_timeout = lambda ms: setattr(_Clock, "now", _Clock.now + ms / 1000.0)
    real_monotonic = runner_browser.time.monotonic
    runner_browser.time.monotonic = lambda: _Clock.now
    try:
        urls = BrowserMixin()._collect_virtualized_media_urls(
            page, ".catalog-item", max_rounds=1000, settle_ms=1000, max_seconds=5.0)
    finally:
        runner_browser.time.monotonic = real_monotonic

    assert len(urls) == 6  # rounds at t=0..5s inclusive, then the budget stops it
    assert page.evaluate_calls == 6


def test_page_error_mid_traversal_keeps_what_was_collected():
    """Fail-soft: the caller unions this with the pager's own result; an escaping
    exception there would zero the pager's URLs too (measured at the call site)."""
    class _Breaking(_VirtualizedCatalogPage):
        def wait_for_timeout(self, milliseconds):
            raise RuntimeError("Target page, context or browser has been closed")

    page = _Breaking([
        {"urls": ["https://media.example/1.mp4"], "scroll_y": 0},
        {"urls": ["https://media.example/2.mp4"], "scroll_y": 540},
    ])

    urls = BrowserMixin()._collect_virtualized_media_urls(page, ".catalog-item")

    assert urls == ["https://media.example/1.mp4"]
    assert page.scroll_calls == 1


_VIRTUALIZED_FIXTURE = """<!doctype html><html><head><base href="http://catalog.invalid/"></head>
<body style="margin:0"><div id="spacer" style="height:%(total)dpx;position:relative"></div>
<script>
const N=%(n)d, H=%(h)d, DEBOUNCE=%(debounce)d, sp=document.getElementById('spacer');
let timer=null;
function paint(real){
  const top=window.scrollY, bottom=top+window.innerHeight;
  const first=Math.max(0,Math.floor(top/H)), last=Math.min(N-1,Math.floor(bottom/H));
  sp.innerHTML='';
  for(let i=first;i<=last;i++){
    const d=document.createElement('div'); d.className='item';
    d.style.cssText='position:absolute;top:'+(i*H)+'px;height:'+H+'px;width:100%%';
    d.innerHTML=real?'<a href="/media/'+i+'.mp4">'+i+'</a>':'<div class="skeleton"></div>';
    sp.appendChild(d);
  }
}
function render(){
  if(!DEBOUNCE){ paint(true); return; }
  paint(false); clearTimeout(timer); timer=setTimeout(()=>paint(true), DEBOUNCE);
}
window.addEventListener('scroll',render); paint(true);
</script></body></html>"""

_INNER_CONTAINER_FIXTURE = """<!doctype html><html><head><base href="http://catalog.invalid/">
<style>html,body{height:100%%;margin:0;overflow:hidden}#root{height:100vh;overflow-y:auto}</style></head>
<body><div id="root"><div id="spacer" style="height:%(total)dpx;position:relative"></div></div>
<script>
const N=%(n)d, H=%(h)d, root=document.getElementById('root'), sp=document.getElementById('spacer');
function render(){
  const top=root.scrollTop, bottom=top+root.clientHeight;
  const first=Math.max(0,Math.floor(top/H)), last=Math.min(N-1,Math.floor(bottom/H));
  sp.innerHTML='';
  for(let i=first;i<=last;i++){
    const d=document.createElement('div'); d.className='item';
    d.style.cssText='position:absolute;top:'+(i*H)+'px;height:'+H+'px;width:100%%';
    d.innerHTML='<a href="/media/'+i+'.mp4">'+i+'</a>'; sp.appendChild(d);
  }
}
root.addEventListener('scroll',render); render();
</script></body></html>"""

_INFINITE_FIXTURE = """<!doctype html><html><head><base href="http://catalog.invalid/"></head>
<body style="margin:0"><div id="list"></div>
<script>
let loaded=0; const BATCH=%(batch)d, TOTAL=%(total)d, DELAY=%(delay)d;
function add(){ const l=document.getElementById('list');
  for(let i=0;i<BATCH&&loaded<TOTAL;i++,loaded++){
    const d=document.createElement('div'); d.className='item'; d.style.height='100px';
    d.innerHTML='<a href="/media/'+loaded+'.mp4">'+loaded+'</a>'; l.appendChild(d);} }
add();
let pending=false;
window.addEventListener('scroll',()=>{ if(pending||loaded>=TOTAL) return;
  if(window.scrollY+window.innerHeight>=document.documentElement.scrollHeight-2){
    pending=true; setTimeout(()=>{add();pending=false;},DELAY);} });
</script></body></html>"""

_CARD_FIXTURE = """<!doctype html><html><head><base href="http://catalog.invalid/"></head><body>%s</body></html>"""


def _ids(urls):
    return sorted(int(u.rsplit("/", 1)[1].split(".")[0]) for u in urls if "/media/" in u)


def _collect(context, html, selector=".item", **kwargs):
    page = context.new_page()
    try:
        page.set_content(html)
        mounted = page.evaluate("() => document.querySelectorAll('.item').length")
        started = time.monotonic()
        urls = BrowserMixin()._collect_virtualized_media_urls(page, selector, **kwargs)
        return mounted, urls, time.monotonic() - started
    finally:
        page.close()


def test_real_chromium_extracts_every_item_of_each_measured_catalog_shape():
    # FLEET_RULE 46: a browser test fails closed -- no skip when the browser
    # is missing; the runtime this repo pins ships playwright + chromium.
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            # (a) 40px-item virtualizer, NO overscan: mounts only the on-screen
            # band -- the shape an over-viewport scroll step misses (191/200 measured).
            mounted, urls, elapsed = _collect(
                context, _VIRTUALIZED_FIXTURE % {"total": 4800, "n": 120, "h": 40, "debounce": 0})
            assert 0 < mounted < 120, mounted
            assert len(urls) == len(set(urls)) == 120 and _ids(urls) == list(range(120)), len(urls)
            assert elapsed < 30, elapsed
            # (b) the same virtualizer painting skeletons while scrolling and the
            # real rows only after a 150ms idle (react-window isScrolling): 37/200
            # when the settle is shorter than the debounce.
            mounted, urls, elapsed = _collect(
                context, _VIRTUALIZED_FIXTURE % {"total": 4800, "n": 120, "h": 40, "debounce": 150},
                end_settle_ms=200)
            assert 0 < mounted < 120, mounted
            assert len(urls) == len(set(urls)) == 120 and _ids(urls) == list(range(120)), len(urls)
            # (c) the catalog scrolls inside its own overflow:auto container; the
            # window never moves and reports at_end from round 0 (19/200).
            mounted, urls, elapsed = _collect(
                context, _INNER_CONTAINER_FIXTURE % {"total": 4800, "n": 120, "h": 40}, end_settle_ms=200)
            assert 0 < mounted < 120, mounted
            assert len(urls) == len(set(urls)) == 120 and _ids(urls) == list(range(120)), len(urls)
            # (d) infinite scroll appending 20 items 1200ms after the bottom is
            # reached: the first at_end snapshot sees 20 of 100.
            mounted, urls, elapsed = _collect(
                context, _INFINITE_FIXTURE % {"batch": 20, "total": 100, "delay": 1200})
            assert mounted == 20, mounted
            assert len(urls) == len(set(urls)) == 100 and _ids(urls) == list(range(100)), len(urls)
            assert elapsed < 30, elapsed
            # (e) the media URL on the matched card itself, thumbnails lazy in
            # data-src: both are read, nothing is read twice.
            cards = "".join(
                f'<div class="item" data-media-url="/media/{i}.mp4">'
                f'<img src="/static/ph.gif" data-src="/thumbs/{i}.jpg"></div>' for i in range(50))
            mounted, urls, elapsed = _collect(context, _CARD_FIXTURE % cards, end_settle_ms=200)
            assert mounted == 50
            assert _ids(urls) == list(range(50))
            assert len(urls) == len(set(urls)) == 100, len(urls)
            # Negative controls: a non-matching selector collects nothing and
            # terminates; an invalid selector fails soft to [] (no exception).
            assert _collect(context, _CARD_FIXTURE % cards, selector=".nothing",
                            end_settle_ms=200)[1] == []
            assert _collect(context, _CARD_FIXTURE % cards, selector="div[",
                            end_settle_ms=200)[1] == []
            context.close()
        finally:
            browser.close()
    finally:
        pw.stop()


def test_playlist_expansion_enqueues_virtualized_media(monkeypatch):
    page = object()

    @contextmanager
    def cloaked_page(**_kwargs):
        yield page

    class _Playlist:
        @staticmethod
        def extract_playlist_urls(_page, _url, **_kwargs):
            return type("Result", (), {
                "ok": True,
                "urls": ["https://media.example/0.mp4", "https://media.example/1.mp4"],
                "titles": {},
            })()

    monkeypatch.setattr(runner_module, "_PLAYLIST_AVAILABLE", True)
    monkeypatch.setattr(runner_module, "_playlist", _Playlist())
    monkeypatch.setattr(cloak, "cloaked_page", cloaked_page)
    runner = object.__new__(SiteRunner)
    runner.config = {"virtual_scroll_selector": ".catalog-item"}
    runner.site_id = "test"
    runner._lock = threading.RLock()
    runner._listing_titles = {}
    runner._collect_virtualized_media_urls = lambda actual_page, selector: (
        ["https://media.example/1.mp4", "https://media.example/2.mp4"]
        if actual_page is page and selector == ".catalog-item" else [])

    assert runner._playlist_expand_one("https://catalog.example/") == [
        "https://media.example/0.mp4", "https://media.example/1.mp4",
        "https://media.example/2.mp4"]
