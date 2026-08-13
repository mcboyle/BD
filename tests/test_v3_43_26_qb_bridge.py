"""v3.43.26 regression tests — qBittorrent bridge.

Mirror of test_v3_43_21_jd_bridge.py adapted for qB.

Covers:
  - qb_bridge module: classify_qb_error, cookies/path conversion,
    looks_like_torrent_url, is_qb_backend
  - QBittorrentClient: construction with defaults, unreachable path
  - Runner integration: branch in _process_one, fallback contract,
    auto-routing of torrent URLs
  - DEFAULTS: qb_host, qb_port, qb_username, qb_password
  - Endpoint: /api/sites/<sid>/qb/diagnose registered
  - PRESERVE_IF_BLANK includes qb_password
"""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
class _AggregateSrc:
    """Reads runner.py + runner_integrations.py concatenated. The integration
    methods (stash/plex/jellyfin/qb/jd) moved to runner_integrations.py at
    v3.66.398 (Phase 3 decomposition cut 2, pure motion); the orchestration call
    sites that spawn enrichment stay in runner.py core. Reading both preserves
    every source-level assertion across the move (core-first ordering)."""
    def __init__(self, *paths):
        self._paths = paths
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader" / "runner.py",
                           _REPO_ROOT / "bulk_downloader" / "runner_integrations.py")
_QB_BRIDGE_PY = _REPO_ROOT / "bulk_downloader" / "qb_bridge.py"


# ── qb_bridge module surface ──────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_qb_bridge_module_exists():
    import bulk_downloader.qb_bridge as qb  # noqa: F401


def test_classify_qb_error_auth():
    from bulk_downloader.qb_bridge import classify_qb_error
    assert classify_qb_error("HTTP 401 Unauthorized") == "auth"
    assert classify_qb_error("403 Forbidden") == "auth"
    assert classify_qb_error("login required") == "auth"


def test_classify_qb_error_network():
    from bulk_downloader.qb_bridge import classify_qb_error
    assert classify_qb_error("Connection refused") == "network"
    assert classify_qb_error("timeout") == "network"
    assert classify_qb_error("HTTP 503") == "network"


def test_classify_qb_error_unknown_for_empty():
    from bulk_downloader.qb_bridge import classify_qb_error
    assert classify_qb_error("") == "unknown"
    assert classify_qb_error(None) == "unknown"


def test_looks_like_torrent_url_magnet():
    """Magnet links must auto-route to qB regardless of backend setting."""
    from bulk_downloader.qb_bridge import looks_like_torrent_url
    assert looks_like_torrent_url("magnet:?xt=urn:btih:abc123")
    assert looks_like_torrent_url("MAGNET:?xt=urn:btih:abc")  # case-insensitive
    # Whitespace tolerated
    assert looks_like_torrent_url("  magnet:?xt=urn:btih:xyz  ")


def test_looks_like_torrent_url_dot_torrent():
    """*.torrent URLs likewise."""
    from bulk_downloader.qb_bridge import looks_like_torrent_url
    assert looks_like_torrent_url("https://example.com/file.torrent")
    assert looks_like_torrent_url("https://example.com/file.torrent?token=abc")
    assert looks_like_torrent_url("HTTP://example.com/FILE.TORRENT")  # case


def test_looks_like_torrent_url_negatives():
    """Regular HTTP URLs and empty inputs must NOT auto-route."""
    from bulk_downloader.qb_bridge import looks_like_torrent_url
    assert not looks_like_torrent_url("")
    assert not looks_like_torrent_url(None)
    assert not looks_like_torrent_url("https://example.com/video.mp4")
    assert not looks_like_torrent_url("https://example.com/")
    # Suspicious-looking but not actually a torrent
    assert not looks_like_torrent_url("https://torrent-site.example/video.mp4")


def test_is_qb_backend_helper():
    from bulk_downloader.qb_bridge import is_qb_backend
    assert is_qb_backend({"backend": "qbittorrent"}) is True
    assert is_qb_backend({"backend": "QBITTORRENT"}) is True
    assert is_qb_backend({"backend": " qbittorrent "}) is True
    assert is_qb_backend({"backend": "teach"}) is False
    assert is_qb_backend({"backend": "jd"}) is False
    assert is_qb_backend({}) is False
    assert is_qb_backend({"backend": None}) is False


def test_qb_client_construction_defaults():
    from bulk_downloader.qb_bridge import QBittorrentClient, DEFAULT_HOST, DEFAULT_PORT
    c = QBittorrentClient()
    assert c.host == DEFAULT_HOST
    assert c.port == DEFAULT_PORT
    assert c.username == "admin"
    assert c.password == ""
    c.close()


def test_qb_client_handles_bad_port_in_cfg():
    """get_client_for_site must not raise on a non-integer port —
    just fall back to default."""
    from bulk_downloader.qb_bridge import get_client_for_site, DEFAULT_PORT
    c = get_client_for_site({"qb_port": "not_a_number"})
    assert c.port == DEFAULT_PORT
    c.close()


def test_qb_is_reachable_returns_false_for_closed_port():
    from bulk_downloader.qb_bridge import QBittorrentClient
    c = QBittorrentClient(host="127.0.0.1", port=1)
    assert c.is_reachable() is False
    c.close()


def test_qb_diagnose_structured_error_when_unreachable():
    """UI relies on the {reachable, api_enabled, hint, error} shape."""
    from bulk_downloader.qb_bridge import QBittorrentClient
    c = QBittorrentClient(host="127.0.0.1", port=1)
    d = c.diagnose()
    assert d["reachable"] is False
    assert d["api_enabled"] is False
    assert d["logged_in"] is False
    assert d["error"]
    assert d["hint"]
    assert d["host"] == "127.0.0.1"
    assert d["port"] == 1
    c.close()


def test_qb_submit_raises_qberror_on_unreachable():
    from bulk_downloader.qb_bridge import QBittorrentClient, QBError
    c = QBittorrentClient(host="127.0.0.1", port=1)
    try:
        c.submit("magnet:?xt=urn:btih:abc", dest_dir="/tmp")
    except QBError as e:
        assert e.kind in ("unreachable", "auth", "network", "unknown")
    except Exception as e:
        raise AssertionError(
            f"submit raised {type(e).__name__} instead of QBError: {e}")
    else:
        raise AssertionError("submit should have raised QBError")
    c.close()


class _Status:
    """Minimal stand-in for an httpx response: submit()'s listing probe
    reads status_code and nothing else on the non-200 path."""
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        raise AssertionError("json() must not be reached on a non-200")


def test_qb_submit_wraps_a_non_httpx_listing_failure_in_qberror():
    """submit() documents QBError as its only failure mode, and
    _list_hashes wrapped httpx.RequestError ONLY -- so anything else it
    raised escaped submit() untouched, because both of submit()'s call
    sites catch QBError alone and cannot catch what was never wrapped.

    MEASURED on test6 at v3.66.1083, capture parallel lane, worker gw27: a
    refused connection surfaced as httpcore.ConnectError rather than
    httpx.ConnectError, so the RequestError clause matched nothing and the
    raw error left submit(). That module-identity route is closed
    separately; this test is about the contract, and it raises a plain
    exception so it does not depend on the accident that exposed it.
    """
    from bulk_downloader.qb_bridge import QBittorrentClient, QBError

    class _TransportWentSideways(Exception):
        pass

    c = QBittorrentClient(host="127.0.0.1", port=1)

    def _raise(*_a, **_k):
        raise _TransportWentSideways("not an httpx.RequestError")

    # Only the listing probe uses .get. The add POST still runs against a
    # closed port, and its QBError is what the caller should end up seeing.
    c._client.get = _raise
    try:
        c.submit("magnet:?xt=urn:btih:abc", dest_dir="/tmp")
    except QBError as e:
        assert e.kind in ("unreachable", "auth", "network", "unknown")
    except Exception as e:
        raise AssertionError(
            f"submit raised {type(e).__name__} instead of QBError: {e}")
    else:
        raise AssertionError("submit should have raised QBError")
    c.close()


def test_qb_submit_on_a_closed_client_raises_qberror():
    """The same hole reached with no transport involved at all: close()
    sets _client to None, so the listing probe raises AttributeError,
    which is neither an httpx.RequestError nor a QBError and therefore
    escaped submit(). A caller that closes and then retries is the
    ordinary way a program arrives here, which is why the contract hole
    is a product defect rather than an artifact of the capture."""
    from bulk_downloader.qb_bridge import QBittorrentClient, QBError
    c = QBittorrentClient(host="127.0.0.1", port=1)
    c.close()
    try:
        c.submit("magnet:?xt=urn:btih:abc", dest_dir="/tmp")
    except QBError as e:
        assert e.kind in ("unreachable", "auth", "network", "unknown")
    except Exception as e:
        raise AssertionError(
            f"submit raised {type(e).__name__} instead of QBError: {e}")
    else:
        raise AssertionError("submit should have raised QBError")


def test_qb_list_hashes_keeps_the_kind_it_chose():
    """The over-sensitive direction of the fix above, asserted in the same
    cut. _list_hashes raises QBError("network") itself for a non-200, and a
    wrapper that caught every exception would re-label that to "unknown".
    submit() swallows this QBError by design, so nothing else in the suite
    can observe the kind -- it is checked directly or not at all."""
    from bulk_downloader.qb_bridge import QBittorrentClient, QBError
    c = QBittorrentClient(host="127.0.0.1", port=1)
    c._client.get = lambda *_a, **_k: _Status(503)
    try:
        c._list_hashes()
    except QBError as e:
        assert e.kind == "network", f"kind was re-labelled to {e.kind!r}"
        assert "503" in str(e), f"the status is not in the message: {e}"
    else:
        raise AssertionError("_list_hashes should have raised QBError")
    c.close()


# ── DEFAULTS integration ──────────────────────────────────────────────

def test_qb_defaults_present():
    src = _APP_SRC()
    assert '"qb_host": "127.0.0.1"' in src
    assert '"qb_port": 8080' in src
    assert '"qb_username": "admin"' in src
    assert '"qb_password": ""' in src


def test_qb_fields_in_cfg_fields():
    """Without these in CFG_FIELDS, the values would be dropped on
    every config reload."""
    src = _APP_SRC()
    # CFG_FIELDS contains nested lists (notes about deferred phases),
    # so the simple regex `\[(.*?)\]` matches the wrong scope. Use
    # bracket-depth matching to find the actual end of the list.
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    assert m, "CFG_FIELDS not found"
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        pos += 1
    fields_block = src[start:pos - 1]
    for fld in ("qb_host", "qb_port", "qb_username", "qb_password"):
        assert f'"{fld}"' in fields_block, f"{fld} missing from CFG_FIELDS"


def test_qb_password_in_preserve_if_blank():
    """Without this, opening edit modal + saving with blank qB password
    field would silently wipe a stored qB password."""
    src = _APP_SRC()
    # Locate PRESERVE_IF_BLANK definition
    pos = src.find("PRESERVE_IF_BLANK = {")
    assert pos > 0
    block = src[pos:pos+1500]
    assert "qb_password" in block


def test_qb_password_stripped_from_api_response():
    """Same treatment as the main site password. _strip_password()
    must strip qb_password and surface has_qb_password instead."""
    src = _APP_SRC()
    assert "has_qb_password" in src
    assert 'meta.pop("qb_password"' in src


# ── Runner integration ────────────────────────────────────────────────

def test_runner_has_qb_methods():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    for name in ("_get_qb_client", "_record_qb_outcome",
                  "_try_qb_download", "qb_health"):
        assert f"def {name}" in src, f"SiteRunner.{name} missing"


def test_runner_branches_on_qb_before_jd_and_teach():
    """qB takes priority for torrent URLs and explicit qbittorrent
    backend. The branch (the `if _use_qb:` block) sits BEFORE the JD
    branch (`if _is_jd:`) in _process_one.

    The variable INITIALIZATION can happen in either order (both
    `_is_jd = ...` and `_is_qb_explicit = ...` are set up at the top
    of the chain). What matters is the BRANCH order — which backend
    actually gets the URL first."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    proc_one_pos = src.find("def _process_one(")
    teach_pos = src.find("Phase 9.3: pick context strategy")
    assert proc_one_pos > 0
    assert teach_pos > 0
    body = src[proc_one_pos:teach_pos]
    # The actual conditional branches, not the variable assignments
    qb_branch = body.find("if _use_qb")
    jd_branch = body.find("if _is_jd:")
    assert qb_branch > 0, "qB branch `if _use_qb` missing from _process_one"
    assert jd_branch > 0, "JD branch `if _is_jd:` missing from _process_one"
    assert qb_branch < jd_branch, (
        f"qB branch must come BEFORE JD branch — torrent URLs "
        f"can't be HTTP-downloaded, so they have to short-circuit "
        f"the chain entirely (got qb@{qb_branch}, jd@{jd_branch})"
    )


def test_runner_torrent_url_marks_needs_review_on_qb_failure():
    """For torrent URLs (which can't fall back to teach), failure
    must mark needs_review explicitly. Without this, the URL would
    fall through to teach and fail with a misleading error."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Look for the explicit handling
    pos = src.find("_is_torrent_url")
    assert pos > 0
    # The needs_review fallback for torrent URLs must be near the qB
    # branch
    block = src[pos:pos + 3000]
    assert "needs_review" in block, (
        "Torrent URLs that fail qB must mark needs_review, not "
        "fall through to teach (which can't download magnets)"
    )


def test_get_status_exposes_qb_health():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert '"qb_health": self.qb_health()' in src


def test_qb_health_returns_disabled_when_backend_is_teach():
    """Teach-mode sites must not show qB pill — would be misleading."""
    import collections
    from bulk_downloader.runner import SiteRunner

    class _MinStub:
        def __init__(self):
            self.config = {"backend": "teach"}
            self._qb_recent_outcomes = collections.deque(maxlen=20)
    stub = _MinStub()
    health = SiteRunner.qb_health(stub)
    assert health["enabled"] is False


def test_qb_health_warns_on_low_success_rate():
    """The actionable signal — when ≥5 samples show <50% success,
    warn the user that the backend is degraded."""
    import collections
    from bulk_downloader.runner import SiteRunner

    class _MinStub:
        def __init__(self):
            self.config = {"backend": "qbittorrent"}
            self._qb_recent_outcomes = collections.deque(
                [False]*6 + [True], maxlen=20)
    stub = _MinStub()
    health = SiteRunner.qb_health(stub)
    assert health["warn_broken"] is True


# ── Endpoint ──────────────────────────────────────────────────────────

def test_qb_diagnose_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/qb/diagnose' in src
    assert "def api_qb_diagnose" in src
