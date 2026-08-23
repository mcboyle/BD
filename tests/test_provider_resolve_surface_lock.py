"""test_provider_resolve_surface_lock.py -- attribute-surface guard for the
provider_resolve -> provider_resolve_impl package split (DECOMP-LEAF cut 6, final)."""
import json
from pathlib import Path
import subprocess
import sys

from bulk_downloader import provider_resolve as pr

PUBLIC = {"build_signing_callback", "resolve_provider_embed", "resolve_vimeo", "resolve_wistia",
          "resolve_jwplayer", "resolve_brightcove", "resolve_youtube"}
# tests monkeypatch these on the shim; they must stay attributes that callers honor at call time
MONKEYPATCHED = {"_default_http_get", "_now", "_yt_cipher_ytdlp_path", "_is_safe_public_host",
                 "_decipher_signed_formats", "_YT_CIPHER_YTDLP_PATH_CACHE"}
ALIASES = {"HttpGet", "CacheWrite"}


def test_public_surface_present():
    assert not (PUBLIC - set(dir(pr))), f"dropped: {sorted(PUBLIC - set(dir(pr)))}"


def test_monkeypatch_targets_present():
    assert not (MONKEYPATCHED - set(dir(pr))), f"dropped monkeypatch targets: {sorted(MONKEYPATCHED - set(dir(pr)))}"


def test_all_matches_original():
    assert pr.__all__ == ["HttpGet", "CacheWrite", "DEFAULT_CACHE_TTL_SECONDS", "SSRFBlocked",
                          "resolve_provider_embed", "resolve_vimeo", "resolve_youtube",
                          "resolve_brightcove", "resolve_wistia", "resolve_jwplayer",
                          "build_signing_callback"]


def test_aliases_and_class_present():
    assert ALIASES <= set(dir(pr)) and hasattr(pr, "SSRFBlocked")
    assert sorted(pr._RESOLVERS) == ["brightcove", "jwplayer", "vimeo", "wistia", "youtube"]


def test_default_http_get_monkeypatch_is_honored():
    # the core H-07 contract: rebinding pr._default_http_get is seen by resolve_provider_embed
    calls = []
    def fail(url):
        calls.append(url); raise ConnectionError("net")
    orig = pr._default_http_get
    pr._default_http_get = fail
    try:
        pr.resolve_provider_embed({"provider": "vimeo",
                                   "embed_url": "https://player.vimeo.com/video/1",
                                   "ids": {"clip_id": "1"}})
    except Exception:
        pass
    finally:
        pr._default_http_get = orig
    assert calls, "resolve_provider_embed did not honor the pr._default_http_get monkeypatch"


def test_facade_late_binds_every_retained_implementation_before_module_wipe():
    """Implementation-first imports must keep the facade's injected seams."""
    script = r'''
import json
import sys

from bulk_downloader.provider_resolve_impl import _common, dispatch, youtube

before = [module._PR_SHIM_REF is None
          for module in (_common, dispatch, youtube)]
assert before == [True, True, True], before

from bulk_downloader import provider_resolve as old_pr

bound = [module._PR_SHIM_REF is old_pr
         for module in (_common, dispatch, youtube)]
assert bound == [True, True, True], bound

old_impls = (_common, dispatch, youtube)
for name in tuple(sys.modules):
    if name.startswith("bulk_downloader"):
        del sys.modules[name]

from bulk_downloader import learn

clock = {"now": 1000.0}
old_pr._now = lambda: clock["now"]
calls = []

def http_get(url):
    calls.append(url)
    body = {"media": {"assets": [{
        "type": "hls_playlist",
        "url": "https://media.invalid/master.m3u8",
        "content_type": "application/vnd.apple.mpegurl",
    }]}}
    return 200, {}, json.dumps(body).encode("utf-8")

config = {}
writer = learn.make_provider_cache_writer(config)
embed = {
    "provider": "wistia",
    "source_type": "wistia_embed",
    "ids": {"hashed_id": "abc"},
    "url": "https://fast.wistia.net/embed/iframe/abc",
    "found_in": "test",
}
for now in (1000.0, 1010.0, 26200.0):
    clock["now"] = now
    candidates, error = old_pr.resolve_provider_embed(
        embed,
        http_get=http_get,
        site_memory=learn.deep_detect_site_memory(config),
        cache_write=writer,
    )
    assert error is None and candidates, (error, candidates)

entry = config["learned"]["deep_detect"]["provider_embeds_seen"]["wistia"]
after = [module._PR_SHIM_REF is old_pr for module in old_impls]
result = {
    "before": before,
    "bound": bound,
    "after": after,
    "http_calls": len(calls),
    "refreshed_at": entry["last_resolved"]["at"],
}
print(json.dumps(result, sort_keys=True))
assert after == [True, True, True], result
assert result["http_calls"] == 2, result
assert result["refreshed_at"] == 26200.0, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "implementation-first facade lifecycle failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "after": [True, True, True],
        "before": [True, True, True],
        "bound": [True, True, True],
        "http_calls": 2,
        "refreshed_at": 26200.0,
    }


def test_facade_reimport_cannot_steal_a_retained_implementation_owner():
    """Old and new facade twins each retain their own public injection seams."""
    script = r'''
import importlib
import json
import sys

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
from bulk_downloader.provider_resolve_impl import _common, dispatch, youtube

old_function = old_pr.resolve_provider_embed
old_raw_function = dispatch.resolve_provider_embed
old_calls = []
new_calls = []
old_writes = []
new_writes = []
raw_writes = []

def response():
    body = {"media": {"assets": [{
        "type": "hls_playlist",
        "url": "https://media.invalid/master.m3u8",
        "content_type": "application/vnd.apple.mpegurl",
    }]}}
    return 200, {}, json.dumps(body).encode("utf-8")

old_pr._now = lambda: 111.0
old_pr._default_http_get = lambda url: (old_calls.append(url), response())[1]
assert all(module._PR_SHIM_REF is old_pr
           for module in (_common, dispatch, youtube))

del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")
assert new_pr is not old_pr
new_pr._now = lambda: 222.0
new_pr._default_http_get = lambda url: (new_calls.append(url), response())[1]

candidates, error = old_function(
    {
        "provider": "wistia",
        "source_type": "wistia_embed",
        "ids": {"hashed_id": "abc"},
        "url": "https://fast.wistia.net/embed/iframe/abc",
        "found_in": "test",
    },
    cache_write=lambda provider, embed_id, url, at: old_writes.append(at),
)
new_candidates, new_error = new_pr.resolve_provider_embed(
    {
        "provider": "wistia",
        "source_type": "wistia_embed",
        "ids": {"hashed_id": "def"},
        "url": "https://fast.wistia.net/embed/iframe/def",
        "found_in": "test",
    },
    cache_write=lambda provider, embed_id, url, at: new_writes.append(at),
)
raw_candidates, raw_error = old_raw_function(
    {
        "provider": "wistia",
        "source_type": "wistia_embed",
        "ids": {"hashed_id": "ghi"},
        "url": "https://fast.wistia.net/embed/iframe/ghi",
        "found_in": "test",
    },
    cache_write=lambda provider, embed_id, url, at: raw_writes.append(at),
)
result = {
    "candidates": len(candidates),
    "error": error,
    "new_candidates": len(new_candidates),
    "new_error": new_error,
    "raw_candidates": len(raw_candidates),
    "raw_error": raw_error,
    "old_calls": len(old_calls),
    "new_calls": len(new_calls),
    "refs_old": [module._PR_SHIM_REF is old_pr
                 for module in (_common, dispatch, youtube)],
    "old_timestamp": old_writes,
    "new_timestamp": new_writes,
    "raw_timestamp": raw_writes,
}
print(json.dumps(result, sort_keys=True))
assert result == {
    "candidates": 1,
    "error": None,
    "new_candidates": 1,
    "new_error": None,
    "raw_candidates": 1,
    "raw_error": None,
    "old_calls": 2,
    "new_calls": 1,
    "refs_old": [True, True, True],
    "old_timestamp": [111.0],
    "new_timestamp": [222.0],
    "raw_timestamp": [111.0],
}, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "facade-only reimport stole a retained implementation:\n"
        "stdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["refs_old"] == [True, True, True]
    assert result["old_calls"] == 2 and result["new_calls"] == 1
    assert result["old_timestamp"] == [111.0]
    assert result["new_timestamp"] == [222.0]
    assert result["raw_timestamp"] == [111.0]


def test_facade_twins_isolate_injection_seams_during_concurrent_calls():
    """Context overlap cannot route either facade through its twin's seams."""
    script = r'''
import importlib
import json
import sys
import threading

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")

barrier = threading.Barrier(2)
calls = []
writes = []
outcomes = {}
arrivals = []
trace_state = threading.local()

def response(label):
    body = {"media": {"assets": [{
        "type": "hls_playlist",
        "url": "https://media.invalid/%s.m3u8" % label,
        "content_type": "application/vnd.apple.mpegurl",
    }]}}
    return 200, {}, json.dumps(body).encode("utf-8")

def transport(label):
    def get(url):
        calls.append((label, url))
        return response(label)
    return get

old_pr._now = lambda: 111.0
new_pr._now = lambda: 222.0
old_pr._default_http_get = transport("old")
new_pr._default_http_get = transport("new")

# Pause both calls at the first line of the ONE shared implementation function.
# At this instant both facade wrappers have installed their owner and neither
# implementation has read it. A process-global owner deterministically routes
# both calls through one facade; a ContextVar keeps the two owners distinct.
shared_code = getattr(
    old_pr.resolve_provider_embed,
    "__wrapped__",
    old_pr.resolve_provider_embed,
).__code__

def trace(frame, event, arg):
    if (event == "line" and frame.f_code is shared_code
            and not getattr(trace_state, "paused", False)):
        trace_state.paused = True
        arrivals.append(threading.current_thread().name)
        barrier.wait(timeout=5)
    return trace

threading.settrace(trace)

def invoke(label, facade):
    candidates, error = facade.resolve_provider_embed(
        {
            "provider": "wistia",
            "source_type": "wistia_embed",
            "ids": {"hashed_id": label},
            "url": "https://fast.wistia.net/embed/iframe/%s" % label,
            "found_in": "test",
        },
        cache_write=lambda provider, embed_id, url, at:
            writes.append((label, at, url)),
    )
    outcomes[label] = (len(candidates), error, candidates[0]["url"])

threads = [
    threading.Thread(name="old", target=invoke, args=("old", old_pr)),
    threading.Thread(name="new", target=invoke, args=("new", new_pr)),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=10)
threading.settrace(None)

result = {
    "alive": [thread.is_alive() for thread in threads],
    "arrivals": sorted(arrivals),
    "calls": sorted(calls),
    "outcomes": outcomes,
    "writes": sorted(writes),
}
print(json.dumps(result, sort_keys=True))
assert result == {
    "alive": [False, False],
    "arrivals": ["new", "old"],
    "calls": [
        ("new", "https://fast.wistia.net/embed/medias/new.json"),
        ("old", "https://fast.wistia.net/embed/medias/old.json"),
    ],
    "outcomes": {
        "new": (1, None, "https://media.invalid/new.m3u8"),
        "old": (1, None, "https://media.invalid/old.m3u8"),
    },
    "writes": [
        ("new", 222.0, "https://media.invalid/new.m3u8"),
        ("old", 111.0, "https://media.invalid/old.m3u8"),
    ],
}, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "concurrent facade isolation failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))


def test_facade_twins_bind_deferred_transport_and_ytdlp_helpers():
    """Returned callables and direct YouTube helpers keep the owning facade."""
    script = r'''
import importlib
import json
import sys
from types import SimpleNamespace

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
from bulk_downloader.provider_resolve_impl import _common
del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")

old_pr._now = lambda: 111.0
new_pr._now = lambda: 222.0
old_pr._is_safe_public_host = lambda host: (False, "old facade guard")
new_pr._is_safe_public_host = lambda host: (False, "new facade guard")
old_factory_get = old_pr._make_default_http_get()
new_factory_get = new_pr._make_default_http_get()

cache_state = {
    "provider_embeds_seen": {
        "wistia": {"last_resolved": {
            "id": "wrapped", "url": "https://media.invalid/wrapped.m3u8",
            "at": 150.0,
        }},
    },
}
old_cache = old_pr._cache_lookup(cache_state, "wistia", "wrapped", 50.0)
new_cache = new_pr._cache_lookup(cache_state, "wistia", "wrapped", 50.0)

guard_errors = []
for getter in (old_factory_get, new_factory_get,
               old_pr._default_http_get, new_pr._default_http_get):
    try:
        getter("https://public.invalid/video")
    except old_pr.SSRFBlocked as exc:
        guard_errors.append(str(exc))

# Deferred wrappers must restore even when their transport raises. A raw
# retained helper then falls back to the established old owner: at=150 is a hit
# at old time 111 and expired at new time 222.
raw_cache = _common._cache_lookup({
    "provider_embeds_seen": {
        "wistia": {"last_resolved": {
            "id": "raw", "url": "https://media.invalid/raw.m3u8", "at": 150.0,
        }},
    },
}, "wistia", "raw", 50.0)

old_pr._yt_cipher_ytdlp_path = lambda: "/old/yt-dlp"
new_pr._yt_cipher_ytdlp_path = lambda: "/new/yt-dlp"
argvs = []

def run(argv, **kwargs):
    argvs.append(argv)
    return SimpleNamespace(returncode=0, stdout=b'{"formats": []}', stderr=b'')

old_result = old_pr._decipher_signed_formats_ytdlp("dQw4w9WgXcQ", _run=run)
new_result = new_pr._decipher_signed_formats_ytdlp("dQw4w9WgXcQ", _run=run)
result = {
    "facade_cache_urls": [
        None if old_cache is None else old_cache["url"],
        None if new_cache is None else new_cache["url"],
    ],
    "guard_errors": guard_errors,
    "raw_cache_url": None if raw_cache is None else raw_cache["url"],
    "argv_heads": [argv[0] for argv in argvs],
    "result_errors": [old_result[1], new_result[1]],
}
print(json.dumps(result, sort_keys=True))
assert result["facade_cache_urls"] == [
    "https://media.invalid/wrapped.m3u8", None,
], result
assert result["guard_errors"] == [
    "SSRF guard: old facade guard",
    "SSRF guard: new facade guard",
    "SSRF guard: old facade guard",
    "SSRF guard: new facade guard",
], result
assert result["raw_cache_url"] == "https://media.invalid/raw.m3u8", result
assert result["argv_heads"] == ["/old/yt-dlp", "/new/yt-dlp"], result
assert all("no formats" in error for error in result["result_errors"]), result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "deferred facade isolation failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))


def test_deferred_transport_success_restores_the_retained_facade_owner():
    """A successful factory-returned call cannot leak its facade context."""
    script = r'''
import importlib
import json
import sys

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
from bulk_downloader.provider_resolve_impl import _common
del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")

old_pr._now = lambda: 111.0
new_pr._now = lambda: 222.0

import httpx
owner_during_get = []

class Response:
    status_code = 204
    headers = {"x-facade": "new"}
    content = b"new facade body"

class Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
    def __enter__(self):
        return self
    def __exit__(self, *exc_info):
        return False
    def get(self, url, headers):
        owner_during_get.append(_common._PR_SHIM_CONTEXT.get() is new_pr)
        return Response()

httpx.Client = Client
getter = new_pr._make_default_http_get(allow_private_hosts=True)
response = getter("https://public.invalid/success")

# The raw retained helper has no facade wrapper. After successful return it
# must see the established old fallback, not the just-used new facade.
raw_cache = _common._cache_lookup({
    "provider_embeds_seen": {
        "wistia": {"last_resolved": {
            "id": "raw", "url": "https://media.invalid/old.m3u8",
            "at": 150.0,
        }},
    },
}, "wistia", "raw", 50.0)
result = {
    "response": [response[0], response[1], response[2].decode("ascii")],
    "owner_during_get": owner_during_get,
    "context_after": _common._PR_SHIM_CONTEXT.get() is None,
    "fallback_is_old": _common._PR_SHIM_REF is old_pr,
    "raw_cache_url": None if raw_cache is None else raw_cache["url"],
}
print(json.dumps(result, sort_keys=True))
assert result == {
    "response": [204, {"x-facade": "new"}, "new facade body"],
    "owner_during_get": [True],
    "context_after": True,
    "fallback_is_old": True,
    "raw_cache_url": "https://media.invalid/old.m3u8",
}, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "deferred success restoration failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))


def test_deferred_transport_baseexception_restores_exact_primary_and_owner():
    """A factory-returned cancellation keeps identity and resets context."""
    script = r'''
import importlib
import json
import sys

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
from bulk_downloader.provider_resolve_impl import _common
del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")

old_pr._now = lambda: 111.0
new_pr._now = lambda: 222.0
primary = KeyboardInterrupt("exact deferred cancellation")

import httpx
owner_during_get = []

class Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
    def __enter__(self):
        return self
    def __exit__(self, *exc_info):
        return False
    def get(self, url, headers):
        owner_during_get.append(_common._PR_SHIM_CONTEXT.get() is new_pr)
        raise primary

httpx.Client = Client
getter = new_pr._make_default_http_get(allow_private_hosts=True)
try:
    getter("https://public.invalid/cancel")
except BaseException as caught:
    exact_primary = caught is primary and type(caught) is KeyboardInterrupt
else:
    raise AssertionError("deferred transport did not propagate cancellation")

# Cancellation must unwind the deferred wrapper before this raw retained call.
raw_cache = _common._cache_lookup({
    "provider_embeds_seen": {
        "wistia": {"last_resolved": {
            "id": "raw", "url": "https://media.invalid/old.m3u8",
            "at": 150.0,
        }},
    },
}, "wistia", "raw", 50.0)
result = {
    "exact_primary": exact_primary,
    "primary_args": primary.args,
    "owner_during_get": owner_during_get,
    "context_after": _common._PR_SHIM_CONTEXT.get() is None,
    "fallback_is_old": _common._PR_SHIM_REF is old_pr,
    "raw_cache_url": None if raw_cache is None else raw_cache["url"],
}
print(json.dumps(result, sort_keys=True))
assert result == {
    "exact_primary": True,
    "primary_args": ("exact deferred cancellation",),
    "owner_during_get": [True],
    "context_after": True,
    "fallback_is_old": True,
    "raw_cache_url": "https://media.invalid/old.m3u8",
}, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "deferred cancellation restoration failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))


def test_facade_context_restores_after_baseexception_without_replacing_primary():
    """Cancellation keeps exact identity and cannot leak the twin's context."""
    script = r'''
import importlib
import json
import sys

old_pr = importlib.import_module("bulk_downloader.provider_resolve")
from bulk_downloader.provider_resolve_impl import dispatch
del sys.modules["bulk_downloader.provider_resolve"]
new_pr = importlib.import_module("bulk_downloader.provider_resolve")

old_calls = []
new_calls = []
primary = KeyboardInterrupt("exact facade cancellation")

def response():
    body = {"media": {"assets": [{
        "type": "hls_playlist",
        "url": "https://media.invalid/old.m3u8",
        "content_type": "application/vnd.apple.mpegurl",
    }]}}
    return 200, {}, json.dumps(body).encode("utf-8")

old_pr._default_http_get = lambda url: (old_calls.append(url), response())[1]

def cancel(url):
    new_calls.append(url)
    raise primary

new_pr._default_http_get = cancel
embed = {
    "provider": "wistia",
    "source_type": "wistia_embed",
    "ids": {"hashed_id": "cancel"},
    "url": "https://fast.wistia.net/embed/iframe/cancel",
    "found_in": "test",
}
try:
    new_pr.resolve_provider_embed(embed)
except BaseException as caught:
    assert caught is primary and type(caught) is KeyboardInterrupt
else:
    raise AssertionError("new facade did not reach its cancellation seam")

# The raw retained implementation has no wrapper of its own. It must fall back
# to the old established owner after the exceptional new-facade call returns.
candidates, error = dispatch.resolve_provider_embed({
    **embed,
    "ids": {"hashed_id": "after-cancel"},
    "url": "https://fast.wistia.net/embed/iframe/after-cancel",
})
result = {
    "candidates": len(candidates),
    "error": error,
    "new_calls": len(new_calls),
    "old_calls": len(old_calls),
    "primary_args": primary.args,
}
print(json.dumps(result, sort_keys=True))
assert result == {
    "candidates": 1,
    "error": None,
    "new_calls": 1,
    "old_calls": 1,
    "primary_args": ("exact facade cancellation",),
}, result
'''
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(root)}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, (
        "facade cancellation restoration failed:\nstdout:\n%s\nstderr:\n%s"
        % (completed.stdout, completed.stderr))


def test_each_submodule_imports():
    import importlib
    for mod in ("_common", "vimeo", "wistia", "jwplayer", "brightcove", "youtube", "dispatch"):
        importlib.import_module(f"bulk_downloader.provider_resolve_impl.{mod}")
