<!-- verified-against: v3.66.158 -->
# BulkDownloader — template pipeline schemas (v3.66.158)

The three JSON shapes in the **capture → build → normalize → review → promote**
pipeline, extracted from the code that produces and consumes them. **Source is
ground truth** — each section cites the `file::func` it came from; re-verify
there if anything looks off. The schema version strings are real and asserted by
the pipeline.

```
 capture.wacz
   │  tools/build_template_from_wacz.py :: build_template
   ▼
 ① DRAFT            templates/drafts/<host>.template-draft.json
   │                schema_version: bulk_downloader.template_draft.v1
   │                template_status: draft_requires_review
   │  bulk_downloader/template_normalize.py :: normalize_draft
   ▼
 ② REVIEW CANDIDATE templates/review_candidates/<host>.candidate.json
   │                schema: bulk_downloader.template.review_candidate.v1
   │                status: review_ready | draft_review_required   (NEVER enabled)
   │  ── human review: add api{}, quality.resolution_option, modal row_selectors ──
   │  tools/promote_template.py :: main   (--enable)
   ▼
 ③ REVIEWED         templates/reviewed/<host>.template.json
                    status: enabled | reviewed_not_enabled
                    consumed by template_registry.py + template_assist.py
```

**What is auto-derived vs human-added** (the crux of the "review-middle"):

| Field | ① draft | ② candidate | ③ reviewed |
|---|:--:|:--:|:--:|
| `selectors.login/player/quality{open_menu}` | auto | auto | auto |
| `selectors.download.button_hint` → `.trigger` | `button_hint` | `trigger` | `trigger` |
| `network_patterns` (flat, scrubbed) | relative `api_patterns` | flattened+scrubbed | full URLs |
| `resolutions` / ladder | `resolutions_seen` | `resolutions` | `resolutions` |
| `network_discovery.observed_api_hosts` (hint) | auto | (warning text) | — |
| **`api{base, paths}`** | — | — | **human** |
| **`selectors.quality.resolution_option`** | — | — | **human** |
| **`selectors.download.row_selectors`** (modal) | only if already present | only if pre-existing+safe+modal | **human** |
| **`template_logic`** | — | — | **human** |
| `status` | `draft_requires_review` | `review_ready`/… | `enabled` |

---

## ① Draft — `bulk_downloader.template_draft.v1`

**Producer:** `tools/build_template_from_wacz.py :: build_template(path)`.
**Status:** always `draft_requires_review`. A seed, never enabled. **Never has an
`api` block** — the API stays relative on purpose (no-guess rule).

```jsonc
{
  "schema_version": "bulk_downloader.template_draft.v1",
  "template_status": "draft_requires_review",
  "confidence": "low|medium|high",          // high needs player+quality+api_patterns
  "source": {
    "capture_file": "<name>.wacz", "capture_sha256": "…",
    "captured_at": "…", "url_no_query": "…", "origin": "…", "host": "…",
    "dom_log_count": 0, "network_log_count": 0, "full_snapshot_labels": []
  },
  "match": { "hosts": ["app.example.com"], "url_patterns": ["^https://app\\.example\\.com/"] },
  "selectors": {
    "login":   { "email": "…", "password": "…", "submit": "…" },
    "player":  { "container": "…", "play_button": "…" },
    "quality": { "open_menu": "…", "available_resolutions": [1080, 720] },
    "download":{ "button_hint": "[aria-label*=\"Download\" i]" }   // a HINT, not a row
  },
  "network_discovery": {
    "top_hosts": [{ "host": "…", "count": 0 }],
    "api_patterns": ["/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"],
    "observed_api_hosts": ["api2.example.com"],   // 157: non-authoritative review hint only
    "media_patterns": [".../AVC_{resolution}.mp4", ".../{manifest}.m3u8"],
    "resolutions_seen": [2160, 1080, 720],        // 154: incl. HLS/DASH manifest + bare heights
    "status_counts": { "200": 0 }, "content_type_counts": { "application/json": 0 }
  },
  "resolution_priority": [2160, 1080, 720],        // intersection of seen ∪ quality menu
  "workflow": { "auth": "…", "capture_mode": "user_driven", "recommended_steps": [ … ] },
  "guardrails": [ "Do not bypass Cloudflare/CAPTCHA/DRM/paywalls.", … ],
  "anti_bot_detection": { "present": false, "vendors": [], "challenge_count": 0 },
  "notes": [ "This is a template seed, not a final template.", … ]
}
```

Key points: `selectors.download.button_hint` is a click hint, **never** a row
selector. `observed_api_hosts` records the netloc(s) that served
`download-resolution` (a diagnostic surfaced to the reviewer), **not** the
authoritative `api_host` — so the builder never auto-creates an `api` block.
`resolutions_seen` recovers the ladder from API calls, segment heights, and HLS
master / DASH MPD manifest bodies (154) — but only when those bodies were captured.

---

## ② Review candidate — `bulk_downloader.template.review_candidate.v1`

**Producer:** `bulk_downloader/template_normalize.py :: normalize_draft(draft)`
(CLI `tools/normalize_template_draft.py`). Bridges the draft into the runtime
shape **without enabling anything**.
**Status:** `review_ready` or `draft_review_required` — **never `enabled`**.

```jsonc
{
  "schema": "bulk_downloader.template.review_candidate.v1",
  "normalized_from": "bulk_downloader.template_draft.v1",
  "status": "review_ready | draft_review_required",
  "host": "app.example.com",
  "confidence": "low|medium|high",
  "match": { "hosts": [ … ], "url_patterns": [ … ] },
  "selectors": {
    "login":   { … }, "player": { … },
    "quality": { "open_menu": "…" },                 // resolution_option added in review
    "download":{ "trigger": "…",                     // from draft button_hint/trigger/button
                 "row_selectors": [ … ] }            // ONLY if pre-existing + safe + modal-scoped
  },
  "network_patterns": [ "https://…/download-resolution/…" ],  // flattened + pattern_hygiene scrub
  "resolutions": [2160, 1080, 720],
  "source": { … }, "source_capture": "<name>.wacz",
  "evidence_counts": { "dom_log_count": 0, "network_log_count": 0 },
  "safety_notes": [ … ],                              // carried from draft guardrails
  "warnings": [ "API patterns kept relative — download-resolution observed on: api2.example.com; set api{base} accordingly", … ],
  "rejected_patterns": [ … ],                         // scrubbed/unsafe URLs, recorded
  "review_notes": [ "Add the concrete api{base, paths} and modal-scoped row_selectors during review.", … ]
}
```

**Normalizer rules** (`_map_selectors`, `_flatten_patterns`, `_resolutions`):
- `download.button_hint | trigger | button` → **`download.trigger`** (a click
  target that may open a modal) — **never** promoted to a row selector.
- `download.row_selectors` are kept **only** when already present, non-blocking by
  the linter, and modal-scoped (`_is_modal_scoped`). Otherwise a warning is added
  telling the reviewer the trigger only opens the modal.
- `network_patterns` = host-bearing draft patterns, flattened and run through
  `pattern_hygiene.scrub_network_patterns` (drops signed/short-lived/PII), plus
  host-less media suffixes.
- **Readiness:** `status == review_ready` iff `download.{trigger|row_selectors}`
  **and** non-empty `network_patterns` **and** non-empty `resolutions` **and** no
  blocking `selector_lint` issue; else `draft_review_required`.

**Still missing after normalize (review must add):** the concrete `api{base,
paths}` block, `selectors.quality.resolution_option`, and the modal-scoped
`download.row_selectors`. `review_ready` means "shaped correctly," **not**
"complete" — see ③.

---

## ③ Reviewed template — runtime shape (status `enabled`)

**Producer:** human review of a candidate, then
`tools/promote_template.py :: main <candidate> --enable`. Promote operates on the
(edited) candidate dict and writes it as-is plus `status` + `promotion_notes`, so
**reviewed = the reviewed candidate ∪ {api, resolution_option, row_selectors,
template_logic} + status**.
**Status:** `enabled` (with `--enable`) or `reviewed_not_enabled`. Written to
`--out-dir` (default `templates/reviewed/`) as `<host>.template.json`.

Gold-shaped example (`templates/reviewed/app.reptyle.com.template.json`):

```jsonc
{
  "schema": "…", "source_capture": "…", "status": "enabled",
  "safety_notes": [ … ], "host": "app.reptyle.com", "confidence": "high",
  "selectors": {
    "login":   { "email": "input[type=\"email\"]", "password": "input[type=\"password\"]",
                 "submit": "button[type=\"submit\"], input[type=\"submit\"]" },
    "player":  { "container": ".video-js, .vjs-tech, video",
                 "play_button": ".vjs-big-play-button, button[aria-label*='Play']" },
    "quality": { "open_menu": "[aria-label*=\"quality\" i]",
                 "resolution_option": "[aria-label*=\"{resolution}\" i], button:has-text(\"{resolution}\")" },
    "download":{ "trigger": "button[data-tooltip=\"Download Full Movie\"], button:has([aria-label=\"download\"]), [data-tooltip*=\"Download\" i]",
                 "row_selectors": [ /* 12 modal-scoped — see §gold below */ ] }
  },
  "network_patterns": [ "https://api2.reptyle.com/api/v1/movie/{id}/download-resolution/{resolution}", … ],
  "resolutions": [2160, 1440, 1080, 720, 540, 480, 360, 240],
  "evidence_counts": { … }, "review_notes": [ … ],
  "api": {
    "base": "https://api2.reptyle.com/api/v1",
    "movie_watch": "/movie/{movie_id}/watch",
    "download_resolution": "/movie/{movie_id}/download-resolution/{resolution}",
    "trailer": "/movie/{movie_id}/trailer"
  },
  "template_logic": { "movie_id_source": "…", "resolution_priority": [ … ], "download_flow": [ … ] },
  "promotion_notes": [ "Passed BAD_TERMS + selector-lint safety checks.", … ]
}
```

Note: the gold routes on **`host`** and carries no `match` block — runtime
routing uses `host` (see ④). `match` may be carried from the candidate for
provenance but is not required.

**Promote gates** (all must pass, else `SystemExit`):
1. Refuses if `status == "enabled"` already (no re-promote).
2. Refuses a raw builder draft (`_looks_like_raw_builder_draft` — e.g. a
   `template_draft.v1` / `draft_requires_review`); promote only takes candidates.
3. **BAD_TERMS** scan over `network_patterns` + `api` values (blocks signed-URL /
   token / cookie leakage).
4. `selectors.download` must have `trigger` **or** `row_selectors` (or legacy
   `button`).
5. No **blocking** `selector_lint` issue (`has_blocking_issues`).
6. `resolutions` must be non-empty.
7. Warns (does not fail) if there are no modal `row_selectors`.

---

## ④ What the runtime actually reads (load-bearing fields)

If these are wrong, downloads break — everything else is provenance/diagnostic.

| Field | Consumer | Effect |
|---|---|---|
| `status == "enabled"` | `template_registry.load_templates` | Non-enabled files are skipped entirely. |
| `host` | `template_registry.find_template_for_url` → `_host_matches` | Matched to the URL host (exact or subdomain). No host match → template unused. |
| `api{base}` + `api{<key>}` | `template_assist.build_api_url(t, key, **vals)` | `urljoin(base, path)` with `{placeholder}` substitution. **No `api` block → returns `None`** (no API download). |
| `resolutions` | `template_assist.preferred_resolutions` | Drives the resolution ladder (desc, deduped). |
| `selectors.download.trigger` | `template_to_learned_download` → `trigger_selectors` | Click target tried first (opens the modal). |
| `selectors.download.row_selectors` | `template_to_learned_download` → `row_selectors` | The real per-resolution download links scraped from the modal. |
| `selectors.quality.open_menu` | `template_to_learned_download` → `trigger_selectors` | Appended as a trigger. |
| `selectors.quality.resolution_option` (`{resolution}`) | `template_to_learned_download` | Expanded per `preferred_resolutions` into concrete option selectors. |
| `selectors.download.button` (legacy single) | `template_to_learned_download` → `row_selectors` | Back-compat. |

Dirs scanned: `templates/reviewed` + `templates/enabled`, files `*.template.json`.
`network_patterns` documents the discovered URL shape but is **not** consumed by
these three assist helpers — `build_api_url` works off the `api` block, which is
why review must add it.

---

## ⑤ Gold reference + the human-review payload

`templates/reviewed/app.reptyle.com.template.json` is the proven, enabled
reference. **Back it up before any promote** — `promote --enable` writes to that
exact path and will overwrite it. Regeneration runbook:
`REPTYLE_CAPTURE_RUNBOOK_v3_66_158.md` (promote to a staging dir, diff, swap).

The two blocks review adds (verbatim from the gold, reusable when re-capturing
reptyle):

`api`:
```json
{
  "base": "https://api2.reptyle.com/api/v1",
  "movie_watch": "/movie/{movie_id}/watch",
  "download_resolution": "/movie/{movie_id}/download-resolution/{resolution}",
  "trailer": "/movie/{movie_id}/trailer"
}
```

`selectors.download.row_selectors` (12 modal-scoped):
```json
[
  "[role=\"dialog\"] a[href*=\"download\" i]",
  "[role=\"dialog\"] a[href*=\"download-resolution\" i]",
  "[role=\"dialog\"] button:has-text(\"2160\")",
  "[role=\"dialog\"] button:has-text(\"1440\")",
  "[role=\"dialog\"] button:has-text(\"1080\")",
  "[role=\"dialog\"] button:has-text(\"720\")",
  ".ant-modal a[href*=\"download\" i]",
  ".ant-modal a[href*=\"download-resolution\" i]",
  ".ant-modal button:has-text(\"2160\")",
  ".ant-modal button:has-text(\"1440\")",
  ".ant-modal button:has-text(\"1080\")",
  ".ant-modal button:has-text(\"720\")"
]
```

---

## ⑥ Quick reference

| | Schema string | Status values | Default path |
|---|---|---|---|
| ① draft | `bulk_downloader.template_draft.v1` | `draft_requires_review` | `templates/drafts/<host>.template-draft.json` |
| ② candidate | `bulk_downloader.template.review_candidate.v1` | `review_ready`, `draft_review_required` | `templates/review_candidates/<host>.candidate.json` |
| ③ reviewed | (carried from candidate) | `enabled`, `reviewed_not_enabled` | `templates/reviewed/<host>.template.json` |

**Source of truth (re-verify here):** `tools/build_template_from_wacz.py`,
`bulk_downloader/template_normalize.py`, `tools/promote_template.py`,
`bulk_downloader/template_assist.py`, `bulk_downloader/template_registry.py`,
`bulk_downloader/pattern_hygiene.py`, `bulk_downloader/selector_lint.py`.
