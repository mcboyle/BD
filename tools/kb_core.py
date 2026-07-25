#!/usr/bin/env python3
"""
kb_core.py — shared core for KB tooling (consolidation).

One docs-tree walk + read (`collect`), then link-checking, duplicate detection, and
staleness all operate over that single cached set — so `kb_audit` walks the docs
tree ONCE instead of three times. Standalone wrappers still call `collect` once
each, preserving their existing outputs.

Public surface (shapes match the existing wrappers byte-for-byte):
  collect(root)                 -> {"root","files","texts"}   (the single walk+read)
  links(collected)             -> {"docs_scanned","links_checked","broken","broken_count"}
  duplicates(collected, thr)   -> {"docs","threshold","duplicate_pairs","pair_count"}
  staleness(collected, drift=) -> {"current_version","archival_candidates",
                                   "archival_count","stale_version_refs"}

Read-only; never writes or persists anything.
"""
import glob
import os
import re

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_WORD = re.compile(r"[a-z0-9]{4,}")
_VER = re.compile(r"v?(\d+\.\d+\.\d+)")
_PATS = ["*.md", "docs/*.md", "docs/**/*.md"]


def collect(root="."):
    """Single walk + read of the docs tree (superset: top-level + docs/ + docs/**)."""
    files = sorted({p for pat in _PATS
                    for p in glob.glob(os.path.join(root, pat), recursive=True)})
    texts = {}
    for f in files:
        try:
            texts[f] = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            texts[f] = ""
    return {"root": root, "files": files, "texts": texts}


def _without_fenced_code(text):
    """Remove CommonMark-style fenced blocks before scanning prose links."""
    visible = []
    fence_char = None
    fence_len = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if fence_char is None:
            if match:
                marker = match.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
            else:
                visible.append(line)
            continue
        if match:
            marker = match.group(1)
            if (marker[0] == fence_char and len(marker) >= fence_len
                    and not match.group(2).strip()):
                fence_char = None
                fence_len = 0
    return "".join(visible)


def links(collected):
    root = collected["root"]
    broken, checked = [], 0
    for f in collected["files"]:
        rel = os.path.relpath(f, root)
        text = _without_fenced_code(collected["texts"].get(f, ""))
        for target in _LINK.findall(text):
            t = target.strip().split()[0]
            if t.startswith(("http://", "https://", "#", "mailto:")):
                continue
            t = t.split("#")[0]
            if not t:
                continue
            checked += 1
            cand = os.path.normpath(os.path.join(os.path.dirname(f), t))
            if not os.path.exists(cand):
                broken.append({"doc": rel, "target": t})
    return {"docs_scanned": len(collected["files"]), "links_checked": checked,
            "broken": broken, "broken_count": len(broken)}


def duplicates(collected, threshold=0.6):
    files = collected["files"]
    toks = {f: set(_WORD.findall(collected["texts"].get(f, "").lower())) for f in files}
    pairs = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            a, b = toks[files[i]], toks[files[j]]
            if not a or not b:
                continue
            jac = len(a & b) / len(a | b)
            if jac >= threshold:
                pairs.append({"a": os.path.relpath(files[i], collected["root"]),
                              "b": os.path.relpath(files[j], collected["root"]),
                              "similarity": round(jac, 3)})
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return {"docs": len(files), "threshold": threshold,
            "duplicate_pairs": pairs, "pair_count": len(pairs)}


def staleness(collected, drift=None):
    """Stale-doc detection over the top-level + docs/ subset. `drift` (a
    check_doc_drift.scan result) may be passed to avoid a duplicate scan."""
    root = collected["root"]
    if drift is None:
        import check_doc_drift as CDD  # type: ignore
        drift = CDD.scan(root)
    current = drift.get("version")
    stale_refs = []
    for f in collected["files"]:
        rel = os.path.relpath(f, root)
        parts = rel.split(os.sep)
        # original scope: top-level *.md + docs/*.md only (not docs/**)
        if not (len(parts) == 1 or (len(parts) == 2 and parts[0] == "docs")):
            continue
        for i, line in enumerate(collected["texts"].get(f, "").splitlines(), 1):
            if re.search(r"current|latest|now at|as of", line, re.I):
                for m in _VER.findall(line):
                    if current and m != current and m.startswith("3.66"):
                        stale_refs.append({"doc": rel, "line": i, "found": m,
                                           "text": line.strip()[:100]})
    return {"current_version": current,
            "archival_candidates": drift["archive_candidates"],
            "archival_count": len(drift["archive_candidates"]),
            "stale_version_refs": stale_refs[:50]}
