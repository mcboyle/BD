"""v3.66.703 -- MOD-4 (Batch C / M4): TS-safe ffmpeg binary pin.

SEVEN modules each resolved ffmpeg INDEPENDENTLY via bare `shutil.which("ffmpeg")`
-- dedup, enrichment, healthcheck, hls_downloader, live_recorder, thumbnail_gen,
thumbnail_sheets -- with NO central resolver and NO way to say WHICH ffmpeg to use.

That is not academic. The project's own footgun list records it: the static ffmpeg
build (johnvansickle 7.0.2) SEGFAULTS on HLS+HTTPS, and the distro build must be
used instead. `healthcheck._ffmpeg_capability` already PROBES for exactly this class
(mpegts muxer + https protocol). But probing tells you the binary on PATH is bad --
it gives you no way to point BD at the good one. Today, seven call sites just take
whatever `which` returns.

This cut adds the missing half: ONE resolver (`ffmpeg_bin`) honouring a single
`ffmpeg_path` pin, with `which()` as the fallback -- so with no pin set, behaviour is
BYTE-IDENTICAL to today.

The pin is a GLOBAL_CONFIG key, NOT a per-site CFG_FIELDS key. Verified empirically:
adding a global_config key does NOT move the config-surface inventory (178 -> 178),
so it does not trip the count-coupled settings-center pins that took 702 RED on
stash. It is also the semantically right home: which ffmpeg binary the HOST runs is
a deployment concern, not a per-site one.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# EIGHT, not seven. The initial scope said 7 -- these tests then caught two more
# resolution sites: a SECOND ffprobe resolver inside thumbnail_gen, and integrity.py,
# which resolved ffprobe at MODULE IMPORT time (so a pin set later could never have
# reached it at all). A pin with a hole in it is worse than no pin: the missed site
# keeps using the PATH binary while everything else honours the pin -- exactly the
# mixed-build inconsistency this cut exists to remove.
CONSUMERS = ["dedup", "enrichment", "healthcheck", "hls_downloader",
             "live_recorder", "thumbnail_gen", "thumbnail_sheets", "integrity"]


# ── A. the resolver exists and has the right precedence ──────────────────
def test_module_exists():
    from bulk_downloader import ffmpeg_bin          # noqa: F401


def test_no_pin_falls_back_to_which(monkeypatch):
    """With NO pin set, resolution must be exactly what it is today -- so every
    existing deployment's behaviour is unchanged."""
    from bulk_downloader import ffmpeg_bin
    ffmpeg_bin.reset()
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: "")
    assert ffmpeg_bin.ffmpeg() == (shutil.which("ffmpeg") or None)


def test_pin_wins_over_path(monkeypatch, tmp_path):
    """The whole point: a pinned binary must OVERRIDE whatever is on PATH."""
    from bulk_downloader import ffmpeg_bin
    ffmpeg_bin.reset()
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(tmp_path))
    assert ffmpeg_bin.ffmpeg() == str(fake)


def test_ffprobe_comes_from_the_same_build(monkeypatch, tmp_path):
    """ffprobe must be taken from the PINNED build, not mixed with a PATH ffprobe --
    mixing an ffmpeg from one build with an ffprobe from another is exactly the
    inconsistency the pin exists to remove."""
    from bulk_downloader import ffmpeg_bin
    ffmpeg_bin.reset()
    for n in ("ffmpeg", "ffprobe"):
        p = tmp_path / n
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(tmp_path))
    assert ffmpeg_bin.ffprobe() == str(tmp_path / "ffprobe")


def test_missing_pin_target_degrades_to_which(monkeypatch, tmp_path):
    """A pin pointing at nothing must NOT hard-fail the app -- it degrades to the
    old behaviour (fail-open on RESOLUTION; the capability probe is what fails
    closed on a bad binary)."""
    from bulk_downloader import ffmpeg_bin
    ffmpeg_bin.reset()
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(tmp_path / "nope"))
    assert ffmpeg_bin.ffmpeg() == (shutil.which("ffmpeg") or None)


def test_available_reflects_resolution(monkeypatch):
    from bulk_downloader import ffmpeg_bin
    ffmpeg_bin.reset()
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: None)
    assert ffmpeg_bin.available() is False


# ── B. the pin is a declared GLOBAL config key ───────────────────────────
def test_pin_is_a_global_config_key():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA
    assert "ffmpeg_path" in GLOBAL_CONFIG_SCHEMA
    spec = GLOBAL_CONFIG_SCHEMA["ffmpeg_path"]
    assert spec["type"] is str
    assert spec.get("safe_default") == "", "default must be EMPTY = unchanged behaviour"


def test_pin_is_not_a_bd_env_literal():
    """700's lesson: any BD_* token literal in a shipped .py trips the env-tranche
    gate. The pin is config, not env."""
    src = (ROOT / "bulk_downloader" / "ffmpeg_bin.py").read_text(encoding="utf-8")
    assert "BD_FFMPEG" not in src


# ── C. every consumer now goes through the ONE resolver ──────────────────
@pytest.mark.parametrize("mod", CONSUMERS)
def test_consumer_uses_the_resolver(mod):
    """The point of the cut: no module may resolve ffmpeg on its own any more --
    otherwise the pin is silently ignored by whichever site was missed."""
    src = (ROOT / "bulk_downloader" / f"{mod}.py").read_text(encoding="utf-8")
    assert "ffmpeg_bin" in src, f"{mod} must resolve ffmpeg through ffmpeg_bin"


@pytest.mark.parametrize("mod", CONSUMERS)
def test_consumer_no_longer_calls_which_ffmpeg_directly(mod):
    """A leftover bare which() is a HOLE in the pin: that call site would keep using
    the PATH binary while everything else honours the pin -- the mixed-build
    inconsistency this cut exists to remove."""
    src = (ROOT / "bulk_downloader" / f"{mod}.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines()
                      if not l.lstrip().startswith("#"))
    assert 'which("ffmpeg")' not in body, (
        f"{mod} still resolves ffmpeg directly -- the pin would not reach it")
    # integrity keeps a which() FALLBACK inside its resolver helper (fail-open by
    # contract); what must not exist is a resolution that BYPASSES ffmpeg_bin.
    if mod != "integrity":
        assert 'which("ffprobe")' not in body, (
            f"{mod} still resolves ffprobe directly -- builds could be mixed")
