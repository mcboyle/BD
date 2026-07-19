"""RED-first guard for v3.66.555: candidate_filter.classify SSRF host-guard (F-CORE_BD15-01).

classify() decides whether a scraped URL is a 'download' candidate using text/class/selector
signals + a registrable-domain same-site check. It had NO IP-range check, so a media-signalled
URL pointing at an INTERNAL IP literal (RFC1918 / loopback / RFC6598 CGNAT / link-local cloud-
metadata / IPv6 loopback) was ACCEPTED as 'download'. The transport extracted-URL path then
fetches the chosen candidate WITH the session cookies (runner_util.gate_candidate_url is
fail-open and does not apply _is_url_public) -> SSRF to internal infra, including BD's own
loopback API port. Unlike the hooks, a download candidate is NEVER legitimately internal, so
ALL non-public hosts are rejected (RFC1918 included).

The fix classifies the candidate host with the canonical provider_resolve_impl._common.
_classify_ip: a non-public IP literal -> rejected. (Literal only -- no DNS in this hot path;
the fetch boundary resolves hostnames.)

RED on the pre-555 tree: an internal-host media URL classifies as accepted 'download'.
GREEN once classify rejects the non-public host.

Convention: zero-arg fns; pure classify() calls (literal IPs, no network).
"""
import bulk_downloader.candidate_filter as cf

_PAGE = "approved-site.com"
_TEXT = "Download 1080p"

_INTERNAL = [
    "http://10.0.0.5/video.mp4",           # RFC1918
    "http://192.168.1.1/video.mp4",        # RFC1918
    "http://172.16.0.9/video.mp4",         # RFC1918
    "http://100.64.0.1/video.mp4",         # RFC6598 CGNAT
    "http://127.0.0.1:5555/video.mp4",     # loopback -- BD's own API port
    "http://[::1]/video.mp4",              # IPv6 loopback
    "http://169.254.169.254/video.mp4",    # link-local / cloud metadata (media ext)
]


def _accepted(url):
    return bool(cf.classify(url=url, text=_TEXT, page_host=_PAGE).accepted)


def test_internal_hosts_rejected():
    for u in _INTERNAL:
        assert _accepted(u) is False, f"internal-host media URL must be rejected: {u}"


def test_public_media_url_accepted():
    # regression: a public CDN media URL must still be accepted as a download candidate.
    assert _accepted("http://93.184.216.34/video.mp4") is True, "public IP media URL rejected"
    # a same-site hostname CDN (not an IP -> no DNS here) still classifies normally.
    assert _accepted("https://cdn.approved-site.com/video.mp4") is True, "same-site CDN rejected"
