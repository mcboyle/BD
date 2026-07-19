"""redaction_mutation_gate -- standing critical-predicate load-bearing gate.

Cut 668 (v3.66.668). A dependency-free, targeted realization of the Phase-12.2
"mutation gate on critical paths" intent -- NOT a mutmut integration (no new
dep, no slow per-mutant full-suite run). For each security-critical predicate it
checks two things:

  * TEETH        -- the predicate itself rejects a known-bad input (it is not a
                    stub that accepts everything).
  * LOAD-BEARING -- when the predicate is mutated to its INSECURE constant, a
                    downstream enforcement probe REGRESSES (the secret leaks /
                    the unsafe host is allowed). A mutation that changes nothing
                    means the protection path does not actually depend on the
                    predicate (dead code / bypassed / toothless test) -> the
                    mutant SURVIVED and the gate fails.

Targets: the redaction SoT key-secret predicate (`_kv_key_is_secret`, via the
`redact_value` scrub path) and the SSRF host guard (`_is_safe_public_host`).
Extend by appending to CRITICAL. Stdlib only; each mutation is restored in a
finally so the gate never leaves a predicate weakened.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, List, Optional


class Mutation:
    """One critical-predicate entry.

    module/attr    -- the predicate to mutate (module dotted path + attribute).
    bad_input      -- an input the intact predicate MUST reject.
    rejects        -- (result) -> bool: True iff `result` is a rejection.
    mutant         -- the insecure replacement callable.
    probe          -- () -> Any downstream enforcement result (or None if teeth-only).
    leaked         -- (probe_result) -> bool: True iff the probe shows the
                      vulnerability (secret present / host allowed). Only the
                      DELTA matters: intact must be not-leaked, mutant must be
                      leaked, else the mutant survived.
    """

    def __init__(self, name: str, module: str, attr: str, bad_input: Any,
                 rejects: Callable[[Any], bool], mutant: Callable,
                 probe: Optional[Callable[[], Any]] = None,
                 leaked: Optional[Callable[[Any], bool]] = None):
        self.name = name
        self.module = module
        self.attr = attr
        self.bad_input = bad_input
        self.rejects = rejects
        self.mutant = mutant
        self.probe = probe
        self.leaked = leaked


def _redaction_probe() -> str:
    # Dynamic import (importlib) keeps this tool off the static import graph --
    # it is a dev-facing gate, not part of the package's module dependency web.
    rv = importlib.import_module("bulk_downloader.capture_artifact_redact")
    return rv.redact_value("password=SENTINEL_MUT_668")


CRITICAL: List[Mutation] = [
    Mutation(
        name="redaction_kv_secret",
        module="bulk_downloader.capture_artifact_redact",
        attr="_kv_key_is_secret",
        bad_input="password",
        rejects=lambda r: r is True,          # predicate flags 'password' secret
        mutant=lambda k: False,               # insecure: "nothing is a secret"
        probe=_redaction_probe,
        leaked=lambda out: "SENTINEL_MUT_668" in out,  # secret survived the scrub
    ),
    Mutation(
        name="ssrf_safe_public_host",
        module="bulk_downloader.provider_resolve_impl._common",
        attr="_is_safe_public_host",
        bad_input="169.254.169.254",          # link-local (cloud metadata)
        rejects=lambda r: r[0] is False,      # (ok, reason) -> ok must be False
        mutant=lambda host: (True, ""),       # insecure: "every host is safe"
        probe=None,                            # enforcement needs request context: teeth-only
        leaked=None,
    ),
]


def _check_one(m: Mutation) -> dict:
    """Return {name, teeth_ok, mutant_killed, note}. mutant_killed is None when
    the entry is teeth-only (no downstream probe)."""
    mod = importlib.import_module(m.module)
    original = getattr(mod, m.attr)
    # TEETH: intact predicate rejects the bad input.
    try:
        teeth_ok = bool(m.rejects(original(m.bad_input)))
    except Exception as e:
        return {"name": m.name, "teeth_ok": False, "mutant_killed": None,
                "note": f"teeth probe raised: {e}"}
    if m.probe is None:
        return {"name": m.name, "teeth_ok": teeth_ok, "mutant_killed": None,
                "note": "teeth-only (no downstream probe)"}
    # LOAD-BEARING: intact probe must be secure; mutated probe must leak.
    try:
        intact_secure = not m.leaked(m.probe())
    except Exception as e:
        return {"name": m.name, "teeth_ok": teeth_ok, "mutant_killed": False,
                "note": f"intact probe raised: {e}"}
    try:
        setattr(mod, m.attr, m.mutant)
        mutant_leaks = bool(m.leaked(m.probe()))
    finally:
        setattr(mod, m.attr, original)
    killed = intact_secure and mutant_leaks
    return {"name": m.name, "teeth_ok": teeth_ok, "mutant_killed": killed,
            "note": "" if killed else
                    "mutant SURVIVED: mutating the predicate did not regress the "
                    "probe (dead code / bypassed / toothless)"}


def run_gate(registry: Optional[List[Mutation]] = None) -> dict:
    """Run the gate. Returns {ok, results, survivors, teeth_failures}.
    ok is True iff every predicate has teeth and every downstream mutation was
    killed."""
    reg = CRITICAL if registry is None else registry
    results = [_check_one(m) for m in reg]
    survivors = [r["name"] for r in results if r["mutant_killed"] is False]
    teeth_failures = [r["name"] for r in results if not r["teeth_ok"]]
    return {"ok": not survivors and not teeth_failures, "results": results,
            "survivors": survivors, "teeth_failures": teeth_failures}


def main(argv=None) -> int:
    r = run_gate()
    for res in r["results"]:
        mk = res["mutant_killed"]
        tag = "TEETH-ONLY" if mk is None else ("KILLED" if mk else "SURVIVED")
        teeth = "teeth" if res["teeth_ok"] else "NO-TEETH"
        print(f"  [{tag:10}] [{teeth:8}] {res['name']}  {res['note']}".rstrip())
    if r["ok"]:
        print(f"redaction_mutation_gate: PASS ({len(r['results'])} predicate(s))")
        return 0
    print(f"redaction_mutation_gate: FAIL "
          f"survivors={r['survivors']} teeth_failures={r['teeth_failures']}")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
