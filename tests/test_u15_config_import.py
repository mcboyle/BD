"""U15 — config schema audit (D-86) + import-file preflight (D-89).

Both tools reuse site_editor.validate_config / csv_bulk.parse_import
rather than reimplementing validation; these tests cover the
fleet-wide aggregation and the import-row layer that is the genuine
new surface.
"""
import types

from bulk_downloader import dev_suite as ds


def _runner(cfg):
    return types.SimpleNamespace(config=cfg)


# ── config_schema_audit (D-86) ────────────────────────────────────

def test_config_audit_flags_errors_and_clean_sites():
    # "good" is genuinely pristine — creds AND selectors set, so
    # validate_config emits neither errors nor warnings.
    cfgs = {
        "good": {"name": "Good",
                 "login_url": "https://good.example/login",
                 "username": "u", "password": "p",
                 "user_field": "#u", "pass_field": "#p",
                 "submit_btn": "#go"},
        "bad_url": {"name": "Bad", "login_url": "not-a-url"},
        "noname": {"login_url": "https://x.example"},
    }
    r = ds.config_schema_audit(site_configs=cfgs)
    assert r["ok"] is True
    assert r["sites_total"] == 3
    assert r["sites_with_errors"] == 2          # bad_url + noname
    assert r["sites_clean"] == 1                # good (no errors/warnings)
    by_id = {s["site_id"]: s for s in r["sites"]}
    assert by_id["good"]["ok"] is True
    assert by_id["good"]["warnings"] == []
    assert by_id["bad_url"]["ok"] is False


def test_config_audit_detects_list_in_scalar_field():
    cfgs = {"s": {"name": "S", "wait": [1, 2, 3]}}
    r = ds.config_schema_audit(site_configs=cfgs)
    assert r["sites_with_type_mismatches"] == 1
    mismatches = r["sites"][0]["type_mismatches"]
    assert any("wait" in m for m in mismatches)


def test_config_audit_reads_from_runners():
    runners = {"a": _runner({"name": "A",
                             "login_url": "https://a.example/login"})}
    r = ds.config_schema_audit(runners=runners)
    assert r["config_source"] == "live runners"
    assert r["sites_total"] == 1


def test_config_audit_empty_when_nothing_to_read():
    r = ds.config_schema_audit(runners={})
    assert r["ok"] is True
    assert r["sites_total"] == 0


# ── import_preflight (D-89) ───────────────────────────────────────

_GOOD_CSV = (
    "name,login_url,username,password,login_template\n"
    "Site One,https://one.example/login,u1,p1,\n"
    "Site Two,https://two.example/login,u2,p2,\n"
)


def test_preflight_clean_file_is_safe_to_import():
    r = ds.import_preflight(text=_GOOD_CSV)
    assert r["ok"] is True
    assert r["rows_parsed"] == 2
    assert r["rows_with_errors"] == 0
    assert r["safe_to_import"] is True


def test_preflight_flags_in_file_duplicate_url():
    csv = _GOOD_CSV + "Dup,https://one.example/login,u,p,\n"
    r = ds.import_preflight(text=csv)
    assert r["duplicate_urls_in_file"] == 1
    assert r["safe_to_import"] is False
    dup_row = [x for x in r["rows"] if x["name"] == "Dup"][0]
    assert any("duplicate" in e for e in dup_row["errors"])


def test_preflight_flags_collision_with_existing_site():
    r = ds.import_preflight(
        text=_GOOD_CSV,
        existing_urls=["https://two.example/login"])
    assert r["collisions_with_existing_sites"] == 1
    assert r["safe_to_import"] is False


def test_preflight_flags_bad_url_and_unknown_template():
    csv = ("name,login_url,username,password,login_template\n"
           "Bad,not-a-url,u,p,no_such_template\n")
    r = ds.import_preflight(text=csv,
                            known_login_templates=["login_real"])
    assert r["rows_with_errors"] == 1
    assert r["unknown_login_templates"] == 1
    row = r["rows"][0]
    assert any("URL" in e for e in row["errors"])
    assert any("template" in w for w in row["warnings"])


def test_preflight_suppresses_inapplicable_selector_warnings():
    # An import row cannot carry login selectors — the "credentials
    # set but selector empty" warning must NOT fire here.
    r = ds.import_preflight(text=_GOOD_CSV)
    for row in r["rows"]:
        assert all("selector ('" not in w for w in row["warnings"])


def test_preflight_requires_an_input():
    r = ds.import_preflight()
    assert r["ok"] is False


def test_preflight_rejects_non_csv_xlsx_path():
    r = ds.import_preflight(path="something.txt")
    assert r["ok"] is False
    assert "csv" in r["error"].lower()


def test_preflight_reports_parse_errors_for_headerless_file():
    r = ds.import_preflight(text="just,some,random,columns\na,b,c,d\n")
    # no login_url column -> a structural parse error, nothing imported
    assert r["ok"] is True
    assert r["parse_errors"]
    assert r["safe_to_import"] is False


def test_preflight_reads_a_csv_file_from_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "import.csv"
    f.write_text(_GOOD_CSV, encoding="utf-8")
    r = ds.import_preflight(path="import.csv")
    assert r["ok"] is True
    assert r["rows_parsed"] == 2
    assert r["safe_to_import"] is True


# ── routes ────────────────────────────────────────────────────────

def test_config_audit_route(fresh_app):
    resp = fresh_app.get("/api/dev/config_audit")
    assert resp.status_code == 200
    assert resp.get_json()["tool"] == "config_schema_audit"


def test_import_preflight_route_needs_path(fresh_app):
    resp = fresh_app.get("/api/dev/import_preflight")
    assert resp.status_code == 400


def test_import_preflight_route_with_file(fresh_app, tmp_path,
                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "imp.csv").write_text(_GOOD_CSV, encoding="utf-8")
    resp = fresh_app.get("/api/dev/import_preflight?path=imp.csv")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["rows_parsed"] == 2
