# Reviewer checklist (human gate)

For each proposed corpus entry / promotion:

- [ ] Evidence is REAL (captures exist; not synthetic/simulated).
- [ ] Observation matches what the analysis actually produced.
- [ ] Defensibility (audit-readiness) is adequate (evidence + basis + traceability).
- [ ] No signing values anywhere in the artifacts; no replay/executable content.
- [ ] Decision recorded: accept / revise / reject / request-more-evidence.
- [ ] If accepted: entry finalized per corpus-entry template and appended manually
      (append-only; corrections are new entries that resolve the old).
- [ ] Debt retirement (if any): backed by real perturbation captures only.
- [ ] Re-ran corpus-consuming checks (audit/graph/assumptions) for coherence.

Release-specific:

- [ ] Readiness gate run; no critical-risk / governance / regression blockers.
- [ ] Governance scan clean over release artifacts.
- [ ] Full unit suite: 0 logic failures (live-env L-checks expected absent offline).
- [ ] build_release gates pass incl. POSTURE GATE (no raw-capture capability in zip).
