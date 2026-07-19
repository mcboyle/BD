"""Cut 3 (v3.66.post-365 UI/UX) — read-only import-PREVIEW for user templates.

`preview_user_templates_import(payload, merge)` classifies what an import WOULD
do without writing anything. Contract:

    {
      ok: bool,                       # False only on a malformed payload
      mode: "merge"|"replace",
      counts: {new, changed, conflict, invalid, destructive, secrets_omitted},
      items: [{id, name, status, secrets_omitted: [keys], error?}],
      destructive: [{id, name}],      # existing templates a replace WOULD remove
      errors: [str],                  # payload-level notes (e.g. all-invalid refusal)
    }

Status semantics:
  merge   : new | conflict (id collision -> skipped) | invalid
  replace : new | changed (id exists -> overwritten) | invalid; existing ids
            absent from the incoming valid set are listed in `destructive`.

RED on pristine 372: `preview_user_templates_import` does not exist yet.

Disk handling mirrors test_user_templates.py: rebind USER_TEMPLATES_FILE to a
tempdir per test, and ALSO assert the store is byte-unchanged after the preview
(the whole point is that preview never writes).
"""
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _with_temp_user_templates():
    from bulk_downloader import user_templates as ut
    with tempfile.TemporaryDirectory() as td:
        orig = ut.USER_TEMPLATES_FILE
        ut.USER_TEMPLATES_FILE = Path(td) / "user_templates.json"
        try:
            yield ut
        finally:
            ut.USER_TEMPLATES_FILE = orig


def _good_learned():
    return {"download": {"row_selectors": ["a.btn[href]"], "url_attribute": "href"}}


def _store_bytes(ut):
    """Raw on-disk bytes of the store (b'' if the file doesn't exist)."""
    p = ut.USER_TEMPLATES_FILE
    return p.read_bytes() if p.exists() else b""


def _incoming(tid, name="Imported"):
    return {
        "id": tid,
        "name": name,
        "description": "from elsewhere",
        "patterns": [r"imp\.com"],
        "learned": _good_learned(),
    }


# ── merge mode ──────────────────────────────────────────────────────

def test_preview_merge_new_template_is_new_and_writes_nothing():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Existing", "d", [], _good_learned())
        before = _store_bytes(ut)
        payload = {"version": 1, "templates": [_incoming("user_new_xyz")]}
        out = ut.preview_user_templates_import(payload, merge=True)
        assert out["ok"] is True, out
        assert out["mode"] == "merge"
        assert out["counts"]["new"] == 1
        assert out["counts"]["conflict"] == 0
        assert out["counts"]["destructive"] == 0
        statuses = {i["id"]: i["status"] for i in out["items"]}
        assert statuses["user_new_xyz"] == "new"
        # PREVIEW MUST NOT WRITE
        assert _store_bytes(ut) == before
        assert len(ut.list_user_templates()) == 1


def test_preview_merge_id_collision_is_conflict():
    with _with_temp_user_templates() as ut:
        ok, t = ut.save_user_template("Original", "d", [], _good_learned())
        before = _store_bytes(ut)
        payload = {"version": 1, "templates": [_incoming(t["id"], "Would-be dup")]}
        out = ut.preview_user_templates_import(payload, merge=True)
        assert out["counts"]["conflict"] == 1
        assert out["counts"]["new"] == 0
        item = next(i for i in out["items"] if i["id"] == t["id"])
        assert item["status"] == "conflict"
        assert _store_bytes(ut) == before
        # existing untouched
        assert ut.get_user_template(t["id"])["name"] == "Original"


def test_preview_merge_invalid_is_reported_not_raised():
    with _with_temp_user_templates() as ut:
        before = _store_bytes(ut)
        bad = {"id": "user_bad", "name": "Bad"}  # no learned/patterns -> invalid
        out = ut.preview_user_templates_import({"templates": [bad]}, merge=True)
        assert out["counts"]["invalid"] == 1
        item = next(i for i in out["items"] if i["id"] == "user_bad")
        assert item["status"] == "invalid"
        assert item.get("error")
        assert _store_bytes(ut) == before


# ── replace mode ────────────────────────────────────────────────────

def test_preview_replace_lists_destructive_removals():
    with _with_temp_user_templates() as ut:
        _, a = ut.save_user_template("Keep-or-drop A", "d", [], _good_learned())
        _, b = ut.save_user_template("Keep-or-drop B", "d", [], _good_learned())
        before = _store_bytes(ut)
        # incoming reuses A's id (changed) + one brand-new; B is absent -> destructive
        payload = {"version": 1, "templates": [
            _incoming(a["id"], "A replaced"),
            _incoming("user_brand_new", "New one"),
        ]}
        out = ut.preview_user_templates_import(payload, merge=False)
        assert out["mode"] == "replace"
        st = {i["id"]: i["status"] for i in out["items"]}
        assert st[a["id"]] == "changed"
        assert st["user_brand_new"] == "new"
        destructive_ids = {d["id"] for d in out["destructive"]}
        assert b["id"] in destructive_ids
        assert a["id"] not in destructive_ids
        assert out["counts"]["destructive"] == 1
        assert _store_bytes(ut) == before  # still no write


def test_preview_replace_all_invalid_nonempty_is_refused_no_destructive():
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Precious", "d", [], _good_learned())
        before = _store_bytes(ut)
        payload = {"templates": [{"id": "x", "name": "bad"}]}  # invalid only
        out = ut.preview_user_templates_import(payload, merge=False)
        # A real import would refuse to wipe -> preview must NOT report the
        # existing template as destructive, and must surface the refusal.
        assert out["counts"]["destructive"] == 0
        assert any("refus" in e.lower() or "wipe" in e.lower() for e in out["errors"]), out["errors"]
        assert _store_bytes(ut) == before


# ── secrets-omitted signal ──────────────────────────────────────────

def test_preview_reports_secrets_omitted_per_item():
    with _with_temp_user_templates() as ut:
        tmpl = _incoming("user_with_secret", "Has secret")
        tmpl["password"] = "hunter2"      # a secret-like field that import drops
        tmpl["cookies_b64"] = "AAAA"
        out = ut.preview_user_templates_import({"templates": [tmpl]}, merge=True)
        item = next(i for i in out["items"] if i["id"] == "user_with_secret")
        assert "password" in item["secrets_omitted"]
        assert "cookies_b64" in item["secrets_omitted"]
        assert out["counts"]["secrets_omitted"] >= 1


# ── malformed payload ───────────────────────────────────────────────

def test_preview_malformed_payload_is_not_ok():
    with _with_temp_user_templates() as ut:
        out = ut.preview_user_templates_import({"nope": 1}, merge=True)
        assert out["ok"] is False
        assert out["errors"]
