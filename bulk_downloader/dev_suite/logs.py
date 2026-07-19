"""dev_suite.logs -- logging + SSE/event surface

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re



# ── 15. log-level toggle ───────────────────────────────────────────

def set_log_level(level) -> dict:
    """Change the runtime log level for the bulk_downloader logger
    tree. Reversible — pass the previous level to restore it."""
    from bulk_downloader import log as _log
    previous = _log.get_level()
    ok = _log.set_level(level)
    if not ok:
        return {"ok": False, "error": f"unrecognized level {level!r}",
                "level": previous,
                "valid": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}
    return {"ok": True, "previous": previous, "level": _log.get_level()}



def get_log_level() -> dict:
    from bulk_downloader import log as _log
    return {"level": _log.get_level()}



# ── 16a. SSE broker status ─────────────────────────────────────────

def sse_status() -> dict:
    """Live SSE broker state — connected subscriber count and the age
    of each connection. Read-only."""
    try:
        from bulk_downloader import sse_broker as _sse
        broker = _sse.get_broker()
        import time as _t
        now = _t.time()
        with broker._lock:
            subs = list(broker._subscribers.values())
        clients = [{"id": s.id,
                    "age_seconds": round(now - s.created_at, 1),
                    "queued_events": s.q.qsize()} for s in subs]
        return {"connected_clients": len(clients), "clients": clients}
    except Exception as e:
        return {"error": str(e)[:200]}



# ════════════════ diagnostic inspectors (backlog Tier 1) ══════════
# Read-only operational inspectors. Like the rest of dev_suite they
# return plain dicts and do no work at import time.

# ── 25. structured log search (D-63) ──────────────────────────────

def log_search(level=None, logger=None, contains=None,
               limit=200) -> dict:
    """Search logs/bulk_downloader.log by level, logger name, and/or
    free text — newest match first. Reads only the tail of the file
    (last 4 MB) so a large log is never loaded whole. Log lines look
    like 'YYYY-MM-DD HH:MM:SS LEVEL  logger: message'."""
    import re
    try:
        limit = max(1, min(int(limit), 2000))
    except Exception:
        limit = 200
    log_path = Path("logs") / "bulk_downloader.log"
    if not log_path.exists():
        return {"path": str(log_path), "exists": False, "matches": []}
    want_level = ((level or "").strip().upper() or None)
    want_logger = ((logger or "").strip() or None)
    want_text = ((contains or "").strip() or None)
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 4 * 1024 * 1024)
            fh.seek(size - window)
            data = fh.read(window)
        lines = data.decode("utf-8", "replace").splitlines()
        if window < size and lines:
            lines = lines[1:]  # drop a partial first line
    except Exception as e:
        return {"path": str(log_path), "error": str(e)[:200]}
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+"
                     r"([A-Z]+)\s+([\w.]+):\s?(.*)$")
    matches = []
    for raw in reversed(lines):
        line = raw.rstrip("\n")
        m = pat.match(line)
        ts, lvl, lg, msg = (m.groups() if m else ("", "", "", line))
        if want_level and lvl != want_level:
            continue
        if want_logger and want_logger not in lg:
            continue
        if want_text and want_text.lower() not in line.lower():
            continue
        matches.append({"ts": ts, "level": lvl, "logger": lg,
                        "message": msg})
        if len(matches) >= limit:
            break
    return {
        "path": str(log_path),
        "exists": True,
        "filters": {"level": want_level, "logger": want_logger,
                    "contains": want_text},
        "scanned_lines": len(lines),
        "match_count": len(matches),
        "matches": matches,
    }



# ── 41. event-broker tap + SSE-event tap UI (U22: D-65 + D-105) ────
#
# D-65 + D-105 — the broker stream and its renderer.
#   • event_tap (D-65) — recent SSE events captured from the broker's
#     publish path (the dev_events ring buffer). Read-only.
#   • event_tap_ui_html (D-105) — a self-contained dev HTML page that
#     polls event_tap and renders the stream live. Deliberately
#     standalone — it does NOT touch the shared static/app.js.

def event_tap(limit=50):
    """D-65 — recent SSE events captured from the broker: event type,
    payload summary, recipient count, timestamp. Newest first, from a
    bounded ring buffer. Read-only."""
    try:
        from bulk_downloader import dev_events as _de
    except Exception as e:
        return {"tool": "event_tap", "ok": False,
                "error": f"dev_events unavailable: {e}"}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, _de._MAX_EVENTS))
    events = _de.recent(limit)
    return {
        "tool": "event_tap",
        "ok": True,
        "returned": len(events),
        "events": events,
        **_de.stats(),
    }



_EVENT_TAP_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BulkDownloader - SSE event tap</title>
<style>
 body{font-family:system-ui,sans-serif;margin:1rem;background:#111;color:#ddd}
 h1{font-size:1.1rem}
 #bar{margin:.5rem 0}
 button{padding:.3rem .8rem}
 table{border-collapse:collapse;width:100%;font-size:.85rem}
 th,td{text-align:left;padding:.25rem .5rem;border-bottom:1px solid #333;vertical-align:top}
 th{color:#9cf}
 td.payload{font-family:monospace;color:#9d9;word-break:break-all;max-width:40rem}
 .meta{color:#888}
</style>
</head>
<body>
<h1>SSE event tap <span class="meta" id="status"></span></h1>
<div id="bar">
 <button id="toggle">Pause</button>
 <span class="meta" id="counts"></span>
</div>
<table>
 <thead><tr><th>#</th><th>time</th><th>event</th><th>recipients</th><th>payload</th></tr></thead>
 <tbody id="rows"></tbody>
</table>
<script>
(function(){
  var paused=false;
  var toggle=document.getElementById('toggle');
  toggle.addEventListener('click',function(){
    paused=!paused;
    toggle.textContent=paused?'Resume':'Pause';
  });
  function esc(s){
    return String(s).replace(/[&<>]/g,function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];
    });
  }
  function fmtTime(ts){
    return new Date(ts*1000).toLocaleTimeString();
  }
  function render(data){
    document.getElementById('counts').textContent=
      'buffered '+data.buffered+'/'+data.capacity+
      ' &middot; total '+data.total_recorded;
    var rows=document.getElementById('rows');
    rows.innerHTML='';
    (data.events||[]).forEach(function(e){
      var tr=document.createElement('tr');
      tr.innerHTML='<td>'+e.seq+'</td><td>'+esc(fmtTime(e.ts))+
        '</td><td>'+esc(e.event_type)+'</td><td>'+e.recipients+
        '</td><td class="payload">'+esc(e.payload)+'</td>';
      rows.appendChild(tr);
    });
  }
  function poll(){
    if(paused){return;}
    fetch('/api/dev/event_tap?limit=100')
      .then(function(r){return r.json();})
      .then(function(d){
        if(d&&d.ok){
          render(d);
          document.getElementById('status').textContent='';
        }else{
          document.getElementById('status').textContent='(tap unavailable)';
        }
      })
      .catch(function(){
        document.getElementById('status').textContent='(offline)';
      });
  }
  poll();
  setInterval(poll,2000);
})();
</script>
</body>
</html>"""



def event_tap_ui_html():
    """D-105 — the standalone SSE-event-tap dev page. Self-contained
    HTML+JS that polls /api/dev/event_tap; does not load or modify the
    shared static/app.js."""
    return _EVENT_TAP_UI_HTML
