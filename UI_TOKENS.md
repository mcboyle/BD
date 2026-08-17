# BulkDownloader UI tokens

**Current implementation:** `frontend/src/index.css`

**Tailwind binding:** `frontend/tailwind.config.js`

This document explains the shipped token contract. The CSS variables are the
runtime values; Tailwind maps utilities to those variables. A token change must
update both the light and dark palettes where applicable and keep the existing
frontend token tests green.

## Light palette

| Token | Value |
| --- | --- |
| `--bg` | `#f0f0f5` |
| `--surface` | `#ffffff` |
| `--surface-2` | `#f3f3fa` |
| `--ink` | `#0a0a0f` |
| `--ink-2` | `#3a3a46` |
| `--ink-3` | `#6c6c7c` |
| `--hairline` | `rgba(10, 10, 15, 0.18)` |
| `--primary` | `#3a4cff` |
| `--primary-soft` | `#e8edff` |
| `--green` | `#28c76f` |
| `--green-soft` | `#e6f7ee` |
| `--amber` | `#f59e0b` |
| `--amber-soft` | `#fff4e3` |
| `--amber-dim` | `#92400e` |
| `--red` | `#ef4444` |
| `--red-soft` | `#fdecec` |

## Dark palette

The `.dark` block in `frontend/src/index.css` supplies a value for every color
token above. It retains the same token names so consumers never branch on theme.
`--amber-dim` is deliberately brighter in dark mode because the composited dark
surface already meets the contrast requirement.

## Radii and type

| Token | Value |
| --- | --- |
| `--r-sm` | `10px` |
| `--r-md` | `14px` |
| `--r-lg` | `18px` |

The Tailwind configuration binds the Inter/system sans stack, `-0.01em` tight
tracking, `-0.025em` tighter tracking, and the complete color/radius token set.
Counts, versions, percentages, and durations use the `.tabular` utility for
stable widths.

## Structural rules

- Use `0.5px` hairlines through `.hairline`; do not substitute a default 1px
  border when the token treatment is intended.
- Cards rely on hairlines and token surfaces rather than decorative shadows.
- Use `text-amber-dim` for text on `bg-amber-soft` in light mode.
- Shared primitives such as `.section-head`, `.eyebrow`, form-control defaults,
  focus rings, and `.bd-table` live beside the tokens in `frontend/src/index.css`.
- The shipped light/dark pairing is current behavior, not deferred work.

When implementation and this explanation disagree, fix the disagreement in the
same cut and keep the point-of-use CSS/Tailwind tests as the executable authority.
