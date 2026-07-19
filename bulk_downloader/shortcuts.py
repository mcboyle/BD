"""Keyboard shortcuts catalog (Phase 151, Block Q).

Declarative catalog of every keyboard shortcut BD supports. The
front-end fetches this list to render the "Keyboard shortcuts" modal
(Shift+? convention) and to validate bindings.

Centralized here so:
  • There's a single source of truth between modal docs and actual
    bindings
  • Operators can override bindings via global_config.shortcut_overrides
  • A11y audit can flag missing shortcuts (e.g. "no shortcut for primary
    action")
"""
from __future__ import annotations


# Default catalog. Each entry: {keys, action, description, context}
CATALOG = [
    # Global
    {"keys": "?", "action": "show_shortcuts",
     "description": "Show keyboard shortcuts", "context": "global"},
    {"keys": "Ctrl+K", "action": "open_palette",
     "description": "Open command palette", "context": "global"},
    {"keys": "Ctrl+/", "action": "focus_search",
     "description": "Focus search bar", "context": "global"},
    {"keys": "Escape", "action": "dismiss_modal",
     "description": "Close any open modal/overlay", "context": "global"},
    # Queue control
    {"keys": "Space", "action": "toggle_pause",
     "description": "Pause/resume current site", "context": "queue"},
    {"keys": "Shift+Space", "action": "toggle_pause_all",
     "description": "Pause/resume all sites", "context": "queue"},
    {"keys": "G then S", "action": "goto_status",
     "description": "Jump to status panel", "context": "navigation"},
    {"keys": "G then H", "action": "goto_history",
     "description": "Jump to history", "context": "navigation"},
    {"keys": "G then E", "action": "goto_events",
     "description": "Jump to recent events", "context": "navigation"},
    {"keys": "G then C", "action": "goto_capacity",
     "description": "Jump to capacity dashboard", "context": "navigation"},
    # History
    {"keys": "J", "action": "next_row",
     "description": "Move down one row", "context": "history"},
    {"keys": "K", "action": "prev_row",
     "description": "Move up one row", "context": "history"},
    {"keys": "X", "action": "toggle_select",
     "description": "Toggle row selection", "context": "history"},
    {"keys": "Shift+R", "action": "retry_selected",
     "description": "Retry selected rows", "context": "history"},
    {"keys": "Shift+D", "action": "delete_selected",
     "description": "Delete selected rows", "context": "history"},
    {"keys": "Enter", "action": "open_row",
     "description": "Open selected row's details", "context": "history"},
    # Site
    {"keys": "T", "action": "take_over",
     "description": "Take over current site (manual control)",
     "context": "site"},
    {"keys": "Shift+L", "action": "relogin",
     "description": "Force re-login on current site", "context": "site"},
    # Utility
    {"keys": "Shift+R", "action": "reload_data",
     "description": "Reload data without full page refresh",
     "context": "global"},
    {"keys": "Shift+H", "action": "show_health",
     "description": "Show health checklist", "context": "global"},
    {"keys": "Shift+D", "action": "diagnostics_download",
     "description": "Download diagnostics bundle", "context": "global"},
]


def list_shortcuts(*, context: str = "") -> list:
    """All shortcuts, optionally filtered by context."""
    if not context:
        return list(CATALOG)
    return [c for c in CATALOG if c.get("context") == context]


def contexts() -> list:
    """Distinct contexts."""
    return sorted({c.get("context", "global") for c in CATALOG})


def merged_with_overrides() -> list:
    """Apply operator overrides from global_config."""
    try:
        from .global_config import get_config
        overrides = (get_config() or {}).get("shortcut_overrides") or {}
    except Exception:
        overrides = {}
    out = []
    for c in CATALOG:
        if c["action"] in overrides:
            out.append({**c, "keys": overrides[c["action"]],
                        "overridden": True,
                        "default_keys": c["keys"]})
        else:
            out.append(c)
    return out
