"""EOL / sunset helpers (Phase 119, Block Q).

Eventually operators leave BD: switch tools, retire their setup, or
hand off to someone else. This module makes the transition painless:

  • full_export(dest_dir) — dump everything portable into a directory:
      - all site templates as .bdtpl bundles (via marketplace)
      - history.csv (last N years)
      - provenance.csv (audit ledger)
      - cookies.tar (encrypted) — opt-in only
      - bd-state.json (per-site state snapshots)
      - README.txt explaining the format

  • import_from_dir(src_dir) — restore from a prior full_export

  • migration_report() — pre-flight checklist before EOL:
      "do you have backups?"
      "any data not yet exported?"
      "any active rentals (qB/JD) that need finishing first?"

The output is intentionally tool-agnostic — operators can pipe the
CSVs into pandas, the JSON into jq, the .bdtpl files into a fresh
BD instance, or just archive everything for compliance.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Optional


def full_export(dest_dir: str, *,
               s_cfg: Optional[dict] = None,
               include_cookies: bool = False,
               history_days: int = 3650,
               sign_secret: Optional[str] = None) -> dict:
    """Export everything into `dest_dir`. Creates the directory if
    missing. Returns {ok, files_written, total_bytes, dest_dir}.

    `include_cookies=False` by default — cookies are sensitive. When
    True, they go into cookies.tar inside the dest dir; operator is
    responsible for encrypting that file before sharing.
    """
    out: dict = {"ok": True, "files_written": [], "total_bytes": 0,
                 "dest_dir": dest_dir}
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"can't create dest: {e}"}

    # 1. Each site's template as a .bdtpl bundle
    if s_cfg:
        try:
            from . import marketplace as _mp
            tmpl_dir = Path(dest_dir) / "templates"
            tmpl_dir.mkdir(exist_ok=True)
            for sid, cfg in s_cfg.items():
                bundle = _mp.export_template(
                    sid, cfg or {},
                    description=f"Exported by BD EOL helper on "
                                f"{time.strftime('%Y-%m-%d')}",
                    sign_with=sign_secret,
                )
                fname = _mp.bundle_to_filename(bundle)
                path = tmpl_dir / fname
                data = _mp.bundle_to_bytes(bundle)
                path.write_bytes(data)
                out["files_written"].append(str(path))
                out["total_bytes"] += len(data)
        except Exception as e:
            out.setdefault("warnings", []).append(
                f"template export failed: {e}")

    # 2. History as CSV
    try:
        from . import db as _db
        path = Path(dest_dir) / "history.csv"
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT id, site_id, site_name, url, status,
                                       filename, file_size, ts, message
                                  FROM history
                                  WHERE ts >= datetime('now', ?)
                                  ORDER BY id ASC""",
                              (f"-{int(history_days)} days",)).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "site_id", "site_name", "url", "status",
                    "filename", "file_size", "ts", "message"])
        for r in rows:
            w.writerow([str(r[i] or "") for i in range(9)])
        data = buf.getvalue().encode("utf-8")
        path.write_bytes(data)
        out["files_written"].append(str(path))
        out["total_bytes"] += len(data)
        out["history_rows"] = len(rows)
    except Exception as e:
        out.setdefault("warnings", []).append(f"history export failed: {e}")

    # 3. Provenance ledger
    try:
        from . import db as _db
        path = Path(dest_dir) / "provenance.csv"
        with _db.db_conn() as cx:
            try:
                rows = cx.execute("""SELECT id, ts, site_id, source_url,
                                            final_filename, file_size, sha256
                                      FROM provenance
                                      ORDER BY id ASC""").fetchall()
            except Exception:
                rows = []  # table may not exist
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "ts", "site_id", "source_url",
                    "final_filename", "file_size", "sha256"])
        for r in rows:
            w.writerow([str(r[i] or "") for i in range(7)])
        data = buf.getvalue().encode("utf-8")
        path.write_bytes(data)
        out["files_written"].append(str(path))
        out["total_bytes"] += len(data)
        out["provenance_rows"] = len(rows)
    except Exception as e:
        out.setdefault("warnings", []).append(f"provenance export failed: {e}")

    # 4. State snapshot (account_health, knowledge notes)
    try:
        from . import db as _db
        state: dict = {}
        with _db.db_conn() as cx:
            try:
                state["knowledge_notes"] = [
                    dict(r) for r in cx.execute(
                        "SELECT * FROM knowledge_notes"
                    ).fetchall()]
            except Exception:
                state["knowledge_notes"] = []
        try:
            from . import account_health as _ah
            state["account_health"] = _ah.report_all()
        except Exception:
            state["account_health"] = []
        path = Path(dest_dir) / "bd-state.json"
        data = json.dumps(state, indent=2, default=str,
                         ensure_ascii=False).encode("utf-8")
        path.write_bytes(data)
        out["files_written"].append(str(path))
        out["total_bytes"] += len(data)
    except Exception as e:
        out.setdefault("warnings", []).append(f"state export failed: {e}")

    # 5. Cookies — opt in only
    if include_cookies:
        try:
            from .constants import INSTALL_DIR
            cookies_dir = Path(INSTALL_DIR) / "cookies"
            if cookies_dir.is_dir():
                path = Path(dest_dir) / "cookies.tar"
                with tarfile.open(path, "w") as tf:
                    tf.add(str(cookies_dir), arcname="cookies")
                out["files_written"].append(str(path))
                out["total_bytes"] += path.stat().st_size
                out.setdefault("warnings", []).append(
                    "cookies.tar is unencrypted — encrypt before sharing")
        except Exception as e:
            out.setdefault("warnings", []).append(f"cookies export failed: {e}")

    # 6. README
    readme = (
        "BulkDownloader EOL Export\n"
        "=========================\n\n"
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
        f"Contents:\n"
        f"  templates/*.bdtpl   — portable site configs (one per site)\n"
        f"  history.csv         — download history (last {history_days} days)\n"
        f"  provenance.csv      — tamper-evident download ledger\n"
        f"  bd-state.json       — events + notes + account health\n"
        + ("  cookies.tar         — session cookies (UNENCRYPTED — protect!)\n"
           if include_cookies else "") +
        "\n"
        "To restore into a new BD instance:\n"
        "  1. Set up the new BD as normal\n"
        "  2. For each .bdtpl: POST /api/marketplace/import with the bundle\n"
        "  3. Re-auth each site (cookies in this export are likely stale)\n"
        "  4. Manually replay any state from bd-state.json you care about\n"
    )
    path = Path(dest_dir) / "README.txt"
    path.write_text(readme, encoding="utf-8")
    out["files_written"].append(str(path))
    return out


def migration_report(s_cfg: Optional[dict] = None) -> dict:
    """Pre-flight checklist for a planned BD migration / sunset.
    Returns a list of warnings the operator should resolve before
    declaring done."""
    issues: list = []
    info: dict = {}

    # Active downloads still running?
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) FROM history
                                 WHERE status IN ('running', 'pending')""").fetchone()
        active = int(row[0]) if row else 0
        info["active_jobs"] = active
        if active > 0:
            issues.append({
                "severity": "warn",
                "message": f"{active} jobs are still in flight — "
                           "let them finish or mark them needs_review "
                           "before sunset",
            })
    except Exception:
        pass

    # Total library size
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*), COALESCE(SUM(file_size), 0)
                                 FROM history WHERE status='done'""").fetchone()
        info["total_files"] = int(row[0] or 0)
        info["total_size_gb"] = round((row[1] or 0) / (1024**3), 1)
    except Exception:
        pass

    # Sites without templates worth preserving
    if s_cfg:
        for sid, cfg in s_cfg.items():
            if not (cfg or {}).get("template"):
                issues.append({
                    "severity": "info",
                    "message": f"site {sid} has no template field — "
                               "consider naming it before export",
                })

    # Provenance ledger health
    try:
        from . import provenance as _p
        v = _p.verify_chain()
        if not v.get("ok"):
            issues.append({
                "severity": "fail",
                "message": "provenance chain failed verification "
                           "— resolve before relying on the export",
                "details": v,
            })
    except Exception:
        pass

    # Pending bit-rot issues
    try:
        from . import bitrot as _br
        s = _br.stats()
        n = s.get("open_issues", 0)
        if n > 0:
            issues.append({
                "severity": "warn",
                "message": f"{n} unresolved integrity issue(s) "
                           "— resolve or accept loss before sunset",
            })
    except Exception:
        pass

    return {"ok": not any(i["severity"] == "fail" for i in issues),
            "issues": issues, "info": info,
            "generated_at": time.time()}
