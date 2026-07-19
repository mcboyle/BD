# Vendored browser-side capture libraries

These are checked-in, unmodified third-party browser bundles injected into
pages during a capture session (see `bulk_downloader/dom_recorder.py`). They
are vendored (not npm-installed) because the capture browser is driven
server-side and needs a self-contained global-attaching script to inject via
Playwright `add_init_script` — no module loader is available in page scope.

| Library            | Version | File                      | Global         | Source build                    |
|--------------------|---------|---------------------------|----------------|---------------------------------|
| rrweb              | 2.0.1   | rrweb/rrweb.min.js        | `window.rrweb` | npm `rrweb` `umd/rrweb.min.js`  |
| @zumer/snapdom     | 2.12.8  | snapdom/snapdom.js        | `window.snapdom` | npm `@zumer/snapdom` `dist/snapdom.js` |

- rrweb records full-snapshot + incremental DOM mutation/input/scroll events;
  `dom_recorder` forwards them into `DomCapture.record_dom_event` (the
  rrweb-style ingest the project already implemented in `dom_capture.py`).
- snapdom captures a DOM→image snapshot; `dom_recorder.snapshot_dom` stores it
  via `DomCapture.record_dom_snapshot`.
- Both are MIT-licensed. Update by re-copying the same files from the pinned
  npm versions; keep this table in sync.
