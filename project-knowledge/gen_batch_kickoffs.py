#!/usr/bin/env python3
"""gen_batch_kickoffs.py -- regenerate the 58 per-batch kickoff messages + INDEX
at v3.66.539 from the LIVE review artifacts (deterministic; no stale 532 numbers).

Inputs (all under /home/claude/review/artifacts unless noted):
  - BATCH_ORDER.json   : risk-ranked batch -> {files, max_risk, mean_risk, n}
  - RISK_SCORES.json   : per-file {lines, max_cc, prior_defect, secrets, sink_weight, risk}
  - REVIEW_STATE.json  : which files are 'reviewed' (=> batch DONE status)
Outputs:
  - <out>/KICKOFF_<BATCH>.md   x58
  - <out>/00_INDEX.md

Mirrors the shipped 532 kickoff shape (per-batch bootstrap + loop + verify_audit
self-gate + witness schema) but corrected for 539 reality:
  * version 3.66.539, 58 batches, TOOLS_OTHER-08 present
  * tooling = hydrate audit_state_v3_66_539_01.zip + the built L0 tools (NOT the
    old bd_review_tools_FULL_kit / folded-532 framing)
  * parallel-merge rule: sessions write ONLY their deliverable, do NOT review_merge
  * per-file signal tuple recomputed from RISK_SCORES.json at 539
"""
import json, os, argparse

ART = "/home/claude/review/artifacts"
VERSION = "3.66.539"


def load():
    bo = json.load(open(f"{ART}/BATCH_ORDER.json"))
    rs = json.load(open(f"{ART}/RISK_SCORES.json"))["scores"]
    st = json.load(open(f"{ART}/REVIEW_STATE.json"))
    reviewed = {p for p, v in st["files"].items()
                if isinstance(v, dict) and v.get("status") == "reviewed"}
    order = [e["batch"] if isinstance(e, dict) else e for e in bo["order"]]
    batches = bo["batches"]
    return order, batches, rs, reviewed


def sig(path, rs):
    r = rs.get(path, {})
    return (r.get("lines", 0), r.get("max_cc", 0), r.get("prior_defect", 0.0),
            r.get("secrets", 0), r.get("sink_weight", 0), r.get("risk", 0.0))


def batch_status(bn, files, reviewed):
    fs = set(files)
    if bn == "CAP-01":
        return "REVERIFY"   # pilot-covered at 532; needs a 539 deliverable
    if fs and fs <= reviewed:
        return "DONE"
    return ""


def surface_tags(files, rs):
    secrets = sum(rs.get(f, {}).get("secrets", 0) for f in files)
    sinks = sum(rs.get(f, {}).get("sink_weight", 0) for f in files)
    maxcc = max((rs.get(f, {}).get("max_cc", 0) for f in files), default=0)
    prior = any(rs.get(f, {}).get("prior_defect", 0.0) >= 1.0 for f in files)
    tags = []
    if secrets: tags.append(f"secrets({secrets})")
    if sinks: tags.append(f"sinks({sinks})")
    if maxcc >= 50: tags.append(f"cc({maxcc})")
    if prior: tags.append("prior-defect")
    return " ".join(tags) or "-", secrets, sinks, maxcc, prior


def files_by_risk(files, rs):
    return sorted(files, key=lambda f: rs.get(f, {}).get("risk", 0.0), reverse=True)


def index_row(i, bn, meta, rs, reviewed):
    files = files_by_risk(meta["files"], rs)
    lead = files[0]
    parts = []
    for f in files:
        L, cc, pr, se, sk, _ = sig(f, rs)
        star = "*" if f == lead else ""
        parts.append(f"{star}{os.path.basename(f)} ({L}/{cc}/{pr}/{se}/{sk})")
    tags, *_ = surface_tags(files, rs)
    st = batch_status(bn, meta["files"], reviewed)
    return (f"| {i} | **{bn}** | {st} | {meta['max_risk']:.3f} | "
            f"{' · '.join(parts)} | {tags} |")


INDEX_HEADER = f"""# BulkDownloader audit batches — full templating index (v{VERSION})

Risk-ranked (`BATCH_ORDER.json`, re-derived at {VERSION}). `status`: **DONE** = audited+folded at 539 · **REVERIFY** = pilot-covered at 532, needs a 539 deliverable · **ASSIGNED** = in flight · (blank) = unassigned. Signal columns drive read-priority.

Each `KICKOFF_<BATCH>.md` is generated from this data — regenerate with `gen_batch_kickoffs.py` after any consolidation (so `status` + counts stay current).

| # | batch | status | max_risk | files (SLOC / max_cc / prior / secrets / sink) | lead surface |
|---|---|---|---|---|---|"""

INDEX_FOOTER = """
*lead file marked `*`. signal tuple = (SLOC / max_cc / prior_defect / secrets / sink_weight). surface: secrets(N)=credential/token density → AUTH/secret-floor lens · sinks(N)=subprocess/path density → injection/traversal lens · cc(N)=parsing complexity · prior-defect=file burned before (elevated residual-bug odds).*

*SLOC convention note: the per-file `SLOC` in the tuple is `l0_extract`'s **non-blank** line count (sum over `RISK_SCORES.json` = 247,549). The program-level "277,105 SLOC" figure is `audit_partition.py`'s **raw** line count (`wc -l`, includes blanks). Both are correct, different metrics — the partition uses raw for balance, the risk score uses non-blank for density. A file's raw `wc -l` runs higher (e.g. runner.py: 2884 non-blank / 3153 raw).*

Total: {n} batches with files ({done} DONE, {rev} REVERIFY, {rest} unassigned) · 1028 files · 247,549 non-blank SLOC / 277,105 raw lines.

Per-batch kickoff = `KICKOFF_<BATCH>.md`. All are read-only parallel sessions: **write ONLY your deliverable, do NOT `review_merge`** (that's the serial consolidation). See `AUDIT_PARALLEL_BATCH_HANDOFF_v3_66_539.md`."""


def kickoff(bn, rank, n_total, meta, rs, reviewed):
    files = files_by_risk(meta["files"], rs)
    lead = files[0]
    tags, secrets, sinks, maxcc, prior = surface_tags(files, rs)
    st = batch_status(bn, meta["files"], reviewed)
    tag = ""
    if st == "DONE":
        tag = "  [DONE at 539 — do not take]"
    elif st == "REVERIFY":
        tag = "  [REVERIFY — pilot was 532; produce the 539 deliverable]"

    lines = []
    lines.append(f"BulkDownloader — audit kickoff · BATCH {bn}{tag}")
    lines.append(f"Read-only parallel wave. This message = batch {bn} "
                 f"(risk-rank #{rank} of {n_total}, max_risk {meta['max_risk']:.3f}), v{VERSION}.")
    lines.append("Parallel sessions each claim a DIFFERENT batch; do not take one already DONE/ASSIGNED.")
    lines.append("")
    lines.append("BOOTSTRAP to v3.66.539 (NOT 532):")
    lines.append("  bash /mnt/project/setup.sh ; bd-boot  (resumable — if it times out on prestage,")
    lines.append("  `bd-prestage --max 4` in a loop until 'all staged', then) bd-install ; bd-venv ;")
    lines.append("  bd-preflight (PASS, 2241 files) ; bd-state (PASS, 7 guards). Confirm live=built=deployed=539,")
    lines.append("  static-KB INTEGRITY OK (539e). Read STATE.json + post_539_addendum + KB_HANDOFF_v3_66_539e.md.")
    lines.append("")
    lines.append("HYDRATE the shared audit state (539-derived; do NOT rebuild the graph):")
    lines.append("  unzip -o audit_state_v3_66_539_01.zip -d /home/claude   # -> /home/claude/review/{tools,artifacts,witnesses}")
    lines.append("  python3 -m venv ~/rev && ~/rev/bin/pip install radon vulture bandit   # battery OFF the service venv")
    lines.append("  (cd <audit_tools_offline_pack_v3_66_540>/ && bash install_audit_tools_offline.sh)")
    lines.append("  export PATH=\"$HOME/.audit_tools/bin:$PATH\"   # OpenGrep 1.25.0 + ast-grep 0.44.0 + mutmut 3.6.0")
    lines.append("  Graph pin dd31bcaf… is the 539 output (deterministic; re-run l0_extract/graph_build/risk_score only to re-prove).")
    lines.append("  Sanity: cd review/tools && env PYTHONPATH=/home/claude/work python3 run_witnesses.py  # 12/12 green")
    lines.append("")
    lines.append("You are an AUDIT session — READ-ONLY. No cut / bump / guard-edit / baseline-update / tracker-write /")
    lines.append("stash / deploy. Do NOT modify /home/claude/work. Write ONLY your own AUDIT_" + bn + "_v3_66_539.{md,json}.")
    lines.append("Stamp the version FROM STATE.json (3.66.539) — derive it, don't hardcode blindly.")
    lines.append("")
    lines.append(f"YOUR BATCH: {bn} (max_risk {meta['max_risk']:.3f}, risk-rank #{rank} of {n_total}). "
                 f"Files = review/audit_manifests/{bn}.txt:")
    for f in files:
        L, cc, pr, se, sk, rk = sig(f, rs)
        mark = "  <-- lead (highest risk), read FIRST" if f == lead else ""
        lines.append(f"  - {f}  (SLOC {L}, max_cc {cc}, prior_defect {pr}, secrets {se}, sink_weight {sk}, risk {rk:.3f}){mark}")
    lines.append("Surface focus (from the measured 539 signal):")
    if secrets:
        lines.append(f"  - SECRETS lens (density {secrets}): does any file persist/emit a credential/token/signed-URL/")
        lines.append("    challenge artifact? Cross-check the CAP-01 floor classes (I0008 SoT: code/state/apikey/challenge/")
        lines.append("    captcha/nonce/otp/token/authorization/csrf/bearer). AUTOMATION_POLICY forbids these in templates/outputs.")
    if sinks:
        lines.append(f"  - SINKS lens (weight {sinks}): subprocess/path/SQL sinks. Injection — argv `--` end-of-options")
        lines.append("    separator (cf. F-RUN01-02), SQL parameterization; SSRF — is_global host check not a denylist")
        lines.append("    (cf. F-RUN01-01, canonical fix = provider_resolve_impl/_common._classify_ip); traversal — zip-slip /")
        lines.append("    safe_dest before os.path.join.")
    if maxcc >= 50:
        lines.append(f"  - COMPLEXITY: highest-CC function here is max_cc={maxcc} — read it first; dense control-flow hides")
        lines.append("    type/None + error-contract bugs (NaN-evades-bounds cf. F-RUN01-03, swallowed except).")
    if prior:
        lines.append("  - PRIOR-DEFECT: a file here has burned before — elevated residual-bug odds; re-check the")
        lines.append("    historically-fixed classes didn't regress.")
    lines.append("")
    lines.append("RUN THE LOOP (validated on CAP-01 + RUN-01):")
    lines.append(f" 1. Pull your files' neighborhood + open findings + invariants from KNOWLEDGE_GRAPH.db +")
    lines.append(f"    REVIEW_STATE.json + INVARIANTS.json, keyed off review/audit_manifests/{bn}.txt.")
    lines.append(" 2. Mechanical pre-pass (LEADS, not verdicts): `bash tools/bd-audit-taint " + bn + "` (OpenGrep")
    lines.append("    --taint-intrafile; TUNE rules/ssrf_cmdi_starters.yaml to THIS batch's real source/sink/sanitizer")
    lines.append("    names first — see rules/run01_tuned.yaml for the pattern) + ast-grep for risky call-sites.")
    lines.append(" 3. Read EVERY line to the §4 rubric: auth · injection · SSRF · secrets · error-contract · type/None ·")
    lines.append("    concurrency · resource · input-validation · dead-code. The 18 DEFECT_PATTERN_CATALOG classes are the")
    lines.append("    checklist. Each unit -> a finding (RED repro) or a recorded assurance. Guard files: SHA-verify, never edit.")
    lines.append(f" 4. Emit AUDIT_{bn}_v3_66_539.{{md,json}} on the RUN-01 template + a witnesses/{bn.lower().replace('-','')}_witnesses.py.")
    lines.append("    HONOR THE TOOL CONTRACT (stricter than the schema doc): line_range is a STRING; every finding needs a")
    lines.append("    `witness` field; every new_invariant needs id+status+witness; top-level needs tree_reverified_byte_identical.")
    lines.append("    Copy the shape of review/AUDIT_RUN-01_v3_66_539.json — it passes. Give each mechanical finding a")
    lines.append("    \"signature\" regex (verify_audit greps it, REJECTs if cited lines miss a match). New bug-classes -> new_patterns[].")
    lines.append(" 5. SELF-GATE before done:")
    lines.append(f"       bd python3 review/tools/verify_audit.py --audit AUDIT_{bn}_v3_66_539.json \\")
    lines.append(f"          --witnesses review/witnesses/<your>_witnesses.py --root /home/claude/work   # -> ACCEPT")
    lines.append("    Then reachability_ledger.py build + record any NEW boundary-deferred finding (name the resolving subsystem).")
    lines.append(" 6. Attest guard_touch=false / tracker_write=false; re-verify tree byte-identical (bd-preflight PASS).")
    lines.append("")
    lines.append("File findings RED-first; FIX NOTHING — fixes are a separate serial cut-chain AFTER a slice.")
    lines.append("DO NOT `review_merge` — you are one of N parallel sessions; concurrent merges clobber the ledger.")
    lines.append("Hand back ONLY AUDIT_" + bn + "_v3_66_539.{md,json}. The serial consolidation session merges the wave.")
    if bn.startswith("APP"):
        lines.append("")
        lines.append("NOTE (APP family): open cross-file deferral F-RUN01-01-app-sibling -> APP. app._is_url_public "
                     "(app.py:~4914) is the 3rd stale copy of the CGNAT-missing SSRF denylist; if app.py is in YOUR batch, "
                     "close it by delegating to provider_resolve_impl/_common._classify_ip (finding, not fix — RED repro only).")
    if bn == "CAP-01":
        lines.append("")
        lines.append("NOTE (REVERIFY): the CAP-01 pilot deliverable is AUDIT_CAP-01_v3_66_532.* (in the recovery archive). "
                     "Its witnesses (cap01_witnesses.py) are already green at 539. Produce the 539 deliverable the same way "
                     "RUN-01 did: re-confirm the pilot findings against 539 source, emit AUDIT_CAP-01_v3_66_539.{md,json}.")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/home/claude/review/kickoffs_539")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    order, batches, rs, reviewed = load()
    order = [b for b in order if b in batches]  # only batches with files
    n = len(order)

    rows = []
    done = rev = 0
    for i, bn in enumerate(order):
        meta = batches[bn]
        rows.append(index_row(i, bn, meta, rs, reviewed))
        open(os.path.join(a.out, f"KICKOFF_{bn}.md"), "w").write(
            kickoff(bn, i, n, meta, rs, reviewed))
        stt = batch_status(bn, meta["files"], reviewed)
        if stt == "DONE": done += 1
        elif stt == "REVERIFY": rev += 1

    idx = INDEX_HEADER + "\n" + "\n".join(rows) + INDEX_FOOTER.format(
        n=n, done=done, rev=rev, rest=n - done - rev)
    open(os.path.join(a.out, "00_INDEX.md"), "w").write(idx)
    print(f"generated {n} kickoffs + 00_INDEX.md into {a.out} "
          f"(DONE={done} REVERIFY={rev} unassigned={n-done-rev})")


if __name__ == "__main__":
    main()
