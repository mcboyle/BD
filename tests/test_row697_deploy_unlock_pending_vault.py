"""Row 697: deploy resolves restart-lock ambiguity before judging health.

The production deploy script is exercised through the existing isolated real-
git harness.  Curl and the operator unlock hook are fixture-local executables;
no service, fleet host, credential, installer, or deploy checkout is touched.
The exact refusals and curl bound are pinned because row 697 measured that each
could be weakened independently while the earlier behavior tests stayed green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_deploy_script as deploy_support

BD_GATE_SCOPE = "repo-wide"


_SEQUENCED_CURL = r"""#!/usr/bin/env bash
printf '__ROW697_CALL__\0' >> "$CURL_ARGV_LOG"
printf '%s\0' "$@" >> "$CURL_ARGV_LOG"
outfile=""; wfmt=""; prev=""; url=""
for arg in "$@"; do
  if [ "$prev" = "-o" ] || [ "$prev" = "--output" ]; then outfile="$arg"; fi
  if [ "$prev" = "-w" ] || [ "$prev" = "--write-out" ]; then wfmt="$arg"; fi
  case "$arg" in http://*|https://*) url="$arg";; esac
  prev="$arg"
done

if [ "${url##*/api/health}" != "$url" ]; then
  count=0
  [ -f "$HEALTH_COUNT" ] && count="$(cat "$HEALTH_COUNT")"
  count=$((count + 1))
  printf '%s' "$count" > "$HEALTH_COUNT"
  printf 'health:%s\n' "$count" >> "$EVENTS_LOG"
  if [ "$count" -eq 1 ]; then
    body="$FIRST_HEALTH_BODY"; code="503"
  else
    body="$SECOND_HEALTH_BODY"; code="$SECOND_HEALTH_CODE"
  fi
else
  printf 'root\n' >> "$EVENTS_LOG"
  body=""; code="200"
fi

if [ -n "$outfile" ]; then printf '%s' "$body" > "$outfile"; else printf '%s' "$body"; fi
if [ -n "$wfmt" ]; then printf '%b' "${wfmt//%\{http_code\}/$code}"; fi
"""

_UNLOCK_HOOK = r"""#!/usr/bin/env bash
printf 'unlock:%s\n' "$*" >> "$EVENTS_LOG"
exit 0
"""

_FAILING_UNLOCK_HOOK = r"""#!/usr/bin/env bash
printf 'unlock:%s\n' "$*" >> "$EVENTS_LOG"
exit 23
"""

_REENTRY_TRIPWIRE_HOOK = r"""#!/usr/bin/env bash
printf 'unlock:%s\n' "$*" >> "$EVENTS_LOG"
count=0
[ -f "$UNLOCK_COUNT" ] && count="$(cat "$UNLOCK_COUNT")"
count=$((count + 1))
printf '%s' "$count" > "$UNLOCK_COUNT"
[ "$count" -eq 1 ]
"""


def _payload(*, degraded: str, state: str, initialized: bool,
             unlocked: bool, missing: int, resolved: int,
             stored: int, unavailable: int, credential_ok: bool = False) -> dict:
    return {
        "ok": False,
        "version": deploy_support.TREE_VERSION,
        "degraded": degraded,
        "db_ok": True,
        "queue_depth": 0,
        "active_downloads": 0,
        "sites_loaded": 1,
        "download_hold": {"state": "clear", "downloads_allowed": True},
        "credentials": {
            "backend": "master_password",
            "is_initialized": initialized,
            "is_unlocked": unlocked,
            "missing_count": missing,
            "ok": credential_ok,
            "reference_count": 2,
            "resolved_count": resolved,
            "state": state,
            "stored_count": stored,
            "unavailable_count": unavailable,
        },
    }


def _locked_missing_payload() -> dict:
    return _payload(
        degraded="credential_missing",
        state="missing_credentials",
        initialized=True,
        unlocked=False,
        missing=1,
        resolved=0,
        stored=1,
        unavailable=1,
    )


def _healthy_payload() -> dict:
    return {
        "ok": True,
        "db_ok": True,
        "version": deploy_support.TREE_VERSION,
    }


def _unlocked_missing_payload() -> dict:
    return _payload(
        degraded="credential_missing",
        state="missing_credentials",
        initialized=True,
        unlocked=True,
        missing=1,
        resolved=1,
        stored=1,
        unavailable=0,
    )


def _fixture(first: dict, second: dict, *, second_code: int,
             hook: str | None = _UNLOCK_HOOK):
    fx = deploy_support._setup()
    events = Path(fx.work) / "row697-events"
    count = Path(fx.work) / "row697-health-count"
    unlock_count = Path(fx.work) / "row697-unlock-count"
    curl_argv = Path(fx.work) / "row697-curl-argv"
    fx.env.update({
        "CURL_ARGV_LOG": str(curl_argv),
        "EVENTS_LOG": str(events),
        "HEALTH_COUNT": str(count),
        "FIRST_HEALTH_BODY": json.dumps(first, separators=(",", ":")),
        "SECOND_HEALTH_BODY": json.dumps(second, separators=(",", ":")),
        "SECOND_HEALTH_CODE": str(second_code),
        "UNLOCK_COUNT": str(unlock_count),
    })
    deploy_support._write_exec(Path(fx.binroot) / "curl", _SEQUENCED_CURL)
    if hook is not None:
        deploy_support._write_exec(
            Path(fx.env["HOME"]) / "bd-vault-unlock.sh", hook
        )
    deploy_support._bundle_current(fx)
    return fx, events


def _events(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _curl_calls(fx) -> list[list[str]]:
    path = Path(fx.env["CURL_ARGV_LOG"])
    tokens = [
        token.decode("utf-8")
        for token in path.read_bytes().split(b"\0")
        if token
    ]
    calls: list[list[str]] = []
    for token in tokens:
        if token == "__ROW697_CALL__":
            calls.append([])
        else:
            assert calls, "curl argv appeared before the call boundary"
            calls[-1].append(token)
    return calls


def test_initialized_locked_vault_runs_unlock_hook_then_reprobes_health():
    first = _locked_missing_payload()
    credentials = first["credentials"]
    assert (
        first["degraded"],
        credentials["is_initialized"],
        credentials["is_unlocked"],
        credentials["state"],
    ) == ("credential_missing", True, False, "missing_credentials")
    assert first["sites_loaded"] == 1, "precondition: census must contain one site"
    fx, events = _fixture(first, _healthy_payload(), second_code=200)

    result = deploy_support._deploy(fx)

    observed = _events(events)
    assert observed == ["health:1", "unlock:local", "health:2", "root"], (
        "initialized locked vault did not run the sanctioned local unlock hook "
        f"and re-probe exactly once before judgment: observed={observed}"
        + deploy_support._ctx(result)
    )
    assert result.returncode == 0, deploy_support._ctx(result)
    assert "UNLOCK-PENDING" in deploy_support._out(result), deploy_support._ctx(result)
    assert "re-probing within 5s" in deploy_support._out(result), deploy_support._ctx(result)
    curl_calls = _curl_calls(fx)
    assert len(curl_calls) == 3, f"expected two health probes and one root probe: {curl_calls}"
    max_times = []
    for call in curl_calls:
        assert call.count("--max-time") == 1, f"curl bound was not singular: {call}"
        option = call.index("--max-time")
        assert option + 1 < len(call), f"curl bound had no value: {call}"
        max_times.append(call[option + 1])
    assert max_times == ["5", "5", "5"], (
        f"curl argv did not enforce the stated five-second bound: {curl_calls}"
    )


def test_a_genuinely_missing_credential_fails_after_the_unlock_reprobe():
    first = _locked_missing_payload()
    second = _unlocked_missing_payload()
    assert second["credentials"]["missing_count"] == 1
    fx, events = _fixture(first, second, second_code=503)

    result = deploy_support._deploy(fx)

    assert _events(events) == ["health:1", "unlock:local", "health:2"]
    assert result.returncode == 1, deploy_support._ctx(result)
    assert "CREDENTIAL-MISSING-AFTER-UNLOCK" in deploy_support._out(result), (
        "post-unlock missing credentials shared only deploy.sh's generic rc=1"
        + deploy_support._ctx(result)
    )


def test_missing_credentials_without_unlock_hook_refuses_before_accepting_degraded():
    first = _locked_missing_payload()
    fx, events = _fixture(first, _healthy_payload(), second_code=200, hook=None)
    hook_path = Path(fx.env["HOME"]) / "bd-vault-unlock.sh"
    assert not hook_path.exists(), "precondition: isolated HOME unexpectedly has an unlock hook"
    assert first["credentials"]["state"] == "missing_credentials"

    result = deploy_support._deploy(fx)

    observed = _events(events)
    assert observed == ["health:1"], (
        "missing credentials without a hook crossed the refusal seam: "
        f"observed={observed}" + deploy_support._ctx(result)
    )
    assert observed.count("health:1") == 1
    assert result.returncode == 1, deploy_support._ctx(result)
    assert "VAULT-UNLOCK-HOOK-UNAVAILABLE" in deploy_support._out(result), (
        "no-hook missing credentials lost their distinctive fail-closed refusal"
        + deploy_support._ctx(result)
    )


def test_still_locked_after_unlock_refuses_after_one_bounded_reprobe():
    first = _locked_missing_payload()
    second = _locked_missing_payload()
    fx, events = _fixture(
        first,
        second,
        second_code=503,
        hook=_REENTRY_TRIPWIRE_HOOK,
    )
    assert first == second, "precondition: both health observations must remain locked"

    result = deploy_support._deploy(fx)

    observed = _events(events)
    assert observed == ["health:1", "unlock:local", "health:2"], (
        "the post-unlock locked state re-entered the hook or exceeded one re-probe: "
        f"observed={observed}" + deploy_support._ctx(result)
    )
    assert observed.count("unlock:local") == 1
    assert observed.count("health:2") == 1
    assert result.returncode == 1, deploy_support._ctx(result)
    assert "VAULT-STILL-LOCKED-AFTER-UNLOCK" in deploy_support._out(result), (
        "the bounded re-entry refusal lost its distinctive diagnostic"
        + deploy_support._ctx(result)
    )


def test_nonzero_unlock_hook_refuses_without_an_extra_health_probe():
    first = _locked_missing_payload()
    fx, events = _fixture(
        first,
        _healthy_payload(),
        second_code=200,
        hook=_FAILING_UNLOCK_HOOK,
    )
    hook_path = Path(fx.env["HOME"]) / "bd-vault-unlock.sh"
    assert hook_path.is_file(), "precondition: failing unlock hook was not installed"

    result = deploy_support._deploy(fx)

    observed = _events(events)
    assert observed == ["health:1", "unlock:local"], (
        f"a failed hook reached another health probe: observed={observed}"
        + deploy_support._ctx(result)
    )
    assert observed.count("unlock:local") == 1
    assert result.returncode == 1, deploy_support._ctx(result)
    assert "VAULT-UNLOCK-HOOK-FAILED" in deploy_support._out(result), (
        "nonzero hook failure lost its distinctive refusal"
        + deploy_support._ctx(result)
    )


def test_preunlock_missing_credential_keeps_its_distinctive_refusal():
    first = _unlocked_missing_payload()
    fx, events = _fixture(first, _healthy_payload(), second_code=200)
    assert first["credentials"]["is_unlocked"] is True
    assert first["credentials"]["missing_count"] == 1

    result = deploy_support._deploy(fx)

    observed = _events(events)
    assert observed == ["health:1"], (
        f"pre-unlock missing credential crossed its refusal: observed={observed}"
        + deploy_support._ctx(result)
    )
    assert observed.count("health:1") == 1
    assert result.returncode == 1, deploy_support._ctx(result)
    output = deploy_support._out(result)
    assert "CREDENTIAL-MISSING:" in output, (
        "pre-unlock missing credential collapsed into the generic version refusal"
        + deploy_support._ctx(result)
    )
    assert "CREDENTIAL-MISSING-AFTER-UNLOCK" not in output


@pytest.mark.parametrize(
    ("first", "diagnostic"),
    [
        (
            _payload(
                degraded="credential_vault_uninitialized",
                state="uninitialized",
                initialized=False,
                unlocked=False,
                missing=2,
                resolved=0,
                stored=0,
                unavailable=0,
            ),
            "VAULT-UNINITIALIZED",
        ),
        (
            _payload(
                degraded="runner_status_error",
                state="unlocked",
                initialized=True,
                unlocked=True,
                missing=0,
                resolved=2,
                stored=2,
                unavailable=0,
                credential_ok=True,
            ),
            "UNRELATED-DEGRADED",
        ),
    ],
)
def test_non_unlockable_degradation_fails_without_calling_the_hook(first, diagnostic):
    assert first["sites_loaded"] == 1
    assert first["degraded"] in {
        "credential_vault_uninitialized",
        "runner_status_error",
    }
    fx, events = _fixture(first, _healthy_payload(), second_code=200)

    result = deploy_support._deploy(fx)

    assert _events(events) == ["health:1"], (
        "a non-unlockable degradation reached the unlock or unrelated root seam"
        + deploy_support._ctx(result)
    )
    assert result.returncode == 1, deploy_support._ctx(result)
    assert diagnostic in deploy_support._out(result), (
        f"refusal did not carry distinctive diagnostic {diagnostic}"
        + deploy_support._ctx(result)
    )


def test_transform_control_imports_subject_without_asserting_behavior():
    assert deploy_support.SCRIPT.is_file()
