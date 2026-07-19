"""H — Live Site-Config Apply (learned + scoring). The first PRODUCTION write.

The system can autonomously apply a `learned`/`scoring` change to a site's live
`sites_config.json` — and only that — when the operator has GRANTED the site and
human-designated held-out evidence corroborates the change (oracle tier 3), under a
fail-closed window that reverts on silence and a pure post-apply validation that reverts on
failure. Chain (generic, reused from v1): record_change("live_site_config", …) →
register_pending → fail-closed 24h window → rollback → audit.

DELIBERATELY NOT: no login-field change, no credential read/write, no corpus, no debt
retirement, no finding confirm/falsify, no policy change, no release approval, no capture
execution, no third-party interaction, no second site in one in-flight window. H NEVER
authors selectors — it forwards a proposal the synthesis subsystem produced.

AUTHORITY: `participation_eligible` (the live gate) becomes True only when ALL hold — a
non-suspended grant, Class C allowed, the live apply path exists (this module's reverser is
registered), tier-3 corroborated evidence, trust above floor, not frozen, blast OK. The
system may auto-SUSPEND a grant (contraction); it may never auto-grant (expansion).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools import autonomy_apply as aap

try:  # canonical I0008 secret-keyword floor (F-TOOLSO04-03)
    from bulk_downloader.capture_artifact_redact import (
        _kv_key_is_secret as _floor_key_is_secret)
except Exception:  # pragma: no cover
    def _floor_key_is_secret(_k):
        return False

LIVE_TARGET_KIND = "live_site_config"
_BACKUP_KEEP = 10

# Backup redaction: H never changes credentials, so backups carry only non-secret config
# (learned/scoring/selectors/url) — secrets/PII never enter a backup.
_BACKUP_KEEP_KEYS = {"user_field", "pass_field", "submit_btn", "success_url", "output"}
_BACKUP_PII_KEYS = {"username", "user", "email", "login"}
_BACKUP_SECRET_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|cookie|auth|credential|session)", re.I)


def _redact_site(site: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (site or {}).items():
        kl = str(k).lower()
        if kl in _BACKUP_KEEP_KEYS:
            out[k] = v
            continue
        if kl in _BACKUP_PII_KEYS:
            continue
        if _BACKUP_SECRET_RE.search(kl) or _floor_key_is_secret(kl):
            continue
        out[k] = v
    return out


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── config file IO (READ + the audited atomic writer; mirrors _save_sites_config) ──

def _config_path() -> Path:
    return Path(os.environ.get("BD_SITES_CONFIG_PATH", "sites_config.json"))


def _full_config() -> List[Dict[str, Any]]:
    p = _config_path()
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _index_of(cfg: List[Dict[str, Any]], site: str) -> Optional[int]:
    from tools.cockpit_templates import _site_id as sid
    for i, c in enumerate(cfg):
        if sid(c) == site:
            return i
    return None


def _live_block(site: str) -> Optional[Dict[str, Any]]:
    cfg = _full_config()
    i = _index_of(cfg, site)
    return cfg[i] if i is not None else None


def _atomic_write_config(cfg_list: List[Dict[str, Any]]) -> None:
    # .tmp + os.replace, UTF-8 — same contract the security probe verifies for
    # _save_sites_config. A crash leaves the old or the new whole file, never partial.
    p = _config_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg_list, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _backup_dir() -> Path:
    return _config_path().parent / "sites_config_backups"


def _backup_sites_config() -> Optional[Path]:
    """Timestamped, CREDENTIAL-REDACTED snapshot before each live write. Reverser truth is
    the change record's `before`; this is defense in depth + audit. Secrets/PII never enter
    the backup (H never changes credentials, so a redacted snapshot still carries the prior
    learned/scoring for recovery). Keeps the last 10."""
    p = _config_path()
    if not p.is_file():
        return None
    redacted = [_redact_site(s) for s in _full_config()]
    bd = _backup_dir()
    bd.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dst = bd / f"sites_config.{ts}.json"
    dst.write_text(json.dumps(redacted, indent=2, ensure_ascii=False), encoding="utf-8")
    backups = sorted(bd.glob("sites_config.*.json"))
    for old in backups[:-_BACKUP_KEEP]:
        try:
            old.unlink()
        except Exception:
            pass
    return dst


# ── proposal (from the synthesis subsystem; never authored here) ──────────────

def _proposed_block(site: str) -> Optional[Dict[str, Any]]:
    """The proposed {learned, scoring} from the EXISTING synthesis subsystem
    (`live_template_integration.build_learned_guidance`, which only REORDERS existing
    selector confidence). H forwards it verbatim and never invents selectors. Returns None
    when the subsystem has produced nothing — so build-dark applies nothing."""
    try:
        from tools import live_template_integration as lti
        live = _live_block(site) or {}
        profile = live.get("_profile")
        confidence = live.get("_selector_confidence")
        if profile is None and confidence is None:
            return None
        learned = lti.build_learned_guidance(profile, confidence)
        if not learned or not learned.get("row_selectors"):
            return None
        return {"learned": learned, "scoring": live.get("scoring")}
    except Exception:
        return None


# ── corroboration + pure post-apply validation (NO I/O) ───────────────────────

def _oracle_corroborates(site: str) -> bool:
    """Human-designated held-out evidence corroborates the site at tier 3. PURE."""
    return ao.oracle_verdict(site).get("tier") == 3


def _descriptor_from_block(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    exp = ((block or {}).get("learned") or {}).get("_expectations", {}) or {}
    ids = exp.get("identity_descriptors") or []
    rends = exp.get("rendition_descriptors") or []
    return {"identity": ids[0] if ids else None,
            "identity_descriptors": ids, "renditions": rends,
            "template_shape": "media_present" if rends else "thin",
            "signing_marker_names": exp.get("signing_markers") or []}


def _post_apply_validation(site: str, proposed: Dict[str, Any]) -> Dict[str, Any]:
    """PURE descriptor match. Derives the proposed descriptor from the block's expectations
    and requires the oracle's identity / rendition / template-shape agreement with the
    human-designated held-out descriptors. NO network fetch, NO browser action, NO
    re-download, NO byte comparison. Fail-closed: no held-out or no identity expectation
    ⇒ not ok."""
    ho = ao._held_out_descriptors(site)
    if not ho:
        return {"ok": False, "reason": "no held-out descriptors"}
    cand = _descriptor_from_block(proposed)
    if not cand.get("identity_descriptors"):
        return {"ok": False, "reason": "proposed block carries no identity expectations"}
    v = ao.oracle_verdict(site, candidate=cand, held_out=ho)
    if v.get("tier") != 3 or v.get("hard_failures"):
        return {"ok": False, "reason": "held-out does not corroborate at tier 3",
                "verdict": {"tier": v.get("tier"), "hard_failures": v.get("hard_failures")}}
    ho_ids = {d.get("identity") for d in ho if d.get("identity")}
    prop_ids = set(cand.get("identity_descriptors") or [])
    if ho_ids and prop_ids and not (prop_ids & ho_ids):
        return {"ok": False, "reason": "proposed identity disagrees with held-out"}
    return {"ok": True, "tier": 3}


# ── live write + the reverser (logical confinement: only this site's learned+scoring) ──

def _apply_live_block(site: str, after: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _full_config()
    i = _index_of(cfg, site)
    if i is None:
        raise ValueError("live target site not present")
    block = cfg[i]
    before = {"learned": block.get("learned"), "scoring": block.get("scoring")}
    block["learned"] = after.get("learned")
    block["scoring"] = after.get("scoring")
    _atomic_write_config(cfg)
    return before


def _restore_live_block(target_ref: str, before: Any) -> None:
    site = target_ref.split("site::", 1)[1] if "site::" in str(target_ref) else str(target_ref)
    cfg = _full_config()
    i = _index_of(cfg, site)
    if i is None:
        raise ValueError("live target site not present")
    block = cfg[i]
    if before is None:
        block.pop("learned", None)
        block.pop("scoring", None)
    else:
        block["learned"] = before.get("learned")
        block["scoring"] = before.get("scoring")
    _atomic_write_config(cfg)


def _current_live(site: str):
    b = _live_block(site)
    return None if b is None else {"learned": b.get("learned"), "scoring": b.get("scoring")}


# Register the live kind with the generic Class-C harness. Wrappers are late-binding (resolve
# the module globals at call time) so tests that monkeypatch `_proposed_block` /
# `_oracle_corroborates` / `_post_apply_validation` / `el.evaluate_site` still take effect.
# This also registers `_restore_live_block` as the reverser ⇒ apply_path_exists(kind) True.
aap.register_apply_kind(
    LIVE_TARGET_KIND,
    gate=lambda s: bool(el.evaluate_site(s, kind=LIVE_TARGET_KIND).get("participation_eligible")),
    current=_current_live,
    proposer=lambda s: _proposed_block(s),
    applier=lambda s, after: _apply_live_block(s, after),
    reverser=_restore_live_block,
    corroborate=lambda s: _oracle_corroborates(s),
    validator=lambda s, after: _post_apply_validation(s, after),
    backup=lambda: _backup_sites_config(),
    transition_field="live_config",
)


# ── gate + apply (host-scheduled; never a cockpit button) ─────────────────────

def participation_eligible(site: str, kind: str = LIVE_TARGET_KIND) -> bool:
    """The LIVE gate — True only when an active per-(site, kind) grant + Class C at auto +
    apply-path(reverser) + tier-3 + trust + not-frozen + blast all hold. Computed by
    autonomy_eligibility; dark by default."""
    return bool(el.evaluate_site(site, kind=kind).get("participation_eligible"))


def maintain_live_config(site: str, *, by: str = "system") -> Dict[str, Any]:
    """Thin wrapper over the generic harness for the live kind. Behavior is identical to the
    former bespoke flow (gate → proposal → corroborate → before/after → backup →
    record_change → apply → register_pending → validate → transition; fail-closed revert)."""
    return aap.apply_for_kind(site, LIVE_TARGET_KIND, by=by)


def maintain_all_live(*, by: str = "system",
                      sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Host-scheduled loop. With no grant or no tier-3 evidence, nothing is
    participation_eligible and nothing applies (build-dark)."""
    return aap.apply_all(LIVE_TARGET_KIND, by=by, sites=sites)


# ── read-only views: COMPATIBILITY ALIASES over the unified Authority surface ──
# These back the legacy /cockpit/api/live/* routes. They return the same data the Authority
# endpoints return, filtered to kind="live_site_config" — equivalence is by construction
# (one reader). The Authority view is the new primary UI; /api/live/* remain for compat.

def live_grants() -> Dict[str, Any]:
    return aap.authority_grants(kind=LIVE_TARGET_KIND)


def live_pending() -> Dict[str, Any]:
    return aap.authority_pending(kind=LIVE_TARGET_KIND)


def live_change(change_id: str) -> Dict[str, Any]:
    return aap.authority_change(change_id, kind=LIVE_TARGET_KIND)


def live_status() -> Dict[str, Any]:
    return aap.authority_status(kind=LIVE_TARGET_KIND)
