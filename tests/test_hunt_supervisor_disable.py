BD_GATE_SCOPE = "module"

"""Disabling a bandwidth cap must release work already waiting on it."""

import pytest

from bulk_downloader import download_supervisor as supervisor


@pytest.mark.parametrize("site_cap", [False, True])
def test_disable_releases_inflight_acquire(monkeypatch, site_cap):
    global_bps = 0 if site_cap else 1
    per_site_bps = {"site": 1} if site_cap else {}
    supervisor.reset()
    supervisor.configure(
        enabled=True, global_bps=global_bps, per_site_bps=per_site_bps
    )
    try:
        assert supervisor.acquire("site", 1) == 0.0
        sleeps = []

        def disable_on_wait(_seconds):
            sleeps.append(_seconds)
            if len(sleeps) > 1:
                raise AssertionError("in-flight acquire remained throttled")
            supervisor.configure(
                enabled=False, global_bps=global_bps, per_site_bps=per_site_bps
            )

        monkeypatch.setattr(supervisor.time, "sleep", disable_on_wait)
        supervisor.acquire("site", 1)
        assert len(sleeps) == 1
    finally:
        supervisor.reset()
