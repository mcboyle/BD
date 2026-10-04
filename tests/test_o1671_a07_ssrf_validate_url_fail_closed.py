import logging
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from bulk_downloader import hooks, media_server_sync as media

BD_GATE_SCOPE = "module"
PUBLIC = "http://93.184.216.34/library"
BLOCKED = "http://169.254.169.254/latest/meta-data/"


@pytest.mark.parametrize("error", [ImportError, AttributeError, ValueError])
def test_validator_fault_blocks_and_logs_once(monkeypatch, caplog, error):
    def unavailable(url):
        raise error("fixture validator fault")

    monkeypatch.setattr(hooks, "_validate_webhook_url", unavailable)
    with caplog.at_level(logging.WARNING, logger=media.__name__):
        ok, why = media._validate_url(PUBLIC)
    assert ok is False, "SSRF-VALIDATOR-FAULT-ALLOWED"
    assert error.__name__ in why, "SSRF-FAULT-DIAGNOSTIC-MISSING"
    records = [r for r in caplog.records if r.name == media.__name__]
    assert len(records) == 1, "SSRF-FAULT-LOG-COUNT"
    assert error.__name__ in records[0].getMessage()


def test_missing_validator_attribute_blocks(monkeypatch, caplog):
    monkeypatch.delattr(hooks, "_validate_webhook_url")
    assert not hasattr(hooks, "_validate_webhook_url")
    with caplog.at_level(logging.WARNING, logger=media.__name__):
        ok, why = media._validate_url(PUBLIC)
    assert ok is False, "SSRF-MISSING-VALIDATOR-ALLOWED"
    assert "ImportError" in why
    assert len([r for r in caplog.records if r.name == media.__name__]) == 1


@pytest.mark.parametrize("url", [PUBLIC, "http://127.0.0.1/library", "http://10.0.0.2/library"])
def test_existing_allowed_policy_is_preserved(url):
    assert hooks._validate_webhook_url(url) == (True, url)
    assert media._validate_url(url) == (True, url)


@pytest.fixture
def fallback(monkeypatch):
    monkeypatch.delattr(hooks, "_hook_urlopen")
    assert not hasattr(hooks, "_hook_urlopen")
    state = {"built": 0, "fetched": [], "chain": [], "result": object()}

    def build(handler):
        state["built"] += 1
        assert isinstance(handler, urllib.request.HTTPRedirectHandler)

        class Opener:
            def open(self, request, timeout):
                assert timeout == 3
                current = request
                state["fetched"].append(current.full_url)
                for target in state["chain"]:
                    current = handler.redirect_request(current, None, 302, "Found", {}, target)
                    assert current is not None
                    state["fetched"].append(current.full_url)
                return state["result"]

        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    return state


@pytest.mark.parametrize(
    "url", [BLOCKED, "http://100.64.0.1/", "http://[::ffff:169.254.169.254]/"],
    ids=["metadata", "cgnat", "mapped-metadata"],
)
def test_fallback_refuses_initial_url_before_fetch(fallback, url):
    assert hooks._validate_webhook_url(url)[0] is False
    fallback["chain"] = [PUBLIC]
    with pytest.raises(urllib.error.HTTPError, match="blocked"):
        media._open(urllib.request.Request(url), 3)
    assert fallback["fetched"] == [], "SSRF-FALLBACK-FIRST-HOP-FETCHED"
    assert fallback["built"] == 0, "SSRF-BLOCKED-TRANSPORT-BUILT"


def test_fallback_refuses_blocked_redirect(fallback):
    fallback["chain"] = ["http://127.0.0.1/library", BLOCKED, PUBLIC]
    with pytest.raises(urllib.error.HTTPError, match="blocked redirect"):
        media._open(urllib.request.Request(PUBLIC), 3)
    assert fallback["fetched"] == [PUBLIC, "http://127.0.0.1/library"]
    assert fallback["built"] == 1


@pytest.mark.parametrize("error", [ImportError, AttributeError, ValueError])
def test_fallback_redirect_validator_fault_stops_fetch(monkeypatch, caplog, fallback, error):
    validator = hooks._validate_webhook_url
    assert validator(PUBLIC) == (True, PUBLIC)
    fault_on_redirect = Mock(side_effect=[validator(PUBLIC), error("fixture redirect fault")])
    monkeypatch.setattr(hooks, "_validate_webhook_url", fault_on_redirect)
    fallback["chain"] = ["http://127.0.0.1/library"]
    with caplog.at_level(logging.WARNING, logger=media.__name__):
        with pytest.raises(urllib.error.HTTPError, match=error.__name__):
            media._open(urllib.request.Request(PUBLIC), 3)
    calls = [call.args[0] for call in fault_on_redirect.call_args_list]
    assert calls == [PUBLIC, "http://127.0.0.1/library"], "SSRF-HOP-VALIDATION-MISSING"
    assert fallback["fetched"] == [PUBLIC], "SSRF-FAULT-REDIRECT-FETCHED"
    assert len([r for r in caplog.records if r.name == media.__name__]) == 1


def test_fallback_allowed_chain_checks_each_hop(monkeypatch, fallback):
    record = Mock(wraps=hooks._validate_webhook_url)
    monkeypatch.setattr(hooks, "_validate_webhook_url", record)
    fallback["chain"] = ["http://127.0.0.1/library", "http://10.0.0.2/library"]
    result = media._open(urllib.request.Request(PUBLIC), 3)
    expected = [PUBLIC, *fallback["chain"]]
    assert result is fallback["result"]
    checked = [call.args[0] for call in record.call_args_list]
    assert checked == expected, "SSRF-FIRST-HOP-NOT-VALIDATED"
    assert fallback["fetched"] == expected
    assert fallback["built"] == 1


def test_primary_pinned_opener_is_preserved(monkeypatch):
    request = urllib.request.Request(PUBLIC)
    result = object()
    pinned = Mock(return_value=result)
    monkeypatch.setattr(hooks, "_hook_urlopen", pinned)
    assert media._open(request, 3) is result
    pinned.assert_called_once_with(request, timeout=3)
