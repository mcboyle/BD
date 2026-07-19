"""U20 — ffmpeg command previewer (D-33) + resolution-scoring tester
(D-34). The previewer uses the real hls_downloader._build_ffmpeg_cmd;
the resolution tester exercises both INV-005 resolution code paths.
"""
from bulk_downloader import dev_suite as ds


# ── ffmpeg_command_preview (D-33) ─────────────────────────────────

def test_ffmpeg_preview_requires_a_url():
    assert ds.ffmpeg_command_preview("")["ok"] is False


def test_ffmpeg_preview_argv_ordering():
    r = ds.ffmpeg_command_preview(
        "https://cdn.example.com/v/playlist.m3u8")
    assert r["ok"] is True
    argv = r["argv"]
    # the ffmpeg argv-ordering invariant: -i present, URL right after
    # it, output path last
    i = argv.index("-i")
    assert argv[i + 1] == "https://cdn.example.com/v/playlist.m3u8"
    assert argv[-1] == "preview.mp4"
    # input options precede -i
    assert "-timeout" in argv and argv.index("-timeout") < i
    # output options follow -i
    assert "-c" in argv and argv.index("-c") > i


def test_ffmpeg_preview_honours_output_name():
    r = ds.ffmpeg_command_preview("https://x/y.m3u8",
                                  output_name="my_clip.mkv")
    assert r["argv"][-1] == "my_clip.mkv"


def test_ffmpeg_preview_detects_hls():
    r = ds.ffmpeg_command_preview("https://x/stream.m3u8")
    assert r["looks_like_hls"] is True
    assert r["command"]


# ── resolution_scoring_test (D-34) ────────────────────────────────

def test_resolution_test_runs_the_probe_battery():
    r = ds.resolution_scoring_test()
    assert r["ok"] is True
    assert r["probe_count"] >= 10
    for p in r["probes"]:
        assert "detect_res_score" in p
        assert "heuristic_tier" in p


def test_resolution_test_both_paths_agree_on_common_tiers():
    # the two INV-005 tables should both detect a clear "1080p" string
    r = ds.resolution_scoring_test()
    by_input = {p["input"]: p for p in r["probes"]}
    p = by_input["1080p"]
    assert p["detect_res_score"] == 1080
    assert p["heuristic_tier"] > 0
    assert p["both_paths_detect"] is True


def test_resolution_test_ad_hoc_text():
    r = ds.resolution_scoring_test(text="4K UHD release")
    ad = r["ad_hoc"]
    assert ad is not None
    assert ad["detect_res_score"] == 2160


def test_resolution_test_no_ad_hoc_when_no_text():
    r = ds.resolution_scoring_test()
    assert r["ad_hoc"] is None


def test_resolution_test_reports_divergence_signal():
    # divergent_inputs is the INV-005 desync signal — present as a list
    r = ds.resolution_scoring_test()
    assert isinstance(r["divergent_inputs"], list)
    assert r["probes_one_path_only"] == len(r["divergent_inputs"])


# ── routes ────────────────────────────────────────────────────────

def test_ffmpeg_preview_route(fresh_app):
    resp = fresh_app.get(
        "/api/dev/ffmpeg_preview?url=https://x/y.m3u8")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["argv"][-1] == "preview.mp4"


def test_resolution_test_route(fresh_app):
    resp = fresh_app.get("/api/dev/resolution_test?text=720p")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tool"] == "resolution_scoring_test"
    assert body["ad_hoc"] is not None
