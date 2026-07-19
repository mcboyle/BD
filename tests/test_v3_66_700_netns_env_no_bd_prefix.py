"""700 (v3.66.700) -- regression guard: the netns shim's env vars must never
carry a ``BD_`` prefix.

699 shipped the Phase-2 browser shim using ``BD_NETNS`` / ``BD_BROWSER`` and went
RED on stash against TWO gates:

    test_v3_66_319_env_tranche_4_3d :: open env_vars remain: ['BD_NETNS','BD_BROWSER']
    test_v3_66_305_config_danger    :: open_runtime_tunable count bumped

ROOT CAUSE (a correction to the received "env-tranche" lore): tools/
config_surface_inventory.py does NOT only scan ``os.environ.get("BD_...")`` read
sites -- it ALSO does a bare token-boundary scan for any ``BD_[A-Z0-9_]+``
literal anywhere in a .py file (line ~126). So a BD_* name inside a *string*
(the shim script body) or as a *dict key* is registered as an operator-tunable
env var, even though nothing ever reads it via os.environ.

These two names are an internal BD<->shim calling convention (closer to argv
than to configuration) and are never operator-tunable, so the fix is to drop the
BD_ prefix entirely rather than classify them deploy-only. This test freezes
that: no BD_* token may appear in netns_isolation.py.
"""
import re
from pathlib import Path

_SRC = (Path(__file__).resolve().parent.parent
        / "bulk_downloader" / "netns_isolation.py")

_BD_TOKEN = re.compile(r"(?<![A-Za-z0-9_])BD_[A-Z0-9_]+")


def test_no_bd_prefixed_env_tokens_in_netns_isolation():
    hits = _BD_TOKEN.findall(_SRC.read_text(encoding="utf-8"))
    assert hits == [], (
        "BD_* token(s) in netns_isolation.py will be scanned as operator-tunable "
        f"env vars and trip the env-tranche + danger gates: {sorted(set(hits))}")


def test_shim_uses_the_unprefixed_names():
    from bulk_downloader import netns_isolation as ni
    src = ni.netns_browser_shim()
    assert "$NETNS_NS" in src and "$NETNS_BROWSER_BIN" in src
    env = ni.browser_launch_env("bd_cap_x", "/opt/chrome/chrome")
    assert set(env) == {"NETNS_NS", "NETNS_BROWSER_BIN"}
