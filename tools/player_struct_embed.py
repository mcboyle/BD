#!/usr/bin/env python3
"""player_struct_embed.py — AI-6 structural-embedding player classifier.

FR-AI6's remaining work (the corpus-volume gate having cleared at 17/17) is this
build cut: a **structural fingerprint** primitive + a **nearest-centroid cosine
classifier**, validated **leave-one-DOMAIN-out** over the cleared player
families. It is a complement to (not a replacement for) the rule-based
``player_recognition.detect`` — vidstack and every other family stay rule-based;
this layer measures whether a family's *structure* generalizes across distinct
domains, and gives a confidence/tie-break signal that can later be wired in.

DESIGN
------
* **Fingerprint = a sparse count vector of STRUCTURE only** (F2-safe — names,
  shapes, and counts; never an attribute VALUE, token, or signed URL):
    - ``cls:<prefix>``   class-token prefix histogram (``vjs-``, ``plyr__`` …)
    - ``tag:<name>``     custom-element tag histogram (``media-player`` …)
    - ``stk:<prefix>``   localStorage/sessionStorage KEY-prefix histogram
                         (``THEOplayer``, ``bitmovinplayer``, ``vds-player`` …)
    - ``js:<host>``      <script src> registrable-host histogram
    - ``flag:video|hls|dash``  presence flags (``<video>``, ``.m3u8``, ``.mpd``)
* **Classifier** = per-family mean vector (centroid); predict = argmax cosine.
* **Validation** = leave-one-DOMAIN-out: a family is validatable only with >=2
  distinct domains (the binding metric per the corpus KB — recaptures of one
  domain teach that site, not the family). Single-domain families are skipped.

Pure stdlib (no numpy/sklearn) so it runs under the deterministic, offline
chain interpreter exactly like the other CLIs.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

Vector = Dict[str, float]

# leading alnum run + its first separator ('-', '_', or '__')
_CLS_PREFIX = re.compile(r"^([a-z0-9]+(?:__|[-_]))", re.I)
_CLASS_ATTR = re.compile(r'class\s*=\s*"([^"]*)"', re.I)
_CUSTOM_TAG = re.compile(r"<([a-z][a-z0-9]*-[a-z0-9-]+)", re.I)
_STK_SPLIT = re.compile(r"[:._\-]")


def _class_prefix(token: str) -> str:
    m = _CLS_PREFIX.match(token)
    return m.group(1) if m else token


def _registrable(host: str) -> str:
    h = (host or "").lower().replace("www.", "")
    parts = [p for p in h.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def _host_of(src: str) -> str:
    s = src or ""
    m = re.match(r"https?://([^/]+)", s, re.I)
    if m:
        return _registrable(m.group(1))
    m = re.match(r"//([^/]+)", s)
    if m:
        return _registrable(m.group(1))
    return ""


def fingerprint(html: str,
                *,
                script_srcs: Optional[List[str]] = None,
                storage_keys: Optional[List[str]] = None,
                network: Optional[List[dict]] = None) -> Vector:
    """Structural fingerprint of one capture. Counts/names/shapes only — no
    attribute values, tokens, or URLs are ever stored."""
    fp: Vector = defaultdict(float)
    html = html or ""

    # class-token prefix histogram
    for attr in _CLASS_ATTR.findall(html):
        for tok in attr.split():
            if tok:
                fp["cls:" + _class_prefix(tok)] += 1.0

    # custom-element tag histogram
    for tag in _CUSTOM_TAG.findall(html):
        fp["tag:" + tag.lower()] += 1.0

    # storage-key PREFIX histogram (shape, never the full key/value)
    for key in (storage_keys or []):
        pref = _STK_SPLIT.split(str(key), 1)[0]
        if pref:
            fp["stk:" + pref] += 1.0

    # <script src> registrable-host histogram
    for src in (script_srcs or []):
        host = _host_of(str(src))
        if host:
            fp["js:" + host] += 1.0

    # presence flags
    if re.search(r"<video\b", html, re.I):
        fp["flag:video"] += 1.0
    urls = " ".join(str((e or {}).get("url", "")) for e in (network or []))
    if ".m3u8" in urls or ".m3u8" in html:
        fp["flag:hls"] += 1.0
    if ".mpd" in urls or ".mpd" in html:
        fp["flag:dash"] += 1.0

    return dict(fp)


# Brand class-namespace prefixes from the recognizer's §8 tells. Restricting
# class features to these (the ``focus`` transform) is what makes the structural
# embedding separate: on a real 1 MB DOM the family-distinguishing tokens are
# otherwise swamped by generic site classes (nav-/btn-/col-/…), collapsing the
# cosine. tag:/stk:/js:/flag: features are inherently player-relevant and always
# kept. (NOTE: native_custom has NO brand namespace by definition — a structural
# embedding cannot cluster the *absence* of a signature; iframe-embed families
# like youtube likewise carry no class namespace.)
PLAYER_NS = (
    "vjs-", "video-js", "jw-", "jwplayer", "jw_", "plyr__", "plyr-",
    "mejs__", "mejs-", "fp-", "art-", "vds-", "rmp-", "shaka-", "dplayer-",
    "pjs", "theo-", "bmpui-", "bitmovin", "clappr", "dash-", "brid",
)


def prepare(fp: Vector, *, focus: bool = True, log_tf: bool = True) -> Vector:
    """Transform a raw fingerprint for classification.

    ``focus``  — keep all tag:/stk:/js:/flag: features but restrict cls:
                 features to the PLAYER_NS brand namespaces (drops generic
                 site-markup noise). This is the lever that recovers
                 separation on real captures.
    ``log_tf`` — dampen high counts with log1p so a few dominant tokens don't
                 overwhelm the cosine.
    """
    out: Vector = {}
    for k, v in fp.items():
        if focus and k.startswith("cls:"):
            pref = k[4:]
            if not any(pref.startswith(p) for p in PLAYER_NS):
                continue
        out[k] = math.log1p(v) if log_tf else v
    return out


def cosine(a: Vector, b: Vector) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def centroid(vectors: List[Vector]) -> Vector:
    n = len(vectors)
    if n == 0:
        return {}
    acc: Vector = defaultdict(float)
    for v in vectors:
        for k, val in v.items():
            acc[k] += val
    return {k: val / n for k, val in acc.items()}


class NearestCentroidClassifier:
    """Per-family mean-vector classifier; predict = argmax cosine to centroid."""

    def __init__(self) -> None:
        self.centroids: Dict[str, Vector] = {}

    def fit(self, samples: List[Tuple[str, Vector]]) -> "NearestCentroidClassifier":
        groups: Dict[str, List[Vector]] = defaultdict(list)
        for fam, vec in samples:
            groups[fam].append(vec)
        self.centroids = {fam: centroid(vs) for fam, vs in groups.items()}
        return self

    def scores(self, vec: Vector) -> Dict[str, float]:
        return {fam: cosine(vec, c) for fam, c in self.centroids.items()}

    def predict(self, vec: Vector) -> Optional[str]:
        sc = self.scores(vec)
        if not sc:
            return None
        # deterministic: highest cosine, ties broken by family name
        return sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _separation(samples: List[Tuple[str, str, Vector]]) -> Tuple[float, float]:
    """(intra, inter) mean cosine. Intra = same family, DIFFERENT domain pairs;
    inter = different family pairs."""
    intra: List[float] = []
    inter: List[float] = []
    for i in range(len(samples)):
        fi, di, vi = samples[i]
        for j in range(i + 1, len(samples)):
            fj, dj, vj = samples[j]
            c = cosine(vi, vj)
            if fi == fj:
                if di != dj:
                    intra.append(c)
            else:
                inter.append(c)
    im = sum(intra) / len(intra) if intra else 0.0
    em = sum(inter) / len(inter) if inter else 0.0
    return im, em


def leave_one_domain_out(samples: List[Tuple[str, str, Vector]]) -> dict:
    """Hold out each distinct domain, train on the rest, predict the held-out
    captures. A family with a single domain is unvalidatable (its only domain,
    when held out, leaves it with no training centroid) -> those captures are
    skipped and ``skipped_single_domain`` is set.

    Returns: total, correct, accuracy, per_family{fam:{total,correct,accuracy}},
    intra_cosine, inter_cosine, skipped_single_domain.
    """
    domains = sorted({d for _f, d, _v in samples})
    total = correct = 0
    per_family: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "correct": 0})
    skipped = False

    for hd in domains:
        train = [(f, v) for f, d, v in samples if d != hd]
        held = [(f, v) for f, d, v in samples if d == hd]
        if not train:
            skipped = True
            continue
        train_fams = {f for f, _v in train}
        clf = NearestCentroidClassifier().fit(train)
        for f, v in held:
            if f not in train_fams:        # family had no other domain
                skipped = True
                continue
            pred = clf.predict(v)
            total += 1
            per_family[f]["total"] += 1
            if pred == f:
                correct += 1
                per_family[f]["correct"] += 1

    intra, inter = _separation(samples)
    pf = {f: {**c, "accuracy": (c["correct"] / c["total"] if c["total"] else 0.0)}
          for f, c in per_family.items()}
    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "per_family": pf,
        "intra_cosine": intra,
        "inter_cosine": inter,
        "skipped_single_domain": skipped,
    }


# ------------------------------------------------- baked-model classify -----
_CENTROIDS_CACHE: Optional[Dict[str, Vector]] = None


def _centroids_path() -> str:
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "player_struct_centroids.json")


def load_centroids(path: Optional[str] = None) -> Dict[str, Vector]:
    """Load the baked per-family centroids (trained over the consolidated
    corpus, media_chrome excluded). Cached. ``_meta`` keys are stripped."""
    global _CENTROIDS_CACHE
    if path is None and _CENTROIDS_CACHE is not None:
        return _CENTROIDS_CACHE
    import json
    with open(path or _centroids_path(), "r", encoding="utf-8") as f:
        raw = json.load(f)
    cents = {k: v for k, v in raw.items() if not k.startswith("_")}
    if path is None:
        _CENTROIDS_CACHE = cents
    return cents


def classify(html: str,
             *,
             script_srcs: Optional[List[str]] = None,
             storage_keys: Optional[List[str]] = None,
             network: Optional[List[dict]] = None,
             centroids: Optional[Dict[str, Vector]] = None) -> Optional[dict]:
    """Structural-embedding verdict for one page (non-authoritative — a
    complement to the rule recognizer). Returns ``None`` when the page has no
    player-namespace structure to embed (e.g. native_custom / iframe-embed) or
    the model is unavailable. Otherwise: family, cosine score, margin over the
    runner-up, and a coarse confidence band."""
    try:
        cents = centroids if centroids is not None else load_centroids()
    except Exception:
        return None
    if not cents:
        return None
    fp = prepare(fingerprint(html, script_srcs=script_srcs,
                             storage_keys=storage_keys, network=network))
    if not fp:
        return None
    ranked = sorted(((cosine(fp, c), fam) for fam, c in cents.items()),
                    key=lambda t: (-t[0], t[1]))
    top_s, top_f = ranked[0]
    if top_s <= 0.0:
        return None
    second_s = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = top_s - second_s
    conf = ("high" if (top_s >= 0.5 and margin >= 0.15)
            else "medium" if top_s >= 0.3 else "low")
    return {
        "family": top_f,
        "score": round(top_s, 3),
        "margin": round(margin, 3),
        "confidence": conf,
        "runner_up": ranked[1][1] if len(ranked) > 1 else None,
    }


# ----------------------------------------------------------------- CLI -------
def _load_labelled(path: str) -> List[Tuple[str, str, Vector]]:
    """Load a labelled corpus JSON: a list of
    {family, domain, html, storage_keys?, script_srcs?, network?}.
    Returns (family, domain, fingerprint) rows."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for e in data:
        rows.append((
            e["family"], e["domain"],
            fingerprint(e.get("html", ""),
                        script_srcs=e.get("script_srcs"),
                        storage_keys=e.get("storage_keys"),
                        network=e.get("network")),
        ))
    return rows


def main(argv=None) -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(
        prog="player_struct_embed.py",
        description="Train/validate the structural-embedding player classifier "
                    "leave-one-domain-out over a labelled corpus.")
    p.add_argument("corpus", help="labelled corpus JSON (list of {family,domain,html,...})")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = p.parse_args(argv)
    rows = _load_labelled(args.corpus)
    rep = leave_one_domain_out(rows)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(f"leave-one-domain-out: {rep['correct']}/{rep['total']} "
              f"acc={rep['accuracy']:.3f} | intra={rep['intra_cosine']:.3f} "
              f"inter={rep['inter_cosine']:.3f} "
              f"(skipped_single_domain={rep['skipped_single_domain']})")
        for fam, c in sorted(rep["per_family"].items()):
            print(f"  {fam:24s} {c['correct']}/{c['total']} acc={c['accuracy']:.3f}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
