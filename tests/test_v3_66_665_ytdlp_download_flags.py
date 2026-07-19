"""Cut 665 — download-engine flags threaded into the yt-dlp fallback builder.

FEAT-MAX 2.2 (segment-parallel HLS/DASH) + 2.5 (per-download bandwidth cap):
``_build_ytdlp_cmd`` gains two config-driven, inert-by-default flags:

  * ``--concurrent-fragments N``  (config key ``ytdlp_concurrent_fragments``)
      threaded ONLY when N > 1 (yt-dlp's default is 1; N<=1 -> omit so the cmd
      stays byte-identical for unconfigured sites).
  * ``--limit-rate <rate>``       (config key ``download_rate_limit``)
      a yt-dlp rate string (e.g. "2M", "500K", "1048576"). Threaded ONLY when
      the value matches a strict numeric[+K/M/G] pattern; anything else (empty,
      a flag-looking string, shell metacharacters) is dropped -> unlimited,
      byte-identical prior cmd. This keeps a config value from ever smuggling a
      separate flag into yt-dlp's option surface (mirrors F-RUN01-02 discipline).

Both live in the same pure builder and are driven off the same config surface,
so they form one coherent cut. These are pure/structural unit tests (no yt-dlp
binary, no network). Registration of the two keys in CFG_FIELDS/DEFAULTS is also
asserted so the operator can set them through the normal config API (the save
path rejects any key not in CFG_FIELDS).
"""
from bulk_downloader.runner_extractors import _build_ytdlp_cmd


def _base():
    return _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v")


# ── 2.2 --concurrent-fragments ───────────────────────────────────────

def test_concurrent_fragments_threaded_as_pair_when_gt_1():
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
                           concurrent_fragments=4)
    assert "--concurrent-fragments" in cmd
    i = cmd.index("--concurrent-fragments")
    assert cmd[i + 1] == "4", "N must follow the flag as a string argv element"


def test_concurrent_fragments_omitted_when_unset_or_1():
    # Default (0) and an explicit 1 both omit the flag -> byte-identical base cmd.
    assert "--concurrent-fragments" not in _base()
    assert "--concurrent-fragments" not in _build_ytdlp_cmd(
        ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v", concurrent_fragments=1)
    assert _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
                            concurrent_fragments=0) == _base()


# ── 2.5 --limit-rate ─────────────────────────────────────────────────

def test_rate_limit_threaded_as_pair_when_valid():
    for rate in ("2M", "500K", "1048576", "4.2M"):
        cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
                               rate_limit=rate)
        assert "--limit-rate" in cmd, rate
        i = cmd.index("--limit-rate")
        assert cmd[i + 1] == rate, (rate, cmd)


def test_rate_limit_omitted_when_empty():
    assert "--limit-rate" not in _base()
    assert _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
                            rate_limit="") == _base()


def test_rate_limit_rejects_unsafe_values():
    # A config value that isn't a clean rate string must be dropped, never
    # threaded -- so it can't smuggle a flag or shell metacharacters.
    for bad in ("-oPWNED", "--exec=touch /tmp/x", "2M; rm -rf /", "abc", "$(id)",
                "-2M", " "):
        cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url="https://ex/v",
                               rate_limit=bad)
        assert "--limit-rate" not in cmd, f"unsafe rate {bad!r} must be dropped"


# ── ordering / terminator safety ─────────────────────────────────────

def test_new_flags_precede_the_double_dash_terminator():
    url = "https://ex/v"
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/dl", url=url,
                           concurrent_fragments=8, rate_limit="1M")
    assert cmd[-1] == url and cmd[-2] == "--", "url stays last, terminator before it"
    term = cmd.index("--")
    assert cmd.index("--concurrent-fragments") < term
    assert cmd.index("--limit-rate") < term
    assert cmd.count("--") == 1, "exactly one bare terminator"


# ── registration so the keys are operator-settable via the config API ─

def test_new_config_keys_registered_in_cfg_fields_and_defaults():
    from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
    for k in ("ytdlp_concurrent_fragments", "download_rate_limit"):
        assert k in CFG_FIELDS, f"{k} must be in CFG_FIELDS (else config-save drops it)"
        assert k in DEFAULTS, f"{k} must have a default"
    assert DEFAULTS["ytdlp_concurrent_fragments"] == 0, "inert default (yt-dlp uses 1)"
    assert DEFAULTS["download_rate_limit"] == "", "inert default (unlimited)"
