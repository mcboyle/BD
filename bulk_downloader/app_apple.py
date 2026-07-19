"""apple-touch-icon route -- extracted from app.py (#12 decomposition, thin-core-shell).

Pure code MOTION: the /apple-touch-icon.png view moved onto a Flask Blueprint.
The endpoint label gains an "apple." prefix; the (rule, methods, bare-name) routing
surface is byte-identical (test_route_map_invariant diffs empty).

The route serves a pre-rendered 180x180 PNG embedded as base64 so the icon works
without Pillow at runtime. The old post-return Pillow/SVG fallback in app.py was dead
code -- the try/except above it always returns the PNG or a 404 -- so it is dropped in
the move, leaving this module self-contained (base64 + Response only, no back-import
into app.py and no new cross-subsystem edge).
"""
from __future__ import annotations

import base64

from flask import Blueprint, Response

apple_bp = Blueprint("apple", __name__)

# Pre-rendered apple-touch-icon -- embedded so we don't need Pillow at runtime.
# 180x180 PNG, ~630 bytes. Re-generate via the script in the build pipeline
# if the icon design changes.
APPLE_TOUCH_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAACO0lEQVR42u3dPW7CUBCF0YCos0kisiYk"
    "NklWkC6VZSD+4c2dc9o0yPMxfo6i+HC+3D9gytElQByIA3EgDsSBOBAH4kAciAPEgTgQB+JAHIgDcSAO"
    "xIE4QByIA3EgDsSBOBAH4kAciAPEgTgQB+JAHIgDcSAOUpxcgtv1c+anX98/NgeIA3EgDsSBOBAH4kAc"
    "iANxgDgQB+JAHIgDcSAOxIE4EAeIA3EgDsSBOBAH4kAciANxIA4QB+JAHIgDcSAOxIE4EAfiAHEgDsSB"
    "OBAH4kAciANxIA4QB+LgHw7ny73ch55/2eewyr2F9Ogq+8xpt5Va17rom4sLnzmqXPG677R2ICU0jvG/"
    "lHXXRsLmGPnqly4j5LYy5gyql5Fz5hhtEgFlRB1Ix5lHRhlpTysjTCWmDI+ydIrjvV/cpLWRuTneNaGw"
    "MmJvK/vPKa+M5DPHntOKLCP8QLrPzFLLyH9a2XpywWV4lKV3HNt9ubPXRpfNscUU48todFtZd5Ydyuh1"
    "5lhrok3KaHcgXT7XPmV0fFpZMt1WZXiURRxrLIBua6Pv5nh10g3LaH1beX7ePcvofuZ4Zupty3AgfTD7"
    "zmWIY66A5mWIA3G8vjysDXFM16AMcUz3oYw/Jf8nGDYH4kAciANxIA7EAeJAHKziNObHKvpehCUG/LW9"
    "zYE4EAfiQByIA3EgDsRBPH9Dis2BOBAH4kAciANxIA7EgThAHIgDcSAOxIE4EAfiQByIA8SBOBAH4kAc"
    "iANxIA7i/AIur3mdCAGNZQAAAABJRU5ErkJggg=="
)


@apple_bp.route("/apple-touch-icon.png")
def pwa_apple_icon():
    """iOS demands PNG for apple-touch-icon. We embed a pre-rendered 180x180
    PNG (base64-encoded) so the icon works without requiring Pillow at runtime;
    identical to the SVG icon's design (indigo background, white download arrow)."""
    try:
        png = base64.b64decode(APPLE_TOUCH_ICON_B64)
        return Response(png, mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        # Explicit 404 rather than an opaque 500 if the embedded constant is corrupt.
        return Response(status=404)


def register_routes(app) -> int:
    app.register_blueprint(apple_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("apple."))
