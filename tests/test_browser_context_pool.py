from bulk_downloader.browser_pool import BrowserContextPool, PerThreadBrowser, scrub

import threading
import time

import pytest

BD_GATE_SCOPE = "module"


class Context:
    """Playwright's real surface only: clear_cookies + storage_state + close.
    (There is no BrowserContext.clear_storage in playwright 1.62.)"""

    def __init__(self):
        self.cookies = ["stale"]
        self.local_storage = {}
        self.closed = False
        self.pages = []
        self.extra_headers = {}
        self.permissions = ["geolocation"]
        self.routes = 1
        self.offline = False
        self.created_by = threading.get_ident()
        self.used_by = set()

    def set_extra_http_headers(self, headers):
        self.extra_headers = dict(headers)

    def clear_permissions(self):
        self.permissions = []

    def unroute_all(self):
        self.routes = 0

    def set_offline(self, offline):
        self.offline = offline

    def new_page(self):
        page = _FakePage(self)
        self.pages.append(page)
        return page

    def clear_cookies(self):
        self.cookies.clear()

    def storage_state(self):
        origins = [{"origin": o, "localStorage": [{"name": k, "value": v} for k, v in items.items()]}
                   for o, items in self.local_storage.items() if items]
        return {"cookies": list(self.cookies), "origins": origins}

    def close(self):
        self.closed = True


class _FakePage:
    def __init__(self, context):
        self._context = context
        self.closed = False

    def close(self):
        self.closed = True
        self._context.pages.remove(self)


def _idle(pool):
    return [c for lst in pool._idle.values() for c in lst]


def _pool():
    made = []
    return made, BrowserContextPool(lambda: made.append(Context()) or made[-1])


def test_reuses_a_proven_clean_context_and_closes_it():
    made, pool = _pool()
    with pool.acquire() as first:
        first.cookies.append("session")
    with pool.acquire() as second:
        assert second is first
        assert second.cookies == []
    pool.close()
    assert first.closed and len(made) == 1


def test_context_holding_origin_storage_is_closed_not_reused():
    """E1: local storage cannot be cleared through the real API; a context
    that still reports an origin after the scrub is never handed to the next
    caller -- it is closed and a fresh one is made."""
    made, pool = _pool()
    with pool.acquire() as first:
        first.local_storage["https://site.invalid"] = {"token": "x"}
    assert first.closed is True
    with pool.acquire() as second:
        assert second is not first
        assert second.storage_state()["origins"] == []
    assert len(made) == 2
    pool.close()


def test_context_without_the_real_scrub_surface_is_never_reused():
    class Bare:
        def __init__(self): self.closed = False
        def close(self): self.closed = True

    made = []
    pool = BrowserContextPool(lambda: made.append(Bare()) or made[-1])
    with pool.acquire() as a:
        pass
    assert a.closed is True
    with pool.acquire() as b:
        assert b is not a
    assert len(made) == 2
    assert scrub(Bare()) is False


def test_raising_scrub_closes_the_context():
    class Raising(Context):
        def clear_cookies(self):
            raise RuntimeError("browser gone")

    made = []
    pool = BrowserContextPool(lambda: made.append(Raising()) or made[-1])
    with pool.acquire() as ctx:
        pass
    assert ctx.closed is True and _idle(pool) == []


def test_close_closes_checked_out_contexts_and_they_are_not_re_idled():
    """E2: a context checked out at close() time is closed too, and its
    release afterwards does not resurrect it into the idle list."""
    made, pool = _pool()
    with pool.acquire() as busy:
        pool.close()
        assert busy.closed is True
    assert _idle(pool) == [] and pool._busy == set()
    with pytest.raises(RuntimeError):
        with pool.acquire():
            pass


def test_open_pages_are_closed_before_a_context_is_reused():
    """Round 2 E3: a page left open carries in-memory JS state (window
    globals) into the next job; scrub closes every page, and a context whose
    pages cannot be closed is never re-idled."""
    made, pool = _pool()
    with pool.acquire() as first:
        page = first.new_page()
    assert page.closed is True and first.pages == []
    with pool.acquire() as second:
        assert second is first and second.pages == []

    class Sticky(Context):
        def new_page(self):
            page = super().new_page()
            page.close = lambda: None  # a page that will not close
            return page

    made2 = []
    pool2 = BrowserContextPool(lambda: made2.append(Sticky()) or made2[-1])
    with pool2.acquire() as ctx:
        ctx.new_page()
    assert ctx.closed is True and _idle(pool2) == []


def test_real_chromium_page_state_does_not_leak_between_jobs():
    # FLEET_RULE 46: a browser test fails closed -- no skip when the browser
    # is missing; the runtime this repo pins ships playwright + chromium.
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        pool = BrowserContextPool(browser.new_context)
        with pool.acquire() as ctx:
            page = ctx.new_page()
            page.set_content("<title>job1</title>")
            page.evaluate("window.token = 'in-memory-secret'")
            ctx.add_cookies([{"name": "sid", "value": "x", "url": "https://fixture.invalid/"}])
        with pool.acquire() as ctx2:
            assert ctx2 is ctx  # proven-clean reuse
            assert ctx2.pages == []
            assert ctx2.cookies() == []
            fresh = ctx2.new_page()
            assert fresh.evaluate("typeof window.token") == "undefined"
        pool.close()
        browser.close()
    finally:
        pw.stop()


def test_contexts_are_reused_across_concurrent_jobs():
    """Acceptance 1: N worker threads share the pool; every checkout is
    exclusive (no context is held by two jobs at once) and the pool never
    grows past the peak concurrency -- contexts are reused, not re-created."""
    import threading
    made, pool = _pool()
    workers, jobs_per_worker = 8, 25
    in_use, in_use_lock = set(), threading.Lock()
    overlaps, errors = [], []
    start = threading.Barrier(workers)

    def job():
        start.wait()
        for _ in range(jobs_per_worker):
            try:
                with pool.acquire() as ctx:
                    ctx.used_by.add(threading.get_ident())
                    with in_use_lock:
                        if id(ctx) in in_use:
                            overlaps.append(id(ctx))
                        in_use.add(id(ctx))
                    ctx.cookies.append("job")
                    with in_use_lock:
                        in_use.discard(id(ctx))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        pool.release_thread()          # a worker closes its own idle contexts before exiting

    threads = [threading.Thread(target=job) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and overlaps == []
    assert len(made) == workers                 # one per THREAD, reused per job
    assert _idle(pool) == [] and pool._busy == set()
    assert all(c.closed for c in made)          # released by their own threads: nothing leaks
    assert all(c.cookies == [] for c in made)   # every context proven clean
    # thread affinity: a context is only ever used by the thread that built it
    assert all(len(c.used_by) == 1 and c.used_by == {c.created_by} for c in made)
    pool.close()


def test_factory_runs_outside_the_pool_lock():
    """E4: a slow factory (Chromium IPC) must not serialise other workers'
    release/acquire of idle contexts."""
    import threading
    entered, release = threading.Event(), threading.Event()
    made = []

    def factory():
        if made:                       # second context: a slow build
            entered.set()
            release.wait(5)
        made.append(Context())
        return made[-1]

    pool = BrowserContextPool(factory)
    with pool.acquire() as first:      # seed: one context, built fast
        pass
    seeded = first
    with pool.acquire() as held:       # main holds the only context ...
        assert held is seeded
        builder_ctx = []
        builder = threading.Thread(
            target=lambda: builder_ctx.append(pool.acquire().__enter__()))
        builder.start()                # ... so the builder must call the factory
        assert entered.wait(5)         # and is now blocked inside it
    # main's release (above) and this re-acquire must NOT wait for the factory
    t0 = time.perf_counter()
    with pool.acquire() as again:
        assert again is seeded
    assert time.perf_counter() - t0 < 0.5
    assert not release.is_set()        # the factory is still blocked
    release.set()
    builder.join(5)
    assert builder_ctx and builder_ctx[0] is not seeded
    pool.close()


def test_request_level_state_is_reset_before_reuse():
    """Round 4 E1: extra HTTP headers (an auth token), granted permissions,
    route handlers and offline mode are not in storage_state(); scrub resets
    them so job 2 never sends job 1's X-Auth-Token."""
    made, pool = _pool()
    with pool.acquire() as first:
        first.set_extra_http_headers({"X-Auth-Token": "secret"})
        first.set_offline(True)
    assert first.extra_headers == {} and first.permissions == [] and first.routes == 0
    assert first.offline is False
    with pool.acquire() as second:
        assert second is first and second.extra_headers == {}


def test_tainted_context_is_closed_not_reused():
    """What scrub cannot undo (add_init_script, credentials) the job declares
    with taint(): the context is closed on release, never re-idled."""
    made, pool = _pool()
    with pool.acquire() as first:
        pool.taint(first)
    assert first.closed is True and _idle(pool) == []
    with pool.acquire() as second:
        assert second is not first
    assert len(made) == 2


def test_idle_contexts_are_never_handed_to_a_foreign_thread():
    """Round 4 E2: a sync-Playwright context belongs to the thread that made
    it; another thread gets its own, never the idle foreign one."""
    made, pool = _pool()
    with pool.acquire() as mine:
        pass
    got = []
    t = threading.Thread(target=lambda: got.append(pool.acquire().__enter__()))
    t.start(); t.join(5)
    assert got and got[0] is not mine and got[0].created_by != mine.created_by
    with pool.acquire() as again:
        assert again is mine                     # my own idle context, still there
    pool.close()


def test_real_chromium_auth_header_does_not_leak_between_jobs():
    """E1 on real Chromium: job 1 sets an auth header; job 2's requests on the
    reused context carry no such header (recorded by a loopback server)."""
    import http.server, socketserver
    from playwright.sync_api import sync_playwright
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            body = b"<html><body>ok</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        pool = BrowserContextPool(browser.new_context)
        with pool.acquire() as ctx:
            ctx.set_extra_http_headers({"X-Auth-Token": "secret"})
            ctx.new_page().goto(url)
        assert any(h.get("X-Auth-Token") == "secret" for h in seen)   # positive control
        seen.clear()
        with pool.acquire() as ctx2:
            assert ctx2 is ctx
            ctx2.new_page().goto(url)
        assert seen and all("X-Auth-Token" not in h for h in seen), seen
        pool.close()
        browser.close()
    finally:
        pw.stop()
        srv.shutdown(); srv.server_close()


def test_idle_contexts_of_a_dead_thread_are_reaped_on_the_next_acquire():
    """Round 5 E2: a worker that exits without release_thread() leaves idle
    contexts behind; the next acquire (any thread) drops and closes them."""
    made, pool = _pool()

    def job():
        with pool.acquire():
            pass
    t = threading.Thread(target=job); t.start(); t.join(5)
    dead = made[0]
    assert dead.closed is False and len(_idle(pool)) == 1
    with pool.acquire() as mine:
        assert mine is not dead
    assert dead.closed is True and _idle(pool) == [mine]
    pool.close()


def test_release_thread_closes_the_callers_idle_contexts_on_its_own_thread():
    made, pool = _pool()
    closed_on = []

    class Ctx(Context):
        def close(self):
            closed_on.append(threading.get_ident())
            super().close()

    pool = BrowserContextPool(lambda: made.append(Ctx()) or made[-1])
    ident = []

    def job():
        ident.append(threading.get_ident())
        with pool.acquire():
            pass
        pool.release_thread()

    t = threading.Thread(target=job); t.start(); t.join(5)
    assert made[0].closed is True and closed_on == ident and _idle(pool) == []


def test_real_chromium_contexts_are_reused_by_concurrent_worker_threads():
    """Round 5 E1 on real Chromium: PerThreadBrowser gives every worker thread
    its own Browser, so acquire()/new_page()/scrub run on the owning thread --
    no greenlet cross-thread switch. Each thread reuses ITS context across
    jobs; nothing leaks after release_thread()/close_thread()."""
    factory = PerThreadBrowser(lambda pw: pw.chromium.launch(headless=True))
    pool = BrowserContextPool(factory)
    results, errors = {}, []

    def worker(n):
        try:
            seen = []
            for job in range(3):
                with pool.acquire() as ctx:
                    seen.append(id(ctx))
                    page = ctx.new_page()
                    page.set_content(f"<title>w{n}-j{job}</title>")
                    assert page.title() == f"w{n}-j{job}"
                    assert ctx.pages == [page]
            results[n] = seen
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))
        finally:
            pool.release_thread()
            factory.close_thread()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [], errors
    assert len(results) == 2
    assert all(len(set(seen)) == 1 for seen in results.values())      # reused within a thread
    assert results[0][0] != results[1][0]                              # never shared across threads
    assert factory.open_threads() == set() and factory.leaked_threads() == set() and _idle(pool) == []
    assert pool.close() == 0 and pool.leaked == 0


def test_close_reports_the_contexts_it_could_not_close():
    """Round 6 E1: a context whose close() raises (a real one bound to
    another thread raises greenlet.error) is COUNTED, never silently passed
    over: close() returns the count and pool.leaked keeps it."""
    class Stuck(Context):
        def close(self):
            raise RuntimeError("cannot switch to a different thread")

    made = []
    pool = BrowserContextPool(lambda: made.append(Stuck()) or made[-1])
    with pool.acquire():
        pass
    assert pool.close() == 1 and pool.leaked == 1
    made2, pool2 = _pool()
    with pool2.acquire():
        pass
    assert pool2.close() == 0 and pool2.leaked == 0 and made2[0].closed is True   # positive control


def test_worker_scope_closes_the_threads_contexts_and_browser_on_exit():
    """Round 6 E1: pool.worker() is the worker thread's scope -- on exit its
    idle contexts are closed on their own thread and a PerThreadBrowser
    factory's close_thread() runs there too, even when the body raises."""
    closed_on, thread_closes = [], []

    class Ctx(Context):
        def close(self):
            closed_on.append(threading.get_ident())
            super().close()

    class Factory:
        def __call__(self):
            return Ctx()

        def close_thread(self):
            thread_closes.append(threading.get_ident())

    pool = BrowserContextPool(Factory())
    ident, seen = [], []

    def job():
        ident.append(threading.get_ident())
        try:
            with pool.worker():
                with pool.acquire() as ctx:
                    seen.append(ctx)
                raise KeyError("job failed after releasing")
        except KeyError:
            pass

    t = threading.Thread(target=job); t.start(); t.join(5)
    assert seen[0].closed is True and closed_on == ident and thread_closes == ident
    assert _idle(pool) == [] and pool.close() == 0


def test_real_chromium_pool_close_from_another_thread_cannot_close_a_workers_browser():
    """Round 6 E1 on real Chromium, both directions. A worker that runs inside
    pool.worker() leaves nothing: open_threads()/leaked_threads() empty and
    close() == 0. A worker that exits WITHOUT the scope leaves a browser no
    thread can close: the pool and factory MEASURE that (close() == 1,
    leaked_threads() names the thread) instead of pretending it closed."""
    factory = PerThreadBrowser(lambda pw: pw.chromium.launch(headless=True))
    pool = BrowserContextPool(factory)
    errors, idents = [], {}

    def scoped():
        idents["scoped"] = threading.get_ident()
        try:
            with pool.worker():
                with pool.acquire() as ctx:
                    page = ctx.new_page(); page.set_content("<title>scoped</title>")
                    assert page.title() == "scoped"
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    def unscoped():
        idents["unscoped"] = threading.get_ident()
        try:
            with pool.acquire() as ctx:
                ctx.new_page().set_content("<title>leak</title>")
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    t = threading.Thread(target=scoped); t.start(); t.join(60)
    assert errors == [], errors
    assert factory.open_threads() == set() and factory.leaked_threads() == set() and _idle(pool) == []

    t = threading.Thread(target=unscoped); t.start(); t.join(60)
    assert errors == [], errors
    assert len(_idle(pool)) == 1                       # its idle context is still registered
    assert factory.leaked_threads() == {idents["unscoped"]} and factory.open_threads() == set()
    assert pool.close() == 1 and pool.leaked == 1      # measured: this thread cannot close it
