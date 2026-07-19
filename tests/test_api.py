"""API contract tests — basic endpoints respond with expected shapes.

These don't exercise the worker pipeline (no real downloads, no
playwright). They verify HTTP-level invariants: 404s for unknown sites,
JSON content types, expected response keys, etc.
"""


class TestSiteCRUD:
    def test_create_site(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "alpha"})
        assert r.status_code == 200
        body = r.get_json()
        assert "id" in body
        assert len(body["id"]) == 8  # uuid hex prefix

    def test_create_applies_defaults(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "defaulted"})
        sid = r.get_json()["id"]
        status = fresh_app.get("/api/status").get_json()
        cfg = status[sid]["config"]
        # Defaults that should be populated
        assert cfg.get("max_concurrent") == 2
        assert cfg.get("max_retries") == 2
        assert cfg.get("auto_retry_review") is False  # Phase 30 default
        assert cfg.get("accounts_mode") == "failover"  # Phase 31 default

    def test_update_site(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "updateable"})
        sid = r.get_json()["id"]
        r = fresh_app.put(f"/api/sites/{sid}",
                          json={"max_concurrent": 8, "wait": 6})
        assert r.status_code == 200
        status = fresh_app.get("/api/status").get_json()
        cfg = status[sid]["config"]
        assert cfg["max_concurrent"] == 8
        assert cfg["wait"] == 6

    def test_delete_site(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "doomed"})
        sid = r.get_json()["id"]
        r = fresh_app.delete(f"/api/sites/{sid}")
        assert r.status_code == 200
        # Status no longer has the site
        status = fresh_app.get("/api/status").get_json()
        assert sid not in status

    def test_unknown_site_actions_return_404(self, fresh_app):
        for action in ("start", "pause", "resume", "stop", "clear", "retry"):
            r = fresh_app.post(f"/api/sites/UNKNOWN/{action}")
            assert r.status_code == 404, f"{action} should 404"


class TestDashboard:
    def test_dashboard_no_sites(self, fresh_app):
        r = fresh_app.get("/api/dashboard")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["totals"]["pending"] == 0
        assert d["active_workers"] == 0
        assert d["today"]["done"] == 0
        # Optional warning lists are empty
        assert d["expiring_cookies"] == []
        assert d["rate_limited"] == []
        assert d["low_disk"] == []

    def test_dashboard_with_sites(self, fresh_app):
        fresh_app.post("/api/sites", json={"name": "a"})
        fresh_app.post("/api/sites", json={"name": "b"})
        d = fresh_app.get("/api/dashboard").get_json()
        # Totals should still be 0 (no URLs loaded)
        assert d["totals"]["pending"] == 0


class TestEvents:
    def test_events_unknown_site(self, fresh_app):
        r = fresh_app.get("/api/sites/UNKNOWN/events")
        assert r.status_code == 404

    def test_events_empty_site(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "e"})
        sid = r.get_json()["id"]
        r = fresh_app.get(f"/api/sites/{sid}/events")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["events"] == []
        assert d["last_seq"] == 0


class TestAIEndpoints:
    """AI endpoints should always respond — never 500 — even when AI is
    disabled or unreachable. The error path is structured JSON."""

    def test_ai_status_when_disabled(self, fresh_app):
        r = fresh_app.get("/api/ai/status")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is False
        assert d["enabled"] is False

    def test_ai_normalize_resolution_works_without_ai(self, fresh_app):
        # Regex fast path doesn't need AI enabled
        r = fresh_app.post("/api/ai/normalize_resolution",
                          json={"filename": "Movie.4K.2160p.mp4"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["label"] == "4K"
        assert d["via"] == "regex"

    def test_ai_diff_repair_empty_input(self, fresh_app):
        # Empty broken_selectors is a no-op success regardless of AI state
        r = fresh_app.post("/api/ai/diff_repair", json={"broken_selectors": []})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["repairs"] == []

    def test_ai_suggest_when_disabled(self, fresh_app):
        # AI disabled → ok=False with structured error
        r = fresh_app.post("/api/ai/suggest_selectors",
                          json={"dom_excerpt": "<html></html>"})
        assert r.status_code == 200  # NOT 500
        d = r.get_json()
        assert d["ok"] is False
        assert "disabled" in d["error"].lower()


class TestTemplates:
    def test_templates_list(self, fresh_app):
        r = fresh_app.get("/api/templates")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert len(d["templates"]) >= 7

    def test_templates_suggest_vimeo(self, fresh_app):
        r = fresh_app.get("/api/templates?url=https://vimeo.com/123")
        d = r.get_json()
        suggested = [t["id"] for t in d["templates"] if t.get("suggested")]
        assert "vimeo_progressive" in suggested

    def test_template_apply_unknown(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "t"})
        sid = r.get_json()["id"]
        r = fresh_app.post(f"/api/sites/{sid}/templates/apply",
                          json={"template_id": "nonexistent"})
        assert r.status_code == 400


class TestAccountsAPI:
    """Phase 31 accounts endpoints."""

    def test_accounts_endpoint_empty(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "a"})
        sid = r.get_json()["id"]
        r = fresh_app.get(f"/api/sites/{sid}/accounts")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["accounts"] == []

    def test_accounts_with_two(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "multi",
            "accounts": [
                {"label": "A", "username": "u1", "password": "p1"},
                {"label": "B", "username": "u2", "password": "p2"},
            ]
        })
        sid = r.get_json()["id"]
        d = fresh_app.get(f"/api/sites/{sid}/accounts").get_json()
        assert len(d["accounts"]) == 2
        assert d["accounts"][0]["has_password"] is True
        assert "password" not in d["accounts"][0]

    def test_reset_cooldown_out_of_range(self, fresh_app):
        r = fresh_app.post("/api/sites", json={
            "name": "r",
            "accounts": [{"username": "u", "password": "p"}],
        })
        sid = r.get_json()["id"]
        r = fresh_app.post(f"/api/sites/{sid}/accounts/reset_cooldown",
                          json={"account_index": 99})
        assert r.status_code == 400

    def test_rotate_needs_two_accounts(self, fresh_app):
        # Single account → rotation impossible → 400
        r = fresh_app.post("/api/sites", json={
            "name": "single",
            "accounts": [{"username": "u", "password": "p"}]
        })
        sid = r.get_json()["id"]
        r = fresh_app.post(f"/api/sites/{sid}/accounts/rotate")
        assert r.status_code == 400


class TestCaptchaEndpoints:
    def test_captcha_stats_empty(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "c"})
        sid = r.get_json()["id"]
        r = fresh_app.get(f"/api/sites/{sid}/captcha/stats")
        assert r.status_code == 200
        d = r.get_json()
        assert d["submitted"] == 0
        assert d["success_rate"] == 0
        assert d["provider"] == "2captcha"
        assert d["has_key"] is False

    def test_captcha_test_no_key(self, fresh_app):
        r = fresh_app.post("/api/sites", json={"name": "c2"})
        sid = r.get_json()["id"]
        r = fresh_app.post(f"/api/sites/{sid}/captcha/test")
        d = r.get_json()
        assert d["ok"] is False
        assert "no API key" in d["error"]


class TestExecRemoved:
    """Phase 26.4 sanity: the exec() that used to dynamically generate
    action routes is gone."""

    def test_no_exec_in_app(self):
        import bulk_downloader.app as app_mod
        with open(app_mod.__file__, encoding="utf-8") as f:
            src = f.read()
        # Allow exec() in strings/comments incidentally, but no actual call
        for line in src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"): continue
            assert not stripped.startswith("exec("), \
                f"exec() at module level: {line!r}"
