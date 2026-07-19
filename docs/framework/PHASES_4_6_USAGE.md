# Phases 4-6 — closed-loop learning, evidence-gated policy, operations dashboard

Three tools that sit ON TOP of Phases 1-3, turning their artifacts into reviewable
learning, policy decisions, and operator dashboards. They add NO detector behavior — they
aggregate, score, and report. None writes the profile, selectors, or corpus; none retires
debt; none reuses signing values or replays anything. Recognition-only posture unchanged.
All three go in `tools/`.

## Phase 4 — closed_loop_learning.py
Compares the stored profile to new evidence, tracks confidence/drift history across runs,
distinguishes one-off from repeated drift and confirmed from weak evidence.

    python3 tools/closed_loop_learning.py --site bros \
        --site-profile site_profile.json --selector-confidence selector_confidence.json \
        --live-drift live_drift_observation.json --download-decision download_decision_report.json \
        --prior-confidence-history confidence_history.json \   # from last run
        --prior-drift-history drift_history.json \             # from last run
        --out-dir ./closed_loop/bros

Outputs: profile_update_candidate.json, profile_diff_report.md, site_learning_report.md,
confidence_history.json, drift_history.json. All updates suggested-only.

## Phase 5 — evidence_policy.py
Turns accumulated evidence into trust/distrust/warn/request-review decisions. Takes no
action — queues work for a human.

    python3 tools/evidence_policy.py --site bros \
        --selector-confidence selector_confidence.json \
        --drift-history ./closed_loop/bros/drift_history.json \
        --confidence-history ./closed_loop/bros/confidence_history.json \
        --profile-update-candidate ./closed_loop/bros/profile_update_candidate.json \
        --out-dir ./policy/bros

Outputs: automation_policy.md, automation_decision_report.json, manual_review_queue.md,
capture_request_queue.md, profile_approval_queue.md. Hard rules: structural drift always
requires human review; signing-pattern drift NEVER triggers token reuse (warn + capture only);
one observation may suggest, repeated consistent observations raise confidence, conflicting
observations lower it.

## Phase 6 — ops_dashboard.py
Rolls everything into day-to-day dashboards + the final report. Reads the corpus debt report
read-only (never writes/retires).

    python3 tools/ops_dashboard.py --sites-root ./all_sites --out-dir ./dashboard
    # ./all_sites/<site>/ holds each site's Phase 1-5 artifacts.

Outputs: site_dashboard.md, framework_operations_dashboard.md, review_queue.md,
pending_evidence.md, operator_next_actions.md, end_of_phase_report.md (answers the five
questions: what runs automatically / needs approval / needs captures / stays prohibited /
next highest-ROI phase).

## Boundaries (enforced)
Each tool runs a posture scan over its output and fails closed on any signing value. None
emits executable/replay content, promotes selectors, writes the corpus, or retires debt.
Corpus access in Phase 6 is read-only. Verified: corpus unchanged at 34 entries.

## Tests
`test_phases456.py` proves: drift history accumulates across runs (one-off→repeated),
profile updates are suggested-only, selectors are trusted/distrusted by confidence, the
signing-drift no-token-reuse hard rule is present, policy takes no action, the end-of-phase
report answers all five questions, validation debt surfaces read-only, and no signing values
or replay content appear across any Phase 4/5/6 output.
