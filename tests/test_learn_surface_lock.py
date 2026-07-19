"""test_learn_surface_lock.py -- attribute-surface guard for the learn -> learn_impl
package split (DECOMP-LEAF cut 5)."""
from bulk_downloader import learn as L

PUBLIC = {
    "install_recorder", "install_teach_overlay", "harvest_recordings",
    "synthesize_selectors", "classify_login", "classify_download", "merge_learned",
    "record_deep_detect_outcome", "deep_detect_site_memory", "record_post_reveal_decision",
    "record_auto_submit_decision", "record_pending_approvals", "pending_approvals",
    "make_provider_cache_writer",
}
# external code does `from .learn import RECORDER_JS` and `import learn as _learn`
EXTERNAL_CONSTS = {"RECORDER_JS", "TEACH_OVERLAY_JS"}


def test_public_surface_present():
    missing = (PUBLIC | EXTERNAL_CONSTS) - set(dir(L))
    assert not missing, f"learn shim dropped: {sorted(missing)}"


def test_js_assets_are_strings():
    # the injected-JS blobs must remain importable as module-level strings
    assert isinstance(L.RECORDER_JS, str) and len(L.RECORDER_JS) > 1000
    assert isinstance(L.TEACH_OVERLAY_JS, str) and len(L.TEACH_OVERLAY_JS) > 10000


def test_full_surface_reexported():
    priv = {
        "_css_escape_ident", "_css_escape_attr_value", "_looks_hashed", "_is_submit_shaped",
        "_which_url_attr", "_synthesize_download_row_selector", "_dd_init_block",
        "_dd_now_iso", "_dd_prune_dict", "_pending_why",
    }
    missing = priv - set(dir(L))
    assert not missing, f"learn shim dropped privates: {sorted(missing)}"


def test_each_submodule_imports():
    import importlib
    for mod in ("_assets", "selectors", "memory", "classify"):
        importlib.import_module(f"bulk_downloader.learn_impl.{mod}")
