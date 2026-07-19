"""Cost economics — per-download cost tracking + ROI dashboard
(Phase 101, Block M).

For each configured site, the operator can record:
  • monthly_cost: subscription cost ($/month) — typed as plain float
  • currency: 'USD' (default), 'EUR', etc — display only, no FX
  • billing_cycle: 'monthly' | 'yearly' (default monthly)

This module aggregates:

  • cost_per_download(site_id, window_days)  — monthly cost / downloads
                                                in window
  • roi_summary(site_id)                      — comparative cost-per-GB,
                                                  cost-per-scene
  • report_all()                              — flat list of per-site
                                                  stats for dashboards
  • sunset_candidates(threshold_per_dl)       — sites where each
                                                  download is paying
                                                  too much (above
                                                  threshold)

Costs are advisory and operator-supplied. BD doesn't try to scrape
billing portals or guess. If the cost field is unset, the site is
omitted from cost-aware aggregates.

All currency is treated as opaque labels — no FX conversion. If you
mix EUR and USD sites, the totals are meaningless. Either pick one
or do the conversion yourself in the site config.
"""
from __future__ import annotations

import time
from typing import Optional


def _site_costs(s_cfg: dict) -> dict:
    """Filter s_cfg to sites with a non-zero monthly_cost. Returns
    {site_id: {monthly_cost, currency, billing_cycle, name}}.
    Yearly costs are normalized to monthly internally (÷12)."""
    out = {}
    for sid, cfg in (s_cfg or {}).items():
        if not isinstance(cfg, dict):
            continue
        cost = cfg.get("monthly_cost")
        if cost is None:
            cost = cfg.get("subscription_cost", 0)
        try:
            monthly = float(cost or 0)
        except (TypeError, ValueError):
            monthly = 0
        if monthly <= 0:
            continue
        cycle = (cfg.get("billing_cycle") or "monthly").lower()
        if cycle == "yearly" or cycle == "annual":
            monthly = monthly / 12.0
        out[sid] = {
            "monthly_cost": round(monthly, 2),
            "currency": cfg.get("currency", "USD"),
            "billing_cycle": cycle,
            "name": cfg.get("name", sid),
        }
    return out


def cost_per_download(site_id: str, *, window_days: int = 30,
                      s_cfg: Optional[dict] = None) -> dict:
    """Compute $/download for one site over the last `window_days`."""
    out = {"site_id": site_id, "downloads": 0, "monthly_cost": 0,
           "cost_per_download": None, "window_days": window_days,
           "currency": "USD"}
    if not s_cfg:
        return out
    costs = _site_costs(s_cfg)
    if site_id not in costs:
        return out
    out.update(costs[site_id])
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) AS n,
                                       COALESCE(SUM(file_size), 0) AS bytes
                                FROM history
                                WHERE site_id = ?
                                  AND status = 'done'
                                  AND ts >= datetime('now', ?)""",
                              (site_id, f"-{int(window_days)} days")).fetchone()
        n = int(row[0] if not hasattr(row, "keys") else row["n"])
        total_bytes = int(row[1] if not hasattr(row, "keys") else row["bytes"])
    except Exception:
        return out
    out["downloads"] = n
    out["total_gb"] = round(total_bytes / (1024**3), 2)
    # Cost for window is monthly_cost × (window_days / 30)
    window_cost = out["monthly_cost"] * (window_days / 30.0)
    if n > 0:
        out["cost_per_download"] = round(window_cost / n, 4)
        if total_bytes > 0:
            out["cost_per_gb"] = round(window_cost / (total_bytes / (1024**3)), 4)
    out["window_cost"] = round(window_cost, 2)
    return out


def report_all(*, window_days: int = 30,
              s_cfg: Optional[dict] = None) -> list:
    """Per-site cost report. Returns list of dicts sorted by
    cost_per_download DESC (most expensive first — what to drop)."""
    if not s_cfg:
        return []
    rows = []
    for sid in _site_costs(s_cfg):
        r = cost_per_download(sid, window_days=window_days, s_cfg=s_cfg)
        # RUN-6: roll per-run accounting (bytes/time/retries) into the report.
        r["run_accounting"] = run_accounting_summary(sid, window_days=window_days)
        rows.append(r)
    rows.sort(key=lambda r: r.get("cost_per_download") or 0, reverse=True)
    return rows


def total_monthly_cost(s_cfg: Optional[dict] = None) -> dict:
    """Portfolio-level monthly cost across all sites. Groups by
    currency since BD doesn't FX."""
    out: dict = {"by_currency": {}, "total_sites_with_cost": 0}
    if not s_cfg:
        return out
    for sid, info in _site_costs(s_cfg).items():
        cur = info["currency"]
        out["by_currency"].setdefault(cur, 0)
        out["by_currency"][cur] += info["monthly_cost"]
        out["total_sites_with_cost"] += 1
    out["by_currency"] = {k: round(v, 2) for k, v in out["by_currency"].items()}
    return out


def sunset_candidates(*, threshold_per_download: float,
                      window_days: int = 90,
                      min_window_downloads: int = 1,
                      s_cfg: Optional[dict] = None) -> list:
    """Sites where the operator might want to cancel.

    Criteria:
      • cost_per_download > threshold_per_download
      • OR downloads in window < min_window_downloads AND monthly_cost > 0

    Second criterion catches paid-but-unused subscriptions even if
    the per-DL cost is undefined (would divide by zero)."""
    if not s_cfg:
        return []
    out = []
    for row in report_all(window_days=window_days, s_cfg=s_cfg):
        per_dl = row.get("cost_per_download")
        n = row.get("downloads", 0)
        reasons = []
        if per_dl is not None and per_dl > threshold_per_download:
            reasons.append(f"${per_dl:.2f}/dl exceeds ${threshold_per_download:.2f} threshold")
        if n < min_window_downloads and row.get("monthly_cost", 0) > 0:
            reasons.append(f"only {n} download(s) in {window_days}d "
                          f"despite ${row['monthly_cost']:.2f}/mo")
        if reasons:
            row["sunset_reasons"] = reasons
            out.append(row)
    return out


# ── RUN-6: per-site run-cost accounting (bytes + time + retries) ──────
# Aggregated from the queue ledger's terminal rows (done/failed/error), which
# retain retries / file_size / ts_added / ts_updated until the operator clears
# them. bytes + retries + runs are exact; elapsed_seconds is an enqueue->terminal
# wall-time proxy (a precise per-download timer would need runner instrumentation).
_TERMINAL_STATUSES = ("done", "failed", "error")


def run_accounting_summary(site_id: str, *, window_days: int = 30) -> dict:
    """Per-site {runs, bytes, retries, elapsed_seconds} over the last window_days,
    from terminal queue rows (filtered on ts_updated). Never raises."""
    from . import db as _db
    out = {"site_id": site_id, "runs": 0, "bytes": 0, "retries": 0,
           "elapsed_seconds": 0.0, "window_days": int(window_days)}
    ph = ",".join("?" * len(_TERMINAL_STATUSES))
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT COUNT(*) AS n, "
                "COALESCE(SUM(file_size),0) AS b, "
                "COALESCE(SUM(retries),0) AS r, "
                "COALESCE(SUM((julianday(ts_updated)-julianday(ts_added))*86400.0),0) AS secs "
                f"FROM queue WHERE site_id=? AND status IN ({ph}) "
                "AND ts_updated >= datetime('now', ?)",
                (site_id, *_TERMINAL_STATUSES, f"-{int(window_days)} days")).fetchone()
            if row:
                out["runs"] = int(row["n"] or 0)
                out["bytes"] = int(row["b"] or 0)
                out["retries"] = int(row["r"] or 0)
                out["elapsed_seconds"] = round(max(0.0, float(row["secs"] or 0.0)), 3)
    except Exception:
        pass
    return out


def run_accounting_all(*, window_days: int = 30) -> list:
    """run_accounting_summary for every site that has terminal queue rows."""
    from . import db as _db
    ph = ",".join("?" * len(_TERMINAL_STATUSES))
    sids = []
    try:
        with _db.db_conn() as cx:
            sids = [r[0] for r in cx.execute(
                f"SELECT DISTINCT site_id FROM queue WHERE status IN ({ph})",
                _TERMINAL_STATUSES).fetchall()]
    except Exception:
        return []
    return [run_accounting_summary(s, window_days=window_days) for s in sids]
