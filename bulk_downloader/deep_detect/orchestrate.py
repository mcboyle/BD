from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ._common import (PROGRESSIVE_MEDIA_EXTENSIONS, RESOLUTION_TIERS, STREAM_MANIFEST_EXTENSIONS, _DD_COUNTERS, _count_by_type, _parse_content_disposition, _score_to_confidence, _selector_for, _url_path)
from .candidates import (_apply_signed_url_annotations, _attach_confidence, _attach_confidence_ceiling, _bk_accepted, _bk_rejected_raw, _bk_set_accepted, _bk_warnings, _build_disclaimers, _dedup_candidates, _finalize_buckets, _flatten_download_candidates, score_download_link)
from .csp import (_apply_csp_annotations, _extract_csp_from_headers, _extract_csp_from_html)
from .http_probe import (_poll_async_workflow, _probe_head, _refine_source_type_from_headers, follow_meta_refresh)
from .login import (detect_post_reveal_forms, scan_blockers, scan_links_for_traps, score_login_page)
from .manifests import (is_dash_manifest, is_hls_manifest, is_smooth_manifest, parse_dash_mpd, parse_hls_master, parse_smooth_streaming)
from .providers import (extract_jsonld_media, extract_player_configs, extract_provider_embeds, extract_state_blob_urls)
from .resolution import (extract_resolution_cards)
from .urls import (classify_url)


def _annotate_download_candidate(c: dict, blockers: dict) -> dict:
    """Final pass over a download candidate: attach page-level blocker
    context as a single concise warning, avoiding duplication with
    per-candidate signals already attached (e.g. an HLS manifest that
    already detected its own encryption shouldn't also carry the
    generic page-level DRM warning).

    v3.66.10 changes from the earlier implementation:
      • Page-level DRM no longer adds BOTH a reason and a warning
        saying the same thing.
      • Candidates that already have an explicit encryption marker in
        their own warnings (HLS/DASH parsers set these) are not
        re-flagged — the per-candidate signal is more reliable.
      • CAPTCHA warning is only added when the candidate is a
        click-driven flow (where the CAPTCHA actually gates the
        download); pure URL-only candidates aren't blocked by page
        CAPTCHAs since the URL bypasses the page UI.
    """
    c.setdefault("warnings", [])
    own_warnings_text = " ".join(c.get("warnings") or []).lower()
    own_signals_drm = any(t in own_warnings_text
                          for t in ("encryption", "drm",
                                    "contentprotection"))

    if blockers.get("drm_or_encryption") and not own_signals_drm:
        c["warnings"].append(
            "page-level DRM/encryption markers detected — heuristic; "
            "verify before downloading")

    # CAPTCHA warning only matters for candidates that go through the
    # page's UI (click flow or workflow POST). A direct URL with a
    # signed token won't see the CAPTCHA at all.
    if blockers.get("captchas"):
        if (c.get("requires_click")
                or c.get("needs_workflow")
                or c.get("source_type") == "two_step_post_reveal"):
            c["warnings"].append(
                "page has CAPTCHA; this candidate goes through the "
                "page UI and may require human interaction")
    return c


def deep_detect(html: str, *,
                base_url: str = "",
                prefer_resolution: str = "highest",
                site_memory: Optional[dict] = None,
                resolve_providers: bool = False,
                http_get=None,
                signing_callback=None,
                response_headers: Optional[dict] = None) -> dict:
    """Run every detector in this module and return a single ranked
    report.

    Parameters:
        html              — page HTML (or manifest text; we sniff).
        base_url          — used to resolve relative URLs anywhere in
                            the document.
        prefer_resolution — "highest" (default) ranks higher rank first.
                            Pass a label ("1080p", "4k", "8k", etc.)
                            to bias toward that target.
        site_memory       — v3.66.15 P5: learned.deep_detect block from a
                            site config. When supplied, applies modest
                            scoring biases:
                              • prefer_resolution defaults to memory's
                                preferred_resolution when caller passed
                                "highest" (the default)
                              • candidates whose source_type appears in
                                memory.winning_source_types get a small
                                "+repeat_winner" reason and +N bonus
                                where N = min(count, MEMORY_BONUS_CAP)
                            Never *rejects* a candidate; never produces
                            new candidates. Pass `None` (default) for
                            the legacy stateless behaviour — used by
                            most tests and any caller without site
                            context.
        resolve_providers — v3.66.16 P4: when True, call provider APIs
                            (Vimeo `/config`, etc.) to turn
                            ``needs_provider_resolution: True``
                            candidates into playable candidates. Network
                            access happens via ``http_get`` (or httpx
                            by default). Resolution outcomes are
                            captured in ``report["provider_resolutions"]``;
                            successfully-resolved streams are appended
                            to ``download_candidates``. Off by default —
                            ``deep_detect`` stays a pure HTML→report
                            function unless the caller opts in. Errors
                            are non-fatal: the original embed candidate
                            stays in the report when resolution fails.
        http_get          — v3.66.16 P4: optional ``(url) -> (status,
                            headers, body_bytes)`` callable used by
                            ``resolve_providers``. Tests inject a fake;
                            production leaves this None and the
                            resolver falls back to httpx.
        response_headers  — v3.66.20 P12: optional case-insensitive
                            mapping of HTTP response headers from the
                            page fetch. When supplied, ``Content-Security-
                            Policy`` and ``Content-Security-Policy-Report-
                            Only`` header values are merged with any
                            ``<meta http-equiv>`` CSP found in the HTML
                            (header takes precedence per-directive,
                            since header CSP is the more authoritative
                            policy for media). Header values may be a
                            comma-separated list of policies (per spec);
                            we parse each, intersect the directive
                            sets, and use the resulting effective
                            policy for annotation. Leaving this None
                            preserves the legacy meta-only behaviour
                            used by every caller before v3.66.20.

    Output shape:
        {
            "best_login":          dict | None,    # from item 6
            "best_download":       dict | None,    # top non-rejected
            "login_candidates":    [...],
            "download_candidates": [...],
            "rejected":            [...],          # trap-link rejects
            "warnings":            [...],          # session-level
            "blockers":            { ... },        # from item 8
            "provider_embeds":     [...],          # item 4 verbatim
            "workflow_required":   dict | None,    # top post-reveal
            "manifests": {
                "hls":  dict | None,                # parse_hls_master result
                "dash": dict | None,                # parse_dash_mpd result
                "smooth": dict | None,              # parse_smooth_streaming result
            },
            "source_breakdown": { type: count },   # how many of each
            "buckets": {                            # F7 (additive)
                "accepted": [...],                  # == download_candidates
                "rejected": [{"url", "reasons": [...]}],
                "warnings": [...],                  # == warnings
                "counts": {accepted, rejected, warnings},
            },
        }
    """
    out = {
        "best_login": None,
        "best_download": None,
        "login_candidates": [],
        "download_candidates": [],
        "rejected": [],
        "warnings": [],
        "blockers": {},
        "provider_embeds": [],
        "workflow_required": None,
        "manifests": {"hls": None, "dash": None, "smooth": None},
        "source_breakdown": {},
        # v3.66.15 (P12) / v3.66.20: parsed CSP from <meta> tag and/or
        # response headers, or None if neither was supplied. When both
        # are present they are intersected (header takes precedence
        # per-directive — the browser must satisfy ALL policies).
        # Shape: {"policy": str, "directives": {dir: [src, ...]}, ...}.
        "csp": None,
        # v3.66.16 (P4): per-embed resolution outcomes when the caller
        # passes resolve_providers=True. Each entry is
        # ``{"provider": str, "video_id": str|None, "ok": bool,
        #   "candidates": int, "error": str|None}``.  Empty list when
        # resolve_providers=False or the page has no provider embeds.
        "provider_resolutions": [],
    }
    if not isinstance(html, str) or not html.strip():
        return _finalize_buckets(out, accepted=[])

    # v3.66.15 (P5): if caller didn't override prefer_resolution
    # (i.e. left it at the "highest" default) AND site_memory has a
    # learned preference, use the memory's preference. Caller-passed
    # values always win — this only fills in the unset case.
    if (prefer_resolution == "highest"
            and isinstance(site_memory, dict)
            and site_memory.get("preferred_resolution")):
        prefer_resolution = site_memory["preferred_resolution"]

    # Manifest-as-input: if the caller passes an .m3u8 or .mpd, we
    # don't have a page — just emit the variants as the candidate
    # list. The HTML-as-input path runs below.
    #
    # v3.66.11 (bug JJ): order is HLS first then DASH. They're
    # mutually exclusive in practice (HLS starts with #EXTM3U, DASH
    # starts with <?xml/<MPD), but the previous `is_hls_master(html)
    # or is_hls_manifest(html)` was redundant — every master is also
    # a manifest. Simplified to a single is_hls_manifest() check; the
    # parser distinguishes master vs media internally.
    if is_hls_manifest(html):
        hls = parse_hls_master(html, base_url=base_url)
        out["manifests"]["hls"] = hls
        out["warnings"].extend(hls.get("warnings") or [])
        if hls.get("drm_or_encryption_detected"):
            out["warnings"].append(
                "HLS encryption detected; do not bypass")
        cands = _flatten_download_candidates(
            resolution_cards=[], hls_master=hls, dash_mpd=None,
            state_urls=[], provider_embeds=[],
            player_configs=[], jsonld_media=[], post_reveal=[],
            base_url=base_url)
        # If this was a MEDIA playlist (not a master), the flatten
        # step emitted zero candidates — but the input itself IS a
        # playable stream URL when the caller knows the base_url.
        # Surface a single manifest candidate so download_candidates
        # is non-empty. v3.66.10 fix.
        if not cands and hls.get("kind") == "hls_media":
            # v3.66.14 (P18): the v3.66.10 fix required `base_url` to
            # be truthy. Without it, an HLS media playlist as input
            # silently produced zero candidates — which surprises
            # callers passing the manifest body in isolation. We now
            # emit a synthetic candidate in both cases:
            #
            #   * with base_url:    url=base_url, score=80 (the
            #                       v3.66.10 behaviour, unchanged)
            #   * without base_url: url=None, score=40, requires a
            #                       base_url to materialize, reason
            #                       explains the action. needs_workflow
            #                       is set so the candidate's ceiling
            #                       reflects "needs more context".
            #
            # Either way `download_candidates` is non-empty and the
            # caller can see the input was *valid*, just incomplete.
            if base_url:
                cands = [{
                    "url": base_url,
                    "source_type": "hls_manifest",
                    "score": 80,
                    "resolution": None,
                    "codec": None,
                    "fps": None,
                    "size_bytes": None,
                    "found_in": "manifest_input:hls_media",
                    "reasons": ["input is an HLS media playlist; "
                                "no variants to choose from but the "
                                "manifest itself is playable"],
                    "warnings": list(hls.get("warnings") or []),
                    "requires_click": False,
                }]
            else:
                cands = [{
                    "url": None,
                    "source_type": "hls_manifest",
                    "score": 40,
                    "resolution": None,
                    "codec": None,
                    "fps": None,
                    "size_bytes": None,
                    "found_in": "manifest_input:hls_media",
                    "reasons": ["input is an HLS media playlist; "
                                "supply base_url to make the manifest "
                                "URL itself the download target"],
                    "warnings": list(hls.get("warnings") or []) + [
                        "HLS media playlist input has no base_url; "
                        "re-run with base_url=<manifest URL> to "
                        "materialize the candidate"],
                    "requires_click": False,
                    "needs_workflow": True,
                }]
        cands = _dedup_candidates(cands)
        # v3.66.15 (P5): bias before sort
        _apply_site_memory_bias(cands, site_memory)
        cands.sort(key=lambda c: c.get("score", 0), reverse=True)
        _attach_confidence(cands)  # v3.66.14 (P7)
        _attach_confidence_ceiling(cands)  # v3.66.14 (P9)
        out["disclaimers"] = _build_disclaimers(out)  # v3.66.14 (P17)
        return _finalize_buckets(out, accepted=cands)  # F7 phase 2

    if is_dash_manifest(html):
        dash = parse_dash_mpd(html, base_url=base_url)
        out["manifests"]["dash"] = dash
        out["warnings"].extend(dash.get("warnings") or [])
        cands = _flatten_download_candidates(
            resolution_cards=[], hls_master=None, dash_mpd=dash,
            state_urls=[], provider_embeds=[],
            player_configs=[], jsonld_media=[], post_reveal=[],
            base_url=base_url)
        cands = _dedup_candidates(cands)
        # v3.66.15 (P5): bias before sort
        _apply_site_memory_bias(cands, site_memory)
        cands.sort(key=lambda c: c.get("score", 0), reverse=True)
        _attach_confidence(cands)  # v3.66.14 (P7)
        _attach_confidence_ceiling(cands)  # v3.66.14 (P9)
        out["disclaimers"] = _build_disclaimers(out)  # v3.66.14 (P17)
        return _finalize_buckets(out, accepted=cands)  # F7 phase 2

    # F5: Smooth Streaming (.ism/Manifest). POSTURE — we REPORT the
    # QualityLevels and any <Protection>/DRM, but do NOT synthesize the
    # Fragment URL templates into a playable stream (that's segment
    # reassembly, which is declined). So we surface the parse + warnings
    # and leave download_candidates empty rather than fabricating URLs.
    if is_smooth_manifest(html):
        smooth = parse_smooth_streaming(html, base_url=base_url)
        out["manifests"]["smooth"] = smooth
        out["warnings"].extend(smooth.get("warnings") or [])
        if smooth.get("drm_or_encryption_detected"):
            out["warnings"].append(
                "Smooth Streaming DRM detected; do not bypass")
        else:
            out["warnings"].append(
                "Smooth Streaming manifest parsed: "
                f"{len(smooth.get('video') or [])} video / "
                f"{len(smooth.get('audio') or [])} audio quality "
                "level(s). Fragment reassembly is not performed; "
                "use a stream-aware downloader on the manifest URL.")
        out["disclaimers"] = _build_disclaimers(out)  # v3.66.14 (P17)
        return _finalize_buckets(out, accepted=[])  # F7 phase 2 (no cands)

    # Blockers first — the rest of the orchestrator consults this.
    blockers = scan_blockers(html, base_url=base_url,
                             site_memory=site_memory)
    out["blockers"] = blockers
    out["warnings"].extend(blockers.get("warnings") or [])

    # Login
    login = score_login_page(html, base_url=base_url,
                             site_memory=site_memory)
    out["login_candidates"] = login.get("candidates") or []
    out["best_login"] = login.get("best")
    out["warnings"].extend(login.get("warnings") or [])

    # Resolution cards (the screenshot pattern)
    cards = extract_resolution_cards(html, base_url=base_url)

    # Provider embeds
    embeds = extract_provider_embeds(html, base_url=base_url)
    out["provider_embeds"] = embeds

    # Player configs
    players = extract_player_configs(html, base_url=base_url)

    # State blobs
    state_urls = extract_state_blob_urls(html, base_url=base_url)

    # JSON-LD media
    jsonld = extract_jsonld_media(html, base_url=base_url)

    # Two-step POST reveal forms
    post_reveal = detect_post_reveal_forms(html, base_url=base_url,
                                           site_memory=site_memory)
    if post_reveal:
        # The strongest workflow becomes the top-level `workflow_required`.
        post_reveal.sort(key=lambda f: f.get("confidence", 0),
                         reverse=True)
        out["workflow_required"] = post_reveal[0]

    # Trap-link rejection scan
    rejected = scan_links_for_traps(html, base_url=base_url)
    out["rejected"] = rejected

    # Flatten everything into one ranked candidate list.
    flat = _flatten_download_candidates(
        resolution_cards=cards,
        hls_master=None, dash_mpd=None,
        state_urls=state_urls,
        provider_embeds=embeds,
        player_configs=players,
        jsonld_media=jsonld,
        post_reveal=post_reveal,
        base_url=base_url,
    )
    flat = _dedup_candidates(flat)

    # v3.66.16 (P4): resolve provider embeds (Vimeo, etc.) into
    # playable candidates when the caller opts in. Off by default
    # so deep_detect stays a pure HTML->report function unless the
    # caller threads an http_get through. Resolution happens AFTER
    # the dedup pass because resolved CDN URLs are structurally
    # different from anything the HTML parsers produce, so dedup
    # has nothing to collapse them with. Failures are surfaced in
    # ``out["provider_resolutions"]``; the original
    # ``needs_provider_resolution: True`` candidate is left in the
    # report either way as a fallback for the operator.
    if resolve_providers and embeds:
        from bulk_downloader.provider_resolve import resolve_provider_embed
        for e in embeds:
            ids = e.get("ids") or {}
            # Match P5's last_id convention so the outcome row is
            # comparable to learn.py's bookkeeping.
            video_id = (
                ids.get("video_id")
                or ids.get("clip_id")
                or ids.get("entry_id")
                or ids.get("playback_id")
                or (next(iter(ids.values())) if ids else None))
            # C2 (v3.66.x): attach a per-site signing_callback to
            # self-hosted JWPlayer embeds so operators with legitimate
            # credentials can supply their own signed fetcher (the hook
            # is read off the embed dict by resolve_provider_embed). Only
            # jwplayer consumes it; other providers ignore the key. A
            # shallow copy keeps the outcome-row reference to `e` clean.
            _e = e
            if signing_callback is not None and e.get("provider") == "jwplayer":
                _e = {**e, "signing_callback": signing_callback}
            new_cands, err = resolve_provider_embed(
                _e, http_get=http_get, site_memory=site_memory)
            out["provider_resolutions"].append({
                "provider": e.get("provider"),
                "video_id": video_id,
                "ok": (err is None and bool(new_cands)),
                "candidates": len(new_cands),
                "error": err,
            })
            if new_cands:
                flat.extend(new_cands)
            elif err:
                # Per-embed warning so the operator can see why
                # resolution didn't produce streams for this one.
                out["warnings"].append(
                    f"Provider resolution failed for "
                    f"{e.get('provider')}: {err}")

    # v3.66.15 (P12): CSP / mixed-content awareness. Extract any
    # <meta http-equiv="Content-Security-Policy"> from the page and
    # annotate candidates that would be blocked by the policy or by
    # the browser's mixed-content rules. Annotations are side-effects
    # only — we never reject a candidate for CSP/mixed-content; the
    # operator may legitimately want to fetch a violating URL outside
    # the browser context (e.g. inspecting a manifest body).
    #
    # v3.66.20 (P12 follow-up): when `response_headers` is supplied,
    # also extract CSP from `Content-Security-Policy` /
    # `Content-Security-Policy-Report-Only` headers and intersect
    # with the meta CSP. Header CSP is the more authoritative layer
    # for media-src; the merge picks the strictest allow-list when
    # both are present.
    meta_csp = _extract_csp_from_html(html)
    header_csp = _extract_csp_from_headers(response_headers)
    out["csp"] = _merge_csp(meta_csp, header_csp)
    _apply_csp_annotations(flat, out["csp"], base_url, out["warnings"])

    # Apply preference bias if not "highest".
    pref = (prefer_resolution or "highest").lower().strip()
    pref_unknown = False  # tracks "tier label not in our table"
    pref_fallback = False  # tracks "no exact-match candidate found"
    pref_resolution_less = False  # tracks "all candidates lack resolution"
    if pref != "highest":
        # Find the target rank from the tier table.
        target_rank = None
        for label, rank, _w, _h, _terms in RESOLUTION_TIERS:
            if label == pref:
                target_rank = rank
                break
        if target_rank is None:
            # v3.66.14 (P14): the operator asked for a tier we don't
            # recognize. Don't silently apply zero bias — flag it so
            # they can fix the typo.
            pref_unknown = True
        else:
            # Strategy: every candidate at the target tier gets a huge
            # bonus that beats any other tier's base score. Higher
            # tiers get a heavy penalty so they don't overshoot the
            # preference; lower tiers get a smaller penalty so they
            # can still surface as fallbacks if the target tier is
            # absent.
            #
            # v3.66.11 (bug KK): resolution-less candidates (provider
            # embeds, anchor heuristics, extensionless candidates
            # awaiting HEAD probe) previously got `continue`'d — so
            # the user's preference had ZERO effect on them. If every
            # candidate was resolution-less, the operator's "I want
            # 4K" was silently ignored. Now they get the same soft
            # undershoot penalty as known-lower-resolution candidates:
            # still surface as fallbacks, but cede ranking to any
            # candidate that DOES match the requested tier. Doesn't
            # apply when preference is "highest" (this branch isn't
            # reached then).
            n_at_target = 0
            n_resolution_less = 0
            for c in flat:
                r = (c.get("resolution") or {}).get("rank")
                if r is None:
                    c["score"] = (c.get("score") or 0) - 100
                    n_resolution_less += 1
                    continue
                if r == target_rank:
                    c["score"] = (c.get("score") or 0) + 500
                    n_at_target += 1
                elif r > target_rank:
                    # overshoot — strongly disfavor
                    c["score"] = (c.get("score") or 0) - 300
                else:
                    # undershoot — soft disfavor
                    c["score"] = (c.get("score") or 0) - 100
            # v3.66.14 (P14): record fallback state. Two distinct
            # cases get distinct messages so callers can react
            # appropriately (e.g. retry with a different tier vs
            # accept best-available).
            if flat and n_at_target == 0:
                if n_resolution_less == len(flat):
                    pref_resolution_less = True
                else:
                    pref_fallback = True

    # v3.66.14 (P14): emit per-candidate reasons + top-level warning
    # for the cases where the bias didn't produce an exact match.
    # Done here, after the bias has shifted scores but before the
    # final sort, so the resulting ordering reflects the fallback
    # while the explanation rides along on each candidate.
    if pref_unknown:
        msg = (f"prefer_resolution={prefer_resolution!r} is not a "
               f"recognized tier label; bias was not applied")
        out["warnings"].append(msg)
        for c in flat:
            c.setdefault("reasons", []).append(
                f"prefer_resolution={prefer_resolution!r} unknown; "
                f"ranking is the unbiased default")
    elif pref_fallback:
        msg = (f"prefer_resolution={prefer_resolution!r} requested but "
               f"no candidate at that tier; falling back to best "
               f"available")
        out["warnings"].append(msg)
        for c in flat:
            c.setdefault("reasons", []).append(
                f"prefer_resolution={prefer_resolution!r} requested; "
                f"this candidate is a fallback (no exact-tier match)")
    elif pref_resolution_less:
        msg = (f"prefer_resolution={prefer_resolution!r} requested but "
               f"all candidates lack resolution metadata; ranking "
               f"reflects soft fallback")
        out["warnings"].append(msg)
        for c in flat:
            c.setdefault("reasons", []).append(
                f"prefer_resolution={prefer_resolution!r} requested; "
                f"candidate has no resolution metadata to match against")

    # Annotate each candidate with blocker-derived warnings.
    for c in flat:
        _annotate_download_candidate(c, blockers)

    # Reject candidates that the trap penalizer's URLs would flag.
    rejected_urls = {r.get("url") for r in rejected if r.get("url")}
    keep, dropped = [], []
    for c in flat:
        if c.get("url") and c["url"] in rejected_urls:
            c.setdefault("warnings", []).append(
                "URL matches a trap-link signature")
            dropped.append(c)
        else:
            keep.append(c)
    out["rejected"] = dropped + out["rejected"]

    # v3.66.10: signed-URL annotation. For each candidate with a real
    # URL, fingerprint its query string against the known signing
    # schemes and either attach metadata or reject if expired.
    # Helper is shared with deep_detect_live's active-surfacing path
    # so anchors added there also get annotated.
    keep = _apply_signed_url_annotations(
        keep, out["rejected"], out["warnings"])

    # v3.66.15 (P5): apply site_memory scoring bias. Modest nudge to
    # surfaces that have produced winners on this site before, so
    # that in tied or near-tied rankings the historically-correct
    # surface floats up. Never rejects, never produces new candidates.
    _apply_site_memory_bias(keep, site_memory)

    # Sort by score, then by URL stability (prefer entries that have
    # a real URL over needs_provider_resolution / needs_workflow ones
    # at the same score).
    keep.sort(key=lambda c: (
        c.get("score", 0),
        1 if c.get("url") and not c.get("needs_provider_resolution")
              and not c.get("needs_workflow") else 0,
    ), reverse=True)
    _attach_confidence(keep)  # v3.66.14 (P7)
    _attach_confidence_ceiling(keep)  # v3.66.14 (P9)
    # Login candidates use a different scoring helper (`score_login_page`)
    # but the same confidence semantics apply. The login schema uses
    # `raw_score` rather than `score`; pass it through the same curve.
    for lc in out["login_candidates"]:
        lc["confidence_calibrated"] = _score_to_confidence(
            int(lc.get("raw_score") or 0), "login_form")
    if out["best_login"]:
        out["best_login"]["confidence_calibrated"] = _score_to_confidence(
            int(out["best_login"].get("raw_score") or 0), "login_form")
    # F7 phase 2 (v3.66.45): `buckets` is now the canonical store; the
    # flat keys (download_candidates / best_download / rejected /
    # warnings) are retired by _finalize_buckets. `keep` is the final
    # sorted accepted list; out['rejected'] holds the raw reject dicts
    # accumulated during the build.
    _src_breakdown = _count_by_type(keep)
    out["disclaimers"] = _build_disclaimers(out)  # reads buckets/flat warns
    return _finalize_buckets(out, accepted=keep,
                             rejected_raw=out.get("rejected") or [],
                             source_breakdown=_src_breakdown)


def _apply_site_memory_bias(cands: List[dict],
                            site_memory: Optional[dict]) -> None:
    """v3.66.15 (P5): nudge candidate scores using site memory.

    For each candidate whose source_type appears in
    site_memory["winning_source_types"], add a bounded bonus to the
    candidate's score and append a "+repeat_winner" reason. Provider
    embeds with cached IDs also get a small bonus so that an embed
    we've seen before is preferred over a freshly-discovered one
    (Phase 4 will use the cached id to skip a network round-trip).

    Modifications are in-place. No-op if site_memory is None / not a
    dict / missing the relevant keys.

    Bonus cap: +5 per candidate (count is clamped). This is small
    enough that a fresh high-score candidate still beats a stale
    low-score one, but large enough to break ties consistently. With
    P7's confidence ceiling and P9's signal-based caps, a +5 score
    bonus typically moves confidence by 1–3 percentage points.
    """
    if not isinstance(site_memory, dict):
        return
    wst = site_memory.get("winning_source_types")
    if not isinstance(wst, dict):
        wst = {}
    pes = site_memory.get("provider_embeds_seen")
    if not isinstance(pes, dict):
        pes = {}

    # Bonus is bounded so a long-tenured site can't drown out other
    # signals. Five points is roughly half of the smallest meaningful
    # score gap observed in dd-score-audit on the v3.66.14 corpus.
    BONUS_CAP = 5

    for c in cands:
        st = c.get("source_type")
        bonus = 0
        if st and st in wst:
            count = int(wst.get(st, 0) or 0)
            if count > 0:
                bonus = min(count, BONUS_CAP)
                c["score"] = (c.get("score") or 0) + bonus
                reasons = c.setdefault("reasons", [])
                reasons.append(
                    f"+repeat_winner ({st} won {count}× on this site)")
        # Provider-embed-specific small bonus: if this candidate is a
        # provider embed whose provider we've seen on this site, +2
        # (also bounded). Separate path because provider embeds
        # don't typically share source_type with the winning history
        # of *resolved* URLs.
        if c.get("needs_provider_resolution"):
            found_in = c.get("found_in") or ""
            # found_in is "provider:<name>" per _flatten_download_candidates
            if found_in.startswith("provider:"):
                prov = found_in.split(":", 1)[1]
                if prov in pes and isinstance(pes[prov], dict):
                    c["score"] = (c.get("score") or 0) + 2
                    reasons = c.setdefault("reasons", [])
                    reasons.append(
                        f"+repeat_provider ({prov} seen on this site)")


def _merge_csp(meta_csp: Optional[dict],
               header_csp: Optional[dict]) -> Optional[dict]:
    """v3.66.20 — combine meta CSP and header CSP.

    Per spec, multiple policies are intersected: a candidate must
    satisfy every policy. We model that with a per-directive
    intersection. Where only one of the two sources mentions a
    directive, that directive's sources are used as-is (the other
    source effectively had no opinion on it, falling back to
    default-src at match time).

    Returns None when both inputs are None. Returns the non-None
    one when only one is supplied. Otherwise returns a merged dict
    with shape ``{"policy": str, "directives": dict, "sources":
    {"meta": dict|None, "header": dict|None}}``.

    Note: the result's ``directives`` field is what
    ``_apply_csp_annotations`` consumes. The original ``meta`` /
    ``header`` fields are preserved for the report so callers
    (notably the UI) can show *which* layer flagged a violation.
    """
    if meta_csp is None and header_csp is None:
        return None
    if header_csp is None:
        return meta_csp
    if meta_csp is None:
        return header_csp

    meta_d = meta_csp.get("directives") or {}
    head_d = header_csp.get("directives") or {}
    merged: Dict[str, List[str]] = {}
    all_dirs = set(meta_d.keys()) | set(head_d.keys())
    for d in all_dirs:
        if d in meta_d and d in head_d:
            common = [s for s in head_d[d] if s in set(meta_d[d])]
            # If the intersection is empty, the policies are mutually
            # exclusive for this directive — the browser would block
            # everything. Represent that as the explicit-deny token
            # so _candidate_violates_csp treats it as 'none'.
            merged[d] = common if common else ["'none'"]
        elif d in meta_d:
            merged[d] = list(meta_d[d])
        else:
            merged[d] = list(head_d[d])

    return {
        "policy": (
            f"meta: {meta_csp.get('policy', '')} | "
            f"header: {header_csp.get('policy', '')}"
        ),
        "directives": merged,
        "sources": {"meta": meta_csp, "header": header_csp},
    }


def _fetch_manifest_capped(
        http,
        url: str,
        *,
        cap_bytes: int,
        timeout: float = 5.0,
        headers: Optional[dict] = None,
) -> dict:
    """v3.66.12 (roadmap P1): fetch a manifest URL with a hard byte
    cap, streaming when possible.

    The pre-v3.66.12 behaviour was `body = resp.text` — which buffers
    the ENTIRE response body into memory before we truncate. A
    hostile server returning 100 MB of garbage at an .m3u8 endpoint
    would consume 100 MB of process memory before our cap fired.
    That's a denial-of-service vector now closed.

    Strategy:
      1. Try `http.stream("GET", url, ...)` — real httpx supports it
         and returns a context-manager response we iterate byte by
         byte, capping at `cap_bytes+1` so we know if we truncated.
      2. If the http client lacks `.stream()` (test stubs, the
         non-httpx adapter), fall back to `http.get(...)` — same
         behaviour as before, capping `.text` post-hoc. This keeps
         the existing tests working without changes.

    Returns a dict with the shape:
        {
            "ok":           bool,    # 2xx/3xx response received
            "status":       int,     # HTTP status
            "body":         str,     # decoded, capped at cap_bytes
            "truncated":    bool,    # True if we hit the cap
            "final_url":    str,     # post-redirect URL or request URL
            "content_type": str,     # header value (lowercased)
            "error":        str,     # None if ok; else message
        }

    All exceptions caught — never raises. Caller can branch on `ok`
    and `error` without try/except.
    """
    out = {
        "ok": False,
        "status": 0,
        "body": "",
        "truncated": False,
        "final_url": url,
        "content_type": "",
        "error": None,
    }
    if http is None:
        out["error"] = "no http client"
        return out

    headers = headers or {}

    # ── Path A: streaming. Real httpx supports .stream() returning a
    # context manager. We iterate bytes summing to cap+1 so a "100 MB
    # garbage" response stops at cap+1 bytes read.
    if hasattr(http, "stream"):
        try:
            with http.stream(
                    "GET", url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True) as resp:
                out["status"] = int(getattr(resp, "status_code", 0) or 0)
                # Final URL (post-redirect). httpx exposes resp.url
                out["final_url"] = str(getattr(resp, "url", url)) or url
                # Content-Type for downstream parser selection.
                ct = ""
                hdrs = getattr(resp, "headers", None)
                if hdrs is not None and hasattr(hdrs, "get"):
                    ct = str(hdrs.get("content-type") or "").lower()
                out["content_type"] = ct
                if not (200 <= out["status"] < 400):
                    out["error"] = f"http status {out['status']}"
                    return out
                # Iterate bytes. The exact iter method varies by client:
                #   httpx → resp.iter_bytes(chunk_size=...)
                #   stub  → resp.iter_bytes() with no kw works too
                buf = bytearray()
                limit = cap_bytes + 1  # one extra so we know we truncated
                iter_method = getattr(resp, "iter_bytes", None)
                if iter_method is None:
                    # Edge case: a stub returned a stream-shaped response
                    # without iter_bytes. Read .content/.text directly.
                    raw = (getattr(resp, "content", None)
                           or getattr(resp, "text", "") or "")
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8", errors="replace")
                    buf.extend(raw[:limit])
                else:
                    for chunk in iter_method():
                        if not chunk:
                            continue
                        if not isinstance(chunk, (bytes, bytearray)):
                            # Some stubs yield str; coerce.
                            chunk = chunk.encode(
                                "utf-8", errors="replace")
                        # Take only as much of this chunk as fits.
                        room = limit - len(buf)
                        if room <= 0:
                            break
                        buf.extend(chunk[:room])
                        if len(buf) >= limit:
                            break
                if len(buf) > cap_bytes:
                    out["truncated"] = True
                    body_bytes = bytes(buf[:cap_bytes])
                else:
                    body_bytes = bytes(buf)
                try:
                    out["body"] = body_bytes.decode(
                        "utf-8", errors="replace")
                except Exception:
                    out["body"] = ""
                out["ok"] = True
                return out
        except Exception as e:
            # .stream() raised — could be a connection error or a
            # stub that has `stream` as a non-callable. Fall through
            # to Path B so existing test stubs keep working.
            out["error"] = (
                f"{type(e).__name__}: {str(e)[:120]}")
            # If the stream attempt failed with a network error, the
            # GET fallback will likely also fail the same way — but
            # try anyway. Reset the error so a successful GET clears
            # it.
            pass

    # ── Path B: non-streaming fallback. Same shape as pre-v3.66.12.
    # The DoS hardening still partially applies: we cap the decoded
    # `.text` to cap_bytes. The memory ceiling is *the stub's*
    # response size, which is normal-test scale.
    try:
        resp = http.get(
            url, headers=headers, timeout=timeout,
            follow_redirects=True)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out
    out["status"] = int(getattr(resp, "status_code", 0) or 0)
    out["final_url"] = str(getattr(resp, "url", url)) or url
    hdrs = getattr(resp, "headers", None)
    if hdrs is not None and hasattr(hdrs, "get"):
        out["content_type"] = str(hdrs.get("content-type") or "").lower()
    if not (200 <= out["status"] < 400):
        out["error"] = f"http status {out['status']}"
        return out
    body = (getattr(resp, "text", None)
            or getattr(resp, "content", "")
            or "")
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            body = ""
    if not isinstance(body, str):
        out["error"] = "non-string body"
        return out
    if len(body) > cap_bytes:
        body = body[:cap_bytes]
        out["truncated"] = True
    out["body"] = body
    # Clear any error left over from a failed .stream() attempt above.
    out["error"] = None
    out["ok"] = True
    return out


def _ssrf_guard_hook(request):
    """SSRF guard for the deep_detect runtime probe client (F-REC01-02,
    F-REC03-01).

    Installed as an httpx 'request' event hook so it fires on EVERY outgoing
    request -- the initial page-derived candidate AND every redirect target
    (the client uses follow_redirects=True). Candidate URLs are extracted from
    attacker-influenceable page HTML / parsed manifests, so an internal URL (or
    a public URL that 302-redirects inward) would otherwise let a probe reach
    private / loopback / link-local (169.254/16) / CGNAT (100.64/10) /
    reserved space. Revalidate the host via the single canonical predicate and
    refuse non-public ones; the three probe sinks (_probe_head /
    _fetch_manifest_capped / _poll_async_workflow) all catch the raised
    SSRFBlocked and record the candidate as an errored probe.
    """
    from bulk_downloader.provider_resolve_impl._common import (
        _is_safe_public_host, SSRFBlocked,
    )
    try:
        host = request.url.host or ""
    except Exception:
        host = ""
    ok, reason = _is_safe_public_host(host)
    if not ok:
        raise SSRFBlocked("deep_detect SSRF guard: %s" % reason)


def _build_default_http_client(timeout: float = 5.0):
    """Construct an httpx.Client with sensible defaults for runtime
    probing. Wrapped so tests don't import httpx unless they
    actually exercise the live path.

    v3.66.10 hardening:
      • max_redirects=5: bounds redirect chains so a misbehaving
        server can't trap us in a loop of 302s. Five is enough for
        legitimate use (CDN → signed-URL → final file), and well
        below httpx's default 20.
      • Connection limit: keeps our load on a single host bounded
        even with probe_parallelism — at most 10 concurrent
        connections to one origin. This is well-mannered for any
        normal page (a few candidates) and prevents pathological
        pages with hundreds of candidates from opening a connection
        storm.
    """
    try:
        import httpx
    except ImportError:
        return None
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [_ssrf_guard_hook]},
        limits=httpx.Limits(
            max_connections=16,
            max_keepalive_connections=8,
        ),
        headers={
            "User-Agent": (
                "BulkDownloader/3.66 deep_detect probe "
                "(+offline static analysis with header sniffing)"),
        },
    )


def deep_detect_live(
        html: str,
        *,
        base_url: str = "",
        prefer_resolution: str = "highest",
        http=None,
        probe_headers: Optional[dict] = None,
        max_probes: int = 10,
        probe_timeout: float = 5.0,
        follow_meta_refresh_redirects: bool = True,
        sniff_attachments: bool = True,
        poll_async_workflows: bool = False,
        poll_max_attempts: int = 20,
        poll_interval: float = 2.0,
        probe_parallelism: int = 1,
        max_total_probe_time: Optional[float] = None,
        follow_manifests: bool = True,
        max_manifest_bytes: int = 1_048_576,
        site_memory: Optional[dict] = None,
        resolve_providers: bool = False,
        http_get=None,
        signing_callback=None,
        response_headers: Optional[dict] = None,
        _sleep=None,
        _now=None,
) -> dict:
    """Run deep_detect, then layer Tier 2 HTTP probes on top of its
    output. Returns the same shape as deep_detect plus:

        report["probes"] = {
            "head_probes":         [{url, status, content_type,
                                     content_disposition, refined_type,
                                     reasons, error, elapsed_ms,
                                     final_url}, ...],
            "meta_refresh":        {from, to} | None,
            "workflow_result":     dict | None,   # _poll_async_workflow
            "probes_performed":    int,
            "probes_skipped":      [{url, reason}, ...],
            "probes_truncated":    bool,           # budget exceeded?
            "cookie_view":         bool,           # v3.66.14 (P13)
        }

    `elapsed_ms` (v3.66.14, P8) is the per-probe wall-clock time in
    milliseconds. Useful for surfacing slow upstreams in dashboards
    and for the manifest-truncation diagnostic.

    `cookie_view` (v3.66.14, P13) is True iff probe_headers contained
    a Cookie header at call time. When True, the report's findings
    reflect an authenticated view that may differ from a guest view —
    a top-level warning is also added.

    The base report's `best_download` and `download_candidates` are
    re-ranked after refinements. Candidates whose `source_type` was
    promoted (e.g. "unknown" → "header_attachment") get a small score
    bump so they move up the list.

    Set http=None to use the default httpx.Client; pass any object
    with .head()/.get()/.post() to inject a stub.

    v3.66.20: ``response_headers`` is passed through to ``deep_detect``
    so the underlying CSP layer can inspect the response's
    ``Content-Security-Policy`` header. In live mode this should be
    the page-fetch response's headers, captured by the browser
    automation or HTTP client before deep_detect_live runs.

    Concurrency:
      • `probe_parallelism` (default 1, recommended 4–8 in production):
        run HEAD probes through a ThreadPoolExecutor. Order of
        results is preserved (same candidate gets the same
        refinement). httpx.Client is thread-safe.
      • `max_total_probe_time` (default None = unbounded): a global
        wall-clock budget across all HEAD probes. When exceeded,
        remaining probes are cancelled and `probes_truncated` is set.
        Independent of per-probe `probe_timeout` (which still applies
        per request).
    """
    report = deep_detect(html, base_url=base_url,
                         prefer_resolution=prefer_resolution,
                         site_memory=site_memory,
                         resolve_providers=resolve_providers,
                         http_get=http_get,
                         signing_callback=signing_callback,
                         response_headers=response_headers)
    report["probes"] = {
        "head_probes": [],
        "meta_refresh": None,
        "workflow_result": None,
        "probes_performed": 0,
        "probes_skipped": [],
        "probes_truncated": False,
        "manifests_followed": [],
        # Advisory string for the UI. Populated below when blockers
        # are present so the caller can show a banner explaining
        # what was detected — the markers are pattern-matched against
        # the page text and are prone to false positives (e.g. a
        # blog post about Widevine triggers the same marker as an
        # actual Widevine player), so this is informational, not a
        # gate. The user decides whether to proceed.
        "disclaimer": None,
    }

    blockers = report.get("blockers") or {}
    # Build a disclaimer string when any blocker markers were found,
    # but DO NOT skip probing. The detection is heuristic and the
    # user is the one who chose to point this tool at the page; we
    # surface what we saw and let them decide. Probing continues for
    # both DRM-flagged and CAPTCHA-flagged pages so HEAD/Content-Type
    # sniffing still works on legitimate content that happens to
    # mention these terms.
    disclaimer_parts: List[str] = []
    if blockers.get("drm_systems"):
        systems = ", ".join(blockers["drm_systems"])
        disclaimer_parts.append(
            f"This page contains markers that may indicate DRM "
            f"({systems}). The detection is heuristic and may be "
            "wrong. If the content is actually DRM-protected, "
            "downloading it without rights may violate copyright "
            "law in your jurisdiction. Proceed only if you have "
            "the right to access this content.")
    if blockers.get("captchas"):
        captchas = ", ".join(blockers["captchas"])
        disclaimer_parts.append(
            f"This page contains markers for a CAPTCHA system "
            f"({captchas}). Forms cannot be auto-submitted "
            "through one; manual interaction in a real browser is "
            "required to pass the challenge.")
    if disclaimer_parts:
        report["probes"]["disclaimer"] = " ".join(disclaimer_parts)

    # v3.66.14 (P13): cookie-driven re-detection awareness.
    # When probe_headers carries a Cookie header (case-insensitive),
    # the HEAD probes are running as an authenticated session and the
    # report may differ from what a guest user would see. Surface this
    # so downstream tools (rendering, caching, sharing-link generation)
    # can react appropriately. The actual cookie value is NEVER
    # echoed back — only the *fact* that one is set.
    _has_cookie = False
    if probe_headers:
        for k in probe_headers.keys():
            if isinstance(k, str) and k.strip().lower() == "cookie":
                _has_cookie = True
                break
    if _has_cookie:
        _bk_warnings(report).append(
            "probe_headers includes a Cookie; this report may reflect "
            "an authenticated view that differs from a guest view")
        report["probes"]["cookie_view"] = True
    else:
        report["probes"]["cookie_view"] = False

    owns_http = False
    if http is None:
        http = _build_default_http_client(timeout=probe_timeout)
        owns_http = http is not None

    try:
        # Global wall-clock timer shared across all live-mode work
        # (HEAD probes + manifest follows). Lazily-imported so the
        # offline path doesn't pay for it.
        #
        # v3.66.12 (roadmap P3): the clock can be injected via the
        # `_now` keyword for deterministic budget-truncation tests.
        # Without injection, `_now` defaults to `time.monotonic` and
        # the production code paths are unchanged byte-for-byte
        # (the local `_clock` is a direct alias to the same function).
        # Test stubs that hand-roll a SleepyStubHttp now have a
        # companion: pass `_now=lambda: monotonic_counter()` to
        # advance time deterministically.
        import time as _time
        _clock = _now if _now is not None else _time.monotonic
        t_start = _clock() if max_total_probe_time else None

        # ── Meta-refresh follow on the page itself ────────────────
        if follow_meta_refresh_redirects:
            target = follow_meta_refresh(html, base_url=base_url)
            if target:
                # v3.66.11 (bug FF): flag self-referential targets
                # so callers don't follow a meta-refresh loop. We
                # don't recurse here (single-call helper), but
                # downstream tooling that walks `report["probes"]
                # ["meta_refresh"]` needs to know.
                is_self_loop = bool(base_url) and (target == base_url)
                report["probes"]["meta_refresh"] = {
                    "from": base_url or None,
                    "to": target,
                    "self_loop": is_self_loop,
                }

        # ── HEAD probes for unknown / extensionless candidates ────
        if sniff_attachments and http is not None:
            cands = _bk_accepted(report)

            # Active surfacing: pick up positive-scoring anchors the
            # offline orchestrator left on the floor. score_download_link
            # already does the heavy lifting (download vocab, same-domain
            # bonus, extension awareness); we just collect every anchor
            # with score > 0 whose URL isn't already in the candidate
            # list, then let the HEAD probe decide whether it's real.
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                known_urls = {c.get("url") for c in cands
                              if c.get("url")}
                for el in soup.find_all(["a", "button"]):
                    r = score_download_link(el, base_url=base_url)
                    if r["rejected"] or r["score"] <= 0:
                        continue
                    url = r.get("url")
                    if not url or url in known_urls:
                        continue
                    # If the URL has a clear media extension, we still
                    # want it in the candidate list — pre-fix, the
                    # active-surfacing skipped these on the assumption
                    # that the offline orchestrator would have caught
                    # them, but the orchestrator doesn't scan plain
                    # anchors. So they silently disappeared. Now we
                    # add them, tagged with source_type derived from
                    # classify_url so they don't waste a HEAD probe.
                    path = _url_path(url)
                    has_extension = any(
                        path.endswith(ext)
                        for ext in PROGRESSIVE_MEDIA_EXTENSIONS
                                    + STREAM_MANIFEST_EXTENSIONS)
                    if has_extension:
                        # Classify from the URL — the file extension
                        # gives us enough to assign a specific
                        # source_type without a HEAD probe.
                        cls = classify_url(url)
                        cands.append({
                            "url": url,
                            "source_type": cls.get("type") or "direct_file",
                            "score": r["score"] + 10,  # extension bonus
                            "resolution": None,
                            "codec": None,
                            "fps": None,
                            "size_bytes": None,
                            "click_selector": _selector_for(el),
                            "found_in": "anchor_active_surface",
                            "reasons": r["reasons"] + [
                                f"URL extension classifies as "
                                f"{cls.get('type') or 'direct_file'}"],
                            "warnings": [],
                            "requires_click": False,
                        })
                        known_urls.add(url)
                        continue
                    cands.append({
                        "url": url,
                        "source_type": "unknown",
                        "score": r["score"],
                        "resolution": None,
                        "codec": None,
                        "fps": None,
                        "size_bytes": None,
                        # _selector_for(el) is the same helper used by
                        # extract_resolution_cards. Preserving it here
                        # lets the runner's _try_deep_detect_fallback
                        # treat these anchor candidates as DOM-clickable,
                        # not just as URL-only surface (which the runner
                        # currently can't consume).
                        "click_selector": _selector_for(el),
                        "found_in": "anchor_active_surface",
                        "reasons": r["reasons"],
                        "warnings": [],
                        "requires_click": False,
                    })
                    known_urls.add(url)
            except ImportError:
                pass

            # Probe the top N candidates whose source_type is fuzzy
            # — "unknown" or "extensionless_file" — capped at max_probes.
            to_probe = []
            for c in cands:
                if len(to_probe) >= max_probes:
                    break
                if not c.get("url"):
                    continue
                if c.get("source_type") in (
                        "unknown", "extensionless_file"):
                    to_probe.append(c)

            # Probe dispatcher: runs each candidate through _probe_head
            # (serially or in parallel based on probe_parallelism) and
            # collects (index, probe_result) pairs. Post-processing
            # always happens in candidate-index order so the order of
            # head_probes and the side effects on candidates are
            # deterministic regardless of which path executes.
            # _time + t_start hoisted to the outer try block.
            probe_results: List[Tuple[int, dict]] = []

            def _run_one(idx_url: Tuple[int, str]) -> Tuple[int, dict]:
                idx, url = idx_url
                return idx, _probe_head(
                    http, url,
                    headers=probe_headers,
                    timeout=probe_timeout,
                    _clock=_clock,  # v3.66.14 (P8)
                )

            indexed = [(i, c["url"]) for i, c in enumerate(to_probe)]

            if probe_parallelism <= 1 or len(indexed) <= 1:
                # Serial path — preserves the original byte-for-byte
                # behavior, with the optional total-time budget check
                # added between probes.
                for iu in indexed:
                    if (t_start is not None
                            and _clock() - t_start
                                > max_total_probe_time):
                        report["probes"]["probes_truncated"] = True
                        break
                    probe_results.append(_run_one(iu))
            else:
                # Parallel path via ThreadPoolExecutor. httpx.Client is
                # thread-safe (per docs and source); test-stub clients
                # we've seen are pure Python objects sharing nothing.
                import concurrent.futures as _futures
                # Cap workers at 16 — past that, most servers reject
                # the connection storm and we'd just add overhead.
                workers = min(probe_parallelism, len(indexed), 16)
                with _futures.ThreadPoolExecutor(
                        max_workers=workers) as executor:
                    future_to_idx = {
                        executor.submit(_run_one, iu): iu[0]
                        for iu in indexed
                    }
                    remaining = max_total_probe_time
                    for future in _futures.as_completed(
                            future_to_idx,
                            timeout=remaining):
                        try:
                            probe_results.append(future.result())
                        except _futures.TimeoutError:
                            # Outer as_completed timeout; cancel
                            # remaining and mark truncated.
                            report["probes"]["probes_truncated"] = True
                            break
                        except Exception as e:
                            # A future raised — record a stub probe
                            # so the candidate gets a visible error
                            # entry rather than silently going missing.
                            idx = future_to_idx[future]
                            probe_results.append((idx, {
                                "ok": False,
                                "status": 0,
                                "headers": {},
                                "final_url": indexed[idx][1],
                                "error":
                                    f"executor exception: "
                                    f"{type(e).__name__}: "
                                    f"{str(e)[:120]}",
                                "elapsed_ms": 0,  # v3.66.14 (P8)
                            }))
                        if (t_start is not None
                                and _clock() - t_start
                                    > max_total_probe_time):
                            # Soft budget tripped — cancel remaining.
                            report["probes"]["probes_truncated"] = True
                            for f in future_to_idx:
                                if not f.done():
                                    f.cancel()
                            break

            # Sort results by candidate index so head_probes order and
            # side effects are deterministic regardless of completion
            # order.
            probe_results.sort(key=lambda r: r[0])

            for idx, probe in probe_results:
                c = to_probe[idx]
                url = c["url"]
                report["probes"]["probes_performed"] += 1
                probe_record = {
                    "url": url,
                    "status": probe["status"],
                    "content_type": probe["headers"].get(
                        "content-type", ""),
                    "content_disposition": probe["headers"].get(
                        "content-disposition", ""),
                    "final_url": probe["final_url"],
                    "refined_type": None,
                    "reasons": [],
                    "error": probe["error"],
                    "elapsed_ms": probe.get("elapsed_ms", 0),  # v3.66.14 (P8)
                }
                if probe["ok"] and 200 <= probe["status"] < 400:
                    refined, reasons = _refine_source_type_from_headers(
                        c.get("source_type") or "unknown",
                        probe["headers"].get("content-type", ""),
                        probe["headers"].get("content-disposition", ""),
                        url,
                    )
                    probe_record["refined_type"] = refined
                    probe_record["reasons"] = reasons
                    if refined != c.get("source_type"):
                        c["source_type"] = refined
                        c["score"] = (c.get("score") or 0) + 25
                        c.setdefault("reasons", []).extend(reasons)
                    elif reasons:
                        # v3.66.10: HEAD confirmed the existing type
                        # (e.g. extensionless_file's body really is
                        # binary). Smaller bonus, but worth recording
                        # — pre-fix this branch did nothing, so
                        # extensionless candidates that were
                        # confirmed by HEAD never got the boost.
                        c["score"] = (c.get("score") or 0) + 10
                        c.setdefault("reasons", []).extend(reasons)
                    # Capture the filename if one came back — useful
                    # for the UI even when the source type didn't change.
                    cd = _parse_content_disposition(
                        probe["headers"].get("content-disposition", ""))
                    if cd.get("filename"):
                        c["filename_hint"] = cd["filename"]
                report["probes"]["head_probes"].append(probe_record)

            # Resort candidates by score post-refinement.
            cands.sort(key=lambda x: x.get("score", 0), reverse=True)
            _bk_set_accepted(report, cands)

        # ── Manifest following (v3.66.10) ─────────────────────────────
        # When the candidate list contains HLS/DASH manifest URLs, fetch
        # each manifest (size-capped + timeout-bounded) and inject its
        # variants as separate candidates. Lets the resolution picker
        # see "1080p", "720p", "480p" etc. instead of a single opaque
        # "hls_manifest" entry.
        #
        # The original manifest candidate stays in the list (now
        # annotated with manifest_resolved_to=N) so a UI that wants to
        # play the manifest directly (via a player that does ABR) still
        # can. Variants are also added so a UI that wants to download
        # a specific quality has the URL ready.
        if follow_manifests and http is not None:
            cands = _bk_accepted(report)

            def _is_manifest_candidate(c):
                """Return ("hls"|"dash"|None) if this candidate looks
                like a manifest worth following (i.e. could be a
                MASTER playlist that resolves to variants).

                Checks source_type first, then the URL path extension —
                necessary because a manifest URL surfaced via a
                resolution_download_card or anchor won't have
                source_type="hls_manifest" set.

                v3.66.10: skip candidates that are themselves the
                product of a previous manifest-follow (their `found_in`
                begins with `manifest_follow:`) and skip flattened
                variants (source_type=hls_variant) — those are media
                playlists, not master playlists, so following them
                would return zero variants and waste a network call.
                Also skip the per-variant DASH URLs we put None on
                (they have no URL to fetch anyway)."""
                u = c.get("url") or ""
                st = c.get("source_type") or ""
                found_in = c.get("found_in") or ""
                if found_in.startswith("manifest_follow:"):
                    return None
                if st == "hls_variant":
                    return None
                if st == "hls_manifest":
                    return "hls"
                if st == "dash_manifest":
                    return "dash"
                path = _url_path(u)
                if path.endswith(".m3u8"):
                    return "hls"
                if path.endswith(".mpd"):
                    return "dash"
                return None

            manifest_cands = []
            for c in cands:
                kind = _is_manifest_candidate(c)
                if (kind and c.get("url")
                        and not c.get("_manifest_followed")):
                    # Cache the resolved kind on the candidate so the
                    # later parser branch doesn't have to recompute.
                    c["_manifest_kind"] = kind
                    manifest_cands.append(c)
            # Cap how many manifests we follow per call — pages
            # occasionally list dozens of mirror URLs and we don't
            # want to fetch them all.
            max_manifests_to_follow = 4
            manifest_cands = manifest_cands[:max_manifests_to_follow]

            new_variant_cands: List[dict] = []
            for mc in manifest_cands:
                mc_url = mc["url"]
                # Respect the same global probe budget.
                if (t_start is not None
                        and _clock() - t_start
                            > max_total_probe_time):
                    report["probes"]["probes_truncated"] = True
                    break
                follow_record = {
                    "url": mc_url,
                    "type": mc.get("source_type"),
                    "fetched": False,
                    "variants_added": 0,
                    "drm_or_encryption": False,
                    "error": None,
                }
                # v3.66.12 (roadmap P1): stream the manifest with a
                # hard byte cap so a hostile 100MB response can't blow
                # process memory. Helper handles both real httpx
                # (streaming path) and test stubs (GET fallback).
                fetched = _fetch_manifest_capped(
                    http, mc_url,
                    cap_bytes=max_manifest_bytes,
                    timeout=probe_timeout,
                    headers=probe_headers or {},
                )
                if not fetched["ok"]:
                    follow_record["error"] = (
                        fetched.get("error") or "manifest fetch failed")
                    report["probes"]["manifests_followed"].append(
                        follow_record)
                    continue
                body = fetched["body"]
                if not body.strip():
                    follow_record["error"] = "empty or non-text manifest"
                    report["probes"]["manifests_followed"].append(
                        follow_record)
                    continue
                if fetched["truncated"]:
                    follow_record["truncated"] = True

                follow_record["fetched"] = True
                # The base URL for resolving relative variant URLs is
                # the manifest's final URL (after redirects).
                manifest_base = fetched["final_url"] or mc_url

                source_t = mc.get("_manifest_kind")
                if source_t == "hls":
                    parsed = parse_hls_master(
                        body, base_url=manifest_base)
                    follow_record["drm_or_encryption"] = parsed.get(
                        "drm_or_encryption_detected", False)
                    if parsed.get("kind") == "hls_master":
                        variants = _flatten_download_candidates(
                            resolution_cards=[], hls_master=parsed,
                            dash_mpd=None, state_urls=[],
                            provider_embeds=[], player_configs=[],
                            jsonld_media=[], post_reveal=[],
                            base_url=manifest_base)
                        # Annotate provenance.
                        for v in variants:
                            v["found_in"] = (
                                f"manifest_follow:hls "
                                f"({mc_url[:60]})")
                            v.setdefault("reasons", []).append(
                                "discovered by following the HLS "
                                "master playlist")
                        new_variant_cands.extend(variants)
                        follow_record["variants_added"] = len(variants)
                        # Also store the parsed manifest at the report
                        # level so callers can access the alt-audio /
                        # subtitles lists, etc.
                        if report["manifests"].get("hls") is None:
                            report["manifests"]["hls"] = parsed
                    elif parsed.get("kind") == "hls_media":
                        # Media playlist — no variants to fan out. The
                        # manifest is the deliverable on its own. Still
                        # honor DRM detection.
                        if report["manifests"].get("hls") is None:
                            report["manifests"]["hls"] = parsed
                elif source_t == "dash":
                    parsed = parse_dash_mpd(
                        body, base_url=manifest_base)
                    follow_record["drm_or_encryption"] = parsed.get(
                        "drm_or_encryption_detected", False)
                    if parsed.get("kind") == "dash_mpd":
                        variants = _flatten_download_candidates(
                            resolution_cards=[], hls_master=None,
                            dash_mpd=parsed, state_urls=[],
                            provider_embeds=[], player_configs=[],
                            jsonld_media=[], post_reveal=[],
                            base_url=manifest_base)
                        for v in variants:
                            v["found_in"] = (
                                f"manifest_follow:dash "
                                f"({mc_url[:60]})")
                            v.setdefault("reasons", []).append(
                                "discovered by following the DASH MPD")
                        new_variant_cands.extend(variants)
                        follow_record["variants_added"] = len(variants)
                        if report["manifests"].get("dash") is None:
                            report["manifests"]["dash"] = parsed
                # Mark the parent so we don't follow it again on a
                # re-run, and annotate how many variants it produced.
                mc["_manifest_followed"] = True
                mc["manifest_resolved_to"] = (
                    follow_record["variants_added"])
                # If the parent was classified as something more
                # generic (e.g. resolution_download_card on an anchor
                # that happened to link to .m3u8), upgrade it now that
                # we've confirmed it actually is a manifest. The UI
                # should know to use a manifest-aware player.
                upgrade_to = ("hls_manifest" if source_t == "hls"
                              else "dash_manifest")
                if mc.get("source_type") != upgrade_to:
                    mc["source_type"] = upgrade_to
                    mc.setdefault("reasons", []).append(
                        "source_type upgraded to manifest after "
                        "successful parse")
                report["probes"]["manifests_followed"].append(
                    follow_record)

            if new_variant_cands:
                # Dedup against existing candidates (manifest variants
                # sometimes overlap with player-config URLs).
                cands = _bk_accepted(report)
                cands.extend(new_variant_cands)
                cands = _dedup_candidates(cands)
                # Resort and refresh best_download.
                cands.sort(key=lambda c: c.get("score", 0), reverse=True)
                _bk_set_accepted(report, cands)
                report["source_breakdown"] = _count_by_type(cands)

        # ── Async-export workflow polling ──────────────────────────
        # v3.66.6: same relaxation as the HEAD-probe block above.
        # When scan_blockers flagged DRM/CAPTCHA markers on the page,
        # we used to silently skip polling. The detection is heuristic
        # (a blog post about Widevine triggers the same marker as an
        # actual Widevine player), so blocking the POST produced too
        # many false negatives on legitimate pages. We now POST anyway
        # and annotate the result with `blocker_warnings` so the UI
        # can surface them alongside the disclaimer banner.
        if poll_async_workflows and http is not None \
                and report.get("workflow_required"):
            wf = report["workflow_required"]
            result = _poll_async_workflow(
                http, wf,
                headers=probe_headers,
                max_attempts=poll_max_attempts,
                interval=poll_interval,
                timeout=probe_timeout,
                sleep=_sleep,
            )
            # Carry through any blocker context so callers don't have
            # to re-derive it. The top-level disclaimer is still the
            # primary surface; this is for tooling that consumes the
            # workflow_result directly (bdctl, dev API).
            blocker_warns: List[str] = []
            if blockers.get("drm_systems"):
                blocker_warns.append(
                    "workflow POSTed despite DRM markers ("
                    + ", ".join(blockers["drm_systems"])
                    + ") — see report['probes']['disclaimer']")
            if blockers.get("captchas"):
                blocker_warns.append(
                    "workflow POSTed despite CAPTCHA markers ("
                    + ", ".join(blockers["captchas"])
                    + "); resolved URL may be a holding page if the "
                    "challenge actually fires")
            if blocker_warns:
                result.setdefault("warnings", []).extend(blocker_warns)
            report["probes"]["workflow_result"] = result
            if result.get("ok") and result.get("download_url"):
                # Add the resolved download as a top-tier candidate.
                resolved = {
                    "url": result["download_url"],
                    "source_type": "header_attachment",
                    "score": 200,
                    "resolution": None,
                    "codec": None,
                    "fps": None,
                    "size_bytes": None,
                    "found_in": "workflow_resolved",
                    "reasons": [
                        f"workflow at {wf.get('action')} resolved to "
                        f"{result['download_url']}"],
                    "warnings": list(blocker_warns),
                    "requires_click": False,
                }
                cands = _bk_accepted(report)
                cands.insert(0, resolved)
                # Defensive re-sort. With score=200 the resolved entry
                # will win regardless of position, but the original
                # `insert(0, ...)` assumed nothing else in the list
                # would beat it. If someone later raises another
                # candidate's score above 200 (or lowers `resolved`'s),
                # the sort here keeps the invariant
                # `best_download == download_candidates[0]` intact.
                cands.sort(key=lambda c: c.get("score", 0), reverse=True)
                _bk_set_accepted(report, cands)
                report["workflow_required"] = None

        # v3.66.10: re-run signed-URL annotation after live-mode passes.
        # Active-surfacing, HEAD-promoted URLs, manifest-follow, and
        # workflow-resolved URLs may all have introduced signed URLs
        # that weren't present in deep_detect()'s offline pass. The
        # helper is idempotent — candidates already annotated by
        # deep_detect() are skipped.
        cands = _bk_accepted(report)
        cands = _apply_signed_url_annotations(
            cands, _bk_rejected_raw(report), _bk_warnings(report))
        cands.sort(key=lambda c: c.get("score", 0), reverse=True)
        _bk_set_accepted(report, cands)
        report["source_breakdown"] = _count_by_type(cands)

        # ── v3.66.12 (roadmap P0.5): bump live-mode counters ──────
        # All increments live here so each call updates the counters
        # at most once per source — avoids the multi-site truncation
        # double-count (the same call can hit the `probes_truncated`
        # flag in both the HEAD-probe phase and the manifest-follow
        # phase).
        try:
            if report["probes"].get("probes_truncated"):
                _DD_COUNTERS["budget_truncated_count"] += 1
            # manifests_followed is a list of follow_records; len()
            # is the count of manifests we actually fetched (not just
            # those we attempted — fetched=False entries indicate
            # something prevented the GET, e.g. budget truncation).
            n_followed = sum(
                1 for r in report["probes"].get("manifests_followed", [])
                if r.get("fetched"))
            if n_followed:
                _DD_COUNTERS["manifests_followed_count"] += n_followed
            # signed_urls_rejected: count entries in the rejected
            # list whose reason mentions signed URLs. Stable across
            # signed-URL annotation passes because we count the
            # CURRENT state of `rejected`, not deltas.
            n_signed = sum(
                1 for r in (report.get("rejected") or [])
                if any("signed" in str(reason).lower()
                       for reason in (r.get("reasons") or [])))
            if n_signed:
                _DD_COUNTERS["signed_urls_rejected_count"] += n_signed
        except Exception:
            # Metrics MUST NOT break detection. Swallow any oddity in
            # the counter math (missing keys, type drift) silently.
            pass

    finally:
        if owns_http and http is not None and hasattr(http, "close"):
            try:
                http.close()
            except Exception:
                pass

    return report


def to_site_config_block(report: dict) -> dict:
    """Map a deep_detect()/deep_detect_live() report onto the
    {ok, source, learned: {login, download}, warnings, error} schema
    that auto_detect.detect_site_config produces. The runtime
    auto-pick path consumes that schema, so this lets deep_detect's
    richer output drive site configuration without changing any of
    the downstream code.

    The translation is intentionally conservative: deep_detect knows
    a lot more than the runtime currently uses, so we drop the extra
    structure rather than try to invent new schema fields the runtime
    would ignore anyway. Specifically:

      • login.user_field / pass_field / submit_btn come from the
        best_login candidate's safe_fields and submit_selector.
        Field names get wrapped as input[name='...'] CSS selectors.
      • download.row_selectors / trigger_selectors / url_attribute
        come from the resolution-card candidates that have a real
        click_selector. If multiple candidates exist, we keep only
        the click_selector list and let the runtime fall back to the
        full URL when it can't drive the click.

    When the report lacks either side, that side is omitted from
    `learned`; the caller is expected to honor partial results the
    same way it honors partial auto_detect output.
    """
    out = {
        "ok": False,
        "source": "deep_detect",
        "learned": {},
        "warnings": list((report.get("buckets") or {}).get("warnings")
                         or report.get("warnings") or []),
        "error": None,
    }

    # ── Login side ───────────────────────────────────────────────
    best_login = report.get("best_login") or {}
    safe = best_login.get("safe_fields") or {}
    has_pw = best_login.get("has_password")
    if has_pw and safe:
        # Find the user and password field NAMES from safe_fields.
        # safe_fields maps {name: "username"|"email"|"password"|"token"}
        user_name = next(
            (n for n, role in safe.items()
             if role in ("username", "email")),
            None,
        )
        pass_name = next(
            (n for n, role in safe.items()
             if role == "password"),
            None,
        )
        if user_name and pass_name:
            login_block = {
                "user_field": f"input[name='{user_name}']",
                "pass_field": f"input[name='{pass_name}']",
            }
            sub = best_login.get("submit_selector")
            if sub:
                login_block["submit_btn"] = sub
            out["learned"]["login"] = login_block

    # ── Download side ────────────────────────────────────────────
    # We surface candidates that have a click_selector AND a URL
    # AND came from a resolution-card surface — those carry the most
    # reliable structural fingerprint for the runtime to replay.
    #
    # v3.66.10: don't gate on source_type=='resolution_download_card'
    # alone. HEAD probes can promote a resolution card to
    # 'header_attachment' / 'extensionless_file' (more SPECIFIC types).
    # Pre-fix, those promoted candidates lost their place in
    # learned.download because the source_type check excluded them.
    # Use found_in (which is set by _flatten_download_candidates and
    # NEVER mutated by HEAD probes) as the gate instead.
    candidates = ((report.get("buckets") or {}).get("accepted")
                  or report.get("download_candidates") or [])
    card_candidates = [
        c for c in candidates
        if (c.get("found_in") == "resolution_card"
            or (c.get("source_type") == "resolution_download_card"))
        and c.get("click_selector")
        and c.get("url")  # URL-less candidates can't be verified;
                          # they're often false matches from peer-DOM
                          # pollution (e.g. a login button in a sibling
                          # form to a resolution card).
    ]
    if card_candidates:
        # The runtime expects:
        #   row_selectors:     list of CSS selectors that identify a "row"
        #                      (a per-item container on the page)
        #   trigger_selectors: list of CSS selectors INSIDE a row that,
        #                      when clicked, start the download
        #   url_attribute:     either a string or a list parallel to
        #                      row_selectors describing where the URL
        #                      lives on each row (e.g. 'href', 'data-href')
        #
        # We translate one-card-per-row by reusing click_selector for
        # both row_selectors and trigger_selectors. The runtime walks
        # row_selectors and clicks trigger_selectors[i] inside each.
        # url_attribute is "href" by default; we infer "data-href" when
        # every candidate's URL came from a data-* attribute.
        click_selectors = [c["click_selector"]
                            for c in card_candidates]
        # Infer URL attribute. Currently we have no per-candidate
        # provenance for which attribute supplied the URL — every
        # resolution card was resolved through _candidate_url_from_element
        # which tries URL_BEARING_ATTRS in order. Default to 'href';
        # the runtime knows to fall back to data-href when href is '#'.
        out["learned"]["download"] = {
            "row_selectors":     click_selectors,
            "trigger_selectors": click_selectors,
            "url_attribute":     "href",
        }

    out["ok"] = bool(out["learned"])

    # v3.66.10: surface omissions explicitly. The runtime auto-pick
    # path can't drive POST workflows or SSO/WebAuthn login. If the
    # report contains those signals, the auto-config is INCOMPLETE
    # and the operator should know to fill in the missing pieces by
    # hand. Pre-fix these signals were silently dropped.
    if report.get("workflow_required"):
        out["warnings"].append(
            "report includes a two_step_post_reveal workflow that "
            "the runtime auto-pick can't drive; the runtime will "
            "still see the workflow_required field if you pass the "
            "full report directly, but it's NOT in learned.download")
    best_login = report.get("best_login") or {}
    if (best_login and not best_login.get("has_password")
            and best_login.get("login_types")):
        types = best_login.get("login_types")
        out["warnings"].append(
            f"best login uses {types} which the runtime can't "
            "automate (SSO / WebAuthn / passwordless require "
            "browser interaction); no login block in learned")
    # v3.66.10: signed-URL awareness. If best_download is a signed
    # URL, the persisted site config can't reuse the URL directly —
    # signatures expire. Warn so operators don't bake a dead URL
    # into a learned config.
    best_dl = ((report.get("buckets") or {}).get("best")
               or report.get("best_download") or {})
    sig = best_dl.get("signed_url")
    if sig:
        out["warnings"].append(
            f"best_download is a signed URL ({sig.get('provider')}); "
            f"signatures expire ({sig.get('expires_at') or 'unknown'})"
            f" — the URL can't be persisted in a site config, only "
            "the click/workflow path can. Re-detect before each "
            "fetch.")
    return out
