"""NEXT-2 — wowgirls re-promote runtime-shape golden + safety invariants.

Background: ``reviewed/auth.wowgirls.com.template.json`` once reached the on-stash
reviewed/ dir in RAW WACZ-builder shape (``resolution_priority`` +
``network_discovery``, NO top-level ``resolutions``), bypassing the normalizer; a
one-liner mapped a top-level ``resolutions`` in place to clear
``validate_templates`` but left the file raw. The proper fix is to re-promote it
through the documented pipeline (normalize_template_draft -> promote_template),
which the operator runs on stash.

This test lands the runtime-shape GOLD the re-promote is validated against and
pins the safety posture the re-promote depends on, all from the real pipeline run
over the §9 ``wow`` corpus fixture:

  build_template_from_wacz.build_template  ->  template_normalize.normalize_draft

  drift     — the deterministic runtime-shape projection matches the committed
              golden (``tools/wow_promote_golden.py --check`` is the operator's
              on-stash equivalent). Regen on an intentional normalizer/recognizer
              change: ``python3 tools/wow_promote_golden.py --write``.
  gate      — the normalized candidate clears ``promote_gate_errors`` (the SAME
              shared readiness/safety gate the CLI + Workbench promote call). This
              is the headline NEXT-2 fix: a clean re-promote now passes the gate
              the raw template would have failed.
  ladder    — the candidate carries a NON-EMPTY top-level ``resolutions`` ladder
              (the exact field the raw on-stash template lacked).
  f2        — no signed/secret value survives into the candidate
              (``scan_artifact_secrets`` clean).
  never-en  — the normalizer never emits an ``enabled`` candidate; promotion stays
              an explicit, separate, operator step.

Browser-free; stdlib + project modules; zero-arg test functions (custom-runner
safe). The golden carries the template SHAPE only — never a signed value.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wow_promote_golden as G                                  # type: ignore
from bulk_downloader.template_manager import promote_gate_errors
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets

_GOLDEN = _ROOT / "tests" / "golden" / "wow_promote.golden.json"


def test_golden_present():
    assert _GOLDEN.is_file(), (
        "wow_promote golden missing — run `python3 tools/wow_promote_golden.py --write`")


def test_runtime_shape_matches_golden():
    """The normalized wow candidate's deterministic projection == committed gold."""
    ok, diff = G.check_golden()
    assert ok, (
        "wow re-promote runtime-shape DRIFT (normalizer/recognizer changed the "
        "candidate). If intended, regen with "
        "`python3 tools/wow_promote_golden.py --write` and recommit:\n"
        + "\n".join(diff[:80]))


def test_candidate_clears_the_promote_gate():
    """A clean re-promote passes the shared readiness/safety gate (headline fix)."""
    cand = G.build_candidate()
    errs = promote_gate_errors(cand)
    assert errs == [], f"re-promote would be refused by the gate: {errs}"


def test_candidate_has_resolution_ladder():
    """The exact field the raw on-stash template lacked is present + non-empty."""
    cand = G.build_candidate()
    res = cand.get("resolutions")
    assert isinstance(res, list) and res, f"missing top-level resolutions ladder: {res!r}"
    assert all(isinstance(r, int) for r in res), res


def test_candidate_is_f2_clean():
    """No signed/secret value survives into the runtime candidate."""
    cand = G.build_candidate()
    findings = scan_artifact_secrets(cand)
    assert findings == [], f"secret leak in re-promote candidate: {findings}"


def test_candidate_is_never_enabled():
    """The normalizer never emits an enabled candidate (promotion stays manual)."""
    cand = G.build_candidate()
    assert cand.get("status") in ("review_ready", "draft_review_required"), cand.get("status")
    assert cand.get("status") != "enabled"


def test_golden_carries_no_signed_values():
    """Belt-and-braces: the committed gold itself has no obvious signed material."""
    blob = json.dumps(json.loads(_GOLDEN.read_text(encoding="utf-8")))
    for needle in ("?expires=", "&Signature=", "token=", "X-Amz-Signature", "sig="):
        assert needle not in blob, f"signed-material needle in gold: {needle}"
