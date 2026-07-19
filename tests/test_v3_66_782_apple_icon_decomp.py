"""v3.66.782 (#12 decomposition): move pwa_apple_icon off app.py onto app_apple.py.

Pure code MOTION, thin-core-shell: the /apple-touch-icon.png view leaves the app.py
monolith for a Flask Blueprint in bulk_downloader/app_apple.py. The endpoint label
gains an "apple." prefix; the (rule, methods, bare-name) routing surface is
byte-identical, so test_route_map_invariant diffs empty. The old post-return
Pillow/SVG fallback (unreachable -- the try/except above it always returns or 404s)
is dropped in the move, so the extracted module is self-contained (base64 + Response
only), adding no cross-subsystem import edge.

RED-first witness: on the pre-782 tree the /apple-touch-icon.png rule resolves to the
bare app-level endpoint "pwa_apple_icon"; after the move it resolves to the
blueprint-scoped "apple.pwa_apple_icon". The serve-behaviour and routing surface are
pinned unchanged.
"""


def _rule_endpoint(rule):
    from bulk_downloader.app import app
    return {r.rule: r.endpoint for r in app.url_map.iter_rules()}.get(rule)


def test_apple_icon_endpoint_is_blueprint_scoped():
    """RED on pristine (bare 'pwa_apple_icon'), GREEN once moved to the blueprint."""
    ep = _rule_endpoint("/apple-touch-icon.png")
    assert ep == "apple.pwa_apple_icon", (
        f"/apple-touch-icon.png should be served by the apple blueprint, got {ep!r}")


def test_apple_route_owned_by_app_apple_not_app():
    """The view + its embedded PNG constant now live in app_apple.py, not app.py."""
    from bulk_downloader import app as app_mod
    from bulk_downloader import app_apple
    assert hasattr(app_apple, "pwa_apple_icon")
    assert hasattr(app_apple, "APPLE_TOUCH_ICON_B64")
    assert not hasattr(app_mod, "pwa_apple_icon"), "app.py must no longer define the view"
    assert not hasattr(app_mod, "APPLE_TOUCH_ICON_B64"), "the constant must move too"


def test_dead_fallback_dropped_in_move():
    """The unreachable Pillow/SVG fallback must NOT be carried into app_apple.py."""
    from bulk_downloader import app_apple
    src = open(app_apple.__file__, encoding="utf-8").read()
    for token in ("PIL", "ImageDraw", "BytesIO", "pwa_icon_svg"):
        assert token not in src, f"dead-fallback token {token!r} carried into app_apple.py"


def test_apple_icon_still_serves_png():
    """Regression: the move must not change what the route serves (200 + PNG)."""
    from bulk_downloader.app import app
    r = app.test_client().get("/apple-touch-icon.png")
    assert r.status_code == 200, r.status_code
    assert r.mimetype == "image/png", r.mimetype
    body = r.get_data()
    assert body.startswith(b"\x89PNG\r\n\x1a\n"), "not a PNG payload"
    assert len(body) > 100, "PNG payload suspiciously small"


def test_icon_svg_route_stays_in_app():
    """Sibling /icon.svg is NOT part of this cut -- it must stay app-level."""
    assert _rule_endpoint("/icon.svg") == "pwa_icon_svg"
