"""tests/test_row837_audio_fingerprint.py - Cut 837: Acoustic Audio Fingerprint Dedup.

Verifies:
(1) matching acoustic fingerprints across identical audio with differing video resolutions (720p vs 1080p),
(2) non-blocking failure recovery when fpcalc is missing,
(3) in-memory cache limits comparison latency to <10ms.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from unittest.mock import MagicMock, patch
import pytest

from bulk_downloader import audio_fingerprint as af

BD_GATE_SCOPE = "module"


class TestAvailabilityAndFailureRecovery:
    """Verifies non-blocking failure recovery when fpcalc is missing or unexecutable."""

    def test_is_fpcalc_available_returns_bool(self):
        result = af.is_fpcalc_available()
        assert isinstance(result, bool)

    def test_is_ffmpeg_available_returns_bool(self):
        result = af.is_ffmpeg_available()
        assert isinstance(result, bool)

    def test_is_available_returns_bool(self):
        result = af.is_available()
        assert isinstance(result, bool)

    def test_missing_fpcalc_returns_none_non_blocking(self, tmp_path):
        dummy_media = tmp_path / "sample_720p.mp4"
        dummy_media.write_bytes(b"dummy mp4 media content")

        with patch("shutil.which", return_value=None):
            assert af.is_fpcalc_available() is False
            # Must return None or fail-open without raising any exception
            result = af.compute_audio_fingerprint(str(dummy_media))
            assert result is None

    def test_missing_file_returns_none_non_blocking(self):
        nonexistent = "/nonexistent/path/to/media.mp4"
        result = af.compute_audio_fingerprint(nonexistent)
        assert result is None

    def test_subprocess_failure_recovers_gracefully(self, tmp_path):
        dummy_media = tmp_path / "broken.mp4"
        dummy_media.write_bytes(b"corrupt media")

        with patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", side_effect=OSError("Command failed")):
            result = af.compute_audio_fingerprint(str(dummy_media))
            assert result is None

    def test_subprocess_nonzero_exit_recovers_gracefully(self, tmp_path):
        dummy_media = tmp_path / "unsupported.mp4"
        dummy_media.write_bytes(b"non-audio stream")

        mock_proc = MagicMock(returncode=1, stdout="", stderr="ERROR: cannot decode audio")
        with patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", return_value=mock_proc):
            result = af.compute_audio_fingerprint(str(dummy_media))
            assert result is None

    def test_subprocess_timeout_recovers_gracefully(self, tmp_path):
        dummy_media = tmp_path / "huge.mp4"
        dummy_media.write_bytes(b"huge video")

        with patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["fpcalc"], timeout=30.0)):
            result = af.compute_audio_fingerprint(str(dummy_media))
            assert result is None

    def test_corrupt_json_output_recovers_gracefully(self, tmp_path):
        dummy_media = tmp_path / "garbled.mp4"
        dummy_media.write_bytes(b"garbled")

        mock_proc = MagicMock(returncode=0, stdout="not-json-output", stderr="")
        with patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", return_value=mock_proc):
            result = af.compute_audio_fingerprint(str(dummy_media))
            assert result is None


class TestCrossResolutionMatching:
    """Verifies matching acoustic fingerprints across identical audio with differing video resolutions."""

    def test_identical_audio_different_resolution_matches(self):
        # 32-bit Chromaprint fingerprint vector representing the shared audio track
        shared_audio_vector = (
            0x1A2B3C4D, 0x5E6F7081, 0x92A3B4C5, 0xD6E7F809,
            0x13579BDF, 0x02468ACE, 0x11223344, 0x55667788,
            0x99AABBCC, 0xDDEEFF00, 0x12345678, 0x9ABCDEF0,
        )

        fp_720p = af.AudioFingerprint(
            path="/downloads/video_scene_01_720p.mp4",
            duration=120.0,
            fingerprint=shared_audio_vector,
            computed_at=time.time(),
        )

        fp_1080p = af.AudioFingerprint(
            path="/downloads/video_scene_01_1080p.mp4",
            duration=120.0,
            fingerprint=shared_audio_vector,
            computed_at=time.time(),
        )

        match = af.compare_fingerprints(fp_720p, fp_1080p)
        assert match.is_match is True
        assert match.similarity == 1.0
        assert match.hamming_distance == 0

    def test_near_identical_audio_with_slight_reencode_noise_matches(self):
        # Base vector for 1080p
        base_vector = [
            0x1A2B3C4D, 0x5E6F7081, 0x92A3B4C5, 0xD6E7F809,
            0x13579BDF, 0x02468ACE, 0x11223344, 0x55667788,
        ]
        # 720p re-encode: flip 1 bit in 2 subfingerprints (slight acoustic compression variance)
        reencoded_vector = [
            0x1A2B3C4D ^ 0x01,
            0x5E6F7081,
            0x92A3B4C5,
            0xD6E7F809 ^ 0x02,
            0x13579BDF,
            0x02468ACE,
            0x11223344,
            0x55667788,
        ]

        fp_1080p = af.AudioFingerprint(
            path="scene_1080p.mkv", duration=60.0, fingerprint=tuple(base_vector), computed_at=time.time()
        )
        fp_720p = af.AudioFingerprint(
            path="scene_720p.mp4", duration=60.0, fingerprint=tuple(reencoded_vector), computed_at=time.time()
        )

        match = af.compare_fingerprints(fp_1080p, fp_720p, threshold=0.85)
        assert match.is_match is True
        assert match.similarity > 0.95
        assert match.hamming_distance == 2

    def test_distinct_content_does_not_match(self):
        # Completely different audio tracks
        audio_a = (0x00000000, 0x00000000, 0x00000000, 0x00000000)
        audio_b = (0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)

        fp_a = af.AudioFingerprint(path="movie_a.mp4", duration=100.0, fingerprint=audio_a, computed_at=time.time())
        fp_b = af.AudioFingerprint(path="movie_b.mp4", duration=100.0, fingerprint=audio_b, computed_at=time.time())

        match = af.compare_fingerprints(fp_a, fp_b, threshold=0.80)
        assert match.is_match is False
        assert match.similarity < 0.10

    def test_empty_fingerprint_vectors_handled_cleanly(self):
        match = af.compare_fingerprints((), ())
        assert match.is_match is False
        assert match.similarity == 0.0
        assert match.evaluated_elements == 0

    def test_successful_compute_via_mock_fpcalc(self, tmp_path):
        dummy_media = tmp_path / "valid.mp4"
        dummy_media.write_bytes(b"media file bytes")

        fp_data = {
            "duration": 42.5,
            "fingerprint": [1234567, 7654321, 9999999]
        }
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(fp_data), stderr="")

        with patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", return_value=mock_proc):
            fp = af.compute_audio_fingerprint(str(dummy_media), use_cache=False)

            assert fp is not None
            assert fp.duration == 42.5
            assert fp.fingerprint == (1234567, 7654321, 9999999)
            assert fp.path == os.path.abspath(str(dummy_media))

    def test_fingerprint_normalizes_sequence_and_32_bit_values(self):
        fp = af.AudioFingerprint("clip.mp4", 1.0, [-1, 2], time.time())
        assert fp.fingerprint == (0xFFFFFFFF, 2)

    def test_hamming_distance_counts_changed_bits(self):
        assert af.hamming_distance_32(0b1010, 0b1100) == 2

    def test_fpcalc_path_is_used_for_invocation(self, tmp_path, monkeypatch):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"media")
        monkeypatch.setenv("FPCALC_PATH", "/opt/fpcalc-custom")
        proc = MagicMock(returncode=0, stdout='{"duration": 1, "fingerprint": [1]}', stderr="")
        with patch("shutil.which", return_value="/opt/fpcalc-custom"), \
             patch("subprocess.run", return_value=proc) as run:
            assert af.compute_audio_fingerprint(media, use_cache=False) is not None
        assert run.call_args.args[0][0] == "/opt/fpcalc-custom"


class TestInMemoryCacheAndLatency:
    """Verifies that in-memory cache limits comparison latency to <10ms."""

    def test_in_memory_cache_hit_and_comparison_latency_under_10ms(self, tmp_path):
        cache = af.AudioFingerprintCache()

        sample_a = tmp_path / "clip_720p.mp4"
        sample_b = tmp_path / "clip_1080p.mp4"
        sample_a.write_bytes(b"content 720p")
        sample_b.write_bytes(b"content 1080p")

        # 500 subfingerprints (~50 seconds of 32-bit audio frames)
        vector_a = tuple((i * 1234567) & 0xFFFFFFFF for i in range(500))
        vector_b = tuple((i * 1234567) & 0xFFFFFFFF for i in range(500))

        fp_a = af.AudioFingerprint(path=str(sample_a), duration=50.0, fingerprint=vector_a, computed_at=time.time())
        fp_b = af.AudioFingerprint(path=str(sample_b), duration=50.0, fingerprint=vector_b, computed_at=time.time())

        cache.put(fp_a)
        cache.put(fp_b)

        # Retrieve from cache
        cached_a = cache.get(str(sample_a))
        cached_b = cache.get(str(sample_b))
        assert cached_a is fp_a
        assert cached_b is fp_b

        # Benchmark comparison latency across cached entries (must be <10ms)
        t0 = time.perf_counter()
        match = cache.compare(str(sample_a), str(sample_b))
        latency_ms = (time.perf_counter() - t0) * 1000.0

        assert match is not None
        assert match.is_match is True
        assert latency_ms < 10.0, f"Comparison latency was {latency_ms:.2f}ms (threshold: <10ms)"

    def test_cache_dedup_matcher_registry(self, tmp_path):
        matcher = af.AcousticDedupMatcher()

        path_720p = str(tmp_path / "video_720p.mp4")
        path_1080p = str(tmp_path / "video_1080p.mp4")
        path_other = str(tmp_path / "video_other.mp4")

        vector_shared = tuple((i * 99991) & 0xFFFFFFFF for i in range(100))
        vector_other = tuple((i * 33331) & 0xFFFFFFFF for i in range(100))

        matcher.index_fingerprint(af.AudioFingerprint(path_720p, 30.0, vector_shared, time.time()))
        matcher.index_fingerprint(af.AudioFingerprint(path_other, 30.0, vector_other, time.time()))

        # Query 1080p against registry to find 720p duplicate
        query_fp = af.AudioFingerprint(path_1080p, 30.0, vector_shared, time.time())
        duplicates = matcher.find_duplicates(query_fp, threshold=0.90)

        assert len(duplicates) == 1
        assert duplicates[0].path == path_720p
        assert duplicates[0].similarity == 1.0

    def test_cache_eviction_and_management(self):
        cache = af.AudioFingerprintCache(max_entries=2)
        fp1 = af.AudioFingerprint("f1.mp4", 10.0, (1, 2), time.time())
        fp2 = af.AudioFingerprint("f2.mp4", 10.0, (3, 4), time.time())
        fp3 = af.AudioFingerprint("f3.mp4", 10.0, (5, 6), time.time())

        cache.put(fp1)
        cache.put(fp2)
        assert cache.size() == 2

        cache.put(fp3)
        assert cache.size() == 2
        # Oldest (f1) was evicted
        assert cache.get("f1.mp4") is None
        assert cache.get("f2.mp4") is not None
        assert cache.get("f3.mp4") is not None

        cache.remove("f2.mp4")
        assert cache.size() == 1
        assert cache.get("f2.mp4") is None

        cache.clear()
        assert cache.size() == 0

    def test_compute_reuses_default_cache_without_a_second_fpcalc_call(self, tmp_path):
        media = tmp_path / "cached.mp4"
        media.write_bytes(b"media")
        proc = MagicMock(returncode=0, stdout='{"duration": 1, "fingerprint": [7]}', stderr="")
        with patch.object(af, "_DEFAULT_CACHE", af.AudioFingerprintCache()), \
             patch("shutil.which", return_value="/usr/bin/fpcalc"), \
             patch("subprocess.run", return_value=proc) as run:
            first = af.compute_audio_fingerprint(media)
            second = af.compute_audio_fingerprint(media)
        assert second is first
        assert run.call_count == 1

    def test_index_file_computes_and_indexes_the_result(self):
        matcher = af.AcousticDedupMatcher()
        fp = af.AudioFingerprint("indexed.mp4", 1.0, (1, 2), time.time())
        with patch.object(af, "compute_audio_fingerprint", return_value=fp) as compute:
            assert matcher.index_file("indexed.mp4") is fp
        compute.assert_called_once_with("indexed.mp4")
        assert matcher._cache.get("indexed.mp4") is fp
        assert matcher._index[af._norm_path("indexed.mp4")] is fp

    def test_string_query_uses_index_cache_before_computing(self):
        matcher = af.AcousticDedupMatcher()
        source = af.AudioFingerprint("source.mp4", 1.0, (1, 2), time.time())
        candidate = af.AudioFingerprint("candidate.mp4", 1.0, (1, 2), time.time())
        matcher.index_fingerprint(source)
        matcher.index_fingerprint(candidate)
        with patch.object(af, "compute_audio_fingerprint") as compute:
            matches = matcher.find_duplicates("source.mp4")
        compute.assert_not_called()
        assert [match.path for match in matches] == ["candidate.mp4"]

    def test_string_cache_miss_computes_query_before_matching(self):
        matcher = af.AcousticDedupMatcher()
        query = af.AudioFingerprint("query.mp4", 1.0, (1, 2), time.time())
        candidate = af.AudioFingerprint("candidate.mp4", 1.0, (1, 2), time.time())
        matcher.index_fingerprint(candidate)
        with patch.object(af, "compute_audio_fingerprint", return_value=query) as compute:
            matches = matcher.find_duplicates("query.mp4")
        compute.assert_called_once_with("query.mp4")
        assert [match.path for match in matches] == ["candidate.mp4"]


# ---- fixer (O928) controls for the correctness REFUTE P1/P1/P2/P2/P2/P2 ----

class TestFixerControls:
    def test_p1_one_shared_element_is_not_a_duplicate(self):
        from bulk_downloader.audio_fingerprint import compare_fingerprints
        import random
        rng = random.Random(837)
        a = tuple(rng.getrandbits(32) for _ in range(6))
        b = tuple(x ^ 0xFFFFFFFF for x in a)      # bitwise complement: every aligned pair differs in 32 bits
        b = (a[5],) + b[1:]                         # ...except ONE shared element, a[5] == b[0] at offset +5
        res = compare_fingerprints(a, b, threshold=0.85, max_offset=5)
        assert res.is_match is False, res
        assert res.evaluated_elements >= 3 and res.similarity < 0.85
        # the old behaviour (a 1-element overlap counted) would have called this an exact duplicate:
        one = compare_fingerprints(a[5:], b[:1], max_offset=0, min_overlap_fraction=0.0)
        assert one.similarity == 1.0 and one.evaluated_elements == 1
        # meaningful overlap still matches a genuine shifted duplicate
        base = tuple((i * 2654435761) & 0xFFFFFFFF for i in range(1, 41))
        shifted = base[2:] + (7, 8)
        res = compare_fingerprints(base, shifted, max_offset=5)
        assert res.is_match is True and res.offset in (2, -2) and res.evaluated_elements >= 36

    def test_p2_fully_different_elements_report_evaluated_distance(self):
        from bulk_downloader.audio_fingerprint import compare_fingerprints
        res = compare_fingerprints((0x00000000, 0x00000000), (0xFFFFFFFF, 0xFFFFFFFF), max_offset=0)
        assert res.evaluated_elements == 2 and res.hamming_distance == 64
        assert res.similarity == 0.0 and res.is_match is False
        res = compare_fingerprints((0x00000000,), (0xFFFFFFFF,), max_offset=0)
        assert res.evaluated_elements == 1 and res.hamming_distance == 32

    def test_p2_overwrite_at_capacity_keeps_the_other_entry(self):
        from bulk_downloader.audio_fingerprint import AudioFingerprint, AudioFingerprintCache
        cache = AudioFingerprintCache(max_entries=2)
        mk = lambda p, v: AudioFingerprint(path=p, duration=1.0, fingerprint=(v,), computed_at=0.0)
        cache.put(mk("/a", 1)); cache.put(mk("/b", 2))
        cache.put(mk("/b", 3))   # overwrite, not a new insertion
        assert cache.size() == 2
        assert cache.get("/a") is not None and cache.get("/b").fingerprint == (3,)
        cache.put(mk("/c", 4))   # a real insertion evicts the oldest (/a)
        assert cache.size() == 2 and cache.get("/a") is None

    def test_p2_tuple_input_is_normalized_too(self):
        from bulk_downloader.audio_fingerprint import AudioFingerprint
        fp = AudioFingerprint(path="/x", duration=0.0, fingerprint=(-1, 4294967303), computed_at=0.0)
        assert fp.fingerprint == (4294967295, 7)
        fp = AudioFingerprint(path="/x", duration=0.0, fingerprint=[-1, 4294967303], computed_at=0.0)
        assert fp.fingerprint == (4294967295, 7)

    def test_p2_non_object_fpcalc_json_returns_none(self, tmp_path, monkeypatch):
        import subprocess
        from bulk_downloader import audio_fingerprint as af
        media = tmp_path / "x.mp4"; media.write_bytes(b"\x00" * 10)
        monkeypatch.setattr(af, "is_fpcalc_available", lambda: True)
        monkeypatch.setattr(af, "get_fpcalc_path", lambda: "fpcalc")
        for out in ("[]", "null", '{"fingerprint": "abc"}', '{"fingerprint": {"0": 1}}'):
            monkeypatch.setattr(af.subprocess, "run",
                                lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=out, stderr=""))
            assert af.compute_audio_fingerprint(str(media), use_cache=False) is None, out

    def test_p1_replaced_file_is_not_served_from_cache(self, tmp_path, monkeypatch):
        import json as _json, os, subprocess
        from bulk_downloader import audio_fingerprint as af
        media = tmp_path / "clip.mp4"; media.write_bytes(b"A" * 100)
        monkeypatch.setattr(af, "is_fpcalc_available", lambda: True)
        monkeypatch.setattr(af, "get_fpcalc_path", lambda: "fpcalc")
        calls = []

        def run(argv, **k):
            calls.append(argv)
            size = os.path.getsize(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout=_json.dumps(
                {"duration": float(size), "fingerprint": [size, size + 1]}), stderr="")

        monkeypatch.setattr(af.subprocess, "run", run)
        af._DEFAULT_CACHE.clear()
        first = af.compute_audio_fingerprint(str(media))
        again = af.compute_audio_fingerprint(str(media))
        assert first.fingerprint == (100, 101) and again is first and len(calls) == 1
        media.write_bytes(b"B" * 250)   # the file at this path is now a different file
        os.utime(media, ns=(1, 1))      # make the identity change unambiguous
        replaced = af.compute_audio_fingerprint(str(media))
        assert len(calls) == 2
        assert replaced.fingerprint == (250, 251) and replaced.duration == 250.0
        assert af.compute_audio_fingerprint(str(media)) is replaced and len(calls) == 2
        af._DEFAULT_CACHE.clear()


# ---- fixer (O928) round 2 controls: correctness REFUTE items 2, 3, 4 ----

class TestFixerRound2:
    def test_matcher_string_query_recomputes_when_the_file_was_replaced(self, tmp_path, monkeypatch):
        """Item 2: find_duplicates(str) applied the cache without the file
        identity check that compute_audio_fingerprint enforces."""
        media = tmp_path / "q.mp4"
        media.write_bytes(b"v1")
        matcher = af.AcousticDedupMatcher()
        stale = af.AudioFingerprint(str(media), 1.0, (1, 2), time.time(),
                                    file_identity=af._file_identity(str(media)))
        candidate = af.AudioFingerprint("candidate.mp4", 1.0, (1, 2), time.time())
        matcher.index_fingerprint(stale)
        matcher.index_fingerprint(candidate)
        # same content, cache is trusted
        with patch.object(af, "compute_audio_fingerprint") as compute:
            matcher.find_duplicates(str(media))
        compute.assert_not_called()
        # file rewritten at the same path -> identity differs -> recompute
        media.write_bytes(b"v2-longer")
        fresh = af.AudioFingerprint(str(media), 1.0, (0xFFFFFFFF, 0), time.time())
        with patch.object(af, "compute_audio_fingerprint", return_value=fresh) as compute:
            matches = matcher.find_duplicates(str(media))
        compute.assert_called_once_with(str(media))
        assert matches == []

    def test_cache_refuses_an_empty_bound_instead_of_stopiteration(self):
        """Item 3: max_entries=0 raised StopIteration out of put()."""
        with pytest.raises(ValueError, match="max_entries"):
            af.AudioFingerprintCache(max_entries=0)
        with pytest.raises(ValueError, match="max_entries"):
            af.AudioFingerprintCache(max_entries=-1)
        cache = af.AudioFingerprintCache(max_entries=1)
        cache.put(af.AudioFingerprint("a.mp4", 1.0, (1,), time.time()))
        cache.put(af.AudioFingerprint("b.mp4", 1.0, (1,), time.time()))
        assert cache.size() == 1 and cache.get("b.mp4") is not None

    def test_ffmpeg_probe_goes_through_the_pinned_resolver(self, monkeypatch):
        """Item 4 (MOD-4): ffmpeg_bin decides which ffmpeg exists; PATH is
        never probed directly."""
        from bulk_downloader import ffmpeg_bin
        monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: None)
        monkeypatch.setattr(af.shutil, "which", lambda *_a, **_k: "/usr/bin/ffmpeg")
        assert af.is_ffmpeg_available() is False
        monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/opt/bd/ffmpeg")
        monkeypatch.setattr(af.shutil, "which", lambda *_a, **_k: None)
        assert af.is_ffmpeg_available() is True


# ---- fixer (O928) round 3: correctness REFUTE E1 (comparison latency at register scale) ----

class TestFixerRound3:
    def _hour(self, seed):
        # Chromaprint emits ~8 uint32 per second: 60 minutes ~ 28800 elements
        return tuple(((i * 2654435761) ^ seed) & 0xFFFFFFFF for i in range(28800))

    def test_sixty_minute_comparison_is_under_10ms_from_cache(self, tmp_path):
        cache = af.AudioFingerprintCache()
        a = af.AudioFingerprint(str(tmp_path / "a.mp4"), 3600.0, self._hour(0), time.time())
        b = af.AudioFingerprint(str(tmp_path / "b.mp4"), 3600.0, self._hour(0), time.time())
        cache.put(a)
        cache.put(b)
        best = float("inf")
        for _ in range(5):
            t0 = time.perf_counter()
            match = cache.compare(str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"))
            best = min(best, (time.perf_counter() - t0) * 1000.0)
        assert match is not None and match.is_match and match.similarity == 1.0
        assert match.evaluated_elements == 28800
        assert best < 10.0, f"60-min compare min-of-5 {best:.2f}ms (threshold <10ms)"

    def test_packed_comparison_equals_the_elementwise_definition(self):
        import random
        rng = random.Random(837)
        v1 = tuple(rng.getrandbits(32) for _ in range(300))
        v2 = tuple(x ^ (rng.getrandbits(32) if rng.random() < 0.1 else 0) for x in v1)
        v2 = v2[3:] + (7, 8, 9)                       # shifted by 3 + tail noise
        res = af.compare_fingerprints(v1, v2, max_offset=5)
        # reference: the elementwise hamming sum at the reported offset
        off = res.offset
        s1, s2 = (off, 0) if off >= 0 else (0, -off)
        overlap = min(len(v1) - s1, len(v2) - s2)
        ref = sum(af.hamming_distance_32(v1[s1 + i], v2[s2 + i]) for i in range(overlap))
        assert (res.hamming_distance, res.evaluated_elements) == (ref, overlap)
        assert abs(res.similarity - (1.0 - ref / (overlap * 32))) < 1e-4
        assert off == 3

    def test_relative_and_absolute_spellings_of_one_file_are_one_record(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        matcher = af.AcousticDedupMatcher()
        v = tuple(i & 0xFFFFFFFF for i in range(50))
        rel = af.AudioFingerprint("a_720p.mp4", 1.0, v, time.time())
        other = af.AudioFingerprint(str(tmp_path / "b.mp4"), 1.0, v, time.time())
        matcher.index_fingerprint(rel)
        matcher.index_fingerprint(other)
        q = af.AudioFingerprint(str(tmp_path / "a_720p.mp4"), 1.0, v, time.time())
        assert [m.path for m in matcher.find_duplicates(q)] == [str(tmp_path / "b.mp4")]
