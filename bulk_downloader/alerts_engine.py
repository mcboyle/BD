"""Alerts engine (Phase 146, Block Q).

Rule-based alerting that watches metric values and fires notifications
when conditions hold. Complements Prometheus/Grafana for operators
who don't run external monitoring — BD's own UI can show "5 alerts
firing now" without external infra.

Rule format:
  {
    id: "high_failure_rate",
    name: "High failure rate",
    metric: "bd_failure_rate_1h",
    op: ">=",
    threshold: 25.0,
    duration_minutes: 5,        # condition must hold this long
    severity: "warn",            # info | warn | fail
    cooldown_minutes: 60,        # don't re-fire within this window
    actions: ["webhook", "dashboard"]
  }

Built-in metrics evaluated:
  • bd_failure_rate_1h        % failed jobs in last hour
  • bd_pending_count          number of pending jobs
  • bd_disk_free_gb           min free GB across configured dirs
  • bd_circuit_breakers_open  count of tripped circuits
  • bd_account_health_min     min account health score
  • bd_bitrot_open_issues     open integrity issues
  • bd_oldest_pending_hours   age of oldest pending job
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import time
from typing import Optional


# ─── Built-in rules (operator can override / disable per rule) ────────

DEFAULT_RULES = [
    {
        "id": "high_failure_rate",
        "name": "High failure rate",
        "metric": "bd_failure_rate_1h",
        "op": ">=", "threshold": 25.0,
        "duration_minutes": 5, "severity": "warn",
        "cooldown_minutes": 60,
    },
    {
        "id": "queue_backlog",
        "name": "Queue backlog growing",
        "metric": "bd_pending_count",
        "op": ">=", "threshold": 500,
        "duration_minutes": 30, "severity": "info",
        "cooldown_minutes": 120,
    },
    {
        "id": "disk_low",
        "name": "Disk space low",
        "metric": "bd_disk_free_gb",
        "op": "<=", "threshold": 10.0,
        "duration_minutes": 1, "severity": "fail",
        "cooldown_minutes": 60,
    },
    {
        "id": "circuits_open",
        "name": "Multiple circuits open",
        "metric": "bd_circuit_breakers_open",
        "op": ">=", "threshold": 3,
        "duration_minutes": 5, "severity": "warn",
        "cooldown_minutes": 30,
    },
    {
        "id": "bitrot_growing",
        "name": "Bit-rot issues open",
        "metric": "bd_bitrot_open_issues",
        "op": ">=", "threshold": 5,
        "duration_minutes": 1, "severity": "warn",
        "cooldown_minutes": 720,
    },
    {
        "id": "stale_pending",
        "name": "Stale pending jobs",
        "metric": "bd_oldest_pending_hours",
        "op": ">=", "threshold": 24,
        "duration_minutes": 1, "severity": "info",
        "cooldown_minutes": 360,
    },
]


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS alert_rules(
                rule_id TEXT PRIMARY KEY,
                rule_json TEXT NOT NULL,
                enabled INTEGER DEFAULT 1
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS alert_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT NOT NULL,
                ts REAL NOT NULL,
                metric_value REAL,
                kind TEXT NOT NULL,
                message TEXT
            )""")
    except Exception as e:
        sys.stderr.write(f"[alerts] schema: {e}\n")


# ─── Metric evaluators ────────────────────────────────────────────────

def _evaluate_metric(name: str, *, s_cfg: Optional[dict] = None) -> Optional[float]:
    """Return current value of a built-in metric, or None if not
    computable."""
    try:
        if name == "bd_failure_rate_1h":
            # history.ts is TEXT `YYYY-MM-DDTHH:MM:SS`, compared as text. A bare
            # datetime('now', ...) renders `YYYY-MM-DD HH:MM:SS`; 'T' sorts
            # after ' ', so that bound admitted every row of the same UTC date
            # and "last hour" measured the whole day. The bound is rendered in
            # the stored format (here and in bd_job_failures_1h).
            from . import db as _db
            with _db.db_conn() as cx:
                r = cx.execute("""SELECT
                      SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS d,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS f
                    FROM history
                    WHERE ts >= strftime('%Y-%m-%dT%H:%M:%S',
                                         datetime('now', '-1 hour'))""").fetchone()
            d, f = int(r[0] or 0), int(r[1] or 0)
            if d + f == 0:
                return 0.0
            return 100 * f / (d + f)
        if name == "bd_job_failures_1h":
            # Cut 8 job-lifecycle metric: raw COUNT of failed jobs in the
            # last hour (vs the rate above). Rides the existing polled
            # evaluate() path -- no event-bus subscription needed.
            from . import db as _db
            with _db.db_conn() as cx:
                r = cx.execute("""SELECT COUNT(*) FROM history
                    WHERE status='failed'
                      AND ts >= strftime('%Y-%m-%dT%H:%M:%S',
                                         datetime('now', '-1 hour'))""").fetchone()
            return float(r[0] or 0)
        if name == "bd_pending_count":
            from . import db as _db
            with _db.db_conn() as cx:
                # Pending jobs live in `queue`; `history` only ever holds
                # finished rows, so counting it there read 0 forever.
                r = cx.execute("""SELECT COUNT(*) FROM queue
                                   WHERE status = 'pending'""").fetchone()
            return float(r[0] or 0)
        if name == "bd_disk_free_gb":
            if not s_cfg:
                return None
            free_gbs = []
            for cfg in s_cfg.values():
                d = (cfg or {}).get("download_dir", "")
                if d:
                    try:
                        usage = shutil.disk_usage(d)
                        free_gbs.append(usage.free / (1024**3))
                    except OSError:
                        pass
            if not free_gbs:
                return None
            return min(free_gbs)
        if name == "bd_circuit_breakers_open":
            from . import circuit_breaker as _cb
            rep = _cb.report()
            return float(sum(1 for s in rep.values()
                             if s.get("state") == "open"))
        if name == "bd_account_health_min":
            from . import account_health as _ah
            rows = _ah.report_all()
            if not rows:
                return 100.0
            return float(min(r.get("score", 100) for r in rows))
        if name == "bd_bitrot_open_issues":
            from . import bitrot as _br
            return float(_br.stats().get("open_issues", 0))
        if name == "bd_oldest_pending_hours":
            from . import db as _db
            with _db.db_conn() as cx:
                # 'now' is read here, not in SQL: a clock read inside the
                # statement can never be shadow-compared (row 127).
                r = cx.execute("""SELECT
                  CAST(strftime('%s', MIN(ts_added)) AS INTEGER)
                  FROM queue WHERE status = 'pending'""").fetchone()
            return ((time.time() - r[0]) / 3600.0
                    if r and r[0] is not None else 0.0)
    except Exception as e:
        if type(e).__name__ == "OperationalError" and (
                "no such table: history" in str(e).lower()
                or "no such table: queue" in str(e).lower()):
            return 0.0
        sys.stderr.write(f"[alerts] metric {name}: {e}\n")
        return None
    return None


_OFF_WORDS = {"0", "false", "off", "no", "disabled"}


def _rule_enabled(rule: dict) -> bool:
    """False when the operator switched the rule off (JSON key `enabled`, or
    the alert_rules.enabled column, which _merged_rules folds into the key)."""
    v = rule.get("enabled", True)
    if isinstance(v, str):
        return v.strip().lower() not in _OFF_WORDS
    return v is None or bool(v)


def _minutes(rule: dict, key: str, default: int) -> int:
    """A rule's whole, finite, non-negative minute count. Raises ValueError."""
    v = rule.get(key, default)
    if isinstance(v, bool) or v is None:
        raise ValueError(f"bad {key}: {v!r}")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"bad {key}: {v!r}") from None
    if not math.isfinite(f) or f < 0:
        raise ValueError(f"bad {key}: {v!r}")
    return int(f)


def _json_safe(rule: dict) -> dict:
    """`rule` with non-finite floats replaced by None: NaN / Infinity are not
    JSON, and one such stored value made every rules response unparseable."""
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in rule.items()}


_OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# ─── Rule evaluation ──────────────────────────────────────────────────

class AlertEventStoreUnavailable(RuntimeError):
    """The alert_events table could not be read or written.

    ROW 421. This state used to be indistinguishable from a measured
    answer: ``_condition_was_held`` returned False on ANY exception and
    ``_record_event`` swallowed its own INSERT failure, so a locked,
    corrupt, unwritable or malformed-``ts`` table made the duration gate
    answer False forever, ``fired`` stayed False, and ``_fire_actions``
    never ran. Nothing raised and nothing logged.

    That is the fail-open shape CLAUDE.md A7 forbids, and it is aimed at
    operator truth: the disk-full, failure-rate and account-health rules
    exist to warn that the machinery is degrading, and precisely when the
    database -- the substrate those alerts watch -- is failing, the alerting
    layer reported nothing rather than UNKNOWN.

    ``False`` from ``_condition_was_held`` remains a MEASURED answer (no such
    event, or one that is too young). Only an unavailable store raises.
    """


def _condition_was_held(rule_id: str, *, duration_minutes: int) -> bool:
    """Check if the rule has been continuously tripping for at least
    `duration_minutes`. We approximate this by looking at our most
    recent 'tripped' event; if it's older than duration_minutes ago,
    and no 'cleared' in between, we say the condition held."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT kind, ts FROM alert_events
                              WHERE rule_id = ?
                              ORDER BY id DESC LIMIT 1""",
                          (rule_id,)).fetchone()
        if not r:
            return False
        kind = r[0] if not hasattr(r, "keys") else r["kind"]
        ts = float(r[1] if not hasattr(r, "keys") else r["ts"])
        if kind != "tripped":
            return False
        # Has it held long enough?
        return (time.time() - ts) >= (duration_minutes * 60)
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unreadable for {rule_id}: "
            f"{type(e).__name__}: {e}") from e


def _last_fire_age(rule_id: str) -> Optional[float]:
    """Seconds since last 'fired' event for this rule."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT ts FROM alert_events
                              WHERE rule_id = ? AND kind = 'fired'
                              ORDER BY id DESC LIMIT 1""",
                          (rule_id,)).fetchone()
        if not r:
            return None
        return time.time() - float(r[0])
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unreadable for {rule_id}: "
            f"{type(e).__name__}: {e}") from e


def _record_event(rule_id: str, kind: str, value: Optional[float],
                 message: str = ""):
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO alert_events(
                rule_id, ts, metric_value, kind, message
            ) VALUES (?,?,?,?,?)""",
                (rule_id, time.time(), value, kind, str(message)[:300]))
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unwritable for {rule_id} ({kind}): "
            f"{type(e).__name__}: {e}") from e


def evaluate(s_cfg: Optional[dict] = None,
            *, rules: Optional[list] = None) -> dict:
    """Run all rules. For each, evaluate metric, compare to threshold,
    track state, fire if condition has held and cooldown elapsed.

    Returns:
      {evaluated: N, tripping: N, fired: N, unknown: N, results: [...]}

    ROW 421. ``unknown`` counts rules whose event store could not be read or
    written. Such a rule reports a DISTINCT per-rule error state carried in
    its result and written to stderr -- never a silent ``fired: False``,
    which is what an unavailable alert_events table used to produce.

    It deliberately does NOT fire on an unavailable store: a fire that cannot
    be recorded cannot be cooldown-gated either, so it would re-fire every 60
    seconds for as long as the outage lasted. UNKNOWN is the honest answer,
    and it is now visible to the operator through /api/alerts/evaluate and,
    past the scheduler wrapper, through /api/bg/status.
    """
    out = {"evaluated": 0, "tripping": 0, "fired": 0, "unknown": 0,
           "results": []}
    if rules is None:
        # Saved custom rules ride the default pass. A store that cannot be
        # read is reported as UNKNOWN (row 421), never as "no custom rules".
        rules, store_error, skipped = _merged_rules()
        if store_error:
            out["unknown"] += 1
            out["results"].append({"rule_id": "custom_rules", "metric": "",
                                   "value": None, "threshold": None,
                                   "tripping": False, "fired": False,
                                   "error": store_error})
        # F039: a malformed saved row is not evaluated, and not silent either.
        for rid, problem in skipped.items():
            out["unknown"] += 1
            out["results"].append({"rule_id": rid, "metric": "",
                                   "value": None, "threshold": None,
                                   "tripping": False, "fired": False,
                                   "error": f"malformed rule skipped: {problem}"})
    for rule in rules:
        metric = rule.get("metric", "")
        if not _rule_enabled(rule):
            # Switched off by the operator: not evaluated, never fires.
            out["results"].append({"rule_id": rule.get("id"), "metric": metric,
                                   "value": None, "threshold": None,
                                   "tripping": False, "fired": False,
                                   "enabled": False})
            continue
        out["evaluated"] += 1
        value = _evaluate_metric(metric, s_cfg=s_cfg)
        result: dict = {"rule_id": rule.get("id"), "metric": metric,
                       "value": value,
                       "threshold": _json_safe(rule).get("threshold"),
                       "tripping": False, "fired": False}
        try:
            # Parsed per RULE and before any state is touched: one stored rule
            # with a bad field used to raise out of the whole pass, so no rule
            # after it was evaluated on any 60 s tick.
            rule_id = rule["id"]
            duration = _minutes(rule, "duration_minutes", 1)
            cooldown = _minutes(rule, "cooldown_minutes", 60)
        except (KeyError, ValueError) as e:
            result["error"] = (str(e) if isinstance(e, ValueError)
                               else "rule has no id")
            out["results"].append(result)
            continue
        if value is None:
            result["error"] = "metric unavailable"
            out["results"].append(result)
            continue
        op_fn = _OPS.get(rule.get("op", ">="))
        if not op_fn:
            result["error"] = f"unknown op: {rule.get('op')}"
            out["results"].append(result)
            continue
        try:
            threshold = float(rule["threshold"])
            if not math.isfinite(threshold):
                raise ValueError("not finite")
        except (KeyError, TypeError, ValueError):
            result["error"] = f"bad threshold: {rule.get('threshold')!r}"
            out["results"].append(result)
            continue
        tripping = op_fn(value, threshold)
        result["tripping"] = tripping
        if tripping:
            out["tripping"] += 1
        try:
            if tripping:
                # Was a previous trip recorded? If not, record one now.
                if not _condition_was_held(rule["id"],
                                           duration_minutes=0):
                    _record_event(rule["id"], "tripped", value,
                                 f"{metric}={value} {rule.get('op', '>=')} {threshold}")
                # Has it been tripping long enough?
                if _condition_was_held(
                        rule["id"],
                        duration_minutes=duration):
                    # Cooldown gate
                    last_fire = _last_fire_age(rule["id"])
                    cool = cooldown * 60
                    if last_fire is None or last_fire >= cool:
                        _record_event(rule["id"], "fired", value,
                                     str(rule.get("name") or rule_id))
                        result["fired"] = True
                        out["fired"] += 1
                        # Fire side effects
                        _fire_actions(rule, value)
            else:
                # Condition has cleared — record so duration logic resets
                if _condition_was_held(rule["id"], duration_minutes=0):
                    _record_event(rule["id"], "cleared", value, "")
        except AlertEventStoreUnavailable as e:
            # Row 421: UNKNOWN, named, counted and logged -- not silence.
            # Caught per RULE so one broken row cannot hide the others'
            # measured answers.
            result["store_unavailable"] = True
            result["error"] = f"alert event store unavailable: {e}"
            out["unknown"] += 1
            sys.stderr.write(
                f"[alerts] {rule['id']}: event store unavailable, so this "
                f"rule's state is UNKNOWN rather than clear: {e}\n")
        out["results"].append(result)
    return out


def _fire_actions(rule: dict, value: float):
    """Dispatch alert side effects (webhook, log, etc.)."""
    actions = rule.get("actions") or ["dashboard"]
    if "webhook" in actions:
        try:
            from . import webhooks as _wh
            _wh.fire("alert.fired", {
                "rule_id": rule["id"],
                "name": rule.get("name", ""),
                "metric": rule.get("metric"),
                "value": value,
                "threshold": rule.get("threshold"),
                "severity": rule.get("severity", "warn"),
                "ts": time.time(),
            })
        except Exception:
            pass


def active_alerts(*, lookback_hours: int = 24) -> list:
    """All alerts that fired in the last `lookback_hours`.

    Raises AlertEventStoreUnavailable when alert_events cannot be read. It
    used to answer [] -- "nothing is firing" -- for a locked or broken store,
    the row 421 fail-open shape evaluate() no longer has."""
    _ensure_tables()
    cutoff = time.time() - lookback_hours * 3600
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM alert_events
                                  WHERE ts >= ? AND kind = 'fired'
                                  ORDER BY id DESC""", (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unreadable: {type(e).__name__}: {e}") from e


def _rule_problem(rule) -> Optional[str]:
    """Why an effective (defaults-merged) rule cannot be listed or
    evaluated, else None. Checks the fields the /alerts table renders and
    evaluate() reads, so one bad stored row cannot break either (F039)."""
    if not isinstance(rule, dict):
        return "rule_json is not a JSON object"
    key = _non_finite_key(rule)
    if key is not None:
        return f"non-finite number in {key!r}"
    metric = rule.get("metric")
    if not isinstance(metric, str) or metric not in _KNOWN_METRICS:
        return f"unknown metric {metric!r}"
    # no default: evaluate() reads rule["op"] once a rule trips, so a custom
    # row without one is malformed (built-ins get theirs from the defaults)
    op = rule.get("op")
    if not isinstance(op, str) or op not in _VALID_OPS:
        return f"invalid op {op!r}"
    threshold = rule.get("threshold")
    try:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float, str)):
            raise TypeError
        finite = math.isfinite(float(threshold))
    except (TypeError, ValueError, OverflowError):
        finite = False
    # c5884f8a0 (na-fix F4): a stored NaN / Infinity threshold still lists
    # (rules_report sends it as null) and evaluate() reports "bad threshold"
    if not finite and not isinstance(threshold, float):
        # evaluate()'s own wording for a threshold it cannot use
        return f"bad threshold: {threshold!r}"
    if not isinstance(rule.get("name", ""), str):
        return "name is not a string"
    # evaluate() int()s these and tests actions with "in" once a rule trips
    for key in ("duration_minutes", "cooldown_minutes"):
        if key in rule:
            try:
                int(rule[key])
            except (TypeError, ValueError, OverflowError):
                return f"bad {key}: {rule[key]!r}"
    actions = rule.get("actions")
    if actions and not isinstance(actions, (list, str, dict)):
        return f"bad actions: {actions!r}"
    return None


def _non_finite_key(rule: dict) -> Optional[str]:
    """First top-level key (threshold aside) whose value holds a NaN /
    Infinity float, else None: jsonify writes those as bare tokens the SPA
    cannot parse, and _json_safe only nulls top-level values."""
    for key, value in rule.items():
        if key == "threshold":
            continue
        stack = [value]
        while stack:
            v = stack.pop()
            if isinstance(v, float) and not math.isfinite(v):
                return str(key)
            if isinstance(v, dict):
                stack.extend(v.values())
            elif isinstance(v, list):
                stack.extend(v)
    return None


def _decode_rule_rows(rows) -> tuple:
    """({rule_id: stored rule}, {rule_id: problem}) from alert_rules rows.

    Decodes row by row: a malformed row is skipped and reported on stderr
    instead of dropping every saved rule (F039). A bad override of a
    built-in is skipped too, so the built-in keeps its default fields."""
    defaults = {rule["id"]: rule for rule in DEFAULT_RULES}
    custom, skipped = {}, {}
    for r in rows:
        rid = r[0] if not hasattr(r, "keys") else r["rule_id"]
        try:
            rule = json.loads(r[1] if not hasattr(r, "keys") else r["rule_json"])
        except (TypeError, ValueError, RecursionError):
            rule = None
        if not isinstance(rid, str) or not rid:
            problem = "rule_id is not a non-empty string"
        elif rule is None:
            problem = "rule_json is not valid JSON"
        elif isinstance(rule, dict) and rule.get("id", rid) != rid:
            problem = f"id {rule.get('id')!r} does not match rule_id"
        else:
            effective = {**defaults[rid], **rule} if (
                rid in defaults and isinstance(rule, dict)) else rule
            problem = _rule_problem(effective)
        if problem:
            sys.stderr.write(
                f"[alerts] skipping malformed alert_rules row {rid!r}: {problem}\n")
            skipped[str(rid)] = problem
            continue
        custom[rid] = rule
    return custom, skipped


def _merged_rules() -> tuple:
    """(DEFAULT_RULES overlaid with saved rules + custom-only rules, error,
    skipped).

    ``error`` is None when alert_rules was read, else a string; the defaults
    are still returned so built-in alerting survives a store outage.
    ``skipped`` maps malformed rows to why they were left out (F039)."""
    try:
        _ensure_tables()
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT rule_id, rule_json, enabled FROM alert_rules").fetchall()
    except Exception as e:
        return list(DEFAULT_RULES), f"alert_rules store unavailable: {type(e).__name__}", {}
    custom, skipped = _decode_rule_rows(rows)
    for r in rows:
        if r[0] in custom and r[2] is not None and not r[2]:
            custom[r[0]] = {**custom[r[0]], "enabled": False}   # column says off
    out = []
    for rule in DEFAULT_RULES:
        out.append({**rule, **custom[rule["id"]]} if rule["id"] in custom else rule)
    default_ids = {rule["id"] for rule in DEFAULT_RULES}
    out.extend({**rule, "id": rid} for rid, rule in custom.items() if rid not in default_ids)
    return out, None, skipped


def rules_report(*, s_cfg: Optional[dict] = None) -> tuple:
    """(all known rules with current metric values, store error or None).

    A store that cannot be read yields the built-in rules AND the error, so a
    caller can say "custom rules unknown" instead of showing only defaults as
    if no custom rule existed. Malformed rows are skipped (F039)."""
    rules, store_error, _skipped = _merged_rules()
    out = []
    default_by_id = {rule["id"]: rule for rule in DEFAULT_RULES}
    for rule in rules:
        # scs-fix F5a: builtin = a default with no saved override. delete_rule
        # cannot remove it (no row), so the SPA hides Remove; an overridden
        # default keeps Remove, which drops the override. _merged_rules hands an
        # un-overridden default back as the DEFAULT_RULES object itself and an
        # overridden one as a new dict, so identity (not equality: an override
        # may repeat the default's values) tells them apart.
        builtin = default_by_id.get(rule.get("id")) is rule
        value = _evaluate_metric(rule.get("metric", ""), s_cfg=s_cfg)
        out.append({**_json_safe(rule), "enabled": _rule_enabled(rule),
                    "current_value": value, "builtin": builtin})
    return out, store_error


def list_rules(*, s_cfg: Optional[dict] = None) -> list:
    """All known rules, with their current metric values + state."""
    return rules_report(s_cfg=s_cfg)[0]


# ─── Cut 8: rule write API (job-lifecycle rule type rides this) ───────
# Metrics the engine can evaluate -- save_rule validates against this set so
# a rule can't be saved against a metric evaluate() will never resolve.
_KNOWN_METRICS = {
    "bd_failure_rate_1h", "bd_job_failures_1h", "bd_pending_count",
    "bd_disk_free_gb", "bd_circuit_breakers_open", "bd_account_health_min",
    "bd_bitrot_open_issues", "bd_oldest_pending_hours",
}
_VALID_OPS = {">=", "<=", ">", "<", "=="}


def save_rule(rule: dict) -> Optional[str]:
    """Upsert a user rule into alert_rules. Validates id (non-empty),
    metric (must be evaluable), op, a finite numeric threshold and, when
    present, duration_minutes / cooldown_minutes. Returns the
    rule_id on success or None on invalid input. Idempotent by rule_id."""
    _ensure_tables()
    if not isinstance(rule, dict):
        return None
    rid = str(rule.get("id") or "").strip()
    metric = str(rule.get("metric") or "").strip()
    op = str(rule.get("op") or "").strip()
    if not rid or metric not in _KNOWN_METRICS or op not in _VALID_OPS:
        return None
    # scs-fix F4: the id is a path segment of POST /api/alerts/rules/<rule_id>/remove;
    # a "/" in it (even sent as %2F) never routes there, so the rule could never be removed.
    if "/" in rid:
        return None
    try:
        threshold = float(rule.get("threshold"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(threshold):
        return None     # NaN / Infinity: never trips, and is not JSON
    stored = {**rule, "id": rid, "metric": metric, "op": op,
              "threshold": threshold}
    # Every field evaluate() parses is checked here, so a rule that would
    # break the evaluation pass is refused instead of stored.
    try:
        for key in ("duration_minutes", "cooldown_minutes"):
            if key in stored:
                stored[key] = _minutes(stored, key, 0)
    except ValueError:
        return None
    if "name" in stored:
        stored["name"] = str(stored["name"] if stored["name"] is not None else "")
    if "enabled" in stored:
        stored["enabled"] = _rule_enabled(stored)
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            if "enabled" in stored:
                # An explicit on/off also sets the column, so a rule switched
                # off there can be switched back on through the API.
                cx.execute(
                    """INSERT INTO alert_rules(rule_id, rule_json, enabled)
                       VALUES (?,?,?)
                       ON CONFLICT(rule_id) DO UPDATE SET
                         rule_json=excluded.rule_json, enabled=excluded.enabled""",
                    (rid, json.dumps(stored), 1 if stored["enabled"] else 0))
            else:
                cx.execute(
                    """INSERT INTO alert_rules(rule_id, rule_json, enabled)
                       VALUES (?,?,1)
                       ON CONFLICT(rule_id) DO UPDATE SET rule_json=excluded.rule_json""",
                    (rid, json.dumps(stored)))
        return rid
    except Exception:
        return None


def delete_rule(rule_id: str) -> bool:
    """Delete a custom rule. Returns True if a row was removed."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM alert_rules WHERE rule_id = ?",
                             (str(rule_id),))
            return cur.rowcount > 0
    except Exception:
        return False
