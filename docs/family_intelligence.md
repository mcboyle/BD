# Family Intelligence

*v3.66.115 (Phase 6). Read-only cross-site family analysis. Builds on the family
inference from Phase 5. See `template_health_cockpit.md` and `site_playbooks.md`.*

## What it is

One cockpit page — **Family Intelligence** — that groups sites by inferred player/
provider family and surfaces what members SHARE, plus where one site can learn from
its siblings. Pure read-only aggregation: no live fetch, no model, no replay, no
writes.

The overview (`/cockpit/api/template/family-intel`) lists each family with member
count, shared download/login selector counts, the shared workflow shape (two-step
fraction, common url_attribute), and shared drift patterns. Click a family to open
its detail (`/cockpit/api/template/family?name=…`).

## Family detail

- **Shared selectors** — download and login selectors used by ≥2 members, with how
  many members use each and which sites.
- **Shared workflow** — two-step (trigger→reveal) fraction across the family and the
  common url_attribute.
- **Shared drift patterns** — which drift kinds recur across members.
- **Shared failure modes** — which failure causes recur across members.
- **Cross-pollination** — the payoff: selectors a *majority* of family members use
  that a given member is **missing**, surfaced as a **data-only** suggestion
  ("learning on one site can help others"). Members that already have every
  family-common selector are not flagged. Never auto-applied.

## Boundaries (enforced, tested)

- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`)
- recognition-only: no live page fetch, no model/network call, no replay,
  no `do_login`/fill/click
- cross-pollination is **data-only** — every suggestion is `applies_automatically:
  false`; nothing is auto-applied or promoted
- family membership is inferred from stored markers (Phase 5), not a live page;
  single-member families correctly share nothing (honest)

## Endpoints (v3.66.115)

`GET /cockpit/api/template/family-intel`, `GET /cockpit/api/template/family?name=…`
— both GET, both read-only. Cockpit route count 93; POST surface unchanged.
