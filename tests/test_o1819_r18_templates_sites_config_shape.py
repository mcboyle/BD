"""O1819 R18 — cockpit_templates must read the sites_config.json shape the app
actually persists.

bulk_downloader/app.py _save_sites_config writes {site_id: cfg} and CFG_FIELDS
carries no "id" (the key is the id). The loader only accepted a bare list or
{"sites": [...]}, so every real config read as [] and every template panel was
silently empty.
"""
import json

from tools import cockpit_templates as ct

BD_GATE_SCOPE = "module"

_CFG = {"name": "Example Site", "login_url": "https://example.test/login",
        "learned": {"download": {"row_selectors": ["a.dl", "a.video"]}}}


def _use(monkeypatch, tmp_path, data):
    p = tmp_path / "sites_config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(p))


def test_app_persisted_mapping_shape_is_read(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path, {"7": _CFG})
    rows = ct.video_template_health()["sites"]
    assert [r["site"] for r in rows] == ["7"], (
        "O1819-R18: app-shaped {site_id: cfg} sites_config read as "
        f"{[r['site'] for r in rows]!r}; expected the key as the site id")
    assert rows[0]["template_present"] is True


def test_explicit_id_in_mapping_entry_wins(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path, {"7": dict(_CFG, id="explicit")})
    assert [ct._site_id(c) for c in ct._load_sites_config()] == ["explicit"]


def test_list_and_sites_wrapper_shapes_still_read(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path, [dict(_CFG, id="a")])
    assert [ct._site_id(c) for c in ct._load_sites_config()] == ["a"]
    _use(monkeypatch, tmp_path, {"sites": [dict(_CFG, id="b")]})
    assert [ct._site_id(c) for c in ct._load_sites_config()] == ["b"]


def test_non_dict_mapping_values_are_skipped(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path, {"7": _CFG, "junk": "x", "n": None})
    assert [ct._site_id(c) for c in ct._load_sites_config()] == ["7"]
