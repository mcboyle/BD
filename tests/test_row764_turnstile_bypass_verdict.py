"""Row 764: a returned cookie cannot turn a visible challenge into success."""
import importlib.machinery
import sys
import types

from bulk_downloader import scrapling_adapter


BD_GATE_SCOPE = "repo-wide"


def _scrapling_with_fetch(fetch):
    module = types.ModuleType("scrapling")
    module.__spec__ = importlib.machinery.ModuleSpec("scrapling", loader=None)
    module.StealthyFetcher = type("StealthyFetcher", (), {"fetch": staticmethod(fetch)})
    return module


def test_still_challenged_response_with_one_cookie_is_a_failure_and_requests_solving(monkeypatch):
    captured = {}

    def fetch(**kwargs):
        captured.update(kwargs)
        # zero-entropy fixture cookie, not a credential
        return types.SimpleNamespace(
            html_content="<div class='cf-turnstile'></div>",
            cookies=[{"name": "SRV", "value": "0"}],
        )

    module = _scrapling_with_fetch(fetch)
    assert callable(module.StealthyFetcher.fetch), "precondition: fixture exposes fetch"
    monkeypatch.setitem(sys.modules, "scrapling", module)
    scrapling_adapter.reset_stats()

    result = scrapling_adapter.bypass_turnstile("https://fixture.test/challenge")

    assert result.ok is False
    assert len(result.cookies) == 0, "failure result never treats incidental cookie as success"
    assert captured["solve_cloudflare"] is True
    assert result.error == "bypass_completed_but_still_challenged"
    assert scrapling_adapter.stats()["turnstile_failed"] == 1


def test_cookie_on_a_cleared_page_still_succeeds(monkeypatch):
    def fetch(**_kwargs):
        # zero-entropy fixture cookie, not a credential
        return types.SimpleNamespace(html_content="<main>gallery</main>", cookies=[{"name": "SRV", "value": "0"}])

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    result = scrapling_adapter.bypass_turnstile("https://fixture.test/clear")

    assert result.ok is True
    assert len(result.cookies) == 1, "precondition: cleared response has exactly one cookie"


def test_typeerror_fetch_uses_the_legacy_positional_fallback(monkeypatch):
    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs:
            raise TypeError("fixture only accepts positional url")
        return types.SimpleNamespace(html_content="<main>gallery</main>", cookies=[])

    monkeypatch.setitem(sys.modules, "scrapling", _scrapling_with_fetch(fetch))
    result = scrapling_adapter.bypass_turnstile("https://fixture.test/legacy")

    assert len(calls) == 2, "precondition: kwargs attempt followed by positional fallback"
    assert calls[1][0] == ("https://fixture.test/legacy",)
    assert result.ok is True
