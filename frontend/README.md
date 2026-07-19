# BulkDownloader frontend (D3 — React SPA)

This is the React SPA served by Flask at `/m2/*`. Locked stack:

- React 18 + Vite + TypeScript
- Tailwind CSS (palette bound to `UI_TOKENS.md`)
- TanStack Query for data fetching, sonner for toasts
- React Router for routing (basename `/m2`)
- shadcn/ui primitives (copied into `src/components/ui/`, not npm)
- Recharts for throughput sparklines

The full rationale lives in the project KB:
`D3_UI_DESIGN_NARRATIVE.md`. Unit-by-unit plan in `OPEN_THREADS.md`
§D3. **U1 is this scaffold**; U3+ start replacing the placeholder
home with real pages.

## Build

```
cd frontend
npm ci         # one-shot, uses package-lock.json once it exists
npm run build  # produces dist/, served by Flask
```

Build happens automatically during `install_linux.sh` /
`install_windows.bat` if Node is present. If Node is missing, the
install completes anyway and `/m2` returns 503 with a clear message
until Node is installed and `npm run build` runs.

## Dev

```
npm run dev    # Vite dev server on http://localhost:5173/m2/
```

The dev server proxies `/api/*` to `http://127.0.0.1:5555`, the Flask
port from `start_linux.sh`. Start Flask first, then the dev server.

## Tests

```
npm run test   # Vitest, one-shot
```

Per the D3 narrative: Vitest matches the Vite ecosystem. Python tests
(`run_tests.py`) cover the Flask side of the `/m2` mount.

## Why /m2/ and not /

`/` and `/m` are the existing UIs. Both untouched during D3. `/m2/`
ships behind `?ui=v2` (U2 work) until stable, then `/m` retires in
v3.65.0. See `OPEN_THREADS.md` §D3 for the retirement timeline.
