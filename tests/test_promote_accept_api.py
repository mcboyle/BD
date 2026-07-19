"""A6-1 reviewer affordance — promote_draft ``accept_api`` gate preservation.

The runtime ``api`` block is what ungates ``template_assist.build_api_url``
(gated v3.66.155 / v3.66.157). These tests pin the gate-preservation contract:

  * default (accept_api omitted/False): NO ``api`` block is written — the gate
    stays closed, byte-for-byte the prior promote behaviour, build_api_url None;
  * accept_api=True with a candidate whose base host WAS observed: the concrete
    ``api`` block is materialized and build_api_url expands a reviewed path;
  * accept_api=True with a candidate base host that was NOT observed: the whole
    promote is REFUSED (never a silent API-less or unverified-host success);
  * accept_api=True with no api_candidate at all: refused;
  * list_templates surfaces a secret-free api_candidate summary so the SPA can
    offer the affordance only when a candidate exists.

Synthetic fixtures only; browser-free; stdlib + project modules. Zero-arg test
functions + tempfile.mkdtemp so it runs under the custom runner and pytest.
"""
import json
import tempfile
from pathlib import Path

from bulk_downloader import template_manager as tm
from bulk_downloader.template_assist import build_api_url


def _draft(*, with_api=True, base_host="api.example.com"):
    d = {
        "schema": "bulk_downloader.template.review_candidate.v1",
        "host": "example.com",
        "status": "draft_review_required",
        "selectors": {
            "download": {"trigger": ".dl", "row_selectors": [".row"]},
            "player": {"play_button": ".play"},
        },
        "resolutions": [1080, 720],
        # a media-relevant pattern so the promote readiness gate (resolutions +
        # a meaningful pattern + download selector) passes; the api_candidate
        # path under test is orthogonal to it.
        "network_patterns": ["https://example.com/video/play.mp4"],
        "observed_api_hosts": ["api.example.com"],
        "api_base_candidate": "https://api.example.com",
    }
    if with_api:
        d["api_candidate"] = {
            "base": "https://%s/v1" % base_host,
            "movie": "/movie/{id}/sources",
            "listing": "/list/{page}",
        }
    return d


def _dirs():
    root = Path(tempfile.mkdtemp())
    rd = root / "templates" / "reviewed"
    dd = root / "templates" / "drafts"
    dd.mkdir(parents=True)
    return rd, dd


def _write(dd, draft, name="example.com.template-draft.json"):
    (dd / name).write_text(json.dumps(draft), "utf-8")
    return name


def _reviewed(rd, name="example.com.template.json"):
    return json.loads((rd / name).read_text("utf-8"))


# ── default: gate stays closed ───────────────────────────────────────────────

def test_default_promote_writes_no_api_block():
    rd, dd = _dirs()
    name = _write(dd, _draft())
    res = tm.promote_draft(name, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert res.get("api_accepted") is False
    t = _reviewed(rd)
    assert "api" not in t, "default promote must not materialize a runtime api block"
    # the gate: no api block -> build_api_url returns None
    assert build_api_url(t, "movie", id="42") is None


def test_accept_api_false_explicit_also_no_api_block():
    rd, dd = _dirs()
    name = _write(dd, _draft())
    res = tm.promote_draft(name, accept_api=False, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert "api" not in _reviewed(rd)


# ── accept_api=True with an observed host: materialize + ungate ──────────────

def test_accept_api_materializes_and_ungates():
    rd, dd = _dirs()
    name = _write(dd, _draft())
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert res.get("api_accepted") is True
    t = _reviewed(rd)
    assert t.get("api", {}).get("base") == "https://api.example.com/v1"
    assert t["api"].get("movie") == "/movie/{id}/sources"
    # gate now open for this reviewed template
    assert build_api_url(t, "movie", id="42") == \
        "https://api.example.com/v1/movie/42/sources"


# ── gate preservation: unobserved host is refused ────────────────────────────

def test_accept_api_refuses_unobserved_host():
    rd, dd = _dirs()
    # candidate base points at a host that was NOT in observed_api_hosts
    name = _write(dd, _draft(base_host="evil.example.net"))
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert not res.get("ok"), "unobserved host must refuse"
    assert "observ" in (res.get("error") or "").lower()
    # and nothing was written
    assert not (rd / "example.com.template.json").exists()


def test_accept_api_refuses_when_no_candidate():
    rd, dd = _dirs()
    name = _write(dd, _draft(with_api=False))
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert not res.get("ok"), "no candidate + accept_api must refuse"
    assert "api_candidate" in (res.get("error") or "")
    assert not (rd / "example.com.template.json").exists()


def test_accept_api_refuses_candidate_with_no_paths():
    rd, dd = _dirs()
    d = _draft()
    d["api_candidate"] = {"base": "https://api.example.com/v1"}  # base only
    name = _write(dd, d)
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert not res.get("ok"), "candidate with no endpoint paths must refuse"


# ── gate integrity: a smuggled api block can never bypass the accept path ─────

def test_smuggled_api_block_stripped_when_not_accepted():
    rd, dd = _dirs()
    d = _draft(with_api=False)  # no legitimate candidate
    # tampered/hand-authored draft smuggles a runtime api block for an unobserved host
    d["api"] = {"base": "https://evil.example.net/v1", "steal": "/exfil/{id}"}
    name = _write(dd, d)
    res = tm.promote_draft(name, accept_api=False, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert res.get("api_accepted") is False
    t = _reviewed(rd)
    assert "api" not in t, "smuggled api block must be stripped, gate stays closed"
    assert build_api_url(t, "steal", id="42") is None


def test_smuggled_api_block_does_not_survive_even_with_accept_when_no_candidate():
    rd, dd = _dirs()
    d = _draft(with_api=False)
    d["api"] = {"base": "https://evil.example.net/v1", "steal": "/exfil/{id}"}
    name = _write(dd, d)
    # accept_api=True but no legitimate candidate -> whole promote refused
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert not res.get("ok")
    assert not (rd / "example.com.template.json").exists()


def test_accept_uses_validated_candidate_not_smuggled_block():
    rd, dd = _dirs()
    d = _draft()  # legitimate candidate -> api.example.com (observed)
    d["api"] = {"base": "https://evil.example.net/v1", "steal": "/exfil/{id}"}
    name = _write(dd, d)
    res = tm.promote_draft(name, accept_api=True, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok") and res.get("api_accepted") is True
    t = _reviewed(rd)
    # the materialized block is the validated candidate, NOT the smuggled one
    assert t["api"]["base"] == "https://api.example.com/v1"
    assert "steal" not in t["api"]


def test_list_templates_surfaces_api_candidate_summary():
    rd, dd = _dirs()
    _write(dd, _draft())
    listing = tm.list_templates(reviewed_dir=rd, drafts_dir=dd)
    drafts = listing.get("drafts") or []
    assert len(drafts) == 1
    ac = drafts[0].get("api_candidate")
    assert ac and ac.get("base") == "https://api.example.com/v1"
    assert sorted(ac.get("endpoints") or []) == ["listing", "movie"]


def test_list_templates_api_candidate_none_when_absent():
    rd, dd = _dirs()
    _write(dd, _draft(with_api=False))
    listing = tm.list_templates(reviewed_dir=rd, drafts_dir=dd)
    assert listing["drafts"][0].get("api_candidate") is None
