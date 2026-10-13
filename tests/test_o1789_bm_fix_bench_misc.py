"""O1789 bughunt bench-misc (finder S41-C) -- fixes for findings V1, M2, M3, M4, C1.

V1  viewport_clipping_filter kept the common visually-hidden forms as visible: only ``rect(0,0,0,0)`` with commas
    and ``inset(100%)`` were recognised.
M2  multi_site_benchmark.compare_sites counted a MISSING goal classification (None) as a distinct outcome.
M3  compare_sites({}) reported the segment-role rule "reproduced on all sites" with zero sites.
M4  compare_sites counted "no signing params" as a signing scheme.
C1  tools/multi_site_benchmark.py silently replaced the earlier pair when a --site NAME was repeated.

r2 (lens cx168 F1 + PM ruling on M1):
F1  the r1 V1 fix dropped VISIBLE static / relative / sticky nodes carrying an empty legacy ``clip``: CSS applies
    ``clip`` only to absolutely positioned elements.
M1  compare_sites called a class "confirmed reusable" whenever it was seen on two sites; the spec needs DIFFERENT
    instances. The producer now emits instance ids beside the count; without ids the class is undecidable.

Pure-function tests: no network, no app, no DB. V2 and E1 of the same sweep are closed WONTFIX (PM ruling).
"""

from __future__ import annotations

import pytest

from bulk_downloader import capture_workbench as workbench
from bulk_downloader import multi_site_benchmark as ms
from bulk_downloader.capture_synth import synthesize
from bulk_downloader.viewport_clipping_filter import ViewportBounds, filter_off_canvas, is_off_canvas
from tools import multi_site_benchmark as cli

BD_GATE_SCOPE = "module"
VP = ViewportBounds(width=1280, height=720)
IN_VIEW = {"x": 50, "y": 50, "width": 80, "height": 20}


def _node(**style):
    return {"tag": "span", "style": {k.replace("_", "-"): v for k, v in style.items()}, "rect": dict(IN_VIEW)}


# ── V1 ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("style", [
    {"clip": "rect(0 0 0 0)"},                 # CSS2 space form
    {"clip": "rect(0px 0px 0px 0px)"},
    {"clip": "rect(1px, 1px, 1px, 1px)"},      # Bootstrap / WordPress sr-only: zero area
    {"clip": "rect(1px 1px 1px 1px)"},
    {"clip": "RECT(0,0,0,0)"},
    {"clip": "rect(0, 0, 0, 0)"},              # landed control forms must stay hidden
    {"clip": "rect(0px,0px,0px,0px)"},
    {"clip": "rect(10px, 5px, 20px, 5px)"},    # right == left: no width
    {"clip_path": "inset(50%)"},
    {"clip_path": "inset(100% 0 0 0)"},
    {"clip_path": "INSET(100%)"},
    {"clip_path": "inset(100%)"},
    {"clip_path": "inset(0 60% 0 40%)"},
    {"clip_path": "inset(50% round 4px)"},
])
def test_v1_visually_hidden_forms_are_off_canvas(style):
    assert is_off_canvas(_node(**style), VP) is True, f"BMFIX-V1-HIDDEN-KEPT: {style} was kept as a visible node"


@pytest.mark.parametrize("style", [
    {"clip": "rect(0, 100px, 100px, 0)"},      # a real 100x100 window
    {"clip": "rect(0 80px 20px 0)"},
    {"clip": "rect(auto, auto, auto, auto)"},
    {"clip": "auto"},
    {"clip": "rect(0, 0, 0)"},                 # not four values: cannot measure
    {"clip_path": "inset(10%)"},
    {"clip_path": "inset(49% 0)"},
    {"clip_path": "inset(0 50% 0 49%)"},
    {"clip_path": "inset(100px)"},             # px inset: depends on the box, cannot measure
    {"clip_path": "inset(0)"},
    {"clip_path": "none"},
    {},
])
def test_v1_visible_or_unmeasurable_clips_are_kept(style):
    assert is_off_canvas(_node(**style), VP) is False, f"BMFIX-V1-VISIBLE-DROPPED: {style} was dropped as off-canvas"


def test_v1_filter_drops_the_sr_only_node_and_keeps_its_sibling():
    nodes = [_node(clip="rect(1px, 1px, 1px, 1px)"), _node(), _node(clip_path="inset(50%)")]
    kept = filter_off_canvas(nodes, VP)
    assert kept == [nodes[1]], f"BMFIX-V1-HIDDEN-KEPT: filter kept {len(kept)} of 3 nodes, expected only the unstyled one"


# ── M2 / M3 / M4 ─────────────────────────────────────────────────────────────
def _profile(**over):
    base = dict(goal_host="h", goal_classification="direct_file", new_provider_required=False,
                signing_params=["token"], identity_slot_count=1, rendition_slot_count=1,
                reusable_classes=["telemetry"], framework_level_count=3, site_specific_count=10,
                sensitivity_robust=["assume:goal_selection"])
    base.update(over)
    return base


def test_m2_a_missing_classification_is_not_a_differing_outcome():
    c = ms.compare_sites({"a": _profile(), "b": _profile(goal_classification=None)})
    assert c["goal_classification_varies"] is False and c["verdict"]["confirmed_site_specific"] == [], (
        f"BMFIX-M2-NONE-IS-AN-OUTCOME: varies={c['goal_classification_varies']} "
        f"confirmed_site_specific={c['verdict']['confirmed_site_specific']} for direct_file vs no classification")
    assert c["goal_classification_by_site"] == {"a": "direct_file", "b": None}  # still surfaced, not hidden


def test_m2_controls_real_outcomes_still_vary():
    c = ms.compare_sites({"a": _profile(), "b": _profile(goal_classification="unknown")})
    assert c["goal_classification_varies"] is True and c["verdict"]["confirmed_site_specific"] == ["goal_classification"], (
        "BMFIX-M2-REAL-VARIATION-LOST: direct_file vs the string 'unknown' must still vary")
    c = ms.compare_sites({"a": _profile(), "b": _profile(goal_classification=None), "c": _profile(goal_classification="hls")})
    assert c["goal_classification_varies"] is True, "BMFIX-M2-REAL-VARIATION-LOST: two real outcomes beside a missing one"
    assert ms.compare_sites({"a": _profile(), "b": _profile()})["goal_classification_varies"] is False


def test_m3_zero_sites_reproduce_nothing():
    c = ms.compare_sites({})
    assert c["segment_role_reproduced_all_sites"] is False, (
        "BMFIX-M3-VACUOUS-ALL: segment_role_reproduced_all_sites is True with zero sites")
    assert c["sites"] == [] and c["sensitivity_robust_across_all"] == [] and c["distinct_signing_schemes"] == 0


def test_m3_controls():
    assert ms.compare_sites({"a": _profile()})["segment_role_reproduced_all_sites"] is True
    assert ms.compare_sites({"a": _profile(), "b": _profile(identity_slot_count=0)})["segment_role_reproduced_all_sites"] is False


@pytest.mark.parametrize("schemes,want", [
    ([[], []], 0),
    ([[], ["sig"]], 1),
    ([["sig"], ["sig"]], 1),
    ([["expires", "token"], ["token", "expires"]], 1),
    ([["sig"], ["hash"], []], 2),
])
def test_m4_an_unsigned_site_is_not_a_signing_scheme(schemes, want):
    c = ms.compare_sites({f"s{i}": _profile(signing_params=s) for i, s in enumerate(schemes)})
    assert c["distinct_signing_schemes"] == want and c["verdict"]["signing_opacity_tested_across_schemes"] == want, (
        f"BMFIX-M4-UNSIGNED-COUNTED: {schemes} -> {c['distinct_signing_schemes']} distinct signing schemes, expected {want}")
    assert c["signing_schemes_by_site"] == {f"s{i}": s for i, s in enumerate(schemes)}


# ── C1 ───────────────────────────────────────────────────────────────────────
class _Ran(Exception):
    pass


def _stub_cli(monkeypatch):
    loaded: list[str] = []
    monkeypatch.setattr(cli, "_load", lambda path: loaded.append(path) or {"capture": path})

    def fake_run(pairs):
        raise _Ran(sorted(pairs))

    monkeypatch.setattr(cli, "multi_site_run", fake_run)
    return loaded


def test_c1_a_repeated_site_name_is_refused_before_anything_runs(monkeypatch):
    loaded = _stub_cli(monkeypatch)
    try:
        cli.main(["run", "--site", "s", "a1", "b1", "--site", "s", "a2", "b2", "--site", "t", "a3", "b3"])
    except _Ran as ran:
        pytest.fail(f"BMFIX-C1-DUPLICATE-SITE-DROPPED: 3 sites given, run covered {ran.args[0]} with no error")
    except SystemExit as stop:
        msg = str(stop.code)
        assert "duplicate --site" in msg and "'s'" in msg and "3 sites given" in msg, f"BMFIX-C1-MESSAGE: {msg!r}"
    else:
        pytest.fail("BMFIX-C1-DUPLICATE-SITE-DROPPED: main returned normally")
    assert loaded == [], f"BMFIX-C1-LOADED-BEFORE-REFUSAL: captures were read before the refusal: {loaded}"


def test_c1_control_distinct_names_run_every_site(monkeypatch):
    loaded = _stub_cli(monkeypatch)
    with pytest.raises(_Ran) as ran:
        cli.main(["run", "--site", "s", "a1", "b1", "--site", "t", "a2", "b2"])
    assert ran.value.args[0] == ["s", "t"] and loaded == ["a1", "b1", "a2", "b2"], (
        f"BMFIX-C1-CONTROL: {ran.value.args[0]} {loaded}")


# ── r2 F1: legacy clip acts only on absolutely positioned nodes ──────────────────────────────────────────────────────
_EMPTY_CLIPS = [
    "rect(1px, 1px, 1px, 1px)",
    "rect(0, 0, 0, 0)",
    "rect(0 0 0 0)",
    "rect(10px, 5px, 20px, 5px)",
]


@pytest.mark.parametrize(
    "position",
    ["static", "relative", "sticky", " Relative ", "STATIC", "inherit", "", None, 5],
)
@pytest.mark.parametrize("clip", _EMPTY_CLIPS)
def test_f1_an_empty_clip_on_a_node_that_is_not_absolutely_positioned_is_kept(
    position, clip
):
    """cx168 F1: three visible links (static, relative, sticky) with computed clip rect(1px,1px,1px,1px) took a real
    click in a browser and were dropped by r1. A recorded position that is not absolute/fixed makes clip a no-op."""
    node = _node(clip=clip, position=position)
    assert is_off_canvas(node, VP) is False, (
        f"BMFIX-F1-VISIBLE-ANCHOR-DROPPED: position={position!r} clip={clip!r} was dropped as off-canvas"
    )


@pytest.mark.parametrize("position", ["absolute", "fixed", "ABSOLUTE", " fixed "])
@pytest.mark.parametrize("clip", _EMPTY_CLIPS)
def test_f1_control_an_empty_clip_on_an_absolutely_positioned_node_is_hidden(
    position, clip
):
    assert is_off_canvas(_node(clip=clip, position=position), VP) is True, (
        f"BMFIX-V1-HIDDEN-KEPT: position={position!r} clip={clip!r} was kept as a visible node"
    )


def test_f1_control_a_real_clip_window_is_kept_whatever_the_position():
    for position in ("absolute", "fixed", "static"):
        assert (
            is_off_canvas(_node(clip="rect(0, 160px, 60px, 0)", position=position), VP)
            is False
        ), f"BMFIX-V1-VISIBLE-DROPPED: a 160x60 clip window, position={position}"


def test_f1_a_style_that_records_no_position_is_judged_as_before():
    """Landed behaviour (tests/test_viewport_clipping_filter.py: rect(0, 0, 0, 0) with no position is clipped) and the
    finder's V1 repro (no position recorded) both stand."""
    for clip in _EMPTY_CLIPS:
        assert is_off_canvas(_node(clip=clip), VP) is True, (
            f"BMFIX-V1-HIDDEN-KEPT: {clip!r}, no position recorded"
        )


@pytest.mark.parametrize(
    "position", ["static", "relative", "sticky", "absolute", "fixed"]
)
def test_f1_clip_path_and_geometry_do_not_depend_on_position(position):
    """clip-path applies to every element; so does being outside the viewport."""
    assert (
        is_off_canvas(_node(clip_path="inset(100%)", position=position), VP) is True
    ), f"BMFIX-V1-HIDDEN-KEPT: clip-path inset(100%) with position={position}"
    gone = {
        "tag": "a",
        "style": {"position": position, "clip": "rect(1px, 1px, 1px, 1px)"},
        "rect": {"x": -500, "y": 10, "width": 100, "height": 20},
    }
    assert is_off_canvas(gone, VP) is True, (
        f"BMFIX-F1-GEOMETRY-LOST: x=-500 width=100, position={position}"
    )


def test_f1_filter_keeps_the_three_visible_links_and_drops_the_hidden_ones():
    clip = "rect(1px, 1px, 1px, 1px)"
    nodes = [
        _node(clip=clip, position=p)
        for p in ("static", "relative", "sticky", "absolute", "fixed")
    ]
    kept = filter_off_canvas(nodes, VP)
    assert kept == nodes[:3], (
        f"BMFIX-F1-VISIBLE-ANCHOR-DROPPED: kept positions {[n['style']['position'] for n in kept]}, "
        "expected static, relative, sticky"
    )


# ── r2 M1: confirmed reusable needs DIFFERENT instances ──────────────────────────────────────────────────────────────
def _with(ids_by_class, **over):
    return _profile(
        reusable_classes=list(ids_by_class),
        reusable_class_instances=dict(ids_by_class),
        **over,
    )


def test_m1_the_same_instances_on_two_sites_do_not_confirm_a_reusable_class():
    """Finder repro: two sites, the same class -> "confirmed reusable", with nothing compared but the class name."""
    c = ms.compare_sites(
        {
            "a": _with({"cdn_shard": ["x1", "x2"]}),
            "b": _with({"cdn_shard": ["x2", "x1"]}),
        }
    )
    assert c["reusable_classes_reproduced"] == [
        "cdn_shard"
    ]  # seen on both: still reported
    assert (
        "cdn_shard" not in c["verdict"]["confirmed_reusable"]
        and c["reusable_classes_confirmed"] == []
    ), f"BMFIX-M1-SAME-INSTANCES-CONFIRMED: {c['verdict']['confirmed_reusable']}"
    assert (
        c["reusable_classes_same_instances"]
        == ["cdn_shard"]
        == c["verdict"]["reproduced_same_instances"]
    )
    assert c["reusable_class_instances"]["cdn_shard"] == {
        "decision": "same",
        "instances_by_site": {"a": ["x1", "x2"], "b": ["x1", "x2"]},
    }


def test_m1_different_instances_on_two_sites_confirm_the_class():
    c = ms.compare_sites(
        {
            "a": _with({"cdn_shard": ["x1"], "only_a": ["q"]}),
            "b": _with({"cdn_shard": ["y9"]}),
            "c": _with({"cdn_shard": ["x1"]}),
        }
    )
    assert c["reusable_classes_confirmed"] == ["cdn_shard"], (
        f"BMFIX-M1-DIFFERENT-NOT-CONFIRMED: {c}"
    )
    assert c["verdict"]["confirmed_reusable"] == [
        "cdn_shard",
        "sensitivity:assume:goal_selection",
    ]
    assert c["reusable_class_instances"]["cdn_shard"]["decision"] == "different"
    assert (
        c["reusable_classes_site_local"] == ["only_a"]
        and "only_a" not in c["reusable_class_instances"]
    )
    assert (
        c["reusable_classes_undecidable"] == [] == c["verdict"]["undecidable_reusable"]
    )


def test_m1_a_site_without_the_class_takes_no_part_in_its_decision():
    """Only the sites that HAVE the class are compared: a third site that never showed it names no instances of it,
    and that is not a reason to call the class undecidable."""
    c = ms.compare_sites({"a": _with({"cdn_shard": ["x1"]}), "b": _with({"cdn_shard": ["y9"]}), "c": _with({"other": ["o"]})})
    assert c["reusable_class_instances"]["cdn_shard"] == {
        "decision": "different", "instances_by_site": {"a": ["x1"], "b": ["y9"]}}, (
        f"BMFIX-M1-FOREIGN-SITE-COMPARED: {c['reusable_class_instances']}")
    assert c["reusable_classes_confirmed"] == ["cdn_shard"] and c["reusable_classes_site_local"] == ["other"]


@pytest.mark.parametrize("b_instances", [None, [], "absent", 3, "x1"])
def test_m1_a_site_that_names_no_instances_leaves_the_class_undecidable(b_instances):
    """PM ruling: ids when present, else 'undecidable' -- never confirmed from the count alone. Covers count-only
    workbench output (no key / None), an empty id list, and a value that is not a list of ids."""
    b = _with({"cdn_shard": b_instances})
    if b_instances == "absent":
        del b[
            "reusable_class_instances"
        ]  # a profile made before ids existed (the landed tests' shape)
    c = ms.compare_sites({"a": _with({"cdn_shard": ["x1"]}), "b": b})
    assert c["reusable_classes_reproduced"] == ["cdn_shard"]
    assert (
        c["reusable_classes_undecidable"]
        == ["cdn_shard"]
        == c["verdict"]["undecidable_reusable"]
    ), f"BMFIX-M1-UNDECIDABLE-DECIDED: {c['reusable_class_instances']}"
    assert "cdn_shard" not in c["verdict"]["confirmed_reusable"], (
        "BMFIX-M1-COUNT-ONLY-CONFIRMED"
    )
    assert c["reusable_classes_confirmed"] == [] == c["reusable_classes_same_instances"]


def test_m1_site_profile_reads_ids_beside_the_count_and_none_without_them():
    gen = {
        "reusable_classes": [
            {"class": "with_ids", "instances": 2, "instance_ids": ["b", "a"]},
            {"class": "count_only", "instances": 3},
            {"class": "not_a_list", "instances": 1, "instance_ids": 1},
        ]
    }
    p = ms.site_profile({"generalization": gen})
    assert p["reusable_classes"] == [
        "with_ids",
        "count_only",
        "not_a_list",
    ]  # the landed key is unchanged
    assert p["reusable_class_instances"] == {
        "with_ids": ["a", "b"],
        "count_only": None,
        "not_a_list": None,
    }, f"BMFIX-M1-PROFILE: {p['reusable_class_instances']}"


def _entry(seq, url, body=None):
    return {
        "seq": seq,
        "iso": "2026-01-01T00:00:00Z",
        "timestamp": seq * 1000,
        "type": "xhr",
        "method": "GET",
        "url": url,
        "request_headers": [],
        "request_body": None,
        "response_status": 200,
        "response_status_text": "OK",
        "response_headers": [
            {
                "name": "Content-Type",
                "value": "application/json" if body else "text/plain",
            }
        ],
        "response_body": body,
        "response_body_truncated": False,
        "response_body_skipped_reason": None,
        "duration_ms": 5,
        "error": None,
    }


def _capture(host, sign, vid, sess, tok, exp):
    """The landed workbench test's capture shape (tests/test_v3_66_60_capture_workbench.py), with the host and the
    NAME of the signing parameter as arguments so two sites can differ in them."""
    body = '{"video_id":"%s","session":"%s","playlist":"/play/%s/master.m3u8"}' % (
        vid,
        sess,
        vid,
    )
    return {
        "capture_version": 1,
        "host": host,
        "url": f"https://{host}/watch?v={vid}",
        "origin": f"https://{host}",
        "pathname": "/watch",
        "search": f"?v={vid}",
        "title": "watch",
        "user_agent": "ua",
        "cookies": "<scrubbed>",
        "network_log": [
            _entry(0, f"https://{host}/api/config?v={vid}", body),
            _entry(
                1,
                f"https://cdn.{host}/play/{vid}/master.m3u8"
                f"?session={sess}&{sign}={tok}&expires={exp}&_=1{exp}",
            ),
        ],
    }


def _pair(host, sign):
    return (
        _capture(host, sign, "vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000"),
        _capture(host, sign, "vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999"),
    )


def test_m1_the_producer_emits_one_id_per_counted_instance_and_never_a_value():
    a, b = _pair("example.test", "token")
    gen = workbench.build_workbench(synthesize(a, b), captures=(a, b)).to_dict()[
        "generalization"
    ]
    classes = {c["class"]: c for c in gen["reusable_classes"]}
    assert classes, "COULD NOT LOOK: this capture pair produced no reusable class"
    for name, c in classes.items():
        ids = c.get("instance_ids")
        assert (
            isinstance(ids, list)
            and ids == sorted(ids)
            and all(isinstance(i, str) for i in ids)
        ), f"BMFIX-M1-NO-INSTANCE-IDS: {name}: {ids!r}"
        assert len(ids) == c["instances"], (
            f"BMFIX-M1-IDS-NOT-THE-COUNT: {name}: {c['instances']} counted, ids {ids}"
        )
    assert "token" in classes["signing_marker_policy"]["instance_ids"]
    assert "tokSIGN" not in str(gen["reusable_classes"]), (
        "BMFIX-M1-VALUE-LEAK: a signing VALUE is in the ids"
    )
    goal = [c for n, c in classes.items() if n.startswith("classify_url:")]
    assert goal and all(c["instance_ids"] == ["cdn.example.test"] for c in goal), goal


def test_m1_end_to_end_two_sites_are_decided_from_their_ids():
    """Through multi_site_run: different hosts -> the classifier class is confirmed; the SAME signing-marker names on
    two sites are the same instances (not confirmed), different names are different instances (confirmed)."""
    same = ms.multi_site_run(
        {"x": _pair("example.test", "token"), "y": _pair("other.test", "token")}
    )["comparison"]
    diff = ms.multi_site_run(
        {"x": _pair("example.test", "token"), "y": _pair("other.test", "sig")}
    )["comparison"]
    for c in (same, diff):
        assert "signing_marker_policy" in c["reusable_classes_reproduced"]
        assert c["reusable_classes_undecidable"] == [], (
            f"BMFIX-M1-UNDECIDABLE-DECIDED: {c['reusable_class_instances']}"
        )
        goal = [
            k for k in c["reusable_classes_reproduced"] if k.startswith("classify_url:")
        ]
        assert goal and set(goal) <= set(c["reusable_classes_confirmed"]), c[
            "reusable_class_instances"
        ]
    assert "signing_marker_policy" in same["reusable_classes_same_instances"], same[
        "reusable_class_instances"
    ]
    assert "signing_marker_policy" not in same["verdict"]["confirmed_reusable"], (
        "BMFIX-M1-SAME-INSTANCES-CONFIRMED"
    )
    assert "signing_marker_policy" in diff["verdict"]["confirmed_reusable"], (
        f"BMFIX-M1-DIFFERENT-NOT-CONFIRMED: {diff['reusable_class_instances']}"
    )


def test_m1_the_cli_prints_confirmed_same_and_undecidable_apart(monkeypatch, capsys):
    profiles = {
        "a": _with({"diff": ["1"], "same": ["s"], "blind": ["b"]}),
        "b": _with({"diff": ["2"], "same": ["s"], "blind": None}),
    }
    monkeypatch.setattr(cli, "_load", lambda path: {"capture": path})
    monkeypatch.setattr(
        cli,
        "multi_site_run",
        lambda pairs: {"profiles": profiles, "comparison": ms.compare_sites(profiles)},
    )
    cli.main(["run", "--site", "a", "a1", "a2", "--site", "b", "b1", "b2"])
    out = capsys.readouterr().out
    assert (
        "CONFIRMED reusable (reproduced >=2 sites, different instances): ['diff']"
        in out
    ), f"BMFIX-M1-CLI: {out}"
    assert (
        "SAME instances (not confirmed): ['same']" in out
        and "(undecidable): ['blind']" in out
    ), f"BMFIX-M1-CLI: {out}"
