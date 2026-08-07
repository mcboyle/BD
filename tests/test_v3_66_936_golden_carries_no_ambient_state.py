"""v3.66.936 -- the "synthetic only" capture golden embedded live state.

THE DEFECT. `tools/capture_model_golden.py` fabricates a capture in-process and
pins the three readers' derived output against a committed golden; its own
docstring says "Synthetic only; browser-free". But `_proj_workflow` kept two
whole blocks verbatim, and EIGHT of the projection's 192 leaves are not a
function of the fixture at all:

  capture_health.arm_fail_streak      dom_recorder._ARM_FAIL_STREAK   (process)
  capture_health.dom_events_dropped   dom_recorder._DOM_DROPPED_TOTAL (process)
  capture_health.rrweb_present        a vendored file's existence     (fs)
  redaction_profile.emails            BD_REDACT_EMAILS                (env)
  redaction_profile.network_signed_urls   BD_REDACT_NETWORK_URLS      (env)
  redaction_profile.dom_embedded_urls     BD_REDACT_DOM_URLS          (env)
  redaction_profile.custom_sensitive_headers[0]  BD_REDACT_EXTRA_HEADERS
  redaction_profile.reduced_redaction     second-order on the three above

MEASURED on the box 2026-08-07 (capture at 48707ad, v3.66.932). The golden
failed with exactly:

    -      "arm_fail_streak": 0,
    +      "arm_fail_streak": 3,

`tests/test_v3_66_165_dom_drain.py` ends with three failing arm probes and no
teardown, leaving the streak at 3; `--dist loadfile` decides whether that file
and this one share a worker, so whether it fires is a property of the FILE LIST
rather than of the code under test.

THE HALF THE CAPTURE DID NOT SHOW IS THE MORE DANGEROUS ONE.
`bulk_downloader/__init__.py` calls `_envfile.load_envfile()` at IMPORT, which
applies every KEY=VALUE from `$BD_ENVFILE`, else `cwd/.env`, else
`$HOME/BulkDownloader/.env` into os.environ with no allow-list. `BD_REDACT_*`
passes straight through, and on the box `$HOME/BulkDownloader` IS the install
directory. `isolated_bd_home` chdirs per test, which closes the cwd half and
CANNOT close the HOME half. Measured here, with cwd deliberately elsewhere:

    control (clean):                            OK
    env BD_REDACT_EMAILS=keep:                  DRIFT (emails, reduced_redaction)
    $HOME/BulkDownloader/.env, cwd elsewhere:   DRIFT (the same two)

That file is the GUI env-editor's persistence target, so it is operator-
writable: dialling a redaction grey in the UI would fail this gate.

WHERE THE FIX GOES, AND WHY NOT ANYWHERE ELSE.
  - NOT `wacz_export._capture_health`: tests/test_v3_66_171_redaction_profile.py
    asserts `"dom_events_dropped" in h and "arm_fail_streak" in h` on a real
    WACZ. Persisting the counters into capture.json is CORRECT product
    behaviour; treating them as reader behaviour in a characterization golden
    is what was wrong.
  - NOT `dom_recorder.py`, `tools/capture_session.py` or
    `tools/build_release.py`: all three are SHA-pinned guard files, and
    build_release.py is the caller that runs this gate as a subprocess with
    cwd=root and inherited env -- both ambient channels live -- so sanitising
    at the call site is not available either.
  - So: an explicit allow-list in `_proj_workflow`, which every caller shares.

OVER-CORRECTION IS THE OTHER HALF. Dropping `capture_health` wholesale would
pass every assertion about ambient state and quietly retire a real gate: six of
its nine keys are genuine derived behaviour, and a mutation probe of
`_capture_health`'s derivation logic showed 4 of 5 mutants CAUGHT by the golden.
The last three tests exist to make that failure mode visible.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capture_model_golden as G                     # noqa: E402
from bulk_downloader import dom_recorder as _dr      # noqa: E402

# Not a function of the synthetic fixture.
_AMBIENT = ("arm_fail_streak", "dom_events_dropped", "rrweb_present",
            "emails", "network_signed_urls", "dom_embedded_urls",
            "custom_sensitive_headers", "reduced_redaction")

# Genuine derived behaviour of the readers on the fixture. Named explicitly so
# a fix that drops capture_health wholesale FAILS instead of passing.
_DERIVED_HEALTH = ("dom_log_len", "dom_log_count", "dom_full_snapshots",
                   "network_log_len", "network_error_count",
                   "dom_integrity_ok")


@pytest.fixture
def dirty_recorder():
    """Leave the recorder's process globals exactly as a prior test would."""
    saved = (_dr._ARM_FAIL_STREAK, _dr._DOM_DROPPED_TOTAL)
    _dr._ARM_FAIL_STREAK = 3          # the value the box actually carried
    _dr._DOM_DROPPED_TOTAL = 7
    try:
        yield
    finally:
        _dr._ARM_FAIL_STREAK, _dr._DOM_DROPPED_TOTAL = saved


def _golden_in_subprocess(env_overrides=None, cwd=None, home=None):
    """Run check_golden() in a FRESH interpreter.

    A subprocess, not monkeypatch, because the `.env` seed happens at IMPORT of
    bulk_downloader and `os.environ.setdefault` fires once per process -- so an
    in-process env change cannot reach it, and a test that tried would measure
    the wrong thing while looking rigorous.

    The parent's BD_REDACT_* are POPPED rather than merely left unset: this
    harness varies those variables, so the parent's values are part of its
    denominator (CLAUDE.md section 0).
    """
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("BD_REDACT_") or k == "BD_ENVFILE":
            env.pop(k)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    if home:
        env["HOME"] = home
    env.update(env_overrides or {})
    probe = (
        f"import sys; sys.path[:0]=[{str(_REPO)!r}, {str(_REPO / 'tools')!r}]\n"
        "import capture_model_golden as G\n"
        "ok, diff = G.check_golden()\n"
        "print('OK' if ok else 'DRIFT')\n"
        "print('\\n'.join(diff[:40]))\n"
    )
    p = subprocess.run([sys.executable, "-c", probe], env=env,
                       cwd=str(cwd or _REPO), capture_output=True, text=True,
                       timeout=300)
    assert p.returncode == 0, f"probe crashed:\n{p.stderr[-2000:]}"
    return p.stdout


# ── process globals: the box's failure ───────────────────────────────────────

def test_the_projection_is_invariant_under_recorder_globals(dirty_recorder):
    dirty = G.build_projection()
    saved = (_dr._ARM_FAIL_STREAK, _dr._DOM_DROPPED_TOTAL)
    _dr._ARM_FAIL_STREAK, _dr._DOM_DROPPED_TOTAL = 0, 0
    try:
        clean = G.build_projection()
    finally:
        _dr._ARM_FAIL_STREAK, _dr._DOM_DROPPED_TOTAL = saved
    assert dirty == clean, (
        "the projection moved when only a dom_recorder process global moved; "
        "a characterization golden over a synthetic fixture must be a "
        "function of the fixture")


def test_check_golden_survives_a_dirty_recorder(dirty_recorder):
    """The box's failure, restated as an assertion."""
    ok, diff = G.check_golden()
    assert ok, ("the committed golden drifts when a prior test in the same "
                "process left the recorder counters dirty:\n"
                + "\n".join(diff[:40]))


def test_the_real_polluter_no_longer_drifts_the_golden():
    """End to end against the actual file, rather than a hand-set global.
    tests/test_v3_66_165_dom_drain.py ends with three failing arm probes and
    no teardown; measured, it leaves _ARM_FAIL_STREAK == 3."""
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q",
         "tests/test_v3_66_165_dom_drain.py",
         "tests/test_capture_model_golden.py"],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, (
        "the drain file still poisons the golden when they share a worker:\n"
        + p.stdout[-3000:])


# ── filesystem ───────────────────────────────────────────────────────────────

def test_a_missing_rrweb_asset_does_not_drift_the_golden(monkeypatch):
    """`rrweb_present` is a vendored file's existence smuggled into a capture
    -model golden. A host that has not vendored the asset would fail this gate
    for an environmental reason -- the same class as the counters, and the one
    nobody would have found from the diff the box produced."""
    monkeypatch.setattr(_dr, "_RRWEB_JS",
                        Path("/nonexistent/rrweb-absent-on-this-host.js"))
    ok, diff = G.check_golden()
    assert ok, ("the golden drifts on a host where the vendored rrweb asset "
                "is absent:\n" + "\n".join(diff[:40]))


# ── environment, and the .env channel no chdir can close ─────────────────────

@pytest.mark.parametrize("var,value", [
    ("BD_REDACT_EMAILS", "keep"),
    ("BD_REDACT_NETWORK_URLS", "keep_full"),
    ("BD_REDACT_DOM_URLS", "strip_all"),
    ("BD_REDACT_EXTRA_HEADERS", "x-foo"),
])
def test_a_redaction_env_var_does_not_drift_the_golden(var, value):
    out = _golden_in_subprocess({var: value})
    assert out.startswith("OK"), f"{var}={value} drifts the golden:\n{out}"


def test_a_home_relative_envfile_does_not_drift_the_golden():
    """THE CHANNEL isolated_bd_home CANNOT CLOSE. bulk_downloader/__init__.py
    seeds os.environ at IMPORT from $HOME/BulkDownloader/.env when no closer
    candidate exists -- and no chdir can move $HOME. On the box that path IS
    the install directory, and it is the GUI env-editor's persistence target.

    cwd is deliberately a temp dir, mirroring what conftest's chdir does, so
    this measures the HOME fallback and not the cwd candidate.
    """
    home = tempfile.mkdtemp()
    (Path(home) / "BulkDownloader").mkdir()
    (Path(home) / "BulkDownloader" / ".env").write_text(
        "BD_REDACT_EMAILS=keep\n", "utf-8")
    elsewhere = tempfile.mkdtemp()
    out = _golden_in_subprocess(cwd=elsewhere, home=home)
    assert out.startswith("OK"), (
        "a .env under $HOME/BulkDownloader drifts the golden; no fixture can "
        f"chdir away from that:\n{out}")


def test_the_harness_can_actually_observe_the_env_channel():
    """Discriminate the harness from the subject. If BD_REDACT_EMAILS did not
    in fact reach the redaction profile, every test above would pass on any
    tree, fixed or not. Assert the mechanism still exists by reading the
    profile directly rather than through the projection."""
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["BD_REDACT_EMAILS"] = "keep"
    probe = (
        f"import sys; sys.path[:0]=[{str(_REPO)!r}]\n"
        "from bulk_downloader import redaction_profile as rp\n"
        "print('EMAILS=%s' % rp.current_profile()['emails'])\n"
    )
    p = subprocess.run([sys.executable, "-c", probe], env=env,
                       cwd=str(_REPO), capture_output=True, text=True,
                       timeout=300)
    assert "EMAILS=keep" in p.stdout, (
        "BD_REDACT_EMAILS no longer reaches the redaction profile, so the "
        "env tests above are vacuous.\n"
        f"stdout={p.stdout!r}\nstderr={p.stderr[-1500:]}")


# ── the committed file ───────────────────────────────────────────────────────

def test_no_ambient_key_survives_anywhere_in_the_committed_golden():
    """Denominator is the whole committed file, not the block we noticed."""
    blob = G.GOLDEN_PATH.read_text("utf-8")
    for key in _AMBIENT:
        assert f'"{key}"' not in blob, (
            f"{key} is still pinned in {G.GOLDEN_PATH.name}; it is process, "
            f"filesystem or environment state, not reader behaviour")


def test_the_golden_on_disk_matches_a_clean_process():
    ok, diff = G.check_golden()
    assert ok, "\n".join(diff[:40])
    assert json.loads(G.GOLDEN_PATH.read_text("utf-8")) == G.build_projection()


# ── over-correction guards ───────────────────────────────────────────────────

def test_the_derived_health_fields_are_still_guarded():
    """A fix that dropped `capture_health` entirely would pass every assertion
    above and silently retire a gate that catches 4 of 5 derivation mutants."""
    health = (G.build_projection()["workflow_diagnostic.load_capture"]
              ["capture_health"])
    missing = [k for k in _DERIVED_HEALTH if k not in health]
    assert not missing, (
        f"the projection stopped guarding derived health fields {missing}; "
        f"the ambient fields were the target, not the block")


def test_the_non_ambient_profile_fields_are_still_guarded():
    prof = (G.build_projection()["workflow_diagnostic.load_capture"]
            ["redaction_profile"])
    for k in ("schema", "forced_floor_scrub"):
        assert k in prof, f"redaction_profile.{k} was dropped with the rest"


def test_keep_preserves_absence_rather_than_inventing_null():
    """`_keep` must not manufacture a key the reader did not emit.

    ESCAPED ITS MUTANT ONCE. Swapping `block[k] for k in names if k in block`
    for `block.get(k) for k in names` is EQUIVALENT on the current fixture --
    measured, every allow-listed name is present in both blocks, so the two
    spellings agree -- and the whole battery stayed green. The behaviour is
    still a real claim: the difference between "the reader stopped emitting
    dom_integrity_ok" and "it emitted null" is exactly the change this golden
    exists to catch, and `.get()` would silently convert the first into the
    second. Asserted directly, because no fixture-driven test can reach it.
    """
    got = G._keep({"kept": 1}, ("kept", "never_emitted"))
    assert got == {"kept": 1}, (
        f"_keep invented a key the block did not have: {got!r} -- a reader "
        f"that stopped emitting a field would be indistinguishable from one "
        f"emitting null")
    assert "never_emitted" not in got
    # and a real null must survive as a null, not be dropped as falsy
    assert G._keep({"nullable": None}, ("nullable",)) == {"nullable": None}


def test_the_derived_fields_still_track_the_fixture():
    """Presence is not guarding. Perturb the fixture and the derived fields
    must move, or they are pinned constants dressed as behaviour.

    BOTH LEVELS, and the reason is a mutant that escaped. There are TWO
    `dom_log_len` fields -- one that `_proj_workflow` computes itself, and one
    that `wacz_export._capture_health` derives -- and on this fixture both are
    2. Pinning the top-level one to the literal `2` left every assertion in
    this file green, because they all read the health block. Assert the level
    the projection owns as well as the one it forwards.
    """
    base = G.build_projection()["workflow_diagnostic.load_capture"]
    real_fixed = G.fixed_capture

    def _one_more_event():
        cap = copy.deepcopy(real_fixed())
        cap.setdefault("dom_log", []).append(
            {"type": 3, "data": {"source": 2}, "timestamp": 1})
        return cap

    G.fixed_capture = _one_more_event
    try:
        moved = G.build_projection()["workflow_diagnostic.load_capture"]
    finally:
        G.fixed_capture = real_fixed

    assert (moved["capture_health"]["dom_log_len"]
            == base["capture_health"]["dom_log_len"] + 1), (
        "adding a DOM event to the fixture did not move capture_health's "
        "dom_log_len -- the forwarded block is no longer reading the fixture")
    assert moved["dom_log_len"] == base["dom_log_len"] + 1, (
        "adding a DOM event did not move the projection's OWN dom_log_len -- "
        "it is a pinned constant dressed as derived behaviour")
    assert moved["network_log_len"] == base["network_log_len"], (
        "a DOM-only perturbation moved network_log_len; the two counts are "
        "reading the same list")


def test_the_other_two_projections_are_untouched():
    """The ambient surface is confined to _proj_workflow. capture_ingest (47
    leaves) and build_template (121) must keep every field they had."""
    proj = G.build_projection()
    committed = json.loads(G.GOLDEN_PATH.read_text("utf-8"))
    for key in ("capture_ingest.normalize_capture",
                "build_template_from_wacz.build_template"):
        assert proj[key] == committed[key]
        assert proj[key], f"{key} projection collapsed to empty"
