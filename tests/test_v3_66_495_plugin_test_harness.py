"""v3.66.495 O3 (plugin-v3): the `plugin test <file>` dry-run harness.

A sandbox harness that exercises a plugin against the R3 hook-payload golden:
for every documented event the plugin subscribes to, it synthesizes a payload
whose keys are EXACTLY the golden key-set for that event and fires it through the
plugin's hook in an isolated registry, reporting per-event pass/fail. It also
dry-runs the pure advisory kinds (prefilter / namer / recognizer / processor)
with synthetic inputs.

It NEVER triggers a real side effect: sinks, sources, and enrichers are reported
as registered but are NOT invoked (no delivery, no network poll, no file write) --
the "never a real download" guarantee. Exit 0 iff every fired event/kind passed.

Stdlib-only; isolated registry per plugin; runner-safe (no pytest fixtures).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import plugin_test as PT  # noqa: E402
from bulk_downloader import plugins as P  # noqa: E402

_GOLDEN = _REPO / "tests" / "golden" / "hook_payloads.golden.json"


def _write(dirpath, name, body):
    fp = Path(dirpath) / name
    fp.write_text(body, encoding="utf-8")
    return fp


# A well-behaved plugin: records the payloads it sees for two events.
_GOOD = '''
from bulk_downloader import plugins as P
PLUGIN = {"name": "good", "version": "1.0", "api_version": 2,
          "capabilities": ["hook"]}
@P.hook("download.done")
def on_done(payload):
    pass
@P.hook("queue.drained")
def on_drained(payload):
    pass
'''

# A plugin whose hook raises on a documented event.
_BAD = '''
from bulk_downloader import plugins as P
PLUGIN = {"name": "bad", "version": "1.0", "api_version": 2,
          "capabilities": ["hook"]}
@P.hook("download.retry")
def boom(payload):
    raise RuntimeError("kaboom")
'''

# A plugin that registers a SINK which writes a sentinel file if ever called.
_SIDE = '''
import os
from bulk_downloader import plugins as P
PLUGIN = {"name": "side", "version": "1.0", "api_version": 2,
          "capabilities": ["sink", "hook"]}
SENTINEL = os.environ.get("BD_PT_SENTINEL", "")
@P.sink(name="sentinel_sink")
def deliver(event, payload, ctx):
    # would be a real side effect -- harness must NEVER call this
    if SENTINEL:
        open(SENTINEL, "w").write("delivered")
    return {"ok": True}
@P.hook("download.done")
def on_done(payload):
    pass
'''


# ── golden payload synthesis ──────────────────────────────────────────
def test_golden_payload_keys_match_golden_exactly():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    pins = golden.get("events", golden)
    for event, keys in pins.items():
        if not keys:
            continue
        pay = PT.golden_payload(event)
        assert sorted(pay.keys()) == sorted(keys), \
            f"{event}: {sorted(pay.keys())} != {sorted(keys)}"


# ── happy path: subscribed events fire with golden payloads ───────────
def test_good_plugin_passes_and_receives_golden_payloads():
    d = tempfile.mkdtemp()
    fp = _write(d, "good.py", _GOOD)
    res = PT.test_plugin(fp)
    assert res["ok"] is True, res
    fired = {e["event"]: e["ok"] for e in res["events"]}
    assert fired.get("download.done") is True
    assert fired.get("queue.drained") is True
    # the harness fired golden-keyed payloads for exactly the subscribed events
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    pins = golden.get("events", golden)
    seen = PT._last_seen()
    seen_events = {ev for ev, _ in seen}
    assert "download.done" in seen_events and "queue.drained" in seen_events
    for ev, payload in seen:
        assert sorted(payload.keys()) == sorted(pins[ev]), (ev, payload)


# ── failure path: a raising hook fails the harness ────────────────────
def test_raising_hook_is_reported_and_nonzero_exit():
    d = tempfile.mkdtemp()
    fp = _write(d, "bad.py", _BAD)
    res = PT.test_plugin(fp)
    assert res["ok"] is False, res
    retry = [e for e in res["events"] if e["event"] == "download.retry"]
    assert retry and retry[0]["ok"] is False
    # CLI returns non-zero
    rc = PT.main([str(fp)])
    assert rc == 1


# ── safety: side-effecting kinds are NEVER invoked ────────────────────
def test_sink_is_registered_but_never_invoked():
    d = tempfile.mkdtemp()
    sentinel = os.path.join(d, "SENTINEL")
    os.environ["BD_PT_SENTINEL"] = sentinel
    try:
        fp = _write(d, "side.py", _SIDE)
        res = PT.test_plugin(fp)
        assert res["ok"] is True, res
        assert "sentinel_sink" in res.get("side_effecting_skipped", []), res
        assert not os.path.exists(sentinel), "sink must NOT have been delivered"
    finally:
        os.environ.pop("BD_PT_SENTINEL", None)


def test_main_on_good_plugin_returns_zero():
    d = tempfile.mkdtemp()
    fp = _write(d, "good2.py", _GOOD)
    assert PT.main([str(fp)]) == 0
