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
            from . import db as _db
            with _db.db_conn() as cx:
                r = cx.execute("""SELECT
                      SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS d,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS f
                    FROM history WHERE ts >= datetime('now', '-1 hour')""").fetchone()
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
                      AND ts >= datetime('now', '-1 hour')""").fetchone()
            return float(r[0] or 0)
        if name == "bd_pending_count":
            from . import db as _db
            with _db.db_conn() as cx:
                r = cx.execute("""SELECT COUNT(*) FROM history
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
                r = cx.execute("""SELECT
                  (strftime('%s', 'now') - strftime('%s', MIN(ts))) / 3600.0
                  FROM history WHERE status = 'pending'""").fetchone()
            return float(r[0]) if r and r[0] is not None else 0.0
    except Exception as e:
        sys.stderr.write(f"[alerts] metric {name}: {e}\n")
        return None
    return None


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
    """The alert_events store could not be read or written.

    Row 421. Until v3.66.1362 the three helpers below each collapsed this
    state into their own "nothing to report" value -- False, None, and a
    silent no-op -- so a locked, corrupted, unwritable or malformed-ts table
    was indistinguishable from a condition that had simply not held. A rule
    whose metric genuinely tripped then recorded nothing, answered the
    duration gate False forever, and never reached _fire_actions(); nothing
    raised and nothing logged. CLAUDE.md A7: an unavailable measurement is
    UNKNOWN, never OK. The distinction has to survive as far as evaluate(),
    which is why it is an exception rather than another sentinel value.
    """


def _condition_was_held(rule_id: str, *, duration_minutes: int) -> bool:
    """Check if the rule has been continuously tripping for at least
    `duration_minutes`. We approximate this by looking at our most
    recent 'tripped' event; if it's older than duration_minutes ago,
    and no 'cleared' in between, we say the condition held.

    Raises AlertEventStoreUnavailable when the answer cannot be measured.
    False is reserved for a store that WAS read and says the condition did
    not hold; a malformed ts is unmeasured, not a negative answer.
    """
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT kind, ts FROM alert_events
                              WHERE rule_id = ?
                              ORDER BY id DESC LIMIT 1""",
                          (rule_id,)).fetchone()
        if r is None:
            return False
        kind = r[0] if not hasattr(r, "keys") else r["kind"]
        ts = float(r[1] if not hasattr(r, "keys") else r["ts"])
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unavailable reading rule {rule_id!r} history: "
            f"{type(e).__name__}: {e}") from e
    if kind != "tripped":
        return False
    # Has it held long enough?
    return (time.time() - ts) >= (duration_minutes * 60)


def _last_fire_age(rule_id: str) -> Optional[float]:
    """Seconds since last 'fired' event for this rule, or None if it has
    never fired.

    Raises AlertEventStoreUnavailable when the store cannot answer. None
    means "measured, never fired" and opens the cooldown gate, so an
    unreadable store must not be able to produce it.
    """
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT ts FROM alert_events
                              WHERE rule_id = ? AND kind = 'fired'
                              ORDER BY id DESC LIMIT 1""",
                          (rule_id,)).fetchone()
        if r is None:
            return None
        return time.time() - float(r[0])
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unavailable reading rule {rule_id!r} last fire: "
            f"{type(e).__name__}: {e}") from e


def _record_event(rule_id: str, kind: str, value: Optional[float],
                 message: str = ""):
    """Append one event row.

    Raises AlertEventStoreUnavailable when the INSERT does not land. A lost
    'tripped' row is not cosmetic: the duration gate is computed from these
    rows, so swallowing the failure suppressed every subsequent fire.
    """
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO alert_events(
                rule_id, ts, metric_value, kind, message
            ) VALUES (?,?,?,?,?)""",
                (rule_id, time.time(), value, kind, message[:300]))
    except Exception as e:
        raise AlertEventStoreUnavailable(
            f"alert_events unavailable writing {kind!r} for rule "
            f"{rule_id!r}: {type(e).__name__}: {e}") from e


def evaluate(s_cfg: Optional[dict] = None,
            *, rules: Optional[list] = None) -> dict:
    """Run all rules. For each, evaluate metric, compare to threshold,
    track state, fire if condition has held and cooldown elapsed.

    Returns:
      {evaluated: N, tripping: N, fired: N, unknown: N, results: [...]}

    Row 421. Every result carries an explicit `status`:

      ok       the rule was fully measured; `fired` is that measurement
      unknown  a required measurement was unavailable, so whether this rule
               should have fired is NOT KNOWN. `fired` being False here is
               the absence of an answer, not a negative one
      error    the rule itself is malformed (an operator typo'd `op`), which
               is deterministic rather than unavailable

    An unavailable event store is additionally written to stderr. That is
    deliberate and load-bearing: bg_scheduler registers this function behind
    a wrapper that DISCARDS the returned dict, so on the production path the
    log is the only channel by which the operator ever learns that the
    alerting layer has stopped being able to alert.
    """
    if rules is None:
        rules = DEFAULT_RULES
    out = {"evaluated": 0, "tripping": 0, "fired": 0, "unknown": 0,
           "results": []}
    for rule in rules:
        out["evaluated"] += 1
        metric = rule.get("metric", "")
        value = _evaluate_metric(metric, s_cfg=s_cfg)
        result: dict = {"rule_id": rule["id"], "metric": metric,
                       "value": value, "threshold": rule["threshold"],
                       "tripping": False, "fired": False}
        if value is None:
            # The metric could not be computed, so the comparison below never
            # happened. Not a measured "did not trip".
            result["status"] = "unknown"
            result["error"] = "metric unavailable"
            out["unknown"] += 1
            out["results"].append(result)
            continue
        op_fn = _OPS.get(rule.get("op", ">="))
        if not op_fn:
            result["status"] = "error"
            result["error"] = f"unknown op: {rule.get('op')}"
            out["results"].append(result)
            continue
        tripping = op_fn(value, rule["threshold"])
        result["tripping"] = tripping
        try:
            if tripping:
                out["tripping"] += 1
                # Was a previous trip recorded? If not, record one now.
                if not _condition_was_held(rule["id"],
                                           duration_minutes=0):
                    _record_event(rule["id"], "tripped", value,
                                 f"{metric}={value} {rule['op']} {rule['threshold']}")
                # Has it been tripping long enough?
                if _condition_was_held(
                        rule["id"],
                        duration_minutes=int(rule.get("duration_minutes", 1))):
                    # Cooldown gate
                    last_fire = _last_fire_age(rule["id"])
                    cool = int(rule.get("cooldown_minutes", 60)) * 60
                    if last_fire is None or last_fire >= cool:
                        _record_event(rule["id"], "fired", value,
                                     rule.get("name", rule["id"]))
                        result["fired"] = True
                        out["fired"] += 1
                        # Fire side effects
                        _fire_actions(rule, value)
            else:
                # Condition has cleared — record so duration logic resets
                if _condition_was_held(rule["id"], duration_minutes=0):
                    _record_event(rule["id"], "cleared", value, "")
        except AlertEventStoreUnavailable as e:
            # The metric reading above stays truthful -- `tripping` was really
            # measured. What is unknown is the rule's STATE, and therefore
            # whether it should have fired.
            result["status"] = "unknown"
            result["error"] = str(e)[:300]
            out["unknown"] += 1
            sys.stderr.write(
                f"[alerts] UNKNOWN rule {rule['id']}: {e}\n")
        else:
            result["status"] = "ok"
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
    """All alerts that fired in the last `lookback_hours`."""
    _ensure_tables()
    cutoff = time.time() - lookback_hours * 3600
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM alert_events
                                  WHERE ts >= ? AND kind = 'fired'
                                  ORDER BY id DESC""", (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def list_rules(*, s_cfg: Optional[dict] = None) -> list:
    """All known rules, with their current metric values + state."""
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("SELECT * FROM alert_rules").fetchall()
        custom = {r[0] if not hasattr(r, "keys") else r["rule_id"]:
                  json.loads(r[1] if not hasattr(r, "keys") else r["rule_json"])
                  for r in rows}
    except Exception:
        custom = {}
    # Merge with defaults
    out = []
    default_ids = set()
    for rule in DEFAULT_RULES:
        default_ids.add(rule["id"])
        if rule["id"] in custom:
            rule = {**rule, **custom[rule["id"]]}
        value = _evaluate_metric(rule.get("metric", ""), s_cfg=s_cfg)
        out.append({**rule, "current_value": value})
    # Cut 8: surface custom-only rules (e.g. operator-created job-lifecycle
    # rules) whose id is not one of the built-ins.
    for rid, rule in custom.items():
        if rid in default_ids:
            continue
        value = _evaluate_metric(rule.get("metric", ""), s_cfg=s_cfg)
        out.append({**rule, "id": rid, "current_value": value})
    return out


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
    metric (must be evaluable), op, and a numeric threshold. Returns the
    rule_id on success or None on invalid input. Idempotent by rule_id."""
    _ensure_tables()
    if not isinstance(rule, dict):
        return None
    rid = str(rule.get("id") or "").strip()
    metric = str(rule.get("metric") or "").strip()
    op = str(rule.get("op") or "").strip()
    if not rid or metric not in _KNOWN_METRICS or op not in _VALID_OPS:
        return None
    try:
        float(rule.get("threshold"))
    except (TypeError, ValueError):
        return None
    stored = {**rule, "id": rid, "metric": metric, "op": op}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
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
