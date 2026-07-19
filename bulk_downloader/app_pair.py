"""pair API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/pair views moved onto a Flask Blueprint.
Endpoint labels gain a "pair." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (PAIRING_TTL, SESSION_IDLE_TTL, _app_cfg, _pairing_lock, _pairing_tokens) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, request

pair_bp = Blueprint("pair", __name__)

def _csrf_token_for(*_a, **_k):
    """Delegate to app._csrf_token_for at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_csrf_token_for")(*_a, **_k)

def _lan_ip_guess(*_a, **_k):
    """Delegate to app._lan_ip_guess at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_lan_ip_guess")(*_a, **_k)

def _session_create(*_a, **_k):
    """Delegate to app._session_create at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_session_create")(*_a, **_k)

def _app_PAIRING_TTL():
    """The live shared PAIRING_TTL from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "PAIRING_TTL")

def _app_SESSION_IDLE_TTL():
    """The live shared SESSION_IDLE_TTL from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "SESSION_IDLE_TTL")

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")

def _app__pairing_lock():
    """The live shared _pairing_lock from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_pairing_lock")

def _app__pairing_tokens():
    """The live shared _pairing_tokens from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_pairing_tokens")


@pair_bp.route("/api/pair/redeem", methods=["POST"])
def api_pair_redeem():
    """Phase 40: complete the QR pairing flow started in Phase 37.
    POST {token} → if valid, set a session cookie + return the CSRF
    token for immediate use. Token is one-shot — removed from the
    store on success."""
    SESSION_IDLE_TTL = _app_SESSION_IDLE_TTL()
    _pairing_lock = _app__pairing_lock()
    _pairing_tokens = _app__pairing_tokens()
    body = request.json or {}
    token = (body.get("token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "token required"}), 400
    now = time.time()
    with _pairing_lock:
        rec = _pairing_tokens.pop(token, None)
    if not rec:
        return jsonify({"ok": False, "error": "unknown or already-redeemed pairing token"}), 404
    if now > rec["expires_at"]:
        return jsonify({"ok": False, "error": "pairing token expired (5 min TTL — generate a new QR)"}), 410
    # Mint session + CSRF
    sess = _session_create(source="pair_redeem")
    csrf = _csrf_token_for(sess)
    response = jsonify({"ok": True, "csrf_token": csrf,
                        "expires_in": SESSION_IDLE_TTL})
    secure = request.scheme == "https"
    response.set_cookie("bd_session", sess,
                        max_age=SESSION_IDLE_TTL, httponly=True,
                        samesite="Lax", secure=secure)
    return response
@pair_bp.route("/api/pair")
def api_pair():
    """Return data for the QR pairing modal: the LAN URL, an optional
    one-time pairing token, and (if the qrcode package is installed) an
    inline SVG QR code rendering."""
    PAIRING_TTL = _app_PAIRING_TTL()
    _app_cfg = _app__app_cfg()
    _pairing_lock = _app__pairing_lock()
    _pairing_tokens = _app__pairing_tokens()
    import secrets, urllib.parse as _up
    # Determine LAN IP. Prefer an explicit override from app_config,
    # then the host header (when not loopback), then the auto-detected
    # LAN IP. The explicit override is useful in multi-NIC setups.
    lan_ip = (_app_cfg.get("pair_lan_ip") or "").strip()
    if not lan_ip:
        host_hdr = (request.host or "").split(":")[0]
        if host_hdr and not host_hdr.startswith(("127.", "localhost")):
            lan_ip = host_hdr
        else:
            lan_ip = _lan_ip_guess()
    port = int(_app_cfg.get("pair_port") or 5555)
    url = f"http://{lan_ip}:{port}/"
    # One-time token: random hex. Future work could exchange this for a
    # session via /api/pair/redeem. For now it's purely informational.
    token = secrets.token_urlsafe(12)
    # Phase 40: register the token so /api/pair/redeem can exchange it
    # for a session. One-shot, expires in PAIRING_TTL.
    now = time.time()
    with _pairing_lock:
        _pairing_tokens[token] = {"created": now,
                                   "expires_at": now + PAIRING_TTL}
        # Garbage-collect expired tokens — keeps the store small without
        # a dedicated cleanup thread
        expired = [k for k, v in _pairing_tokens.items() if v["expires_at"] < now]
        for k in expired: del _pairing_tokens[k]
    full_url = f"{url}?t={_up.quote(token)}"

    # Try to render the QR as SVG. The qrcode package is OPTIONAL; if
    # not installed, the UI falls back to showing just the URL.
    qr_svg = None
    qr_error = None
    try:
        import qrcode
        import qrcode.image.svg
        qr = qrcode.QRCode(
            version=None,  # auto-size
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8, border=2,
        )
        qr.add_data(full_url)
        qr.make(fit=True)
        # Generate SVG. The SvgPathImage gives a single-path SVG that's
        # ~3kb for typical URLs — much smaller than SvgImage's per-module
        # rectangles.
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        import io
        buf = io.BytesIO()
        img.save(buf)
        qr_svg = buf.getvalue().decode("utf-8")
    except ImportError:
        qr_error = ("qrcode package not installed; install it for visual QR support:"
                    "  pip install qrcode")
    except Exception as e:
        qr_error = f"QR generation failed: {e}"

    return jsonify({
        "ok": True,
        "url": full_url,
        "base_url": url,
        "lan_ip": lan_ip,
        "port": port,
        "token": token,
        "qr_svg": qr_svg,
        "qr_error": qr_error,
    })

def register_routes(app) -> int:
    app.register_blueprint(pair_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("pair."))

