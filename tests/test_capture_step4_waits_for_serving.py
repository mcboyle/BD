"""Step [4] gates the vault unlock on 'started', not on 'serving'.

THE DEFECT. capture.sh step [4] reads:

    ./install_service.sh > "$OUT/04_service_install.log" 2>&1
    INSTALL_EXIT=$?
    sleep 3
    ...
    ACTIVE=$(systemctl is-active bulkdownloader 2>&1)
    ...
    if [ "$CAPTURE_VAULT" = "1" ] && [ "$ACTIVE" = "active" ]; then
      UNLOCK_CODE=$(... curl -X POST .../api/secrets/unlock ...)

Both gates answer the wrong question. `systemctl restart` returns when the unit
is STARTED, and with Type=simple that means the process was spawned -- not that
waitress has bound :5555. install_service.sh has the same shape: it polls
`systemctl is-active` and prints "is RUNNING and enabled on boot" the moment the
unit goes active. A fixed `sleep 3` is a guess at the gap between those two
events.

When the guess is wrong the unlock POSTs into a closed socket, curl records
HTTP 000, and the capture continues with a LOCKED vault -- which makes the
seeder refuse, which makes every check that needs seeded state report on
nothing. The failure is silent in the worst way: the log line reads
`capture-vault unlock: HTTP 000` next to `service: active`, so the two most
visible facts disagree and neither is wrong.

This is the same defect already fixed once in this file. `wait_for_service_ready`
exists (capture.sh:151) and polls `/api/health` until it answers -- but it is
wired only into the vault-RESTORE path after step [6]. Step [4], where the
unlock actually depends on the service serving, never calls it.

STICKINESS. SERVICE_READY_EXIT is a single global plumbed to the capture verdict
as a stage exit. With two call sites it becomes last-write-wins, so a failure at
step [4] would be erased by a success after step [6] -- the capture would report
a clean stage while the unlock had in fact fired into a dead socket. It must
latch: once the service was not known to be serving at a point where the capture
acted on it, that is true for the run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"


@pytest.fixture(scope="module")
def body() -> str:
    if not CAPTURE_SH.is_file():
        pytest.fail(f"{CAPTURE_SH} not found; this gate cannot verify its subject")
    return CAPTURE_SH.read_text(encoding="utf-8")


def _step4(body: str) -> str:
    """The step [4] block: from its banner to the next step banner."""
    start = body.find("[4/9] Install + start systemd service")
    if start < 0:
        pytest.fail("step [4] banner not found in capture.sh; the shape this "
                    "gate reads has changed and it cannot answer")
    nxt = body.find("[5/9]", start)
    if nxt < 0:
        nxt = body.find("[5a/9]", start)
    return body[start:nxt if nxt > 0 else len(body)]


def _code_lines(chunk: str) -> list[str]:
    return [ln for ln in chunk.splitlines() if not ln.strip().startswith("#")]


# ── denominator canaries ─────────────────────────────────────────────────────

def test_step_four_exists_and_unlocks(body):
    """No step [4], or no unlock in it, and everything below is vacuous."""
    chunk = _step4(body)
    assert chunk.strip(), "step [4] block is empty"
    assert "/api/secrets/unlock" in chunk, (
        "step [4] no longer performs the vault unlock; this gate is aimed at "
        "the wrong block and would pass without checking anything."
    )


def test_the_readiness_helper_still_exists(body):
    assert "wait_for_service_ready()" in body, (
        "capture.sh no longer defines wait_for_service_ready; the fix this gate "
        "requires has no implementation to call."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_step_four_waits_for_serving_not_a_fixed_sleep(body):
    """A fixed sleep is a guess at the started->serving gap."""
    code = "\n".join(_code_lines(_step4(body)))
    bare_sleeps = re.findall(r"^\s*sleep\s+[\d.]+\s*$", code, re.MULTILINE)
    assert not bare_sleeps, (
        f"step [4] still uses a bare fixed sleep {bare_sleeps} to wait for the "
        f"service. systemctl returns on STARTED, not on SERVING; the unlock "
        f"that follows can POST into a closed socket and record HTTP 000."
    )


def test_step_four_calls_the_readiness_helper(body):
    """The positive form, so the fix cannot be a bare deletion of the sleep."""
    code = "\n".join(_code_lines(_step4(body)))
    assert "wait_for_service_ready" in code, (
        "step [4] does not call wait_for_service_ready. Removing the sleep "
        "without waiting for /api/health to answer makes the race worse, not "
        "better."
    )


def test_the_unlock_is_gated_on_serving(body):
    """`is-active` alone is the wrong precondition for an HTTP POST."""
    chunk = _step4(body)
    unlock_at = chunk.find("/api/secrets/unlock")
    guard = chunk[:unlock_at]
    gated = ("SERVICE_READY_EXIT" in guard) or ("READY" in guard and "=" in guard)
    assert gated, (
        "the vault unlock is not gated on the service actually serving -- only "
        "on `systemctl is-active`, which is true while the socket is still "
        "closed. The unlock is an HTTP POST; its precondition must be an HTTP "
        "fact."
    )


def test_a_readiness_failure_cannot_be_erased_by_a_later_success(body):
    """SERVICE_READY_EXIT is plumbed to the verdict and has two call sites now.

    Plain assignment makes it last-write-wins, so a failure at step [4] would be
    cleared by a success after step [6] and the capture would report a clean
    stage over an unlock that fired into a dead socket.
    """
    assign = re.findall(r"^\s*SERVICE_READY_EXIT=0\s*$", body, re.MULTILINE)
    # The initial declaration is legitimate; a reset INSIDE the helper is not,
    # unless it is guarded so it cannot clear a previous failure.
    helper_start = body.find("wait_for_service_ready()")
    helper = body[helper_start:helper_start + 1200]
    resets_unconditionally = re.search(r"^\s*SERVICE_READY_EXIT=0\s*$",
                                       helper, re.MULTILINE)
    assert not resets_unconditionally, (
        f"wait_for_service_ready sets SERVICE_READY_EXIT=0 unconditionally "
        f"(found {len(assign)} plain assignments in the file). With two call "
        f"sites a later success erases an earlier failure, and the capture "
        f"verdict reports a stage that did not hold."
    )
