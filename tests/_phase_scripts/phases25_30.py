import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable; T="tools/operator_layer.py"
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p

# build a portfolio root with two sites carrying Phase 1-24 artifacts
root=os.path.join(d,"portfolio")
def site(name, maturity, overall, p_broken, stale, review, captures, approvals):
    sd=os.path.join(root,name); os.makedirs(sd)
    open(os.path.join(sd,"site_health_score.json"),"w").write(json.dumps({"maturity_state":maturity,"scores":{"overall":overall}}))
    open(os.path.join(sd,"drift_forecast.json"),"w").write(json.dumps({"probability_enter_broken":p_broken,"probability_enter_fragile":p_broken+0.1}))
    open(os.path.join(sd,"evidence_freshness.json"),"w").write(json.dumps({"review_priority":["selectors"] if stale else []}))
    open(os.path.join(sd,"automation_decision_report.json"),"w").write(json.dumps({"queued":{"manual_review":review,"capture_requests":captures,"profile_approvals":approvals},"selector_decisions":[{"selector":"a","action":"trust"}]}))
    open(os.path.join(sd,"drift_history.json"),"w").write(json.dumps([{"drift_flags":["selector_drift"]},{"drift_flags":["selector_drift"]}]))
    open(os.path.join(sd,"verdict_change_queue.json"),"w").write(json.dumps({"to_review":[{"field":"selector_policy:a"}]}))
site("bros","Stable",0.85,0.1,False,1,0,1)
site("fragilesite","Fragile",0.4,0.7,True,4,2,0)
site("neglected","Fragile",0.3,0.8,False,0,0,0)  # high risk, ZERO effort -> under-invested

# framework-level artifacts
scorecard=w("scorecard.json",{"maturity":"Mature","overall":0.78})
risk=w("risk.json",{"risk_register":[{"risk":"sites_trending_broken","severity":"High","detail":"1 site"},{"risk":"x","severity":"Low"}]})
audit=w("audit.json",{"distribution":{"Fully Defensible":29,"Partially Defensible":3,"Weakly Supported":2,"Unsupported":1}})

# PHASE 25 cockpit
o25=os.path.join(d,"ck"); subprocess.run([py,T,"cockpit","--portfolio-root",root,"--framework-scorecard",scorecard,"--risk-register",risk,"--audit-gaps",audit,"--out-dir",o25],check=True)
ck=json.load(open(os.path.join(o25,"operator_cockpit.json")))
assert ck["framework_maturity"]=="Mature"
assert "fragilesite" in ck["fragile_sites"], ck["fragile_sites"]
assert "sites_trending_broken" in ck["active_high_risks"]
assert ck["audit_unsupported"]==1
print(f"TEST P25 PASS: cockpit surfaces maturity, fragile sites {ck['fragile_sites']}, high risks, debt")

# PHASE 26 portfolio
o26=os.path.join(d,"pf"); subprocess.run([py,T,"portfolio","--portfolio-root",root,"--out-dir",o26],check=True)
pf=json.load(open(os.path.join(o26,"portfolio_rankings.json")))
# fragilesite (high risk+burden) should rank above bros
assert pf["rankings"][0]["site"]=="fragilesite", [r["site"] for r in pf["rankings"]]
print(f"TEST P26 PASS: portfolio ranks fragilesite first ({pf['rankings'][0]['priority']})")

# PHASE 27 capacity
o27=os.path.join(d,"cap"); subprocess.run([py,T,"capacity","--portfolio-root",root,"--minutes-per-review","10","--weekly-capacity-minutes","40","--out-dir",o27],check=True)
cap=json.load(open(os.path.join(o27,"review_forecast.json")))
# 6 review+approval items * 10 = 60 min > 40 capacity -> doesn't scale
assert cap["scales"]==False, cap
print(f"TEST P27 PASS: capacity forecast scales={cap['scales']} ({cap['pending_review_effort_minutes']}min vs 40)")

# PHASE 28 governance — scan the artifacts we've produced (all clean)
o28=os.path.join(d,"gov"); subprocess.run([py,T,"governance","--artifacts-root",o25,"--out-dir",o28],check=True)
gov=json.load(open(os.path.join(o28,"governance_findings.json")))
assert gov["compliant"]==True and gov["invariants"]["recognition_only_no_signing_values"]
print(f"TEST P28 PASS: governance scanned {gov['artifacts_scanned']} artifacts, compliant={gov['compliant']}")
# and governance CATCHES a planted violation
bad=os.path.join(d,"bad"); os.makedirs(bad); open(os.path.join(bad,"x.md"),"w").write("token=SECRET123 and page.goto('x')")
o28b=os.path.join(d,"govbad"); subprocess.run([py,T,"governance","--artifacts-root",bad,"--out-dir",o28b],check=True)
govbad=json.load(open(os.path.join(o28b,"governance_findings.json")))
assert govbad["compliant"]==False and len(govbad["findings"])>=2
print(f"TEST P28b PASS: governance CATCHES planted signing+replay violations ({len(govbad['findings'])} findings)")

# PHASE 29 memory
o29=os.path.join(d,"mem"); subprocess.run([py,T,"memory","--portfolio-root",root,"--out-dir",o29],check=True)
mem=json.load(open(os.path.join(o29,"institutional_memory.json")))
assert "selector_drift" in mem["recurring_drift_patterns"], mem["recurring_drift_patterns"]
print(f"TEST P29 PASS: institutional memory captures recurring drift {mem['recurring_drift_patterns']}")

# PHASE 30 exec
o30=os.path.join(d,"ex"); subprocess.run([py,T,"exec","--view","weekly","--operator-cockpit",os.path.join(o25,"operator_cockpit.json"),"--portfolio-rankings",os.path.join(o26,"portfolio_rankings.json"),"--risk-register",risk,"--framework-scorecard",scorecard,"--audit-gaps",audit,"--out-dir",o30],check=True)
ex=json.load(open(os.path.join(o30,"executive_dashboard.json")))
assert ex["view"]=="weekly" and ex["least_defensible"]==1
assert "fragilesite" in ex["what_should_happen_next"]
print(f"TEST P30 PASS: exec summary (weekly) — next: {ex['what_should_happen_next']}")

# CROSS-CUTS
o_r=os.path.join(d,"res"); subprocess.run([py,T,"resources","--portfolio-root",root,"--out-dir",o_r],check=True)
res=open(os.path.join(o_r,"resource_allocation_report.md")).read()
resj=None
assert "under-invested" in res, "neglected high-risk/zero-effort site should be under-invested"
print("TEST CROSS-1 PASS: resource allocation flags neglected high-risk site as under-invested")
o_roi=os.path.join(d,"roi"); subprocess.run([py,T,"reviewroi","--portfolio-root",root,"--out-dir",o_roi],check=True)
print("TEST CROSS-2 PASS: review ROI computed")
o_b=os.path.join(d,"bn"); subprocess.run([py,T,"bottlenecks","--portfolio-root",root,"--out-dir",o_b],check=True)
bn=open(os.path.join(o_b,"operational_bottlenecks_report.md")).read()
assert "scaling" in bn.lower()
print("TEST CROSS-3 PASS: bottleneck analysis (incl scaling constraint)")

# GLOBAL POSTURE
alltext=""
for od in (o25,o26,o27,o28,o29,o30,o_r,o_roi,o_b):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
# note: governance test on 'bad' dir is separate; our own outputs must be clean
assert "token=SECRET" not in alltext, "SIGNING LEAK in our outputs"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY"
print("TEST GLOBAL PASS: no signing values, no replay content across P25-30 outputs")
print("\nALL PHASE 25-30 TESTS PASS")


# v3.66.95: cockpit honest-degradation — no bare "None" when scorecard is absent
def _test_cockpit_honest_degradation():
    import json, tempfile, os, subprocess, sys
    d = tempfile.mkdtemp()
    root = os.path.join(d, "portfolio"); os.makedirs(os.path.join(root, "s1"))
    # per-site health present, but NO framework scorecard passed
    open(os.path.join(root, "s1", "site_health_score.json"), "w").write(
        json.dumps({"maturity_state": "Stable", "scores": {"overall": 0.82}}))
    open(os.path.join(root, "s1", "automation_decision_report.json"), "w").write(
        json.dumps({"queued": {"manual_review": 0}}))
    out = os.path.join(d, "ck")
    subprocess.run([sys.executable, "tools/operator_layer.py", "cockpit",
                    "--portfolio-root", root, "--out-dir", out],
                   cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   check=True)
    ck = json.load(open(os.path.join(out, "operator_cockpit.json")))
    m = ck["framework_maturity"]
    # must NOT be a bare None; must explain itself
    assert m is not None, "maturity should never be bare None"
    assert "interim" in m or "not computed" in m, f"maturity not honestly labelled: {m}"
    print("  honest-degradation: maturity =", m)

_test_cockpit_honest_degradation()
print("PHASE 25-30 honest-degradation test PASS")
