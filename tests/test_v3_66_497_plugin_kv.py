"""v3.66.497 O4 (plugin-v3): shared, namespaced plugin KV store.

A durable key/value store plugins can use to persist small state across runs --
namespaced per plugin so two plugins never collide. Backend-abstracted:

  * DEFAULT = SQLite (the app's actual datastore as of v3.65.1) -- a `plugin_kv`
    table, self-created on first use; fully sandbox + stash deployable.
  * OPTIONAL = the datastores-kit Postgres, selected ONLY when a DSN is
    configured (BD_PLUGIN_KV_DSN) AND a psycopg driver is importable. Absent
    either, it transparently falls back to SQLite -- so the store is never a
    hard dependency on Postgres.

Values are JSON-serialized (dict/list/str/int/bool/None). This is the store that
R2 quarantine persistence + K6 source state can retro-back onto.

Logic-only slice: no route, no api bump, no guard. Runner-safe.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.environ.setdefault("BD_HOME", tempfile.mkdtemp())

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugin_kv as KV  # noqa: E402
from bulk_downloader import plugins as P  # noqa: E402


def _fresh(ns="t"):
    kv = KV.for_namespace(ns)
    kv.clear()
    return kv


def test_set_get_roundtrip_json_values():
    kv = _fresh("roundtrip")
    kv.set("a_dict", {"x": 1, "y": [2, 3]})
    kv.set("a_list", [1, "two", 3.0])
    kv.set("a_str", "hello")
    kv.set("an_int", 42)
    kv.set("a_bool", True)
    kv.set("a_none", None)
    assert kv.get("a_dict") == {"x": 1, "y": [2, 3]}
    assert kv.get("a_list") == [1, "two", 3.0]
    assert kv.get("a_str") == "hello"
    assert kv.get("an_int") == 42
    assert kv.get("a_bool") is True
    assert kv.get("a_none") is None


def test_get_missing_returns_default():
    kv = _fresh("missing")
    assert kv.get("nope") is None
    assert kv.get("nope", "fallback") == "fallback"


def test_namespaces_are_isolated():
    a = _fresh("ns_a")
    b = _fresh("ns_b")
    a.set("shared_key", "from_a")
    b.set("shared_key", "from_b")
    assert a.get("shared_key") == "from_a"
    assert b.get("shared_key") == "from_b"


def test_delete_and_keys_and_items():
    kv = _fresh("ops")
    kv.set("k1", 1)
    kv.set("k2", 2)
    assert sorted(kv.keys()) == ["k1", "k2"]
    assert dict(kv.items()) == {"k1": 1, "k2": 2}
    assert kv.delete("k1") is True
    assert kv.delete("k1") is False  # already gone
    assert kv.keys() == ["k2"]


def test_persists_across_instances():
    KV.for_namespace("durable").clear()
    first = KV.for_namespace("durable")
    first.set("counter", 7)
    # a brand-new handle on the same namespace sees the persisted value
    second = KV.for_namespace("durable")
    assert second.get("counter") == 7


def test_clear_only_affects_one_namespace():
    a = _fresh("clear_a")
    b = _fresh("clear_b")
    a.set("x", 1)
    b.set("y", 2)
    a.clear()
    assert a.keys() == []
    assert b.get("y") == 2


def test_backend_defaults_to_sqlite():
    # No BD_* env var selects the backend (deliberately -- no open governance
    # surface). Default is SQLite; Postgres requires an explicit dsn= + driver.
    assert KV.backend() == "sqlite"
    assert KV.backend(None) == "sqlite"


def test_plugins_kv_accessor_returns_namespaced_store():
    P.reset()
    kv = P.kv("my-plugin")
    assert isinstance(kv, KV.PluginKV)
    kv.clear()
    kv.set("hello", "world")
    assert P.kv("my-plugin").get("hello") == "world"
