# BulkDownloader

Self-hosted batch video downloader: **Flask + Playwright** backend, **React/TypeScript** SPA frontend.
Single-operator deployment to a headless host.

> **This repository is private by intent.** `tests/fixtures/recon_corpus/` contains
> real captures from authenticated member sites. The embedded JWTs are expired and
> cannot be replayed, but their payloads still carry account identifiers. See
> [SECURITY.md](SECURITY.md) before changing repository visibility.

## Layout

| Path | Contents |
| --- | --- |
| `bulk_downloader/` | 561 `.py` — the application |
| `tests/` | 1073 test files, plus `corpus/` and `fixtures/` capture assets |
| `tools/` | 216 `.py` — build, graph, regeneration, and gate scripts |
| `frontend/` | React/TS SPA (own `node_modules`, not tracked) |
| `toolchain/bin/` | ~249 `bd-*` operator tools ("bdsuite") |
| `project-knowledge/` | 365 durable docs, schemas, and operating cards |
| `docs/repo/` | environment and layout references |

## Quick start

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cd frontend && npm ci && cd ..
./venv/bin/python -m pytest tests/test_settings_center_slice4.py   # fast sanity check
```

Do **not** run the whole `tests/` directory locally — it contains known long
runners (`test_perf_lab.py`, `test_v3_66_146_nav_guard`). Run a derived band.

## Before contributing anything

Read **[CLAUDE.md](CLAUDE.md)**. It is the operating contract — release
discipline, the seven SHA-pinned guard files, band-derivation rules, and the
environment traps. It applies to humans and to Claude Code equally.

Three rules that catch newcomers:

1. **A version bump is three edits together** — `__init__.py`, the test pin, and
   an ASCII-only `CHANGELOG.md` entry. Never one without the others.
2. **A route change bands two suites**, and requires re-freezing the route-map
   baseline.
3. **`unzip -o` deploys never delete.** A file removed in a cut keeps living on
   the target host until the deploy-manifest step removes it. Git tracking the
   deletion does not delete it there.
