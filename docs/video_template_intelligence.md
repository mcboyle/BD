# Video / Download Template Intelligence

*v3.66.110. Read-only, recognition-only. Part of the cockpit Template Intelligence
area. See also `template_health_cockpit.md`.*

## What it is

Two cockpit surfaces that explain the video/download template (the per-site
`learned.download` block) and the download-candidate decision:

- **Video Templates** (`/cockpit/api/template/video-health`) — per-site health of
  the download template + selector-drift history.
- **Download Decision Explorer** (`/cockpit/api/template/download-explain`) — why a
  download candidate is chosen, narrated from the project's pure heuristic scorer.

Both are read-only. Neither fetches a live page, calls a model, replays a captured
request, or writes anything.

## Video Template Health

For each site in `sites_config.json` it reports: whether the template exists
(`learned.download` with `row_selectors`); the row-selector count; the
`url_attribute`; whether a two-step (trigger→reveal) flow is modelled
(`trigger_selectors`); a **defined** selector-confidence signal (selector breadth
capped at 4, minus a drift penalty — every input shown, not an objective measure);
the highest rendition the **corpus** has seen for the site (honest — empty until
captures are ingested); and the selector-drift state from `selector_drift`
(`flagged_stale`, `consecutive_failures`, last success/failure timestamps, last
selector, and a **query-stripped** last URL).

Sites are ordered missing-template first, then drifted, then by failure count — so
the page leads with what needs attention.

## Download Decision Explainer

Given a set of download candidates (or a labelled sample when no live page is
available), it runs the project's **pure** `score_candidate` / `rank_candidates`
and reports, per candidate: the score, the per-signal reasons (text/href/data-attr
signals + resolution tier), the resolution tier, and whether it clears the
`min_score` threshold. Then it names the chosen option (top-ranked) and the
rejected ones, with the reasons behind the choice.

The highest rendition is still chosen from **live state** at download time; this
explainer narrates the same scoring on recorded or sample candidates so the
operator can see *why* a choice was made or would be made.

## Boundaries (enforced, tested)

- reads `sites_config.json` read-only; never writes it; no corpus writes
- credential and signing **values** are never read or echoed — only field names,
  CSS selectors, and query-stripped paths (signing lives in the query string and
  is always dropped)
- no live page fetch, no model/network call, no request replay, no captured-token
  reuse, no generated browser replay scripts
- any suggested template update is returned as **data only** — never auto-applied,
  never auto-promoted; debt is never auto-retired

## Example

See `example_download_decision_report.md` for real explainer output on the sample
set (4K chosen at score 120 / tier 60 over 1080p and 720p).
