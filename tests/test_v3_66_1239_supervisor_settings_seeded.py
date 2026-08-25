"""The supervisor throttle form shows the limits it is about to send.

BACKLOG ROW 240. `frontend/src/routes/Settings.tsx` declared the three
supervisor fields as `useState(false)` / `useState("0")` / `useState("")` and
put all three into `POST /api/supervisor/configure` UNCONDITIONALLY. The
`.trim()` guard beside them gates PARSING, not inclusion. So an operator who
opened Settings for any other reason and pressed Apply POSTed
``{enabled: false, global_bps: 0, per_site_bps: {}}``;
``bulk_downloader/app_supervisor.py:42-65`` hands that straight to
``download_supervisor.configure()`` and every live byte-rate limit is gone,
with nothing on screen having said so.

WHAT MADE THIS ITS OWN ROW RATHER THAN PART OF ITS SIBLING (row 238, the
Notifications allowlist): there was NO GET CONSUMER for supervisor state
anywhere in the SPA. ``grep -rc supervisor/status frontend/src`` matched
nothing at the base of this cut, so there was no payload to seed from and the
fix had to WIRE the read endpoint, not merely read a cache someone else had
already filled. The response shape was taken from the server rather than
assumed: ``app_supervisor.py:32-39`` returns ``{ok: False, error: ...}`` with no
``stats`` key when the supervisor is unavailable, and otherwise
``{ok: True, stats: <download_supervisor.stats()>}`` whose ``config`` sub-object
carries ``global_bps`` and ``per_site_bps`` -- the two live LIMITS -- alongside
token-bucket counters that are not settings.

SEVERITY, MEASURED RATHER THAN INFLATED. This is live IN-MEMORY throttle state
that a restart resets anyway, not a persisted list, and it sits behind an
Apply button plus a Confirm dialog. Lower than row 238's silent data loss. It
is still a silent reset of state the operator never saw.

THE FIX IS THE NULL-SENTINEL OVERLAY the sibling cut established:
``const x = xEdit ?? server ?? default`` behind ``useState<T | null>(null)``.
Deliberately NOT ``useState(server)`` -- the initializer runs once, before the
fetch resolves. Deliberately NOT ``useEffect(setX)`` -- it clobbers an
in-progress edit on every refetch. "The operator cleared it" and "the operator
never typed it" stay distinguishable BY CONSTRUCTION rather than by a dirty
flag someone can forget.

THREE VITEST SPECS, and the split is load-bearing rather than cosmetic:

* ``Settings.supervisorRoundtrip.test.tsx`` -- the TRANSPORT half: the SPA
  consumes the status endpoint at all, an untouched Apply sends the operator's
  own limits back, a DELIBERATELY cleared field still sends ``0``/``{}`` (the
  over-correction control), an edit beats the seed, and a successful apply
  re-reads the status it just changed;
* ``Settings.supervisorSeeding.test.tsx`` -- the DISPLAY half, including the
  case a preloaded cache cannot pose: a payload that arrives AFTER mount must
  still reach the fields, which is what separates the shipped fix from a
  ``useState(query.data)`` initializer. Its controls cover a later payload not
  clobbering an in-progress edit, and the ``{ok: false}`` unavailable payload
  degrading to the shipped defaults without throwing;
* ``Settings.supervisorSection.test.tsx`` -- the STRUCTURAL spec, which is also
  this cut's TRANSFORM CONTROL. ``toolchain/bin/bd-mutate`` has no parse handler
  for ``.tsx``, so a mutant that merely broke the file would score CAUGHT on
  "named catcher failed" alone. This spec renders and drives the same mutated
  module without asserting anything about seeding, so re-pointing a seeding
  mutant at it must ESCAPE -- which is what proves the other two discriminate
  on behaviour rather than on compilability.

RED PROVENANCE, a real base replay rather than a mutation battery dressed up as
one: the three spec files were copied UNCHANGED onto a detached checkout of the
base and run there. 9 of 11 failed on value assertions
(``expected '0' to be '12500000'``, ``expected +0 to be 12500000``,
``expected false to be true``, ``expected 0 to be greater than 0``), and the
two that passed were exactly the structural spec's two, which is what the
transform control is for.

run_tests.py conventions: zero-arg test functions; repo root from __file__; no
pytest builtins. Each test delegates to ONE tracked spec through
tests/frontend_vitest.py, which fails closed when Vitest is unavailable and
reconciles the executed denominator against the pin below.
"""
from __future__ import annotations

from pathlib import Path

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]

# The executed denominator each spec must reconcile to. Pinned HERE, in the
# gate, rather than read out of the spec: deriving the expectation from the
# artifact under test is how a silently dropped `it()` keeps a shard green.
_SPEC_DENOMINATORS = {
    "src/routes/Settings.supervisorRoundtrip.test.tsx": 5,
    "src/routes/Settings.supervisorSeeding.test.tsx": 4,
    "src/routes/Settings.supervisorSection.test.tsx": 2,
}


def test_1239_every_pinned_spec_exists_and_the_pin_is_nonzero():
    """The denominator guard, before any Vitest process starts.

    A missing spec file or a zero pin would make each delegating test below
    assert about nothing at all, and `run_vitest` would never be reached to say
    so. This is the independent existence proof of the population.
    """
    assert _SPEC_DENOMINATORS, "the spec denominator pin is empty"
    for spec, expected in sorted(_SPEC_DENOMINATORS.items()):
        path = REPO / "frontend" / spec
        assert path.is_file(), f"pinned Vitest spec is missing: {path}"
        assert expected > 0, f"{spec} is pinned at a zero denominator"


def test_1239_supervisor_limits_survive_an_untouched_apply():
    """Row 240, the transport half: an Apply sends the limits the operator can
    see, and a deliberately emptied field still sends its emptied value."""
    spec = "src/routes/Settings.supervisorRoundtrip.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_1239_supervisor_fields_are_seeded_from_get():
    """Row 240, the display half: the fields show the stored limits, including
    when the payload lands AFTER mount; a later payload does not clobber an
    in-progress edit; and the unavailable payload degrades to the defaults."""
    spec = "src/routes/Settings.supervisorSeeding.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_1239_supervisor_section_still_mounts_and_is_confirm_gated():
    """Row 240, the structural half AND the battery's transform control: the
    collapsed section mounts its three controls when opened, and Apply stays
    confirm-gated. Asserts nothing about seeding, on purpose."""
    spec = "src/routes/Settings.supervisorSection.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])
