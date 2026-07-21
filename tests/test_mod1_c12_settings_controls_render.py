"""MOD-1 Cut (v3.66.811): close the section-0 gate hole -- a settings key can read
gui_exposure="full" with NO control rendered.

config_surface_inventory derives gui_exposure="full" for a global_config key when
the key STRING appears in any frontend/src/*.ts* file. settingsSchema.ts (a
field->section map for the palette + change-markers) satisfies that match on its
own -- so a key added there reads "full" even though Settings.tsx renders global
controls from EXPLICIT hand-written JSX and no control exists. That is exactly how
v3.66.808/809 shipped captcha_vnc_display / captcha_vnc_websocket_port /
netns_isolation as "full" while the operator saw no toggle (a browser render + a
stash report both confirmed refs_in_Settings.tsx=0).

This gate re-derives the SUBJECT: every key in the settings section-map must have
an EXPLICIT control in Settings.tsx (or Advanced.tsx) -- a `draft.<key>` read or a
`setField("<key>", ...)` write. A string in settingsSchema alone no longer counts.

RED-first: on pristine v3.66.810 four keys fail (the three above +
automation.disco_enabled, a pre-existing omission from the Automation toggle list).
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_FE = _REPO / "frontend" / "src"


def _settings_keys() -> set[str]:
    sch = (_FE / "lib" / "settingsSchema.ts").read_text(encoding="utf-8")
    keys = set(re.findall(r'^\s*([a-z][a-zA-Z0-9_.]+):\s*\{\s*section:', sch, re.M))
    keys |= set(re.findall(r'^\s*"([a-zA-Z0-9_.]+)":\s*\{\s*section:', sch, re.M))
    return keys


def _render_blob() -> str:
    blob = (_FE / "routes" / "Settings.tsx").read_text(encoding="utf-8")
    adv = _FE / "routes" / "Advanced.tsx"
    if adv.is_file():
        blob += adv.read_text(encoding="utf-8")
    return blob


def _has_control(key: str, blob: str) -> bool:
    """An explicit control reads or writes the key by name. The `[k]` forms cover
    the data-driven toggle lists that .map() over `["automation.x", ...] as const`
    and render `draft[k]` / `setField(k, ...)` -- there the literal key appears in
    an array element and `draft[k]`/`setField(k` appear generically."""
    if f'draft.{key}' in blob or f'setField("{key}"' in blob:
        return True
    if f'draft["{key}"]' in blob or f"draft['{key}']" in blob:
        return True
    # data-driven list: the key appears as a quoted array element AND the loop
    # body renders draft[k]/setField(k,...). Require BOTH so a bare mention (a
    # comment, the type) doesn't count.
    if f'"{key}"' in blob and "draft[k]" in blob and "setField(k" in blob:
        return True
    return False


def test_every_settings_key_has_an_explicit_control():
    blob = _render_blob()
    missing = sorted(k for k in _settings_keys() if not _has_control(k, blob))
    assert not missing, (
        "settingsSchema keys with gui_exposure=full but NO explicit Settings.tsx "
        "control (they cannot be set from the UI): %s" % missing)


def test_the_three_arch_b_knobs_specifically_render():
    """Belt-and-braces on the exact keys v3.66.808/809 declared -- so a future
    settingsSchema edit can't silently drop their controls again."""
    blob = _render_blob()
    for k in ("captcha_vnc_display", "captcha_vnc_websocket_port", "netns_isolation"):
        assert _has_control(k, blob), f"{k} has no explicit control in Settings.tsx"
