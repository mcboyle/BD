#!/usr/bin/env python3
"""cockpit_console.py — the operator cockpit GUI (Flask blueprint).

A sleek, dark, single-page operator console for AUTHORIZED LOCAL BD operations.
It turns the cockpit into the day-to-day workflow surface: view every report,
run allowlisted report generators, trigger authorized local capture tasks, drive
the one-click autopilot workflow, embed the local noVNC capture browser, and
import a planning spreadsheet as reviewable structured data.

Security model (enforced in cockpit_core.py, which this only wraps):
  * GET routes view; POST routes act — and POST only ever invokes ALLOWLISTED
    tools via fixed argv (shell=False). There is no free-form command path.
  * Auth + CSRF are inherited from the host app's before_request hooks (bearer
    or session + X-CSRF-Token), identical to every other POST in the app.
  * All paths confine under approved roots; logs/artifacts are posture-scanned
    and redacted before display.
  * The noVNC URL is config/env only — never taken from the browser.

This is NOT C2. No remote node, no agent dispatch, no arbitrary command, no
shell console, no replay, no signed-URL reconstruction, no token reuse, no
automatic selector/profile/corpus/debt mutation. Everything stays human-gated.

Integration:
    from tools.cockpit_console import bp as cockpit_bp
    app.register_blueprint(cockpit_bp)        # mounts at /cockpit
Env:
    BD_FRAMEWORK_REPORTS  reports root (shared with the read-only dashboard)
    BD_CAPTURES_ROOT      approved root for capture output
    BD_COCKPIT_TASKS      task working dir
    BD_NOVNC_URL          the local noVNC URL (e.g. http://10.0.70.20:6080/vnc.html)
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, render_template_string, abort

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc  # noqa: E402

try:
    import markdown as _md
except Exception:  # pragma: no cover
    _md = None

import re as _re
import html as _html
from html.parser import HTMLParser as _HTMLParser

# F-COCKPIT01-03: allowlist sanitizer for rendered-markdown report HTML before
# it is handed to the client (which sets it via innerHTML). python-markdown
# passes raw inline HTML through, so a report could carry <script>/on*/
# javascript: and execute in the cockpit. We re-emit ONLY markdown's own safe
# tag/attr set; everything else (script/style/iframe/object, event handlers,
# javascript:/data: URLs) is dropped. Dependency-free (stdlib HTMLParser).
_MD_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "ul", "ol", "li",
    "strong", "em", "b", "i", "code", "pre", "blockquote", "a", "img",
    "table", "thead", "tbody", "tr", "th", "td", "del", "sup", "sub", "span",
}
_MD_ALLOWED_ATTRS = {
    "a": {"href", "title"}, "img": {"src", "alt", "title"}, "*": {"class"},
}
_MD_SAFE_URL = _re.compile(r"^(?:https?:|mailto:|#|/|\./|\.\./)", _re.I)


class _ReportHtmlSanitizer(_HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def _clean_attrs(self, tag, attrs):
        allowed = _MD_ALLOWED_ATTRS.get(tag, set()) | _MD_ALLOWED_ATTRS.get("*", set())
        kept = []
        for k, v in attrs:
            k = (k or "").lower()
            if k.startswith("on") or k not in allowed:
                continue
            if k in ("href", "src") and (not v or not _MD_SAFE_URL.match(v.strip())):
                continue
            kept.append((k, v or ""))
        return "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in kept)

    def handle_starttag(self, tag, attrs):
        if tag in _MD_ALLOWED_TAGS:
            self.out.append(f"<{tag}{self._clean_attrs(tag, attrs)}>")

    def handle_startendtag(self, tag, attrs):
        if tag in _MD_ALLOWED_TAGS:
            self.out.append(f"<{tag}{self._clean_attrs(tag, attrs)}/>")

    def handle_endtag(self, tag):
        if tag in _MD_ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(_html.escape(data, quote=False))


def _sanitize_report_html(html_str):
    """Return an allowlist-sanitized copy of rendered-markdown HTML."""
    if not html_str:
        return html_str
    try:
        p = _ReportHtmlSanitizer()
        p.feed(html_str)
        p.close()
        return "".join(p.out)
    except Exception:
        # Fail closed: if sanitization can't complete, drop all markup.
        return _html.escape(html_str, quote=False)

bp = Blueprint("cockpit", __name__, url_prefix="/cockpit")


# ─────────────────────────────────────────────────────────────────────────────
# Report discovery (GET) — every report type, read-only
# ─────────────────────────────────────────────────────────────────────────────

# the report families the viewer surfaces, matched by filename
_REPORT_FAMILIES = [
    ("Executive", ("exec_summary", "executive")),
    ("Cockpit", ("cockpit", "operator_cockpit", "capture_cockpit", "autopilot_cockpit")),
    ("Framework", ("framework", "maturity", "risk", "audit", "calibration")),
    ("Corpus / debt", ("corpus", "debt")),
    ("Capture analysis", ("offline_analysis", "drift_report", "capture_inventory")),
    ("Site learning", ("site_profile", "site_health", "site_drift", "rendition_profile")),
    ("Selector learning", ("selector", "cross_site")),
    ("Validation readiness", ("validation_readiness", "validation")),
]


def _classify(name: str) -> str:
    low = name.lower()
    for fam, keys in _REPORT_FAMILIES:
        if any(k in low for k in keys):
            return fam
    return "Other"


def _iter_reports() -> List[Dict[str, Any]]:
    out = []
    for root in (cc.reports_root(), cc.tasks_root()):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_file() and f.suffix in (".md", ".json"):
                rel = f.relative_to(root)
                out.append({
                    "name": str(rel),
                    "root": "reports" if root == cc.reports_root() else "tasks",
                    "family": _classify(f.name),
                    "kind": f.suffix.lstrip("."),
                    "mtime": f.stat().st_mtime,
                })
    return sorted(out, key=lambda r: -r["mtime"])


@bp.get("/api/reports")
def api_reports():
    return jsonify({"reports": _iter_reports()})


@bp.get("/api/report")
def api_report():
    """View one report (confined, redacted)."""
    name = request.args.get("name", "")
    root_key = request.args.get("root", "reports")
    root = cc.reports_root() if root_key == "reports" else cc.tasks_root()
    p = cc.confine(name, root)
    if p is None or not p.is_file():
        abort(404)
    text = p.read_text(encoding="utf-8", errors="replace")
    text = cc.redact(text)                       # never show signing values
    if p.suffix == ".json":
        try:
            return jsonify({"name": name, "kind": "json",
                            "data": json.loads(text)})
        except Exception:
            pass
    html = _sanitize_report_html(
        _md.markdown(text, extensions=["tables", "fenced_code"])) if _md else None
    return jsonify({"name": name, "kind": p.suffix.lstrip("."),
                    "text": text, "html": html})


# ─────────────────────────────────────────────────────────────────────────────
# Allowlist surfaces (GET) — what can be run, and why it's allowed
# ─────────────────────────────────────────────────────────────────────────────

@bp.get("/api/allowlist")
def api_allowlist():
    def _pub(d):
        return {k: {"label": v["label"], "why": v["why"],
                    "human_review": v.get("human_review", False)}
                for k, v in d.items()}
    return jsonify({
        "report_runners": _pub(cc.REPORT_RUNNERS),
        "capture_tools": _pub(cc.CAPTURE_TOOLS),
        "axes": list(cc._AXES),
        "roots": {
            "reports": str(cc.reports_root()),
            "captures": str(cc.captures_root()),
            "tasks": str(cc.tasks_root()),
        },
    })


@bp.get("/api/novnc")
def api_novnc():
    url = cc.novnc_url()                          # config/env ONLY
    return jsonify({
        "url": url,
        "configured": bool(url),
        "note": "For authorized LOCAL capture sessions only. The URL is set by "
                "server config (BD_NOVNC_URL); it cannot be supplied from the "
                "browser. The cockpit does not drive the browser — you log in "
                "and play manually in your own session.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Actions (POST) — allowlisted only. Auth+CSRF inherited from the host app.
# ─────────────────────────────────────────────────────────────────────────────

@bp.post("/api/run-report")
def api_run_report():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "")
    params = body.get("params", {}) or {}
    if name not in cc.REPORT_RUNNERS:
        return jsonify({"error": f"report runner not allowlisted: {name!r}"}), 400
    try:
        rec = cc.start_task("report", name, params)
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"task": rec})


@bp.post("/api/run-capture")
def api_run_capture():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "")
    params = body.get("params", {}) or {}
    if name not in cc.CAPTURE_TOOLS:
        return jsonify({"error": f"capture tool not allowlisted: {name!r}"}), 400
    try:
        rec = cc.start_task("capture", name, params)
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"task": rec})


@bp.post("/api/captures/finish")
def api_capture_finish():
    """Stop a running interactive (noVNC) capture by writing the FINISH/CANCEL
    sentinel that capture_session.py polls for. Body: {task_id, discard?}."""
    body = request.get_json(silent=True) or {}
    tid = str(body.get("task_id", ""))
    discard = bool(body.get("discard", False))
    try:
        res = cc.finish_capture(tid, discard=discard)
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@bp.post("/api/captures/goto")
def api_capture_goto():
    """Re-navigate a running interactive capture back to its start URL by
    dropping the GOTO sentinel capture_session.py polls for. Body: {task_id}.
    Used after a login redirect dumps the session on a host/landing page."""
    body = request.get_json(silent=True) or {}
    tid = str(body.get("task_id", ""))
    try:
        res = cc.goto_capture(tid)
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@bp.post("/api/captures/pick")
def api_capture_pick():
    """Arm / poll / clear a one-shot ACTIVE element-pick on a running
    interactive (noVNC) capture. Body: {task_id, action: arm|poll|clear}.

    Drives the PICK_ARM / PICK_RESULT sentinels that capture_session.py services
    on its poll tick -- the same cross-process pattern as /api/captures/finish.
    Auth + CSRF are inherited from the host app's before_request (like finish).
    """
    body = request.get_json(silent=True) or {}
    tid = str(body.get("task_id", ""))
    action = str(body.get("action", "arm"))
    try:
        res = cc.pick_capture(tid, action=action)
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


def _wacz_for_task(task_id: str):
    """Return (wacz_path, None) for the newest .wacz in a capture task's output
    dir, or (None, error_message)."""
    t = cc.get_task(task_id)
    if not t:
        return None, f"no such task: {task_id!r}"
    out = t.get("out_dir")
    if not out:
        return None, f"task {task_id!r} has no output dir"
    waczs = sorted(Path(out).glob("*.wacz"), key=lambda p: -p.stat().st_mtime)
    if not waczs:
        return None, "no .wacz in the task output yet (finish the capture first)"
    return waczs[0], None


@bp.post("/api/captures/normalize")
def api_capture_normalize():
    """Onboarding step: build a RICH template draft from a captured .wacz and
    normalize it into a runtime-shape REVIEW CANDIDATE under
    templates/review_candidates/. Runs in-process; the normalizer scrubs
    signing material and never enables anything. Body: {task_id} (newest .wacz
    in that capture task's output) or {wacz: <filename under captures root>}."""
    body = request.get_json(silent=True) or {}
    tid = str(body.get("task_id", "")).strip()
    wname = str(body.get("wacz", "")).strip()
    if tid:
        wacz, err = _wacz_for_task(tid)
        if err:
            return jsonify({"error": err}), 400
    elif wname:
        p = cc.confine(wname, cc.captures_root())
        if p is None or not p.is_file():
            return jsonify({"error": f"capture not found under captures root: {wname!r}"}), 400
        wacz = p
    else:
        return jsonify({"error": "provide 'task_id' or 'wacz' (filename under captures root)"}), 400
    try:
        from tools import build_template_from_wacz as _wb
        from bulk_downloader import template_normalize as _tn
        draft = _wb.build_template(Path(wacz))
        cand = _tn.normalize_draft(draft)
    except Exception as e:  # pragma: no cover - defensive
        return jsonify({"error": f"build/normalize failed: {e}"}), 500
    host = cand.get("host") or (draft.get("source") or {}).get("host") or "unknown"
    outdir = cc._ROOT / "templates" / "review_candidates"
    outdir.mkdir(parents=True, exist_ok=True)
    outp = outdir / f"{host}.candidate.json"
    try:
        outp.write_text(json.dumps(cand, indent=2), encoding="utf-8")
    except OSError as e:
        return jsonify({"error": f"could not write candidate: {e}"}), 500
    nd = draft.get("network_discovery") or {}
    sel = cand.get("selectors") or {}
    return jsonify({
        "host": host,
        "status": cand.get("status"),
        "warnings": cand.get("warnings", []),
        "resolutions": cand.get("resolutions", []),
        "observed_api_hosts": nd.get("observed_api_hosts", []),
        "network_patterns": len(cand.get("network_patterns") or []),
        "has_download_trigger": bool((sel.get("download") or {}).get("trigger")),
        "candidate_path": str(outp.relative_to(cc._ROOT)),
        "wacz": Path(wacz).name,
    })


def _first_selector(v):
    """A candidate's selector fields are either a single CSS string or a
    fallback CHAIN (list). The /api/template/sandbox match-checker takes one
    CSS string per field, so collapse a chain to its FIRST non-empty entry
    (runtime is first-match-wins, so the primary selector is the right probe).
    Returns "" for anything empty/unusable. Structural only — never a value."""
    if isinstance(v, (list, tuple)):
        for x in v:
            if isinstance(x, str) and x.strip():
                return x.strip()
        return ""
    if isinstance(v, str):
        return v.strip()
    return ""


def _candidate_sandbox_template(selectors):
    """Map a review candidate's NESTED, scrubbed runtime-shape selectors
    (selectors.download.trigger / .row_selectors[], selectors.login.{user_field,
    pass_field, submit_btn}) to the FLAT shape /api/template/sandbox consumes
    (trigger_selector, dl_selector, user_field, pass_field, submit_btn,
    dismiss_selectors). Wave B1: lets the cockpit "test static / test live"
    buttons probe a draft's selectors against a fresh page without enabling it.

    POSTURE: structural ONLY. The candidate is already scrubbed at the normalize
    boundary (no values, no signing material); this forwards selector SHAPES, not
    runtime download triggers or any secret. Named keys only (defense-in-depth)."""
    sel = selectors if isinstance(selectors, dict) else {}
    dl = sel.get("download") if isinstance(sel.get("download"), dict) else {}
    login = sel.get("login") if isinstance(sel.get("login"), dict) else {}
    rows = dl.get("row_selectors")
    return {
        # trigger opens the modal; the in-modal row link is the actual dl target.
        "trigger_selector": _first_selector(dl.get("trigger")),
        "dl_selector": _first_selector(rows),
        "user_field": _first_selector(login.get("user_field")),
        "pass_field": _first_selector(login.get("pass_field")),
        "submit_btn": _first_selector(login.get("submit_btn")),
        # candidates carry no dismiss-modal selectors (added in manual review).
        "dismiss_selectors": "",
    }


def _candidate_override_template(c, src_name=None):
    """Build the NESTED, structural template the Wave B2 (v3.66.240)
    ``POST /api/template/test_extract`` override branch consumes.

    ``template_assist.merge_template_download_hints`` feeds the override through
    ``template_to_learned_download``, which reads ``selectors.download``
    (trigger / row_selectors / button) + ``selectors.quality`` (open_menu /
    resolution_option) + ``resolutions`` + ``host`` — the NESTED shape. The FLAT
    ``sandbox_template`` (built for /api/template/sandbox) would resolve ZERO
    selectors there, so the live-extract surface needs this distinct nested
    object. Returns ``None`` when there is nothing the override could act on
    (no download selectors) so the GUI can hide the no-op.

    POSTURE: identical to ``_candidate_sandbox_template`` — the candidate is
    already scrubbed at the normalize boundary; we forward selector SHAPES under
    known keys only (never ``**c``). No values, no signing material."""
    if not isinstance(c, dict):
        return None
    sel = c.get("selectors") if isinstance(c.get("selectors"), dict) else {}
    dl = sel.get("download") if isinstance(sel.get("download"), dict) else {}
    qual = sel.get("quality") if isinstance(sel.get("quality"), dict) else {}
    _empty = (None, "", [], {})
    dl_out = {k: dl.get(k) for k in ("trigger", "row_selectors", "button")
              if dl.get(k) not in _empty}
    if not dl_out:
        return None  # nothing to extract off -> don't offer a no-op override
    qual_out = {k: qual.get(k) for k in ("open_menu", "resolution_option")
                if qual.get(k) not in _empty}
    out = {
        "host": c.get("host"),
        "_template_file": src_name,
        "selectors": {"download": dl_out},
        "resolutions": [r for r in (c.get("resolutions") or [])],
    }
    if qual_out:
        out["selectors"]["quality"] = qual_out
    return out


def _candidate_draft_file(host):
    """The originating draft filename in ``templates/drafts/`` for a candidate
    host, IF it exists on disk. Used by the Wave B2 GUI enable box (-> promote)
    and the persist-ON draft writeback. Returns ``None`` when absent (enable via
    the CLI command instead; persist-ON then writes the live site config only,
    no draft writeback)."""
    if not host:
        return None
    name = f"{host}.template-draft.json"
    try:
        if (cc._ROOT / "templates" / "drafts" / name).is_file():
            return name
    except Exception:
        pass
    return None


@bp.get("/api/review-candidates")
def api_review_candidates():
    """List normalized review candidates (templates/review_candidates/*.candidate.json):
    runtime-shape, scrubbed, status review_ready | draft_review_required — never
    enabled. Read-only; promotion stays a deliberate CLI/runbook step (it can
    overwrite an enabled template), so the cockpit only surfaces them + the exact
    promote command."""
    d = cc._ROOT / "templates" / "review_candidates"
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.candidate.json")):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                out.append({"file": p.name, "error": f"unreadable: {e}"})
                continue
            sel = c.get("selectors") or {}
            dl = sel.get("download") or {}
            src = c.get("source") or {}
            rel = p.relative_to(cc._ROOT)
            # Wave B1 panel: forward the REVIEW-ONLY observed-workflow block the
            # normalizer carried onto the candidate (template_normalize
            # ._review_workflow). Structural ONLY — derived_steps are scrubbed
            # pre-formatted strings; trigger_candidate is a selector SHAPE (never
            # the runtime download trigger); verify is the advisory readout.
            # redact_artifact already ran at the normalize boundary; we forward
            # only the known structural keys (defense-in-depth — never **wf).
            _wf = c.get("workflow") or {}
            _ver = _wf.get("verify") or {}
            wf_out = None
            if _wf:
                wf_out = {
                    "derived_steps": [str(s) for s in (_wf.get("derived_steps") or [])],
                    "trigger_candidate": _wf.get("trigger_candidate"),
                    "trigger_evidence": _wf.get("trigger_evidence"),
                    "source": _wf.get("source"),
                    "verify": ({
                        "tier": _ver.get("tier"),
                        "checks": list(_ver.get("checks") or []),
                        "warnings": list(_ver.get("warnings") or []),
                        "gap_count": _ver.get("gap_count"),
                        "action_count": _ver.get("action_count"),
                    } if _ver else None),
                }
            out.append({
                "file": p.name,
                "host": c.get("host"),
                "status": c.get("status"),
                "warnings": c.get("warnings", []),
                "resolutions": c.get("resolutions", []),
                "has_download_trigger": bool(dl.get("trigger")),
                "has_row_selectors": bool(dl.get("row_selectors")),
                "network_patterns": len(c.get("network_patterns") or []),
                "captured_at": src.get("captured_at"),
                "path": str(rel),
                "promote_cmd": f"python3 tools/promote_template.py {rel} --out-dir templates/_staged_review --enable",
                "mtime": p.stat().st_mtime,
                "workflow": wf_out,
                # Wave B1: flat selector shape for the cockpit "test static/live"
                # buttons -> POST /api/template/sandbox. Structural, named keys.
                "sandbox_template": _candidate_sandbox_template(sel),
                # Wave B2 (v3.66.240): NESTED structural template the
                # /api/template/test_extract override branch consumes (the flat
                # sandbox shape above resolves ZERO selectors there). Same scrub
                # posture; None when there are no download selectors to act on.
                "override_template": _candidate_override_template(c, p.name),
                # Wave B2: originating draft file (templates/drafts/), present
                # only if on disk -> drives the GUI enable box (promote) + the
                # persist-ON draft writeback. None => enable via CLI; persist-ON
                # then writes the live site config only (no draft writeback).
                "draft_file": _candidate_draft_file(c.get("host")),
            })
    return jsonify({"candidates": out, "dir": str((cc._ROOT / 'templates' / 'review_candidates').relative_to(cc._ROOT))})


@bp.post("/api/captures/build-template")
def api_build_template():
    """Build a recognition template from TWO recon captures of one action.

    Runs the existing offline pipeline in-process:
    ``capture_synth.synthesize`` -> ``capture_workbench.build_workbench`` to a
    reviewable ``DetectorDraft``, and (when ``freeze`` is set)
    ``capture_template.build_template`` to a frozen template dict. This stays
    recognition-only — no capture replay, no network, no rule-table writes
    (the build functions guarantee this) — so it fits the cockpit's
    read/compute posture. The multi-capture build pipeline was previously
    reachable from no route or GUI.

    Body: ``{a, b}`` capture filenames under ``BD_CAPTURES_ROOT`` (.json or
    .wacz), plus optional ``freeze: bool``. For unit testing, inline capture
    dicts may be supplied as ``{cap_a, cap_b}`` instead of filenames.
    """
    from bulk_downloader.capture_synth import synthesize
    from bulk_downloader import capture_workbench as wb
    from bulk_downloader import capture_template as ct
    from bulk_downloader import capture_ingest as ci

    body = request.get_json(silent=True) or {}
    freeze = bool(body.get("freeze"))

    cap_a, cap_b = body.get("cap_a"), body.get("cap_b")
    if not (isinstance(cap_a, dict) and isinstance(cap_b, dict)):
        name_a = str(body.get("a") or "").strip()
        name_b = str(body.get("b") or "").strip()
        if not name_a or not name_b:
            return jsonify({"error": "provide two captures: filenames "
                            "{a,b} under the captures root, or inline "
                            "{cap_a,cap_b}"}), 400
        root = cc.captures_root()
        loaded = []
        for nm in (name_a, name_b):
            p = (root / nm).resolve()
            if not p.is_relative_to(root) or not p.is_file():
                return jsonify({"error": f"capture not found under "
                                f"captures root: {nm!r}"}), 400
            try:
                loaded.append(ci.load_capture(str(p)))
            except Exception as e:
                return jsonify({"error": f"could not load capture "
                                f"{nm!r}: {e}"}), 400
        cap_a, cap_b = loaded

    try:
        synth = synthesize(cap_a, cap_b)
        draft = wb.build_workbench(synth, captures=(cap_a, cap_b))
    except Exception as e:
        return jsonify({"error": f"build failed: {e}"}), 400

    out = {
        "ok": True,
        "draft": draft.to_dict(),
        "synth": {
            "host": synth.get("host"),
            "entry_url": synth.get("entry_url"),
            "confidence": synth.get("confidence"),
            "request_count": len(synth.get("requests", []) or []),
        },
    }
    if freeze:
        try:
            out["template"] = ct.build_template(draft)
        except Exception as e:
            return jsonify({"error": f"freeze failed: {e}"}), 400
    return jsonify(out)


@bp.post("/api/captures/build-multi-template")
def api_build_multi_template():
    """Compare several APPROVED captures of one site into a review-required
    draft (selector support counts, rejected candidates, reusable network
    patterns, resolution priority). Recognition-only — no replay, no network,
    no rule writes.

    Body: ``{captures: [{role, name}|{role, capture}], host?}`` where each
    item names a file under the captures root or supplies an inline capture
    dict. ``role`` is one of login / player / quality_menu / download_menu /
    download_result (free-form is accepted and surfaced as-is).
    """
    from bulk_downloader import template_multi as tm
    from bulk_downloader import capture_ingest as ci

    body = request.get_json(silent=True) or {}
    items = body.get("captures")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "provide 'captures': a list of {role, name} "
                        "(file under the captures root) or {role, capture} "
                        "(inline dict)"}), 400

    root = cc.captures_root()
    norm = []
    for it in items:
        if not isinstance(it, dict):
            return jsonify({"error": "each capture must be an object"}), 400
        role = str(it.get("role") or "unknown")
        if isinstance(it.get("capture"), dict):
            norm.append({"role": role, "capture": it["capture"]})
            continue
        nm = str(it.get("name") or "").strip()
        if not nm:
            return jsonify({"error": "each capture needs 'name' (file under "
                            "the captures root) or 'capture' (inline dict)"}), 400
        p = (root / nm).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            return jsonify({"error": f"capture not found under captures "
                            f"root: {nm!r}"}), 400
        try:
            norm.append({"role": role, "capture": ci.load_capture(str(p))})
        except Exception as e:
            return jsonify({"error": f"could not load capture {nm!r}: {e}"}), 400

    try:
        draft = tm.build_multi_capture_draft(norm, host=body.get("host"))
    except Exception as e:
        return jsonify({"error": f"build failed: {e}"}), 400
    return jsonify({"ok": True, "draft": draft})


@bp.get("/api/status")
def api_status():
    """Consolidated 'what is actually active' snapshot for the System Status
    page: selected browser backend + CloakBrowser availability, local DOM
    capture assets (rrweb/snapdom), manual-login -> runtime profile handoff,
    keepalive backend + keeper states, and capture/template corpus. Read-only,
    no browser, safe offline. Each block is individually guarded so one missing
    subsystem can't blank the whole panel."""
    from bulk_downloader import cloak
    from bulk_downloader import dom_recorder as dr
    from bulk_downloader import profile_sync as ps

    out = {"ok": True}

    try:
        out["browser_backend"] = {
            "selected": cloak.resolve_backend({}),
            "cloakbrowser": cloak.get_status(),
        }
    except Exception as e:
        out["browser_backend"] = {"error": str(e)}

    try:
        st = dr.get_status()
        st["local"] = dr.using_local_assets()
        out["capture_assets"] = st
    except Exception as e:
        out["capture_assets"] = {"error": str(e)}

    try:
        out["manual_login_handoff"] = ps.handoff_status()
    except Exception as e:
        out["manual_login_handoff"] = {"error": str(e)}

    try:
        from bulk_downloader import session_keeper as sk
        out["keepalive"] = {
            "default_backend": cloak.resolve_backend({}),
            "keepers": sk.get_status(),
        }
    except Exception as e:
        out["keepalive"] = {"error": str(e)}

    try:
        root = cc.captures_root()
        present = bool(root) and root.is_dir()
        n = 0
        if present:
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in (".json", ".wacz"):
                    n += 1
        out["corpus"] = {"captures_root": str(root), "present": present,
                         "capture_count": n}
    except Exception as e:
        out["corpus"] = {"error": str(e)}

    return jsonify(out)


@bp.get("/api/tasks")
def api_tasks():
    return jsonify({"tasks": cc.list_tasks()})


@bp.get("/api/task/<task_id>")
def api_task(task_id: str):
    t = cc.get_task(task_id)
    if not t:
        abort(404)
    return jsonify({"task": t, "log": cc.get_task_log(task_id)})


@bp.post("/api/import-plan/preview")
def api_import_preview():
    """Preview a planning spreadsheet/CSV as DATA. Never executes anything."""
    body = request.get_json(silent=True) or {}
    if "csv" in body:
        rows = cc.read_csv_text(body["csv"])
    elif "rows" in body and isinstance(body["rows"], list):
        rows = body["rows"]
    else:
        return jsonify({"error": "provide 'csv' text or 'rows' list"}), 400
    return jsonify(cc.parse_plan(rows))


# ─────────────────────────────────────────────────────────────────────────────
# Corpus / debt status (GET, read-only — reuses the validation_corpus reader)
# ─────────────────────────────────────────────────────────────────────────────

@bp.get("/api/debt")
def api_debt():
    try:
        from bulk_downloader import validation_corpus as vc
        r = vc.debt_report(vc.load_corpus())
        return jsonify({
            "entries": len(vc.load_corpus()),
            "correction": len(r["correction_debt"]),
            "capability": len(r["capability_debt"]),
            "validation": len(r["validation_debt"]),
            "validation_items": [e["id"] for e in r["validation_debt"]],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 200


# ── Wave 1 data views (all read-only) ───────────────────────────────────────

@bp.get("/api/mission")
def api_mission():
    return jsonify(cc.mission_control())


@bp.get("/api/timeline")
def api_timeline():
    return jsonify(cc.evidence_timeline(request.args.get("site") or None))


@bp.get("/api/corpus")
def api_corpus():
    def _b(v):
        return None if v in (None, "", "any") else (v == "true")
    return jsonify(cc.corpus_explorer(
        category=request.args.get("category") or None,
        outcome=request.args.get("outcome") or None,
        site=request.args.get("site") or None,
        has_debt=_b(request.args.get("has_debt")),
        query=request.args.get("q") or None,
    ))


@bp.get("/api/corpus/<entry_id>")
def api_corpus_entry(entry_id: str):
    e = cc.corpus_entry(entry_id)
    if not e:
        abort(404)
    # redact the entry text before returning (defensive — corpus is recognition-only)
    import json as _j
    return jsonify(_j.loads(cc.redact(_j.dumps(e))))


@bp.get("/api/drift")
def api_drift():
    return jsonify(cc.drift_ops())


@bp.get("/api/risk")
def api_risk():
    return jsonify(cc.risk_board())


@bp.get("/api/site/<site>")
def api_site(site: str):
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.site_intelligence(site)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/search")
def api_search():
    return jsonify(cc.smart_search(request.args.get("q", "")))


@bp.get("/api/warehouse")
def api_warehouse():
    return jsonify(cc.artifact_warehouse())


# ── Wave 2 operator state (campaigns / queue / notebook / review / packets) ──
# GET = view; POST = create/update local state. None of these POSTs run a tool
# except queue/launch, which routes through the SAME validated capture path.

@bp.get("/api/campaigns")
def api_campaigns():
    return jsonify({"campaigns": cc.campaign_list(), "goals": list(cc._CAMPAIGN_GOALS)})


@bp.post("/api/campaigns")
def api_campaign_create():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"campaign": cc.campaign_create(
            b.get("name"), b.get("goal"), b.get("site"), b.get("notes", ""))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/queue")
def api_queue():
    return jsonify(cc.queue_list())


@bp.post("/api/queue")
def api_queue_add():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"item": cc.queue_add(
            b.get("site"), b.get("label"), b.get("url", ""),
            b.get("axis"), b.get("priority", "medium"), b.get("campaign"))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/queue/reorder")
def api_queue_reorder():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(cc.queue_reorder(b.get("order", [])))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/queue/state")
def api_queue_state():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"item": cc.queue_set_state(b.get("id"), b.get("state"))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/queue/launch")
def api_queue_launch():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"task": cc.queue_launch(b.get("id"))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/notes/<site>")
def api_notes(site: str):
    try:
        return jsonify(cc.note_list(site))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/notes")
def api_note_add():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"note": cc.note_add(b.get("site"), b.get("kind"), b.get("text", ""))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/review")
def api_review():
    return jsonify(cc.review_items())


@bp.post("/api/review/decide")
def api_review_decide():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(cc.review_decide(b.get("item"), b.get("decision"), b.get("note", "")))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/packet/<site>")
def api_packet(site: str):
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.review_packet(site)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


# ── Wave 3 composition + ops (all read-only) ────────────────────────────────

@bp.get("/api/readiness")
def api_readiness():
    return jsonify(cc.release_readiness())


@bp.get("/api/exec")
def api_exec():
    return jsonify(cc.exec_summary(request.args.get("period", "all")))


@bp.get("/api/coverage")
def api_coverage():
    return jsonify(cc.coverage_heatmap())


@bp.get("/api/resources")
def api_resources():
    return jsonify(cc.resource_stats())


@bp.get("/api/graph")
def api_graph():
    import json as _j
    return jsonify(_j.loads(cc.redact(_j.dumps(cc.knowledge_graph()))))


@bp.get("/api/diff")
def api_diff():
    kind = request.args.get("kind", "capture")
    a = request.args.get("a", "")
    b = request.args.get("b", "")
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.evidence_diff(kind, a, b)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/health-checks")
def api_health_checks():
    return jsonify(cc.health_checks(run=False))


@bp.post("/api/health-checks/run")
def api_health_checks_run():
    # read-only refresh: recomputes dashboards + writes a snapshot. No capture,
    # no browser, no corpus/selector/profile change.
    return jsonify(cc.health_checks(run=True))


# ── Phase 1 completion (this list's Waves 1-3 new features) ─────────────────
# GET = view; the only POSTs add inert state (saved views, collections).

@bp.get("/api/inbox")
def api_inbox():
    return jsonify(cc.next_best_action())


@bp.get("/api/daily-mission")
def api_daily_mission():
    return jsonify(cc.daily_mission())


@bp.get("/api/notifications")
def api_notifications():
    return jsonify(cc.smart_notifications())


@bp.get("/api/activity")
def api_activity():
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    return jsonify(cc.activity_feed(limit))


@bp.get("/api/investigate/<site>")
def api_investigate(site: str):
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.investigation_workspace(site)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/review-roi")
def api_review_roi():
    return jsonify(cc.review_roi())


@bp.get("/api/saved-views")
def api_saved_views():
    return jsonify({"views": cc.saved_view_list(), "kinds": list(cc._SAVED_VIEW_KINDS)})


@bp.post("/api/saved-views")
def api_saved_view_add():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"view": cc.saved_view_add(b.get("name"), b.get("kind"),
                                                  b.get("params", {}))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/saved-views/delete")
def api_saved_view_delete():
    b = request.get_json(silent=True) or {}
    return jsonify(cc.saved_view_delete(b.get("id")))


@bp.get("/api/trace/<entry_id>")
def api_trace(entry_id: str):
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.decision_trace(entry_id)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/assumptions")
def api_assumptions():
    return jsonify(cc.assumption_center())


@bp.get("/api/confidence")
def api_confidence():
    return jsonify(cc.confidence_decomposition())


@bp.get("/api/collections")
def api_collections():
    return jsonify({"collections": cc.collection_list()})


@bp.post("/api/collections")
def api_collection_create():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"collection": cc.collection_create(b.get("name"), b.get("note", ""))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/collections/add")
def api_collection_add():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"collection": cc.collection_add(b.get("id"), b.get("entry_id"))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/lessons")
def api_lessons():
    import json as _j
    return jsonify(_j.loads(cc.redact(_j.dumps(cc.lessons_learned()))))


@bp.get("/api/org-memory")
def api_org_memory():
    return jsonify(cc.organizational_memory())


# ── Phase 2 TRIVIAL reskins (read-only views/pivots over existing data) ─────

@bp.get("/api/cross-site-drift")
def api_cross_site_drift():
    return jsonify(cc.cross_site_drift())


@bp.get("/api/portfolio-ranking")
def api_portfolio_ranking():
    return jsonify(cc.portfolio_ranking())


@bp.get("/api/blind-spots")
def api_blind_spots():
    return jsonify(cc.blind_spots())


@bp.get("/api/compliance")
def api_compliance():
    return jsonify(cc.compliance_summary())


@bp.get("/api/evidence-scarcity")
def api_evidence_scarcity():
    return jsonify(cc.evidence_scarcity())


@bp.get("/api/capture-yield")
def api_capture_yield():
    return jsonify(cc.capture_yield())


@bp.get("/api/decision-quality")
def api_decision_quality():
    return jsonify(cc.decision_quality())


# ── Band B: Cross-Site + What-If (read-only except inert escalation flags) ──

@bp.get("/api/impact/<target>")
def api_impact(target: str):
    try:
        import json as _j
        return jsonify(_j.loads(cc.redact(_j.dumps(cc.impact_simulator(target)))))
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/api/capture-opportunity")
def api_capture_opportunity():
    return jsonify(cc.capture_opportunity())


@bp.get("/api/structural-similarity")
def api_structural_similarity():
    return jsonify(cc.structural_similarity())


@bp.get("/api/family-explorer")
def api_family_explorer():
    return jsonify(cc.family_explorer())


@bp.get("/api/family-health")
def api_family_health():
    return jsonify(cc.family_health())


@bp.get("/api/escalations")
def api_escalations():
    return jsonify(cc.escalation_list())


@bp.post("/api/escalations")
def api_escalate():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"escalation": cc.escalate(b.get("item_id"), b.get("reason", ""))})
    except cc.ValidationError as e:
        return jsonify({"error": str(e)}), 400


@bp.post("/api/escalations/clear")
def api_escalation_clear():
    b = request.get_json(silent=True) or {}
    return jsonify(cc.escalation_clear(b.get("item_id")))


# ── Band C scoring models + #127 + narrative (all read-only) ────────────────

@bp.get("/api/maturity")
def api_maturity():
    return jsonify(cc.maturity_score())


@bp.get("/api/complexity")
def api_complexity():
    return jsonify(cc.complexity_score())


@bp.get("/api/org-health")
def api_org_health():
    return jsonify(cc.org_health_index())


@bp.get("/api/portfolio-opportunity")
def api_portfolio_opportunity():
    return jsonify(cc.portfolio_opportunity())


@bp.get("/api/narrative")
def api_narrative():
    return jsonify(cc.operational_narrative())


# ── Band F: Forecasting & Trends (v3.66.109) — DATA-GATED, read-only ─────────

@bp.get("/api/forecasting")
def api_forecasting():
    """Band F aggregate: every data-blocked forecasting/trend/sustainability
    metric, each gated and honest. Withholds rather than fabricates. Read-only."""
    return jsonify(cc.forecasting_overview())


# ── Template Intelligence (v3.66.110) — read-only, recognition-only ──────────
from tools import cockpit_templates as ctpl  # noqa: E402
from tools import autonomy_policy as apol  # noqa: E402
from tools import autonomy_housekeeping as ahk  # noqa: E402
from tools import autonomy_guardrails as agr  # noqa: E402
from tools import autonomy_review as arv  # noqa: E402
from tools import autonomy_oracle as aor  # noqa: E402
from tools import autonomy_center as ac  # noqa: E402
from tools import autonomy_eligibility as ael  # noqa: E402
from tools import autonomy_rollback as arb  # noqa: E402
from tools import autonomy_trust as atr  # noqa: E402
from tools import autonomy_validation as av  # noqa: E402
from tools import autonomy_impact as ai  # noqa: E402
from tools import autonomy_promotion as apr  # noqa: E402
from tools import autonomy_staging as stg  # noqa: E402
from tools import autonomy_live as liv  # noqa: E402
from tools import autonomy_queue_hk as qhk  # noqa: E402,F401  (registers queue_housekeeping kind)
from tools import autonomy_library_reconcile as lrc  # noqa: E402,F401  (registers library_reconcile kind)
from tools import autonomy_apply as aap  # noqa: E402


@bp.get("/api/template/video-health")
def api_template_video_health():
    """Per-site video/download template health + drift. Read-only."""
    return jsonify(ctpl.video_template_health())


@bp.get("/api/template/download-explain")
def api_template_download_explain():
    """Explain the download-candidate scoring decision (pure scorer, no live
    fetch/model/replay). Read-only."""
    return jsonify(ctpl.download_decision_explainer())


# ── Phase 2: Login Template Intelligence (v3.66.111) — read-only ─────────────

@bp.get("/api/template/login-health")
def api_template_login_health():
    """Per-site login-template health + session freshness + recent login history.
    Read-only; no credential values, no login attempted."""
    return jsonify({"health": ctpl.login_template_health(),
                    "history": ctpl.login_history()})


@bp.get("/api/template/login-drift")
def api_template_login_drift():
    """Login drift signals (classified) + a safe login dry-run sample. Read-only;
    no credential submission."""
    return jsonify({"drift": ctpl.login_drift_report(),
                    "dry_run": ctpl.login_dry_run()})


@bp.get("/api/template/login-review")
def api_template_login_review():
    """Read-only login-template review queue (suggestions are data-only; the
    approve/reject workbench is Phase 4)."""
    return jsonify(ctpl.login_review_queue())


# ── Phase 3: Template Drift Intelligence (v3.66.112) — read-only ─────────────

@bp.get("/api/template/unified-health")
def api_template_unified_health():
    """Unified video+login template health with stability + maturity + drift
    summary per site. Read-only; recognition-only."""
    return jsonify(ctpl.unified_template_health())


@bp.get("/api/template/drift-intel")
def api_template_drift_intel():
    """Drift intelligence aggregate: timeline + frequency + severity summary +
    likely root causes. Factual logs (not forecasts); honest thin-data flags.
    Read-only."""
    timeline = ctpl.drift_timeline()
    sev = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for e in timeline.get("events", []):
        s = e.get("severity")
        if s in sev:
            sev[s] += 1
    return jsonify({
        "timeline": timeline,
        "frequency": ctpl.drift_frequency(),
        "severity_summary": sev,
        "root_causes": ctpl.drift_root_causes(),
        "stability": ctpl.template_stability_score(),
        "maturity": ctpl.template_maturity_score(),
    })


# ── Phase 4: Template Review Workbench (v3.66.113) — read-only data; ──────────
# approve/reject reuses the existing inert /api/review/decide (no new POST).

@bp.get("/api/template/review-queue")
def api_template_review_queue():
    """Template review workbench feed: suggestions with before/after diffs,
    confidence, change history, evidence, and any recorded decision. Recording a
    decision (via /api/review/decide) never applies it. Read-only data."""
    return jsonify(ctpl.template_review_queue())


# ── Phase 5: Site Playbooks (v3.66.114) — read-only living dossier ───────────

@bp.get("/api/template/playbook-index")
def api_template_playbook_index():
    """Directory of every site's dossier-at-a-glance. Read-only."""
    return jsonify(ctpl.site_playbook_index())


@bp.get("/api/template/playbook")
def api_template_playbook():
    """Living dossier for one site (?site=). Read-only aggregation."""
    return jsonify(ctpl.site_playbook(request.args.get("site")))


# ── Phase 6: Family Intelligence (v3.66.115) — read-only cross-site analysis ─

@bp.get("/api/template/family-intel")
def api_template_family_intel():
    """Overview of every inferred family: shared selectors/workflow/drift/failures.
    Read-only."""
    return jsonify(ctpl.family_intelligence())


@bp.get("/api/template/family")
def api_template_family():
    """One family's detail (?name=): shared selectors + cross-pollination
    suggestions (data-only). Read-only."""
    return jsonify(ctpl.family_detail(request.args.get("name")))


# ── Phase 7: Template Autopilot (v3.66.116) — operator-guided, read-only ─────

@bp.get("/api/template/autopilot")
def api_template_autopilot():
    """Operator-guided run for one URL/site (?target=): detect → templates →
    health → download analysis → drift → suggested updates → review queue. Detection
    is recognition-only (no fetch); nothing applied. Read-only."""
    return jsonify(ctpl.template_autopilot(request.args.get("target")))


# ── Phase 8: Capture Intelligence (v3.66.117) — read-only, posture-safe ──────

@bp.get("/api/template/capture-intel")
def api_template_capture_intel():
    """Per-capture quality/completeness/coverage + missing evidence. POSTURE-SAFE
    metadata only (presence/counts/rendition names/signing-marker NAMES); no content,
    no signing values, no reassembly. Read-only."""
    return jsonify(ctpl.capture_intelligence())


# ── Phase 9: Site Readiness Score (v3.66.118) — read-only composite ──────────

@bp.get("/api/template/site-readiness")
def api_template_site_readiness():
    """Per-site readiness ('can I trust this site today?') — DEFINED composite of
    login/video health, drift, evidence freshness, capture quality, template
    maturity, review debt; weights + inputs shown. Read-only."""
    return jsonify(ctpl.site_readiness())


# ── Phase 10: Operator Mission Control (v3.66.119) — read-only capstone ──────

@bp.get("/api/template/mission-control")
def api_template_mission_control():
    """The single operator screen: Needs Attention / Healthy / Active Work /
    Recommended Actions, rolled up from Phases 1–9 + ops state. Read-only — nothing
    here acts; recommended actions are suggestions tied to a site."""
    return jsonify(ctpl.operator_mission_control())


# ── Phase A: Autonomy Governance (v3.66.120) — READ-ONLY status surface ──────
# Policy edits and the kill switch are deliberate governance actions performed via
# audited functions (apol.set_policy_level / freeze / unfreeze), not casual web
# toggles (doc §7). The cockpit exposes only read-only views of that state.

@bp.get("/api/policy/matrix")
def api_policy_matrix():
    """The 2-D autonomy policy matrix: per class — configured level, selectable
    ceiling, Level-3 availability + why, required vs present guardrails, pinned
    actions. Read-only; nothing here executes."""
    return jsonify(apol.policy_report())


@bp.get("/api/policy/status")
def api_policy_status():
    """Compact governance status: policy version/hash, kill-switch state, guardrails
    built vs pending, snapshot count, whether any class is autonomous (False in
    Phase A). Read-only."""
    return jsonify(apol.governance_status())


@bp.get("/api/policy/audit")
def api_policy_audit():
    """The policy-change + kill-switch audit log (append-only). Read-only view."""
    return jsonify({"entries": apol.read_audit()})


@bp.get("/api/policy/snapshots")
def api_policy_snapshots():
    """Recorded immutable decision snapshots (empty in Phase A — nothing produces
    decisions yet). Read-only. ?id=<id> returns one full snapshot."""
    sid = request.args.get("id")
    if sid:
        snap = apol.get_decision_snapshot(sid)
        return jsonify(snap or {"error": "not found"})
    return jsonify({"snapshots": apol.list_decision_snapshots()})


# ── Phase B: Class B automation (v3.66.121) — READ-ONLY views ────────────────
# Housekeeping actions run via audited functions (ahk.run_housekeeping / reorder_queue
# / ... and ahk.reverse_action), gated by the kill switch and the Class B policy
# level. The cockpit exposes read-only views of the log + a suggest-mode preview.

@bp.get("/api/housekeeping/status")
def api_housekeeping_status():
    """Class B status: level, auto-eligibility, kill-switch, guardrails, counts.
    Read-only."""
    return jsonify(ahk.housekeeping_status())


@bp.get("/api/housekeeping/preview")
def api_housekeeping_preview():
    """What each Class B action WOULD do right now (suggest mode for all four) —
    applies nothing. Read-only."""
    return jsonify(ahk.run_housekeeping(mode="suggest", by="cockpit-preview"))


@bp.get("/api/housekeeping/log")
def api_housekeeping_log():
    """The append-only Class B action log. Read-only."""
    return jsonify({"entries": ahk.housekeeping_log()})


# ── Phase C: Guardrail infrastructure (v3.66.122) — READ-ONLY views ──────────
# Rollback / review-window sweep / self-throttle run via audited functions
# (agr.rollback / sweep_review_windows / self_throttle_check). Class C auto remains
# impossible (the correctness oracle is Phase E). The cockpit exposes read-only views.

@bp.get("/api/guardrails/status")
def api_guardrails_status():
    """Guardrail registry, caps config, backlog/inflight, self-throttle metrics, kill
    switch, and whether Class C auto is possible (False until Phase E). Read-only."""
    return jsonify(agr.guardrails_status())


@bp.get("/api/guardrails/changes")
def api_guardrails_changes():
    """The rollback ledger — recorded changes and their rolled-back state. Read-only."""
    return jsonify({"changes": agr.list_changes()})


@bp.get("/api/guardrails/pending")
def api_guardrails_pending():
    """Outstanding unreviewed auto-changes and their fail-closed review deadlines
    (Class C). Read-only."""
    return jsonify({"pending": agr.outstanding_unreviewed(),
                    "inflight_sites": agr.inflight_sites(),
                    "backlog": agr.backlog_ok()})


# ── Phase D: Human review experience (v3.66.123) — READ-ONLY surfaces ────────
# These inform a human's Approve/Reject decision; the decision itself is committed via
# the existing audited path (/api/review/decide or agr.mark_reviewed). No autonomy,
# no mutation here.

@bp.get("/api/review/dashboard")
def api_review_dashboard():
    """Outstanding reviews with evidence-chain pointers, soonest fail-closed deadline
    first. Read-only."""
    return jsonify(arv.review_dashboard())


@bp.get("/api/review/evidence")
def api_review_evidence():
    """Full evidence chain for a change (?change_id=): decision snapshot, diff, review
    window, site evidence. Read-only."""
    return jsonify(arv.evidence_chain(request.args.get("change_id", "")))


@bp.get("/api/review/diff")
def api_review_diff():
    """Before/after + structured diff for a change (?change_id=). Read-only."""
    return jsonify(arv.change_diff(request.args.get("change_id", "")))


@bp.get("/api/review/rollback-preview")
def api_review_rollback_preview():
    """What rolling a change back would restore (?change_id=) — does not execute.
    Read-only."""
    return jsonify(arv.rollback_preview(request.args.get("change_id", "")))


@bp.get("/api/review/audit")
def api_review_audit():
    """Unified decision audit across policy / housekeeping / guardrails. Read-only."""
    return jsonify(arv.decision_audit())


# ── Phase E: Tiered correctness oracle & eligibility engine (v3.66.124) ──────
# Eligibility ASSESSMENT only — no automation. Completing the oracle does NOT enable
# Class C auto (default Approve-each; per-site grant store empty by design; no Class C
# apply path). All endpoints read-only.

@bp.get("/api/oracle/status")
def api_oracle_status():
    """Oracle/eligibility summary: guardrails complete, tier distribution, 0
    automation-eligible sites, Class C not enabled by default. Read-only."""
    return jsonify(aor.oracle_status())


@bp.get("/api/oracle/eligibility")
def api_oracle_eligibility():
    """The per-site eligibility matrix (tiers; automation-eligible: none). Read-only."""
    return jsonify(aor.eligibility_matrix())


@bp.get("/api/oracle/verdict")
def api_oracle_verdict():
    """Per-site tiered oracle verdict (?site=). Read-only."""
    return jsonify(aor.oracle_verdict(request.args.get("site", "")))


@bp.get("/api/oracle/held-out")
def api_oracle_held_out():
    """Held-out vs training capture designation per site. Read-only."""
    return jsonify(aor.held_out_evidence_report())


@bp.get("/api/oracle/ineligible")
def api_oracle_ineligible():
    """Sites at Tier 0 / with hard failures + permanently-ineligible actions. Read-only."""
    return jsonify(aor.ineligible_sites_report())


@bp.get("/api/oracle/reports")
def api_oracle_reports():
    """The assembled oracle report bundle (read-only data). Artifacts are written only
    by the explicit generate_oracle_reports() function, never automatically."""
    return jsonify(aor.oracle_reports())


# ── Phase F: Controlled Class B autonomy (v3.66.125) — READ-ONLY views ───────
# The autonomy cycle (ac.run_autonomy_cycle) is host-scheduled (cron/CLI), kill-switch
# + Class-B-level + BD_AUTONOMY_ENABLED gated, default dry-run. The cockpit shows what
# cycles did and what the next would do; it does not trigger cycles (no new POST).

@bp.get("/api/autonomy/center")
def api_autonomy_center():
    """Class B autonomy overview: level, whether the cycle would apply + why, kill
    switch, last cycle. Class C/D stay human-controlled. Read-only."""
    return jsonify(ac.autonomy_center())


@bp.get("/api/autonomy/queue")
def api_autonomy_queue():
    """Queue + proposed (dry-run) reordering + health. Read-only."""
    return jsonify(ac.queue_intelligence())


@bp.get("/api/autonomy/review-ops")
def api_autonomy_review_ops():
    """Review-deadline tracking + pending Class C reviews. Read-only."""
    return jsonify(ac.review_operations())


@bp.get("/api/autonomy/notifications")
def api_autonomy_notifications():
    """In-GUI notifications + what the next generation would add. Read-only."""
    return jsonify(ac.notification_center())


@bp.get("/api/autonomy/governance-health")
def api_autonomy_governance_health():
    """Governance monitoring: kill switch, policy, guardrails, throttle, anomalies.
    Read-only."""
    return jsonify(ac.governance_health())


@bp.get("/api/autonomy/metrics")
def api_autonomy_metrics():
    """Class B autonomy metrics: cycle history + action/reversal counts + throttle.
    Read-only."""
    return jsonify(ac.automation_metrics())


# ── Phase G / G1: Eligibility Governance (v3.66.126) — READ-ONLY views ────────
# Participation-eligibility EVALUATION with decay, a layer above the oracle. No
# automation, no grant, no Class C apply path -> participation_eligible is 0 for every
# site; qualification decays automatically as held-out evidence goes stale. No new POST.

@bp.get("/api/eligibility/status")
def api_eligibility_status():
    """Eligibility-governance summary: Class C level, freshness/tier/cap thresholds,
    evidence-qualified + considered counts, 0 participation-eligible. Read-only."""
    return jsonify(ael.eligibility_status())


@bp.get("/api/eligibility/overview")
def api_eligibility_overview():
    """Per-site participation eligibility + decay reasons + the capped considered set.
    Read-only."""
    return jsonify(ael.eligibility_overview())


@bp.get("/api/eligibility/site")
def api_eligibility_site():
    """Per-site eligibility verdict (?site=): oracle tier, evidence freshness, decay
    reasons, and why participation is blocked. Read-only."""
    return jsonify(ael.evaluate_site(request.args.get("site", "")))


# ── Phase G / G2: Rollback Center (v3.66.127) — READ-ONLY views ──────────────
# Surfaces the guardrail rollback engine. Reverting a change is an audited guardrail
# function (host cycle / operator), never a cockpit button -> no new POST. The engine
# already guarantees: revert restores before-state + is idempotent; review rejection
# triggers a revert; expired Class C auto-reverts; a reverser error freezes automation.

@bp.get("/api/rollback/center")
def api_rollback_center():
    """Rollback Center dashboard: engine readiness, reverser registry, history counts,
    pending/expiring windows, throttle health, freeze state. Read-only."""
    return jsonify(arb.rollback_center())


@bp.get("/api/rollback/history")
def api_rollback_history():
    """Recorded changes + which were reverted. Read-only."""
    return jsonify(arb.rollback_history())


@bp.get("/api/rollback/reversibility")
def api_rollback_reversibility():
    """Per-change reversibility (reverser registered AND not already rolled back).
    Read-only."""
    return jsonify(arb.reversibility_report())


@bp.get("/api/rollback/reversers")
def api_rollback_reversers():
    """The registered-reverser registry — the change kinds that are reversible (and thus
    eligible). Read-only."""
    return jsonify(arb.reverser_registry())


# ── Phase G / G3: Trust Decay (v3.66.128) — READ-ONLY views ──────────────────
# Per-site trust with automatic decay. Trust may only ever DECREASE automatically;
# restoring it is a human governance action (autonomy_trust.reset_trust, host/operator),
# never a cockpit button -> no new POST. Trust feeds eligibility (below min -> ineligible).

@bp.get("/api/trust/status")
def api_trust_status():
    """Trust summary: min/baseline thresholds, below-min count, freeze state. Read-only."""
    return jsonify(atr.trust_status())


@bp.get("/api/trust/overview")
def api_trust_overview():
    """Per-site trust + current signal + eligibility + the below-minimum set. Read-only."""
    return jsonify(atr.trust_overview())


@bp.get("/api/trust/site")
def api_trust_site():
    """Per-site trust detail (?site=): stored trust, current signal, what decay would
    lower it to, and the last human restore. Read-only."""
    return jsonify(atr.trust_site(request.args.get("site", "")))


# ── Phase G / G4: Validation Operations (v3.66.129) — READ-ONLY advisory ─────
# Advisory re-validation scheduling for held-out evidence. Recommends WHEN to re-validate;
# never captures, logs in, or re-runs the oracle (those are operator/host actions) -> no
# new POST. Freshness itself is enforced by eligibility (G1); this is the lead-time view.

@bp.get("/api/validation/status")
def api_validation_status():
    """Re-validation summary: interval/floor, due-soon/overdue/never counts. Read-only."""
    return jsonify(av.validation_status())


@bp.get("/api/validation/overview")
def api_validation_overview():
    """Per-site re-validation schedule + status counts. Read-only."""
    return jsonify(av.validation_overview())


@bp.get("/api/validation/site")
def api_validation_site():
    """Per-site re-validation detail (?site=): evidence age, status, recommended date.
    Read-only."""
    return jsonify(av.validation_schedule(request.args.get("site", "")))


# ── Phase G / G5: Impact Analysis (v3.66.130) — READ-ONLY, single-change ─────
# Analyses one proposed change (blast radius / reversibility / pinned / trust / tier).
# No family-wide promotion; never applies anything -> no new POST. Composes G1–G3.

@bp.get("/api/impact/status")
def api_impact_status():
    """Impact summary across sites (benign probe). Read-only."""
    return jsonify(ai.impact_status())


@bp.get("/api/impact/overview")
def api_impact_overview():
    """Per-site impact of a benign reversible probe change. Read-only."""
    return jsonify(ai.impact_overview())


@bp.get("/api/impact/analyze")
def api_impact_analyze():
    """Analyse one proposed change (?site=&target_kind=&action=). Read-only — never
    applies or promotes."""
    cand = {"site": request.args.get("site", ""),
            "target_kind": request.args.get("target_kind") or None,
            "action": request.args.get("action") or None}
    return jsonify(ai.impact_report(cand))


# ── Phase G / G6: Promotion Activity (v3.66.131) — READ-ONLY views over an ───
# append-only audit log of governance-state TRANSITIONS (ties G1–G5). The scan that
# records transitions is host-scheduled (cron/CLI), never a button -> no new POST.
# Nothing here is applied; participation_eligible never transitions to True.

@bp.get("/api/promotion/status")
def api_promotion_status():
    """Transition totals + tracked fields. Read-only."""
    return jsonify(apr.promotion_status())


@bp.get("/api/promotion/activity")
def api_promotion_activity():
    """Append-only governance-transition log (recent entries). Read-only."""
    return jsonify(apr.activity_log())


@bp.get("/api/promotion/site")
def api_promotion_site():
    """Per-site transition history (?site=). Read-only."""
    return jsonify(apr.site_activity(request.args.get("site", "")))


# ── v1 autonomy wire: Staged Config Candidates (v3.66.132) — READ-ONLY views ─
# The maintenance loop + fail-closed sweep are host-scheduled (cron/CLI), never buttons ->
# no new POST. Accept/reject reuses the existing audited /api/review/decide path. v1
# maintains staged candidates only: no production write, no promotion, no behavioral change.

@bp.get("/api/staging/status")
def api_staging_status():
    """Staged-candidate summary: pending count, next deadline, eligible sites. Read-only."""
    return jsonify(stg.staging_status())


@bp.get("/api/staging/candidates")
def api_staging_candidates():
    """Pending staged candidates + behavioral-unchanged check. Read-only."""
    return jsonify(stg.staged_candidates())


@bp.get("/api/staging/candidate")
def api_staging_candidate():
    """One site's staged candidate (?site=): evidence delta, behavioral-unchanged
    confirmation, rollback-preview, deadline. Read-only."""
    return jsonify(stg.staged_candidate(request.args.get("site", "")))


# ── H autonomy wire: Live Config Apply (v3.66.133) — READ-ONLY views ─────────
# Apply loop, fail-closed sweep, and grant reconcile are host-scheduled (cron/CLI). Grants
# are issued human-only via the CLI tool. Accept/reject reuse /api/review/decide. No new POST.

@bp.get("/api/live/status")
def api_live_status():
    """Live-apply summary: pending live changes, next deadline, grants active/suspended,
    apply-path/Class-C state. Read-only."""
    return jsonify(liv.live_status())


@bp.get("/api/live/grants")
def api_live_grants():
    """Per-site grant table with current tier/trust and participation_eligible. Read-only.
    Grants are created human-only via the CLI; the system may only suspend them."""
    return jsonify(liv.live_grants())


@bp.get("/api/live/pending")
def api_live_pending():
    """Pending live changes with the learned/scoring change flags and deadline. Read-only."""
    return jsonify(liv.live_pending())


@bp.get("/api/live/change")
def api_live_change():
    """One live change (?id=): before/after learned+scoring, rollback-preview. Read-only.
    COMPATIBILITY ALIAS for /api/authority/change filtered to kind=live_site_config."""
    return jsonify(liv.live_change(request.args.get("id", "")))


# ── Authority (preferred generic Class-C governance API) — READ-ONLY ──────────
# One surface over all apply kinds. /api/live/* above remain as compatibility aliases
# returning this data filtered to kind=live_site_config. No new POST: accept/reject reuse
# /api/review/decide; grants are issued human-only via the CLI (no grant button).

@bp.get("/api/authority/status")
def api_authority_status():
    """Class-C apply summary across all kinds: pending, next deadline, grants active/
    suspended, registered kinds, Class C state. Read-only."""
    return jsonify(aap.authority_status())


@bp.get("/api/authority/grants")
def api_authority_grants():
    """The (site x kind) grant matrix with current tier/trust and participation_eligible.
    Read-only. Grants are created human-only via the CLI; the system may only suspend."""
    return jsonify(aap.authority_grants())


@bp.get("/api/authority/pending")
def api_authority_pending():
    """Pending Class-C changes across all kinds (kind, site, changed keys, deadline).
    Read-only."""
    return jsonify(aap.authority_pending())


@bp.get("/api/authority/kinds")
def api_authority_kinds():
    """Registered Class-C apply kinds (action class, reverser/validator presence).
    Read-only."""
    return jsonify(aap.authority_kinds())


@bp.get("/api/authority/change")
def api_authority_change():
    """One Class-C change (?id=) of any kind: before/after, rollback-preview. Read-only."""
    return jsonify(aap.authority_change(request.args.get("id", "")))


# ── Debug log console (read-only, redacted) ────────────────────────────────

@bp.get("/api/debug-log")
def api_debug_log():
    try:
        n = int(request.args.get("lines", "200"))
    except ValueError:
        n = 200
    return jsonify(cc.debug_log(n))


# ── Interactive shell (OPT-IN, hard-gated on BD_COCKPIT_SHELL=1) ────────────
# Imported lazily so cockpit_core / the recognition surface never depend on it.

def _shell_request_trusted() -> bool:
    """F-COCKPIT03-01: self-contained origin/bind guard for the arbitrary-command
    web-shell. Trusted = loopback (a local request / the standalone cockpit's
    127.0.0.1 bind) or a same-origin browser request (the cockpit UI this server
    served, Referer host:port == Host). A remote cross-origin request is refused
    regardless of the host app's auth config, so default-on can never mean
    reachable from an untrusted network."""
    ra = (request.remote_addr or "")
    if ra in ("127.0.0.1", "::1", "localhost"):
        return True
    ref = request.headers.get("Referer", "")
    host = request.headers.get("Host", "")
    if not (ref and host):
        return False
    try:
        from urllib.parse import urlparse
        r = urlparse(ref)
        ref_netloc = (r.hostname or "").lower()
        if r.port:
            ref_netloc = f"{ref_netloc}:{r.port}"
        if r.scheme == "http" and r.port == 80:
            ref_netloc = (r.hostname or "").lower()
        elif r.scheme == "https" and r.port == 443:
            ref_netloc = (r.hostname or "").lower()
        return ref_netloc == host.lower()
    except Exception:
        return False


@bp.before_request
def _shell_origin_guard():
    """Apply the origin/bind guard to every /api/shell/* endpoint."""
    if "/api/shell/" not in (request.path or ""):
        return None
    if _shell_request_trusted():
        return None
    return jsonify({"error": "shell endpoints require a same-origin or loopback "
                             "request (not reachable from an untrusted network)"}), 403


@bp.get("/api/shell/status")
def api_shell_status():
    from tools import cockpit_shell as sh
    return jsonify(sh.shell_status())


@bp.post("/api/shell/open")
def api_shell_open():
    from tools import cockpit_shell as sh
    try:
        return jsonify(sh.shell_open())
    except sh.ShellError as e:
        return jsonify({"error": str(e)}), 403


@bp.post("/api/shell/input")
def api_shell_input():
    from tools import cockpit_shell as sh
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(sh.shell_input(b.get("session", ""), b.get("data", "")))
    except sh.ShellError as e:
        return jsonify({"error": str(e)}), 403


@bp.post("/api/shell/signal")
def api_shell_signal():
    from tools import cockpit_shell as sh
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(sh.shell_signal(b.get("session", ""), b.get("signal", "INT")))
    except sh.ShellError as e:
        return jsonify({"error": str(e)}), 403


@bp.get("/api/shell/poll")
def api_shell_poll():
    from tools import cockpit_shell as sh
    try:
        off = int(request.args.get("offset", "0"))
    except ValueError:
        off = 0
    try:
        return jsonify(sh.shell_poll(request.args.get("session", ""), off))
    except sh.ShellError as e:
        return jsonify({"error": str(e)}), 403


@bp.post("/api/shell/close")
def api_shell_close():
    from tools import cockpit_shell as sh
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(sh.shell_close(b.get("session", "")))
    except sh.ShellError as e:
        return jsonify({"error": str(e)}), 403


# ─────────────────────────────────────────────────────────────────────────────
# The GUI (single page, dark, sidebar nav)
# ─────────────────────────────────────────────────────────────────────────────

# ── Phase O: server-backed appearance prefs (optional sync; client localStorage
# stays primary). Cockpit-only/deploy-excluded; not gated by G12. Values are
# enum-validated; nothing free-form, no secrets. Persisted under BD_HOME/state.
_UI_LAYOUTS = {"side", "top", "rail", "mode", "miller", "bottombar", "focus"}
_UI_THEMES = {"live", "ocean", "forest", "tech", "galaxy", "sunset", "golden",
              "arctic", "desert", "botanical", "minimalist"}
_UI_TIERS = {"everyday", "advanced", "system"}
_UI_VALID = {"layout": _UI_LAYOUTS, "theme": _UI_THEMES, "vtier": _UI_TIERS}


def _ui_prefs_file() -> Path:
    base = Path(os.environ.get("BD_HOME") or _ROOT)
    return base / "state" / "cockpit_ui_prefs.json"


@bp.route("/api/ui_prefs", methods=["GET", "POST"])
def api_ui_prefs():
    f = _ui_prefs_file()
    if request.method == "GET":
        try:
            return jsonify({"prefs": json.loads(f.read_text(encoding="utf-8"))})
        except Exception:
            return jsonify({"prefs": {}})
    body = request.get_json(silent=True) or {}
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}
    for key, allowed in _UI_VALID.items():
        if key in body:
            val = body[key]
            if val not in allowed:
                return jsonify({"error": f"invalid {key}: {val!r}"}), 400
            cur[key] = val
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur), encoding="utf-8")
        tmp.replace(f)
    except Exception as e:  # pragma: no cover - disk failure
        return jsonify({"error": f"persist failed: {e}"}), 500
    return jsonify({"prefs": cur})


@bp.get("/")
@bp.get("")
def index():
    return render_template_string(_PAGE)


_PAGE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BD Operator Cockpit</title>
<style>
:root{
  /* Unified product dark palette (canonical: SPA themes.ts / index.css .dark).
   * Cockpit var NAMES kept (panel=surface, panel2=surface-2, line=hairline,
   * dim=ink-3, acc=primary, ok/warn/err=green/amber/red) — values mapped to
   * the product palette so both surfaces share one look. The 10 [data-theme]
   * blocks below stay for the picker until step A folds in the canonical 62. */
  --bg:#08090c; --surface:#14161c; --surface-2:#1a1d24; --hairline:rgba(255,255,255,0.08);
  --ink:#f5f5f7; --ink-3:#8a8a96; --primary:#6f7eff; --green:#2ed87a; --amber:#fbbf24;
  --red:#f87171; --pill:#1e222b; --font-head:inherit;
}
/* 1a — appearance themes: remap the tokens above; default (no data-theme) is live. */
[data-theme="ocean"]{--bg:#131c28;--surface:#1a2535;--surface-2:#223247;--hairline:#2c3a4f;--ink:#f1faee;--ink-3:#9fbcc4;--primary:#2d8b8b;--pill:#223247;--green:#56c79c;--amber:#e0b24a;--red:#e7777a;}
[data-theme="forest"]{--bg:#16241a;--surface:#1d2f20;--surface-2:#26402a;--hairline:#2f4a33;--ink:#faf9f6;--ink-3:#a9b394;--primary:#5f9a52;--pill:#26402a;--green:#7bbf6a;--amber:#d8b54a;--red:#d98080;--font-head:Georgia,'Times New Roman',serif;}
[data-theme="tech"]{--bg:#121214;--surface:#1c1c20;--surface-2:#26262d;--hairline:#2f2f36;--ink:#ffffff;--ink-3:#b6b6c0;--primary:#0066ff;--pill:#26262d;--green:#2fd07a;--amber:#e3b341;--red:#ff5a5a;}
[data-theme="galaxy"]{--bg:#1a1228;--surface:#241a37;--surface-2:#2f2448;--hairline:#382b52;--ink:#e9e9fb;--ink-3:#b9aacf;--primary:#6f73c8;--pill:#2f2448;--green:#6fc99a;--amber:#d8b54a;--red:#e07a9a;}
[data-theme="sunset"]{--bg:#fbf6ef;--surface:#ffffff;--surface-2:#fbf2e8;--hairline:#e8d7c2;--ink:#264653;--ink-3:#5d6f72;--primary:#e76f51;--pill:#fbf2e8;--green:#4f8f6b;--amber:#cf8a2b;--red:#cf5a43;--font-head:Georgia,'Times New Roman',serif;}
[data-theme="golden"]{--bg:#faf4ea;--surface:#fffdf8;--surface-2:#f8efdf;--hairline:#e8d7be;--ink:#4a403a;--ink-3:#7a6f64;--primary:#b0505a;--pill:#f8efdf;--green:#6a8f4f;--amber:#d99500;--red:#b34a3f;}
[data-theme="arctic"]{--bg:#f4f8fd;--surface:#ffffff;--surface-2:#eef4fb;--hairline:#d5e1ef;--ink:#243447;--ink-3:#56697f;--primary:#4a6fa5;--pill:#eef4fb;--green:#3f8f6a;--amber:#c79023;--red:#c25a55;}
[data-theme="desert"]{--bg:#faf2ec;--surface:#fffaf6;--surface-2:#f8ece3;--hairline:#e9d1c3;--ink:#5d2e46;--ink-3:#8a6273;--primary:#a85c4c;--pill:#f8ece3;--green:#6f9070;--amber:#c79030;--red:#a8485f;}
[data-theme="botanical"]{--bg:#f6f4ec;--surface:#fffefb;--surface-2:#f1f3e9;--hairline:#dde2d2;--ink:#243528;--ink-3:#5a6a55;--primary:#4a7c59;--pill:#f1f3e9;--green:#4a7c59;--amber:#d98f12;--red:#b7472a;--font-head:Georgia,'Times New Roman',serif;}
[data-theme="minimalist"]{--bg:#f7f8f9;--surface:#ffffff;--surface-2:#f2f4f6;--hairline:#dde2e6;--ink:#2b3840;--ink-3:#5d6b73;--primary:#3d4f5c;--pill:#f2f4f6;--green:#3f8f5a;--amber:#bf8a1f;--red:#c0504a;}
.brand,h1,h2,h3{font-family:var(--font-head,inherit)}
*{box-sizing:border-box} html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--primary);text-decoration:none}
/* thin, near-invisible scrollbars — track transparent, thumb only on hover of the scroll area */
*{scrollbar-width:thin;scrollbar-color:transparent transparent}
*:hover{scrollbar-color:var(--hairline) transparent}
*::-webkit-scrollbar{width:7px;height:7px}
*::-webkit-scrollbar-track{background:transparent}
*::-webkit-scrollbar-thumb{background:transparent;border-radius:7px;transition:background .15s}
*::-webkit-scrollbar-corner{background:transparent}
*:hover::-webkit-scrollbar-thumb{background:var(--hairline)}
*::-webkit-scrollbar-thumb:hover{background:var(--ink-3)}
.app{display:grid;grid-template-columns:var(--side-w,248px) 1fr;height:100vh;position:relative}
/* top-bar layout: nav becomes a horizontal strip across the top */
.app.topnav{grid-template-columns:1fr;grid-template-rows:auto 1fr}
.app.topnav .side{border-right:0;border-bottom:1px solid var(--hairline);padding:6px 14px;overflow-x:auto;overflow-y:hidden;max-height:none;display:flex;align-items:center;gap:12px;position:relative;z-index:50}
.app.topnav .brand{display:flex;align-items:center;gap:6px;padding:0;margin:0;flex:0 0 auto}
.app.topnav .brand .bword{flex:0 0 auto}
.app.topnav #layout_sel{width:auto;margin-top:0}
.app.topnav .nav{display:flex;flex-wrap:nowrap;gap:14px;align-items:center;flex:1 1 auto;min-width:0}
.app.topnav .navsec{margin-bottom:0;flex:0 0 auto}
.app.topnav .navhead{padding:2px 4px;white-space:nowrap}
/* 1.0 — collapsible Advanced/System drawers (collapsed by default) */
.drawerhead{cursor:pointer;display:flex;justify-content:flex-start;align-items:center;gap:6px;user-select:none}
.drawerhead .caret{display:none}
.navdrawer:not(.open) .drawerhead::before{transform:rotate(-90deg)}
.navdrawer .drawerbody{display:none}
.navdrawer.open .drawerbody{display:block}
.app.topnav .nav a{padding:4px 8px;white-space:nowrap;font-size:12px}
.app.topnav.focus .side{display:block}
/* 1b/1c — selectable layouts (all are nav-presentation; #main still swaps one panel) */
.app.rail{grid-template-columns:54px 1fr}
.app.rail .side{width:54px;overflow:hidden;white-space:nowrap;transition:width .14s ease}
.app.rail .side:hover{width:236px;overflow:auto;box-shadow:2px 0 18px rgba(0,0,0,.4);z-index:60}
.app.rail .navhead{opacity:.6}
.app.bottombar{grid-template-columns:1fr;grid-template-rows:1fr auto}
.app.bottombar .main{order:1}
.app.bottombar .side{order:2;border-right:0;border-top:1px solid var(--hairline);padding:6px 14px;overflow-x:auto;overflow-y:hidden;max-height:none;display:flex;align-items:center;gap:12px;position:relative;z-index:50}
.app.bottombar .brand{display:flex;align-items:center;gap:6px;padding:0;margin:0;flex:0 0 auto}
.app.bottombar .brand .bword{flex:0 0 auto}
.app.bottombar .nav{display:flex;flex-wrap:nowrap;gap:4px;align-items:center;flex:1 1 auto;min-width:0}
.app.bottombar .navsec,.app.bottombar .navdrawer{flex:0 0 auto;margin-bottom:0;position:relative}
.app.bottombar .navhead{padding:6px 10px;border-radius:6px;cursor:default;font-size:11px}
.app.bottombar .navhead:hover{background:var(--pill);color:var(--ink)}
.app.bottombar .navsec .navitems,.app.bottombar .navdrawer .drawerbody{display:none;position:fixed;min-width:184px;max-width:min(320px,92vw);background:var(--surface);border:1px solid var(--hairline);border-radius:8px;padding:6px;box-shadow:0 -12px 30px rgba(0,0,0,.5);z-index:1200}
.app.bottombar .navsec.baropen .navitems,.app.bottombar .navdrawer.baropen .drawerbody{display:block}
.app.bottombar .nav a{display:block;padding:6px 10px;white-space:nowrap;font-size:12.5px}
/* Slice C: bottom-bar More (folds Advanced + System under one menubar item) */
.moresec{display:none}
.app.bottombar .moresec{display:block}
.app.bottombar .navdrawer[data-tier="advanced"],.app.bottombar .navdrawer[data-tier="system"]{display:none}
.app.bottombar .moresubhead{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--ink-3);padding:6px 10px 2px}
.app.bottombar .main{padding-bottom:calc(28px + env(safe-area-inset-bottom,0px))}
.app.bottombar .side{padding-bottom:calc(6px + env(safe-area-inset-bottom,0px))}
.app.bottombar #appmenu{top:auto;bottom:calc(100% + 8px);left:12px}
/* polish: active-group pill on top + bottom bars */
.app.topnav .navsec.active .navhead,.app.topnav .navdrawer.active .navhead,
.app.bottombar .navsec.active .navhead,.app.bottombar .navdrawer.active .navhead{
  background:color-mix(in srgb,var(--primary) 16%,transparent);color:var(--ink);
  border:1px solid color-mix(in srgb,var(--primary) 45%,transparent)}
.tierseg{display:none}
.tierseg button{flex:1;background:var(--pill);border:1px solid var(--hairline);color:var(--ink-3);border-radius:6px;padding:4px 6px;font-size:11px;cursor:pointer}
.tierseg button.on{background:var(--primary);color:#fff;border-color:var(--primary)}
.app.mode .tierseg{display:flex;gap:4px;padding:8px 12px 2px}
.app.mode .drawerhead,.app.miller .drawerhead{pointer-events:none}
.app.mode .navsec[data-tier],.app.mode .navdrawer{display:none}
.app.mode[data-vtier="everyday"] .navsec[data-tier="everyday"]{display:block}
.app.mode[data-vtier="advanced"] .navdrawer[data-tier="advanced"]{display:block}
.app.mode[data-vtier="advanced"] .navdrawer[data-tier="advanced"] .drawerbody{display:block}
.app.mode[data-vtier="system"] .navdrawer[data-tier="system"]{display:block}
.app.mode[data-vtier="system"] .navdrawer[data-tier="system"] .drawerbody{display:block}
.app.miller{grid-template-columns:330px 1fr}
.app.miller .side{display:grid;grid-template-columns:104px 1fr;grid-template-rows:auto 1fr;padding:0}
.app.miller .brand{grid-column:1/-1;grid-row:1;padding:12px}
.app.miller .tierseg{display:flex;flex-direction:column;gap:4px;grid-column:1;grid-row:2;padding:8px;border-right:1px solid var(--hairline);align-content:start}
.app.miller .tierseg button{flex:0 0 auto}
.app.miller .nav{grid-column:2;grid-row:2;overflow:auto}
.app.miller .navsec[data-tier],.app.miller .navdrawer{display:none}
.app.miller[data-vtier="everyday"] .navsec[data-tier="everyday"]{display:block}
.app.miller[data-vtier="advanced"] .navdrawer[data-tier="advanced"]{display:block}
.app.miller[data-vtier="advanced"] .navdrawer[data-tier="advanced"] .drawerbody{display:block}
.app.miller[data-vtier="system"] .navdrawer[data-tier="system"]{display:block}
.app.miller[data-vtier="system"] .navdrawer[data-tier="system"] .drawerbody{display:block}
/* ── Shell redesign Slice 1: collapse / resize / compact brand / horizontal tiers ── */
/* Collapse primitive — doubled-class (0,3,0) beats every layout class (0,2,0) so it
   works in ALL layouts (this is the structural fix for the broken focus toggle). */
.app.app[data-collapsed="1"]{grid-template-columns:1fr}
.app.app[data-collapsed="1"] .side{display:none}
.app.app[data-collapsed="1"] .main{padding-left:52px}
.app.app[data-collapsed="1"] .resizer{display:none}
#reexpand{display:none;position:fixed;top:10px;left:8px;z-index:1600;width:30px;height:30px;
  align-items:center;justify-content:center;background:var(--surface-2);border:1px solid var(--hairline);
  color:var(--ink-3);border-radius:8px;cursor:pointer;font-size:15px;box-shadow:0 4px 14px rgba(0,0,0,.4)}
#reexpand:hover{color:var(--ink);border-color:var(--primary)}
.app[data-collapsed="1"] #reexpand{display:flex}
/* Resize handle on the sidebar's right edge */
.resizer{position:absolute;top:0;left:calc(var(--side-w,248px) - 3px);width:6px;height:100vh;
  cursor:col-resize;z-index:70;background:transparent;transition:background .1s}
.resizer:hover,.resizer.drag{background:var(--primary);opacity:.55}
.app.topnav .resizer,.app.bottombar .resizer,.app.rail .resizer{display:none}
/* Compact single-row brand + gear / collapse controls */
.brand{display:flex;align-items:center;gap:8px;padding:10px 12px;font-weight:700;font-size:14px;letter-spacing:.3px}
.brand .bword{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.brand small{display:none}
.brand .iconbtn{flex:0 0 auto;width:26px;height:26px;display:flex;align-items:center;justify-content:center;
  background:var(--pill);border:1px solid var(--hairline);color:var(--ink-3);border-radius:6px;cursor:pointer;font-size:13px}
.brand .iconbtn:hover{color:var(--ink);border-color:var(--primary)}
/* Appearance popover (holds the layout + theme + density controls; IDs preserved) */
#appmenu{display:none;position:absolute;z-index:1500;top:46px;left:12px;width:226px;
  background:color-mix(in srgb, var(--surface) 80%, transparent);
  -webkit-backdrop-filter:blur(16px) saturate(140%);backdrop-filter:blur(16px) saturate(140%);
  border:1px solid color-mix(in srgb, var(--hairline) 70%, transparent);border-radius:14px;padding:12px 14px;
  box-shadow:0 20px 54px rgba(0,0,0,.5)}
#appmenu.open{display:block}
#appmenu label{display:block;font-size:10.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.4px;margin:9px 0 3px}
#appmenu label:first-child{margin-top:0}
#appmenu select{width:100%;min-width:0}
#appmenu .seg{display:flex;gap:4px}
#appmenu .seg button{flex:1;background:var(--pill);border:1px solid var(--hairline);color:var(--ink-3);border-radius:6px;padding:5px;font-size:11px;cursor:pointer}
#appmenu .seg button.on{background:var(--primary);color:#fff;border-color:var(--primary)}
/* Slice A: described-list layout picker (replaces the visible native select) */
.iconbtn[aria-expanded="true"]{color:var(--ink);border-color:var(--primary);background:color-mix(in srgb,var(--primary) 18%,var(--pill))}
.laypick{display:flex;flex-direction:column;gap:3px}
.laypick .opt{display:flex;flex-direction:column;gap:1px;padding:8px 10px;border-radius:9px;cursor:pointer;border:1px solid transparent}
.laypick .opt:hover{background:var(--pill)}
.laypick .opt.on{background:color-mix(in srgb,var(--primary) 16%,transparent);border-color:color-mix(in srgb,var(--primary) 45%,transparent)}
.laypick .opt .on-name{font-size:13px;color:var(--ink);font-weight:600;display:flex;align-items:center;gap:6px}
.laypick .opt.on .on-name::after{content:"\2713";color:var(--primary);font-size:12px}
.laypick .opt .on-desc{font-size:11px;color:var(--ink-3)}
.laypick .opt:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
/* Horizontal tier segmenter everywhere it shows (kills Miller's vertical 104px column) */
.app.miller{grid-template-columns:var(--side-w,300px) 1fr}
.app.miller .side{display:block;grid-template-columns:none;padding:0}
.app.miller .brand{padding:10px 12px}
.app.miller .tierseg{display:flex;flex-direction:row;gap:4px;padding:6px 12px 4px;border-right:0}
.app.miller .nav{overflow:auto}
.app.mode .tierseg,.app.miller .tierseg{display:flex;flex-direction:row}
/* Top bar, rebuilt: each section is a menubar item with a hover dropdown (was a flood) */
.app.topnav .nav{gap:4px}
.app.topnav .navsec,.app.topnav .navdrawer{position:relative}
.app.topnav .navhead{padding:6px 10px;border-radius:6px;cursor:default;font-size:11px}
.app.topnav .navhead:hover{background:var(--pill);color:var(--ink)}
.app.topnav .navsec .navitems,.app.topnav .navdrawer .drawerbody{display:none;position:fixed;
  min-width:184px;max-width:min(320px,92vw);background:var(--surface);border:1px solid var(--hairline);border-radius:8px;padding:6px;
  box-shadow:0 12px 30px rgba(0,0,0,.5);z-index:1200}
.app.topnav .navsec.baropen .navitems,.app.topnav .navdrawer.baropen .drawerbody{display:block}
.app.topnav .nav a{display:block;padding:6px 10px;white-space:nowrap;font-size:12.5px}
/* Density: compact trims padding across nav, main, and cards */
.app.compact .nav a{padding:5px 12px;margin-bottom:1px}
.app.compact .main{padding:14px 18px}
.app.compact .card,.app.compact .panel{padding:11px}
.app.compact th,.app.compact td{padding:5px 9px}
/* command palette */
#cmdk{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2000;display:none;align-items:flex-start;justify-content:center}
#cmdk.open{display:flex}
#cmdk .box{background:var(--surface);border:1px solid var(--primary);border-radius:12px;margin-top:12vh;width:min(560px,92vw);box-shadow:0 16px 48px rgba(0,0,0,.6)}
#cmdk input{width:100%;border:0;background:transparent;color:var(--ink);font-size:16px;padding:16px 18px;outline:none;border-bottom:1px solid var(--hairline)}
#cmdk .results{max-height:50vh;overflow:auto;padding:6px}
#cmdk .res{padding:10px 14px;border-radius:8px;cursor:pointer;color:var(--ink)}
#cmdk .res.sel,#cmdk .res:hover{background:var(--primary);color:#fff}
.kbd{display:inline-block;background:var(--pill);border:1px solid var(--hairline);border-radius:4px;padding:1px 6px;font-size:11px;color:var(--ink-3);font-family:ui-monospace,monospace}
/* ── Slice 2: non-blocking toasts (replace native alert) ── */
#toasts{position:fixed;bottom:18px;right:18px;z-index:3000;display:flex;flex-direction:column;gap:8px;max-width:min(420px,92vw)}
.toast{background:var(--surface);border:1px solid var(--hairline);border-left:3px solid var(--primary);border-radius:8px;
  padding:10px 14px;color:var(--ink);font-size:13px;box-shadow:0 8px 26px rgba(0,0,0,.5);
  display:flex;gap:10px;align-items:flex-start;animation:toastin .16s ease}
.toast.err{border-left-color:var(--red)} .toast.ok{border-left-color:var(--green)} .toast.warn{border-left-color:var(--amber)}
.toast .tx{flex:1;min-width:0;word-break:break-word}
.toast .tcl{cursor:pointer;color:var(--ink-3);flex:0 0 auto;font-size:14px;line-height:1}
.toast .tcl:hover{color:var(--ink)}
@keyframes toastin{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
/* ── Slice 4: keyboard focus ring + help overlay ── */
a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,[tabindex]:focus-visible,.nav a:focus-visible,.tabbar button:focus-visible{outline:2px solid var(--primary);outline-offset:2px;border-radius:6px}
#help{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2100;display:none;align-items:center;justify-content:center}
#help.open{display:flex}
#help .box{background:var(--surface);border:1px solid var(--hairline);border-radius:12px;padding:18px 20px;width:min(440px,92vw);box-shadow:0 16px 48px rgba(0,0,0,.6)}
#help h3{margin:0 0 12px;font-size:15px} #help table{width:100%} #help td{padding:5px 8px;border:0;font-size:13px}
#help td:first-child{width:96px}
/* ── Slice 5: skeleton loaders + zebra rows + sticky viewer headers + empty state ── */
.skel{background:linear-gradient(90deg,var(--surface-2) 25%,var(--pill) 37%,var(--surface-2) 63%);background-size:400% 100%;
  animation:shimmer 1.2s ease infinite;border-radius:8px;height:14px;margin:9px 0}
@keyframes shimmer{from{background-position:100% 0}to{background-position:0 0}}
.skelbox{padding:8px 2px}
tbody tr:nth-child(even) td{background:rgba(127,127,127,.045)}
.viewer thead th{position:sticky;top:0;background:var(--surface-2);z-index:1}
.emptystate{color:var(--ink-3);text-align:center;padding:28px 16px;border:1px dashed var(--hairline);border-radius:10px}
.tscroll{overflow-x:auto;max-width:100%}
/* ── Slice 6: dismissible posture banner + pin-to-Everyday star ── */
.banner{position:relative}
.banner .bx{position:absolute;top:8px;right:10px;background:none;border:0;color:var(--ink-3);cursor:pointer;font-size:16px;line-height:1}
.banner .bx:hover{color:var(--ink)}
.nav a{position:relative}
.pinstar{position:absolute;right:8px;top:50%;transform:translateY(-50%);opacity:.4;cursor:pointer;font-size:12px}
.pinstar:hover{opacity:1}
.pinstar:focus-visible{opacity:1;outline:2px solid var(--primary);outline-offset:2px;border-radius:4px}
/* Slice D: status badges (separate from the favourite star toggle) */
.badge{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;padding:1px 6px;border-radius:999px;margin-left:6px;vertical-align:middle;line-height:1.5}
.badge.pinned{background:color-mix(in srgb,var(--primary) 22%,transparent);color:var(--primary)}
.badge.beta{background:color-mix(in srgb,var(--amber) 24%,transparent);color:var(--amber)}
.badge.new{background:color-mix(in srgb,var(--green) 24%,transparent);color:var(--green)}
@media (max-width:820px){
  .app{grid-template-columns:1fr}
  .side{position:fixed;z-index:1500;width:240px;height:100vh;transform:translateX(-100%);transition:transform .2s}
  .app.navopen .side{transform:translateX(0)}
  .cards{grid-template-columns:repeat(2,1fr)!important}
  .main{padding:62px 14px 16px}
  #mobilebar{display:flex!important}
}
#navscrim{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1490}
.app.navopen #navscrim{display:block}
#mobilebar{display:none;position:fixed;top:0;left:0;right:0;height:46px;background:var(--surface-2);border-bottom:1px solid var(--hairline);z-index:1400;align-items:center;gap:12px;padding:0 14px}
.side{background:var(--surface-2);border-right:1px solid var(--hairline);padding:10px 8px;overflow:auto}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:14px;letter-spacing:.3px;padding:9px 12px}
.brand small{display:none}
.nav a{display:block;padding:6px 10px;border-radius:7px;color:var(--ink-3);margin-bottom:1px;cursor:pointer}
.nav a:hover{background:var(--pill);color:var(--ink)}
.nav a.on{background:var(--primary);color:#fff}
.homecard{color:inherit;text-decoration:none}
.homecard:hover{border-color:var(--primary)}
/* Phase 2 — tabbed container chrome */
.tabbar{display:flex;gap:2px;border-bottom:1px solid var(--hairline);margin-bottom:14px;flex-wrap:wrap}
.tabbar button{background:none;border:none;border-bottom:2px solid transparent;color:var(--ink-3);padding:8px 13px;font-size:13px;cursor:pointer}
.tabbar button:hover{color:var(--ink)}
.tabbar button.on{color:var(--ink);border-bottom-color:var(--primary)}
.navsec{margin-bottom:2px}
.navhead{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--ink-3);padding:6px 10px 3px;cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px}
.navhead:hover{color:var(--ink)}
.navhead::before{content:"▾";font-size:9px;transition:transform .15s}
.navsec.collapsed .navhead::before{transform:rotate(-90deg)}
.navsec.collapsed .navitems{display:none}
.navitems a{padding-left:20px}
/* ── Home redesign (scoped .h* classes so other pages' .card styling is untouched) ── */
.main-inner{max-width:1280px}
.hrel{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:18px;font-size:12.5px;color:var(--ink-3)}
.hrel .dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px color-mix(in srgb,var(--green) 22%,transparent)}
.hrel .dot.bad{background:var(--red);box-shadow:0 0 0 3px color-mix(in srgb,var(--red) 22%,transparent)}
.hrel b{color:var(--ink);font-weight:600}.hrel .sep{opacity:.45}.hrel .stamp{margin-left:auto;font-size:11.5px;opacity:.8}
/* P6-7: Posture banner converges on the SPA Callout caution treatment
 * (amber-soft surface + amber/30 border + amber leading icon + role=note),
 * replacing the old left-rule notice so both surfaces frame advisories alike. */
.hposture{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;margin-bottom:18px;border-radius:8px;line-height:1.45;
  background:color-mix(in srgb,var(--amber) 12%,var(--surface));border:1px solid color-mix(in srgb,var(--amber) 30%,var(--hairline));font-size:13px}
.hposture .ic{flex:0 0 auto;color:var(--amber);font-size:15px;line-height:1.3;margin-top:1px}
.hposture .tx{flex:1 1 auto}.hposture b{color:var(--ink)}
.hreco{display:flex;align-items:center;gap:16px;padding:16px 20px;margin-bottom:24px;border-radius:10px;
  background:linear-gradient(180deg,color-mix(in srgb,var(--primary) 12%,var(--surface)),var(--surface));border:1px solid color-mix(in srgb,var(--primary) 35%,var(--hairline))}
.hreco .ric{flex:0 0 auto;width:38px;height:38px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,var(--primary) 20%,transparent);color:var(--primary);font-size:18px}
.hreco .rb{flex:1 1 auto;min-width:0}.hreco .rl{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--ink-3);margin-bottom:2px}
.hreco .rt{font-size:14px;color:var(--ink)}.hreco .rt b{color:var(--amber)}
.hreco .rc{flex:0 0 auto;padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;background:var(--primary);color:#fff;cursor:pointer;border:1px solid var(--primary);text-decoration:none}
.hkpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:24px}
@media (max-width:1180px){.hkpis{grid-template-columns:repeat(3,1fr)}}@media (max-width:680px){.hkpis{grid-template-columns:repeat(2,1fr)}}
.hkpi{min-height:86px;padding:16px 16px 18px;border-radius:10px;background:var(--surface);border:1px solid var(--hairline);display:flex;flex-direction:column}
.hkpi .kl{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--ink-3);margin-bottom:10px}
.hkpi .kv{font-size:26px;font-weight:700;line-height:1}.hkpi .kv.ok{color:var(--green)}.hkpi .kv.warn{color:var(--amber)}.hkpi .kv.err{color:var(--red)}
.hkpi .kh{margin-top:6px;font-size:11.5px;color:var(--ink-3)}
.hsec{margin-top:24px;margin-bottom:16px;font-size:15px}.hsec .sh{font-size:12px;color:var(--ink-3);font-weight:400}
.hacts{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media (max-width:1180px){.hacts{grid-template-columns:repeat(3,1fr)}}@media (max-width:820px){.hacts{grid-template-columns:repeat(2,1fr)}}
.hact{min-height:90px;padding:16px;border-radius:10px;background:var(--surface);border:1px solid var(--hairline);cursor:pointer;display:flex;flex-direction:column;transition:border-color .12s,transform .12s,background .12s;color:inherit;text-decoration:none}
.hact:hover{border-color:var(--primary);transform:translateY(-1px);background:color-mix(in srgb,var(--primary) 6%,var(--surface))}
.hact .at{font-size:13px;font-weight:600;color:var(--ink);margin-bottom:8px;line-height:1.45}
.hact .ad{font-size:12px;color:var(--ink-3);line-height:1.45;flex:1 1 auto}
.hact .ac{margin-top:12px;font-size:12px;font-weight:600;color:var(--primary)}
.hact:focus-visible,.hreco .rc:focus-visible{outline:2px solid var(--primary);outline-offset:2px;border-radius:8px}
.main{overflow:auto;padding:22px 26px}
h1{font-size:18px;margin:0 0 4px} .sub{color:var(--ink-3);margin:0 0 18px;font-size:13px}
.banner{background:linear-gradient(90deg,#15202e,#121722);border:1px solid var(--hairline);
  border-left:3px solid var(--amber);border-radius:10px;padding:10px 14px;margin-bottom:18px;color:var(--ink-3);font-size:12.5px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--surface);border:1px solid var(--hairline);border-radius:12px;padding:14px 16px}
.card .k{color:var(--ink-3);font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.card .v{font-size:22px;font-weight:700;margin-top:6px}
.card.ok .v{color:var(--green)} .card.warn .v{color:var(--amber)} .card.err .v{color:var(--red)}
.panel{background:var(--surface);border:1px solid var(--hairline);border-radius:12px;padding:16px 18px;margin-bottom:16px}
.panel h2{font-size:14px;margin:0 0 12px;letter-spacing:.2px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{background:var(--primary);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}
.btn.sec{background:var(--pill);color:var(--ink);border:1px solid var(--hairline)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.tool{border:1px solid var(--hairline);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--surface-2)}
.tool .nm{font-weight:600} .tool .why{color:var(--ink-3);font-size:12.5px;margin:4px 0 8px}
.tag{display:inline-block;background:var(--pill);border:1px solid var(--hairline);color:var(--ink-3);
  border-radius:999px;padding:1px 9px;font-size:11px;margin-left:6px}
.tag.rev{color:var(--amber);border-color:#3a2f12}
input,select{background:var(--surface-2);border:1px solid var(--hairline);color:var(--ink);
  border-radius:8px;padding:7px 10px;font:inherit;min-width:160px}
label.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--ink-3)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--hairline);vertical-align:top}
th{color:var(--ink-3);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.3px}
.st{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:999px}
.st.running{background:#15263a;color:#6cb0ff} .st.succeeded{background:#102a18;color:var(--green)}
.st.failed{background:#2a1213;color:var(--red)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:var(--surface-2);
  border:1px solid var(--hairline);border-radius:8px;padding:10px;white-space:pre-wrap;overflow:auto;max-height:340px}
.viewer{background:var(--surface-2);border:1px solid var(--hairline);border-radius:10px;padding:16px;max-height:60vh;overflow:auto}
.viewer h1,.viewer h2,.viewer h3{color:var(--ink)} .viewer table{margin:8px 0}
.muted{color:var(--ink-3)} .err{color:var(--red)} .ok{color:var(--green)} .warn{color:var(--amber)}
iframe.vnc{width:100%;height:64vh;border:1px solid var(--hairline);border-radius:10px;background:#000}
.hidden{display:none}
.flist a{display:block;padding:6px 8px;border-radius:6px;color:var(--ink-3)}
.flist a:hover{background:var(--pill);color:var(--ink)} .flist a.on{background:var(--primary);color:#fff}
.split{display:grid;grid-template-columns:280px 1fr;gap:14px}
.hoverpop{position:fixed;z-index:1000;background:var(--surface);border:1px solid var(--primary);
  border-radius:10px;padding:6px;min-width:220px;max-width:420px;max-height:50vh;overflow:auto;
  box-shadow:0 8px 28px rgba(0,0,0,.55)}
.hoverpop .hp-title{color:var(--ink-3);font-size:11px;text-transform:uppercase;letter-spacing:.4px;padding:4px 8px 6px}
.hoverpop .hp-row{padding:7px 10px;border-radius:6px;cursor:pointer;font-size:13px;color:var(--ink)}
.hoverpop .hp-row:hover{background:var(--primary);color:#fff}
.card.clk{cursor:pointer;transition:border-color .12s}
.card.clk:hover{border-color:var(--primary)}
tr.clk{cursor:pointer}
tr.clk:hover{background:var(--pill)}
</style></head><body>
<div class="app">
  <div id="mobilebar">
    <button class="iconbtn" id="navtoggle" aria-label="Open navigation menu" aria-expanded="false" title="Menu">&#9776;</button>
    <span class="bword" style="font-weight:700">BD Cockpit</span>
  </div>
  <div id="navscrim" aria-hidden="true"></div>
  <aside class="side">
    <div class="brand">
      <button class="iconbtn" id="collapsebtn" title="Collapse sidebar (f)">&lsaquo;</button>
      <span class="bword" title="authorized local ops">BD Cockpit<small>authorized local ops</small></span>
      <button class="iconbtn" id="gearbtn" title="View settings" aria-label="View settings" aria-expanded="false">&#9881;</button>
    </div>
    <div id="appmenu" role="menu" aria-label="View settings">
      <label>Layout</label>
      <div class="laypick" id="laypick" role="group" aria-label="Layout"></div>
      <select id="layout_sel" title="Layout" style="display:none"></select>
      <label>Theme</label>
      <select id="theme_sel" title="Theme"></select>
      <label>Density</label>
      <div class="seg" id="density_seg"><button data-d="">Comfortable</button><button data-d="compact">Compact</button></div>
    </div>
    <div class="tierseg" id="tierseg"></div>
    <nav class="nav" id="nav">
      <div class="navsec" data-tier="everyday" id="pinnedsec" style="display:none"><div class="navhead">Pinned</div><div class="navitems" id="pinned"></div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Home</div><div class="navitems">
        <a data-p="home" class="on">Home</a>
        <a data-p="mission">Mission Control</a>
        <a data-p="priority">Priority</a>
        <a data-p="activity">Activity Feed</a>
        <a data-p="dashboard">Dashboard</a>
        <a data-p="exec">Exec Summary</a>
        <a data-p="narrative">Narrative</a>
      </div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Captures</div><div class="navitems">
        <a data-p="capturesc">Captures</a>
      </div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Templates</div><div class="navitems">
        <a data-p="templatesc">Templates</a>
      </div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Review</div><div class="navitems">
        <a data-p="reviewc">Review</a>
      </div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Reports</div><div class="navitems">
        <a data-p="release">Release Center</a>
        <a data-p="run">Run Reports</a>
        <a data-p="reports">Reports</a>
      </div></div>
      <div class="navsec" data-tier="everyday"><div class="navhead">Settings</div><div class="navitems">
        <a data-p="settings">Settings</a>
      </div></div>
      <div class="navdrawer" data-tier="advanced"><div class="navhead drawerhead" data-drawer="adv">Advanced <span class="caret">▸</span></div><div class="drawerbody">
        <div class="navitems">
        <a data-p="insightsc">Insights</a>
        <a data-p="impactc">Impact</a>
        <a data-p="familiesc">Families</a>
        <a data-p="oracle">Oracle &amp; Eligibility</a>
        <a data-p="driftc">Drift</a>
        <a data-p="trustc">Trust</a>
        <a data-p="validationc">Validation</a>
        <a data-p="healthc">Health</a>
        <a data-p="rollbackc">Rollback</a>
        <a data-p="governancec">Governance</a>
      </div>
      </div></div>
      <div class="navdrawer" data-tier="system"><div class="navhead drawerhead" data-drawer="sys">System <span class="caret">▸</span></div><div class="drawerbody"><div class="navitems">
        <a data-p="investigate">Investigation</a>
        <a data-p="timeline">Evidence Timeline</a>
        <a data-p="graph">Knowledge Graph</a>
        <a data-p="trace">Decision Trace</a>
        <a data-p="assumptions">Assumptions</a>
        <a data-p="lessons">Lessons / Memory</a>
        <a data-p="risk">Risk Board</a>
        <a data-p="diff">Evidence Diff</a>
        <a data-p="notifcenter">Notification Center</a>
        <a data-p="siteplaybooks">Site Playbooks</a>
        <a data-p="downloadexplain">Download Decision Explorer</a>
        <a data-p="savedviews">Saved Views</a>
        <a data-p="notebook">Operator Notebook</a>
        <a data-p="campaigns">Campaigns</a>
        <a data-p="search">Smart Search</a>
        <a data-p="resources">Resources</a>
        <a data-p="import">Import Plan</a>
        <a data-p="warehouse">Artifact Warehouse</a>
        <a data-p="artifacts">Task Artifacts</a>
        <a data-p="console">Debug Log Console</a>
        <a data-p="shell">Shell (opt-in)</a>
        <a href="/framework/">Framework</a>
        <a href="/fleet/">Fleet</a>
        <a href="/">Main UI</a>
      </div></div></div>
      <div class="navsec moresec"><div class="navhead">More</div><div class="navitems" id="morebody"></div></div>
    </nav>
  </aside>
  <main class="main" id="main"></main>
  <div class="resizer" id="resizer" title="Drag to resize"></div>
  <button id="reexpand" title="Show sidebar">&rsaquo;</button>
</div>
<div id="cmdk"><div class="box"><input id="cmdk_in" placeholder="Jump to… (type a page name)" autocomplete="off"><div class="results" id="cmdk_res"></div></div></div>
<div id="toasts"></div>
<div id="help"><div class="box"><h3>Keyboard shortcuts</h3><table>
<tr><td><span class="kbd">&#8984;/Ctrl K</span></td><td>Command palette</td></tr>
<tr><td><span class="kbd">/</span></td><td>Smart search</td></tr>
<tr><td><span class="kbd">f</span></td><td>Collapse / show sidebar</td></tr>
<tr><td><span class="kbd">g</span> then key</td><td>Go: m mission · i inbox · d daily · s search · r risk · c corpus · t timeline · a activity</td></tr>
<tr><td><span class="kbd">?</span></td><td>This help</td></tr>
<tr><td><span class="kbd">Esc</span></td><td>Close overlay</td></tr>
</table></div></div>
<script>
const $=(s,e=document)=>e.querySelector(s); const $$=(s,e=document)=>[...e.querySelectorAll(s)];
function csrf(){const m=document.querySelector('meta[name="csrf-token"]');return m?m.content:''}
async function api(path,opts={}){
  const o=Object.assign({headers:{}},opts);
  if(o.body){
    o.headers['Content-Type']='application/json';
    // PHC-1 follow-up: the cockpit shell embeds no csrf-token meta tag, so
    // csrf() is empty here; self-mint from /api/csrf (mirrors apiRoot) so the
    // @531 /cockpit/api/ CSRF gate accepts the cockpit's own writes.
    let tok=csrf();
    if(!tok){try{const c=await fetch('/api/csrf');const j=await c.json();tok=(j&&j.csrf_token)||'';}catch(e){}}
    o.headers['X-CSRF-Token']=tok;
  }
  const r=await fetch('/cockpit'+path,o);
  if(!r.ok){let t=await r.text();throw new Error(t||r.status)} return r.json();
}
// apiRoot: call a MAIN-APP route (no /cockpit prefix), e.g. /api/template/sandbox.
// Seeds X-CSRF-Token from /api/csrf (self-minting) since the cockpit shell does
// not embed a csrf meta tag. Used by the Wave B1 test-static/live buttons.
async function apiRoot(path,opts={}){
  const o=Object.assign({headers:{}},opts);
  if(o.body){
    o.headers['Content-Type']='application/json';
    let tok=csrf();
    if(!tok){try{const c=await fetch('/api/csrf');const j=await c.json();tok=(j&&j.csrf_token)||'';}catch(e){}}
    o.headers['X-CSRF-Token']=tok;
  }
  const r=await fetch(path,o);
  if(!r.ok){let t=await r.text();throw new Error(t||r.status)} return r.json();
}
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// ── Slice 2: non-blocking toast (replaces native alert; textContent = XSS-safe) ──
function toast(msg,kind){
  let host=$('#toasts'); if(!host){host=document.createElement('div');host.id='toasts';document.body.appendChild(host);}
  const t=document.createElement('div'); t.className='toast'+(kind?(' '+kind):'');
  t.innerHTML='<span class="tx"></span><span class="tcl" title="Dismiss">&times;</span>';
  t.querySelector('.tx').textContent=String(msg==null?'':msg);
  const kill=()=>{t.style.opacity='0';setTimeout(()=>t.remove(),160);};
  t.querySelector('.tcl').onclick=kill;
  host.appendChild(t);
  setTimeout(kill, kind==='err'?6000:3500);
}
// ── Slice 5: presentation helpers (skeleton loader, empty state, list filter) ──
function skel(n){let s='<div class="skelbox">';for(let i=0;i<(n||5);i++)s+='<div class="skel" style="width:'+(60+Math.floor(Math.random()*35))+'%"></div>';return s+'</div>';}
function empty(msg){return '<div class="emptystate">'+esc(msg||'Nothing here yet.')+'</div>';}
function filterList(inp,sel){if(!inp)return;inp.addEventListener('input',()=>{const q=inp.value.toLowerCase();$$(sel).forEach(r=>{r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';});});}
let STATE={allow:null};

const PAGES={};
PAGES.home=async()=>{
  // Home (redesign): release strip + posture + recommended-next-step + KPI/debt
  // cards + Next actions. All values from existing safe endpoints; nothing invented.
  const [debt,tasks,health]=await Promise.all([api('/api/debt'),api('/api/tasks'),apiRoot('/api/health').catch(()=>({}))]);
  const running=tasks.tasks.filter(t=>t.status==='running').length;
  const failed=tasks.tasks.filter(t=>t.status==='failed').length;
  const corr=Number(debt.correction||0), cap=Number(debt.capability||0), val=Number(debt.validation||0);
  const ver=health.version?('v'+health.version):'—';
  const healthy=(health.ok!==false)&&(health.db_ok!==false);
  const stamp=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  // recommended next step (priority order; safe generic fallback)
  let rec;
  if(val>0)        rec={t:`<b>Validation debt: ${val} item${val>1?'s':''}</b> need${val>1?'':'s'} review.`,cta:'Open Review →',p:'reviewc'};
  else if(failed>0)rec={t:`<b>${failed} task${failed>1?'s':''}</b> failed.`,cta:'Open Activity Feed →',p:'activity'};
  else if(corr>0)  rec={t:`<b>Correction debt: ${corr}</b> open.`,cta:'Open Mission Control →',p:'mission'};
  else if(running>0)rec={t:`<b>${running} task${running>1?'s':''}</b> running.`,cta:'Open Activity Feed →',p:'activity'};
  else             rec={t:`All clear — nothing needs attention right now.`,cta:'Open Mission Control →',p:'mission'};
  const kpi=(label,value,hint,cls)=>`<div class="hkpi"><div class="kl">${label}</div><div class="kv ${cls||''}">${value}</div><div class="kh">${hint}</div></div>`;
  const card=(p,title,desc,cta)=>`<a data-p="${p}" class="hact"><div class="at">${title}</div><div class="ad">${desc}</div><div class="ac">${cta}</div></a>`;
  return `<div class="main-inner">
  <div class="hrel">
    <span class="dot ${healthy?'':'bad'}"></span><span>Live <b>${ver}</b></span><span class="sep">·</span>
    <span>Health <b>${healthy?'OK':'check'}</b></span>
    <span class="stamp">UI updated ${stamp}</span>
  </div>
  <h1>Home</h1><p class="sub">What matters right now — authorized local operations.</p>
  <div class="hposture" role="note"><span class="ic" aria-hidden="true">&#9888;</span><div class="tx"><b>Posture:</b> Authorized local operations only. Human-gated. No shell. No token reuse. Signing values redacted.</div></div>
  <a data-p="${rec.p}" class="hreco">
    <span class="ric">→</span>
    <div class="rb"><div class="rl">Recommended next step</div><div class="rt">${rec.t}</div></div>
    <span class="rc">${rec.cta}</span>
  </a>
  <div class="hkpis">
    ${kpi('Correction Debt',corr,corr?`${corr} pending`:'No corrections pending',corr?'err':'ok')}
    ${kpi('Capability Debt',cap,cap?`${cap} gap${cap>1?'s':''}`:'No capability gaps',cap?'err':'')}
    ${kpi('Validation Debt',val,val?`${val} item${val>1?'s':''} need${val>1?'':'s'} review`:'No items pending',val?'warn':'')}
    ${kpi('Corpus Entries',debt.entries??'—','corpus entries','')}
    ${kpi('Tasks Running',running,running?`${running} active`:'No active jobs','')}
    ${kpi('Tasks Failed',failed,failed?`${failed} failed`:'No failures',failed?'err':'')}
  </div>
  <h2 class="hsec">Next actions <span class="sh">— click a card to jump to it</span></h2>
  <div class="hacts">
    ${card('mission','Mission Control','Sites needing attention, recent drift, running tasks','Open →')}
    ${card('priority','Priority','Inbox, today, and alerts','Review →')}
    ${card('activity','Activity Feed','Recent operator + system activity','Open →')}
    ${card('scores','Recognition Scores','Recognition scores rollup — open for the full breakdown','View details →')}
    ${card('exec','Executive Summary','Executive digest of posture + progress','Open →')}
    ${card('narrative','Narrative','Plain-language status narrative','Open →')}
    ${card('run','Recent captures, audits &amp; verification runs','Latest capture + report runs','Open →')}
  </div>
  </div>`;
};
PAGES.advlanding=async()=>{
  const card=(p,title,desc,cta)=>`<a data-p="${p}" class="hact"><div class="at">${title}</div><div class="ad">${desc}</div><div class="ac">${cta}</div></a>`;
  return `<div class="main-inner">
  <h1>Advanced</h1><p class="sub">Choose an analysis surface.</p>
  <div class="hacts">
    ${card('validationc','Validation','Review pending validations and corrections','Open →')}
    ${card('insightsc','Insights','Recognition insights and rollups','Open →')}
    ${card('driftc','Drift','Site drift and change detection','Open →')}
    ${card('familiesc','Families','Recognizer families and coverage','Open →')}
    ${card('impactc','Impact','Change-impact analysis','Open →')}
    ${card('trustc','Trust','Trust posture and signals','Open →')}
  </div>
  </div>`;
};
PAGES.syslanding=async()=>{
  const card=(p,title,desc,cta)=>`<a data-p="${p}" class="hact"><div class="at">${title}</div><div class="ad">${desc}</div><div class="ac">${cta}</div></a>`;
  return `<div class="main-inner">
  <h1>System Overview</h1><p class="sub">Diagnostics, evidence timeline, knowledge graph, and internal checks.</p>
  <div class="hacts">
    ${card('investigate','Investigation','Diagnostics and investigation tools','Open →')}
    ${card('timeline','Evidence Timeline','Chronological evidence trail','Open →')}
    ${card('graph','Knowledge Graph','Entity and relationship graph','Open →')}
    ${card('lessons','Decision Trace','Decision history and lessons learned','Open →')}
    ${card('govhealth','Internal Checks','Internal governance and health checks','Open →')}
  </div>
  </div>`;
};
PAGES.capturesc=async()=>{
  const def=__pendingTab||'queue'; __pendingTab=null; setTimeout(()=>mountTab('cap',def),0);
  return `<h1>Captures</h1>`+tabbar('cap',[['queue','Queue'],['captures','Live session'],['autopilot','Autopilot'],['captureintel','Intelligence'],['sitereadiness','Readiness'],['novnc','noVNC']])+`<div id="tabhost"></div>`;
};
PAGES.templatesc=async()=>{
  const def=__pendingTab||'templateautopilot'; __pendingTab=null; setTimeout(()=>mountTab('tmpl',def),0);
  return `<h1>Templates</h1>`+tabbar('tmpl',[['templateautopilot','Autopilot'],['templatereview','Workbench'],['videotemplates','Video'],['stagingcandidates','Candidates'],['logintemplates','Login'],['missioncontrol','Operator MC']])+`<div id="tabhost"></div>`;
};
PAGES.reviewc=async()=>{
  const def=__pendingTab||'review'; __pendingTab=null; setTimeout(()=>mountTab('rev',def),0);
  return `<h1>Review</h1>`+tabbar('rev',[['review','Workbench'],['packet','Packet'],['reviewroi','ROI'],['escalations','Escalations'],['loginreview','Login review'],['queueintel','Queue intel'],['reviewops','Ops'],['reviewexp','Review']])+`<div id="tabhost"></div>`;
};
PAGES.insightsc=async()=>{
  const def=__pendingTab||'confidence'; __pendingTab=null; setTimeout(()=>mountTab('insightsc',def),0);
  return `<h1>Insights</h1>`+tabbar('insightsc',[['confidence','Confidence'],['portfolio','Portfolio'],['portfolioopp','Portfolio opp'],['blindspots','Blind spots'],['scarcity','Scarcity'],['captureyield','Yield'],['decisionquality','Decision quality'],['coverage','Coverage'],['opportunity','Capture opp'],['scores','Scores'],['forecasting','Forecasting']])+`<div id="tabhost"></div>`;
};
PAGES.impactc=async()=>{
  const def=__pendingTab||'impact'; __pendingTab=null; setTimeout(()=>mountTab('impactc',def),0);
  return `<h1>Impact</h1>`+tabbar('impactc',[['impact','Simulator'],['impactanalysis','Analysis']])+`<div id="tabhost"></div>`;
};
PAGES.familiesc=async()=>{
  const def=__pendingTab||'family'; __pendingTab=null; setTimeout(()=>mountTab('familiesc',def),0);
  return `<h1>Families</h1>`+tabbar('familiesc',[['family','Explorer'],['familyhealth','Health'],['similarity','Similarity'],['familyintel','Intelligence'],['corpus','Corpus'],['collections','Collections'],['sites','Sites']])+`<div id="tabhost"></div>`;
};
PAGES.driftc=async()=>{
  const def=__pendingTab||'drift'; __pendingTab=null; setTimeout(()=>mountTab('driftc',def),0);
  return `<h1>Drift</h1>`+tabbar('driftc',[['drift','Ops'],['crosssitedrift','Cross-site'],['driftintel','Intelligence'],['logindrift','Login drift']])+`<div id="tabhost"></div>`;
};
PAGES.trustc=async()=>{
  const def=__pendingTab||'trustdecay'; __pendingTab=null; setTimeout(()=>mountTab('trustc',def),0);
  return `<h1>Trust</h1>`+tabbar('trustc',[['trustdecay','Trust decay'],['eligibility','Eligibility gov']])+`<div id="tabhost"></div>`;
};
PAGES.validationc=async()=>{
  const def=__pendingTab||'validationops'; __pendingTab=null; setTimeout(()=>mountTab('validationc',def),0);
  return `<h1>Validation</h1>`+tabbar('validationc',[['validationops','Ops'],['debt','Corpus & debt']])+`<div id="tabhost"></div>`;
};
PAGES.healthc=async()=>{
  const def=__pendingTab||'unifiedhealth'; __pendingTab=null; setTimeout(()=>mountTab('healthc',def),0);
  return `<h1>Health</h1>`+tabbar('healthc',[['unifiedhealth','Templates'],['health','Checks'],['systemstatus','System status']])+`<div id="tabhost"></div>`;
};
PAGES.rollbackc=async()=>{
  const def=__pendingTab||'rollbackcenter'; __pendingTab=null; setTimeout(()=>mountTab('rollbackc',def),0);
  return `<h1>Rollback</h1>`+tabbar('rollbackc',[['rollbackcenter','Rollback'],['promotionactivity','Promotions']])+`<div id="tabhost"></div>`;
};
PAGES.governancec=async()=>{
  const def=__pendingTab||'autonomycenter'; __pendingTab=null; setTimeout(()=>mountTab('governancec',def),0);
  return `<h1>Governance</h1>`+tabbar('governancec',[['autonomycenter','Autonomy'],['govhealth','Gov health'],['autmetrics','Metrics'],['governance','Policy'],['guardrails','Guardrails'],['authority','Authority'],['compliance','Compliance'],['housekeeping','Housekeeping']])+`<div id="tabhost"></div>`;
};
PAGES.mission=async()=>{
  const m=await api('/api/mission');
  const d=m.debt||{};
  const att=(m.sites_needing_attention||[]).map(a=>`<tr class="clk" data-site="${esc(a.site)}" data-entry="${esc(a.id||'')}"><td>${esc(a.site)}</td><td>${esc(a.why)}</td><td>${esc(a.id)}</td></tr>`).join('');
  const drift=(m.recent_drift||[]).map(x=>`<tr class="clk" data-entry="${esc(x.id||'')}"><td>${esc(x.date)}</td><td>${esc(x.subject)}</td><td><span class="st ${x.outcome==='falsified'?'failed':(x.outcome==='confirmed'?'succeeded':'')}">${esc(x.outcome)}</span></td></tr>`).join('');
  const run=(m.running_tasks||[]).map(t=>`<li>${esc(t.label)} <span class="muted">(${esc(t.task_id)})</span></li>`).join('');
  setTimeout(()=>{
    // clickable stat cards -> jump to the page; hover lists the items behind the number
    hoverList($('#mc-review'), (m.review_queue?[{label:`${m.review_queue} candidate(s) awaiting review`, page:'review'}]:[]), 'Review queue');
    hoverList($('#mc-failed'), (m.failed_tasks||[]).map(t=>({label:t.label+' (rc='+(t.rc??'?')+')', page:'run'})), 'Failed tasks');
    hoverList($('#mc-correction'), (d.correction_items||[]).map(id=>({label:id, page:'corpus', deeplink:{entry:id}})), 'Correction debt');
    hoverList($('#mc-validation'), (d.validation_items||[]).map(id=>({label:id, page:'corpus', deeplink:{entry:id}})), 'Validation debt');
    hoverList($('#mc-corpus'), [{label:`Browse all ${d.entries} corpus entries`, page:'corpus'}], 'Corpus');
    hoverList($('#mc-captures'), (m.capture_names||[]).map(n=>({label:n, page:'warehouse'})), 'Captures present');
    hoverList($('#mc-active'), (m.running_tasks||[]).map(t=>({label:t.label, page:'run'})), 'Active captures');
    hoverList($('#mc-running'), (m.running_tasks||[]).map(t=>({label:t.label, page:'run'})), 'Running tasks');
    // deep-linking rows: a drift/attention row opens that entry in Corpus Explorer
    $$('#main tr.clk[data-entry]').forEach(r=>r.onclick=()=>{const id=r.dataset.entry; if(id) go('corpus',{entry:id}); else if(r.dataset.site) go('investigate',{site:r.dataset.site});});
  },0);
  return `<h1>Mission Control</h1><p class="sub">Single-screen overview. Hover a card to see what's behind the number; click an item to jump to it.</p>${banner()}
  <div class="cards">
    <div id="mc-active" class="card clk"><div class="k">Active captures</div><div class="v">${m.active_captures}</div></div>
    <div id="mc-running" class="card clk"><div class="k">Running tasks</div><div class="v">${(m.running_tasks||[]).length}</div></div>
    <div id="mc-failed" class="card clk ${(m.failed_tasks||[]).length?'err':''}"><div class="k">Failed tasks</div><div class="v">${(m.failed_tasks||[]).length}</div></div>
    <div id="mc-review" class="card clk warn"><div class="k">Review queue</div><div class="v">${m.review_queue}</div></div>
    <div id="mc-correction" class="card clk ${d.correction?'err':'ok'}"><div class="k">Correction debt</div><div class="v">${d.correction??'?'}</div></div>
    <div id="mc-validation" class="card clk warn"><div class="k">Validation debt</div><div class="v">${d.validation??'?'}</div></div>
    <div id="mc-captures" class="card clk"><div class="k">Captures present</div><div class="v">${m.captures_present}</div></div>
    <div id="mc-corpus" class="card clk"><div class="k">Corpus entries</div><div class="v">${d.entries??'?'}</div></div>
  </div>
  <div class="panel"><h2>Sites needing attention</h2>
    <table><thead><tr><th>Site</th><th>Why</th><th>Entry</th></tr></thead><tbody>${att||'<tr><td colspan=3 class="muted">None — no open correction debt.</td></tr>'}</tbody></table></div>
  <div class="panel"><h2>Recent drift detections <span class="muted" style="font-weight:400;font-size:12px">— click a row to open the entry</span></h2>
    <table><thead><tr><th>Date</th><th>Subject</th><th>Outcome</th></tr></thead><tbody>${drift||'<tr><td colspan=3 class="muted">No drift recorded.</td></tr>'}</tbody></table></div>
  ${run?`<div class="panel"><h2>Active sessions</h2><ul>${run}</ul></div>`:''}`;
};
PAGES.sites=async()=>{
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  setTimeout(()=>$$('#main [data-site]').forEach(a=>a.onclick=()=>openSite(a.dataset.site)),0);
  const chips=sites.map(s=>`<button class="btn sec" data-site="${esc(s)}">${esc(s)}</button>`).join(' ');
  return `<h1>Site Intelligence</h1><p class="sub">One page per site — corpus + any reports. Read-only.</p>
   <div class="panel"><div class="row">${chips||'<span class="muted">No sites in the corpus yet.</span>'}</div></div>
   <div id="site_out" class="panel"><span class="muted">Pick a site.</span></div>`;
};
async function openSite(s){const d=await api('/api/site/'+encodeURIComponent(s));const o=$('#site_out');
  const ce=(d.corpus_entries||[]).map(e=>`<tr><td>${esc(e.date)}</td><td>${esc(e.id)}</td><td>${esc(e.category)}</td><td>${esc(e.outcome)}</td><td>${esc(e.subject)}</td></tr>`).join('');
  const con=(d.open_concerns||[]).map(c=>`<li>${esc(c.id)}: ${esc(c.subject)} <span class="warn">(${esc(c.outcome)})</span></li>`).join('');
  o.innerHTML=`<h2>${esc(d.site)}</h2>
    <div class="cards">
      <div class="card"><div class="k">Corpus entries</div><div class="v">${d.n_corpus_entries}</div></div>
      <div class="card ${(d.open_concerns||[]).length?'warn':'ok'}"><div class="k">Open concerns</div><div class="v">${(d.open_concerns||[]).length}</div></div>
      <div class="card"><div class="k">Renditions known</div><div class="v">${(d.known_rendition_descriptors||[]).length}</div></div>
    </div>
    ${con?`<h3>Open concerns</h3><ul>${con}</ul>`:''}
    ${(d.known_rendition_descriptors||[]).length?`<h3>Known renditions</h3><p class="mono">${(d.known_rendition_descriptors||[]).map(esc).join(', ')}</p>`:''}
    ${(d.known_signing_markers||[]).length?`<h3>Signing markers (names only)</h3><p class="mono">${(d.known_signing_markers||[]).map(esc).join(', ')}</p>`:''}
    <h3>Corpus history</h3>
    <table><thead><tr><th>Date</th><th>ID</th><th>Category</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${ce||'<tr><td colspan=5 class="muted">No entries.</td></tr>'}</tbody></table>
    <p class="muted">${esc(d._note)}</p>`;
}
PAGES.corpus=async()=>{
  const ex=await api('/api/corpus');const f=ex.facets;
  const opt=(arr)=>['<option value="">any</option>'].concat(arr.map(x=>`<option>${esc(x)}</option>`)).join('');
  setTimeout(()=>{$('#cx_go').onclick=cxRun;cxRun();},0);
  return `<h1>Corpus Explorer</h1><p class="sub">Browse the corpus like a database. Read-only.</p>
   <div class="panel"><div class="row">
     <label class="f">Category<select id="cx_cat">${opt(f.categories)}</select></label>
     <label class="f">Outcome<select id="cx_out">${opt(f.outcomes)}</select></label>
     <label class="f">Site<select id="cx_site">${opt(f.sites)}</select></label>
     <label class="f">Debt only<select id="cx_debt"><option value="">any</option><option value="true">yes</option><option value="false">no</option></select></label>
     <label class="f">Text<input id="cx_q" placeholder="search…"></label>
     <button class="btn" id="cx_go">Filter</button></div></div>
   <div class="panel"><div id="cx_out_tbl">…</div></div>
   <div id="cx_detail" class="panel hidden"></div>`;
};
async function cxRun(){
  const qs=new URLSearchParams({category:$('#cx_cat').value,outcome:$('#cx_out').value,site:$('#cx_site').value,has_debt:$('#cx_debt').value,q:$('#cx_q').value});
  const r=await api('/api/corpus?'+qs.toString());
  let rows='';r.rows.forEach(x=>{rows+=`<tr style="cursor:pointer" data-id="${esc(x.id)}"><td>${esc(x.id)}</td><td>${esc(x.date)}</td><td>${esc(x.category)}</td>
    <td>${esc(x.outcome)}</td><td>${esc(x.site)}</td><td>${x.is_debt?'<span class="warn">debt</span>':''}</td><td>${esc(x.subject)}</td></tr>`;});
  $('#cx_out_tbl').innerHTML=`<p class="muted">${r.n} entries</p><table><thead><tr><th>ID</th><th>Date</th><th>Category</th><th>Outcome</th><th>Site</th><th></th><th>Subject</th></tr></thead><tbody>${rows}</tbody></table>`;
  $$('#cx_out_tbl [data-id]').forEach(tr=>tr.onclick=()=>cxDetail(tr.dataset.id));
}
async function cxDetail(id){const d=await api('/api/corpus/'+id);const e=d.entry;const box=$('#cx_detail');box.classList.remove('hidden');
  box.innerHTML=`<h2>${esc(e.id)} — ${esc(e.subject)}</h2>
   <p><b>Outcome:</b> ${esc(e.outcome)} · <b>Category:</b> ${esc(e.category)} · <b>Class:</b> ${esc(e.conclusion_class||'')} · <b>${esc(e.date)}</b> (v${esc(e.version)})</p>
   <p><b>Prediction:</b> ${esc(e.prediction||'')}</p>
   <p><b>Observation:</b> ${esc(e.observation||'')}</p>
   <p><b>Evidence:</b> ${esc(e.evidence||'')}</p>
   ${(e.resolves||[]).length?`<p><b>Resolves:</b> ${e.resolves.map(esc).join(', ')}</p>`:''}
   ${(d.resolved_by||[]).length?`<p><b>Resolved by:</b> ${d.resolved_by.map(esc).join(', ')}</p>`:''}
   ${e.notes?`<p class="muted"><b>Notes:</b> ${esc(e.notes)}</p>`:''}`;
}
PAGES.timeline=async()=>{
  const t=await api('/api/timeline');
  let rows='';t.events.forEach(e=>{const cls=e.outcome==='falsified'?'failed':(e.outcome==='confirmed'?'succeeded':'');
    rows+=`<tr class="clk" data-entry="${esc(e.id)}"><td>${esc(e.date)}</td><td>${esc(e.site)}</td><td>${esc(e.id)}</td><td>${esc(e.category)}</td>
    <td><span class="st ${cls}">${esc(e.outcome)}</span></td><td>${esc(e.subject)}</td>
    <td>${e.resolved_by?('→ '+esc(e.resolved_by)):(e.resolves.length?('resolves '+e.resolves.map(esc).join(',')):'')}</td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>{const id=r.dataset.entry; if(id)r.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Evidence Timeline</h1><p class="sub">Chronological history across the framework. Click a row to open the entry. Read-only.</p>
   <div class="panel"><table><thead><tr><th>Date</th><th>Site</th><th>ID</th><th>Category</th><th>Outcome</th><th>Subject</th><th>Link</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No events.</td></tr>'}</tbody></table></div>`;
};
PAGES.drift=async()=>{
  const d=await api('/api/drift');
  let rows='';d.drift.forEach(x=>{const sev=['','low','medium','high'][x.severity]||'';
    rows+=`<tr class="clk" data-entry="${esc(x.id||'')}"><td>${esc(x.date)}</td><td>${esc(x.site)}</td><td>${esc(x.subject)}</td>
    <td><span class="st ${x.severity>=3?'failed':(x.severity>=2?'':'succeeded')}">${esc(x.outcome)}</span></td><td>${sev}</td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>{const id=r.dataset.entry; if(id)r.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Drift Operations</h1><p class="sub">Drift recorded in the corpus, severity-ranked. Click a row to open the entry. Read-only.</p>${banner()}
   <div class="panel"><table><thead><tr><th>Date</th><th>Site</th><th>Subject</th><th>Outcome</th><th>Severity</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No drift recorded.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.risk=async()=>{
  const r=await api('/api/risk');
  const a=(r.assumptions||[]).map(x=>`<tr class="clk" data-entry="${esc(x.id)}"><td>${esc(x.id)}</td><td>${esc(x.site)}</td><td>${esc(x.outcome)}</td><td>${esc(x.subject)}</td></tr>`).join('');
  const w=(r.weakest_evidence||[]).map(x=>`<tr class="clk" data-entry="${esc(x.id)}"><td>${esc(x.id)}</td><td>${esc(x.failure_class||'')}</td><td>${esc(x.subject)}</td></tr>`).join('');
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(rw=>{const id=rw.dataset.entry; if(id)rw.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Risk Command Board</h1><p class="sub">Prioritization view over corpus + debt. Click a row to open the entry. Read-only.</p>${banner()}
   <div class="cards">
     <div class="card ${r.open_debt.correction?'err':'ok'}"><div class="k">Correction debt</div><div class="v">${r.open_debt.correction}</div></div>
     <div class="card warn"><div class="k">Validation debt</div><div class="v">${r.open_debt.validation}</div></div>
     <div class="card"><div class="k">Assumptions</div><div class="v">${(r.assumptions||[]).length}</div></div></div>
   <div class="panel"><h2>Weakest evidence (open debt)</h2>
     <table><thead><tr><th>ID</th><th>Failure class</th><th>Subject</th></tr></thead><tbody>${w||'<tr><td colspan=3 class="muted">None.</td></tr>'}</tbody></table></div>
   <div class="panel"><h2>Assumptions on record</h2>
     <table><thead><tr><th>ID</th><th>Site</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${a||'<tr><td colspan=4 class="muted">None.</td></tr>'}</tbody></table></div>`;
};
PAGES.search=async()=>{
  setTimeout(()=>{$('#sq_go').onclick=sqRun;$('#sq').onkeydown=e=>{if(e.key==='Enter')sqRun()};},0);
  return `<h1>Smart Search</h1><p class="sub">Across corpus, reports, captures. Read-only.</p>
   <div class="panel"><div class="row"><input id="sq" placeholder="search everything…" style="min-width:320px">
   <button class="btn" id="sq_go">Search</button></div><div id="sq_out" style="margin-top:12px"></div></div>`;
};
async function sqRun(){const q=$('#sq').value;const r=await api('/api/search?q='+encodeURIComponent(q));
  let rows='';(r.results||[]).forEach(x=>{rows+=`<tr><td><span class="tag">${esc(x.kind)}</span></td><td>${esc(x.title)}</td><td class="mono" style="padding:4px 8px">${esc(x.path||x.id||'')}</td></tr>`;});
  $('#sq_out').innerHTML=`<p class="muted">${r.n||0} result(s)</p><table><tbody>${rows||'<tr><td class="muted">No matches.</td></tr>'}</tbody></table>`;}
PAGES.warehouse=async()=>{
  const w=await api('/api/warehouse');let html='';
  for(const [cat,files] of Object.entries(w.categories||{})){
    let rows='';files.forEach(f=>{rows+=`<tr><td>${esc(f.name)}</td><td class="mono" style="padding:4px 8px">${esc(f.root)}/${esc(f.path)}</td>
      <td>${(f.size/1024).toFixed(1)} KB</td><td><a data-open="${esc(f.path)}" data-root="${esc(f.root==='captures'?'':f.root==='tasks'?'tasks':'reports')}">view</a></td></tr>`;});
    html+=`<div class="panel"><h2>${esc(cat)} <span class="muted">(${files.length})</span></h2>
      <table><thead><tr><th>Name</th><th>Path</th><th>Size</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  setTimeout(()=>$$('#main [data-open]').forEach(a=>a.onclick=()=>{if(!a.dataset.root){toast('Captures are binary .wacz — open via analysis, not the text viewer.','warn');return;}openWarehouseFile(a.dataset.open,a.dataset.root);}),0);
  return `<h1>Artifact Warehouse</h1><p class="sub">Central storage browser. Preview opens the redacted viewer.</p>
   ${html||'<div class="panel muted">No artifacts yet.</div>'}<div class="panel"><div id="rv" class="muted">Select a file to preview.</div></div>`;
};
async function openWarehouseFile(name,root){
  const d=await api('/api/report?name='+encodeURIComponent(name)+'&root='+root);const rv=$('#rv');
  if(d.kind==='json'){rv.className='';rv.innerHTML=`<div class="mono">${esc(JSON.stringify(d.data,null,2))}</div>`}
  else if(d.html){rv.className='viewer';rv.innerHTML=d.html}
  else{rv.className='';rv.innerHTML=`<div class="mono">${esc(d.text)}</div>`}
}
PAGES.dashboard=async()=>{
  const [debt,tasks,allow]=await Promise.all([api('/api/debt'),api('/api/tasks'),api('/api/allowlist')]);
  STATE.allow=allow;
  const running=tasks.tasks.filter(t=>t.status==='running').length;
  const failed=tasks.tasks.filter(t=>t.status==='failed').length;
  return `<h1>Dashboard</h1><p class="sub">What matters right now — authorized local operations.</p>
  ${banner()}
  <div class="cards">
    <div class="card ${debt.correction?'err':'ok'}"><div class="k">Correction debt</div><div class="v">${debt.correction??'?'}</div></div>
    <div class="card"><div class="k">Capability debt</div><div class="v">${debt.capability??'?'}</div></div>
    <div class="card warn"><div class="k">Validation debt</div><div class="v">${debt.validation??'?'}</div></div>
    <div class="card"><div class="k">Corpus entries</div><div class="v">${debt.entries??'?'}</div></div>
    <div class="card ${running?'':''}"><div class="k">Tasks running</div><div class="v">${running}</div></div>
    <div class="card ${failed?'err':''}"><div class="k">Tasks failed</div><div class="v">${failed}</div></div>
  </div>
  <div class="panel"><h2>Recent tasks</h2>${taskTable(tasks.tasks.slice(0,6))}</div>
  <div class="panel"><h2>Validation debt items</h2>
    <p class="muted">${(debt.validation_items||[]).join(', ')||'none'} — each requires a real capture + human review to retire.</p></div>`;
};
PAGES.reports=async()=>{
  const r=await api('/api/reports');
  const fams={};r.reports.forEach(x=>{(fams[x.family]=fams[x.family]||[]).push(x)});
  let list='';for(const f of Object.keys(fams).sort()){
    list+=`<div style="margin:6px 0 2px;color:var(--ink-3);font-size:11px;text-transform:uppercase">${esc(f)}</div>`;
    fams[f].forEach(x=>list+=`<a data-r="${esc(x.name)}" data-root="${x.root}">${esc(x.name)} <span class="tag">${x.kind}</span></a>`);
  }
  setTimeout(()=>$$('#main .flist a').forEach(a=>a.onclick=()=>openReport(a.dataset.r,a.dataset.root,a)),0);
  return `<h1>Reports</h1><p class="sub">Every generated report — read-only, redacted.</p>
   <div class="split"><div class="panel flist">${list||'<p class="muted">No reports yet. Run one from “Run Reports”.</p>'}</div>
   <div class="panel"><div id="rv" class="muted">Select a report to view.</div></div></div>`;
};
async function openReport(name,root,el){
  $$('#main .flist a').forEach(a=>a.classList.remove('on'));el&&el.classList.add('on');
  const d=await api('/api/report?name='+encodeURIComponent(name)+'&root='+root);
  const rv=$('#rv');
  if(d.kind==='json'){rv.innerHTML=`<div class="mono">${esc(JSON.stringify(d.data,null,2))}</div>`}
  else if(d.html){rv.className='viewer';rv.innerHTML=d.html}
  else{rv.innerHTML=`<div class="mono">${esc(d.text)}</div>`}
}
PAGES.run=async()=>{
  const allow=STATE.allow||await api('/api/allowlist');STATE.allow=allow;
  let html=`<h1>Run Reports</h1><p class="sub">Allowlisted report generators only. No free-form commands.</p>${banner()}`;
  for(const [k,v] of Object.entries(allow.report_runners)){
    html+=`<div class="tool"><div class="nm">${esc(v.label)} ${v.human_review?'<span class="tag rev">human review</span>':''}</div>
      <div class="why">${esc(v.why)}</div>
      <div class="row"><button class="btn" data-run="${k}">Run</button>
      <span class="muted">→ output under ${esc(allow.roots.tasks)}</span></div></div>`;
  }
  html+=`<div class="panel"><h2>Task history</h2><div id="th">loading…</div></div>`;
  setTimeout(()=>{$$('#main [data-run]').forEach(b=>b.onclick=()=>runReport(b.dataset.run,b));refreshTasks()},0);
  return html;
};
async function runReport(name,btn){btn.disabled=true;btn.textContent='Starting…';
  try{await api('/api/run-report',{method:'POST',body:JSON.stringify({name,params:{}})});await refreshTasks()}
  catch(e){toast(e.message,'err')} btn.disabled=false;btn.textContent='Run';}
PAGES.captures=async()=>{
  const allow=STATE.allow||await api('/api/allowlist');STATE.allow=allow;
  const axes=allow.axes.map(a=>`<option>${a}</option>`).join('');
  return `<h1>Captures</h1><p class="sub">Authorized local capture tasks only — validated arguments, no shell.</p>${banner()}
  <div class="tool"><div class="nm">${esc(allow.capture_tools.capture_session.label)} <span class="tag rev">human review</span></div>
    <div class="why">${esc(allow.capture_tools.capture_session.why)}</div>
    <div class="row">
      <label class="f">Start URL<input id="cs_url" placeholder="https://site/members/...item"></label>
      <label class="f">Label<input id="cs_label" placeholder="site_clip_4k"></label>
      <label class="f">Autofill<select id="cs_af"><option value="true">on</option><option value="false">off</option></select></label>
      <label class="f">Profile dir<input id="cs_profile" placeholder="(blank = fresh) e.g. profiles/reptyle"></label>
      <label class="f">Body cap MiB<input id="cs_bodycap" type="number" min="1" max="64" placeholder="(blank = 1)"></label>
      <label class="f">Chunk events<input id="cs_chunks" type="number" min="1000" max="1000000" placeholder="(blank = 10000)"></label>
      <label class="f">Max seconds<input id="cs_maxsec" type="number" min="1" max="1700" placeholder="(blank = 1500)"></label>
      <label class="f">HUD overlay<input id="cs_hud" type="checkbox" checked> <span class="muted">(on by default)</span></label>
      <label class="f">Relaxed redaction<input id="cs_reduced" type="checkbox"> <span class="muted">(LOCAL ONLY &mdash; keeps signed URLs in the capture so it writes even when capture-time scrubbing misses a signing shape; the WACZ is stamped local_only and must NEVER be shared)</span></label>
      <button class="btn" id="cs_go">Start capture</button>
    </div>
    <p class="muted" style="margin-top:8px">Will run: <b>capture_session.py</b> with your URL/label, output a <code>.wacz</code> under
      ${esc(allow.roots.captures)}. You log in and play in the noVNC session; the tool records and redacts. No token stored.
      When you're done, click <b>finish</b> on the task row below to save (or <b>discard</b> to drop it).</p>
  </div>
  <div class="tool"><div class="nm">${esc(allow.capture_tools.offline_capture_analyze.label)}</div>
    <div class="why">${esc(allow.capture_tools.offline_capture_analyze.why)}</div>
    <div class="row">
      <label class="f">Baseline (under root)<input id="oa_b" placeholder="clip_4k.wacz"></label>
      <label class="f">Perturbed<input id="oa_p" placeholder="clip_720p.wacz"></label>
      <label class="f">Axis<select id="oa_axis">${axes}</select></label>
      <button class="btn" id="oa_go">Analyze</button></div>
  </div>
  <div class="tool"><div class="nm">Build template from two captures</div>
    <div class="why">Synthesize a recognition template from two recon captures of the same action — recognition-only (no replay, no network, no writes). Optionally freeze the draft to a template dict.</div>
    <div class="row">
      <label class="f">Capture A (under root)<input id="bt_a" placeholder="clip_a.wacz"></label>
      <label class="f">Capture B<input id="bt_b" placeholder="clip_b.wacz"></label>
      <label class="f">Freeze<select id="bt_freeze"><option value="false">draft only</option><option value="true">freeze template</option></select></label>
      <button class="btn" id="bt_go">Build</button></div>
    <div id="bt_out" class="muted" style="margin-top:8px">Draft will appear here.</div>
  </div>
  <div class="panel"><h2>Task history</h2><div id="th">loading…</div></div>`;
};
PAGES.autopilot=async()=>{
  const allow=STATE.allow||await api('/api/allowlist');STATE.allow=allow;
  const axes=['<option value="">no perturbation</option>'].concat(allow.axes.map(a=>`<option>${a}</option>`)).join('');
  return `<h1>Capture Autopilot</h1><p class="sub">One click: a capture folder → analyze → temporal/perturbation → cockpit.</p>${banner()}
  <div class="tool"><div class="nm">Run autopilot on the captures folder</div>
    <div class="why">${esc(allow.capture_tools.autopilot.why)}</div>
    <div class="row">
      <label class="f">Folder (under captures root)<input id="ap_folder" placeholder="(blank = whole captures root)"></label>
      <label class="f">Axis<select id="ap_axis">${axes}</select></label>
      <button class="btn" id="ap_go">Run autopilot</button></div>
    <p class="muted" style="margin-top:8px">Discovers <code>.wacz</code> under ${esc(allow.roots.captures)}, runs the temporal floor +
      axes, optional perturbation, writes a cockpit. <b>Never writes the corpus, never acts.</b></p>
  </div>
  <div class="panel"><h2>Result</h2><div id="ap_out" class="muted">Run autopilot to see the cockpit here.</div></div>
  <div class="panel"><h2>Task history</h2><div id="th">loading…</div></div>`;
};
PAGES.novnc=async()=>{
  const n=await api('/api/novnc');
  let body;
  if(!n.configured){body=`<p class="warn">No noVNC URL configured.</p>
    <p class="muted">Set <code>BD_NOVNC_URL</code> in the server environment (e.g. your local
    <code>http://10.0.70.20:6080/vnc.html</code>) and bring up the remote-teach stack on the host.
    The URL is server-config only — it cannot be entered here.</p>`;}
  else{body=`<div class="row" style="margin-bottom:10px">
      <span class="tag">configured</span>
      <a class="btn sec" href="${esc(n.url)}" target="_blank" rel="noopener">Open in new tab ↗</a>
      <button class="btn sec" id="vnc_embed">Embed below</button></div>
    <div id="vnc_holder"><p class="muted">Click “Embed below” to load the local session in-page.</p></div>`;}
  setTimeout(()=>{const b=$('#vnc_embed');if(b)b.onclick=()=>{$('#vnc_holder').innerHTML=`<iframe class="vnc" src="${esc(n.url)}" allow="clipboard-read; clipboard-write"></iframe>`}},0);
  return `<h1>noVNC</h1><p class="sub">${esc(n.note)}</p>${banner()}<div class="panel">${body}</div>`;
};
PAGES.artifacts=async()=>{
  const t=await api('/api/tasks');
  let rows='';t.tasks.forEach(x=>{(x.output_files||[]).forEach(f=>{
    const cls=f.posture==='clean'?'ok':(f.posture==='unknown'?'warn':'err');
    rows+=`<tr><td>${esc(x.label)}</td><td class="mono" style="padding:4px 8px">${esc(f.path)}</td>
      <td><span class="${cls}">${esc(f.posture)}</span></td><td>${esc(x.task_id)}</td></tr>`;
  })});
  return `<h1>Artifacts</h1><p class="sub">Files produced by tasks — posture-scanned. Withheld = a leak was detected and the file is not shown.</p>
  <div class="panel"><table><thead><tr><th>Task</th><th>File</th><th>Posture</th><th>Task ID</th></tr></thead>
  <tbody>${rows||'<tr><td colspan=4 class="muted">No artifacts yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.debt=async()=>{
  const d=await api('/api/debt');
  return `<h1>Corpus / Debt</h1><p class="sub">Read-only. The corpus is never written from the cockpit.</p>${banner()}
  <div class="cards">
    <div class="card ${d.correction?'err':'ok'}"><div class="k">Correction</div><div class="v">${d.correction??'?'}</div></div>
    <div class="card"><div class="k">Capability</div><div class="v">${d.capability??'?'}</div></div>
    <div class="card warn"><div class="k">Validation</div><div class="v">${d.validation??'?'}</div></div>
    <div class="card"><div class="k">Entries</div><div class="v">${d.entries??'?'}</div></div></div>
  <div class="panel"><h2>Open validation debt</h2><p class="muted">${(d.validation_items||[]).join(', ')||'none'}</p>
   <p class="muted">Retiring any of these needs a real capture, a review, and a deliberate corpus change — done outside the cockpit.</p></div>`;
};
PAGES.import=async()=>{
  return `<h1>Import Plan</h1><p class="sub">A spreadsheet/CSV is structured DATA only — never executable. Preview, validate, confirm.</p>${banner()}
  <div class="panel"><h2>Paste CSV (columns: site,url,label,workflow,notes,priority)</h2>
    <textarea id="csv" class="mono" style="width:100%;height:130px" placeholder="site,url,label,workflow,notes,priority
ultrafilms,https://ultrafilms.com/members/...,two_candies_4k,play 4k,,high"></textarea>
    <div class="row" style="margin-top:10px"><button class="btn" id="prev">Preview & validate</button></div>
    <div id="prev_out" style="margin-top:12px"></div></div>`;
};
PAGES.settings=async()=>{
  const a=await api('/api/allowlist');const n=await api('/api/novnc');
  return `<h1>Settings</h1><p class="sub">Read-only configuration (set via server env). No secrets shown.</p>
  <div class="panel"><h2>Approved roots</h2>
    <table><tr><th>Reports</th><td class="mono">${esc(a.roots.reports)}</td></tr>
    <tr><th>Captures</th><td class="mono">${esc(a.roots.captures)}</td></tr>
    <tr><th>Tasks</th><td class="mono">${esc(a.roots.tasks)}</td></tr>
    <tr><th>noVNC</th><td class="mono">${esc(n.url||'(not set — BD_NOVNC_URL)')}</td></tr></table></div>
  <div class="panel"><h2>Allowlisted tools</h2>
    <p class="muted">Report runners: ${Object.keys(a.report_runners).map(esc).join(', ')}</p>
    <p class="muted">Capture tools: ${Object.keys(a.capture_tools).map(esc).join(', ')}</p>
    <p class="muted">These are the ONLY things the cockpit can run. There is no shell, no arbitrary command, no remote control.</p></div>
  <div class="panel"><h2>Appearance</h2>
    <p class="muted">Layout and theme are saved on this device and apply across the whole cockpit.</p>
    <p><label class="muted">Layout&nbsp;</label><select id="s_layout_sel" style="background:var(--pill);border:1px solid var(--hairline);color:var(--ink);border-radius:6px;padding:4px 8px"></select>
    &nbsp;&nbsp;<label class="muted">Theme&nbsp;</label><select id="s_theme_sel" style="background:var(--pill);border:1px solid var(--hairline);color:var(--ink);border-radius:6px;padding:4px 8px"></select></p></div>`;
};

PAGES.campaigns=async()=>{
  const d=await api('/api/campaigns');
  const goals=d.goals.map(g=>`<option>${esc(g)}</option>`).join('');
  let rows='';d.campaigns.forEach(c=>{rows+=`<tr><td>${esc(c.name)}</td><td>${esc(c.goal)}</td><td>${esc(c.site||'—')}</td>
    <td>${c.evidence_count}</td><td>${c.looks_complete?'<span class="ok">ready</span>':'<span class="warn">collecting</span>'}</td>
    <td>${esc(c.recommended_next)}</td></tr>`;});
  setTimeout(()=>{$('#cc_go').onclick=async()=>{
    try{await api('/api/campaigns',{method:'POST',body:JSON.stringify({name:$('#cc_name').value,goal:$('#cc_goal').value,site:$('#cc_site').value||null,notes:$('#cc_notes').value})});go('campaigns');}
    catch(e){toast(e.message,'err')}};},0);
  return `<h1>Capture Campaigns</h1><p class="sub">Group captures toward a validation goal. The campaign tracks progress; it doesn't run anything.</p>${banner()}
   <div class="panel"><h2>New campaign</h2><div class="row">
     <label class="f">Name<input id="cc_name" placeholder="ultrafilms_n3_validation"></label>
     <label class="f">Goal<select id="cc_goal">${goals}</select></label>
     <label class="f">Site<input id="cc_site" placeholder="ultrafilms"></label>
     <label class="f">Notes<input id="cc_notes" placeholder="optional"></label>
     <button class="btn" id="cc_go">Create</button></div></div>
   <div class="panel"><h2>Campaigns</h2><table><thead><tr><th>Name</th><th>Goal</th><th>Site</th><th>Evidence</th><th>Status</th><th>Recommended next</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No campaigns yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.queue=async()=>{
  const q=await api('/api/queue');
  const order=['pending','running','requires_review','completed','failed'];
  let cols='';order.forEach(s=>{const items=(q.by_state[s]||[]);
    let lis='';items.forEach(it=>{lis+=`<div class="tool" draggable="true" data-qid="${esc(it.id)}" style="cursor:grab">
      <div class="nm">${esc(it.label)} <span class="tag">${esc(it.priority)}</span></div>
      <div class="why">${esc(it.site)}${it.axis?(' · '+esc(it.axis)):''}</div>
      <div class="row">${s==='pending'?`<button class="btn" data-launch="${esc(it.id)}">Launch</button>`:''}
       ${s!=='completed'?`<button class="btn sec" data-mark="${esc(it.id)}" data-to="completed">✓ done</button>`:''}
       <button class="btn sec" data-mark="${esc(it.id)}" data-to="requires_review">flag review</button></div></div>`;});
    cols+=`<div style="flex:1;min-width:200px"><h3 style="text-transform:capitalize">${s.replace('_',' ')} (${items.length})</h3>${lis||'<p class="muted">—</p>'}</div>`;});
  setTimeout(()=>{
    $$('#main [data-launch]').forEach(b=>b.onclick=async()=>{b.disabled=true;
      try{await api('/api/queue/launch',{method:'POST',body:JSON.stringify({id:b.dataset.launch})});go('queue');}
      catch(e){toast(e.message,'err');b.disabled=false;}});
    $$('#main [data-mark]').forEach(b=>b.onclick=async()=>{
      try{await api('/api/queue/state',{method:'POST',body:JSON.stringify({id:b.dataset.mark,state:b.dataset.to})});go('queue');}
      catch(e){toast(e.message,'err')}});
    $('#q_go').onclick=async()=>{
      try{await api('/api/queue',{method:'POST',body:JSON.stringify({site:$('#q_site').value,label:$('#q_label').value,url:$('#q_url').value,axis:$('#q_axis').value||null,priority:$('#q_prio').value})});go('queue');}
      catch(e){toast(e.message,'err')}};},0);
  return `<h1>Capture Queue</h1><p class="sub">Plan captures here; launching routes through the validated capture path one at a time. The queue runs nothing itself.</p>${banner()}
   <div class="panel"><h2>Add to queue</h2><div class="row">
     <label class="f">Site<input id="q_site" placeholder="ultrafilms"></label>
     <label class="f">Label<input id="q_label" placeholder="two_candies_720p"></label>
     <label class="f">URL<input id="q_url" placeholder="https://...item"></label>
     <label class="f">Axis<select id="q_axis"><option value="">—</option><option>player_config</option><option>workflow</option></select></label>
     <label class="f">Priority<select id="q_prio"><option>medium</option><option>high</option><option>low</option></select></label>
     <button class="btn" id="q_go">Add</button></div></div>
   <div class="panel"><h2>Queue</h2><div class="row" style="align-items:flex-start">${cols}</div>
   <p class="muted">${esc(q._note)}</p></div>`;
};
PAGES.review=async()=>{
  const r=await api('/api/review');
  let cand='';r.corpus_candidates.forEach((c,i)=>{const key=c.root+'/'+c.path;
    cand+=`<tr><td>${esc(c.subject||c.path)}</td><td>${esc(c.outcome||'')}</td><td>${(c.resolves||[]).join(',')||'—'}</td>
    <td><button class="btn sec" data-dec="accept" data-key="${esc(key)}">accept</button>
        <button class="btn sec" data-dec="reject" data-key="${esc(key)}">reject</button>
        <button class="btn sec" data-dec="defer" data-key="${esc(key)}">defer</button></td></tr>`;});
  const dec=Object.entries(r.decisions_recorded||{}).map(([k,v])=>`<tr><td class="mono" style="padding:4px 8px">${esc(k)}</td><td>${esc(v.decision)}</td><td>${esc(v.note||'')}</td></tr>`).join('');
  setTimeout(()=>$$('#main [data-dec]').forEach(b=>b.onclick=async()=>{
    const note=prompt('Note for this '+b.dataset.dec+' (optional):')||'';
    try{await api('/api/review/decide',{method:'POST',body:JSON.stringify({item:b.dataset.key,decision:b.dataset.dec,note})});go('review');}
    catch(e){toast(e.message,'err')}}),0);
  return `<h1>Review Workbench</h1><p class="sub">Central review. Recording a decision NEVER applies it — writing the corpus, promoting selectors, updating profiles stay separate human steps.</p>${banner()}
   <div class="panel"><h2>Corpus candidates awaiting review (${r.corpus_candidates.length})</h2>
     <table><thead><tr><th>Subject</th><th>Outcome</th><th>Resolves</th><th>Decision</th></tr></thead><tbody>${cand||'<tr><td colspan=4 class="muted">None pending.</td></tr>'}</tbody></table></div>
   <div class="panel"><h2>Decisions recorded</h2><table><thead><tr><th>Item</th><th>Decision</th><th>Note</th></tr></thead><tbody>${dec||'<tr><td colspan=3 class="muted">None.</td></tr>'}</tbody></table>
   <p class="muted">${esc(r._note)}</p></div>`;
};
PAGES.notebook=async()=>{
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  const opt=sites.map(s=>`<option>${esc(s)}</option>`).join('');
  setTimeout(()=>{$('#nb_load').onclick=nbLoad;$('#nb_go').onclick=async()=>{
    try{await api('/api/notes',{method:'POST',body:JSON.stringify({site:$('#nb_site').value,kind:$('#nb_kind').value,text:$('#nb_text').value})});nbLoad();$('#nb_text').value='';}
    catch(e){toast(e.message,'err')}};if(sites.length)nbLoad();},0);
  return `<h1>Operator Notebook</h1><p class="sub">Per-site notes — observations, hypotheses, follow-ups, capture plans. Separate from the corpus; never feeds it.</p>
   <div class="panel"><div class="row">
     <label class="f">Site<input id="nb_site" list="nb_sites" placeholder="ultrafilms"><datalist id="nb_sites">${opt}</datalist></label>
     <label class="f">Kind<select id="nb_kind"><option>observation</option><option>hypothesis</option><option>followup</option><option>capture_plan</option></select></label>
     <label class="f" style="flex:1">Note<input id="nb_text" placeholder="what you observed…" style="min-width:280px"></label>
     <button class="btn" id="nb_go">Add</button><button class="btn sec" id="nb_load">Load site</button></div></div>
   <div id="nb_out" class="panel muted">Enter a site and load its notes.</div>`;
};
async function nbLoad(){const s=$('#nb_site').value;if(!s)return;
  try{const d=await api('/api/notes/'+encodeURIComponent(s));
  let rows='';d.notes.forEach(n=>{rows+=`<tr><td>${esc(n.kind)}</td><td>${esc(n.text)}</td><td class="muted">${new Date(n.created*1000).toLocaleString()}</td></tr>`;});
  $('#nb_out').className='panel';$('#nb_out').innerHTML=`<h2>${esc(s)}</h2><table><thead><tr><th>Kind</th><th>Note</th><th>When</th></tr></thead><tbody>${rows||'<tr><td colspan=3 class="muted">No notes yet.</td></tr>'}</tbody></table><p class="muted">${esc(d._note)}</p>`;}
  catch(e){$('#nb_out').innerHTML='<span class="err">'+esc(e.message)+'</span>'}}
PAGES.packet=async()=>{
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  const chips=sites.map(s=>`<button class="btn sec" data-pk="${esc(s)}">${esc(s)}</button>`).join(' ');
  setTimeout(()=>$$('#main [data-pk]').forEach(b=>b.onclick=()=>pkBuild(b.dataset.pk)),0);
  return `<h1>One-Click Review Packet</h1><p class="sub">Package a site's evidence for review — timeline, corpus, candidates, recommended actions. Read-only; nothing is written.</p>
   <div class="panel"><div class="row">${chips||'<span class="muted">No sites yet.</span>'}</div></div>
   <div id="pk_out" class="panel muted">Pick a site to build its packet.</div>`;
};
async function pkBuild(s){const d=await api('/api/packet/'+encodeURIComponent(s));const o=$('#pk_out');o.className='panel';
  const tl=d.timeline.map(e=>`<tr><td>${esc(e.date)}</td><td>${esc(e.id)}</td><td>${esc(e.outcome)}</td><td>${esc(e.subject)}</td></tr>`).join('');
  const ac=d.recommended_actions.map(a=>`<li>${esc(a)}</li>`).join('');
  o.innerHTML=`<h2>Review packet — ${esc(d.site)}</h2>
    <div class="cards">
      <div class="card"><div class="k">Corpus entries</div><div class="v">${d.summary.corpus_entries}</div></div>
      <div class="card ${d.summary.open_concerns?'warn':'ok'}"><div class="k">Open concerns</div><div class="v">${d.summary.open_concerns}</div></div>
      <div class="card"><div class="k">Timeline events</div><div class="v">${d.summary.timeline_events}</div></div>
      <div class="card"><div class="k">Candidates</div><div class="v">${d.summary.candidates_pending}</div></div></div>
    ${ac?`<h3>Recommended actions</h3><ul>${ac}</ul>`:''}
    <h3>Timeline</h3><table><thead><tr><th>Date</th><th>ID</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${tl||'<tr><td colspan=4 class="muted">No events.</td></tr>'}</tbody></table>
    <p class="muted">${esc(d._note)}</p>`;}
PAGES.release=async()=>{
  const r=await api('/api/readiness');
  const bl=(r.blockers||[]).map(b=>`<li class="err">${esc(b)}</li>`).join('');
  const ad=(r.advisories||[]).map(a=>`<li class="warn">${esc(a)}</li>`).join('');
  const lk=(r.posture_scan.leak_files||[]).map(esc).join(', ');
  return `<h1>Release Center</h1><p class="sub">Read-only readiness preview. The authoritative gate is build_release.py on the host — this neither builds nor bumps.</p>${banner()}
   <div class="panel" style="text-align:center">
     <div style="font-size:34px;font-weight:800;color:var(--${r.ready?'ok':'err'})">${esc(r.verdict)}</div></div>
   <div class="cards">
     <div class="card ${r.debt.correction?'err':'ok'}"><div class="k">Correction debt</div><div class="v">${r.debt.correction??'?'}</div></div>
     <div class="card warn"><div class="k">Validation debt</div><div class="v">${r.debt.validation??'?'}</div></div>
     <div class="card"><div class="k">Artifacts scanned</div><div class="v">${r.posture_scan.artifacts_scanned}</div></div>
     <div class="card ${r.posture_scan.with_leaks?'err':'ok'}"><div class="k">Posture leaks</div><div class="v">${r.posture_scan.with_leaks}</div></div></div>
   ${bl?`<div class="panel"><h2>Blockers</h2><ul>${bl}</ul></div>`:'<div class="panel ok">No blockers.</div>'}
   ${ad?`<div class="panel"><h2>Advisories</h2><ul>${ad}</ul></div>`:''}
   ${lk?`<div class="panel"><h2>Artifacts with leaks (withheld from display)</h2><p class="mono">${esc(lk)}</p></div>`:''}
   <div class="panel muted">${esc(r._note)}</div>`;
};
PAGES.exec=async()=>{
  setTimeout(()=>{$('#ex_go').onclick=exRun;exRun();},0);
  return `<h1>Executive Summary</h1><p class="sub">Generated from existing artifacts. Read-only.</p>
   <div class="panel"><div class="row"><label class="f">Period
     <select id="ex_period"><option value="all">all</option><option value="daily">daily</option><option value="weekly">weekly</option><option value="release">release</option><option value="validation">validation</option></select></label>
     <button class="btn" id="ex_go">Generate</button></div></div>
   <div id="ex_out" class="panel muted">…</div>`;
};
async function exRun(){const p=$('#ex_period').value;const s=await api('/api/exec?period='+p);const o=$('#ex_out');o.className='panel';
  const drift=(s.recent_drift||[]).map(d=>`<li class="clk" data-entry="${esc(d.id)}" style="cursor:pointer">${esc(d.id)}: ${esc(d.subject)} (${esc(d.outcome)})</li>`).join('');
  const rel=(s.recent_releases||[]).map(r=>`<li><b>${esc(r.version)}</b> — ${esc(r.summary)}</li>`).join('');
  const oc=Object.entries(s.recent_by_outcome||{}).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ');
  o.innerHTML=`<h2>Summary — ${esc(s.period)} <span class="muted">(as of ${esc(s.as_of)})</span></h2>
    <p style="font-size:15px">${esc(s.headline)}</p>
    <div class="cards">
      <div class="card"><div class="k">Corpus total</div><div class="v">${s.corpus_total}</div></div>
      <div class="card"><div class="k">Recent entries</div><div class="v">${s.recent_entries}</div></div>
      <div class="card ${s.debt.correction?'err':'ok'}"><div class="k">Correction</div><div class="v">${s.debt.correction??'?'}</div></div>
      <div class="card warn"><div class="k">Validation</div><div class="v">${s.debt.validation??'?'}</div></div></div>
    ${oc?`<p class="muted">Recent outcomes — ${oc}</p>`:''}
    ${drift?`<h3>Recent drift</h3><ul>${drift}</ul>`:''}
    ${rel?`<h3>Recent releases</h3><ul>${rel}</ul>`:''}`;
  $$('#ex_out li.clk[data-entry]').forEach(li=>{const id=li.dataset.entry; if(id)li.onclick=()=>go('corpus',{entry:id});});}
PAGES.coverage=async()=>{
  const c=await api('/api/coverage');
  const colorFor=(o,n)=>{if(!n)return 'var(--surface-2)';return o==='confirmed'?'#103a1e':o==='falsified'?'#3a1213':o==='partial'?'#3a2f12':'#15263a';};
  let head='<tr><th>Category</th>'+c.outcomes.map(o=>`<th>${esc(o)}</th>`).join('')+'</tr>';
  let rows='';c.categories.forEach(cat=>{rows+='<tr><td>'+esc(cat)+'</td>'+c.outcomes.map(o=>{const n=c.grid[cat][o];
    return `<td class="${n?'covcell':''}" data-cat="${esc(cat)}" data-out="${esc(o)}" style="text-align:center;background:${colorFor(o,n)};font-weight:${n?'700':'400'};cursor:${n?'pointer':'default'}">${n||''}</td>`;}).join('')+'</tr>';});
  const s=c.support;
  setTimeout(()=>$$('#main td.covcell').forEach(td=>td.onclick=()=>go('corpus',{filter:{cat:td.dataset.cat,out:td.dataset.out}})),0);
  return `<h1>Evidence Coverage Heatmap</h1><p class="sub">Where evidence is strong vs thin. Click a cell to open those entries in Corpus Explorer. Read-only.</p>
   <div class="cards">
     <div class="card ok"><div class="k">Well-supported</div><div class="v">${s.well_supported}</div></div>
     <div class="card warn"><div class="k">Weakly-supported</div><div class="v">${s.weakly_supported}</div></div>
     <div class="card"><div class="k">Untested</div><div class="v">${s.untested}</div></div>
     <div class="card ${s.open_debt?'err':'ok'}"><div class="k">Open debt</div><div class="v">${s.open_debt}</div></div></div>
   <div class="panel"><h2>Category × outcome</h2><table>${head}${rows}</table>
   <p class="muted">${esc(c._note)}</p></div>`;
};
PAGES.graph=async()=>{
  const g=await api('/api/graph');
  // simple column layout by type, edges as SVG lines; clickable nodes
  const types=g.types;const cols={};types.forEach((t,i)=>cols[t]=i);
  const W=900,colW=W/types.length,rowH=46;
  const pos={};const counts={};
  g.nodes.forEach(n=>{const c=cols[n.type]??0;counts[c]=(counts[c]||0);
    pos[n.id]={x:c*colW+colW/2,y:40+counts[c]*rowH};counts[c]++;});
  const H=Math.max(...Object.values(counts),1)*rowH+80;
  const tcol={assumption:'#4c8dff',finding:'#8b98a9',validation:'#3fb950',drift:'#d29922',debt:'#f85149'};
  let lines='';g.edges.forEach(e=>{const a=pos[e.from],b=pos[e.to];if(a&&b)
    lines+=`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#2a3543" stroke-width="1.5" marker-end="url(#arr)"/>`;});
  let dots='';g.nodes.forEach(n=>{const p=pos[n.id];
    dots+=`<g class="gnode" data-id="${esc(n.id)}" style="cursor:pointer"><circle cx="${p.x}" cy="${p.y}" r="7" fill="${tcol[n.type]||'#888'}"/>
     <text x="${p.x+11}" y="${p.y+4}" fill="var(--ink)" font-size="11">${esc(n.id)}</text></g>`;});
  let heads='';types.forEach((t,i)=>heads+=`<text x="${i*colW+colW/2}" y="20" fill="${tcol[t]}" font-size="12" font-weight="700" text-anchor="middle">${esc(t)}</text>`);
  setTimeout(()=>$$('#main .gnode').forEach(g=>g.onclick=()=>gNode(g.dataset.id)),0);
  return `<h1>Knowledge Graph</h1><p class="sub">The corpus's resolves-relationship graph (${g.n_nodes} nodes, ${g.n_edges} edges). Click a node. Read-only.</p>
   <div class="panel" style="overflow:auto"><svg width="${W}" height="${H}" style="min-width:${W}px">
     <defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="#2a3543"/></marker></defs>
     ${heads}${lines}${dots}</svg></div>
   <div id="g_detail" class="panel muted">Click a node to inspect its corpus entry.</div>`;
};
async function gNode(id){const d=await api('/api/corpus/'+id);const e=d.entry;const b=$('#g_detail');b.className='panel';
  b.innerHTML=`<h2>${esc(e.id)} — ${esc(e.subject)}</h2><p><b>${esc(e.outcome)}</b> · ${esc(e.category)} · ${esc(e.date)}</p>
   <p>${esc(e.observation||e.prediction||'')}</p>
   ${(e.resolves||[]).length?`<p><b>Resolves:</b> ${e.resolves.map(esc).join(', ')}</p>`:''}
   ${(d.resolved_by||[]).length?`<p><b>Resolved by:</b> ${d.resolved_by.map(esc).join(', ')}</p>`:''}`;}
PAGES.diff=async()=>{
  const ware=await api('/api/warehouse');const caps=(ware.categories.Captures||[]).map(f=>f.path);
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  const capOpt=caps.map(c=>`<option>${esc(c)}</option>`).join('');
  const siteOpt=sites.map(s=>`<option>${esc(s)}</option>`).join('');
  setTimeout(()=>{$('#df_kind').onchange=dfToggle;$('#df_go').onclick=dfRun;dfToggle();},0);
  return `<h1>Evidence Diff</h1><p class="sub">Compare captures or sites. Posture: signing values are never compared or shown — only marker names.</p>${banner()}
   <div class="panel"><div class="row">
     <label class="f">Kind<select id="df_kind"><option value="capture">capture vs capture</option><option value="site">site vs site</option></select></label>
     <label class="f">A<select id="df_a"></select></label>
     <label class="f">B<select id="df_b"></select></label>
     <button class="btn" id="df_go">Diff</button></div>
     <datalist></datalist>
     <div id="df_caps" class="hidden">${capOpt}</div><div id="df_sites" class="hidden">${siteOpt}</div></div>
   <div id="df_out" class="panel muted">Pick A and B.</div>`;
};
function dfToggle(){const k=$('#df_kind').value;const src=k==='capture'?$('#df_caps'):$('#df_sites');
  $('#df_a').innerHTML=src.innerHTML;$('#df_b').innerHTML=src.innerHTML;}
async function dfRun(){const k=$('#df_kind').value;const a=$('#df_a').value,b=$('#df_b').value;
  try{const d=await api('/api/diff?kind='+k+'&a='+encodeURIComponent(a)+'&b='+encodeURIComponent(b));const o=$('#df_out');o.className='panel';
  if(k==='capture'){const r=d.renditions;
    o.innerHTML=`<h2>${esc(d.a)} vs ${esc(d.b)}</h2>
     <h3>Renditions</h3><p><b>Only A:</b> ${r.only_a.map(esc).join(', ')||'—'}</p><p><b>Only B:</b> ${r.only_b.map(esc).join(', ')||'—'}</p><p><b>Shared:</b> ${r.shared.map(esc).join(', ')||'—'}</p>
     <h3>Signing markers (names only)</h3><p>A: ${d.signing_markers.a.map(esc).join(', ')||'—'} · B: ${d.signing_markers.b.map(esc).join(', ')||'—'}</p>
     <p class="muted">${esc(d.signing_markers.note)}</p>
     <h3>Login</h3><p>A: ${d.login.a?'yes':'no'} · B: ${d.login.b?'yes':'no'}</p><p class="muted">${esc(d._note)}</p>`;}
  else{const c=d.corpus_entries;
    o.innerHTML=`<h2>${esc(d.a)} vs ${esc(d.b)}</h2><p>${esc(d.a)}: ${c.count_a} entries · ${esc(d.b)}: ${c.count_b} entries</p>
     <p><b>Only ${esc(d.a)}:</b> ${c.only_a.map(esc).join(', ')||'—'}</p><p><b>Only ${esc(d.b)}:</b> ${c.only_b.map(esc).join(', ')||'—'}</p>
     <p><b>Shared:</b> ${c.shared.map(esc).join(', ')||'—'}</p><p class="muted">${esc(d._note)}</p>`;}}
  catch(e){$('#df_out').innerHTML='<span class="err">'+esc(e.message)+'</span>'}}
PAGES.resources=async()=>{
  const r=await api('/api/resources');
  return `<h1>Resource Utilization</h1><p class="sub">Read-only snapshot of stash. No process is controlled. Source: ${esc(r.source)}.</p>
   <div class="cards">
     <div class="card"><div class="k">CPU</div><div class="v">${r.cpu_percent==null?'n/a':r.cpu_percent+'%'}</div></div>
     <div class="card"><div class="k">Memory</div><div class="v">${r.mem_percent==null?'n/a':r.mem_percent+'%'}</div><div class="muted">${r.mem_used_gb||'?'} / ${r.mem_total_gb||'?'} GB</div></div>
     <div class="card"><div class="k">Disk</div><div class="v">${r.disk_percent==null?'n/a':r.disk_percent+'%'}</div><div class="muted">${r.disk_free_gb||'?'} GB free</div></div>
     <div class="card ${r.active_captures?'':''}"><div class="k">Active captures</div><div class="v">${r.active_captures}</div></div>
     <div class="card"><div class="k">Queue depth</div><div class="v">${r.queue_depth}</div></div>
     <div class="card"><div class="k">Captures completed</div><div class="v">${r.captures_completed}</div></div></div>
   <div class="panel muted">${esc(r._note)} "Active captures" and "queue depth" come from the cockpit's own task state, not a system process scan.</div>`;
};
PAGES.health=async()=>{
  const h=await api('/api/health-checks');
  const ch=h.checks.map(c=>`<li>${esc(c.label)} <span class="tag">${esc(c.id)}</span></li>`).join('');
  setTimeout(()=>{$('#hc_go').onclick=async()=>{$('#hc_go').disabled=true;$('#hc_go').textContent='Running…';
    try{const r=await api('/api/health-checks/run',{method:'POST',body:JSON.stringify({})});
      $('#hc_out').className='panel';$('#hc_out').innerHTML=`<p class="ok">Snapshot written at ${esc(r.snapshot.at)}.</p><div class="mono">${esc(JSON.stringify(r.snapshot,null,2))}</div>`;}
    catch(e){toast(e.message,'err')}$('#hc_go').disabled=false;$('#hc_go').textContent='Run checks now';};},0);
  return `<h1>Scheduled Health Checks</h1><p class="sub">Read-only refreshes — recompute dashboards + write a snapshot. No browser, no capture, no mutation.</p>${banner()}
   <div class="panel"><h2>Available checks</h2><ul>${ch}</ul>
     <div class="row"><button class="btn" id="hc_go">Run checks now</button></div>
     <p class="muted">${esc(h._note)} To schedule, call this from host cron — there is no always-on scheduler inside the app by design.</p></div>
   <div id="hc_out" class="panel muted">Run the checks to write a snapshot.</div>`;
};

PAGES.systemstatus=async()=>{
  const d=await api('/api/status');
  const yn=b=>b?'<span class="ok">✓ yes</span>':'<span class="failed">✗ no</span>';
  const bb=d.browser_backend||{}, cb=bb.cloakbrowser||{}, ca=d.capture_assets||{};
  const hf=d.manual_login_handoff||{}, ka=d.keepalive||{}, co=d.corpus||{};
  const backendCard=`<div class="panel"><h2>Browser backend</h2>
    <p>Selected backend: <b>${esc(bb.selected||bb.error||'?')}</b></p>
    <p>CloakBrowser available: ${yn(cb.available)} ${cb.version?('<span class="tag">'+esc(cb.version)+'</span>'):''}</p>
    ${cb.import_error?('<p class="muted">import error: '+esc(cb.import_error)+'</p>'):''}
    <p class="muted">Set via Settings <span class="tag">browser_backend</span> / env <span class="tag">BD_BROWSER_BACKEND</span>. <b>playwright</b> is the exact pre-141 fallback.</p></div>`;
  const assetsCard=`<div class="panel"><h2>Local capture assets — offline DOM capture</h2>
    <p>Local vendored assets: ${yn(ca.local)}</p>
    <p>rrweb: ${yn(ca.rrweb_present)} <span class="tag">${esc(ca.rrweb_bytes||0)} bytes</span> · snapdom: ${yn(ca.snapdom_present)} <span class="tag">${esc(ca.snapdom_bytes||0)} bytes</span></p>
    <p class="muted">No remote CDN by design — a missing bundle fails capture rather than fetching.</p></div>`;
  let hfRows='';
  (hf.sites||[]).forEach(s=>{
    const rts=(s.runtime_profiles||[]).map(r=>`${esc(r.profile)} ${r.has_session?'✓':'·'}${r.backup_count?(' <span class="tag">'+esc(r.backup_count)+' bak</span>'):''}`).join(', ');
    hfRows+=`<tr><td>${esc(s.site)}</td><td>${s.manual_present?'✓':'✗'}</td><td>${s.handed_off?'<span class="ok">✓</span>':'<span class="failed">✗</span>'}</td><td>${rts||'<span class="muted">none</span>'}</td></tr>`;
  });
  const handoffCard=`<div class="panel"><h2>Manual-login session handoff</h2>
    <p>Profiles root: <span class="tag">${esc(hf.profiles_root||'?')}</span> · present: ${yn(hf.present)}</p>
    ${hf.present?`<table><thead><tr><th>Site</th><th>Manual</th><th>Handed off</th><th>Runtime profiles (✓ = has session)</th></tr></thead><tbody>${hfRows||'<tr><td colspan="4" class="muted">no sites</td></tr>'}</tbody></table>`:'<p class="muted">No profiles directory yet — no manual logins have run in this environment.</p>'}</div>`;
  const keepers=ka.keepers||[];
  const kaRows=keepers.map(k=>`<tr><td>${esc(k.site_id||'?')}</td><td>${esc(k.account_idx)}</td><td>${esc(k.status||k.state||'?')}</td><td>${esc(k.detail||k.last_error||'')}</td></tr>`).join('');
  const keepaliveCard=`<div class="panel"><h2>Keepalive</h2>
    <p>Default backend keepalive would use: <b>${esc(ka.default_backend||ka.error||'?')}</b></p>
    ${keepers.length?`<table><thead><tr><th>Site</th><th>Acct</th><th>State</th><th>Detail</th></tr></thead><tbody>${kaRows}</tbody></table>`:'<p class="muted">No keepers currently running in this process.</p>'}</div>`;
  const corpusCard=`<div class="panel"><h2>Capture / template corpus</h2>
    <p>Captures root: <span class="tag">${esc(co.captures_root||'?')}</span> · present: ${yn(co.present)}</p>
    <p>Capture files: <b>${esc(co.capture_count!=null?co.capture_count:'?')}</b></p>
    ${co.error?('<p class="muted">'+esc(co.error)+'</p>'):''}</div>`;
  return `<h1>System Status</h1><p class="sub">What is actually active right now — read-only, no browser. Use this when GUI behaviour is unclear.</p>${banner()}
    ${backendCard}${assetsCard}${handoffCard}${keepaliveCard}${corpusCard}`;
};

PAGES.inbox=async()=>{
  const d=await api('/api/inbox');
  const sevcls=s=>s==='high'?'failed':s==='medium'?'':'succeeded';
  // map an inbox item to its destination (page + deeplink)
  const dest=i=>{
    if(i.kind==='review') return {page:'review'};
    if(i.kind==='validation_debt'||i.kind==='correction_debt') return {page:'corpus', deeplink:i.id?{entry:i.id}:{filter:{debt:'true'}}};
    if(i.kind==='failed_task') return {page:'run'};
    if(i.kind==='posture') return {page:'release'};
    if(i.kind==='campaign') return {page:'campaigns'};
    return null;
  };
  let rows='';d.items.forEach((i,n)=>{const t=dest(i); rows+=`<tr class="${t?'clk':''}" data-n="${n}"><td><span class="st ${sevcls(i.severity)}">${esc(i.severity)}</span></td>
    <td>${esc(i.kind)}</td><td>${esc(i.title)}</td><td class="muted">${esc(i.action)}</td></tr>`;});
  setTimeout(()=>{
    $$('#main tr.clk[data-n]').forEach(r=>{const i=d.items[+r.dataset.n];const t=dest(i); if(t)r.onclick=()=>go(t.page,t.deeplink);});
    // severity cards hover-list the items at that level, each clickable
    const bySev=s=>d.items.filter(i=>i.severity===s).map(i=>{const t=dest(i)||{}; return {label:i.title, page:t.page, deeplink:t.deeplink};});
    hoverList($('#ib-high'), bySev('high'), 'High priority');
    hoverList($('#ib-medium'), bySev('medium'), 'Medium priority');
    hoverList($('#ib-low'), bySev('low'), 'Low priority');
  },0);
  return `<h1>Priority Inbox</h1><p class="sub">What needs attention, ranked. Hover a card to list its items; click any row to jump to it. Advisory — nothing acts automatically.</p>${banner()}
   <div class="cards">
     <div id="ib-high" class="card clk ${d.counts.high?'err':'ok'}"><div class="k">High</div><div class="v">${d.counts.high}</div></div>
     <div id="ib-medium" class="card clk warn"><div class="k">Medium</div><div class="v">${d.counts.medium}</div></div>
     <div id="ib-low" class="card clk"><div class="k">Low</div><div class="v">${d.counts.low}</div></div></div>
   <div class="panel"><table><thead><tr><th>Severity</th><th>Kind</th><th>Item</th><th>Suggested action</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=4 class="ok">Inbox zero — nothing needs attention.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.daily=async()=>{
  const d=await api('/api/daily-mission');
  const f=d.focus.map((i,n)=>`<li><b>${n+1}.</b> ${esc(i.title)} <span class="muted">— ${esc(i.action)}</span></li>`).join('');
  return `<h1>Daily Mission</h1><p class="sub">Today's focus, from the prioritization engine.</p>
   <div class="panel" style="text-align:center"><div style="font-size:18px;font-weight:700">${esc(d.mission)}</div></div>
   <div class="panel"><h2>Focus (top 3)</h2><ul>${f||'<li class="ok">All clear.</li>'}</ul>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.activity=async()=>{
  const d=await api('/api/activity');
  const icon=k=>k.includes('fail')?'✗':k.includes('succeed')?'✓':k.includes('review')?'⚑':k.includes('queue')?'▶':k.includes('health')?'♻':'•';
  let rows='';d.events.forEach(e=>{const when=e.at?new Date(e.at*1000).toLocaleString():'';
    rows+=`<tr><td>${icon(e.kind)}</td><td>${esc(e.text)}</td><td class="muted">${esc(when)}</td></tr>`;});
  return `<h1>Activity Feed</h1><p class="sub">Operational event stream — tasks, reviews, launches, snapshots. Read-only.</p>
   <div class="panel"><table><tbody>${rows||'<tr><td class="muted">No activity yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.investigate=async()=>{
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  const chips=sites.map(s=>`<button class="btn sec" data-inv="${esc(s)}">${esc(s)}</button>`).join(' ');
  setTimeout(()=>$$('#main [data-inv]').forEach(b=>b.onclick=()=>invOpen(b.dataset.inv)),0);
  return `<h1>Investigation Workspace</h1><p class="sub">Investigate a site without leaving the cockpit — intelligence, timeline, notes, captures in one place.</p>
   <div class="panel"><div class="row">${chips||'<span class="muted">No sites yet.</span>'}</div></div>
   <div id="inv_out" class="panel muted">Pick a site.</div>`;
};
async function invOpen(s){const d=await api('/api/investigate/'+encodeURIComponent(s));const o=$('#inv_out');o.className='panel';
  const p=d.panels;
  const con=(p.intelligence.open_concerns||[]).map(c=>`<li>${esc(c.id)}: ${esc(c.subject)} (${esc(c.outcome)})</li>`).join('');
  const tl=(p.timeline||[]).slice(0,10).map(e=>`<tr><td>${esc(e.date)}</td><td>${esc(e.id)}</td><td>${esc(e.outcome)}</td><td>${esc(e.subject)}</td></tr>`).join('');
  const nt=(p.notes||[]).map(n=>`<li>${esc(n.kind)}: ${esc(n.text)}</li>`).join('');
  o.innerHTML=`<h2>${esc(d.site)}</h2>
   <div class="cards">
     <div class="card"><div class="k">Corpus entries</div><div class="v">${p.intelligence.corpus_entries}</div></div>
     <div class="card ${(p.intelligence.open_concerns||[]).length?'warn':'ok'}"><div class="k">Open concerns</div><div class="v">${(p.intelligence.open_concerns||[]).length}</div></div>
     <div class="card"><div class="k">Captures</div><div class="v">${(p.captures||[]).length}</div></div>
     <div class="card"><div class="k">Notes</div><div class="v">${(p.notes||[]).length}</div></div></div>
   ${con?`<h3>Open concerns</h3><ul>${con}</ul>`:''}
   <h3>Timeline (recent)</h3><table><thead><tr><th>Date</th><th>ID</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${tl||'<tr><td colspan=4 class="muted">—</td></tr>'}</tbody></table>
   ${(p.captures||[]).length?`<h3>Captures</h3><p class="mono">${p.captures.map(esc).join(', ')}</p>`:''}
   ${nt?`<h3>Notes</h3><ul>${nt}</ul>`:''}
   <p class="muted">${esc(d._note)}</p>`;
}
PAGES.assumptions=async()=>{
  const d=await api('/api/assumptions');
  let rows='';d.assumptions.forEach(a=>{const cls=a.status==='validated'?'succeeded':a.status==='open_debt'?'failed':'';
    rows+=`<tr class="clk" data-entry="${esc(a.id)}"><td>${esc(a.id)}</td><td>${esc(a.site)}</td><td><span class="st ${cls}">${esc(a.status)}</span></td><td>${esc(a.subject)}</td></tr>`;});
  const bs=Object.entries(d.by_status).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ');
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>{const id=r.dataset.entry; if(id)r.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Assumption Center</h1><p class="sub">Assumptions on record with validation status. Click a row to open the entry. Read-only.</p>
   <div class="panel"><p class="muted">${d.n} assumptions — ${bs}</p>
   <table><thead><tr><th>ID</th><th>Site</th><th>Status</th><th>Subject</th></tr></thead><tbody>${rows}</tbody></table></div>`;
};
PAGES.trace=async()=>{
  const ex=await api('/api/corpus?has_debt=any');
  const ids=ex.rows.filter(r=>r.resolves.length||r.is_debt).map(r=>r.id);
  const allids=ex.rows.map(r=>r.id);
  const opt=allids.map(i=>`<option>${esc(i)}</option>`).join('');
  setTimeout(()=>{$('#tr_go').onclick=trGo;},0);
  return `<h1>Decision Trace / Audit</h1><p class="sub">Trace any conclusion back through its resolves-chain to the evidence. Read-only provenance.</p>${banner()}
   <div class="panel"><div class="row"><label class="f">Entry<select id="tr_id">${opt}</select></label>
   <button class="btn" id="tr_go">Trace</button></div></div>
   <div id="tr_out" class="panel muted">Pick an entry to trace its provenance.</div>`;
};
async function trGo(){const id=$('#tr_id').value;const d=await api('/api/trace/'+encodeURIComponent(id));const o=$('#tr_out');o.className='panel';
  const chain=d.chain.map(n=>`<div style="margin-left:${n.depth*24}px;border-left:2px solid var(--hairline);padding:6px 10px;margin-bottom:6px">
    <b>${esc(n.id)}</b> <span class="muted">(${esc(n.category||'')} · ${esc(n.outcome||'')})</span><br>${esc(n.subject||'')}
    ${n.evidence?`<div class="muted" style="font-size:12px">evidence: ${esc(n.evidence)}</div>`:''}</div>`).join('');
  o.innerHTML=`<h2>Provenance of ${esc(d.root)}</h2>
   ${d.resolved_by.length?`<p><b>Resolved by:</b> ${d.resolved_by.map(esc).join(', ')}</p>`:''}
   <h3>Chain (depth ${d.depth})</h3>${chain}
   <p class="muted">${esc(d._note)}</p>`;}
PAGES.confidence=async()=>{
  const d=await api('/api/confidence');
  const caps=d.confidence_caps.map(c=>`<li class="clk" data-entry="${esc(c.id)}" style="cursor:pointer">${esc(c.id)}: ${esc(c.subject)}</li>`).join('');
  const flags=d.sensitivity_flags.map(f=>`<li class="clk" data-entry="${esc(f.id)}" style="cursor:pointer">${esc(f.id)}: ${esc(f.subject)}</li>`).join('');
  const om=Object.entries(d.outcome_mix).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ');
  setTimeout(()=>$$('#main li.clk[data-entry]').forEach(li=>{const id=li.dataset.entry; if(id)li.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Confidence Decomposition</h1><p class="sub">What bounds confidence: explicit caps, sensitivity flags, outcome mix. Click an item to open the entry. Read-only.</p>
   <div class="cards"><div class="card"><div class="k">Confirmed fraction</div><div class="v">${(d.confirmed_fraction*100).toFixed(0)}%</div></div>
     <div class="card"><div class="k">Confidence caps</div><div class="v">${d.confidence_caps.length}</div></div>
     <div class="card"><div class="k">Sensitivity flags</div><div class="v">${d.sensitivity_flags.length}</div></div></div>
   <div class="panel"><h2>Outcome mix</h2><p class="muted">${om}</p>
   ${caps?`<h3>Confidence caps</h3><ul>${caps}</ul>`:''}
   ${flags?`<h3>Sensitivity flags</h3><ul>${flags}</ul>`:''}</div>`;
};
PAGES.collections=async()=>{
  const d=await api('/api/collections');const ex=await api('/api/corpus');
  const entryOpt=ex.rows.map(r=>`<option>${esc(r.id)}</option>`).join('');
  let rows='';d.collections.forEach(c=>{rows+=`<tr><td>${esc(c.name)}</td><td>${c.entry_ids.length}</td><td class="mono">${c.entry_ids.map(esc).join(', ')||'—'}</td>
    <td><select data-coladd="${esc(c.id)}"><option value="">+ add entry…</option>${entryOpt}</select></td></tr>`;});
  setTimeout(()=>{$('#col_go').onclick=async()=>{
    try{await api('/api/collections',{method:'POST',body:JSON.stringify({name:$('#col_name').value})});go('collections');}
    catch(e){toast(e.message,'err')}};
    $$('#main [data-coladd]').forEach(sel=>sel.onchange=async()=>{if(!sel.value)return;
      try{await api('/api/collections/add',{method:'POST',body:JSON.stringify({id:sel.dataset.coladd,entry_id:sel.value})});go('collections');}
      catch(e){toast(e.message,'err')}});},0);
  return `<h1>Evidence Collections</h1><p class="sub">Group corpus entries into named collections. Inert data; nothing executes.</p>
   <div class="panel"><div class="row"><label class="f">New collection<input id="col_name" placeholder="n3_evidence"></label>
   <button class="btn" id="col_go">Create</button></div></div>
   <div class="panel"><table><thead><tr><th>Name</th><th>#</th><th>Entries</th><th>Add</th></tr></thead><tbody>${rows||'<tr><td colspan=4 class="muted">No collections yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.lessons=async()=>{
  const l=await api('/api/lessons');const om=await api('/api/org-memory');
  const cl=l.corpus_lessons.map(x=>`<tr class="clk" data-entry="${esc(x.id)}"><td>${esc(x.id)}</td><td>${esc(x.conclusion_class)}</td><td>${esc(x.outcome)}</td><td>${esc(x.subject)}</td></tr>`).join('');
  const omx=Object.entries(om.outcome_mix).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ');
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>{const id=r.dataset.entry; if(id)r.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Lessons & Organizational Memory</h1><p class="sub">Transferable lessons + aggregate institutional memory. Click a lesson to open the entry. Read-only.</p>
   <div class="cards">
     <div class="card"><div class="k">Corpus entries</div><div class="v">${om.corpus_entries}</div></div>
     <div class="card"><div class="k">Corpus lessons</div><div class="v">${l.n_corpus_lessons}</div></div>
     <div class="card"><div class="k">Sites w/ notes</div><div class="v">${(om.sites_with_notes||[]).length}</div></div>
     <div class="card"><div class="k">Collections</div><div class="v">${om.collections}</div></div></div>
   <div class="panel"><h2>Outcome mix</h2><p class="muted">${omx}</p></div>
   <div class="panel"><h2>Corpus-derived lessons</h2>
     <table><thead><tr><th>ID</th><th>Class</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${cl||'<tr><td colspan=4 class="muted">—</td></tr>'}</tbody></table></div>
   ${l.doc_present?`<div class="panel"><h2>LESSONS_LEARNED.md (excerpt)</h2><div class="mono">${esc(l.doc_excerpt)}</div></div>`
     :`<div class="panel muted">LESSONS_LEARNED.md is KB-only (excluded from the release zip); showing corpus-derived lessons above.</div>`}`;
};
PAGES.reviewroi=async()=>{
  const r=await api('/api/review-roi');
  return `<h1>Review ROI</h1><p class="sub">How review effort relates to debt retired. Approximate planning signal. Read-only.</p>
   <div class="cards">
     <div class="card ok"><div class="k">Debt retired</div><div class="v">${r.debt_retired_total}</div></div>
     <div class="card"><div class="k">Resolutions</div><div class="v">${r.resolution_entries}</div></div>
     <div class="card warn"><div class="k">Open debt</div><div class="v">${r.open_debt}</div></div>
     <div class="card"><div class="k">Retire ratio</div><div class="v">${(r.retire_ratio*100).toFixed(0)}%</div></div></div>
   <div class="panel"><h2>Review decisions recorded</h2>
     <p class="muted">accept: ${r.decisions.accept} · reject: ${r.decisions.reject} · defer: ${r.decisions.defer} · total: ${r.decisions.total}</p>
     <p class="muted">${esc(r._note)}</p></div>`;
};
PAGES.savedviews=async()=>{
  const d=await api('/api/saved-views');
  let rows='';d.views.forEach(v=>{rows+=`<tr><td>${esc(v.name)}</td><td>${esc(v.kind)}</td>
    <td class="mono">${esc(JSON.stringify(v.params))}</td><td><button class="btn sec" data-del="${esc(v.id)}">delete</button></td></tr>`;});
  setTimeout(()=>{$('#sv_go').onclick=async()=>{
    let params={};try{params=JSON.parse($('#sv_params').value||'{}')}catch(e){toast('params must be JSON','err');return;}
    try{await api('/api/saved-views',{method:'POST',body:JSON.stringify({name:$('#sv_name').value,kind:$('#sv_kind').value,params})});go('savedviews');}
    catch(e){toast(e.message,'err')}};
    $$('#main [data-del]').forEach(b=>b.onclick=async()=>{await api('/api/saved-views/delete',{method:'POST',body:JSON.stringify({id:b.dataset.del})});go('savedviews');});},0);
  return `<h1>Saved Views</h1><p class="sub">Save filter/search states for reuse. Inert stored queries.</p>
   <div class="panel"><div class="row">
     <label class="f">Name<input id="sv_name" placeholder="confirmed_assumptions"></label>
     <label class="f">Kind<select id="sv_kind"><option>corpus</option><option>search</option><option>timeline</option><option>site</option></select></label>
     <label class="f" style="flex:1">Params (JSON)<input id="sv_params" placeholder='{"category":"assumption","outcome":"confirmed"}' style="min-width:280px"></label>
     <button class="btn" id="sv_go">Save</button></div></div>
   <div class="panel"><table><thead><tr><th>Name</th><th>Kind</th><th>Params</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan=4 class="muted">No saved views.</td></tr>'}</tbody></table></div>`;
};

PAGES.crosssitedrift=async()=>{
  const d=await api('/api/cross-site-drift');
  let rows='';d.by_site.forEach(r=>{const sev=['','low','medium','high'][r.max_severity]||'';
    rows+=`<tr class="clk" data-site="${esc(r.site)}"><td>${esc(r.site)}</td><td>${r.drift_count}</td>
      <td><span class="st ${r.max_severity>=3?'failed':(r.max_severity>=2?'':'succeeded')}">${esc(sev||'none')}</span></td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-site]').forEach(r=>r.onclick=()=>go('investigate',{site:r.dataset.site})),0);
  return `<h1>Cross-Site Drift</h1><p class="sub">Drift verdicts grouped by site (pivot of Drift Ops). Click a site to investigate. Read-only.</p>
   <div class="panel"><p class="muted">${d.n_sites} site(s) with recorded drift</p>
   <table><thead><tr><th>Site</th><th>Drift count</th><th>Max severity</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=3 class="muted">No drift recorded.</td></tr>'}</tbody></table></div>`;
};
PAGES.portfolio=async()=>{
  const d=await api('/api/portfolio-ranking');
  let rows='';d.ranking.forEach((r,n)=>{rows+=`<tr class="clk" data-site="${esc(r.site)}"><td>${n+1}</td><td>${esc(r.site)}</td>
    <td>${r.entries}</td><td>${r.confirmed}</td><td>${r.concerns?`<span class="warn">${r.concerns}</span>`:'0'}</td>
    <td><span class="st ${r.health>=0.8?'succeeded':(r.health>=0.5?'':'failed')}">${(r.health*100).toFixed(0)}%</span></td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-site]').forEach(r=>r.onclick=()=>go('investigate',{site:r.dataset.site})),0);
  return `<h1>Portfolio Ranking</h1><p class="sub">Sites ranked by corpus volume + open concerns, with a confirmed-ratio health figure. Click a site. Read-only.</p>
   <div class="panel"><table><thead><tr><th>#</th><th>Site</th><th>Entries</th><th>Confirmed</th><th>Concerns</th><th>Health</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No sites.</td></tr>'}</tbody></table></div>`;
};
PAGES.blindspots=async()=>{
  const d=await api('/api/blind-spots');
  const ua=d.untested_assumptions.map(a=>`<tr class="clk" data-entry="${esc(a.id)}"><td>${esc(a.id)}</td><td>${esc(a.site)}</td><td>${esc(a.subject)}</td></tr>`).join('');
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>r.onclick=()=>go('corpus',{entry:r.dataset.entry})),0);
  return `<h1>Blind Spots</h1><p class="sub">Under-supported areas: untested assumptions, sites without captures, categories lacking confirmed evidence. Read-only.</p>
   <div class="cards">
     <div class="card warn"><div class="k">Untested assumptions</div><div class="v">${d.n_untested}</div></div>
     <div class="card"><div class="k">Sites w/o captures</div><div class="v">${(d.sites_without_captures||[]).length}</div></div>
     <div class="card"><div class="k">Cats w/o confirmed</div><div class="v">${(d.categories_without_confirmed_evidence||[]).length}</div></div></div>
   <div class="panel"><h2>Untested assumptions <span class="muted" style="font-weight:400;font-size:12px">— click to open</span></h2>
     <table><thead><tr><th>ID</th><th>Site</th><th>Subject</th></tr></thead><tbody>${ua||'<tr><td colspan=3 class="ok">None untested.</td></tr>'}</tbody></table></div>
   ${(d.sites_without_captures||[]).length?`<div class="panel"><h2>Sites without captures</h2><p class="mono">${d.sites_without_captures.map(esc).join(', ')}</p></div>`:''}
   ${(d.categories_without_confirmed_evidence||[]).length?`<div class="panel"><h2>Categories without confirmed evidence</h2><p class="mono">${d.categories_without_confirmed_evidence.map(esc).join(', ')}</p></div>`:''}
   <div class="panel muted">${esc(d._note)}</div>`;
};
PAGES.scarcity=async()=>{
  const d=await api('/api/evidence-scarcity');
  return `<h1>Evidence Scarcity Index</h1><p class="sub">Where evidence is thinnest. Higher index = thinner evidence. Read-only.</p>
   <div class="cards">
     <div class="card ${d.scarcity_index>=0.5?'err':(d.scarcity_index>=0.25?'warn':'ok')}"><div class="k">Scarcity index</div><div class="v">${(d.scarcity_index*100).toFixed(0)}%</div></div>
     <div class="card ok"><div class="k">Well supported</div><div class="v">${d.well_supported}</div></div>
     <div class="card"><div class="k">Untested</div><div class="v">${d.untested}</div></div>
     <div class="card warn"><div class="k">Open debt</div><div class="v">${d.open_debt}</div></div></div>
   ${(d.thinnest_sites||[]).length?`<div class="panel"><h2>Thinnest sites</h2><p class="mono">${d.thinnest_sites.map(esc).join(', ')}</p></div>`:''}
   <div class="panel muted">${esc(d._note)}</div>`;
};
PAGES.captureyield=async()=>{
  const d=await api('/api/capture-yield');
  const rows=Object.entries(d.confirmed_by_site||{}).map(([s,n])=>`<tr class="clk" data-site="${esc(s)}"><td>${esc(s)}</td><td>${n}</td></tr>`).join('');
  setTimeout(()=>$$('#main tr.clk[data-site]').forEach(r=>r.onclick=()=>go('investigate',{site:r.dataset.site})),0);
  return `<h1>Capture Yield</h1><p class="sub">Captures present vs confirmed evidence by site. Approximate. Read-only.</p>
   <div class="cards">
     <div class="card"><div class="k">Captures present</div><div class="v">${d.captures_present}</div></div>
     <div class="card ok"><div class="k">Total confirmed</div><div class="v">${d.total_confirmed}</div></div></div>
   <div class="panel"><h2>Confirmed evidence by site</h2>
     <table><thead><tr><th>Site</th><th>Confirmed entries</th></tr></thead><tbody>${rows||'<tr><td colspan=2 class="muted">None.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.decisionquality=async()=>{
  const d=await api('/api/decision-quality');
  return `<h1>Decision Quality</h1><p class="sub">Recorded review decisions + the confirm-rate of corpus resolutions. Read-only.</p>
   <div class="cards">
     <div class="card"><div class="k">Decisions recorded</div><div class="v">${d.decisions_recorded}</div></div>
     <div class="card ok"><div class="k">Accept</div><div class="v">${d.accept}</div></div>
     <div class="card err"><div class="k">Reject</div><div class="v">${d.reject}</div></div>
     <div class="card warn"><div class="k">Defer</div><div class="v">${d.defer}</div></div></div>
   <div class="panel"><h2>Resolution quality</h2>
     <p class="muted">${d.resolutions} resolution(s), ${d.resolutions_confirmed} confirmed</p>
     <div class="card ${d.confirm_rate>=0.8?'ok':(d.confirm_rate>=0.5?'warn':'err')}" style="max-width:240px"><div class="k">Confirm rate</div><div class="v">${(d.confirm_rate*100).toFixed(0)}%</div></div>
     <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.compliance=async()=>{
  const d=await api('/api/compliance');
  return `<h1>Compliance Summary</h1><p class="sub">Standing posture/compliance rollup. Read-only — the authoritative gate is build_release.py.</p>${banner()}
   <div class="panel" style="text-align:center"><div style="font-size:30px;font-weight:800;color:var(--${d.verdict==='compliant'?'ok':'warn'})">${esc(d.verdict.toUpperCase())}</div></div>
   <div class="cards">
     <div class="card"><div class="k">Artifacts scanned</div><div class="v">${d.posture.artifacts_scanned}</div></div>
     <div class="card ${d.posture.with_leaks?'err':'ok'}"><div class="k">Posture leaks</div><div class="v">${d.posture.with_leaks}</div></div>
     <div class="card ${d.correction_debt?'err':'ok'}"><div class="k">Correction debt</div><div class="v">${d.correction_debt??'?'}</div></div></div>
   ${(d.posture.leak_files||[]).length?`<div class="panel"><h2>Artifacts with leaks (withheld from display)</h2><p class="mono">${d.posture.leak_files.map(esc).join(', ')}</p></div>`:''}
   <div class="panel muted">${esc(d._note)}</div>`;
};

PAGES.impact=async()=>{
  const ex=await api('/api/corpus');const sites=ex.facets.sites;
  const ids=ex.rows.map(r=>r.id);
  const opt=[...sites.map(s=>`<option value="${esc(s)}">site: ${esc(s)}</option>`),...ids.map(i=>`<option value="${esc(i)}">${esc(i)}</option>`)].join('');
  setTimeout(()=>{$('#im_go').onclick=imGo;},0);
  return `<h1>Impact Simulator</h1><p class="sub">What-if over the resolves-graph: pick an entry or site to see its blast radius — what would be affected if it changed. Read-only graph reachability.</p>${banner()}
   <div class="panel"><div class="row"><label class="f">Target<select id="im_tgt">${opt}</select></label>
   <button class="btn" id="im_go">Simulate</button></div></div>
   <div id="im_out" class="panel muted">Pick a target.</div>`;
};
async function imGo(){const t=$('#im_tgt').value;const d=await api('/api/impact/'+encodeURIComponent(t));const o=$('#im_out');o.className='panel';
  const aff=d.would_be_affected.map(r=>`<tr class="clk" data-entry="${esc(r.id)}"><td>${esc(r.id)}</td><td>${esc(r.site)}</td><td>${esc(r.outcome||'')}</td><td>${esc(r.subject)}</td></tr>`).join('');
  const dep=d.depends_on.map(r=>`<tr class="clk" data-entry="${esc(r.id)}"><td>${esc(r.id)}</td><td>${esc(r.site)}</td><td>${esc(r.outcome||'')}</td><td>${esc(r.subject)}</td></tr>`).join('');
  o.innerHTML=`<h2>Impact of ${esc(d.target)} <span class="muted">(${esc(d.scope)}, ${d.seeds.length} seed${d.seeds.length===1?'':'s'})</span></h2>
   <div class="cards"><div class="card ${d.blast_radius?'warn':'ok'}"><div class="k">Blast radius</div><div class="v">${d.blast_radius}</div></div></div>
   <h3>Would be affected if this changed</h3><table><thead><tr><th>ID</th><th>Site</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${aff||'<tr><td colspan=4 class="ok">Nothing depends on it.</td></tr>'}</tbody></table>
   <h3>This depends on</h3><table><thead><tr><th>ID</th><th>Site</th><th>Outcome</th><th>Subject</th></tr></thead><tbody>${dep||'<tr><td colspan=4 class="muted">Depends on nothing.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p>`;
  $$('#main tr.clk[data-entry]').forEach(r=>r.onclick=()=>go('corpus',{entry:r.dataset.entry}));
}
PAGES.opportunity=async()=>{
  const d=await api('/api/capture-opportunity');
  const pr=p=>['','#f85149','#d29922','#8b98a9'][p]||'';
  let rows='';d.opportunities.forEach(o=>{rows+=`<tr class="${o.id?'clk':''}" data-entry="${esc(o.id||'')}">
    <td><span class="st" style="background:${pr(o.priority)}22;color:${pr(o.priority)}">P${o.priority}</span></td>
    <td>${esc(o.site)}</td><td>${esc(o.reason)}</td><td>${esc(o.axis||'—')}</td><td class="muted">${esc(o.action)}</td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-entry]').forEach(r=>{const id=r.dataset.entry;if(id)r.onclick=()=>go('corpus',{entry:id});}),0);
  return `<h1>Capture Opportunity</h1><p class="sub">Where evidence is missing, ranked: open validation debt > untested assumptions > sites without captures. Recommends only.</p>${banner()}
   <div class="cards">
     <div class="card err"><div class="k">P1 — validation debt</div><div class="v">${d.by_priority[1]}</div></div>
     <div class="card warn"><div class="k">P2 — untested</div><div class="v">${d.by_priority[2]}</div></div>
     <div class="card"><div class="k">P3 — no capture</div><div class="v">${d.by_priority[3]}</div></div></div>
   <div class="panel"><table><thead><tr><th>Priority</th><th>Site</th><th>Reason</th><th>Axis</th><th>Suggested action</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="ok">No gaps — evidence is complete.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.similarity=async()=>{
  const d=await api('/api/structural-similarity');
  let rows='';d.pairs.forEach(p=>{rows+=`<tr><td>${esc(p.a)}</td><td>${esc(p.b)}</td>
    <td><span class="st ${p.similarity>=0.5?'succeeded':(p.similarity>=0.25?'':'failed')}">${(p.similarity*100).toFixed(0)}%</span></td>
    <td class="mono">${(p.shared||[]).map(esc).join(', ')||'—'}</td></tr>`;});
  return `<h1>Structural Similarity</h1><p class="sub">Pairwise similarity between sites. Signal: <b>${esc(d.signal)}</b>.</p>
   ${d.signal==='corpus-only'?'<div class="banner">This is the corpus-only signal (category + conclusion-class profiles). It sharpens automatically once captures populate rendition/signing descriptors.</div>':''}
   <div class="panel"><table><thead><tr><th>Site A</th><th>Site B</th><th>Similarity</th><th>Shared features</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=4 class="muted">Need ≥2 sites.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.family=async()=>{
  const d=await api('/api/family-explorer');
  let cards='';d.families.forEach(f=>{cards+=`<div class="panel"><h2>${esc(f.family_id)} <span class="muted" style="font-weight:400;font-size:12px">— ${f.size} site(s)</span></h2>
    <div class="row">${f.members.map(s=>`<button class="btn sec" data-inv="${esc(s)}">${esc(s)}</button>`).join(' ')}</div></div>`;});
  setTimeout(()=>$$('#main [data-inv]').forEach(b=>b.onclick=()=>go('investigate',{site:b.dataset.inv})),0);
  return `<h1>Family Explorer</h1><p class="sub">Sites grouped into families by structural similarity. Click a site to investigate. Signal: <b>${esc(d.signal)}</b>.</p>
   ${d.signal==='corpus-only'?'<div class="banner">Corpus-only grouping today (sharpens with captures).</div>':''}
   <div class="cards"><div class="card"><div class="k">Families</div><div class="v">${d.n_families}</div></div>
     <div class="card"><div class="k">Threshold</div><div class="v">${(d.threshold*100).toFixed(0)}%</div></div></div>
   ${cards||'<div class="panel muted">No sites.</div>'}`;
};
PAGES.familyhealth=async()=>{
  const d=await api('/api/family-health');
  let rows='';d.families.forEach(f=>{rows+=`<tr><td>${esc(f.family_id)}</td><td class="mono">${f.members.map(esc).join(', ')}</td>
    <td>${f.entries}</td><td>${f.confirmed}</td><td>${f.concerns?`<span class="warn">${f.concerns}</span>`:'0'}</td><td>${f.drift}</td>
    <td><span class="st ${f.health>=0.8?'succeeded':(f.health>=0.5?'':'failed')}">${(f.health*100).toFixed(0)}%</span></td></tr>`;});
  return `<h1>Family Health</h1><p class="sub">Aggregate health per family. Read-only. Signal: <b>${esc(d.signal)}</b>.</p>
   <div class="panel"><table><thead><tr><th>Family</th><th>Members</th><th>Entries</th><th>Confirmed</th><th>Concerns</th><th>Drift</th><th>Health</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No families.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.escalations=async()=>{
  const d=await api('/api/escalations');const ex=await api('/api/corpus');
  const entryOpt=ex.rows.map(r=>`<option>${esc(r.id)}</option>`).join('');
  let rows='';d.escalations.forEach(e=>{const when=e.at?new Date(e.at*1000).toLocaleString():'';
    rows+=`<tr><td class="clk" data-entry="${esc(e.item_id)}"><b>${esc(e.item_id)}</b></td><td>${esc(e.reason||'—')}</td><td class="muted">${esc(when)}</td>
      <td><button class="btn sec" data-clear="${esc(e.item_id)}">clear</button></td></tr>`;});
  setTimeout(()=>{
    $('#es_go').onclick=async()=>{try{await api('/api/escalations',{method:'POST',body:JSON.stringify({item_id:$('#es_id').value,reason:$('#es_reason').value})});go('escalations');}catch(e){toast(e.message,'err')}};
    $$('#main [data-clear]').forEach(b=>b.onclick=async()=>{await api('/api/escalations/clear',{method:'POST',body:JSON.stringify({item_id:b.dataset.clear})});go('escalations');});
    $$('#main td.clk[data-entry]').forEach(c=>c.onclick=()=>go('corpus',{entry:c.dataset.entry}));
  },0);
  return `<h1>Escalations</h1><p class="sub">Flag items for human attention. Inert — flagging triggers no action.</p>${banner()}
   <div class="panel"><div class="row">
     <label class="f">Item<select id="es_id">${entryOpt}</select></label>
     <label class="f" style="flex:1">Reason<input id="es_reason" placeholder="why this needs escalation" style="min-width:280px"></label>
     <button class="btn" id="es_go">Flag</button></div></div>
   <div class="panel"><p class="muted">${d.n_open} open</p>
   <table><thead><tr><th>Item</th><th>Reason</th><th>Flagged</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan=4 class="muted">Nothing escalated.</td></tr>'}</tbody></table></div>`;
};

PAGES.maturity=async()=>{
  const d=await api('/api/maturity');
  const comp=Object.entries(d.components).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${(v*100).toFixed(0)}%</td><td class="muted">weight ${((d.weights[k]||0)*100).toFixed(0)}%</td></tr>`).join('');
  const col=d.score>=70?'ok':d.score>=40?'warn':'err';
  return `<h1>Maturity</h1><p class="sub">A defined composite (not an objective measure) — every input shown, weights adjustable.</p>
   <div class="panel" style="text-align:center"><div style="font-size:40px;font-weight:800;color:var(--${col})">${d.score}<span style="font-size:18px">/100</span></div>
     <div class="st ${col==='ok'?'succeeded':col==='warn'?'':'failed'}">${esc(d.band)}</div></div>
   <div class="panel"><h2>Components (equal weight)</h2><table><thead><tr><th>Component</th><th>Value</th><th>Weight</th></tr></thead><tbody>${comp}</tbody></table></div>
   <div class="panel"><h2>Raw inputs</h2><p class="muted">corpus ${d.inputs.corpus_total} · confirmed ${d.inputs.confirmed} · open debt ${d.inputs.open_debt} · resolutions ${d.inputs.resolutions}</p>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.complexity=async()=>{
  const d=await api('/api/complexity');
  const dr=Object.entries(d.drivers).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${v}</td><td class="muted">ref ${d.references[k]}</td></tr>`).join('');
  return `<h1>Operational Complexity</h1><p class="sub">A defined relative index — drivers are real counts, references documented + adjustable.</p>
   <div class="panel" style="text-align:center"><div style="font-size:40px;font-weight:800">${d.complexity_index}<span style="font-size:18px">/100</span></div><div class="muted">relative complexity index</div></div>
   <div class="panel"><h2>Drivers</h2><table><thead><tr><th>Driver</th><th>Count</th><th>Soft reference</th></tr></thead><tbody>${dr}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.orghealth=async()=>{
  const d=await api('/api/org-health');
  const comp=Object.entries(d.components).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${(v*100).toFixed(0)}%</td></tr>`).join('');
  const col=d.score>=70?'ok':d.score>=40?'warn':'err';
  return `<h1>Organizational Health Index</h1><p class="sub">A defined composite: maturity + concern-freedom + evidence-freshness.</p>
   <div class="panel" style="text-align:center"><div style="font-size:40px;font-weight:800;color:var(--${col})">${d.score}<span style="font-size:18px">/100</span></div>
     <div class="st ${col==='ok'?'succeeded':col==='warn'?'':'failed'}">${esc(d.band)}</div></div>
   <div class="panel"><h2>Components (equal weight)</h2><table><thead><tr><th>Component</th><th>Value</th></tr></thead><tbody>${comp}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.portfolioopp=async()=>{
  const d=await api('/api/portfolio-opportunity');
  let rows='';d.by_site.forEach(r=>{rows+=`<tr class="clk" data-site="${esc(r.site)}"><td>${esc(r.site)}</td><td><b>${r.opportunity_score}</b></td>
    <td>${r.p1?`<span class="err">${r.p1}</span>`:'0'}</td><td>${r.p2}</td><td>${r.p3}</td></tr>`;});
  setTimeout(()=>$$('#main tr.clk[data-site]').forEach(r=>r.onclick=()=>go('investigate',{site:r.dataset.site})),0);
  return `<h1>Portfolio Opportunity</h1><p class="sub">Per-site rollup of capture opportunities (P1 validation debt weighted heaviest). Click a site. Read-only.</p>${banner()}
   <div class="panel"><table><thead><tr><th>Site</th><th>Opportunity score</th><th>P1 debt</th><th>P2 untested</th><th>P3 no-capture</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="ok">No gaps.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.narrative=async()=>{
  const d=await api('/api/narrative');
  const paras=d.paragraphs.map(p=>`<p style="font-size:15px;line-height:1.6">${esc(p)}</p>`).join('');
  return `<h1>Operational Narrative</h1><p class="sub">Deterministic prose from current figures (no model call). As of ${esc(d.as_of)}.</p>
   <div class="panel">${paras}<p class="muted">${esc(d._note)}</p></div>`;
};

// ── Template Intelligence (v3.66.110): first-class cockpit area. Read-only. ──
PAGES.videotemplates=async()=>{
  const d=await api('/api/template/video-health');
  const rows=d.sites.map(s=>{
    const c=s.selector_confidence;
    const conf=`<span class="${c.band==='high'?'ok':c.band==='low'?'err':'muted'}">${c.band} (${c.score})</span>`;
    const present=s.template_present?`<span class="ok">yes</span>`:`<span class="err">missing</span>`;
    const stale=s.drift.flagged_stale?`<span class="err">STALE (${s.drift.consecutive_failures} fails)</span>`:`<span class="ok">ok</span>`;
    return `<tr><td>${esc(s.site||'(unnamed)')}</td><td>${present}</td><td>${s.row_selector_count}</td>
      <td>${s.two_step_flow?'yes ('+s.two_step_trigger_count+')':'no'}</td><td>${conf}</td>
      <td>${esc(s.highest_rendition_seen||'—')}</td><td>${stale}</td>
      <td>${esc(s.drift.last_success_ts||'—')}</td></tr>`;
  }).join('');
  return `<h1>Video Templates</h1>
   <p class="sub">Per-site health of the video/download template (the learned.download block) + selector-drift.
   Read-only; recognition-only — no live fetch, no replay, no model call.</p>${banner()}
   <div class="panel"><b>${d.site_count}</b> site(s) · <b>${d.missing_templates}</b> missing template(s) · <b>${d.stale}</b> drifted.
   ${d.config_present?'':'<span class="err">No sites_config.json found in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Template</th><th>Selectors</th><th>Two-step</th>
   <th>Selector confidence</th><th>Highest rendition</th><th>Drift</th><th>Last success</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=8 class="muted">No sites configured.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.logintemplates=async()=>{
  const r=await api('/api/template/login-health'); const d=r.health; const hist=r.history;
  const hmap={}; (hist.sites||[]).forEach(h=>hmap[h.site]=h);
  const rows=d.sites.map(s=>{
    const c=s.selector_confidence;
    const conf=`<span class="${c.band==='high'?'ok':c.band==='low'?'err':'muted'}">${c.band} (${c.score})</span>`;
    const present=s.template_present?`<span class="ok">yes</span>`:`<span class="err">missing</span>`;
    const sess=s.session.suggested_action==='unknown'
      ? `<span class="muted">unknown (${esc(s.session.measurement_status||'unmeasured')})</span>`
      : s.session.available?(s.session.cookie_score!=null?`${s.session.band||''} (${s.session.cookie_score})`:'—'):'<span class="muted">n/a</span>';
    const rate=s.recent_success_rate!=null?(Math.round(s.recent_success_rate*100)+'%'):'—';
    const mfa=s.mfa_captcha_indicated?'<span class="muted">yes</span>':'no';
    const h=hmap[s.site]; const last=h&&h.last?`${esc(h.last.outcome)} @ ${esc(h.last.ts||'')}`:'—';
    return `<tr><td>${esc(s.site||'(unnamed)')}</td><td>${present}</td>
      <td>u:${s.selector_counts.user_field} p:${s.selector_counts.pass_field} s:${s.selector_counts.submit_btn}</td>
      <td>${conf}</td><td>${sess}</td><td>${rate}</td><td>${mfa}</td><td class="muted">${last}</td></tr>`;
  }).join('');
  return `<h1>Login Templates</h1>
   <p class="sub">Per-site login-template health (learned.login) + session freshness + recent login outcomes.
   Read-only; no credential values shown, no login attempted.</p>${banner()}
   <div class="panel"><b>${d.site_count}</b> site(s) · <b>${d.missing_templates}</b> missing · <b>${d.mfa_captcha_sites}</b> with MFA/captcha observed.
   ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Template</th><th>Selectors (u/p/s)</th>
   <th>Confidence</th><th>Session</th><th>Success rate</th><th>MFA/captcha</th><th>Last login event</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=8 class="muted">No sites configured.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.logindrift=async()=>{
  const r=await api('/api/template/login-drift'); const d=r.drift; const dry=r.dry_run;
  const rows=d.sites.map(s=>{
    const sig=s.signals;
    const cell=v=>v===true?'<span class="err">yes</span>':v===false?'<span class="ok">no</span>':v==='needs_dry_run'?'<span class="muted">needs dry-run</span>':'<span class="muted">—</span>';
    return `<tr><td>${esc(s.site||'(unnamed)')}</td><td>${cell(sig.cookie_expired)}</td>
      <td>${cell(sig.login_failing)}</td><td>${cell(sig.mfa_captcha_present)}</td>
      <td>${cell(sig.user_field_changed)}</td><td>${cell(sig.submit_changed)}</td>
      <td>${cell(sig.success_marker_changed)}</td></tr>`;
  }).join('');
  const f=dry.fields_identified;
  return `<h1>Login Drift</h1>
   <p class="sub">Drift classified from observable state. Field/form/marker changes need a Safe Dry Run to confirm — marked, never guessed.</p>${banner()}
   <div class="panel"><h2>Safe login dry-run ${dry.is_sample?'<span class="muted" style="font-weight:400">(sample form)</span>':''}</h2>
   Fields identified — username: ${f.username_field?'✓':'✗'}, password: ${f.password_field?'✓':'✗'}, submit: ${f.submit_button?'✓':'✗'}, form: ${f.login_form?'✓':'✗'}.
   Captcha: ${dry.captcha_detected?('<span class="muted">'+esc(dry.captcha_kind||'yes')+'</span>'):'none'}. Confidence: <b>${dry.confidence}</b> (${dry.band}).
   <b>Would submit credentials: ${dry.would_submit?'YES':'NO'}</b>.<p class="muted">${esc(dry._note)}</p></div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Cookie expired</th><th>Login failing</th><th>MFA/captcha</th>
   <th>User field</th><th>Submit</th><th>Success marker</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No sites.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.loginreview=async()=>{
  const d=await api('/api/template/login-review');
  const rows=d.queue.map(i=>`<tr><td>${esc(i.site)}</td><td>${i.reasons.map(esc).join('; ')}</td>
    <td>${i.has_suggestion?'<span class="muted">data-only suggestion</span>':'—'}</td></tr>`).join('');
  return `<h1>Login Review Queue</h1>
   <p class="sub">Login templates that look like they need attention. Suggestions are data-only — the approve/reject workbench is Phase 4. Nothing is applied automatically.</p>${banner()}
   <div class="panel"><b>${d.count}</b> template(s) flagged for review.</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Why</th><th>Suggestion</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=3 class="ok">Nothing needs review.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.autonomycenter=async()=>{
  const [d,pv]=await Promise.all([api('/api/autonomy/center'),api('/api/autonomy/metrics')]);
  const cw=d.cycle_would||{}; const ks=d.kill_switch||{};
  const applyClass=cw.mode==='apply'?'ok':cw.mode==='skipped'?'err':'acc';
  const lc=d.last_cycle;
  return `<h1>Autonomy Center</h1>
   <p class="sub">Controlled Class B autonomy. Class B housekeeping can run autonomously on a host-scheduled cycle; Class C and D stay human-controlled.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--${applyClass})">
     <b>Next cycle would:</b> <b class="${applyClass}">${esc((cw.mode||'').toUpperCase())}</b>
     <span class="muted">— ${esc(cw.reason||'')}</span>
     <div class="muted" style="font-size:11px;margin-top:4px">Apply requires all of: not frozen, Class B at auto, and the host flag <code>${esc(d.host_env_flag)}</code> set (currently ${d.host_env_set?'<span class="ok">set</span>':'<span class="muted">not set</span>'}). Default is a dry-run.</div>
   </div>
   <div class="panel"><b>Class B:</b> ${esc(d.class_b_level)} ${d.class_b_can_autonomously&&d.class_b_can_autonomously.allowed?'<span class="ok">(autonomous when host flag set)</span>':'<span class="muted">(not autonomous)</span>'}
     · <b>Class C:</b> ${esc(d.class_c_level)} <span class="muted">(human)</span> · <b>Class D:</b> ${esc(d.class_d_level)} <span class="muted">(human)</span></div>
   <div class="panel" style="${ks.frozen?'border-left:3px solid var(--red)':''}"><b>Kill switch:</b> ${ks.frozen?'<span class="err">FROZEN</span> '+esc(ks.reason||''):'<span class="ok">armed</span>'}</div>
   <div class="panel"><b>Cycles run:</b> ${d.cycles_run} · applied ${pv.cycles_applied} · dry-run ${pv.cycles_dry_run} · skipped(frozen) ${pv.cycles_skipped_frozen}
     ${lc?`<br><span class="muted">last: ${esc(lc.ts||'')} — ${esc(lc.mode||'')} (${esc(lc.reason||'')})</span>`:''}</div>
   <div class="panel">
     <a class="btn" data-p="queueintel">Queue Intelligence</a> <a class="btn" data-p="reviewops">Review Operations</a>
     <a class="btn" data-p="notifcenter">Notification Center</a> <a class="btn" data-p="govhealth">Governance Health</a>
     <a class="btn" data-p="autmetrics">Automation Metrics</a> <a class="btn" data-p="governance">Governance →</a></div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.queueintel=async()=>{
  const d=await api('/api/autonomy/queue');
  const rows=(d.proposed_reorder||[]).map((x,i)=>`<tr><td>${i+1}</td><td>${esc(typeof x==='string'?x:JSON.stringify(x))}</td></tr>`).join('');
  return `<h1>Queue Intelligence</h1><p class="sub">The plan queue and the proposed (reversible) reordering. Read-only — applying happens in the autonomy cycle.</p>${banner()}
   <div class="panel"><a class="btn" data-p="autonomycenter">← Autonomy Center</a> · queue size <b>${d.queue_size}</b> · would change <b>${d.would_change}</b> position(s)</div>
   <div class="panel"><table><thead><tr><th>#</th><th>Proposed order</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=2 class="muted">Queue empty / no reorder proposed.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.reviewops=async()=>{
  const d=await api('/api/autonomy/review-ops');
  const exp=(d.expired||[]).map(x=>`<tr><td><code>${esc(x.change_id||'')}</code></td><td>${esc(x.site||'')}</td><td class="err">expired</td></tr>`).join('');
  const app=(d.approaching||[]).map(x=>`<tr><td><code>${esc(x.change_id||'')}</code></td><td>${esc(x.site||'')}</td><td class="acc">${esc(x.deadline||'')}</td></tr>`).join('');
  return `<h1>Review Operations</h1><p class="sub">Pending Class C reviews and their fail-closed deadlines. Tracking is read-only; expired reviews auto-revert on the guardrail sweep.</p>${banner()}
   <div class="panel"><a class="btn" data-p="autonomycenter">← Autonomy Center</a> · pending <b>${d.pending_count}</b> · approaching <b>${(d.approaching||[]).length}</b> · expired <b>${(d.expired||[]).length}</b></div>
   <div class="panel"><table><thead><tr><th>Change</th><th>Site</th><th>Deadline / status</th></tr></thead>
   <tbody>${exp}${app}${(exp||app)?'':'<tr><td colspan=3 class="muted">No pending reviews near deadline.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.notifcenter=async()=>{
  const d=await api('/api/autonomy/notifications');
  const rows=(d.notifications||[]).map(n=>`<tr><td class="muted">${esc(n.ts||n.created_at||'')}</td><td>${esc(n.kind||n.type||'')}</td><td>${esc(n.message||n.detail||JSON.stringify(n))}</td></tr>`).join('');
  return `<h1>Notification Center</h1><p class="sub">In-GUI notifications only — no external push.</p>${banner()}
   <div class="panel"><a class="btn" data-p="autonomycenter">← Autonomy Center</a> · ${(d.notifications||[]).length} notification(s) · would add <b>${d.would_create}</b> on next cycle</div>
   <div class="panel"><table><thead><tr><th>When</th><th>Kind</th><th>Message</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=3 class="muted">No notifications.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.govhealth=async()=>{
  const d=await api('/api/autonomy/governance-health');
  const ks=d.kill_switch||{}; const m=d.throttle_metrics||{};
  const an=(d.anomalies||[]).map(a=>`<li class="err">${esc(a)}</li>`).join('');
  return `<h1>Governance Health</h1><p class="sub">Read-only governance monitoring.</p>${banner()}
   <div class="panel"><a class="btn" data-p="autonomycenter">← Autonomy Center</a></div>
   <div class="panel"><b>Kill switch:</b> ${ks.frozen?'<span class="err">FROZEN</span>':'<span class="ok">armed</span>'} · <b>Policy v</b>${d.policy_version} · hash <code>${esc((d.policy_hash||'').slice(0,16))}…</code> · <b>Guardrails complete:</b> ${d.guardrails_complete?'<span class="ok">yes</span>':'<span class="err">no</span>'}</div>
   <div class="panel"><b>Self-throttle:</b> rollback rate ${m.rollback_rate} · review-expiry rate ${m.review_expiry_rate} · oracle-disagreement ${m.oracle_disagreement_rate==null?'—':m.oracle_disagreement_rate}</div>
   <div class="panel"><b>Anomalies:</b> ${an?('<ul>'+an+'</ul>'):'<span class="ok">none</span>'}</div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.autmetrics=async()=>{
  const d=await api('/api/autonomy/metrics');
  return `<h1>Automation Metrics</h1><p class="sub">Class B autonomy cycle history and counts.</p>${banner()}
   <div class="panel"><a class="btn" data-p="autonomycenter">← Autonomy Center</a></div>
   <div class="panel"><b>Cycles:</b> total ${d.cycles_total} · applied ${d.cycles_applied} · dry-run ${d.cycles_dry_run} · skipped(frozen) ${d.cycles_skipped_frozen}</div>
   <div class="panel"><b>Housekeeping:</b> applied ${d.housekeeping_applied} · reversed ${d.housekeeping_reversed} · notifications ${d.notifications} · review packets ${d.review_packets}</div>
   <div class="panel"><b>Throttle:</b> ${esc(JSON.stringify(d.throttle_metrics||{}))}</div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.oracle=async()=>{
  const [st,em]=await Promise.all([api('/api/oracle/status'),api('/api/oracle/eligibility')]);
  const td=st.tier_distribution||{};
  const tierBadge=(t,n)=>`<span class="${t==='strong_descriptor'?'ok':t==='no_oracle'?'muted':'acc'}" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(t)}: <b>${n}</b></span>`;
  const rows=(em.sites||[]).map(r=>`<tr><td>${esc(r.site||'')}</td>
    <td><b>${r.tier}</b> ${esc(r.tier_name)}</td><td>${r.held_out_count}</td>
    <td><span class="muted">no</span></td>
    <td><a class="btn" data-dl="${esc(r.site)}" data-p="oracleverdict">verdict</a></td></tr>`).join('');
  return `<h1>Oracle Status</h1>
   <p class="sub">Tiered correctness oracle &amp; eligibility engine — descriptor-only, eligibility ASSESSMENT only. No tier authorizes automation.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Guardrail set complete:</b> <span class="ok">${st.guardrails_complete?'yes':'no'}</span>
     · <b>Class C auto enabled by default:</b> <b class="ok">NO</b>
     · <b>Automation-eligible sites:</b> <b class="ok">${st.automation_eligible_sites}</b>
     <div class="muted" style="font-size:11px;margin-top:4px">Completing the oracle does not enable automation: Class C defaults to Approve-each, the per-site grant store is empty by design (no writer),
     and there is no Class C apply path. Enabling any per-site autonomy is a separate governance decision.</div>
   </div>
   <div class="panel"><b>Tier distribution:</b> ${tierBadge('no_oracle',td.no_oracle||0)}${tierBadge('weak_descriptor',td.weak_descriptor||0)}${tierBadge('standard_descriptor',td.standard_descriptor||0)}${tierBadge('strong_descriptor',td.strong_descriptor||0)}
     · <a class="btn" data-p="oracleheldout">Held-out evidence</a> <a class="btn" data-p="oracleineligible">Ineligible sites</a> <a class="btn" data-p="oraclereports">Reports</a> <a class="btn" data-p="governance">← Governance</a></div>
   <h2>Eligibility Matrix</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Tier</th><th>Held-out</th><th>Automation eligible</th><th>Verdict</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No sites configured in this environment.</td></tr>'}</tbody></table>
   <p class="muted">${esc(em._note)}</p></div>`;
};
PAGES.oracleverdict=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Oracle Verdict</h1>${banner()}<div class="panel muted">Open from the Eligibility Matrix.</div>`;
  const v=await api('/api/oracle/verdict?site='+encodeURIComponent(site));
  const hf=(v.hard_failures||[]).map(esc).join('<br>')||'<span class="ok">none</span>';
  const ck=(v.checks||[]).map(c=>'· '+esc(c)).join('<br>')||'—';
  return `<h1>Oracle Verdict — ${esc(v.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="oracle">← Oracle</a></div>
   <div class="panel"><b>Tier ${v.tier}</b> (${esc(v.tier_name)}) · held-out ${v.held_out_count} · training ${v.training_count} · <b>automation eligible:</b> <b class="ok">no</b></div>
   <div class="panel"><b>Eligible for</b><br><span class="muted">${esc(v.eligible_for||'')}</span></div>
   <div class="panel"><b>Checks</b><br>${ck}</div>
   <div class="panel"><b>Hard failures</b><br>${hf}</div>
   <p class="muted">${esc(v._note)}</p>`;
};
PAGES.oracleheldout=async()=>{
  const d=await api('/api/oracle/held-out');
  const rows=(d.sites||[]).map(r=>`<tr><td>${esc(r.site||'')}</td><td class="muted">${esc(JSON.stringify(r.training_captures||[]))}</td>
    <td class="muted">${esc(JSON.stringify(r.held_out_captures||[]))}</td><td>${r.tier}</td></tr>`).join('');
  return `<h1>Held-Out Evidence</h1><p class="sub">Held-out evidence must be disjoint from training evidence — overlap is a hard failure.</p>${banner()}
   <div class="panel"><a class="btn" data-p="oracle">← Oracle</a></div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Training captures</th><th>Held-out captures</th><th>Tier</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=4 class="muted">No capture provenance designated.</td></tr>'}</tbody></table></div>`;
};
PAGES.oracleineligible=async()=>{
  const d=await api('/api/oracle/ineligible');
  const rows=(d.ineligible||[]).map(r=>`<tr><td>${esc(r.site||'')}</td><td>${r.tier}</td><td class="muted">${esc(JSON.stringify(r.hard_failures||r.why||''))}</td></tr>`).join('');
  const pin=(d.permanently_ineligible_actions||[]).map(x=>`<span class="muted" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(x.replace(/_/g,' '))}</span>`).join('');
  return `<h1>Ineligible Sites</h1><p class="sub">Tier 0 / hard-failure sites, plus actions that are permanently ineligible regardless of tier.</p>${banner()}
   <div class="panel"><a class="btn" data-p="oracle">← Oracle</a> · <b>${d.count}</b> ineligible site(s)</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Tier</th><th>Why</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=3 class="muted">None.</td></tr>'}</tbody></table></div>
   <div class="panel"><b>Permanently ineligible (any tier):</b><br>${pin}</div>`;
};
PAGES.oraclereports=async()=>{
  const d=await api('/api/oracle/reports');
  const st=d.oracle_status||{};
  return `<h1>Oracle Reports</h1><p class="sub">Assembled report bundle (read-only). Artifacts (oracle_verdict.json, eligibility_matrix.{json,md}, oracle_report.md, held_out_evidence_report.md, ineligible_sites_report.md) are written only by the explicit generate_oracle_reports() function — never automatically.</p>${banner()}
   <div class="panel"><a class="btn" data-p="oracle">← Oracle</a></div>
   <div class="panel"><b>Summary:</b> ${st.site_count} site(s) · tiers ${esc(JSON.stringify(st.tier_distribution||{}))} · automation-eligible <b class="ok">${st.automation_eligible_sites}</b></div>
   <div class="panel muted" style="font-size:12px">Generate artifacts to disk via:<br><code>generate_oracle_reports('operator')</code></div>`;
};
PAGES.eligibility=async()=>{
  const [st,ov]=await Promise.all([api('/api/eligibility/status'),api('/api/eligibility/overview')]);
  const yn=b=>b?'<span class="ok">yes</span>':'<span class="muted">no</span>';
  const rows=(ov.sites||[]).map(r=>{
    const decay=(r.decay_reasons||[]).map(esc).join('; ')||'<span class="ok">qualified</span>';
    return `<tr><td>${esc(r.site||'')}</td>
      <td><b>${r.oracle_tier}</b> ${esc(r.tier_name||'')}</td>
      <td>${yn(r.evidence_fresh)}</td>
      <td>${yn(r.evidence_qualified)}</td>
      <td><span class="muted">no</span></td>
      <td class="muted" style="font-size:11px">${decay}</td>
      <td><a class="btn" data-dl="${esc(r.site)}" data-p="eligibilitysite">detail</a></td></tr>`;}).join('');
  return `<h1>Eligibility Governance</h1>
   <p class="sub">Per-site participation-eligibility evaluation with decay — a layer above the oracle.
   Evaluation ONLY: no grant, no Class C apply path. Qualification decays automatically as held-out evidence goes stale (trust only ever decreases).</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Class C level:</b> <b class="acc">${esc(st.class_c_level||'')}</b>
     · <b>Class C auto by default:</b> <b class="ok">NO</b>
     · <b>Participation-eligible sites:</b> <b class="ok">${st.participation_eligible_sites}</b>
     · <b>Apply path exists:</b> <b class="ok">${st.apply_path_exists?'yes':'no'}</b>
     · <b>Frozen:</b> ${st.frozen?'<span class="err">yes</span>':'<span class="muted">no</span>'}
     <div class="muted" style="font-size:11px;margin-top:4px">Qualify = oracle Tier &ge; ${st.min_oracle_tier} AND held-out evidence &le; ${st.evidence_fresh_days}d AND not frozen AND candidate not pinned.
     Participation additionally needs the (empty) per-site grant + an apply path (none exists), so it is 0 for every site. Considered set capped at ${st.max_eligible_sites}.</div>
   </div>
   <div class="panel"><b>Evidence-qualified:</b> ${st.evidence_qualified_count} · <b>Considered (capped at ${ov.max_eligible_sites}):</b> ${(ov.considered_for_experimentation||[]).map(esc).join(', ')||'none'}${(ov.over_cap_excluded||[]).length?(' · <span class="muted">over-cap excluded: '+ov.over_cap_excluded.map(esc).join(', ')+'</span>'):''}
     · <a class="btn" data-p="oracle">← Oracle</a></div>
   <h2>Per-site eligibility</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Tier</th><th>Evidence fresh</th><th>Qualified</th><th>Participation eligible</th><th>Decay reasons</th><th></th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No sites configured in this environment.</td></tr>'}</tbody></table>
   <p class="muted">${esc(ov._note)}</p></div>`;
};
PAGES.eligibilitysite=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Site Eligibility</h1>${banner()}<div class="panel muted">Open from the Eligibility Governance table.</div>`;
  const v=await api('/api/eligibility/site?site='+encodeURIComponent(site));
  const decay=(v.decay_reasons||[]).map(x=>'· '+esc(x)).join('<br>')||'<span class="ok">none — evidence-qualified</span>';
  const reasons=(v.reasons||[]).map(x=>'· '+esc(x)).join('<br>')||'—';
  const pin=(v.permanently_ineligible_actions||[]).map(x=>`<span class="muted" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(x.replace(/_/g,' '))}</span>`).join('');
  const age=(v.evidence_age_days==null)?'unknown':(Math.round(v.evidence_age_days*10)/10)+'d';
  return `<h1>Site Eligibility — ${esc(v.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="eligibility">← Eligibility Governance</a></div>
   <div class="panel"><b>Oracle tier ${v.oracle_tier}</b> (${esc(v.tier_name||'')}) · evidence fresh: ${yn2(v.evidence_fresh)} (age ${esc(age)})
     · <b>evidence-qualified:</b> ${yn2(v.evidence_qualified)} · <b>participation-eligible:</b> <b class="ok">no</b></div>
   <div class="panel"><b>Why qualification would decay</b><br>${decay}</div>
   <div class="panel"><b>Why participation is blocked</b><br>${reasons}</div>
   <div class="panel"><b>Permanently ineligible (any tier):</b><br>${pin}</div>
   <p class="muted">${esc(v._note)}</p>`;
};
function yn2(b){return b?'<span class="ok">yes</span>':'<span class="muted">no</span>';}
PAGES.rollbackcenter=async()=>{
  const [c,reg,h]=await Promise.all([api('/api/rollback/center'),api('/api/rollback/reversers'),api('/api/rollback/history')]);
  const kinds=(reg.target_kinds||[]).map(k=>`<span class="muted" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(k)}</span>`).join('')||'<span class="err">none registered</span>';
  const hist=(h.changes||[]).slice(-25).reverse().map(r=>`<tr><td><code>${esc(r.id||'')}</code></td><td>${esc(r.action_class||'')}</td>
    <td>${esc(r.target_kind||'')}</td><td>${r.rolled_back?'<span class="ok">reverted</span>':'<span class="muted">live</span>'}</td>
    <td class="muted">${esc(r.ts||'')}</td></tr>`).join('');
  return `<h1>Rollback Center</h1>
   <p class="sub">Read-only view of the guardrail rollback engine. Reverting a change is an audited guardrail function (host cycle / operator) — never a button here.
   The engine guarantees: revert restores before-state &amp; is idempotent; review rejection triggers a revert; expired Class C auto-reverts (fail-closed); a reverser error freezes all automation.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Engine operational:</b> ${yn2(c.engine_operational)} · <b>Reversers:</b> <b class="acc">${c.reverser_count}</b>
     · <b>Changes recorded:</b> ${c.changes_recorded} · <b>Reverted:</b> ${c.changes_rolled_back}
     · <b>Frozen:</b> ${c.frozen?'<span class="err">yes</span>':'<span class="muted">no</span>'}</div>
   <div class="panel"><b>Reverser registry</b> (the change kinds that are reversible — and thus eligible):<br>${kinds}
     <div class="muted" style="font-size:11px;margin-top:4px">A change kind with no registered reverser is irreversible and never eligible. A real Class C apply would register its own reverser at apply time.</div></div>
   <div class="panel"><b>Pending review windows:</b> ${c.pending_windows} · <b>Expired Class C (would auto-revert on next sweep):</b> ${(c.expired_pending_class_c||[]).length?('<span class="err">'+c.expired_pending_class_c.length+'</span>'):'<span class="ok">0</span>'}
     · rollback rate ${c.rollback_rate==null?'—':c.rollback_rate} · review-expiry rate ${c.review_expiry_rate==null?'—':c.review_expiry_rate}
     · <a class="btn" data-p="rollbackreversibility">Reversibility</a> <a class="btn" data-p="reviewexp">Review queue</a></div>
   <h2>Recent changes (most recent first)</h2>
   <div class="panel"><table><thead><tr><th>Change</th><th>Class</th><th>Target kind</th><th>State</th><th>When (UTC)</th></tr></thead>
   <tbody>${hist||'<tr><td colspan=5 class="muted">No changes recorded.</td></tr>'}</tbody></table>
   <p class="muted">${esc(c._note)}</p></div>`;
};
PAGES.rollbackreversibility=async()=>{
  const d=await api('/api/rollback/reversibility');
  const rows=(d.changes||[]).map(r=>`<tr><td><code>${esc(r.id||'')}</code></td><td>${esc(r.target_kind||'')}</td>
    <td>${r.rolled_back?'<span class="muted">rolled back</span>':(r.reversible_now?'<span class="ok">reversible</span>':'<span class="err">irreversible</span>')}</td>
    <td class="muted">${esc(r.reason||'')}</td></tr>`).join('');
  return `<h1>Reversibility</h1><p class="sub">Per recorded change: can it be reverted right now? A pending change with no registered reverser is irreversible — a guardrail failure if it were ever applied.</p>${banner()}
   <div class="panel"><a class="btn" data-p="rollbackcenter">← Rollback Center</a> · <b>${d.count}</b> change(s) · <b>irreversible pending:</b> ${(d.irreversible_pending||[]).length?('<span class="err">'+d.irreversible_pending.length+'</span>'):'<span class="ok">0</span>'}</div>
   <div class="panel"><table><thead><tr><th>Change</th><th>Target kind</th><th>Reversible now</th><th>Reason</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=4 class="muted">No changes recorded.</td></tr>'}</tbody></table></div>`;
};
PAGES.trustdecay=async()=>{
  const [st,ov]=await Promise.all([api('/api/trust/status'),api('/api/trust/overview')]);
  const tcell=t=>`<span class="${(t!=null&&t>=st.min_trust)?'ok':'err'}"><b>${t==null?'—':t.toFixed(2)}</b></span>`;
  const rows=(ov.sites||[]).map(r=>`<tr><td>${esc(r.site||'')}</td>
    <td>${tcell(r.trust)}</td><td class="muted">${r.signal==null?'—':r.signal.toFixed(2)}</td>
    <td>${r.trust_eligible?'<span class="ok">yes</span>':'<span class="err">no</span>'}</td>
    <td class="muted">${esc(r.raised_by||'')}</td>
    <td><a class="btn" data-dl="${esc(r.site)}" data-p="trustsite">detail</a></td></tr>`).join('');
  return `<h1>Trust Decay</h1>
   <p class="sub">Per-site trust with automatic decay. Trust may only ever DECREASE automatically — adverse signals lower it; favourable signals never raise it.
   The only way trust goes back up is an explicit human restore (a governance action). Below the minimum, a site is not eligible.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Minimum trust:</b> <b class="acc">${st.min_trust}</b> · <b>Baseline (unseen):</b> ${st.baseline_trust}
     · <b>Sites below minimum:</b> ${st.below_min_count?('<span class="err">'+st.below_min_count+'</span>'):'<span class="ok">0</span>'}
     · <b>Frozen:</b> ${st.frozen?'<span class="err">yes</span>':'<span class="muted">no</span>'}
     <div class="muted" style="font-size:11px;margin-top:4px">Decay (host-scheduled) lowers stored trust toward the current signal; it never raises it. A site decayed below the minimum stays ineligible until a human restores trust — even if its evidence later looks good.</div></div>
   <h2>Per-site trust</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Trust (stored)</th><th>Signal (now)</th><th>Eligible</th><th>Last restored by</th><th></th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No sites configured in this environment.</td></tr>'}</tbody></table>
   <p class="muted">${esc(ov._note)}</p></div>`;
};
PAGES.trustsite=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Site Trust</h1>${banner()}<div class="panel muted">Open from the Trust Decay table.</div>`;
  const d=await api('/api/trust/site?site='+encodeURIComponent(site));
  return `<h1>Site Trust — ${esc(d.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="trustdecay">← Trust Decay</a></div>
   <div class="panel"><b>Stored trust:</b> <b class="${(d.trust!=null&&d.trust>=d.min_trust)?'ok':'err'}">${d.trust==null?'—':d.trust.toFixed(2)}</b> (min ${d.min_trust})
     · <b>Current signal:</b> ${d.signal==null?'—':d.signal.toFixed(2)} · <b>Would decay to:</b> ${d.would_decay_to==null?'—':d.would_decay_to.toFixed(2)}
     · <b>Eligible:</b> ${d.trust_eligible?'<span class="ok">yes</span>':'<span class="err">no</span>'}</div>
   <div class="panel"><b>Last human restore:</b> ${d.raised_by?(esc(d.raised_by)+' @ '+esc(d.raised_at||'')):'<span class="muted">never (auto-decay only)</span>'}
     · updated ${esc(d.updated_at||'—')}</div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.validationops=async()=>{
  const [st,ov]=await Promise.all([api('/api/validation/status'),api('/api/validation/overview')]);
  const badge=s=>({current:'<span class="ok">current</span>',due_soon:'<span class="acc">due soon</span>',overdue:'<span class="err">overdue</span>',never:'<span class="muted">never</span>'}[s]||esc(s));
  const rows=(ov.sites||[]).map(r=>`<tr><td>${esc(r.site||'')}</td><td>${badge(r.status)}</td>
    <td class="muted">${r.age_days==null?'—':r.age_days+'d'}</td><td>${r.held_out_count}</td>
    <td class="muted">${esc((r.recommended_date||'').slice(0,10)||'—')}</td>
    <td><a class="btn" data-dl="${esc(r.site)}" data-p="validationsite">detail</a></td></tr>`).join('');
  return `<h1>Validation Ops</h1>
   <p class="sub">Advisory re-validation scheduling for held-out evidence. Flags a site <b>due soon</b> after ${st.interval_days} days — before its evidence goes stale at the ${st.fresh_floor_days}-day freshness floor — and <b>overdue</b> once evidence is already stale (and the site has therefore lost eligibility).</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Interval:</b> ${st.interval_days}d · <b>Freshness floor:</b> ${st.fresh_floor_days}d
     · <b>Due soon:</b> ${st.due_soon_count?('<span class="acc">'+st.due_soon_count+'</span>'):'0'}
     · <b>Overdue:</b> ${st.overdue_count?('<span class="err">'+st.overdue_count+'</span>'):'<span class="ok">0</span>'}
     · <b>Never validated:</b> ${st.never_count}
     <div class="muted" style="font-size:11px;margin-top:4px">Advisory only. Re-validation (re-capturing held-out evidence and re-running the oracle) is an operator/host action — this view never captures or logs in.</div></div>
   <h2>Per-site schedule</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Status</th><th>Evidence age</th><th>Held-out</th><th>Re-validate by</th><th></th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No sites configured in this environment.</td></tr>'}</tbody></table>
   <p class="muted">${esc(ov._note)}</p></div>`;
};
PAGES.validationsite=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Site Validation</h1>${banner()}<div class="panel muted">Open from the Validation Ops table.</div>`;
  const d=await api('/api/validation/site?site='+encodeURIComponent(site));
  const badge={current:'<span class="ok">current</span>',due_soon:'<span class="acc">due soon</span>',overdue:'<span class="err">overdue</span>',never:'<span class="muted">never</span>'}[d.status]||esc(d.status);
  return `<h1>Site Validation — ${esc(d.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="validationops">← Validation Ops</a></div>
   <div class="panel"><b>Status:</b> ${badge} · <b>Evidence age:</b> ${d.age_days==null?'—':d.age_days+' days'} · <b>Held-out captures:</b> ${d.held_out_count}
     · <b>Designated:</b> ${esc((d.designated_at||'').slice(0,10)||'never')}</div>
   <div class="panel"><b>Recommended re-validation by:</b> ${esc((d.recommended_date||'').slice(0,10)||'— (designate held-out first)')}
     · interval ${d.interval_days}d · freshness floor ${d.fresh_floor_days}d</div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.impactanalysis=async()=>{
  const [st,ov]=await Promise.all([api('/api/impact/status'),api('/api/impact/overview')]);
  const rows=(ov.sites||[]).map(r=>`<tr><td>${esc(r.site||'')}</td>
    <td>${r.reversible?'<span class="ok">yes</span>':'<span class="err">no</span>'}</td>
    <td>tier ${r.oracle_tier}</td><td class="muted">${r.trust==null?'—':r.trust.toFixed(2)}</td>
    <td>${r.evidence_qualified?'<span class="ok">yes</span>':'<span class="muted">no</span>'}</td>
    <td>${r.safe_to_consider?'<span class="ok">would clear</span>':'<span class="muted">blocked</span>'}</td>
    <td><a class="btn" data-dl="${esc(r.site)}" data-p="impactsite">analyze</a></td></tr>`).join('');
  return `<h1>Impact Analysis</h1>
   <p class="sub">Read-only analysis of a single proposed change: blast radius, reversibility, pinned-target, trust and oracle tier. <b>No family-wide promotion</b> — a change touching more than one site is flagged as out of scope. Even when a change <i>would clear</i> every gate, automation is not enabled: participation stays Approve-each with no apply path.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Sites analysed:</b> ${st.site_count} · <b>Would clear all gates:</b> ${st.safe_to_consider_count}
     · <b>Participation-eligible anywhere:</b> ${st.any_participation_eligible?'<span class="err">yes</span>':'<span class="ok">no (no apply path)</span>'}
     <div class="muted" style="font-size:11px;margin-top:4px">Each row analyses a benign reversible probe change for that site. 'Would clear' means the reversibility / pinned / evidence / blast-radius gates pass — it does not authorize anything.</div></div>
   <h2>Per-site (benign probe)</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Reversible</th><th>Oracle</th><th>Trust</th><th>Evidence-qualified</th><th>Gates</th><th></th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No sites configured in this environment.</td></tr>'}</tbody></table>
   <p class="muted">${esc(ov._note)}</p></div>`;
};
PAGES.impactsite=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Change Impact</h1>${banner()}<div class="panel muted">Open from the Impact Analysis table.</div>`;
  const d=await api('/api/impact/analyze?target_kind=staging_json&site='+encodeURIComponent(site));
  const concerns=(d.concerns||[]).map(c=>`<li class="muted">${esc(c)}</li>`).join('')||'<li class="ok">no blocking concerns for this probe</li>';
  return `<h1>Change Impact — ${esc(d.site||'')}</h1><p class="sub">Benign reversible probe (target_kind staging_json).</p>${banner()}
   <div class="panel"><a class="btn" data-p="impactanalysis">← Impact Analysis</a></div>
   <div class="panel"><b>Blast radius:</b> ${d.blast_radius} site(s) · <b>Family-wide:</b> ${d.family_wide?'<span class="err">yes</span>':'<span class="ok">no</span>'}
     · <b>Reversible:</b> ${d.reversible?'<span class="ok">yes</span>':'<span class="err">no</span>'} · <b>Touches pinned:</b> ${d.touches_pinned?'<span class="err">yes</span>':'<span class="ok">no</span>'}</div>
   <div class="panel"><b>Oracle tier:</b> ${d.oracle_tier} (${esc(d.oracle_tier_name||'')}) · <b>Trust:</b> ${d.trust==null?'—':d.trust.toFixed(2)} · <b>Evidence-qualified:</b> ${d.evidence_qualified?'<span class="ok">yes</span>':'<span class="muted">no</span>'}
     · <b>Participation-eligible:</b> ${d.participation_eligible?'<span class="err">yes</span>':'<span class="ok">no</span>'}</div>
   <div class="panel"><b>Concerns:</b><ul>${concerns}</ul></div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.promotionactivity=async()=>{
  const [st,ac]=await Promise.all([api('/api/promotion/status'),api('/api/promotion/activity')]);
  const entries=(ac.entries||[]).slice().reverse();
  const rows=entries.map(e=>`<tr><td class="muted">${esc((e.ts||'').slice(0,19).replace('T',' '))}</td>
    <td><a class="btn" data-dl="${esc(e.site||'')}" data-p="promotionsite">${esc(e.site||'')}</a></td>
    <td>${esc(e.field||'')}</td>
    <td class="muted">${esc(String(e.before))} → <b>${esc(String(e.after))}</b></td>
    <td class="muted">${esc(e.by||'')}</td><td class="muted">${esc(e.reason||'')}</td></tr>`).join('');
  return `<h1>Promotion Activity</h1>
   <p class="sub">Append-only audit of governance-state <b>transitions</b> — a site gaining or losing evidence-qualification, trust crossing the floor, validation going overdue. It ties together eligibility, trust, and validation movement. Nothing here was <i>applied</i>: these are governance transitions, and participation never becomes eligible (no apply path).</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Total transitions:</b> ${st.total_transitions} · <b>Tracked fields:</b> ${esc((st.tracked_fields||[]).join(', '))}
     · <b>Participation ever eligible:</b> <span class="ok">no (no apply path)</span>
     <div class="muted" style="font-size:11px;margin-top:4px">The scan that records transitions is host-scheduled (cron/CLI), never a button here. This view is read-only.</div></div>
   <h2>Transitions (newest first)</h2>
   <div class="panel"><table><thead><tr><th>When</th><th>Site</th><th>Field</th><th>Change</th><th>By</th><th>Reason</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No transitions recorded yet. The host scan appends here as governance state changes.</td></tr>'}</tbody></table>
   <p class="muted">${esc(ac._note)}</p></div>`;
};
PAGES.promotionsite=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Site Activity</h1>${banner()}<div class="panel muted">Open from the Promotion Activity log.</div>`;
  const d=await api('/api/promotion/site?site='+encodeURIComponent(site));
  const entries=(d.entries||[]).slice().reverse();
  const rows=entries.map(e=>`<tr><td class="muted">${esc((e.ts||'').slice(0,19).replace('T',' '))}</td>
    <td>${esc(e.field||'')}</td><td class="muted">${esc(String(e.before))} → <b>${esc(String(e.after))}</b></td>
    <td class="muted">${esc(e.by||'')}</td><td class="muted">${esc(e.reason||'')}</td></tr>`).join('');
  return `<h1>Site Activity — ${esc(d.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="promotionactivity">← Promotion Activity</a> · <b>${d.count}</b> transition(s)</div>
   <div class="panel"><table><thead><tr><th>When</th><th>Field</th><th>Change</th><th>By</th><th>Reason</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No transitions for this site.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.stagingcandidates=async()=>{
  const [st,c]=await Promise.all([api('/api/staging/status'),api('/api/staging/candidates')]);
  const rows=(c.candidates||[]).map(r=>`<tr><td><a class="btn" data-dl="${esc(r.site||'')}" data-p="stagingcandidate">${esc(r.site||'')}</a></td>
    <td>tier ${r.oracle_tier}</td><td class="muted">${r.trust==null?'—':Number(r.trust).toFixed(2)}</td>
    <td>${r.behavioral_unchanged?'<span class="ok">unchanged</span>':'<span class="err">DIFFERS</span>'}</td>
    <td class="muted">${esc((r.deadline||'').slice(0,16).replace('T',' ')||'—')}</td></tr>`).join('');
  return `<h1>Staged Candidates</h1>
   <p class="sub">v1 autonomy wire: the system maintains a reversible, per-site <b>staged config candidate</b> when its evidence qualifies — annotation-only, with behavioral fields a credential-redacted copy of live. Nothing here is applied to production. Promotion to live config is manual; silence reverts at the deadline; reject reverts immediately; accept blesses without promoting.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--green)">
     <b>Pending:</b> ${st.pending_count} · <b>Eligible sites:</b> ${st.eligible_sites} · <b>Review window:</b> ${st.review_window_hours}h · <b>Next deadline:</b> ${esc((st.next_deadline||'').slice(0,16).replace('T',' ')||'—')}
     <div class="muted" style="font-size:11px;margin-top:4px">The maintenance loop and the fail-closed sweep are host-scheduled, never buttons here. Accept/reject uses the existing audited review path. participation_eligible stays False — this is a separate, weaker authority (staged candidates only).</div></div>
   <h2>Pending candidates</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Oracle</th><th>Trust</th><th>Behavioral</th><th>Reverts by</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No staged candidates. (Empty oracle/trust stores qualify nothing — expected until real evidence accrues.)</td></tr>'}</tbody></table>
   <p class="muted">${esc(c._note)}</p></div>`;
};
PAGES.stagingcandidate=async()=>{
  const site=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!site) return `<h1>Staged Candidate</h1>${banner()}<div class="panel muted">Open from the Staged Candidates table.</div>`;
  const d=await api('/api/staging/candidate?site='+encodeURIComponent(site));
  if(!d.exists) return `<h1>Staged Candidate — ${esc(site)}</h1>${banner()}<div class="panel"><a class="btn" data-p="stagingcandidates">← Staged Candidates</a></div><div class="panel muted">No staged candidate for this site.</div>`;
  const ev=d.evidence||{};
  return `<h1>Staged Candidate — ${esc(d.site||'')}</h1>${banner()}
   <div class="panel"><a class="btn" data-p="stagingcandidates">← Staged Candidates</a></div>
   <div class="panel"><b>Behavioral vs live:</b> ${d.behavioral_unchanged?'<span class="ok">unchanged (byte-identical, credential-redacted)</span>':'<span class="err">DIFFERS — do not promote</span>'}
     · <b>Reverts by:</b> ${esc((d.deadline||'').slice(0,16).replace('T',' ')||'—')} · <b>change_id:</b> <span class="muted">${esc(d.change_id||'—')}</span></div>
   <div class="panel"><b>Evidence (system-authored):</b> tier ${ev.oracle_tier} (${esc(ev.tier_name||'')}) · held-out ${ev.held_out_count} · qualified ${ev.evidence_qualified?'<span class="ok">yes</span>':'no'} · trust ${ev.trust==null?'—':Number(ev.trust).toFixed(2)} · validated ${esc((ev.validated_at||'').slice(0,16).replace('T',' '))}</div>
   <div class="panel"><b>Behavioral fields embedded (non-secret):</b> <span class="muted">${esc((d.behavioral_keys||[]).join(', ')||'—')}</span><br><b>Redacted (secret/PII, never embedded):</b> <span class="muted">${esc((d.redacted_keys||[]).join(', ')||'—')}</span></div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.authority=async()=>{
  const [st,gr,pn,ks]=await Promise.all([api('/api/authority/status'),api('/api/authority/grants'),api('/api/authority/pending'),api('/api/authority/kinds')]);
  const grows=(gr.grants||[]).map(g=>`<tr><td>${esc(g.site||'')}</td><td><code>${esc(g.kind||'')}</code></td><td>${g.suspended?'<span class="err">suspended</span>':'<span class="ok">active</span>'}</td>
    <td class="muted">${esc(g.granted_by||'')}</td><td>tier ${g.oracle_tier==null?'—':g.oracle_tier}</td><td class="muted">${g.trust==null?'—':Number(g.trust).toFixed(2)}</td>
    <td>${g.participation_eligible?'<span class="ok">eligible</span>':'<span class="muted">no</span>'}</td><td class="muted">${esc(g.suspend_reason||'')}</td></tr>`).join('');
  const prows=(pn.pending||[]).map(p=>`<tr><td><a class="btn" data-dl="${esc(p.change_id||'')}" data-p="authoritychange">${esc(p.site||'')}</a></td>
    <td><code>${esc(p.kind||'')}</code></td><td>${(p.changed_keys||[]).map(k=>`<span class="acc">${esc(k)}</span>`).join(' ')||'—'}</td>
    <td class="muted">${esc((p.deadline||'').slice(0,16).replace('T',' ')||'—')}</td></tr>`).join('');
  const krows=(ks.kinds||[]).map(k=>`<tr><td><code>${esc(k.kind||'')}</code></td><td>${esc(k.action_class||'')}</td><td>${k.has_reverser?'<span class="ok">yes</span>':'<span class="err">no</span>'}</td><td>${k.has_validator?'yes':'—'}</td></tr>`).join('');
  return `<h1>Authority</h1>
   <p class="sub">The operator-controlled Class-C apply surface. For an explicitly <b>granted</b> (site × kind) with tier-3 corroborated evidence, Class C at auto, and a registered reverser, the system applies a reversible change under a fail-closed window. Granting a site for one kind never grants another. It never changes credentials or login fields and never authors selectors. Reject reverts now; silence reverts at the deadline; validation failure already reverted; accept blesses without further change.</p>${banner()}
   <div class="panel" style="border-left:3px solid var(--primary)">
     <b>Pending:</b> ${st.pending_count} · <b>Grants:</b> ${st.grants_active} active / ${st.grants_suspended} suspended · <b>Kinds:</b> ${(st.kinds||[]).map(esc).join(', ')||'—'} · <b>Class C:</b> ${st.class_c_allowed?'allowed':'<span class="muted">approve-each/frozen</span>'} · <b>Window:</b> ${st.review_window_hours}h
     <div class="muted" style="font-size:11px;margin-top:4px">Grants are issued <b>human-only</b> from the CLI (<code>python -m tools.autonomy_grant grant &lt;site&gt; --kind &lt;kind&gt; --by you --reason …</code>) — there is no grant button. The system may automatically <b>suspend</b> a grant (trust↓, tier&lt;3, freeze, expiry) but never grant or un-suspend.</div></div>
   <h2>Grants (site × kind)</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Kind</th><th>State</th><th>By</th><th>Oracle</th><th>Trust</th><th>Eligible</th><th>Suspend reason</th></tr></thead>
   <tbody>${grows||'<tr><td colspan=8 class="muted">No grants. Without a grant no (site, kind) is participation_eligible — nothing applies (build-dark).</td></tr>'}</tbody></table></div>
   <h2>Pending changes (all kinds)</h2>
   <div class="panel"><table><thead><tr><th>Site</th><th>Kind</th><th>Changed</th><th>Reverts by</th></tr></thead>
   <tbody>${prows||'<tr><td colspan=4 class="muted">No pending changes.</td></tr>'}</tbody></table></div>
   <h2>Registered kinds</h2>
   <div class="panel"><table><thead><tr><th>Kind</th><th>Class</th><th>Reverser</th><th>Validator</th></tr></thead>
   <tbody>${krows||'<tr><td colspan=4 class="muted">No kinds registered.</td></tr>'}</tbody></table>
   <p class="muted">${esc(st._note)}</p></div>`;
};
PAGES.authoritychange=async()=>{
  const cid=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!cid) return `<h1>Change</h1>${banner()}<div class="panel muted">Open from the Authority table.</div>`;
  const d=await api('/api/authority/change?id='+encodeURIComponent(cid));
  if(!d.exists) return `<h1>Change</h1>${banner()}<div class="panel"><a class="btn" data-p="authority">← Authority</a></div><div class="panel muted">No such change.</div>`;
  const j=o=>esc(JSON.stringify(o,null,2));
  return `<h1>Change — ${esc(d.site||'')} · <code>${esc(d.kind||'')}</code></h1>${banner()}
   <div class="panel"><a class="btn" data-p="authority">← Authority</a> · <b>change_id:</b> <span class="muted">${esc(d.change_id)}</span></div>
   <div class="panel"><b>Scope:</b> only this kind's fields change. Credentials and login fields are never part of a Class-C change.</div>
   <div class="panel"><b>After (applied):</b><pre class="muted" style="white-space:pre-wrap;font-size:11px">${j(d.after)}</pre></div>
   <div class="panel"><b>Rollback preview (prior state a revert restores):</b><pre class="muted" style="white-space:pre-wrap;font-size:11px">${j(d.rollback_preview)}</pre></div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.reviewexp=async()=>{
  const d=await api('/api/review/dashboard');
  const rows=(d.pending||[]).map(p=>`<tr><td><code>${esc(p.change_id||'')}</code></td><td>${esc(p.action_class||'')}</td>
    <td>${esc(p.site||'')}</td><td class="muted">${esc(p.deadline||'(no deadline — Class B)')}</td>
    <td><a class="btn" data-dl="${esc(p.change_id)}" data-p="reviewevidence">evidence</a>
        <a class="btn" data-dl="${esc(p.change_id)}" data-p="reviewrollback">rollback preview</a></td></tr>`).join('');
  return `<h1>Review</h1>
   <p class="sub">Informed human review of correctness-critical changes. These surfaces inform your Approve/Reject decision —
   the decision is committed via the existing audited path, not from here. Class C changes auto-revert if their window expires unreviewed.</p>${banner()}
   <div class="panel">Outstanding: <b>${d.pending_count}</b> · backlog ${(d.backlog||{}).outstanding}/${(d.backlog||{}).cap} · in-flight sites: ${(d.inflight_sites||[]).map(esc).join(', ')||'none'}
     · <a class="btn" data-p="reviewaudit">Decision audit</a> <a class="btn" data-p="governance">← Governance</a></div>
   <div class="panel"><table><thead><tr><th>Change</th><th>Class</th><th>Site</th><th>Deadline (UTC)</th><th>Review</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No changes awaiting review.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.reviewevidence=async()=>{
  const cid=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!cid) return `<h1>Evidence Chain</h1>${banner()}<div class="panel muted">Open from the Review list to pick a change.</div>`;
  const ec=await api('/api/review/evidence?change_id='+encodeURIComponent(cid));
  if(!ec.ok) return `<h1>Evidence Chain</h1>${banner()}<div class="panel err">${esc(ec.error||'not found')}</div>`;
  const snap=ec.decision_snapshot||{};
  const ev=ec.site_evidence||{};
  return `<h1>Evidence Chain</h1><p class="sub">The reconstructable "why" behind a change.</p>${banner()}
   <div class="panel"><a class="btn" data-p="reviewexp">← Review</a> <a class="btn" data-dl="${esc(cid)}" data-p="reviewrollback">rollback preview</a></div>
   <div class="panel"><b>Change</b> <code>${esc(ec.change_id)}</code> · class ${esc(ec.action_class)} · target ${esc((ec.target||{}).kind)}:${esc((ec.target||{}).ref)} · ${ec.rolled_back?'<span class="muted">rolled back</span>':'<span class="ok">active</span>'}</div>
   <div class="panel"><b>Decision snapshot</b><br>${snap.policy_hash?(`policy v${snap.policy_version} · hash <code>${esc((snap.policy_hash||'').slice(0,16))}…</code><br>scores: ${esc(JSON.stringify((snap.decision||{}).scores_used||{}))} · thresholds: ${esc(JSON.stringify((snap.decision||{}).thresholds_used||{}))}`):('<span class="muted">'+esc(snap.note||'none')+'</span>')}</div>
   <div class="panel"><b>Diff</b><br><code>${esc(JSON.stringify(ec.diff||{}))}</code></div>
   <div class="panel"><b>Review window</b><br>${ec.review&&ec.review.deadline?('deadline '+esc(ec.review.deadline)+(ec.review.reviewed?(' · '+esc(ec.review.decision)):' · <span class="acc">pending (fail-closed)</span>')):('<span class="muted">'+esc((ec.review||{}).note||'')+'</span>')}</div>
   <div class="panel"><b>Site evidence</b> (${esc(ev.site||'n/a')})<br><span class="muted">concerns: ${esc(JSON.stringify(ev.open_concerns||ev.note||'—'))}</span></div>
   <p class="muted">${esc(ec._note)}</p>`;
};
PAGES.reviewrollback=async()=>{
  const cid=(window.__deeplink&&window.__deeplink.__s)||'';
  if(!cid) return `<h1>Rollback Preview</h1>${banner()}<div class="panel muted">Open from the Review list.</div>`;
  const rp=await api('/api/review/rollback-preview?change_id='+encodeURIComponent(cid));
  if(!rp.ok) return `<h1>Rollback Preview</h1>${banner()}<div class="panel err">${esc(rp.error||'not found')}</div>`;
  return `<h1>Rollback Preview</h1><p class="sub">What reverting would restore — nothing is executed here.</p>${banner()}
   <div class="panel"><a class="btn" data-dl="${esc(cid)}" data-p="reviewevidence">← Evidence</a> <a class="btn" data-p="reviewexp">Review</a></div>
   <div class="panel"><b>Reversible:</b> ${rp.reversible?'<span class="ok">yes</span>':'<span class="err">no</span>'} ${rp.already_rolled_back?'· <span class="muted">already rolled back</span>':''}</div>
   <div class="panel"><b>Currently applied</b><br><code>${esc(JSON.stringify(rp.currently_applied||{}))}</code></div>
   <div class="panel"><b>Would restore</b><br><code>${esc(JSON.stringify(rp.would_restore||{}))}</code></div>
   <p class="muted">${esc(rp._note)}</p>`;
};
PAGES.reviewaudit=async()=>{
  const d=await api('/api/review/audit');
  const rows=(d.events||[]).map(e=>`<tr><td class="muted">${esc(e.ts||'')}</td><td>${esc(e.source||'')}</td><td>${esc(e.kind||'')}</td>
    <td class="muted">${esc(e.detail||'')}</td><td>${esc(e.by||'')}</td></tr>`).join('');
  return `<h1>Decision Audit</h1><p class="sub">One timeline across policy edits, Class B housekeeping, and guardrail activity.</p>${banner()}
   <div class="panel"><a class="btn" data-p="reviewexp">← Review</a> · <b>${d.total}</b> event(s)</div>
   <div class="panel"><table><thead><tr><th>When (UTC)</th><th>Source</th><th>Kind</th><th>Detail</th><th>By</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No decisions recorded yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.guardrails=async()=>{
  const d=await api('/api/guardrails/status');
  const g=d.guardrails||{}; const cfg=d.config||{}; const m=d.throttle_metrics||{};
  const ks=d.kill_switch||{};
  const gbadge=(k)=>`<span class="${g[k]?'ok':'err'}" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(k)} ${g[k]?'✓':'✗'}</span>`;
  const gall=Object.keys(g).sort().map(gbadge).join('');
  const cAuto=d.class_c_auto_possible;
  const c3=d.class_c_level3||{};
  return `<h1>Guardrails</h1>
   <p class="sub">The safety apparatus for Class C autonomy — rollback, caps, fail-closed review windows, self-throttle, and the guardrail-failure branch.
   Built, but Class C auto stays OFF until the correctness oracle (Phase E) exists.</p>${banner()}
   <div class="panel" style="border-left:3px solid ${cAuto?'var(--red)':'var(--green)'}">
     <b>Class C auto possible right now:</b> <b class="${cAuto?'err':'ok'}">${cAuto?'YES':'NO'}</b>
     <span class="muted">${cAuto?'':('— '+esc(c3.reason||'')+(c3.missing_guardrails?(': '+c3.missing_guardrails.join(', ')):''))}</span>
     <div class="muted" style="font-size:11px;margin-top:4px">Class C remains at Approve-each by default; even once the oracle lands, enabling auto is per-site and deliberate.</div>
   </div>
   <div class="panel"><b>Guardrails (8):</b><br>${gall}</div>
   <div class="panel" style="${ks.frozen?'border-left:3px solid var(--red)':''}"><b>Kill switch:</b> ${ks.frozen?'<span class="err">FROZEN</span> '+esc(ks.reason||''):'<span class="ok">armed</span>'}</div>
   <div class="panel"><b>Caps:</b> backlog cap <b>${cfg.backlog_cap}</b> (outstanding <b>${(d.backlog||{}).outstanding}</b>) · max in-flight sites <b>${cfg.max_inflight_sites}</b> (now: ${(d.inflight_sites||[]).map(esc).join(', ')||'none'}) · review window <b>${cfg.review_window_hours}h</b> (fail-closed for Class C)</div>
   <div class="panel"><b>Self-throttle metrics:</b> rollback rate <b>${m.rollback_rate}</b> (demote ≥ ${cfg.throttle_rollback_rate}) · review-expiry rate <b>${m.review_expiry_rate}</b> (demote ≥ ${cfg.throttle_expiry_rate}) · oracle-disagreement rate <b>${m.oracle_disagreement_rate==null?'— (needs Phase E)':m.oracle_disagreement_rate}</b>
     <div class="muted" style="font-size:11px;margin-top:4px">On breach, Class C is automatically demoted to Approve-each (lower-only) and an alert is recorded. Guardrail failure ⇒ freeze-and-alert.</div></div>
   <div class="panel"><b>Recorded changes:</b> ${d.recorded_changes} · <b>Pending reviews:</b> ${d.pending_reviews}
     · <a class="btn" data-p="grchanges">Rollback ledger</a> <a class="btn" data-p="grpending">Pending windows</a> <a class="btn" data-p="governance">← Governance</a></div>
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.grchanges=async()=>{
  const d=await api('/api/guardrails/changes');
  const rows=(d.changes||[]).slice().reverse().map(c=>`<tr><td><code>${esc(c.id||'')}</code></td><td class="muted">${esc(c.ts||'')}</td>
    <td>${esc(c.action_class||'')}</td><td>${esc(c.target_kind||'')}</td>
    <td>${c.rolled_back?'<span class="muted">rolled back</span>':'<span class="ok">applied</span>'}</td></tr>`).join('');
  return `<h1>Rollback Ledger</h1><p class="sub">Recorded changes (before/after/diff) and their rollback state. Recording is bookkeeping; nothing is applied to live config here.</p>${banner()}
   <div class="panel"><a class="btn" data-p="guardrails">← Guardrails</a></div>
   <div class="panel"><table><thead><tr><th>Change</th><th>When (UTC)</th><th>Class</th><th>Target</th><th>State</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No changes recorded.</td></tr>'}</tbody></table></div>`;
};
PAGES.grpending=async()=>{
  const d=await api('/api/guardrails/pending');
  const rows=(d.pending||[]).map(p=>`<tr><td><code>${esc(p.change_id||'')}</code></td><td>${esc(p.action_class||'')}</td>
    <td>${esc(p.site||'')}</td><td class="muted">${esc(p.deadline||'(no deadline — Class B)')}</td><td>${p.reviewed?esc(p.decision||''):'<span class="acc">pending</span>'}</td></tr>`).join('');
  return `<h1>Pending Review Windows</h1><p class="sub">Outstanding unreviewed auto-changes. Class C is FAIL-CLOSED — expired-unreviewed changes auto-revert on sweep. Class B may stay provisional.</p>${banner()}
   <div class="panel">Backlog: <b>${(d.backlog||{}).outstanding}</b> / ${(d.backlog||{}).cap} · in-flight sites: ${(d.inflight_sites||[]).map(esc).join(', ')||'none'} · <a class="btn" data-p="guardrails">← Guardrails</a></div>
   <div class="panel"><table><thead><tr><th>Change</th><th>Class</th><th>Site</th><th>Deadline (UTC)</th><th>Status</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=5 class="muted">No pending auto-changes.</td></tr>'}</tbody></table></div>`;
};
PAGES.housekeeping=async()=>{
  const [st,pv]=await Promise.all([api('/api/housekeeping/status'),api('/api/housekeeping/preview')]);
  const ks=st.kill_switch||{};
  const ca=st.class_b_can_autonomously||{};
  const g=st.guardrails||{};
  const gbadge=(k)=>`<span class="${g[k]?'ok':'muted'}" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(k)} ${g[k]?'✓':'—'}</span>`;
  const res=(pv.results)||{};
  const card=(key,title)=>{
    const r=res[key]||{};
    let body='';
    if(key==='reorder_queue') body=`would change <b>${r.would_change||0}</b> queue position(s)`;
    else if(key==='generate_notifications') body=`would create <b>${r.would_create||0}</b> in-GUI alert(s)`;
    else if(key==='refresh_dashboard_cache') body=`would cache: ${esc(JSON.stringify(r.would_cache||{}).slice(0,80))}…`;
    else if(key==='generate_review_packet') body=`would include <b>${r.would_include||0}</b> pending review(s)`;
    return `<div class="panel" style="flex:1;min-width:240px"><b>${esc(title)}</b><div class="muted" style="margin-top:6px">${body}</div></div>`;
  };
  return `<h1>Class B Housekeeping</h1>
   <p class="sub">Reversible operational housekeeping — the safest automation. Writes only regenerable state, launches no external activity,
   touches nothing correctness-critical. Every action checks the kill switch, is logged, and is reversible.</p>${banner()}
   <div class="panel" style="${ca.allowed?'border-left:3px solid var(--primary)':'border-left:3px solid var(--green)'}">
     <b>Class B level:</b> <span class="ok">${esc(st.class_b_level)}</span>
     · <b>autonomous right now:</b> <b class="${ca.allowed?'acc':'ok'}">${ca.allowed?'YES':'no'}</b>
     <span class="muted">${ca.allowed?'':('— '+esc(ca.reason||''))}</span>
     <div class="muted" style="font-size:11px;margin-top:4px">Default is <b>suggest</b>: nothing runs autonomously until Class B is set to auto_with_guardrails.
     Set via <code>set_policy_level('B','auto_with_guardrails','operator','reason')</code>. Actions run via <code>run_housekeeping(...)</code>; undo via <code>reverse_action(id,'operator')</code>.</div>
   </div>
   <div class="panel" style="${ks.frozen?'border-left:3px solid var(--red)':''}"><b>Kill switch:</b> ${ks.frozen?'<span class="err">FROZEN</span>':'<span class="ok">armed</span>'} · <b>Guardrails:</b> ${gbadge('kill_switch')}${gbadge('action_logging')}${gbadge('reversibility')}
     · applied <b>${st.applied_count}</b> · reversed <b>${st.reversed_count}</b> · alerts <b>${st.notifications}</b> · packets <b>${st.review_packets}</b></div>
   <h2>Preview — what housekeeping would do now (applies nothing)</h2>
   <div style="display:flex;gap:12px;flex-wrap:wrap">
     ${card('reorder_queue','Reorder queue')}${card('generate_notifications','Notifications')}
     ${card('refresh_dashboard_cache','Dashboard cache')}${card('generate_review_packet','Review packet')}
   </div>
   <div class="panel"><a class="btn" data-p="hklog">View housekeeping log</a> <a class="btn" data-p="governance">← Governance</a></div>
   <p class="muted">${esc(st._note)}</p>`;
};
PAGES.hklog=async()=>{
  const d=await api('/api/housekeeping/log');
  const rows=(d.entries||[]).filter(e=>e.mode!=='marker').slice().reverse().map(e=>{
    const rev=e.reversed?'<span class="muted">reversed</span>':(e.reversible?'<span class="ok">reversible</span>':'—');
    const sk=e.skipped?'<span class="err">skipped</span>':esc(e.mode||'');
    return `<tr><td class="muted">${esc(e.ts||'')}</td><td>${esc(e.action||'')}</td><td>${sk}</td>
      <td class="muted">${esc(e.auth||'')}</td><td>${esc(e.by||'')}</td><td>${rev}</td>
      <td class="muted">${esc(e.reason||(e.changed!=null?('changed '+e.changed):'')||(e.created!=null?('created '+e.created):''))}</td></tr>`;
  }).join('');
  return `<h1>Housekeeping Log</h1><p class="sub">Append-only record of Class B actions. Skipped = kill switch was frozen.</p>${banner()}
   <div class="panel"><a class="btn" data-p="housekeeping">← Housekeeping</a></div>
   <div class="panel"><table><thead><tr><th>When (UTC)</th><th>Action</th><th>Mode</th><th>Auth</th><th>By</th><th>Reversible</th><th>Detail</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No housekeeping actions yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.governance=async()=>{
  const [m,st]=await Promise.all([api('/api/policy/matrix'),api('/api/policy/status')]);
  const ks=st.kill_switch||{};
  const lvlLabel={observe:'Observe',suggest:'Suggest',approve_each:'Approve-each',auto_with_guardrails:'Auto (guardrails)'};
  const rows=m.classes.map(c=>{
    const l3=c.level3||{};
    const avail=l3.available?'<span class="ok">available</span>':'<span class="muted">not available</span>';
    const why=l3.reason?esc(l3.reason):'';
    const miss=(l3.missing_guardrails||[]).map(esc).join(', ');
    const present=(c.level3_guardrails_present||[]).map(esc).join(', ')||'—';
    const reqd=(c.level3_guardrails_required||[]).map(esc).join(', ')||'n/a';
    return `<tr>
      <td><b>${esc(c.class)}</b> ${esc(c.name)}<div class="muted" style="font-size:11px">writes: ${esc(c.write_target)}</div></td>
      <td><span class="ok">${esc(lvlLabel[c.configured_level]||c.configured_level)}</span></td>
      <td class="muted">${esc(lvlLabel[c.selectable_ceiling]||c.selectable_ceiling)}</td>
      <td>${avail}<div class="muted" style="font-size:11px">${why}${miss?(' — missing: '+miss):''}</div></td>
      <td class="muted" style="font-size:11px">need: ${reqd}<br>have: ${present}</td></tr>`;
  }).join('');
  const pins=(m.pinned_approve_each||[]).map(x=>`<span class="muted" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(x.replace(/_/g,' '))}</span>`).join('');
  const gbuilt=(st.guardrails_built||[]).map(g=>`<span class="ok" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#1a2a1a">${esc(g)}</span>`).join('')||'<span class="muted">none</span>';
  const gpend=(st.guardrails_pending||[]).map(g=>`<span class="muted" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(g)}</span>`).join('')||'<span class="muted">none</span>';
  return `<h1>Autonomy Governance</h1>
   <p class="sub">The 2-D autonomy policy — action class (by write-target) × involvement level. Classes move independently.
   This is a read-only view; policy edits and the kill switch are audited governance actions, not web toggles.</p>${banner()}
   <div class="panel" style="${ks.frozen?'border-left:3px solid var(--red)':'border-left:3px solid var(--green)'}">
     <b>Kill switch:</b> ${ks.frozen?'<span class="err">FROZEN</span> — all automation halted':'<span class="ok">armed / not frozen</span>'}
     ${ks.by?`<span class="muted"> · by ${esc(ks.by)}${ks.reason?(' — '+esc(ks.reason)):''}</span>`:''}
     <div class="muted" style="font-size:11px;margin-top:4px">Independent of the policy file. Set via: <code>freeze('operator','reason')</code> / <code>unfreeze(...)</code>.</div>
   </div>
   <div class="panel">Policy model <b>${esc(m.policy_model)}</b> · version <b>${m.policy_version}</b> · hash <code>${esc((m.policy_hash||'').slice(0,16))}…</code>
     · any class autonomous: <b class="${st.any_class_autonomous?'err':'ok'}">${st.any_class_autonomous?'YES':'no'}</b>
     · decision snapshots: <b>${st.decision_snapshots}</b></div>
   <div class="panel"><table><thead><tr><th>Action class</th><th>Configured</th><th>Ceiling</th><th>Level 3 (auto)</th><th>Guardrails</th></tr></thead>
     <tbody>${rows}</tbody></table></div>
   <div class="panel"><b>Guardrails built:</b> ${gbuilt}<br><b>Guardrails pending (later phases):</b> ${gpend}</div>
   <div class="panel"><b>Permanently pinned at Approve-each</b> (never advance to auto): ${pins}</div>
   <div class="panel" style="display:flex;gap:12px;flex-wrap:wrap">
     <a class="btn" data-p="govaudit">View audit log</a>
     <a class="btn" data-p="govsnapshots">View decision snapshots</a>
   </div>
   <p class="muted">${esc(m._note)}</p>`;
};
PAGES.govaudit=async()=>{
  const d=await api('/api/policy/audit');
  const rows=(d.entries||[]).slice().reverse().map(e=>`<tr><td class="muted">${esc(e.ts||'')}</td><td>${esc(e.action||'')}</td>
    <td>${esc(e.class||'')}</td><td class="muted">${e.from!=null?esc(e.from)+' → '+esc(e.to):''}</td>
    <td>${esc(e.by||'')}</td><td class="muted">${esc(e.reason||'')}</td></tr>`).join('');
  return `<h1>Policy Audit Log</h1><p class="sub">Append-only record of policy edits and kill-switch changes.</p>${banner()}
   <div class="panel"><a class="btn" data-p="governance">← Governance</a></div>
   <div class="panel"><table><thead><tr><th>When (UTC)</th><th>Action</th><th>Class</th><th>Change</th><th>By</th><th>Reason</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No policy changes recorded yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.govsnapshots=async()=>{
  const d=await api('/api/policy/snapshots');
  const rows=(d.snapshots||[]).slice().reverse().map(s=>`<tr><td><code>${esc(s.id||'')}</code></td><td class="muted">${esc(s.ts||'')}</td>
    <td>${esc(s.action_class||'')}</td><td>${esc(s.action||'')}</td><td>${esc(s.site||'')}</td>
    <td class="muted">v${s.policy_version} · ${esc((s.policy_hash||'').slice(0,12))}…</td></tr>`).join('');
  return `<h1>Decision Snapshots</h1><p class="sub">Immutable record per proposed/autonomous change — inputs + the policy state in effect.
   Empty in Phase A: nothing produces decisions yet. The recorder is built and ready.</p>${banner()}
   <div class="panel"><a class="btn" data-p="governance">← Governance</a></div>
   <div class="panel"><table><thead><tr><th>ID</th><th>When (UTC)</th><th>Class</th><th>Action</th><th>Site</th><th>Policy</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No decisions recorded yet.</td></tr>'}</tbody></table></div>`;
};
PAGES.missioncontrol=async()=>{
  const d=await api('/api/template/mission-control');
  const na=d.needs_attention, hl=d.healthy, aw=d.active_work;
  const chip=(t,cls)=>`<span class="${cls||'muted'}" style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;background:#222">${esc(t)}</span>`;
  const list=(arr,cls)=>arr.length?arr.map(x=>chip(x,cls)).join(''):'<span class="muted">none</span>';
  const hd=(na.high_drift_sites||[]).map(h=>chip(`${h.site} (${h.events})`,'err')).join('')||'<span class="muted">none</span>';
  const nr=(na.not_ready_sites||[]).map(s=>chip(`${s.site} (${s.readiness})`,'err')).join('')||'<span class="muted">none</span>';
  const rt=(aw.running_tasks||[]).map(t=>chip(t.label)).join('')||'<span class="muted">none</span>';
  const r7=(aw.recent_drift_7d||[]).map(x=>chip(`${x.site} (${x.last_7d})`)).join('')||'<span class="muted">none</span>';
  const acts=(d.recommended_actions||[]).map(a=>{
    const icon={run_capture:'⦿',review_template:'✎',refresh_evidence:'↻',investigate_drift:'⚠'}[a.action]||'•';
    const pcol=a.priority===1?'err':a.priority===2?'muted':'muted';
    return `<tr><td>${icon} <b>${esc(a.action.replace(/_/g,' '))}</b></td><td>${esc(a.site)}</td><td class="muted">${esc(a.why)}</td><td><span class="${pcol}">P${a.priority}</span></td></tr>`;
  }).join('')||'<tr><td colspan=4 class="ok">Nothing recommended — all clear.</td></tr>';
  return `<h1>Operator Mission Control</h1>
   <p class="sub">One screen across all ${d.site_count} site(s) — rolled up from the template-intelligence stack + ops state. Read-only; nothing here acts.</p>${banner()}
   ${d.config_present?'':'<div class="panel"><span class="err">No sites_config.json in this environment.</span></div>'}
   <div style="display:flex;gap:16px;flex-wrap:wrap">
     <div class="panel" style="flex:1;min-width:320px;border-left:3px solid var(--red)">
       <h2>Needs Attention <span class="muted" style="font-weight:400">(${na.count})</span></h2>
       <div>Broken login templates: ${list(na.broken_login_templates,'err')}</div>
       <div>Broken video templates: ${list(na.broken_video_templates,'err')}</div>
       <div>High-drift sites: ${hd}</div>
       <div>Not ready: ${nr}</div>
       <div>Open reviews: <b>${na.open_reviews}</b> · Open debt: correction <b>${na.open_debt.correction!=null?na.open_debt.correction:'—'}</b>, validation <b>${na.open_debt.validation!=null?na.open_debt.validation:'—'}</b></div>
     </div>
     <div class="panel" style="flex:1;min-width:320px;border-left:3px solid var(--green)">
       <h2>Healthy <span class="muted" style="font-weight:400">(${hl.count})</span></h2>
       <div>Ready sites: ${list(hl.ready_sites.map(r=>r.site+' ('+r.readiness+')'),'ok')}</div>
       <div>Trusted templates: ${list(hl.trusted_templates,'ok')}</div>
       <div>Fresh evidence: ${list(hl.fresh_evidence,'ok')}</div>
     </div>
   </div>
   <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:16px">
     <div class="panel" style="flex:1;min-width:320px;border-left:3px solid var(--primary)">
       <h2>Active Work</h2>
       <div>Captures running: <b>${aw.captures_running}</b> ${rt}</div>
       <div>Review queue: <b>${aw.review_queue}</b></div>
       <div>Recent drift (7d): ${r7}</div>
     </div>
     <div class="panel" style="flex:1;min-width:320px;border-left:3px solid var(--primary)">
       <h2>Recommended Actions <span class="muted" style="font-weight:400">(suggestions — you decide)</span></h2>
       <table><thead><tr><th>Action</th><th>Site</th><th>Why</th><th>Pri</th></tr></thead><tbody>${acts}</tbody></table>
     </div>
   </div>
   <p class="muted" style="margin-top:12px">${esc(d._status)}</p>`;
};
PAGES.sitereadiness=async()=>{
  const d=await api('/api/template/site-readiness');
  const cap=(label,v)=>{const pct=Math.round((v||0)*100);const col=pct>=70?'var(--green)':pct>=40?'var(--primary)':'var(--red)';
    return `<span title="${label} ${pct}%" style="display:inline-block;width:22px;height:8px;background:${col};border-radius:2px;margin-right:2px"></span>`;};
  const rows=d.sites.map(s=>{
    const c=s.components;
    const bandcol=s.band==='ready'?'ok':s.band==='not_ready'?'err':'muted';
    const thin=s.thin_signals?` <span class="muted" title="thin signals (neutral 0.5): ${esc(s.thin_signals.join(', '))}">·thin</span>`:'';
    return `<tr><td>${esc(s.site||'(unnamed)')}</td>
      <td><b class="${bandcol}">${s.readiness}</b> <span class="${bandcol}">${esc(s.band)}</span>${thin}</td>
      <td>${cap('login health',c.login_health)}${cap('video health',c.video_health)}${cap('drift',c.drift)}${cap('evidence',c.evidence_freshness)}${cap('capture',c.capture_quality)}${cap('maturity',c.template_maturity)}${cap('review debt',c.review_debt)}</td>
      <td class="muted">L ${Math.round(c.login_health*100)} · V ${Math.round(c.video_health*100)} · D ${Math.round(c.drift*100)} · E ${Math.round(c.evidence_freshness*100)} · C ${Math.round(c.capture_quality*100)} · M ${Math.round(c.template_maturity*100)} · R ${Math.round(c.review_debt*100)}</td></tr>`;
  }).join('');
  return `<h1>Site Readiness</h1>
   <p class="sub">One number per site — can I trust this site today? A defined composite of login health, video health, drift,
   evidence freshness, capture quality, template maturity, and review debt. Weights and inputs shown; thin signals use a neutral 0.5.</p>${banner()}
   <div class="panel"><span class="ok">${d.ready} ready</span> · <span class="muted">${d.caution} caution</span> · <span class="err">${d.not_ready} not ready</span> of ${d.site_count}.
   ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Readiness</th><th>Components (L V D E C M R)</th><th>Breakdown</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=4 class="muted">No sites configured.</td></tr>'}</tbody></table>
   <p class="muted">Bars: login · video · drift · evidence · capture · maturity · review-debt. ${esc(d._note)}</p></div>`;
};
PAGES.captureintel=async()=>{
  const d=await api('/api/template/capture-intel');
  const bar=(label,v)=>{const pct=Math.round((v||0)*100);const col=pct>=75?'var(--green)':pct>=40?'var(--primary)':'var(--red)';
    return `<div style="font-size:11px">${label} <span style="display:inline-block;width:60px;height:8px;background:#2a2a2a;border-radius:4px;vertical-align:middle"><span style="display:inline-block;width:${pct*0.6}px;height:8px;background:${col};border-radius:4px"></span></span></div>`;};
  const rows=d.captures.map(c=>{
    if(c._unreadable)return `<tr><td>${esc(c.capture)}</td><td colspan=5 class="muted">unreadable (no network_log) — not a recon capture</td></tr>`;
    const cov=c.coverage||{};
    const miss=(c.missing_evidence||[]).map(esc).join(', ')||'<span class="ok">complete</span>';
    const sm=(c.signing_markers||[]).length?(' · signing: '+c.signing_markers.map(esc).join(',')):'';
    return `<tr><td>${esc(c.capture)}</td>
      <td><span class="${c.band==='rich'?'ok':c.band==='thin'?'err':'muted'}">${c.quality} (${esc(c.band)})</span></td>
      <td>${Math.round(c.completeness*100)}%</td>
      <td>${bar('DOM',cov.dom)}${bar('NET',cov.network)}${bar('TPL',cov.template)}${bar('DRF',cov.drift)}</td>
      <td class="muted">net ${c.counts.network_events} · media ${c.counts.media_events} · rend ${c.counts.renditions}${sm}</td>
      <td class="muted">${miss}</td></tr>`;
  }).join('');
  return `<h1>Capture Intelligence</h1>
   <p class="sub">Per-capture quality, completeness, and coverage (DOM / network / template / drift) + missing evidence.
   Posture-safe metadata only — no capture content, no signing values, no reassembly.</p>${banner()}
   <div class="panel"><b>${d.capture_count}</b> capture(s) · <b>${d.readable_count}</b> readable · avg quality <b>${d.average_quality!=null?d.average_quality:'—'}</b> · <b>${d.thin_captures}</b> thin.
   ${d.captures_root_present?'':'<span class="err">No captures under the captures root in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Capture</th><th>Quality</th><th>Complete</th><th>Coverage</th><th>Counts</th><th>Missing evidence</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=6 class="muted">No captures found.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.templateautopilot=async()=>{
  setTimeout(()=>{const b=document.getElementById('ap_go');if(b)b.onclick=apRun;
    const i=document.getElementById('ap_target');if(i)i.onkeydown=e=>{if(e.key==='Enter')apRun()};},0);
  return `<h1>Template Autopilot</h1>
   <p class="sub">Operator-guided run for a URL or site: detect → login/video templates → health → download analysis → drift → suggested updates → review.
   Detection is recognition-only (the URL is never fetched); nothing is applied — it ends at the review workbench for your decision.</p>${banner()}
   <div class="panel"><input id="ap_target" placeholder="site id or URL (e.g. vipsite or https://site/video/123)" style="width:60%"/>
     <button class="btn" id="ap_go">Run</button></div>
   <div id="ap_out"></div>`;
};
async function apRun(){
  const t=(document.getElementById('ap_target')||{}).value||'';
  const el=document.getElementById('ap_out'); if(!el)return;
  el.innerHTML='<div class="panel muted">Running guided checks…</div>';
  let r; try{r=await api('/api/template/autopilot?target='+encodeURIComponent(t));}catch(e){el.innerHTML='<div class="panel err">'+esc(e.message)+'</div>';return;}
  const stIcon=s=>({ok:'✓','present':'✓','ready':'✓',missing:'—',not_recognized:'✗',failed:'✗',nothing_flagged:'✓',data_only:'•'}[s]||'•');
  const rows=r.steps.map(s=>{
    let detail=s.detail||'';
    if(s.result)detail=esc(JSON.stringify(s.result));
    return `<tr><td><b>${esc(s.step)}</b></td><td>${stIcon(s.status)} ${esc(s.status||'')}</td><td class="muted" style="max-width:520px;overflow:hidden;text-overflow:ellipsis">${detail}</td></tr>`;
  }).join('');
  const nextStep=r.steps.find(s=>s.next);
  el.innerHTML=`<div class="panel"><h2>Run: ${esc(r.target||'')}</h2>
    <p>Detected site: <b>${esc(r.detected_site||'(none)')}</b>${r.family?(' · family '+r.family.map(esc).join(', ')):''}</p>
    <table><thead><tr><th>Step</th><th>Status</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>
    ${r.human_decision_required?`<p><b>Human decision required.</b> <a data-p="templatereview" style="cursor:pointer;text-decoration:underline">Open the Template Review Workbench →</a></p>`:'<p class="muted">No template flagged for review.</p>'}
    ${nextStep?('<p class="muted">Next: '+esc(nextStep.next)+'</p>'):''}
    <p class="muted">${esc(r._note)}</p></div>`;
  setTimeout(()=>$$('#ap_out a[data-p]').forEach(a=>a.onclick=()=>go(a.dataset.p)),0);
}
PAGES.familyintel=async()=>{
  const d=await api('/api/template/family-intel');
  const rows=d.families.map(f=>{
    const dp=(f.shared_drift_patterns||[]).map(x=>esc(x.kind)).slice(0,3).join(', ')||'<span class="muted">—</span>';
    return `<tr class="clk" data-fam="${esc(f.family)}"><td>${esc(f.family)}</td><td>${f.member_count}</td>
      <td>${f.shared_download_selectors}</td><td>${f.shared_login_selectors}</td>
      <td>${Math.round(f.workflow.two_step_fraction*100)}%</td><td>${esc(f.workflow.common_url_attribute||'—')}</td><td class="muted">${dp}</td></tr>`;
  }).join('');
  setTimeout(()=>$$('#main tr[data-fam]').forEach(r=>r.onclick=()=>famLoad(r.dataset.fam)),0);
  return `<h1>Family Intelligence</h1>
   <p class="sub">Sites grouped by player/provider family. What members share — selectors, workflow, drift, failure modes —
   and where one site can learn from its siblings. Read-only; click a family to open it.</p>${banner()}
   <div class="panel"><b>${d.family_count}</b> family(ies). ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Family</th><th>Members</th><th>Shared dl selectors</th><th>Shared login selectors</th>
   <th>Two-step</th><th>URL attr</th><th>Shared drift</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=7 class="muted">No families inferred.</td></tr>'}</tbody></table></div>
   <div id="fam_detail"></div>`;
};
async function famLoad(name){
  const el=document.getElementById('fam_detail'); if(!el)return;
  el.innerHTML='<div class="panel muted">Loading family…</div>';
  let f; try{f=await api('/api/template/family?name='+encodeURIComponent(name));}catch(e){el.innerHTML='<div class="panel err">'+esc(e.message)+'</div>';return;}
  if(f.error){el.innerHTML='<div class="panel err">'+esc(f.error)+'</div>';return;}
  const sel=arr=>arr.map(s=>`<tr><td class="mono" style="padding:3px 8px">${esc(s.selector)}</td><td>${s.used_by}</td><td class="muted">${(s.sites||[]).map(esc).join(', ')}</td></tr>`).join('');
  const dl=sel(f.shared_download_selectors||[])||'<tr><td colspan=3 class="muted">none shared</td></tr>';
  const lg=sel(f.shared_login_selectors||[])||'<tr><td colspan=3 class="muted">none shared</td></tr>';
  const dp=(f.shared_drift_patterns||[]).map(x=>`<li>${esc(x.kind)} <span class="muted">(${x.members_affected} member(s))</span></li>`).join('')||'<li class="muted">none</li>';
  const fm=(f.shared_failure_modes||[]).map(x=>`<li>${esc(x.cause)} <span class="muted">(${x.members_affected} member(s))</span></li>`).join('')||'<li class="muted">none</li>';
  const cp=(f.cross_pollination||[]).map(c=>{
    const md=(c.missing_common_download_selectors||[]).map(esc).join(', ');
    const ml=(c.missing_common_login_selectors||[]).map(esc).join(', ');
    return `<tr><td>${esc(c.site)}</td><td class="muted">${md||'—'}</td><td class="muted">${ml||'—'}</td></tr>`;
  }).join('')||'<tr><td colspan=3 class="ok">every member has the family-common selectors</td></tr>';
  el.innerHTML=`<div class="panel"><h2>${esc(f.family)} family — ${f.member_count} member(s)</h2>
    <p class="muted">Members: ${f.members.map(esc).join(', ')} · two-step ${Math.round(f.workflow.two_step_fraction*100)}% · URL attr ${esc(f.workflow.common_url_attribute||'—')}</p>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div style="min-width:320px"><b>Shared download selectors</b><table><thead><tr><th>Selector</th><th>Used by</th><th>Sites</th></tr></thead><tbody>${dl}</tbody></table></div>
      <div style="min-width:320px"><b>Shared login selectors</b><table><thead><tr><th>Selector</th><th>Used by</th><th>Sites</th></tr></thead><tbody>${lg}</tbody></table></div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:10px">
      <div style="min-width:240px"><b>Shared drift patterns</b><ul>${dp}</ul></div>
      <div style="min-width:240px"><b>Shared failure modes</b><ul>${fm}</ul></div>
    </div>
    <div style="margin-top:10px"><b>Cross-pollination</b> <span class="muted">(family-common selectors a member is missing — data-only suggestion, never auto-applied)</span>
      <table><thead><tr><th>Site</th><th>Missing download selectors</th><th>Missing login selectors</th></tr></thead><tbody>${cp}</tbody></table></div>
    <p class="muted">${esc(f._note)}</p></div>`;
}
PAGES.siteplaybooks=async()=>{
  const d=await api('/api/template/playbook-index');
  const rows=d.sites.map(s=>{
    const fam=(s.families||[]).map(esc).join(', ')||'<span class="muted">—</span>';
    const yn=v=>v?'<span class="ok">yes</span>':'<span class="muted">no</span>';
    const conc=s.open_concerns>0?`<span class="err">${s.open_concerns}</span>`:'0';
    return `<tr class="clk" data-pbsite="${esc(s.site)}"><td>${esc(s.site||'(unnamed)')}</td><td>${fam}</td>
      <td>${yn(s.login_template)}</td><td>${yn(s.video_template)}</td><td>${esc(s.stability||'—')}</td>
      <td>${esc(s.maturity||'—')}</td><td>${conc}</td><td>${s.notes}</td></tr>`;
  }).join('');
  setTimeout(()=>$$('#main tr[data-pbsite]').forEach(r=>r.onclick=()=>pbLoad(r.dataset.pbsite)),0);
  return `<h1>Site Playbooks</h1>
   <p class="sub">A living dossier per site — login model, download model, selectors, drift, failure modes, notes, family, confidence.
   Read-only aggregation; click a site to open its dossier.</p>${banner()}
   <div class="panel"><b>${d.site_count}</b> site(s). ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Family</th><th>Login tpl</th><th>Video tpl</th>
   <th>Stability</th><th>Maturity</th><th>Concerns</th><th>Notes</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=8 class="muted">No sites configured.</td></tr>'}</tbody></table></div>
   <div id="pb_detail"></div>`;
};
async function pbLoad(site){
  const el=document.getElementById('pb_detail'); if(!el)return;
  el.innerHTML='<div class="panel muted">Loading dossier…</div>';
  let p; try{p=await api('/api/template/playbook?site='+encodeURIComponent(site));}catch(e){el.innerHTML='<div class="panel err">'+esc(e.message)+'</div>';return;}
  if(p.error){el.innerHTML='<div class="panel err">'+esc(p.error)+'</div>';return;}
  const selBlock=(o)=>Object.entries(o||{}).map(([k,v])=>`<div><b>${esc(k)}</b>: ${Array.isArray(v)?(v.map(esc).join('<br>')||'<span class="muted">(none)</span>'):esc(String(v||'—'))}</div>`).join('');
  const fm=(p.known_failure_modes||[]).map(f=>`<li>${esc(f.subject)} <span class="muted">[${esc(f.source)}${f.outcome?'; '+esc(f.outcome):''}]</span>${f.next?'<br><span class="muted">→ '+esc(f.next)+'</span>':''}</li>`).join('')||'<li class="muted">none recorded</li>';
  const notes=(p.operator_notes||[]).map(n=>`<li>${esc(n.kind||'note')}: ${esc(n.text||'')}</li>`).join('')||'<li class="muted">none</li>';
  const hist=(p.drift_history.events||[]).map(e=>`<li>${esc(e.ts||'')} — ${esc(e.kind)} <span class="muted">(${esc(e.severity)})</span></li>`).join('')||'<li class="muted">no drift events</li>';
  const ch=(p.confidence_history||[]).length?('<ul>'+p.confidence_history.map(c=>`<li>${esc(JSON.stringify(c))}</li>`).join('')+'</ul>'):'<span class="muted">no history yet (point-in-time scores below)</span>';
  const fc=p.family_confidence;
  el.innerHTML=`<div class="panel"><h2>${esc(p.site)} — dossier</h2>
    <p>Family: <b>${(p.family.inferred||[]).map(esc).join(', ')||'unknown'}</b> <span class="muted">(${esc(p.family.basis)})</span></p>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div style="min-width:240px"><b>Login model</b><br>template: ${p.login_model.template_present?'yes':'no'} · success ${p.login_model.recent_success_rate!=null?Math.round(p.login_model.recent_success_rate*100)+'%':'—'} · MFA/captcha ${p.login_model.mfa_captcha_indicated?'yes':'no'}</div>
      <div style="min-width:240px"><b>Download model</b><br>template: ${p.download_model.template_present?'yes':'no'} · two-step ${p.download_model.two_step_flow?'yes':'no'} · top rendition ${esc(p.download_model.highest_rendition_seen||'—')}</div>
      <div style="min-width:240px"><b>Confidence</b><br>stability ${esc(fc.stability.band||'—')} (${fc.stability.score!=null?fc.stability.score:'—'}) · maturity ${esc(fc.maturity.band||'—')} (${fc.maturity.score!=null?fc.maturity.score:'—'}) · ${esc(fc.maturity.trust||'')}</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:10px">
      <div style="min-width:280px"><b>Login selectors</b>${selBlock(p.selector_model.login)}</div>
      <div style="min-width:280px"><b>Download selectors</b>${selBlock(p.selector_model.download)}</div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:10px">
      <div style="min-width:240px"><b>Known failure modes</b><ul>${fm}</ul></div>
      <div style="min-width:240px"><b>Drift history</b><ul>${hist}</ul></div>
      <div style="min-width:240px"><b>Operator notes</b><ul>${notes}</ul></div>
    </div>
    <div style="margin-top:10px"><b>Confidence history</b>: ${ch}</div>
    <p class="muted">${esc(p._note)}</p></div>`;
}
PAGES.templatereview=async()=>{
  const d=await api('/api/template/review-queue');
  const rc=await api('/api/review-candidates').catch(()=>({candidates:[],dir:''}));
  const selList=a=>(a&&a.length)?a.map(esc).join('<br>'):'<span class="muted">(none)</span>';
  const diffGroup=(name,g)=>{
    if(g.before!==undefined&&!Array.isArray(g.before)){
      return `<tr><td>${esc(name)}</td><td>${esc(String(g.before||''))}</td><td>${esc(String(g.after||''))}</td><td>${g.changed?'<span class="err">changed</span>':'<span class="ok">same</span>'}</td></tr>`;
    }
    const add=(g.added||[]).map(x=>`<span class="ok">+ ${esc(x)}</span>`).join('<br>');
    const rem=(g.removed||[]).map(x=>`<span class="err">- ${esc(x)}</span>`).join('<br>');
    return `<tr><td>${esc(name)}</td><td>${selList(g.before)}</td><td>${selList(g.after)}</td><td>${add}${add&&rem?'<br>':''}${rem||(!add?'<span class="muted">same</span>':'')}</td></tr>`;
  };
  const items=d.items.map(it=>{
    const diff=Object.entries(it.diff).map(([k,g])=>diffGroup(k,g)).join('');
    const hist=(it.change_history||[]).map(e=>`<li>${esc(e.ts||'')} — ${esc(e.kind)} <span class="muted">(${esc(e.severity)})</span></li>`).join('')||'<li class="muted">no recorded changes</li>';
    const ev=(it.evidence||[]).slice(0,8).map(e=>`<li>${esc(e.date||'')} — ${esc(e.subject||'')} <span class="muted">[${esc(e.category||'')}]</span></li>`).join('')||'<li class="muted">no evidence on file</li>';
    const ce=it.confidence_explanation||{};
    const dec=it.decision?`<span class="${it.decision.decision==='accept'?'ok':it.decision.decision==='reject'?'err':'muted'}">${esc(it.decision.decision)}</span>${it.decision.note?(' — '+esc(it.decision.note)):''}`:'<span class="muted">pending</span>';
    return `<div class="panel">
      <h2>${esc(it.site||'(unnamed)')} · ${esc(it.kind)} template <span class="muted" style="font-weight:400">(${it.reasons.map(esc).join('; ')})</span></h2>
      <p class="muted">${esc(ce.why||'')}</p>
      <table><thead><tr><th>Selector group</th><th>Before (current)</th><th>After (suggested)</th><th>Change</th></tr></thead><tbody>${diff}</tbody></table>
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:8px">
        <div><b>Change history</b><ul>${hist}</ul></div>
        <div><b>Capture evidence</b><ul>${ev}</ul></div>
      </div>
      <div style="margin-top:8px">Decision: ${dec} &nbsp;
        <button class="btn" data-tdec="accept" data-tkey="${esc(it.item_key)}">approve</button>
        <button class="btn sec" data-tdec="reject" data-tkey="${esc(it.item_key)}">reject</button>
        <button class="btn sec" data-tdec="defer" data-tkey="${esc(it.item_key)}">defer</button>
        <span class="muted"> — recording a decision never applies it; apply approved templates via the existing path.</span>
      </div></div>`;
  }).join('');
  setTimeout(()=>$$('#main [data-tdec]').forEach(b=>b.onclick=async()=>{
    const note=prompt('Note for this '+b.dataset.tdec+' (optional):')||'';
    try{await api('/api/review/decide',{method:'POST',body:JSON.stringify({item:b.dataset.tkey,decision:b.dataset.tdec,note})});go('templatereview');}
    catch(e){toast(e.message,'err')}}),0);
  const candPanel=(()=>{
    const cs=(rc.candidates||[]);
    if(!cs.length)return `<div class="panel"><h2>Review candidates (from captures)</h2><p class="muted">None yet. On the Captures tab: start a capture, finish it, then click <b>build template</b> on the task — the normalized candidate appears here.</p></div>`;
    // Wave B1: the guided lifecycle stepper. Stage pills make the path
    // finish → build → test static → test live → workflow → approve visible
    // per candidate; ③/④ flip to ✓/✗ after a sandbox test runs.
    const stp=(n,label,state,id)=>{
      const c={done:'ok',pend:'muted',fail:'err',cli:'warn'}[state]||'muted';
      const m={done:'✓',pend:'○',fail:'✗',cli:'⌨'}[state]||'○';
      return `<span ${id?`id="${id}"`:''} class="${c}" style="border:1px solid var(--hairline);border-radius:4px;padding:1px 6px;margin:0 4px 4px 0;display:inline-block;white-space:nowrap">${m} ${n}. ${label}</span>`;
    };
    window.__bd_b1_cands=cs;
    const rows=cs.map((x,i)=>{
      const st=x.status==='review_ready'?'<span class="ok">review_ready</span>':`<span class="warn">${esc(x.status||'?')}</span>`;
      const w=(x.warnings||[]).map(z=>`<li>${esc(z)}</li>`).join('')||'<li class="muted">none</li>';
      const wf=x.workflow;
      const wfBlock=(()=>{
        if(!wf)return '<div class="muted" style="margin-top:4px">No observed workflow on this candidate (pre-Wave-B draft, or no action_timeline / dom_log).</div>';
        const tierCls=({ready:'ok',partial:'warn',thin:'warn',blocked:'err'})[(wf.verify&&wf.verify.tier)||'']||'muted';
        const steps=(wf.derived_steps||[]).map(s=>`<li class="mono">${esc(s)}</li>`).join('')||'<li class="muted">none recorded</li>';
        const v=wf.verify;
        const checks=v&&v.checks&&v.checks.length?v.checks.map(esc).join(', '):'—';
        const warns=v&&v.warnings&&v.warnings.length?`<ul>${v.warnings.map(z=>`<li class="warn">${esc(z)}</li>`).join('')}</ul>`:'<span class="muted">none</span>';
        const verifyLine=v?`<div style="margin-top:4px">verify: <span class="${tierCls}">${esc(v.tier||'—')}</span> &middot; checks: ${esc(checks)} &middot; gaps: ${v.gap_count!=null?v.gap_count:'—'} &middot; actions: ${v.action_count!=null?v.action_count:'—'}<br>warnings: ${warns}</div>`:'<div class="muted" style="margin-top:4px">no verify readout</div>';
        return `<details style="margin-top:4px"><summary>observed workflow — ${(wf.derived_steps||[]).length} step(s) <span class="muted">(${esc(wf.source||'?')}, review provenance)</span></summary>
          <ol style="margin:4px 0 4px 18px">${steps}</ol>
          <div>observed trigger: <span class="mono">${esc(wf.trigger_candidate||'—')}</span> <span class="muted">— provenance only; NOT the runtime download trigger</span></div>
          ${wf.trigger_evidence?`<div class="muted">evidence: ${esc(wf.trigger_evidence)}</div>`:''}
          ${verifyLine}</details>`;
      })();
      // A candidate file existing means finish (①) + build draft (②) already ran.
      const stepper=`<div style="margin:4px 0;display:flex;flex-wrap:wrap;align-items:center">
        ${stp(1,'finish','done')}${stp(2,'build draft','done')}
        ${stp(3,'test static','pend','sbx_s3_'+i)}${stp(4,'test live','pend','sbx_s4_'+i)}
        ${stp(5,'workflow',wf?'done':'pend')}${stp(6,'approve','cli')}</div>`;
      const sbxPanel=`<details style="margin-top:4px"><summary>test draft selectors against a live page <span class="muted">(match-only — no download, never enables the draft)</span></summary>
        <div class="row" style="margin-top:4px;gap:6px;align-items:center">
          <input id="sbx_url_${i}" placeholder="https://… a page to probe the draft's selectors on" style="flex:1;min-width:280px">
          <button class="btn sec" data-sbx-static="${i}">Test (static)</button>
          <button class="btn sec" data-sbx-live="${i}">Test (live)</button>
        </div>
        <div class="muted" style="margin-top:2px">Static = fast HTML fetch · Live = JS-rendered via Playwright. Reports how many elements each draft selector matches on that page. It only counts matches — it never downloads and never enables the draft.</div>
        <div id="sbx_out_${i}" class="mono" style="margin-top:4px"></div></details>`;
      // Wave B2 (v3.66.240): the GATE-CROSSING live-extract surface. AUGMENTS
      // (does NOT repoint) the match-only "Test (live)" above — this one sets a
      // per-site draft-test override and triggers ONE REAL download off this
      // UNREVIEWED draft via the main-app POST /api/template/test_extract. It
      // enables nothing and never writes reviewed/enabled. Persist defaults OFF.
      const txReady=!!(x.override_template&&x.override_template.selectors&&x.override_template.selectors.download);
      const liveExtract=`<details style="margin-top:4px;border:1px solid #a86b00;border-radius:4px;padding:4px 6px">
        <summary class="warn">&#9888; Test (live extract) — REAL download off this UNREVIEWED draft</summary>
        <div class="muted" style="margin:4px 0">Sets a per-site draft-test override and runs ONE real extraction off this draft on the chosen site. It does <b>not</b> enable the draft and never writes <code>reviewed/</code>. Clear it when done.</div>
        ${txReady?`<div class="row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px">
          <select id="tx_site_${i}" class="tx-site"><option value="">— pick a configured site —</option></select>
          <input id="tx_url_${i}" placeholder="optional: one http(s):// URL to enqueue" style="flex:1;min-width:220px">
          <label class="muted" style="white-space:nowrap"><input type="checkbox" id="tx_persist_${i}"> persist learned selectors <b>(default OFF)</b></label>
          <button class="btn" data-tx-run="${i}">Run live extract</button>
          <button class="btn sec" data-tx-clear="${i}">Stop testing (clear)</button>
        </div>
        <div id="tx_ind_${i}" class="mono" style="margin-top:4px"></div>
        <div id="tx_out_${i}" class="mono" style="margin-top:4px"></div>`
        :`<div class="muted" style="margin-top:4px">This candidate has no download selectors — nothing for a live extract to act on. (The match-only "Test (live)" above still works for selector probing.)</div>`}
      </details>`;
      const enableBox=x.draft_file?`<div class="row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px">
          <button class="btn" data-enable="${i}">Enable (promote &rarr; reviewed, enabled)</button>
          <label class="muted" style="white-space:nowrap"><input type="checkbox" id="en_api_${i}"> accept_api (A6-1)</label>
          <span class="muted">Runs the canonical <code>promote_draft</code> — blocking-lint refusal &middot; A6-1 api-gate &middot; network scrub &middot; A5 backup-before-overwrite. The deliberate enable step.</span>
        </div><div id="en_out_${i}" class="mono"></div>`
        :`<div class="muted" style="margin-top:6px">No <code>drafts/${esc(x.host||'')}.template-draft.json</code> on disk &mdash; enable via the CLI command below once the draft exists.</div>`;
      return `<div style="border-top:1px solid var(--hairline);padding:8px 0">
        <div><b>${esc(x.host||x.file)}</b> &middot; ${st} &middot; res: ${esc((x.resolutions||[]).join(', ')||'—')} &middot; download trigger: ${x.has_download_trigger?'✓':'✗'} &middot; modal rows: ${x.has_row_selectors?'✓':'✗'} &middot; patterns: ${x.network_patterns}</div>
        ${stepper}
        ${wfBlock}
        ${sbxPanel}
        ${liveExtract}
        <details style="margin-top:4px"><summary>review notes (${(x.warnings||[]).length}) &amp; approve / enable (⑥)</summary><ul>${w}</ul>
          ${enableBox}
          <div class="muted" style="margin-top:4px">Or promote via CLI (writes to a staging dir — review the diff, then swap into <code>templates/reviewed/</code>; back up the gold first).</div>
          <div class="mono">${esc(x.promote_cmd||'')}</div></details></div>`;
    }).join('');
    setTimeout(()=>{
      $$('#main [data-sbx-static]').forEach(b=>b.onclick=()=>runSandbox(+b.dataset.sbxStatic,'http'));
      $$('#main [data-sbx-live]').forEach(b=>b.onclick=()=>runSandbox(+b.dataset.sbxLive,'browser'));
      $$('#main [data-tx-run]').forEach(b=>b.onclick=()=>runLiveExtract(+b.dataset.txRun));
      $$('#main [data-tx-clear]').forEach(b=>b.onclick=()=>clearLiveExtract(+b.dataset.txClear));
      $$('#main [data-enable]').forEach(b=>b.onclick=()=>enableDraft(+b.dataset.enable));
      wireLiveExtract(cs);
    },0);
    return `<div class="panel"><h2>Review candidates (from captures) <span class="tag rev">human review</span></h2>
      <p class="muted">Normalized from captures (<code>${esc(rc.dir||'templates/review_candidates')}</code>). Runtime-shape, scrubbed, never auto-enabled — promotion is a deliberate CLI step. The stepper on each candidate tracks: finish → build draft → test static → test live → observed workflow → approve.</p>${rows}</div>`;
  })();
  return `<h1>Template Review Workbench</h1>
   <p class="sub">Human review layer for login + video template suggestions. Side-by-side before/after, confidence, change history, evidence.
   Recording approve/reject NEVER applies the change — the cockpit does not rewrite sites_config; apply approved templates via the existing path.</p>${banner()}
   ${candPanel}
   <div class="panel"><b>${d.count}</b> suggestion(s) · <b>${d.pending}</b> pending.
   ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   ${items||'<div class="panel ok">Nothing needs review.</div>'}
   <p class="muted">${esc(d._note)}</p>`;
};
PAGES.unifiedhealth=async()=>{
  const d=await api('/api/template/unified-health');
  const rows=d.sites.map(s=>{
    const yn=v=>v?'<span class="ok">yes</span>':'<span class="muted">no</span>';
    const stab=s.stability.score!=null?`<span class="${s.stability.band==='stable'?'ok':s.stability.band==='unstable'?'err':'muted'}">${s.stability.score} (${s.stability.band})</span>`:'—';
    const mat=s.maturity.score!=null?`<span class="${s.maturity.band==='mature'?'ok':s.maturity.band==='nascent'?'err':'muted'}">${s.maturity.score} (${s.maturity.band})</span>`:'—';
    const trust=s.maturity.trust==='trusted'?'<span class="ok">trusted</span>':'<span class="muted">use w/ review</span>';
    const drift=s.drift_events>0?`<span class="err">${s.drift_events}${s.worst_drift_severity?' ('+esc(s.worst_drift_severity)+')':''}</span>`:'<span class="ok">0</span>';
    const rate=s.login_success_rate!=null?(Math.round(s.login_success_rate*100)+'%'):'—';
    return `<tr><td>${esc(s.site||'(unnamed)')}</td><td>${yn(s.video_template)}</td><td>${yn(s.login_template)}</td>
      <td>${s.video_drift?'<span class="err">stale</span>':'<span class="ok">ok</span>'}</td><td>${rate}</td>
      <td>${stab}</td><td>${mat}</td><td>${trust}</td><td>${drift}</td></tr>`;
  }).join('');
  return `<h1>Unified Template Health</h1>
   <p class="sub">Video + login templates, stability, maturity, and drift per site — sorted least-stable first.
   Which templates can I trust today? Recognition-only; scores sharpen as captures/logins accrue.</p>${banner()}
   <div class="panel"><b>${d.site_count}</b> site(s) · <b>${d.trusted_count}</b> trusted.
   ${d.config_present?'':'<span class="err">No sites_config.json in this environment.</span>'}</div>
   <div class="panel"><table><thead><tr><th>Site</th><th>Video tpl</th><th>Login tpl</th><th>Video drift</th>
   <th>Login rate</th><th>Stability</th><th>Maturity</th><th>Trust</th><th>Drift events</th></tr></thead>
   <tbody>${rows||'<tr><td colspan=9 class="muted">No sites configured.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d._note)}</p></div>`;
};
PAGES.driftintel=async()=>{
  const d=await api('/api/template/drift-intel');
  const sev=d.severity_summary;
  const tl=d.timeline.events.map(e=>`<tr><td>${esc(e.ts||'—')}</td><td>${esc(e.site)}</td><td>${esc(e.kind)}</td>
    <td><span class="${e.severity==='critical'||e.severity==='high'?'err':'muted'}">${esc(e.severity)}</span></td>
    <td class="muted">${esc(e.detail||'')}</td></tr>`).join('');
  const fr=d.frequency.sites.map(r=>`<tr><td>${esc(r.site)}</td><td>${r.last_7d}</td><td>${r.last_30d}</td><td>${r.total}</td>
    <td><span class="${r.worst_severity==='critical'||r.worst_severity==='high'?'err':'muted'}">${esc(r.worst_severity)}</span></td></tr>`).join('');
  const rc=d.root_causes.sites.map(s=>`<tr><td>${esc(s.site)}</td><td>${s.causes.map(c=>`${esc(c.cause)} <span class="muted">(${esc(c.severity)}; ${esc(c.next)})</span>`).join('<br>')}</td></tr>`).join('');
  return `<h1>Drift Intelligence</h1>
   <p class="sub">What changed, how often, and the likely cause. Timelines/frequencies are factual logs — not forecasts.</p>${banner()}
   <div class="panel">Severity totals — <span class="err">critical ${sev.critical}</span> · <span class="err">high ${sev.high}</span> · medium ${sev.medium} · low ${sev.low}.
   ${d.frequency.trend_reliable?'':'<span class="muted">Too few events to read a trend — counts shown as a record, not extrapolated.</span>'}</div>
   <div class="panel"><h2>Drift timeline ${d.timeline.sparse?'<span class="muted" style="font-weight:400">(sparse)</span>':''}</h2>
   <table><thead><tr><th>When</th><th>Site</th><th>Kind</th><th>Severity</th><th>Detail</th></tr></thead>
   <tbody>${tl||'<tr><td colspan=5 class="ok">No drift events recorded.</td></tr>'}</tbody></table></div>
   <div class="panel"><h2>Drift frequency</h2><table><thead><tr><th>Site</th><th>7d</th><th>30d</th><th>Total</th><th>Worst</th></tr></thead>
   <tbody>${fr||'<tr><td colspan=5 class="muted">No drift.</td></tr>'}</tbody></table></div>
   <div class="panel"><h2>Likely root causes</h2><table><thead><tr><th>Site</th><th>Cause &amp; next step</th></tr></thead>
   <tbody>${rc||'<tr><td colspan=2 class="ok">No drift causes detected.</td></tr>'}</tbody></table>
   <p class="muted">${esc(d.root_causes._note)}</p></div>`;
};
PAGES.downloadexplain=async()=>{
  const d=await api('/api/template/download-explain');
  if(d.error)return `<h1>Download Decision Explorer</h1><p class="err">${esc(d.error)}</p>`;
  const rows=d.candidates.map(c=>{
    const why=c.reasons.map(r=>`${r.delta>0?'+':''}${r.delta} ${esc(r.label)}`).join(', ');
    const pick=(d.chosen&&c.url===d.chosen.url&&c.label===d.chosen.label)?' style="outline:2px solid var(--primary)"':'';
    return `<tr${pick}><td>${esc(c.label||'—')}</td><td class="muted">${esc(c.url||'—')}</td>
      <td><b>${c.score}</b></td><td>${c.resolution_tier}</td><td>${c.passes_threshold?'<span class="ok">✓</span>':'<span class="muted">below</span>'}</td>
      <td class="muted">${why||'—'}</td></tr>`;
  }).join('');
  return `<h1>Download Decision Explorer</h1>
   <p class="sub">Why a download candidate is chosen — the pure heuristic scorer, narrated. No live fetch, no model, no replay.</p>${banner()}
   ${d.is_sample?'<div class="panel muted">Showing a labelled SAMPLE candidate set (no live page in this context). Pass real recorded candidates to explain a specific page.</div>':''}
   <div class="panel">${d.chosen?`Chosen: <b>${esc(d.chosen.label)}</b> (score ${d.chosen.score}, tier ${d.chosen.resolution_tier}) — ${d.chosen.why.map(esc).join(', ')}`:'No candidate passed the threshold.'}</div>
   <div class="panel"><table><thead><tr><th>Candidate</th><th>URL (query-stripped)</th><th>Score</th><th>Res tier</th><th>≥${d.min_score}?</th><th>Why</th></tr></thead>
   <tbody>${rows}</tbody></table><p class="muted">${esc(d._method)}</p></div>`;
};

// ── Band F (v3.66.109): Forecasting & Trends — one page that closes out the
//    data-blocked roadmap features. Every metric is gated and honest. ──
PAGES.forecasting=async()=>{
  const d=await api('/api/forecasting');
  const secs=d.panels.map(p=>{
    const rows=p.metrics.map(m=>{
      const ok=m.status==='available';
      const badge=ok?`<span class="ok">available</span>`:`<span class="muted">withheld</span>`;
      return `<tr><td>#${esc(m.feature)}</td><td><b>${esc(m.name)}</b></td>
        <td>${esc(m.definition)}</td><td>${badge}</td></tr>`;
    }).join('');
    const g=p.gate;
    const gtxt=g.sufficient?'sufficient history'
      :('required_records' in g?`needs \u2265${g.required_records} logged forecasts (have ${g.records})`
        :`needs \u2265${g.required_days} distinct days (have ${g.distinct_days})`);
    return `<div class="panel"><h2 style="text-transform:capitalize">${esc(p.panel)} <span class="muted" style="font-weight:400">— ${gtxt}</span></h2>
      <table><thead><tr><th>#</th><th>Metric</th><th>What it computes</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="muted">${esc(p._note)}</p></div>`;
  }).join('');
  return `<h1>Forecasting &amp; Trends</h1>
   <p class="sub">Band F — longitudinal metrics. Data-blocked, not effort-blocked: each computes its
   structure but withholds output until the corpus has real history, instead of projecting from a
   few points. They populate automatically as history accrues. Read-only; no model call.</p>${banner()}
   <div class="panel"><b>${d.gated_count}/${d.metric_count}</b> metrics are currently withheld.
   The corpus has <b>${d.distinct_days}</b> distinct day(s); trend metrics need \u2265${d.min_trend_days}.
   <p class="muted">${esc(d._note)}</p></div>
   ${secs}`;
};

// ── Consolidated pages (C): Priority merges Inbox/Daily/Alerts (one engine);
//    Scores merges Maturity/Complexity/Org Health. Underlying renderers kept
//    intact so deep-links + direct go('inbox'|'maturity'|…) still work. ──
let _prioTab='inbox';
PAGES.priority=async()=>{
  const tabs=[['inbox','Inbox'],['daily','Daily'],['alerts','Alerts']];
  const bar=tabs.map(([k,l])=>`<button class="btn ${k===_prioTab?'':'sec'}" data-ptab="${k}">${l}</button>`).join(' ');
  setTimeout(()=>{$$('#main [data-ptab]').forEach(b=>b.onclick=()=>{_prioTab=b.dataset.ptab;renderPrioTab();});renderPrioTab();},0);
  return `<h1>Priority</h1><p class="sub">What needs attention, from the prioritization engine. Inbox = ranked list · Daily = today's focus · Alerts = high-severity only. Advisory — nothing acts automatically.</p>${banner()}
   <div class="panel"><div class="row">${bar}</div></div>
   <div id="prio_out" class="panel muted">…</div>`;
};
async function renderPrioTab(){
  const o=$('#prio_out');if(!o)return;o.className='panel';
  const sevcls=s=>s==='high'?'failed':s==='medium'?'':'succeeded';
  const dest=i=>{
    if(i.kind==='review') return {page:'review'};
    if(i.kind==='validation_debt'||i.kind==='correction_debt') return {page:'corpus', deeplink:i.id?{entry:i.id}:{filter:{debt:'true'}}};
    if(i.kind==='failed_task') return {page:'run'};
    if(i.kind==='posture') return {page:'release'};
    if(i.kind==='campaign') return {page:'campaigns'};
    return null;
  };
  if(_prioTab==='daily'){
    const d=await api('/api/daily-mission');
    const f=d.focus.map((i,n)=>`<li><b>${n+1}.</b> ${esc(i.title)} <span class="muted">— ${esc(i.action)}</span></li>`).join('');
    o.innerHTML=`<div style="text-align:center;font-size:17px;font-weight:700;margin-bottom:10px">${esc(d.mission)}</div>
      <h3>Focus (top 3)</h3><ul>${f||'<li class="ok">All clear.</li>'}</ul><p class="muted">${esc(d._note)}</p>`;
    return;
  }
  const d=await api('/api/inbox');
  let items=d.items;if(_prioTab==='alerts')items=items.filter(i=>i.severity==='high');
  let rows='';items.forEach((i,n)=>{const t=dest(i);rows+=`<tr class="${t?'clk':''}" data-n="${n}"><td><span class="st ${sevcls(i.severity)}">${esc(i.severity)}</span></td>
    <td>${esc(i.kind)}</td><td>${esc(i.title)}</td><td class="muted">${esc(i.action)}</td></tr>`;});
  o.innerHTML=`<div class="cards">
     <div class="card ${d.counts.high?'err':'ok'}"><div class="k">High</div><div class="v">${d.counts.high}</div></div>
     <div class="card warn"><div class="k">Medium</div><div class="v">${d.counts.medium}</div></div>
     <div class="card"><div class="k">Low</div><div class="v">${d.counts.low}</div></div></div>
   <table><thead><tr><th>Severity</th><th>Kind</th><th>Item</th><th>Suggested action</th></tr></thead>
   <tbody>${rows||`<tr><td colspan=4 class="ok">${_prioTab==='alerts'?'No high-severity alerts.':'Inbox zero — nothing needs attention.'}</td></tr>`}</tbody></table>
   <p class="muted">${esc(d._note)}</p>`;
  $$('#prio_out tr.clk[data-n]').forEach(r=>{const i=items[+r.dataset.n];const t=dest(i);if(t)r.onclick=()=>go(t.page,t.deeplink);});
}
PAGES.scores=async()=>{
  const [m,x,h]=await Promise.all([api('/api/maturity'),api('/api/complexity'),api('/api/org-health')]);
  const gauge=(label,score,band,colByScore)=>{const col=colByScore?(score>=70?'ok':score>=40?'warn':'err'):'';
    return `<div class="card" style="text-align:center;min-width:180px"><div class="k">${label}</div>
      <div style="font-size:34px;font-weight:800;${col?`color:var(--${col})`:''}">${score}<span style="font-size:14px">/100</span></div>
      ${band?`<div class="st ${col==='ok'?'succeeded':col==='warn'?'':'failed'}">${esc(band)}</div>`:'<div class="muted">relative index</div>'}</div>`;};
  const matc=Object.entries(m.components).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${(v*100).toFixed(0)}%</td></tr>`).join('');
  const cxd=Object.entries(x.drivers).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${v}</td><td class="muted">ref ${x.references[k]}</td></tr>`).join('');
  const ohc=Object.entries(h.components).map(([k,v])=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td>${(v*100).toFixed(0)}%</td></tr>`).join('');
  return `<h1>Scores</h1><p class="sub">Defined composites (not objective measures) — every input shown, weights/references adjustable.</p>
   <div class="cards">${gauge('Maturity',m.score,m.band,true)}${gauge('Complexity',x.complexity_index,null,false)}${gauge('Org Health',h.score,h.band,true)}</div>
   <div class="panel"><h2>Maturity components</h2><table><thead><tr><th>Component</th><th>Value</th></tr></thead><tbody>${matc}</tbody></table><p class="muted">${esc(m._note)}</p></div>
   <div class="panel"><h2>Complexity drivers</h2><table><thead><tr><th>Driver</th><th>Count</th><th>Soft reference</th></tr></thead><tbody>${cxd}</tbody></table><p class="muted">${esc(x._note)}</p></div>
   <div class="panel"><h2>Org Health components</h2><table><thead><tr><th>Component</th><th>Value</th></tr></thead><tbody>${ohc}</tbody></table><p class="muted">${esc(h._note)}</p></div>`;
};

PAGES.console=async()=>{
  setTimeout(()=>{$('#dl_go').onclick=dlGo;dlGo();},0);
  return `<h1>Debug Log Console</h1><p class="sub">Read-only, redacted tail of the application log. Lines that trip the posture scan are withheld. This view cannot run anything.</p>
   <div class="panel"><div class="row"><label class="f">Lines<select id="dl_n"><option>100</option><option selected>200</option><option>500</option><option>1000</option></select></label>
   <button class="btn" id="dl_go">Refresh</button><label class="f" style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="dl_auto"> auto-refresh (3s)</label></div></div>
   <div id="dl_meta" class="muted" style="margin:6px 0"></div>
   <pre id="dl_out" style="background:#0b0f16;border:1px solid var(--hairline);border-radius:8px;padding:12px;max-height:62vh;overflow:auto;font-size:12px;white-space:pre-wrap"></pre>`;
};
let _dlTimer=null;
async function dlGo(){const n=$('#dl_n').value;const d=await api('/api/debug-log?lines='+n);
  const out=$('#dl_out');
  if(!d.present){out.textContent='(no log found)\n\nsearched:\n'+(d.searched||[]).join('\n')+'\n\n'+(d._note||'');$('#dl_meta').textContent='';}
  else{out.textContent=d.lines.join('\n');out.scrollTop=out.scrollHeight;
    $('#dl_meta').textContent=`${d.path} — ${d.shown} lines${d.withheld?`, ${d.withheld} withheld (posture)`:''} · as of ${d.as_of}`;}
  const auto=$('#dl_auto');if(auto&&auto.checked){if(!_dlTimer)_dlTimer=setInterval(()=>{if($('#dl_out'))dlGo();else{clearInterval(_dlTimer);_dlTimer=null;}},3000);}
  else if(_dlTimer){clearInterval(_dlTimer);_dlTimer=null;}
}
let _shSid=null,_shOff=0,_shTimer=null;
PAGES.shell=async()=>{
  const st=await api('/api/shell/status');
  if(!st.enabled){
    return `<h1>Shell <span class="muted" style="font-size:13px">(opt-in)</span></h1>
     <div class="banner" style="border-color:var(--red)"><b>Disabled.</b> The interactive shell is OFF. ${st.pty_available?'':'(PTY not available on this OS.) '}To enable it, set <span class="kbd">BD_COCKPIT_SHELL=1</span> in the cockpit's environment and restart.</div>
     <div class="panel"><h2>Before you enable this</h2>
       <p>This is a real terminal that runs arbitrary commands as the cockpit's OS user — anyone who can reach this page gets that user's full access.</p>
       <ul>
         <li>Bind the cockpit to <b>localhost or your LAN only</b> — never expose it to an untrusted network.</li>
         <li>Put the cockpit <b>behind authentication</b>.</li>
         <li>Every command is recorded to <span class="mono">shell_audit.log</span>.</li>
       </ul>
       <p class="muted">${esc(st.note)}</p></div>`;
  }
  setTimeout(()=>shInit(),0);
  return `<h1>Shell <span class="muted" style="font-size:13px">(opt-in · live)</span></h1>
   <div class="banner" style="border-color:var(--amber)"><b>Live shell.</b> Runs as the cockpit user; every command is audited. Ensure the cockpit is localhost/LAN-only and behind auth.</div>
   <div class="panel">
     <pre id="sh_out" style="background:#000;color:#d6e2f0;border:1px solid var(--hairline);border-radius:8px;padding:12px;height:54vh;overflow:auto;font-size:12.5px;white-space:pre-wrap;margin:0"></pre>
     <div class="row" style="margin-top:8px">
       <input id="sh_in" placeholder="type a command, Enter to run…" style="flex:1;min-width:320px;font-family:ui-monospace,monospace" autocomplete="off">
       <button class="btn sec" id="sh_ctrlc">Ctrl-C</button>
       <button class="btn sec" id="sh_kill">End session</button>
     </div>
     <div class="muted" style="font-size:11px;margin-top:6px">Line-mode terminal (handles cd, pipes, env, git, etc.). Full-screen TUI programs (vim/top) won't render here.</div>
   </div>`;
};
async function shInit(){
  try{const o=await api('/api/shell/open',{method:'POST',body:'{}'});_shSid=o.session;_shOff=0;}
  catch(e){$('#sh_out').textContent='Refused: '+e.message;return;}
  const inp=$('#sh_in');inp.focus();
  inp.onkeydown=async e=>{if(e.key==='Enter'){const cmd=inp.value;inp.value='';
    try{await api('/api/shell/input',{method:'POST',body:JSON.stringify({session:_shSid,data:cmd+'\n'})});}catch(err){appendSh('\n[refused: '+err.message+']\n');}}};
  $('#sh_ctrlc').onclick=async()=>{try{await api('/api/shell/signal',{method:'POST',body:JSON.stringify({session:_shSid,signal:'INT'})});}catch(e){}};
  $('#sh_kill').onclick=async()=>{await shStop();$('#sh_out').textContent+='\n[session ended]';};
  if(_shTimer)clearInterval(_shTimer);
  _shTimer=setInterval(shPoll,500);
}
async function shPoll(){
  if(!_shSid||!$('#sh_out')){if(_shTimer){clearInterval(_shTimer);_shTimer=null;}return;}
  try{const d=await api('/api/shell/poll?session='+_shSid+'&offset='+_shOff);
    if(d.data){appendSh(d.data);_shOff=d.offset;}
    if(!d.alive){clearInterval(_shTimer);_shTimer=null;appendSh('\n[session closed]');}}
  catch(e){clearInterval(_shTimer);_shTimer=null;}
}
function appendSh(t){const o=$('#sh_out');if(!o)return;o.textContent+=t;o.scrollTop=o.scrollHeight;}
async function shStop(){if(_shTimer){clearInterval(_shTimer);_shTimer=null;}
  if(_shSid){try{await api('/api/shell/close',{method:'POST',body:JSON.stringify({session:_shSid})});}catch(e){}_shSid=null;}}

function banner(){ if(_loadRaw&&_loadRaw('bd_cockpit_posture_dismissed')==='1')return '';
  return `<div class="banner" id="postbanner"><button class="bx" title="Dismiss" onclick="dismissPosture()">&times;</button><b>Posture:</b> authorized local operations only. Allowlisted tools, validated arguments,
  no shell, no remote control, no replay or token reuse. The corpus, selectors, and profiles are never written automatically —
  everything stays human-gated. Signing values are redacted from all output.</div>`}
function dismissPosture(){_store('bd_cockpit_posture_dismissed','1');$$('.banner').forEach(b=>b.remove());}
function taskTable(tasks){
  if(!tasks.length)return '<p class="muted">No tasks yet.</p>';
  let r='';tasks.forEach(t=>{const when=t.finished?new Date(t.finished*1000).toLocaleTimeString():'…';
    const fin=(t.status==='running'&&t.category==='capture')
      ? ` · <a data-finish="${t.task_id}">finish</a> · <a data-cancel="${t.task_id}" class="err">discard</a>`
      : ((t.status==='succeeded'&&t.category==='capture')
         ? ` · <a data-buildtpl="${t.task_id}">build template</a>` : '');
    r+=`<tr><td>${esc(t.label)}${t.reduced_redaction?' <span class="tag rev" title="relaxed redaction — local only, never share">local-only</span>':''}</td><td><span class="st ${t.status}">${t.status}</span></td>
    <td>${esc(when)}</td><td>${t.returncode==null?'':('rc='+t.returncode)}</td>
    <td><a data-log="${t.task_id}">log</a>${fin}</td></tr>`;});
  setTimeout(()=>{$$('#main [data-log]').forEach(a=>a.onclick=()=>showLog(a.dataset.log));
    $$('#main [data-finish]').forEach(a=>a.onclick=()=>finishCapture(a.dataset.finish,false));
    $$('#main [data-cancel]').forEach(a=>a.onclick=()=>finishCapture(a.dataset.cancel,true));
    $$('#main [data-buildtpl]').forEach(a=>a.onclick=()=>buildTemplateFromTask(a.dataset.buildtpl));},0);
  return `<table><thead><tr><th>Task</th><th>Status</th><th>When</th><th>Result</th><th></th></tr></thead><tbody>${r}</tbody></table>
    <div id="logbox"></div>`;
}
async function refreshTasks(){const el=$('#th');if(!el)return;const t=await api('/api/tasks');el.innerHTML=taskTable(t.tasks);}
async function showLog(id){const d=await api('/api/task/'+id);const b=$('#logbox');
  if(b)b.innerHTML=`<div class="mono" style="margin-top:10px">${esc(d.log||'(no output)')}</div>`;}
async function finishCapture(id,discard){
  if(discard&&!confirm('Discard this capture? No WACZ will be saved.'))return;
  try{await api('/api/captures/finish',{method:'POST',body:JSON.stringify({task_id:id,discard:!!discard})});
    await refreshTasks();}catch(e){toast(e.message,'err')}}
async function buildTemplateFromTask(id){
  const box=$('#logbox');
  if(box)box.innerHTML='<div class="muted" style="margin-top:10px">Building draft + normalizing to a review candidate…</div>';
  try{const r=await api('/api/captures/normalize',{method:'POST',body:JSON.stringify({task_id:id})});
    const w=(r.warnings||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li class="muted">none</li>';
    if(box)box.innerHTML=`<div class="mono" style="margin-top:10px">
      candidate: <b>${esc(r.candidate_path)}</b> &middot; status: <b>${esc(r.status)}</b><br>
      host: ${esc(r.host)} &middot; resolutions: ${esc((r.resolutions||[]).join(', ')||'(none)')} &middot;
      download trigger: ${r.has_download_trigger?'yes':'no'} &middot; api host observed: ${esc((r.observed_api_hosts||[]).join(', ')||'(none)')}<br>
      <b>review notes:</b><ul>${w}</ul>
      Open the <b>Template Review Workbench</b> tab to review it.</div>`;
  }catch(e){if(box)box.innerHTML='<span class="err">'+esc(e.message)+'</span>';}}

// Wave B1: run the EXISTING /api/template/sandbox against a draft candidate's
// flat selector shape (forwarded as x.sandbox_template) — match-only probe, no
// download, no enable. Flips the candidate's ③/④ stepper pill on the result.
async function runSandbox(i,mode){
  const cs=window.__bd_b1_cands||[];const x=cs[i];if(!x)return;
  const inp=$('#sbx_url_'+i);const out=$('#sbx_out_'+i);
  const url=((inp&&inp.value)||'').trim();
  const pill=$('#sbx_s'+(mode==='browser'?4:3)+'_'+i);
  if(!url){if(out)out.innerHTML='<span class="err">enter a URL to test against first</span>';return;}
  if(out)out.innerHTML='<span class="muted">testing ('+mode+')…</span>';
  const setPill=(cls,mark)=>{if(pill){pill.className=cls;pill.innerHTML=pill.innerHTML.replace(/^[○✗✓]/,mark);}};
  try{
    const r=await apiRoot('/api/template/sandbox',{method:'POST',body:JSON.stringify({url,template:x.sandbox_template||{},mode})});
    if(!r.ok){if(out)out.innerHTML='<span class="err">'+esc(r.error||'sandbox failed')+'</span>';setPill('err','✗');return;}
    const m=r.matches||{};
    const fields=['trigger_selector','dl_selector','user_field','pass_field','submit_btn'];
    const present=fields.filter(f=>m[f]&&m[f].selector);
    const trs=present.map(f=>{
      const c=m[f];const cnt=c.match_count||0;
      return `<tr><td>${esc(f)}</td><td class="mono">${esc(c.selector)}</td><td class="${cnt>0?'ok':'err'}">${cnt}</td><td class="muted">${esc(c.error||'')}</td></tr>`;
    }).join('')||'<tr><td colspan=4 class="muted">no selectors on this draft to test</td></tr>';
    const hit=present.some(f=>(m[f].match_count||0)>0);
    setPill(hit?'ok':'err',hit?'✓':'✗');
    if(out)out.innerHTML=`<div class="muted">fetched ${r.html_bytes} bytes from ${esc(r.final_url||url)} (mode: ${esc(r.mode)})</div>
      <table><thead><tr><th>field</th><th>selector</th><th>matches</th><th></th></tr></thead><tbody>${trs}</tbody></table>
      <div class="muted">Match-only — no download was performed and the draft was not enabled.</div>`;
  }catch(e){if(out)out.innerHTML='<span class="err">'+esc(e.message)+'</span>';setPill('err','✗');}
}

// Wave B2 (v3.66.240): GATE-CROSSING live-extract wiring. AUGMENTS the
// match-only sandbox above. runLiveExtract() calls the main-app POST
// /api/template/test_extract (a FULL literal) which sets a per-site draft-test
// override and triggers ONE real download off the UNREVIEWED draft; it enables
// nothing and never writes reviewed/enabled. The ENABLE step is separate:
// enableDraft() -> the EXISTING POST /api/template_manager/promote (no new
// route). The standing indicator reads the _build_meta booleans
// draft_test_override_active / draft_test_override_persist off /api/status.
async function _txSites(){try{const r=await apiRoot('/api/sites_list');return (r&&r.sites)||[];}catch(e){return [];}}
async function _txStatus(){try{return (await apiRoot('/api/status'))||{};}catch(e){return {};}}
function renderTxIndicator(i,status){
  const ind=$('#tx_ind_'+i);if(!ind)return;
  const active=Object.entries(status||{})
    .filter(([sid,st])=>st&&st.config&&st.config.draft_test_override_active)
    .map(([sid,st])=>({sid,name:(st.config&&st.config.name)||sid,persist:!!(st.config&&st.config.draft_test_override_persist)}));
  ind.innerHTML=active.length
    ? active.map(a=>`<span class="warn">&#9888; running off draft override: <b>${esc(a.name)}</b> &middot; persist ${a.persist?'<b>ON</b>':'off'}</span>`).join('<br>')
    : '<span class="muted">no draft override active on any site</span>';
}
async function wireLiveExtract(cs){
  const [sites,status]=await Promise.all([_txSites(),_txStatus()]);
  (cs||[]).forEach((x,i)=>{
    const selEl=$('#tx_site_'+i);
    if(selEl) selEl.innerHTML='<option value="">— pick a configured site —</option>'+
      sites.map(s=>`<option value="${esc(s.site_id)}">${esc(s.name||s.site_id)}${s.hostname?' ('+esc(s.hostname)+')':''}</option>`).join('');
    renderTxIndicator(i,status);
  });
}
async function runLiveExtract(i){
  const cs=window.__bd_b1_cands||[];const x=cs[i];if(!x)return;
  const out=$('#tx_out_'+i);const selEl=$('#tx_site_'+i);
  const sid=(selEl&&selEl.value)||'';
  if(!sid){if(out)out.innerHTML='<span class="err">pick a configured site to point the override at</span>';return;}
  const tpl=x.override_template;
  if(!tpl||!tpl.selectors||!tpl.selectors.download){if(out)out.innerHTML='<span class="err">this candidate has no override template (no download selectors)</span>';return;}
  const persist=!!($('#tx_persist_'+i)&&$('#tx_persist_'+i).checked);
  const url=(($('#tx_url_'+i)&&$('#tx_url_'+i).value)||'').trim();
  const persistNote=persist?('PERSIST is ON — learned selectors persist to the live site config'+(x.draft_file?' AND back onto the draft. ':' (no drafts/ file: live config only). ')):'';
  if(!confirm('Run a REAL download off this UNREVIEWED draft on the selected site? '+persistNote+'This does NOT enable the draft.'))return;
  const body={site_id:sid,template:tpl,persist};
  if(x.draft_file)body.draft_file=x.draft_file;
  if(url)body.url=url;
  if(out)out.innerHTML='<span class="muted">setting override + starting one real run…</span>';
  try{
    const r=await apiRoot('/api/template/test_extract',{method:'POST',body:JSON.stringify(body)});
    if(!r.ok){if(out)out.innerHTML='<span class="err">'+esc(r.error||'test_extract failed')+'</span>';return;}
    if(out)out.innerHTML=`<span class="ok">override set on ${esc(sid)}</span> &middot; persist: ${r.persist?'<b class="warn">ON</b>':'off'} &middot; enqueued: ${r.enqueued?'yes':'no'} &middot; run started: ${r.started?'yes':'<span class="warn">no (no runner for that site)</span>'}<div class="muted">Real extraction is running on the live box. The draft was NOT enabled. Use "Stop testing" to clear the override.</div>`;
    renderTxIndicator(i,await _txStatus());
  }catch(e){if(out)out.innerHTML='<span class="err">'+esc(e.message)+'</span>';}
}
async function clearLiveExtract(i){
  const out=$('#tx_out_'+i);const selEl=$('#tx_site_'+i);
  const sid=(selEl&&selEl.value)||'';
  if(!sid){if(out)out.innerHTML='<span class="err">pick the site whose override to clear</span>';return;}
  if(out)out.innerHTML='<span class="muted">clearing override…</span>';
  try{
    const r=await apiRoot('/api/template/test_extract',{method:'POST',body:JSON.stringify({site_id:sid,clear:true})});
    if(!r.ok){if(out)out.innerHTML='<span class="err">'+esc(r.error||'clear failed')+'</span>';return;}
    if(out)out.innerHTML='<span class="ok">override cleared on '+esc(sid)+'</span>';
    renderTxIndicator(i,await _txStatus());
  }catch(e){if(out)out.innerHTML='<span class="err">'+esc(e.message)+'</span>';}
}
async function enableDraft(i){
  const cs=window.__bd_b1_cands||[];const x=cs[i];if(!x||!x.draft_file)return;
  const out=$('#en_out_'+i);
  if(!confirm('Promote AND ENABLE drafts/'+x.draft_file+'? This makes the template LIVE (enabled) via the canonical promote_draft — blocking-lint refusal, A6-1 api-gate, network scrub, A5 backup-before-overwrite on an already-live host.'))return;
  const accept_api=!!($('#en_api_'+i)&&$('#en_api_'+i).checked);
  if(out)out.innerHTML='<span class="muted">promoting…</span>';
  try{
    const r=await apiRoot('/api/template_manager/promote',{method:'POST',body:JSON.stringify({file:x.draft_file,enable:true,accept_api})});
    if(!r.ok){if(out)out.innerHTML='<span class="err">'+esc(r.error||'promote refused')+'</span>';return;}
    if(out)out.innerHTML='<span class="ok">promoted + enabled: '+esc(x.draft_file)+'</span>';
  }catch(e){if(out)out.innerHTML='<span class="err">'+esc(e.message)+'</span>';}
}

// wire dynamic action handlers after each render
function wire(){
  initAppearanceControls();
  const csgo=$('#cs_go');if(csgo)csgo.onclick=async()=>{
    const reduced=!!($('#cs_reduced')&&$('#cs_reduced').checked);
    if(reduced && !confirm('Relaxed redaction is ON: signed URLs will be KEPT in this capture so it writes even when capture-time scrubbing misses a signing shape. The WACZ is stamped LOCAL_ONLY and must never be shared. Continue?'))return;
    try{await api('/api/run-capture',{method:'POST',body:JSON.stringify({name:'capture_session',
      params:{url:$('#cs_url').value,label:$('#cs_label').value,autofill:$('#cs_af').value,profile_dir:$('#cs_profile').value,body_cap_mib:$('#cs_bodycap').value,chunk_events:$('#cs_chunks').value,max_seconds:$('#cs_maxsec').value,hud:$('#cs_hud').checked,reduced_redaction:reduced}})});
      await refreshTasks();}catch(e){toast(e.message,'err')}};
  const oago=$('#oa_go');if(oago)oago.onclick=async()=>{
    try{await api('/api/run-capture',{method:'POST',body:JSON.stringify({name:'offline_capture_analyze',
      params:{baseline:$('#oa_b').value,perturbed:$('#oa_p').value,axis:$('#oa_axis').value}})});
      await refreshTasks();}catch(e){toast(e.message,'err')}};
  const btgo=$('#bt_go');if(btgo)btgo.onclick=async()=>{
    const out=$('#bt_out');if(out){out.className='muted';out.textContent='Building…';}
    try{const r=await api('/api/captures/build-template',{method:'POST',body:JSON.stringify({
      a:$('#bt_a').value,b:$('#bt_b').value,freeze:$('#bt_freeze').value==='true'})});
      const d=r.draft||{};
      let h=`<div class="mono">host: ${esc(d.host||'?')} · confidence: ${esc(d.confidence||'?')} · slots: ${(d.slots||[]).length} · patterns: ${(d.patterns||[]).length}</div>`;
      if(r.template)h+=`<div class="mono" style="margin-top:6px">frozen template (truncated): ${esc(JSON.stringify(r.template).slice(0,400))}…</div>`;
      h+=`<details style="margin-top:6px"><summary>full draft</summary><div class="mono">${esc(JSON.stringify(d,null,2))}</div></details>`;
      if(out){out.className='';out.innerHTML=h;}
    }catch(e){if(out){out.className='';out.innerHTML='<span class="err">'+esc(e.message)+'</span>';}}};
  const apgo=$('#ap_go');if(apgo)apgo.onclick=async()=>{apgo.disabled=true;apgo.textContent='Running…';
    try{const r=await api('/api/run-capture',{method:'POST',body:JSON.stringify({name:'autopilot',
      params:{folder:$('#ap_folder').value,axis:$('#ap_axis').value}})});
      pollAutopilot(r.task.task_id);}catch(e){toast(e.message,'err')}
    apgo.disabled=false;apgo.textContent='Run autopilot';await refreshTasks();};
  const prev=$('#prev');if(prev)prev.onclick=async()=>{
    try{const r=await api('/api/import-plan/preview',{method:'POST',body:JSON.stringify({csv:$('#csv').value})});
      renderPreview(r);}catch(e){$('#prev_out').innerHTML='<span class="err">'+esc(e.message)+'</span>'}};
  // Delegated routing for in-page [data-p] links (panels, sub-view back-links).
  // Nav anchors are bound separately at boot; this catches links rendered inside a
  // page body. Optional data-dl carries a single-string deeplink (e.g. a change id).
  $$('#main a[data-p]').forEach(a=>{ if(a.dataset.bound) return; a.dataset.bound='1';
    a.onclick=(ev)=>{ev.preventDefault(); const dl=a.dataset.dl?{__s:a.dataset.dl}:null; go(a.dataset.p, dl);};});
  // Phase 2: container tab buttons mount an existing renderer into #tabhost.
  $$('#main .tabbar button[data-sub]').forEach(b=>{ if(b.dataset.bound) return; b.dataset.bound='1';
    b.onclick=()=>mountTab(b.closest('.tabbar').dataset.tabs, b.dataset.sub);});
}
async function pollAutopilot(id){const out=$('#ap_out');if(out)out.textContent='Running…';
  for(let i=0;i<60;i++){await new Promise(r=>setTimeout(r,1500));const d=await api('/api/task/'+id);
    if(d.task.status!=='running'){
      // find the cockpit md among outputs
      const md=(d.task.output_files||[]).find(f=>f.path.endsWith('capture_cockpit.md'));
      if(md&&md.posture==='clean'){const rep=await api('/api/report?name='+encodeURIComponent(d.task.task_id+'/out/'+md.path)+'&root=tasks');
        if(out)out.className='viewer',out.innerHTML=rep.html||('<div class="mono">'+esc(rep.text||'')+'</div>');}
      else if(out)out.innerHTML='<div class="mono">'+esc(d.log||'(done)')+'</div>';
      return;}}
}
function renderPreview(r){
  let rows='';r.items.forEach(it=>{rows+=`<tr><td>${it.row}</td><td>${esc(it.site)}</td><td>${esc(it.label)}</td>
    <td>${esc(it.priority)}</td><td>${it.valid?'<span class="ok">valid</span>':'<span class="err">'+esc(it.errors.join('; '))+'</span>'}</td></tr>`;});
  $('#prev_out').innerHTML=`<p class="muted">${r.n_valid} valid / ${r.n_invalid} invalid of ${r.n_rows}. ${esc(r._note)}</p>
   <table><thead><tr><th>#</th><th>Site</th><th>Label</th><th>Priority</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>
   <div class="row" style="margin-top:10px"><button class="btn" ${r.n_valid?'':'disabled'} onclick="toast('Queued for review — capture still launched one at a time through the validated path. No automatic execution.','ok')">Confirm valid rows → review queue</button></div>`;
}

let cur='home';
// DEEPLINK: a click on a card/row can navigate to a page AND focus a specific
// item there (e.g. go('corpus',{entry:'VC-0018'}) opens that one debt entry).
// The target page reads window.__deeplink in its renderer and acts on it once.
let __deeplink=null;
// merged sub-views: highlight the parent nav entry when a sub-page is shown directly
const NAV_PARENT={inbox:'priority',daily:'priority',alerts:'priority',maturity:'insightsc',complexity:'insightsc',orghealth:'insightsc',eligibilitysite:'trustc',trustsite:'trustc',validationsite:'validationc',impactsite:'impactc',promotionsite:'rollbackc'};
// Phase 2 — tabbed containers: folded pages mount as tabs (existing renderers,
// not rewritten); old per-panel routes redirect to the right container + tab (2.5).
const REDIRECT={
  captures:['capturesc','captures'],
  autopilot:['capturesc','autopilot'],
  captureintel:['capturesc','captureintel'],
  queue:['capturesc','queue'],
  novnc:['capturesc','novnc'],
  sitereadiness:['capturesc','sitereadiness'],
  templatereview:['templatesc','templatereview'],
  videotemplates:['templatesc','videotemplates'],
  templateautopilot:['templatesc','templateautopilot'],
  stagingcandidates:['templatesc','stagingcandidates'],
  missioncontrol:['templatesc','missioncontrol'],
  logintemplates:['templatesc','logintemplates'],
  review:['reviewc','review'],
  packet:['reviewc','packet'],
  reviewroi:['reviewc','reviewroi'],
  escalations:['reviewc','escalations'],
  reviewexp:['reviewc','reviewexp'],
  loginreview:['reviewc','loginreview'],
  queueintel:['reviewc','queueintel'],
  reviewops:['reviewc','reviewops'],
  confidence:['insightsc','confidence'],
  portfolio:['insightsc','portfolio'],
  portfolioopp:['insightsc','portfolioopp'],
  blindspots:['insightsc','blindspots'],
  scarcity:['insightsc','scarcity'],
  captureyield:['insightsc','captureyield'],
  decisionquality:['insightsc','decisionquality'],
  coverage:['insightsc','coverage'],
  opportunity:['insightsc','opportunity'],
  scores:['insightsc','scores'],
  forecasting:['insightsc','forecasting'],
  impact:['impactc','impact'],
  impactanalysis:['impactc','impactanalysis'],
  family:['familiesc','family'],
  familyhealth:['familiesc','familyhealth'],
  similarity:['familiesc','similarity'],
  familyintel:['familiesc','familyintel'],
  corpus:['familiesc','corpus'],
  collections:['familiesc','collections'],
  sites:['familiesc','sites'],
  drift:['driftc','drift'],
  crosssitedrift:['driftc','crosssitedrift'],
  driftintel:['driftc','driftintel'],
  logindrift:['driftc','logindrift'],
  trustdecay:['trustc','trustdecay'],
  eligibility:['trustc','eligibility'],
  validationops:['validationc','validationops'],
  debt:['validationc','debt'],
  unifiedhealth:['healthc','unifiedhealth'],
  health:['healthc','health'],
  systemstatus:['healthc','systemstatus'],
  rollbackcenter:['rollbackc','rollbackcenter'],
  promotionactivity:['rollbackc','promotionactivity'],
  autonomycenter:['governancec','autonomycenter'],
  govhealth:['governancec','govhealth'],
  autmetrics:['governancec','autmetrics'],
  governance:['governancec','governance'],
  guardrails:['governancec','guardrails'],
  authority:['governancec','authority'],
  compliance:['governancec','compliance'],
  housekeeping:['governancec','housekeeping']
};
let __pendingTab=null;
// BUG-3 stale-render guard: a monotonic token bumped on every page/tab nav.
// go()/mountTab() capture it at entry and re-check after their await, so a
// slow prior tab that resolves late cannot overwrite the now-active panel.
let __navGen=0;
function tabbar(group,tabs){return `<div class="tabbar" data-tabs="${group}">`+tabs.map(t=>`<button data-sub="${t[0]}">${esc(t[1])}</button>`).join('')+`</div>`;}
async function mountTab(group,sub){
  const __g=++__navGen;
  const host=$('#tabhost'); if(!host)return;
  $$(`.tabbar[data-tabs="${group}"] button`).forEach(b=>b.classList.toggle('on',b.dataset.sub===sub));
  _writeHash();
  host.innerHTML=skel(5);
  try{const _h=await PAGES[sub]();if(__g!==__navGen)return;host.innerHTML=_h;wire();
    if(['run','captures','autopilot'].includes(sub))refreshTasks();
  }catch(e){if(__g!==__navGen)return;host.innerHTML='<p class="err">'+esc(e.message)+'</p>';}
}
// ── Slice 3: hash-routing — reload-restore + back/forward + bookmark/share + subtab persistence ──
let __routing=false;
function _activeSub(){const b=document.querySelector('.tabbar button.on');return b?b.dataset.sub:null;}
function _writeHash(){
  if(__routing||!cur)return;
  const sub=_activeSub(); const h='#'+cur+(sub?('/'+sub):'');
  if(location.hash!==h){__routing=true;try{location.hash=h;}finally{setTimeout(()=>{__routing=false;},0);}}
}
function _bootRoute(){
  const app=document.querySelector('.app');
  const lay=_load('bd_cockpit_layout','side');
  const vt=(app&&app.dataset.vtier)||_load('bd_cockpit_vtier','everyday');
  if((lay==='mode'||lay==='miller')&&(vt==='advanced'||vt==='system'))return _tierLanding(vt);
  return 'home';
}
function routeFromHash(){
  const raw=(location.hash||'').replace(/^#/,'');
  if(!raw){go(_bootRoute());return;}
  const slash=raw.indexOf('/');
  const p=slash<0?raw:raw.slice(0,slash);
  const sub=slash<0?'':raw.slice(slash+1);
  if(!PAGES[p]&&!REDIRECT[p]){go('home');return;}
  if(sub)__pendingTab=sub;
  go(p);
}
window.addEventListener('hashchange',()=>{ if(__routing)return; routeFromHash(); });
async function go(p, deeplink){
  const __g=++__navGen;
  if(typeof closeBarDropdowns==='function')closeBarDropdowns();
  {const _a=document.querySelector('.app');if(_a&&_a.classList.contains('navopen')){_a.classList.remove('navopen');const _nt=document.getElementById('navtoggle');if(_nt)_nt.setAttribute('aria-expanded','false');}}
  if(REDIRECT[p] && !deeplink){__pendingTab=REDIRECT[p][1];p=REDIRECT[p][0];}
  cur=p; __deeplink=deeplink||null; window.__deeplink=__deeplink;
  const navKey=NAV_PARENT[p]||p;
  $$('#nav a').forEach(a=>a.classList.toggle('on',a.dataset.p===navKey));
  // auto-expand the section containing the active item so the highlight is visible
  const active=[...document.querySelectorAll('#nav a')].find(a=>a.dataset.p===navKey);
  $$('#nav .navsec,#nav .navdrawer').forEach(s=>s.classList.remove('active'));
  if(active){const sec=active.closest('.navsec,.navdrawer');if(sec){sec.classList.remove('collapsed');sec.classList.add('active');}}
  const m=$('#main');m.innerHTML=skel(6);
  try{let html=await PAGES[p]();
    if(__g!==__navGen)return;
    if(html.indexOf('main-inner')<0)html='<div class="main-inner">'+html+'</div>';
    m.innerHTML=html;wire();
    if(__deeplink){applyDeeplink(p,__deeplink);__deeplink=null;window.__deeplink=null;}
    if(['run','captures','autopilot'].includes(p))refreshTasks();
    _writeHash();}
  catch(e){m.innerHTML='<h1>'+p+'</h1><p class="err">'+esc(e.message)+'</p>'}}

// applyDeeplink runs AFTER a page renders; it focuses the requested item.
function applyDeeplink(page,dl){
  if(page==='corpus' && dl.entry){
    // open the Corpus Explorer entry detail for one id
    setTimeout(()=>{ if(typeof cxDetail==='function') cxDetail(dl.entry); },60);
  } else if(page==='corpus' && dl.filter){
    // pre-set filters then run. Map friendly keys -> the actual field ids.
    const idmap={category:'cat',outcome:'out',site:'site',debt:'debt',has_debt:'debt',q:'q',cat:'cat',out:'out'};
    setTimeout(()=>{ for(const[k,v]of Object.entries(dl.filter)){const fid=idmap[k]||k; const el=$('#cx_'+fid); if(el)el.value=v;} if(typeof cxRun==='function')cxRun(); },60);
  } else if(page==='trace' && dl.entry){
    setTimeout(()=>{ const sel=$('#tr_id'); if(sel){sel.value=dl.entry; if(typeof trGo==='function')trGo();} },60);
  } else if(page==='sites' && dl.site){
    setTimeout(()=>{ if(typeof openSite==='function')openSite(dl.site); },60);
  } else if(page==='investigate' && dl.site){
    setTimeout(()=>{ if(typeof invOpen==='function')invOpen(dl.site); },60);
  } else if(page==='drift' && dl.site){
    setTimeout(()=>{ const r=[...document.querySelectorAll('#main tr')].find(tr=>tr.textContent.includes(dl.site)); if(r){r.style.outline='2px solid var(--primary)'; r.scrollIntoView({block:'center'});} },60);
  }
}

// HOVER POPOVER: attach a tooltip listing items to any element. Each listed item
// is clickable and deep-links. Usage: hoverList(el, [{label, page, deeplink}], title)
let __pop=null;
function hoverList(el, items, title){
  if(!el) return;
  el.style.cursor = items.length ? 'pointer' : 'default';
  if(!items.length) return;
  el.addEventListener('mouseenter', ()=>{
    if(__pop) __pop.remove();
    const pop=document.createElement('div'); pop.className='hoverpop';
    pop.innerHTML = (title?`<div class="hp-title">${esc(title)}</div>`:'') +
      items.map((it,i)=>`<div class="hp-row" data-i="${i}">${esc(it.label)}</div>`).join('');
    document.body.appendChild(pop);
    const r=el.getBoundingClientRect();
    pop.style.left=Math.min(r.left,window.innerWidth-pop.offsetWidth-12)+'px';
    pop.style.top=(r.bottom+6)+'px';
    pop.querySelectorAll('.hp-row').forEach(row=>{
      row.onclick=(ev)=>{ev.stopPropagation(); const it=items[+row.dataset.i]; if(it.page) go(it.page, it.deeplink); if(__pop){__pop.remove();__pop=null;}};
    });
    // keep open while hovering the popover; close when leaving both
    let over=true;
    const close=()=>{ if(!over && __pop){__pop.remove();__pop=null;} };
    pop.addEventListener('mouseenter',()=>{over=true;});
    pop.addEventListener('mouseleave',()=>{over=false;setTimeout(close,120);});
    el.addEventListener('mouseleave',()=>{over=false;setTimeout(close,200);},{once:true});
    __pop=pop;
  });
  // a plain click on the element (not a list row) navigates to the page itself
  if(items[0] && items[0].page){
    el.addEventListener('click',(ev)=>{ if(ev.target.closest('.hoverpop'))return; go(items[0].page); });
  }
}
$$('#nav a').forEach(a=>a.onclick=()=>go(a.dataset.p));
// Slice C: populate the bottom-bar "More" dropdown by cloning the live Advanced +
// System drawer links (always in sync; cloned links carry their own go() handler
// since the boot nav-wiring above only binds the original #nav a set).
function buildMoreSec(){
  const host=$('#morebody'); if(!host)return;
  host.innerHTML='';
  [['advanced','Advanced'],['system','System']].forEach(pair=>{
    const links=$$('#nav .navdrawer[data-tier="'+pair[0]+'"] a[data-p]');
    if(!links.length)return;
    const h=document.createElement('div');h.className='moresubhead';h.textContent=pair[1];host.appendChild(h);
    links.forEach(src=>{const p=src.dataset.p;
      const a=document.createElement('a');a.dataset.p=p;a.setAttribute('tabindex','0');a.setAttribute('role','link');
      a.textContent=((src.childNodes[0]&&src.childNodes[0].textContent)||src.textContent).trim();
      const bd=document.createElement('span');bd.className='badge pinned';bd.textContent='Pinned';bd.hidden=true;a.appendChild(bd);
      const act=(e)=>{e.preventDefault();go(p);};a.onclick=act;a.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')act(e);});
      host.appendChild(a);});
  });
}
// Slice D: render curated beta/new badges on their nav items (idempotent).
function renderNavBadges(){
  Object.keys(NAV_BADGES).forEach(p=>{const a=document.querySelector('#nav a[data-p="'+p+'"]');
    if(!a||a.querySelector('.badge'))return;
    const kind=NAV_BADGES[p];const b=document.createElement('span');b.className='badge '+kind;b.textContent=kind.toUpperCase();a.appendChild(b);});
}
buildMoreSec();
// Bar-dropdown controller (top + bottom bars). The dropdowns are position:fixed
// so the bar itself can be overflow-x:auto (horizontal scroll at narrow widths)
// while the menu still escapes the bar's clip box. They open on hover (desktop)
// AND on tap/click (touch) — a CSS :hover reveal never fires on a real touch tap.
function _isBarLayout(){const a=document.querySelector('.app');return !!a&&(a.classList.contains('topnav')||a.classList.contains('bottombar'));}
function _barDD(sec){return sec.querySelector(':scope > .navitems, :scope > .drawerbody');}
function _clearDD(dd){if(dd){dd.style.left='';dd.style.top='';dd.style.bottom='';}}
function closeBarDropdowns(except){
  $$('#nav .navsec.baropen,#nav .navdrawer.baropen').forEach(s=>{
    if(s===except)return; s.classList.remove('baropen'); _clearDD(_barDD(s));
  });
}
function closeBarDropdown(sec){sec.classList.remove('baropen');_clearDD(_barDD(sec));}
function openBarDropdown(sec){
  if(!_isBarLayout())return;
  const head=sec.querySelector(':scope > .navhead, :scope > .drawerhead');
  const dd=_barDD(sec); if(!head||!dd)return;
  closeBarDropdowns(sec);
  sec.classList.add('baropen');            // display:block first, so we can measure
  const hr=head.getBoundingClientRect();
  const ddw=dd.offsetWidth||184;
  const left=Math.max(8,Math.min(hr.left, window.innerWidth-ddw-8));
  dd.style.left=left+'px';
  if(document.querySelector('.app').classList.contains('bottombar')){
    dd.style.top=''; dd.style.bottom=(Math.max(8,window.innerHeight-hr.top+6))+'px';
  } else {
    dd.style.bottom=''; dd.style.top=(hr.bottom+6)+'px';
  }
}
function toggleBarDropdown(sec){if(sec.classList.contains('baropen'))closeBarDropdown(sec);else openBarDropdown(sec);}
function wireBarDropdowns(){
  $$('#nav .navsec,#nav .navdrawer').forEach(sec=>{
    if(sec.dataset.barwired)return; sec.dataset.barwired='1';
    sec.addEventListener('mouseenter',()=>{if(_isBarLayout())openBarDropdown(sec);});
    sec.addEventListener('mouseleave',()=>{if(_isBarLayout())closeBarDropdown(sec);});
  });
  const side=document.querySelector('.app .side');
  if(side&&!side.dataset.barscroll){side.dataset.barscroll='1';side.addEventListener('scroll',()=>closeBarDropdowns());}
}
if(!window.__barWinWired){window.__barWinWired=1;
  window.addEventListener('resize',()=>closeBarDropdowns());
  document.addEventListener('click',e=>{if(!e.target.closest('#nav .navsec,#nav .navdrawer'))closeBarDropdowns();},true);
}
// collapsible nav sections: in a bar layout the header toggles the fixed
// dropdown (hover for desktop, tap for touch); otherwise it collapses the
// section in the sidebar.
$$('#nav .navhead').forEach(h=>h.onclick=()=>{
  const sec=h.closest('.navsec'); if(!sec)return;
  if(_isBarLayout()){toggleBarDropdown(sec);}else{sec.classList.toggle('collapsed');}
});
wireBarDropdowns();
// appearance controller (1a/1b/1c): theme + layout + tier, persisted, app-wide.
// Exposed at script scope so the Settings page's duplicate selects can drive them.
const LAYOUTS=[['side','Sidebar'],['top','Top bar'],['rail','Compact rail'],['mode','Mode switcher'],['miller','Deep nav'],['bottombar','Bottom bar']];
const LAYDESC={side:'Best for desktop',top:'Wide-screen compact nav',rail:'Icon-first power-user nav',bottombar:'Mobile / tablet friendly',mode:'Everyday / Advanced / System',miller:'Deep system navigation'};
// Slice D: curated status badges. Only genuinely new/beta surfaces go here —
// never invent. 'shell' is the one defensible opt-in (beta) surface in-code.
// PINNED is data-driven (real pin state), not listed here.
const NAV_BADGES={shell:'beta'};
const THEMES=[['live','Cockpit (live)'],['auto','Auto (system)'],['ocean','Ocean Depths'],['forest','Forest Canopy'],['tech','Tech Innovation'],['galaxy','Midnight Galaxy'],['sunset','Sunset Boulevard'],['golden','Golden Hour'],['arctic','Arctic Frost'],['desert','Desert Rose'],['botanical','Botanical Garden'],['minimalist','Modern Minimalist']];
if(window.matchMedia){try{window.matchMedia('(prefers-color-scheme: light)').addEventListener('change',()=>{if(_load('bd_cockpit_theme','live')==='auto')applyTheme('auto');});}catch(e){}}
const TIERS=[['everyday','Everyday'],['advanced','Advanced'],['system','System']];
const LAYOUT_CLS={top:'topnav',rail:'rail',mode:'mode',miller:'miller',bottombar:'bottombar'};
function _store(k,v){try{localStorage.setItem(k,v);}catch(e){}}
function _load(k,d){try{return localStorage.getItem(k)||d;}catch(e){return d;}}
function _loadRaw(k){try{return localStorage.getItem(k);}catch(e){return null;}}
// Phase O: optional server-backed prefs. localStorage stays primary; these are
// fire-and-forget so an offline/500 server never blocks the UI.
function _postPref(k,v){(async()=>{try{await api('/api/ui_prefs',{method:'POST',body:JSON.stringify({[k]:v})});}catch(e){}})();}
let __uiSynced=false;
function applyLayout(v){
  const app=document.querySelector('.app'); if(!app)return;
  if(typeof closeBarDropdowns==='function')closeBarDropdowns();
  Object.values(LAYOUT_CLS).forEach(c=>app.classList.remove(c));
  if(LAYOUT_CLS[v])app.classList.add(LAYOUT_CLS[v]);
  if(typeof wireBarDropdowns==='function')wireBarDropdowns();
  if(!app.dataset.vtier)app.dataset.vtier=_load('bd_cockpit_vtier','everyday');
  document.querySelectorAll('#layout_sel,#s_layout_sel').forEach(s=>{s.value=v;});
  _markLaypick(v);
  document.querySelectorAll('#tierseg button').forEach(b=>b.classList.toggle('on',b.dataset.vt===app.dataset.vtier));
}
function applyTheme(k){
  if(k==='auto'){const light=window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches;
    if(light)document.documentElement.dataset.theme='arctic'; else delete document.documentElement.dataset.theme;}
  else if(!k||k==='live')delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme=k;
  document.querySelectorAll('#theme_sel,#s_theme_sel').forEach(s=>{s.value=k;});
}
function _tierLanding(t){return t==='advanced'?'advlanding':t==='system'?'syslanding':'home';}
function setTier(t){
  const app=document.querySelector('.app'); if(!app)return;
  app.dataset.vtier=t; _store('bd_cockpit_vtier',t); _postPref('vtier',t);
  document.querySelectorAll('#tierseg button').forEach(b=>b.classList.toggle('on',b.dataset.vt===t));
  const lay=_load('bd_cockpit_layout','side');
  // under mode/deep-nav, route to the tier's landing — but only when sitting on a
  // landing/home, so we never yank the operator away from a child they chose.
  if((lay==='mode'||lay==='miller')&&(cur==='home'||cur==='advlanding'||cur==='syslanding'||!cur)){
    go(_tierLanding(t));
  }
}
function _markLaypick(k){$$('#laypick .opt').forEach(o=>{const on=o.dataset.l===k;o.classList.toggle('on',on);o.setAttribute('aria-checked',on?'true':'false');});}
function _fillLaypick(host,cur){
  if(!host)return;
  host.innerHTML=LAYOUTS.map(o=>`<div class="opt${o[0]===cur?' on':''}" role="menuitemradio" tabindex="0" data-l="${o[0]}" aria-checked="${o[0]===cur?'true':'false'}"><span class="on-name">${esc(o[1])}</span><span class="on-desc">${esc(LAYDESC[o[0]]||'')}</span></div>`).join('');
  $$('#laypick .opt').forEach(o=>{const pick=()=>{const k=o.dataset.l;_store('bd_cockpit_layout',k);const sel=$('#layout_sel');if(sel)sel.value=k;applyLayout(k);_postPref('layout',k);};
    o.onclick=pick;o.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick();}});});
}
function _fill(sel,opts,val){if(!sel)return;sel.innerHTML=opts.map(o=>`<option value="${o[0]}">${esc(o[1])}</option>`).join('');sel.value=val;}
function initAppearanceControls(){
  let L=_load('bd_cockpit_layout','side'); const T=_load('bd_cockpit_theme','live');
  if(!LAYOUTS.some(x=>x[0]===L)){L='side';_store('bd_cockpit_layout','side');}
  ['#layout_sel','#s_layout_sel'].forEach(id=>{const s=$(id);if(s){_fill(s,LAYOUTS,L);s.onchange=()=>{_store('bd_cockpit_layout',s.value);applyLayout(s.value);_postPref('layout',s.value);};}});
  _fillLaypick($('#laypick'),L);
  ['#theme_sel','#s_theme_sel'].forEach(id=>{const s=$(id);if(s){_fill(s,THEMES,T);s.onchange=()=>{_store('bd_cockpit_theme',s.value);applyTheme(s.value);_postPref('theme',s.value);};}});
  const ts=$('#tierseg'); if(ts&&!ts.dataset.init){ts.dataset.init='1';ts.innerHTML=TIERS.map(t=>`<button data-vt="${t[0]}">${esc(t[1])}</button>`).join('');$$('#tierseg button').forEach(b=>b.onclick=()=>setTier(b.dataset.vt));}
  applyTheme(T); applyLayout(L);
  // Phase O: one-time server sync — seed from server only where this device has
  // no local value yet (fresh device); never override an existing local choice.
  if(!__uiSynced){__uiSynced=true;(async()=>{try{
    const r=await api('/api/ui_prefs'); const sp=(r&&r.prefs)||{};
    if(sp.layout&&_loadRaw('bd_cockpit_layout')==null){_store('bd_cockpit_layout',sp.layout);applyLayout(sp.layout);}
    if(sp.theme&&_loadRaw('bd_cockpit_theme')==null){_store('bd_cockpit_theme',sp.theme);applyTheme(sp.theme);}
    if(sp.vtier&&_loadRaw('bd_cockpit_vtier')==null){setTier(sp.vtier);}
  }catch(e){}})();}
}
initAppearanceControls();

// ── Shell redesign Slice 1: collapse / resize / gear popover / density ──
function setCollapsed(v){const app=$('.app');if(!app)return;
  if(v)app.dataset.collapsed='1';else app.removeAttribute('data-collapsed');
  _store('bd_cockpit_collapsed',v?'1':'0');}
function initShellControls(){
  const app=$('.app'); if(!app)return;
  const w=parseInt(_load('bd_cockpit_sidew',''),10);
  if(w&&w>=168&&w<=460)app.style.setProperty('--side-w',w+'px');
  if(_load('bd_cockpit_collapsed','0')==='1')app.dataset.collapsed='1';
  if(_load('bd_cockpit_density','')==='compact')app.classList.add('compact');
  $$('#density_seg button').forEach(b=>{
    b.classList.toggle('on',(b.dataset.d==='compact')===app.classList.contains('compact'));
    b.onclick=()=>{const c=b.dataset.d==='compact';app.classList.toggle('compact',c);
      _store('bd_cockpit_density',c?'compact':'');
      $$('#density_seg button').forEach(x=>x.classList.toggle('on',(x.dataset.d==='compact')===c));};});
  const cb=$('#collapsebtn'); if(cb)cb.onclick=()=>setCollapsed(true);
  const rx=$('#reexpand'); if(rx)rx.onclick=()=>setCollapsed(false);
  // mobile off-canvas drawer (<=820px): tap-to-open affordance
  const nt=$('#navtoggle'), scr=$('#navscrim');
  const _navSync=(o)=>{if(nt)nt.setAttribute('aria-expanded',o?'true':'false');if(scr)scr.setAttribute('aria-hidden',o?'false':'true');};
  const openNav=()=>{app.classList.add('navopen');_navSync(true);};
  const closeNav=()=>{app.classList.remove('navopen');_navSync(false);};
  if(nt)nt.onclick=()=>{app.classList.contains('navopen')?closeNav():openNav();};
  if(scr)scr.onclick=closeNav;
  document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&app.classList.contains('navopen'))closeNav();});
  window.addEventListener('resize',()=>{if(window.innerWidth>820&&app.classList.contains('navopen'))closeNav();});
  const gb=$('#gearbtn'), am=$('#appmenu');
  if(gb&&am){
    const setExp=(o)=>gb.setAttribute('aria-expanded',o?'true':'false');
    const placeMenu=()=>{
      if(typeof _isBarLayout==='function'&&_isBarLayout()){
        const r=gb.getBoundingClientRect(); const mw=am.offsetWidth||226;
        const left=Math.max(8,Math.min(r.left, window.innerWidth-mw-8));
        am.style.position='fixed'; am.style.left=left+'px';
        if(document.querySelector('.app').classList.contains('bottombar')){
          am.style.top='auto'; am.style.bottom=(Math.max(8,window.innerHeight-r.top+6))+'px';
        } else { am.style.bottom='auto'; am.style.top=(r.bottom+6)+'px'; }
      } else { am.style.position=''; am.style.left=''; am.style.top=''; am.style.bottom=''; }
    };
    const openM=()=>{am.classList.add('open');setExp(true);placeMenu();};
    const closeM=(refocus)=>{am.classList.remove('open');setExp(false);if(refocus)gb.focus();};
    gb.onclick=(e)=>{e.stopPropagation();am.classList.contains('open')?closeM(false):openM();};
    document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&am.classList.contains('open'))closeM(true);});
    document.addEventListener('click',(e)=>{if(am.classList.contains('open')&&!am.contains(e.target)&&e.target!==gb&&!gb.contains(e.target))closeM(false);});}
  const rz=$('#resizer');
  if(rz){let dragging=false,last=0;
    const move=(e)=>{if(!dragging)return;let x=(e.touches?e.touches[0].clientX:e.clientX);
      x=Math.max(168,Math.min(460,x));last=x;app.style.setProperty('--side-w',x+'px');};
    const up=()=>{if(!dragging)return;dragging=false;rz.classList.remove('drag');document.body.style.userSelect='';
      if(last)_store('bd_cockpit_sidew',String(last));};
    rz.addEventListener('mousedown',(e)=>{dragging=true;rz.classList.add('drag');document.body.style.userSelect='none';e.preventDefault();});
    document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);}
  // Slice 4: keyboard-reachable nav (Enter/Space activate) + help overlay close
  $$('#nav a').forEach(a=>{a.setAttribute('tabindex','0');if(!a.getAttribute('role'))a.setAttribute('role','link');
    a.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();a.click();}});});
  const hp=$('#help'); if(hp)hp.addEventListener('click',e=>{if(e.target.id==='help')hp.classList.remove('open');});
  // Slice 6: pin-to-Everyday
  function _pins(){try{return JSON.parse(_loadRaw('bd_cockpit_pins')||'[]');}catch(e){return [];}}
  window.togglePin=function(p){let pins=_pins();pins=pins.includes(p)?pins.filter(x=>x!==p):pins.concat([p]);_store('bd_cockpit_pins',JSON.stringify(pins));pinnedRender();};
  window.pinnedRender=function(){const pins=_pins(),host=$('#pinned'),sec=$('#pinnedsec');if(!host||!sec)return;
    host.innerHTML='';
    pins.forEach(p=>{const src=document.querySelector('#nav .navdrawer a[data-p="'+p+'"]');if(!src)return;
      const a=document.createElement('a');a.dataset.p=p;a.setAttribute('tabindex','0');a.setAttribute('role','link');
      a.textContent=((src.childNodes[0]&&src.childNodes[0].textContent)||src.textContent).trim();
      const act=(e)=>{e.preventDefault();go(p);};a.onclick=act;a.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')act(e);});
      host.appendChild(a);});
    sec.style.display=pins.length?'':'none';
    $$('#nav .navdrawer a[data-p]').forEach(a=>{const st=a.querySelector('.pinstar');if(st){const on=pins.includes(a.dataset.p);st.textContent=on?'\u2605':'\u2606';const lbl=on?'Unpin from Everyday':'Pin to Everyday';st.title=lbl;st.setAttribute('aria-label',lbl);st.setAttribute('aria-pressed',on?'true':'false');}});
    $$('#morebody a[data-p]').forEach(a=>{const b=a.querySelector('.badge.pinned');if(b)b.hidden=!pins.includes(a.dataset.p);});};
  $$('#nav .navdrawer a[data-p]').forEach(a=>{if(a.querySelector('.pinstar'))return;
    const st=document.createElement('span');st.className='pinstar';st.textContent='\u2606';
    st.setAttribute('role','button');st.setAttribute('tabindex','0');st.setAttribute('aria-label','Pin to Everyday');st.title='Pin to Everyday';
    const tog=(e)=>{e.preventDefault();e.stopPropagation();togglePin(a.dataset.p);};
    st.onclick=tog;st.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')tog(e);});a.appendChild(st);});
  pinnedRender();
}
initShellControls();
renderNavBadges();

// nav drawers (1.0): Advanced/System collapsed by default, toggle persisted
(function(){
  $$('.drawerhead').forEach(h=>{
    const key='bd_cockpit_drawer_'+h.dataset.drawer;
    const dr=h.closest('.navdrawer'); const car=h.querySelector('.caret');
    let open=false; try{open=localStorage.getItem(key)==='1';}catch(e){}
    if(open)dr.classList.add('open'); if(car)car.textContent=open?'\u25be':'\u25b8';
    h.onclick=()=>{
      if(_isBarLayout()){toggleBarDropdown(dr);return;}
      const o=dr.classList.toggle('open');try{localStorage.setItem(key,o?'1':'0');}catch(e){}if(car)car.textContent=o?'\u25be':'\u25b8';
    };
  });
  // auto-open whichever drawer holds the active page so it's never hidden
  const on=$('#nav a.on'); if(on){const dr=on.closest('.navdrawer'); if(dr&&!dr.classList.contains('open')){dr.classList.add('open');const c=dr.querySelector('.caret');if(c)c.textContent='\u25be';}}
})();

// ── Band D: command palette (⌘K / Ctrl+K), keyboard shortcuts, focus mode ──
const NAVITEMS=[...document.querySelectorAll('#nav a')].map(a=>({p:a.dataset.p,label:a.textContent.trim()})).concat(Object.keys(REDIRECT).map(k=>({p:k,label:k})));
let cmdkSel=0, cmdkList=[];
function cmdkOpen(){const o=$('#cmdk');o.classList.add('open');const i=$('#cmdk_in');i.value='';cmdkRender('');setTimeout(()=>i.focus(),0);}
function cmdkClose(){$('#cmdk').classList.remove('open');}
function cmdkRender(q){q=(q||'').toLowerCase();cmdkList=NAVITEMS.filter(n=>n.label.toLowerCase().includes(q)||n.p.includes(q));cmdkSel=0;
  $('#cmdk_res').innerHTML=cmdkList.map((n,i)=>`<div class="res ${i===0?'sel':''}" data-i="${i}">${esc(n.label)}</div>`).join('')||'<div class="res muted">no match</div>';
  $$('#cmdk_res .res[data-i]').forEach(r=>{r.onclick=()=>{go(cmdkList[+r.dataset.i].p);cmdkClose();};});}
function cmdkMove(d){if(!cmdkList.length)return;cmdkSel=(cmdkSel+d+cmdkList.length)%cmdkList.length;
  $$('#cmdk_res .res').forEach((r,i)=>r.classList.toggle('sel',i===cmdkSel));
  const sel=$('#cmdk_res .res.sel');if(sel)sel.scrollIntoView({block:'nearest'});}
$('#cmdk_in').addEventListener('input',e=>cmdkRender(e.target.value));
$('#cmdk_in').addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'){e.preventDefault();cmdkMove(1);}
  else if(e.key==='ArrowUp'){e.preventDefault();cmdkMove(-1);}
  else if(e.key==='Enter'){e.preventDefault();if(cmdkList[cmdkSel]){go(cmdkList[cmdkSel].p);cmdkClose();}}
  else if(e.key==='Escape'){cmdkClose();}});
$('#cmdk').addEventListener('click',e=>{if(e.target.id==='cmdk')cmdkClose();});

// keyboard shortcuts: ⌘K/Ctrl+K palette · / search · f focus · g-then-key go
let gPending=false;
const GMAP={m:'mission',i:'inbox',d:'daily',s:'search',r:'risk',c:'corpus',t:'timeline',a:'activity'};
document.addEventListener('keydown',e=>{
  const typing=/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();cmdkOpen();return;}
  if(typing)return;
  if(e.key==='?'){e.preventDefault();const h=$('#help');if(h)h.classList.toggle('open');return;}
  if(e.key==='Escape'){const h=$('#help');if(h&&h.classList.contains('open')){h.classList.remove('open');return;}}
  if(e.key==='/'){e.preventDefault();go('search');return;}
  if(e.key==='f'){const a=$('.app');if(a)setCollapsed(a.dataset.collapsed!=='1');return;}
  if(e.key==='g'){gPending=true;setTimeout(()=>gPending=false,800);return;}
  if(gPending&&GMAP[e.key]){gPending=false;go(GMAP[e.key]);return;}
});
routeFromHash();
</script></body></html>"""


def register_routes(app) -> None:
    """Register the cockpit blueprint onto the host Flask app. Matches the
    framework_dashboard/fleet integration style; the caller wraps this in
    try/except so the app stays up if tools/ isn't importable."""
    app.register_blueprint(bp)


# Standalone (local viewing without the main app)
if __name__ == "__main__":  # pragma: no cover
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    port = int(os.environ.get("BD_COCKPIT_PORT", "8771"))
    print(f"cockpit console on http://127.0.0.1:{port}/cockpit  "
          f"(reports={cc.reports_root()}, captures={cc.captures_root()})")
    app.run(host="127.0.0.1", port=port)
