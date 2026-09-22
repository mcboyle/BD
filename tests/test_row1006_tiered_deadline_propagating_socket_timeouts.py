"""Row 1006: a socket wait must not outlive the run's wall budget.

THE DEFECT, MEASURED AT BASE bc1544b7. `run_budget.RunBudget.wall_s` is built
from the site config key `run_wall_budget_s` and has NO consumer anywhere under
bulk_downloader/ -- `is_over_mem_budget` is the only entry point anything calls.
Meanwhile every `httpx.Timeout(...)` in the tree is a fixed literal, so the
payload stream in `TransportMixin._do_direct_http_download` waits `read=60.0`
for one chunk no matter what the run was told its whole budget was. A run
configured to last five seconds can therefore block for sixty in a single
socket read, and nothing in the tree notices: the budget is configured, is
displayed, and is inert.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT. The subject is the TIERS handed
to the transport, captured at the real call boundary -- not wall-clock duration,
which would make this a timing test that fails on a slow runner. The tiers are
read off the `httpx.Timeout` the production code actually passes.

THE OVER-CORRECTION THIS GUARDS AGAINST is the obvious wrong fix: clamp every
request to some small constant. That passes the capped assertion and destroys
the uncapped path, which is most runs. `test_an_uncapped_run_keeps_its_generous
_tiers` fails on exactly that fix, so the pair is machine-checked rather than
argued.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import threading

import httpx
import pytest


class _Response:
    status_code = 200
    headers = {"content-length": "2"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, _chunk_size):
        yield b"ok"


def _runner(config):
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "row1006"
    runner.config = dict(config)
    runner._stop = threading.Event()
    runner._download_proxy_url = lambda: None
    runner._update_job = lambda *args, **kwargs: None
    return runner


def _captured_timeout(monkeypatch, tmp_path, config):
    """The httpx.Timeout the production download path actually hands httpx."""
    seen = []

    from bulk_downloader import ssrf_transport
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport",
        lambda _policy, *, proxy=None: httpx.HTTPTransport(),
    )

    def _stream(_self, *_args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return _Response()

    monkeypatch.setattr(httpx.Client, "stream", _stream)

    ok = _runner(config)._do_direct_http_download(
        "https://page.example/item", "https://cdn.example/video.mp4",
        str(tmp_path / "video.mp4"),
    )
    assert ok is True, "the harness did not reach the payload stream at all"
    assert seen, "no timeout was captured; the probe cannot say no about tiers"
    return seen[0]


def _tiers(timeout) -> dict:
    assert isinstance(timeout, httpx.Timeout), (
        f"the transport passed {type(timeout).__name__}, not an httpx.Timeout, "
        "so the tier assertions below would be vacuous")
    return {
        "connect": timeout.connect,
        "read": timeout.read,
        "write": timeout.write,
        "pool": timeout.pool,
    }


def test_the_probe_reads_real_tiers(monkeypatch, tmp_path):
    """Positive control. Before any zero is reported, the probe must be able to
    report a NUMBER for each tier off the untouched, uncapped path."""
    tiers = _tiers(_captured_timeout(monkeypatch, tmp_path, {}))
    assert set(tiers) == {"connect", "read", "write", "pool"}
    assert all(isinstance(v, float) and v > 0 for v in tiers.values()), tiers


def test_a_five_second_run_budget_bounds_every_socket_tier(monkeypatch, tmp_path):
    """The row. No single socket wait may exceed the whole run's wall budget."""
    budget = 5
    tiers = _tiers(_captured_timeout(
        monkeypatch, tmp_path, {"run_wall_budget_s": budget}))
    over = {k: v for k, v in tiers.items() if v > budget}
    assert not over, (
        f"run_wall_budget_s={budget}s, yet these socket tiers can outlive the "
        f"whole run: {over}. The budget is configured and inert.")


def test_an_uncapped_run_keeps_its_generous_tiers(monkeypatch, tmp_path):
    """Over-correction control. Clamping everyone to a small constant passes the
    assertion above and breaks every ordinary run; it fails here."""
    tiers = _tiers(_captured_timeout(monkeypatch, tmp_path, {}))
    assert tiers["read"] >= 60.0, (
        f"an uncapped run lost its generous read tier: {tiers}")
    assert tiers["connect"] >= 15.0, tiers


def test_a_zero_budget_is_uncapped_not_instantly_expired(monkeypatch, tmp_path):
    """0 means uncapped on every RunBudget field; a fix reading it as a deadline
    of zero would make every capped-looking run fail closed at once."""
    tiers = _tiers(_captured_timeout(
        monkeypatch, tmp_path, {"run_wall_budget_s": 0}))
    assert tiers["read"] >= 60.0, tiers


@pytest.mark.parametrize("budget", [1, 30, 120])
def test_the_clamp_tracks_the_budget_rather_than_a_constant(
        monkeypatch, tmp_path, budget):
    """A fix that hardcodes one number passes a single-value assertion. Every
    tier must be bounded by whatever budget it was actually given, and a budget
    wider than the literals must not shrink them."""
    tiers = _tiers(_captured_timeout(
        monkeypatch, tmp_path, {"run_wall_budget_s": budget}))
    assert all(v <= budget for v in tiers.values()), (budget, tiers)
    if budget >= 60:
        assert tiers["read"] >= 60.0, (budget, tiers)
