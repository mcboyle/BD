# Template Host Alias Matching Design

> **Current status (2026-07-21):** This design is implemented and focused-tested
> on the pre-documentation implementation baseline
> `51c63341de697bb3f585055ba73f84e03fe8658b`. Deployment proof for the final
> merged head is still pending: the last fully validated live deployment
> is the distinct commit `b60f58f0d25cbfb5d3bda07b81ee113e10650218`.
> Treat the Deployment and Verification section as unfinished until the parent
> performs the final merged deployment and acceptance run. Later docs-only
> closeout commits do not change the runtime implementation baseline.

## Goal

Prevent reviewed templates from silently dropping out when a site moves an
authenticated workflow to a sibling host, while keeping cross-subdomain
template application explicit and safe by default.

The shared HTTP transport fix for closeable `curl_cffi` responses is already
site-independent. This design generalizes the remaining template-host failure
that was exposed by the FilthyKings live run.

## Current Failure

Runtime template lookup considers only the top-level `host` field. Template
metadata may already list valid authenticated aliases under `match.hosts`, but
the registry ignores those aliases. A template captured on `www.example.com`
therefore does not apply to `members.example.com`, even when the template
explicitly declares both hosts. The runner then falls back to generic DOM
scoring, which can select unrelated navigation controls that happen to contain
quality labels such as `4K`.

## Matching Model

Reviewed templates gain three host scopes:

1. `host` remains the canonical host and preserves existing behavior.
2. `match.hosts` is an explicit list of additional permitted hosts.
3. `match.sibling_domain` is an optional parent-domain opt-in that permits any
   subdomain beneath the declared value.

Default behavior remains restrictive: sibling hosts not named by `host` or
`match.hosts` do not match unless `match.sibling_domain` is present and valid.

Example:

```json
{
  "host": "www.example.com",
  "match": {
    "hosts": [
      "www.example.com",
      "members.example.com"
    ],
    "sibling_domain": "example.com"
  }
}
```

Removing `sibling_domain` from this example permits the two declared hosts
(plus the canonical host's already-supported child-host behavior). Keeping it
also permits other hosts beneath `example.com`.

## Safety Rules

- Host comparisons are case-insensitive and ignore surrounding whitespace.
- Non-list `match.hosts` values and non-string entries fail closed.
- `match.hosts` entries are exact aliases. An alias does not implicitly enable
  its children or siblings.
- `match.sibling_domain` matches only the domain itself and hosts ending in
  `.` plus that domain, preventing substring matches such as
  `notexample.com`.
- A sibling domain is accepted only when the canonical `host` is equal to it or
  is a subdomain beneath it. Unrelated declarations fail closed.
- Empty, malformed, IP-address, localhost, and single-label sibling domains
  fail closed. Operators can still list such destinations explicitly in
  `match.hosts` when needed for a controlled local template.
- Invalid metadata never raises during template lookup; that candidate simply
  does not receive the invalid match scope.
- Existing templates without `match.hosts` or `match.sibling_domain` retain
  their current canonical-host matching behavior.

## Selection Priority

When several enabled templates match one URL, specificity is evaluated in this
order:

1. Exact canonical `host` match.
2. Exact `match.hosts` alias match.
3. Existing canonical-host child match.
4. Opt-in `match.sibling_domain` match.

Within the same category, the longer matching host or domain wins. Existing
HTML-based variant selection continues to operate on the resulting candidate
set and remains unchanged.

This ordering ensures that a site-specific template cannot be shadowed by a
broader parent-domain template.

## Components

### Template registry

`bulk_downloader.template_registry` will expose a small, pure match-scoring
helper used by both `find_template_for_url` and
`find_template_variants_for_url`. Centralizing the decision prevents the two
lookup paths from drifting.

The helper returns either no match or a sortable specificity tuple. Callers do
not need to know which metadata field produced the match.

### Reviewed template data

The FilthyKings reviewed template will use its public host as canonical and
declare the authenticated member host explicitly in `match.hosts`. It will not
enable broad sibling matching, demonstrating the secure default.

Other templates can opt into `match.sibling_domain` during review when a whole
domain family is known to share one application and selector contract.

### Shared transport

The existing `curl_cffi` response adapter remains in the common transport
mixin. Its regression test stays part of this change set to prove the fix is
not coupled to a particular site template.

## Data Flow

1. The runner opens a page and asks the template registry for a matching
   enabled template.
2. The registry parses the page host once.
3. Every template receives a match-specificity score from canonical host,
   explicit aliases, or the optional sibling domain.
4. The registry selects the highest-scoring candidate, preserving HTML variant
   scoring where applicable.
5. The template bridge merges the selected download trigger and row selectors
   into the runner's learned hints.
6. The shared transport handles the resulting download response, including
   closeable `curl_cffi` responses that do not implement a context manager.

## Tests

Automated regression coverage will prove:

- An explicit `match.hosts` member alias matches.
- An undeclared sibling host is rejected by default.
- A valid `match.sibling_domain` permits a sibling host.
- An unrelated or malformed sibling domain fails closed.
- Exact canonical templates outrank alias and sibling-domain matches.
- Exact aliases outrank broad sibling-domain matches.
- Variant discovery and primary lookup return consistent candidates.
- Existing canonical parent-host behavior remains unchanged.
- A closeable, non-context-manager HTTP response is closed after streaming
  context exit.
- The FilthyKings reviewed template matches its authenticated member URL while
  an unrelated sibling remains rejected.

Tests will follow red-green order: each new matching behavior must fail against
the current registry before implementation is added.

## Deployment and Verification

1. Run the focused registry and transport regression tests.
2. Run the existing template-registry and template-validation suites.
3. Back up the deployed registry and reviewed template.
4. Deploy the registry, template, and transport changes to `stash`.
5. Restart BulkDownloader because Python registry and transport modules are
   process-loaded.
6. Confirm service health and the enabled 2,000 GiB watchdog.
7. Confirm an authenticated scene selects the reviewed trigger and produces a
   growing 2160p partial file without the context-manager exception.
8. Leave the full queue running only after those checks pass.

## Non-Goals

- No automatic redirect-based alias learning.
- No network-dependent public-suffix lookup.
- No automatic widening from `www` to every sibling subdomain.
- No change to `match.url_patterns`; URL-pattern matching remains outside the
  runtime host-selection path in this scope.
- No changes to selector scoring, authentication, paywall handling, or DRM
  behavior.
