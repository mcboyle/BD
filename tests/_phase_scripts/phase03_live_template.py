import json, tempfile, os, subprocess, sys, re

d=tempfile.mkdtemp()
# Phase-1 profile + Phase-2 confidence as inputs
prof={"site":"bros","known_identity_descriptors":["alice123"],
      "known_rendition_descriptors":["720p","1080p"],
      "known_signing_markers":["token","expires"],
      "known_goal_url_shapes":["https://cdn.bros.com/v/alice123"]}
conf=[{"selector":"a[download]","kind":"attribute","roles":["download"],"confidence":1.0,"stability_across_captures":1.0},
      {"selector":"a.signin","kind":"class","roles":["login"],"confidence":0.8,"stability_across_captures":1.0},
      {"selector":".btn.download-btn","kind":"class","roles":["download"],"confidence":0.7,"stability_across_captures":1.0}]
pf=os.path.join(d,"site_profile.json"); open(pf,"w").write(json.dumps(prof))
cf=os.path.join(d,"selector_confidence.json"); open(cf,"w").write(json.dumps(conf))

# a LIVE observation the existing workflow would produce (matched case)
live_match={"identity":"alice123","renditions":["720p","1080p","480p"],
            "signing_markers":["token","expires"],
            "goal_url_shape":"https://cdn.bros.com/v/alice123",
            "selector_hits":{"a[download]":True,"a.signin":True},
            "via_learned":True,"structural_ok":True}
lm=os.path.join(d,"live_match.json"); open(lm,"w").write(json.dumps(live_match))

out=os.path.join(d,"out")
r=subprocess.run([sys.executable,"tools/live_template_integration.py","--site","bros",
                  "--site-profile",pf,"--selector-confidence",cf,"--live-observation",lm,
                  "--out-dir",out,"--emit-learned"],capture_output=True,text=True)
assert r.returncode==0, r.stderr
arts=os.listdir(out); print("artifacts:",sorted(arts))
alltext="".join(open(os.path.join(out,f)).read() for f in arts)

# TEST 1: no executable/replay content
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)|await ",alltext), "REPLAY CONTENT"
print("TEST 1 PASS: no executable/replay content in any artifact")

# TEST 2: guidance is confidence-ORDERED row_selectors (highest first), falls through (not exclusive)
g=json.load(open(os.path.join(out,"guidance_learned.json")))
assert g["row_selectors"][0]=="a[download]", "row_selectors not confidence-ordered"
assert "a.signin" in g["trigger_selectors"], "login selector should be a trigger"
print("TEST 2 PASS: selectors confidence-ordered; download→row, login→trigger")

# TEST 3: highest available LIVE resolution selected (from live options, not template)
dec=json.load(open(os.path.join(out,"download_decision_report.json")))
assert dec["selected"]=="1080p", f"expected 1080p, got {dec['selected']}"
assert "480p" in dec["available_renditions_live"], "must score from LIVE options incl ones not in template"
print(f"TEST 3 PASS: selected highest LIVE rendition {dec['selected']} from {dec['available_renditions_live']}")

# TEST 4: matched template -> template_matched, no drift
tm=json.load(open(os.path.join(out,"template_match_report.json")))
assert tm["verdict"]=="template_matched" and not tm["drift_flags"], tm
print("TEST 4 PASS: clean match -> template_matched, no drift")

# TEST 5: DRIFT case detected
live_drift=dict(live_match); live_drift["identity"]="bob999"; live_drift["renditions"]=["240p"]
live_drift["selector_hits"]={"a[download]":False,"a.signin":False}
ld=os.path.join(d,"live_drift.json"); open(ld,"w").write(json.dumps(live_drift))
out2=os.path.join(d,"out2")
subprocess.run([sys.executable,"tools/live_template_integration.py","--site","bros",
                "--site-profile",pf,"--selector-confidence",cf,"--live-observation",ld,"--out-dir",out2],check=True)
tm2=json.load(open(os.path.join(out2,"template_match_report.json")))
assert "identity_drift" in tm2["drift_flags"] and "selector_drift" in tm2["drift_flags"], tm2
print(f"TEST 5 PASS: drift detected -> {tm2['drift_flags']}")

# TEST 6: all updates SUGGESTED only
upd=json.load(open(os.path.join(out,"suggested_profile_update.json")))
assert "SUGGESTED" in upd["_status"] and "does not auto" in upd["_status"].lower()
print("TEST 6 PASS: profile update suggested-only, no auto-apply")

# TEST 7: signing markers are NAMES only, no values anywhere
assert "token=" not in alltext and "expires=" not in alltext
print("TEST 7 PASS: signing markers by name only; no signing values")

# TEST 8: pre-flight only (no live obs) still works, produces guidance not reports
out3=os.path.join(d,"out3")
r3=subprocess.run([sys.executable,"tools/live_template_integration.py","--site","bros",
                   "--site-profile",pf,"--selector-confidence",cf,"--out-dir",out3,"--emit-learned"],capture_output=True,text=True)
assert r3.returncode==0 and os.path.exists(os.path.join(out3,"guidance_learned.json"))
assert not os.path.exists(os.path.join(out3,"template_match_report.json")), "no reports without live obs"
print("TEST 8 PASS: pre-flight guidance works without live observation")

print("\nALL PHASE 3 TESTS PASS")
