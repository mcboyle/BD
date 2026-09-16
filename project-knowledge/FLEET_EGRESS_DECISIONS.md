# Fleet Egress Decision Evidence

The operator's own `OPERATOR_DECISIONS.md` ledger is authoritative. This
tracked file is evidence for row 722; it is not a second operator ledger or an
independent source of fleet authority. The evidence remains visible until a
later operator decision replaces it.

## Row 722 -- campaign egress and keeper login cap

Status: DEFAULT

Evidence basis: one measured runner had no tunnel, WireGuard interface, or app
proxy configuration, and campaign traffic used one residential public egress
address. Across three concurrent lanes the campaign driver counted 470 login
submits and 847 keepalive context launches. The public address changed during
the session, so this decision does not pin an address. Real Google Chrome did
not change the result. Captcha escalation under load remains relevant; the
`/login-abused` landing is excluded because row 721 identifies it as an age
gate.

### Distinct per-host egress

Decision: Keep campaign traffic on the existing shared residential egress for now; distinct per-host egress is not currently provisioned.

Reason: The measured public address changed during the session, and row 722 authorizes a decision record rather than unmeasured fleet networking changes.

Consequence: Shared-egress reputation and lockout risk is accepted explicitly,
not treated as an unnoticed omission. Any later move to distinct egress needs a
new measured fleet decision before host networking changes.

### Keeper re-login cap

Decision: Count every keeper-triggered login submit against the same five-per-campaign-per-night login cap before a context launch.

Reason: Keeper re-logins are the measured traffic that can cause lockouts; leaving them outside the cap would preserve the unbounded volume described by rows 667 and 722.

Consequence: row 667 owns implementation of the cap correction. Until that
correction is deployed, the five-per-night campaign cap is not claimed to bound
total login traffic.
