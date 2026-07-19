"""learn_impl.memory -- verbatim from learn.py (DECOMP-LEAF cut 5). Lazy `from . import deep_detect` absolutized to `..`."""

from __future__ import annotations


_DD_MAX_TOP_TYPES = 20


_DD_MAX_PROVIDERS = 20


_DD_MAX_PENDING = 20


def _dd_init_block():
    """Return a fresh learned.deep_detect block with all sub-dicts present.
    Callers can mutate the result and write it back."""
    return {
        "winning_source_types": {},
        "preferred_resolution": None,
        "blocker_history": {},
        "provider_embeds_seen": {},
        "last_winner": None,
        "post_reveal_decisions": {},
        "auto_submit_decisions": {},
        # T11 (v3.66.264): the CURRENT pending auto-submit / post-reveal
        # approval candidates a deep_detect run surfaced for this site,
        # so the SPA can render the per-site approval gate without
        # re-running analysis (the candidates are otherwise ephemeral —
        # only the operator's *decisions* used to survive a run). Keyed
        # "<surface>|<bare_key>"; values carry kind/why markers only,
        # never a secret value (F2). Self-clears once a decision lands.
        "pending_approvals": {},
        "stats": {
            "runs": 0,
            "candidates_found": 0,
            "candidates_picked": 0,
            "last_run": None,
        },
    }


def _dd_now_iso():
    """ISO timestamp for `at` / `last_run` fields. Wrapped so tests
    can monkeypatch this if they need determinism."""
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _dd_prune_dict(d, max_keys):
    """If `d` exceeds max_keys, drop the lowest-value entries until it
    fits. Used to keep winning_source_types and provider_embeds_seen
    bounded. Lowest-count entries are evicted first; ties broken by
    key name for determinism."""
    if not isinstance(d, dict) or len(d) <= max_keys:
        return d
    # Sort by (count, key) ascending — lowest first. For provider
    # entries the "count" lives under .count, so use a key fn.
    def _weight(item):
        k, v = item
        if isinstance(v, dict):
            return (int(v.get("count", 0) or 0), k)
        return (int(v or 0), k)
    items = sorted(d.items(), key=_weight)
    keep = dict(items[-max_keys:])
    return keep


def record_deep_detect_outcome(config, report, *, base_url=""):
    """Update config['learned']['deep_detect'] from a deep_detect report.

    Called by the runner after `_try_deep_detect_fallback` produces a
    result (success OR failure — both are signal). The report is the
    output of `deep_detect()` / `deep_detect_live()`.

    Returns the updated config (also mutated in place — matching the
    pattern of merge_learned).

    What gets recorded:
      • winning_source_types: incremented by 1 for best_download.source_type
      • preferred_resolution: updated mode-of-history if winner has resolution
      • blocker_history: incremented for every disclaimer type seen
      • provider_embeds_seen: count + last_id per provider in report
      • last_winner: replaced wholesale with current winner
      • stats: runs +=1, candidates_found += len, candidates_picked += 1|0

    Idempotency: NO — each call increments counters. Callers should
    invoke this once per deep_detect run, not per candidate. For test
    determinism you'd snapshot the config first and compare.

    Silent on malformed input: a non-dict config, non-dict report, or
    missing best_download field is tolerated (no crash, no record).
    """
    if not isinstance(config, dict) or not isinstance(report, dict):
        return config

    learned = config.setdefault("learned", {})
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        block = _dd_init_block()
        learned["deep_detect"] = block
    # Heal partial blocks (older format / hand-edited / etc.)
    fresh = _dd_init_block()
    for k, v in fresh.items():
        if k not in block:
            block[k] = v
        elif type(block[k]) is not type(v) and not (
                k == "preferred_resolution" or k == "last_winner"):
            # Allow None for preferred_resolution and last_winner;
            # everything else must match the fresh-block type or we
            # heal it.
            block[k] = v

    # --- stats ---
    stats = block["stats"]
    stats["runs"] = int(stats.get("runs", 0) or 0) + 1
    stats["last_run"] = _dd_now_iso()
    _bk = report.get("buckets") or {}
    cands = _bk.get("accepted")
    if cands is None:
        cands = report.get("download_candidates") or []
    if isinstance(cands, list):
        stats["candidates_found"] = (
            int(stats.get("candidates_found", 0) or 0) + len(cands))

    # --- winner ---
    best = _bk.get("best")
    if best is None:
        best = report.get("best_download")
    picked = isinstance(best, dict) and bool(best.get("url"))
    if picked:
        stats["candidates_picked"] = (
            int(stats.get("candidates_picked", 0) or 0) + 1)

        st = str(best.get("source_type") or "unknown")
        wst = block["winning_source_types"]
        wst[st] = int(wst.get(st, 0) or 0) + 1
        block["winning_source_types"] = _dd_prune_dict(
            wst, _DD_MAX_TOP_TYPES)

        # preferred_resolution: track the resolution label of the
        # best candidate. We use a small embedded histogram (NOT
        # surfaced in the schema — derived field only) to compute the
        # mode. Stored in a private key prefixed with _ so a
        # future _build_meta-like sanitizer can strip it if needed.
        res = best.get("resolution")
        label = None
        if isinstance(res, dict):
            label = res.get("label")
        elif isinstance(res, str):
            label = res
        if label:
            hist = block.setdefault("_resolution_hist", {})
            hist[label] = int(hist.get(label, 0) or 0) + 1
            # Mode-of-history → preferred_resolution. Ties → newer
            # label wins (the one we just incremented).
            mode_label = max(
                hist.items(),
                key=lambda kv: (kv[1], kv[0] == label))
            block["preferred_resolution"] = mode_label[0]

        block["last_winner"] = {
            "source_type": st,
            "url": str(best.get("url") or ""),
            "confidence": float(best.get("confidence") or 0.0),
            "at": _dd_now_iso(),
        }

    # --- blockers ---
    # report["blockers"]["warnings"] is the legacy string-list shape
    # (from v3.66.12). v3.66.14 added structured disclaimers under
    # report["disclaimers"]. Use disclaimers when present; fall back
    # to scanning the legacy warning strings for known blocker tokens.
    bh = block["blocker_history"]
    disclaimers = report.get("disclaimers")
    if isinstance(disclaimers, list):
        for d in disclaimers:
            if not isinstance(d, dict):
                continue
            t = d.get("type")
            if not t:
                continue
            bh[t] = int(bh.get(t, 0) or 0) + 1
    else:
        # Legacy path: scan blocker warnings for token mentions.
        blockers = report.get("blockers") or {}
        warns = blockers.get("warnings") if isinstance(blockers, dict) else None
        if isinstance(warns, list):
            tokens = ("login", "drm", "captcha", "paywall", "geofence",
                      "encryption", "signed_url")
            for w in warns:
                lw = str(w).lower()
                for tok in tokens:
                    if tok in lw:
                        bh[tok] = int(bh.get(tok, 0) or 0) + 1

    # --- provider embeds seen ---
    pes = block["provider_embeds_seen"]
    embeds = report.get("provider_embeds") or []
    if isinstance(embeds, list):
        for e in embeds:
            if not isinstance(e, dict):
                continue
            prov = e.get("provider")
            ids = e.get("ids") or {}
            if not prov:
                continue
            entry = pes.get(prov)
            if not isinstance(entry, dict):
                entry = {"last_id": None, "count": 0}
                pes[prov] = entry
            entry["count"] = int(entry.get("count", 0) or 0) + 1
            # Prefer video_id over other ids for the "last_id"
            # cache key. Falls back to the first id otherwise.
            if isinstance(ids, dict) and ids:
                entry["last_id"] = (
                    ids.get("video_id")
                    or ids.get("entry_id")
                    or ids.get("playback_id")
                    or next(iter(ids.values())))
        block["provider_embeds_seen"] = _dd_prune_dict(
            pes, _DD_MAX_PROVIDERS)

    return config


def deep_detect_site_memory(config):
    """Extract the read-shape site_memory dict from a site config.

    Returns the `learned.deep_detect` block (or an empty fresh one if
    absent), as the dict that deep_detect's site_memory= argument
    expects. Safe to call on configs that have never been recorded
    against — returns an empty-but-valid block, so deep_detect's
    read-path no-ops naturally.

    This is the canonical reader. deep_detect.py imports it lazily
    to avoid an import cycle.
    """
    if not isinstance(config, dict):
        return _dd_init_block()
    learned = config.get("learned")
    if not isinstance(learned, dict):
        return _dd_init_block()
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        return _dd_init_block()
    # Return a shallow copy so the caller can't mutate the config
    # accidentally.
    return dict(block)


def record_post_reveal_decision(config, action_url, decision, *,
                                site_id=None):
    """F12: persist the operator's approve/decline choice for a
    two-step POST-reveal workflow so it isn't re-prompted on this site.

    Writes config['learned']['deep_detect']['post_reveal_decisions']
    [<key>] = {"decision": "approve"|"decline", "at": <ISO>}, where
    <key> is the normalized action URL (host+path) — the same key the
    detector reads via deep_detect._post_reveal_saved_decision, so a
    write here makes the next detect a remembered hit.

    `decision` must be "approve" or "decline" (anything else is
    rejected, returning config unchanged). Mutates config in place and
    returns it, matching record_deep_detect_outcome / merge_learned.
    Tolerant of a non-dict config (no-op)."""
    if not isinstance(config, dict):
        return config
    if decision not in ("approve", "decline"):
        return config
    # One source of truth for the key shape — borrow the detector's
    # normalizer (lazy import avoids the deep_detect <-> learn cycle).
    try:
        from .. import deep_detect as _dd
        key = _dd._post_reveal_key(action_url)
    except Exception:
        key = action_url or ""
    if not key:
        return config

    learned = config.setdefault("learned", {})
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        block = _dd_init_block()
        learned["deep_detect"] = block
    decisions = block.setdefault("post_reveal_decisions", {})
    if not isinstance(decisions, dict):
        decisions = {}
        block["post_reveal_decisions"] = decisions
    decisions[key] = {"decision": decision, "at": _dd_now_iso()}
    return config


def record_auto_submit_decision(config, key, decision, *, site_id=None):
    """Persist the operator's approve/decline choice for an
    auto-submit-gated login form or page blocker (bot-defense /
    CAPTCHA / interactive-challenge), so it isn't re-prompted on this
    site.

    `key` is the approval key the report surfaced (deep_detect sets it
    as `approval_key` on the gated candidate / blocker — the form
    action's host+path, or the page host). `decision` must be
    "approve" or "decline".

    Writes config['learned']['deep_detect']['auto_submit_decisions']
    [key] = {"decision": ..., "at": <ISO>} so the next analysis reads
    it back via deep_detect._auto_submit_saved_decision and reports
    approval_status as "approved"/"declined" instead of "pending".

    Mutates config in place and returns it. Tolerant of a non-dict
    config or bad decision (no-op)."""
    if not isinstance(config, dict):
        return config
    if decision not in ("approve", "decline"):
        return config
    key = (key or "").strip()
    if not key:
        return config

    learned = config.setdefault("learned", {})
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        block = _dd_init_block()
        learned["deep_detect"] = block
    decisions = block.setdefault("auto_submit_decisions", {})
    if not isinstance(decisions, dict):
        decisions = {}
        block["auto_submit_decisions"] = decisions
    decisions[key] = {"decision": decision, "at": _dd_now_iso()}
    return config


def _pending_why(markers):
    """Short human reason from a candidate's bot-defense / login-type
    markers. Marker LABELS only (e.g. 'cf-turnstile'), never a value —
    keeps the persisted gate F2-clean."""
    markers = [str(m) for m in (markers or []) if m]
    if not markers:
        return "interactive challenge; awaiting operator approval"
    return "bot defense: " + ", ".join(markers[:4])


def record_pending_approvals(config, report, *, base_url=""):
    """T11 (v3.66.264). Persist the CURRENT pending auto-submit /
    post-reveal approval candidates from a deep_detect report into
    config['learned']['deep_detect']['pending_approvals'], so the SPA
    can render the per-site approval gate without re-running analysis.

    Called by the runner once per deep_detect run, right beside
    record_deep_detect_outcome (the candidates are ephemeral — only the
    operator's *decisions* survived a run before this).

    What gets recorded (one entry per gated surface):
      • login forms / page blockers with approval_status == "pending":
        keyed "auto_submit|<approval_key>", where <approval_key> is the
        SAME key auto_submit_decisions uses, so a later decision
        self-clears the entry on read.
      • two-step POST-reveal forms with approval_status == "pending":
        keyed "post_reveal|<post_reveal_key(action)>", matching the
        post_reveal_decisions key for the same reason.

    Each entry value is {"surface", "key", "kind", "why", "at"} — kind
    is a marker LABEL (e.g. "cf-turnstile") and why is a short reason;
    NO secret value is stored (F2). Bounded by _DD_MAX_PENDING (oldest
    by `at` evicted first). Upserts (refreshes `at`) on re-run.

    Idempotency: re-running with the same report refreshes timestamps
    but does not duplicate. Mutates config in place and returns it;
    tolerant of a non-dict config / report (no-op).
    """
    if not isinstance(config, dict) or not isinstance(report, dict):
        return config

    learned = config.setdefault("learned", {})
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        block = _dd_init_block()
        learned["deep_detect"] = block
    pend = block.get("pending_approvals")
    if not isinstance(pend, dict):
        pend = {}
        block["pending_approvals"] = pend

    try:
        from .. import deep_detect as _dd
        _pr_key = _dd._post_reveal_key
    except Exception:
        def _pr_key(u):
            return (u or "").strip()

    now = _dd_now_iso()

    # ── auto-submit surface: login candidates (+ best_login) ──
    logins = list(report.get("login_candidates") or [])
    bl = report.get("best_login")
    if isinstance(bl, dict):
        logins.append(bl)
    for c in logins:
        if not isinstance(c, dict):
            continue
        if c.get("approval_status") != "pending":
            continue
        key = (c.get("approval_key") or "").strip()
        if not key:
            continue
        markers = c.get("bot_defenses") or c.get("login_types") or []
        ek = "auto_submit|" + key
        pend[ek] = {
            "surface": "auto_submit",
            "key": key,
            "kind": (str(markers[0]) if markers else "challenge"),
            "why": _pending_why(markers),
            "at": pend.get(ek, {}).get("at") or now,
        }

    # ── post-reveal surface: workflow candidates ──
    reveals = list(report.get("download_candidates") or [])
    wr = report.get("workflow_required")
    if isinstance(wr, dict):
        reveals.append(wr)
    for c in reveals:
        if not isinstance(c, dict):
            continue
        wf = c.get("workflow") if isinstance(c.get("workflow"), dict) else c
        if wf.get("approval_status") != "pending":
            continue
        action = (wf.get("action") or "").strip()
        bare = _pr_key(action)
        if not bare:
            continue
        markers = wf.get("bot_defenses") or []
        ek = "post_reveal|" + bare
        pend[ek] = {
            "surface": "post_reveal",
            "key": bare,
            "kind": (str(markers[0]) if markers else "challenge"),
            "why": _pending_why(markers),
            "at": pend.get(ek, {}).get("at") or now,
        }

    # Bound the set: evict oldest by `at` (ties by key) until it fits.
    if len(pend) > _DD_MAX_PENDING:
        items = sorted(pend.items(),
                       key=lambda kv: (kv[1].get("at") or "", kv[0]))
        for ek, _ in items[:len(pend) - _DD_MAX_PENDING]:
            pend.pop(ek, None)

    return config


def pending_approvals(config):
    """T11. Return the list of pending approval candidates for this
    site that have NOT yet been decided — the read shape the SPA gate
    renders. Self-clearing: an entry whose key already carries a
    recorded decision (auto_submit_decisions / post_reveal_decisions)
    is filtered out here, so an approve/decline removes the gate row on
    the next read WITHOUT needing a fresh deep_detect run.

    Each returned item: {"surface", "key", "kind", "why", "at"}.
    Sorted newest-first by `at`. Tolerant of a non-dict / never-recorded
    config (returns [])."""
    if not isinstance(config, dict):
        return []
    learned = config.get("learned")
    if not isinstance(learned, dict):
        return []
    block = learned.get("deep_detect")
    if not isinstance(block, dict):
        return []
    pend = block.get("pending_approvals")
    if not isinstance(pend, dict):
        return []
    auto_dec = block.get("auto_submit_decisions") or {}
    reveal_dec = block.get("post_reveal_decisions") or {}
    if not isinstance(auto_dec, dict):
        auto_dec = {}
    if not isinstance(reveal_dec, dict):
        reveal_dec = {}

    out = []
    for entry in pend.values():
        if not isinstance(entry, dict):
            continue
        surface = entry.get("surface")
        key = entry.get("key") or ""
        if surface == "auto_submit" and key in auto_dec:
            continue  # decided -> self-clear
        if surface == "post_reveal" and key in reveal_dec:
            continue
        out.append({
            "surface": surface,
            "key": key,
            "kind": entry.get("kind") or "challenge",
            "why": entry.get("why") or "",
            "at": entry.get("at") or "",
        })
    out.sort(key=lambda e: e.get("at") or "", reverse=True)
    return out


def make_provider_cache_writer(config):
    """Return a cache-write callback bound to `config`.

    The returned callable has signature
    ``(provider, embed_id, url, ts) -> None`` and is intended to be
    passed to ``provider_resolve.resolve_provider_embed`` as the
    ``cache_write=`` kwarg. It mutates
    ``config['learned']['deep_detect']['provider_embeds_seen']
        [<provider>]['last_resolved']``
    with ``{id, url, at}``.

    The shape mirrors what ``_cache_lookup`` in ``provider_resolve``
    reads, so a write here makes a future read a cache hit (assuming
    same id and within TTL).

    Returns a no-op callback if `config` isn't a dict — so callers
    can wire this unconditionally without guarding every call site.
    """
    if not isinstance(config, dict):
        def _noop(provider, embed_id, url, ts):
            pass
        return _noop

    def _write(provider, embed_id, url, ts):
        # All inputs validated defensively: this runs on the hot
        # resolution path and we never want to crash a successful
        # network resolution because of a cache-write quirk.
        if not isinstance(provider, str) or not provider:
            return
        if not isinstance(embed_id, str) or not embed_id:
            return
        if not isinstance(url, str) or not url:
            return
        if not isinstance(ts, (int, float)):
            return

        learned = config.setdefault("learned", {})
        block = learned.get("deep_detect")
        if not isinstance(block, dict):
            block = _dd_init_block()
            learned["deep_detect"] = block

        pes = block.setdefault("provider_embeds_seen", {})
        entry = pes.get(provider)
        if not isinstance(entry, dict):
            # No prior sighting recorded for this provider yet. Create
            # a minimal entry; record_deep_detect_outcome will fill in
            # count/last_id on its next run.
            entry = {"last_id": embed_id, "count": 0}
            pes[provider] = entry
        entry["last_resolved"] = {
            "id": embed_id,
            "url": url,
            "at": float(ts),
        }

    return _write
