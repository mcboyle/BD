# D3 frontend bundle audit (U8)

Static analysis of `frontend/src/**` against `frontend/package.json`.
Full byte-count requires `npm run build` which needs network access;
this audit covers what can be verified without one.

## Results

| Check | Result |
|---|---|
| Declared runtime deps | 15 |
| Declared dev/toolchain | 10 |
| Imports detected | 15 |
| **Unused runtime deps** | **0** |
| **Heavy-import anti-patterns** | **none** |
| Imports not in `package.json` | 0 |

## Per-package import counts

| Dep | Files | Notes |
|---|---|---|
| `react` | 19 | core |
| `@tanstack/react-query` | 17 | every route + several components |
| `lucide-react` | 17 | tree-shakable per-icon — verified via named imports |
| `sonner` | 8 | toasts, mounted at root + per-route |
| `react-router-dom` | 6 | routing |
| `class-variance-authority` | 2 | shadcn variants (Button, Badge) |
| `cmdk` | 1 | command palette only |
| `recharts` | 1 | ThroughputSparkline only |
| `clsx`, `tailwind-merge` | 1 each | inside `cn()` helper |
| `react-dom` | 1 | bootstrap only |
| `@radix-ui/react-dialog` | 1 | Dialog primitive |
| `@radix-ui/react-dropdown-menu` | 1 | DropdownMenu primitive |
| `@radix-ui/react-slot` | 1 | Button asChild |
| `@radix-ui/react-tabs` | 1 | Tabs primitive |

## Bundle-size estimate

Order-of-magnitude estimates from package documentation
(gzip, post-tree-shake, NOT confirmed by a real build):

| Bucket | Approx gzip |
|---|---|
| React 18 + ReactDOM | ~45 KB |
| react-router-dom | ~12 KB |
| @tanstack/react-query | ~14 KB |
| recharts (used surface only) | ~35-50 KB (LARGEST) |
| sonner | ~6 KB |
| Radix primitives (the 4 used) | ~12 KB |
| cmdk | ~5 KB |
| lucide-react (~40 icons used) | ~8 KB (tree-shaken) |
| App code | ~20-30 KB |
| **Total estimate** | **~155-180 KB gzip** |

Recharts is the headline cost. The narrative budgeted for it deliberately
when picking the React+Recharts stack over htmx+Chart.js.

## Recommended verification step (operator)

```
cd frontend
npm ci
npm run build
ls -lh dist/assets/*.js   # confirm gzip sizes
```

Expected: one main chunk in the 150-200 KB gzip range. If anything is
notably larger, look at the breakdown via:

```
npx vite-bundle-visualizer
```

(One-shot; no need to add it to package.json.)

## What was NOT audited

- Actual rendered byte count (no network access to run `npm install`
  in this sandbox).
- CSS bundle size — Tailwind's JIT only emits classes that appear in
  the source, so this is already minimized by definition.
- Image assets — there are none in the SPA (icons are SVG via lucide,
  inline in the bundle).
- Polyfill cost — Vite's `legacy` plugin is not used; modern browsers
  target only.

If any of the operator's `npm run build` numbers are surprising, the
single biggest lever is `recharts` — it's a 50KB chunk for one
sparkline. A 1KB hand-rolled SVG sparkline would shrink the bundle by
30%. Not recommended now (U8 polish, not refactor), flag for future
work if size matters.
