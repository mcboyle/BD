# Phases 25-30 + cross-cutting analyses — final deliverables

The operationalization layer. These do not make decisions and do not add analysis — they
make the analysis already produced by Phases 1-24 visible, prioritized, governable, and
manageable. All nine subcommands live in one tool (`tools/operator_layer.py`), read existing
artifacts, fail closed on a posture scan, and change nothing: no corpus write, no debt
change, no live behavior, no replay, no approval.

## 1. Implementation summary

- **Phase 25 `cockpit`** — single-pane operational view: framework maturity and health,
  active high/critical risks, evidence freshness, debt status, review workload, capture
  priorities, fragile sites, unsupported-conclusion count. Answers "what matters right now?"
  Outputs: `operator_cockpit.json`, `operator_dashboard.md`.
- **Phase 26 `portfolio`** — ranks all sites by combined risk, uncertainty, information gain,
  review burden, and maintenance cost, with per-dimension top-5 lists. Answers "where should
  effort go next?" Outputs: `portfolio_priority_report.md`, `portfolio_rankings.json`.
- **Phase 27 `capacity`** — forecasts reviewer workload: pending effort in minutes, approval
  backlog, queue-growth sites, the primary bottleneck, and whether it fits weekly capacity.
  Answers "will human review scale?" Outputs: `review_capacity_report.md`,
  `review_forecast.json`.
- **Phase 28 `governance`** — the meta-check: re-runs the posture scan over every produced
  artifact and verifies the governance invariants (recognition-only / no replay / human gates
  / corpus + debt read-only). Answers "are we still operating inside the rules?" Outputs:
  `governance_compliance_report.md`, `governance_findings.json`.
- **Phase 29 `memory`** — institutional knowledge: recurring portfolio-wide drift patterns,
  major discoveries and durable lessons from the corpus. Answers "what should never need to
  be rediscovered?" Outputs: `institutional_memory.json`, `lessons_learned_report.md`.
- **Phase 30 `exec`** — leadership summary in daily/weekly/monthly/snapshot views: what is
  healthy, what is risky, least defensible, what needs review, what evidence is missing, what
  should happen next. Outputs: `executive_summary.md`, `executive_dashboard.json`.
- **Cross-cut `resources`** — effort vs. risk per site, flagging over- and under-invested
  mismatches. Output: `resource_allocation_report.md`.
- **Cross-cut `reviewroi`** — which review categories actually change outcomes (via Phase 14
  verdict-change queues) vs. rarely do. Output: `review_roi_report.md`.
- **Cross-cut `bottlenecks`** — review / capture / evidence / scaling constraints. Output:
  `operational_bottlenecks_report.md`.

Reuse: every per-site signal comes from existing Phase 7/16/18/5 artifacts; framework-level
inputs are the Phase 22/24/21 outputs; debt is read-only via `debt_report`; the governance
scan reuses the same `posture_scan` every prior phase used. No new scoring logic — these
re-present and rank what already exists.

## 2. Validation and testing summary

`test_phases2530.py` builds a two-then-three-site portfolio and proves: the cockpit surfaces
maturity, fragile sites, high risks, and debt; the portfolio ranks the high-risk/high-burden
site first; capacity correctly reports that 60 minutes of pending review does not fit a
40-minute budget; governance scans clean artifacts as compliant AND catches a planted
`token=SECRET123` + `page.goto` violation (2 findings); institutional memory captures
recurring selector drift; the executive summary produces a weekly view with the right
next-focus sites; and the cross-cuts flag a neglected high-risk site as under-invested,
compute review ROI, and identify the scaling bottleneck. One real test-logic gap was caught
and fixed (the resource-mismatch case needed a genuine zero-effort/high-risk site to
exercise the path). All pass. The 157 relevant engine tests still pass.

## 3. Posture verification summary

Every subcommand runs the shared fail-closed check before writing. The global test confirms
no signing value and no replay content in any of the nine phases' own outputs. Phase 28 is
itself the institutionalized version of this guarantee — it scans the entire artifact tree
and was demonstrated to catch real violations, so posture compliance is now continuously
monitorable rather than only asserted. The corpus is read-only and verified unchanged at 34
entries with debt 0/0/2 after the full run.

## 4. Operator cockpit summary

The cockpit collapses twenty-four phases into one screen answering "what matters right now":
the framework's maturity and health at the top, then the active high/critical risks, the
fragile and at-risk sites, stale evidence, the review and capture workload, and any
unsupported conclusions. It is the daily starting point — read-only, with every action still
routed through the existing human gates.

## 5. Portfolio prioritization summary

Sites are ranked into a single ordered worklist by risk, uncertainty, review burden, and
maintenance cost, plus per-dimension top-5 lists so the operator can slice by "highest risk"
or "highest information gain" directly. The answer to "where should effort go next?" is now a
ranked list, and the resource-allocation cross-cut flags where current effort is mismatched to
risk.

## 6. Review workload summary

Capacity planning turns the queues into a workload forecast: pending effort in minutes against
a configurable weekly budget, the approval backlog, the sites likely to add future load, and
the primary bottleneck (review vs. approvals). It answers "will human review scale?" with a
concrete yes/no against capacity — and the review-ROI cross-cut shows which review categories
are worth the time.

## 7. Governance compliance summary

Governance monitoring is the standout of this batch: it operationalizes the posture guarantee
that every prior phase asserted individually. It scans every artifact for signing values,
replay/executable content, and auto-action language, and reports the invariants as holding or
violated. It was demonstrated catching a planted violation, so "are we still operating inside
the rules?" is now answerable continuously and mechanically, not by trust.

## 8. Institutional knowledge summary

The memory layer preserves what should not be rediscovered: recurring drift patterns across
the portfolio, the major discoveries and model corrections recorded in the corpus, and the
durable framework-level lessons. It turns scattered history into a single reference so the
same failures and findings do not have to be relearned.

## 9. Highest-ROI next phase after Phase 30

The analytical and operational stack is now complete: the framework recognizes, scores,
explains, forecasts, audits, prioritizes, governs, and summarizes — all read-only,
human-gated, recognition-only. There is no remaining high-value pure-software phase that
stays on the safe side of the posture; further analysis layers would be diminishing returns
over the same evidence. The single highest-ROI action is no longer a build at all — it is
**capturing the real perturbation pairs that retire the two open validation-debt items**
(VC-0017 player_config axis, VC-0018 workflow axis), which is the one thing the whole stack
keeps surfacing as outstanding and the one thing synthetic analysis cannot do. If a build is
wanted, the only one that adds operational value without adding analysis is wiring these
read-only views into the existing operator UI as a live cockpit so they are consulted daily
rather than generated on demand — explicitly still read-only, still human-gated, with no new
detector behavior. Anything beyond that (auto-approval, closing the loop, acting on the
rankings) is the posture boundary and would require an explicit, recorded posture decision —
not an increment.
