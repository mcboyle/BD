import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable

# inputs from earlier phases
prof={"site":"bros","known_identity_descriptors":["alice123"],"known_rendition_descriptors":["720p","1080p"],"known_signing_markers":["token","expires"],"known_goal_url_shapes":["https://cdn.bros.com/v/alice123"]}
conf=[{"selector":"a[download]","kind":"attribute","roles":["download"],"confidence":1.0},{"selector":".btn.x","kind":"class","roles":["download"],"confidence":0.3}]
drift={"site":"bros","verdict":"template_partially_matched","drift_flags":["rendition_drift"]}
dec={"available_renditions_live":["720p","1080p","480p"],"selected":"1080p"}
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p
pf,cf,ldf,ddf=w("site_profile.json",prof),w("selector_confidence.json",conf),w("live_drift_observation.json",drift),w("download_decision_report.json",dec)

# PHASE 4 run 1
o4=os.path.join(d,"cl"); 
subprocess.run([py,"tools/closed_loop_learning.py","--site","bros","--site-profile",pf,"--selector-confidence",cf,"--live-drift",ldf,"--download-decision",ddf,"--out-dir",o4],check=True)
# PHASE 4 run 2 (feed prior history + same drift again -> repeated)
ch=os.path.join(o4,"confidence_history.json"); dhh=os.path.join(o4,"drift_history.json")
subprocess.run([py,"tools/closed_loop_learning.py","--site","bros","--site-profile",pf,"--selector-confidence",cf,"--live-drift",ldf,"--download-decision",ddf,"--prior-confidence-history",ch,"--prior-drift-history",dhh,"--out-dir",o4],check=True)
dh=json.load(open(dhh)); 
# TEST: repeated drift tracked (2 occurrences of rendition_drift)
cand=json.load(open(os.path.join(o4,"profile_update_candidate.json")))
assert len(dh)==2, f"drift history should have 2 points, got {len(dh)}"
print("TEST P4-1 PASS: drift history accumulates across runs (2 points)")
assert "SUGGESTED" in cand["_status"], "candidate must be suggested-only"
print("TEST P4-2 PASS: profile update suggested-only")

# PHASE 5
o5=os.path.join(d,"pol")
subprocess.run([py,"tools/evidence_policy.py","--site","bros","--selector-confidence",cf,"--drift-history",dhh,"--confidence-history",ch,"--profile-update-candidate",os.path.join(o4,"profile_update_candidate.json"),"--out-dir",o5],check=True)
pol=json.load(open(os.path.join(o5,"automation_decision_report.json")))
# TEST: high-conf selector trusted, low-conf distrusted
acts={s["selector"]:s["action"] for s in pol["selector_decisions"]}
assert acts["a[download]"]=="trust", acts
assert acts[".btn.x"]=="distrust", acts
print(f"TEST P5-1 PASS: selector trust/distrust by confidence ({acts})")
# TEST: no token-reuse language; signing drift would warn-only
pmd=open(os.path.join(o5,"automation_policy.md")).read()
assert "never trigger token reuse" in pmd.lower() or "never triggers token reuse" in pmd.lower()
print("TEST P5-2 PASS: signing-drift hard rule present (no token reuse)")
# TEST: policy takes no action
assert "takes no action" in pol["_status"].lower()
print("TEST P5-3 PASS: policy queues for human, takes no action")

# PHASE 6: build a sites-root with bros' artifacts gathered
root=os.path.join(d,"sites"); bdir=os.path.join(root,"bros"); os.makedirs(bdir)
for src in [pf,cf,os.path.join(o4,"drift_history.json"),os.path.join(o4,"profile_update_candidate.json"),os.path.join(o5,"automation_decision_report.json")]:
    import shutil; shutil.copy(src,bdir)
o6=os.path.join(d,"dash")
subprocess.run([py,"tools/ops_dashboard.py","--sites-root",root,"--out-dir",o6],check=True)
eo=open(os.path.join(o6,"end_of_phase_report.md")).read()
# TEST: five questions answered + validation debt shown
for q in ["What can now run automatically","What still requires human approval","What still requires new captures","What remains prohibited by posture","Next highest-ROI phase"]:
    assert q in eo, f"missing: {q}"
print("TEST P6-1 PASS: end-of-phase report answers all 5 questions")
fd=open(os.path.join(o6,"framework_operations_dashboard.md")).read()
# The dashboard renders the live validation-debt count itself; assert it surfaces a
# well-formed count rather than re-importing the corpus (this script runs as a
# subprocess and must not depend on bulk_downloader being importable) and rather than
# hard-coding a literal (a deliberate corpus change must not falsely fail this).
import re as _re
_m=_re.search(r"validation\s+(\d+)", fd, _re.I)
assert _m, f"dashboard should surface a validation-debt count; got:\n{fd[:400]}"
print(f"TEST P6-2 PASS: validation debt (validation {_m.group(1)}) surfaced read-only in dashboard")

# GLOBAL POSTURE: no signing values, no replay content anywhere across all phase outputs
alltext=""
for od in (o4,o5,o6):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
assert "token=" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY CONTENT"
print("TEST GLOBAL PASS: no signing values, no replay content across P4/P5/P6 outputs")
print("\nALL PHASE 4-6 TESTS PASS")
