# Corpus-entry template

Use this to record a reviewed finding as a validation-corpus entry. This is a STRUCTURAL
template — it makes no claim itself. A human reviewer fills it after the corpus-review
workflow and appends it manually (append-only). No tool writes the corpus.

Fields (mirror the existing corpus schema):

- id            : assigned by the reviewer at append time (e.g. VC-00NN). Leave null in drafts.
- date          : date the entry is appended (ISO).
- version       : framework version at which the finding was recorded.
- subject       : short subject of the finding (what it is about).
- category      : one of {assumption, confidence_cap, drift_verdict, perturbation_rule,
                  sensitivity_flag} — match an existing category; do not invent.
- conclusion_class : one of {anomaly, capability_gap, framework_level, method_validation,
                  model_correction}.
- basis_kind    : how the finding is grounded (the evidence basis). Be specific and honest.
- prediction    : what was predicted/claimed.
- observation   : what was actually observed in the evidence.
- outcome       : {untested, confirmed, falsified, partial}. Drafts are 'untested'.
- evidence      : pointer to the real captures/artifacts behind it. Real evidence only.
- resolves      : id(s) of prior entries this one closes/supersedes, if any (else null).
- notes         : caveats. Recognition-only; signing by marker name only; URLs query-stripped.

Rules:
- Real evidence only — synthetic/simulated evidence cannot back a corpus entry or retire debt.
- Append-only. Corrections are NEW entries that `resolve` the old one, not silent edits.
- Outcome stays 'untested' until a human confirms it against the evidence.
