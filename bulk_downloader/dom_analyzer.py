"""DOM Analyzer Workbench — server core (F2.6, Track F).

Post-hoc capture inspection: load an existing capture, render its DOM as a
browsable (redacted) tree, run selector tests against it, derive a candidate
selector for a clicked node, and pin a review-only template candidate. The
REPLAY half of the dev-tools experience (F2.7 is the live half).

F2 GATE (load-bearing, fail-closed)
-----------------------------------
A capture's DOM can carry tokens / signed URLs / session values in *attributes*,
*hrefs*, and *text nodes* — places ``dom_capture.redact_dom_node`` (class-PII
subtrees + input values only) does NOT reach. So the tree this module emits is
LAYERED-redacted and PROVEN clean before it leaves the process:

    root  --redact_dom_node-->     structural  (class-PII subtree drop/mask, input values)
          --redact_artifact-->     final       (value-content secrets in attrs/href/text)
          --scan_artifact_secrets--> residual
    residual != []  =>  FAIL CLOSED: return counts only, never the tree/html.

This mirrors the standalone scrubber's refuse-to-write-on-residual posture.
A clean scan is the *proof* the rendered tree carries no secret.

This module consumes guard OUTPUTS (``dom_capture`` / ``dom_recorder`` /
``capture_session``) and never edits them. It imports only package primitives
(no ``tools/`` inversion). New file; zero guard declarations.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dom_capture import redact_dom_node, PII_MASK_CLASSES
from .dom_serialize import nodes_to_html, _MAX_DOM_DEPTH
from .capture_artifact_redact import redact_artifact, scan_artifact_secrets
from . import selector_playground as _sp

# rrweb NodeType
_NT_ELEMENT = 2
_NT_TEXT = 3

_DRAFT_SUFFIX = ".template-draft.json"
_DRAFT_SCHEMA = "bulk_downloader.template.draft.v1"
_SAFETY_NOTES = (
    "Generated from a DOM-analyzer pin over an existing capture.",
    "Review before enabling.",
    "Do not store signed one-time URLs, cookies, tokens, or challenge artifacts.",
    "This template stores selectors and reusable URL patterns only.",
)


# ── capture loading ──────────────────────────────────────────────────────────

# Capture artifacts live under these dirs (relative to the project root), the
# same set tools/capture_analytics enumerates. Resolution is basename-only
# against this enumerated set — a client never supplies a path.
#
# Phase 1 Cut 1.3: the dirs split into two classes with DIFFERENT bases:
#   * capture-OUTPUT dirs -> the configured capture store root (default
#     PROJECT_ROOT). These hold the raw capture artifacts and are relocatable.
#   * template dirs -> ALWAYS PROJECT_ROOT. They are the template review queues,
#     managed by template_manager.DRAFTS_DIR (build_draft / drift_repair /
#     app_template_manager write there), independent of the capture store.
# When no store root is configured the two bases are identical -> byte-identical.
_CAPTURE_OUTPUT_DIRS = ("captures", "offline_out", "offline_captures")
_TEMPLATE_CAPTURE_DIRS = ("templates/drafts", "templates/review_candidates")
_CAPTURE_DIRS = _CAPTURE_OUTPUT_DIRS + _TEMPLATE_CAPTURE_DIRS


def _project_root() -> Path:
    from .template_registry import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def _capture_store_root() -> Path:
    """Base for the capture-OUTPUT dirs. The `capture_store_root` app-config key
    when it is a valid ABSOLUTE EXISTING dir; otherwise PROJECT_ROOT. Falling back
    to PROJECT_ROOT on a missing/blank/invalid value means a bad config value can
    never redirect capture resolution at a nonexistent or unexpected base."""
    try:
        from . import global_config as _gc
        v = _gc.get("capture_store_root", "") or ""
        if v:
            p = Path(v)
            if p.is_absolute() and p.is_dir():
                return p
    except Exception:
        pass
    return _project_root()


def _base_for_dir(d: str, root=None) -> Path:
    """The base under which capture dir ``d`` lives. An explicit ``root`` overrides
    everything (tests + the migrator pass one). Otherwise capture-output dirs use
    the configured store root; template dirs use PROJECT_ROOT."""
    if root is not None:
        return Path(root)
    if d in _CAPTURE_OUTPUT_DIRS:
        return _capture_store_root()
    return _project_root()


def _base_for_token(token: str, root=None) -> Path:
    """Pick the resolution base for a rel_path token by its leading dir component:
    a capture-output token resolves under the store root, a template token under
    PROJECT_ROOT. Same routing as _base_for_dir, keyed off the token prefix."""
    if root is not None:
        return Path(root)
    lead = (token or "").replace("\\", "/").split("/", 1)[0]
    if lead in _CAPTURE_OUTPUT_DIRS:
        return _capture_store_root()
    return _project_root()


def list_captures(root=None) -> List[Dict[str, Any]]:
    """Enumerate available capture artifacts (``*.wacz`` / ``capture_*.json``)
    under the known capture dirs. Returns ``[{name, dir, size, kind}]`` sorted
    by name. ``name`` is the basename — the only token a caller may pass back
    to :func:`resolve_capture`."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for d in _CAPTURE_DIRS:
        dd = _base_for_dir(d, root) / d
        if not dd.is_dir():
            continue
        for pat, kind in (("*.wacz", "wacz"), ("capture_*.json", "json")):
            for fp in sorted(dd.glob(pat)):
                if fp.name in seen:
                    continue
                seen.add(fp.name)
                try:
                    size = fp.stat().st_size
                except OSError:
                    size = -1
                out.append({"name": fp.name, "dir": d, "size": size, "kind": kind})
    return sorted(out, key=lambda x: x["name"])


def resolve_capture(name: str, root=None) -> Optional[Path]:
    """Resolve a capture BASENAME to a real path — only if it is an enumerated
    capture. Returns ``None`` for anything unrecognised. Rejects path
    separators / ``..`` / absolute paths up front so a client can never escape
    the capture dirs (path-traversal guard)."""
    if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        return None
    # Pass root through so list_captures uses the two-base model (store root for
    # capture-output dirs, PROJECT_ROOT for template dirs) unless an explicit root
    # overrides. Return the path under the dir's OWN base, not a single base.
    for cap in list_captures(root=root):
        if cap["name"] == name:
            return _base_for_dir(cap["dir"], root) / cap["dir"] / name
    return None


# ── Item 3: recursive capture SCAN + browser ─────────────────────────────────
# list_captures globs the 5 fixed dirs NON-recursively, so onboarding captures
# nested under captures/template_onboarding/<host>_<siteid>_<ts>/ are invisible.
# scan_captures walks the SAME roots recursively with cheap metadata and opens
# ZERO wacz zips (host comes from the path naming, never the archive).

import re as _re  # noqa: E402  (local to the scan block; module already light)

# {host}_{siteid}_{YYYYMMDD}[_HHMMSS][_rand] tail. Requiring a date-ish tail
# keeps a plain "x.redacted" / "flat_top" from ever reading as a host.
_CAPTURE_TS_RE = _re.compile(r"(?:^|_)(\d{8})(?:_\d{6})?(?:_[0-9a-f]{2,})?$")


def _parse_capture_host(token: str) -> Optional[str]:
    if not token or not _CAPTURE_TS_RE.search(token):
        return None
    head = token.split("_", 1)[0]
    return head if "." in head else None


def _capture_host(fp: Path) -> Optional[str]:
    """Host for a capture, parsed from the {host}_{siteid}_{ts} naming on the
    file stem (flat layout) or the parent dir (template_onboarding subdir)."""
    return _parse_capture_host(fp.stem) or _parse_capture_host(fp.parent.name)


def capture_host_from_name(name: str, root=None) -> str:
    """Host derived from a capture's FILENAME/path ({host}_{siteid}_{ts}) — the
    SAME derivation the picker/scan uses (host comes from the path naming, never
    the archive). For callers that hold only the enumerated basename. Resolves
    both flat and template_onboarding-subdir captures. Returns '' when the name
    encodes no host or isn't an enumerated capture. This is the canonical host
    source for a REDACTED capture, whose archive content carries no url."""
    p = _resolve_capture_any(name, root=root)
    return (_capture_host(p) or "") if p is not None else ""


def _is_under(child: Path, base: Path) -> bool:
    try:
        child.relative_to(base)
        return True
    except ValueError:
        return False


def scan_captures(root=None, limit=None) -> List[Dict[str, Any]]:
    """Recursively enumerate capture artifacts (``*.wacz`` / ``capture_*.json``)
    under the known capture roots, with cheap per-capture metadata. Opens NO zip
    (host is parsed from the path; the wacz is loaded only on a detail request).

    Each row: ``{rel_path, name, dir, host, captured_at, size, kind, redacted}``
    where ``rel_path`` is the project-root-relative subpath used as the resolve
    token. Symlinked files are skipped (no escape via a symlinked capture).
    Sorted by ``captured_at`` descending (newest first).
    """
    def _dir_mtime(pp: Path) -> float:
        try:
            return pp.stat().st_mtime
        except OSError:
            return 0.0

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for d in _CAPTURE_DIRS:
        # Two-base: capture-output dirs under the store root, template dirs under
        # PROJECT_ROOT. rel_path is relative to THIS dir's base, so a capture-output
        # token is "captures/..." (store-root-relative) and a template token is
        # "templates/drafts/..." (PROJECT_ROOT-relative) -- disjoint prefixes keep
        # tokens unambiguous, and _base_for_token routes resolution to the match.
        dir_base = _base_for_dir(d, root)
        try:
            dir_base = dir_base.resolve()
        except OSError:
            pass
        ddir = dir_base / d
        if not ddir.is_dir():
            continue
        # followlinks=False: never descend INTO a symlinked dir.
        for dirpath, _dirnames, filenames in os.walk(ddir, followlinks=False):
            dp = Path(dirpath)
            if limit is not None:
                # BUG-3/7 perf: descend newest-subdir-first and stop after `limit`
                # captures so a very large capture store can't turn the picker into
                # a multi-minute recursive walk. Mutating _dirnames steers os.walk.
                _dirnames.sort(key=lambda n: _dir_mtime(dp / n), reverse=True)
            for fn in filenames:
                is_wacz = fn.endswith(".wacz")
                is_json = fn.startswith("capture_") and fn.endswith(".json")
                if not (is_wacz or is_json):
                    continue
                fp = dp / fn
                # Skip symlinked files (no escape via a symlinked capture).
                if fp.is_symlink():
                    continue
                try:
                    rel = str(fp.relative_to(dir_base))
                except ValueError:
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    st = fp.stat()
                    size = st.st_size
                    captured_at = st.st_mtime
                except OSError:
                    size, captured_at = -1, 0.0
                kind = "wacz" if is_wacz else "json"
                redacted = fn.endswith(".redacted.wacz") or (
                    is_wacz and (dp / (fn[: -len(".wacz")] + ".redacted.wacz")).exists()
                )
                out.append({
                    "rel_path": rel,
                    "name": fn,
                    "dir": d,
                    "host": _capture_host(fp),
                    "captured_at": captured_at,
                    "size": size,
                    "kind": kind,
                    "redacted": bool(redacted),
                })
                if limit is not None and len(out) >= limit:
                    break
            if limit is not None and len(out) >= limit:
                break
        if limit is not None and len(out) >= limit:
            break
    out.sort(key=lambda x: x["captured_at"], reverse=True)
    return out


def scan_captures_summary(root=None) -> Dict[str, Any]:
    """Build the recursive inventory + a cheap summary {total, by_host, took_ms}.
    Returns the rows too so a caller can cache both in one pass."""
    import time as _time
    t0 = _time.monotonic()
    rows = scan_captures(root=root)
    by_host: Dict[str, int] = {}
    for r in rows:
        h = r.get("host") or "(unknown)"
        by_host[h] = by_host.get(h, 0) + 1
    return {
        "total": len(rows),
        "by_host": by_host,
        "took_ms": round((_time.monotonic() - t0) * 1000, 1),
        "rows": rows,
    }


def resolve_capture_token(token: str, root=None) -> Optional[Path]:
    """Resolve a project-root-relative capture subpath token to a real file —
    only if it is an enumerated capture under one of the capture roots. The
    recursive (subdir-aware) counterpart to :func:`resolve_capture`.

    Refuses absolute paths, ``..`` components, anything that resolves outside the
    project root, and any symlink (no escape via a symlinked capture).
    """
    if not token:
        return None
    norm = token.replace("\\", "/")
    if norm.startswith("/") or norm.startswith("~"):
        return None
    parts = norm.split("/")
    if ".." in parts or "" in parts[1:]:
        return None
    # Two-base: route this token to its base (store root for a capture-output
    # token, PROJECT_ROOT for a template token). The FS validation below is
    # UNCHANGED -- store_root only changes WHICH base the token is validated
    # against, never how (symlink / is_file / is_under all still enforced).
    base = _base_for_token(norm, root)
    try:
        base = base.resolve()
    except OSError:
        return None
    cand = base / norm
    # Reject a symlinked leaf outright (the escape vector) + require a real file.
    try:
        if cand.is_symlink() or not cand.is_file():
            return None
        real = cand.resolve()
    except OSError:
        return None
    if not _is_under(real, base):
        return None
    # Defense in depth: it must be one the recursive scan actually enumerates.
    # Check against the FULL two-base set (root passed through: None -> both bases).
    if norm not in {r["rel_path"] for r in scan_captures(root=root)}:
        return None
    return cand


def _resolve_capture_any(token: str, root=None) -> Optional[Path]:
    """Resolve a capture selector that may be EITHER a project-root-relative
    rel_path (subfolder-aware, from :func:`scan_captures`) OR a bare basename
    (legacy flat picker). BUG-3/7: onboarding/guided captures nest in subfolders.

    * A token containing a path separator is a rel_path -> resolve recursively.
    * A bare basename resolves against the flat dirs first (fast path); on a miss
      it falls back to a RECURSIVE basename match so a subfolder capture selected
      by bare basename (an un-rebuilt picker still sends basenames) also loads --
      but only when the basename is UNIQUE, so a client can never be steered to
      the wrong file by an ambiguous name.
    """
    if token and ("/" in token or "\\" in token):
        return resolve_capture_token(token, root=root)
    p = resolve_capture(token, root=root)
    if p is not None:
        return p
    if not token:
        return None
    matches = [r for r in scan_captures(root=root) if r.get("name") == token]
    if len(matches) == 1:
        return resolve_capture_token(matches[0]["rel_path"], root=root)
    return None


def build_draft_from_token(token: str, *, root=None, drafts_dir=None) -> Dict[str, Any]:
    """Build a REVIEW-ONLY template draft from a stored capture (resolved by
    ``token``) and write it to the drafts dir. Per-capture action behind the
    capture browser.

    Honest boundary: this only ever writes into ``templates/drafts/`` (the
    review queue) — it NEVER promotes, enables, or touches ``reviewed/``. The
    written draft is exactly what the onboarding builder would have produced.
    Returns ``{ok, draft, host}`` or ``{ok: False, error}``; never raises.
    """
    try:
        p = resolve_capture_token(token, root=root)
        if p is None:
            return {"ok": False, "error": "capture not found"}
        if not str(p).lower().endswith(".wacz"):
            return {"ok": False, "error": "not a wacz capture"}
        # build_template_from_wacz lives under tools/ (a CLI module); import it
        # lazily so the analyzer stays import-light and the tools dir is only
        # touched when this action actually runs.
        import sys as _sys
        # The builder CLI lives at <install>/tools/ -- anchor to THIS module's
        # location (package parent), never the capture root (which may be a
        # scan-only dir with no tools/).
        tools_dir = str((Path(__file__).resolve().parent.parent / "tools").resolve())
        if tools_dir not in _sys.path:
            _sys.path.insert(0, tools_dir)
        import build_template_from_wacz as _btw  # noqa: E402
        tpl = _btw.build_template(p)
        host = ((tpl.get("source") or {}).get("host") or "capture").replace(":", "_")
        if drafts_dir is None:
            from . import template_manager as _tm
            drafts_dir = _tm.DRAFTS_DIR
        out = Path(drafts_dir) / f"{host}.template-draft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(tpl, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "draft": out.name, "host": host}
    except Exception as e:  # fail-soft: a bad capture must not 500 the action
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def scrub_capture_token(token: str, *, root=None) -> Dict[str, Any]:
    """Scrub a raw ``.wacz`` capture (resolved by ``token``) to its share-ready
    ``.redacted.wacz`` twin via the capture-scrub hook. Per-capture action
    behind the capture browser.

    The hook is fail-soft and never touches the raw capture; this returns its
    status dict under ``result``. Returns ``{ok: False, error}`` for an
    unresolvable token; never raises.
    """
    try:
        p = resolve_capture_token(token, root=root)
        if p is None:
            return {"ok": False, "error": "capture not found"}
        from . import capture_scrub_hook as _csh
        res = _csh.scrub_on_capture(str(p))
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def load_capture(path) -> Dict[str, Any]:
    """Load a stored capture into a capture dict.

    Accepts a ``.wacz`` (zip carrying ``archive/capture.json`` or
    ``capture.json``) or a bare ``capture.json``. Loader semantics mirror
    ``tools/build_template_from_wacz._load_capture`` so the analyzer reads the
    same artifacts the builder does. Raises ``FileNotFoundError`` /
    ``ValueError`` rather than ``SystemExit`` (this runs in-process, not as a
    CLI).
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no such capture: {p}")
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            cap = next((n for n in names if n.endswith("archive/capture.json")), None)
            cap = cap or next((n for n in names if n.endswith("capture.json")), None)
            if not cap:
                raise ValueError(f"no capture.json found in {p}")
            return json.loads(z.read(cap))
    return json.loads(p.read_text("utf-8"))


def dom_root(capture: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the full-snapshot root DOM node from a capture's ``dom_log``.

    rrweb records the document tree as ``data.node`` on the FullSnapshot event;
    incremental mutations carry ``data.adds[].node``. The browsable tree is the
    full snapshot, so prefer the first event carrying ``data.node``; fall back
    to the first ``adds[].node`` if no full snapshot is present (a partial
    capture). Returns ``None`` for an empty / DOM-less capture (an iframe-player
    capture legitimately has 0 dom_log — the caller surfaces "no DOM", not an
    error).
    """
    for ev in capture.get("dom_log") or []:
        d = ev.get("data") if isinstance(ev, dict) else None
        if isinstance(d, dict) and isinstance(d.get("node"), dict):
            return d["node"]
    for ev in capture.get("dom_log") or []:
        d = ev.get("data") if isinstance(ev, dict) else None
        if isinstance(d, dict):
            for a in d.get("adds") or []:
                if isinstance(a, dict) and isinstance(a.get("node"), dict):
                    return a["node"]
    return None


# ── the F2 gate ──────────────────────────────────────────────────────────────

def _node_classes(node: Dict[str, Any]) -> List[str]:
    return str((node.get("attributes") or {}).get("class") or "").split()


def _mask_all_text(node: Any, _depth: int = 0) -> None:
    """In-place: replace every descendant text node's content with asterisks."""
    if not isinstance(node, dict) or _depth > _MAX_DOM_DEPTH:
        return
    if node.get("type") == _NT_TEXT and "textContent" in node:
        node["textContent"] = "*" * min(len(str(node["textContent"])), 8)
    for c in node.get("childNodes") or []:
        _mask_all_text(c, _depth + 1)


def _propagate_mask(node: Any, _depth: int = 0) -> Any:
    """Honor a PII mask class over the WHOLE subtree, not just the element's own
    text. For any element carrying a mask class (or already flagged
    ``_bd_redacted=mask`` by ``redact_dom_node``), mask every descendant text
    node. Block subtrees are already emptied by ``redact_dom_node``. Operates on
    the structural copy; returns it for chaining. Depth-bounded (deep-DOM guard)."""
    if not isinstance(node, dict) or _depth > _MAX_DOM_DEPTH:
        return node
    classes = _node_classes(node)
    if node.get("_bd_redacted") == "mask" or any(c in classes for c in PII_MASK_CLASSES):
        _mask_all_text(node)
    for c in node.get("childNodes") or []:
        _propagate_mask(c, _depth + 1)
    return node


def tree_view(name: str, *, root=None, max_depth: int = 200,
              max_children: int = 0) -> Dict[str, Any]:
    """Resolve a capture basename, load it, run the F2 gate, and return a
    depth/breadth-limited display tree (the gate still scans the FULL tree;
    only the returned display is limited). ``{ok:False, error}`` on a bad name."""
    p = _resolve_capture_any(name, root=root)
    if p is None:
        return {"ok": False, "error": "unknown capture"}
    res = redacted_dom(load_capture(p), max_depth=max_depth, max_children=max_children)
    res["capture"] = name
    return res


def analyze_capture(name: str, *, root=None) -> Dict[str, Any]:
    """Resolve + load + gate a capture by basename. Returns the gate result
    plus the resolved capture name; ``{ok:False, error}`` for a bad name."""
    p = _resolve_capture_any(name, root=root)
    if p is None:
        return {"ok": False, "error": "unknown capture"}
    res = redacted_dom(load_capture(p))
    res["capture"] = name
    # filename/path-derived host (the picker's source) so the UI can display +
    # pre-fill it for pin; redacted captures carry no url in content. '' if none.
    res["host"] = _capture_host(p) or ""
    return res


def analyze_test(name: str, selectors: List[str], *, root=None) -> Dict[str, Any]:
    """Evaluate operator selectors against a capture's GATED (redacted) DOM —
    the workbench's "selector box → matches + count". Tests against the captured
    offline DOM, NOT a live fetch (that is why this exists rather than reusing
    ``/api/playground/test``, which fetches a URL). Runs the full F2 gate first;
    a fail-closed capture yields no html to test against. Returns
    ``{ok, results:[{selector, count, sample, ...}]}``."""
    p = _resolve_capture_any(name, root=root)
    if p is None:
        return {"ok": False, "error": "unknown capture"}
    gate = redacted_dom(load_capture(p))
    if not gate.get("ok"):
        return {"ok": False, "error": "redaction gate held; no DOM to test",
                "residual_kinds": gate.get("residual_kinds", {})}
    if not gate.get("has_dom"):
        return {"ok": True, "results": [], "note": "capture has no DOM"}
    return {"ok": True, "capture": name,
            "results": test_selectors(gate.get("html") or "", list(selectors or []))}


def redacted_dom(capture: Dict[str, Any], *, max_depth: int = 200,
                 max_children: int = 0) -> Dict[str, Any]:
    """Produce the layered-redacted, proven-clean DOM for the workbench.

    Returns a dict:
      * ``ok``            — True iff a clean tree could be emitted.
      * ``has_dom``       — False for a DOM-less (e.g. iframe-player) capture.
      * ``residual_count``/``residual_kinds`` — proof. On a clean emit both are
        0 / {} ; on a fail-closed they carry COUNTS BY KIND only (never values,
        never paths-with-values), so the gate surfaces *that* something leaked
        without re-leaking it.
      * ``tree``/``html`` — present only when ``ok``; ``None`` on fail-closed.
        ``max_depth``/``max_children`` limit only the returned ``tree`` display;
        the scan and ``html`` always cover the FULL tree.
    """
    root = dom_root(capture)
    if root is None:
        return {"ok": True, "has_dom": False, "residual_count": 0,
                "residual_kinds": {}, "tree": None, "html": None,
                "note": "capture has no DOM snapshot (iframe player or aborted capture)"}

    # Layer 1 — structural class-PII + input-value redaction (recursive).
    structural = redact_dom_node(root)
    # Layer 1b — propagate mask intent to DESCENDANT text nodes. redact_dom_node
    # masks a mask-classed element's OWN textContent/value, but rrweb-serialized
    # text lives in child text nodes; a non-secret-shaped PII value under bd-mask
    # (a plaintext username) would otherwise survive layer 1 AND layer 2. The
    # workbench must honor the mask fully, regardless of whether rrweb masked it
    # at record time. Sink-side only; the SoT redact_dom_node is untouched.
    structural = _propagate_mask(structural)
    # Layer 2 — value-content secrets anywhere (attrs / href / text).
    final = redact_artifact(structural)
    # Proof — scan exactly what we are about to emit (the FULL tree).
    residual: List[Tuple[str, str]] = scan_artifact_secrets(final)
    if residual:
        kinds = Counter(kind for _path, kind in residual)
        return {"ok": False, "has_dom": True,
                "residual_count": len(residual),
                "residual_kinds": dict(kinds),
                "tree": None, "html": None,
                "note": "redaction gate FAILED CLOSED: residual secret(s) detected; "
                        "tree withheld. Counts by kind only."}

    return {"ok": True, "has_dom": True, "residual_count": 0,
            "residual_kinds": {},
            "tree": _to_display_tree(final, _max_depth=max_depth, _max_children=max_children),
            "html": nodes_to_html(final)}


def _to_display_tree(node: Any, _depth: int = 0, _max_depth: int = 200,
                     _max_children: int = 0) -> Optional[Dict[str, Any]]:
    """Compact, display-oriented view of an (already-redacted) node tree.

    The input is post-gate, so every value here is already redacted; this only
    trims bulk for the SPA tree renderer. Text is previewed (<=120 chars).
    ``_max_children`` > 0 caps siblings per node (0 = unlimited); ``_max_depth``
    caps nesting. A trimmed node carries ``truncated: True``.
    """
    if not isinstance(node, dict) or _depth > _max_depth:
        return None
    ntype = node.get("type")
    if ntype == _NT_TEXT:
        txt = str(node.get("textContent") or "")
        if not txt.strip():
            return None
        return {"type": "text", "text": txt[:120]}
    raw_children = node.get("childNodes") or []
    trimmed = False
    if _max_children and len(raw_children) > _max_children:
        raw_children = raw_children[:_max_children]
        trimmed = True
    children = [c for c in (
        _to_display_tree(ch, _depth + 1, _max_depth, _max_children) for ch in raw_children
    ) if c is not None]
    if ntype == _NT_ELEMENT:
        attrs = {k: v for k, v in (node.get("attributes") or {}).items()}
        out = {
            "type": "element",
            "tag": str(node.get("tagName") or "").lower(),
            "id": attrs.get("id"),
            "classes": str(attrs.get("class") or "").split() if attrs.get("class") else [],
            "attrs": attrs,
            "redacted": node.get("_bd_redacted"),
            "children": children,
        }
        if trimmed:
            out["truncated"] = True
        return out
    # document / doctype / comment wrapper — flatten to its children
    if children:
        out = {"type": "fragment", "children": children}
        if trimmed:
            out["truncated"] = True
        return out
    return None


def capture_host(capture: Dict[str, Any]) -> str:
    """Best-effort host for a capture (for pin's draft filename) from its
    page-context url. Empty string if unknown."""
    import urllib.parse as _u
    url = capture.get("url") or capture.get("page_url") or ""
    try:
        return _u.urlsplit(url).netloc or ""
    except Exception:
        return ""


# ── click → candidate selector ───────────────────────────────────────────────

_HASHY_ID = re.compile(r"(?:[0-9a-f]{6,}|\d{4,}|--|__\d)", re.I)
_VOLATILE_CLASS = re.compile(
    r"^(?:js|is|has|u|ng|v|x|aos)-|^(?:fade|sr-only|visually-hidden|active|open|"
    r"show|hidden|selected|disabled)$|[0-9a-f]{5,}", re.I)


def _stable_id(elid: Optional[str]) -> bool:
    return bool(elid) and not _HASHY_ID.search(elid or "")


def _stable_classes(class_attr: Optional[str]) -> List[str]:
    return [c for c in str(class_attr or "").split() if c and not _VOLATILE_CLASS.search(c)]


def candidate_selector_for(node: Dict[str, Any]) -> Dict[str, Any]:
    """Derive a candidate CSS selector for a clicked element node.

    Preference order (most→least stable): stable ``#id`` · ``[name=…]`` ·
    distinctive ``[data-*=…]`` · ``input[type=…]`` · ``tag.stable-class`` ·
    bare ``tag``. Returns ``{selector, basis}``. Self-contained (mirrors the
    documented ``selector_learning`` / ``cross_site_selectors`` heuristics
    without importing their privates). The selector is a SHAPE — structure, not
    a secret — and is re-validated by the caller via ``evaluate_selectors``.
    """
    attrs = {k: v for k, v in (node.get("attributes") or {}).items()}
    tag = str(node.get("tagName") or "").lower() or "*"
    elid = attrs.get("id")
    if _stable_id(elid):
        return {"selector": f"{tag}#{elid}", "basis": "id"}
    name = attrs.get("name")
    if isinstance(name, str) and name and not _HASHY_ID.search(name):
        return {"selector": f'{tag}[name="{name}"]', "basis": "name"}
    for k, v in attrs.items():
        if k.startswith("data-") and isinstance(v, str) and v and not _HASHY_ID.search(v):
            return {"selector": f'{tag}[{k}="{v}"]', "basis": "data-attr"}
    if tag == "input" and isinstance(attrs.get("type"), str) and attrs.get("type"):
        return {"selector": f'input[type="{attrs["type"]}"]', "basis": "input-type"}
    sc = _stable_classes(attrs.get("class"))
    if sc:
        return {"selector": tag + "." + ".".join(sc[:2]), "basis": "class"}
    return {"selector": tag, "basis": "tag"}


def test_selectors(html: str, selectors: List[str]) -> List[Dict[str, Any]]:
    """Thin wrapper over ``selector_playground.evaluate_selectors`` — run
    operator selectors against the (redacted) workbench HTML. Delegates to the
    same engine the existing ``/api/playground/test`` route uses, so behaviour
    is identical."""
    return _sp.evaluate_selectors(html or "", list(selectors or []))


# ── pin → review-only draft ──────────────────────────────────────────────────

def _safe_host(host: str) -> str:
    h = re.sub(r"[^A-Za-z0-9._-]", "_", str(host or "").strip().lower())
    return h or "unknown_host"


def pin_candidate(selector: str, role: str, *, host: str,
                  drafts_dir, name: str = "target",
                  capture_name: Optional[str] = None) -> Dict[str, Any]:
    """Pin a workbench selector as a REVIEW-ONLY template candidate.

    Writes ``<drafts_dir>/<host>.template-draft.json`` with
    ``status="draft_review_required"`` and ``review_required=True`` — so
    ``enabled`` (computed as ``status=="enabled"``) is always False and the
    only path to enabling it is the operator's explicit ``promote_draft``.
    Merges into an existing analyzer draft for the same host rather than
    clobbering. The whole draft passes through ``redact_artifact`` before write
    as defence-in-depth (a selector is a shape, but the chokepoint is
    unconditional). Returns ``{ok, file, status, enabled, selectors}``.
    """
    dd = Path(drafts_dir)
    dd.mkdir(parents=True, exist_ok=True)
    fp = dd / f"{_safe_host(host)}{_DRAFT_SUFFIX}"

    draft: Dict[str, Any]
    if fp.is_file():
        try:
            draft = json.loads(fp.read_text("utf-8"))
        except Exception:
            draft = {}
    else:
        draft = {}
    if not isinstance(draft, dict) or not draft:
        draft = {
            "schema": _DRAFT_SCHEMA,
            "status": "draft_review_required",
            "safety_notes": list(_SAFETY_NOTES),
            "host": str(host),
            "confidence": "review",
            "origin": "dom_analyzer_pin",
            "selectors": {},
        }
    # NEVER allow a pin to set/keep an enabled status.
    draft["status"] = "draft_review_required"
    draft["review_required"] = True
    if capture_name:
        draft["source_capture"] = str(capture_name)
    sels = draft.setdefault("selectors", {})
    sels.setdefault(str(role or "target"), {})[str(name or "target")] = str(selector)

    draft = redact_artifact(draft)  # unconditional chokepoint

    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(fp)
    return {"ok": True, "file": fp.name, "status": draft["status"],
            "enabled": draft["status"] == "enabled", "selectors": draft["selectors"]}
