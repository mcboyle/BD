"""v3.66.747 — exec-bridge option injection (audit round 2, finding R14).

tool_bridge is a strong control -- no shell, resolved argv0, path containment,
minimal env, hard timeout. One gap, and the audit found it: a POSITIONAL whose
VALUE looks like an option passes straight through, because build_argv appends
positionals with no `--` end-of-options separator and the `url` str-positional
has no leading-dash / scheme guard.

  build_argv("yt-dlp", {"url": "--evil"})  ->  ['/usr/local/bin/yt-dlp', '--evil']

Not shell injection (the no-shell defense holds) -- OPTION injection into the
tool itself. yt-dlp options can write files, run post-processors, read option
files. The HTTP surface is CSRF-gated and same-origin, so this is
defense-in-depth, not a remote hole -- but the whole premise of the bridge is
"paranoid, centrally," and a flag-shaped positional value defeats that premise.

The existing suite (test_v3_66_717) covers shell metachars, traversal,
unallowlisted tool, unallowlisted FLAG KEY, csrf, timeout -- but has no case
for a positional whose VALUE is flag-shaped. The denominator was "unknown flag
keys"; the gap is "known positional values." This file is that case.

Two guards, tested independently so a regression names which one broke:
  1. `--` end-of-options separator before positionals in build_argv.
  2. A leading-dash reject on positional str values (+ a scheme allowlist for
     the url positional): a value starting with '-' is never a positional.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_build_argv_inserts_end_of_options_separator():
    """The `--` guard, in isolation. Even if a flag-shaped value somehow
    validated, `--` guarantees the tool treats everything after it as
    positional operands, never options."""
    from bulk_downloader import tool_bridge

    # a tool with a str positional
    tool = "yt-dlp"
    if tool not in tool_bridge.ALLOWLIST:
        return  # allowlist is environment-derived; vacuous if the tool is absent

    # use a benign, well-formed positional so validation passes and we can see
    # the argv shape
    argv = tool_bridge.build_argv(tool, {"url": "https://example.com/v"})
    assert "--" in argv, (
        "build_argv must insert a `--` end-of-options separator before "
        "positionals; without it a flag-shaped positional value is parsed as "
        "an option by the tool"
    )
    # everything after `--` is a positional operand
    i = argv.index("--")
    assert argv[i + 1:] == ["https://example.com/v"]


def test_leading_dash_positional_value_is_refused():
    """The validation guard. A positional value that begins with '-' is an
    option-shaped value and must be REJECTED at validation (400), not rendered
    into argv at all."""
    from bulk_downloader import tool_bridge

    tool = "yt-dlp"
    if tool not in tool_bridge.ALLOWLIST:
        return

    for evil in ("--evil-injected", "--help", "-x", "--exec=rm -rf /"):
        try:
            tool_bridge.build_argv(tool, {"url": evil})
            raise AssertionError(
                f"a leading-dash positional value {evil!r} was accepted — it "
                "can reach the tool as an option"
            )
        except tool_bridge.BridgeError:
            pass  # correct: refused at validation


def test_url_positional_requires_an_http_scheme():
    """The url positional is a URL, not free text. A value with no http(s)
    scheme is refused -- mirroring the rigor the `path` type already has."""
    from bulk_downloader import tool_bridge

    tool = "yt-dlp"
    if tool not in tool_bridge.ALLOWLIST:
        return

    for bad in ("file:///etc/passwd", "ftp://x/y", "not-a-url", "javascript:x"):
        try:
            tool_bridge.build_argv(tool, {"url": bad})
            raise AssertionError(
                f"non-http url {bad!r} was accepted for the url positional"
            )
        except tool_bridge.BridgeError:
            pass

    # a normal http(s) URL still works
    argv = tool_bridge.build_argv(tool, {"url": "https://example.com/video"})
    assert "https://example.com/video" in argv
    argv2 = tool_bridge.build_argv(tool, {"url": "http://example.com/video"})
    assert "http://example.com/video" in argv2


def test_a_flag_shaped_value_does_not_reach_the_tool_end_to_end():
    """The whole point, through build_argv: a --version value in the url field
    must not produce an argv where the tool sees --version as an option."""
    from bulk_downloader import tool_bridge

    tool = "yt-dlp"
    if tool not in tool_bridge.ALLOWLIST:
        return

    try:
        argv = tool_bridge.build_argv(tool, {"url": "--version"})
    except tool_bridge.BridgeError:
        return  # refused at validation -- the strongest outcome

    # if it did not raise, `--version` MUST be behind the `--` separator so the
    # tool cannot interpret it as an option
    assert "--" in argv
    assert argv.index("--") < argv.index("--version")
