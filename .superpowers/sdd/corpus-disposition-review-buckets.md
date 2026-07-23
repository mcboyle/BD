# Corpus semantic-review buckets

This is a deterministic, review-only index of blocked corpus payloads.
No auto-promotion is permitted. Source paths, URLs, queries, headers,
cookies, and captured values are intentionally excluded; use the capture
SHA-256 to resolve a row inside the private source manifest.

## Summary

- Semantic review required: **445**
- Exact gate-error buckets: **8**
- Selector review: **378**
- Media/network-pattern review: **204**
- Resolution evidence review: **73**
- Auto-promotions: **0**

Counts overlap because a row can require more than one review dimension.

## Exact gate-error families

| Bucket | Rows | Review dimensions | Exact gate errors |
| --- | ---: | --- | --- |
| `gate-18ffa9a7f157` | 212 | selector | `selectors.download must have a trigger or row_selectors` |
| `gate-420ff719a548` | 99 | media_network, selector | `network_patterns must be a non-empty list`<br>`selectors.download must have a trigger or row_selectors` |
| `gate-7e7e373a2ac1` | 61 | media_network | `network_patterns must be a non-empty list` |
| `gate-2faac3c74075` | 40 | media_network, selector, resolution | `network_patterns must be a non-empty list`<br>`selectors.download must have a trigger or row_selectors`<br>`resolutions list is empty` |
| `gate-effddfe2503d` | 25 | selector, resolution | `selectors.download must have a trigger or row_selectors`<br>`resolutions list is empty` |
| `gate-a970ac7fe7bf` | 4 | resolution | `resolutions list is empty` |
| `gate-69e8ba56ed34` | 2 | media_network, selector, resolution | `no media/API-relevant network pattern found`<br>`selectors.download must have a trigger or row_selectors`<br>`resolutions list is empty` |
| `gate-ffb9343cd3b5` | 2 | media_network, resolution | `network_patterns must be a non-empty list`<br>`resolutions list is empty` |

## Operator workflow

Review rows within one bucket at a time, resolving each capture SHA-256
against the private source manifest. Record an explicit semantic accept
or reject decision outside this artifact. This report never enables,
promotes, or rewrites a template.
