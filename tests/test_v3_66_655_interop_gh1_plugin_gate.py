"""INTEROP-GH-1 (v3.66.655): yt-dlp / gallery-dl external-plugin provenance gate.

The interop_registry keystone (GOV-1, 638) already defines the ``ytdlp_plugin`` and
``gallerydl_plugin`` kinds, but -- unlike ``chromium_extension`` (runner_browser) and
``jd_plugin`` (runner_integrations) -- nothing consulted the gate at plugin-load time.
This closes that gap at the subprocess cmd-build layer:

  * ``_permitted_plugin_dirs(kind, config)`` resolves configured plugin dirs and, when
    ``interop_governance_enabled`` is on, keeps only dirs the interop_registry permits
    (registered + risk-acknowledged + enabled + live content hash matches the pin).
    Governance OFF (toggle absent) -> configured dirs pass through unchanged. Empty
    config (the default for every existing site) -> no dirs -> ZERO behavior change.
  * ``_build_ytdlp_cmd``   appends ``--plugin-dirs DIR`` per permitted dir (yt-dlp).
  * ``_build_gallerydl_cmd`` appends ``-X DIR`` per permitted dir (gallery-dl).

Both flags are placed BEFORE the bare ``--`` terminator so a plugin dir can never be
smuggled into the positional/URL slot (mirrors the F-RUN01-02 discipline).

Real-CLI surfaces verified in-sandbox before writing: yt-dlp 2026.03.17 has
``--plugin-dirs``; gallery-dl 1.32.5 has ``-X``/``--extractors PATH``
(``action="append"``). Both take an appendable directory path.

Pure-builder tests need no subprocess and no registry file; the governance tests
monkeypatch ``interop_registry.is_permitted`` so the unit under test is the WIRING,
not the registry internals (already covered by the GOV-1 suite).
"""
from bulk_downloader import runner_extractors as rx


# --- pure builder: yt-dlp --plugin-dirs -------------------------------------

def test_ytdlp_cmd_appends_plugin_dirs_before_terminator():
    cmd = rx._build_ytdlp_cmd(
        ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
        plugin_dirs=("/opt/p1", "/opt/p2"))
    # each permitted dir threaded as its own --plugin-dirs occurrence
    assert cmd.count("--plugin-dirs") == 2
    assert "/opt/p1" in cmd and "/opt/p2" in cmd
    # every plugin flag must precede the '--' terminator
    term = cmd.index("--")
    for i, tok in enumerate(cmd):
        if tok == "--plugin-dirs":
            assert i < term, "plugin flag must come before the -- terminator"


def test_ytdlp_cmd_no_plugin_dirs_is_unchanged():
    base = rx._build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v")
    with_empty = rx._build_ytdlp_cmd(
        ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v", plugin_dirs=())
    assert base == with_empty
    assert "--plugin-dirs" not in base


# --- pure builder: gallery-dl -X --------------------------------------------

def test_gallerydl_cmd_appends_extractors_flag_before_terminator():
    cmd = rx._build_gallerydl_cmd(
        gallerydl="gallery-dl", dl_dir="/dl", url="https://ex/g",
        plugin_dirs=("/opt/gx",))
    assert cmd.count("-X") == 1
    assert "/opt/gx" in cmd
    term = cmd.index("--")
    assert cmd.index("-X") < term


def test_gallerydl_cmd_no_plugin_dirs_is_unchanged():
    base = rx._build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl", url="https://ex/g")
    with_empty = rx._build_gallerydl_cmd(
        gallerydl="gallery-dl", dl_dir="/dl", url="https://ex/g", plugin_dirs=())
    assert base == with_empty
    assert "-X" not in base


# --- governance gate: _permitted_plugin_dirs --------------------------------

def test_permitted_dirs_governance_off_passes_through():
    cfg = {"ytdlp_plugin_dirs": ["/a", "/b"]}  # governance toggle absent
    got = rx._permitted_plugin_dirs("ytdlp_plugin", cfg)
    assert list(got) == ["/a", "/b"]


def test_permitted_dirs_empty_config_is_empty():
    assert list(rx._permitted_plugin_dirs("ytdlp_plugin", {})) == []
    assert list(rx._permitted_plugin_dirs("gallerydl_plugin", {})) == []


def test_permitted_dirs_governance_on_filters_via_is_permitted(monkeypatch):
    from bulk_downloader import interop_registry as ir
    # only /keep is permitted; /drop is not
    monkeypatch.setattr(ir, "is_permitted",
                        lambda kind, item, live=None: item == "/keep")
    monkeypatch.setattr(ir, "dir_sha256", lambda d: "deadbeef")
    cfg = {"interop_governance_enabled": True,
           "gallerydl_plugin_dirs": ["/keep", "/drop"]}
    got = rx._permitted_plugin_dirs("gallerydl_plugin", cfg)
    assert list(got) == ["/keep"]


def test_permitted_dirs_governance_on_passes_kind_and_pin(monkeypatch):
    from bulk_downloader import interop_registry as ir
    seen = {}
    def fake_permit(kind, item, live=None):
        seen["kind"] = kind
        seen["live"] = live
        return True
    monkeypatch.setattr(ir, "is_permitted", fake_permit)
    monkeypatch.setattr(ir, "dir_sha256", lambda d: "PIN:" + d)
    rx._permitted_plugin_dirs("ytdlp_plugin",
                              {"interop_governance_enabled": True,
                               "ytdlp_plugin_dirs": ["/x"]})
    # the gate is consulted with the right kind and the live content pin
    assert seen["kind"] == "ytdlp_plugin"
    assert seen["live"] == "PIN:/x"
