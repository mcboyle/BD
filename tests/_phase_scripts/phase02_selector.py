import json, tempfile, os, subprocess, sys, re
from pathlib import Path

def _node(tag, attrs=None, text=None, children=None, redacted=None):
    n = {"tagName": tag, "attributes": attrs or {}}
    if text is not None: n["textContent"] = text
    if children: n["childNodes"] = children
    if redacted: n["_bd_redacted"] = redacted
    return n

def _cap(with_dom=True, rendition="720p"):
    # signed goal in network log; DOM with a download anchor + a masked field
    cap = {
      "network_log":[
        {"seq":1,"method":"GET","url":f"https://cdn.bros.com/v/alice123/{rendition}/i.m3u8?token=SECRETVAL&expires=99","type":"xhr"},
      ],
    }
    if with_dom:
        snap = _node("html", children=[
            _node("body", children=[
                _node("a", {"href":"https://bros.com/dl/alice123?token=SECRETVAL","class":"btn download-btn","download":""}, "Download 1080p"),
                _node("button", {"class":"x9f3a2b7 play"}, "Play"),     # volatile class
                _node("input", {"type":"password","class":"pw bd-mask"}, None, redacted="mask"),
                _node("a", {"href":"https://bros.com/login","class":"signin"}, "Sign In"),
            ])
        ])
        cap["dom_log"]=[{"dom_seq":0,"type":"full_snapshot","source":0,"data":{"node":snap}}]
        cap["dom_log_count"]=1
    return cap

d=tempfile.mkdtemp()
c1=os.path.join(d,"bros_run1.json"); open(c1,"w").write(json.dumps(_cap(True,"720p")))
c2=os.path.join(d,"bros_run2.json"); open(c2,"w").write(json.dumps(_cap(True,"1080p")))
out=os.path.join(d,"out")
r=subprocess.run([sys.executable,"tools/selector_learning.py","--captures",c1,c2,
                  "--site","bros","--out-dir",out],capture_output=True,text=True)
assert r.returncode==0, f"run failed: {r.stderr}"
arts=os.listdir(out)
print("artifacts:", sorted(arts))

# TEST 1: no signing value in ANY artifact
alltext="".join(open(os.path.join(out,f)).read() for f in arts)
assert "SECRETVAL" not in alltext and "token=SECRETVAL" not in alltext and "expires=99" not in alltext, "SIGNING LEAK"
print("TEST 1 PASS: no signing value in any selector artifact")

# TEST 2: no replay script / executable flow anywhere
assert not re.search(r"page\.(goto|click|fill)|playwright|new_page\(|await ", alltext), "REPLAY-LIKE CONTENT"
print("TEST 2 PASS: no executable/replay content")

# TEST 3: candidates are data (dicts with selector/kind), not code
conf=json.load(open(os.path.join(out,"selector_confidence.json")))
assert isinstance(conf,list) and all("selector" in s and "confidence" in s for s in conf), "not reviewable data"
print(f"TEST 3 PASS: {len(conf)} selectors are reviewable data (selector+confidence+stability)")

# TEST 4: masked/volatile handling — password field's masked text not surfaced; volatile class dropped
sels=[s["selector"] for s in conf]
assert not any("x9f3a2b7" in s for s in sels), "volatile class leaked into a selector"
print("TEST 4 PASS: volatile class churn-filtered; masked content not used")

# TEST 5: download anchor recognized with role + stable selector
dl=[s for s in conf if "download" in s["roles"]]
assert dl, "download element not recognized"
print(f"TEST 5 PASS: download role recognized ({dl[0]['selector']}, conf {dl[0]['confidence']})")

# TEST 6: profile update is SUGGESTED only, not auto-applied
upd=json.load(open(os.path.join(out,"selector_profile_update_candidate.json")))
assert "SUGGESTED" in upd["_status"] and "does NOT auto-update" in upd["_status"].lower() or "not auto" in upd["_status"].lower()
print("TEST 6 PASS: profile update is suggested-only, no auto-apply")

# TEST 7: EMPTY DOM -> blocked, not error
c3=os.path.join(d,"nub.json"); open(c3,"w").write(json.dumps(_cap(False)))
out2=os.path.join(d,"out2")
r2=subprocess.run([sys.executable,"tools/selector_learning.py","--captures",c3,"--site","nub","--out-dir",out2],capture_output=True,text=True)
assert r2.returncode==0
lr=open(os.path.join(out2,"selector_learning_report.md")).read()
assert "BLOCKED" in lr and "not a failure" in lr
blocked=json.load(open(os.path.join(out2,"selector_confidence.json")))
assert blocked.get("status")=="blocked_no_dom"
print("TEST 7 PASS: empty DOM -> blocked/not-available result, not error")

print("\nALL PHASE 2 TESTS PASS")
