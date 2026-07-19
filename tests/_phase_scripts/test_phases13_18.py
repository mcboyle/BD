import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable; T="tools/trust_intelligence.py"
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p

# PHASE 13 calibrate — explicit outcomes so it's deterministic
outcomes=[{"subsystem":"selector","predicted":0.9,"success":True},
          {"subsystem":"selector","predicted":0.9,"success":False},  # 0.9 predicted, 50% observed -> overconfident
          {"subsystem":"rendition","predicted":0.5,"success":True},
          {"subsystem":"rendition","predicted":0.5,"success":True}]  # 0.5 predicted, 100% observed -> underconfident
of=w("outcomes.json",outcomes)
o13=os.path.join(d,"cal"); subprocess.run([py,T,"calibrate","--site","bros","--outcomes",of,"--out-dir",o13],check=True)
cal=json.load(open(os.path.join(o13,"confidence_calibration.json")))
assert cal["n_observations"]==4
assert cal["overconfidence_rate"]>0, "should detect overconfidence in the 0.9/50% bucket"
assert cal["underconfidence_rate"]>0, "should detect underconfidence in the 0.5/100% bucket"
print(f"TEST P13 PASS: calibration over={cal['overconfidence_rate']} under={cal['underconfidence_rate']} err={cal['calibration_error']}")

# PHASE 14 regress
old=w("old.json",{"maturity_state":"Stable","selector_decisions":[{"selector":"a[download]","action":"trust"}],"drift_flags":[]})
new=w("new.json",{"maturity_state":"Fragile","selector_decisions":[{"selector":"a[download]","action":"distrust"}],"drift_flags":["selector_drift"]})
o14=os.path.join(d,"reg"); subprocess.run([py,T,"regress","--site","bros","--old-snapshot",old,"--new-snapshot",new,"--out-dir",o14],check=True)
reg=json.load(open(os.path.join(o14,"verdict_drift_history.json")))
fields={c["field"] for c in reg["verdict_changes"]}
assert "maturity_state" in fields and "selector_policy:a[download]" in fields and "drift_flags" in fields, fields
print(f"TEST P14 PASS: {reg['n_changes']} verdict changes flagged (maturity, selector, drift)")

# PHASE 15 capquality — good capture vs no-goal
def cap(goal,dom):
    c={"network_log":[{"url":"https://cdn/v/x/720p/i.m3u8" if goal else "https://cdn/style.css"}]}
    if dom: c["dom_log"]=[{"data":{"node":{"tagName":"a"},"adds":[1,2,3]}}]; c["dom_log_count"]=1
    return c
good=w("good.json",cap(True,True)); nogoal=w("nogoal.json",cap(False,False))
o15=os.path.join(d,"cq")
subprocess.run([py,T,"capquality","--capture",good,"--out-dir",o15],check=True)
gq=json.load(open(os.path.join(o15,"capture_quality.json")))
subprocess.run([py,T,"capquality","--capture",nogoal,"--out-dir",o15+"2"],check=True)
nq=json.load(open(os.path.join(o15+"2","capture_quality.json")))
assert gq["quality_band"] in ("Good","Excellent","Usable") and nq["quality_band"]=="Discard"
print(f"TEST P15 PASS: good→{gq['quality_band']}, no-goal→{nq['quality_band']}")

# PHASE 16 forecast — repeated+structural drift -> high risk
dh=w("dh.json",[{"drift_flags":["selector_drift"]},{"drift_flags":["selector_drift","structural_drift"]},{"drift_flags":["structural_drift"]}])
ch=w("ch.json",[{"at":"2026-01-01T00:00:00Z","avg_confidence":0.9},{"at":"2026-02-01T00:00:00Z","avg_confidence":0.6}])
hs=w("hs.json",{"maturity_state":"Watch"})
o16=os.path.join(d,"fc"); subprocess.run([py,T,"forecast","--site","bros","--drift-history",dh,"--confidence-history",ch,"--site-health-score",hs,"--out-dir",o16],check=True)
fc=json.load(open(os.path.join(o16,"drift_forecast.json")))
assert fc["probability_enter_broken"]>0.4 and fc["signals"]["confidence_declining"]==True
print(f"TEST P16 PASS: P(broken)={fc['probability_enter_broken']} declining={fc['signals']['confidence_declining']}")

# PHASE 17 simulate
dec=w("dec.json",{"selector_decisions":[{"selector":"a[download]","action":"trust"},{"selector":".x","action":"distrust"}]})
cand=w("cand.json",{"proposed_field_changes":{"known_rendition_descriptors":{"added":["1440p"]}}})
hs2=w("hs2.json",{"maturity_state":"Watch"})
o17=os.path.join(d,"sim"); subprocess.run([py,T,"simulate","--site","bros","--automation-decision-report",dec,"--profile-update-candidate",cand,"--site-health-score",hs2,"--out-dir",o17],check=True)
sim=json.load(open(os.path.join(o17,"approval_simulation.json")))
assert "a[download]" in sim["if_approved"]["selectors_promoted"]
assert sim["if_approved"]["maturity"]["projected"]=="Stable"  # Watch+promotions, no distrust-only
assert "ONLY" in sim["_status"].upper()
print(f"TEST P17 PASS: simulate previews promotions + maturity {sim['if_approved']['maturity']}")

# PHASE 18 freshness — old timestamp -> stale/expired
import datetime
oldts=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=120)).isoformat()
chf=w("chf.json",[{"at":oldts,"avg_confidence":0.9}])
o18=os.path.join(d,"fr"); subprocess.run([py,T,"freshness","--site","bros","--confidence-history",chf,"--out-dir",o18],check=True)
fr=json.load(open(os.path.join(o18,"evidence_freshness.json")))
assert fr["freshness"]["selectors"]=="Expired", fr["freshness"]
print(f"TEST P18 PASS: 120d-old evidence → {fr['freshness']['selectors']}")

# CROSS-CUT failures / impact / family
o_f=os.path.join(d,"fail"); subprocess.run([py,T,"failures","--site","bros","--drift-history",dh,"--out-dir",o_f],check=True)
fi=open(os.path.join(o_f,"failure_intelligence_report.md")).read()
assert "Selector Failure" in fi and "Workflow Failure" in fi
print("TEST CROSS-1 PASS: failure intelligence classifies selector+workflow")
o_i=os.path.join(d,"imp"); subprocess.run([py,T,"impact","--site","bros","--drift-history",dh,"--out-dir",o_i],check=True)
ii=json.load(open(os.path.join(o_i,"evidence_impact.json")))
assert any(o["impact"]=="Critical" for o in ii["observations"]), "structural drift -> Critical"
print("TEST CROSS-2 PASS: evidence impact rates structural drift Critical")
mem=w("mem.json",{"bros":{"player_family":"jwplayer_family"},"site2":{"player_family":"jwplayer_family"}})
root=os.path.join(d,"sites"); 
for s in ("bros","site2"):
    sd=os.path.join(root,s); os.makedirs(sd)
    open(os.path.join(sd,"drift_history.json"),"w").write(json.dumps([{"drift_flags":["selector_drift"]}]))
    open(os.path.join(sd,"site_health_score.json"),"w").write(json.dumps({"maturity_state":"Stable","scores":{"overall":0.8}}))
o_fm=os.path.join(d,"fam"); subprocess.run([py,T,"family","--site-family-membership",mem,"--sites-root",root,"--out-dir",o_fm],check=True)
fh=open(os.path.join(o_fm,"family_health_report.md")).read()
assert "jwplayer_family" in fh
print("TEST CROSS-3 PASS: family-level aggregation")

# GLOBAL posture
alltext=""
for od in (o13,o14,o15,o16,o17,o18,o_f,o_i,o_fm):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
assert "token=" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY"
print("TEST GLOBAL PASS: no signing values, no replay content across P13-18 + cross-cuts")
print("\nALL PHASE 13-18 TESTS PASS")
