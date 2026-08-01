"""Shared scaffolding for tools/*_probe.py scripts.

Provides:
  * make_isolated_client() — spin up bulk_downloader.app with a tmp
    BD_HOME so probes don't touch real user data.
  * Finding / Report — uniform severity ladder & summary.
  * csrf_post(client, ...) — POST helper that pre-fetches CSRF tokens,
    mimicking the JS fetch wrapper.
  * cleanup_BD_HOME() — registered at exit; idempotent.

Why a shared lib instead of a class hierarchy? Probes are read-top-to-
bottom checklists. A flat helper module keeps each probe self-contained
in its `_probe.py` file while removing the copy-paste tax.
"""
from __future__ import annotations
import atexit
import os
import shutil
import sys
import tempfile
from typing import Optional

# Force repo root on sys.path even when run from tools/
SCRIPT = os.path.abspath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

# Severity symbols & ordering
_SYM = {"info": "·", "ok": "✓", "warn": "⚠", "bug": "✗", "crit": "🛑"}
_ORDER = ("info", "ok", "warn", "bug", "crit")

# The grades that FAIL a probe run -- ADD A NEW FAILING GRADE HERE.
#
# _ORDER above is the LADDER, not this set. It also carries info/ok/warn, which
# are healthy outcomes, so a consumer that derived "failing" from _ORDER would
# fire on every healthy probe run. The two are different questions and this is
# the answer to the second one.
#
# Two consumers read this constant and must not be able to disagree:
#   * Report.exit_code() below -- the process exit status of tools/*_probe.py
#   * tests/test_cut35_csrf_meta_premise_retired_in_tools.py -- builds the
#     pattern deciding whether a probe section graded a defect
#
# It used to exist only as an inline expression inside exit_code(), with the
# gate hardcoding its own second copy. A grade added to the ladder then updated
# the exit status while leaving the gate blind, and no positive control noticed:
# a control only proves the instrument sees what its author thought to plant.
# Adding a grade here (plus _SYM/_ORDER above) now moves both.
FAILING_GRADES = ("bug", "crit")

# Loud at import, because exit_code() indexes self.counts (built from
# _ORDER) by every member: a grade added HERE but not to _SYM/_ORDER
# would otherwise KeyError at run time, inside a probe, long after the
# edit. The old inline form could not fail this way -- it hardcoded two
# permanent members -- so the check is new obligation, not decoration.
assert set(FAILING_GRADES) <= set(_ORDER), (
    "FAILING_GRADES must be a subset of _ORDER; add the grade to _SYM "
    "and _ORDER too: %s" % (set(FAILING_GRADES) - set(_ORDER),))


class Report:
    """Unified severity counter + finding log.

    Usage:
        rep = Report()
        rep.F("ok", "thing worked")
        rep.F("bug", "thing broke", detail="explanation")
        rep.section("New section")
        rep.summary()
        sys.exit(rep.exit_code())
    """

    def __init__(self, name: str = "PROBE") -> None:
        self.name = name
        self.counts = {k: 0 for k in _ORDER}
        self.findings: list[tuple[str, str, str]] = []

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def F(self, sev: str, name: str, detail: str = "") -> None:
        if sev not in _SYM:
            raise ValueError(f"bad severity: {sev}")
        self.counts[sev] += 1
        self.findings.append((sev, name, detail))
        print(f"  {_SYM[sev]} [{sev:4}] {name}")
        if detail:
            print(f"           {detail[:240]}")

    def summary(self) -> None:
        print()
        print("=" * 70)
        bits = " ".join(f"{k}={self.counts[k]}" for k in _ORDER)
        print(f"{self.name}: {bits}")
        print("=" * 70)
        non_info = [f for f in self.findings if f[0] not in ("ok", "info")]
        if non_info:
            print(f"\n{len(non_info)} non-info findings:")
            for sev, name, detail in non_info:
                print(f"  [{sev}] {name}")
                if detail:
                    print(f"        {detail[:200]}")

    def exit_code(self) -> int:
        return 1 if sum(self.counts[g] for g in FAILING_GRADES) > 0 else 0


_TMP_HOMES: list[str] = []


def make_isolated_client(prefix: str = "bd_probe_"):
    """Create an isolated BD_HOME + return (client, bd_app, bd_db, home_path).

    Sets BD_HOME env var, chdirs into it, imports bulk_downloader, initialises
    the DB, returns the Flask test_client. Caller can issue requests immediately.
    """
    home = tempfile.mkdtemp(prefix=prefix)
    _TMP_HOMES.append(home)
    os.environ["BD_HOME"] = home
    os.chdir(home)

    import bulk_downloader.app as bd_app  # noqa: WPS433
    import bulk_downloader.db as bd_db    # noqa: WPS433
    bd_db.db_init()
    client = bd_app.app.test_client()
    bd_app.app.config["TESTING"] = True
    return client, bd_app, bd_db, home


def csrf_post(client, url: str, **kw):
    """POST with a fresh X-CSRF-Token, mimicking the JS fetch wrapper.

    Flask test_client is sticky on cookies; once GET / mints a bd_session
    the client carries it forward, and from then on every POST needs the
    CSRF header or the request gets 403'd. Real browser fetch wrapper
    handles this transparently — probes must too, or load tests just
    measure CSRF rejection.
    """
    r = client.get("/api/csrf")
    tok = ""
    if r.status_code == 200:
        try:
            tok = (r.get_json() or {}).get("csrf_token", "") or ""
        except Exception:
            tok = ""
    hdrs = dict(kw.pop("headers", {}) or {})
    if tok:
        hdrs["X-CSRF-Token"] = tok
    return client.post(url, headers=hdrs, **kw)


@atexit.register
def _cleanup() -> None:
    for home in _TMP_HOMES:
        try:
            shutil.rmtree(home, ignore_errors=True)
        except Exception:
            pass


def parse_flags(argv: Optional[list] = None) -> dict:
    """Parse a small set of common flags so probes don't each reinvent argparse.

    Recognised:
      --fast       — run a reduced iteration count where applicable
      --duration N — soak/stress run length in seconds
      --port N     — external scanner port (default 9876)
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    out = {"fast": False, "duration": 60, "port": 9876}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fast":
            out["fast"] = True
        elif a == "--duration" and i + 1 < len(argv):
            try:
                out["duration"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 1
        elif a == "--port" and i + 1 < len(argv):
            try:
                out["port"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 1
        elif a in ("-h", "--help"):
            print(f"Usage: {sys.argv[0]} [--fast] [--duration SECONDS] [--port N]")
            sys.exit(0)
        i += 1
    return out
