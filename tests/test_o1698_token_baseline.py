import ast
import csv
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import time
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
_candidate = os.environ.get("BD_O1698_TOKEN_BASELINE_CANDIDATE", "")
CANDIDATE = Path(_candidate) if _candidate else None
pytestmark = pytest.mark.skipif(CANDIDATE is None, reason="BD_O1698_TOKEN_BASELINE_CANDIDATE opt-in required")


def usage_function():
    tree = ast.parse((CANDIDATE / "server.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_usage")
    scope = {'Path': Path, 'sqlite3': sqlite3, 'time': time, 'json': json}
    module_code = compile(ast.Module(body=[node], type_ignores=[]), str(CANDIDATE / "server.py"), "exec")
    code = next(value for value in module_code.co_consts if isinstance(value, types.CodeType) and value.co_name == "_usage")
    return types.FunctionType(code, scope)


def database(tmp_path, responses=1):
    db = tmp_path / "usage.sqlite"
    with sqlite3.connect(db) as c:
        c.executescript('''
          CREATE TABLE usage_responses(provider, response_id, timestamp, model, usage, fingerprint, quarantined DEFAULT 0, PRIMARY KEY(provider,response_id));
          CREATE TABLE usage_occurrences(provider, response_id, host, thread, source, PRIMARY KEY(provider,response_id,host,thread,source));
          CREATE TABLE usage_assignments(host, thread, cut, phase, owner, start, end, parent);
          CREATE VIEW v_usage_distinct AS SELECT r.*,min(o.host) host,min(o.source) source FROM usage_responses r JOIN usage_occurrences o USING(provider,response_id) WHERE r.quarantined=0 GROUP BY r.provider,r.response_id;
        ''')
        for i in range(responses):
            c.execute("INSERT INTO usage_responses VALUES(?,?,?,?,?,?,0)", ("claude", str(i), "2026-10-01T00:30:00Z", "model", json.dumps({'input_tokens': 100, 'output_tokens': 7, 'cache_read_input_tokens': 60}), "fp"))
            for host in ("test5", "hub-mesh01"):
                c.execute("INSERT INTO usage_occurrences VALUES(?,?,?,?,?)", ("claude", str(i), host, "session", "/home/mboyle/.claude-d/projects/-var-tmp-bd-seats-worker-7-D/session.jsonl"))
                c.execute("INSERT INTO usage_assignments VALUES(?,?,?,?,?,?,?,?)", (host, "session", "cut", "build", "bd-cx-worker7", "2026-10-01T00:00:00Z", None, None))
    return db


@pytest.mark.parametrize("group", ["session", "role", "model", "host"])
@pytest.mark.parametrize("responses", [1, 2])
def test_response_identity_counted_once(tmp_path, group, responses):
    result = usage_function()(database(tmp_path, responses), "2026-10-01", group, 25)
    assert result["responses"] == responses, "O1698 DUPLICATE-COLLECTOR response counted twice"
    assert sum(r["turns"] for r in result["rows"]) == responses
    assert sum(r["total_ctx"] for r in result["rows"]) == responses * 100
    assert sum(r["out_tokens"] for r in result["rows"]) == responses * 7
    assert sum(r["cache_read"] for r in result["rows"]) == responses * 60


def test_quarantine_and_missing_occurrence(tmp_path):
    db = database(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE usage_responses SET quarantined=1")
        c.execute("INSERT INTO usage_responses VALUES('codex','new','2026-10-01T00:40:00Z','model','{\"input_tokens\":50}', 'fp',0)")
    result = usage_function()(db, "2026-10-01", "session", 25)
    assert result["responses"] == 1
    assert result["rows"][0]["key"] == "?"
    assert result["rows"][0]["total_ctx"] == 50


def baseline_module():
    path = CANDIDATE / "bd-token-baseline.py"
    assert path.is_file(), "O1698 BASELINE producer absent"
    spec = importlib.util.spec_from_file_location("token_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_inputs(tmp_path):
    db = database(tmp_path)
    root = tmp_path / "FIX"
    cut = root / "fixture-bd-cx-worker7"
    cut.mkdir(parents=True)
    brief = cut / "BRIEF.md"
    done = cut / "DONE.md"
    brief.write_text("fixture\n")
    done.write_text("VERDICT: PATCH\n")
    start = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp()
    os.utime(brief, (start, start))
    os.utime(done, (start + 5400, start + 5400))
    decisions = tmp_path / "decisions.md"
    decisions.write_text("INSTALL (2026-10-01T01:30:00Z, PM): fixture installed\n")
    occupancy = tmp_path / "roles.tsv"
    occupancy.write_text("# role\tseat\tpid\thost\tclaimed_at\nworker\tbd-cx-worker7\t12\thost\t2026-10-01T00:00:00Z\n")
    return {'db_path': db, 'since': '2026-10-01T00:00:00Z', 'until': '2026-10-01T02:00:00Z', 'roots': [root], 'brief_roots': [], 'decisions_path': decisions, 'occupancy_path': occupancy, 'out_dir': tmp_path / 'out', 'landed_gate': None}


def tsv(path):
    with path.open() as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_baseline_cycle_and_deduped_tokens(tmp_path):
    args = fixture_inputs(tmp_path)
    baseline_module().generate(**args)
    out = args["out_dir"]
    assert {p.name for p in out.iterdir()} == {"tokens-by-seat.tsv", "tokens-by-role.tsv", "tokens-per-landed-cut.tsv", "cycle-time.tsv", "burn.tsv", "README.md"}
    assert all(p.stat().st_size <= 4096 for p in out.iterdir())
    cycle = tsv(out / "cycle-time.tsv")
    assert len(cycle) == 1 and float(cycle[0]["minutes"]) == 90, "O1698 CYCLE must use measured install UTC"
    seat = tsv(out / "tokens-by-seat.tsv")[0]
    assert seat["seat"] == "bd-cx-worker7" and int(seat["turns"]) == 1
    assert int(seat["input_tokens"]) == 100 and int(seat["output_tokens"]) == 7
    assert tsv(out / "tokens-by-role.tsv")[0]["role"] == "worker"
    cut = tsv(out / "tokens-per-landed-cut.tsv")[0]
    assert int(cut["turns"]) == 1 and int(cut["input_tokens"]) == 100
    assert float(tsv(out / "burn.tsv")[0]["seat_hours"]) == 2
    assert "median_cycle_minutes: 90" in (out / "README.md").read_text()


def test_missing_install_never_uses_done_as_landed(tmp_path):
    args = fixture_inputs(tmp_path)
    args["decisions_path"].write_text("INSTALL (2026-10-01T01:30:00Z, PM): fixture-other installed\n")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "cycle-time.tsv")[0]
    assert row["status"].startswith("COULD NOT LOOK"), "O1698 MISSING-LANDING must remain unknown"
    assert row["minutes"] == ""
    assert not tsv(args["out_dir"] / "tokens-per-landed-cut.tsv")


def test_output_overflow_is_explicit(tmp_path):
    args = fixture_inputs(tmp_path)
    with sqlite3.connect(args["db_path"]) as c:
        for i in range(300):
            thread = f"session-{i}"
            c.execute("INSERT INTO usage_responses VALUES(?,?,?,?,?,?,0)", ("claude", str(i + 1), "2026-10-01T00:30:00Z", "model", '{"input_tokens":100}', "fp"))
            c.execute("INSERT INTO usage_occurrences VALUES(?,?,?,?,?)", ("claude", str(i + 1), "test5", thread, "source"))
            c.execute("INSERT INTO usage_assignments VALUES(?,?,?,?,?,?,?,?)", ("test5", thread, "cut", "build", f"seat-{i}", "2026-10-01", None, None))
    baseline_module().generate(**args)
    assert all(p.stat().st_size <= 4096 for p in args["out_dir"].iterdir())
    readme = (args["out_dir"] / "README.md").read_text()
    omitted = json.loads(readme.split("omitted_rows: ")[1].split(" (bounded")[0])
    assert omitted["tokens-by-seat.tsv"] > 0
    assert len(tsv(args["out_dir"] / "tokens-by-seat.tsv")) + omitted["tokens-by-seat.tsv"] == 301


def test_occurrence_keeps_host_thread_pair(tmp_path):
    db = database(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE usage_occurrences SET thread='other' WHERE host='hub-mesh01'")
        c.execute("UPDATE usage_assignments SET thread='other', owner='wrong-seat' WHERE host='hub-mesh01'")
    result = usage_function()(db, "2026-10-01", "role", 25)
    assert result["responses"] == 1
    assert result["rows"][0]["key"] == "bd-cx-worker7", "O1698 OCCURRENCE host/thread must remain paired"


def test_unknown_seat_not_zero_attribution(tmp_path):
    args = fixture_inputs(tmp_path)
    with sqlite3.connect(args["db_path"]) as c:
        c.execute("DELETE FROM usage_assignments")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "tokens-per-landed-cut.tsv")[0]
    assert row["status"].startswith("COULD NOT LOOK"), "O1698 OWNER missing must not become zero measured tokens"
    assert row["input_tokens"] == ""


def test_timestamp_fraction_window(tmp_path):
    args = fixture_inputs(tmp_path)
    args["since"] = "2026-10-01T00:30:00Z"
    with sqlite3.connect(args["db_path"]) as c:
        c.execute("UPDATE usage_responses SET timestamp='2026-10-01T00:30:00.100Z'")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "tokens-by-seat.tsv")
    assert len(row) == 1 and row[0]["turns"] == "1", "O1698 FRACTION within UTC window was dropped"


def test_dispatch_stamp_survives_bulk_brief_retouch(tmp_path):
    args = fixture_inputs(tmp_path)
    brief = args["roots"][0] / "fixture-bd-cx-worker7" / "BRIEF.md"
    brief.write_text("# BRIEF fixture (bd-dispatch-D, 2026-10-01T00:00:00Z; order fixture)\n")
    touched = dt.datetime(2026, 10, 1, 1, 20, tzinfo=dt.timezone.utc).timestamp()
    os.utime(brief, (touched, touched))
    baseline_module().generate(**args)
    out = args["out_dir"]
    row = tsv(out / "cycle-time.tsv")[0]
    assert float(row["minutes"]) == 90, "O1698 RETOUCH must not shorten the measured build cycle"
    assert row["start_source"] == "dispatch_stamp"
    assert int(tsv(out / "tokens-per-landed-cut.tsv")[0]["input_tokens"]) == 100, "O1698 RETOUCH must not drop tokens before rewritten mtime"
    readme = (out / "README.md").read_text()
    assert "start_sources: dispatch_stamp=1; mtime_fallback=0; unknown=0" in readme
    assert "median_cycle_minutes: 90" in readme


def test_mtime_fallback_is_explicit(tmp_path):
    args = fixture_inputs(tmp_path)
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "cycle-time.tsv")[0]
    assert float(row["minutes"]) == 90
    assert row["start_source"] == "mtime_fallback", "O1698 FALLBACK mtime must be visible"
    assert "mtime fallback" in row["status"]


def test_malformed_dispatch_stamp_is_unknown(tmp_path):
    args = fixture_inputs(tmp_path)
    brief = args["roots"][0] / "fixture-bd-cx-worker7" / "BRIEF.md"
    brief.write_text("# BRIEF fixture (bd-dispatch-D, broken-UTC; order fixture)\n")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "cycle-time.tsv")[0]
    assert row["status"].startswith("COULD NOT LOOK"), "O1698 INVALID dispatch stamp must not become a guessed start"
    assert row["minutes"] == "" and row["start_source"] == "UNKNOWN"


def test_earliest_builder_ledger_precedes_stamp(tmp_path, monkeypatch):
    args = fixture_inputs(tmp_path)
    brief = args["roots"][0] / "fixture-bd-cx-worker7" / "BRIEF.md"
    brief.write_text("# BRIEF fixture (bd-dispatch-D, 2026-10-01T01:00:00Z; order fixture)\n")
    ledger = tmp_path / "ledger.tsv"
    ledger.write_text(
        f"2026-10-01T00:40:00Z\tbd-cx-worker7\tfixture\t0\t{brief}\n"
        f"2026-09-30T23:00:00Z\tbd-worker-D2\tlens-fixture\t0\t{brief}\n"
        f"2026-09-30T22:00:00Z\tbd-cx-worker7\tfixture-other\t0\t{tmp_path / 'other.md'}\n"
        f"2026-10-01T00:00:00Z\tbd-cx-worker7\tfixture\t0\t{brief}\n"
    )
    module = baseline_module()
    monkeypatch.setattr(module, "DEFAULT_LEDGER", ledger, raising=False)
    module.generate(**args)
    row = tsv(args["out_dir"] / "cycle-time.tsv")[0]
    assert float(row["minutes"]) == 90, "O1698 LEDGER earliest builder dispatch must precede header/mtime"
    assert row["start_source"] == "brief_ledger"
    assert int(tsv(args["out_dir"] / "tokens-per-landed-cut.tsv")[0]["input_tokens"]) == 100
    assert "brief_ledger=1" in (args["out_dir"] / "README.md").read_text()


def test_empty_usage_span_is_unknown(tmp_path):
    args = fixture_inputs(tmp_path)
    with sqlite3.connect(args["db_path"]) as connection:
        connection.execute("DELETE FROM usage_responses")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "tokens-per-landed-cut.tsv")[0]
    assert row["status"].startswith("COULD NOT LOOK: no usage rows"), "O1698 EMPTY-SPAN cannot claim measured zero tokens"
    assert all(row[metric] == "" for metric in ("turns", "input_tokens", "output_tokens"))


def test_other_seat_usage_is_not_measured_zero(tmp_path):
    args = fixture_inputs(tmp_path)
    with sqlite3.connect(args["db_path"]) as connection:
        connection.execute("UPDATE usage_assignments SET owner='bd-worker-D3'")
    baseline_module().generate(**args)
    row = tsv(args["out_dir"] / "tokens-per-landed-cut.tsv")[0]
    assert row["status"].startswith("COULD NOT LOOK"), "O1698 NO-ATTRIBUTION cannot claim measured zero tokens"
    assert row["turns"] == ""


def test_readme_names_ever_ingested_source_roots(tmp_path):
    args = fixture_inputs(tmp_path)
    with sqlite3.connect(args["db_path"]) as connection:
        connection.execute("DELETE FROM usage_responses")
    baseline_module().generate(**args)
    readme = (args["out_dir"] / "README.md").read_text()
    assert 'ingested_source_roots_ever: ["/home/mboyle/.claude-d"]' in readme, "O1698 COVERAGE source roots must be measured beyond the selected window"
    assert "claude_pools_never_seen: A,B,C" in readme
