#!/usr/bin/env python3
"""bdctl — command-line companion for Bulk Downloader.

Talks to the local Bulk Downloader API. Useful for scripting,
cron jobs, and quick status checks without opening the web UI.

Examples:
  bdctl status                       # show all sites + queue totals
  bdctl status --site wowgirls       # show one site
  bdctl add URL [URL ...]            # add URL(s) to the auto-routed site
  bdctl add --site SID URL           # add to a specific site
  bdctl start --site SID             # start processing
  bdctl pause --site SID             # pause
  bdctl logs --site SID --tail 50    # last 50 events
  bdctl logs --site SID --follow     # stream events live (Ctrl-C to stop)
  bdctl learned export --site SID    # dump learned selectors to stdout
  bdctl learned import --site SID < profile.json

Configuration:
  Server URL: $BD_URL or  http://127.0.0.1:5555  (default)
  Auth token: $BD_TOKEN or ~/.config/bdctl/token (when configured)

Auth token is optional. If the server doesn't require auth (default
in current versions), bdctl works without one. When global auth is
turned on (Phase 26), the same token works for both the CLI and the
web UI.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1.0"
DEFAULT_URL = "http://127.0.0.1:5555"


def _config_dir():
    """Resolve ~/.config/bdctl across platforms. Falls back to APPDATA on
    Windows for the same role."""
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "bdctl"
    return Path.home() / ".config" / "bdctl"


def _token():
    tok = os.environ.get("BD_TOKEN") or ""
    if tok: return tok.strip()
    f = _config_dir() / "token"
    try:
        return f.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _server_url():
    return os.environ.get("BD_URL", DEFAULT_URL).rstrip("/")


class RequestFailed(SystemExit):
    """A request that did not produce a body, carried structurally.

    ROW 467. ``_request`` raised a bare ``SystemExit`` on any transport
    failure, so ``bdctl health --json`` printed NOTHING to stdout against a
    503 and ``json.loads("")`` failed -- on every host whose vault is locked
    or uninitialised, which is EVERY host after every restart until a human
    unlocks it. The flag promises a structured answer and delivered an empty
    one.

    SUBCLASSING SystemExit IS THE POINT. All 48 ``_request`` call sites keep
    their exact previous behaviour -- same message on stderr, same nonzero
    exit -- because an uncaught ``RequestFailed`` IS an uncaught
    ``SystemExit`` carrying the identical string. Only a caller that opts in
    by catching it sees the structure.

    ``kind`` names WHICH step failed rather than collapsing distinct
    failures into one diagnostic (CLAUDE.md A7): a server that refused with
    503 and a server that never answered lead to opposite actions -- read the
    refusal, or start the service.
    """

    def __init__(self, message, *, kind, status=None, reason=None,
                 body_text="", body_json=None, method="", path=""):
        super().__init__(message)
        self.kind = kind            # "http_error" | "unreachable"
        self.status = status        # None when nothing answered
        self.reason = reason
        self.body_text = body_text
        self.body_json = body_json  # parsed dict/list, or None
        self.method = method
        self.path = path


def _request(method, path, body=None, query=None, stream=False):
    """Issue one HTTP request to the server. Returns parsed JSON for
    non-streaming responses, or the urlopen response object for streams.
    Raises on connection failure with a clean message instead of a
    traceback."""
    url = _server_url() + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"bdctl/{VERSION}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    token = _token()
    if token: headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "replace")
        except Exception: pass
        parsed = None
        try: parsed = json.loads(body)
        except Exception: parsed = None
        raise RequestFailed(
            f"HTTP {e.code} {e.reason} on {method} {path}\n  {body[:500]}",
            kind="http_error", status=e.code, reason=str(e.reason),
            body_text=body, body_json=parsed, method=method, path=path)
    except urllib.error.URLError as e:
        raise RequestFailed(
            f"Can't reach {_server_url()}: {e.reason}\n"
            f"  Is the Bulk Downloader running? Set BD_URL if it's not on the default port.",
            kind="unreachable", reason=str(e.reason), method=method, path=path)
    if stream: return resp
    raw = resp.read().decode("utf-8", "replace")
    if not raw: return {}
    try: return json.loads(raw)
    except Exception: return {"_raw": raw}


def _resolve_site(sid_or_name):
    """Look up a site ID from either an ID or a name. Allows --site
    wowgirls to work even when the actual ID is b80923a1."""
    if not sid_or_name: return None
    data = _request("GET", "/api/status", query={"light": 1})
    if not isinstance(data, dict): return None
    # Exact ID match wins
    if sid_or_name in data: return sid_or_name
    # Otherwise match by name (case-insensitive, substring)
    needle = sid_or_name.lower()
    candidates = [(sid, s.get("name","").lower())
                  for sid, s in data.items()]
    exact = [sid for sid, n in candidates if n == needle]
    if len(exact) == 1: return exact[0]
    partial = [sid for sid, n in candidates if needle in n]
    if len(partial) == 1: return partial[0]
    if len(partial) > 1:
        raise SystemExit(f"Ambiguous site '{sid_or_name}' — matches: " +
                         ", ".join(data[s].get("name","") for s in partial))
    raise SystemExit(f"No site matches '{sid_or_name}'. Available: " +
                     ", ".join(s.get("name","") or sid for sid, s in data.items()))


# ── Output formatting helpers ─────────────────────────────────────────

def _fmt_bytes(n):
    try: n = int(n)
    except Exception: return ""
    if not n: return ""
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024: return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}P"


def _print_table(rows, headers):
    if not rows:
        print("(no rows)")
        return
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(*[str(c) for c in r]))


def _maybe_json(data, args):
    """v3.60 (Phase 12, #98): if --json was passed, print `data` as
    JSON and return True (caller should return early). Otherwise return
    False so the caller does its normal human-readable formatting.

    Centralizes the --json handling so every command behaves the same
    way without each handler re-implementing it."""
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return True
    return False


# ── Commands ──────────────────────────────────────────────────────────

def cmd_status(args):
    if args.site:
        sid = _resolve_site(args.site)
        data = _request("GET", "/api/status").get(sid, {})
        if args.json:
            print(json.dumps(data, indent=2, default=str)); return
        print(f"Site: {data.get('name')}  (id={sid})")
        print(f"State: {data.get('state')}")
        c = data.get("counts", {})
        print(f"Queue: {c.get('pending',0)} pending · {c.get('running',0)} running")
        print(f"Done:  {c.get('done',0)} · failed {c.get('failed',0)} · review {c.get('needs_review',0)}")
        if data.get("login_status"): print(f"Login: {data['login_status']}")
        return
    # Multi-site overview
    data = _request("GET", "/api/status", query={"light": 1})
    if args.json:
        print(json.dumps(data, indent=2, default=str)); return
    dash = _request("GET", "/api/dashboard")
    if dash.get("ok"):
        t = dash.get("totals", {})
        td = dash.get("today", {})
        print(f"Totals: {t.get('pending',0)} pending · {t.get('running',0)} running · "
              f"{t.get('done',0)} done · {t.get('failed',0)} failed · "
              f"{t.get('needs_review',0)} review")
        print(f"Today:  {td.get('done',0)} done · {td.get('needs_review',0)} review · "
              f"{td.get('failed',0)} failed")
        if dash.get("eta_seconds"):
            print(f"ETA:    {int(dash['eta_seconds']//60)}m {int(dash['eta_seconds']%60)}s")
        print()
    rows = []
    for sid, s in data.items():
        c = s.get("counts", {})
        rows.append([
            sid[:8], s.get("name","")[:24], s.get("state","")[:10],
            c.get("pending",0), c.get("running",0),
            c.get("done",0), c.get("failed",0), c.get("needs_review",0),
        ])
    _print_table(rows, ["ID", "NAME", "STATE", "PENDING", "RUNNING", "DONE", "FAIL", "REVIEW"])


def cmd_add(args):
    sid = _resolve_site(args.site) if args.site else None
    urls = list(args.urls)
    if not urls and not sys.stdin.isatty():
        urls = [l.strip() for l in sys.stdin if l.strip()]
    if not urls:
        raise SystemExit("No URLs to add (pass as args or pipe to stdin)")
    if sid:
        text = "\n".join(urls)
        r = _request("POST", f"/api/sites/{sid}/load_urls",
                     body={"text": text, "mode": args.mode or "append"})
        added = r.get("added", 0); dupes = r.get("dupes_skipped", 0)
        print(f"Added {added} URL(s) to '{args.site}' ({dupes} duplicate{'s' if dupes!=1 else ''} skipped)")
        return
    # No site specified — route per URL via /api/quick_add
    added = skipped = 0
    for u in urls:
        r = _request("POST", "/api/quick_add", body={"url": u})
        if r.get("ok"): added += 1
        else: skipped += 1
    print(f"Routed {added} URL(s); {skipped} unrouted (no site matched the hostname pattern)")


def cmd_action(args):
    """Generic dispatcher for start/pause/resume/stop/clear/retry."""
    sid = _resolve_site(args.site)
    r = _request("POST", f"/api/sites/{sid}/{args.action}")
    if r.get("ok"):
        if r.get("blocked_by") == "rate_limited":
            print(f"⚠ Site is rate limited — start blocked")
        elif r.get("blocked_by") == "low_disk":
            print(f"⚠ Site is low on disk — start blocked")
        else:
            print(f"✓ {args.action} sent to {sid}")
    else:
        print(f"✗ {r.get('error', 'failed')}", file=sys.stderr)
        sys.exit(1)


def cmd_logs(args):
    sid = _resolve_site(args.site)
    if args.follow:
        cursor = 0
        try:
            while True:
                r = _request("GET", f"/api/sites/{sid}/events",
                             query={"after": cursor, "limit": 200,
                                    "kind": args.kind or None})
                for e in r.get("events", []):
                    _print_event(e, args.json)
                cursor = r.get("last_seq", cursor)
                time.sleep(1.5)
        except KeyboardInterrupt:
            return
    r = _request("GET", f"/api/sites/{sid}/events",
                 query={"limit": args.tail, "kind": args.kind or None})
    events = r.get("events", [])
    # show last N
    for e in events[-args.tail:]:
        _print_event(e, args.json)


def _print_event(e, as_json=False):
    if as_json:
        print(json.dumps(e, default=str)); return
    ts = (e.get("ts","") or "")[11:19]
    kind = (e.get("kind","") or "")[:12]
    msg = (e.get("message","") or "").replace("\n"," ")
    url = (e.get("url","") or "")[-50:]
    print(f"{ts}  {kind:<14}  {url:<50}  {msg[:80]}")


def cmd_sites(args):
    data = _request("GET", "/api/status", query={"light": 1})
    if args.json:
        slim = {sid: {"name": s.get("name"), "state": s.get("state"),
                      "counts": s.get("counts")} for sid, s in data.items()}
        print(json.dumps(slim, indent=2, default=str)); return
    rows = [[sid[:8], s.get("name",""), s.get("state","")]
            for sid, s in data.items()]
    _print_table(rows, ["ID", "NAME", "STATE"])


def cmd_learned_export(args):
    sid = _resolve_site(args.site)
    data = _request("GET", "/api/status").get(sid, {})
    learned = (data.get("config") or {}).get("learned") or {}
    out = {"site_id": sid, "name": data.get("name"),
           "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "learned": learned}
    print(json.dumps(out, indent=2, default=str))


def cmd_learned_import(args):
    sid = _resolve_site(args.site)
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raise SystemExit("Pipe a JSON profile to stdin: bdctl learned import --site X < profile.json")
    try:
        data = json.loads(raw)
    except Exception as e:
        raise SystemExit(f"Invalid JSON: {e}")
    learned = data.get("learned") or data  # accept either {learned:{...}} or flat
    r = _request("PUT", f"/api/sites/{sid}", body={"learned": learned})
    if r.get("ok"):
        print(f"✓ Learned selectors imported into {args.site}")
    else:
        print(f"✗ {r.get('error', 'failed')}", file=sys.stderr); sys.exit(1)


def cmd_deep_detect(args):
    """v3.66.5: drive /api/dev/deep_detect from the CLI.

    Three input modes:
      • --html FILE              : read HTML from a file
      • --html -                 : read HTML from stdin
      • --url URL                : fetch URL first with stdlib urllib,
                                    then send the HTML to the API
                                    (no Playwright; static HTML only)

    Most useful for testing detection against arbitrary pages without
    touching the GUI. The server runs the offline detector by
    default; pass --live to enable HEAD probing and meta-refresh
    follow. Pass --poll to run async-export workflow polling
    (default off).

    Output is the full report as JSON. Use jq to slice it:
        bdctl deep-detect --url X | jq '.report.best_download'
    """
    # ── Resolve HTML input ─────────────────────────────────────────
    html = ""
    base_url = args.base_url or ""

    if args.html:
        if args.html == "-":
            html = sys.stdin.read()
            if not base_url:
                # Reading from stdin with no --base-url; resolution of
                # relative URLs inside the page will be best-effort.
                pass
        else:
            try:
                with open(args.html, "r", encoding="utf-8",
                          errors="replace") as f:
                    html = f.read()
            except OSError as e:
                raise SystemExit(f"Can't read {args.html!r}: {e}")
    elif args.url:
        # Fetch the URL ourselves with urllib (no Playwright).
        # For pages that need JS, the server-side `live=True` path
        # is still preferred — but this is enough for quick tests.
        ua = ("BulkDownloader/3.66 deep-detect CLI "
              "(+offline static analysis)")
        try:
            req = urllib.request.Request(
                args.url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                # Honor declared charset; fall back to utf-8 with
                # replace so we never crash on bytes.
                charset = resp.headers.get_content_charset() or "utf-8"
                try:
                    html = raw.decode(charset, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")
                if not base_url:
                    base_url = resp.url
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise SystemExit(f"Fetch failed: {e}")
    else:
        raise SystemExit(
            "Need one of --html FILE | --html - | --url URL")

    # ── Build request body ─────────────────────────────────────────
    body = {"html": html}
    if base_url:
        body["base_url"] = base_url
    if args.prefer_resolution:
        body["prefer_resolution"] = args.prefer_resolution
    if args.live:
        body["live"] = True
        if args.poll:
            body["poll_async_workflows"] = True
        if args.max_probes is not None:
            body["max_probes"] = args.max_probes
        if args.probe_timeout is not None:
            body["probe_timeout"] = args.probe_timeout
        if args.probe_parallelism is not None:
            body["probe_parallelism"] = args.probe_parallelism
        if args.max_total_probe_time is not None:
            body["max_total_probe_time"] = args.max_total_probe_time

    # ── POST it ────────────────────────────────────────────────────
    r = _request("POST", "/api/dev/deep_detect", body=body)
    if not isinstance(r, dict):
        print(json.dumps({"_raw": r}, indent=2, default=str))
        return
    if not r.get("ok"):
        # Server reported a structured failure — surface it on stderr
        # and exit non-zero so scripts can react.
        print(json.dumps(r, indent=2, default=str), file=sys.stderr)
        sys.exit(1)

    report = r.get("report") or {}

    # ── Output ─────────────────────────────────────────────────────
    if args.json or not sys.stdout.isatty():
        # Default to JSON when stdout isn't a tty (piped to jq, etc.)
        print(json.dumps(report, indent=2, default=str))
        return

    # Human-readable summary for terminal output.
    bd = report.get("best_download") or {}
    bl = report.get("best_login") or {}
    blockers = report.get("blockers") or {}
    print(f"Source breakdown: {report.get('source_breakdown') or {}}")
    print()
    if bl:
        print(f"Best login  : confidence={bl.get('confidence')} "
              f"types={bl.get('login_types')}")
        if bl.get("safe_fields"):
            print(f"  fields    : {list(bl['safe_fields'].keys())}")
        if bl.get("submit_selector"):
            print(f"  submit    : {bl['submit_selector']}")
        if bl.get("bot_defenses"):
            print(f"  defenses  : {bl['bot_defenses']}")
    else:
        print("Best login  : (none)")
    print()
    if bd:
        res = (bd.get("resolution") or {}).get("label") or "?"
        print(f"Best download: {bd.get('source_type')} @ {res}  "
              f"score={bd.get('score')}")
        if bd.get("url"):
            print(f"  url       : {bd['url']}")
        if bd.get("codec"):
            print(f"  codec     : {bd['codec']}")
        if bd.get("size_bytes"):
            print(f"  size      : {_fmt_bytes(bd['size_bytes'])}")
    else:
        print("Best download: (none)")
    print()
    cands = report.get("download_candidates") or []
    if cands:
        print(f"All download candidates ({len(cands)}):")
        rows = []
        for c in cands[:12]:
            res = (c.get("resolution") or {}).get("label") or ""
            rows.append([
                str(c.get("score") or 0),
                c.get("source_type") or "",
                res,
                (c.get("url") or "")[:60],
            ])
        _print_table(rows, ["score", "type", "res", "url"])
    if report.get("rejected"):
        print()
        print(f"Rejected: {len(report['rejected'])} candidate(s) "
              "— see --json for reasons")
    if blockers.get("blocked") or blockers.get("captchas") \
            or blockers.get("drm_systems"):
        print()
        print("⚠ Blockers detected:")
        if blockers.get("captchas"):
            print(f"  CAPTCHA: {blockers['captchas']}")
        if blockers.get("drm_systems"):
            print(f"  DRM:     {blockers['drm_systems']}")
    if report.get("probes"):
        probes = report["probes"]
        print()
        print(f"Probes: performed={probes.get('probes_performed')} "
              f"meta_refresh={probes.get('meta_refresh') is not None}")
        if probes.get("disclaimer"):
            print()
            print(f"NOTICE: {probes['disclaimer']}")


def cmd_dd_diff(args):
    """v3.66.13 (P10): snapshot-based deep_detect diffing for dev work.

    Runs `deep_detect()` against the local corpus at
    `tests/fixtures/deep_detect/` and either:
      * saves the current output as a named baseline (--save NAME), or
      * compares the current output against a saved baseline (default).

    Distinct from `dd-replay`:
      * dd-replay tests against the CI-committed `expected.json` files
        — the "what should the world see" snapshot.
      * dd-diff lets you snapshot the *current* behaviour to a private
        baseline, make code changes, and see what shifted. Use for
        "what does my patch actually do?" exploration.

    Baselines live at `~/.config/bdctl/dd-baselines/<name>.json`.
    Default name is "default".

    Examples:
        # snapshot current behaviour
        bdctl dd-diff --save before

        # ... make code changes ...

        bdctl dd-diff --against before
        # → unified diff per fixture, grouped by name

        # quick "did anything change?" without naming a baseline:
        bdctl dd-diff --save default
        # ... edit ...
        bdctl dd-diff          # diffs against "default"

    Reuses tools/dd-replay.py for the runner + diff machinery so a
    single normalization is shared with the CI harness and the score
    audit (P11).
    """
    # Resolve repo root from this file's location. bdctl.py lives at
    # the repo root, so __file__'s parent is the repo.
    repo_root = Path(__file__).resolve().parent
    tools_dir = repo_root / "tools"
    if not tools_dir.is_dir():
        print(f"✗ tools/ not found at {tools_dir} — is bdctl.py at "
              f"the repo root?", file=sys.stderr)
        sys.exit(2)

    # Lazy-load dd-replay.py via importlib (hyphenated filename).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dd_replay", tools_dir / "dd-replay.py")
    if spec is None or spec.loader is None:
        print(f"✗ couldn't load {tools_dir/'dd-replay.py'}",
              file=sys.stderr)
        sys.exit(2)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)

    # Where baselines live.
    baseline_dir = _config_dir() / "dd-baselines"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / f"{args.name}.json"

    # Filter fixtures.
    fixtures = replay._list_fixtures(args.only)
    if not fixtures:
        print(f"✗ no fixtures found in {replay.CORPUS_DIR}",
              file=sys.stderr)
        sys.exit(2)

    # Build the current snapshot.
    current: dict[str, dict] = {}
    for d in fixtures:
        try:
            current[d.name] = replay._run_fixture(d)
        except Exception as e:
            print(f"✗ ERROR running {d.name}: {e}", file=sys.stderr)
            sys.exit(2)

    if args.save:
        baseline_path.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n")
        if _maybe_json({"saved": args.name,
                        "path": str(baseline_path),
                        "fixtures": sorted(current)}, args):
            return
        print(f"✓ baseline {args.name!r} saved "
              f"({len(current)} fixtures → {baseline_path})")
        return

    if args.list_baselines:
        existing = sorted(baseline_dir.glob("*.json"))
        rows = [
            {
                "name": p.stem,
                "fixtures": len(json.loads(p.read_text())),
                "size_bytes": p.stat().st_size,
                "path": str(p),
            }
            for p in existing
        ]
        if _maybe_json({"baselines": rows}, args):
            return
        if not existing:
            print("(no baselines saved yet)")
            return
        for r in rows:
            print(f"  {r['name']:30s} {r['fixtures']:3d} fixtures"
                  f"   {r['size_bytes']} bytes")
        return

    # Diff mode.
    if not baseline_path.exists():
        msg = (f"no baseline {args.name!r} at {baseline_path}. "
               f"Save one first:  bdctl dd-diff --save --name "
               f"{args.name}")
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(f"✗ {msg}", file=sys.stderr)
        sys.exit(2)
    baseline = json.loads(baseline_path.read_text())

    # Build a structured per-fixture result.
    results: list[dict] = []
    for name in sorted(set(baseline) | set(current)):
        if name not in current:
            results.append({"name": name, "status": "gone", "diff": ""})
        elif name not in baseline:
            results.append({"name": name, "status": "new", "diff": ""})
        else:
            diff = replay._diff(baseline[name], current[name], name)
            results.append({
                "name": name,
                "status": "changed" if diff else "same",
                "diff": diff,
            })

    n_changed = sum(1 for r in results if r["status"] == "changed")
    n_added = sum(1 for r in results if r["status"] == "new")
    n_removed = sum(1 for r in results if r["status"] == "gone")
    n_total = len(results)

    if _maybe_json({
        "baseline": args.name,
        "summary": {
            "total": n_total,
            "changed": n_changed,
            "new": n_added,
            "gone": n_removed,
        },
        "results": results,
    }, args):
        if n_changed or n_added or n_removed:
            sys.exit(1)
        return

    for r in results:
        if r["status"] == "gone":
            print(f"  GONE  {r['name']} (in baseline, not in current corpus)")
        elif r["status"] == "new":
            print(f"  NEW   {r['name']} (in current corpus, not in baseline)")
        elif r["status"] == "changed":
            print(f"  DIFF  {r['name']}")
            sys.stdout.write(r["diff"])
        else:  # same
            if not args.quiet:
                print(f"  same  {r['name']}")

    summary = (f"\nvs baseline {args.name!r}: "
               f"{n_changed} changed, {n_added} new, {n_removed} gone "
               f"({n_total} fixtures total)")
    print(summary)
    if n_changed or n_added or n_removed:
        sys.exit(1)


def cmd_teach(args):
    """Open a Manual Login session for the site. The browser opens
    server-side; you finish login in that window, then run
    'bdctl teach --site X --done' to capture and learn."""
    sid = _resolve_site(args.site)
    if args.done:
        r = _request("POST", f"/api/sites/{sid}/login_manual_done")
        if r.get("ok"): print(f"✓ Captured: {r.get('message','')}")
        else: print(f"✗ {r.get('message','')}", file=sys.stderr); sys.exit(1)
        return
    if args.cancel:
        r = _request("POST", f"/api/sites/{sid}/login_manual_cancel")
        print(r.get("message","") or "Cancelled"); return
    r = _request("POST", f"/api/sites/{sid}/manual_login")
    if r.get("ok"):
        print(f"✓ Manual login window opened on the server.")
        print(f"  Log in, then run: bdctl teach --site {args.site} --done")
    else:
        print(f"✗ {r.get('message','')}", file=sys.stderr); sys.exit(1)


def cmd_completion(args):
    """Phase 81 (v3.40.0): emit shell completion script.

    The scripts call `bdctl _complete-sites` for live site-ID completion
    so completion stays current as sites are added/removed."""
    cmds = ["status", "add", "start", "pause", "stop", "logs",
            "teach", "learned", "token", "completion"]
    if args.shell == "bash":
        sys.stdout.write(f"""# Bulk Downloader bdctl completion for bash
# Install:  source <(bdctl completion bash)
# Or permanently:
#   bdctl completion bash > ~/.local/share/bash-completion/completions/bdctl
_bdctl_complete() {{
  local cur prev cmds
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  prev="${{COMP_WORDS[COMP_CWORD-1]}}"
  cmds="{ ' '.join(cmds) }"
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=( $(compgen -W "$cmds" -- "$cur") )
    return
  fi
  if [ "$prev" = "--site" ]; then
    local sites
    sites="$(bdctl _complete-sites 2>/dev/null)"
    COMPREPLY=( $(compgen -W "$sites" -- "$cur") )
    return
  fi
  COMPREPLY=( $(compgen -W "--site --tail --follow --help" -- "$cur") )
}}
complete -F _bdctl_complete bdctl
""")
    elif args.shell == "zsh":
        sys.stdout.write(f"""# Bulk Downloader bdctl completion for zsh
# Install:  source <(bdctl completion zsh)
# Or permanently, add to your fpath as `_bdctl`.
#compdef bdctl
_bdctl() {{
  local -a cmds sites
  cmds=({ ' '.join(f'"{c}"' for c in cmds) })
  if (( CURRENT == 2 )); then
    _describe 'command' cmds
    return
  fi
  if [[ "${{words[CURRENT-1]}}" == "--site" ]]; then
    sites=("${{(@f)$(bdctl _complete-sites 2>/dev/null)}}")
    _describe 'site' sites
    return
  fi
  _arguments '--site[site id]:site:' '--tail[number of events]:N:' '--follow[stream]' '--help[show help]'
}}
compdef _bdctl bdctl
""")
    elif args.shell == "fish":
        sys.stdout.write(f"""# Bulk Downloader bdctl completion for fish
# Install:  bdctl completion fish > ~/.config/fish/completions/bdctl.fish
complete -c bdctl -n '__fish_use_subcommand' -a '{ ' '.join(cmds) }'
complete -c bdctl -l site -d 'site id' -a '(bdctl _complete-sites 2>/dev/null)'
complete -c bdctl -l tail -d 'number of events to show'
complete -c bdctl -l follow -d 'stream events live'
""")


def cmd_ytdlp_archive_stats(args):
    """v3.43.75: show yt-dlp download_archive stats."""
    r = _request("GET", "/api/ytdlp_archive/stats")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"ytdlp-archive: {err}", file=sys.stderr)
        sys.exit(1)
    if not r.get("configured"):
        print("  No archive path configured on any site.")
        print("  Set ytdlp_archive_path in a site's config.")
        return
    print(f"  archive:   {r.get('archive_path')}")
    stats = r.get("stats", {})
    print(f"  total:     {stats.get('total', 0)} entries")
    print(f"  exists:    {stats.get('path_exists', False)}")
    by_ext = stats.get("by_extractor", {})
    if by_ext:
        print(f"  by extractor:")
        for ext, n in sorted(by_ext.items(), key=lambda x: -x[1]):
            print(f"    {ext:20} {n}")


def cmd_scrapers_list(args):
    """v3.43.75: show locally-installed CommunityScrapers."""
    r = _request("GET", "/api/community_scrapers/status")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"scrapers list: {err}", file=sys.stderr)
        sys.exit(1)
    names = r.get("installed_names", [])
    print(f"  installed: {len(names)}")
    for n in names:
        print(f"    {n}")


def cmd_scrapers_available(args):
    """v3.43.75: list scrapers available from CommunityScrapers GitHub."""
    url = "/api/community_scrapers/index"
    if args.force:
        url += "?force=1"
    r = _request("GET", url)
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"scrapers available: {err}", file=sys.stderr)
        sys.exit(1)
    entries = r.get("entries", [])
    if args.filter:
        flt = args.filter.lower()
        entries = [e for e in entries if flt in e["name"].lower()]
    print(f"  available: {len(entries)}")
    for e in entries[:200]:
        print(f"    {e['name']:35} {e['sha'][:8]}  {e['size']:8} bytes")
    if len(entries) > 200:
        print(f"  ...and {len(entries) - 200} more")


def cmd_scrapers_install(args):
    """v3.43.75: install one CommunityScrapers entry."""
    r = _request("POST", "/api/community_scrapers/install",
                 body={"scraper_name": args.name,
                       "stash_dir": args.stash_dir})
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"scrapers install: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"  installed: {r.get('scraper_name')}")
    for f in r.get("files_written", []):
        print(f"    {f}")


def cmd_phoenix_lookup(args):
    """v3.43.76: look up a URL in the PhoenixAdult catalog."""
    r = _request("POST", "/api/phoenix/lookup", body={"url": args.url})
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"phoenix-lookup: {err}", file=sys.stderr)
        sys.exit(1)
    m = r.get("match")
    if m is None:
        print(f"  no catalog match for {args.url}")
        return
    print(f"  url:        {args.url}")
    print(f"  brand:      {m.get('name')} ({m.get('brand_id')})")
    print(f"  network:    {m.get('network_display')}")
    print(f"  confidence: {m.get('confidence')}")
    print(f"  matched:    {m.get('matched_pattern')}")


def cmd_supervisor_status(args):
    """v3.43.76: show bandwidth supervisor stats."""
    r = _request("GET", "/api/supervisor/status")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"supervisor-status: {err}", file=sys.stderr)
        sys.exit(1)
    stats = r.get("stats", {})
    print(f"  enabled:      {stats.get('enabled')}")
    g = stats.get("global", {})
    print(f"  global rate:  {g.get('rate_bps', 0)} bps "
          f"({g.get('rate_bps', 0) / 1e6:.1f} MB/s)")
    print(f"  global tokens: {g.get('tokens_available', 0)}")
    print(f"  global acquires: {g.get('acquires', 0)}")
    print(f"  global wait_s:  {g.get('total_wait_s', 0)}")
    per_site = stats.get("per_site", {})
    if per_site:
        print("  per-site:")
        for sid, st in per_site.items():
            rate_mb = st.get("rate_bps", 0) / 1e6
            print(f"    {sid:20} {rate_mb:.1f} MB/s  "
                  f"acquires={st.get('acquires', 0)}  "
                  f"wait_s={st.get('total_wait_s', 0)}")


def cmd_search_site(args):
    """v3.43.77: search one site for a query."""
    body = {"site_id": args.site, "query": args.query,
            "max_results": int(args.max)}
    r = _request("POST", "/api/search/site", body=body)
    if not isinstance(r, dict):
        print("search: no response", file=sys.stderr); sys.exit(1)
    if not r.get("ok"):
        print(f"search: {r.get('error', '?')}", file=sys.stderr); sys.exit(1)
    hits = r.get("hits", [])
    print(f"  query:      {r.get('query')}")
    print(f"  search URL: {r.get('search_url')}")
    print(f"  hits:       {len(hits)} (in {r.get('elapsed_s', 0)}s)")
    for h in hits[:30]:
        title = h.get("title", "")[:70]
        print(f"    {h.get('url')}")
        if title:
            print(f"      {title}")
    if len(hits) > 30:
        print(f"  ...and {len(hits) - 30} more")


def cmd_search_all(args):
    """v3.43.77: search every searchable site for a query."""
    body = {"query": args.query,
            "max_results_per_site": int(args.max_per_site)}
    r = _request("POST", "/api/search/all", body=body)
    if not isinstance(r, dict):
        print("search-all: no response", file=sys.stderr); sys.exit(1)
    if not r.get("ok"):
        print(f"search-all: {r.get('error', '?')}", file=sys.stderr); sys.exit(1)
    stats = r.get("stats", {})
    print(f"  query:    {r.get('query')}")
    print(f"  searched: {stats.get('sites_searched', 0)} sites "
          f"({stats.get('sites_with_results', 0)} had results)")
    print(f"  skipped:  {stats.get('sites_skipped', 0)} (not searchable)")
    print(f"  failed:   {stats.get('sites_failed', 0)}")
    print(f"  total:    {stats.get('total_hits', 0)} hits across all sites\n")
    for sid, result in (r.get("results") or {}).items():
        hits = result.get("hits", [])
        if not result.get("ok"):
            print(f"  [{sid}] error: {result.get('error', '?')}")
            continue
        if not hits:
            continue
        print(f"  [{sid}] {len(hits)} hit(s)")
        for h in hits[:5]:
            print(f"    {h.get('url')}")
        if len(hits) > 5:
            print(f"    ...and {len(hits) - 5} more")


# ── v3.53 (Phase 6): power-user commands for the Phase 1-5 surfaces ─────

def _health_refusal_envelope(exc):
    """Render a RequestFailed as the JSON `health --json` promised.

    The server's own structured body becomes the envelope, so a consumer
    reading `.version` or `.degraded` finds them exactly where a 200 puts
    them, and `bdctl` names the transport outcome alongside. A 503 carrying
    `degraded: credential_vault_locked`, a 500 carrying some other error, and
    a server that never answered are three different diagnoses and stay three
    different objects (CLAUDE.md A7).
    """
    envelope = dict(exc.body_json) if isinstance(exc.body_json, dict) else {}
    envelope.setdefault("ok", False)
    detail = {
        "error_kind": exc.kind,
        "http_status": exc.status,
        "http_reason": exc.reason,
        "server_url": _server_url(),
        "path": exc.path,
    }
    if not isinstance(exc.body_json, dict) and exc.body_text:
        detail["raw_body"] = exc.body_text[:2000]
    envelope["bdctl"] = detail
    return envelope


def cmd_health(args):
    """v3.53: hit the /api/health probe (added in v3.47.8). Exit code
    0 if healthy, 1 if degraded — usable in a cron heartbeat or a
    monitoring script.

    ROW 467: a non-200 is an ANSWER, not an absence. /api/health returns 503
    with a full body whenever the probe is degraded -- a locked vault, a db
    error, a runner status failure -- and `--json` used to print nothing at
    all in that case. The human path is unchanged: it still fails with the
    same diagnostic and the same nonzero exit.
    """
    try:
        r = _request("GET", "/api/health")
    except RequestFailed as exc:
        if not getattr(args, "json", False):
            raise                      # human path: unchanged diagnostic+exit
        print(json.dumps(_health_refusal_envelope(exc), indent=2, default=str))
        # Loud as well as structured: the operator's own words on stderr, and
        # a nonzero exit, so a refusal never reads as an empty success.
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if not isinstance(r, dict):
        print("health: unexpected response", file=sys.stderr)
        sys.exit(2)
    ok = r.get("ok", False)
    if _maybe_json(r, args):
        sys.exit(0 if ok else 1)
    print(f"  status:           {'OK' if ok else 'DEGRADED'}")
    print(f"  version:          {r.get('version', '?')}")
    up = r.get("uptime_s")
    if up is not None:
        h, rem = divmod(int(up), 3600)
        m, s = divmod(rem, 60)
        print(f"  uptime:           {h}h {m}m {s}s")
    print(f"  queue depth:      {r.get('queue_depth', '?')}")
    print(f"  active downloads: {r.get('active_downloads', '?')}")
    print(f"  sites loaded:     {r.get('sites_loaded', '?')}")
    print(f"  db ok:            {r.get('db_ok', '?')}")
    sys.exit(0 if ok else 1)


def cmd_library_stats(args):
    """v3.53: print library collection stats."""
    r = _request("GET", "/api/library/stats")
    if not isinstance(r, dict) or not r.get("ok"):
        print("library stats: failed", file=sys.stderr)
        sys.exit(1)
    s = r.get("stats", {})
    print(f"  total files:   {s.get('total_files', 0)}")
    print(f"  total size:    {_fmt_bytes(s.get('total_size', 0))}")
    print(f"  watched:       {s.get('watched_files', 0)}")
    print(f"  unrated:       {s.get('unrated_files', 0)}")
    print(f"  missing:       {s.get('missing_files', 0)}")
    by_studio = s.get("by_studio") or []
    if by_studio:
        print("  top studios:")
        for row in by_studio[:5]:
            print(f"    {row.get('k', '?'):24} {row.get('n', 0):>5} files"
                  f"  {_fmt_bytes(row.get('s', 0))}")


def cmd_library_scan(args):
    """v3.53: kick off a library folder scan, optionally wait for it."""
    body = {}
    if args.root:
        body["roots"] = args.root  # list — argparse nargs='+'
    r = _request("POST", "/api/library/scan/start", body=body)
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"library scan: {err}", file=sys.stderr)
        sys.exit(1)
    print("  scan started")
    if not args.wait:
        print("  poll with: bdctl library scan-status")
        return
    # Poll until done
    import time as _t
    while True:
        _t.sleep(1.0)
        st = _request("GET", "/api/library/scan/status")
        scan = st.get("scan", {}) if isinstance(st, dict) else {}
        if not scan.get("running"):
            print(f"  done: seen={scan.get('seen', 0)} "
                  f"added={scan.get('added', 0)} "
                  f"updated={scan.get('updated', 0)} "
                  f"missing={scan.get('missing_marked', 0)} "
                  f"errors={scan.get('errors', 0)}")
            break
        print(f"  scanning… {scan.get('current_root', '')} "
              f"(seen {scan.get('seen', 0)})")


def cmd_library_scan_status(args):
    """v3.53: print the current/last library scan state."""
    r = _request("GET", "/api/library/scan/status")
    scan = r.get("scan", {}) if isinstance(r, dict) else {}
    if scan.get("never_run"):
        print("  no scan has run yet")
        return
    print(f"  running:       {scan.get('running', False)}")
    print(f"  current root:  {scan.get('current_root', '')}")
    print(f"  seen:          {scan.get('seen', 0)}")
    print(f"  added:         {scan.get('added', 0)}")
    print(f"  updated:       {scan.get('updated', 0)}")
    print(f"  missing-mark:  {scan.get('missing_marked', 0)}")
    print(f"  errors:        {scan.get('errors', 0)}")
    print(f"  elapsed:       {scan.get('elapsed_s', 0)}s")


def cmd_audit(args):
    """v3.53: print recent config-change audit events."""
    r = _request("GET", "/api/audit/recent", query={"limit": args.limit})
    if not isinstance(r, dict) or not r.get("ok"):
        print("audit: failed", file=sys.stderr)
        sys.exit(1)
    if _maybe_json(r, args):
        return
    rows = r.get("rows", [])
    if not rows:
        print("(no audit events)")
        return
    import datetime as _dt
    table = []
    for row in rows:
        ts = row.get("ts", 0)
        try:
            when = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            when = str(ts)
        table.append([when, row.get("action", ""),
                      row.get("target", ""), row.get("actor", "")])
    _print_table(table, ["WHEN", "ACTION", "TARGET", "ACTOR"])


def cmd_site_export(args):
    """v3.53: export a site config to stdout or a file."""
    sid = _resolve_site(args.site)
    query = {"include_secrets": "1"} if args.include_secrets else None
    r = _request("GET", f"/api/sites/{sid}/export", query=query)
    text = json.dumps(r, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  exported {sid} -> {args.out}")
    else:
        print(text)


def cmd_site_import(args):
    """v3.53: import a site config from a file (or stdin)."""
    if args.file == "-":
        payload = json.loads(sys.stdin.read())
    else:
        with open(args.file, encoding="utf-8") as f:
            payload = json.load(f)
    r = _request("POST", "/api/sites/import", body=payload)
    if not isinstance(r, dict) or not r.get("ok"):
        errs = r.get("errors", []) if isinstance(r, dict) else [str(r)]
        print("site import failed:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  imported as new site: {r.get('id')}")
    for w in r.get("warnings", []):
        print(f"  warning: {w}")


def cmd_site_validate(args):
    """v3.53: dry-run validate a site config file without importing."""
    if args.file == "-":
        cfg = json.loads(sys.stdin.read())
    else:
        with open(args.file, encoding="utf-8") as f:
            cfg = json.load(f)
    # If it's an export envelope, validate the inner config
    if isinstance(cfg, dict) and "config" in cfg and "schema" in cfg:
        cfg = cfg["config"]
    r = _request("POST", "/api/sites/validate", body=cfg)
    if not isinstance(r, dict):
        print("validate: unexpected response", file=sys.stderr)
        sys.exit(2)
    errs = r.get("errors", [])
    warns = r.get("warnings", [])
    if errs:
        print(f"INVALID ({len(errs)} error(s)):")
        for e in errs:
            print(f"  ERROR:   {e}")
    else:
        print("VALID")
    for w in warns:
        print(f"  warning: {w}")
    sys.exit(0 if r.get("ok") else 1)


def cmd_pause_all(args):
    """v3.53: pause every site's runner."""
    r = _request("POST", "/api/runners/pause_all")
    if _maybe_json(r, args):
        sys.exit(0 if isinstance(r, dict) and r.get("ok") else 1)
    if isinstance(r, dict) and r.get("ok"):
        print(f"  paused {r.get('paused', 0)} site(s)")
        for f in r.get("failures", []):
            print(f"  failed: {f.get('site_id')} — {f.get('error')}")
    else:
        print("pause-all: failed", file=sys.stderr)
        sys.exit(1)


def cmd_resume_all(args):
    """v3.53: resume every paused runner."""
    r = _request("POST", "/api/runners/resume_all")
    if _maybe_json(r, args):
        sys.exit(0 if isinstance(r, dict) and r.get("ok") else 1)
    if isinstance(r, dict) and r.get("ok"):
        print(f"  resumed {r.get('resumed', 0)} site(s)")
        for f in r.get("failures", []):
            print(f"  failed: {f.get('site_id')} — {f.get('error')}")
    else:
        print("resume-all: failed", file=sys.stderr)
        sys.exit(1)


def cmd_doctor(args):
    """v3.54: run the bd-doctor diagnostic pass + optionally diagnose a
    specific failure string."""
    # --diagnose <text>: just run the failure diagnoser, skip the full
    # environment pass.
    if args.diagnose:
        r = _request("POST", "/api/doctor/diagnose",
                     body={"error": args.diagnose})
        d = r.get("diagnosis", {}) if isinstance(r, dict) else {}
        print(f"  cause:       {d.get('cause', '?')}")
        print(f"  confidence:  {d.get('confidence', '?')}")
        print(f"  suggestion:  {d.get('suggestion', '')}")
        sys.exit(0 if d.get("matched") else 1)
    # Full diagnostic report
    r = _request("GET", "/api/doctor")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"doctor: {err}", file=sys.stderr)
        sys.exit(2)
    report = r.get("report", {})
    if _maybe_json(report, args):
        sys.exit(0 if report.get("ok") else 1)
    summary = report.get("summary", {})
    checks = report.get("checks", [])
    # Print each check, grouped by status icon
    sym = {"ok": "  OK  ", " warn": " WARN ", "warn": " WARN ",
           "fail": " FAIL "}
    for c in checks:
        icon = sym.get(c.get("status"), "  ?   ")
        print(f"[{icon}] {c.get('test', ''):22} {c.get('message', '')}")
        det = c.get("detail", {})
        if det.get("hint"):
            print(f"          hint: {det['hint']}")
    print()
    print(f"  {summary.get('ok', 0)} ok / "
          f"{summary.get('warn', 0)} warn / "
          f"{summary.get('fail', 0)} fail "
          f"({report.get('elapsed_ms', 0)} ms)")
    # Exit 0 if no FAILs, 1 if any
    sys.exit(0 if report.get("ok") else 1)


def cmd_thumbnails_regenerate(args):
    """v3.43.76: queue a thumbnail regeneration for a specific file."""
    r = _request("POST", "/api/thumbnails/regenerate",
                 body={"source_path": args.path, "mode": args.mode})
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"thumbnails-regenerate: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"  queued: {r.get('source_path')}")
    print(f"  watch with: bdctl status (or /api/thumbnails/status)")


def cmd_flare_status(args):
    """v3.43.74: show FlareSolverr ping + solve counters."""
    r = _request("GET", "/api/flaresolverr/status")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"flare-status: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"  endpoint:      {r.get('endpoint', '')}")
    ping = r.get("ping") or {}
    if ping.get("ok"):
        print(f"  ping:          OK")
        if ping.get("version"):
            print(f"  version:       {ping.get('version')}")
        if ping.get("userAgent"):
            print(f"  agent:         {ping.get('userAgent')[:70]}")
    else:
        print(f"  ping:          FAILED ({ping.get('error', '?')})")
    stats = r.get("stats") or {}
    print(f"  solves attempted:  {stats.get('solves_attempted', 0)}")
    print(f"  solves succeeded:  {stats.get('solves_succeeded', 0)}")
    print(f"  solves failed:     {stats.get('solves_failed', 0)}")
    if stats.get("last_error"):
        print(f"  last error:        {stats.get('last_error', '')[:80]}")


def cmd_multi_conn_probe(args):
    """v3.43.74: probe whether a URL supports multi-connection download."""
    body = {"url": args.url}
    if args.user_agent:
        body["user_agent"] = args.user_agent
    if args.referer:
        body["referer"] = args.referer
    r = _request("POST", "/api/multi_conn/probe", body=body)
    if not isinstance(r, dict):
        print("multi-conn-probe: no response", file=sys.stderr)
        sys.exit(1)
    print(f"  URL:           {args.url}")
    print(f"  probe ok:      {r.get('ok')}")
    print(f"  size:          {r.get('content_length_human', '?')}")
    print(f"  accept ranges: {r.get('accept_ranges')}")
    print(f"  viable:        {r.get('viable')}")
    if r.get("server"):
        print(f"  server:        {r.get('server')}")
    if r.get("final_url"):
        print(f"  final URL:     {r.get('final_url')}")
    if r.get("error"):
        print(f"  error:         {r.get('error')}")


def cmd_scrapling_status(args):
    """v3.43.73: show Scrapling availability + runtime counters."""
    r = _request("GET", "/api/scrapling/status")
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"scrapling-status: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"  scrapling:        {r.get('available')}")
    print(f"  stealthy_fetcher: {r.get('stealthy_fetcher')}")
    capabilities = r.get("capabilities") or {}
    for name in ("adaptive_selectors", "turnstile_bypass"):
        state = capabilities.get(name) or {}
        status = state.get("status") or "unknown"
        reason = state.get("reason") or "measurement_not_supplied"
        print(f"  {name}: {status} ({reason})")
    stats = r.get("stats") or {}
    print(f"  fingerprints built:    {stats.get('fingerprints_built', 0)}")
    rec_att = stats.get("recoveries_attempted", 0)
    rec_ok = stats.get("recoveries_succeeded", 0)
    rate = stats.get("recovery_success_rate", 0.0)
    print(f"  recoveries:            {rec_ok}/{rec_att} ({rate*100:.1f}%)")
    ts_det = stats.get("turnstile_detected", 0)
    ts_byp = stats.get("turnstile_bypassed", 0)
    ts_fail = stats.get("turnstile_failed", 0)
    print(f"  turnstile detected:    {ts_det}")
    print(f"  turnstile bypassed:    {ts_byp}")
    print(f"  turnstile failed:      {ts_fail}")


def cmd_dedup_status(args):
    """v3.43.72: show dedup module availability + registry stats + scan state."""
    r = _request("GET", "/api/dedup/status")
    if not isinstance(r, dict) or not r.get("ok"):
        print("dedup-status: unavailable", file=sys.stderr)
        sys.exit(1)
    print(f"  videohash:   {r.get('videohash_installed')}")
    print(f"  ffmpeg:      {r.get('ffmpeg_installed')}")
    print(f"  available:   {r.get('available')}")
    stats = r.get("stats") or {}
    print(f"  registry:    {stats.get('total', 0)} files hashed")
    last_ts = stats.get("last_computed_at", 0) or 0
    if last_ts:
        from datetime import datetime
        print(f"  last hash:   {datetime.fromtimestamp(last_ts).isoformat()}")
    scan = r.get("scan") or {}
    if scan.get("running"):
        done = scan.get("done", 0)
        total = scan.get("total", 0)
        pct = (100.0 * done / total) if total else 0.0
        print(f"  SCAN:        running {done}/{total} ({pct:.1f}%)")
        cur = scan.get("current_path", "")
        if cur:
            print(f"  current:     {cur[-70:]}")
    elif scan.get("summary"):
        s = scan["summary"]
        print(f"  last scan:   hashed={s.get('hashed', 0)} "
              f"skipped={s.get('skipped', 0)} "
              f"failed={s.get('failed', 0)} "
              f"in {s.get('elapsed_s', 0):.1f}s")


def cmd_dedup_scan(args):
    """v3.43.72: kick off a folder scan in the background."""
    body = {"root": args.root} if args.root else {}
    r = _request("POST", "/api/dedup/scan", body=body)
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"dedup-scan failed: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"scan started in background; root={r.get('root')}")
    print("watch with: bdctl dedup status")


def cmd_dedup_find(args):
    """v3.43.72: find duplicates of a specific file."""
    r = _request("POST", "/api/dedup/find",
                 body={"path": args.path, "distance": args.distance})
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error") if isinstance(r, dict) else str(r)
        print(f"dedup-find failed: {err}", file=sys.stderr)
        sys.exit(1)
    dups = r.get("duplicates", [])
    print(f"  hash:     {r.get('hash_hex')}")
    print(f"  threshold: {r.get('distance_threshold')} bits")
    print(f"  matches:  {len(dups)}")
    for d in dups[:20]:
        size_mb = d.get("file_size_bytes", 0) / (1024 * 1024)
        print(f"    dist={d['distance']:2d} size={size_mb:7.1f}MB "
              f"{d['path']}")
    if len(dups) > 20:
        print(f"  ...and {len(dups) - 20} more")


def cmd_tg_status(args):
    """v3.43.71: show Telegram bot status (running, last poll, counts)."""
    r = _request("GET", "/api/tg/status")
    if not isinstance(r, dict) or not r.get("ok"):
        print("tg-status: bot unavailable", file=sys.stderr)
        sys.exit(1)
    if not r.get("available"):
        print("Telegram bot: module unavailable", file=sys.stderr)
        sys.exit(1)
    enabled = r.get("enabled", False)
    running = r.get("running", False)
    last_poll = r.get("last_poll_wall", 0) or 0
    if last_poll:
        from datetime import datetime
        last_poll_str = datetime.fromtimestamp(last_poll).isoformat()
    else:
        last_poll_str = "never"
    print(f"  enabled:     {enabled}")
    print(f"  running:     {running}")
    print(f"  bot:         @{r.get('bot_username', '?')}")
    print(f"  allowlist:   {r.get('allowlist_size', 0)} chat IDs")
    print(f"  updates rcv: {r.get('updates_received', 0)}")
    print(f"  cmds exec:   {r.get('commands_executed', 0)}")
    print(f"  last poll:   {last_poll_str}")
    last_err = r.get("last_error", "")
    if last_err:
        print(f"  last error:  {last_err}")


def cmd_notify(args):
    """v3.43.70: POST a notification to /api/notify/apprise/test.
    Uses whatever apprise URLs the server has configured."""
    r = _request("POST", "/api/notify/apprise/test",
                 body={"title": args.title, "body": args.body})
    if not isinstance(r, dict) or not r.get("ok"):
        err = r.get("error", "unknown error") if isinstance(r, dict) else str(r)
        print(f"notify FAILED: {err}", file=sys.stderr)
        if isinstance(r, dict) and r.get("errors"):
            for e in r["errors"][:5]:
                print(f"  - {e.get('url')}: {e.get('error')}",
                      file=sys.stderr)
        sys.exit(1)
    sent = r.get("sent_count", 0)
    failed = r.get("failed_count", 0)
    skipped = r.get("skipped_count", 0)
    print(f"notify OK: sent={sent} failed={failed} skipped={skipped}")


def cmd_complete_sites(args):
    """Phase 81: internal. Print one site id per line for shell completion.
    Fails silently with empty output if the server is unreachable —
    completion shouldn't error out the user's prompt."""
    try:
        r = _request("GET", "/api/status", timeout=2)
        if isinstance(r, dict):
            for sid in r.keys():
                print(sid)
    except Exception:
        pass  # silent


def cmd_token(args):
    """Manage the local token. The default location is ~/.config/bdctl/token."""
    cdir = _config_dir()
    f = cdir / "token"
    if args.set is not None:
        cdir.mkdir(parents=True, exist_ok=True)
        f.write_text(args.set.strip(), encoding="utf-8")
        try:
            # Lock down permissions on POSIX so other users can't read it
            os.chmod(f, 0o600)
        except Exception: pass
        print(f"✓ Token saved to {f}")
        return
    if args.clear:
        try: f.unlink()
        except FileNotFoundError: pass
        print(f"✓ Token cleared")
        return
    if args.show:
        print(_token() or "(no token configured)")
        return
    print(f"Token path: {f}")
    print(f"Configured: {'yes' if _token() else 'no (anonymous calls used)'}")


# ── Entry point ────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        prog="bdctl",
        description="Command-line companion for Bulk Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"bdctl {VERSION}")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("status", help="show site overview or one site")
    sp.add_argument("--site", help="site ID or name (partial match OK)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("sites", help="list all sites")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sites)

    sp = sub.add_parser("add", help="add URLs to a site (or auto-route)")
    sp.add_argument("urls", nargs="*", help="URLs to add; if omitted, reads from stdin")
    sp.add_argument("--site", help="target site (else auto-route via /api/quick_add)")
    sp.add_argument("--mode", choices=("append","replace"), help="default: append")
    sp.set_defaults(func=cmd_add)

    for action, helptext in [
        ("start", "start processing the site's queue"),
        ("pause", "pause processing"),
        ("resume", "resume processing"),
        ("stop", "stop processing"),
        ("clear", "clear done/stopped jobs from queue"),
        ("retry", "reset failed jobs to pending"),
    ]:
        sp = sub.add_parser(action, help=helptext)
        sp.add_argument("--site", required=True)
        sp.set_defaults(func=cmd_action, action=action)

    sp = sub.add_parser("logs", help="show recent events for a site")
    sp.add_argument("--site", required=True)
    sp.add_argument("--tail", type=int, default=20)
    sp.add_argument("--follow", "-f", action="store_true", help="stream live (Ctrl-C to stop)")
    sp.add_argument("--kind", help="filter to event kind (state, takeover, mirror, ...)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_logs)

    sp = sub.add_parser("teach", help="manual login / takeover")
    sp.add_argument("--site", required=True)
    sp.add_argument("--done", action="store_true", help="finalize a pending manual session")
    sp.add_argument("--cancel", action="store_true", help="cancel a pending manual session")
    sp.set_defaults(func=cmd_teach)

    sp = sub.add_parser("learned", help="export/import learned selectors")
    lsub = sp.add_subparsers(dest="lcmd")
    lsp = lsub.add_parser("export", help="dump learned to stdout as JSON")
    lsp.add_argument("--site", required=True)
    lsp.set_defaults(func=cmd_learned_export)
    lsp = lsub.add_parser("import", help="load learned from stdin JSON")
    lsp.add_argument("--site", required=True)
    lsp.set_defaults(func=cmd_learned_import)

    # v3.66.5: deep-detect — drive the offline-or-live detector against
    # an HTML document or a URL. Useful for testing detection without
    # the GUI; also handy in scripts that want a structured report on
    # a page's login/download/provider/DRM situation.
    sp = sub.add_parser("deep-detect",
                        help="run deep_detect against HTML or a URL")
    sp.add_argument("--url",
                    help="URL to fetch + analyze "
                         "(plain GET, no Playwright)")
    sp.add_argument("--html",
                    help="path to HTML file (or '-' for stdin)")
    sp.add_argument("--base-url",
                    help="explicit base URL for resolving relative "
                         "links (defaults to the response URL when "
                         "--url is given)")
    sp.add_argument("--prefer-resolution",
                    help="bias toward a specific tier: "
                         "highest (default) | 1080p | 4k | 8k | ...")
    sp.add_argument("--live", action="store_true",
                    help="enable server-side HEAD probes + meta-refresh follow")
    sp.add_argument("--poll", action="store_true",
                    help="(with --live) poll async-export workflows")
    sp.add_argument("--max-probes", type=int,
                    help="cap on number of HEAD probes (default 10)")
    sp.add_argument("--probe-timeout", type=float,
                    help="per-probe HTTP timeout in seconds")
    sp.add_argument("--probe-parallelism", type=int,
                    help="concurrent HEAD probes (default 4, v3.66.10). "
                         "Set to 1 to disable concurrency.")
    sp.add_argument("--max-total-probe-time", type=float,
                    help="global wall-clock budget across all HEAD "
                         "probes in seconds (default 12.0)")
    sp.add_argument("--json", action="store_true",
                    help="print full JSON report (default when stdout "
                         "is not a tty)")
    sp.set_defaults(func=cmd_deep_detect)

    # v3.66.13 (P10): snapshot-diffing against the local fixture corpus.
    sp = sub.add_parser(
        "dd-diff",
        help="snapshot-diff deep_detect across the fixture corpus")
    sp.add_argument("--save", action="store_true",
                    help="save the current corpus output as a baseline")
    sp.add_argument("--name", default="default",
                    help="baseline name (default: 'default'). Baselines "
                         "live at ~/.config/bdctl/dd-baselines/")
    sp.add_argument("--against", dest="name",
                    help="alias for --name when diffing (reads "
                         "explicitly nicer at call sites)")
    sp.add_argument("--only", default=None,
                    help="filter fixtures by name substring")
    sp.add_argument("--list-baselines", action="store_true",
                    help="list saved baselines and exit")
    sp.add_argument("-q", "--quiet", action="store_true",
                    help="only print fixtures that changed")
    sp.set_defaults(func=cmd_dd_diff)

    sp = sub.add_parser("token", help="manage auth token")
    sp.add_argument("--set", metavar="TOKEN", help="store this token")
    sp.add_argument("--clear", action="store_true", help="remove stored token")
    sp.add_argument("--show", action="store_true", help="print current token")
    sp.set_defaults(func=cmd_token)

    # Phase 81 (v3.40.0): shell completion. Print a bash/zsh/fish script
    # that the user `source`s to get tab completion for bdctl commands +
    # site names. Site names come from a live API call so they're always
    # up to date.
    sp = sub.add_parser("completion", help="print shell completion script")
    sp.add_argument("shell", choices=["bash", "zsh", "fish"], help="target shell")
    sp.set_defaults(func=cmd_completion)

    # Internal: used by completion scripts to fetch site IDs dynamically
    sp = sub.add_parser("_complete-sites", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_complete_sites)

    # v3.43.70: send a test notification through the configured apprise
    # URLs. Useful for cron / scripts / monitoring integration.
    sp = sub.add_parser("notify", help="send a notification via apprise")
    sp.add_argument("body", help="message body")
    sp.add_argument("--title", default="BulkDownloader", help="message title")
    sp.set_defaults(func=cmd_notify)

    # v3.43.71: Telegram bot status. Shows whether the bot is running,
    # last poll time, command count.
    sp = sub.add_parser("tg-status", help="show Telegram bot status")
    sp.set_defaults(func=cmd_tg_status)

    # v3.43.72: perceptual dedup commands.
    sp = sub.add_parser("dedup", help="perceptual video dedup")
    dsub = sp.add_subparsers(dest="dcmd")
    dsp = dsub.add_parser("status", help="show registry stats + scan state")
    dsp.set_defaults(func=cmd_dedup_status)
    dsp = dsub.add_parser("scan", help="kick off a folder scan in the background")
    dsp.add_argument("--root", default="", help="root directory (default: first site's download_dir)")
    dsp.set_defaults(func=cmd_dedup_scan)
    dsp = dsub.add_parser("find", help="find duplicates of a file")
    dsp.add_argument("path", help="path to the file")
    dsp.add_argument("--distance", type=int, default=4, help="Hamming distance threshold (0-32)")
    dsp.set_defaults(func=cmd_dedup_find)

    # v3.43.73: Scrapling adaptive selectors + Turnstile bypass.
    sp = sub.add_parser("scrapling-status",
                         help="show Scrapling availability + runtime counters")
    sp.set_defaults(func=cmd_scrapling_status)

    # v3.43.74: FlareSolverr + multi-connection downloads.
    sp = sub.add_parser("flare-status",
                         help="show FlareSolverr connection + solve counters")
    sp.set_defaults(func=cmd_flare_status)

    sp = sub.add_parser("multi-conn-probe",
                         help="check whether a URL supports multi-connection download")
    sp.add_argument("url", help="URL to probe")
    sp.add_argument("--user-agent", default="", help="optional User-Agent header")
    sp.add_argument("--referer", default="", help="optional Referer header")
    sp.set_defaults(func=cmd_multi_conn_probe)

    # v3.43.75: yt-dlp archive + community scrapers
    sp = sub.add_parser("ytdlp-archive",
                         help="show yt-dlp download_archive stats")
    sp.set_defaults(func=cmd_ytdlp_archive_stats)

    sp = sub.add_parser("scrapers",
                         help="manage CommunityScrapers")
    ssub = sp.add_subparsers(dest="scmd")

    ssp = ssub.add_parser("list", help="list installed scrapers")
    ssp.set_defaults(func=cmd_scrapers_list)

    ssp = ssub.add_parser("available",
                          help="list scrapers available from GitHub")
    ssp.add_argument("--force", action="store_true",
                     help="bypass local cache")
    ssp.add_argument("--filter", default="",
                     help="substring filter (case-insensitive)")
    ssp.set_defaults(func=cmd_scrapers_available)

    ssp = ssub.add_parser("install",
                          help="install one scraper by name")
    ssp.add_argument("name", help="scraper name (without .yml)")
    ssp.add_argument("--stash-dir", required=True,
                     help="path to Stash's scrapers directory")
    ssp.set_defaults(func=cmd_scrapers_install)

    # v3.43.76: phoenix catalog + supervisor + thumbnails
    sp = sub.add_parser("phoenix-lookup",
                         help="look up a URL in the PhoenixAdult catalog")
    sp.add_argument("url", help="URL to look up")
    sp.set_defaults(func=cmd_phoenix_lookup)

    sp = sub.add_parser("supervisor-status",
                         help="show bandwidth supervisor stats")
    sp.set_defaults(func=cmd_supervisor_status)

    sp = sub.add_parser("thumbnails-regenerate",
                         help="queue a thumbnail regeneration for a file")
    sp.add_argument("path", help="absolute path to the video file")
    sp.add_argument("--mode", default="single",
                    choices=("single", "sheet"))
    sp.set_defaults(func=cmd_thumbnails_regenerate)

    # v3.43.77: Search-and-add by query
    sp = sub.add_parser("search",
                         help="search one site for a query")
    sp.add_argument("query", help="search query string")
    sp.add_argument("--site", required=True, help="site_id")
    sp.add_argument("--max", type=int, default=50,
                    help="max results (default 50)")
    sp.set_defaults(func=cmd_search_site)

    sp = sub.add_parser("search-all",
                         help="search every searchable site")
    sp.add_argument("query", help="search query string")
    sp.add_argument("--max-per-site", type=int, default=20,
                    help="max results per site (default 20)")
    sp.set_defaults(func=cmd_search_all)

    # ── v3.53 (Phase 6): power-user commands ──────────────────────────
    sp = sub.add_parser("health",
                         help="check server health (exit 0=ok, 1=degraded)")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("pause-all", help="pause every site's runner")
    sp.set_defaults(func=cmd_pause_all)

    sp = sub.add_parser("resume-all", help="resume every paused runner")
    sp.set_defaults(func=cmd_resume_all)

    sp = sub.add_parser("audit", help="show recent config-change audit log")
    sp.add_argument("--limit", type=int, default=30,
                    help="max events (default 30)")
    sp.set_defaults(func=cmd_audit)

    # library — subcommands
    sp = sub.add_parser("library", help="library collection commands")
    lib_sub = sp.add_subparsers(dest="libcmd")
    lsp = lib_sub.add_parser("stats", help="collection size + breakdown")
    lsp.set_defaults(func=cmd_library_stats)
    lsp = lib_sub.add_parser("scan", help="scan folder(s) into the library")
    lsp.add_argument("--root", nargs="+",
                     help="folder(s) to scan (default: every site's "
                          "download_dir)")
    lsp.add_argument("--wait", action="store_true",
                     help="block until the scan finishes")
    lsp.set_defaults(func=cmd_library_scan)
    lsp = lib_sub.add_parser("scan-status", help="current/last scan state")
    lsp.set_defaults(func=cmd_library_scan_status)

    # site — subcommands (export/import/validate)
    sp = sub.add_parser("site", help="site config export/import/validate")
    site_sub = sp.add_subparsers(dest="sitecmd")
    ssp = site_sub.add_parser("export", help="export a site config as JSON")
    ssp.add_argument("site", help="site id or name")
    ssp.add_argument("--out", help="write to file (default: stdout)")
    ssp.add_argument("--include-secrets", action="store_true",
                     help="include credentials (personal backup only)")
    ssp.set_defaults(func=cmd_site_export)
    ssp = site_sub.add_parser("import", help="import a site config")
    ssp.add_argument("file", help="config JSON file, or - for stdin")
    ssp.set_defaults(func=cmd_site_import)
    ssp = site_sub.add_parser("validate",
                               help="dry-run validate a config file")
    ssp.add_argument("file", help="config JSON file, or - for stdin")
    ssp.set_defaults(func=cmd_site_validate)

    # v3.54 (Phase 7): bd-doctor
    sp = sub.add_parser("doctor",
                         help="run diagnostics (env, deps, cookies)")
    sp.add_argument("--diagnose", metavar="ERROR_TEXT",
                    help="instead of the full pass, pattern-match a "
                         "single failure error string")
    sp.set_defaults(func=cmd_doctor)

    # v3.60 (Phase 12, #98): universal --json. Rather than adding the
    # flag to all 44 subparsers by hand, add it post-hoc to every
    # subparser that doesn't already declare it. After this loop,
    # `bdctl <anycommand> --json` is accepted everywhere; handlers that
    # call _maybe_json() honor it.
    for _sub_name, _sub_parser in sub.choices.items():
        if not any(getattr(a, "dest", None) == "json"
                   for a in _sub_parser._actions):
            _sub_parser.add_argument(
                "--json", action="store_true",
                help="emit machine-readable JSON instead of text")
        # Sub-subcommand parsers (learned/dedup/site/library/scrapers)
        for _act in _sub_parser._actions:
            choices = getattr(_act, "choices", None)
            if isinstance(choices, dict):
                for _ss in choices.values():
                    if not any(getattr(a, "dest", None) == "json"
                               for a in _ss._actions):
                        _ss.add_argument(
                            "--json", action="store_true",
                            help="emit JSON instead of text")

    args = p.parse_args()
    if not args.cmd:
        p.print_help(); sys.exit(1)
    # Subcommand "learned" needs a subsub
    if args.cmd == "learned" and not getattr(args, "lcmd", None):
        sub.choices["learned"].print_help(); sys.exit(1)
    # v3.43.72: dedup needs a subsub too
    if args.cmd == "dedup" and not getattr(args, "dcmd", None):
        sub.choices["dedup"].print_help(); sys.exit(1)
    # v3.43.75: scrapers needs a subsub too
    if args.cmd == "scrapers" and not getattr(args, "scmd", None):
        sub.choices["scrapers"].print_help(); sys.exit(1)
    # v3.53: library + site need a subsub too
    if args.cmd == "library" and not getattr(args, "libcmd", None):
        sub.choices["library"].print_help(); sys.exit(1)
    if args.cmd == "site" and not getattr(args, "sitecmd", None):
        sub.choices["site"].print_help(); sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
