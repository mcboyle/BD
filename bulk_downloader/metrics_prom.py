"""Prometheus metrics export (Phase 128, Block Q).

Exposes BD state in Prometheus text exposition format at /metrics.
Lets operators scrape BD into Grafana / VictoriaMetrics / Thanos
without bringing in the prometheus_client dependency — we generate
the wire format directly.

Metrics exposed:

  • bd_jobs_total{site,status}            counter
  • bd_jobs_active{site,status}           gauge (running/queued/needs_review)
  • bd_downloads_bytes_total{site}        counter
  • bd_history_total                      gauge (rows in history table)
  • bd_disk_free_bytes{path}              gauge
  • bd_disk_runway_days{path}             gauge
  • bd_account_health_score{site,account} gauge (0-100)
  • bd_circuit_breaker_state{host}        gauge (0=closed,1=open,2=halfopen)
  • bd_bitrot_issues_total{kind}          gauge (open issues)
  • bd_provenance_rows_total              gauge
  • bd_yt_dlp_age_days                    gauge
  • bd_budget_pct_used{site}              gauge (omitted when unreadable)
  • bd_budget_usage_unknown{site}         gauge (1 = usage counter unreadable)
  • bd_uptime_seconds                     counter
  • bd_dd_budget_truncated_total          counter  (v3.66.12 P0.5)
  • bd_dd_manifests_followed_total        counter  (v3.66.12 P0.5)
  • bd_dd_signed_urls_rejected_total      counter  (v3.66.12 P0.5)

Cardinality: per-site labels are bounded by configured site count;
per-host (circuit breaker) bounded by sites' upstream hosts. No
unbounded label dimensions.

Format reference: openmetrics.io / prometheus.io/docs/instrumenting/
exposition_formats — we emit the simpler v0.0.4 text format which all
modern Prometheus / OTel scrapers accept.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Optional


_PROCESS_START = time.time()


def _esc(label_value: str) -> str:
    """Escape a label value per Prometheus text format rules:
    backslash, double-quote, newline get escaped."""
    if not isinstance(label_value, str):
        label_value = str(label_value)
    return (label_value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n"))


def _line(name: str, value, *, labels: Optional[dict] = None) -> str:
    """Render one metric line. labels rendered as Prometheus-formatted
    {k="v",k2="v2"}. value rendered without trailing zeros."""
    if labels:
        parts = []
        for k in sorted(labels):
            v = _esc(str(labels[k]))
            parts.append(f'{k}="{v}"')
        label_str = "{" + ",".join(parts) + "}"
    else:
        label_str = ""
    # int → no decimal; float → strip trailing zeros
    if isinstance(value, bool):
        rendered = "1" if value else "0"
    elif isinstance(value, int):
        rendered = str(value)
    elif isinstance(value, float):
        rendered = f"{value:.6g}"
    else:
        rendered = str(value)
    return f"{name}{label_str} {rendered}"


def _help(name: str, help_text: str, metric_type: str = "gauge") -> list:
    """Render HELP + TYPE preamble lines for one metric."""
    return [f"# HELP {name} {help_text}",
            f"# TYPE {name} {metric_type}"]


def render(s_cfg: Optional[dict] = None,
          runners: Optional[dict] = None) -> str:
    """Build the complete metrics document. Each section is independent
    and failure-isolated — if one block raises, others still render."""
    lines: list = []
    s_cfg = s_cfg or {}
    runners = runners or {}

    # ── Uptime ────────────────────────────────────────────────────────
    try:
        lines.extend(_help("bd_uptime_seconds",
                          "Process uptime in seconds since first metrics scrape",
                          "counter"))
        lines.append(_line("bd_uptime_seconds", time.time() - _PROCESS_START))
    except Exception:
        pass

    # ── deep_detect live-mode counters (v3.66.12, roadmap P0.5) ──────
    # Shows whether the runner-integration features (P0) are firing
    # in production: budget-truncation, manifest-follow,
    # signed-URL rejection. Failure-isolated like every other block.
    try:
        from . import deep_detect as _dd
        ddm = _dd.get_metrics()
        lines.extend(_help(
            "bd_dd_budget_truncated_total",
            "deep_detect_live calls that hit max_total_probe_time "
            "and truncated their probe phase",
            "counter"))
        lines.append(_line("bd_dd_budget_truncated_total",
                          ddm.get("budget_truncated_count", 0)))
        lines.extend(_help(
            "bd_dd_manifests_followed_total",
            "HLS / DASH manifests successfully fetched and parsed "
            "by deep_detect_live across all calls",
            "counter"))
        lines.append(_line("bd_dd_manifests_followed_total",
                          ddm.get("manifests_followed_count", 0)))
        lines.extend(_help(
            "bd_dd_signed_urls_rejected_total",
            "Signed-URL candidates deep_detect_live rejected as "
            "expired or about-to-expire (skipped at probe time)",
            "counter"))
        lines.append(_line("bd_dd_signed_urls_rejected_total",
                          ddm.get("signed_urls_rejected_count", 0)))
    except Exception:
        pass

    # ── MOD-1 A-5c: remote captcha-takeover observability ────────────
    # bd_takeover_active: open takeover channels right now (== active remote
    # solve sessions). bd_takeover_total: cumulative channels opened since boot.
    # Failure-isolated like every other block; the takeover module is stdlib-
    # only so this import cannot pull in Flask/heavy deps.
    try:
        from . import takeover as _tk
        lines.extend(_help("bd_takeover_active",
                          "Open remote captcha-takeover channels (active "
                          "remote solve sessions) right now",
                          "gauge"))
        lines.append(_line("bd_takeover_active", _tk.active_channel_count()))
        lines.extend(_help("bd_takeover_total",
                          "Cumulative remote captcha-takeover channels opened "
                          "since process start",
                          "counter"))
        lines.append(_line("bd_takeover_total", _tk.takeover_total()))
    except Exception:
        pass

    # ── History totals + jobs by status ──────────────────────────────
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT COUNT(*) FROM history").fetchone()
            total = int(row[0] if row else 0)
        lines.extend(_help("bd_history_total",
                          "Total rows in history table (all-time)"))
        lines.append(_line("bd_history_total", total))
        with _db.db_conn() as cx:
            by_status = cx.execute("""SELECT site_id, status, COUNT(*)
                                       FROM history GROUP BY site_id, status""").fetchall()
        lines.extend(_help("bd_jobs_total",
                          "Cumulative job count by site and status",
                          "counter"))
        for r in by_status:
            sid = r[0] if not hasattr(r, "keys") else r["site_id"]
            st = r[1] if not hasattr(r, "keys") else r["status"]
            n = int(r[2] if not hasattr(r, "keys") else r[2])
            lines.append(_line("bd_jobs_total", n,
                              labels={"site": sid or "", "status": st or ""}))
    except Exception:
        pass

    # ── Active jobs by status (from runner state) ────────────────────
    try:
        active_counts: dict = {}
        for sid, runner in runners.items():
            jobs = getattr(runner, "jobs", {}) or {}
            for j in jobs.values() if isinstance(jobs, dict) else []:
                s = (j or {}).get("status", "")
                if s in ("running", "pending", "needs_review"):
                    active_counts[(sid, s)] = active_counts.get((sid, s), 0) + 1
        lines.extend(_help("bd_jobs_active",
                          "Currently active jobs by site and status"))
        for (sid, status), n in active_counts.items():
            lines.append(_line("bd_jobs_active", n,
                              labels={"site": sid, "status": status}))
    except Exception:
        pass

    # ── Bytes downloaded ─────────────────────────────────────────────
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT site_id, COALESCE(SUM(file_size), 0)
                                  FROM history WHERE status='done'
                                  GROUP BY site_id""").fetchall()
        lines.extend(_help("bd_downloads_bytes_total",
                          "Bytes successfully downloaded per site",
                          "counter"))
        for r in rows:
            sid = r[0] if not hasattr(r, "keys") else r["site_id"]
            b = int(r[1] if not hasattr(r, "keys") else r[1])
            lines.append(_line("bd_downloads_bytes_total", b,
                              labels={"site": sid or ""}))
    except Exception:
        pass

    # ── Disk free bytes + runway ─────────────────────────────────────
    try:
        seen_paths: set = set()
        for sid, cfg in s_cfg.items():
            dl = (cfg or {}).get("download_dir", "")
            if not dl or dl in seen_paths or not os.path.isdir(dl):
                continue
            seen_paths.add(dl)
            usage = shutil.disk_usage(dl)
            lines.append(_line("bd_disk_free_bytes", usage.free,
                              labels={"path": dl[:64]}))
        if seen_paths:
            lines.extend(_help("bd_disk_free_bytes",
                              "Free bytes on each configured download_dir"))
    except Exception:
        pass

    # ── Account health (Phase 100) ───────────────────────────────────
    try:
        from . import account_health as _ah
        rows = _ah.report_all()
        if rows:
            lines.extend(_help("bd_account_health_score",
                              "Account health score 0-100 (higher=better)"))
            for r in rows:
                lines.append(_line("bd_account_health_score",
                    r.get("score", 0),
                    labels={"site": r.get("site_id", ""),
                            "account": r.get("account_id", "default")}))
    except Exception:
        pass

    # ── Circuit breaker state (Phase 99) ─────────────────────────────
    try:
        from . import circuit_breaker as _cb
        rep = _cb.report()
        if rep:
            lines.extend(_help("bd_circuit_breaker_state",
                              "Per-host circuit state 0=closed,1=open,2=halfopen"))
            for host, st in rep.items():
                state_num = {"closed": 0, "open": 1, "half_open": 2}.get(
                    st.get("state", "closed"), 0)
                lines.append(_line("bd_circuit_breaker_state", state_num,
                                  labels={"host": host[:80]}))
    except Exception:
        pass

    # ── Bit-rot issues (Phase 105) ───────────────────────────────────
    try:
        from . import bitrot as _br
        s = _br.stats()
        lines.extend(_help("bd_bitrot_issues_total",
                          "Open bit-rot issues by kind"))
        for kind, n in (s.get("by_kind") or {}).items():
            lines.append(_line("bd_bitrot_issues_total", n,
                              labels={"kind": kind}))
    except Exception:
        pass

    # ── Provenance rows (Phase 104) ──────────────────────────────────
    try:
        from . import provenance as _p
        ps = _p.stats()
        if ps:
            lines.extend(_help("bd_provenance_rows_total",
                              "Total provenance ledger entries"))
            lines.append(_line("bd_provenance_rows_total",
                              ps.get("total_rows", 0)))
    except Exception:
        pass

    # ── yt-dlp age (Phase 87) ────────────────────────────────────────
    try:
        from . import ytdlp_updater as _yt
        info = _yt.current_version()
        age_days = (info or {}).get("age_days")
        if age_days is not None:
            lines.extend(_help("bd_yt_dlp_age_days",
                              "Days since installed yt-dlp was released"))
            lines.append(_line("bd_yt_dlp_age_days", float(age_days)))
    except Exception:
        pass

    # ── Bandwidth budget (Phase 103) ─────────────────────────────────
    try:
        from . import policy_gates as _pg
        unknown_sites = []
        for sid, cfg in s_cfg.items():
            bs = _pg.budget_state(cfg or {})
            if bs.get("state") == "unset":
                continue
            pct = bs.get("pct")
            if pct is None or bs.get("unknown"):
                # The usage counter could not be read. Exporting 0 here would
                # publish an untaken measurement as a real sample and let a
                # cap alert read "0% used" from a locked database, so the
                # sample is OMITTED and the gap is named explicitly instead.
                unknown_sites.append(sid)
                continue
            lines.append(_line("bd_budget_pct_used", pct,
                              labels={"site": sid}))
        if s_cfg:
            lines.extend(_help("bd_budget_pct_used",
                              "Percent of monthly bandwidth budget used per site"))
        if unknown_sites:
            lines.extend(_help("bd_budget_usage_unknown",
                              "1 when a site's budget usage counter could not "
                              "be read (no bd_budget_pct_used sample exists)"))
            for sid in unknown_sites:
                lines.append(_line("bd_budget_usage_unknown", 1,
                                  labels={"site": sid}))
    except Exception:
        pass

    return "\n".join(lines) + "\n"
