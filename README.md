# BulkDownloader

Self-hosted batch video downloader: **Flask + Playwright** backend, **React/TypeScript** SPA frontend.
Single-operator deployment to a headless host.

## Layout

| Path | Contents |
| --- | --- |
| `bulk_downloader/` | 565 `.py` — the application |
| `tests/` | 1139 tracked `test_*.py` files, plus `corpus/` and `fixtures/` capture assets |
| `tools/` | 246 `.py` — build, graph, regeneration, and gate scripts |
| `frontend/` | React/TS SPA (own `node_modules`, not tracked; `dist/` is gitignored) |
| `toolchain/bin/` | 246 `bd-*` operator tools ("bdsuite") |
| `project-knowledge/` | 365 durable docs, schemas, and operating cards |
| `docs/repo/` | environment and layout references |

Counts were measured at v3.66.818 and move every cut -- re-derive before quoting
them anywhere (`git ls-files bulk_downloader | grep -c '\.py$'`). `tools/*.py`
and the `toolchain/bin` `bd-*` suite are **disjoint populations** that happen to
be the same size right now; counting one never answers for the other.

## Quick start

```bash
python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt   # 3.12 is the box/CI interpreter; bare python3 may be 3.11
cd frontend && npm ci && cd ..
./venv/bin/python -m pytest tests/test_settings_center_slice4.py   # fast sanity check
```

Do **not** run the whole `tests/` directory locally — it contains known long
runners (`test_perf_lab.py`). Run a derived band.

## Before contributing anything

Read **[CLAUDE.md](CLAUDE.md)**. It is the operating contract — release
discipline, the seven SHA-pinned guard files, band-derivation rules, and the
environment traps. It applies to humans and to Claude Code equally.

Three rules that catch newcomers:

1. **A version bump is three edits together** — `__init__.py`, the test pin, and
   an ASCII-only `CHANGELOG.md` entry. Never one without the others.
2. **A route change bands two suites**, and requires re-freezing the route-map
   baseline.
3. **Deploy is `git fetch origin main && git reset --hard origin/main`** -- there
   is no zip overlay, so deletions propagate natively and there is no
   orphan-removal step. But moving files is not the same as making the running
   system match them. `git reset --hard` does not clear `__pycache__`/`.pyc`,
   does not refresh gitignored generated artifacts (`git clean -fd` will not
   remove them either -- that needs `-x`), does not restart the service, and
   does not deliver `frontend/dist/` at all (it is untracked, so a missing or
   stale SPA bundle is a silent 503). Work the **Deploy** section of
   [docs/repo/FRESH_HOST_BRINGUP.md](docs/repo/FRESH_HOST_BRINGUP.md)
   -- it is the canonical operator post-deploy checklist, and a second copy of that list
   here would only drift.
