// Types for the deep_detect report shape, mirroring
// bulk_downloader/deep_detect.py. These describe what comes back from
// POST /api/dev/deep_detect (the dev API) and what
// template_auto_detect_mode='deep' feeds into the auto-pick flow.
//
// Kept narrow to what the SPA actually needs to render — the Python
// report has additional fields (warnings, blockers, manifests, etc.)
// not yet consumed here. Extend as the UI grows.

export type CandidateSourceType =
  | "resolution_download_card"
  | "state_url"
  | "provider_embed"
  | "player_config"
  | "jsonld_media"
  | "post_reveal"
  | "hls_variant"
  | "dash_representation"
  | string; // forward-compat: unknown sources from future deep_detect versions

export interface Resolution {
  label: string; // "1080p", "4k", "8k", "sd", ...
  rank: number; // numeric pixel height for sort (e.g. 1080, 2160, 4320)
}

export interface DownloadCandidate {
  source_type: CandidateSourceType;
  url: string | null;
  click_selector?: string | null;
  width?: number | null;
  height?: number | null;
  resolution?: Resolution;
  quality_label?: string;
  codec?: string | null;
  fps?: number | null;
  size_bytes?: number | null;
  requires_click?: boolean;
  text?: string;
  reasons?: string[];
  warnings?: string[];
  score: number;
}

export interface DeepDetectReport {
  best_login: unknown | null;
  best_download: DownloadCandidate | null;
  login_candidates: unknown[];
  download_candidates: DownloadCandidate[];
  rejected: unknown[];
  warnings: string[];
  blockers: Record<string, unknown>;
  provider_embeds: unknown[];
  workflow_required: unknown | null;
  manifests: {
    hls: unknown | null;
    dash: unknown | null;
  };
  source_breakdown: Record<string, number>;
  // Optional disclaimer banner from deep_detect_live when scan_blockers
  // flags DRM/CAPTCHA markers (v3.66.5 — HEAD-probe relaxation).
  probes?: {
    disclaimer?: string;
    [k: string]: unknown;
  };
}
