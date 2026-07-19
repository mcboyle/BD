"""Plugin/hooks system (Phase 116, Block Q; expanded v3.66.465).

Lets operators extend BD with their own Python code without modifying the
core. As of v3.66.465 the system has five extension kinds, a manifest +
API-version contract, an enable/order + quarantine robustness layer, and a
gated full-access lifecycle surface.

Extension kinds
---------------
  - Extractors   @extractor("site")        fn(url, context) -> dict|{}|None
  - Hooks        @hook("event")            fn(payload)             (lifecycle events)
  - Processors   @processor(priority=...)  fn(payload) -> dict|None (post-download)
  - Config       @config_provider(...)     fn(site_id, cfg) -> dict (per-site cfg layering)
  - Lifecycle    @lifecycle("event")       fn(...)                  (browser/capture; GATED)

Manifest (optional but recommended). A plugin module may define::

    PLUGIN = {
        "name": "plex-refresh",
        "version": "1.0.0",
        "api_version": 2,                 # must match PLUGIN_API_VERSION (major)
        "author": "you",
        "capabilities": ["processor"],    # what it registers; see CAP_* below
        "description": "Refresh Plex on download.done",
    }

`api_version` is checked at load: a mismatched major is skipped with a warning,
so a stale plugin can't silently break when an event payload changes.

Enable / order / full-access config (optional `plugins.json` in the plugin dir,
overridable by env)::

    {"enabled": ["a.py", "b.py"],   // if present, ONLY these load (in this order)
     "disabled": ["c.py"],          // never load these
     "order": ["b.py", "a.py"],     // load priority when `enabled` absent
     "allow_full_access": false}    // gate for lifecycle/page-access plugins

Backward compatible: with no `plugins.json`, every non-`_` `*.py` loads (the
historical behavior) and full-access is OFF.

Robustness
----------
Every plugin callback is wrapped: an exception is caught + logged and never
propagates. A callback that fails `_FAIL_BUDGET` times is **quarantined** (no
longer invoked) and surfaced on the status page until `clear_quarantine`.
Processor/lifecycle calls support an optional per-registration timeout so a
hanging plugin can't stall a download.

Full-access gate + disclaimer
------------------------------
Lifecycle hooks receive the **live** Playwright context/page and the raw launch
kwargs -- the same privilege BD itself has. That surface is **disabled by
default**. The operator opts in via `allow_full_access` (plugins.json) or
`BD_PLUGINS_ALLOW_FULL_ACCESS=1`. With the gate off, plugins declaring the
`lifecycle`/`page_access` capabilities are skipped at load. See DISCLAIMER.

Security model: there is NO sandbox. A loaded plugin runs in BD's process with
full access to the filesystem, network, config, secrets, session profiles, and
(with the gate on) the live browser. Trust = whoever can drop a file in the
plugin dir. Responsibility for ToS / legal / charter compliance is the
operator's. BD ships no evasion/DRM/challenge-solving plugins.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

# ── API contract ──────────────────────────────────────────────────────
PLUGIN_API_VERSION = 2          # current major; what the host emits/reports
# Supported plugin-API range (R5). A plugin loads iff its declared API range
# OVERLAPS [PLUGIN_API_MIN, PLUGIN_API_MAX]. Ships at [2, 2] -- byte-behaviour
# identical to the old exact-match for api_version==2 -- so a later kind can
# raise PLUGIN_API_MAX without breaking plugins pinned to 2.
PLUGIN_API_MIN = 2
# K1 488->3 recognizer; K2 489->4 prefilter; K3 490->5 namer; K5 491->6
# sink; K6 492->7 source; K4 493->8 enricher. Range stays [2,8] so an
# api_version=2 plugin still overlaps and loads (R5 range model).
PLUGIN_API_MAX = 8
# Hook-payload schema version, INDEPENDENT of the API major (R5). Additive
# payload changes bump THIS, not PLUGIN_API_VERSION, so an older plugin keeps
# loading; a removed/renamed key is caught by R3's hook-payload golden.
PLUGIN_PAYLOAD_SCHEMA = 1


def api_compatible(man: dict) -> tuple:
    """Return ``(ok, reason)`` for a plugin manifest's API declaration.

    A plugin is compatible iff its declared API range overlaps the host's
    supported range ``[PLUGIN_API_MIN, PLUGIN_API_MAX]``:

      * ``min_api`` / ``max_api`` present -> treat as the plugin's [lo, hi]
        (each defaults to the host bound when omitted) and test for overlap.
      * else a scalar ``api_version`` -> the range [api_version, api_version].
      * neither present -> compatible (manifest is optional).

    Compatibility keys off the API major ONLY -- the payload-schema version is
    orthogonal and never gates load.
    """
    if not isinstance(man, dict):
        return (True, "")
    if ("min_api" in man) or ("max_api" in man):
        lo = man.get("min_api", PLUGIN_API_MIN)
        hi = man.get("max_api", PLUGIN_API_MAX)
        try:
            lo, hi = int(lo), int(hi)
        except (TypeError, ValueError):
            return (False, f"min_api/max_api not ints ({lo!r}, {hi!r})")
        if lo > hi:
            return (False, f"min_api {lo} > max_api {hi}")
        if hi < PLUGIN_API_MIN:
            return (False, f"max_api {hi} below host api min {PLUGIN_API_MIN}")
        if lo > PLUGIN_API_MAX:
            return (False, f"min_api {lo} above host api max {PLUGIN_API_MAX}")
        return (True, "")
    api = man.get("api_version")
    if api is None:
        return (True, "")
    try:
        api = int(api)
    except (TypeError, ValueError):
        return (False, f"api_version {api!r} not an int")
    if api < PLUGIN_API_MIN:
        return (False, f"api_version {api} below host api min {PLUGIN_API_MIN}")
    if api > PLUGIN_API_MAX:
        return (False, f"api_version {api} above host api max {PLUGIN_API_MAX}")
    return (True, "")

# ── Capability tokens (declared in a plugin's PLUGIN["capabilities"]) ──
CAP_EXTRACTOR = "extractor"
CAP_HOOK = "hook"
CAP_PROCESSOR = "processor"
CAP_CONFIG = "config"
CAP_LIFECYCLE = "lifecycle"        # requires full-access
CAP_PAGE_ACCESS = "page_access"    # requires full-access
CAP_RECOGNIZER = "recognizer"      # K1: review-only player/site detector
CAP_PREFILTER = "prefilter"        # K2: pre-enqueue filter (keep/drop/rewrite)
CAP_NAMER = "namer"                # K3: output filename/path owner
CAP_SINK = "sink"                  # K5: retryable event delivery sink
CAP_SOURCE = "source"              # K6: URL-producing source/watcher
CAP_ENRICHER = "enricher"          # K4: post-download sidecar enricher
CAP_HEALTHCHECK = "healthcheck"    # K7: plugin-contributed self-test probe
_GATED_CAPS = frozenset({CAP_LIFECYCLE, CAP_PAGE_ACCESS})


def capability_gate(caps, gated_caps, full_access, granted=frozenset()):
    """Single SoT for the gated-capability admission decision (V3-A W3).

    Returns (ok, reason). A capability in ``gated_caps`` is admitted iff
    ``full_access`` is on OR that specific cap is individually granted (the
    plugins.json ``granted_capabilities`` list). Deny-by-default: an ungranted
    gated cap without full-access is refused, with the offending caps named.
    Non-gated caps are never blocked here.

    Consolidated from four verbatim copies of the inline gate (this module plus
    plugin_node / plugin_exec / plugin_py_bridge) so the four runtimes enforce one
    identical policy and cannot drift.
    """
    gated = set(caps) & set(gated_caps)
    if not gated or full_access:
        return (True, "")
    ungranted = gated - set(granted or ())
    if not ungranted:
        return (True, "")
    return (False, "requires ungranted capability %s -- grant it via plugins.json "
            "granted_capabilities or enable full-access" % sorted(ungranted))


def _version_tuple(s):
    """Parse 'x.y.z' -> (x, y, z) ints, or None if unparsable."""
    try:
        return tuple(int(p) for p in str(s).strip().split("."))
    except (TypeError, ValueError):
        return None


def requires_satisfied(man) -> tuple:
    """Single SoT for the manifest ``requires`` admission decision (V3-C, 777).

    ``requires: {"bd_min": "3.66.700", "plugins": ["dep.py"]}`` -- both keys
    optional. Returns (ok, reason). Fail-closed on malformed input with a
    named reason (api_compatible's posture: a requirement that cannot be
    parsed cannot be VERIFIED). A plugin dep is satisfied iff the named file
    already loaded ok in this scan -- load order is the operator's
    plugins.json ``order``, and the skip reason says so. Called from the
    in-proc validator AND all three bridge runtimes at the capability_gate
    seam (the @774 consolidation lesson applied on day one, so the decision
    cannot drift per-runtime).
    """
    if not isinstance(man, dict):
        return (True, "")
    req = man.get("requires")
    if req is None:
        return (True, "")
    if not isinstance(req, dict):
        return (False, "requires must be a dict, got %s" % type(req).__name__)
    bd_min = req.get("bd_min")
    if bd_min is not None:
        want = _version_tuple(bd_min)
        if want is None:
            return (False, "requires.bd_min %r is not a version" % (bd_min,))
        from . import __version__ as _host_v
        host = _version_tuple(_host_v) or ()
        if host < want:
            return (False, "requires BD >= %s, host is %s" % (bd_min, _host_v))
    deps = req.get("plugins")
    if deps is not None:
        if not isinstance(deps, list):
            return (False, "requires.plugins must be a list of filenames")
        loaded_ok = {e.get("filename") for e in _loaded if e.get("ok")}
        for dep in deps:
            if str(dep) not in loaded_ok:
                return (False, "requires plugin %r (not loaded; order it "
                        "earlier via plugins.json order)" % (str(dep),))
    return (True, "")

LIFECYCLE_EVENTS = ("before_launch", "after_context", "before_capture", "after_capture")

_FAIL_BUDGET = 5
# Quarantine decay (R2): a quarantined key is SKIPPED only while inside this
# cooldown; once it elapses the next call is a re-probe (recover-on-success,
# re-quarantine-on-failure). A plugins.json/default constant -- NOT a BD_* env
# var (the 482 config-parity governance lesson).
_QUARANTINE_COOLDOWN = 600.0     # seconds

# K5 sink delivery defaults. max_attempts bounds retry (no infinite loop);
# backoff is exponential off the base unless the sink returns retry_after.
_SINK_MAX_ATTEMPTS = 3
_SINK_BACKOFF_BASE = 0.5

# ── Registries ────────────────────────────────────────────────────────
_extractors: dict = {}                 # site_id -> fn
_hooks: dict = {}                       # event -> [fn]
_processors: list = []                  # [(priority, name, fn, timeout)]
_config_providers: list = []            # [(priority, name, fn, timeout)]
_lifecycle: dict = {}                   # event -> [(name, fn, timeout)]
_recognizers: list = []                 # [(priority, name, fn, timeout)]  (K1)
_prefilters: list = []                  # [(priority, name, fn, timeout)]  (K2)
_namers: list = []                      # [(priority, name, fn, timeout)]  (K3)
_sinks: list = []                       # [(prio,name,fn,timeout,idempotent,max_attempts)] (K5)
_dead_letter: list = []                 # [{sink,event,payload,attempts,reason}] (K5)
_sources: list = []                     # [(name, fn, timeout, interval_seconds)] (K6)
_source_state: dict = {}                # name -> persisted poll state (K6)
_enrichers: list = []                   # [(priority, name, fn, timeout)]  (K4)
_healthchecks: list = []                # [(priority, name, fn, timeout)]  (K7)
_manifests: dict = {}                   # plugin filename -> manifest dict
_loaded: list = []                      # [{filename, ok, error, manifest, loaded_at, skipped_reason}]
_quarantine: dict = {}                  # key -> {fails, last_error, quarantined_at, quarantined}
_metrics: dict = {}                     # key -> {calls, fails, total_s, last_s} (O2 per-plugin metrics)

# ── Full-access gate ──────────────────────────────────────────────────
_full_access_enabled = False

# ── Per-capability grants (V3-A W3) ───────────────────────────────────
# Gated caps the operator has consented to individually (plugins.json
# granted_capabilities). Deny-by-default; independent of full-access.
_granted_capabilities: frozenset = frozenset()

# ── V3-C enable/disable lifecycle (777) ───────────────────────────────
# on_disable callables queued at load, fired by reset() BEFORE registries
# clear (an unload/reload gives a declaring plugin its teardown).
_disable_hooks: list = []


def _wire_enable_disable(mod, man, name: str) -> str:
    """Wire the manifest-declared on_enable/on_disable pair for an IN-PROC
    plugin (V3-C, 777). Returns "" on success or a fail-closed skip reason:
    declaring a hook the module does not provide is a manifest defect.
    on_enable fires NOW through _call_guarded (a crashing setup is isolated
    and quarantine-accounted, never breaks the scan); on_disable is queued
    for reset(). Bridge-runtime enable/disable events are DEFERRED -- unlike
    ``requires`` this is a convenience lifecycle, not an admission gate, so
    a per-runtime rollout is honest and documented.
    """
    if not isinstance(man, dict):
        return ""
    for hook in ("on_enable", "on_disable"):
        if man.get(hook) and not callable(getattr(mod, hook, None)):
            return ("manifest declares %s but module lacks a callable %s"
                    % (hook, hook))
    if man.get("on_enable"):
        _call_guarded("on_enable:%s" % name, getattr(mod, "on_enable"))
    if man.get("on_disable"):
        _disable_hooks.append((name, getattr(mod, "on_disable")))
    return ""


def set_granted_capabilities(caps) -> None:
    """Set the operator-granted gated capabilities. Defaults to none."""
    global _granted_capabilities
    _granted_capabilities = frozenset(str(c) for c in (caps or ()))


def granted_capabilities() -> frozenset:
    return _granted_capabilities


_force_isolated: frozenset = frozenset()


def set_force_isolated(items) -> None:
    """W6 (778): set the operator-forced isolation list ("*" = all)."""
    global _force_isolated
    _force_isolated = frozenset(str(x) for x in (items or ()))


def _is_force_isolated(name: str) -> bool:
    return "*" in _force_isolated or name in _force_isolated


def set_full_access(enabled: bool) -> None:
    """Enable/disable the gated lifecycle/page-access surface. Defaults off."""
    global _full_access_enabled
    _full_access_enabled = bool(enabled)


def full_access_enabled() -> bool:
    return _full_access_enabled


DISCLAIMER = (
    "BulkDownloader Plugins -- Full-Access Notice\n\n"
    "Plugins run INSIDE the BulkDownloader process with NO sandbox. A loaded "
    "plugin has the same access BD has: the filesystem, the network, your "
    "configuration and secrets, your authenticated session profiles, and -- "
    "with full-access lifecycle hooks enabled -- the LIVE browser context and "
    "page. There is no privilege boundary between a plugin and BD.\n\n"
    "This is a general, dual-use extension capability. What a plugin does with "
    "that access is YOUR responsibility as the operator. By enabling "
    "allow_full_access and loading a plugin you accept that:\n"
    "  - You are responsible for ensuring every plugin complies with the Terms "
    "of Service of the sites involved, applicable law, and BulkDownloader's "
    "capture charter: authenticated, site-provided playback only; no "
    "access-control bypass, no DRM circumvention, no anti-bot challenge-solving "
    "or behavior-simulation as a shipped behavior.\n"
    "  - A misbehaving or hostile plugin can corrupt downloads, exfiltrate your "
    "secrets and session cookies, or get your accounts flagged or banned. Load "
    "only plugins you wrote or fully trust.\n"
    "  - Third-party plugins carry NO support and NO warranty. BulkDownloader "
    "does not ship, endorse, or support plugins whose purpose is to defeat "
    "access controls, DRM, or challenge systems; in-tree plugins stay within "
    "the charter.\n\n"
    "Full-access lifecycle hooks are DISABLED by default. Leave them off unless "
    "you need them and trust what you have loaded."
)


def disclaimer() -> str:
    return DISCLAIMER


# ── Quarantine ────────────────────────────────────────────────────────
def _qkey(fn: Callable, prefix: str = "") -> str:
    mod = getattr(fn, "__module__", "?")
    qn = getattr(fn, "__qualname__", getattr(fn, "__name__", "?"))
    return f"{prefix}{mod}.{qn}"


def _quarantine_state_path() -> Path:
    """Path of the persisted quarantine state.

    v3.66.805: quarantine state is RUNTIME data, not plugin code, so it must not
    live inside the install tree (an overlay deploy must never clobber or
    resurrect it, and it must not leak into a release build; the @798 manifest
    exclusion papered over the symptom, not the cause).

    The state is keyed to the PLUGIN DIR (a distinct plugin set has a distinct
    quarantine ledger), so an explicit ``_plugin_dir()`` override still owns its
    own state file -- this preserves per-plugin-dir isolation (the 485 contract,
    and the plugin_py_bridge tests that patch ``_plugin_dir`` to a tempdir).

    Only the DEFAULT resolution moves: when ``_plugin_dir()`` resolves inside the
    install tree AND ``BD_HOME`` is set, the state relocates under ``BD_HOME``
    (mirroring the ``interop_registry`` / ``backup_verify`` ``BD_HOME or "."``
    convention -- no new BD_-prefixed env var enters the config-surface). With
    ``BD_HOME`` unset the path stays under ``_plugin_dir()`` -- no silent move,
    current stash behaviour unchanged. Plugin CODE always loads from
    ``_plugin_dir()``; only this state file relocates.
    """
    pdir = _plugin_dir()
    home = os.environ.get("BD_HOME")
    if home:
        try:
            from .constants import INSTALL_DIR
            install_plugins = Path(INSTALL_DIR).resolve() / "plugins"
        except Exception:
            install_plugins = None
        # Only redirect the install-tree DEFAULT; an explicit override (tests,
        # external plugin dirs) keeps its own co-located state for isolation.
        if install_plugins is not None and pdir.resolve() == install_plugins:
            return Path(home).resolve() / ".plugin_state.json"
    return pdir / ".plugin_state.json"


def _save_quarantine_state() -> None:
    """Persist the quarantine map so it survives a restart. Best-effort: a
    write failure (no dir, read-only fs) is non-fatal -- persistence is a
    durability nicety, never a correctness gate."""
    try:
        pdir = _plugin_dir()
        if not pdir.is_dir():
            return
        _quarantine_state_path().write_text(
            json.dumps({"quarantine": _quarantine}), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_quarantine_state() -> None:
    """Re-hydrate the quarantine map from disk (idempotent merge). Called on
    load_all so a quarantine survives a process restart."""
    try:
        sp = _quarantine_state_path()
        if not sp.is_file():
            return
        raw = json.loads(sp.read_text("utf-8"))
        q = raw.get("quarantine") if isinstance(raw, dict) else None
        if isinstance(q, dict):
            for k, v in q.items():
                if isinstance(v, dict):
                    _quarantine.setdefault(k, v)
    except Exception:  # noqa: BLE001
        pass


# Re-entrancy guard: firing a transition hook must not recurse into the same
# transition for the same key (a transition hook that itself fails/heals).
_in_transition: set = set()


def _notify_transition(event: str, payload: dict) -> None:
    guard = f"{event}:{payload.get('key')}"
    if guard in _in_transition:
        return
    _in_transition.add(guard)
    try:
        fire_hook(event, payload)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _in_transition.discard(guard)


def _record_fail(key: str, err: BaseException) -> None:
    q = _quarantine.setdefault(
        key, {"fails": 0, "last_error": "", "quarantined_at": 0.0, "quarantined": False})
    q["fails"] += 1
    q["last_error"] = str(err)[:300]
    crossed = False
    if q["fails"] >= _FAIL_BUDGET:
        if not q["quarantined"]:
            crossed = True            # first-time quarantine transition
        # (re-)arm the cooldown window: a re-probe failure refreshes the clock
        # WITHOUT resetting the accumulated fail count.
        q["quarantined"] = True
        q["quarantined_at"] = time.time()
    if crossed:
        sys.stderr.write(f"[plugins] quarantined {key} after {q['fails']} failures\n")
    _save_quarantine_state()
    if crossed:
        _notify_transition("plugin.quarantined",
                           {"key": key, "fails": q["fails"], "last_error": q["last_error"]})


def _record_metric(key: str, ok: bool, dt: float) -> None:
    """O2: accumulate per-key invocation metrics. Cheap + isolated -- a metrics
    bug must never perturb the call path (callers wrap nothing; this never
    raises on normal input)."""
    m = _metrics.setdefault(key, {"calls": 0, "fails": 0, "total_s": 0.0, "last_s": 0.0})
    m["calls"] += 1
    if not ok:
        m["fails"] += 1
    try:
        d = float(dt)
    except (TypeError, ValueError):
        d = 0.0
    if d < 0:
        d = 0.0
    m["total_s"] += d
    m["last_s"] = d
    # V3-E residual (776): bounded duration ring for tail percentiles. maxlen
    # bounds memory regardless of call count; percentiles reflect the recent
    # window. Same never-raises property -- d is already a sanitized float.
    ring = m.get("samples")
    if ring is None:
        ring = m["samples"] = deque(maxlen=128)
    ring.append(d)


def _recover(key: str) -> None:
    """Clear a key's quarantine after a successful re-probe + fire the hook once."""
    q = _quarantine.get(key)
    if not q or not q.get("quarantined"):
        return
    del _quarantine[key]
    _save_quarantine_state()
    _notify_transition("plugin.recovered", {"key": key})


def _is_quarantined(key: str) -> bool:
    """True iff the key should be SKIPPED right now: quarantined AND still
    inside the cooldown. Once the cooldown elapses the key is eligible for a
    re-probe (this returns False so the next call goes through)."""
    q = _quarantine.get(key)
    if not q or not q.get("quarantined"):
        return False
    return (time.time() - q.get("quarantined_at", 0.0)) < _QUARANTINE_COOLDOWN


def clear_quarantine(key: Optional[str] = None) -> int:
    """Clear quarantine for one key, or all if key is None. Returns count cleared."""
    if key is None:
        n = sum(1 for v in _quarantine.values() if v.get("quarantined"))
        _quarantine.clear()
        _save_quarantine_state()
        return n
    if key in _quarantine:
        del _quarantine[key]
        _save_quarantine_state()
        return 1
    return 0


def _call_guarded(key: str, fn: Callable, args: tuple = (), kwargs: Optional[dict] = None,
                  timeout: Optional[float] = None) -> tuple:
    """Invoke fn with full error isolation + quarantine accounting.

    Returns (ok: bool, result). A key inside its cooldown is skipped and returns
    (False, None) without invoking. Once the cooldown elapses the call is a
    re-probe: success on a previously-quarantined key RECOVERS it (clears the
    quarantine + fires ``plugin.recovered``); failure re-quarantines it.
    """
    kwargs = kwargs or {}
    if _is_quarantined(key):
        return (False, None)
    # Was this key quarantined (now past cooldown -> we are re-probing)?
    was_q = bool(_quarantine.get(key, {}).get("quarantined"))
    if timeout and timeout > 0:
        box: dict = {}

        def _runner():
            _rt0 = time.perf_counter()
            try:
                box["r"] = fn(*args, **kwargs)
                box["ok"] = True
            except Exception as e:  # noqa: BLE001
                box["err"] = e
            finally:
                box["_dt"] = time.perf_counter() - _rt0

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            _record_metric(key, False, timeout)
            _record_fail(key, TimeoutError(f"exceeded {timeout}s"))
            return (False, None)
        _dt = box.get("_dt", 0.0)
        if box.get("ok"):
            _record_metric(key, True, _dt)
            if was_q:
                _recover(key)
            return (True, box.get("r"))
        _record_metric(key, False, _dt)
        _record_fail(key, box.get("err") or RuntimeError("unknown"))
        return (False, None)
    _t0 = time.perf_counter()
    try:
        r = fn(*args, **kwargs)
        _record_metric(key, True, time.perf_counter() - _t0)
        if was_q:
            _recover(key)
        return (True, r)
    except Exception as e:  # noqa: BLE001
        _record_metric(key, False, time.perf_counter() - _t0)
        _record_fail(key, e)
        sys.stderr.write(f"[plugins] {key}: {e}\n")
        return (False, None)


# ── Decorators ────────────────────────────────────────────────────────
def extractor(site_id: str):
    """Register fn as the extractor for site_id. fn(url, context) -> dict.

    Must return a dict with at least `video_url`; {} or None means
    'couldn't extract; fall back to the default path.'"""
    def deco(fn: Callable):
        _extractors[site_id] = fn
        return fn
    return deco


def hook(event_name: str):
    """Register fn to fire on event_name. fn(payload: dict). Multiple allowed;
    fire in registration order. Errors are isolated + quarantine-counted."""
    def deco(fn: Callable):
        _hooks.setdefault(event_name, []).append(fn)
        return fn
    return deco


def processor(priority: int = 100, name: Optional[str] = None,
              timeout: Optional[float] = None):
    """Register a post-download processor. Lower priority runs first.

        @processor(priority=50)
        def move_to_nas(payload):
            # payload: {site_id, url, filename, path, file_size, ts, ...}
            return {"moved": "/nas/..."}    # optional result dict

    Results are collected and returned by run_processors()."""
    def deco(fn: Callable):
        _processors.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _processors.sort(key=lambda t: t[0])
        return fn
    return deco


def config_provider(priority: int = 100, name: Optional[str] = None,
                    timeout: Optional[float] = None):
    """Register a per-site config provider. Lower priority applies first.

        @config_provider()
        def proxy_for_site(site_id, cfg):
            if site_id == "foo":
                return {"proxy": "http://127.0.0.1:8888"}   # patch merged over cfg
            return {}

    Return a dict patch (merged shallow over the running cfg) or a full cfg."""
    def deco(fn: Callable):
        _config_providers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _config_providers.sort(key=lambda t: t[0])
        return fn
    return deco


def lifecycle(event_name: str, name: Optional[str] = None,
              timeout: Optional[float] = None):
    """Register a GATED full-access lifecycle hook. event_name in
    LIFECYCLE_EVENTS. These fire only when full-access is enabled.

        @lifecycle("after_context")
        def seed(context, page, site_id):
            page.add_init_script("...")        # full live access

    A plugin using this MUST declare capabilities ["lifecycle"] (and
    "page_access" if it touches the page) so the gate + status page see it."""
    if event_name not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event {event_name!r}; "
                         f"valid: {LIFECYCLE_EVENTS}")

    def deco(fn: Callable):
        _lifecycle.setdefault(event_name, []).append(
            (name or getattr(fn, "__name__", "?"), fn, timeout))
        return fn
    return deco


def recognizer(priority: int = 100, name: Optional[str] = None,
               timeout: Optional[float] = None):
    """Register a K1 recognizer: a review-only custom player/site detector.

        @recognizer(priority=50)
        def acme(dom_excerpt, network_summary, ctx):
            if "data-acme" in (dom_excerpt or ""):
                return {"player_family": "acme", "confidence": 0.9,
                        "evidence": ["data-acme"]}
            return {}              # no opinion -> ignored

    The verdict is ADVISORY: it is folded into the merged recognition
    scorecard by :func:`bulk_downloader.detect.merge_plugin_recognitions`
    but can NEVER auto-enable a template (posture invariant; the merge layer
    strips any enable signal). Low-confidence verdicts are demoted, errors are
    isolated + quarantine-counted. Lower priority runs first."""
    def deco(fn: Callable):
        _recognizers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _recognizers.sort(key=lambda t: t[0])
        return fn
    return deco


def prefilter(priority: int = 100, name: Optional[str] = None,
              timeout: Optional[float] = None):
    """Register a K2 pre-enqueue filter: decide/rewrite/tag a URL BEFORE queue.

        @prefilter(priority=10)
        def skip_in_library(url, meta, ctx):
            if already_have(url):
                return {"action": "drop", "reason": "already in library"}
            return {"action": "keep"}

    Return ``{action: keep|drop|rewrite, url?, tags?, reason?}``. Run in
    priority order (lower first); the first ``drop`` wins and short-circuits;
    ``rewrite`` mutates the threaded URL and composes; a throwing filter FAILS
    OPEN (the URL still enqueues) + is quarantine-counted."""
    def deco(fn: Callable):
        _prefilters.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _prefilters.sort(key=lambda t: t[0])
        return fn
    return deco


def namer(priority: int = 100, name: Optional[str] = None,
          timeout: Optional[float] = None):
    """Register a K3 namer: own the output filename/path. ONE winner by
    priority; lower runs first.

        @namer(priority=10)
        def plex_name(meta, ctx):
            return f"{meta['show']}/Season {meta['season']:02d}/{meta['title']}.mp4"

    Return a RELATIVE path (validated: no absolute, no ``..`` traversal; each
    component sanitized). Return ""/None to defer to the next namer / built-in."""
    def deco(fn: Callable):
        _namers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _namers.sort(key=lambda t: t[0])
        return fn
    return deco


def sink(priority: int = 100, name: Optional[str] = None,
         timeout: Optional[float] = None, idempotent: bool = False,
         max_attempts: Optional[int] = None):
    """Register a K5 sink: a retryable event consumer with delivery guarantees.

        @sink(priority=10, idempotent=True)
        def discord(event, payload, ctx):
            r = post(WEBHOOK, payload)
            if r.status == 429:
                return {"ok": False, "retry_after": r.retry_after}   # transient
            if r.status >= 400:
                return {"ok": False, "permanent": True}              # dead-letter
            return {"ok": True}                                      # ack

    The FRAMEWORK (plugins.deliver_to_sinks) owns retry/backoff/dead-letter +
    at-least-once. ``idempotent`` is declared (informational). Lower priority
    delivers first."""
    def deco(fn: Callable):
        _sinks.append((int(priority), name or getattr(fn, "__name__", "?"),
                       fn, timeout, bool(idempotent), max_attempts))
        _sinks.sort(key=lambda t: t[0])
        return fn
    return deco


def source(name: Optional[str] = None, *, interval_seconds: int = 3600,
           timeout: Optional[float] = None):
    """Register a K6 source/watcher: a plugin that PRODUCES URLs to enqueue,
    polled on a declared interval via bg_scheduler.

        @source(name="rss", interval_seconds=900)
        def feed(state, ctx):
            seen = set(state.get("seen", []))
            new = [u for u in fetch_feed() if u not in seen]
            return {"urls": new, "next_state": {"seen": list(seen | set(new))}}

    The framework threads ``state`` across polls (``next_state`` persists, so a
    source dedupes its own emissions), routes every emitted URL through the K2
    pre-enqueue filter chain, and isolates/bounds a throwing or hung poll."""
    def deco(fn: Callable):
        _sources.append((name or getattr(fn, "__name__", "?"), fn, timeout, int(interval_seconds)))
        return fn
    return deco


def enricher(priority: int = 100, name: Optional[str] = None,
             timeout: Optional[float] = None):
    """Register a K4 enricher: post-download.done sidecar metadata fetch+attach.
    Runs AFTER the processor stage; lower priority first.

        @enricher(priority=50)
        def tpdb(payload, ctx):
            meta = lookup(payload["filename"])           # plugin's own fetch
            return {"sidecar_files": {payload["filename"] + ".nfo": to_nfo(meta)},
                    "tags": ["enriched"], "metadata": meta}

    The framework writes each returned sidecar NEXT TO the download with a
    traversal-safe relative name; a failing/throwing enricher is NON-FATAL (the
    download already succeeded) + isolated + quarantine-counted."""
    def deco(fn: Callable):
        _enrichers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
        _enrichers.sort(key=lambda t: t[0])
        return fn
    return deco


# ── Programmatic register / unregister ────────────────────────────────
def register_extractor(site_id: str, fn: Callable):
    _extractors[site_id] = fn


def register_hook(event_name: str, fn: Callable):
    _hooks.setdefault(event_name, []).append(fn)


def register_processor(fn: Callable, priority: int = 100, name: Optional[str] = None,
                       timeout: Optional[float] = None):
    _processors.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _processors.sort(key=lambda t: t[0])


def register_config_provider(fn: Callable, priority: int = 100, name: Optional[str] = None,
                             timeout: Optional[float] = None):
    _config_providers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _config_providers.sort(key=lambda t: t[0])


def register_lifecycle(event_name: str, fn: Callable, name: Optional[str] = None,
                       timeout: Optional[float] = None):
    if event_name not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event {event_name!r}")
    _lifecycle.setdefault(event_name, []).append(
        (name or getattr(fn, "__name__", "?"), fn, timeout))


def register_recognizer(fn: Callable, priority: int = 100, name: Optional[str] = None,
                        timeout: Optional[float] = None):
    _recognizers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _recognizers.sort(key=lambda t: t[0])


def register_prefilter(fn: Callable, priority: int = 100, name: Optional[str] = None,
                       timeout: Optional[float] = None):
    _prefilters.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _prefilters.sort(key=lambda t: t[0])


def register_namer(fn: Callable, priority: int = 100, name: Optional[str] = None,
                   timeout: Optional[float] = None):
    _namers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _namers.sort(key=lambda t: t[0])


def register_sink(fn: Callable, priority: int = 100, name: Optional[str] = None,
                  timeout: Optional[float] = None, idempotent: bool = False,
                  max_attempts: Optional[int] = None):
    _sinks.append((int(priority), name or getattr(fn, "__name__", "?"),
                   fn, timeout, bool(idempotent), max_attempts))
    _sinks.sort(key=lambda t: t[0])


def register_source(fn: Callable, name: Optional[str] = None, *,
                    interval_seconds: int = 3600, timeout: Optional[float] = None):
    _sources.append((name or getattr(fn, "__name__", "?"), fn, timeout, int(interval_seconds)))


def register_enricher(fn: Callable, priority: int = 100, name: Optional[str] = None,
                      timeout: Optional[float] = None):
    _enrichers.append((int(priority), name or getattr(fn, "__name__", "?"), fn, timeout))
    _enrichers.sort(key=lambda t: t[0])


def unregister_extractor(site_id: str) -> bool:
    return _extractors.pop(site_id, None) is not None


def unregister_hook(event_name: str, fn: Callable) -> bool:
    fns = _hooks.get(event_name, [])
    try:
        fns.remove(fn)
        return True
    except ValueError:
        return False


# ── Call surfaces (used by BD core) ───────────────────────────────────
def get_extractor(site_id: str) -> Optional[Callable]:
    return _extractors.get(site_id)


def fire_hook(event_name: str, payload: dict) -> None:
    """Fire all callbacks for event_name. Errors isolated + quarantine-counted."""
    for fn in list(_hooks.get(event_name, [])):
        _call_guarded(_qkey(fn, "hook:"), fn, (payload,))


def emit(event_name: str, payload: dict) -> None:
    """Canonical producer entry for a hook event (E1).

    Validates that ``event_name`` is a documented ``HOOK_EVENTS`` member -- an
    undocumented event warns (so a stray/renamed producer is visible) but STILL
    fires, so a producer is never silently dropped -- then dispatches through the
    isolated :func:`fire_hook` path (each consumer is quarantine-guarded; a
    throwing consumer never breaks the producer).
    """
    if event_name not in HOOK_EVENTS:
        sys.stderr.write(f"[plugins] emit: undocumented event {event_name!r}\n")
    fire_hook(event_name, payload)


def run_processors(payload: dict) -> list:
    """Run all post-download processors in priority order. Returns a list of
    {name, ok, result} for processors that ran (quarantined ones are skipped)."""
    out = []
    for (_prio, name, fn, timeout) in list(_processors):
        key = _qkey(fn, "processor:")
        if _is_quarantined(key):
            continue
        ok, result = _call_guarded(key, fn, (payload,), timeout=timeout)
        out.append({"name": name, "ok": ok, "result": result if ok else None})
    return out


def resolve_site_config(site_id: str, base_cfg: Optional[dict] = None) -> dict:
    """Apply registered config providers over base_cfg, in priority order.

    Each provider returns a dict patch (merged shallow) or a full cfg. A
    failing/quarantined provider is skipped. Returns the merged cfg (a copy;
    base_cfg is never mutated)."""
    cfg = dict(base_cfg or {})
    for (_prio, _name, fn, timeout) in list(_config_providers):
        key = _qkey(fn, "config:")
        if _is_quarantined(key):
            continue
        ok, patch = _call_guarded(key, fn, (site_id, dict(cfg)), timeout=timeout)
        if ok and isinstance(patch, dict):
            cfg.update(patch)
    return cfg


def fire_lifecycle(event_name: str, *args, **kwargs) -> int:
    """Fire gated lifecycle hooks for event_name. NO-OP unless full-access is
    enabled. Returns the number of hooks invoked. Hooks get the live objects
    passed through (context, page, launch kwargs, artifact, ...)."""
    if not _full_access_enabled:
        return 0
    if event_name not in LIFECYCLE_EVENTS:
        return 0
    n = 0
    for (_name, fn, timeout) in list(_lifecycle.get(event_name, [])):
        key = _qkey(fn, f"lifecycle.{event_name}:")
        if _is_quarantined(key):
            continue
        ok, _ = _call_guarded(key, fn, args, kwargs, timeout=timeout)
        if ok:
            n += 1
    return n


def run_recognizers(dom_excerpt: str = "", network_summary: Optional[dict] = None,
                    ctx: Optional[dict] = None) -> list:
    """Run all registered K1 recognizer plugins in priority order.

    Returns a list of raw advisory verdicts:
        [{"name", "ok", "verdict"}]
    where ``verdict`` is the plugin's ``{player_family, confidence, evidence,
    ...}`` dict (or ``{}`` when the plugin had no opinion / ``None`` when it
    failed). Quarantined plugins are skipped; a throwing plugin is isolated +
    quarantine-counted (``ok=False``). This function is the runner only -- the
    review-only FOLDING (confidence clamp/demote, enable-signal stripping,
    builtin-preservation) is done by
    :func:`bulk_downloader.detect.merge_plugin_recognitions`."""
    out = []
    for (_prio, name, fn, timeout) in list(_recognizers):
        key = _qkey(fn, "recognizer:")
        if _is_quarantined(key):
            continue
        ok, verdict = _call_guarded(
            key, fn, (dom_excerpt, network_summary, ctx), timeout=timeout)
        if ok and isinstance(verdict, dict):
            out.append({"name": name, "ok": True, "verdict": verdict})
        elif ok:
            out.append({"name": name, "ok": True, "verdict": {}})
        else:
            out.append({"name": name, "ok": False, "verdict": None})
    return out


def run_prefilters(url: str, meta: Optional[dict] = None,
                   ctx: Optional[dict] = None) -> dict:
    """Run the K2 pre-enqueue filter chain over ``url`` in priority order.

    Returns ``{"action": "keep"|"drop", "url": <final>, "tags": [...],
    "reasons": [...], "applied": [...]}``.

    Semantics:
      * the first filter returning ``action == "drop"`` wins and
        SHORT-CIRCUITS (later filters do not run); its ``reason`` is recorded;
      * ``action == "rewrite"`` threads a (validated, non-empty, str) ``url``
        forward so later filters see it; tags accumulate;
      * ``keep`` / no-opinion / quarantined / throwing filters leave the URL
        unchanged -- a throwing filter FAILS OPEN (the URL still enqueues) and
        is quarantine-counted via ``_call_guarded``.
    """
    cur = url
    tags: list = []
    reasons: list = []
    applied: list = []
    for (_prio, name, fn, timeout) in list(_prefilters):
        key = _qkey(fn, "prefilter:")
        if _is_quarantined(key):
            continue
        ok, res = _call_guarded(key, fn, (cur, dict(meta or {}), ctx), timeout=timeout)
        if not ok or not isinstance(res, dict):
            # fail OPEN: a buggy/quarantined filter must not swallow the URL.
            continue
        action = str(res.get("action") or "keep").lower()
        new_tags = res.get("tags")
        if isinstance(new_tags, (list, tuple)):
            tags.extend(str(t) for t in new_tags)
        if action == "drop":
            reason = res.get("reason")
            if reason:
                reasons.append(str(reason))
            applied.append({"name": name, "action": "drop"})
            return {"action": "drop", "url": cur, "tags": tags,
                    "reasons": reasons, "applied": applied}
        if action == "rewrite":
            new_url = res.get("url")
            if isinstance(new_url, str) and new_url:
                cur = new_url
                applied.append({"name": name, "action": "rewrite"})
    return {"action": "keep", "url": cur, "tags": tags,
            "reasons": reasons, "applied": applied}


def _validate_namer_path(rel) -> Optional[str]:
    """Return a CWE-22-safe RELATIVE path, or None if ``rel`` is unusable.

    Mirrors the /screenshots/ basename discipline: reject absolute paths
    (posix ``/`` or Windows drive) and any ``..`` traversal component; sanitize
    each remaining component via the canonical ``fname`` sanitizer; normalize
    separators to ``/``."""
    if not isinstance(rel, str):
        return None
    rel = rel.strip()
    if not rel:
        return None
    norm = rel.replace("\\", "/")
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return None
    try:
        from . import fname as _fn
    except Exception:
        _fn = None
    parts = []
    for comp in norm.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            return None
        safe = _fn._sanitize_filename_var(comp, allow_paths=False) if _fn else comp
        if not safe:
            return None
        parts.append(safe)
    if not parts:
        return None
    return "/".join(parts)


def run_namer(meta: Optional[dict] = None, ctx: Optional[dict] = None) -> Optional[str]:
    """Resolve the output relative path from K3 namer plugins. Priority winner:
    the first plugin returning a NON-EMPTY, VALID relative path wins. A plugin
    returning ""/None/invalid (absolute or traversal) is skipped. Returns None
    when no plugin produces a valid path -> caller falls back to the built-in
    namer. Throwing/quarantined namers are isolated + skipped."""
    for (_prio, name, fn, timeout) in list(_namers):
        key = _qkey(fn, "namer:")
        if _is_quarantined(key):
            continue
        ok, rel = _call_guarded(key, fn, (dict(meta or {}), ctx), timeout=timeout)
        if not ok or rel is None or rel == "":
            continue
        safe = _validate_namer_path(rel)
        if safe:
            return safe
    return None


def deliver_to_sinks(event: str, payload: dict, ctx: Optional[dict] = None, *,
                     max_attempts: Optional[int] = None, sleep=None) -> list:
    """Deliver ``event``/``payload`` to every registered K5 sink with retry +
    backoff + dead-letter + at-least-once.

    Returns ``[{name, ok, attempts, dead_lettered, skipped}]``. Per sink:
      * up to ``max_attempts`` (call arg > sink decl > ``_SINK_MAX_ATTEMPTS``);
      * ``{"ok": True}`` acks and stops;
      * ``{"ok": False, "permanent": True}`` dead-letters immediately (no retry);
      * any other non-ack (or a raise) is transient -> backoff (``retry_after``
        if given, else exponential) + retry until attempts exhaust, then
        dead-letter;
      * an exhausted/permanent delivery records ONE R2 fail (clean not-ok
        returns; raises are already counted by ``_call_guarded``) -> a sink that
        sustains failure is quarantined + skipped;
      * a dead-lettered event is preserved in the dead-letter queue (never
        silently dropped -- at-least-once)."""
    _sleep = sleep if sleep is not None else time.sleep
    results = []
    for (_prio, name, fn, timeout, _idem, sink_max) in list(_sinks):
        key = _qkey(fn, "sink:")
        if _is_quarantined(key):
            results.append({"name": name, "ok": False, "attempts": 0,
                            "dead_lettered": False, "skipped": True})
            continue
        cap = int(max_attempts or sink_max or _SINK_MAX_ATTEMPTS)
        if cap < 1:
            cap = 1
        attempts = 0
        acked = False
        dead = False
        last_reason = ""
        last_ok_guarded = True   # did the final attempt return cleanly (vs raise)?
        while attempts < cap:
            attempts += 1
            ok, res = _call_guarded(key, fn, (event, dict(payload or {}), ctx),
                                    timeout=timeout)
            last_ok_guarded = ok
            if ok and isinstance(res, dict) and res.get("ok"):
                acked = True
                break
            permanent = bool(ok and isinstance(res, dict) and res.get("permanent"))
            if not ok:
                last_reason = "exception"
            elif isinstance(res, dict):
                last_reason = str(res.get("reason")
                                  or ("permanent" if permanent else "transient"))
            else:
                last_reason = "no-ack"
            if permanent:
                dead = True
                break
            if attempts < cap:
                retry_after = res.get("retry_after") if (ok and isinstance(res, dict)) else None
                if isinstance(retry_after, (int, float)) and retry_after > 0:
                    backoff = float(retry_after)
                else:
                    backoff = _SINK_BACKOFF_BASE * (2 ** (attempts - 1))
                try:
                    _sleep(backoff)
                except Exception:
                    pass
            else:
                dead = True   # transient retries exhausted
        if not acked and dead:
            _dead_letter.append({"sink": name, "event": event,
                                 "payload": dict(payload or {}),
                                 "attempts": attempts, "reason": last_reason})
            # Only count here for CLEAN not-ok returns; a raise was already
            # counted inside _call_guarded (avoid double-counting toward R2).
            if last_ok_guarded:
                _record_fail(key, RuntimeError(f"sink {name} undelivered: {last_reason}"))
        results.append({"name": name, "ok": acked, "attempts": attempts,
                        "dead_lettered": (not acked) and dead, "skipped": False})
    return results


def list_dead_letter() -> list:
    """Return a copy of the K5 dead-letter queue (undelivered events)."""
    return [dict(d) for d in _dead_letter]


def drain_dead_letter() -> list:
    """Return + clear the dead-letter queue (e.g. for a manual re-drive)."""
    out = [dict(d) for d in _dead_letter]
    _dead_letter.clear()
    return out


def poll_sources(ctx: Optional[dict] = None, *, enqueue_fn=None) -> dict:
    """Poll every registered K6 source once. Returns
    ``{name: {ok, emitted, dropped, raw, skipped}}``.

    Per source: skip if quarantined; thread the persisted ``state`` in and store
    the returned ``next_state``; route every emitted URL THROUGH the K2
    pre-enqueue filter chain (:func:`run_prefilters`) -- dropped URLs are
    excluded, rewrites applied -- then hand survivors to ``enqueue_fn`` if given.
    A throwing/hung poll is isolated + bounded via ``_call_guarded`` (R1
    timeout); a clean-but-malformed return records one R2 fail. Sustained
    failure quarantines the source."""
    out: dict = {}
    for (name, fn, timeout, _interval) in list(_sources):
        key = _qkey(fn, "source:")
        if _is_quarantined(key):
            out[name] = {"ok": False, "emitted": [], "dropped": [],
                         "raw": 0, "skipped": True}
            continue
        state = _source_state.get(name, {})
        ok, res = _call_guarded(key, fn, (dict(state), ctx), timeout=timeout)
        if not ok:
            # raise/timeout already R2-counted inside _call_guarded.
            out[name] = {"ok": False, "emitted": [], "dropped": [],
                         "raw": 0, "skipped": False}
            continue
        if not isinstance(res, dict):
            _record_fail(key, RuntimeError(f"source {name} bad return"))
            out[name] = {"ok": False, "emitted": [], "dropped": [],
                         "raw": 0, "skipped": False}
            continue
        if "next_state" in res:
            _source_state[name] = res.get("next_state") or {}
        urls = res.get("urls") or []
        emitted: list = []
        dropped: list = []
        for u in urls:
            if not isinstance(u, str) or not u:
                continue
            verdict = run_prefilters(u, {"source": name}, ctx)
            if verdict.get("action") == "drop":
                dropped.append(u)
                continue
            final = verdict.get("url", u)
            emitted.append(final)
            if enqueue_fn is not None:
                try:
                    enqueue_fn(final, {"source": name, "tags": verdict.get("tags", [])})
                except Exception:  # noqa: BLE001
                    pass
        out[name] = {"ok": True, "emitted": emitted, "dropped": dropped,
                     "raw": len(urls), "skipped": False}
    return out


def schedule_sources(register=None, ctx: Optional[dict] = None, enqueue_fn=None) -> list:
    """Register each K6 source's poll under bg_scheduler at its declared
    interval. ``register`` defaults to ``bg_scheduler.register`` (injectable for
    tests). Returns the list of scheduled source names."""
    if register is None:
        from . import bg_scheduler as _bs
        register = _bs.register
    scheduled = []
    for (name, _fn, _timeout, interval) in list(_sources):
        _src_name = name

        def _task(_n=_src_name):
            return poll_sources(ctx, enqueue_fn=enqueue_fn).get(_n)
        register(f"source:{name}", _task, interval_seconds=int(interval))
        scheduled.append(name)
    return scheduled


def run_enrichers(payload: dict, ctx: Optional[dict] = None) -> list:
    """Run K4 enrichers in priority order AFTER the processor stage. For each,
    writes the returned ``sidecar_files`` NEXT TO the download
    (``payload["path"]``'s directory) with traversal-safe relative names.

    Returns ``[{name, ok, sidecars, tags, metadata}]``. NON-FATAL by
    construction: a throwing/quarantined enricher, a bad return, a traversal-
    unsafe name, or a write error is skipped -- never raised (the download
    already succeeded). ``sidecar_files`` accepts ``{relname: content}`` or
    ``[{name, content}]``."""
    out = []
    dl_path = (payload or {}).get("path")
    base_dir = None
    if isinstance(dl_path, str) and dl_path:
        try:
            base_dir = os.path.dirname(os.path.abspath(dl_path))
        except Exception:
            base_dir = None
    for (_prio, name, fn, timeout) in list(_enrichers):
        key = _qkey(fn, "enricher:")
        if _is_quarantined(key):
            continue
        ok, res = _call_guarded(key, fn, (dict(payload or {}), ctx), timeout=timeout)
        if not ok or not isinstance(res, dict):
            out.append({"name": name, "ok": False, "sidecars": [],
                        "tags": [], "metadata": {}})
            continue
        sidecars = res.get("sidecar_files") or {}
        items = []
        if isinstance(sidecars, dict):
            items = list(sidecars.items())
        elif isinstance(sidecars, (list, tuple)):
            for it in sidecars:
                if isinstance(it, dict) and "name" in it:
                    items.append((it["name"], it.get("content", "")))
        written = []
        for rel, content in items:
            safe = _validate_namer_path(rel)
            if not safe or base_dir is None:
                continue
            dest = os.path.join(base_dir, safe)
            try:
                # defense-in-depth: dest must stay within base_dir
                if os.path.commonpath([base_dir, os.path.abspath(dest)]) != base_dir:
                    continue
            except Exception:
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content if isinstance(content, str) else json.dumps(content))
                written.append(dest)
            except Exception:  # noqa: BLE001
                pass
        out.append({"name": name, "ok": True, "sidecars": written,
                    "tags": list(res.get("tags") or []),
                    "metadata": dict(res.get("metadata") or {})})
    return out


# ── Introspection ─────────────────────────────────────────────────────
def list_extractors() -> list:
    return [{"site_id": sid, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?")}
            for sid, fn in _extractors.items()]


def list_hooks() -> dict:
    return {event: [{"function": getattr(fn, "__name__", "?"),
                     "module": getattr(fn, "__module__", "?")}
                    for fn in fns]
            for event, fns in _hooks.items()}


def list_processors() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _processors]


def list_config_providers() -> list:
    return [{"name": name, "priority": prio, "module": getattr(fn, "__module__", "?")}
            for (prio, name, fn, timeout) in _config_providers]


def list_lifecycle() -> dict:
    return {event: [{"name": name, "module": getattr(fn, "__module__", "?")}
                    for (name, fn, timeout) in fns]
            for event, fns in _lifecycle.items()}


def list_recognizers() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _recognizers]


def list_prefilters() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _prefilters]


def list_namers() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _namers]


def list_sinks() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout,
             "idempotent": idem, "max_attempts": mx}
            for (prio, name, fn, timeout, idem, mx) in _sinks]


# ── K7: healthcheck kind (plugin-contributed self-test probes) ────────
def register_healthcheck(fn: Callable, priority: int = 100, name: Optional[str] = None,
                         timeout: Optional[float] = None):
    """Register a K7 healthcheck: a zero-arg probe a plugin contributes to BD's
    self-test. It returns a dict ``{ok: bool, message: str}`` (or a bare bool);
    the host invokes it under full error isolation and surfaces the row on the
    health page. A throwing probe is isolated + quarantine-counted, never a
    crash."""
    _healthchecks.append((int(priority), name or getattr(fn, "__name__", "?"),
                          fn, timeout))
    _healthchecks.sort(key=lambda t: t[0])


def list_healthchecks() -> list:
    return [{"name": name, "priority": prio,
             "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _healthchecks]


def run_healthchecks() -> list:
    """Run every registered K7 healthcheck in priority order under full error
    isolation. Returns ``[{name, ok, message}]``. A probe that returns a dict
    contributes its ``ok``/``message``; a bare truthy/falsy return is coerced;
    a throwing or quarantined probe yields ``ok=False`` (never raises)."""
    out = []
    for (_prio, name, fn, timeout) in list(_healthchecks):
        key = _qkey(fn, "healthcheck:")
        if _is_quarantined(key):
            out.append({"name": name, "ok": False, "message": "quarantined"})
            continue
        ok, res = _call_guarded(key, fn, (), timeout=timeout)
        if not ok:
            out.append({"name": name, "ok": False, "message": "probe failed"})
        elif isinstance(res, dict):
            out.append({"name": name, "ok": bool(res.get("ok", False)),
                        "message": str(res.get("message", ""))[:300]})
        else:
            out.append({"name": name, "ok": bool(res), "message": ""})
    return out


def list_sources() -> list:
    return [{"name": name, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout,
             "interval_seconds": interval}
            for (name, fn, timeout, interval) in _sources]


def list_enrichers() -> list:
    return [{"name": name, "priority": prio, "function": getattr(fn, "__name__", "?"),
             "module": getattr(fn, "__module__", "?"), "timeout": timeout}
            for (prio, name, fn, timeout) in _enrichers]


def list_quarantine() -> list:
    return [{"key": k, **v} for k, v in _quarantine.items() if v.get("quarantined")]


# ── Plugin loader ─────────────────────────────────────────────────────
def _plugin_dir() -> Path:
    try:
        from .constants import INSTALL_DIR
        return Path(INSTALL_DIR) / "plugins"
    except Exception:
        return Path.cwd() / "plugins"


def _read_load_config(pdir: Path) -> dict:
    """Read optional plugins.json from the plugin dir; env overrides. Returns
    {enabled, disabled, order, allow_full_access}. Absent -> permissive."""
    cfg = {"enabled": None, "disabled": [], "order": [], "allow_full_access": False,
           "node_bin": "", "risk_acknowledged": False, "granted_capabilities": [],
           "force_isolated": []}
    fp = pdir / "plugins.json"
    if fp.is_file():
        try:
            raw = json.loads(fp.read_text("utf-8"))
            if isinstance(raw.get("enabled"), list):
                cfg["enabled"] = [str(x) for x in raw["enabled"]]
            if isinstance(raw.get("disabled"), list):
                cfg["disabled"] = [str(x) for x in raw["disabled"]]
            if isinstance(raw.get("order"), list):
                cfg["order"] = [str(x) for x in raw["order"]]
            cfg["allow_full_access"] = bool(raw.get("allow_full_access", False))
            cfg["risk_acknowledged"] = bool(raw.get("risk_acknowledged", False))
            # V3-A W3: per-capability operator consent. Deny-by-default; a gated cap
            # named here loads without opening full-access.
            if isinstance(raw.get("granted_capabilities"), list):
                cfg["granted_capabilities"] = [str(x) for x in raw["granted_capabilities"]]
            # W6 (778): operator-forced isolation. A listed .py file (or "*")
            # never takes the in-proc importlib path.
            if isinstance(raw.get("force_isolated"), list):
                cfg["force_isolated"] = [str(x) for x in raw["force_isolated"]]
            if isinstance(raw.get("node_bin"), str):
                cfg["node_bin"] = raw["node_bin"].strip()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugins] plugins.json unreadable: {e}\n")
    env = os.environ.get("BD_PLUGINS_ALLOW_FULL_ACCESS", "")
    if env.strip():
        cfg["allow_full_access"] = env.strip() not in ("0", "false", "False", "no", "")
    env_en = os.environ.get("BD_PLUGINS_ENABLE", "")
    if env_en.strip():
        cfg["enabled"] = [x.strip() for x in env_en.split(",") if x.strip()]
    return cfg


def _plugin_files(pdir: Path) -> list:
    """Loadable plugin files (non-`_`), sorted by name.

    Covers .py (in-proc/bridge) + node .js/.mjs + the X1 generic-exec languages
    (.rb/.sh/.php) + no-suffix executables (shebang scripts / compiled binaries
    with the exec bit set), so any-language plugins are discovered uniformly.
    """
    out = [p for p in pdir.glob("*.py") if not p.name.startswith("_")]
    for suf in ("*.js", "*.mjs", "*.rb", "*.sh", "*.php"):
        out += [p for p in pdir.glob(suf) if not p.name.startswith("_")]
    # No-suffix executables (X1 direct-exec): only those with the exec bit set,
    # so plain data files (plugins.json, READMEs without a suffix) are ignored.
    for p in pdir.iterdir():
        if (p.is_file() and p.suffix == "" and not p.name.startswith("_")
                and os.access(str(p), os.X_OK)):
            out.append(p)
    return sorted(out, key=lambda p: p.name)


def _ordered_files(pdir: Path, lcfg: dict) -> list:
    """Resolve which plugin files to load and in what order, honoring
    enable/disable/order. Covers .py + node .js/.mjs uniformly."""
    present = _plugin_files(pdir)
    by_name = {p.name: p for p in present}
    disabled = set(lcfg.get("disabled") or [])
    if lcfg.get("enabled") is not None:
        # explicit allowlist, in listed order; ignore disabled + absent
        out = []
        for nm in lcfg["enabled"]:
            if nm in by_name and nm not in disabled:
                out.append(by_name[nm])
        return out
    # no allowlist: all present minus disabled, ordered by `order` then alpha
    order = [nm for nm in (lcfg.get("order") or []) if nm in by_name]
    rest = [p.name for p in present if p.name not in order]
    final_names = [nm for nm in (order + sorted(rest)) if nm not in disabled]
    return [by_name[nm] for nm in final_names]


def _validate_manifest(mod, filename: str) -> tuple:
    """Return (ok, manifest, skip_reason). Enforces api_version + full-access gate."""
    man = getattr(mod, "PLUGIN", None)
    if not isinstance(man, dict):
        return (True, {}, "")  # manifest optional
    ok_api, why = api_compatible(man)
    if not ok_api:
        return (False, man, why)
    caps = set(man.get("capabilities") or [])
    ok_gate, gate_why = capability_gate(
        caps, _GATED_CAPS, _full_access_enabled, _granted_capabilities)
    if not ok_gate:
        return (False, man, gate_why)
    ok_req, req_why = requires_satisfied(man)
    if not ok_req:
        return (False, man, req_why)
    return (True, man, "")


def load_all() -> dict:
    """Scan the plugin dir and import each eligible *.py file. Idempotent.

    Honors plugins.json (enable/disable/order/allow_full_access) + env. Reads
    each module's PLUGIN manifest; skips on api_version mismatch or an
    unsatisfied full-access requirement. Returns a summary dict."""
    pdir = _plugin_dir()
    out: dict = {"loaded": 0, "errors": 0, "skipped": 0,
                 "full_access": False, "plugins": []}
    if not pdir.is_dir():
        out["full_access"] = _full_access_enabled
        return out

    lcfg = _read_load_config(pdir)
    _load_quarantine_state()    # R2: re-hydrate quarantine across restarts
    set_full_access(lcfg.get("allow_full_access", False))
    set_granted_capabilities(lcfg.get("granted_capabilities", []))
    set_force_isolated(lcfg.get("force_isolated", []))
    out["full_access"] = _full_access_enabled
    if _full_access_enabled:
        sys.stderr.write("[plugins] full-access lifecycle hooks ENABLED\n")

    for path in _ordered_files(pdir, lcfg):
        suf = path.suffix.lower()
        if suf in (".js", ".mjs"):
            # node-runtime plugin: delegate to the subprocess bridge.
            from . import plugin_node
            entry = plugin_node.load_node_plugin(
                path, full_access=_full_access_enabled,
                gated_caps=_GATED_CAPS, granted_caps=_granted_capabilities,
                api_version=PLUGIN_API_VERSION)
            if entry.get("ok"):
                out["loaded"] += 1
                if entry.get("manifest"):
                    _manifests[path.name] = entry["manifest"]
            elif entry.get("skipped_reason"):
                out["skipped"] += 1
                sys.stderr.write(
                    f"[plugins] skipped {path.name}: {entry['skipped_reason']}\n")
            else:
                out["errors"] += 1
                sys.stderr.write(
                    f"[plugins] failed to load {path.name}: {entry.get('error','')}\n")
            entry["loaded_at"] = time.time()
            out["plugins"].append(entry)
            _loaded.append(entry)
            continue
        if suf != ".py":
            # X1: any-language / no-suffix executable plugin via the generic
            # interpreter-keyed exec bridge (mirrors the node delegate).
            from . import plugin_exec as _px
            interp = _px.interpreter_for(path, lcfg)
            if interp is None:
                out["skipped"] += 1
                sys.stderr.write(
                    f"[plugins] skipped {path.name}: unsupported plugin type\n")
                continue
            entry = _px.load_exec_plugin(
                path, interp=interp, full_access=_full_access_enabled,
                gated_caps=_GATED_CAPS, granted_caps=_granted_capabilities,
                api_version=PLUGIN_API_VERSION)
            if entry.get("ok"):
                out["loaded"] += 1
                if entry.get("manifest"):
                    _manifests[path.name] = entry["manifest"]
            elif entry.get("skipped_reason"):
                out["skipped"] += 1
                sys.stderr.write(
                    f"[plugins] skipped {path.name}: {entry['skipped_reason']}\n")
            else:
                out["errors"] += 1
                sys.stderr.write(
                    f"[plugins] failed to load {path.name}: {entry.get('error','')}\n")
            entry["loaded_at"] = time.time()
            out["plugins"].append(entry)
            _loaded.append(entry)
            continue
        from . import plugin_py_bridge as _pyb
        _forced = _is_force_isolated(path.name)
        if _pyb.is_bridge_file(path):
            # subprocess-isolated .py plugin (R1): same contract as node.
            entry = _pyb.load_py_plugin(
                path, full_access=_full_access_enabled,
                gated_caps=_GATED_CAPS, granted_caps=_granted_capabilities,
                api_version=PLUGIN_API_VERSION, force_isolated=_forced)
            if entry.get("ok"):
                out["loaded"] += 1
                if entry.get("manifest"):
                    _manifests[path.name] = entry["manifest"]
            elif entry.get("skipped_reason"):
                out["skipped"] += 1
                sys.stderr.write(
                    f"[plugins] skipped {path.name}: {entry['skipped_reason']}\n")
            else:
                out["errors"] += 1
                sys.stderr.write(
                    f"[plugins] failed to load {path.name}: {entry.get('error','')}\n")
            entry["loaded_at"] = time.time()
            out["plugins"].append(entry)
            _loaded.append(entry)
            continue
        entry: dict = {"filename": path.name, "ok": False, "error": "",
                       "manifest": {}, "skipped_reason": ""}
        if _forced:
            # W6 (778): the operator requires isolation and this file lacks
            # the bridge contract -- SKIP, never silently degrade to in-proc
            # (granted capabilities do not weaken the refusal). The remedy is
            # in the reason: add the sentinel + speak the contract.
            entry["skipped_reason"] = (
                "operator requires isolation (force_isolated) but the plugin "
                "lacks the # bd:bridge contract -- add the sentinel and speak "
                "the bridge protocol, or unlist the file")
            out["skipped"] += 1
            sys.stderr.write(
                f"[plugins] skipped {path.name}: {entry['skipped_reason']}\n")
            entry["loaded_at"] = time.time()
            out["plugins"].append(entry)
            _loaded.append(entry)
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"bd_plugin_{path.stem}", str(path))
            if not spec or not spec.loader:
                raise ImportError("could not create spec")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            ok, man, reason = _validate_manifest(mod, path.name)
            entry["manifest"] = man
            if not ok:
                # Roll back any registrations the module made during import by
                # re-running reset is too broad; instead we note the skip. Since
                # exec already ran, a gated module SHOULD guard its own
                # registration on capability; we still surface the skip clearly.
                entry["skipped_reason"] = reason
                out["skipped"] += 1
                sys.stderr.write(f"[plugins] skipped {path.name}: {reason}\n")
            else:
                hook_err = _wire_enable_disable(mod, man, path.name)
                if hook_err:
                    entry["skipped_reason"] = hook_err
                    out["skipped"] += 1
                    sys.stderr.write(
                        f"[plugins] skipped {path.name}: {hook_err}\n")
                else:
                    entry["ok"] = True
                    out["loaded"] += 1
                    if man:
                        _manifests[path.name] = man
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)[:300]
            out["errors"] += 1
            sys.stderr.write(f"[plugins] failed to load {path.name}: {e}\n")
        entry["loaded_at"] = time.time()
        out["plugins"].append(entry)
        _loaded.append(entry)
    return out


def discovered_plugins() -> list:
    """Filenames of loadable plugins (.py + node .js/.mjs, non-`_`), sorted.
    For the GUI plugin-config panel."""
    pdir = _plugin_dir()
    if not pdir.is_dir():
        return []
    return [p.name for p in _plugin_files(pdir)]


def read_config() -> dict:
    """Current plugins.json config (enabled/disabled/order/allow_full_access),
    env overrides applied. Public wrapper for the GUI."""
    return _read_load_config(_plugin_dir())


_CONFIG_KEYS = ("enabled", "disabled", "order", "allow_full_access", "node_bin",
                "risk_acknowledged", "granted_capabilities", "force_isolated")


def write_config(patch: dict) -> dict:
    """Merge `patch` into plugins.json and write it (only known keys honored).
    Creates the plugin dir if needed. Returns the new on-disk config. This is
    the write side of the GUI plugin-config panel -- BD_PLUGINS_ENABLE /
    BD_PLUGINS_ALLOW_FULL_ACCESS remain env overrides of the same knobs, and
    node_bin (the node-runtime path for node plugins) is GUI-settable here with
    BD_PLUGINS_NODE_BIN as its env override."""
    pdir = _plugin_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    fp = pdir / "plugins.json"
    cur: dict = {}
    if fp.is_file():
        try:
            raw = json.loads(fp.read_text("utf-8"))
            if isinstance(raw, dict):
                cur = raw
        except Exception:  # noqa: BLE001
            cur = {}
    for k in _CONFIG_KEYS:
        if k not in patch:
            continue
        if k in ("allow_full_access", "risk_acknowledged"):
            cur[k] = bool(patch[k])
        elif k == "node_bin":
            cur[k] = str(patch[k] or "").strip()
        elif isinstance(patch[k], list):
            cur[k] = [str(x) for x in patch[k]]
    fp.write_text(json.dumps(cur, indent=2), "utf-8")
    return cur


# ── O5: managed install + registry ────────────────────────────────────
# Registry of plugins installed via install_plugin() -- distinct from a file
# hand-dropped into the plugin dir (which still loads, but has no record) and
# from .plugin_state.json (quarantine). No signing: BD is single-operator and
# plugins run in-process with full privilege regardless, so a signature would
# add authenticity without containment. install_plugin gates on the
# at-your-own-risk acknowledgment + the API version-range and NEVER executes
# the candidate module (it ast-reads the PLUGIN manifest).
_REGISTRY_SCHEMA = 1


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _registry_path() -> Path:
    return _plugin_dir() / "plugins.registry.json"


def _read_registry() -> dict:
    try:
        raw = json.loads(_registry_path().read_text("utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("plugins"), dict):
            return {"schema": int(raw.get("schema", _REGISTRY_SCHEMA)),
                    "plugins": raw["plugins"]}
    except Exception:  # noqa: BLE001  (absent / corrupt -> empty registry)
        pass
    return {"schema": _REGISTRY_SCHEMA, "plugins": {}}


def _write_registry(reg: dict) -> None:
    """Atomically persist the registry. Best-effort, like the quarantine state:
    a write failure (no dir, read-only fs) is non-fatal."""
    try:
        pdir = _plugin_dir()
        if not pdir.is_dir():
            return
        rp = _registry_path()
        tmp = rp.with_name(rp.name + ".incoming")
        tmp.write_text(json.dumps(reg, indent=2), "utf-8")
        os.replace(str(tmp), str(rp))
    except Exception:  # noqa: BLE001
        pass


def installed_registry() -> list:
    """GUI-facing: sorted records of plugins installed via the managed install
    path. A hand-dropped file is intentionally absent here."""
    out = []
    for fn, rec in sorted(_read_registry().get("plugins", {}).items()):
        r = dict(rec)
        r["file"] = fn
        out.append(r)
    return out


def _read_manifest_ast(text: str) -> dict:
    """Extract the module-level ``PLUGIN = {...}`` dict literal WITHOUT executing
    the module. Returns {} when absent or non-literal (manifest is optional).
    Checks ast.Assign AND ast.AnnAssign (the AnnAssign blind-spot lesson)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "PLUGIN" and node.value is not None:
                try:
                    val = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    return {}
                return val if isinstance(val, dict) else {}
    return {}


def _resolve_source(src: str) -> tuple:
    """Resolve an install source to ``(text, filename, source_label)``.

    Ships with the LOCAL-PATH resolver only. A curated/URL fetch is the
    stash-only, Tier-A, operator-go part (the offline sandbox can't exercise a
    network fetch); callers needing it pass a pre-fetched local path.
    """
    p = Path(src)
    if p.is_file():
        return (p.read_text("utf-8"), p.name, "local:" + str(p.resolve()))
    raise FileNotFoundError(f"install source not found: {src}")


def install_plugin(src: str, *, ack: bool = False, force: bool = False) -> dict:
    """Install a plugin from a local path into the plugin dir.

    Pipeline (NO plugin code is executed at any step):
      1. resolve source bytes + filename
      2. ast-read the PLUGIN manifest (never exec/import)
      3. version-range gate (api_compatible / R5) -> refuse if incompatible
      4. at-your-own-risk gate -> refuse unless ``ack`` or the persisted
         plugins.json ``risk_acknowledged`` flag is set
      5. atomic stage (temp + os.replace) so discovery never sees a partial
      6. record in plugins.registry.json

    Returns ``{"installed": bool, ...}``. Does NOT enable or load the plugin --
    that stays the operator's plugins.json + load_all() concern.
    """
    try:
        text, name, source_label = _resolve_source(src)
    except (FileNotFoundError, OSError, UnicodeDecodeError) as e:
        return {"installed": False, "reason": f"unreadable source: {e}"}

    man = _read_manifest_ast(text)

    ok, reason = api_compatible(man)
    if not ok:
        return {"installed": False, "reason": f"api incompatible: {reason}"}

    if not (ack or read_config().get("risk_acknowledged", False)):
        return {"installed": False, "reason": "risk not acknowledged",
                "disclaimer": disclaimer()}

    pdir = _plugin_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    dst = pdir / name
    reg = _read_registry()
    registered = name in reg.get("plugins", {})
    if dst.exists() and not registered and not force:
        return {"installed": False,
                "reason": f"{name} exists and is not registry-managed; pass force=True"}

    # temp suffix is NOT a loadable extension, so a crash mid-write can never
    # leave a discoverable partial in the plugin dir.
    tmp = pdir / (name + ".incoming")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(dst))   # atomic commit
    except Exception as e:  # noqa: BLE001
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"installed": False, "reason": f"stage failed: {e}"}

    prev = reg["plugins"].get(name, {}).get("version")
    rec = {
        "name": man.get("name") or Path(name).stem,
        "version": man.get("version") or "",
        "api_version": man.get("api_version"),
        "min_api": man.get("min_api"),
        "max_api": man.get("max_api"),
        "source": source_label,
        "installed_at": _now_iso(),
    }
    if prev is not None:
        rec["previous_version"] = prev
    reg["plugins"][name] = rec
    _write_registry(reg)
    return {"installed": True, "name": rec["name"],
            "version": rec["version"], "file": name}


def uninstall_plugin(file: str, *, ack: bool = False) -> dict:
    """Remove a managed-installed plugin: delete the staged file AND its registry
    record. Destructive -> requires ``ack`` (the GUI routes it through a Tier-A
    confirm). Refuses:

      * anything that is not a bare filename inside the managed plugin dir
        (path-escape -- separators, parent refs, absolute paths),
      * an un-acked call,
      * a file that is NOT registry-managed (a hand-dropped file is the
        operator's, surfaced via ``discovered``; we never delete it).

    Tolerates an already-absent file (registry cleanup still proceeds so a
    half-state can't wedge). Value-free result: ``{uninstalled, file, reason?}``.
    """
    name = str(file or "").strip()
    if not name:
        return {"uninstalled": False, "reason": "no file given"}
    # Bare filename only -- reject path separators / parent refs / abs paths.
    if (name in (".", "..") or "/" in name or "\\" in name
            or name != Path(name).name):
        return {"uninstalled": False, "reason": "invalid plugin filename"}
    if not ack:
        return {"uninstalled": False, "reason": "uninstall not acknowledged"}
    pdir = _plugin_dir()
    dst = pdir / name
    # Defense-in-depth: the resolved target must live directly in the managed dir.
    try:
        if dst.resolve().parent != pdir.resolve():
            return {"uninstalled": False, "reason": "outside managed plugin dir"}
    except OSError:
        return {"uninstalled": False, "reason": "bad path"}
    reg = _read_registry()
    if name not in reg.get("plugins", {}):
        return {"uninstalled": False, "reason": "not a registry-managed plugin"}
    try:
        if dst.exists():
            dst.unlink()
    except OSError as e:
        return {"uninstalled": False, "reason": f"remove failed: {e}"}
    reg["plugins"].pop(name, None)
    _write_registry(reg)
    return {"uninstalled": True, "file": name}


def _normalize_config_schema(schema) -> list:
    """O1: turn a JSON-Schema object into a render-ready form model.

    Input is the optional ``config_schema`` manifest field -- a JSON-Schema
    ``{type:"object", properties:{...}, required:[...]}``. Returns a flat list of
    fields::

        {name, type, label, default, required, enum, help}

    where ``type`` is a UI control: ``select`` (string + enum), ``checkbox``
    (boolean), ``number`` (integer/number), else ``text``. A malformed/empty
    schema yields ``[]`` (a schema-less plugin contributes no form)."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    out = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        jtype = spec.get("type", "string")
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            ctrl = "select"
        elif jtype == "boolean":
            ctrl = "checkbox"
        elif jtype in ("integer", "number"):
            ctrl = "number"
        else:
            ctrl = "text"
        out.append({
            "name": name,
            "type": ctrl,
            "label": spec.get("title") or name,
            "default": spec.get("default"),
            "required": name in required,
            "enum": list(enum) if isinstance(enum, list) else [],
            "help": spec.get("description", ""),
        })
    return out


def plugin_config_schemas() -> dict:
    """O1: ``{plugin_filename: [form-field, ...]}`` for every loaded plugin that
    declares a ``config_schema`` manifest field. Consumed by /api/plugins/config
    so the GUI can auto-render a config form per plugin (no per-plugin UI code)."""
    out = {}
    for fname, man in _manifests.items():
        if not isinstance(man, dict):
            continue
        fields = _normalize_config_schema(man.get("config_schema"))
        if fields:
            out[fname] = fields
    return out


def kv(namespace: str):
    """O4: return a namespaced shared KV store handle for plugin state.

    Plugins call ``plugins.kv(my_name)`` to persist small state across runs
    (cursors, seen-set digests, last-poll timestamps) without inventing a file
    format. Backend is SQLite by default, optionally the datastores-kit Postgres
    when an explicit DSN is passed through (no env var). See
    :mod:`bulk_downloader.plugin_kv`.
    """
    from . import plugin_kv as _kv
    return _kv.for_namespace(namespace)


def plugin_metrics() -> list:
    """O2: per-plugin invocation metrics snapshot, busiest first.

    Each entry: ``{key, calls, fails, total_s, avg_ms, last_ms, p50_ms,
    p95_ms, quarantined}`` where ``key`` is the quarantine-style
    ``<kind>:<module>.<qualname>`` handle. p50/p95 are nearest-rank over a
    bounded ring of the most recent durations (V3-E residual, 776);
    ``quarantined`` joins the _quarantine map on the same key (both are
    written at the _call_guarded seam). Read-only; safe to call any time
    (consumed by /api/plugins/status + the cockpit panel).
    """
    out = []
    for key, m in _metrics.items():
        calls = int(m.get("calls", 0))
        total_s = float(m.get("total_s", 0.0))
        samples = sorted(m.get("samples") or ())
        n = len(samples)
        p50 = samples[int(0.5 * (n - 1))] if n else 0.0
        p95 = samples[int(0.95 * (n - 1))] if n else 0.0
        out.append({
            "key": key,
            "calls": calls,
            "fails": int(m.get("fails", 0)),
            "total_s": round(total_s, 6),
            "avg_ms": round((total_s / calls) * 1000.0, 3) if calls else 0.0,
            "last_ms": round(float(m.get("last_s", 0.0)) * 1000.0, 3),
            "p50_ms": round(p50 * 1000.0, 3),
            "p95_ms": round(p95 * 1000.0, 3),
            "quarantined": bool(_quarantine.get(key, {}).get("quarantined")),
        })
    out.sort(key=lambda e: e["calls"], reverse=True)
    return out


def status() -> dict:
    """Diagnostic snapshot of plugin state (consumed by /api/plugins/status)."""
    return {
        "api_version": PLUGIN_API_VERSION,
        "plugin_dir": str(_plugin_dir()),
        "plugin_dir_exists": _plugin_dir().is_dir(),
        "full_access_enabled": _full_access_enabled,
        # V3-A grant-UI (775): the FE derives gated badges + grant toggles from
        # these instead of hand-mirroring _GATED_CAPS (derive, don't mirror).
        "gated_capabilities": sorted(_GATED_CAPS),
        "granted_capabilities": sorted(_granted_capabilities),
        # W6 (778): the operator-forced isolation list ("*" = all .py files).
        "force_isolated": sorted(_force_isolated),
        "extractors": list_extractors(),
        "hooks": list_hooks(),
        "processors": list_processors(),
        "config_providers": list_config_providers(),
        "lifecycle": list_lifecycle(),
        "recognizers": list_recognizers(),
        "prefilters": list_prefilters(),
        "namers": list_namers(),
        "sinks": list_sinks(),
        "dead_letter": list_dead_letter(),
        "sources": list_sources(),
        "enrichers": list_enrichers(),
        "manifests": dict(_manifests),
        "quarantine": list_quarantine(),
        "metrics": plugin_metrics(),
        "loaded": list(_loaded),
        "registry": installed_registry(),
        "disclaimer": DISCLAIMER if _full_access_enabled else "",
    }


def reset() -> None:
    """Clear all registrations + state. Used by tests + reload."""
    # V3-C (777): fire queued on_disable teardown BEFORE clearing -- an
    # unload/reload gives a declaring plugin its teardown. Drained first so
    # a hook that itself calls reset() cannot loop; a crashing hook never
    # blocks reset.
    hooks = list(_disable_hooks)
    _disable_hooks.clear()
    for _name, _fn in hooks:
        try:
            _fn()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugins] on_disable failed for {_name}: {e}\n")
    _extractors.clear()
    _hooks.clear()
    _processors.clear()
    _config_providers.clear()
    _lifecycle.clear()
    _recognizers.clear()
    _prefilters.clear()
    _namers.clear()
    _sinks.clear()
    _dead_letter.clear()
    _sources.clear()
    _source_state.clear()
    _enrichers.clear()
    _healthchecks.clear()
    _manifests.clear()
    _loaded.clear()
    _quarantine.clear()
    _metrics.clear()
    set_full_access(False)
    set_granted_capabilities(())


# ── Known events (documentation) ──────────────────────────────────────
HOOK_EVENTS = {
    "download.done": "Successful download completed. "
                     "Payload: {site_id, url, filename, path, file_size, ts}",
    "download.failed": "Download failed. Payload: {site_id, url, message, ts}",
    "download.needs_review": "Download blocked (captcha, login, etc.). "
                             "Payload: {site_id, url, message, ts}",
    "site.cooldown": "Site put into cooldown (rate limit / errors). "
                     "Payload: {site_id, reason, duration_seconds}",
    "site.recovered": "Site exited cooldown. Payload: {site_id}",
    "cookies.refreshed": "Cookies refreshed for a site. Payload: {site_id, count}",
    "queue.idle": "All workers idle for some duration. Payload: {site_id, idle_seconds}",
    "plugin.quarantined": "A plugin crossed the fail budget and was quarantined. "
                          "Payload: {key, fails, last_error}",
    "plugin.recovered": "A quarantined plugin healed on a cooldown re-probe. "
                        "Payload: {key}",
    "template.reviewed": "A draft was promoted to a reviewed template. "
                         "Payload: {host, filename, enabled, ts}",
    "template.promoted": "A reviewed template was enabled (taken live). "
                         "Payload: {host, filename, ts}",
    "queue.enqueued": "URLs were added to a site queue. "
                      "Payload: {site_id, added, dupes, skipped, ts}",
    "queue.drained": "A site queue drained to empty (all jobs terminal). "
                     "Payload: {site_id, done, failed, review, ts}",
    "vpn.tunnel_up": "A VPN tunnel came up. "
                     "Payload: {tunnel_id, socks_port, ts}",
    "vpn.tunnel_down": "A VPN tunnel went down. Payload: {tunnel_id, ts}",
    "vpn.killswitch_armed": "The kill switch armed for a tunnel (leak/kill). "
                            "Payload: {tunnel_id, reason, ts}",
    "review.approved": "needs_review jobs were approved to download. "
                       "Payload: {site_id, count, ts}",
    "review.skipped": "needs_review jobs were dismissed without downloading. "
                      "Payload: {site_id, count, ts}",
    "download.progress": "A download advanced (byte progress). "
                         "Payload: {site_id, url, file_size, ts}",
    "download.retry": "A download was requeued for retry. "
                      "Payload: {site_id, url, retries, message, ts}",
    "capture.started": "A session capture run started (the universal capture "
                       "entry tools/capture_session.py:run). "
                       "Payload: {url, ts}",
    "capture.done": "A session capture run finished and the WACZ artifact was "
                    "written. Payload: {url, network_count, ts}",
}

PROCESSOR_PAYLOAD = ("Post-download processors receive the same payload as "
                     "download.done plus a best-effort absolute `path`.")

LIFECYCLE_EVENT_DOCS = {
    "before_launch": "GATED. fn(launch_kwargs: dict, site_id). Mutate launch "
                     "kwargs in place before open_persistent_context.",
    "after_context": "GATED. fn(context, page, site_id). Live Playwright "
                     "context+page; add init scripts, cookies, routes.",
    "before_capture": "GATED. fn(context, page, site_id). Before a capture run.",
    "after_capture": "GATED. fn(artifact: dict, site_id). After a capture; "
                     "read/annotate the artifact.",
}


def known_events() -> dict:
    """Documentation: events BD fires + payloads, processors, lifecycle, caps."""
    return {
        "hooks": dict(HOOK_EVENTS),
        "processors": PROCESSOR_PAYLOAD,
        "lifecycle": dict(LIFECYCLE_EVENT_DOCS),
        "capabilities": [CAP_EXTRACTOR, CAP_HOOK, CAP_PROCESSOR, CAP_CONFIG,
                         CAP_LIFECYCLE, CAP_PAGE_ACCESS, CAP_RECOGNIZER,
                         CAP_PREFILTER, CAP_NAMER, CAP_SINK,
                         CAP_SOURCE, CAP_ENRICHER, CAP_HEALTHCHECK],
        "api_version": PLUGIN_API_VERSION,
        "api_min": PLUGIN_API_MIN,
        "api_max": PLUGIN_API_MAX,
        "payload_schema_version": PLUGIN_PAYLOAD_SCHEMA,
        "full_access_enabled": _full_access_enabled,
    }
