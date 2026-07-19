"""Cut 668 (v3.66.668) -- the redaction/SSRF critical-predicate mutation gate.

Asserts the live security predicates are BOTH toothed and load-bearing:
  * every registered predicate rejects its known-bad input (teeth), and
  * every predicate with a downstream probe is load-bearing (mutating it to the
    insecure constant makes the probe leak -> mutant killed).

Plus a self-teeth test: the gate must itself DETECT a survivor (a predicate
whose mutation changes nothing) -- otherwise the gate is decorative.

Tools-only; stdlib; no network/app boot.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for p in (str(_REPO), str(_REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import redaction_mutation_gate as G  # noqa: E402


def test_live_predicates_pass_the_gate():
    r = G.run_gate()
    assert r["ok"], (r["survivors"], r["teeth_failures"], r["results"])
    assert not r["survivors"], r["survivors"]
    assert not r["teeth_failures"], r["teeth_failures"]


def test_registry_covers_redaction_and_ssrf():
    names = {m.name for m in G.CRITICAL}
    assert "redaction_kv_secret" in names
    assert "ssrf_safe_public_host" in names


def test_redaction_predicate_is_load_bearing():
    # The kv-secret entry must be KILLED (mutating it leaks the sentinel).
    r = G.run_gate()
    kv = [x for x in r["results"] if x["name"] == "redaction_kv_secret"][0]
    assert kv["teeth_ok"] and kv["mutant_killed"] is True, kv


def test_gate_detects_a_surviving_mutant():
    # Self-teeth: a predicate whose mutation does NOT change the probe must be
    # reported as a survivor (this is what makes the gate meaningful).
    inert = G.Mutation(
        name="inert_dead_predicate",
        module="bulk_downloader.capture_artifact_redact",
        attr="_kv_key_is_secret",
        bad_input="password",
        rejects=lambda r: r is True,
        mutant=lambda k: k == "password",   # still flags 'password' -> probe unchanged
        probe=G._redaction_probe,
        leaked=lambda out: "SENTINEL_MUT_668" in out,
    )
    r = G.run_gate([inert])
    assert "inert_dead_predicate" in r["survivors"], r
    assert r["ok"] is False


def test_gate_flags_a_toothless_predicate():
    # A predicate whose rejects() never holds must fail the teeth check.
    bad = G.Mutation(
        name="never_rejects", module="bulk_downloader.capture_artifact_redact",
        attr="_kv_key_is_secret", bad_input="password",
        rejects=lambda r: False,  # pretend nothing counts as a rejection
        mutant=lambda k: False, probe=None, leaked=None,
    )
    r = G.run_gate([bad])
    assert "never_rejects" in r["teeth_failures"], r
