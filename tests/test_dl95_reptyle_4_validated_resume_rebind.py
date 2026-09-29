"""dl95-reptyle-4: a restored job whose signed CDN URL rotated its host/path restarts; it never splices and never parks.

O1508 rerun on test2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-reptyle.md, D3; shot reptyle/nr6/NR6__RP.png):
after the 00:00Z restart both restored reptyle jobs went needs_review "staging resource mismatch for
..._full_2160.mp4.part: this job's 1162036 staged byte(s) belong to a different media URL; refusing before a Range
request". resource_identity ignores query strings, so the rotation is in the host/path, and every restored download
of that site parked.

G1 rebound on a strong ETag; REFUTED (cx-worker-2 F1): an ETag is scoped to one URL, so two files can share one and
If-Range splices them.  G2 is PM ruling A (bd-persist/QUESTION-RP4-G2-POLICY-bd-worker-B1-B.md): on a mismatch for THIS
job's own proven claim the staged bytes are set aside intact (*.orphaned-*.part) and the job restarts at byte 0.
The real-transport regressions for the same contract live in test_part_staging_collision.py and
test_row716_mirror_swap_keeps_the_chain.py.
"""
from __future__ import annotations

import json

import pytest

BD_GATE_SCOPE = "module"

PAGE = "https://www.reptyle.com/movies/abby-rose-juniper-ren"
FIRST = "https://cdn1.example.net/tok-aaaa/pervtherapy_full_2160.mp4?e=1"
ROTATED = "https://cdn2.example.net/tok-bbbb/pervtherapy_full_2160.mp4?e=2"
STAGED = b"\x5a" * (1162036 // 1024)  # a real prefix, kept small


def _staged(tmp_path, meta=None):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "pervtherapy_abby_rose_juniper_ren_full_2160.mp4"
    identity = sc.job_identity(PAGE)
    staging = sc.claim(final, identity, resource_url=FIRST)
    staging.write_bytes(STAGED)
    if meta is not None:
        (staging.parent / (staging.name + ".meta")).write_text(json.dumps(meta), encoding="utf-8")
    return sc, final, identity, staging


def _orphans(tmp_path):
    return [p.read_bytes() for p in sorted(tmp_path.glob("*.orphaned-*.part"))]


def test_a_rotated_url_sets_the_bytes_aside_and_restarts_at_zero(tmp_path):
    sc, final, identity, staging = _staged(tmp_path)
    got = sc.claim(final, identity, resource_url=ROTATED)
    assert got == staging, "dl95-reptyle-4: the restored job did not get its staging path back"
    assert not staging.exists() or staging.stat().st_size == 0, (
        "dl95-reptyle-4: the rotated URL would resume over the old URL's bytes")
    assert _orphans(tmp_path) == [STAGED], "dl95-reptyle-4: the old staged bytes were not set aside intact"


def test_the_restart_is_bound_so_its_own_progress_resumes_next_time(tmp_path):
    sc, final, identity, staging = _staged(tmp_path)
    sc.claim(final, identity, resource_url=ROTATED)
    staging.write_bytes(b"\x01" * 4096)                       # the restarted download's own progress
    assert sc.claim(final, identity, resource_url=ROTATED + "&refresh=1") == staging
    assert staging.read_bytes() == b"\x01" * 4096, "dl95-reptyle-4: the rebound claim discarded its own resume"
    assert _orphans(tmp_path) == [STAGED], "a second set-aside fired for bytes the claim now owns"


def test_a_query_only_rotation_still_resumes_in_place(tmp_path):
    sc, final, identity, staging = _staged(tmp_path)
    assert sc.claim(final, identity, resource_url=FIRST.replace("e=1", "e=99")) == staging
    assert staging.read_bytes() == STAGED and _orphans(tmp_path) == []


def test_an_equal_strong_etag_does_not_license_a_resume(tmp_path):
    # G1 REFUTE F1: the old bytes' ETag says nothing about the new URL's object.
    sc, final, identity, staging = _staged(tmp_path, {"etag": '"v1"'})
    sc.claim(final, identity, resource_url=ROTATED)
    assert not staging.exists() or staging.stat().st_size == 0, "dl95-reptyle-4 G2: an ETag licensed a cross-URL resume"
    assert _orphans(tmp_path) == [STAGED]


def test_another_jobs_claim_is_never_set_aside(tmp_path):
    sc, final, _identity, staging = _staged(tmp_path)
    with pytest.raises(sc.StagingClaimedByAnotherJob):
        sc.claim(final, sc.job_identity(PAGE + "-other"), resource_url=ROTATED)
    assert staging.read_bytes() == STAGED and _orphans(tmp_path) == []
