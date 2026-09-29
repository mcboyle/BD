"""OPUSB-003: bd-cache-ttl-guard.py alerts when Claude main-thread cache writes
fall from the 1h TTL tier to 5m (silent overage fallback), or when a setting or
env var forces 5m. Read-only guard; no keep-alive (CACHE-006 refuted).

Runs the real candidate script as a subprocess on fixture transcript trees.
Opt-in: BD_OPUSB_003_CANDIDATE=<absolute path to bd-cache-ttl-guard.py>.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_OPUSB_003_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

NOW = "2026-09-28T10:00:00Z"
IN_WINDOW = "2026-09-28T09:30:00.000Z"
OLD = "2026-09-28T07:00:00.000Z"


def _line(mid, w1h, w5, ts=IN_WINDOW, model="claude-opus-5-5"):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "id": mid,
                "model": model,
                "usage": {
                    "cache_creation_input_tokens": w1h + w5,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": w1h,
                        "ephemeral_5m_input_tokens": w5,
                    },
                },
            },
        },
        separators=(",", ":"),
    )


def _session(root, lines, slug="-var-tmp-bd-seats-worker", sid="s1", sub=False):
    d = root / "projects" / slug
    if sub:
        d = d / sid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (("agent-x" if sub else sid) + ".jsonl")
    f.write_text("\n".join(lines) + "\n")
    return f


def _run(root, *extra):
    path = Path(CANDIDATE)
    assert path.is_absolute() and path.is_file(), f"candidate missing: {path}"
    p = subprocess.run(
        [
            sys.executable,
            str(path),
            "--root",
            str(root),
            "--now",
            NOW,
            "--no-proc",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return p.returncode, p.stdout


def test_red_5m_fallback_main_thread_alerts(tmp_path):
    f = _session(tmp_path, [_line("m1", 10_000, 0), _line("m2", 0, 90_000)])
    assert f.stat().st_size > 0  # fixture holds data before a result is read
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert "ALERT ttl-5m-fallback" in out and "share_1h=0.100" in out
    assert "w1h=10000 w5m=90000 responses=2" in out
    assert "status=ALERT" in out


def test_green_all_1h_is_ok(tmp_path):
    _session(tmp_path, [_line("m1", 50_000, 0), _line("m2", 40_000, 0)])
    rc, out = _run(tmp_path)
    assert (rc, "status=OK" in out, "sessions_judged=1" in out) == (0, True, True), out


def test_threshold_boundary(tmp_path):
    _session(tmp_path, [_line("m1", 95_000, 5_000)])  # exactly 0.95 -> OK
    assert _run(tmp_path)[0] == 0
    _session(tmp_path, [_line("m1", 94_000, 6_000)], sid="s2")
    rc, out = _run(tmp_path)
    assert rc == 1 and "s2.jsonl share_1h=0.940" in out, out


def test_below_floor_everywhere_is_unknown_not_ok(tmp_path):
    # gen2 (shape R1): a lone 0%-1h session under --min-write is not evidence
    # either way, so the guard must not report OK on it.
    _session(tmp_path, [_line("m1", 0, 5_000)])
    rc, out = _run(tmp_path)
    assert rc == 3 and "status=UNKNOWN" in out and "sessions_judged=0" in out, out


def test_fleet_wide_fallback_across_small_sessions_alerts(tmp_path):
    # shape R1 probe: 3 sessions, each 9,000 tokens all-5m (under the floor).
    for sid in ("s1", "s2", "s3"):
        _session(tmp_path, [_line(sid + "m", 0, 9_000)], sid=sid)
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert "ALERT ttl-5m-fallback pooled-small-sessions=3 share_1h=0.000" in out


def test_small_sessions_all_1h_pool_is_ok(tmp_path):
    for sid in ("s1", "s2", "s3"):
        _session(tmp_path, [_line(sid + "m", 9_000, 0)], sid=sid)
    rc, out = _run(tmp_path)
    assert rc == 0 and "sessions_judged=1" in out, out


def test_unreadable_transcript_is_unknown_not_ok_or_crash(tmp_path):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    bad = _session(tmp_path, [_line("m2", 60_000, 0)], sid="s2")
    bad.chmod(0)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("running as a user who can read mode-000 files")
        rc, out = _run(tmp_path)
    finally:
        bad.chmod(0o600)
    assert rc == 3 and f"COULD NOT LOOK {bad} (PermissionError)" in out, out
    assert "Traceback" not in out


def test_zone_less_timestamp_is_skipped_not_a_crash(tmp_path):
    _session(
        tmp_path,
        [_line("m0", 0, 900_000, ts="2026-09-28T09:30:00"), _line("m1", 60_000, 0)],
    )
    rc, out = _run(tmp_path)
    assert rc == 0 and "sessions_judged=1" in out, out


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]"])
def test_unparseable_settings_is_could_not_look(tmp_path, raw):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    (tmp_path / "settings.json").write_text(raw)
    rc, out = _run(tmp_path)
    assert rc == 3 and f"COULD NOT LOOK {tmp_path / 'settings.json'}" in out, out


def test_subagents_excluded_they_write_5m_by_design(tmp_path):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    _session(tmp_path, [_line("a1", 0, 500_000)], sub=True)
    rc, out = _run(tmp_path)
    assert rc == 0 and "sessions_judged=1" in out, out


def test_window_and_dedupe(tmp_path):
    # outside the window: ignored; same message.id twice: last line wins.
    _session(
        tmp_path,
        [
            _line("old", 0, 900_000, ts=OLD),
            _line("m1", 0, 900_000),
            _line("m1", 900_000, 0),
        ],
    )
    rc, out = _run(tmp_path)
    assert rc == 0 and "sessions_judged=1" in out, out


def test_no_writes_is_unknown_not_ok(tmp_path):
    (tmp_path / "projects").mkdir()
    rc, out = _run(tmp_path)
    assert rc == 3 and "status=UNKNOWN" in out, out


@pytest.mark.parametrize(
    "settings, needle",
    [
        ({"promptCacheTtl": "5m"}, "promptCacheTtl=5m"),
        ({"env": {"FORCE_PROMPT_CACHING_5M": "1"}}, "FORCE_PROMPT_CACHING_5M=1"),
        (
            {"env": {"CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"}},
            "CLAUDE_CODE_PROMPT_CACHE_TTL=5m",
        ),
    ],
)
def test_config_forcing_5m_alerts(tmp_path, settings, needle):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    rc, out = _run(tmp_path)
    assert rc == 1 and "ALERT ttl-5m-config" in out and needle in out, out


def test_config_not_forcing_5m_is_quiet(tmp_path):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    (tmp_path / "settings.json").write_text(
        json.dumps({"promptCacheTtl": "1h", "env": {"FORCE_PROMPT_CACHING_5M": "0"}})
    )
    assert _run(tmp_path)[0] == 0


def test_process_env_forcing_5m_alerts(tmp_path):
    _session(tmp_path, [_line("m1", 60_000, 0)])
    path = Path(CANDIDATE)
    env = dict(os.environ, FORCE_PROMPT_CACHING_5M="1")
    p = subprocess.run(
        [sys.executable, str(path), "--root", str(tmp_path), "--now", NOW],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert p.returncode == 1 and "ALERT ttl-5m-env self" in p.stdout, p.stdout
