"""O1789 deep-bench-misc bench-fix r1 -- fixes for findings F1 F2 F3 F6 F7 F8 F9 (temporal + multi-site benchmarks).

F1  temporal _same_shape_on_other_host compared only literal segments: a slot-only template matched any
    same-length path on another host (a tracker pixel read as "re-hosted, not broken"); an empty or root-only
    path_template matched any bare-host URL.
F2  build_transition's docstring/note promised STRENGTHENED/WEAKENED/CHANGED_CATEGORY from real captures; bands and
    basis are fixed per assumption kind, so real pairs give stable/appeared/disappeared, which the summary lacked.
F3  compare_sites confirmed a framework-level property ("robust on ALL sites") from ONE site; the CLI ran with one --site.
F6  classify_churn read an unknown/None verdict as no_churn "all held"; goal None / checks None / non-dict log
    entries crashed.
F7  build_transition dropped one-sided decisions and sensitivity orderings; a decision with neither category nor
    decision crashed the sort; temporal_run(baseline_pair=()) raised IndexError.
F8  the CLIs read a .WACZ as text and took any member ending in capture.json before archive/capture.json.
F9  site_profile kept None / param-less signing entries and let a repeated class overwrite its instance ids;
    compare_sites crashed on partial profiles.

Pure-function tests: no network, no app, no DB.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from bulk_downloader import capture_template as ct
from bulk_downloader import capture_workbench as wb
from bulk_downloader import multi_site_benchmark as ms
from bulk_downloader import temporal_benchmark as tb
from bulk_downloader.capture_synth import synthesize
from tools import multi_site_benchmark as ms_cli
from tools import temporal_benchmark as tb_cli

BD_GATE_SCOPE = "module"

_CID = "53eb2252"
_RENDS = ["1280x720_60FPS.mp4", "1920x1080_60FPS.mp4", "3840x2160_60FPS_S.mp4"]
# every per-assumption outcome build_transition can report (F2): each must be a summary key
_TRANSITIONS = (
    "stable",
    "strengthened",
    "weakened",
    "changed_category",
    "appeared",
    "disappeared",
    "unknown",
)


def _entry(seq, url, body=None):
    return {
        "seq": seq,
        "url": url,
        "method": "GET",
        "request_headers": {},
        "response_headers": {},
        "response_status": 200,
        "response_body": body,
        "type": "xhr",
    }


def _capture(expires, token, session, cdn="dd.example.test", extra=()):
    log = [
        _entry(1, "https://site.test/play", body='{"session":"%s"}' % session),
        _entry(2, f"https://api.site.test/cfg?session={session}"),
    ]
    if cdn:
        log += [
            _entry(10 + i, f"https://{cdn}/{_CID}/{r}?expires={expires}&token={token}")
            for i, r in enumerate(_RENDS)
        ]
    log += [_entry(50 + i, u) for i, u in enumerate(extra)]
    return {"host": "site.test", "network_log": log}


def _pair():
    return _capture("1700000000", "TOKA", "sA"), _capture("1700009999", "TOKB", "sB")


def _template():
    a, b = _pair()
    return ct.build_template(wb.build_workbench(synthesize(a, b), captures=(a, b)))


def _churn(later):
    return tb.temporal_run(_template(), later)["churn"]


# ── F1: the cross-host probe applies the template's slot shapes ──────────────────────────────────────────────────────
def test_f1_slot_only_template_does_not_match_a_tracker_pixel():
    later = _capture(
        "1800000000", "T", "s", cdn=None, extra=["https://tracker.example/pixel/1.gif"]
    )
    churn = _churn(later)
    assert tb.CDN_CHURN not in churn["churn_categories"], (
        f"BENCHFIX-F1-TRACKER-AS-CDN-CHURN: {churn['churn_categories']} {churn['notes']}"
    )
    assert tb.BREAKAGE in churn["churn_categories"] and churn["real_drift"] is True, (
        f"BENCHFIX-F1-NOT-BREAKAGE: {churn}"
    )


def test_f1_slot_regex_mismatch_on_other_host_is_not_cdn_churn():
    later = _capture(
        "1800000000", "T", "s", cdn=None, extra=["https://cdn9.example.test/video/abc"]
    )
    churn = _churn(later)
    assert tb.CDN_CHURN not in churn["churn_categories"], (
        f"BENCHFIX-F1-SLOT-REGEX-IGNORED: /video/abc vs content_id/rendition regexes -> {churn['churn_categories']}"
    )


@pytest.mark.parametrize("path_template", ["", "goal.example", "goal.example/"])
def test_f1_template_without_segments_matches_nothing(path_template):
    template = {"goal": {"path_template": path_template, "slots": []}}
    capture = {
        "network_log": [
            _entry(1, "https://elsewhere.example/"),
            _entry(2, "https://other.example"),
        ]
    }
    got = tb._same_shape_on_other_host(template, capture)
    assert got is None, (
        f"BENCHFIX-F1-EMPTY-TEMPLATE-MATCHES: {path_template!r} matched host {got!r}"
    )


def test_f1_control_real_rehost_is_still_cdn_churn():
    churn = _churn(_capture("1800000000", "T", "s", cdn="cdn2.example.test"))
    assert (
        tb.CDN_CHURN in churn["churn_categories"]
        and tb.BREAKAGE not in churn["churn_categories"]
    ), f"BENCHFIX-F1-CONTROL: {churn['churn_categories']}"
    assert tb._same_shape_on_other_host(
        _template(), _capture("1", "T", "s", cdn="cdn2.example.test")
    ) == ("cdn2.example.test")


# ── F2: the transition report names only what the code produces, and counts every outcome ─────────────────────────
def test_f2_real_pair_summary_has_every_transition_key():
    a, b = _pair()
    res = tb.temporal_run(
        _template(), _capture("1800000000", "T", "s"), baseline_pair=(a, b)
    )
    t = res["transition"]
    assert t is not None, f"BENCHFIX-F2-NO-TRANSITION: {res['transition_status']}"
    missing = [k for k in _TRANSITIONS if k not in t["summary"]]
    assert not missing, (
        f"BENCHFIX-F2-SUMMARY-KEYS-MISSING: {missing} in {sorted(t['summary'])}"
    )
    counted = sum(t["summary"][k] for k in _TRANSITIONS)
    assert counted == len(t["assumptions"]), (
        f"BENCHFIX-F2-SUMMARY-UNDERCOUNT: {counted} counted, {len(t['assumptions'])} assumptions"
    )
    assert "inferred slot becomes observed" not in t["note"], (
        f"BENCHFIX-F2-NOTE-PROMISE: {t['note']!r}"
    )


def test_f2_docstring_names_only_producible_outcomes():
    doc = tb.__doc__
    assert "inferred assumption became observed" not in doc, (
        "BENCHFIX-F2-DOCSTRING-PROMISE: static bands promised to move"
    )
    assert "APPEARED" in doc and "DISAPPEARED" in doc, (
        "BENCHFIX-F2-DOCSTRING-OMITS-REAL-OUTCOMES"
    )


def test_f2_control_differing_bands_still_strengthen():
    def d(band):
        return {
            "assumption_stability": {
                "assumptions": [{"node": "n", "stability_band": band, "basis": "x"}]
            }
        }

    s = tb.build_transition(d("low"), d("high"))["summary"]
    assert s["strengthened"] == 1 and s["stable"] == 0, f"BENCHFIX-F2-CONTROL: {s}"


# ── F3: one site confirms no framework-level property ────────────────────────────────────────────────────────────
def _profile(**over):
    p = {
        "goal_host": "h",
        "goal_classification": "direct_file",
        "new_provider_required": False,
        "signing_params": ["token"],
        "identity_slot_count": 1,
        "rendition_slot_count": 1,
        "reusable_classes": [],
        "reusable_class_instances": {},
        "framework_level_count": 0,
        "site_specific_count": 0,
        "sensitivity_robust": ["assume:goal_selection"],
    }
    p.update(over)
    return p


def test_f3_one_site_confirms_no_framework_property():
    c = ms.compare_sites({"a": _profile()})
    assert c["sensitivity_robust_across_all"] == [], (
        f"BENCHFIX-F3-N1-FRAMEWORK-CONFIRMED: {c['sensitivity_robust_across_all']}"
    )
    assert not [
        r for r in c["verdict"]["confirmed_reusable"] if r.startswith("sensitivity:")
    ], f"BENCHFIX-F3-N1-VERDICT: {c['verdict']['confirmed_reusable']}"


class _Ran(Exception):
    pass


def test_f3_cli_refuses_one_site_before_loading(monkeypatch):
    loaded: list[str] = []
    monkeypatch.setattr(ms_cli, "_load", lambda path: loaded.append(path) or {})
    monkeypatch.setattr(
        ms_cli,
        "multi_site_run",
        lambda pairs: (_ for _ in ()).throw(_Ran(sorted(pairs))),
    )
    try:
        ms_cli.main(["run", "--site", "only", "a1", "b1"])
    except _Ran as ran:
        pytest.fail(f"BENCHFIX-F3-CLI-ONE-SITE-RAN: {ran.args[0]}")
    except SystemExit as stop:
        assert "need >=2 --site" in str(stop.code), (
            f"BENCHFIX-F3-CLI-MESSAGE: {stop.code!r}"
        )
    else:
        pytest.fail("BENCHFIX-F3-CLI-ONE-SITE-RAN: main returned normally")
    assert loaded == [], f"BENCHFIX-F3-CLI-LOADED: {loaded}"


def test_f3_control_two_agreeing_sites_confirm():
    c = ms.compare_sites({"a": _profile(), "b": _profile()})
    assert c["sensitivity_robust_across_all"] == ["assume:goal_selection"], (
        f"BENCHFIX-F3-CONTROL: {c}"
    )
    assert "sensitivity:assume:goal_selection" in c["verdict"]["confirmed_reusable"]


# ── F6: an unknown verdict is not "all held"; malformed inputs do not crash ──────────────────────────────────────────
@pytest.mark.parametrize(
    "diff",
    [{}, {"verdict": None}, {"verdict": "error"}],
    ids=["empty", "none", "error"],
)
def test_f6_unknown_verdict_is_not_no_churn(diff):
    churn = tb.classify_churn(_template(), diff, _capture("1", "T", "s"))
    assert tb.NO_CHURN not in churn["churn_categories"], (
        f"BENCHFIX-F6-UNKNOWN-AS-HELD: {diff} -> {churn}"
    )
    assert churn["real_drift"] is True and churn["expected_only"] is False, (
        f"BENCHFIX-F6-NOT-FAIL-CLOSED: {churn}"
    )


@pytest.mark.parametrize(
    "case", ["goal_none", "checks_none", "log_none_entry", "check_none_entry"]
)
def test_f6_malformed_inputs_do_not_crash(case):
    template = _template()
    if case == "goal_none":
        template["goal"] = (
            None  # a current-schema template, so migrate_template keeps it
        )
    diff = {"verdict": tb.MISSING, "checks": None if case == "checks_none" else []}
    if case == "check_none_entry":
        diff["checks"] = [None]
    capture = (
        {"network_log": [None, _entry(1, "https://x.example/a/b")]}
        if case == "log_none_entry"
        else _capture("1", "T", "s")
    )
    try:
        churn = tb.classify_churn(template, diff, capture)
    except (AttributeError, TypeError, KeyError) as exc:
        pytest.fail(f"BENCHFIX-F6-CRASH: {case}: {type(exc).__name__}: {exc}")
    assert (
        tb.BREAKAGE in churn["churn_categories"]
        or tb.CDN_CHURN in churn["churn_categories"]
    ), churn


def test_f6_control_held_is_no_churn():
    churn = _churn(_capture("1700000000", "TOKA", "sA"))
    assert (
        churn["churn_categories"] in ([tb.NO_CHURN], [tb.SIGNING_CHURN])
        and churn["real_drift"] is False
    ), f"BENCHFIX-F6-CONTROL: {churn}"


# ── F7: one-sided entries are reported, not dropped ──────────────────────────────────────────────────────────────
def _wbd(decisions=(), sens=()):
    return {
        "decision_confidence": {"decisions": list(decisions)},
        "sensitivity": {"robustness": {"by_downstream_weight": list(sens)}},
    }


def test_f7_one_sided_decision_and_ordering_are_reported():
    before = _wbd(
        [
            {"category": "both", "confidence": "high"},
            {"category": "gone", "confidence": "low"},
        ],
        [
            {"assumption": "s:both", "robust": True},
            {"assumption": "s:gone", "robust": False},
        ],
    )
    later = _wbd(
        [
            {"category": "both", "confidence": "high"},
            {"category": "new", "confidence": "medium"},
        ],
        [
            {"assumption": "s:both", "robust": True},
            {"assumption": "s:new", "robust": True},
        ],
    )
    t = tb.build_transition(before, later)
    conf = {c["decision"]: c["transition"] for c in t["confidence"]}
    sens = {s["assumption"]: s["transition"] for s in t["sensitivity"]}
    assert conf == {"both": "stable", "gone": "disappeared", "new": "appeared"}, (
        f"BENCHFIX-F7-DECISION-DROPPED: {conf}"
    )
    assert sens == {
        "s:both": "stayed_robust",
        "s:gone": "disappeared",
        "s:new": "appeared",
    }, f"BENCHFIX-F7-ORDERING-DROPPED: {sens}"


def test_f7_unnamed_decision_does_not_crash_the_sort():
    before = _wbd([{"category": "named", "confidence": "high"}, {"confidence": "low"}])
    try:
        t = tb.build_transition(
            before, _wbd([{"category": "named", "confidence": "high"}])
        )
    except TypeError as exc:
        pytest.fail(f"BENCHFIX-F7-UNNAMED-SORT-CRASH: {exc}")
    assert len(t["confidence"]) == 2, f"BENCHFIX-F7-UNNAMED-DROPPED: {t['confidence']}"


@pytest.mark.parametrize("pair", [(), ({},)], ids=["empty", "one"])
def test_f7_short_baseline_pair_is_a_clear_error(pair):
    try:
        tb.temporal_run(_template(), _capture("1", "T", "s"), baseline_pair=pair)
    except ValueError as exc:
        assert "baseline_pair must be" in str(exc), (
            f"BENCHFIX-F7-BASELINE-PAIR-UNCLEAR: {exc!r}"
        )
    except Exception as exc:  # noqa: BLE001 -- any other type is the defect
        pytest.fail(f"BENCHFIX-F7-BASELINE-PAIR-UNCLEAR: {type(exc).__name__}: {exc}")
    else:
        pytest.fail(f"BENCHFIX-F7-BASELINE-PAIR-ACCEPTED: {pair!r}")


def test_f7_cli_prints_one_sided_transitions(capsys):
    t = tb.build_transition(_wbd([{"category": "gone", "confidence": "low"}]), _wbd())
    tb_cli._print_run(
        {
            "template_host": "h",
            "diff": {"verdict": tb.HELD, "checks": []},
            "churn": {
                "verdict": tb.HELD,
                "churn_categories": [tb.NO_CHURN],
                "expected_only": True,
                "real_drift": False,
                "notes": [],
            },
            "transition": t,
            "transition_status": "complete",
        }
    )
    out = capsys.readouterr().out
    assert "gone: low -> None" in out and "disappeared=0" in out, (
        f"BENCHFIX-F7-CLI: {out!r}"
    )


# ── F8: the CLI loaders read .WACZ and prefer archive/capture.json ───────────────────────────────────────────────
def _wacz(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, body in members:
            z.writestr(name, json.dumps(body))
    return str(path)


@pytest.mark.parametrize(
    "loader", [tb_cli._load_capture, ms_cli._load], ids=["temporal", "multi_site"]
)
def test_f8_upper_case_wacz_is_read_as_a_zip(tmp_path, loader):
    p = _wacz(tmp_path / "CAP.WACZ", [("archive/capture.json", {"who": "real"})])
    try:
        got = loader(p)
    except UnicodeDecodeError as exc:
        pytest.fail(f"BENCHFIX-F8-WACZ-CASE-AS-TEXT: {exc}")
    assert got == {"who": "real"}, f"BENCHFIX-F8-WACZ-CASE: {got}"


@pytest.mark.parametrize(
    "loader", [tb_cli._load_capture, ms_cli._load], ids=["temporal", "multi_site"]
)
def test_f8_archive_capture_json_wins_over_a_suffix_decoy(tmp_path, loader):
    p = _wacz(
        tmp_path / "c.wacz",
        [
            ("pages/x_capture.json", {"who": "decoy"}),
            ("archive/capture.json", {"who": "real"}),
        ],
    )
    assert loader(p) == {"who": "real"}, f"BENCHFIX-F8-DECOY-MEMBER: {loader(p)}"
    q = _wacz(
        tmp_path / "d.wacz",
        [("x_capture.json", {"who": "decoy"}), ("capture.json", {"who": "real"})],
    )
    assert loader(q) == {"who": "real"}, f"BENCHFIX-F8-DECOY-NO-ARCHIVE: {loader(q)}"


@pytest.mark.parametrize(
    "loader", [tb_cli._load_capture, ms_cli._load], ids=["temporal", "multi_site"]
)
def test_f8_control_plain_json_and_lower_wacz(tmp_path, loader):
    j = tmp_path / "c.json"
    j.write_text(json.dumps({"who": "json"}), encoding="utf-8")
    assert loader(str(j)) == {"who": "json"}
    assert loader(
        _wacz(tmp_path / "c.wacz", [("archive/capture.json", {"who": "real"})])
    ) == {"who": "real"}


# ── F9: site_profile / compare_sites tolerate malformed entries ──────────────────────────────────────────────────
def test_f9_signing_entries_without_a_param_are_skipped():
    try:
        p = ms.site_profile(
            {
                "skeleton": {
                    "signing_params": [None, {"param": None}, {}, {"param": "token"}]
                }
            }
        )
    except AttributeError as exc:
        pytest.fail(f"BENCHFIX-F9-SIGNING-NONE-CRASH: {exc}")
    assert p["signing_params"] == ["token"], (
        f"BENCHFIX-F9-SIGNING-NONE: {p['signing_params']}"
    )


def test_f9_repeated_class_merges_instance_ids():
    gen = {
        "reusable_classes": [
            {"class": "k", "instance_ids": ["a"]},
            {"class": "k", "instance_ids": ["b"]},
            {"class": "m", "instance_ids": ["x"]},
            {"class": "m"},
        ]
    }
    inst = ms.site_profile({"generalization": gen})["reusable_class_instances"]
    assert inst["k"] == ["a", "b"], f"BENCHFIX-F9-CLASS-OVERWRITE: {inst}"
    assert inst["m"] is None, f"BENCHFIX-F9-COUNT-ONLY-MERGE: {inst}"


def test_f9_partial_profiles_compare_without_crashing():
    try:
        c = ms.compare_sites(
            {
                "a": {},
                "b": {"signing_params": [None, "token"], "reusable_classes": ["k"]},
            }
        )
    except (KeyError, TypeError) as exc:
        pytest.fail(f"BENCHFIX-F9-PARTIAL-PROFILE-CRASH: {type(exc).__name__}: {exc}")
    assert (
        c["distinct_signing_schemes"] == 1
        and c["segment_role_reproduced_all_sites"] is False
    ), c


def test_f9_control_full_profile_unchanged():
    gen = {"reusable_classes": [{"class": "k", "instance_ids": ["b", "a"]}]}
    p = ms.site_profile(
        {
            "skeleton": {"signing_params": [{"param": "token"}, {"param": "expires"}]},
            "generalization": gen,
        }
    )
    assert p["signing_params"] == ["token", "expires"] and p[
        "reusable_class_instances"
    ] == {"k": ["a", "b"]}, p
