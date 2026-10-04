"""O1826 C31 -- lifecycle_drift gate must not skip corrupt templates; scheduled_sweep sweeps once.

M099: an unparseable *.template.json was silently dropped by _enabled_templates, so
validation_gate returned rc=0 without having checked it.
M100: scheduled_sweep ran sweep() twice (sweep_and_respond + stage_drift_reviews); a host
quarantined by the first pass was no longer `enabled` in the second, so it got no review bundle.
"""
import contextlib
import json

from bulk_downloader import lifecycle_automation as la
from bulk_downloader import lifecycle_drift as ld

BD_GATE_SCOPE = "module"


@contextlib.contextmanager
def _toggles(on_set):
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: True
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


def _tpl(host, v, status="enabled"):
    return {"host": host, "status": status, "version": v,
            "selectors": {"player": {"play_button": f".p{v}"}},
            "api": {}, "network_patterns": []}


def _reviewed(tmp_path):
    rd = tmp_path / "templates" / "reviewed"
    rd.mkdir(parents=True)
    return rd


def _write(rd, host, live, gold=None):
    (rd / f"{host}.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / f"{host}.template.json.bak").write_text(json.dumps(gold), "utf-8")


def test_corrupt_template_fails_validation_gate_by_name(tmp_path):
    rd = _reviewed(tmp_path)
    _write(rd, "good.example", _tpl("good.example", 1), gold=_tpl("good.example", 1))
    (rd / "corrupt.example.template.json").write_text('{"status": "enabled", ', "utf-8")
    g = ld.validation_gate(reviewed_dir=rd, max_drift=0)
    assert g["rc"] != 0, f"C31-M099 corrupt template passed the gate: {g}"
    assert g["unreadable"] == ["corrupt.example"], g
    assert g["offenders"] == [] and g["checked"] == 1, g
    s = ld.sweep(reviewed_dir=rd)
    assert s["unreadable"] == ["corrupt.example"], s


def test_clean_templates_pass_validation_gate(tmp_path):
    rd = _reviewed(tmp_path)
    _write(rd, "good.example", _tpl("good.example", 1), gold=_tpl("good.example", 1))
    _write(rd, "off.example", _tpl("off.example", 1, status="quarantined"))
    g = ld.validation_gate(reviewed_dir=rd, max_drift=0)
    assert g["rc"] == 0 and g["unreadable"] == [] and g["checked"] == 1, g


def test_scheduled_sweep_sweeps_once(tmp_path, monkeypatch):
    rd = _reviewed(tmp_path)
    _write(rd, "drift.example", _tpl("drift.example", 2), gold=_tpl("drift.example", 1))
    calls = []
    real_sweep = ld.sweep

    def counting_sweep(*a, **kw):
        calls.append(1)
        return real_sweep(*a, **kw)

    monkeypatch.setattr(ld, "sweep", counting_sweep)
    with _toggles({"drift_sweep"}):
        r = ld.scheduled_sweep(reviewed_dir=rd)
    assert len(calls) == 1, f"C31-M100 scheduled_sweep ran sweep() {len(calls)} times"
    assert r["swept"] == 1 and r["review_staged"] == 1, r


def test_quarantined_host_still_gets_review_bundle(tmp_path):
    rd = _reviewed(tmp_path)
    _write(rd, "drift.example", _tpl("drift.example", 2), gold=_tpl("drift.example", 1))
    with _toggles({"drift_sweep", "auto_quarantine"}):
        r = ld.scheduled_sweep(reviewed_dir=rd)
    live = json.loads((rd / "drift.example.template.json").read_text("utf-8"))
    assert live["status"] == la.STATUS_QUARANTINED, r
    bundles = list((rd.parent / ".drift_review" / "drift.example").glob("*/bundle.json"))
    assert r["review_staged"] == 1 and len(bundles) == 1, \
        f"C31-M100 quarantined host got no review bundle: {r}"
