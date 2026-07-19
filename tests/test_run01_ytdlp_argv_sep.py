"""F-RUN01-02 (RUN-01, HIGH): the yt-dlp fallback argv builder must terminate its
options with a bare ``--`` before the positional URL.

Without the terminator, a URL beginning with ``-`` is parsed by yt-dlp as an
option rather than a target (witness run01_witnesses.py::F-RUN01-02 confirmed
live: url=``--version`` was consumed as the ``--version`` flag). A legitimate URL
can never start with ``-``, so inserting ``--`` immediately before the URL is the
standard, zero-cost fix and forces yt-dlp to treat everything after it as
positional -- neutralising option-smuggling into yt-dlp's own flag surface
(e.g. ``--exec``).

These are pure/structural unit tests on
``bulk_downloader.runner_extractors._build_ytdlp_cmd`` (no yt-dlp binary, no
network); the live-yt-dlp behavioural proof lives in the audit witness.
"""
from bulk_downloader.runner_extractors import _build_ytdlp_cmd


def test_build_ytdlp_cmd_has_double_dash_before_url():
    # A URL that is byte-for-byte a yt-dlp option. On the unfixed builder this
    # slot is parsed as the --version flag (witness-confirmed).
    url = "--version"
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/tmp/x", url=url)
    # The URL must remain the final positional argument ...
    assert cmd[-1] == url, "url must remain the final argv element"
    # ... immediately preceded by a bare '--' options-terminator.
    assert cmd[-2] == "--", (
        "a bare '--' must precede the url so yt-dlp treats it as a positional "
        "target, never an option (F-RUN01-02)"
    )


def test_double_dash_terminator_survives_optional_args():
    # The terminator must still be the last thing before the URL when optional
    # args (proxy, min_res filter) are also present, and must not be duplicated.
    url = "-oPWNED"
    cmd = _build_ytdlp_cmd(
        ytdlp="yt-dlp", dl_dir="/tmp/x", url=url,
        proxy_url="socks5://127.0.0.1:1080", min_res=720,
    )
    assert cmd[-1] == url, "url must remain the final argv element"
    assert cmd[-2] == "--", "terminator must sit immediately before the url"
    assert cmd.count("--") == 1, "exactly one bare '--' options-terminator expected"


def test_terminator_neutralizes_dangerous_exec_style_url():
    # The dangerous case the finding calls out: an --exec-style option must land
    # AFTER the terminator (as a positional), never be parsed as a flag.
    url = "--exec=touch /tmp/pwned"
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/tmp/x", url=url)
    assert "--" in cmd, "options terminator '--' must be present"
    term = cmd.index("--")
    assert term == len(cmd) - 2 and cmd[term + 1] == url, (
        "the exec-style url must sit immediately after the '--' terminator"
    )
