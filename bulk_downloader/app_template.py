"""template API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/template views moved onto a Flask Blueprint.
Endpoint labels gain a "template." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (app, runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, request

template_bp = Blueprint("template", __name__)

def _build_meta(*_a, **_k):
    """Delegate to app._build_meta at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_build_meta")(*_a, **_k)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _save_sites_config(*_a, **_k):
    """Delegate to app._save_sites_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_sites_config")(*_a, **_k)

def _app_app():
    """The live shared app from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "app")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app_s_meta():
    """The live shared s_meta from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_meta")


@template_bp.route("/api/template/extract", methods=["POST"])
def api_template_extract():
    """Take pasted HTML, return a draft site template + ranked
    candidates.

    POST body: {
      html: "<...>",         # required
      page_url: "https://..." (optional, used for url_patterns)
      site_hint_name: "..." (optional)
    }

    Returns: {ok, template, candidates, warnings, stats}

    Pure rule-based extraction. Use /api/template/refine for an
    AI-assisted second pass on top of the result."""
    body = request.get_json(silent=True) or {}
    html = body.get("html", "")
    page_url = body.get("page_url", "") or ""
    site_hint_name = body.get("site_hint_name", "") or ""
    if not isinstance(html, str) or not html.strip():
        return jsonify({"ok": False,
                          "error": "html field required"}), 400
    # Cap input — 2MB is plenty for any realistic page; refuse more
    # to avoid degenerate parses
    if len(html) > 2 * 1024 * 1024:
        return jsonify({"ok": False,
                          "error": "html too large (>2MB); paste only the "
                                   "relevant section"}), 413
    try:
        from . import template_extractor as _te
        result = _te.extract_from_html(html, page_url=page_url,
                                          site_hint_name=site_hint_name)
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500
    # Strip BS4 element references from candidates before serializing
    # — _el isn't JSON-safe and the client doesn't need it.
    if result.get("candidates"):
        for c in result["candidates"]:
            c.pop("_el", None)
            c.pop("_attrs", None)
    return jsonify(result)


@template_bp.route("/api/template/extract_login", methods=["POST"])
def api_template_extract_login():
    """Take a pasted login PAGE's HTML, return a learned.login block —
    the user/password/submit selectors. Sits next to /api/template/
    extract (which does download selectors): one teaches a site how to
    log in, the other how to download.

    POST body: { html: "<...>" }   # required
    Returns: {ok, login:{user_field,pass_field,submit_btn},
              form_action, warnings}

    Deterministic — login forms are standardized enough that no AI
    pass is needed. Skips honeypot fields and finds JS-wired submit
    elements (a styled <div>, not only a real <button>)."""
    body = request.get_json(silent=True) or {}
    html = body.get("html", "")
    if not isinstance(html, str) or not html.strip():
        return jsonify({"ok": False,
                          "error": "html field required"}), 400
    if len(html) > 2 * 1024 * 1024:
        return jsonify({"ok": False,
                          "error": "html too large (>2MB); paste only "
                                   "the login form section"}), 413
    try:
        from . import template_extractor as _te
        result = _te.extract_login_from_html(html)
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(result)


@template_bp.route("/api/template/refine", methods=["POST"])
def api_template_refine():
    """Send a rule-based draft template + the source HTML to the
    configured AI provider for refinement. Returns a refined draft
    or the original on failure.

    POST body: {
      html: "<...>",
      template: { ... rule-based draft ... },
      candidates: [ ... rule-based candidates ... ],
    }

    Requires AI assist to be enabled in global config."""
    body = request.get_json(silent=True) or {}
    html = body.get("html", "") or ""
    template = body.get("template")
    candidates = body.get("candidates") or []
    if not isinstance(template, dict):
        return jsonify({"ok": False,
                          "error": "template field required (object)"}), 400
    try:
        from . import template_extractor as _te
        result = _te.refine_with_ai(template, candidates, html)
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(result)


# ── v3.45.1 / v3.46.2 Phase 176: template sandbox ─────────────────────
# Fetches a URL + applies a draft template's selectors. Two modes:
#
#   mode='http' (default) — urllib GET. Fast, cheap, but JS-rendered
#     pages return zero matches even when the selectors would work in
#     a real browser. Good for static SSR pages.
#
#   mode='browser' — Playwright in a sandbox profile. Slow (~3-8s)
#     but executes JS, so single-page apps and lazy-loaded content
#     get evaluated. Costs a browser slot; uses persistent_browsers'
#     keepalive context if available, otherwise spawns a fresh one.
@template_bp.route("/api/template/sandbox", methods=["POST"])
def api_template_sandbox():
    """Fetch a live URL + apply a draft template's selectors, return
    what each selector matches. Lets operators sanity-check a draft
    against a fresh page before saving as a new site.

    Body:
      url       (required) — http(s):// URL to test against
      template  (required) — draft template object (see /api/template/extract)
      mode      (optional) — 'http' (default, fast) or 'browser' (JS-aware)
      wait_ms   (optional) — extra wait after page load for browser mode
                              (default 1500ms; helps lazy-loaded content)

    Returns: {ok, mode, url, html_bytes, matches: {field: {selector,
              match_count, samples, error?}}}.

    Refuses non-http(s) schemes. Caps HTML at 4 MB."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    template = body.get("template") or {}
    mode = (body.get("mode") or "http").lower()
    wait_ms = int(body.get("wait_ms", 1500) or 1500)
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"ok": False,
                          "error": "url must be http(s)://"}), 400
    if not isinstance(template, dict):
        return jsonify({"ok": False,
                          "error": "template must be an object"}), 400
    if mode not in ("http", "browser"):
        return jsonify({"ok": False,
                          "error": "mode must be 'http' or 'browser'"}), 400

    # F-APP03-01: SSRF guard. Validate the URL's host BEFORE any fetch -- this
    # runs ahead of the mode branch so it covers BOTH the browser (page.goto)
    # and http (urllib) paths. Delegates host classification to the single
    # canonical predicate (link-local/cloud-metadata, RFC1918 private, RFC6598
    # CGNAT, reserved, multicast, and DNS failures are all refused).
    #
    # Loopback is deliberately EXEMPTED: pointing the sandbox at a page served
    # on the operator's own box (127.0.0.1 / localhost) is the intentional
    # selector-testing capability, and blocking it would break local template
    # authoring. Only the genuinely dangerous internal targets (the internal
    # network + cloud metadata) are refused. Imported dynamically to avoid a
    # static import edge onto provider_resolve_impl.
    import importlib as _il
    from urllib.parse import urlparse as _urlparse
    _host_safety = _il.import_module(
        "bulk_downloader.provider_resolve_impl._common")
    _is_safe_public_host = getattr(_host_safety, "_is_safe_public_host")
    _HostSafetyReason = getattr(_host_safety, "HostSafetyReason")
    _host_ok, _host_why = _is_safe_public_host(_urlparse(url).hostname or "")
    if not _host_ok and _host_why.code is not _HostSafetyReason.LOOPBACK:
        return jsonify({"ok": False,
                        "error": f"url host not allowed: {_host_why}"}), 400

    html = ""
    content_type = ""
    final_url = url
    if mode == "browser":
        # Browser-backed path — renders JS before extracting HTML, through the
        # CANONICAL backend (CloakBrowser when resolved, else vanilla Playwright)
        # so the operator's selector test renders the SAME way a real cloaked
        # capture would. Fails open to a clear error so the operator can fall
        # back to HTTP.
        try:
            from . import cloak as _cloak
        except ImportError:
            return jsonify({
                "ok": False,
                "error": ("browser backend unavailable; "
                          "use mode=http or install playwright/cloakbrowser"),
            }), 200
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        try:
            with _cloak.cloaked_page(headless=True, user_agent=ua) as page:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=30000)
                # Extra wait for lazy-loaded content. Operator tunes this.
                page.wait_for_timeout(max(0, min(wait_ms, 10000)))
                html = page.content()
                final_url = page.url
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": f"browser fetch failed: {type(e).__name__}: {e}",
            }), 200
        # Cap to 4MB after rendering
        if len(html) > 4 * 1024 * 1024:
            html = html[: 4 * 1024 * 1024]
    else:
        # HTTP path — fast + cheap. Same as v3.45.1.
        try:
            import urllib.request
            import urllib.error
            # F-APP03-01: re-validate every redirect hop. urllib follows
            # redirects by default, so a public host that 302s to an internal
            # one would otherwise bypass the pre-fetch guard above. This opener
            # re-checks each Location against the same canonical predicate and
            # refuses if a hop resolves to a non-public address.
            class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg,
                                     headers, newurl):
                    _ok, _why = _is_safe_public_host(
                        _urlparse(newurl).hostname or "")
                    # Same structured loopback exemption as the pre-fetch guard.
                    if not _ok and _why.code is not _HostSafetyReason.LOOPBACK:
                        raise urllib.error.URLError(
                            f"SSRF redirect blocked: {_why}")
                    return super().redirect_request(
                        req, fp, code, msg, headers, newurl)
            _opener = urllib.request.build_opener(_GuardedRedirect)
            req = urllib.request.Request(
                url, headers={"User-Agent":
                    "Mozilla/5.0 BD-template-sandbox"})
            with _opener.open(req, timeout=20) as resp:
                content_type = resp.headers.get("Content-Type", "")
                html_bytes = resp.read(4 * 1024 * 1024)
                final_url = resp.geturl()
            html = html_bytes.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return jsonify({"ok": False,
                              "error": f"HTTP {e.code}: {e.reason}"}), 200
        except Exception as e:
            return jsonify({"ok": False,
                              "error": f"fetch failed: {e}"}), 200

    # Apply the template's selectors (same code path for both modes)
    matches = {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for field in ("dl_selector", "trigger_selector",
                      "dismiss_selectors", "user_field", "pass_field",
                      "submit_btn"):
            sel = template.get(field) or ""
            if not sel:
                matches[field] = {"selector": "", "match_count": 0,
                                  "samples": []}
                continue
            try:
                els = soup.select(sel)
                matches[field] = {
                    "selector": sel,
                    "match_count": len(els),
                    "samples": [str(e)[:150] for e in els[:3]],
                }
            except Exception as e:
                matches[field] = {"selector": sel, "match_count": 0,
                                  "error": str(e)[:100]}
    except Exception as e:
        return jsonify({"ok": False,
                          "error": f"parse failed: {e}"}), 200
    return jsonify({"ok": True, "mode": mode, "url": url,
                    "final_url": final_url,
                    "html_bytes": len(html),
                    "content_type": content_type,
                    "matches": matches})



@template_bp.route("/api/template/test_extract", methods=["POST"])
def api_template_test_extract():
    """B2 (v3.66.240): set/clear a per-site DRAFT-TEST OVERRIDE and trigger one
    real extraction run off an UNREVIEWED draft.

    This is the only path that lets a draft drive ``_process_one``. The normal
    enabled-only matcher (``find_template_for_url``) still cannot return a
    draft; the override is a separate branch in
    ``merge_template_download_hints``. Distinct from ``/api/template/sandbox``
    (selector MATCH only, never downloads) and from
    ``/api/template_manager/promote`` (the explicit ENABLE step). This route
    enables nothing and never writes to ``reviewed``/``enabled``.

    Body:
      site_id    (required) — target site whose runner is overridden + started
      template   (required for a set) — flat draft template object
      draft_file (optional) — draft filename in ``templates/drafts/``, used
                              ONLY for the persist-ON writeback (Decision 2)
      persist    (optional bool, default False) — persist-bypass toggle. OFF
                              (default) => the run persists NOTHING (neither the
                              live site config nor the draft). ON => learned
                              selectors persist to BOTH (operator opt-in).
      url        (optional) — a single http(s) URL to enqueue for this run
      clear      (optional bool) — when true, REMOVE the override from the site
                              (Decision 4 teardown) and return without running.

    Challenge handling is inherited UNCHANGED from the normal extraction path
    (fail-open manual handoff; B2 adds no auto-solve and no bypass).
    """
    app = _app_app()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    if not sid or sid not in s_cfg:
        return jsonify({"ok": False, "error": "unknown site_id"}), 404
    cfg = s_cfg[sid]

    # Teardown (Decision 4): explicit clear removes the standing override.
    if body.get("clear"):
        # Addendum A3: also undo exactly the login selectors this override
        # seeded at set time (preserve-if-present), and ONLY where the value is
        # still the seeded one -- never clobber a value the operator changed
        # since. Without this, "Stop testing (clear)" silently leaves the draft's
        # login selectors permanently in the live site config.
        _ov = cfg.get("draft_test_override") or {}
        cfg.pop("draft_test_override", None)
        try:
            from .capture_login_wire import revert_seeded_login
            revert_seeded_login(cfg, _ov.get("seeded_login"))
        except Exception:
            pass
        s_meta[sid] = _build_meta(cfg)
        if sid in runners:
            runners[sid].update_config(cfg)
        _save_sites_config()
        return jsonify({"ok": True, "cleared": True, "site_id": sid})

    template = body.get("template") or {}
    if not isinstance(template, dict) or not template:
        return jsonify({"ok": False,
                        "error": "template (draft object) required"}), 400
    persist = bool(body.get("persist", False))
    probe = bool(body.get("probe", False))
    force_download = bool(body.get("force_download", False))  # BP-VH3
    draft_file = (body.get("draft_file") or "").strip() or None
    url = (body.get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "url must be http(s)://"}), 400

    # Set the per-site override. Rides _save_sites_config so it persists
    # per-site across restarts (Decision 4). NOT a reviewed/enabled write —
    # the draft's status is untouched and find_template_for_url still ignores it.
    cfg["draft_test_override"] = {
        "template": template,
        "draft_file": draft_file,
        "persist": persist,
        "set_at": int(time.time()),
    }
    # v3.66.289: seed the live-login selector keys (user_field/pass_field/
    # submit_btn) from the draft's login block so the verification run actually
    # logs in via the operator's PICKED selectors instead of falling back to the
    # 154-selector list. PRESERVE-IF-PRESENT — never clobbers a manually-set or
    # teach-learned selector. Without this the /capture login pickers were
    # disconnected from do_login (which reads only config.* + learned.login).
    try:
        from .capture_login_wire import apply_draft_login_selectors
        _seeded = apply_draft_login_selectors(cfg, template.get("login"))
        if _seeded:
            # A3: remember exactly what was seeded (key -> value) so an explicit
            # clear can revert precisely these keys, and only where unchanged.
            cfg["draft_test_override"]["seeded_login"] = {
                k: cfg[k] for k in _seeded
            }
            app.logger.info(
                "test_extract: seeded login selectors %s for %s from draft picks",
                _seeded, sid)
    except Exception:
        pass
    s_meta[sid] = _build_meta(cfg)
    _save_sites_config()

    started = False
    enqueued = False
    if sid in runners:
        runner = runners[sid]
        runner.update_config(cfg)
        if url:
            try:
                runner.load_urls([url])  # SiteRunner has no add_url; load_urls is the real enqueue (v3.66.245; cf. the v3.66.8 fix at the orphan-resume path)
                enqueued = True
                # GCW probe mode (v3.66.274): stamp the per-job flag so
                # _process_one samples first-bytes-then-aborts instead of
                # downloading the whole file. Mirrors the force_download
                # job-flag pattern; cleared with the job at run end.
                if probe:
                    try:
                        with runner._lock:
                            if url in runner.jobs:
                                runner.jobs[url]["probe"] = True
                    except Exception:
                        pass
                # BP-VH3: force re-download — stamp the per-job flag so
                # _dedup_preflight bypasses the history dedup and a previously
                # `done` URL is re-tested instead of silently skipped.
                if force_download:
                    try:
                        with runner._lock:
                            if url in runner.jobs:
                                runner.jobs[url]["force_download"] = True
                    except Exception:
                        pass
            except Exception as e:
                return jsonify({"ok": False,
                                "error": f"enqueue failed: {str(e)[:160]}"}), 500
        try:
            runner.start()
            started = True
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"start failed: {str(e)[:160]}"}), 500

    return jsonify({"ok": True, "site_id": sid, "override_set": True,
                    "persist": persist, "probe": probe,
                    "force_download": force_download, "enqueued": enqueued,
                    "started": started})

def register_routes(app) -> int:
    app.register_blueprint(template_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("template."))
