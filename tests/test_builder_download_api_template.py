"""Review-only download-resolution API template (Scope-2 builder extension).

The builder surfaces a concrete (templated) download endpoint by combining a
SINGLE observed API host with extraction_core's download/resolution-shaped
relative api_pattern. It must: never guess a host, never invent an endpoint,
never auto-enable, and persist no secrets. SYNTHETIC fixtures only.
"""
import json, os, shutil, sys, tempfile, zipfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_template_from_wacz as btw  # noqa: E402
from bulk_downloader import template_normalize as tn  # noqa: E402
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402


def test_helper_combines_single_host_with_download_pattern():
    net = {"observed_api_hosts": ["api2.example.com"],
           "api_patterns": ["/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"]}
    out = btw._download_api_template(net)
    assert out == "https://api2.example.com/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"


def test_helper_no_host_returns_none():
    net = {"observed_api_hosts": [],
           "api_patterns": ["/api/v1/movie/{movie_id}/download-resolution/{resolution}"]}
    assert btw._download_api_template(net) is None


def test_helper_multiple_hosts_does_not_guess():
    net = {"observed_api_hosts": ["a.example.com", "b.example.com"],
           "api_patterns": ["/api/v1/download-resolution/{resolution}"]}
    assert btw._download_api_template(net) is None


def test_helper_no_download_pattern_invents_nothing():
    net = {"observed_api_hosts": ["api2.example.com"],
           "api_patterns": ["/api/v1/movie/{movie_id}/meta"]}
    assert btw._download_api_template(net) is None


def _make_wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    w = d / "synthetic.wacz"
    with zipfile.ZipFile(w, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return w


def _capture_with_download_api():
    return {
        "url": "https://app.example.com/movie/1", "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z", "dom_log_count": 0, "network_log_count": 2,
        "dom_log": [],
        "network_log": [
            {"method": "GET",
             "url": "https://api2.example.com/api/v1/movie/55/download-resolution/1080?sig=SYN_SIG"},
            {"method": "GET",
             "url": "https://api2.example.com/api/v1/movie/55/download-resolution/720?sig=SYN_SIG2"},
        ],
    }


def test_build_template_surfaces_review_only_api_template():
    wacz = _make_wacz(_capture_with_download_api())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    tmpl = (draft.get("selectors") or {}).get("download", {}).get("api_template")
    assert tmpl, "expected a review-only download.api_template"
    assert tmpl.startswith("https://api2.example.com/")
    assert "{resolution}" in tmpl
    assert "sig=" not in tmpl and "SYN_SIG" not in tmpl  # no query/signing
    assert scan_artifact_secrets(draft) == [], scan_artifact_secrets(draft)


def test_normalize_carries_api_template_and_never_enables():
    wacz = _make_wacz(_capture_with_download_api())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    cand = tn.normalize_draft(draft)
    assert cand.get("selectors", {}).get("download", {}).get("api_template", "").startswith("https://api2.example.com/")
    assert cand.get("status") != "enabled"
    assert "enabled" not in cand
    assert scan_artifact_secrets(cand) == []
