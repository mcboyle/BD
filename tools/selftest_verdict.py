#!/usr/bin/env python3
"""Grade a captured /api/selftest body into a capture stage exit.

WHY THIS IS A FILE AND NOT `curl ... && echo ok`. capture.sh's own header
records step [7] probing `sse_smoke`, a route that is not registered: it
returned ``{"error":"endpoint not found"}`` on every run and nothing noticed,
because ``curl -fsS`` exits 0 on a 200 carrying an error body. A stage keyed on
curl's exit code would write a reassuring log for a battery that never ran,
which makes a blind spot INVISIBLE rather than absent -- strictly worse than not
probing at all.

THE LOAD-BEARING CHECK IS THE DENOMINATOR, not the status. ``ok + warn + fail``
must be at least 1; a body reporting a battery of zero checks has verified
nothing, and "verified nothing" is not a pass. Unknown is a third state and it
fails.

WHY THIS IS A FILE AND NOT AN INLINE `python -c`. tests/test_provision_test_host
asserts comment-stripped capture.sh contains no surviving comment lines, and its
stripper carries quote state across lines -- so a `#` inside a multi-line quoted
program survives stripping and turns that gate red. JSON parsing therefore has
to live here.

WARN IS NOT A FAILURE, deliberately. tools/capture_verdict.py collapses any
nonzero stage exit to FAIL and has no warn tier, and its own comments record why
live WARNs were ungated: gating them "reported FAIL on a healthy box that no
code change could ever turn green ... a gate that cries wolf gets switched off."
Over-sensitivity is a soundness bug in the same way blindness is.

Exit codes:
  0  a real battery ran and reported no failures (warnings allowed)
  1  the battery ran and reported failures
  2  CANNOT EVALUATE -- absent, unreadable, malformed, self-inconsistent, or empty

1 and 2 are both nonzero and both turn the capture red. They are distinct so the
operator reading the log can tell a failing box from an unreadable answer; those
are different problems with different fixes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_CLEAN = 0
EXIT_FAILURES = 1
EXIT_CANNOT_EVALUATE = 2

_STATUSES = ("ok", "warn", "fail")


def grade(raw: str | None) -> tuple[int, str]:
    """Return (exit_code, human_line) for a captured response body."""
    if raw is None:
        return EXIT_CANNOT_EVALUATE, (
            "CANNOT EVALUATE: no response body was captured -- the probe "
            "produced no evidence, which is not the same as a clean battery"
        )
    try:
        body = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: response is not valid JSON ({exc}); "
            f"first 120 bytes: {raw[:120]!r}"
        )

    if not isinstance(body, dict):
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: response is {type(body).__name__}, not an object"
        )

    summary = body.get("summary")
    if not isinstance(summary, dict):
        # why: this is the sse_smoke shape -- {"error": "..."} behind a 200.
        detail = body.get("error") or sorted(body)[:6]
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: no selftest summary in the response -- the route "
            f"answered, but not with a battery. Body said: {detail!r}"
        )

    counts = {}
    for key in _STATUSES:
        value = summary.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return EXIT_CANNOT_EVALUATE, (
                f"CANNOT EVALUATE: summary[{key!r}] is {value!r}, not a count"
            )
        counts[key] = value

    total = sum(counts.values())
    if total < 1:
        return EXIT_CANNOT_EVALUATE, (
            "CANNOT EVALUATE: the battery reported ZERO checks. A denominator "
            "that cannot contain the subject reports clean -- truthfully, and "
            "uselessly"
        )

    checks = body.get("checks")
    if not isinstance(checks, list):
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: 'checks' is {type(checks).__name__}, not a list; "
            "the summary cannot be corroborated"
        )
    if len(checks) != total:
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: summary counts {total} check(s) but 'checks' "
            f"carries {len(checks)} -- the body disagrees with itself, so it is "
            "not evidence about anything"
        )

    reported_ok = body.get("ok")
    if isinstance(reported_ok, bool) and reported_ok != (counts["fail"] == 0):
        return EXIT_CANNOT_EVALUATE, (
            f"CANNOT EVALUATE: body says ok={reported_ok} while reporting "
            f"{counts['fail']} failure(s) -- inconsistent"
        )

    line = (f"{total} check(s): {counts['ok']} ok, {counts['warn']} warn, "
            f"{counts['fail']} fail")
    if counts["fail"]:
        return EXIT_FAILURES, f"FAIL: {line}"
    if counts["warn"]:
        # why: not a stage failure. capture_verdict has no warn tier, and gating
        # warnings would fail a healthy box for a condition no code change fixes.
        return EXIT_CLEAN, f"OK (with warnings): {line}"
    return EXIT_CLEAN, f"OK: {line}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: selftest_verdict.py <captured-response.json>",
              file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    path = Path(argv[1])
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        code, line = grade(None)
        print(f"{line} ({exc})")
        return code

    code, line = grade(raw)
    print(line)
    if code != EXIT_CLEAN:
        failing = []
        try:
            parsed = json.loads(raw)
            for check in parsed.get("checks", []) or []:
                if isinstance(check, dict) and check.get("status") == "fail":
                    failing.append(check)
        except (ValueError, TypeError, AttributeError):
            pass
        for check in failing:
            # @897: this read `name` and `detail`, and the response carries
            # NEITHER as those. bulk_downloader.selftest._result builds every
            # check as {status, test, message, detail, ts} -- `test` is the
            # name, `message` is the sentence, and `detail` is a DICT of
            # structured fields (error_class, hint, free_gb). So `name` was
            # always absent and rendered '?', and a dict was printed where the
            # message belonged. The verdict and the exit code were right, which
            # is why a mutation battery could not see it: it degrades only the
            # output someone reads while debugging a RED capture.
            line = f"  FAIL  {check.get('test', '?')}: {check.get('message', '')}"
            extra = check.get("detail")
            if isinstance(extra, dict) and extra:
                # why: error_class and hint are the fields a reader acts on;
                # dropping detail entirely is the over-correction.
                line += "  [" + ", ".join(
                    f"{k}={v}" for k, v in sorted(extra.items())) + "]"
            print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
