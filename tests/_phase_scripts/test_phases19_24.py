import json, tempfile, os, subprocess, sys, re
d=tempfile.mkdtemp(); py=sys.executable; T="tools/meta_intelligence.py"
def w(n,o): p=os.path.join(d,n); open(p,"w").write(json.dumps(o)); return p

# PHASE 19 assumptions — reads the REAL corpus (read-only)
o19=os.path.join(d,"a"); subprocess.run([py,T,"assumptions","--out-dir",o19],check=True)
ai=json.load(open(os.path.join(o19,"assumption_intelligence.json")))
g=json.load(open(os.path.join(o19,"assumption_dependency_graph.json")))
assert ai["n_assumptions"]>0, "should inventory corpus assumptions"
assert "edges" in g and isinstance(g["edges"],list), "dependency graph has edges"
# blast radius ranking present
assert "highest_blast_radius" in ai and "weakest" in ai and "least_tested" in ai
print(f"TEST P19 PASS: {ai['n_assumptions']} assumptions; {len(g['edges'])} dependency edges; blast-ranked")

# PHASE 20 explain — feed a decision trace (from Phase 8 shape)
trace={"traces":[{"decision":"rendition_selection","final_outcome":"1080p","evidence_used":[{"rendition":"1080p","score":1080},{"rendition":"720p","score":720}],"rule_path":["score live","pick max"],"confidence":"deterministic"}]}
tf=w("trace.json",trace)
o20=os.path.join(d,"e"); subprocess.run([py,T,"explain","--site","bros","--decision-trace",tf,"--out-dir",o20],check=True)
ex=json.load(open(os.path.join(o20,"decision_explanation.json")))
e0=ex["explanations"][0]
assert e0["evidence_ignored"]!="none recorded as ignored", "should surface ignored alternatives"
assert "alternative_outcomes" in e0
print(f"TEST P20 PASS: decision explained w/ evidence used AND ignored + alternatives")

# PHASE 21 audit — reads corpus defensibility
o21=os.path.join(d,"au"); subprocess.run([py,T,"audit","--decision-trace",tf,"--out-dir",o21],check=True)
ag=json.load(open(os.path.join(o21,"audit_gaps.json")))
dist=ag["distribution"]
assert sum(dist.values())>0, "should classify corpus findings"
assert set(dist.keys())=={"Fully Defensible","Partially Defensible","Weakly Supported","Unsupported"}
print(f"TEST P21 PASS: audit defensibility distribution {dist}")

# PHASE 22 risk — compose from assumption intel + forecasts + audit
fc=w("forecasts.json",[{"site":"bros","probability_enter_broken":0.7}])
fr=w("freshness.json",[{"site":"bros","review_priority":["selectors"]}])
o22=os.path.join(d,"r"); subprocess.run([py,T,"risk","--assumption-intelligence",os.path.join(o19,"assumption_intelligence.json"),"--drift-forecasts",fc,"--freshness-reports",fr,"--audit-gaps",os.path.join(o21,"audit_gaps.json"),"--out-dir",o22],check=True)
rr=json.load(open(os.path.join(o22,"risk_register.json")))
assert rr["n_risks"]>0, "should surface risks"
sevs={r["severity"] for r in rr["risk_register"]}
assert sevs <= {"Low","Medium","High","Critical"}
# sites trending broken should be a risk
assert any("broken" in r["risk"] for r in rr["risk_register"]), "broken-trend risk expected"
print(f"TEST P22 PASS: {rr['n_risks']} risks, severities {sevs}")

# PHASE 23 graph — corpus knowledge graph
o23=os.path.join(d,"g"); subprocess.run([py,T,"graph","--out-dir",o23],check=True)
kg=json.load(open(os.path.join(o23,"knowledge_graph.json")))
assert kg["node_count"]>0 and kg["edge_count"]>0
types={n["type"] for n in kg["nodes"]}
assert "finding" in types and "evidence" in types, types
rels={e["rel"] for e in kg["edges"]}
assert "supports" in rels and "resolves" in rels, rels
print(f"TEST P23 PASS: graph {kg['node_count']} nodes/{kg['edge_count']} edges; types {types}")

# PHASE 24 maturity — self scorecard
o24=os.path.join(d,"m"); subprocess.run([py,T,"maturity","--confidence-calibration",w("cal.json",{"calibration_error":0.1,"by_subsystem":{}}),"--audit-gaps",os.path.join(o21,"audit_gaps.json"),"--risk-register",os.path.join(o22,"risk_register.json"),"--freshness-reports",fr,"--out-dir",o24],check=True)
sc=json.load(open(os.path.join(o24,"framework_scorecard.json")))
assert set(sc["scorecard"].keys())=={"evidence_quality","confidence_quality","calibration_quality","auditability","explainability","freshness","governance","stability"}
assert sc["maturity"] in ("Experimental","Emerging","Operational","Mature","Highly Mature")
print(f"TEST P24 PASS: 8-dim scorecard → {sc['maturity']} (overall {sc['overall']})")

# CROSS-CUTS
o_b=os.path.join(d,"b"); subprocess.run([py,T,"blindspots","--confidence-calibration",w("cal2.json",{"by_subsystem":{"selector":{"reliability_gap":-0.3}}}),"--out-dir",o_b],check=True)
bs=open(os.path.join(o_b,"blind_spots_report.md")).read()
assert "poorly_calibrated" in bs or "What are we missing" in bs
print("TEST CROSS-1 PASS: blind spots (poorly-calibrated subsystem surfaced)")
o_c=os.path.join(d,"c"); subprocess.run([py,T,"concentration","--out-dir",o_c],check=True)
print("TEST CROSS-2 PASS: evidence concentration analyzed")
root=os.path.join(d,"sites"); s1=os.path.join(root,"bros"); os.makedirs(s1)
open(os.path.join(s1,"automation_decision_report.json"),"w").write(json.dumps({"queued":{"manual_review":3,"capture_requests":2}}))
o_s=os.path.join(d,"s"); subprocess.run([py,T,"sustainability","--sites-root",root,"--out-dir",o_s],check=True)
ss=open(os.path.join(o_s,"sustainability_report.md")).read()
assert "review burden" in ss.lower()
print("TEST CROSS-3 PASS: sustainability (burden assessed)")

# GLOBAL POSTURE
alltext=""
for od in (o19,o20,o21,o22,o23,o24,o_b,o_c,o_s):
    for f in os.listdir(od): alltext+=open(os.path.join(od,f)).read()
assert "token=" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",alltext), "REPLAY"
print("TEST GLOBAL PASS: no signing values, no replay content across P19-24 + cross-cuts")
print("\nALL PHASE 19-24 TESTS PASS")
