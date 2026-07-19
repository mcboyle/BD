"""player_platform_hints.py — Packs H + I. A SEPARATE channel from player
families: CMS/wrapper (H) and platform/network/CMS/biller shell (I) recognition.

Hard rules (enforced by design + tests):
  * These are NEVER player families and NEVER win family selection.
  * They emit NO selectors and NO download evidence.
  * Structure/label only — recognized from PUBLIC markers (host substrings, CMS
    class/script fingerprints). NEVER persist account/member/billing/entitlement
    values, tokens, emails, or query strings. Output is category LABELS only.
  * Media recognition stays delegated to the player/API/media-pattern detectors.

Markers are CANDIDATES — verify against the capture corpus; many adult-CMS /
network / biller fingerprints drift. Pure / stdlib only.
"""
from __future__ import annotations

import re
from typing import Dict, List


def _blob(html, scripts, hosts) -> str:
    # NB: query strings are intentionally NOT included — host + path-shape only.
    parts = [html or ""]
    parts += [str(s).split("?", 1)[0] for s in (scripts or [])]
    parts += [str(h) for h in (hosts or [])]
    return " ".join(parts).lower()


# ── Pack H — CMS / WordPress / LMS wrappers (workflow hints) ────────────────
# (label, marker-regex, is_membership)  — wrappers delegate to the real player.
_WRAPPERS = [
    ("wordpress_block_video", r"wp-block-video|wp-block-embed", False),
    ("wordpress_shortcode_video", r"\bwp-video-shortcode\b|class=[\"\']wp-video", False),
    ("elementor_video_widget", r"elementor-widget-video|elementor-video", False),
    ("learndash_video", r"learndash|ld-video|sfwd-", False),
    ("memberpress_protected_video", r"\bmemberpress\b|\bmepr-", True),
    ("woocommerce_memberships_video", r"woocommerce-memberships|wc-memberships", True),
    ("uscreen", r"\buscreen\b", True),
    ("kajabi_video", r"\bkajabi\b", True),
    ("vimeo_ott", r"vimeo-ott|vhx\.tv|\bvhx\b", True),
]


def detect_wrappers(html, scripts=None, hosts=None) -> List[Dict[str, object]]:
    b = _blob(html, scripts, hosts)
    out = []
    for label, pat, membership in _WRAPPERS:
        if re.search(pat, b, re.I):
            out.append({
                "hint": label,
                "kind": "membership_workflow" if membership else "cms_wrapper",
                "note": ("Member/entitlement platform — workflow hint only; do not infer "
                         "download availability or entitlement." if membership
                         else "CMS wrapper — underlying player remains primary."),
            })
    return out


# ── Pack I — platform / network / CMS / biller SHELLS (hints only) ──────────
# (label, category, marker-regex). PUBLIC structural markers only.
_SHELLS = [
    # networks (host-based public markers)
    ("adult_time_gamma_shell", "network", r"adulttime\.com|gammaentertainment|\bgamma\b"),
    ("aylo_member_shell", "network", r"\baylo\b|mindgeek"),
    ("brazzers_realitykings_shell", "network", r"brazzers\.com|realitykings\.com"),
    ("bangbros_wgcz_shell", "network", r"bangbros\.com|\bwgcz\b"),
    ("teamskeet_psm_shell", "network", r"teamskeet\.com|paperstreetmedia"),
    ("mylf_neptune_shell", "network", r"\bmylf\.com\b"),
    ("vixen_network_shell", "network", r"vixen\.com|blacked\.com|tushy\.com|deeper\.com|vixenmedia"),
    ("naughtyamerica_shell", "network", r"naughtyamerica\.com"),
    ("metart_network_shell", "network", r"metart\.com|metartnetwork"),
    ("adultempire_sugarinstant_vod_shell", "vod_platform", r"adultempire\.com|sugarinstant\.com"),
    # tube / VOD CMS (public CMS fingerprints)
    ("kvs_tube_shell", "cms", r"\bkt_player\b|/kt_player/|\bkvs_"),
    ("mechbunny_tube_shell", "cms", r"\bmechbunny\b"),
    ("avscms_shell", "cms", r"\bavscms\b|avs_cms"),
    ("elevatedx_shell", "cms", r"\belevatedx\b|elevated-x"),
    ("wp_script_tube_shell", "cms", r"\bwp-script\b|wpscript"),
    # affiliate tracking (structure-only, query-stripped)
    ("nats_affiliate_shell", "affiliate", r"/nats/|\bnats_|tour\.php"),
    # billers (auth/billing surfaces = workflow only, never media evidence)
    ("ccbill_segbay_epoch_vendo_biller_shell", "biller",
     r"ccbill\.com|segpay\.com|\bsegbay\b|epoch\.com|\bvendo\b|verotel\.com"),
]


def detect_shells(html, scripts=None, hosts=None) -> List[Dict[str, str]]:
    b = _blob(html, scripts, hosts)
    out = []
    for label, category, pat in _SHELLS:
        if re.search(pat, b, re.I):
            note = "Platform/network hint only; never implies download capability."
            if category == "biller":
                note = ("Billing/auth surface — workflow hint only; never media evidence; "
                        "no account/billing/entitlement values are captured.")
            elif category == "affiliate":
                note = "Affiliate-tracking structure (query-stripped); structure-only hint."
            out.append({"hint": label, "category": category, "note": note})
    return out


def detect_platform_hints(html, scripts=None, hosts=None) -> Dict[str, object]:
    """Combined hints. NEVER includes selectors, families, or any captured value
    — only category labels + fixed notes. Safe to persist into a draft."""
    wrappers = detect_wrappers(html, scripts, hosts)
    shells = detect_shells(html, scripts, hosts)
    return {
        "wrappers": wrappers,
        "shells": shells,
        "has_membership_workflow": any(w["kind"] == "membership_workflow" for w in wrappers),
    }
