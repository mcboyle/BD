# BulkDownloader UI tokens (D3 phase 2 reference)

**Status:** Reference document only. The tokens below currently live
**inline** in `bulk_downloader/templates/m_ops.html` (PT8, post-v3.63.8).
This document exists so the **next** template that adopts the mockup
design language uses the same token names — extraction to a shared
`tokens.css` file (D3 wedge 3) happens when a second page actually
uses them, not before. Single-consumer extraction is just moving CSS
around.

**Source:** D3 mockup HTML files (conversation outputs only,
`/mnt/user-data/outputs/ui_mockups/`, do not enter repo per
`DEFER_ui_redesign.md`).

## Color palette

| Token            | Hex          | Use                                                    |
|------------------|--------------|--------------------------------------------------------|
| `--bg`           | `#f0f0f5`    | Page background                                        |
| `--surface`      | `#ffffff`    | Card / surface background                              |
| `--surface-2`    | `#fafafe`    | Inset surface, empty-state background                  |
| `--ink`          | `#0a0a0f`    | Primary text                                           |
| `--ink-2`        | `#4a4a55`    | Secondary text                                         |
| `--ink-3`        | `#8a8a9a`    | Tertiary text, labels, hints                           |
| `--hairline`     | `rgba(10,10,15,0.08)` | 0.5px borders                                |
| `--primary`      | `#3a4cff`    | Brand primary, links, action accents                   |
| `--primary-soft` | `#e8edff`    | Tinted surface for primary-associated cards            |
| `--green`        | `#28c76f`    | Success, active state                                  |
| `--green-soft`   | `#e6f7ee`    | Success-tinted pill background                         |
| `--amber`        | `#f59e0b`    | Warning, attention                                     |
| `--amber-soft`   | `#fff4e3`    | Warning-tinted pill background                         |
| `--red`          | `#ef4444`    | Error, destructive action                              |
| `--red-soft`     | `#fdecec`    | Error-tinted pill background                           |

Status palette is **5-color**: primary + green/amber/red, each with a
solid and a `-soft` tinted-background partner. The mockups never
introduce a sixth status color; new states pick from existing four.

## Radii

| Token   | Value | Use                                              |
|---------|-------|--------------------------------------------------|
| `--r-sm`| `10px`| Small buttons, pills, log container              |
| `--r-md`| `14px`| Form fields, status line, alerts                 |
| `--r-lg`| `18px`| Cards, sheets, hero containers                   |

## Type

* **Family:** Inter (loaded from Google Fonts), with the standard
  system fallback stack (`-apple-system, BlinkMacSystemFont,
  system-ui, sans-serif`).
* **Letter-spacing:** `-0.01em` on body, `-0.025em` on display
  headings. The negative tracking is a mockup signature; don't drop it.
* **Tabular numerics:** `font-variant-numeric: tabular-nums` on every
  count, version, percentage, and duration. Status lines + per-site
  metric rows depend on this for stable widths when values change.

## Hairlines & elevation

* Border width: `0.5px` everywhere a border is drawn (not 1px). Use
  `--hairline` for the color. The 0.5px hairline is **the** signature
  visual difference from the existing UI's 1px borders.
* No box-shadows on cards. The hairline + tinted background does the
  separation work.
* Sheets (full-screen overlays) get a backdrop-filter blur, not a
  shadow.

## Glass surfaces

The sticky header uses:
```css
background: rgba(240,240,245,0.92);
backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
```

This is a token-able pattern but currently inline. If extracted later
as `--glass-bg` + `--glass-blur` keep them as a pair.

## When to extract to `tokens.css`

The cost of having tokens inline in one template is one extra block of
CSS variables per template. The cost of extracting is:

* A new file (`bulk_downloader/static/css/tokens.css` or similar)
* Static-file serving wired up if it isn't already (the existing
  `static/` setup serves the manifest and icons, so this is a no-op)
* The `@import` at the top of every consuming template
* A contract test pinning the file existence so a refactor can't
  silently drop it

Worth doing the moment a second template adopts the palette. With
only `m_ops.html` using them, extraction is busywork.

## Not in scope for this document

* Component primitives (cards, pills, buttons) — those are still
  defined per-template until enough pages share them to justify a
  shared library. PT8's m_ops.html has the patterns; copy from
  there rather than re-invent.
* Dark-mode pairing. The existing `mobile.html` is dark-themed
  (`#08090c`). The mockup palette is light-themed. These are two
  different design languages; reconciling them is a separate
  decision (D3-tier, not in this document).
* Desktop `index.html` styling. The current desktop UI does not use
  this palette and is not in scope for D3 phase-2.

## Reference: D3 wedge status (post-PT8 update)

| Wedge                                       | Status                  |
|---------------------------------------------|-------------------------|
| 1. PWA meta tags                            | Done (mobile + desktop) |
| 2. Safe-area bottom padding                 | Done (mobile.html)      |
| 3. Design tokens CSS                        | **Deferred** (this doc) |
| 4. Bottom tab bar as progressive enhancement| Not started             |
| 5. One tab rebuilt as POC (Queue)           | Not started             |

The `DEFER_ui_redesign.md` source list still calls wedges 1+2
"smaller incremental steps a future session could take" — that's
stale. Both are already in the tree (mobile.html had manifest +
apple-touch-icon + theme-color + safe-area; PT8 added the two
missing `apple-mobile-web-app-*` extras and desktop index.html
already had the full set).
