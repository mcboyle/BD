"""IA-10 -- the library-extractor registry names no module that no distribution ships.

The redtube entry imported "eaf_base_api". The distribution eaf-base-api installs
the module `base_api`, and nothing installs a module called eaf_base_api, so
_try_import always returned None: the redtube fast-path could never load and
every redtube URL routed to a dead library entry ("pip install eaf_base_api",
which is already installed).

The replacement on the index, redtube_api 1.1, is async-only (Client.get_video is
a coroutine, the stream URL is an awaited m3u8_base_url or an mp4_url property),
so it does not fit _generic_eaf_adapter's synchronous shape either. redtube is
served by the Aylo free-tube path (extractors_aylo.AYLO_FREE_TUBES, tpl95-redtube-1),
so the dead entry is dropped rather than rebuilt.
"""

from __future__ import annotations

from bulk_downloader import extractors, extractors_aylo

BD_GATE_SCOPE = "module"

# eaf-base-api's import name is base_api; this name is the distribution's, not a module.
_UNSHIPPED_MODULES = {"eaf_base_api"}


def test_no_registry_entry_imports_the_base_library_by_its_dist_name():
    bad = {sid: lib for sid, (lib, _re, _ad) in extractors._REGISTRY.items()
           if lib in _UNSHIPPED_MODULES}
    assert bad == {}, (
        f"IA-10: registry entries import a module no distribution ships: {bad} "
        "(eaf-base-api installs `base_api`); the fast-path can never load"
    )


def test_redtube_is_not_routed_to_a_dead_library_and_stays_on_the_aylo_path():
    url = "https://www.redtube.com/191397851"
    assert extractors.is_supported_url(url) is None, (
        "IA-10: redtube still routes to a library entry that cannot import"
    )
    # The site is not orphaned: the Aylo free-tube path still claims it.
    assert extractors_aylo.is_free_tube_url(url) is True


def test_positive_control_other_library_sites_still_route():
    # The routing probe can still say yes, so the None above is not a dead probe.
    assert extractors.is_supported_url("https://www.youporn.com/watch/1/") == "youporn"
    assert extractors.is_supported_url("https://www.eporner.com/video-x/") == "eporner"
