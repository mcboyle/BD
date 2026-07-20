"""MOD-1 C-8 (RED-first): the KASM-T10 fingerprint-delta logic.

The measurement's VALUE is its honesty about what a GPU-less run cannot see
(CLAUDE.md 0/1): the WebGL renderer -- the property most likely to flag live-X as
datacenter hardware -- is software-rendered in BOTH modes without a GPU, so the
delta collapses and the run understates the real-hardware magnitude. The verdict
must SAY so, not report the floor as the answer.

The browser launch is verified live out of band; these unit tests pin the pure
diff / verdict / gpu-detection logic on synthetic samples.

RED on pristine @805: tools/kasm_fingerprint_probe.py does not exist.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import kasm_fingerprint_probe as fp  # noqa: E402


# software renderer (sandbox) vs a real GPU (box)
_HEADFUL_GPULESS = {
    "webgl_renderer": "ANGLE (Google, Vulkan (SwiftShader Device), SwiftShader driver)",
    "webgl_vendor": "Google Inc. (Google)",
    "webdriver": True,
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/141.0.0.0 Safari/537.36",
    "hardware_concurrency": 4,
}
_HEADLESS_GPULESS = {
    "webgl_renderer": "ANGLE (Google, Vulkan (SwiftShader Device), SwiftShader driver)",
    "webgl_vendor": "Google Inc. (Google)",
    "webdriver": True,
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/141.0.0.0 Safari/537.36",
    "hardware_concurrency": 4,
}


def test_is_gpu_less_detects_software_renderers():
    assert fp.is_gpu_less({"webgl_renderer": "SwiftShader driver"}) is True
    assert fp.is_gpu_less({"webgl_renderer": "ANGLE ... llvmpipe"}) is True
    assert fp.is_gpu_less({"webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060)"}) is False
    assert fp.is_gpu_less({}) is True  # no renderer -> cannot claim a GPU is present


def test_diff_flags_gpu_dependent_and_tells():
    diff = fp.diff_fingerprints(_HEADFUL_GPULESS, _HEADLESS_GPULESS)
    by = {d["key"]: d for d in diff}
    # the only real delta on GPU-less HW is the UA token
    assert by["user_agent"]["differs"] is True and by["user_agent"]["known_tell"] is True
    assert by["webgl_renderer"]["differs"] is False and by["webgl_renderer"]["gpu_dependent"] is True
    assert by["webdriver"]["differs"] is False and by["webdriver"]["known_tell"] is True


def test_verdict_gpu_less_understates_and_says_so():
    diff = fp.diff_fingerprints(_HEADFUL_GPULESS, _HEADLESS_GPULESS)
    v = fp.verdict(diff, _HEADFUL_GPULESS, _HEADLESS_GPULESS)
    assert v["gpu_less_run"] is True
    assert "understates" in v["note"]                       # honest about the floor
    assert v["gpu_dependent_delta_observable"] is False     # can't see the renderer delta
    assert v["webdriver_true_in_both"] is True              # Arch B does NOT clear webdriver
    assert v["user_agent_fixed_by_headful"] is True         # but it does fix the UA token
    assert v["differing_keys"] == ["user_agent"]


def test_verdict_real_gpu_run_reports_the_renderer_delta():
    headful = {"webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11)",
               "webdriver": True, "user_agent": "Chrome/141"}
    headless = {"webgl_renderer": "ANGLE (Google, SwiftShader)",   # headless masks the GPU
                "webdriver": True, "user_agent": "HeadlessChrome/141"}
    diff = fp.diff_fingerprints(headful, headless)
    v = fp.verdict(diff, headful, headless)
    assert v["gpu_less_run"] is False                        # headful has a real GPU
    assert v["gpu_dependent_delta_observable"] is True       # the renderer delta IS the signal
    assert "webgl_renderer" in v["differing_keys"]
    assert "material" in v["note"]


def test_normalize_folds_canvas_raw_into_a_stable_hash():
    a = fp.normalize_sample({"webdriver": False, "_canvas_raw": "data:image/png;base64,AAAA"})
    b = fp.normalize_sample({"webdriver": False, "_canvas_raw": "data:image/png;base64,AAAA"})
    c = fp.normalize_sample({"webdriver": False, "_canvas_raw": "data:image/png;base64,BBBB"})
    assert "_canvas_raw" not in a and len(a["canvas_hash"]) == 16
    assert a["canvas_hash"] == b["canvas_hash"] and a["canvas_hash"] != c["canvas_hash"]
