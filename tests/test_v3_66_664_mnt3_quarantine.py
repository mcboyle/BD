"""v3.66.664 -- MNT-3 (choice B): flake quarantine, isolation-first.

Two tiers, both INERT by default (the release band sets neither env var, so full
coverage is preserved on stash):

  Tier 1 (default, safe): data-driven pre-isolation. A file with a CHRONIC flake
    (per the registry) is pulled from the parallel pool into the serial-isolated
    lane -- reusing the existing pinned-file mechanism -- so the parallelism
    collision that makes it flake can't recur. The test still RUNS, still counts,
    and a real failure in isolation still fails. Only active when BD_FLAKE_REGISTRY
    names a populated registry.

  Tier 2 (opt-in, gated): a file-level quarantine-skip manifest. Under
    BD_QUARANTINE_SKIP=1 only, files in the manifest are skipped entirely, each
    reported loudly, and every entry carries an expiry so a stale skip auto-lifts
    (never a silent, permanent skip -- the MNT-3 safety requirement).
"""
import importlib

rt = importlib.import_module("run_tests")


# ---- Tier 1: _quarantine_files ----

def test_quarantine_files_extracts_chronic(monkeypatch):
    reg = {
        "test_flaky.py :: test_a": {"count": 5, "first_seen": 0, "last_seen": 1},
        "test_flaky.py :: test_b": {"count": 4, "first_seen": 0, "last_seen": 1},
        "test_once.py :: test_c": {"count": 1, "first_seen": 0, "last_seen": 1},
    }
    files = rt._quarantine_files(reg, threshold=3)
    assert files == {"test_flaky.py"}, files


def test_quarantine_files_threshold_disables():
    reg = {"test_x.py :: t": {"count": 9, "first_seen": 0, "last_seen": 1}}
    assert rt._quarantine_files(reg, threshold=0) == set()
    assert rt._quarantine_files({}, threshold=3) == set()


# ---- serial partition (Tier 1 routing) ----

def test_partition_routes_iso_and_pinned_to_serial():
    names = ["test_a.py", "test_flaky.py", "test_fixture_site.py", "test_b.py"]
    serial, parallel = rt._partition_serial(names, {"test_flaky.py"})
    assert "test_flaky.py" in serial          # quarantine-isolated
    assert "test_fixture_site.py" in serial    # pinned-together (pre-existing)
    assert set(parallel) == {"test_a.py", "test_b.py"}


def test_partition_empty_iso_is_pinned_only():
    names = ["test_a.py", "test_b.py"]
    serial, parallel = rt._partition_serial(names, set())
    assert serial == []          # nothing pinned, nothing isolated
    assert parallel == names     # unchanged -> inert by default


# ---- Tier 2: skip manifest ----

def test_skip_manifest_active_entry(tmp_path):
    import json
    now = 1_000_000.0
    m = tmp_path / "q.json"
    m.write_text(json.dumps({
        "test_perf_lab.py": {"reason": "hangs >200s", "expires": now + 86400},
    }))
    active = rt._load_skip_manifest(str(m), now)
    assert active == {"test_perf_lab.py": "hangs >200s"}


def test_skip_manifest_expired_is_lifted(tmp_path):
    import json
    now = 1_000_000.0
    m = tmp_path / "q.json"
    m.write_text(json.dumps({
        "test_old.py": {"reason": "was flaky", "expires": now - 86400},
        "test_live.py": {"reason": "still hangs", "expires": now + 86400},
    }))
    active = rt._load_skip_manifest(str(m), now)
    assert "test_old.py" not in active       # expired -> auto-lifted
    assert "test_live.py" in active


def test_skip_manifest_no_expiry_stays_active(tmp_path):
    import json
    m = tmp_path / "q.json"
    m.write_text(json.dumps({"test_x.py": {"reason": "permanent-until-removed"}}))
    active = rt._load_skip_manifest(str(m), 1_000_000.0)
    assert "test_x.py" in active


def test_skip_manifest_missing_or_malformed_is_empty(tmp_path):
    assert rt._load_skip_manifest(str(tmp_path / "nope.json"), 1_000_000.0) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json{")
    assert rt._load_skip_manifest(str(bad), 1_000_000.0) == {}
