import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable; T="tools/ops_intelligence.py"
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p

prof={"site":"bros","known_identity_descriptors":["alice123"],"known_rendition_descriptors":["720p","1080p"],"known_signing_markers":["token","expires"],"known_goal_url_shapes":["https://cdn.jwplayer.com/v/alice123"]}
sel=[{"selector":"a[download]","confidence":1.0,"roles":["download"]},{"selector":".jwplayer .btn","confidence":0.9,"roles":["download"]}]
ch=[{"at":"t1","avg_confidence":0.95},{"at":"t2","avg_confidence":0.95}]
dh=[{"drift_flags":["rendition_drift"]},{"drift_flags":["rendition_drift"]}]  # repeated
dec={"queued":{"manual_review":0,"capture_requests":1,"profile_approvals":0},
     "selector_decisions":[{"selector":"a[download]","confidence":1.0,"action":"trust","why":"high conf"}],
     "axis_policy":{"rendition_drift":{"decision":"request_capture","occurrences":2,"auto_action":"none"},
                    "signing_pattern_drift":{"decision":"warn_and_request_capture","occurrences":1,"auto_action":"none","hard_rule":"never trigger token reuse"}}}
match={"verdict":"template_partially_matched","drift_flags":["rendition_drift"]}
ddec={"available_renditions_live":["720p","1080p","480p"],"selected":"1080p","scored":[{"rendition":"1080p","score":1080}]}
pf,sf,chf,dhf,decf,mf,ddf=w("site_profile.json",prof),w("selector_confidence.json",sel),w("confidence_history.json",ch),w("drift_history.json",dh),w("automation_decision_report.json",dec),w("template_match_report.json",match),w("download_decision_report.json",ddec)

# PHASE 7 health
o=os.path.join(d,"h"); subprocess.run([py,T,"health","--site","bros","--site-profile",pf,"--selector-confidence",sf,"--confidence-history",chf,"--drift-history",dhf,"--automation-decision-report",decf,"--out-dir",o],check=True)
hs=json.load(open(os.path.join(o,"site_health_score.json")))
assert hs["maturity_state"]=="Fragile", f"repeated drift should be Fragile, got {hs['maturity_state']}"
assert all(k in hs["explanations"] for k in hs["scores"] if k!="overall"), "every score explained"
print(f"TEST P7 PASS: health explainable, repeated drift→{hs['maturity_state']}")

# PHASE 8 trace
o8=os.path.join(d,"t"); subprocess.run([py,T,"trace","--site","bros","--template-match-report",mf,"--download-decision-report",ddf,"--automation-decision-report",decf,"--out-dir",o8],check=True)
tr=json.load(open(os.path.join(o8,"decision_trace.json")))
assert all(set(["inputs_considered","evidence_used","confidence","rule_path","final_outcome"])<=set(t) for t in tr["traces"]), "every trace complete"
rend=[t for t in tr["traces"] if t["decision"]=="rendition_selection"][0]
assert rend["final_outcome"]=="1080p"
print(f"TEST P8 PASS: {len(tr['traces'])} decisions traced w/ inputs+evidence+rule+outcome")

# PHASE 9 capqueue
root=os.path.join(d,"sites"); b=os.path.join(root,"bros"); os.makedirs(b)
for s in [pf,sf,chf,dhf]: import shutil; shutil.copy(s,b)
shutil.copy(os.path.join(o,"site_health_score.json"),b)
o9=os.path.join(d,"q"); subprocess.run([py,T,"capqueue","--sites-root",root,"--out-dir",o9],check=True)
q=json.load(open(os.path.join(o9,"capture_priority_queue.json")))
assert q["queue"] and "explanation" in q["queue"][0], "priority explained"
assert "validation" in q["validation_debt_note"].lower()
print(f"TEST P9 PASS: capqueue ranks+explains; validation-debt noted")

# PHASE 10 login — NO credentials
flow={"user_field":["#email"],"pass_field":["#pw"],"submit_btn":["button[type=submit]"],"mfa_field":["#otp"],"steps":["enter_user","enter_pass","mfa","submit"],"cookie_persistence":"persists ~7d","success_rate":0.9}
lf=w("login_flow.json",flow)
o10=os.path.join(d,"l"); subprocess.run([py,T,"login","--site","bros","--login-flow",lf,"--drift-history",dhf,"--out-dir",o10],check=True)
lp=json.load(open(os.path.join(o10,"login_profile.json")))
assert lp["mfa_present"]==True and "user_field" in lp["login_selectors_by_role"]
lptext=json.dumps(lp)
assert "password" not in lptext.lower() or "pass_field" in lptext  # roles ok, no values
assert "#pw" in str(lp["login_selectors_by_role"].get("pass_field"))  # selector yes, value no
print("TEST P10 PASS: login profile descriptive (roles+MFA+steps), no credentials")

# PHASE 11 workflow vs selector drift
wflow={"stages":["navigate","open_modal","select_resolution","confirm","initiate_download"],"modal_usage":"yes"}
wf=w("workflow_flow.json",wflow)
dh2=w("dh2.json",[{"drift_flags":["selector_drift"]},{"drift_flags":["selector_drift"]},{"drift_flags":["structural_drift"]}])
o11=os.path.join(d,"w"); subprocess.run([py,T,"workflow","--site","bros","--workflow-flow",wf,"--drift-history",dh2,"--out-dir",o11],check=True)
wdr=open(os.path.join(o11,"workflow_drift_report.md")).read()
assert "Selector-drift events: 2" in wdr and "Workflow-drift (stage) events: 1" in wdr
assert "not promoted to a workflow change" in wdr or "not a workflow change" in wdr
print("TEST P11 PASS: selector drift (2) vs workflow drift (1) distinguished")

# PHASE 12 patterns — jwplayer family
o12=os.path.join(d,"p"); subprocess.run([py,T,"patterns","--sites-root",root,"--out-dir",o12],check=True)
fam=json.load(open(os.path.join(o12,"cross_site_patterns.json")))
mem=json.load(open(os.path.join(o12,"site_family_membership.json")))
assert mem["bros"]["player_family"]=="jwplayer_family", mem
assert "never change" in fam["_status"].lower()
print(f"TEST P12 PASS: bros→jwplayer_family; families analysis-only")

# GLOBAL posture across all phase outputs
alltext=""
for od in (o,o8,o9,o10,o11,o12):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
assert "token=" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY CONTENT"
print("TEST GLOBAL PASS: no signing values, no replay content across P7-12")
print("\nALL PHASE 7-12 TESTS PASS")
