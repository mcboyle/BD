"""A deliberately AI-free host is a supported deployment, not a warning.

THE INCONSISTENCY, measured on a full six-host capture round at v3.66.1134.
Three hosts have no GPU (test7's card was physically removed; test3 and test2
were provisioned without one), so ollama is inactive and /api/ai/status reports
`enabled: false`. For that ONE state the suite gives three different answers:

    L17  ollama-reachable        -> PASS   "AI assist disabled by config --
                                            Ollama not required for this
                                            deployment"
    L18  vision-call-roundtrip   -> WARN   "AI assist disabled by config"
    L19  ai-text-call-roundtrip  -> WARN   "AI assist disabled by config"

L17 is right and L18/L19 are wrong, and the asymmetry is not cosmetic: it puts
two permanent WARNs on every GPU-less host, which is exactly how a real AI
regression there becomes invisible. An operator who sees 7 warns every run stops
reading them, and the two that would matter are indistinguishable from the five
that never will.

WHY NOT JUST INSTALL OLLAMA INSTEAD. Two reasons, both measured. The hardware is
gone -- `lspci | grep -ci nvidia` is 0 on all three -- so it would be CPU
inference. And test7 is the ONLY host exercising the AI-disabled configuration
that BD explicitly supports; `install_ai_ollama.sh`'s own header says it is
"Separate from install_linux.sh on purpose: the core app runs fine without AI;
this is opt-in". Installing it everywhere for uniformity would retire the only
coverage of the supported no-AI path -- section 0's blind-gate shape, at fleet
scale.

THE DISTINCTION THIS FILE PROTECTS, and the reason the fix is two lines rather
than one. There are two adjacent states and only ONE of them is intentional:

    enabled=False              -> the operator turned AI off        -> PASS
    enabled=True, ok=False     -> AI is on and the backend is down  -> WARN

Collapsing those would silence a genuine outage, which is the same defect
pointing the other way. Every assertion below is therefore paired: the disabled
state must PASS, and the enabled-but-broken state must NOT.
"""

from __future__ import annotations

import pathlib
import sys

# Its subject is three check functions and the contract between them, not the
# tree. It enumerates nothing.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from live_tests import checks  # noqa: E402  (import IS the registration)
from live_tests.harness import PASS, WARN  # noqa: E402


class _Ctx:
    """The narrowest ctx these three checks need.

    `ctx.get` returns (ok, status, body, elapsed_ms) -- the real harness's
    signature, verified against live_tests/harness.py rather than guessed, on
    the principle that a fake which does not match the real shape certifies
    nothing (CLAUDE.md section 6's harness rule).
    """

    def __init__(self, ai_body, site_body=None):
        self._ai = ai_body
        self._site = site_body if site_body is not None else {}
        self.logged: list[str] = []

    def log(self, msg):
        self.logged.append(str(msg))

    def get(self, path, timeout=15):
        if path.startswith("/api/ai/status"):
            return True, 200, self._ai, 1.0
        return True, 200, self._site, 1.0

    def post(self, path, payload=None, timeout=15):      # pragma: no cover
        raise AssertionError(
            "no AI-disabled path may POST -- reaching the backend is exactly "
            f"what a disabled deployment must not do (path={path})")


THE_THREE = ("l17", "l18", "l19")


def _fn(prefix):
    """The check function whose name starts with the given Lnn prefix."""
    matches = [getattr(checks, n) for n in dir(checks)
               if n.startswith(prefix + "_") and callable(getattr(checks, n))]
    assert len(matches) == 1, (
        f"expected exactly one {prefix}_* check, found {len(matches)}; this "
        "test's denominator is stale and must be re-derived, not relaxed")
    return matches[0]


def test_all_three_checks_are_present():
    """PRECONDITION. Without it, a rename turns every assertion below vacuous."""
    for p in THE_THREE:
        assert callable(_fn(p)), f"{p} is missing"


def test_a_disabled_backend_is_never_contacted():
    """The fake POSTs nothing; reaching the network would raise.

    This is the precondition for the verdict assertions: if a check somehow
    called out, the run would error rather than quietly pass, so a green below
    means the disabled branch was genuinely taken.
    """
    ctx = _Ctx({"enabled": False, "ok": False})
    for p in THE_THREE:
        _fn(p)(ctx)          # _Ctx.post raises if any of them tries


def test_disabled_by_config_is_a_PASS_for_all_three():
    """THE FIX. L17 already does this; L18 and L19 must agree.

    One state, one answer. A host with AI deliberately off is a supported
    deployment and every check that observes that state should say so.
    """
    ctx = _Ctx({"enabled": False, "ok": False})
    verdicts = {}
    for p in THE_THREE:
        v = _fn(p)(ctx)
        verdicts[p] = v[0] if isinstance(v, tuple) else v

    disagreeing = {p: v for p, v in verdicts.items() if v != PASS}
    assert not disagreeing, (
        "these checks disagree about one intentional state: "
        f"{verdicts}. L17 returns PASS for AI-disabled-by-config ('Ollama not "
        "required for this deployment'); L18/L19 must not WARN about the same "
        "deliberate configuration. Two permanent warns on every GPU-less host "
        "is how a real AI regression there becomes unreadable.")


def test_enabled_but_broken_still_warns_for_all_three():
    """THE OVER-SENSITIVE DIRECTION, in the same file as the fix.

    A change that returned PASS whenever the AI section was unhappy would
    satisfy the test above and destroy the checks. AI switched ON with an
    unreachable backend is a real fault and must never read as clean.
    """
    ctx = _Ctx({"enabled": True, "ok": False, "error": "connection refused"})
    for p in ("l18", "l19"):
        v = _fn(p)(ctx)
        verdict = v[0] if isinstance(v, tuple) else v
        assert verdict == WARN, (
            f"{p} returned {verdict!r} for AI ENABLED with an unreachable "
            "backend. That is a genuine outage, not an intentional state -- "
            "silencing it would be the same defect as the one this file fixes, "
            "pointing the other way.")


def test_the_disabled_message_still_names_the_reason():
    """A PASS that explains nothing is worse than a WARN that does.

    The operator reading a green line must still learn WHY the check did not
    exercise anything, or 'PASS' quietly means 'not tested'.
    """
    ctx = _Ctx({"enabled": False, "ok": False})
    for p in THE_THREE:
        v = _fn(p)(ctx)
        detail = (v[1] if isinstance(v, tuple) and len(v) > 1 else "") or ""
        assert "disabled" in detail.lower(), (
            f"{p} passes on a disabled backend without saying so: {detail!r}. "
            "A green line that does not state it skipped the work reads as "
            "evidence the work succeeded.")
