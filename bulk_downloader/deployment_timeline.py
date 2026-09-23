"""Row 1057 -- Deployment Lifecycle & Revision Rollout Timeline.

Records deployment lifecycle transitions and rollout events in the app DB
(deployment_revisions / deployment_events), exposes them as uniform timeline
entries to timeline.merged_timeline() and at GET /api/deploy/timeline.

WHAT FEEDS IT. This app is deployed by replacing the checkout and restarting
the service (see app_health.build_identity), so the one deploy signal the app
itself can observe is "the revision running now differs from the last one
recorded". record_boot_revision() runs from app.boot_once() and turns that
into a deployment that becomes ACTIVE, superseding (or, when the revision was
seen before, rolling back) the previous one. Richer lifecycle stages (staging,
canary, rolling_out) are recorded through advance_state() by whatever drives
them; nothing in-process invents them.

WHY THE DB. The history must survive the restart that IS the deployment; an
in-process registry is empty exactly when it matters. Every sibling source in
timeline.py reads the DB for the same reason.
"""
from __future__ import annotations

import enum
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


class DeploymentState(str, enum.Enum):
    PENDING = "pending"
    STAGING = "staging"
    CANARY = "canary"
    ROLLING_OUT = "rolling_out"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"


_STATES = {s.value for s in DeploymentState}


class RolloutEventType(str, enum.Enum):
    DEPLOY_INITIATED = "deploy_initiated"
    STAGE_UPDATED = "stage_updated"
    CANARY_VERIFIED = "canary_verified"
    HEALTH_CHECK_PASSED = "health_check_passed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    PROGRESS_UPDATED = "progress_updated"
    ROLLOUT_COMPLETED = "rollout_completed"
    ROLLOUT_FAILED = "rollout_failed"
    ROLLOUT_SUPERSEDED = "rollout_superseded"
    ROLLBACK_INITIATED = "rollback_initiated"
    ROLLBACK_COMPLETED = "rollback_completed"


_TRANSITION_EVENT = {
    DeploymentState.ACTIVE.value: RolloutEventType.ROLLOUT_COMPLETED.value,
    DeploymentState.FAILED.value: RolloutEventType.ROLLOUT_FAILED.value,
    DeploymentState.ROLLED_BACK.value: RolloutEventType.ROLLBACK_COMPLETED.value,
    DeploymentState.SUPERSEDED.value: RolloutEventType.ROLLOUT_SUPERSEDED.value,
}


@dataclass
class RolloutEvent:
    event_id: str
    deployment_id: str
    ts: float
    event_type: str
    status: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeploymentRevision:
    deployment_id: str
    revision: str
    target_env: str
    state: str
    created_at: float
    updated_at: float
    progress_percent: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    events: List[RolloutEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["events"] = [e.to_dict() for e in self.events]
        return res


def _db_conn():
    from . import db as _db
    return _db.db_conn()


def ensure_tables(cx) -> None:
    cx.execute("""CREATE TABLE IF NOT EXISTS deployment_revisions(
        deployment_id TEXT PRIMARY KEY,
        revision TEXT NOT NULL,
        target_env TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        progress_percent REAL NOT NULL DEFAULT 0,
        metadata TEXT NOT NULL DEFAULT '{}'
    )""")
    cx.execute("""CREATE TABLE IF NOT EXISTS deployment_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        deployment_id TEXT NOT NULL,
        ts REAL NOT NULL,
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}'
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_deployment_events_ts ON deployment_events(ts)")


def _clamp(p: float) -> float:
    return max(0.0, min(100.0, float(p)))


def _revision_from_row(r) -> DeploymentRevision:
    return DeploymentRevision(
        deployment_id=r["deployment_id"], revision=r["revision"], target_env=r["target_env"],
        state=r["state"], created_at=r["created_at"], updated_at=r["updated_at"],
        progress_percent=r["progress_percent"], metadata=json.loads(r["metadata"] or "{}"),
    )


def _event_from_row(r) -> RolloutEvent:
    return RolloutEvent(
        event_id=r["event_id"], deployment_id=r["deployment_id"], ts=r["ts"],
        event_type=r["event_type"], status=r["status"], message=r["message"],
        details=json.loads(r["details"] or "{}"),
    )


class DeploymentRolloutTimeline:
    """Deployment lifecycle and rollout timeline over the app DB.

    Only create_deployment() and advance_state() change a deployment's state;
    record_event() appends an informational event and never transitions.
    """

    # ── writes ──────────────────────────────────────────────────────────
    def _insert_event(self, cx, deployment_id: str, event_type: str, status: str,
                      message: str, details: Optional[Dict[str, Any]], ts: float) -> RolloutEvent:
        evt = RolloutEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", deployment_id=deployment_id, ts=ts,
            event_type=str(event_type), status=str(status), message=str(message),
            details=dict(details or {}),
        )
        cx.execute(
            """INSERT INTO deployment_events(event_id, deployment_id, ts, event_type, status, message, details)
               VALUES (?,?,?,?,?,?,?)""",
            (evt.event_id, evt.deployment_id, evt.ts, evt.event_type, evt.status, evt.message,
             json.dumps(evt.details, sort_keys=True)),
        )
        return evt

    def _create(self, cx, revision: str, target_env: str, metadata: Optional[Dict[str, Any]],
                now: float) -> str:
        dep_id = f"dep-{uuid.uuid4().hex[:12]}"
        cx.execute(
            """INSERT INTO deployment_revisions(deployment_id, revision, target_env, state,
                   created_at, updated_at, progress_percent, metadata) VALUES (?,?,?,?,?,?,?,?)""",
            (dep_id, str(revision), str(target_env), DeploymentState.PENDING.value, now, now, 0.0,
             json.dumps(dict(metadata or {}), sort_keys=True)),
        )
        self._insert_event(cx, dep_id, RolloutEventType.DEPLOY_INITIATED.value,
                           DeploymentState.PENDING.value,
                           f"Deployment created for revision {revision} in {target_env}", None, now)
        return dep_id

    def _advance(self, cx, deployment_id: str, new_state: str, message: str,
                 progress_percent: Optional[float], details: Optional[Dict[str, Any]], now: float) -> None:
        if new_state not in _STATES:
            raise ValueError(f"unknown deployment state {new_state!r}")
        row = cx.execute("SELECT progress_percent FROM deployment_revisions WHERE deployment_id=?",
                         (deployment_id,)).fetchone()
        if row is None:
            raise KeyError(f"Deployment '{deployment_id}' not found")
        progress = row["progress_percent"] if progress_percent is None else _clamp(progress_percent)
        cx.execute("""UPDATE deployment_revisions SET state=?, updated_at=?, progress_percent=?
                      WHERE deployment_id=?""", (new_state, now, progress, deployment_id))
        self._insert_event(cx, deployment_id,
                           _TRANSITION_EVENT.get(new_state, RolloutEventType.STAGE_UPDATED.value),
                           new_state, message or f"Deployment state transitioned to {new_state}",
                           details, now)

    def create_deployment(self, revision: str, target_env: str = "production",
                          metadata: Optional[Dict[str, Any]] = None,
                          created_at: Optional[float] = None) -> DeploymentRevision:
        now = created_at if created_at is not None else time.time()
        with _db_conn() as cx:
            ensure_tables(cx)
            dep_id = self._create(cx, revision, target_env, metadata, now)
        return self.get_deployment(dep_id)

    def advance_state(self, deployment_id: str, new_state: str, message: str = "",
                      progress_percent: Optional[float] = None,
                      details: Optional[Dict[str, Any]] = None) -> DeploymentRevision:
        with _db_conn() as cx:
            ensure_tables(cx)
            self._advance(cx, deployment_id, new_state, message, progress_percent, details, time.time())
        return self.get_deployment(deployment_id)

    def record_event(self, deployment_id: str, event_type: str, status: str, message: str,
                     details: Optional[Dict[str, Any]] = None,
                     progress_percent: Optional[float] = None,
                     ts: Optional[float] = None) -> RolloutEvent:
        """Append an informational event (health check, progress ...). The
        deployment's state is NOT changed -- only advance_state transitions."""
        now = ts if ts is not None else time.time()
        with _db_conn() as cx:
            ensure_tables(cx)
            if cx.execute("SELECT 1 FROM deployment_revisions WHERE deployment_id=?",
                          (deployment_id,)).fetchone() is None:
                raise KeyError(f"Deployment '{deployment_id}' not found")
            if progress_percent is not None:
                cx.execute("UPDATE deployment_revisions SET progress_percent=?, updated_at=? WHERE deployment_id=?",
                           (_clamp(progress_percent), now, deployment_id))
            return self._insert_event(cx, deployment_id, event_type, status, message, details, now)

    def record_boot_revision(self, revision: str, target_env: str = "production",
                             metadata: Optional[Dict[str, Any]] = None) -> Optional[DeploymentRevision]:
        """Record that `revision` is now running. A restart on the same
        revision records nothing; a new revision becomes ACTIVE and the
        previous active one is SUPERSEDED, or ROLLED_BACK when the new
        revision is one this env ran before. Atomic per DB (BEGIN IMMEDIATE),
        so concurrent boots cannot record the same deploy twice."""
        if not revision:
            return None
        now = time.time()
        with _db_conn() as cx:
            ensure_tables(cx)
            cx.execute("BEGIN IMMEDIATE")
            current = cx.execute(
                """SELECT deployment_id, revision FROM deployment_revisions
                   WHERE target_env=? AND state=? ORDER BY updated_at DESC LIMIT 1""",
                (target_env, DeploymentState.ACTIVE.value)).fetchone()
            if current is not None and current["revision"] == revision:
                return None
            seen_before = cx.execute(
                "SELECT 1 FROM deployment_revisions WHERE target_env=? AND revision=? LIMIT 1",
                (target_env, revision)).fetchone() is not None
            dep_id = self._create(cx, revision, target_env, metadata, now)
            self._advance(cx, dep_id, DeploymentState.ACTIVE.value,
                          f"Revision {revision} started on this host", 100.0, None, now)
            if current is not None:
                prev_state = (DeploymentState.ROLLED_BACK if seen_before
                              else DeploymentState.SUPERSEDED).value
                self._advance(cx, current["deployment_id"], prev_state,
                              f"Replaced by revision {revision}", None, {"next": dep_id}, now)
        return self.get_deployment(dep_id)

    # ── reads ───────────────────────────────────────────────────────────
    def get_deployment(self, deployment_id: str) -> Optional[DeploymentRevision]:
        with _db_conn() as cx:
            ensure_tables(cx)
            r = cx.execute("SELECT * FROM deployment_revisions WHERE deployment_id=?",
                           (deployment_id,)).fetchone()
            if r is None:
                return None
            dep = _revision_from_row(r)
            dep.events = [_event_from_row(e) for e in cx.execute(
                "SELECT * FROM deployment_events WHERE deployment_id=? ORDER BY ts, id", (deployment_id,))]
            return dep

    def get_active_deployment(self, target_env: str = "production") -> Optional[DeploymentRevision]:
        with _db_conn() as cx:
            ensure_tables(cx)
            r = cx.execute("""SELECT deployment_id FROM deployment_revisions
                              WHERE target_env=? AND state=? ORDER BY updated_at DESC LIMIT 1""",
                           (target_env, DeploymentState.ACTIVE.value)).fetchone()
        return self.get_deployment(r["deployment_id"]) if r else None

    def list_deployments(self, target_env: Optional[str] = None, limit: int = 50) -> List[DeploymentRevision]:
        with _db_conn() as cx:
            ensure_tables(cx)
            if target_env:
                rows = cx.execute("""SELECT * FROM deployment_revisions WHERE target_env=?
                                     ORDER BY created_at DESC LIMIT ?""", (target_env, int(limit))).fetchall()
            else:
                rows = cx.execute("SELECT * FROM deployment_revisions ORDER BY created_at DESC LIMIT ?",
                                  (int(limit),)).fetchall()
        return [_revision_from_row(r) for r in rows]

    def get_timeline_entries(self, since_ts: float = 0.0, limit: int = 100) -> List[Dict[str, Any]]:
        """Entries in the uniform activity timeline shape:
        {ts, source, kind, severity, title, description, link}"""
        with _db_conn() as cx:
            ensure_tables(cx)
            rows = cx.execute(
                """SELECT e.*, d.revision AS revision, d.target_env AS target_env
                   FROM deployment_events e LEFT JOIN deployment_revisions d USING (deployment_id)
                   WHERE e.ts >= ? ORDER BY e.ts DESC, e.id DESC LIMIT ?""",
                (float(since_ts), int(limit))).fetchall()
        out = []
        for r in rows:
            status, kind = r["status"], r["event_type"]
            severity = "info"
            if status in (DeploymentState.FAILED.value, DeploymentState.ABORTED.value) or "fail" in kind:
                severity = "error"
            elif status in (DeploymentState.ROLLED_BACK.value, DeploymentState.CANARY.value) or "warn" in kind:
                severity = "warn"
            out.append({
                "ts": r["ts"],
                "source": "deployment_rollout",
                "kind": kind,
                "severity": severity,
                "title": f"Deployment {r['revision'] or r['deployment_id']} ({r['target_env'] or 'fleet'}): {kind}",
                "description": r["message"],
                "link": "/api/deploy/timeline",
            })
        return out


_TIMELINE = DeploymentRolloutTimeline()


def get_deployment_timeline() -> DeploymentRolloutTimeline:
    return _TIMELINE


def running_revision(install_dir: str) -> tuple[str, Dict[str, Any]]:
    """(revision, metadata) of the code running from install_dir: the app
    version plus the checkout's commit (app_health.build_identity). An
    unknown commit leaves the version alone as the revision."""
    from . import __version__
    from .app_health import build_identity
    ident = build_identity(install_dir)
    sha = ident.get("sha")
    revision = f"{__version__}+{sha}" if sha else str(__version__)
    return revision, {"version": __version__, "sha": sha, "built_at": ident.get("built_at"),
                      "source": ident.get("source")}


def record_running_revision(install_dir: str) -> Optional[DeploymentRevision]:
    """Boot hook (app.boot_once): best-effort, never raises into boot."""
    try:
        revision, meta = running_revision(install_dir)
        return get_deployment_timeline().record_boot_revision(revision, metadata=meta)
    except Exception as e:  # why: a timeline write must never block the app from booting
        sys.stderr.write(f"[deployment_timeline] boot revision not recorded: {e}\n")
        return None


def create_blueprint(name: str = "deployment_timeline"):
    """Flask Blueprint exposing GET /api/deploy/timeline."""
    from flask import Blueprint, jsonify, request

    bp = Blueprint(name, __name__)

    @bp.route("/api/deploy/timeline", methods=["GET"])
    def api_deploy_timeline():
        try:
            since_ts = float(request.args.get("since_ts", 0.0))
            limit = int(request.args.get("limit", 100))
        except ValueError:
            return jsonify({"error": "since_ts and limit must be numbers"}), 400
        env = request.args.get("env")
        tracker = get_deployment_timeline()
        active = tracker.get_active_deployment(target_env=env or "production")
        return jsonify({
            "timeline": tracker.get_timeline_entries(since_ts=since_ts, limit=limit),
            "deployments": [d.to_dict() for d in tracker.list_deployments(target_env=env, limit=limit)],
            "active_deployment": active.to_dict() if active else None,
        })

    return bp


def register_routes(app) -> int:
    app.register_blueprint(create_blueprint())
    return sum(1 for r in app.url_map.iter_rules() if r.endpoint.startswith("deployment_timeline."))
