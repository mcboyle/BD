# Dark-cluster adjudication -- thumbs, a11y, vpn (v3.66.753)

Closes the last three clusters of the DARK_CONTROL_SCOPE_v3_66_733 list.
Every verdict below is derived from source read in-session, not from the
scope doc's guesses; where they disagree, the evidence is cited.

## thumbs/* -- CLOSED. Do not wire. (3 endpoints)

`POST /api/thumbs/single`, `/api/thumbs/contact_sheet`,
`/api/thumbs/sprite_sheet` (app_thumbs.py) each take `body["path"]` and
hand it to ffmpeg via thumbnail_sheets helpers with NO root confinement --
no `_validate_path`, no allowlist check, nothing between the request body
and the filesystem. A wired control would be an arbitrary-file-read
surface: any path an operator (or a CSRF'd browser, or a template) can
name gets opened and rendered into an image.

The SAFE siblings already exist and are already wired:
`POST /api/thumbnail_sheets/contact_sheet/<int:hid>` and
`/api/thumbnail_sheets/single/<int:hid>` key on HISTORY IDS -- the path
is resolved server-side from the library row, never taken from the
caller. The thumbs family is a shadow of that family with the safety
removed. Wiring it would ADD an attack surface to duplicate a capability
the GUI already has.

VERDICT: all three stay dark, classified CLOSED. The residual question
is whether the endpoints should be DELETED rather than merely unwired --
that is a backend-removal cut (route deletions trip the route-map
baseline and the overlay-orphan rule) and is left to the operator to
schedule.

Ledger history worth keeping: until v3.66.752 the reachability ledger
reported `/api/thumbs/contact_sheet` as WIRED -- a phantom from the real
thumbnail_sheets caller's `/contact_sheet/` substring under the loose
1-segment tail matcher. The most dangerous family in the dark set was
the one the ledger said needed no adjudication.

## a11y -- SPLIT: two CLOSED (dev-surface), one WIRED @753.

* `POST /api/a11y/audit {html}` -- aria_audit over an arbitrary HTML
  blob (unlabeled inputs, icon-only buttons, duplicate IDs...). The
  input is a UI under construction. No operator workflow produces an
  HTML blob; the audience is the developer QA loop (pack_H ships the
  offline a11y toolchain for exactly that). CLOSED (dev-surface).
* `GET /api/a11y/contrast?fg=&bg=` -- WCAG 2.1 contrast of two CSS
  colors; accessibility.py's own docstring says "used to audit the
  theme." Theme auditing is development. CLOSED (dev-surface).
  (GET-only, so it never appeared in the mutating dark ledger; recorded
  here so the adjudication is complete.)
* `POST /api/a11y/plain_language {message}` -- OPERATOR. The module
  docstring names the moment: an error that survived friendly_error and
  still reads like "ConnectionError: HTTPSConnectionPool..." on a
  non-technical operator's screen. WIRED @753 as an inline affordance on
  the NeedsReview message block (PlainLanguageHint) -- where the input
  actually exists on screen -- not as a panel. Honesty rule carried into
  the component: plain_language returns the ORIGINAL on no pattern
  match, and plain == original renders as "no simpler phrasing is
  available", never as the same text presented twice.

## vpn (3) -- DEFERRED to F5, unchanged.

`vpn/auto_blacklist`, `vpn/tunnels/<tid>/leak_test/run`,
`vpn/tunnels/<tid>/webrtc_result` stay dark pending the F5 netns
launch-routing model. Vpn.tsx already renders leak-test state it cannot
operate (the kill-switch precedent); wiring a leak-test button that
reports against a tunnel the launch path does not actually route
through is worse than no button. Nothing in this adjudication changes
that call.

## Scoreboard after this cut

Of the original 8 dark clusters: queue_templates@733, cookie_clipboard
@735, search+semantic@743, knowledge/notes@751, ai@752, and the a11y
operator half @753 are WIRED; thumbs and the a11y dev half are CLOSED
with this doc; vpn is DEFERRED on F5. The dark residual is now entirely
"classified and justified" -- no cluster remains unadjudicated.
