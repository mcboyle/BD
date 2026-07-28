"""Bucket 2 (GUI-config parity): boot-time `.env` loader + the deploy/path/port/host
editor key set.

These env vars are consumed at boot or by external CLI tools, never by the running
service, so a *live* GUI write cannot take effect. The GUI editor persists them to a
`.env`; this module seeds them into the environment at process start with
``os.environ.setdefault`` — so the real environment / systemd unit ALWAYS wins and
the `.env` is only a fallback.

This decision governs where OPERATOR configuration persists -- it is the GUI
editor's target. It is not a ban on drop-ins as a mechanism: `capture.sh` writes
`20-capture-vault.conf` for the duration of one run and removes it again, and
the box already carries `10-display.conf`. The distinction is lifetime and
authorship, and the deciding factor is the failure mode: a stale `.env` line is
invisible to this editor's model, while a stale drop-in is the first thing
`systemctl cat bulkdownloader` shows.

`.env` location (operator decision: `.env`, not a systemd drop-in):
  1. ``$BD_ENVFILE`` if set (explicit override; used by tests + advanced ops).
  2. ``Path.cwd()/.env`` — the systemd unit sets ``WorkingDirectory=APP_DIR`` and
     Matt's overlay deploys run from ``~/BulkDownloader``, so cwd is the install
     dir. This is independent of ``BD_HOME`` (which is itself one of the editable
     keys), avoiding the locate-the-config chicken-and-egg.

Imported at the very top of ``bulk_downloader/__init__.py`` so the seed is applied
before any env-reading import (Bucket 1's call-time getters read env too).
"""
import os
from pathlib import Path

# ── editable key set (canonical) ─────────────────────────────────────────────
# Single source of truth for the editor. Mirrors
# tools/config_surface_inventory.py::_DEPLOY_ONLY minus the host-managed infra
# vars, plus BD_DISABLE_VPN_RUNTIME (deferred from Bucket 1's _IMPORT_TIME — a
# one-time init() boot-gate a live getter can't retroactively flip). A drift test
# (test_v3_66_504_envfile_editor) re-derives _DEPLOY_ONLY from source and asserts
# this set matches, so a new deploy var can't silently escape the editor.

_FOUNDATION = ("BD_REPO", "BD_ROOT", "BD_HOME", "BD_INSTALL_DIR")
# call-time path roots: read live in many places; a mid-run change splits state
# between the old + new root (split-brain) -> restart-RECOMMENDED by policy.
_CALLTIME_ROOTS = ("BD_KB_DIR", "BD_LOG_FILE", "BD_SITES_CONFIG_PATH",
                   "BD_VPN_CONFIG_PATH", "BD_WIDGETS_CONFIG_PATH", "BD_CAPTURES_ROOT")
# read only by external ops CLIs (rollback.py / edge_deploy k8s seed / audit tools
# like verify_audit + the promoted witnesses), never the service.
_CLI_TOOL = ("BD_DOWNLOAD_DIR", "BD_RELEASE_ARCHIVE", "BD_WORK")
_PORTS = ("BD_PORT", "BD_COCKPIT_PORT", "BD_FLEET_PORT", "BD_FRAMEWORK_PORT")
_HOST = ("BD_HOST",)
_URL = ("BD_URL",)
_BOOL = ("BD_DEV_MODE_DISABLE", "BD_DISABLE_KEEPALIVE")
_VPN_DISABLE = ("BD_DISABLE_VPN_RUNTIME",)

# reader-class -> honest "when does this take effect" UX copy (deep plan §1.1).
APPLIES = {
    "restart": "Applies on restart (bound once before any request handler runs).",
    "restart-recommended": ("Applies on restart (recommended): some readers are call-time, "
                            "so a mid-run change splits state between the old and new root."),
    "cli-tool": "Used by external CLI/ops tools, not the running service.",
    "informational": "Descriptor only — no service reader; recorded for reference.",
}

_VPN_DANGER_NOTE = (
    "Disables the VPN runtime entirely (one-time boot gate in vpn_runtime.py). "
    "With this set, downloads/captures run WITHOUT the VPN — only on a trusted "
    "network you control. Takes effect on the next restart."
)


def _entry(name, kind, applies, *, foundation=False, danger=False, danger_note=""):
    return {"name": name, "kind": kind, "applies": applies, "foundation": foundation,
            "danger": danger, "danger_note": danger_note}


# Ordered for the GUI section render order.
EDITOR_KEYS = (
    [_entry(k, "path", "restart-recommended", foundation=True) for k in _FOUNDATION]
    + [_entry(k, "path", "restart-recommended") for k in _CALLTIME_ROOTS]
    + [_entry(k, "path", "cli-tool") for k in _CLI_TOOL]
    + [_entry(k, "port", "restart") for k in _PORTS]
    + [_entry(k, "host", "restart") for k in _HOST]
    + [_entry(k, "url", "informational") for k in _URL]
    + [_entry(k, "bool", "restart") for k in _BOOL]
    + [_entry(k, "bool", "restart", danger=True, danger_note=_VPN_DANGER_NOTE) for k in _VPN_DISABLE]
)

EDITOR_KEY_NAMES = [e["name"] for e in EDITOR_KEYS]
FOUNDATION_KEYS = frozenset(_FOUNDATION)
PORT_KEYS = frozenset(_PORTS)
BOOL_KEYS = frozenset(_BOOL + _VPN_DISABLE)
# non-foundation path roots whose parent should exist (warn, not hard-reject).
PATH_KEYS = frozenset(_CALLTIME_ROOTS + _CLI_TOOL)


# ── .env file mechanics ──────────────────────────────────────────────────────
def resolve_envfile_path():
    """The canonical read+write `.env` path. ``$BD_ENVFILE`` override, else
    ``cwd/.env`` (systemd ``WorkingDirectory=APP_DIR``). Independent of ``BD_HOME``."""
    override = os.environ.get("BD_ENVFILE")
    if override:
        return Path(override)
    return Path.cwd() / ".env"


def _candidate_paths():
    """Boot-seed candidates in priority order: the canonical path, then a fixed
    ``~/BulkDownloader/.env`` fallback (for launches whose cwd isn't the install
    dir). Never resolved via ``BD_HOME`` (it may itself be set by the `.env`)."""
    cands = [resolve_envfile_path()]
    home_default = Path.home() / "BulkDownloader" / ".env"
    if home_default not in cands:
        cands.append(home_default)
    return cands


def parse_envfile(text):
    """Parse ``KEY=VALUE`` lines. Skips blanks, ``#`` comments, and lines with no
    ``=``. Values keep everything after the first ``=`` (stripped of surrounding
    whitespace and a single layer of matching quotes)."""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k] = v
    return out


def load_envfile(path=None):
    """Seed the `.env` into ``os.environ`` with ``setdefault`` (real env wins).
    Returns the count of keys actually applied. The first existing candidate file
    wins. Never raises on a missing/unreadable file."""
    paths = [Path(path)] if path else _candidate_paths()
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        applied = 0
        for k, v in parse_envfile(text).items():
            if k not in os.environ:
                os.environ[k] = v
                applied += 1
        return applied
    return 0
