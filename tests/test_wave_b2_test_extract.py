"""Wave B2 — real-extraction "Test (live)" draft-override enforced-contract tests.

B2 (v3.66.240) is the FIRST path that lets an UNREVIEWED draft drive a real
download through ``_process_one``. The draft reaches the extractor only via a
per-site override (``config["draft_test_override"]``) read at the single
``merge_template_download_hints`` hook (runner.py); the enabled-only matcher
(``find_template_for_url``) still cannot return a draft.

These tests enforce the four locked operator decisions as a contract. Two of
them are recorded here with the corrections found during the build-session
source reads (re-derived from the v3.66.239 tree):

  * Invariant 2 (persist-bypass is opt-in) — the learned-selector persist
    surface is wider than "two sites": it fires at FOUR chokepoints in
    runner.py (login takeover, download takeover, teach-commit add+save, and
    drift-recovery prune+save). All four are gated by the SAME run-scoped guard
    ``_override_suppresses_persist()``. We test that guard behaviourally (the
    surface-independent property) AND assert every chokepoint references it, so
    a regression that drops a gate is caught. The full both-writes-on-a-real-
    download proof is the stash-only live verify (no network in-sandbox).

  * Invariant 3 (challenge -> manual handoff) — the literal "never auto-solves"
    is not true of the code: ``_handle_captcha_check`` calls
    ``_try_captcha_solve`` BEFORE the manual handoff. But that solver is
    DEFAULT-OFF (no-op unless the operator sets ``captcha_api_key``) and B2 adds
    ZERO uplift — an override run hits the identical branch a normal run does.
    The correct, verifiable invariant is PARITY: the override path inherits the
    unchanged challenge branch (fail-open manual handoff in the default and
    in-sandbox no-key posture); B2 introduces no auto-solve and no bypass.

Group A tests bind the relevant SiteRunner methods to a stub (they read only
``self.config`` / ``self.log``) so no DB/queue/network is needed. Group B boots
the main BD app and drives the new route via a test client, seeding ``s_cfg``
WITHOUT a runner so no worker spins (``started`` is False) and no network is
touched.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Group B boots the main app; wipe bd modules between tests for a clean import.
pytestmark = pytest.mark.bd_module_wipe


# --- a flat draft template the override accepts (selectors->download shape) ---
_DRAFT = {
    "host": "draft-host.test",
    "status": "draft",
    "selectors": {
        "download": {
            "trigger": "button.play-draft",
            "row_selectors": ["a.dl-draft"],
        }
    },
}


class _Stub:
    """Minimal stand-in carrying just the attributes the B2 helpers read."""

    def __init__(self, cfg):
        self.config = cfg
        self.log = logging.getLogger("b2test")
        self.site_id = "stub"


def _bind_helpers():
    """Bind the three B2 SiteRunner helpers onto the stub from the fresh
    (module-wiped) runner import."""
    from bulk_downloader.runner import SiteRunner
    _Stub._draft_override_template = SiteRunner._draft_override_template
    _Stub._override_suppresses_persist = SiteRunner._override_suppresses_persist
    _Stub._persist_learned_to_draft = SiteRunner._persist_learned_to_draft
    _Stub._try_captcha_solve = SiteRunner._try_captcha_solve


# ====================================================================
# Group A — helper + hook-branch units (no app boot)
# ====================================================================

def test_override_branch_uses_draft_directly():
    """The override is a SEPARATE branch: with override_template set, the draft
    drives the hints even though the page would match no enabled template."""
    import bulk_downloader.template_assist as ta

    class _Page:
        url = ""  # get_template_for_page would return None for this

    merged, tmpl = ta.merge_template_download_hints(
        _Page(), {}, override_template=_DRAFT)
    assert tmpl is _DRAFT
    assert "button.play-draft" in (merged.get("trigger_selectors") or [])
    assert "a.dl-draft" in (merged.get("row_selectors") or [])
    assert merged.get("_template_host") == "draft-host.test"


def test_no_override_uses_matcher_path_unchanged():
    """With no override, the enabled-only matcher path is used (byte-identical
    behaviour). An unknown host yields no template -> (learned, None)."""
    import bulk_downloader.template_assist as ta

    class _Page:
        url = "http://definitely-not-a-real-host-xyz.invalid/watch"

    learned = {"download": {"row_selectors": ["a.kept"]}}
    merged, tmpl = ta.merge_template_download_hints(_Page(), learned)
    assert tmpl is None
    # learned passed straight through (no template merged on top)
    assert merged == learned


def test_override_suppresses_persist_three_cases():
    """The single run-scoped guard every persist chokepoint consults.
    OFF override -> suppress (True); ON override -> allow (False);
    no override -> allow (False)."""
    _bind_helpers()
    off = _Stub({"draft_test_override": {"template": _DRAFT, "persist": False}})
    on = _Stub({"draft_test_override": {"template": _DRAFT, "persist": True}})
    none = _Stub({})
    assert off._override_suppresses_persist() is True
    assert on._override_suppresses_persist() is False
    assert none._override_suppresses_persist() is False


def test_draft_override_template_read():
    """_draft_override_template returns the template dict when set, else None."""
    _bind_helpers()
    s = _Stub({"draft_test_override": {"template": _DRAFT, "persist": False}})
    assert s._draft_override_template() is _DRAFT
    assert _Stub({}).  _draft_override_template() is None
    # malformed (no template key) -> None
    assert _Stub({"draft_test_override": {"persist": True}})._draft_override_template() is None


def test_persist_learned_to_draft_on_writes_keeps_authored():
    """Persist ON: the run's learned block is written onto the draft JSON under
    a 'learned' key; the draft's AUTHORED selectors are left intact."""
    _bind_helpers()
    import bulk_downloader.template_manager as tm
    tmp = Path(tempfile.mkdtemp(prefix="b2drafts_"))
    fname = "draft-host.test.template-draft.json"
    (tmp / fname).write_text(json.dumps(dict(_DRAFT)), encoding="utf-8")
    orig_dir = tm.DRAFTS_DIR
    try:
        tm.DRAFTS_DIR = tmp
        learned = {"download": {"row_selectors": ["a.learned-row"],
                                 "trigger_selectors": ["button.learned"]}}
        s = _Stub({"draft_test_override": {"template": _DRAFT,
                                            "persist": True,
                                            "draft_file": fname},
                   "learned": learned})
        s._persist_learned_to_draft()
        after = json.loads((tmp / fname).read_text(encoding="utf-8"))
        assert after.get("learned") == learned          # learned written back
        assert after["selectors"]["download"]["trigger"] == "button.play-draft"  # authored intact
    finally:
        tm.DRAFTS_DIR = orig_dir


def test_persist_learned_to_draft_off_is_noop():
    """Persist OFF (and missing draft_file): the draft is never written."""
    _bind_helpers()
    import bulk_downloader.template_manager as tm
    tmp = Path(tempfile.mkdtemp(prefix="b2drafts_"))
    fname = "draft-host.test.template-draft.json"
    body = json.dumps(dict(_DRAFT))
    (tmp / fname).write_text(body, encoding="utf-8")
    orig_dir = tm.DRAFTS_DIR
    try:
        tm.DRAFTS_DIR = tmp
        # persist OFF
        _Stub({"draft_test_override": {"template": _DRAFT, "persist": False,
                                        "draft_file": fname},
               "learned": {"download": {"row_selectors": ["x"]}}}
              )._persist_learned_to_draft()
        assert (tmp / fname).read_text(encoding="utf-8") == body
        # persist ON but no draft_file recorded -> still a no-op
        _Stub({"draft_test_override": {"template": _DRAFT, "persist": True},
               "learned": {"download": {"row_selectors": ["x"]}}}
              )._persist_learned_to_draft()
        assert (tmp / fname).read_text(encoding="utf-8") == body
    finally:
        tm.DRAFTS_DIR = orig_dir


def test_captcha_solver_failopen_default_and_override_parity():
    """Invariant 3 (parity): with no captcha_api_key (the default and the
    in-sandbox posture), _try_captcha_solve returns False -> the flow falls
    through to the manual handoff. An active override does NOT change that:
    B2 adds no auto-solve and no bypass."""
    _bind_helpers()
    # no key -> returns False before touching the page (page arg unused)
    assert _Stub({})._try_captcha_solve(None) is False
    # override active, still no key -> identical fail-open result
    s = _Stub({"draft_test_override": {"template": _DRAFT, "persist": False}})
    assert s._try_captcha_solve(None) is False


def test_all_persist_chokepoints_gated_and_hook_wired():
    """Surface-independent regression guard: every learned-selector persist
    chokepoint references the run-scoped no-persist guard, and the hook passes
    the override through. Catches a future edit that drops a gate."""
    import bulk_downloader.runner as runner
    # v3.66.399 (PHASE 3 runner cut 3): the persist chokepoints now live across
    # runner.py (core) + the extracted mixin modules (download takeover ->
    # runner_manual, teach commit/drift/draft -> runner_teach). Read the aggregate
    # so every gate literal is still found regardless of which module owns it.
    _rdir = Path(runner.__file__).parent
    src = "\n".join(q.read_text(encoding="utf-8")
                    for q in [_rdir / "runner.py"] + sorted(_rdir.glob("runner_*.py")))
    expected_guards = [
        # CP1 login takeover
        "if (learned_count or cred_captured or url_captured) and not self._override_suppresses_persist():",
        # CP2 download takeover
        "if learned_count and not self._override_suppresses_persist():",
        # CP3 teach commit
        "if n_roles and not self._override_suppresses_persist():",
        # CP4 drift recovery prune+save
        "if misses > hits*2 and not self._override_suppresses_persist():",
    ]
    for g in expected_guards:
        assert g in src, f"missing persist gate: {g}"
    # the hook feeds the per-site override into the single chokepoint
    assert "override_template=self._draft_override_template()" in src


# ====================================================================
# Group B — new route, via the main-app test client (invariants 1 & 4)
# ====================================================================

def _boot():
    """Fresh main-app import + DB init + a test client. No runner is seeded by
    callers, so the route never starts a worker."""
    import bulk_downloader.app as bdapp
    try:
        from bulk_downloader import db
        db.db_init()
    except Exception:
        pass
    bdapp.app.config["TESTING"] = True
    return bdapp, bdapp.app.test_client()


def _seed_site(bdapp, sid="b2site"):
    cfg = {"name": "B2 Test Site", "url": "https://draft-host.test/"}
    bdapp.s_cfg[sid] = cfg
    bdapp.s_meta[sid] = bdapp._build_meta(cfg)
    return sid


def test_route_sets_override_and_standing_indicator():
    """Invariant 4 (set): the route stores the per-site override and the meta
    surfaces the compact standing indicator; the bulky override dict is not
    leaked into meta. No runner seeded -> started is False."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    r = client.post("/api/template/test_extract",
                    json={"site_id": sid, "template": _DRAFT, "persist": False})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] and body["override_set"] is True
    assert body["started"] is False        # no runner -> no worker spun
    ov = bdapp.s_cfg[sid].get("draft_test_override")
    assert ov and ov["template"] == _DRAFT and ov["persist"] is False
    meta = bdapp._build_meta(bdapp.s_cfg[sid])
    assert meta["draft_test_override_active"] is True
    assert meta["draft_test_override_persist"] is False
    assert "draft_test_override" not in meta   # bulky dict stripped from meta


def test_route_persist_toggle_on_is_stored():
    """Invariant 2 (ON path, route level): persist=True is stored and threaded;
    the guard then evaluates to 'allow'."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    r = client.post("/api/template/test_extract",
                    json={"site_id": sid, "template": _DRAFT, "persist": True,
                          "draft_file": "draft-host.test.template-draft.json"})
    assert r.status_code == 200, r.data
    assert r.get_json()["persist"] is True
    ov = bdapp.s_cfg[sid]["draft_test_override"]
    assert ov["persist"] is True
    assert bdapp._build_meta(bdapp.s_cfg[sid])["draft_test_override_persist"] is True


def test_route_per_site_isolation():
    """Invariant 4: the override is keyed per-site; setting it on A leaves B
    untouched."""
    bdapp, client = _boot()
    a = _seed_site(bdapp, "siteA")
    b = _seed_site(bdapp, "siteB")
    client.post("/api/template/test_extract",
                json={"site_id": a, "template": _DRAFT})
    assert bdapp._build_meta(bdapp.s_cfg[a])["draft_test_override_active"] is True
    assert bdapp._build_meta(bdapp.s_cfg[b])["draft_test_override_active"] is False


def test_override_survives_simulated_restart():
    """Invariant 4: the override survives a REAL restart, not merely a disk
    write. _load_sites_config rebuilds each cfg from CFG_FIELDS, and
    draft_test_override is intentionally not a CFG_FIELDS entry, so a naive load
    SILENTLY DROPPED it (Decision 4 break). The load-side passthrough carries it
    back; this exercises the full round trip: save -> clear in-memory state ->
    _load_sites_config() -> assert the override (and its persist flag) restored,
    and the standing indicator reflects it post-restart."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    client.post("/api/template/test_extract",
                json={"site_id": sid, "template": _DRAFT, "persist": True,
                      "draft_file": "draft-host.test.template-draft.json"})
    # save side: present on disk
    on_disk = json.loads(bdapp.SITES_FILE.read_text(encoding="utf-8"))
    assert on_disk[sid].get("draft_test_override", {}).get("template") == _DRAFT
    # simulate a real process restart: drop in-memory state and reload from disk
    bdapp.s_cfg.clear()
    bdapp.s_meta.clear()
    bdapp.runners.clear()
    bdapp._load_sites_config()
    # load side: the override must come back THROUGH the CFG_FIELDS rebuild
    ov = bdapp.s_cfg.get(sid, {}).get("draft_test_override")
    assert ov and ov.get("template") == _DRAFT and ov.get("persist") is True
    meta = bdapp._build_meta(bdapp.s_cfg[sid])
    assert meta["draft_test_override_active"] is True
    assert meta["draft_test_override_persist"] is True


def test_route_clear_removes_override():
    """Invariant 4 (teardown): an explicit clear removes the override and the
    standing indicator goes false."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    client.post("/api/template/test_extract",
                json={"site_id": sid, "template": _DRAFT})
    assert bdapp.s_cfg[sid].get("draft_test_override")
    r = client.post("/api/template/test_extract",
                    json={"site_id": sid, "clear": True})
    assert r.status_code == 200 and r.get_json()["cleared"] is True
    assert bdapp.s_cfg[sid].get("draft_test_override") is None
    assert bdapp._build_meta(bdapp.s_cfg[sid])["draft_test_override_active"] is False


def test_route_never_enables_or_writes_reviewed():
    """Invariant 1: test_extract enables nothing. The draft's status is
    untouched, no reviewed/enabled file is written, and the response carries no
    promote/enable signal. Only /api/template_manager/promote enables."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    import bulk_downloader.template_manager as tm
    before_reviewed = sorted(p.name for p in Path(tm.REVIEWED_DIR).glob("*")) \
        if Path(tm.REVIEWED_DIR).exists() else []
    r = client.post("/api/template/test_extract",
                    json={"site_id": sid, "template": _DRAFT})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    # no enable/promote signal in the response
    assert "promoted" not in body and "enabled" not in body
    # the override copy keeps the draft status as a draft (never "enabled")
    assert bdapp.s_cfg[sid]["draft_test_override"]["template"].get("status") != "enabled"
    after_reviewed = sorted(p.name for p in Path(tm.REVIEWED_DIR).glob("*")) \
        if Path(tm.REVIEWED_DIR).exists() else []
    assert before_reviewed == after_reviewed   # reviewed dir unchanged


def test_route_input_validation():
    """Unknown site, non-http url, and a missing template are all refused."""
    bdapp, client = _boot()
    sid = _seed_site(bdapp)
    # unknown site
    assert client.post("/api/template/test_extract",
                       json={"site_id": "nope", "template": _DRAFT}).status_code == 404
    # non-http(s) url
    assert client.post("/api/template/test_extract",
                       json={"site_id": sid, "template": _DRAFT,
                             "url": "ftp://x/y"}).status_code == 400
    # missing template (and not a clear)
    assert client.post("/api/template/test_extract",
                       json={"site_id": sid}).status_code == 400
