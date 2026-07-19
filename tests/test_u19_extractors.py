"""U19 — extractor capability matrix (D-31) + fast-path simulator
(D-39). Both read extractors._REGISTRY; the sim also exercises the
real is_supported_url host matcher.
"""
import types

from bulk_downloader import dev_suite as ds
from bulk_downloader import extractors as _ex


def _a_registered_host_url():
    """Build a URL whose host matches a registered extractor."""
    sid = sorted(_ex._REGISTRY)[0]
    # pornhub etc. — a plain https URL on the known site works for the
    # registry's host regexes; use is_supported_url to pick one.
    for cand in ("https://www.pornhub.com/view_video?viewkey=x",
                 "https://www.xvideos.com/video1",
                 "https://beeg.com/12345"):
        if _ex.is_supported_url(cand):
            return cand, _ex.is_supported_url(cand)
    return None, None


# ── extractor_matrix (D-31) ───────────────────────────────────────

def test_extractor_matrix_covers_the_whole_registry():
    r = ds.extractor_matrix()
    assert r["ok"] is True
    assert r["registered_extractors"] == len(_ex._REGISTRY)
    assert (r["libraries_installed"] + r["libraries_missing"]
            == r["registered_extractors"])


def test_extractor_matrix_rows_carry_library_and_host():
    r = ds.extractor_matrix()
    for row in r["extractors"]:
        if "error" in row:
            continue
        assert row["library"]
        assert row["host_pattern"]
        assert isinstance(row["library_installed"], bool)


# ── extractor_fastpath_sim (D-39) ─────────────────────────────────

def test_fastpath_sim_requires_a_url():
    assert ds.extractor_fastpath_sim("")["ok"] is False


def test_fastpath_sim_unmatched_host_takes_teach_path():
    r = ds.extractor_fastpath_sim("https://example.invalid/scene/9")
    assert r["ok"] is True
    assert r["matched"] is False
    assert r["fast_path_would_fire"] is False
    assert "teach path" in r["verdict"]


def test_fastpath_sim_matched_host_reports_extractor():
    url, sid = _a_registered_host_url()
    assert url is not None, "no registered extractor host available"
    r = ds.extractor_fastpath_sim(url)
    assert r["matched"] is True
    assert r["matched_site_id"] == sid
    assert r["library"]
    assert isinstance(r["library_installed"], bool)
    # no site given -> config flag not known
    assert r["config_use_library_extractor"] is None


def test_fastpath_sim_with_site_reads_config_flag():
    url, _sid = _a_registered_host_url()
    assert url is not None
    runners = {"s1": types.SimpleNamespace(
        config={"use_library_extractor": True})}
    r = ds.extractor_fastpath_sim(url, site_id="s1", runners=runners)
    assert r["config_use_library_extractor"] is True
    # when the library is missing, the flag being on still can't fire
    if not r["library_installed"]:
        assert r["fast_path_would_fire"] is False


# ── routes ────────────────────────────────────────────────────────

def test_extractor_matrix_route(fresh_app):
    resp = fresh_app.get("/api/dev/extractor_matrix")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "extractor_matrix"


def test_extractor_fastpath_route(fresh_app):
    resp = fresh_app.get(
        "/api/dev/extractor_fastpath?url=https://example.invalid/x")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["matched"] is False


def test_extractor_fastpath_route_needs_url(fresh_app):
    resp = fresh_app.get("/api/dev/extractor_fastpath")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False
