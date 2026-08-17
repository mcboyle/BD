# Template intelligence in the operator cockpit

The template-intelligence surfaces are read-only and recognition-only. They
explain current login/download/template state and propose data for operator
review; they do not write site configuration, fetch a live page, replay a
request, expose credential/signing values, or apply a proposed template.

## Current API surface

The current `ROUTE_INDEX.json` contains exactly these GET endpoints:

- `/cockpit/api/template/autopilot`
- `/cockpit/api/template/capture-intel`
- `/cockpit/api/template/download-explain`
- `/cockpit/api/template/drift-intel`
- `/cockpit/api/template/family`
- `/cockpit/api/template/family-intel`
- `/cockpit/api/template/login-drift`
- `/cockpit/api/template/login-health`
- `/cockpit/api/template/login-review`
- `/cockpit/api/template/mission-control`
- `/cockpit/api/template/playbook`
- `/cockpit/api/template/playbook-index`
- `/cockpit/api/template/review-queue`
- `/cockpit/api/template/site-readiness`
- `/cockpit/api/template/unified-health`
- `/cockpit/api/template/video-health`

The repository gate derives this set from the route index and compares exact
identities, so a new, removed, renamed, or non-GET route requires this focused
operator document to move with it.

## Safety contract

- Suggested updates remain data until an operator approves and promotes them.
- Displayed URLs are query-stripped; credentials and signing values are absent.
- A login dry-run may identify fields and confidence but never submits credentials
  outside the existing approved login path.
- Capture-derived evidence follows the repository capture-sharing and redaction
  policy before it leaves its authorized host.
