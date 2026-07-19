"""v3.66.x — approved capture sessions use LOCAL vendored rrweb/snapdom
(offline, no remote CDN by default)."""
from bulk_downloader import dom_recorder as dr


def test_vendored_assets_present_and_local():
    # both bundles are vendored on disk in this build
    assert dr.using_local_assets() is True
    st = dr.get_status()
    assert st["rrweb_present"] and st["snapdom_present"]
    assert st["rrweb_bytes"] > 0 and st["snapdom_bytes"] > 0


def test_no_cdn_url_in_injected_scripts():
    # the bootstrap + injected bundles must not reference a remote CDN
    blob = (dr.recorder_script() + dr.rrweb_js()[:5000] + dr.snapdom_js()[:5000])
    low = blob.lower()
    for needle in ("cdn.jsdelivr", "unpkg.com", "cdnjs.cloudflare", "esm.sh",
                   "skypack.dev"):
        assert needle not in low, f"unexpected CDN reference: {needle}"
