"""BH1-49 r4: credential characters are written in-realm, never keyed.

r3 SEC A1: keystrokes are routed by focus, and r3 checked focus and then
pressed the key, so page script running between the two (a parent
setInterval focusing a cross-origin frame, K1; the frame focusing its own
input, K3) received credential characters.  r3 SEC S1: the focus check
compared document.activeElement, which is the shadow HOST for a field in an
open shadow root, so such a login never completed.  r4 writes each
character with ONE evaluate on the bound handle (checks, native value
setter, InputEvent), so no key event exists for any document to receive.
Real-Chromium rows use local stub servers only (*.test mapped to
127.0.0.1) and a public fixture value.
"""
import ast
import inspect
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from bulk_downloader import synthetic_input_scheduler
from bulk_downloader.login_impl import _common, submit

BD_GATE_SCOPE = "module"
BOUND_FIELD_LOST = "credential entry aborted: bound field lost"  # the abort reason prefix
TYPED_FIXTURE = "fixture-0123"  # public, 12-character fixture
PAGES = {}
# The foreign frame records every key and input event it receives.
KEYLOG = ("<script>window.cap = []; for (const t of ['keydown', 'keypress', 'keyup', 'input'])"
          " addEventListener(t, e => cap.push(t), true)</script>")


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.partition("?")[0]
        body = PAGES.get((self.server.server_port, path), "<html><body>404</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class Site:
    def __init__(self, browser, a, b):
        self.browser = browser
        self.a, self.b = a, b
        self.login = f"http://www.site.test:{a}/login"

    def page(self, path, script="", frame=None, field=None):
        field = field or "<input id=secret type=password name=pw style='width:200px'>"
        iframe = (f"<iframe id=fr src='http://evil.test:{self.b}{frame}' width=300 height=80></iframe>"
                  if frame else "")
        PAGES[(self.a, path)] = (
            f"<html><body><form><input id=user name=user>{field}<button type=submit>go</button>"
            f"</form>{iframe}<script>{script}</script></body></html>")
        return self.login.replace("/login", path)


@pytest.fixture(scope="module")
def site():
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Stub) for _ in range(2)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    a, b = (s.server_port for s in servers)
    PAGES[(b, "/klframe")] = "<html><body><input id=evil type=password>" + KEYLOG + "</body></html>"
    PAGES[(b, "/klself")] = (
        "<html><body><input id=evil type=password>" + KEYLOG
        + "<script>setInterval(() => document.querySelector('#evil').focus(), 0)</script></body></html>")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True, args=["--host-resolver-rules=MAP *.test 127.0.0.1"])
        try:
            yield Site(browser, a, b)
        finally:
            browser.close()
    finally:
        pw.stop()
        for s in servers:
            s.shutdown()
            s.server_close()


def fill(site, url, sel="#secret", read="() => document.querySelector('#secret').value"):
    ctx = site.browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(url)
        frames = [f for f in page.frames if f.url.startswith(f"http://evil.test:{site.b}")]
        if frames:
            frames[0].locator("#evil").wait_for(timeout=5000)
        ok, why = _common._try_fill(
            page, [sel], TYPED_FIXTURE, "password",
            refusal=lambda: submit._prefill_origin_refusal(page, url, None))
        page.wait_for_timeout(300)
        seen = {"main": page.evaluate(read)}
        if frames:
            seen["frame_events"] = frames[0].evaluate("window.cap")
            seen["frame_value"] = frames[0].locator("#evil").input_value()
        return ok, why, seen
    finally:
        ctx.close()


def leaked(seen):
    return len(seen["frame_events"]) + len(seen["frame_value"])


def test_k1_parent_interval_focus_steal_sends_the_frame_nothing(site):
    url = site.page("/k1", "setInterval(() => document.querySelector('#fr').focus(), 0)", "/klframe")
    ok, why, seen = fill(site, url)
    assert leaked(seen) == 0, (
        f"BH1_49_R4_K1_LEAK: the cross-origin frame received {len(seen['frame_events'])} key/input "
        f"event(s) and holds {len(seen['frame_value'])} char(s) while the parent stole focus; {ok=} {why=}")
    assert (ok, why, seen["main"]) == (True, "#secret", TYPED_FIXTURE), (
        f"BH1_49_R4_K1_ENTRY: {ok=} {why=} main holds {len(seen['main'])} chars")


@pytest.mark.parametrize("run_", [1, 2, 3, 4, 5])
def test_k3_frame_self_focus_interval_sends_the_frame_nothing(site, run_):
    url = site.page("/k3", frame="/klself")
    ok, why, seen = fill(site, url)
    assert leaked(seen) == 0, (
        f"BH1_49_R4_K3_LEAK: run {run_}: the self-focusing cross-origin frame received "
        f"{len(seen['frame_events'])} key/input event(s), holds {len(seen['frame_value'])} char(s); "
        f"{ok=} {why=}")
    assert (ok, why, seen["main"]) == (True, "#secret", TYPED_FIXTURE), (
        f"BH1_49_R4_K3_ENTRY: run {run_}: {ok=} {why=} main holds {len(seen['main'])} chars")


def test_control_keylogger_frame_without_steal(site):
    ok, why, seen = fill(site, site.page("/k0", frame="/klframe"))
    assert leaked(seen) == 0 and (ok, why, seen["main"]) == (True, "#secret", TYPED_FIXTURE), (
        f"BH1_49_R4_K0_CONTROL: {ok=} {why=} {seen['frame_events']}")


def test_control_keylogger_frame_records_a_key_that_reaches_it(site):
    """Positive control: the frame's recorder does see keys typed into it."""
    ctx = site.browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(site.page("/kp", frame="/klframe"))
        frame = page.frame_locator("#fr")
        frame.locator("#evil").click()
        page.keyboard.type("ab")
        cap = [f for f in page.frames if f.url.startswith(f"http://evil.test:{site.b}")][0].evaluate(
            "window.cap")
        assert cap.count("keydown") == 2 and "input" in cap, f"BH1_49_R4_KEYLOG_BLIND: {cap}"
    finally:
        ctx.close()


SHADOW = ("const r = document.querySelector('#host').attachShadow({mode: 'open'});"
          " r.innerHTML = '<input type=password name=pw>';")
SHADOW_VALUE = "() => document.querySelector('#host').shadowRoot.querySelector('input').value"


@pytest.mark.parametrize("sel", ["input[type=password]", "form input[type='password']"])
def test_open_shadow_root_login_completes(site, sel):
    url = site.page(f"/s{len(sel)}", SHADOW, field="<div id=host></div>")
    ok, why, seen = fill(site, url, sel=sel, read=SHADOW_VALUE)
    assert (ok, why) == (True, sel), f"BH1_49_R4_SHADOW_ROOT: {ok=} {why=}"
    assert seen["main"] == TYPED_FIXTURE, (
        f"BH1_49_R4_SHADOW_VALUE: the shadow-root field holds {len(seen['main'])} chars")


EVENTS = ("window.ev = []; const s = document.querySelector('#secret');"
          " for (const t of ['keydown', 'keypress', 'keyup', 'beforeinput', 'input', 'change'])"
          " s.addEventListener(t, e => ev.push([t, e.inputType || '', e.data || '', s.value.length]));")


def test_one_input_event_per_character_then_change(site, monkeypatch):
    delays = []
    monkeypatch.setattr(synthetic_input_scheduler.BoundField, "_sleep",
                        staticmethod(lambda s: delays.append(s)), raising=False)
    ok, why, seen = fill(site, site.page("/ev", EVENTS), read="() => window.ev")
    # Clearing the empty field fires nothing (a Delete key would not either).
    want = [["input", "insertText", c, i + 1] for i, c in enumerate(TYPED_FIXTURE)] + [
        ["change", "", "", len(TYPED_FIXTURE)]]
    assert (ok, why) == (True, "#secret"), f"BH1_49_R4_EVENTS_ENTRY: {ok=} {why=}"
    assert seen["main"] == want, f"BH1_49_R4_EVENTS: {[e[:2] + e[3:] for e in seen['main']]}"
    assert len(delays) == len(TYPED_FIXTURE) and all(d > 0 for d in delays), (
        f"BH1_49_R4_DELAYS: scheduler delays not kept: {delays}")


def test_page_that_submits_on_the_complete_value_logs_in(site):
    # test_v3_66_1020: the page submits from its own input listener.  The
    # 'change' rides the last write (no write into a document that is going
    # away), and the scheduled delay follows each write where
    # keyboard.type(delay) held the key, as a page wait that takes the
    # page's events, so the caller sees the landed page (do_login's gate
    # check enumerated a dying document).  Read at once: no settle wait.
    auto = (f"document.querySelector('#secret').addEventListener('input', e => {{"
            f" if (e.target.value.length === {len(TYPED_FIXTURE)}) e.target.form.submit(); }});")
    url = site.page("/auto", auto)
    ctx = site.browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(url)
        ok, why = _common._try_fill(
            page, ["#secret"], TYPED_FIXTURE, "password",
            refusal=lambda: submit._prefill_origin_refusal(page, url, None))
        landed = page.url
    finally:
        ctx.close()
    assert (ok, why) == (True, "#secret"), f"BH1_49_R4_AUTO_SUBMIT: {ok=} {why=}"
    assert landed == f"{url}?user=&pw={TYPED_FIXTURE}", (
        f"BH1_49_R4_AUTO_SUBMIT_LANDED: the caller still saw {landed.partition('?')[0]}"
        f" with {len(landed.partition('?')[2])} chars of query")


def test_value_changed_under_the_entry_aborts_it(site):
    tamper = ("document.querySelector('#secret').addEventListener('input', e => {"
              " if (e.target.value.length === 3) e.target.value += 'x'; });")
    ok, why, seen = fill(site, site.page("/tamper", tamper))
    # The read-back after the tampered character aborts, not the next
    # character's precondition.
    assert ok is False and why == f"{BOUND_FIELD_LOST} (value: 4 characters after the write)", (
        f"BH1_49_R4_POST_ASSERT: {ok=} {why=}")
    assert len(seen["main"]) == 4, f"BH1_49_R4_AFTER_ABORT: field holds {len(seen['main'])} chars"


# --- no key, press or focus call reachable on the bound path ---

FORBIDDEN = {"keyboard", "press", "press_sequentially", "type", "focus", "fill", "dispatch_event",
             "evaluate_handle"}


def attribute_names(obj):
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def test_static_bound_field_has_no_key_press_or_focus_call():
    used = attribute_names(synthetic_input_scheduler.BoundField) & FORBIDDEN
    assert used == set(), f"BH1_49_R4_STATIC_BOUNDFIELD: BoundField calls {sorted(used)}"
    script = synthetic_input_scheduler.BoundField._WRITE
    assert "focus" not in script and "activeElement" not in script and "key" not in script.lower(), (
        "BH1_49_R4_STATIC_WRITE: the in-realm write depends on focus or keys")


def test_static_type_char_returns_before_the_keyboard_for_a_bound_field():
    body = ast.parse(textwrap.dedent(inspect.getsource(synthetic_input_scheduler.type_char))).body[0].body
    first_if = next(n for n in body if isinstance(n, ast.If))
    assert ast.unparse(first_if.test) == "field is not None", ast.unparse(first_if.test)
    assert isinstance(first_if.body[-1], ast.Return), "BH1_49_R4_STATIC_FALLTHROUGH"
    assert "keyboard" not in ast.unparse(first_if), (
        "BH1_49_R4_STATIC_TYPE_CHAR: the bound branch reaches page.keyboard")


def test_static_credential_plan_is_typing_only():
    actions = _common.get_input_scheduler().schedule_field_input(
        "#secret", TYPED_FIXTURE, clear_first=False, click_to_focus=False)
    kinds = {a.action_type for a in actions}
    assert kinds == {synthetic_input_scheduler.InputActionType.TYPE_CHAR}, (
        f"BH1_49_R4_STATIC_PLAN: the credential plan holds {kinds}")
    src = inspect.getsource(_common._type_field_value)
    assert "clear_first=False, click_to_focus=False" in src, "BH1_49_R4_STATIC_PLAN_ARGS"


def test_static_try_fill_has_no_key_or_focus_call():
    used = attribute_names(_common._try_fill) & (FORBIDDEN - {"fill"})
    used |= attribute_names(_common._type_field_value) & FORBIDDEN
    assert used == set(), f"BH1_49_R4_STATIC_TRY_FILL: {sorted(used)}"


class Forbidden:
    def __getattr__(self, name):
        raise AssertionError(f"BH1_49_R4_RUNTIME_KEY: page.keyboard.{name} on the bound path")


def test_runtime_bound_login_never_touches_the_keyboard(site, monkeypatch):
    """The real page with page.keyboard and the handle's key, press, type,
    focus and fill replaced by raisers: the entry still completes."""
    from playwright.sync_api import ElementHandle, Page

    monkeypatch.setattr(Page, "keyboard", property(lambda self: Forbidden()))
    for name in ("press", "type", "focus", "fill"):
        monkeypatch.setattr(ElementHandle, name, lambda self, *a, _n=name, **k: (_ for _ in ()).throw(
            AssertionError(f"BH1_49_R4_RUNTIME_HANDLE: ElementHandle.{_n} on the bound path")))
    ok, why, seen = fill(site, site.page("/rt"))
    assert (ok, why, seen["main"]) == (True, "#secret", TYPED_FIXTURE), (
        f"BH1_49_R4_RUNTIME: {ok=} {why=}")
