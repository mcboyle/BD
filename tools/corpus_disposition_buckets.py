#!/usr/bin/env python3
"""Build a deterministic, secret-free review manifest for blocked WACZ rows.

The output is deliberately review-only: it contains capture hashes and structural
gate labels, never source paths, captured URLs, queries, headers, or promotion
instructions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


_ERROR_DIMENSIONS = {
    "network_patterns must be a non-empty list": "media_network",
    "no media/API-relevant network pattern found": "media_network",
    "selectors.download must have a trigger or row_selectors": "selector",
    "resolutions list is empty": "resolution",
}
_ERROR_ORDER = {error: index for index, error in enumerate(_ERROR_DIMENSIONS)}


def _read_source(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


def _ordered_errors(values) -> tuple[str, ...]:
    errors = tuple(values or ())
    unknown = sorted(set(errors) - set(_ERROR_DIMENSIONS))
    if unknown:
        raise ValueError("unknown gate error(s); refusing unsafe output: "
                         + ", ".join(repr(value) for value in unknown))
    if not errors:
        raise ValueError("draft_review_required row has no gate errors")
    return tuple(sorted(set(errors), key=_ERROR_ORDER.__getitem__))


def build_manifest(source_bytes: bytes) -> dict:
    source = json.loads(source_bytes)
    rows = []
    seen = set()
    for item in source.get("unique_valid_wacz", []):
        preflight = item.get("read_only_preflight") or {}
        if preflight.get("normalized_status") != "draft_review_required":
            continue
        sha = str(item.get("sha256", "")).lower()
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise ValueError("review row has an invalid capture SHA-256")
        if sha in seen:
            raise ValueError("duplicate review-row capture SHA-256: " + sha)
        seen.add(sha)
        errors = _ordered_errors(preflight.get("gate_errors"))
        dimensions = tuple(dict.fromkeys(_ERROR_DIMENSIONS[e] for e in errors))
        family_material = "\n".join(errors).encode("utf-8")
        bucket_id = "gate-" + hashlib.sha256(family_material).hexdigest()[:12]
        rows.append({
            "auto_promotion": False,
            "bucket_id": bucket_id,
            "bytes": int(item.get("bytes", 0)),
            "capture_sha256": sha,
            "copy_count": int(item.get("copy_count", 0)),
            "gate_errors": list(errors),
            "review_dimensions": list(dimensions),
            "semantic_review_required": True,
        })
    rows.sort(key=lambda row: row["capture_sha256"])

    bucket_rows = {}
    for row in rows:
        bucket_rows.setdefault(row["bucket_id"], []).append(row)
    buckets = []
    for bucket_id, members in bucket_rows.items():
        exemplar = members[0]
        buckets.append({
            "bucket_id": bucket_id,
            "gate_errors": exemplar["gate_errors"],
            "review_dimensions": exemplar["review_dimensions"],
            "row_count": len(members),
        })
    buckets.sort(key=lambda bucket: (-bucket["row_count"], bucket["bucket_id"]))

    dimension_counts = Counter(
        dimension for row in rows for dimension in row["review_dimensions"])
    summary = {
        "auto_promotions": 0,
        "bucket_count": len(buckets),
        "media_or_network_review": dimension_counts["media_network"],
        "resolution_review": dimension_counts["resolution"],
        "review_required": len(rows),
        "selector_review": dimension_counts["selector"],
    }
    return {
        "buckets": buckets,
        "posture": {
            "auto_promotion": False,
            "contains_source_paths": False,
            "contains_urls_or_queries": False,
            "semantic_review_required": True,
        },
        "rows": rows,
        "schema_version": 1,
        "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "summary": summary,
    }


def render_report(manifest: dict) -> str:
    summary = manifest["summary"]
    lines = [
        "# Corpus semantic-review buckets",
        "",
        "This is a deterministic, review-only index of blocked corpus payloads.",
        "No auto-promotion is permitted. Source paths, URLs, queries, headers,",
        "cookies, and captured values are intentionally excluded; use the capture",
        "SHA-256 to resolve a row inside the private source manifest.",
        "",
        "## Summary",
        "",
        f"- Semantic review required: **{summary['review_required']}**",
        f"- Exact gate-error buckets: **{summary['bucket_count']}**",
        f"- Selector review: **{summary['selector_review']}**",
        f"- Media/network-pattern review: **{summary['media_or_network_review']}**",
        f"- Resolution evidence review: **{summary['resolution_review']}**",
        "- Auto-promotions: **0**",
        "",
        "Counts overlap because a row can require more than one review dimension.",
        "",
        "## Exact gate-error families",
        "",
        "| Bucket | Rows | Review dimensions | Exact gate errors |",
        "| --- | ---: | --- | --- |",
    ]
    for bucket in manifest["buckets"]:
        dimensions = ", ".join(bucket["review_dimensions"])
        errors = "<br>".join(f"`{error}`" for error in bucket["gate_errors"])
        lines.append(
            f"| `{bucket['bucket_id']}` | {bucket['row_count']} | "
            f"{dimensions} | {errors} |")
    lines.extend([
        "",
        "## Operator workflow",
        "",
        "Review rows within one bucket at a time, resolving each capture SHA-256",
        "against the private source manifest. Record an explicit semantic accept",
        "or reject decision outside this artifact. This report never enables,",
        "promotes, or rewrites a template.",
        "",
    ])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        help="privacy-safe source manifest path, or - for stdin")
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--check", action="store_true",
                        help="fail if existing outputs differ; never write")
    args = parser.parse_args(argv)
    try:
        source_bytes = _read_source(args.source)
        manifest = build_manifest(source_bytes)
        manifest_text = json.dumps(
            manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        report_text = render_report(manifest)
        if args.check:
            if Path(args.manifest_out).read_text(encoding="utf-8") != manifest_text:
                print("ERROR: review bucket manifest drift", file=sys.stderr)
                return 1
            if Path(args.report_out).read_text(encoding="utf-8") != report_text:
                print("ERROR: review bucket report drift", file=sys.stderr)
                return 1
            print(f"no drift: review_required={manifest['summary']['review_required']} "
                  f"buckets={manifest['summary']['bucket_count']} auto_promotions=0")
            return 0
        Path(args.manifest_out).write_text(manifest_text, encoding="utf-8")
        Path(args.report_out).write_text(report_text, encoding="utf-8")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"review_required={manifest['summary']['review_required']} "
          f"buckets={manifest['summary']['bucket_count']} auto_promotions=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
