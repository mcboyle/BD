"""Row 660 -- a gate run must not rewrite tracked generated artifacts on the way past.

MEASURED on origin/main 06a1ee2e by instrumenting the FILE, not the source:

  strace -f -y -P $PWD/PIN_INDEX.json -e trace=openat,...,write \
    venv/bin/python toolchain/bin/bd-precut --gate

recorded exactly one destructive access in a 17m13s run --

  openat(AT_FDCWD<...>, ".../PIN_INDEX.json", O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC, 0666) = 3
  write(3<.../PIN_INDEX.json>, "{\n  \"schema_version\": 1,\n  \"gene"..., 42579) = 42579

-- followed immediately by SIGPIPE, because bd-precut's FG-CENSUS-NEEDS-THE-VENV
detector runs `bd-regen-order --work {tree}` (FOOTGUNS.json) and gave up on it at
150s.  bd-regen-order is the REGENERATOR: a check was rewriting the tree it checks.
Because the chain writes artifacts in sequence, an INTERRUPTED run leaves an
arbitrary prefix of them rewritten, which is why the three sightings named
different files.

The verdict that detector exists for -- census expiry, then the reachability
ratchet -- is computed BEFORE the chain runs, so the regeneration was pure side
effect for this caller.  These gates pin that the detector asks for the verdict
WITHOUT asking for the write, and that a caller that genuinely wants regeneration
still gets it.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-regen-order"
FOOTGUNS = ROOT / "FOOTGUNS.json"
DETECTOR_ID = "FG-CENSUS-NEEDS-THE-VENV"


def _load_regen_order():
    """Load the extensionless tool. spec_from_file_location cannot: no known suffix."""
    name = "_bd_regen_order_row660"
    loader = importlib.machinery.SourceFileLoader(name, str(TOOL))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None and spec.loader is not None, "bd-regen-order is not loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _detector_argv():
    """The detector's OWN argv, read from FOOTGUNS.json -- never retyped here."""
    raw = json.loads(FOOTGUNS.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("footguns", raw.get("items", []))
    hits = [f for f in items if f.get("id") == DETECTOR_ID]
    assert len(hits) == 1, (
        "expected exactly 1 %s in FOOTGUNS.json, found %d" % (DETECTOR_ID, len(hits)))
    det = hits[0].get("detector") or {}
    assert det.get("kind") == "tool", "%s is no longer a tool detector: %r" % (
        DETECTOR_ID, det.get("kind"))
    argv = list(det.get("cmd") or [])
    assert argv and argv[0] == "bd-regen-order", (
        "%s no longer drives bd-regen-order: %r" % (DETECTOR_ID, argv))
    return argv


class _Recorder:
    """Records every subprocess the tool would have launched, and runs none of them."""

    def __init__(self):
        self.chain = []
        self.venv = []

    def install(self, module, monkeypatch):
        monkeypatch.setattr(module, "_run",
                            lambda work, argv, env=None: (self.chain.append(tuple(argv)), (0, ""))[1])
        monkeypatch.setattr(module, "_run_venv",
                            lambda work, argv: (self.venv.append(tuple(argv)), (0, ""))[1])


def _chain_only(module, launched):
    """The launched subprocesses that are REGENERATORS, not read-only verifiers."""
    writers = {tuple(argv) for _label, argv, _why in module.CHAIN}
    return [a for a in launched if a in writers]


def _verify_only(module, launched):
    """The launched subprocesses that are READ-ONLY VERIFIERS, not regenerators."""
    verifiers = {tuple(argv) for _label, argv, _why in module.VERIFY}
    return [a for a in launched if a in verifiers]


def _invoke(module, monkeypatch, argv, work):
    """Drive main() with an argv, with every subprocess recorded instead of run."""
    rec = _Recorder()
    rec.install(module, monkeypatch)
    monkeypatch.setattr(module, "check_census_expiry",
                        lambda w: (True, "row660 fixture: census fine"))
    monkeypatch.setattr(module, "check_reach", lambda w: (True, "row660 fixture: dark in sync"))
    monkeypatch.setattr(module.sys, "argv", ["bd-regen-order"] + argv + ["--work", str(work)])
    rc = module.main()
    return rc, rec


# ---- PRECONDITION -------------------------------------------------------------

def test_row660_the_chain_population_is_nonzero_and_writes_tracked_files():
    """Denominator: the chain this row is about must actually exist and be tracked."""
    module = _load_regen_order()
    labels = tuple(module.REQUIRED_CHAIN_LABELS)
    assert len(labels) > 0, "REQUIRED_CHAIN_LABELS is empty -- nothing to regenerate"
    outputs = module.tracked_outputs()
    assert len(outputs) > 0, "tracked_outputs() is empty -- no tracked artifact at risk"
    # Every declared output is a real tracked path in this tree, so the hazard is real.
    for rel in outputs:
        assert (ROOT / rel).exists(), "declared chain output is absent: %s" % rel


# ---- THE ROW ------------------------------------------------------------------

def test_row660_the_census_detector_does_not_run_the_regeneration_chain(monkeypatch):
    """RED on the parent: the gate's detector runs the whole regenerating chain."""
    module = _load_regen_order()
    argv = _detector_argv()[1:]          # drop the tool name; keep the tool's own flags
    argv = [a for a in argv if a not in ("--work", "--tree", "{tree}")]
    rc, rec = _invoke(module, monkeypatch, argv, ROOT)
    wrote = _chain_only(module, rec.chain)
    assert wrote == [], (
        "%s asked bd-regen-order to REGENERATE while merely checking: it launched %d "
        "chain generator(s) %r. A check that mutates the tree it checks is the defect "
        "(row 660). rc=%r" % (DETECTOR_ID, len(wrote), [a[0] for a in wrote], rc))


# ---- NEGATIVE CONTROLS --------------------------------------------------------

def test_row660_an_explicit_caller_still_gets_the_regeneration(monkeypatch):
    """A caller that ASKS to regenerate must still regenerate -- exact nonzero count.

    This is the control that fails if the fix were 'stop regenerating', which would
    make the row's own gate green for the wrong reason.
    """
    module = _load_regen_order()
    rc, rec = _invoke(module, monkeypatch, [], ROOT)
    expected = [tuple(argv) for _label, argv, _why in module.CHAIN]
    assert len(expected) > 0, "CHAIN is empty -- the control has no denominator"
    assert _chain_only(module, rec.chain) == expected, (
        "an explicit `bd-regen-order` run launched %d of %d chain generators -- the fix "
        "must not disable regeneration for callers that ask for it"
        % (len(_chain_only(module, rec.chain)), len(expected)))


def test_row660_check_mode_still_judges_the_subject_the_detector_blocks_on(monkeypatch):
    """Clause 3: read-only mode reaches the SAME verdicts on the SAME subject.

    TWO blocking verdicts sit ahead of the VERIFY steps and each must survive --check:
    census expiry, which returns before the ratchet is ever reached, and the reachability
    ratchet itself. A read-only mode that quietly stopped consulting either would satisfy
    the no-write assertion while no longer judging anything -- the same hazard M3 pins for
    the verifiers, one verdict over -- so both are asserted here by firing COUNT and by the
    refusal each must still produce.
    """
    module = _load_regen_order()

    def _drive(census_ok, reach_ok, seen):
        def _census(w):
            seen["census"] += 1
            return census_ok, "row660 fixture: census %s" % ("fine" if census_ok else "EXPIRED")

        def _reach(w):
            seen["reach"] += 1
            return reach_ok, "row660 fixture: dark %s" % ("in sync" if reach_ok else "RATCHET")

        rec = _Recorder()
        rec.install(module, monkeypatch)
        monkeypatch.setattr(module, "check_census_expiry", _census)
        monkeypatch.setattr(module, "check_reach", _reach)
        monkeypatch.setattr(module.sys, "argv",
                            ["bd-regen-order", "--check", "--work", str(ROOT)])
        return module.main(), rec

    # (a) an EXPIRED census still blocks, and is consulted exactly once.
    seen = {"census": 0, "reach": 0}
    rc, rec = _drive(False, True, seen)
    assert seen["census"] == 1, (
        "read-only mode consulted the census verdict %d times, expected exactly 1"
        % seen["census"])
    assert rc == 1, (
        "an EXPIRED census must still block the gate in read-only mode; got rc=%r" % rc)
    assert _chain_only(module, rec.chain) == [], (
        "read-only mode still launched %d generator(s)"
        % len(_chain_only(module, rec.chain)))

    # (b) the REACHABILITY RATCHET is the second blocking verdict, reached only once the
    # census passes: consulted exactly once, and a FAILING ratchet must still refuse.
    seen = {"census": 0, "reach": 0}
    rc, rec = _drive(True, False, seen)
    assert seen["reach"] == 1, (
        "read-only mode consulted the reachability ratchet %d times, expected exactly 1 -- "
        "a --check that skips the ratchet stops judging the dark-endpoint count while the "
        "suite stays green" % seen["reach"])
    assert rc != 0, (
        "a FAILING reachability ratchet must still block the gate in read-only mode; "
        "got rc=%r" % rc)
    assert _chain_only(module, rec.chain) == [], (
        "read-only mode still launched %d generator(s)"
        % len(_chain_only(module, rec.chain)))


def test_row660_read_only_mode_refuses_an_explicit_write_request(monkeypatch):
    """--check is read-only, so pairing it with a --declare-* write must refuse."""
    module = _load_regen_order()
    rc, rec = _invoke(module, monkeypatch, ["--check", "--declare-reach"], ROOT)
    assert rc != 0, "--check --declare-reach must refuse rather than silently write"
    wrote = _chain_only(module, rec.chain)
    assert wrote == [], "a refused run still launched %d generator(s)" % len(wrote)


def test_row660_check_mode_still_runs_every_read_only_verifier(monkeypatch):
    """Clause 3, the other half: read-only mode must still JUDGE, not merely fall silent.

    Skipping the chain is only correct if the verdict survives it. The verifiers are
    read-only by construction (`templates_snapshot.py --check`, `check_route_counts.py`),
    so every one of them must still run, and the denominator must be nonzero -- a fix
    that quietly stopped judging would satisfy the no-write assertion above.
    """
    module = _load_regen_order()
    rc, rec = _invoke(module, monkeypatch, ["--check"], ROOT)
    expected = [tuple(argv) for _label, argv, _why in module.VERIFY]
    assert len(expected) > 0, "VERIFY is empty -- there is no subject left to judge"
    assert _verify_only(module, rec.chain) == expected, (
        "read-only mode ran %d of %d verifier(s); --check must judge the SAME subject"
        % (len(_verify_only(module, rec.chain)), len(expected)))
    assert _chain_only(module, rec.chain) == [], "read-only mode launched a generator"
    assert rc == 0, "a clean tree must still pass in read-only mode; got rc=%r" % rc
