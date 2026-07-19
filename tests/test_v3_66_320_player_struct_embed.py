"""v3.66.320 — AI-6 structural-embedding player classifier (decision-independent core).

FR-AI6's remaining work is a build cut: a structural fingerprint primitive +
a nearest-centroid classifier, validated leave-one-DOMAIN-out over the cleared
player families. This test pins the *mechanism* on a self-contained synthetic
multi-domain mini-corpus (no 200 MB extract needed), so RED->GREEN is fully
reproducible in the sandbox. The real per-family validation runs as a driver
over the consolidated corpus (separate step).

RED-first contract (proven failing before tools/player_struct_embed.py exists):
  1. ``fingerprint(html, ...)`` -> a sparse count vector of STRUCTURAL features
     only (class-prefix tokens, custom-element tags, storage-key prefixes,
     <video>/MSE flags). F2: counts/names only — never an attribute VALUE.
  2. ``cosine(a, b)`` over sparse dict vectors; ``centroid(vectors)`` = mean.
  3. ``NearestCentroidClassifier.fit/predict`` classifies by max cosine to the
     per-family centroid.
  4. ``leave_one_domain_out(samples)`` holds out each domain, trains on the
     rest, and reports overall accuracy + intra/inter mean cosine. On the
     synthetic corpus accuracy must be 1.0 and intra-cosine > inter-cosine.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

import player_struct_embed as pse  # noqa: E402


# ---- synthetic markup: 3 families x 3 distinct "domains" each ---------------
def _markup(family_sig, n_repeat, domain_tag):
    """A structural markup string carrying a family signature plus a little
    per-domain noise (a unique wrapper class) so domains are not identical."""
    blocks = []
    for i in range(n_repeat):
        blocks.append(f'<div class="{family_sig}-control {family_sig}-bar item-{i}">x</div>')
    body = "\n".join(blocks)
    return f'<html><body class="site-{domain_tag}">{body}</body></html>'


def _corpus():
    """list of (family, domain, html, storage_keys, custom_tag) — 3x3."""
    rows = []
    families = {
        "alpha": ("aa", "<alpha-player></alpha-player>", ["aa-player:bg", "aa-player:vol"]),
        "beta":  ("bb", "<beta-controller></beta-controller>", ["bb-store:x"]),
        "gamma": ("gg", "", ["gg-player:display", "gg-player:rate"]),
    }
    for fam, (sig, custom, skeys) in families.items():
        for d in (1, 2, 3):
            html = _markup(sig, 12, f"{fam}{d}").replace("</body>", custom + "</body>")
            rows.append((fam, f"{fam}{d}.example", html, skeys))
    return rows


def test_fingerprint_counts_structural_features_no_values():
    html = '<div class="vjs-control vjs-bar"></div><media-player></media-player>'
    fp = pse.fingerprint(html, storage_keys=["THEOplayer.foo"])
    # class-prefix token present as a count
    assert fp.get("cls:vjs-", 0) >= 2, fp
    # custom element captured
    assert fp.get("tag:media-player", 0) >= 1, fp
    # storage-key PREFIX, never the full key/value
    assert any(k.startswith("stk:") for k in fp), fp
    assert "stk:THEOplayer.foo" not in fp, "must store prefix shape, not the full key"


def test_cosine_and_centroid():
    a = {"x": 3.0, "y": 4.0}
    assert abs(pse.cosine(a, a) - 1.0) < 1e-9
    assert pse.cosine({"x": 1.0}, {"y": 1.0}) == 0.0
    c = pse.centroid([{"x": 2.0}, {"x": 4.0, "y": 2.0}])
    assert c["x"] == 3.0 and c["y"] == 1.0, c


def test_nearest_centroid_predicts_trained_family():
    rows = _corpus()
    samples = [(fam, pse.fingerprint(html, storage_keys=sk)) for fam, _d, html, sk in rows]
    clf = pse.NearestCentroidClassifier().fit(samples)
    # a fresh alpha-shaped sample classifies alpha
    fam, _d, html, sk = rows[0]
    assert clf.predict(pse.fingerprint(html, storage_keys=sk)) == "alpha"


def test_leave_one_domain_out_accuracy_and_separation():
    rows = _corpus()
    samples = [(fam, dom, pse.fingerprint(html, storage_keys=sk))
               for fam, dom, html, sk in rows]
    rep = pse.leave_one_domain_out(samples)
    assert rep["total"] == 9, rep
    assert rep["accuracy"] == 1.0, rep            # every held-out domain correct
    assert rep["intra_cosine"] > rep["inter_cosine"], rep  # structural separation holds
    # per-family folds recorded
    assert set(rep["per_family"]) == {"alpha", "beta", "gamma"}, rep


def test_prepare_focus_drops_generic_keeps_player_features():
    fp = pse.fingerprint(
        '<div class="vjs-control nav-bar col-6"></div><media-player></media-player>',
        storage_keys=["jwplayer.x"])
    prepped = pse.prepare(fp, focus=True, log_tf=False)
    assert "cls:vjs-" in prepped, prepped          # player namespace kept
    assert "cls:nav-" not in prepped, prepped       # generic dropped
    assert "cls:col-" not in prepped, prepped       # generic dropped
    assert "tag:media-player" in prepped, prepped   # custom element kept
    assert any(k.startswith("stk:") for k in prepped), prepped  # storage kept


def test_prepare_log_tf_dampens_counts():
    fp = {"cls:vjs-": 100.0}
    import math as _m
    prepped = pse.prepare(fp, focus=False, log_tf=True)
    assert abs(prepped["cls:vjs-"] - _m.log1p(100.0)) < 1e-9


def test_focus_isolates_player_signal_and_classifies():
    # 3 families x 3 domains; each domain carries heavy generic noise plus its
    # player namespace. The mechanism under test: focus strips the generic noise
    # (which dilutes cosine on real DOMs) and keeps the player signal, giving
    # clean leave-one-domain-out. (The real-corpus impact — 0.27 raw -> 0.64
    # focus — is recorded in the validation artifact, not asserted synthetically.)
    NOISE = " ".join(f'<div class="nav-{i} col-{i} btn-{i} wrap-{i}"></div>'
                     for i in range(20))
    fams = {"videojs": "vjs-control vjs-tech vjs-poster",
            "jwplayer": "jw-controls jw-display jw-icon",
            "plyr": "plyr__controls plyr__control plyr__poster"}
    foc_rows = []
    for fam, sig in fams.items():
        for d in (1, 2, 3):
            html = f'<html><body class="site-{fam}{d}">{NOISE}<div class="{sig}"></div></body></html>'
            raw = pse.fingerprint(html)
            # raw carries the generic dilutors...
            assert any(k.startswith("cls:nav-") for k in raw)
            prepped = pse.prepare(raw)
            # ...which focus removes, keeping only the player namespace
            assert not any(k.startswith("cls:nav-") or k.startswith("cls:col-")
                           or k.startswith("cls:wrap-") for k in prepped), prepped
            assert any(k[4:].startswith(("vjs-", "jw-", "plyr__")) for k in prepped), prepped
            foc_rows.append((fam, f"{fam}{d}.ex", prepped))
    rep = pse.leave_one_domain_out(foc_rows)
    assert rep["accuracy"] == 1.0, rep
    assert rep["intra_cosine"] > rep["inter_cosine"], rep


def test_classify_returns_family_and_margin():
    # classify() loads the baked centroids and scores a fingerprint.
    html = '<div class="vjs-control vjs-tech vjs-poster"></div><video></video>'
    res = pse.classify(html)
    assert res is None or ("family" in res and "margin" in res and "confidence" in res), res


def test_detect_exposes_struct_embed_additively():
    import player_recognition as pr  # noqa: E402
    html = '<div class="vjs-control vjs-tech"></div><video></video>'
    rec = pr.detect(html)
    # new ADDITIVE field — the rule verdict is untouched
    assert "struct_embed" in rec, "detect() must expose struct_embed"
    assert rec["player_family"] == "videojs", rec["player_family"]  # rule path unchanged
    se = rec["struct_embed"]
    assert se is None or se.get("family"), se


def test_leave_one_domain_out_holds_out_whole_domain():
    # a domain seen only in training must not leak into its own test fold:
    # with one domain held out, the classifier is trained on the OTHER domains
    # of that family — so a family with a single domain cannot be validated.
    rows = [("solo", "only.example", pse.fingerprint('<div class="zz-x"></div>'))]
    rep = pse.leave_one_domain_out(rows)
    # single-domain family is skipped (no train data when its one domain is held out)
    assert rep["total"] == 0 or rep.get("skipped_single_domain"), rep
