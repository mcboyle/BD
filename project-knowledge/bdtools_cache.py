#!/usr/bin/env python3
"""bdtools_cache -- the shared, content-addressed analysis cache (Plan A3).

A shared library (like bdtools_sec / bdtools_taint), NOT a tool.

WHY IT EXISTS -- measured, not assumed. The capability doc justified this by
claiming the taint tools "SLOW-PASS at 6s"; they actually run in ~3s, and the
decomp lenses run in ~0.1s. The real cost is elsewhere:

    bd-defect-scan   17.9s   <-- and bd-ratchet calls it for defect_DP_total,
                                 INSIDE bd-precut --gate, on EVERY cut.
    bd-evidence       7.5s
    bd-secret-taint   3.0s
    bd-decomp         0.55s  <-- needs no cache at all

So the cache targets whole-tree AST re-parsing, which is redundant across runs:
between two cuts, almost every file is byte-identical.

CORRECTNESS FIRST. A stale cache that serves a wrong answer to a GATE is far worse
than a slow gate -- it would let a real regression through. Two rules:

  1. KEY ON CONTENT, NEVER ON mtime. mtime lies: this session's `cp -a` tree restore
     preserved mtimes exactly, so an mtime-keyed cache would have served findings
     for the OLD file content. Every entry is keyed by sha256 of the file bytes.

  2. KEY ON THE ANALYZER TOO. If the analyzer's own logic changes, every cached
     finding it produced is invalid. Each entry is therefore keyed by
     (file_sha256, logic_key), where logic_key is a hash of the analyzer source.
     Change the rules -> the cache misses -> it recomputes. It cannot serve a
     finding produced by code that no longer exists.

Anything the cache is unsure about, it recomputes. A cache miss costs time; a bad
cache hit costs a release.
"""
import hashlib
import json
import os

CACHE_DIR = os.environ.get("BDTOOLS_CACHE_DIR",
                           os.path.expanduser("~/.bd_analysis_cache"))
SCHEMA = 2


def file_sha(path):
    """sha256 of the file's BYTES. Not mtime -- mtime lies (see module docstring)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except Exception:  # why: input unavailable/unreadable; return empty so the caller uses its fallback, not a wrong value
        return None
    return h.hexdigest()


def logic_key(*sources):
    """A hash of the ANALYZER's own source. If the rules change, every entry the
    old rules produced must miss. Pass __file__ of the analyzer (and any rule
    module it depends on)."""
    h = hashlib.sha256()
    for s in sources:
        try:
            with open(s, "rb") as fh:
                h.update(fh.read())
        except Exception:  # why: file unreadable; hash its path string instead so the key still varies per input
            h.update(str(s).encode())
    return h.hexdigest()[:16]


class Cache(object):
    """Per-namespace, content-addressed memo of per-file analysis results.

    cache = Cache("defect-scan", logic_key(__file__))
    findings = cache.get_or_compute(path, lambda: analyze(path))
    cache.save()
    """

    def __init__(self, namespace, logic, enabled=True):
        self.ns = namespace
        self.logic = logic
        self.enabled = enabled and os.environ.get("BDTOOLS_CACHE", "1") != "0"
        self.path = os.path.join(CACHE_DIR, "%s.json" % namespace)
        self.hits = 0
        self.misses = 0
        self._data = {}
        if self.enabled:
            try:
                d = json.load(open(self.path))
                # a schema OR logic change invalidates the whole namespace
                if d.get("schema") == SCHEMA and d.get("logic") == self.logic:
                    self._data = d.get("entries", {})
            # why: read failed; degrade to the default and continue -- affects completeness, not correctness
            except Exception:
                self._data = {}

    def get_or_compute(self, path, compute):
        """Return the cached result for THIS EXACT file content, else compute it."""
        if not self.enabled:
            return compute()
        sha = file_sha(path)
        if sha is None:
            return compute()
        hit = self._data.get(sha)
        if hit is not None:
            self.hits += 1
            return hit
        self.misses += 1
        val = compute()
        try:
            json.dumps(val)          # only cache what round-trips honestly
            self._data[sha] = val
        except Exception:  # why: handled failure is non-fatal here; proceed with the value left unset
            pass
        return val

    def save(self):
        if not self.enabled:
            return
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"schema": SCHEMA, "logic": self.logic,
                           "entries": self._data}, fh)
            os.replace(tmp, self.path)   # atomic: never a half-written cache
        except Exception:  # why: handled failure is non-fatal here; proceed with the value left unset
            pass

    def stats(self):
        return {"namespace": self.ns, "hits": self.hits, "misses": self.misses,
                "entries": len(self._data), "enabled": self.enabled}


def selftest():
    """Negative controls first: the ways a cache can LIE."""
    import tempfile
    import shutil
    import time as _t
    ok = True
    d = tempfile.mkdtemp(prefix="bdcache_")
    global CACHE_DIR
    CACHE_DIR = os.path.join(d, "cache")
    f = os.path.join(d, "sample.py")
    open(f, "w").write("x = 1\n")

    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"v": open(f).read().strip()}

    c1 = Cache("selftest", "L1")
    a = c1.get_or_compute(f, compute)
    c1.save()
    c2 = Cache("selftest", "L1")
    b = c2.get_or_compute(f, compute)
    print(("PASS" if (a == b and calls["n"] == 1 and c2.hits == 1) else "FAIL") +
          "  a second run on identical content HITS (computed %d time)" % calls["n"])
    ok = ok and a == b and calls["n"] == 1

    # NEG 1: content changes but mtime is FORCED BACK (the `cp -a` trap that
    # actually happened this session). An mtime-keyed cache would serve stale.
    st = os.stat(f)
    open(f, "w").write("x = 2\n")
    os.utime(f, (st.st_atime, st.st_mtime))     # mtime restored -> mtime says "unchanged"
    c3 = Cache("selftest", "L1")
    v = c3.get_or_compute(f, compute)
    fresh = (v == {"v": "x = 2"} and c3.misses == 1)
    print(("PASS" if fresh else "FAIL") +
          "  NEG: content changed but mtime FAKED -> still recomputes (mtime lies)")
    ok = ok and fresh

    # NEG 2: the ANALYZER's logic changes -> every old entry must miss
    c3.save()
    c4 = Cache("selftest", "L2-different-rules")
    before = calls["n"]
    c4.get_or_compute(f, compute)
    invalidated = (calls["n"] == before + 1 and c4.hits == 0)
    print(("PASS" if invalidated else "FAIL") +
          "  NEG: analyzer logic changed -> cache MISSES (cannot serve dead rules)")
    ok = ok and invalidated

    # NEG 3: disabled cache must never hit
    c5 = Cache("selftest", "L1", enabled=False)
    before = calls["n"]
    c5.get_or_compute(f, compute)
    print(("PASS" if calls["n"] == before + 1 and c5.hits == 0 else "FAIL") +
          "  NEG: disabled cache always recomputes (BDTOOLS_CACHE=0 escape hatch)")
    ok = ok and c5.hits == 0

    shutil.rmtree(d, ignore_errors=True)
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
