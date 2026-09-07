#!/usr/bin/env python3
"""Rebuild a recorded page state from a capture's rrweb DOM log.

A capture stores DOM as an rrweb full snapshot plus incremental mutations, so
the page state an operator actually SAW (a modal open, a menu expanded) exists
only after the mutations are applied.  ``bulk_downloader.dom_serialize`` renders
a node tree, and ``tools/replay_validator.py`` checks that a log is replayable,
but nothing in the tree APPLIES the log.  Without that, a selector claim can
only ever be tested against the initial snapshot -- which for a single-page app
is an empty shell.

This tool replays ``full_snapshot -> incremental*`` up to a chosen ``dom_seq``
and writes the rendered HTML plus a provenance sidecar (source digest, seq
window, applied-operation counts, output digest).  The emitted document is a
RECORDED artifact: every tag, attribute and text node comes from the capture.

Scripts are pruned so a committed fixture is inert when it is served offline to
a browser, and rrweb's inlined ``_cssText`` bookkeeping is dropped; both counts
are recorded rather than silently discarded.

The tool reads a local capture file.  It never contacts a site, so it is not an
operator-only live instrument -- but it is not a gate either: the capture store
lives outside the repository, so this runs once to MAKE a fixture that tests
then read.

TWO ROUTES IN, ONE FIXTURE OUT (row 674).  The rrweb route above is the only
one that existed, and it cannot be fed at all when the capture path armed rrweb
and recorded ZERO events -- which is exactly what run 1's WACZ did.  A page
state that a browser can hand over as HTML (``page.content()`` on a live page, a
page record lifted out of a WACZ) now takes the SAME pipeline: it is parsed into
the same node tree, pruned and stripped by the same passes, and written with the
same provenance sidecar, so a fixture built either way is the same kind of
evidence and says which route made it.

Usage:
    python3 tools/build_recorded_dom_fixture.py CAPTURE.wacz --out FIXTURE.html \
        [--snapshot-index N] [--stop-seq N] [--provenance SIDECAR.json]
    python3 tools/build_recorded_dom_fixture.py PAGE.html --out FIXTURE.html \
        [--source-url URL] [--provenance SIDECAR.json]

Exit: 0 = fixture written, 2 = usage/IO error (including "this file is not a
capture"), 3 = replay integrity failure, 4 = UNKNOWN -- the file IS a capture
and rrweb recorded nothing in it, so there is no page state to rebuild.  4 is
its own code on purpose: those two answers lead to opposite actions, and
CLAUDE.md A7 costs an investigation every time a diagnostic collapses them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bulk_downloader.dom_serialize import nodes_to_html  # noqa: E402

_INERT_TAGS = {"script", "noscript"}

# rrweb NodeType, the same numbers ``dom_serialize`` reads back.
_NT_DOCUMENT = 0
_NT_ELEMENT = 2
_NT_TEXT = 3

# Inputs the page-state route claims. Everything else is a capture and goes
# through the rrweb replay; the ROUTE is decided by the input, and it is
# recorded in the provenance so a fixture never has to be guessed about.
_PAGE_SUFFIXES = {".html", ".htm"}

# HTML void elements never take children, so a parser that pushed them onto the
# open-element stack would swallow the whole rest of the document into an
# ``<img>``. Kept equal to the serializer's own set by
# ``test_row674_...::test_the_void_element_set_matches_the_serializer``, because
# two copies of a denominator drift in silence.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class CaptureUnknown(Exception):
    """The file IS a capture, and the recording it should carry is absent.

    Not the same question as "this file is not a capture", and never the same
    answer: that one is a usage error the operator fixes by naming a different
    file, while this one says the capture RAN and rrweb recorded nothing, which
    is CLAUDE.md A2's failing third state and is repaired at the capture path
    (or routed around by saving the page state and building from that).
    """


def load_capture(path: Path) -> dict[str, Any]:
    """Return the capture JSON stored inside a .wacz (or a plain .json)."""
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith("capture.json")]
        if len(names) != 1:
            raise ValueError(
                f"expected exactly one capture.json in {path}, found {len(names)}"
            )
        return json.loads(archive.read(names[0]))


def _index_nodes(node: Any, index: dict, parents: dict, parent: Any = None) -> None:
    if not isinstance(node, dict):
        return
    node_id = node.get("id")
    if node_id is not None:
        index[node_id] = node
        parents[node_id] = parent
    for child in node.get("childNodes") or []:
        _index_nodes(child, index, parents, node)


def replay_dom(
    dom_log: list,
    snapshot_index: int,
    stop_seq: int | None = None,
) -> tuple[dict, dict]:
    """Apply incremental mutations onto a full snapshot's node tree.

    Returns ``(root_node, stats)``.  ``stats`` reconciles every operation the
    log asked for against every operation actually applied, so a caller can
    refuse a reconstruction that silently dropped part of the recording.
    """
    event = dom_log[snapshot_index]
    if event.get("type") != "full_snapshot":
        raise ValueError(
            f"dom_log[{snapshot_index}] is {event.get('type')!r}, not a full_snapshot"
        )
    root = copy.deepcopy((event.get("data") or {}).get("node"))
    if not isinstance(root, dict):
        raise ValueError(f"full snapshot at {snapshot_index} carries no node tree")

    index: dict = {}
    parents: dict = {}
    _index_nodes(root, index, parents)
    stats = {
        "snapshot_index": snapshot_index,
        "snapshot_seq": event.get("dom_seq"),
        "stop_seq": stop_seq,
        "nodes_in_snapshot": len(index),
        "events_applied": 0,
        "adds_requested": 0,
        "adds_applied": 0,
        "adds_dangling": 0,
        "removes_requested": 0,
        "removes_applied": 0,
        "removes_missing": 0,
        "attributes_requested": 0,
        "attributes_applied": 0,
        "attributes_missing": 0,
        "texts_requested": 0,
        "texts_applied": 0,
        "texts_missing": 0,
    }

    for event in dom_log[snapshot_index + 1:]:
        if not isinstance(event, dict):
            continue
        seq = event.get("dom_seq")
        if stop_seq is not None and isinstance(seq, int) and seq > stop_seq:
            break
        if event.get("type") == "full_snapshot":
            break
        if event.get("type") != "incremental":
            continue
        data = event.get("data") or {}
        if data.get("source") not in (0, None):
            continue
        stats["events_applied"] += 1

        # rrweb may emit a child before its parent inside one batch, so adds are
        # a worklist drained until a pass makes no progress -- not one sweep.
        pending = list(data.get("adds") or [])
        stats["adds_requested"] += len(pending)
        progress = True
        while pending and progress:
            progress = False
            deferred = []
            for add in pending:
                parent = index.get(add.get("parentId"))
                node = add.get("node")
                if not isinstance(parent, dict) or not isinstance(node, dict):
                    deferred.append(add)
                    continue
                children = parent.setdefault("childNodes", [])
                fresh = copy.deepcopy(node)
                position = None
                next_id = add.get("nextId")
                if next_id is not None:
                    for offset, child in enumerate(children):
                        if isinstance(child, dict) and child.get("id") == next_id:
                            position = offset
                            break
                if position is None:
                    children.append(fresh)
                else:
                    children.insert(position, fresh)
                _index_nodes(fresh, index, parents, parent)
                stats["adds_applied"] += 1
                progress = True
            pending = deferred
        stats["adds_dangling"] += len(pending)

        removes = data.get("removes") or []
        stats["removes_requested"] += len(removes)
        for remove in removes:
            node = index.get(remove.get("id"))
            parent = index.get(remove.get("parentId")) or parents.get(remove.get("id"))
            if not isinstance(node, dict) or not isinstance(parent, dict):
                stats["removes_missing"] += 1
                continue
            parent["childNodes"] = [
                child for child in (parent.get("childNodes") or []) if child is not node
            ]
            stats["removes_applied"] += 1

        attributes = data.get("attributes") or []
        stats["attributes_requested"] += len(attributes)
        for mutation in attributes:
            node = index.get(mutation.get("id"))
            if not isinstance(node, dict):
                stats["attributes_missing"] += 1
                continue
            current = node.setdefault("attributes", {})
            for name, value in (mutation.get("attributes") or {}).items():
                if value is None:
                    current.pop(name, None)
                else:
                    current[name] = value
            stats["attributes_applied"] += 1

        texts = data.get("texts") or []
        stats["texts_requested"] += len(texts)
        for mutation in texts:
            node = index.get(mutation.get("id"))
            if not isinstance(node, dict):
                stats["texts_missing"] += 1
                continue
            node["textContent"] = mutation.get("value")
            stats["texts_applied"] += 1

    stats["nodes_after_replay"] = len(index)
    return root, stats


def prune_inert(node: Any, counts: dict | None = None) -> dict:
    """Drop <script>/<noscript> subtrees so a served fixture executes nothing."""
    counts = counts if counts is not None else {"pruned": 0}
    if not isinstance(node, dict):
        return counts
    children = node.get("childNodes") or []
    kept = []
    for child in children:
        tag = str((child or {}).get("tagName") or "").lower() if isinstance(child, dict) else ""
        if tag in _INERT_TAGS:
            counts["pruned"] += 1
            continue
        kept.append(child)
        prune_inert(child, counts)
    node["childNodes"] = kept
    return counts


def strip_inline_css(node: Any, counts: dict | None = None) -> dict:
    """Drop rrweb's inlined ``_cssText`` attribute, recording what was removed.

    rrweb stores a stylesheet's whole text on the ``<link>``/``<style>`` node in
    a synthetic ``_cssText`` attribute -- 688 KB of Bootstrap on the recorded
    login page, against ~130 real elements.  ``_cssText`` is serializer
    bookkeeping rather than page markup (no real ``<link>`` carries it), and no
    template selector can match on it, so removing it cannot change how any
    selector resolves.  The bytes are counted into the provenance record rather
    than silently dropped.
    """
    counts = counts if counts is not None else {"attributes": 0, "bytes": 0}
    if not isinstance(node, dict):
        return counts
    attributes = node.get("attributes")
    if isinstance(attributes, dict) and "_cssText" in attributes:
        value = attributes.pop("_cssText")
        counts["attributes"] += 1
        counts["bytes"] += len(value) if isinstance(value, str) else 0
    for child in node.get("childNodes") or []:
        strip_inline_css(child, counts)
    return counts


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _PageStateNodes(HTMLParser):
    """Parse a page's rendered HTML into the node tree a full snapshot carries.

    The output is the SAME shape ``replay_dom`` produces, so the page-state
    route and the rrweb route converge before anything is pruned, stripped,
    serialized or digested -- one pipeline, one provenance record, two ways in.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"type": _NT_DOCUMENT, "id": 0, "childNodes": []}
        self._open: list[dict[str, Any]] = [self.root]
        self._next_id = 1

    def _adopt(self, node: dict[str, Any]) -> dict[str, Any]:
        node["id"] = self._next_id
        self._next_id += 1
        self._open[-1]["childNodes"].append(node)
        return node

    def _element(self, tag: str, attrs: list) -> dict[str, Any]:
        return self._adopt({
            "type": _NT_ELEMENT,
            "tagName": tag,
            # Every recorded attribute is carried across. Dropping any of them
            # would keep the fixture's SHAPE while losing the page's identity --
            # the ids and classes every selector claim is actually about.
            "attributes": {name: ("" if value is None else value)
                           for name, value in attrs},
            "childNodes": [],
        })

    def handle_starttag(self, tag: str, attrs: list) -> None:
        node = self._element(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self._open.append(node)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._element(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._open) - 1, 0, -1):
            if self._open[depth].get("tagName") == tag:
                del self._open[depth:]
                return
        # An unmatched close tag closes nothing, exactly as a browser treats it.

    def handle_data(self, data: str) -> None:
        if data:
            self._adopt({"type": _NT_TEXT, "textContent": data})


def _carries_an_element(node: Any) -> bool:
    """Does this subtree contain a rendered ELEMENT, and not merely text?

    ``childNodes`` counts TEXT nodes too, so a page state of bare words -- a
    saved page that rendered nothing -- has a NON-EMPTY child list with no
    element in it.  Asking "is the child list empty" therefore answers a
    different question from the one the refusal below is about, and answers it
    wrongly for exactly the input that refusal names, so the question is asked
    directly here instead.
    """
    if not isinstance(node, dict):
        return False
    for child in node.get("childNodes") or []:
        if isinstance(child, dict) and child.get("tagName"):
            return True
        if _carries_an_element(child):
            return True
    return False


def page_html_to_capture(html: str, source_url: str = "") -> dict[str, Any]:
    """Wrap a saved page state as a capture carrying one full snapshot."""
    parser = _PageStateNodes()
    parser.feed(html)
    parser.close()
    root = parser.root
    if not _carries_an_element(root):
        raise CaptureUnknown(
            "page state carries no elements: nothing was rendered to rebuild"
        )
    return {
        "url": source_url,
        "host": urlsplit(source_url).hostname if source_url else None,
        "capture_route": "page_html",
        "dom_log": [{"type": "full_snapshot", "dom_seq": 0, "data": {"node": root}}],
    }


# The surrounding record every capture writer emits. ``dom_log`` is the key
# this tool consumes and is required on its own; the rest keep a bare
# ``{"dom_log": []}`` from being read as a capture that recorded nothing.
_CAPTURE_KEYS = ("network_log", "captured_at", "url", "host", "capture_version")


def is_capture(data: Any) -> bool:
    """Does this parsed file have the SHAPE of a capture at all?

    "This is not a capture" and "this capture recorded nothing" are DIFFERENT
    ANSWERS leading to opposite actions -- name a different file, or re-arm the
    recorder -- so they are decided here, separately, and reported separately.
    """
    return (
        isinstance(data, dict)
        and isinstance(data.get("dom_log"), list)
        and sum(1 for key in _CAPTURE_KEYS if key in data) >= 2
    )


def route_for(path: Path) -> str:
    """Which supported route this input takes into the one fixture pipeline."""
    return "page_html" if path.suffix.lower() in _PAGE_SUFFIXES else "rrweb_replay"


def _read_route(capture_path: Path, source_url: str) -> tuple[str, dict[str, Any]]:
    """Return ``(route, capture)`` for either supported input."""
    route = route_for(capture_path)
    if route == "page_html":
        raw = capture_path.read_bytes()
        if not raw.strip():
            raise ValueError(f"page state is empty: {capture_path}")
        return route, page_html_to_capture(
            raw.decode("utf-8"), source_url=source_url
        )
    return route, load_capture(capture_path)


def build(
    capture_path: Path,
    out_path: Path,
    snapshot_index: int | None,
    stop_seq: int | None,
    provenance_path: Path | None,
    source_url: str = "",
) -> dict[str, Any]:
    raw = capture_path.read_bytes()
    route, capture = _read_route(capture_path, source_url)
    if route == "rrweb_replay" and not is_capture(capture):
        raise ValueError(
            f"not a capture: {capture_path} carries no capture record "
            f"(a capture has a dom_log list and a capture header)"
        )
    dom_log = capture.get("dom_log") or []
    if not dom_log:
        raise CaptureUnknown(
            f"rrweb recorded nothing in this capture: {capture_path} is a capture "
            "and its dom_log is empty, so no page state exists to rebuild; re-arm "
            "the recorder, or save the page state and build from PAGE.html"
        )
    snapshots = [
        i for i, e in enumerate(dom_log)
        if isinstance(e, dict) and e.get("type") == "full_snapshot"
    ]
    if not snapshots:
        raise ValueError(f"capture has no full snapshot: {capture_path}")
    if snapshot_index is None:
        snapshot_index = snapshots[-1]
    if snapshot_index not in snapshots:
        raise ValueError(
            f"index {snapshot_index} is not a full snapshot; snapshots are {snapshots}"
        )

    root, stats = replay_dom(dom_log, snapshot_index, stop_seq)
    if stats["adds_dangling"] or stats["removes_missing"] or \
            stats["attributes_missing"] or stats["texts_missing"]:
        raise RuntimeError(f"replay dropped recorded operations: {stats}")

    pruned = prune_inert(root)
    stripped = strip_inline_css(root)
    html = nodes_to_html(root)
    if not html.strip():
        raise RuntimeError("replay produced an empty document")
    document = "<!doctype html>\n" + html + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(document, encoding="utf-8")

    provenance = {
        "generator": "tools/build_recorded_dom_fixture.py",
        "route": route,
        "source_capture": capture_path.name,
        "source_sha256": _digest(raw),
        "source_bytes": len(raw),
        "capture_host": capture.get("host"),
        "capture_url": capture.get("url"),
        "captured_at": capture.get("captured_at"),
        "dom_log_count": len(dom_log),
        "full_snapshot_indexes": snapshots,
        "replay": stats,
        "pruned_inert_nodes": pruned["pruned"],
        "stripped_css_attributes": stripped["attributes"],
        "stripped_css_bytes": stripped["bytes"],
        "output": out_path.name,
        "output_bytes": len(document.encode("utf-8")),
        "output_sha256": _digest(document.encode("utf-8")),
    }
    if provenance_path is not None:
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "capture",
        help="a .wacz/capture .json, or a saved PAGE.html page state",
    )
    parser.add_argument("--out", required=True, help="HTML fixture to write")
    parser.add_argument("--snapshot-index", type=int, default=None,
                        help="dom_log index of the full snapshot to start from "
                             "(default: the last one)")
    parser.add_argument("--stop-seq", type=int, default=None,
                        help="apply mutations up to and including this dom_seq")
    parser.add_argument("--provenance", default=None,
                        help="write a provenance JSON sidecar here")
    parser.add_argument("--source-url", default="",
                        help="URL the page state came from (page-state route)")
    args = parser.parse_args(argv)

    try:
        provenance = build(
            Path(args.capture),
            Path(args.out),
            args.snapshot_index,
            args.stop_seq,
            Path(args.provenance) if args.provenance else None,
            args.source_url,
        )
    except CaptureUnknown as exc:
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return 4
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"replay failure: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(provenance, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
