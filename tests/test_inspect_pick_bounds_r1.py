"""page-bridge inspect-pick-bounds r1: F1 F2 F3 F4 F5 F9.

FINDINGS (O1789 bughunt page-bridge, BASE 7927017d):

  * F1 -- the picker cuts outerHTML at 600 chars, so a long attribute value
    arrives with no closing quote; ``_ATTR_RE`` needed the quote, so
    ``redact_excerpt`` passed the value (a hidden-input token) through.
  * F2 -- ``correlate_timeline`` re-ran ``media_attribution`` over the whole
    network log for every pick, every pump tick (300 picks x 50k requests:
    77.8 s), and class tokens were unbounded (4 x 1 MB: 47.6 s).
  * F3 -- one malformed descriptor (``classes: 5``, ``attrs: [...]``) raised
    and blanked the timeline for every pick; ``verify_summary`` raised on
    ``effect: 5``.
  * F4 -- a ``data_attrs`` KEY went into the selector unchecked
    (``[data-q"],body,a[href^="http="hd"]``).
  * F5 -- ``el.dataset`` keys are camelCase, so ``data-video-quality`` became
    the dead selector ``[data-videoQuality="hd"]``.
  * F9 -- every page's HUD (readable by the page's own scripts) listed the
    clicks made in every other tab/origin.

RED on BASE, GREEN after the fix.  Controls pin what must keep working.
"""

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import io
import random
import re
import signal
import sys

from bulk_downloader import inspect_pick as ip


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a, **k):
    def _boom(*_):
        raise _Timeout
    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a, **k)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _pick(classes, ts=1000, **extra):
    d = {"tag": "button", "classes": list(classes)}
    d.update(extra)
    return {"descriptor": d, "ts": ts}


# ── F1: an attribute value cut off by the 600-char slice is still redacted ──

_TOKEN = "fake-fixture-csrf-" + "Q" * 900   # not a credential (gitleaks r2)


def _form(token, q='"'):
    return (f'<form class="login"><input type="hidden" name="csrf" '
            f'value={q}{token}{q}><button>Go</button></form>')


def test_f1_truncated_attr_value_is_redacted():
    for q in ('"', "'"):
        sliced = _form(_TOKEN, q)[:600]            # what the picker sends
        ex = ip.redact_excerpt(sliced)
        assert "QQQQQQQQ" not in ex and "fake-fixture" not in ex, (
            f"PB_F1_TRUNCATED_ATTR_VALUE_LEAKS: quote {q} excerpt {ex[:120]!r}")
    props = ('<div class="player" data-props=\'{"token":"' + "Z" * 900 + '"}\'>')[:600]
    assert "ZZZZZZZZ" not in ip.redact_excerpt(props), "PB_F1_TRUNCATED_ATTR_VALUE_LEAKS: data-props"
    tl = ip.correlate_timeline([_pick(["dl-btn"], outer_html=_form(_TOKEN)[:600])], [])
    assert "QQQQQQQQ" not in tl[0]["excerpt"], "PB_F1_TRUNCATED_ATTR_VALUE_LEAKS: timeline excerpt"


def test_f1_control_untruncated_excerpt_unchanged():
    assert ip.redact_excerpt(_form(_TOKEN), max_len=4000) == (
        f'<form class="login"><input type="hidden" name="csrf" '
        f'value="{ip.PLACEHOLDER}"><button>Go</button></form>')
    assert ip.redact_excerpt('<a class="x" href="https://h.test/p?sig=abc">go</a>') == \
        '<a class="x" href="' + ip.redact_query("https://h.test/p?sig=abc") + '">go</a>'


_OLD_ATTR_RE = re.compile(r"""(\s)([:\w-]+)\s*=\s*(["'])(.*?)\3""", re.S)


def test_f1_parity_well_formed_tags_match_the_old_regex():
    rng = random.Random(1789)
    chars = "ab =<>/:.-_\n\t" + "'\""
    tag = lambda m: f"[{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}]"
    for _ in range(4000):
        parts = []
        for _ in range(rng.randint(0, 6)):
            q = rng.choice("\"'")
            val = "".join(rng.choice(chars.replace(q, "")) for _ in range(rng.randint(0, 12)))
            parts.append(f"{rng.choice([' ', chr(10), '  '])}{rng.choice(['a', 'data-x', 'x:y'])}"
                         f"{rng.choice(['=', ' = '])}{q}{val}{q}")
        text = "".join(rng.choice("ab <>/") for _ in range(rng.randint(0, 6)))
        html = "<p" + "".join(parts) + ">" + text + "</p>"
        assert ip._ATTR_RE.sub(tag, html) == _OLD_ATTR_RE.sub(tag, html), html


# ── F2: correlation is bounded per tick ─────────────────────────────────────

def test_f2_300_picks_x_50k_requests_is_one_pass():
    picks = [_pick([f"btn{i % 7}"], ts=0) for i in range(300)]
    nl = [{"url": f"https://x.test/f{i}", "timestamp": 10_000 + i, "type": "Fetch",
           "status": 200} for i in range(50_000)]
    try:
        tl = _run_under(10.0, ip.correlate_timeline, picks, nl)
    except _Timeout:
        raise AssertionError("PB_F2_CORRELATE_PICKS_X_LOG: 300 picks x 50k requests "
                             "took > 10 s for one pump tick")
    assert len(tl) == 300


def test_f2_megabyte_class_tokens_are_bounded():
    picks = [_pick(["dl-btn"] + ["x" + "a" * (1 << 20) + str(k) for k in range(4)])]
    try:
        tl = _run_under(5.0, ip.correlate_timeline, picks, [])
    except _Timeout:
        raise AssertionError("PB_F2_UNBOUNDED_CLASS_TOKEN: 4 x 1 MB class tokens "
                             "took > 5 s for one pump tick")
    assert tl[0]["selector"] == "button.dl-btn"


def test_f2_pick_count_is_capped_to_the_newest():
    picks = [_pick([f"p{i}"], ts=1000 + i) for i in range(1000)]
    tl = ip.correlate_timeline(picks, [])
    assert len(tl) == 300, f"PB_F2_PICK_COUNT_UNCAPPED: {len(tl)} entries from 1000 picks"
    assert tl[0]["selector"] == "button.p700" and tl[-1]["selector"] == "button.p999"


def _base_correlate(picks, network_log, window_ms):
    """BASE 7927017d correlate_timeline, verbatim apart from names."""
    seq = [p for p in (picks or []) if isinstance(p, dict)]
    seq = sorted(seq, key=lambda p: p.get("ts") if isinstance(p.get("ts"), int) else 0)
    cts = [p.get("ts") for p in seq]
    nl = [e for e in (network_log or [])
          if isinstance(e, dict) and isinstance(e.get("timestamp"), int)]
    buckets = [[] for _ in seq]
    for e in nl:
        ts = e["timestamp"]
        for i in range(len(seq) - 1, -1, -1):
            ci = cts[i]
            if isinstance(ci, int) and ci < ts <= ci + window_ms:
                buckets[i].append(e)
                break
    out = []
    for i, p in enumerate(seq):
        entry = ip._entry_from(p.get("descriptor") or {}, p.get("ts"), ip._effects_from(buckets[i]))
        entry["effect"].update(ip.media_attribution(nl, p.get("ts"), window_ms))
        out.append(entry)
    return out


def test_f2_parity_with_the_base_correlation():
    rng = random.Random(65)
    urls = ["https://c.test/s/seg1.ts", "https://c.test/s/seg2.ts", "https://c.test/m/a.mp4",
            "https://c.test/m/b.mp4?x=1", "https://c.test/api/x", "https://d.test/v.m3u8",
            "https://d.test/page"]
    tss = lambda: rng.choice([rng.randint(0, 40)] * 6 + [None, "7", 3.5, True, False])
    for _ in range(3000):
        picks = [_pick([rng.choice(["a", "b", "play"])], ts=tss()) for _ in range(rng.randint(0, 8))]
        nl = [{"url": rng.choice(urls), "timestamp": tss(),
               "type": rng.choice(["Media", "Fetch", "Document"])}
              for _ in range(rng.randint(0, 25))]
        w = rng.choice([0, 3, 10, 50])
        assert ip.correlate_timeline(picks, nl, w) == _base_correlate(picks, nl, w), (picks, nl, w)


# ── F3: a malformed pick is skipped, the rest are kept ─────────────────────

def test_f3_malformed_pick_skipped_rest_kept():
    bad = [{"descriptor": {"tag": "button", "classes": 5}, "ts": 1},
           _pick(["x"], data_attrs=[1]), _pick(["x"], ancestors=7),
           _pick(["x"], attrs=["href"]), _pick(["x"], text=5), _pick(["x"], tag=["a"]),
           {"descriptor": "button", "ts": 1}, {"descriptor": 7, "ts": 1}]
    for b in bad:
        try:
            tl = ip.correlate_timeline([_pick(["dl-btn"]), b], [])
        except Exception as e:
            raise AssertionError(f"PB_F3_BAD_PICK_POISONS_TIMELINE: {b['descriptor']!r} -> "
                                 f"{type(e).__name__}: {e}")
        assert [e["selector"] for e in tl] == ["button.dl-btn"], (b, tl)


def test_f3_verify_summary_tolerates_hostile_entries():
    tl = [{"effect": 5}, 1, None, "x", {"selector": 3, "effect": {"req_count": "x"}}]
    try:
        v = ip.verify_summary(tl, {"network_log": []})
    except Exception as e:
        raise AssertionError(f"PB_F3_VERIFY_RAISES: {type(e).__name__}: {e}")
    assert v["gap_count"] == 0 and v["action_count"] == 5


def test_f3_control_good_pick_and_its_fields_kept():
    tl = ip.correlate_timeline([_pick(["dl-btn"], text="Download", attrs={"download": ""},
                                      ancestors=[{"tag": "div", "id": "player"}], nth=2)], [])
    assert len(tl) == 1 and tl[0]["role"] == "download link"
    assert tl[0]["selector"] == "button.dl-btn"
    assert ip.correlate_timeline([{"descriptor": {"tag": "a"}, "ts": 5}], [])[0]["selector"] == "a"
    assert ip.correlate_timeline([{"descriptor": {"tag": "a", "nth": 3,
                                                  "ancestors": [{"tag": "nav", "id": "top"}]},
                                   "ts": 5}], [])[0]["selector"] == "#top > a:nth-child(3)"


# ── F4 / F5: the data-* qualifier is a valid, live attribute selector ──────

def _sel(data):
    return ip.build_selector({"tag": "button", "classes": ["dl-btn"], "data_attrs": data})


def test_f4_hostile_data_key_never_reaches_the_selector():
    for key in ('q"],body,a[href^="http', "a b", "x]", "q\n", "-x"):
        sel = _sel({key: "hd"})
        assert sel == "button.dl-btn", f"PB_F4_DATA_KEY_UNCHECKED: {key!r} -> {sel!r}"
    assert _sel({'q"],x': "hd", "quality": "hd"}) == 'button.dl-btn[data-quality="hd"]'


def test_f5_camelcase_dataset_key_is_kebab_cased():
    sel = _sel({"videoQuality": "hd"})
    assert sel == 'button.dl-btn[data-video-quality="hd"]', f"PB_F5_DATASET_KEY_CAMELCASE: {sel!r}"
    assert _sel({"aBC": "1"}) == 'button.dl-btn[data-a-b-c="1"]'


def test_f4_f5_control_plain_keys_unchanged():
    assert _sel({"quality": "1080"}) == 'button.dl-btn[data-quality="1080"]'
    assert _sel({"id": "https://x.test/a"}) == "button.dl-btn"       # value still checked
    assert _sel({}) == "button.dl-btn"


# ── F9: a page's HUD lists only its own origin's clicks ────────────────────

class _Page:
    def __init__(self, url):
        self.url = url
        self.binding = None
        self.buffer = []
        self.hud = []                       # every HUD script evaluated here

    def expose_binding(self, name, cb):
        self.binding = cb

    def add_init_script(self, js):
        pass

    def evaluate(self, js, *a):
        if "window.__bd_picks=[];return a;" in js:
            out, self.buffer = self.buffer, []
            return out
        if "data-bd-hud" in js:
            self.hud.append(js)
        return None


def _drive_capture(monkeypatch, tmp_path, pages, on_finish):
    from bulk_downloader import cloak, dom_recorder
    from tools import capture_session as cs

    class _Ctx:
        def new_page(self):
            return pages[0]

        def close(self):
            pass

    class _Browser:
        def new_context(self, **kw):
            return _Ctx()

        def close(self):
            pass

    def _finish(args, start_url, raw_mode, on_tick=None):
        on_finish(on_tick)
        return True                         # CANCEL: no WACZ is written

    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (_Browser(), None, "fake"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **k: None)
    monkeypatch.setattr(dom_recorder, "arm_dom_recorder", lambda page: True)
    monkeypatch.setattr(dom_recorder, "drain_dom_events", lambda page, cap: 0)
    monkeypatch.setattr(cs, "_attach_recorders", lambda ctx, page, cap, redact=True: list(pages))
    monkeypatch.setattr(cs, "_hud_enabled", lambda args, env=None: True)
    monkeypatch.setattr(cs, "_emit_capture_event", lambda *a: None)
    monkeypatch.setattr(cs, "_goto_or_continue_if_usable", lambda *a, **k: True)
    monkeypatch.setattr(cs, "_settle_challenge_handoff", lambda page: None)
    monkeypatch.setattr(cs, "_poll_challenge_resume", lambda *a: None)
    monkeypatch.setattr(cs, "_maybe_recover", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_await_noninteractive_finish", _finish)
    monkeypatch.setattr(cs.sys, "stdin", io.StringIO())
    assert cs.run(["--url", pages[0].url, "--out", str(tmp_path / "cap.wacz")]) == 0


def test_f9_hud_shows_only_its_own_origins_clicks(monkeypatch, tmp_path):
    a, b = _Page("https://a.test/watch/1"), _Page("https://b.test/login")

    def _operator_clicks(tick):
        a.binding({"page": a, "frame": a}, {"d": {"tag": "button", "classes": ["only-on-a"]},
                                            "ts": 1000})
        b.buffer.append({"d": {"tag": "button", "classes": ["only-on-b"]}, "ts": 1001})
        tick()
        tick()

    _drive_capture(monkeypatch, tmp_path, [a, b], _operator_clicks)
    assert a.hud and b.hud
    # Control: each page still shows its own click (the probe can say yes).
    assert "only-on-a" in a.hud[-1] and "only-on-b" in b.hud[-1]
    assert "only-on-b" not in a.hud[-1], "PB_F9_HUD_CROSS_ORIGIN_ACTIONS: a.test HUD shows b.test's click"
    assert "only-on-a" not in b.hud[-1], "PB_F9_HUD_CROSS_ORIGIN_ACTIONS: b.test HUD shows a.test's click"


def test_f9_control_same_origin_tabs_share_and_operator_state_is_whole(monkeypatch, tmp_path):
    from bulk_downloader import element_pick
    states = []
    monkeypatch.setattr(element_pick, "write_inspect_state",
                        lambda out_dir, st: states.append(st))
    a, a2 = _Page("https://a.test/watch/1"), _Page("https://a.test:443/other")

    def _operator_clicks(tick):
        a.binding({"page": a}, {"d": {"tag": "button", "classes": ["tab-one"]}, "ts": 1000})
        a2.buffer.append({"d": {"tag": "button", "classes": ["tab-two"]}, "ts": 1001})
        tick()
        tick()

    _drive_capture(monkeypatch, tmp_path, [a, a2], _operator_clicks)
    for pg in (a, a2):
        assert "tab-one" in pg.hud[-1] and "tab-two" in pg.hud[-1]
    assert [e["selector"] for e in states[-1]["actions"]] == ["button.tab-one", "button.tab-two"]


def test_f9_control_verify_with_a_shared_report_is_unchanged():
    from bulk_downloader.netlog_classify import classify_network_log
    nl = [{"url": "https://c.test/s/seg1.ts", "timestamp": 1500, "type": "Media"},
          {"url": "https://c.test/api", "timestamp": 9000, "type": "Fetch"}]
    tl = ip.correlate_timeline([_pick(["play"], ts=1000), _pick(["menu"], ts=5000)], nl)
    cap = {"network_log": nl}
    whole = ip.verify_summary(tl, cap)
    assert whole["action_count"] == 2 and "play captured" in whole["checks"]
    # BASE has no report= (it re-classifies per call); the fix must give the same answer with a shared one
    import inspect as _insp
    if "report" in _insp.signature(ip.verify_summary).parameters:
        assert ip.verify_summary(tl, cap, report=classify_network_log(cap)) == whole
