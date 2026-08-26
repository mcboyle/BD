"""v3.66.129 — Phase G / G4: Validation Operations.

Proves the advisory re-validation scheduler:
  * SCHEDULE CLASSIFICATION — held-out evidence is `current` within the interval,
    `due_soon` past the interval but before the freshness floor, `overdue` past the floor,
    and `never` when no held-out is designated.
  * DUE QUEUE — `validation_due` surfaces due_soon + overdue + never.
  * ADVISORY & READ-ONLY — the module recommends WHEN to re-validate but never captures,
    logs in, drives a browser, touches the network, or writes anything.
  * WIRING — 3 read-only GET routes (134 -> 137), no new POST.

POSTURE GREPS ban capture/login/browser/network/redownload and any write, since this layer
is purely advisory and read-only.
"""
import datetime as _dt
import json
from pathlib import Path

from _cockpit_tasks import remove_test_governance
from tools import autonomy_oracle as ao
from tools import autonomy_validation as av
from tools.cockpit_core import tasks_root

_SRC = Path(av.__file__).read_text(encoding="utf-8")


def _fresh():
    remove_test_governance(tasks_root())


def _iso(days):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()


def _seed(prov):
    p = ao._provenance_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prov), encoding="utf-8")


_PROV = {
    "fresh": {"held_out": ["c1", "c2"], "held_out_designated_at": _iso(5)},
    "duesoon": {"held_out": ["c1", "c2"], "held_out_designated_at": _iso(25)},
    "overdue": {"held_out": ["c1", "c2"], "held_out_designated_at": _iso(40)},
    "never": {"held_out": []},
}


class TestSchedule:
    def test_status_buckets(self):
        _fresh()
        _seed(_PROV)
        assert av.validation_schedule("fresh")["status"] == "current"
        assert av.validation_schedule("duesoon")["status"] == "due_soon"
        assert av.validation_schedule("overdue")["status"] == "overdue"
        assert av.validation_schedule("never")["status"] == "never"

    def test_recommended_date_present_except_never(self):
        _fresh()
        _seed(_PROV)
        assert av.validation_schedule("fresh")["recommended_date"] is not None
        assert av.validation_schedule("never")["recommended_date"] is None

    def test_overdue_aligns_with_freshness_floor(self):
        # 'overdue' must mean past the same floor eligibility uses
        from tools import autonomy_eligibility as el
        assert av.FRESH_FLOOR_DAYS == el.EVIDENCE_FRESH_DAYS
        assert av.VALIDATION_INTERVAL_DAYS < av.FRESH_FLOOR_DAYS


class TestDueQueue:
    def test_due_surfaces_duesoon_overdue_never(self):
        _fresh()
        _seed(_PROV)
        d = av.validation_due(sites=["fresh", "duesoon", "overdue", "never"])
        assert d["due_count"] == 3
        assert d["overdue"] == ["overdue"]
        assert d["due_soon"] == ["duesoon"]
        assert d["never"] == ["never"]
        assert "fresh" not in (d["overdue"] + d["due_soon"] + d["never"])


class TestAdvisoryReadOnly:
    def test_no_capture_or_login(self):
        for bad in ("capture.sh", "login(", "do_login", "page.goto", "playwright",
                    ".click(", "input.fill", "keyboard."):
            assert bad not in _SRC, bad

    def test_no_network_or_redownload(self):
        for bad in ("requests.", "httpx", "urlopen", "web_fetch", "socket.",
                    "download(", "urlretrieve", "read_bytes()", "hashlib"):
            assert bad not in _SRC, bad

    def test_no_write_or_forbidden_mutation(self):
        for bad in ("open(", ".write_text(", ".write(", "mkdir(", "validation_corpus",
                    "set_policy_level(", "mark_reviewed(", "record_change(", "def apply("):
            assert bad not in _SRC, f"validation ops is read-only/advisory; found {bad!r}"

    def test_advisory_note(self):
        low = _SRC.lower()
        assert "advisory" in low and "never" in low and "read-only" in low


class TestViews:
    def test_overview_counts(self):
        _fresh()
        _seed(_PROV)
        ov = av.validation_overview(sites=["fresh", "duesoon", "overdue", "never"])
        assert ov["site_count"] == 4
        assert ov["counts"]["current"] == 1 and ov["counts"]["overdue"] == 1
        assert ov["counts"]["due_soon"] == 1 and ov["counts"]["never"] == 1

    def test_status_shape(self):
        _fresh()
        s = av.validation_status()
        for k in ("interval_days", "fresh_floor_days", "due_soon_count",
                  "overdue_count", "never_count", "site_count"):
            assert k in s


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path(Path(av.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "overview", "site"):
            assert f'@bp.get("/api/validation/{r}")' in console
        for pg in ("validationops", "validationsite"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="validationops"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G4 adds 3 GET routes (134 -> 137)."
        c = app.test_client()
        for r in ("status", "overview", "site"):
            assert c.get(f"/cockpit/api/validation/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G4 adds no POST."
        for r in ("status", "overview", "site"):
            assert f"/cockpit/api/validation/{r}" not in posts
