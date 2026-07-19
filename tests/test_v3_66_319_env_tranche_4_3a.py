"""v3.66.319 — CLI->GUI parity Phase 4.3a: clean-flag env tranche (non-guard).

ONE genuine FULL promotion + TWO alias/seed DISPLAY-ONLY classifications.

FULL (1): BD_CROSS_SITE_SELECTORS — cross_site_selectors.enabled() gains a
store-first read (store > env seed > default), matching the @315 idiom. New
schema key `cross_site_selectors` (bool, safe_default False).

DISPLAY-ONLY (2):
  * BD_SESSION_KEEPER_USE_CLOAKBROWSER — the legacy *third* alias in cloak's
    _ENV_KEYS triple (BD_BROWSER_BACKEND, BD_USE_CLOAK, <this>). The first two
    are ALREADY gui_exposure=full; a separate control would be a redundant third
    toggle over the identical resolve_backend() resolution (BD_CAPTURE_RAW
    footgun). The live control is Browser Backend / Use Cloak.
  * BD_DOWNLOAD_DIR — deploy seed (edge_deploy k8s manifest gen only; no service
    reader). The live knob `download_dir` is ALREADY gui_exposure=full (site_key).

RED-first: every assertion fails on pristine v3.66.318 (no schema key, no store
read in enabled(), no manifest entries). Zero-arg; env + store restored in
try/finally; cwd restored. No __version__ pin here (bump is gated/separate).
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_MANIFEST = _REPO / "reports" / "config_gui_manifest.json"


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


class _Env:
    """Save/restore a set of env vars around a block."""
    def __init__(self, names):
        self.names = names

    def __enter__(self):
        self.saved = {n: os.environ.get(n) for n in self.names}
        for n in self.names:
            os.environ.pop(n, None)
        return self

    def __exit__(self, *a):
        for n, v in self.saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_cross_site_key():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert "cross_site_selectors" in s, "missing schema key cross_site_selectors"
    assert s["cross_site_selectors"]["type"] is bool
    assert s["cross_site_selectors"].get("safe_default") is False


# ── full read site: store > env seed > default ───────────────────────────────
def test_cross_site_store_over_env():
    import bulk_downloader.cross_site_selectors as CS
    importlib.reload(CS)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_CROSS_SITE_SELECTORS"]):
            # store True, env unset -> enabled (store honored)
            _fresh_store({"cross_site_selectors": True})
            assert CS.enabled() is True
            # store True wins over env "0"
            os.environ["BD_CROSS_SITE_SELECTORS"] = "0"
            _fresh_store({"cross_site_selectors": True})
            assert CS.enabled() is True
            # store unset, env seed "1" -> enabled
            _fresh_store({})
            os.environ["BD_CROSS_SITE_SELECTORS"] = "1"
            assert CS.enabled() is True
            # store False wins over env "1"
            _fresh_store({"cross_site_selectors": False})
            assert CS.enabled() is False
            # nothing set -> default False
            os.environ.pop("BD_CROSS_SITE_SELECTORS", None)
            _fresh_store({})
            assert CS.enabled() is False
    finally:
        os.chdir(cwd)


# ── manifest: 1 full + 2 display-only ─────────────────────────────────────────
def test_manifest_4_3a_entries():
    m = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    exposed = m["exposed"]
    assert exposed.get("BD_CROSS_SITE_SELECTORS") == "full"
    # v3.66.506 (Bucket 3a): BD_SESSION_KEEPER_USE_CLOAKBROWSER was promoted from
    # display-only alias to a first-class full key (declared in GLOBAL_CONFIG_SCHEMA,
    # dropped from _ALIAS_OF_FULL). It was display-only here at 319.
    assert exposed.get("BD_SESSION_KEEPER_USE_CLOAKBROWSER") == "full"
    assert exposed.get("BD_DOWNLOAD_DIR") == "display-only"


# ── guard: the two aliases' canonical keys are already full (the reason they ──
#    are display-only, not redundant new controls) ─────────────────────────────
def test_alias_canonicals_already_full():
    m = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    exposed = m["exposed"]
    # backend-selection canonical env vars already full
    assert exposed.get("BD_BROWSER_BACKEND") == "full"
    assert exposed.get("BD_USE_CLOAK") == "full"
    # download dir canonical key already full
    assert exposed.get("download_dir") == "full"


# ── scanner reclassification: BD_DOWNLOAD_DIR seed targets display-only ───────
#    (runtime_tunable False), so display-only actually CLOSES it in the ratchet.
#    v3.66.506: BD_SESSION_KEEPER_USE_CLOAKBROWSER was promoted to a first-class
#    full key (runtime_tunable True), so it is no longer in the display-only set.
def test_aliases_target_display_only():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in ("BD_DOWNLOAD_DIR",):
        it = items[k]
        assert it["runtime_tunable"] is False, f"{k} should be non-runtime-tunable"
        assert it["parity_target"] == "display-only", k
        assert it["gui_exposure"] == "display-only", k
    # v3.66.506: the keeper alias is now a full, runtime-tunable control.
    keeper = items["BD_SESSION_KEEPER_USE_CLOAKBROWSER"]
    assert keeper["runtime_tunable"] is True, "keeper alias should be runtime-tunable @506"
    assert keeper["gui_exposure"] == "full", keeper["gui_exposure"]
    # the real promotion stays full
    cs = items["BD_CROSS_SITE_SELECTORS"]
    assert cs["runtime_tunable"] is True and cs["gui_exposure"] == "full"


def test_open_count_dropped_by_three():
    """4.3a closes exactly its 3 keys. Asserted as a DELTA (keys absent from the
    open set), not an absolute count == N — an equality pin re-breaks on every
    later tranche (the ratchet-pin discipline)."""
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    openset = set(csi._open_settings(d["items"]))
    for k in ("BD_CROSS_SITE_SELECTORS",
              "BD_SESSION_KEEPER_USE_CLOAKBROWSER", "BD_DOWNLOAD_DIR"):
        assert k not in openset, f"{k} should be closed (full or display-only)"
