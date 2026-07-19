"""v3.66.x — template_multi: compare several approved captures into a
review-required draft (selector support, rejected, network patterns,
resolution priority)."""
from bulk_downloader import template_multi as tm


def _rrweb_snapshot(*els):
    """Build an rrweb full_snapshot event with the given elements as children.
    Each el = {"tag", "attrs": {...}, "text": "..."}."""
    children = []
    for el in els:
        kids = [{"textContent": el["text"]}] if el.get("text") else []
        children.append({"tagName": el["tag"],
                         "attributes": el.get("attrs", {}),
                         "childNodes": kids})
    root = {"tagName": "body", "attributes": {}, "childNodes": children}
    return {"type": "full_snapshot", "data": {"node": root}}


def _cap(role, *, snapshot_els=None, dom_candidates=None, urls=()):
    cap = {"role": role, "host": "site.com", "url": "https://site.com/v/1",
           "network_log": [{"url": u, "type": "xhr", "method": "GET"} for u in urls]}
    if snapshot_els is not None:
        cap["dom_log"] = [_rrweb_snapshot(*snapshot_els)]
    if dom_candidates is not None:
        cap["dom_candidates"] = dom_candidates
    return cap


def test_selector_support_counted_across_captures():
    dl = {"tag": "a", "href": "/get/123/1080.mp4",
          "classes": "dl-btn", "text": "Download 1080p"}
    captures = [
        {"role": "download_menu", "capture": _cap("download_menu", dom_candidates=[dl])},
        {"role": "download_result", "capture": _cap("download_result", dom_candidates=[dl])},
    ]
    draft = tm.build_multi_capture_draft(captures)
    assert draft["review_required"] is True
    assert draft["capture_count"] == 2
    sels = {s["selector"]: s for s in draft["selectors"]}
    assert "a.dl-btn" in sels
    assert sels["a.dl-btn"]["support"] == 2
    assert sels["a.dl-btn"]["roles"] == ["download_menu", "download_result"]
    assert sels["a.dl-btn"]["kind"] == "download"


def test_rrweb_snapshot_walker_extracts_and_filters():
    captures = [{"role": "download_menu", "capture": _cap(
        "download_menu",
        snapshot_els=[
            {"tag": "a", "attrs": {"href": "/get/9/4k.mp4", "class": "dl"},
             "text": "Download 4K"},
            {"tag": "a", "attrs": {"href": "/", "class": "logo"}, "text": "Home"},
            {"tag": "a", "attrs": {"href": "/login"}, "text": "Log in"},
        ])}]
    draft = tm.build_multi_capture_draft(captures)
    sels = [s["selector"] for s in draft["selectors"]]
    assert "a.dl" in sels                      # real download kept
    reasons = {r["selector"]: r["reason"] for r in draft["rejected"]}
    assert "a.logo" in reasons                 # homepage rejected
    assert any("login" in r["reason"] or "logout" in r["reason"]
               for r in draft["rejected"])     # login rejected


def test_network_patterns_and_resolution_priority():
    captures = [
        {"role": "player", "capture": _cap("player", urls=[
            "https://cdn.site.com/v/123/1080.mp4",
            "https://www.google-analytics.com/collect",   # noise -> dropped
        ])},
        {"role": "download_result", "capture": _cap("download_result", urls=[
            "https://cdn.site.com/v/456/1080.mp4",         # same shape, diff id
            "https://cdn.site.com/v/456/2160.mp4",
        ])},
    ]
    draft = tm.build_multi_capture_draft(captures)
    pats = {p["template"]: p for p in draft["network_patterns"]}
    # the media endpoint coalesces across captures (id + resolution collapsed)
    assert "cdn.site.com/v/{id}/{res}.mp4" in pats
    assert pats["cdn.site.com/v/{id}/{res}.mp4"]["support"] == 3
    assert pats["cdn.site.com/v/{id}/{res}.mp4"]["is_media"] is True
    assert pats["cdn.site.com/v/{id}/{res}.mp4"]["roles"] == ["download_result", "player"]
    # analytics noise was not treated as a download pattern
    assert not any("google-analytics" in t for t in pats)
    # resolution priority is high->low and includes the detected tiers
    labels = [r["label"] for r in draft["resolution_priority"]]
    assert labels, "expected resolutions detected"
    tiers = [r["tier"] for r in draft["resolution_priority"]]
    assert tiers == sorted(tiers, reverse=True)


def test_roles_seen_and_empty_is_safe():
    draft = tm.build_multi_capture_draft([])
    assert draft["review_required"] is True
    assert draft["capture_count"] == 0
    assert draft["selectors"] == [] and draft["network_patterns"] == []
