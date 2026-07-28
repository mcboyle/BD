#!/usr/bin/env python3
"""bd-body-contract -- controls that call the RIGHT endpoint with a body it REJECTS.

THE BUG CLASS THIS EXISTS FOR (found at 724, by hand):

    SiteActions rendered "Delete ALL jobs" behind a typed hard-confirm. It posted {} to
    /api/sites/<sid>/jobs/bulk_delete, which validates {urls: [...]} and answers
        400 "urls must be a non-empty list"
    It failed 100% of the time, AFTER the operator typed the confirmation. And EVERY GATE
    WE HAD SCORED IT AS WIRED, because every gate we had asks a question that cannot see it:

      endpoint_reachability : "does a control reach this endpoint?"   -> yes
      bd-fe-dead-control    : "does this control reach anything?"     -> yes
      test_gui_parity       : "is the route literal in the FE source?" -> yes

    Nobody asked: DOES THE BODY SATISFY THE CONTRACT? A control that calls the right route
    with the wrong body is a guaranteed 4xx and reads as reachable in every ledger we own.

HOW THIS ONE DOES NOT REPEAT THE MISTAKE:

    Three previous attempts to detect a soundness class by PATTERN MATCHING all failed, and
    all failed the same way -- the denominator did not contain the thing being asked about.
    (Twice on the wiring-gate sweep; once on a first pass at this, which "found" 7 dead
    controls, of which the first one checked -- selector_drift/reset -- takes no body at
    all. The 'needs' attribution was suffix-substring garbage.)

    So this tool does not INFER the contract. It EXECUTES it:

      1. resolve each FE call to the ACTUAL Flask rule, via the app's url_map -- not by
         string matching;
      2. replay the body the FRONTEND ACTUALLY SENDS against the real app;
      3. read the answer.

    A body-shape rejection is a 400 that names the body. Anything else (200/403/404/500)
    means the body was NOT the blocker, and the control is not dead in this way.

    UNKNOWN IS A THIRD STATE AND IT IS REPORTED. If a call cannot be replayed (the route
    never registers, the request dies before body validation), this says UNKNOWN. It does
    not say OK. An endpoint we could not ask about is not an endpoint that passed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

WORK = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
G, R, Y, DIM, BOLD, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"

MUTATORS = ("apiPost", "apiPut", "apiPatch", "apiDelete")

# An FE call site: the route template + the literal body keys it sends.
CALL = re.compile(
    r"(?P<fn>api(?:Post|Put|Patch|Delete))\s*(?:<[^>]*>)?\s*\(\s*"
    r"[`\"'](?P<path>[^`\"']+)[`\"']"
    r"(?:\s*,\s*(?P<body>\{[^{}]*\}|\{[^;]{0,200}))?",
    re.S,
)


def fe_calls(work):
    """Every mutating call site in the frontend, with the body keys it sends."""
    out = []
    for p in glob.glob(os.path.join(work, "frontend", "src", "**", "*.ts*"),
                       recursive=True):
        if p.endswith((".test.ts", ".test.tsx", ".d.ts")):
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        for m in CALL.finditer(src):
            body = m.group("body")
            if body is None:
                keys, shape = set(), "NO-BODY-ARG"
            else:
                b = body.strip()
                keys = set(re.findall(r"[{,]\s*([A-Za-z_]\w*)\s*[:,}]", b))
                keys |= set(re.findall(r"\.\.\.(\w+)", b))  # spread -> unknown extras
                shape = "{}" if re.match(r"^\{\s*\}$", b) else "keys"
            out.append({
                "file": os.path.relpath(p, work),
                "fn": m.group("fn"),
                "path": m.group("path"),
                "keys": sorted(keys),
                "shape": shape,
            })
    return out


def fe_path_to_probe(path):
    """`/api/sites/${encodeURIComponent(sid)}/bulk_pause` -> `/api/sites/_probe/bulk_pause`.

    Substituting a concrete value is what lets the request actually reach body validation.
    """
    if "${" not in path:
        return path, False
    # replace every ${...} with a probe token; nested parens are handled by counting
    out, i, n = [], 0, len(path)
    while i < n:
        if path.startswith("${", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if path[j] == "{":
                    depth += 1
                elif path[j] == "}":
                    depth -= 1
                j += 1
            out.append("_probe")
            i = j
        else:
            out.append(path[i])
            i += 1
    return "".join(out), True


def ts_calls(work):
    """Ask the TYPE CHECKER what body each control sends. Returns [] if unavailable."""
    import subprocess as sp

    fe = os.path.join(work, "frontend")
    script = os.path.join(fe, "scripts", "body_types.mjs")
    if not os.path.isfile(script):
        return []
    try:
        r = sp.run(["node", "scripts/body_types.mjs", "."], cwd=fe,
                   capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not r.stdout.strip():
            return []
        return json.loads(r.stdout)
    except Exception:
        return []


def probe_typed(work, tcalls):
    """DIFFERENTIAL PROBE -- the trick that makes value-plausibility irrelevant.

    The type checker gives us the KEYS a control sends, but not believable VALUES. Feeding
    synthetic values ("x", 1) into a value-validating endpoint yields a 400 that says
    nothing about whether the control is sound -- that mistake is exactly how an earlier
    pass "found" 99 dead controls, every one of them mine.

    So don't judge the 400. COMPARE TWO OF THEM:

        A = the body the control actually sends (type-directed sample)
        B = {}  -- the empty body

    If A and B are refused IDENTICALLY, the control's keys made NO DIFFERENCE to the
    endpoint -- it is missing what the endpoint requires, and no value could have saved it.
    That is a DEAD CONTROL, and it is the same finding as 724 and 726, now provable for
    bodies passed as typed variables.

    If A gets further than B, the keys ARE the right ones and the remaining complaint is
    about our placeholder value. That is not a defect in the control -- UNKNOWN, honestly.
    """
    import tempfile

    sys.path.insert(0, work)
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    scratch = tempfile.mkdtemp(prefix="bd_probe_")
    os.environ.setdefault("BD_HOME", scratch)
    prev = os.getcwd()
    os.chdir(scratch)
    try:
        return _probe_typed_inner(work, tcalls)
    finally:
        os.chdir(prev)


def _probe_typed_inner(work, tcalls):
    from unittest.mock import MagicMock

    from bulk_downloader.app import app
    import bulk_downloader.app as A

    probe_dir = os.path.join(os.environ.get("BD_HOME", "/tmp"), "_probe_dl")
    os.makedirs(probe_dir, exist_ok=True)
    try:
        A.runners["_probe"] = MagicMock()
        A.s_cfg["_probe"] = {"download_dir": probe_dir, "name": "_probe"}
    except Exception:
        pass

    c = app.test_client()
    c.get("/")
    tok = (c.get("/api/csrf").get_json() or {}).get("csrf_token") or ""
    hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok, "Content-Type": "application/json"}
    meth = {"apiPost": c.post, "apiPut": c.put, "apiPatch": c.patch, "apiDelete": c.delete}

    def hit(fn, path, body):
        r = meth[fn](path, json=body, headers=hdr)
        js = r.get_json(silent=True) or {}
        return r.status_code, str(js.get("error") or js.get("hint") or "")[:120]

    out = []
    for call in tcalls:
        p = call["path"].replace("${}", "_probe")
        if not p.startswith(("/api/", "/cockpit/api/")):
            continue
        if call["fn"] not in meth:
            continue
        if call.get("unknownType") or call.get("sample") is None:
            out.append({**call, "verdict": "UNKNOWN",
                        "why": "the checker could not resolve the body type"})
            continue
        try:
            a_code, a_err = hit(call["fn"], p, call["sample"])
            b_code, b_err = hit(call["fn"], p, {})
        except Exception as e:
            out.append({**call, "verdict": "UNKNOWN",
                        "why": f"probe raised {type(e).__name__}"})
            continue

        if a_code in (403,):
            v, why = "UNKNOWN", "403 -- blocked before body validation"
        elif a_code == 404:
            v, why = "UNKNOWN", "404 -- never reached body validation"
        elif not call["keys"] and b_code == 400:
            # THE PROVABLE RULE, and the only one that survives contact with reality.
            # The type checker says this control's body has NO KEYS, and the endpoint
            # refuses an empty body. No value could have saved it. This is the 724
            # ("Delete ALL jobs") and 726 ("Start import") shape, now detectable for
            # bodies passed as typed variables and not just as literal {}.
            v, why = "DEAD", f"body type has NO keys; endpoint refuses {{}} -> {b_err[:60]}"
        elif call["keys"]:
            # The control sends a NON-EMPTY, TYPE-CORRECT key set. That CLOSES the
            # empty-body class for it -- it is not the 724/726 bug.
            #
            # It does NOT mean the call succeeds: a 400 here is almost always our
            # SYNTHETIC VALUE ("x" is not a real site_id / filename / item_id), and a
            # differential probe cannot tell "missing key" from "invalid value" when the
            # endpoint reports both identically. It cannot: /api/queue/v2/cancel answers
            # "unknown site_id" to BOTH. Judging these needs REAL FIXTURES (a real site, a
            # real draft file), i.e. an integration harness -- not static or replay
            # analysis. Reporting them DEAD would be a lie; an earlier version of this rule
            # did exactly that, and flagged /api/tools/run, which we watched work live at
            # 719.
            v = "KEYS" if a_code == 400 else "OK"
            why = ("sends a type-correct key set (%s); %s"
                   % (",".join(call["keys"]),
                      "accepted" if a_code != 400 else
                      "400 on our synthetic VALUES -- needs real fixtures to judge"))
        elif a_code != 400:
            v, why = "OK", f"{a_code} -- body accepted"
        else:
            v, why = "UNKNOWN", f"400 and no resolved keys -> {a_err[:60]}"
        out.append({**call, "probe": p, "code": a_code, "verdict": v, "why": why})
    return out


def probe(work, calls, verbose=False):
    """Replay each call's body against the REAL app and read the answer.

    Booting the app WRITES runtime state (plugins/plugins.json, notify_apprise.json,
    cockpit_tasks/operator_state.json). Some of those paths are CWD-relative, not
    BD_HOME-relative -- so probing from inside the work tree DIRTIES THE SOURCE TREE, and
    bd-cut then packages the droppings into the release. (It did, at 726, before this.)
    A gate that corrupts the tree it is inspecting is not a gate. So: run from a scratch
    CWD, and put it back.
    """
    import tempfile

    sys.path.insert(0, work)
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    scratch = tempfile.mkdtemp(prefix="bd_probe_")
    os.environ.setdefault("BD_HOME", scratch)
    prev_cwd = os.getcwd()
    os.chdir(scratch)
    try:
        return _probe_inner(work, calls)
    finally:
        os.chdir(prev_cwd)


def _probe_inner(work, calls):
    from unittest.mock import MagicMock

    from bulk_downloader.app import app
    import bulk_downloader.app as A

    # A permissive stub runner, so <sid> routes get PAST the "site not found" 404 and
    # actually reach body validation. Without this the 404 masks the very thing we're
    # asking about -- another denominator that cannot see the question.
    #
    # And a REAL-ENOUGH CONFIG for that site. Some endpoints take no body at all and 400
    # on missing CONFIG (storage_tier/run_now: "download_dir not set"). That is a
    # config-dependent 400, NOT a body rejection -- and a probe with an unconfigured site
    # would report it as a dead control. (It did. That was a false positive, caught by
    # hand.) Configure the probe site so a 400 means what this tool says it means.
    probe_dir = os.path.join(os.environ.get("BD_HOME", "/tmp"), "_probe_dl")
    os.makedirs(probe_dir, exist_ok=True)
    try:
        A.runners["_probe"] = MagicMock()
        A.s_cfg["_probe"] = {"download_dir": probe_dir, "name": "_probe"}
    except Exception:
        pass

    c = app.test_client()
    c.get("/")
    tok = (c.get("/api/csrf").get_json() or {}).get("csrf_token") or ""
    hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok, "Content-Type": "application/json"}
    meth = {"apiPost": c.post, "apiPut": c.put, "apiPatch": c.patch, "apiDelete": c.delete}

    rules = {str(r) for r in app.url_map.iter_rules()}
    results = []
    for call in calls:
        p, _ = fe_path_to_probe(call["path"])
        if not p.startswith(("/api/", "/cockpit/api/")):
            continue
        # Send EXACTLY what the frontend sends: {} if it sends {}, else a shape with its keys.
        body = {} if call["shape"] == "{}" else {k: None for k in call["keys"]}
        if call["shape"] == "NO-BODY-ARG":
            body = {}
        try:
            r = meth[call["fn"]](p, json=body, headers=hdr)
            code, js = r.status_code, (r.get_json(silent=True) or {})
        except Exception as e:
            results.append({**call, "verdict": "UNKNOWN",
                            "why": f"probe raised {type(e).__name__}"})
            continue

        err = str(js.get("error") or js.get("hint") or "")
        # ONLY a LITERAL empty object is provable. "NO-BODY-ARG" means my parser saw no
        # object literal -- but the body is very often a typed VARIABLE
        # (`apiPost("/api/bulk/enqueue", req)`), which is perfectly correct and which this
        # parser cannot see into. Calling those DEAD produced 36 false positives on the
        # first honest run. They are UNKNOWN.
        sends_nothing = call["shape"] == "{}"

        if code == 404:
            # The route may not exist under this probe path, OR the handler 404'd before
            # body validation. Either way we did NOT get to ask the question.
            verdict, why = "UNKNOWN", "404 -- never reached body validation"
        elif code == 403:
            verdict, why = "UNKNOWN", "403 -- blocked before body validation (csrf/origin)"
        elif code == 400 and sends_nothing:
            # THE 724 SHAPE, and the only thing this tool can honestly prove: the control
            # sends NO BODY AT ALL and the endpoint demands one. There is no value we
            # could have supplied -- the call as written can never succeed.
            verdict, why = "DEAD", f"sends {call['shape']} -> 400: {err[:70]}"
        elif code == 400 and call["shape"] == "NO-BODY-ARG":
            verdict, why = "UNKNOWN", ("body is a variable, not a literal -- this parser "
                                       "cannot see its shape; needs type-aware analysis")
        elif code == 400:
            # The control DOES send keys and still got a 400 -- but so would anything,
            # because THIS PROBE FILLS EVERY KEY WITH None. A 400 here is my placeholder
            # being rejected, not proof the control is broken. Saying DEAD would be a lie
            # (the first draft of this tool said exactly that, 99 times).
            #
            # Judging it needs real values, which we cannot synthesize. So: UNKNOWN.
            # An endpoint we could not honestly ask about is not one that passed.
            verdict, why = "UNKNOWN", (f"400 on placeholder values ({','.join(call['keys']) or '-'}) "
                                       f"-- needs real values to judge")
        else:
            verdict, why = "OK", f"{code} -- body accepted"
        results.append({**call, "probe": p, "code": code, "verdict": verdict, "why": why})
    return results


def probe_fixtures(work, tcalls):
    """v3.66.729 -- THE FIXTURE-BACKED PROBE. Replay against a world that EXISTS.

    probe_typed() replays the body a control sends against an EMPTY world: site id
    "_probe", task_id "x", a filename that is on no disk. So the endpoint answers
    "unknown site_id" / "no such task" and the tool -- honestly -- says UNKNOWN. It
    cannot tell a broken control from an absent fixture. That is the ceiling of
    replay analysis, and this function is what lies above it.

    It stands up a real world (a site, real `queue` rows, real library/history rows,
    a real file) and replays into it. 53 call sites become decisively OK, and the
    UNKNOWNs that remain are NAMED rather than assumed.

    THE SOUNDNESS GUARD -- read before touching the DEAD rule.
      Building this produced 38 FALSE DEAD verdicts across five distinct mechanisms,
      every one of them plausible enough to ship as a bug report:
        1. Trusting the literal parser's NO-BODY-ARG shape. It does not mean "sends no
           body" -- apiPost's payload is a REQUIRED positional. It means "the regex
           could not parse a {...} literal", and it fires on `{ site_id, url }`.  (16)
        2. The differential rule A==B => DEAD, when OUR value is invalid. "unknown
           site_id" is the answer to both a MISSING key and a PRESENT-but-unregistered
           one. Same string, opposite meanings.                                    (13)
        3. Replaying 126 MUTATING calls against ONE shared world. apiDelete("/api/
           sites/${}") fires, and every later /api/sites/<sid>/* call 404s. Verdicts
           became a function of replay ORDER. Hence ensure() before every probe.
        4. A type-correct, MEANING-WRONG fixture: `text` for /api/import/start is the
           operator's pasted URL LIST, not prose. "fixture text" has no URLs -> "no
           valid URLs" -> identical to {} -> "DEAD". That control was FIXED at 726.
        5. A stub runner that invents attributes. Handing the app a function where it
           expects a dict yields a 500 the product never had.                       (4)

      So: a RESOURCE/VALUE complaint, or an empty error message, can NEVER return DEAD.
      It returns FIXTURE-GAP -- a named, countable admission that our world is too thin
      to judge this endpoint. A to-do list, not a verdict. And a 5xx is HARNESS-FAULT,
      never a product finding: the app must not be blamed for choking on our fiction.
    """
    import tempfile

    sys.path.insert(0, work)
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    scratch = tempfile.mkdtemp(prefix="bd_fxprobe_")
    prev_home = os.environ.get("BD_HOME")
    os.environ["BD_HOME"] = scratch
    prev = os.getcwd()
    os.chdir(scratch)
    # HERMETIC OR MEANINGLESS.
    #
    # This gate isolates every CALL SITE from the one before it (ensure()), and then
    # the first band run promptly failed because it did not isolate ITSELF from the
    # test FILE before it: 726's probe leaves its own "_probe" site in the module-level
    # s_cfg, and probing a dirty world raised UNKNOWN above the ratchet. Standalone the
    # count was exactly 64; under a single-boot band it was not. Same bug as the shared
    # world, one level up -- a verdict that depends on what ran first is not a verdict.
    #
    # So snapshot the app's module-level state, wipe it, and restore it afterwards.
    _saved = None
    _saved_app_cfg = None
    try:
        import bulk_downloader.app as _A
        _saved = ({k: dict(v) if isinstance(v, dict) else v
                   for k, v in _A.s_cfg.items()},
                  dict(_A.s_meta), dict(_A.runners))
        # v3.66.750 -- _app_cfg is module-level state exactly like s_cfg,
        # and a replayed settings probe mutates it (path_allowlist -> ["x"]).
        # Without this restore, the SECOND probe_fixtures() call in a
        # process inherits the first call's poisoned config and setup_site
        # flaps OK -> UNKNOWN (the ratchet's old +1 tolerance).
        _saved_app_cfg = dict(getattr(_A, "_app_cfg", {}) or {})
        _A.s_cfg.clear()
        _A.s_meta.clear()
        _A.runners.clear()
    except Exception:
        pass
    try:
        return _probe_fixtures_inner(work, tcalls, scratch)
    finally:
        os.chdir(prev)
        if prev_home is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = prev_home
        if _saved is not None:
            try:
                import bulk_downloader.app as _A
                _A.s_cfg.clear()
                _A.s_cfg.update(_saved[0])
                _A.s_meta.clear()
                _A.s_meta.update(_saved[1])
                _A.runners.clear()
                _A.runners.update(_saved[2])
                if _saved_app_cfg is not None:
                    _cfg = getattr(_A, "_app_cfg", None)
                    if _cfg is not None:
                        _cfg.clear()
                        _cfg.update(_saved_app_cfg)
            except Exception:
                pass


# A 400 whose message names a RESOURCE or a VALUE cannot prove a dead control.
_RESOURCEISH = (
    "unknown", "no such", "not found", "invalid", "not pending", "does not exist",
    "doesn't use", "no parser", "not registered", "register first",
    "no download_dir", "out of range", "no valid", "empty", "missing",
)

# INFRASTRUCTURE failures are not verdicts about a control. A broken database
# answers 400 "db error: OperationalError" to EVERY body, including {} -- so the
# differential rule sees an identical refusal and pronounces the control DEAD.
# That is a dead control manufactured out of a broken fixture, and it is the most
# dangerous false positive of the lot: it looks exactly like a real one. An infra
# error can only ever be a HARNESS-FAULT, which FAILS the gate loudly rather than
# quietly libelling a control.
_INFRA = (
    "db error", "operationalerror", "no such table", "database is locked",
    "unable to open database", "integrityerror", "harness-fault",
)


def _probe_fixtures_inner(work, tcalls, home):
    from bulk_downloader.db import db_init
    db_init()
    # db_init() creates the BASE tables. `library`, `tags`, `library_tags` and friends
    # are created by MIGRATIONS, which run once at app import -- against whatever
    # BD_HOME existed then, not ours. So in a fresh scratch DB those tables are simply
    # absent, every /api/library/* call answers "db error: OperationalError", the
    # differential rule sees an identical refusal for {} and for a real body, and it
    # pronounces the control DEAD. A dead control conjured out of a missing table.
    # Apply them here, so the fixture world is the world the app expects.
    try:
        from bulk_downloader import migrations
        migrations.apply_pending(backup_first=False)
    except Exception:
        pass
    os.makedirs(os.path.join(home, "screenshots"), exist_ok=True)

    import bulk_downloader.app as A
    from bulk_downloader.app import app
    from tools.body_contract_fixtures import Fixtures

    app.config["TESTING"] = True
    c = app.test_client()
    fx = Fixtures(A, c, home).build()

    # THE WORLD MUST EXIST BEFORE WE JUDGE ANYTHING IN IT.
    # If the DB has no tables, every endpoint answers "db error" -- to a real body and
    # to {} alike -- and the differential rule reads that identical refusal as proof
    # of a dead control. The gate would then report DEAD controls with total
    # confidence, and the cause would be a missing table. Fail loudly instead.
    try:
        from bulk_downloader.db import db_conn
        with db_conn() as _cx:
            for _t in ("queue", "history", "library"):
                _cx.execute("SELECT 1 FROM %s LIMIT 1" % _t)
    except Exception as e:
        raise RuntimeError(
            "the fixture world did not build (%s: %s). Refusing to emit verdicts: "
            "against a broken DB every control looks DEAD, and every one of them "
            "would be a lie." % (type(e).__name__, e))

    tok = fx.csrf()
    hdr = {"X-CSRFToken": tok, "X-CSRF-Token": tok,
           "Content-Type": "application/json"}
    meth = {"apiPost": c.post, "apiPut": c.put,
            "apiPatch": c.patch, "apiDelete": c.delete}

    def hit(fn, path, body):
        try:
            r = meth[fn](path, json=body, headers=hdr)
        except Exception as e:
            # An exception escaping the test client is OURS -- never a verdict.
            return 599, "HARNESS-FAULT %s" % type(e).__name__
        js = r.get_json(silent=True) or {}
        return r.status_code, str(js.get("error") or js.get("hint") or "")[:100]

    out = []
    for call in tcalls:
        fn, raw = call["fn"], call["path"]
        if fn not in meth or not raw.startswith(("/api/", "/cockpit/api/")):
            continue
        fx.ensure()                      # every call site is INDEPENDENT
        path = fx.probe_path(raw)

        if call.get("unknownType") or call.get("sample") is None:
            out.append({**call, "probe": path, "code": None, "verdict": "UNKNOWN",
                        "why": "type checker could not resolve the body"})
            continue

        body, missing = fx.resolve(call["sample"], path=raw)
        a_code, a_err = hit(fn, path, body)
        fx.ensure()                      # A was a MUTATOR; B must see A's world
        # The empty comparator also receives path-specific inert safety fields.
        # Some operator endpoints default an omitted key to launching a real,
        # detached workload, so B must pass through the resolver just like A.
        b_body, _ = fx.resolve({}, path=raw)
        b_code, b_err = hit(fn, path, b_body)

        resourceish = (not a_err.strip()
                       or any(s in a_err.lower() for s in _RESOURCEISH))
        infra = any(s in a_err.lower() for s in _INFRA)

        if a_code >= 500:
            v, why = "HARNESS-FAULT", "app 5xx'd on OUR fixture -> %s" % a_err[:60]
        elif a_code < 400:
            # A 2xx is a SUCCESS, whatever text the body happens to carry. Checking
            # the infra-substrings BEFORE this flagged 200s as harness faults purely
            # because their `hint` field contained a matching word. Status first,
            # prose second.
            v, why = "OK", "real-fixture body accepted (%d)" % a_code
        elif infra:
            # Only now: a 4xx whose message names an INFRASTRUCTURE failure. A broken
            # database refuses every body identically, and the differential rule reads
            # that as proof of a dead control -- manufacturing a DEAD out of a missing
            # table. Never a verdict.
            v, why = "HARNESS-FAULT", "infra error on OUR fixture -> %s" % a_err[:60]
        elif a_code == 403:
            v, why = "UNKNOWN", "403 -- blocked before body validation"
        elif a_code == 404:
            v, why = "UNKNOWN", "404 -- resource missing even with fixtures"
        elif not call["keys"] and b_code == 400:
            # THE ONLY PROVABLE DEAD: no keys, and the endpoint refuses {}.
            v, why = "DEAD", "no keys; endpoint refuses {} -> %s" % b_err[:60]
        elif missing:
            v, why = ("UNKNOWN", "400; %d key(s) UNRESOLVED by fixtures (%s)"
                      % (len(missing), ",".join(sorted(missing))[:40]))
        elif resourceish:
            v, why = "FIXTURE-GAP", "400 on a RESOURCE/VALUE complaint -> %s" % a_err[:60]
        elif a_code == 400 and (a_code, a_err) == (b_code, b_err):
            v, why = "DEAD", "body refused IDENTICALLY to {} -> %s" % a_err[:60]
        else:
            v, why = ("UNKNOWN",
                      "400, fully resolved, non-resource -> %s" % a_err[:60])
        out.append({**call, "probe": path, "code": a_code, "verdict": v,
                    "why": why, "unresolved": sorted(missing)})
    return out


def load_calls(work):
    """The typed call sites, from the COMMITTED artifact -- never from node.

    v3.66.729: the first version of this gate called ts_calls(), which shells out to
    `node scripts/body_types.mjs`. That needs frontend/node_modules, which is NOT in
    the release zip -- so in the band the extractor returned nothing, the tests
    SKIPPED, and a skip reads as green. The body-contract gate would have shipped
    unable to see, in the very cut whose thesis is that a gate which cannot see is
    worse than no gate.

    So the call sites are a DERIVED, COMMITTED artifact -- exactly like ROUTE_INDEX
    and ENDPOINT_CATALOG -- regenerated by tools/body_contract.py --regen and held in
    sync by test_body_contract_calls_in_sync. The gate reads the artifact and always
    runs. Node is needed to REGENERATE it, never to ENFORCE it.
    """
    p = os.path.join(work, "tools", "BODY_CONTRACT_CALLS.json")
    with open(p) as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(prog="bd-body-contract")
    ap.add_argument("--work", default=WORK)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="show OK + UNKNOWN too")
    ap.add_argument("--regen", action="store_true",
                    help="regenerate tools/BODY_CONTRACT_CALLS.json (needs node)")
    ap.add_argument("--fixtures", action="store_true",
                    help="replay against a REAL world (v3.66.729)")
    a = ap.parse_args()

    if a.regen:
        calls = ts_calls(a.work)
        if not calls:
            print("the TS body extractor produced nothing (needs node + "
                  "frontend/node_modules)", file=sys.stderr)
            return 2
        calls = sorted(calls, key=lambda c: (c["file"], c["fn"], c["path"]))
        out = os.path.join(a.work, "tools", "BODY_CONTRACT_CALLS.json")
        with open(out, "w") as fh:
            json.dump(calls, fh, indent=1, sort_keys=True)
        print("wrote %s (%d call sites)" % (out, len(calls)))
        return 0

    if a.fixtures:
        res = probe_fixtures(a.work, load_calls(a.work))
        dead = [r for r in res if r["verdict"] == "DEAD"]
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            import collections as _c
            for k, v in sorted(_c.Counter(r["verdict"] for r in res).items()):
                print("  %-14s %3d" % (k, v))
        return 1 if dead else 0

    calls = fe_calls(a.work)
    res = probe(a.work, calls)
    dead = [r for r in res if r["verdict"] == "DEAD"]
    unk = [r for r in res if r["verdict"] == "UNKNOWN"]
    ok = [r for r in res if r["verdict"] == "OK"]

    if a.json:
        print(json.dumps({"dead": dead, "unknown": unk, "ok_count": len(ok)}, indent=1))
        return 1 if dead else 0

    print(f"{BOLD}== bd-body-contract :: {len(res)} mutating call sites replayed =={RST}")
    print(f"  {G}OK{RST}      {len(ok):3d}  body accepted by the real endpoint")
    print(f"  {R}DEAD{RST}    {len(dead):3d}  400 -- the endpoint REJECTED the body the control sends")
    print(f"  {Y}UNKNOWN{RST} {len(unk):3d}  could not reach body validation (NOT a pass)")
    if dead:
        print(f"\n{R}DEAD CONTROLS{RST} -- these call the right route and are refused:")
        for r in dead:
            print(f"  {r['path']}")
            print(f"    sends={r['shape']}{r['keys'] or ''}  {DIM}{r['file']}{RST}")
            print(f"    {R}{r['why']}{RST}")
    if a.all and unk:
        print(f"\n{Y}UNKNOWN{RST} (an endpoint we could not ask about is not one that passed):")
        for r in unk:
            print(f"  {r['path']:56s} {DIM}{r['why']}{RST}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
