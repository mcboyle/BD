"""BH1-34 r2 (SEC REFUTE F1) -- the yt-dlp ``@extractor`` shim judges its proxy.

``make_ytdlp_extractor`` mounted the site cfg ``proxy_url`` / ``vpn_socks_url``
as yt-dlp ``--proxy`` without ``judge_proxy_url`` (BD-SEC-3 F14: every proxy is
judged at its mount).  A cloud-metadata or link-local proxy must be refused with
``OutboundRefused(reason="proxy-refused")`` and yt-dlp never spawned -- no
``--proxy``, no direct (unproxied) probe instead.

RED on tree 7d6006d2 (BH1-34 r1): the shim spawns with ``--proxy
http://169.254.169.254:80`` / ``socks5h://[fd00:ec2::254]:1080`` and nothing is
raised.  Distinctive diagnostic: ``BH1-34-R2-YTDLP-PROXY-UNJUDGED``.

Every test injects the subprocess runner and binary resolver: no yt-dlp, no
egress (the judged hosts are IP literals, so the judge's resolution is local).
"""
import json
import types

import pytest

from bulk_downloader import ytdlp_extractor as y
from bulk_downloader.outbound_guard import OutboundRefused

BD_GATE_SCOPE = "module"

DIAG = "BH1-34-R2-YTDLP-PROXY-UNJUDGED"


def _shim(cfg):
    seen = []

    def run(cmd, *a, **k):
        seen.append(list(cmd))
        return (0, json.dumps({"url": "https://example.com/v.mp4",
                               "protocol": "https", "ext": "mp4"}), "")

    ex = y.make_ytdlp_extractor(dict(cfg, ytdlp_extractor=True),
                                run=run, resolve=lambda: ["yt-dlp"])
    return ex, seen


def _proxy_arg(cmd):
    return cmd[cmd.index("--proxy") + 1] if "--proxy" in cmd else None


REFUSED = [
    ("proxy_url", "http://169.254.169.254:80"),           # cloud metadata v4
    ("vpn_socks_url", "socks5://[fd00:ec2::254]:1080"),   # cloud metadata v6 (AWS)
    ("proxy_url", "socks5://[fe80::1]:1080"),             # link-local v6
    ("proxy_url", "http://10.0.0.5"),                     # no explicit port
    ("proxy_url", "ftp://10.0.0.5:21"),                   # not a proxy scheme
]


@pytest.mark.parametrize("key,val", REFUSED)
def test_unjudgeable_proxy_is_refused_and_ytdlp_never_spawned(key, val):
    ex, seen = _shim({key: val})
    try:
        out = ex("https://example.com/watch")
    except OutboundRefused as exc:
        assert exc.reason == "proxy-refused", f"{DIAG}: wrong reason {exc.reason!r}"
        assert seen == [], f"{DIAG}: refused yet spawned {seen!r}"
        return
    px = _proxy_arg(seen[0]) if seen else None
    pytest.fail(f"{DIAG}: {key}={val!r} not refused -- spawned={bool(seen)} "
                f"--proxy={px!r} returned={out!r}")


def test_refusal_message_carries_no_proxy_credentials():
    ex, seen = _shim({"proxy_url": "http://user:s3cr3tpw@169.254.169.254:80"})
    with pytest.raises(OutboundRefused) as ei:
        ex("https://example.com/watch")
    assert seen == [], f"{DIAG}: refused yet spawned {seen!r}"
    assert "s3cr3tpw" not in str(ei.value) and "s3cr3tpw" not in ei.value.url


def test_refusal_reaches_the_plugin_dispatch_as_a_logged_failure_not_a_spawn():
    """The 691 dispatch catches the shim's refusal, logs it and falls through;
    the yt-dlp probe itself never ran."""
    from bulk_downloader import plugins as P
    from bulk_downloader.runner_extractors import ExtractorsMixin

    ex, seen = _shim({"proxy_url": "http://169.254.169.254:80"})
    events = []

    site = types.SimpleNamespace(
        site_id="bh1-34-r2-site", config={"download_dir": "/nonexistent-never-used"},
        log_event=lambda kind, msg, **kw: events.append((kind, msg)))
    P.unregister_extractor(site.site_id)
    P.register_extractor(site.site_id, ex)
    try:
        ok = ExtractorsMixin._try_plugin_extractor(site, "https://example.com/watch")
    finally:
        P.unregister_extractor(site.site_id)
    assert ok is False
    assert seen == [], f"{DIAG}: dispatch spawned yt-dlp {seen!r}"
    assert [k for k, _ in events] == ["plugin_extract_failed"], events
    assert "proxy refused" in events[0][1], events


# -- positive controls: the judge admits the operator's own boxes ------------
@pytest.mark.parametrize("key,val,mounted", [
    ("vpn_socks_url", "socks5://127.0.0.1:9050", "socks5h://127.0.0.1:9050"),
    ("proxy_url", "http://10.0.0.5:3128", "http://10.0.0.5:3128"),
    ("proxy_url", "socks5h://[fd12:3456::1]:1080", "socks5h://[fd12:3456::1]:1080"),
])
def test_admitted_lan_proxy_still_spawns_with_that_proxy(key, val, mounted):
    ex, seen = _shim({key: val})
    assert ex("https://example.com/watch").get("video_url") == "https://example.com/v.mp4"
    assert len(seen) == 1 and _proxy_arg(seen[0]) == mounted, seen


def test_no_proxy_configured_spawns_without_proxy():
    ex, seen = _shim({})
    assert ex("https://example.com/watch").get("video_url") == "https://example.com/v.mp4"
    assert len(seen) == 1 and "--proxy" not in seen[0], seen
