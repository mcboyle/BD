"""Bulk Downloader — package layout

Module map:
  constants.py    — Regexes, hint lists, retry tables, file paths
  detect.py       — Resolution scoring, find_best_download, size parsing
  db.py           — SQLite history + queue persistence
  cookies.py      — Cookie load/save/expiry helpers
  fname.py        — Filename templating + sanitization
  integrity.py    — ffprobe wrapper
  login.py        — Multi-strategy login with 154+ selectors
  runner.py       — SiteRunner class (the worker pool)
  app.py          — Flask routes + HTML/CSS/JS (inline HTML constant)
  learn.py        — Click recorder + selector classifier (auto-teach)
  hooks.py        — Webhook + Stash/Plex/Jellyfin event dispatch
  aiassist.py     — Local LLM (Ollama) selector/diff suggestions
  log.py          — Structured logging setup
  push.py         — Web Push subscriptions
  templates.py    — Filename templates (string-template wrapper)

HTML/CSS/JS is still embedded in app.py's HTML constant (template/static
extraction was planned but never executed; the Phase 3 split was at the
Python-module level only). The thin entry point at top-level
`downloader_ui.py` imports from this package and runs the Flask dev
server. PyInstaller builds bundle the package automatically.
"""
# Bucket 2 (.env editor): seed deploy/path/port/host env vars from a `.env`
# BEFORE any env-reading import below. setdefault semantics -> the real env /
# systemd unit always wins; the `.env` is only a fallback. Must stay at the very
# top so Bucket 1's call-time getters (which also read env) see the seeded values.
from . import _envfile as _envfile  # noqa: E402
_envfile.load_envfile()

__version__ = "3.66.1101"
