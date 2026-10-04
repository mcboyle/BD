"""O1826 c26: scrapling_adapter fails closed (M144, M145, M146)."""

import hashlib
import importlib.machinery
import sys
import threading
import time
import types

from bulk_downloader import scrapling_adapter


BD_GATE_SCOPE = "module"


def _scrapling_with_fetch(fetch):
    module = types.ModuleType("scrapling")
    module.__spec__ = importlib.machinery.ModuleSpec("scrapling", loader=None)
    module.StealthyFetcher = type("StealthyFetcher", (), {"fetch": staticmethod(fetch)})
    return module


class _UnparseablePage:
    html_content = "<main>gallery</main>"

    @property
    def cookies(self):
        raise RuntimeError("o1826 fixture: cookie jar unreadable")


# ─── M146: result parse failure is not a bypass ────────────────────


def test_m146_result_parse_failure_is_not_reported_as_bypassed(monkeypatch):
    def fetch(**_kwargs):
        return _UnparseablePage()

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    scrapling_adapter.reset_stats()

    result = scrapling_adapter.bypass_turnstile("https://fixture.test/unparseable")

    stats = scrapling_adapter.stats()
    assert result.ok is False, (
        f"M146: unparseable bypass result reported ok=True (error={result.error!r})"
    )
    assert result.error.startswith("bypass_result_parse_failed"), result.error
    assert stats["turnstile_bypassed"] == 0, (
        "M146: parse failure bumped turnstile_bypassed"
    )
    assert stats["turnstile_failed"] == 1


def test_m146_negative_control_parseable_result_still_succeeds(monkeypatch):
    def fetch(**_kwargs):
        # zero-entropy fixture cookie, not a credential
        return types.SimpleNamespace(
            html_content="<main>gallery</main>", cookies=[{"name": "SRV", "value": "0"}]
        )

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    scrapling_adapter.reset_stats()

    result = scrapling_adapter.bypass_turnstile("https://fixture.test/clear")

    assert result.ok is True
    assert len(result.cookies) == 1
    assert scrapling_adapter.stats()["turnstile_bypassed"] == 1


# ─── M145: legacy positional fallback keeps the hard time limit ────


def test_m145_legacy_fallback_is_bounded_by_timeout(monkeypatch):
    release = threading.Event()
    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs:
            raise TypeError("fixture only accepts positional url")
        release.wait(5.0)
        return types.SimpleNamespace(html_content="<main>gallery</main>", cookies=[])

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    scrapling_adapter.reset_stats()
    try:
        start = time.monotonic()
        result = scrapling_adapter.bypass_turnstile(
            "https://fixture.test/hang", timeout_s=0.2
        )
        elapsed = time.monotonic() - start
    finally:
        release.set()

    assert len(calls) == 2, (
        "precondition: kwargs attempt followed by positional fallback"
    )
    assert calls[1][0] == ("https://fixture.test/hang",)
    assert elapsed < 2.0, (
        f"M145: legacy fallback ran unbounded ({elapsed:.2f}s > timeout_s=0.2)"
    )
    assert result.ok is False
    assert result.error == "legacy_fetch_timeout"
    assert scrapling_adapter.stats()["turnstile_failed"] == 1
    assert scrapling_adapter.stats()["turnstile_bypassed"] == 0


def test_m145_legacy_fallback_error_is_a_failure(monkeypatch):
    def fetch(*_args, **kwargs):
        if kwargs:
            raise TypeError("fixture only accepts positional url")
        raise ConnectionError("o1826 fixture: legacy fetch failed")

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    scrapling_adapter.reset_stats()

    result = scrapling_adapter.bypass_turnstile(
        "https://fixture.test/legacy-error", timeout_s=2.0
    )

    assert result.ok is False
    assert result.error.startswith("ConnectionError:"), result.error
    assert scrapling_adapter.stats()["turnstile_failed"] == 1


# ─── M144: synthesized selectors are escaped and unique ────────────


def _fingerprint(tag, text):
    return {"tag": tag, "text_hash": hashlib.sha256(text.encode()).hexdigest()}


def _matches(html, selector):
    from scrapling import Selector

    return Selector(html).css(selector)


def test_m144_recovery_never_returns_a_non_unique_bare_tag():
    html = "<body><div>alpha</div><div>hi</div></body>"
    assert len(_matches(html, "div")) == 2, (
        "precondition: bare tag matches two elements"
    )

    recovered = scrapling_adapter.recover_selector(html, _fingerprint("div", "hi"))

    assert recovered.ok is False, (
        f"M144: recovery returned non-unique selector {recovered.selector!r}"
    )
    assert recovered.error == "failed_to_synthesize_new_selector"


def test_m144_recovery_skips_a_shared_first_class_for_a_unique_one():
    html = "<body><div class='x'>alpha</div><div class='x y'>hi</div></body>"
    assert len(_matches(html, "div.x")) == 2, "precondition: first class is shared"

    recovered = scrapling_adapter.recover_selector(html, _fingerprint("div", "hi"))

    assert recovered.ok is True
    assert recovered.selector == "div.y", (
        f"M144: unverified class selector {recovered.selector!r}"
    )
    assert [el.text for el in _matches(html, recovered.selector)] == ["hi"]


def test_m144_recovery_escapes_an_id_with_css_metacharacters():
    html = "<body><div id='a:b'>hi</div><div>alpha</div></body>"

    recovered = scrapling_adapter.recover_selector(html, _fingerprint("div", "hi"))

    assert recovered.ok is True
    assert recovered.selector != "#a:b", "M144: unescaped #id returned"
    assert [el.text for el in _matches(html, recovered.selector)] == ["hi"]


def test_m144_negative_control_normal_page_recovers_unique_id():
    html = "<body><div id='target'>hi</div><div>alpha</div></body>"

    recovered = scrapling_adapter.recover_selector(html, _fingerprint("div", "hi"))

    assert recovered.ok is True
    assert recovered.selector == "#target"
    assert [el.text for el in _matches(html, recovered.selector)] == ["hi"]


def test_m144_build_new_selector_returns_none_not_a_bare_tag():
    from scrapling import Selector

    html = "<body><div class='x'>alpha</div><div class='x'>hi</div></body>"
    page = Selector(html)
    target = page.css("div")[1]
    assert target.text == "hi", "precondition: fixture element located"

    assert scrapling_adapter._build_new_selector(target, "div", page) is None
    assert (
        scrapling_adapter._build_new_selector(types.SimpleNamespace(attrib={}), "div")
        is None
    )
