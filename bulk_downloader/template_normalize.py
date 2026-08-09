"""Normalize a rich WACZ-builder draft into a runtime reviewed-template-shape
REVIEW CANDIDATE.

The pipeline keeps three concerns separate:
  - the WACZ builder stays RICH (network_discovery, button_hint, source, …),
  - the runtime keeps consuming the reviewed-template shape (selectors with
    download.trigger / download.row_selectors / quality.*, flat
    network_patterns, resolutions), and
  - this module bridges them: it converts a rich draft into a candidate in the
    runtime shape so a human can review + promote it.

Hard rules (mirrored from the spec):
  - never emit ``status: enabled`` — only ``review_ready`` / ``draft_review_required``;
  - ``selectors.download.button_hint`` becomes ``selectors.download.trigger``
    (a click target that may open a modal), NEVER ``row_selectors``;
  - ``row_selectors`` are emitted ONLY when already present, safe (non-blocking
    lint) AND modal-scoped — the normalizer never fabricates row selectors;
  - network patterns are flattened and run through ``pattern_hygiene`` so
    trackers/junk are dropped into ``rejected_patterns``;
  - provenance/source metadata is preserved.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from .pattern_hygiene import scrub_network_patterns
from . import selector_lint as sl

# Selectors that establish a modal/dialog scope — only inside one of these is a
# row selector considered safe enough to keep without manual review.
_MODAL_RE = re.compile(
    r'\[role=["\']?dialog|\baria-modal|\.modal\b|\.ant-modal|\.MuiDialog|'
    r'[#.][\w-]*[-_]modal(?![\w-])|\.drawer\b|\.popover\b|\.dialog\b',
    re.I,
)


def _primary_host(draft: Dict[str, Any]) -> str:
    src = draft.get("source") or {}
    if src.get("host"):
        return str(src["host"])
    for h in (draft.get("match") or {}).get("hosts") or []:
        if h:
            return str(h)
    return str(draft.get("host") or "")


def _is_modal_scoped(selector: str) -> bool:
    return bool(_MODAL_RE.search(selector or ""))


# A download affordance: a CLICK TARGET carrying a DOWNLOAD token. Both halves
# are required, and that pairing is what separates the two populations measured
# on the 742-capture corpus at v3.66.988. The token alone would admit
# `span.download-label` and `div.edge-download-item-dimensions`, which are
# captions; the click target alone would admit `a.nav__link` and
# `a:nth-child(31)`, which is how a honeypot gets into a template.
_CLICK_TARGET_RE = re.compile(
    r"(^|[\s>+~])(a|button)[.\[:#\s]"          # an anchor or button element
    r"|[.\-_](clickable|btn|button)([.\-_\[]|$)",   # or a class that says so
    re.I)
_DL_TOKEN_RE = re.compile(
    r"download"
    r"|(?:^|[.\-_\[])dl(?:[.\-_\]]|$)"          # .dl / ct_dl_button / -dl-
    r"|[.\-_]dl_",
    re.I)
# `quality` and `resolution` are DELIBERATELY absent, and the first draft of this
# rule had both. Adversarial review measured what they cost: a newly admitted row
# is a promote-gate SATISFIER (the gate is trigger|rows|button), so
# `button.vjs-quality-selector` took a streaming-only site with no download
# feature from `draft_review_required` to `review_ready` -- a control that
# downloads nothing, satisfying the download clause. That is the operator's
# "Downloads dropdown beside a Quality dropdown" trap, which a previous session
# had already measured and which this rule reintroduced.
#
# What they bought, measured against the corpus rather than argued: `resolution`
# was pinned by NOTHING -- removing it loses no real control and admits no junk.
# `quality` was pinned by exactly one, `a.video-quality-dropdown-item`, and that
# site (members.nubiles-porn.com) also carries `a.dropdown-downloads-link` at the
# SAME 5-of-9 support, so it loses nothing either. Eight of nine measured real
# controls survive on the download tokens alone, and all eight measured
# streaming-quality controls are refused.


def _is_download_affordance(selector: str) -> bool:
    """Does this selector name a clickable DOWNLOAD control?

    Widening `_map_selectors` from "modal-scoped only" to "modal-scoped OR a
    download affordance" is finding B. The modal rule is not wrong and is not
    removed: an unscoped row selector matching every anchor on a page is exactly
    how a decoy gets in, and on the measured corpus it correctly rejects
    `li.theo-menu-item`, `span.title` and `a:nth-child(31)`.

    But 44 of the 143 rows it dropped were real controls on the operator's own
    member sites -- `a.ct_dl_button` at 30 of 39 captures, `a.download__item`,
    `a.dropdown-downloads-link`. Those sites read GREEN on their trigger while
    the rows that pick the RESOLUTION were being discarded, which is the
    operator's fourth step failing silently on a site the report calls good.

    The honeypot resistance traded away here is not recovered in this function:
    a decoy named `a.download-link` is admissible. It is recovered at the corpus
    level, where a decoy that varies per page-load reads support 1 of N against
    a real control's N of N. Rows admitted this way are recorded in the draft's
    warnings so a reviewer can see which ones were not modal-scoped.
    """
    sel = selector or ""
    return bool(_CLICK_TARGET_RE.search(sel) and _DL_TOKEN_RE.search(sel))


def _api_host(draft: Dict[str, Any]) -> str:
    """An EXPLICIT, builder-provided API host, or "" — never guessed.

    The API often lives on a different subdomain than the page (Reptyle pages
    are app.reptyle.com but the API is api2.reptyle.com), so we never infer the
    API host from the page host or from the top_hosts histogram. We only use a
    host the builder stated outright: ``network_discovery.api_host`` or a
    concrete ``api.base``.
    """
    nd = draft.get("network_discovery") or {}
    if nd.get("api_host"):
        return str(nd["api_host"])
    base = str((draft.get("api") or {}).get("base") or "")
    if base:
        try:
            netloc = urlparse(base).netloc
            if netloc:
                return netloc
        except Exception:
            pass
    return ""


def _map_selectors(draft: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    src = draft.get("selectors") or {}
    out: Dict[str, Any] = {}

    if isinstance(src.get("login"), dict) and src["login"]:
        out["login"] = dict(src["login"])
    if isinstance(src.get("player"), dict) and src["player"]:
        out["player"] = dict(src["player"])

    q = src.get("quality") if isinstance(src.get("quality"), dict) else {}
    qq: Dict[str, Any] = {}
    if q.get("open_menu"):
        qq["open_menu"] = q["open_menu"]
    # Builder names it select_resolution_template; runtime reads resolution_option.
    if q.get("select_resolution_template"):
        qq["resolution_option"] = q["select_resolution_template"]
    elif q.get("resolution_option"):
        qq["resolution_option"] = q["resolution_option"]
    if qq:
        out["quality"] = qq

    d = src.get("download") if isinstance(src.get("download"), dict) else {}
    dd: Dict[str, Any] = {}
    # button_hint (rich) / trigger / button (old flat draft) -> trigger.
    # A "button" is a click target, so it becomes a trigger, never a row.
    hint = d.get("button_hint") or d.get("trigger") or d.get("button")
    if hint:
        dd["trigger"] = hint

    # row_selectors: keep ONLY pre-existing, safe, modal-scoped ones.
    rows: List[str] = []
    raw_rows = d.get("row_selectors")
    if isinstance(raw_rows, (list, tuple)):
        raw_rows = [str(r) for r in raw_rows if r]
    elif isinstance(raw_rows, str) and raw_rows:
        raw_rows = [raw_rows]
    else:
        raw_rows = []
    for rs in raw_rows:
        blocking = sl.has_blocking_issues(sl.lint_selector(rs, role="row"))
        if blocking:
            # Lint OUTRANKS the affordance: naming a selector `download` must
            # not be a way to smuggle in one the linter blocks, or the widening
            # below is a hole rather than a rule.
            warnings.append(
                f"dropped row selector (not modal-scoped or unsafe): {rs}")
        elif _is_modal_scoped(rs):
            rows.append(rs)
        elif _is_download_affordance(rs):
            rows.append(rs)
            # RECORDED, because this row was admitted on its NAME rather than
            # on its scope -- the audit trail that pays for the honeypot
            # resistance the widening trades away (see _is_download_affordance).
            warnings.append(
                f"kept row selector by download affordance, not modal-scoped: {rs}")
        else:
            warnings.append(
                f"dropped row selector (not modal-scoped or unsafe): {rs}")
    if rows:
        dd["row_selectors"] = rows
    else:
        warnings.append(
            "no modal-scoped row selectors derived — add them during manual "
            "review (the download trigger only opens the modal)")

    # Review-only: the observed download-resolution endpoint, host + templated
    # path. A suggestion to confirm during review; never the runtime base.
    if d.get("api_template"):
        dd["api_template"] = str(d["api_template"])

    if dd:
        out["download"] = dd
    else:
        warnings.append("no download selectors derived from draft")

    return out


def _flatten_patterns(
    draft: Dict[str, Any], host: str, warnings: List[str]
) -> tuple[List[str], List[str]]:
    """Return (host_bearing, media_suffix).

    host_bearing patterns (full URLs / relative API paths / flat-draft patterns)
    get run through the tracker/junk/signed-URL scrubber. media_suffix patterns
    (".../AVC_{resolution}.mp4", ".../{manifest}.m3u8") are host-less file-suffix
    matchers — they cannot be a tracker beacon, so they are kept directly.

    Relative API paths (``/api/...``) are kept RELATIVE unless the draft carries
    an explicit API host; we never guess the host from the page or top_hosts.
    """
    nd = draft.get("network_discovery") or {}
    host_bearing: List[str] = []
    media_suffix: List[str] = []
    if nd:
        api_host = _api_host(draft)
        kept_relative = False
        for ap in nd.get("api_patterns") or []:
            ap = str(ap)
            if ap.startswith("/") and api_host:
                host_bearing.append(f"https://{api_host}{ap}")
            else:
                host_bearing.append(ap)  # relative reusable pattern, as-is
                if ap.startswith("/"):
                    kept_relative = True
        for mp in nd.get("media_patterns") or []:
            media_suffix.append(str(mp))
        if kept_relative:
            obs = [str(h) for h in (nd.get("observed_api_hosts") or []) if h]
            hint = (
                f" — download-resolution calls were observed on: "
                f"{', '.join(obs)}; set api{{base}} accordingly"
                if obs else ""
            )
            warnings.append(
                "API patterns kept relative — no explicit API host in the draft; "
                "set a concrete api{base} during review (the API may live on a "
                "different subdomain than the page)" + hint)
    else:
        # Fallback: an old/flat draft. Pass its patterns through hygiene.
        host_bearing = [str(p) for p in (draft.get("network_patterns") or [])]
    return host_bearing, media_suffix


def _resolutions(draft: Dict[str, Any]) -> List[int]:
    vals = list(draft.get("resolution_priority") or [])
    if not vals:
        vals = list((draft.get("network_discovery") or {}).get("resolutions_seen") or [])
    if not vals:
        # Old/flat draft (and runtime shape) carry resolutions at the top level.
        vals = list(draft.get("resolutions") or [])
    qres = ((draft.get("selectors") or {}).get("quality") or {}).get("available_resolutions") or []
    out = set()
    for v in list(vals) + list(qres):
        try:
            out.add(int(v))
        except Exception:
            pass
    return sorted(out, reverse=True)


def _review_workflow(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Wave B: assemble the REVIEW-ONLY observed-workflow block to carry onto the
    review candidate — the operator-recorded `derived_steps`, the resolved
    `trigger_candidate` (the click whose effect first produced media), its
    evidence, the `source` (action_timeline | dom_log), and the advisory `verify`
    readout. This is provenance the reviewer reads; it is NEVER the runtime
    download trigger (that is ``selectors.download.trigger``). Structural only —
    `draft` has already been through ``redact_artifact`` in normalize_draft."""
    wf = draft.get("workflow") or {}
    dl = (draft.get("selectors") or {}).get("download") or {}
    out: Dict[str, Any] = {
        "derived_steps": wf.get("derived_steps") or [],
        "trigger_candidate": dl.get("trigger_candidate") or wf.get("trigger_candidate"),
        "trigger_evidence": wf.get("trigger_evidence"),
        "source": wf.get("source"),
    }
    if wf.get("verify") is not None:
        out["verify"] = wf["verify"]
    return out


def normalize_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a rich builder draft into a runtime-shape review candidate."""
    # F2 / wave 166: scrub the incoming draft before deriving anything durable.
    # Defensive: covers drafts that did NOT come through build_template's own
    # redaction (hand-authored, flat, or loaded from disk). Value-content only,
    # so hosts / templated patterns / selector shapes used below are preserved.
    from .capture_artifact_redact import redact_artifact
    draft = redact_artifact(draft)
    warnings: List[str] = []
    host = _primary_host(draft)

    selectors = _map_selectors(draft, warnings)
    host_bearing, media_suffix = _flatten_patterns(draft, host, warnings)
    scrub = scrub_network_patterns(host_bearing)
    network_patterns = scrub["kept"] + media_suffix
    rejected_patterns = scrub["dropped"]
    resolutions = _resolutions(draft)

    if not network_patterns:
        warnings.append("no reusable network patterns derived")
    if not resolutions:
        warnings.append("no resolutions derived")

    # Lint the candidate in its runtime shape; surface issues, gate readiness.
    lint_issues = sl.lint_template({"selectors": selectors})
    blocking = sl.has_blocking_issues(lint_issues)
    for i in lint_issues:
        warnings.append(f"lint[{i.level}] {i.role}: {i.message}")

    dl = selectors.get("download") or {}
    has_download = bool(dl.get("trigger") or dl.get("row_selectors"))
    ready = has_download and bool(network_patterns) and bool(resolutions) and not blocking
    status = "review_ready" if ready else "draft_review_required"

    src = draft.get("source") or {}
    evidence = {
        "dom_log_count": src.get("dom_log_count"),
        "network_log_count": src.get("network_log_count"),
    }

    # Review-only API-host metadata. The host that served the download-resolution
    # API is surfaced as a SUGGESTION for the reviewer — it never sets the runtime
    # api base and patterns stay relative (policy: observed host only, human-
    # confirmed). Direct-stream sites have no API host, so this stays None.
    _nd = draft.get("network_discovery") or {}
    observed_api_hosts = [str(h) for h in (_nd.get("observed_api_hosts") or []) if h]
    api_base_candidate = None
    if _nd.get("api_patterns") and len(set(observed_api_hosts)) == 1:
        api_base_candidate = f"https://{observed_api_hosts[0]}"
    media_hosts = [str(h) for h in (_nd.get("media_hosts") or []) if h]

    return {
        "schema": "bulk_downloader.template.review_candidate.v1",
        "normalized_from": draft.get("schema_version") or draft.get("schema"),
        "status": status,  # never "enabled"
        "host": host or "unknown-host",
        "confidence": draft.get("confidence", "low"),
        "match": draft.get("match") or {"hosts": [host] if host else [], "url_patterns": []},
        "selectors": selectors,
        "network_patterns": network_patterns,
        "resolutions": resolutions,
        "source": src,
        "source_capture": src.get("capture_file") or draft.get("source_capture"),
        "evidence_counts": evidence,
        "observed_api_hosts": observed_api_hosts,   # review-only metadata
        "api_base_candidate": api_base_candidate,   # review-only suggestion; NOT the runtime base
        # A6-1: a CONCRETE review-only base+named-endpoints candidate the reviewer
        # accepts at promotion. Present ONLY when the builder derived one from real
        # observed requests; never the runtime ``api`` block (so build_api_url and
        # the relative-pattern rule stay gated — v3.66.155 / v3.66.157).
        **({"api_candidate": draft["api_candidate"]} if draft.get("api_candidate") else {}),
        "media_hosts": media_hosts,                 # review-only metadata
        # Wave B: the operator-recorded observed workflow (derived_steps +
        # resolved trigger_candidate + source/verify) as REVIEW-ONLY provenance,
        # so the recorded actions reach the review surface. Present only when the
        # draft carried one; never runtime (download runs off
        # `selectors.download.trigger`).
        **({"workflow": _review_workflow(draft)} if (
            draft.get("workflow")
            or ((draft.get("selectors") or {}).get("download") or {}).get("trigger_candidate")
        ) else {}),
        "safety_notes": draft.get("guardrails") or draft.get("safety_notes") or [],
        "warnings": warnings,
        "rejected_patterns": rejected_patterns,
        "review_notes": [
            "Normalized from a rich WACZ-builder draft into the runtime reviewed shape.",
            "Add the concrete api{base, paths} and modal-scoped row_selectors during review.",
            "download.trigger is a click target (may open a modal), not a final row.",
            "Never auto-enabled; promote explicitly after review.",
        ],
    }
