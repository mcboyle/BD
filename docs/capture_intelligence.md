# Capture Intelligence

*v3.66.117 (Phase 8). Read-only, POSTURE-SAFE. Part of the cockpit Template
Intelligence area. See `CAPTURE_SYNTHESIS_POSTURE.md`.*

## What it is

One cockpit page — **Capture Intelligence** — that scores each capture under the
captures root for quality, completeness, and coverage, and lists what evidence is
missing. It answers "how good are my captures, and what's missing?"

For each capture: a **quality** score (0–100, banded rich/usable/thin), a
**completeness** fraction, and four **coverage** dimensions — DOM, network,
template, drift — plus a **missing-evidence** list (no network log, no DOM snapshot,
no cookies, no rendition descriptors).

## Posture: metadata only

This is the phase that touches the WACZ captures, so it stays strictly at the
metadata level using the project's posture-safe descriptor lens:

- **presence + counts only** — has-network, network/media event counts, has-DOM,
  has-cookies; never the content
- **rendition names** and **signing-marker NAMES** only (via `_descriptors_of`) —
  never signing values, never raw URLs (query-stripped)
- **no reassembly** of a capture, **no reconstruction** of a signed stream, **no
  decryption**, **no replay**, **no live fetch**

Scoring is a **DEFINED** composite (mean of the four coverages × 100) with every
input shown — not an objective measure. The scorer is a pure function of metadata,
so a capture's content is never required to score it.

## Coverage dimensions

- **DOM** — does the capture carry a DOM/HTML snapshot?
- **Network** — does it have a network log, and media events within it?
- **Template** — does it carry rendition descriptors a download template can work
  from?
- **Drift** — does it provide what's needed to check drift (network + DOM)?

## Boundaries (enforced, tested)

- posture-safe: no reassembly/reconstruction/decrypt/replay constructs; uses
  `_descriptors_of` (names only)
- signing surfaced as marker names, never values; no raw URLs
- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`),
  no live fetch
- honest: empty when no captures exist; unreadable captures flagged, not scored

## Endpoint (v3.66.117)

`GET /cockpit/api/template/capture-intel` — GET, read-only. Cockpit route count 95;
POST surface unchanged.
