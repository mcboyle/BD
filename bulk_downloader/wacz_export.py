"""WACZ-compatible export (A-T2).

Packages a bd-recon / DomCapture capture into a WACZ-shaped ZIP so
captures interop with the Webrecorder ecosystem (replayweb.page for
inspection, pywb for replay debugging). We follow the WACZ 1.1.1 layout
conventions — ``datapackage.json`` manifest with per-resource SHA-256
digests, ``pages/pages.jsonl``, and a ``datapackage-digest.json`` over
the manifest — without a third-party dependency (stdlib ``zipfile`` +
``hashlib`` only; bash network is denylisted in this environment anyway).

This is **not** a full WARC writer: a real WACZ stores WARC records under
``archive/``. We store the capture as ``archive/capture.json`` (our
recon format) plus the WACZ scaffolding, which is enough for inspection
and for our own diff/synthesis tooling to consume, and is forward-
compatible with adding WARC serialization later. The ZIP is STORED
(uncompressed) for the HTTP Range random-access WACZ relies on.

Posture: packaging/export only — detect-and-surface-risk. The capture it
packages is already redacted at capture time; as defense-in-depth (v3.66.171)
this module ALSO runs a profile-aware export-boundary scrub that closes the
DOM-embedded-URL + email gap the frozen rrweb recorder cannot, stamps the active
redaction profile, attaches a capture-health block, and FAILS LOUD if any floor
secret would survive into ``capture.json``. It performs no replay and adds no
captured network/stream data.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from typing import Any, Dict, List, Optional

WACZ_VERSION = "1.1.1"


class WaczRedactionError(RuntimeError):
    """Raised by :func:`build_wacz_bytes` when the export-boundary floor scan
    finds a credential residual that would otherwise be written to
    ``capture.json``. Carries the residual KINDS (never values) for diagnosis."""


def _capture_health(capture: Dict[str, Any]) -> Dict[str, Any]:
    """A small, value-free health block derived from the capture + the
    recorder's status counters (read-only). Persists the v3.66.170 counters
    (``dom_events_dropped`` / ``arm_fail_streak``) that ``get_status`` exposed
    but that never reached ``capture.json``."""
    dom = capture.get("dom_log") or []
    net = capture.get("network_log") or []
    dlc = capture.get("dom_log_count")
    n_err = sum(1 for e in net if isinstance(e, dict) and (
        e.get("error") or (isinstance(e.get("response_status"), int)
                           and e["response_status"] >= 400)))
    full = sum(1 for e in dom if isinstance(e, dict)
               and e.get("type") in ("full_snapshot", 2))
    health: Dict[str, Any] = {
        "dom_log_len": len(dom),
        "dom_log_count": dlc,
        "dom_full_snapshots": full,
        "network_log_len": len(net),
        "network_error_count": n_err,
        "dom_integrity_ok": (dlc == len(dom)) if isinstance(dlc, int) else None,
    }
    try:  # recorder status is read-only and best-effort
        from .dom_recorder import get_status
        st = get_status()
        health["dom_events_dropped"] = st.get("dom_events_dropped")
        health["arm_fail_streak"] = st.get("arm_fail_streak")
        health["rrweb_present"] = st.get("rrweb_present")
    except Exception:
        pass
    return health


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _pages_jsonl(capture: Dict[str, Any]) -> bytes:
    """One pages.jsonl: the header line + a page entry for the captured
    top-level URL."""
    header = {"format": "json-pages-1.0", "id": "pages",
              "title": "Capture pages"}
    page = {
        "id": "page-0",
        "url": capture.get("url") or "",
        "ts": capture.get("captured_at") or "",
        "title": capture.get("title") or capture.get("url") or "",
    }
    return (json.dumps(header) + "\n" + json.dumps(page) + "\n").encode("utf-8")


def build_wacz_bytes(capture: Dict[str, Any], *,
                     extra_resources: Optional[Dict[str, bytes]] = None,
                     created: Optional[str] = None) -> bytes:
    """Build a WACZ-compatible ZIP in memory and return its bytes.

    Parameters
    ----------
    capture : dict
        A bd-recon / DomCapture ``to_capture_dict()`` output. It is already
        redacted at capture time; v3.66.171 additionally applies a profile-aware
        export-boundary scrub here (defense-in-depth + DOM-embedded-URL/email
        coverage) and FAILS LOUD (``WaczRedactionError``) on any floor residual.
    extra_resources : dict, optional
        Additional ``archive/`` members (e.g. chunked event files) keyed
        by their in-zip path → bytes.
    """
    import io
    from .redaction_profile import current_profile, reduced_redaction
    from .capture_artifact_redact import redact_capture, scan_floor_secrets

    created = created or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # v3.66.171 — profile-aware export-boundary scrub (idempotent; on an
    # already-redacted capture it only adds DOM-embedded-URL + email coverage
    # the frozen rrweb recorder cannot). Operates on a copy; the caller's dict
    # is untouched.
    profile = current_profile()
    forced_floor_scrub = 0
    if isinstance(capture, dict):
        capture = redact_capture(capture, profile)
        residual = scan_floor_secrets(capture, profile)
        if residual:
            # v3.66.470 DEFER-FLOOR-FAILOPEN: fail-open-INTO-scrub. Blunt-scrub the
            # flagged leaves to the placeholder and re-scan before aborting. This is
            # strictly more scrubbing (never less); keep_full residuals are not
            # flagged so they never reach here. The floor stays FAIL-CLOSED: if the
            # re-scan still finds residual, it raises.
            from .capture_artifact_redact import _force_scrub_floor
            capture = _force_scrub_floor(capture, residual)
            forced_floor_scrub = len(residual)
            residual = scan_floor_secrets(capture, profile)
            if residual:
                kinds = sorted({k for _p, k in residual})
                raise WaczRedactionError(
                    "floor secret(s) would survive into capture.json: "
                    + ", ".join(kinds)
                    + f" ({len(residual)} site(s))")
        # capture-health + redaction-profile stamp (value-free)
        capture = dict(capture)
        capture["capture_health"] = _capture_health(capture)
        capture["redaction_profile"] = {
            "schema": "v3.66.171",
            "network_signed_urls": profile.get("network_signed_urls"),
            "dom_embedded_urls": profile.get("dom_embedded_urls"),
            "emails": profile.get("emails"),
            "custom_sensitive_headers": list(profile.get("custom_sensitive_headers", ())),
            "reduced_redaction": reduced_redaction(profile),
            "forced_floor_scrub": forced_floor_scrub,
        }
        # A relaxed capture is self-identifying so it is never circulated.
        if reduced_redaction(profile):
            capture["reduced_redaction"] = True
            capture["local_only"] = True

    # Members that participate in the datapackage manifest.
    members: Dict[str, bytes] = {}
    members["archive/capture.json"] = json.dumps(
        capture, ensure_ascii=False, sort_keys=True).encode("utf-8")
    members["pages/pages.jsonl"] = _pages_jsonl(capture)
    for path, data in (extra_resources or {}).items():
        members[path] = data

    resources: List[Dict[str, Any]] = []
    for path, data in sorted(members.items()):
        resources.append({
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "hash": _sha256(data),
            "bytes": len(data),
        })

    datapackage = {
        "profile": "data-package",
        "wacz_version": WACZ_VERSION,
        "software": "BulkDownloader/session-capture",
        "created": created,
        "resources": resources,
        "mainPageURL": capture.get("url") or "",
    }
    dp_bytes = json.dumps(datapackage, ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
    dp_digest = json.dumps(
        {"path": "datapackage.json", "hash": _sha256(dp_bytes)},
        sort_keys=True).encode("utf-8")

    buf = io.BytesIO()
    # STORED for Range-based random access (WACZ convention).
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for path, data in sorted(members.items()):
            zf.writestr(path, data)
        zf.writestr("datapackage.json", dp_bytes)
        zf.writestr("datapackage-digest.json", dp_digest)
    return buf.getvalue()


def write_wacz(capture: Dict[str, Any], out_path: str, **kwargs) -> str:
    """Build and write a WACZ to ``out_path``; returns the path."""
    data = build_wacz_bytes(capture, **kwargs)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def verify_wacz_bytes(wacz: bytes) -> Dict[str, Any]:
    """Validate a WACZ's internal digests. Returns
    ``{"ok": bool, "errors": [...], "resources": N}``. Used by tests and
    by an operator sanity check after export."""
    import io

    errors: List[str] = []
    with zipfile.ZipFile(io.BytesIO(wacz), "r") as zf:
        names = set(zf.namelist())
        for required in ("datapackage.json", "datapackage-digest.json",
                         "archive/capture.json", "pages/pages.jsonl"):
            if required not in names:
                errors.append(f"missing:{required}")
        if "datapackage.json" in names:
            dp_bytes = zf.read("datapackage.json")
            dp = json.loads(dp_bytes)
            # manifest digest
            if "datapackage-digest.json" in names:
                dig = json.loads(zf.read("datapackage-digest.json"))
                if dig.get("hash") != _sha256(dp_bytes):
                    errors.append("digest_mismatch:datapackage.json")
            # per-resource digests
            for res in dp.get("resources", []):
                path = res.get("path")
                if path not in names:
                    errors.append(f"resource_missing:{path}")
                    continue
                if _sha256(zf.read(path)) != res.get("hash"):
                    errors.append(f"hash_mismatch:{path}")
            n = len(dp.get("resources", []))
        else:
            n = 0
    return {"ok": not errors, "errors": errors, "resources": n}
