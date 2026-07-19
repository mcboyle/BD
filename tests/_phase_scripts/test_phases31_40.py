import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable; T="tools/validation_harness.py"
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p
root=os.path.join(d,"portfolio")
def site(name,m,o,pb,stale,nsel):
    sd=os.path.join(root,name); os.makedirs(sd)
    open(os.path.join(sd,"site_health_score.json"),"w").write(json.dumps({"maturity_state":m,"scores":{"overall":o}}))
    open(os.path.join(sd,"drift_forecast.json"),"w").write(json.dumps({"probability_enter_broken":pb,"probability_enter_fragile":pb+0.05}))
    open(os.path.join(sd,"evidence_freshness.json"),"w").write(json.dumps({"review_priority":["selectors"] if stale else []}))
    open(os.path.join(sd,"selector_confidence.json"),"w").write(json.dumps([{"selector":"a","confidence":0.9}]*nsel))
site("bros","Stable",0.80,0.1,False,2)      # overall 0.80 sits exactly on a band edge -> threshold-edge
site("edgy","Watch",0.49,0.5,True,1)        # 0.49 near 0.5 edge AND forecast 0.5 -> unstable

# PHASE 32 consistency
o32=os.path.join(d,"c"); subprocess.run([py,T,"consistency","--portfolio-root",root,"--out-dir",o32],check=True)
cons=json.load(open(os.path.join(o32,"unstable_decisions.json")))
assert cons["determinism_holds"]==True
assert any(u["site"]=="bros" for u in cons["unstable_decisions"]), "0.80 should be flagged near band edge"
assert any(u.get("field","").startswith("probability") for u in cons["unstable_decisions"]), "0.5 forecast flagged"
print(f"TEST P32 PASS: determinism holds; {len(cons['unstable_decisions'])} threshold-edge verdicts flagged")

# PHASE 33 benchmark — baseline then compare
o33=os.path.join(d,"b"); subprocess.run([py,T,"benchmark","--portfolio-root",root,"--out-dir",o33],check=True)
base=json.load(open(os.path.join(o33,"benchmark_scorecard.json")))
assert base["mode"]=="baseline_recorded"
print(f"TEST P33a PASS: baseline recorded ({base['n_sites']} sites)")
# change a site's health, re-run with baseline -> should detect the change
import shutil
open(os.path.join(root,"bros","site_health_score.json"),"w").write(json.dumps({"maturity_state":"Fragile","scores":{"overall":0.4}}))
o33b=os.path.join(d,"b2"); subprocess.run([py,T,"benchmark","--portfolio-root",root,"--baseline",os.path.join(o33,"benchmark_scorecard.json"),"--out-dir",o33b],check=True)
comp=json.load(open(os.path.join(o33b,"benchmark_scorecard.json")))
assert comp["mode"]=="compared" and any(c["site"]=="bros" for c in comp["changed_sites"]), "bros change should be detected"
print(f"TEST P33b PASS: comparison detects changed verdict (stability {comp['stability_rate']})")

# restore bros for remaining tests
open(os.path.join(root,"bros","site_health_score.json"),"w").write(json.dumps({"maturity_state":"Stable","scores":{"overall":0.80}}))

# PHASE 35 acquire — budget 1 should pick the highest-uncertainty/risk site (edgy)
o35=os.path.join(d,"a"); subprocess.run([py,T,"acquire","--portfolio-root",root,"--budget","1","--out-dir",o35],check=True)
acq=json.load(open(os.path.join(o35,"prioritized_capture_campaigns.json")))
assert len(acq["selected_plan"])==1 and acq["selected_plan"][0]["site"]=="edgy", acq["selected_plan"]
assert acq["validation_debt_campaign"]["items"] is not None
print(f"TEST P35 PASS: budget=1 selects highest-gain site ({acq['selected_plan'][0]['site']}); debt campaign separate")

# PHASE 37 release — compose a ready and a blocked case
cal=w("cal.json",{"calibration_error":0.1}); bench=w("bench.json",{"stability_rate":0.9}); reg=w("reg.json",{"n_changes":0}); gov=w("gov.json",{"compliant":True}); rk=w("rk.json",{"risk_register":[{"severity":"High"}]}); sc=w("sc.json",{"maturity":"Mature"})
o37=os.path.join(d,"r"); subprocess.run([py,T,"release","--confidence-calibration",cal,"--benchmark-scorecard",bench,"--verdict-changes",reg,"--governance-findings",gov,"--risk-register",rk,"--framework-scorecard",sc,"--out-dir",o37],check=True)
rel=json.load(open(os.path.join(o37,"release_scorecard.json")))
assert rel["release_ready"]==True, rel["blockers"]  # high risk ok, only critical blocks
print(f"TEST P37a PASS: ready release passes gate (ready={rel['release_ready']})")
# now a blocked case: critical risk + governance violation + regression
gov2=w("gov2.json",{"compliant":False}); rk2=w("rk2.json",{"risk_register":[{"severity":"Critical"}]}); reg2=w("reg2.json",{"n_changes":3})
o37b=os.path.join(d,"r2"); subprocess.run([py,T,"release","--confidence-calibration",cal,"--benchmark-scorecard",bench,"--verdict-changes",reg2,"--governance-findings",gov2,"--risk-register",rk2,"--framework-scorecard",sc,"--out-dir",o37b],check=True)
rel2=json.load(open(os.path.join(o37b,"release_scorecard.json")))
assert rel2["release_ready"]==False and set(["regression","governance","risk"])<=set(rel2["blockers"]), rel2["blockers"]
print(f"TEST P37b PASS: blocked release caught (blockers={rel2['blockers']})")

# GLOBAL posture
alltext=""
for od in (o32,o33,o33b,o35,o37,o37b):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
assert "token=" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY"
print("TEST GLOBAL PASS: no signing values, no replay content across P32/33/35/37")
print("\nALL NEW-PHASE TESTS PASS")
