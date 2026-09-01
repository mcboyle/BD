"""Phase 20: Integration Hooks.

A unified event dispatcher that fires hooks on download completion,
failure, low disk, and other lifecycle events. All hooks run in
background threads so they never block downloads. Failures are logged
but never crash the worker.

Event types currently fired:
  - completed     — a download finished successfully
  - failed        — a download moved to "failed" state (after retries)
  - needs_review  — a download moved to needs_review (auto-teach, captcha)
  - low_disk      — site paused because disk fell below threshold
  - cookies_expiring — fired when 50%+ of cookies are <1h from expiring

Hook sinks:
  - post_download_cmd  — shell command template with {path}, {url}, etc.
  - webhook_urls       — JSON POST to each URL on each subscribed event
  - stash              — GraphQL scan trigger (specialized webhook)
  - plex / jellyfin    — library refresh (specialized webhook)
  - home_assistant     — notify + webhook (specialized webhook)

All sinks share the same template variables, so any text field that
accepts placeholders ({path}, {url}, {site}, {filename}, {size},
{resolution}, {hash}) is rendered with the same vars dict.
"""

import json
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Module-level thread pool: cap concurrent outbound calls so a slow
# webhook endpoint can't pile up workers. Simple semaphore is enough.
_HOOK_SEMAPHORE = threading.Semaphore(8)

# Event-name → human-readable label, used in webhook payloads + logs.
EVENTS = ["completed", "failed", "needs_review", "low_disk", "cookies_expiring"]

# Per-event default subscription. Most users want "completed" + "failed"
# for webhooks, all for command hooks (where they opt in explicitly).
DEFAULT_WEBHOOK_EVENTS = ["completed", "failed", "low_disk"]


# ── Phase 80 (v3.40.0): Bidirectional webhook plugins ────────────────────
# Webhooks can now influence the system, not just observe it. When the
# 'pre_url_added' event is subscribed, each webhook receives the URL
# BEFORE it lands in the queue and can answer with directives:
#   {"action": "skip"}                 — don't add this URL
#   {"action": "rewrite", "url": "..."} — replace with a different URL
#   {"action": "priority", "value": "high"} — bump priority
#   {"action": "tag", "tags": ["foo"]}  — add tags (future)
#   {"action": "allow"} or no response — default; URL added unchanged
#
# Errors are non-fatal: timeout, parse failure, or unknown action → fall
# through to "allow". This means a misconfigured webhook can't block the
# queue, only fail to influence it.
def webhook_pre_url_added(site_config, url):
    """Phase 80: call all configured webhooks with action='pre_url_added'.
    Returns (allowed, final_url, priority_or_None) where:
      - allowed=False means a webhook said skip; caller drops the URL
      - final_url is the original URL or a rewrite target
      - priority is None or 'high'/'normal'/'low'

    Only fires for webhooks subscribed to the 'pre_url_added' event."""
    webhook_urls = _split_lines(site_config.get("webhook_urls", ""))
    subscribed = _split_csv(site_config.get("webhook_events", ""))
    if not subscribed: subscribed = DEFAULT_WEBHOOK_EVENTS
    if "pre_url_added" not in subscribed: return True, url, None
    if not webhook_urls: return True, url, None
    payload = {
        "event": "pre_url_added",
        "site": site_config.get("name", ""),
        "url": url,
    }
    final_url = url
    priority = None
    allowed = True
    for hook_url in webhook_urls:
        hook_url = hook_url.strip()
        if not hook_url: continue
        ok, msg, resp = send_webhook(hook_url, payload, timeout=5)
        if not ok or not isinstance(resp, dict):
            continue  # fall through; non-blocking
        action = (resp.get("action") or "allow").lower()
        if action == "skip":
            allowed = False
            break  # short-circuit; first skip wins
        elif action == "rewrite":
            new_url = (resp.get("url") or "").strip()
            if new_url.startswith("http"):
                final_url = new_url
        elif action == "priority":
            v = (resp.get("value") or "").lower()
            if v in ("high", "normal", "low"):
                priority = v
        # other actions ignored
    return allowed, final_url, priority


def _log(msg):
    sys.stderr.write(f"  hook: {msg}\n")


def _render_template(template, vars, shell_quote=False):
    """Substitute {placeholder} occurrences in `template` from the `vars`
    dict. Unknown placeholders are left in place (so a typo doesn't
    silently lose data — it shows up in the output). Returns the
    rendered string.

    Phase 41.7: when `shell_quote=True`, every substituted value is
    shlex.quote()'d so it's safe to interpolate into a shell command line.
    This blocks the attack where a downloaded filename like
    `bad.mp4'; rm -rf /; '.mp4` would break out of the surrounding
    quotes and execute attacker code via post_download_cmd. The site
    name and other config-controlled values get the same treatment for
    defense-in-depth.

    For webhooks and non-shell sinks, shell_quote stays off so the
    rendered string is the user's literal template + literal values."""
    if not template: return ""
    out = template
    for k, v in (vars or {}).items():
        sval = str(v if v is not None else "")
        if shell_quote and sval:
            # shlex.quote returns 'value' (single-quoted) or escapes safely.
            # For empty strings, leave as-is to preserve the operator's
            # template structure (e.g., `--option={key}` with empty key
            # should yield `--option=`, not `--option=''`).
            sval = shlex.quote(sval)
        out = out.replace("{" + k + "}", sval)
    return out


def build_vars(site_config, event, job, extra=None):
    """Build the canonical vars dict that every hook gets. Centralized
    so adding a new placeholder later only requires one edit.

    Required-key contract: callers can rely on every key in `vars`
    existing even if empty, so templates never crash on missing keys."""
    vars = {
        "event": event,
        "site": site_config.get("name", "") if site_config else "",
        "site_id": (site_config.get("_site_id", "") if site_config else "") or "",
        "url": (job or {}).get("url", ""),
        "filename": (job or {}).get("filename", ""),
        "path": (job or {}).get("path", ""),
        "size": (job or {}).get("file_size", "") or "",
        "size_human": _fmt_bytes((job or {}).get("file_size", 0) or 0),
        "resolution": (job or {}).get("resolution", "") or "",
        "hash": (job or {}).get("hash", "") or "",
        "message": (job or {}).get("message", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": str(int(time.time())),
    }
    if extra: vars.update(extra)
    return vars


def _fmt_bytes(n):
    try: n = int(n)
    except Exception: return ""
    if not n: return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


# ── 20.1: Post-download command hook ──────────────────────────────────

def run_command_hook(cmd_template, vars, timeout=120):
    """Run a shell command template with vars substituted. Returns
    (ok, output). Quoting: on POSIX we use a shell since users want
    pipes/redirects to work; on Windows we use shell=True for the same
    reason. Always time-bounded. Stdout+stderr captured (truncated to
    8KB for the log).

    The command runs in a background thread; this function blocks for
    `timeout` seconds and returns. Caller is responsible for spawning
    the thread if they don't want to wait."""
    if not cmd_template: return True, "(no command configured)"
    # Phase 41.7: shell-quote ALL substituted values. The operator's
    # cmd_template is trusted (config-controlled), but the values
    # ({path}, {filename}, {url}) come from downloaded files, which
    # means they're influenced by the target website. A malicious or
    # compromised site could produce a filename like
    #     bad.mp4'; rm -rf /home/; '.mp4
    # that breaks out of intended quoting. shlex.quote prevents that.
    # Operators wanting to splice raw paths into specific shell positions
    # can use $(echo {path}) or similar.
    rendered = _render_template(cmd_template, vars, shell_quote=True)
    if not rendered.strip(): return True, "(rendered command is empty)"
    try:
        # shell=True is intentional: users write chained commands like
        # "ffprobe '{path}' && curl ..." — pipes/redirects/&& need a shell.
        # Substituted values are shlex.quote()'d above, so shell metachars
        # in filenames don't escape their quoted slot.
        result = subprocess.run(
            rendered, shell=True, capture_output=True, text=True,
            timeout=timeout, errors="replace",
        )
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return True, out[:8192]
        return False, f"exit={result.returncode}: {out[:8192]}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _host_ok_for_hook(host):
    """(ok, reason) for a hook target host. v3.66.553 (F-CORE_BD04-01).

    The plex/jellyfin/home-assistant/stash hooks are INTENDED to reach internal LAN
    services, so RFC 1918 private addresses and loopback are allowed. What we reject are
    the ranges that are never a legitimate hook target and are the real SSRF payoff:
    link-local / cloud-metadata (169.254/16, fe80::/10, and the IPv6 IMDS address),
    RFC 6598 CGNAT (100.64/10), reserved, multicast, and unspecified. Resolves every
    address the host maps to and fails closed on a DNS error.
    """
    import socket
    import ipaddress
    if not host:
        return False, "no host"
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        addrs = [ipaddress.ip_address(bare)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(bare, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError, UnicodeError) as ex:
            return False, f"DNS resolution failed: {type(ex).__name__}: {ex}"
        addrs = []
        for fam, _st, _pr, _cn, sa in infos:
            if fam in (socket.AF_INET, socket.AF_INET6):
                try:
                    addrs.append(ipaddress.ip_address(sa[0]))
                except ValueError:
                    return False, f"got non-IP from getaddrinfo: {sa[0]!r}"
        if not addrs:
            return False, "DNS resolution returned no addresses"
    _v6_imds = ipaddress.ip_address("fd00:ec2::254")   # AWS IPv6 metadata endpoint
    for a in addrs:
        if isinstance(a, ipaddress.IPv4Address) and a in ipaddress.ip_network("100.64.0.0/10"):
            return False, f"refusing CGNAT/shared address ({host} -> {a})"
        if a.is_link_local:
            return False, f"refusing link-local/metadata address ({host} -> {a})"
        if a == _v6_imds:
            return False, f"refusing IPv6 cloud-metadata address ({host} -> {a})"
        if a.is_reserved:
            return False, f"refusing reserved address ({host} -> {a})"
        if a.is_multicast:
            return False, f"refusing multicast address ({host} -> {a})"
        if a.is_unspecified:
            return False, f"refusing unspecified address ({host} -> {a})"
        # is_private (RFC 1918) and is_loopback are INTENTIONALLY allowed: the
        # plex/jellyfin/home-assistant/stash integrations target LAN/localhost.
    return True, ""


def _validate_webhook_url(url):
    """Phase 41.7 / v3.66.553: defense-in-depth check on webhook URLs.

    The operator who set up the webhook is trusted (only authenticated
    UI sessions can edit configs), but a misconfiguration or a malicious
    config import could point a webhook at file:// or javascript: or
    similar schemes. urllib.request.urlopen is happy to dispatch file://
    GETs which leaks local files into the response body. Reject anything
    that isn't http/https, and (F-CORE_BD04-01) reject the never-legitimate
    SSRF IP ranges (link-local/metadata, CGNAT, reserved, multicast,
    unspecified) while allowing the intended LAN/loopback integration
    targets.

    Returns (ok, normalized_url_or_error_message)."""
    if not url: return False, "empty url"
    try:
        from urllib.parse import urlparse
        pp = urlparse(url)
    except Exception as e:
        return False, f"unparseable url: {e}"
    if pp.scheme.lower() not in ("http", "https"):
        return False, f"webhook url scheme must be http or https (got {pp.scheme!r})"
    if not pp.hostname:
        return False, "webhook url has no hostname"
    host_ok, host_why = _host_ok_for_hook(pp.hostname)
    if not host_ok:
        return False, host_why
    return True, url


class _HookRedirectHandler(urllib.request.HTTPRedirectHandler):
    """v3.66.553 (F-CORE_BD04-01): re-validate every redirect target the same way the
    initial webhook URL is validated, so a public URL can't 30x-redirect a hook into
    cloud-metadata / a dangerous range. urllib follows redirects by default; the hook
    sinks open through _hook_urlopen so this handler is in the chain."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = _validate_webhook_url(newurl)
        if not ok:
            import urllib.error
            raise urllib.error.HTTPError(newurl, code, f"blocked redirect: {why}",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_HOOK_OPENER = None


def _hook_urlopen(req, timeout=15):
    """urlopen for hook sinks, through an opener whose redirect handler re-validates
    each hop against _validate_webhook_url (F-CORE_BD04-01)."""
    global _HOOK_OPENER
    if _HOOK_OPENER is None:
        _HOOK_OPENER = urllib.request.build_opener(_HookRedirectHandler())
    return _HOOK_OPENER.open(req, timeout=timeout)


# ── 20.2: Generic outbound webhook ────────────────────────────────────

def send_webhook(url, payload, timeout=15, headers=None):
    """JSON POST to `url`. Returns (ok, status_or_error, response_body).

    Uses urllib so no extra dependency. httpx would be nicer but we
    already pay an import cost for it elsewhere; keep hooks lightweight.

    Phase 41.7: validates URL scheme to block accidental file://,
    javascript:, gopher://, ftp:// etc. — urllib.urlopen will gladly
    dispatch some of those and either leak local files or hang the
    hook thread.

    Phase 80 (v3.40.0): also return the response body so callers can
    implement bidirectional webhooks (the server can answer 'skip',
    'rewrite URL', 'change priority', etc.). EVERY return path is a
    three-tuple, so every caller must unpack three: tuple unpacking does
    not ignore a trailing element, it raises ValueError."""
    if not url: return True, "(no webhook configured)", None
    ok, msg = _validate_webhook_url(url)
    if not ok: return False, f"rejected: {msg}", None
    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "BulkDownloader/1.0",
                     **(headers or {})},
        )
        with _hook_urlopen(req, timeout=timeout) as resp:
            body_bytes = b""
            try: body_bytes = resp.read(64 * 1024)  # cap at 64KB
            except Exception: pass
            body_text = body_bytes.decode("utf-8", "replace") if body_bytes else ""
            parsed = None
            if body_text.strip():
                try: parsed = json.loads(body_text)
                except Exception: pass
            return True, f"HTTP {resp.status}", parsed
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "replace")[:500]
        except Exception: pass
        return False, f"HTTP {e.code}: {body}", None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None


# ── 20.3: Stash GraphQL hook ──────────────────────────────────────────

def stash_trigger_scan(stash_url, api_key, paths=None, timeout=20):
    """Trigger a Stash scan. If `paths` is given, scan only those
    directories — otherwise full library scan. Stash accepts a list of
    paths under the metadataScan input.

    Returns (ok, job_id_or_error). The job ID can be polled via the
    /jobs endpoint if the caller cares; most users just fire and forget."""
    if not stash_url: return True, "(stash not configured)"
    # Phase 41.7: scheme/host validation
    ok, msg = _validate_webhook_url(stash_url)
    if not ok: return False, f"rejected: {msg}"
    stash_url = stash_url.rstrip("/")
    if not stash_url.endswith("/graphql"):
        stash_url = stash_url + "/graphql"
    # Newer Stash uses `input: {paths: [...]}`; older accepts just paths.
    # Use the newer schema; older Stash returns a clear error if unsupported.
    query = """
    mutation MetadataScan($input: ScanMetadataInput!) {
        metadataScan(input: $input)
    }
    """
    variables = {"input": {"paths": paths or []}}
    payload = {"query": query, "variables": variables}
    headers = {"Content-Type": "application/json"}
    if api_key: headers["ApiKey"] = api_key
    try:
        req = urllib.request.Request(
            stash_url, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers=headers,
        )
        with _hook_urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            data = json.loads(body)
            if "errors" in data and data["errors"]:
                return False, f"Stash GraphQL error: {data['errors'][0].get('message','?')}"
            job = (data.get("data") or {}).get("metadataScan", "")
            return True, f"scan job {job}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── 20.4: Plex / Jellyfin library refresh ─────────────────────────────

def plex_refresh(plex_url, token, section_id=None, timeout=15):
    """Trigger a Plex library refresh. If section_id is given, refresh
    only that library; otherwise refresh all. Requires Plex Token from
    a logged-in account (Settings → Network → "Show Advanced" → tokens).

    Returns (ok, message). Plex returns 200 with no body on success."""
    if not plex_url or not token: return True, "(plex not configured)"
    # Phase 41.7: scheme/host validation
    ok, msg = _validate_webhook_url(plex_url)
    if not ok: return False, f"rejected: {msg}"
    plex_url = plex_url.rstrip("/")
    if section_id:
        path = f"/library/sections/{section_id}/refresh"
    else:
        path = "/library/sections/all/refresh"
    sep = "&" if "?" in path else "?"
    full = f"{plex_url}{path}{sep}X-Plex-Token={urllib.parse.quote(token)}"
    try:
        req = urllib.request.Request(full, method="GET",
                                     headers={"Accept": "application/xml"})
        with _hook_urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def jellyfin_refresh(jellyfin_url, api_key, timeout=15):
    """Trigger a Jellyfin library scan. Single endpoint, no per-library
    granularity (Jellyfin scans everything by default). Auth via header."""
    if not jellyfin_url or not api_key: return True, "(jellyfin not configured)"
    # Phase 41.7: scheme/host validation
    ok, msg = _validate_webhook_url(jellyfin_url)
    if not ok: return False, f"rejected: {msg}"
    jellyfin_url = jellyfin_url.rstrip("/")
    full = f"{jellyfin_url}/Library/Refresh"
    try:
        req = urllib.request.Request(
            full, data=b"", method="POST",
            headers={"X-Emby-Token": api_key},
        )
        with _hook_urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── 20.5: Home Assistant notify ───────────────────────────────────────

def home_assistant_notify(ha_url, token, service, message, title=None, timeout=15):
    """Fire a notify service in Home Assistant. `service` is like
    "mobile_app_iphone" (matches notify.mobile_app_iphone). `token` is
    a Long-Lived Access Token from HA → Profile → Security."""
    if not ha_url or not token or not service: return True, "(HA not configured)"
    # Phase 41.7: scheme/host validation
    ok, msg = _validate_webhook_url(ha_url)
    if not ok: return False, f"rejected: {msg}"
    ha_url = ha_url.rstrip("/")
    if not service.startswith("notify."): service = "notify." + service
    domain, name = service.split(".", 1)
    full = f"{ha_url}/api/services/{domain}/{name}"
    payload = {"message": message}
    if title: payload["title"] = title
    try:
        req = urllib.request.Request(
            full, data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
        with _hook_urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── Central dispatcher ────────────────────────────────────────────────

def fire_event(event, site_config, job=None, extra=None):
    """Dispatch one event across every configured sink for this site.
    Runs in background — returns immediately. Each hook acquires the
    module semaphore so we never have more than 8 concurrent outbound
    calls regardless of how many downloads complete in parallel.

    Args:
        event: one of EVENTS
        site_config: the per-site config dict
        job: optional dict with url/path/filename/file_size/etc.
        extra: optional additional template vars

    The site_config controls which sinks fire. Each sink is opt-in;
    missing config = silently skipped (returns ok=True with reason).

    Returns: nothing. Errors logged to stderr. Use this for fire-and-
    forget. For test buttons that want to surface errors, call the
    individual hook functions directly."""
    if event not in EVENTS:
        _log(f"unknown event: {event}")
        return
    if not site_config: site_config = {}
    vars = build_vars(site_config, event, job, extra=extra)

    def _run():
        with _HOOK_SEMAPHORE:
            _dispatch(event, site_config, vars, job or {})

    threading.Thread(target=_run, daemon=True,
                     name=f"hook-{event}").start()


def _eval_pipeline_condition(condition, vars):
    """Phase 70 (v3.41.0): evaluate a pipeline rule's condition string.

    Supports a tiny embedded language to avoid `eval()`:
      "always"
      "size_gb > N"  (or <, >=, <=, ==)   — checks file size in GB
      "size_mb > N"
      "filename contains TEXT"            — substring match (case-insensitive)
      "filename matches /PATTERN/"        — regex match
      "url contains TEXT"
      "ext == .mp4"                       — file extension match (case-insensitive)

    Returns True/False. Unknown conditions return False (fail closed —
    if the user typo'd a rule, better to skip than run unexpectedly)."""
    if not condition: return False
    cond = condition.strip()
    if cond.lower() == "always": return True
    # Size comparisons
    import re as _re
    m = _re.match(r"size_(gb|mb)\s*(==|!=|>=|<=|>|<)\s*([0-9.]+)", cond, _re.I)
    if m:
        unit, op, val = m.groups()
        try: val = float(val)
        except ValueError: return False
        size = float(vars.get("size_bytes", 0) or 0)
        if unit.lower() == "gb": size /= 1024**3
        else: size /= 1024**2
        return _apply_op(op, size, val)
    # filename/url contains
    m = _re.match(r"(filename|url)\s+contains\s+(.+)", cond, _re.I)
    if m:
        field, needle = m.groups()
        return needle.strip().lower() in str(vars.get(field, "")).lower()
    # filename matches regex
    m = _re.match(r"(filename|url)\s+matches\s+/(.+)/(i?)", cond, _re.I)
    if m:
        field, pattern, flag = m.groups()
        flags = _re.I if flag else 0
        # AUDIT FIX (v3.42.0): user-controlled regex can ReDoS. We can't
        # add a real timeout to `re.search` (Python's stdlib doesn't
        # support it), but we can reject obviously dangerous patterns
        # — nested quantifiers like `(a+)+` or `(a*)*` are the classic
        # exponential backtracking triggers. Cap pattern length too.
        if len(pattern) > 200:
            return False
        if _re.search(r"\([^)]*[*+][^)]*\)[*+]", pattern):
            # heuristic — nested quantifier inside a group
            return False
        try: return bool(_re.search(pattern, str(vars.get(field, "")), flags))
        except _re.error: return False
    # ext ==
    m = _re.match(r"ext\s+(==|!=)\s+(.+)", cond, _re.I)
    if m:
        op, target = m.groups()
        target = target.strip().lower()
        if not target.startswith("."): target = "." + target
        fn = str(vars.get("filename", "")).lower()
        ext = "." + fn.rsplit(".", 1)[-1] if "." in fn else ""
        return _apply_op(op, ext, target)
    return False


def _apply_op(op, lhs, rhs):
    """Numeric or string comparison; both sides must be comparable."""
    try:
        if op == "==": return lhs == rhs
        if op == "!=": return lhs != rhs
        if op == ">":  return lhs >  rhs
        if op == "<":  return lhs <  rhs
        if op == ">=": return lhs >= rhs
        if op == "<=": return lhs <= rhs
    except TypeError: pass
    return False


def _run_post_pipeline(site_config, vars):
    """Phase 70: walk post_download_pipeline rules and execute matching
    commands. Each rule has {condition, command, name?, timeout?}.
    The legacy single post_download_cmd already ran; this is additive."""
    pipeline = site_config.get("post_download_pipeline") or []
    if not isinstance(pipeline, list): return
    first_match = bool(site_config.get("post_download_pipeline_first_match", False))
    default_timeout = int(site_config.get("post_download_timeout", 120))
    for i, rule in enumerate(pipeline):
        if not isinstance(rule, dict): continue
        condition = (rule.get("condition") or "").strip()
        command = (rule.get("command") or "").strip()
        name = rule.get("name") or f"pipeline[{i}]"
        if not condition or not command: continue
        try:
            matched = _eval_pipeline_condition(condition, vars)
        except Exception as e:
            _log(f"pipeline {name}: condition eval failed — {e}")
            continue
        if not matched: continue
        timeout = int(rule.get("timeout", default_timeout))
        ok, out = run_command_hook(command, vars, timeout=timeout)
        _log(f"pipeline {name}: {'ok' if ok else 'FAIL'} — {out[:200]}")
        if first_match: break


def _dispatch(event, site_config, vars, job):
    """Inner dispatcher. Runs synchronously (caller already spawned a
    thread). Walks each sink type and fires only those configured AND
    subscribed to this event."""
    # 1. Post-download command hook — only on `completed`
    if event == "completed":
        cmd = (site_config.get("post_download_cmd") or "").strip()
        if cmd:
            ok, out = run_command_hook(cmd, vars,
                                       timeout=int(site_config.get("post_download_timeout", 120)))
            _log(f"post_download_cmd: {'ok' if ok else 'FAIL'} — {out[:200]}")
        # Phase 70 (v3.41.0): conditional pipeline. `post_download_pipeline`
        # is a list of {condition, command, name?} rules. Each rule's
        # condition is evaluated against the same vars dict as the
        # legacy single-cmd hook. Supports:
        #   condition: "size_gb > 5"        — file size threshold
        #   condition: "filename contains 4K"  — substring match
        #   condition: "filename matches /pattern/"  — regex
        #   condition: "always"             — always run
        # Rules execute sequentially. First-match-wins is opt-in via
        # `post_download_pipeline_first_match` (default False = run all).
        _run_post_pipeline(site_config, vars)

    # 2. Generic webhooks
    webhook_urls = _split_lines(site_config.get("webhook_urls", ""))
    subscribed = _split_csv(site_config.get("webhook_events", ""))
    if not subscribed: subscribed = DEFAULT_WEBHOOK_EVENTS
    if webhook_urls and event in subscribed:
        payload = dict(vars)
        payload["job"] = {k: v for k, v in (job or {}).items() if k != "screenshot"}
        for url in webhook_urls:
            url = _render_template(url, vars).strip()
            if not url: continue
            ok, msg, _resp = send_webhook(url, payload)
            _log(f"webhook {url[:50]}: {'ok' if ok else 'FAIL'} — {msg}")

    # 3. Stash scan — typically on `completed`
    if (event == "completed" and site_config.get("stash_enabled")
            and site_config.get("stash_url")):
        path = job.get("path", "") or ""
        paths = [str(Path(path).parent)] if path else []
        ok, msg = stash_trigger_scan(
            site_config.get("stash_url", ""),
            site_config.get("stash_api_key", ""),
            paths=paths,
        )
        _log(f"stash: {'ok' if ok else 'FAIL'} — {msg}")

    # 4. Plex refresh — typically on `completed`
    if (event == "completed" and site_config.get("plex_enabled")
            and site_config.get("plex_url")):
        ok, msg = plex_refresh(
            site_config.get("plex_url", ""),
            site_config.get("plex_token", ""),
            section_id=site_config.get("plex_section_id", "") or None,
        )
        _log(f"plex: {'ok' if ok else 'FAIL'} — {msg}")

    # 5. Jellyfin refresh
    if (event == "completed" and site_config.get("jellyfin_enabled")
            and site_config.get("jellyfin_url")):
        ok, msg = jellyfin_refresh(
            site_config.get("jellyfin_url", ""),
            site_config.get("jellyfin_api_key", ""),
        )
        _log(f"jellyfin: {'ok' if ok else 'FAIL'} — {msg}")

    # 6. Home Assistant notify — subscribed events
    ha_events = _split_csv(site_config.get("ha_events", ""))
    if not ha_events: ha_events = ["failed", "low_disk"]
    if (site_config.get("ha_enabled") and event in ha_events
            and site_config.get("ha_url")):
        # v3.36.8: default template no longer uses `{filename or url}` —
        # _render_template is literal key-substitution, not a Python
        # expression evaluator. Render filename first; if it's empty,
        # the url placeholder is the natural fallback in any reasonable
        # template. Operators can still write `{filename}{url}` to chain.
        msg_tmpl = site_config.get("ha_message_template") or (
            "[{site}] {event}: {filename}{url}")
        # If filename is non-empty, blank out url in vars so the
        # concatenation doesn't repeat info. (Operators using custom
        # templates with explicit {url} get the full value.)
        vars_for_ha = dict(vars)
        if not site_config.get("ha_message_template") and vars_for_ha.get("filename"):
            vars_for_ha["url"] = ""
        ok, msg = home_assistant_notify(
            site_config.get("ha_url", ""),
            site_config.get("ha_token", ""),
            site_config.get("ha_service", "notify"),
            _render_template(msg_tmpl, vars_for_ha),
            title=f"Bulk Downloader: {event}",
        )
        _log(f"ha_notify: {'ok' if ok else 'FAIL'} — {msg}")

    # ── Phase 24.7: Mobile push on actionable events ───────────────────
    # Only fires for events the user actually wants notifications about.
    # Default: needs_review + failed (both need human attention). Done
    # events would spam too hard on a 2875-URL queue. Explicit per-site
    # opt-in via push_on_failures / push_on_review (default both on if
    # push is globally enabled).
    push_events = []
    if site_config.get("push_on_review", True):    push_events.append("needs_review")
    if site_config.get("push_on_failures", True):  push_events.append("failed")
    if site_config.get("push_on_low_disk", True):  push_events.append("low_disk")
    if event in push_events:
        try:
            from . import push as _push
            site_name = vars.get("site", "")
            fn = vars.get("filename", "") or vars.get("url", "")
            title_map = {
                "needs_review": f"⚠ {site_name}: needs your attention",
                "failed":       f"✗ {site_name}: download failed",
                "low_disk":     f"💾 {site_name}: low disk space",
            }
            body = (vars.get("message") or fn[-60:] or event)[:120]
            result = _push.send_push(
                title=title_map.get(event, f"{site_name}: {event}"),
                body=body,
                url=f"/?site={vars.get('site_id', '')}",
                tag=f"{event}:{vars.get('site_id','')}",
                throttle_seconds=30,
            )
            sent = (result or {}).get("sent", 0)
            err = (result or {}).get("error", "")
            _log(f"push ({event}): sent={sent}" + (f" — {err}" if err else ""))
        except Exception as e:
            _log(f"push ({event}) failed: {str(e)[:120]}")


def _split_lines(s):
    if not s: return []
    return [line.strip() for line in str(s).splitlines() if line.strip()]


def _split_csv(s):
    if not s: return []
    return [x.strip() for x in str(s).split(",") if x.strip()]


# ── 20.6: Auto-spillover for download directories ─────────────────────

def resolve_download_dir(site_config, free_threshold_pct=5.0):
    """Pick the first download dir under `spillover_dirs` that has at
    least `free_threshold_pct` percent free space. Falls back to the
    primary `download_dir` if no spillover is configured OR none of the
    spillover dirs have enough space (operator's problem at that point).

    Returns (chosen_dir, reason). The reason explains which path was
    chosen and why; useful for logging when the operator wonders why
    files landed in dir B instead of dir A."""
    import shutil
    primary = (site_config.get("download_dir") or "").strip()
    spillover_raw = site_config.get("spillover_dirs", "") or ""
    candidates = [primary] + _split_lines(spillover_raw)
    candidates = [c for c in candidates if c]
    if not candidates:
        return "", "no download_dir configured"
    if len(candidates) == 1:
        return primary, "no spillover configured"
    for i, d in enumerate(candidates):
        try:
            if not Path(d).exists():
                continue
            usage = shutil.disk_usage(d)
            pct_free = (usage.free / usage.total) * 100
            if pct_free >= free_threshold_pct:
                tag = "primary" if i == 0 else f"spillover #{i}"
                return d, f"{tag} ({pct_free:.1f}% free)"
        except Exception as e:
            _log(f"spillover: can't stat {d}: {e}")
            continue
    # All dirs below threshold — return primary anyway and let the
    # low_disk check catch it. Better than silently failing.
    return primary, "all dirs below threshold; falling back to primary"
