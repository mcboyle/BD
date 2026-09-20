"""Reusable, scrubbed browser-context pool (row 924).

A context is re-idled ONLY when it is proven clean against Playwright's real
surface: ``clear_cookies()`` then ``storage_state()`` reporting no cookies
and no origins (local storage lives per origin inside the context and
Playwright has no ``clear_storage`` -- a context still holding origin
storage is CLOSED, never reused; fail closed, FLEET_RULE 46). A scrub that
raises, or a context released after ``close()``, is closed as well.
A sync-Playwright Browser and its contexts can only be closed on the thread
that launched them; a worker thread therefore runs inside ``pool.worker()``,
which closes that thread's idle contexts and browser on exit. ``close()``
closes what the calling thread owns and REPORTS what it cannot close (the
count of contexts bound to other threads) instead of leaking silently.
"""
from contextlib import contextmanager
import threading


def _close(context) -> bool:
    """True when the context is closed; False when it could not be (a
    context bound to another -- possibly exited -- thread raises
    ``greenlet.error`` here and stays open: the caller counts the leak)."""
    close = getattr(context, "close", None)
    if not callable(close):
        return False
    try:
        close()
    except Exception:
        return False
    return True


def _reset_request_state(context) -> None:
    """Undo the per-job request-level settings Playwright lets a job change
    after creation and does not report in storage_state(): extra HTTP
    headers (an auth token set by job 1 would ride on every request of
    job 2), granted permissions, route handlers, offline mode. Anything a
    job can only set at creation (http_credentials, proxy) is the factory's
    and is identical for every job; anything a job can add but never remove
    (add_init_script) is what ``taint()`` is for."""
    for name, args in (("set_extra_http_headers", ({},)), ("clear_permissions", ()),
                       ("unroute_all", ()), ("set_offline", (False,))):
        method = getattr(context, name, None)
        if callable(method):
            method(*args)


def scrub(context) -> bool:
    """Close every page, reset request state, clear cookies and prove the
    context clean. True only when no page remains open and
    ``storage_state()`` reports no cookies and no origins afterwards (a page
    left open carries its in-memory JS state -- window globals, timers --
    into the next job)."""
    clear_cookies = getattr(context, "clear_cookies", None)
    storage_state = getattr(context, "storage_state", None)
    if not callable(clear_cookies) or not callable(storage_state):
        return False
    pages = getattr(context, "pages", None)
    if pages is None:
        return False
    for page in list(pages):
        page.close()
    if list(getattr(context, "pages", None) or []):
        return False
    _reset_request_state(context)
    clear_cookies()
    state = storage_state() or {}
    return not state.get("cookies") and not state.get("origins")


class PerThreadBrowser:
    """A context factory for the pool under sync Playwright's rule that a
    Browser -- not just a context -- is greenlet-bound to the thread that
    launched it (``browser.new_context()`` from another thread crashes with
    ``greenlet.error: Cannot switch to a different thread``). Each worker
    thread gets its own Playwright + Browser, launched lazily on first
    ``__call__`` on that thread; ``close_thread()`` (call it on the worker
    thread before it exits) closes that thread's browser and Playwright.
    ``launch(playwright)`` returns the Browser (e.g.
    ``lambda pw: pw.chromium.launch(headless=True)``). A thread that exits
    without ``close_thread()`` leaves a browser NO thread can close any
    more; ``leaked_threads()`` names those so the leak is measured, never
    silent."""

    def __init__(self, launch, **context_kwargs):
        self._launch, self._kwargs = launch, context_kwargs
        self._local = threading.local()
        self._lock = threading.Lock()
        self._threads = set()   # idents of threads that hold a browser

    def __call__(self):
        if getattr(self._local, "browser", None) is None:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            try:
                browser = self._launch(pw)
            except Exception:
                pw.stop()
                raise
            self._local.pw, self._local.browser = pw, browser
            with self._lock:
                self._threads.add(threading.get_ident())
        return self._local.browser.new_context(**self._kwargs)

    def close_thread(self) -> None:
        """Close THIS thread's browser and Playwright (idempotent)."""
        browser, pw = getattr(self._local, "browser", None), getattr(self._local, "pw", None)
        self._local.browser = self._local.pw = None
        for closer in ((browser.close if browser is not None else None), (pw.stop if pw is not None else None)):
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
        with self._lock:
            self._threads.discard(threading.get_ident())

    @contextmanager
    def worker(self):
        """Scope for a worker thread's body: closes this thread's browser on
        exit, whatever the body did."""
        try:
            yield self
        finally:
            self.close_thread()

    def open_threads(self) -> set:
        """Idents of LIVE threads still holding a browser."""
        alive = {t.ident for t in threading.enumerate()}
        with self._lock:
            return {ident for ident in self._threads if ident in alive}

    def leaked_threads(self) -> set:
        """Idents of threads that exited without close_thread(): their
        browsers are unreachable (cross-thread close is impossible)."""
        alive = {t.ident for t in threading.enumerate()}
        with self._lock:
            return {ident for ident in self._threads if ident not in alive}


class BrowserContextPool:
    """Idle contexts are keyed by the thread that created them: a sync
    Playwright context is bound to its creating thread (a foreign thread
    crashes with ``greenlet.error: Cannot switch to a different thread``), so
    a worker only ever receives a context its own thread built; the factory
    is called on the acquiring thread (use ``PerThreadBrowser`` with real
    Playwright: the Browser itself is thread-bound too).

    A worker thread runs its body inside ``worker()``: on exit its idle
    contexts (and, with a ``PerThreadBrowser`` factory, its browser) are
    closed on their own thread. Idle contexts of a thread that died without
    doing so are reaped on the next acquire/close: dropped from the pool and
    closed if the runtime allows it -- with real Playwright a cross-thread
    close cannot succeed, so the reap counts them in ``leaked`` instead of
    pretending; ``worker()`` is what keeps that count at zero."""

    def __init__(self, factory):
        self._factory, self._lock = factory, threading.Lock()
        self._idle = {}          # thread ident -> [context, ...]
        self._owner = {}         # id(context) -> thread ident
        self._busy = set()
        self._tainted = set()    # id(context): close on release, never re-idle
        self._closed = False
        self.leaked = 0          # contexts no thread could close (measured, never silent)

    def _drop(self, contexts) -> int:
        """Close contexts outside the lock; count the ones that stay open."""
        failed = sum(0 if _close(c) else 1 for c in contexts)
        if failed:
            with self._lock:
                self.leaked += failed
        return failed

    @contextmanager
    def worker(self):
        """Scope for a worker thread's body: on exit the thread's idle
        contexts are closed here, on their own thread, and a factory with a
        ``close_thread()`` (PerThreadBrowser) closes this thread's browser."""
        try:
            yield self
        finally:
            self.release_thread()
            close_thread = getattr(self._factory, "close_thread", None)
            if callable(close_thread):
                close_thread()

    def taint(self, context) -> None:
        """Mark a checked-out context as not reusable (a job installed an
        init script, credentials or anything else scrub() cannot undo)."""
        with self._lock:
            self._tainted.add(id(context))

    def _reap_dead_threads_locked(self) -> list:
        """Idle contexts whose owner thread is gone (caller holds the lock)."""
        alive = {t.ident for t in threading.enumerate()}
        dead = [ident for ident in self._idle if ident not in alive]
        reaped = []
        for ident in dead:
            reaped.extend(self._idle.pop(ident))
        for context in reaped:
            self._owner.pop(id(context), None)
        return reaped

    def release_thread(self) -> None:
        """Close and forget the calling thread's idle contexts (call on a
        worker thread before it exits)."""
        with self._lock:
            mine = self._idle.pop(threading.get_ident(), [])
            for context in mine:
                self._owner.pop(id(context), None)
        self._drop(mine)

    @contextmanager
    def acquire(self):
        me = threading.get_ident()
        with self._lock:
            if self._closed:
                raise RuntimeError("BrowserContextPool is closed")
            reaped = self._reap_dead_threads_locked()
            mine = self._idle.get(me)
            context = mine.pop() if mine else None
            if context is not None:
                self._busy.add(context)
        self._drop(reaped)
        if context is None:
            # the factory (a Chromium IPC round-trip) runs OUTSIDE the lock so
            # other workers keep acquiring/releasing idle contexts meanwhile
            context = self._factory()
            with self._lock:
                if self._closed:
                    _close(context)
                    raise RuntimeError("BrowserContextPool is closed")
                self._busy.add(context)
                self._owner[id(context)] = me
        clean = False
        try:
            yield context
        finally:
            try:
                clean = scrub(context)
            except Exception:
                clean = False
            with self._lock:
                self._busy.discard(context)
                tainted = id(context) in self._tainted
                reusable = clean and not tainted and not self._closed
                if reusable:
                    self._idle.setdefault(me, []).append(context)
                else:
                    self._owner.pop(id(context), None)
                    self._tainted.discard(id(context))
            if not reusable:
                _close(context)

    def close(self) -> int:
        """Stop handing out contexts and close every context the pool holds,
        idle or checked out. Returns the number that could NOT be closed and
        adds it to ``leaked``: 0 is the clean shutdown. With real Playwright a
        context bound to another thread cannot be closed from here -- the
        clean shutdown is every worker's ``worker()`` scope having exited
        before close(); a context still checked out by a live worker is
        closed by that worker on release (``_closed`` makes it non-reusable)
        even when this call could not."""
        with self._lock:
            self._closed = True
            contexts = [c for lst in self._idle.values() for c in lst] + list(self._busy)
            self._idle, self._busy, self._owner, self._tainted = {}, set(), {}, set()
        return self._drop(contexts)
