"""fx-dailymotion-player (O1567): dailymotion's own player fetched
/cdn/manifest/video/xbe8y8e.m3u8 (logged "hls manifest detected" on test2), but the
job failed "[page_shape] ... third-party embed (geo.dailymotion.com) whose player listed
no files": scene_stream_candidates only knew numeric scene ids (beeg), so the scene's
own manifest was never a candidate.  A /video/<alphanumeric id> route now identifies
the scene; the stream must still be the file named by that id."""
from bulk_downloader import spa_media_extract as spa

BD_GATE_SCOPE = "module"

JOB = "https://www.dailymotion.com/video/xbe8y8e?ref=o1564"
OWN = ("https://www.dailymotion.com/cdn/manifest/video/xbe8y8e.m3u8"
       "?sec=TOKEN&dmTs=1&dmV1st=2")
OTHER = "https://www.dailymotion.com/cdn/manifest/video/xbe6xz2.m3u8?sec=TOKEN"


def test_dailymotion_scene_manifest_is_a_candidate():
    cands = spa.scene_stream_candidates(JOB, [OWN])
    assert [c["url"] for c in cands] == [OWN], cands
    assert cands[0]["source"] == "scene-stream"
    assert cands[0]["filename"] == "xbe8y8e"


def test_another_scenes_manifest_is_not_a_candidate():
    assert spa.scene_stream_candidates(JOB, [OTHER]) == []


def test_id_as_a_substring_or_query_is_not_identity():
    assert spa.scene_stream_candidates(JOB, [
        "https://www.dailymotion.com/cdn/manifest/video/xbe8y8e0.m3u8",
        "https://www.dailymotion.com/cdn/manifest/video/other.m3u8?v=xbe8y8e",
    ]) == []


def test_route_without_a_video_segment_is_unchanged():
    # neither a beeg opaque id nor a /video/<id> route -> nothing, as before
    assert spa.scene_stream_candidates(
        "https://www.dailymotion.com/xbe8y8e", [OWN]) == []


def test_beeg_numeric_route_unchanged():
    page = "https://beeg.com/-0920833012505915"
    s = "https://video.beeg.com/data=x/av1_720p/920833012505915.mp4.m3u8"
    assert [c["url"] for c in spa.scene_stream_candidates(page, [s])] == [s]
