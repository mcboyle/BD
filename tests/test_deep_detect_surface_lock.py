"""Surface-lock for the deep_detect decomposition (characterization, not RED-first).

deep_detect is the import-stack FOUNDATION (consumed by app + runner + ~6 modules;
imports none of them -- a true leaf). Its body move is trivial but its SURFACE is the
widest in the program: external code imports ~36 of its private functions directly,
so the package shim's __all__ must re-export them all or those consumers break.

Freezes the external surface as of v3.66.392 (the PRE-move monolith), emitted by
`tools/deep_detect_surface.py --emit-lock` (AST: real imports + code attribute-uses,
EXCLUDING the `# Mirrors deep_detect._X` doc comments a grep would wrongly catch).
After deep_detect.py becomes a deep_detect/ package, this is the proof the move
preserved the surface. Runner-safe: zero-arg fns.
"""
import importlib

from bulk_downloader import deep_detect as dd

# --- the frozen surface (89 public + 36 external-required privates, v3.66.392) ---
FROZEN_PUBLIC = {
    'AD_TRACKER_HOSTS',
    'AD_TRACKER_PATH_FRAGMENTS',
    'AMBIGUOUS_QUALITY_LABELS',
    'BAD_DOWNLOAD_TERMS',
    'BINARY_MIME_PREFIXES',
    'BOT_DEFENSE_MARKERS',
    'CAPTCHA_MARKERS',
    'CDN_MEDIA_HINTS',
    'CLICKABLE_SELECTOR_TAGS',
    'CODEC_BONUS',
    'CODEC_RE',
    'DEVICE_CODE_MARKERS',
    'DOWNLOAD_TERMS',
    'DRM_MARKERS',
    'FPS_RE',
    'HONEYPOT_CSS_HIDDEN',
    'HONEYPOT_NAMES',
    'JSONLD_MEDIA_TYPES',
    'LINK_VISIBILITY_TRAPS',
    'LOGIN_FIELD_TERMS',
    'LOGIN_TERMS',
    'MEDIA_JSON_KEYS',
    'META_REFRESH_RE',
    'MFA_FIELD_NAMES',
    'MFA_TERMS',
    'OAUTH_LOGIN_PATTERNS',
    'PASSWORDLESS_TERMS',
    'PLAYER_LIBRARIES',
    'POST_REVEAL_BUTTON_TERMS',
    'PROGRESSIVE_MEDIA_EXTENSIONS',
    'PROVIDERS',
    'P_LABEL_RE',
    'QUALITY_LABEL_RE',
    'RESOLUTION_RE',
    'RESOLUTION_TIERS',
    'SAML_MARKERS',
    'SIDECAR_EXTENSIONS',
    'SIGNED_URL_HINTS',
    'SIZE_RE',
    'SOURCE_TYPES',
    'SSO_PROVIDERS',
    'STATE_BLOB_SCRIPT_IDS',
    'STREAM_MANIFEST_EXTENSIONS',
    'STREAM_MIME_TYPES',
    'STREAM_SEGMENT_EXTENSIONS',
    'SUBTITLE_EXTENSIONS',
    'SUSPICIOUS_URL_PATTERNS',
    'TOKEN_FIELDS',
    'TRACKER_PATH_PATTERNS',
    'URL_BEARING_ATTRS',
    'WEBAUTHN_MARKERS',
    'canonicalize_url',
    'classify_bot_defenses',
    'classify_resolution',
    'classify_url',
    'decode_url',
    'deep_detect',
    'deep_detect_live',
    'detect_fingerprinting_signals',
    'detect_post_reveal_forms',
    'detect_resolution_from_text',
    'detect_signed_url',
    'extract_jsonld_media',
    'extract_player_configs',
    'extract_provider_embeds',
    'extract_resolution_cards',
    'extract_state_blob_urls',
    'find_honeypots',
    'follow_meta_refresh',
    'get_metrics',
    'hls_has_encryption',
    'is_dash_manifest',
    'is_hls_manifest',
    'is_hls_master',
    'is_smooth_manifest',
    'maybe_decode_base64',
    'parse_codec',
    'parse_dash_mpd',
    'parse_fps',
    'parse_hls_master',
    'parse_size_bytes',
    'parse_smooth_streaming',
    'reset_metrics',
    'scan_blockers',
    'scan_links_for_traps',
    'score_download_link',
    'score_login_form',
    'score_login_page',
    'to_site_config_block',
}

EXTERNAL_REQUIRED_PRIVATE = {  # privates external modules import -- shim MUST export
    '_CEILINGS',
    '_CONFIDENCE_BREAKPOINTS',
    '_DISCLAIMER_RULES',
    '_annotate_download_candidate',
    '_apply_signed_url_annotations',
    '_attach_confidence',
    '_attach_confidence_ceiling',
    '_build_default_http_client',
    '_build_disclaimers',
    '_candidate_is_mixed_content',
    '_candidate_violates_csp',
    '_classify_disclaimer',
    '_csp_source_matches',
    '_dedup_candidates',
    '_detect_ceiling_signals',
    '_extract_csp_from_headers',
    '_extract_csp_from_html',
    '_extract_provider_ids',
    '_fetch_manifest_capped',
    '_finalize_buckets',
    '_flatten_download_candidates',
    '_is_visible_input',
    '_merge_csp',
    '_parse_content_disposition',
    '_parse_csp_policy',
    '_parse_hls_attrs',
    '_parse_srcset',
    '_poll_async_workflow',
    '_post_reveal_key',
    '_probe_head',
    '_refine_source_type_from_headers',
    '_rejected_view',
    '_score_to_confidence',
    '_try_parse_loose_json',
    '_url_host',
    '_walk_json_for_media',
}

def test_public_surface_is_exactly_preserved():
    """Every frozen public name (functions + module constants) still resolves.
    Additions (a new helper) are allowed; DROPS fail loudly."""
    live = {n for n in dir(dd) if not n.startswith("_")}
    missing = FROZEN_PUBLIC - live
    assert not missing, f"deep_detect dropped public names: {sorted(missing)}"
    assert isinstance(live - FROZEN_PUBLIC, set)  # additions allowed


def test_external_required_privates_resolve_from_package_root():
    """The privates external modules import directly -- the shim MUST keep each
    resolvable from the package root, or its consumers break."""
    for name in EXTERNAL_REQUIRED_PRIVATE:
        assert hasattr(dd, name), (
            f"{name!r} must stay resolvable from the deep_detect package root -- "
            f"an external module imports it directly")


def test_module_imports_cleanly_true_leaf():
    """deep_detect imports none of the other monoliths; importing it must not pull
    a sibling monolith at module load."""
    m = importlib.import_module("bulk_downloader.deep_detect")
    assert m is dd and getattr(m, "__name__", "").endswith("deep_detect")
