"""v3.66.465: tests for the expanded plugin system (slices 1-4).

Covers: processors (+ordering, result collection, timeout), config providers
(+merge, base immutability), gated lifecycle hooks (+gate off/on), quarantine
(+budget, skip, clear), manifest api_version + full-access gate at load,
enable/disable/order file resolution, full-access config sources, the
disclaimer, and backward-compat of extractor/hook/fire_hook.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
tempfile.mkdtemp, module globals restored in try/finally.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _fresh():
    P.reset()


# ───────────────────────── processors ─────────────────────────
def test_processor_registration_and_order():
    _fresh()
    order = []

    @P.processor(priority=200)
    def late(payload):
        order.append("late")
        return {"who": "late"}

    @P.processor(priority=50)
    def early(payload):
        order.append("early")
        return {"who": "early"}

    results = P.run_processors({"site_id": "x"})
    assert order == ["early", "late"], order
    assert [r["name"] for r in results] == ["early", "late"]
    assert all(r["ok"] for r in results)
    assert results[0]["result"] == {"who": "early"}
    _fresh()


def test_processor_failure_isolated():
    _fresh()

    @P.processor(priority=10)
    def boom(payload):
        raise RuntimeError("nope")

    @P.processor(priority=20)
    def fine(payload):
        return {"ok": 1}

    results = P.run_processors({})
    by = {r["name"]: r for r in results}
    assert by["boom"]["ok"] is False
    assert by["fine"]["ok"] is True
    _fresh()


def test_processor_timeout_records_fail():
    _fresh()

    @P.processor(priority=10, timeout=0.2)
    def slow(payload):
        time.sleep(2.0)
        return {"done": 1}

    results = P.run_processors({})
    assert results[0]["ok"] is False
    # one failure recorded for the slow processor
    q = {k: v for k, v in P._quarantine.items()}
    assert any(v["fails"] >= 1 for v in q.values())
    _fresh()


# ───────────────────────── config providers ─────────────────────────
def test_config_provider_merge_and_base_immutable():
    _fresh()
    base = {"headless": True, "proxy": ""}

    @P.config_provider(priority=10)
    def a(site_id, cfg):
        return {"proxy": "http://127.0.0.1:8888"}

    @P.config_provider(priority=20)
    def b(site_id, cfg):
        if site_id == "foo":
            return {"headless": False}
        return {}

    merged = P.resolve_site_config("foo", base)
    assert merged["proxy"] == "http://127.0.0.1:8888"
    assert merged["headless"] is False
    # base untouched
    assert base["headless"] is True and base["proxy"] == ""
    # non-matching site keeps base headless
    merged2 = P.resolve_site_config("bar", base)
    assert merged2["headless"] is True
    assert merged2["proxy"] == "http://127.0.0.1:8888"
    _fresh()


def test_config_provider_failure_skipped():
    _fresh()

    @P.config_provider(priority=10)
    def boom(site_id, cfg):
        raise ValueError("bad")

    @P.config_provider(priority=20)
    def good(site_id, cfg):
        return {"k": "v"}

    merged = P.resolve_site_config("s", {"base": 1})
    assert merged["k"] == "v"
    assert merged["base"] == 1
    _fresh()


# ───────────────────────── lifecycle gate ─────────────────────────
def test_lifecycle_gated_off_by_default():
    _fresh()
    seen = []

    @P.lifecycle("after_context")
    def seed(context, page, site_id):
        seen.append(site_id)

    # gate is off after reset -> no-op
    n = P.fire_lifecycle("after_context", "ctx", "page", "siteA")
    assert n == 0
    assert seen == []
    _fresh()


def test_lifecycle_fires_when_enabled():
    _fresh()
    seen = []

    @P.lifecycle("after_context")
    def seed(context, page, site_id):
        seen.append((context, page, site_id))

    P.set_full_access(True)
    n = P.fire_lifecycle("after_context", "ctx", "pg", "siteB")
    assert n == 1
    assert seen == [("ctx", "pg", "siteB")]
    _fresh()


def test_lifecycle_unknown_event_rejected():
    _fresh()
    raised = False
    try:
        @P.lifecycle("not_an_event")
        def x():
            pass
    except ValueError:
        raised = True
    assert raised
    _fresh()


# ───────────────────────── quarantine ─────────────────────────
def test_quarantine_after_budget_then_skipped():
    _fresh()
    calls = {"n": 0}

    @P.hook("download.done")
    def flaky(payload):
        calls["n"] += 1
        raise RuntimeError("always")

    for _ in range(P._FAIL_BUDGET):
        P.fire_hook("download.done", {})
    assert calls["n"] == P._FAIL_BUDGET
    # now quarantined -> further fires do not invoke
    P.fire_hook("download.done", {})
    P.fire_hook("download.done", {})
    assert calls["n"] == P._FAIL_BUDGET
    qn = P.list_quarantine()
    assert any(q["quarantined"] for q in qn)
    # clear restores
    cleared = P.clear_quarantine()
    assert cleared >= 1
    P.fire_hook("download.done", {})
    assert calls["n"] == P._FAIL_BUDGET + 1
    _fresh()


# ───────────────────────── manifest + gate at load ─────────────────────────
def _write(pdir, name, body):
    (pdir / name).write_text(body, "utf-8")


def _with_plugin_dir(tmp):
    """Override _plugin_dir to point at tmp; returns the original to restore."""
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def test_manifest_api_version_mismatch_skipped():
    _fresh()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        _write(Path(tmp), "old.py",
               "PLUGIN={'name':'old','api_version':1,'capabilities':['hook']}\n"
               "from bulk_downloader import plugins as P\n"
               "@P.hook('download.done')\n"
               "def h(p):\n    pass\n")
        _write(Path(tmp), "cur.py",
               "PLUGIN={'name':'cur','api_version':2,'capabilities':['hook']}\n"
               "from bulk_downloader import plugins as P\n"
               "@P.hook('download.done')\n"
               "def h(p):\n    pass\n")
        res = P.load_all()
        names = {e["filename"]: e for e in res["plugins"]}
        assert names["old.py"]["skipped_reason"], names["old.py"]
        assert names["cur.py"]["ok"] is True
        assert res["skipped"] >= 1 and res["loaded"] >= 1
    finally:
        P._plugin_dir = orig
        _fresh()


def test_full_access_gate_blocks_lifecycle_plugin():
    _fresh()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        # gate OFF (no plugins.json) -> lifecycle plugin skipped
        _write(Path(tmp), "life.py",
               "PLUGIN={'name':'life','api_version':2,'capabilities':['lifecycle','page_access']}\n")
        res = P.load_all()
        e = {x["filename"]: x for x in res["plugins"]}["life.py"]
        assert e["skipped_reason"] and "full-access" in e["skipped_reason"]
        assert res["full_access"] is False
    finally:
        P._plugin_dir = orig
        _fresh()


def test_full_access_gate_opens_via_plugins_json():
    _fresh()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        (Path(tmp) / "plugins.json").write_text('{"allow_full_access": true}', "utf-8")
        _write(Path(tmp), "life.py",
               "PLUGIN={'name':'life','api_version':2,'capabilities':['lifecycle']}\n")
        res = P.load_all()
        e = {x["filename"]: x for x in res["plugins"]}["life.py"]
        assert e["ok"] is True, e
        assert res["full_access"] is True
        assert P.full_access_enabled() is True
    finally:
        P._plugin_dir = orig
        _fresh()


def test_full_access_via_env():
    _fresh()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    os.environ["BD_PLUGINS_ALLOW_FULL_ACCESS"] = "1"
    try:
        lcfg = P._read_load_config(Path(tmp))
        assert lcfg["allow_full_access"] is True
    finally:
        os.environ.pop("BD_PLUGINS_ALLOW_FULL_ACCESS", None)
        P._plugin_dir = orig
        _fresh()


# ───────────────────────── enable / disable / order ─────────────────────────
def test_ordered_files_enable_disable_order():
    _fresh()
    tmp = tempfile.mkdtemp()
    p = Path(tmp)
    for n in ("a.py", "b.py", "c.py", "_skip.py"):
        (p / n).write_text("# x\n", "utf-8")

    # default: all non-_ alpha
    files = [x.name for x in P._ordered_files(p, {"enabled": None, "disabled": [], "order": []})]
    assert files == ["a.py", "b.py", "c.py"]

    # order hint moves c first
    files = [x.name for x in P._ordered_files(p, {"enabled": None, "disabled": [], "order": ["c.py"]})]
    assert files == ["c.py", "a.py", "b.py"]

    # disabled drops b
    files = [x.name for x in P._ordered_files(p, {"enabled": None, "disabled": ["b.py"], "order": []})]
    assert files == ["a.py", "c.py"]

    # explicit allowlist in its own order, ignoring others
    files = [x.name for x in P._ordered_files(p, {"enabled": ["b.py", "a.py"], "disabled": [], "order": []})]
    assert files == ["b.py", "a.py"]
    _fresh()


# ───────────────────────── disclaimer + status ─────────────────────────
def test_disclaimer_present_and_in_status_when_enabled():
    _fresh()
    d = P.disclaimer()
    assert "NO sandbox" in d and "your responsibility" in d.lower()
    # status hides it when gate off
    assert P.status()["disclaimer"] == ""
    P.set_full_access(True)
    assert P.status()["disclaimer"]
    _fresh()


def test_status_has_new_sections():
    _fresh()
    s = P.status()
    for k in ("api_version", "processors", "config_providers", "lifecycle",
              "manifests", "quarantine", "full_access_enabled"):
        assert k in s, k
    assert s["api_version"] == P.PLUGIN_API_VERSION
    _fresh()


# ───────────────────────── backward compat ─────────────────────────
def test_backward_compat_extractor_hook():
    _fresh()
    fired = []

    @P.extractor("vixen-x")
    def ex(url, context):
        return {"video_url": "u"}

    @P.hook("download.done")
    def h(payload):
        fired.append(payload["url"])

    assert P.get_extractor("vixen-x")("http://x", {}) == {"video_url": "u"}
    P.fire_hook("download.done", {"url": "http://x"})
    assert fired == ["http://x"]
    # list surfaces still work
    assert any(e["site_id"] == "vixen-x" for e in P.list_extractors())
    assert "download.done" in P.list_hooks()
    _fresh()
