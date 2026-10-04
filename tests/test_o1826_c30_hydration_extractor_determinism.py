"""O1826 BRIEF-30: hydration extractor key priority and devalue walk are deterministic.

M068: key priority looped over set literals, so a node carrying both ``title`` and
``name`` (or ``url`` and ``href``) picked a different value per PYTHONHASHSEED.
M069: ``_walk`` kept no visited set and ``_resolve`` treated bools as devalue
references, so shared/cyclic devalue nodes were re-walked exponentially.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import FrameType
from typing import Any

from bulk_downloader import hydration_extractor
from bulk_downloader.hydration_extractor import HydrationExtractor

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]

_PICK_PROBE = r"""
import json
from bulk_downloader.hydration_extractor import HydrationExtractor

node = {
    "headline": "Headline Key Value",
    "name": "Name Key Value",
    "release_title": "Release Title Key Value",
    "scenetitle": "Scenetitle Key Value",
    "title": "Title Key Value",
    "overview": "Overview Key Value",
    "desc": "Desc Key Value",
    "summary": "Summary Key Value",
    "description": "Description Key Value",
    "length": 11,
    "runtime": 22,
    "duration_seconds": 33,
    "duration": 44,
    "cover": "https://img.example.test/cover.jpg",
    "image": "https://img.example.test/image.jpg",
    "poster": "https://img.example.test/poster.jpg",
    "thumbnail": "https://img.example.test/thumbnail.jpg",
    "link": "https://cdn.example.test/link.mp4",
    "href": "https://cdn.example.test/href.mp4",
    "src": "https://cdn.example.test/src.mp4",
    "url": "https://cdn.example.test/url.mp4",
}
html = '<script id="__NEXT_DATA__">' + json.dumps({"props": node}) + "</script>"
meta = HydrationExtractor().extract(html)
print(json.dumps([meta.title, meta.description, meta.duration, meta.thumbnail_url, meta.stream_urls]))
"""

_EXPECTED_PICK = [
    "Title Key Value",
    "Description Key Value",
    44.0,
    "https://img.example.test/thumbnail.jpg",
    ["https://cdn.example.test/url.mp4"],
]

_SELF_REF_PROBE = r"""
import json
from bulk_downloader.hydration_extractor import HydrationExtractor

payload = [[0] * 8, {"title": "Self Reference Title"}]
html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
meta = HydrationExtractor().extract(html)
print(json.dumps([meta.framework, meta.title]))
"""


def _run_probe(
    code: str, seed: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_key_priority_is_stable_across_hash_seeds() -> None:
    picks = {}
    for seed in [str(n) for n in range(12)]:
        proc = _run_probe(_PICK_PROBE, seed, timeout=60)
        assert proc.returncode == 0, proc.stderr
        picks[seed] = json.loads(proc.stdout)
    unstable = {seed: pick for seed, pick in picks.items() if pick != _EXPECTED_PICK}
    assert not unstable, (
        f"O1826-C30 M068 key priority varies with PYTHONHASHSEED: {unstable}"
    )


def test_self_referencing_devalue_payload_walks_linearly() -> None:
    try:
        proc = _run_probe(_SELF_REF_PROBE, "0", timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "O1826-C30 M069 self-referencing devalue payload did not finish in 20s "
            "(_walk re-walks shared nodes)"
        ) from exc
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["nuxt", "Self Reference Title"]


def test_bool_is_not_a_devalue_reference() -> None:
    payload = [
        {"flags": {"hasVideo": True}, "meta": 2},
        {"title": "Bool Target"},
        {"title": "Real Title"},
    ]
    html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
    meta = HydrationExtractor().extract(html)
    assert meta is not None
    assert meta.title == "Real Title", (
        f"O1826-C30 M069 bool resolved as devalue index: {meta.title!r}"
    )
    # False must not resolve as devalue index 0 either (C4 r3 O7: `val is not True`).
    false_payload = [42, {"duration": False, "title": "Some Title"}]
    html = '<script id="__NUXT_DATA__">' + json.dumps(false_payload) + "</script>"
    meta = HydrationExtractor().extract(html)
    assert meta is not None
    assert meta.duration is None, (
        f"O1826-C30 M069 False resolved as devalue index 0: {meta.duration!r}"
    )


def test_shared_devalue_node_still_extracted_once() -> None:
    stream = {"url": "https://cdn.example.test/shared.m3u8", "quality": "1080p"}
    payload = [{"a": 2, "b": 2, "c": [2, 2]}, {"title": "Shared Node Title"}, stream]
    html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
    meta = HydrationExtractor().extract(html)
    assert meta is not None
    assert meta.title == "Shared Node Title"
    assert meta.stream_urls == ["https://cdn.example.test/shared.m3u8"]
    assert [s.quality for s in meta.streams] == ["1080p"]


def test_key_tuples_pin_priority_order() -> None:
    # The first key present wins, so the order IS the behaviour (M068).
    assert hydration_extractor.TITLE_KEYS == (
        "title",
        "scenetitle",
        "release_title",
        "name",
        "headline",
    )
    assert hydration_extractor.DESC_KEYS == (
        "description",
        "summary",
        "desc",
        "overview",
    )
    assert hydration_extractor.DURATION_KEYS == (
        "duration",
        "duration_seconds",
        "runtime",
        "length",
    ), f"O1826-C30 M068 duration priority lost: {hydration_extractor.DURATION_KEYS!r}"
    assert hydration_extractor.THUMB_KEYS == (
        "thumbnail",
        "thumbnail_url",
        "thumbnailurl",
        "poster",
        "posterurl",
        "poster_url",
        "image",
        "cover",
    )
    assert hydration_extractor.URL_KEYS == (
        "url",
        "src",
        "stream_url",
        "download_url",
        "downloadurl",
        "href",
        "link",
    )


def test_shared_node_reached_deep_first_is_rewalked_from_shallower_depth() -> None:
    # payload[0]["chain"] reaches the shared node S = payload[1] at depth 12, where
    # its inline child sits past the depth cap; payload[0]["late"] then reaches S at
    # depth 2. A visited set without depth would skip S there and lose the title.
    payload: list[Any] = [
        {"chain": 2, "late": 1},
        {"x": {"title": "Deep Shared Title"}},
    ]
    payload += [{"n": i + 1} for i in range(2, 11)] + [{"n": 1}]
    html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
    meta = HydrationExtractor().extract(html)
    assert meta is not None
    assert meta.title == "Deep Shared Title", (
        f"O1826-C30 M069 shared node first reached at the depth cap was not re-walked "
        f"from a shallower depth: title={meta.title!r}"
    )


def test_same_depth_shared_nodes_are_walked_once_per_depth() -> None:
    # Layered DAG: each layer references the next layer 3 times, so every node is
    # reached repeatedly at the SAME depth. Re-walking on an equal depth costs 3**10
    # _walk calls; walking each node at most once per strictly shallower depth
    # bounds calls by 1 + 13 * edges.
    layers = 10
    payload: list[Any] = [[i + 1] * 3 for i in range(layers)] + [
        {"title": "Layered Title"}
    ]
    edges = len(payload) + 3 * layers
    html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
    calls = 0

    def _count(frame: FrameType, event: str, arg: object) -> None:
        nonlocal calls
        if event == "call" and frame.f_code.co_name == "_walk":
            calls += 1

    previous = sys.getprofile()
    sys.setprofile(_count)
    try:
        meta = HydrationExtractor().extract(html)
    finally:
        sys.setprofile(previous)
    assert meta is not None
    assert meta.title == "Layered Title"
    assert 0 < calls <= 1 + 13 * edges, (
        f"O1826-C30 M069 _walk re-walks nodes reached again at the same depth: "
        f"{calls} calls for {edges} edges"
    )


def test_same_depth_shared_dict_nodes_are_walked_once_per_depth() -> None:
    # Same layered DAG with DICT layers: devalue object references are dict->dict,
    # so a visited guard applied only to lists would re-walk every shared dict
    # (3**10 _walk calls). Each dict layer references the next layer 3 times.
    layers = 10
    payload: list[Any] = [{"a": i + 1, "b": i + 1, "c": i + 1} for i in range(layers)]
    payload.append({"title": "Dict Layer Title"})
    edges = len(payload) + 3 * layers
    html = '<script id="__NUXT_DATA__">' + json.dumps(payload) + "</script>"
    calls = 0

    def _count(frame: FrameType, event: str, arg: object) -> None:
        nonlocal calls
        if event == "call" and frame.f_code.co_name == "_walk":
            calls += 1

    previous = sys.getprofile()
    sys.setprofile(_count)
    try:
        meta = HydrationExtractor().extract(html)
    finally:
        sys.setprofile(previous)
    assert meta is not None
    assert meta.title == "Dict Layer Title"
    assert 0 < calls <= 1 + 13 * edges, (
        f"O1826-C30 M069 _walk re-walks shared DICT nodes reached again at the same "
        f"depth: {calls} calls for {edges} edges"
    )
