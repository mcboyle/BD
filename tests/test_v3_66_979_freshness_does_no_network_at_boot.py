"""The freshness probe must not reach the network, at boot or in a test.

@979, fixing a regression I shipped at @977. Two tests in
test_v3_66_661_healthcheck_ytdlp_shape failed ON THE BOX, and the message gave
the real problem away:

    assert '90' in 'yt-dlp 2025.01.01 is behind 2026.7.4 - update available'

That `2026.7.4` came from the LIVE PyPI index, during a unit test. @977 made
`_check_ytdlp()` call `latest_version()`, which fetched; every existing test that
mocked only `status_dict` silently got live data, and the whole suite acquired a
network dependency nobody asked for.

MEASURED, and it is the part that should have stopped me: no other probe in
healthcheck.py touches the network. ffmpeg, chromium, loopback and disk are all
local. The boot selftest was network-free by design and I broke that invariant
without noticing, because I derived the band with
`ls tests/ | grep ... | head -8` and the one test file named for the function I
was changing sorts NINTH. A truncated denominator, in a band derivation, in a
session about denominators.

The fix: `latest_version()` never fetches unless explicitly asked, never fetches
in BD_TEST_MODE at all, and persists what it learns so a value fetched by the
update path is still there at the next boot. The probe reads cache-only.
"""

import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _fresh(monkeypatch, tmp_path):
    import importlib
    from bulk_downloader import ytdlp_updater as yt
    importlib.reload(yt)
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    return yt


def test_the_default_call_does_NOT_fetch(monkeypatch, tmp_path):
    """THE REGRESSION. A probe that runs at boot must not do network I/O."""
    yt = _fresh(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_TEST_MODE", raising=False)
    calls = []
    got = yt.latest_version(_fetch=lambda u, t: calls.append(u) or '{"info":{"version":"9.9.9"}}')
    assert calls == [], (
        "latest_version() reached the network on a default call -- this runs "
        "inside the startup selftest and in every test that does not know to "
        "mock it")
    assert got is None, "a non-fetching call must report UNKNOWN, not a guess"


def test_BD_TEST_MODE_forbids_the_fetch_even_when_ASKED(monkeypatch, tmp_path):
    """Belt and braces. conftest sets BD_TEST_MODE for every test, so this makes
    the suite hermetic even if some future caller passes allow_fetch=True."""
    yt = _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("BD_TEST_MODE", "1")
    calls = []
    got = yt.latest_version(allow_fetch=True,
                            _fetch=lambda u, t: calls.append(u) or '{"info":{"version":"9.9.9"}}')
    assert calls == [], "a test-mode run fetched from the live index: %r" % calls
    assert got is None


def test_an_EXPLICIT_fetch_works_and_PERSISTS(monkeypatch, tmp_path):
    """The value has to survive the process, or a boot-time cache read is always
    cold and the feature never says anything useful."""
    yt = _fresh(monkeypatch, tmp_path)
    monkeypatch.delenv("BD_TEST_MODE", raising=False)
    got = yt.latest_version(allow_fetch=True,
                            _fetch=lambda u, t: '{"info":{"version":"2026.8.9"}}')
    assert got == "2026.8.9", got
    import importlib
    from bulk_downloader import ytdlp_updater as yt2
    importlib.reload(yt2)                     # new process, in effect
    assert yt2.latest_version() == "2026.8.9", (
        "the fetched version did not survive a reload, so every boot reads a "
        "cold cache and reports UNKNOWN forever")


def test_a_corrupt_cache_is_UNKNOWN_not_a_crash(monkeypatch, tmp_path):
    """It runs at boot. A truncated write must degrade, never raise."""
    yt = _fresh(monkeypatch, tmp_path)
    (tmp_path / ".bd_ytdlp_latest.json").write_text("{not json", encoding="utf-8")
    assert yt.latest_version() is None


def test_the_PROBE_is_network_free(monkeypatch, tmp_path):
    """The invariant the rest of healthcheck.py already holds."""
    import importlib
    from bulk_downloader import healthcheck as hc, ytdlp_updater as yt
    importlib.reload(yt)
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.delenv("BD_TEST_MODE", raising=False)
    monkeypatch.setattr(yt, "status_dict", lambda: {
        "installed": True, "version": "2026.7.4", "age_days": 36, "stale": True})

    def explode(url, timeout):
        raise AssertionError("the boot probe attempted a network fetch")
    monkeypatch.setattr(yt, "_default_fetch", explode)
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN
    assert "unknown" in r["message"].lower(), (
        "a cold cache must read as UNKNOWN, not as a verdict: %r" % r["message"])


def test_a_WARM_cache_lets_the_probe_answer(monkeypatch, tmp_path):
    """Cache-only is not the same as useless: once the update path has fetched
    once, the boot probe gives a real answer with no network."""
    import importlib
    from bulk_downloader import healthcheck as hc, ytdlp_updater as yt
    importlib.reload(yt)
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    (tmp_path / ".bd_ytdlp_latest.json").write_text(
        json.dumps({"version": "2026.7.4", "ts": 4102444800}), encoding="utf-8")
    monkeypatch.setattr(yt, "status_dict", lambda: {
        "installed": True, "version": "2026.7.4", "age_days": 36, "stale": True})
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_OK, (
        "with a warm cache saying we are current, the probe still warned: %r" % r)
    assert "current" in r["message"]
