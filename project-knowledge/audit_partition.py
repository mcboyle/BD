#!/usr/bin/env python3
"""Subsystem-coherent, SLOC-balanced partition of the production tree for the
100% line-by-line audit. Run from work root: python3 tools/audit_partition.py [TARGET_LOC]
Emits audit_manifests/<SUBSYS>-NN.txt (every production file exactly once),
asserts 100% coverage, prints the subsystem x batch summary.
Within a batch, files are listed SLOC-desc (radon-rank proxy until the kit is installed)."""
import os, re, sys, json
ROOT=os.getcwd(); TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 5500
def sloc(p):
    try: return sum(1 for _ in open(p,encoding='utf-8',errors='ignore'))
    except: return 0
files=[]
for base,exts in (("bulk_downloader",(".py",)),("tools",(".py",)),("frontend/src",(".ts",".tsx"))):
    for dp,_,fns in os.walk(base):
        if "node_modules" in dp or "__pycache__" in dp: continue
        for fn in fns:
            if fn.endswith(exts):
                p=os.path.join(dp,fn); files.append((p,sloc(p)))
# subsystem precedence (first match wins), by path+name
SUBSYS=[
 ("FE",            r"^frontend/src/"),
 ("CAP",           r"bulk_downloader/(session_capture|dom_capture|dom_recorder|capture_bodies|capture_artifact_redact|wacz|provenance|sentinel|novnc|live_record)|tools/capture_session"),
 ("REC",           r"bulk_downloader/(extraction_core|provider_resolve|recogniz|distill|selector_drift|review|promote|teach|decision_confidence)|bulk_downloader/deep_detect|tools/build_template|tools/player_recognition|tools/.*recogniz"),
 ("RUN",           r"bulk_downloader/(runner|queue|transport|multi_conn|ytdlp|smart_wakeup|session_keeper|batch_ops|watch_folder|_job)"),
 ("AUTH",          r"bulk_downloader/(account|auth|cookie|secret|cloak|login|db|datastore)"),
 ("APP",           r"bulk_downloader/app(_|\.py)"),
 ("CORE_BD",       r"bulk_downloader/"),
 ("COCKPIT",       r"tools/cockpit"),
 ("TOOLS_BUILD",   r"tools/(build_|dependency_graph|import_graph|route_map|gui_parity|check_route|tasktracker|verify_release|decomp|audit_atomic|cross_monolith|pin_index|function_index)"),
 ("TOOLS_OTHER",   r"tools/"),
]
def subsys(p):
    for name,pat in SUBSYS:
        if re.search(pat,p): return name
    return "MISC"
buckets={}
for p,n in files: buckets.setdefault(subsys(p),[]).append((p,n))
os.makedirs("audit_manifests",exist_ok=True)
manifests=[]; total=0; allfiles=set()
order=[s[0] for s in SUBSYS]+["MISC"]
for sub in order:
    items=sorted(buckets.get(sub,[]),key=lambda x:-x[1])  # SLOC desc within subsystem
    if not items: continue
    # greedy bin-pack into <=TARGET; a single file > TARGET is its own bin
    bins=[]; cur=[]; curloc=0
    for p,n in items:
        if n>TARGET: bins.append([(p,n)]); continue
        if curloc+n>TARGET and cur: bins.append(cur); cur=[]; curloc=0
        cur.append((p,n)); curloc+=n
    if cur: bins.append(cur)
    for i,b in enumerate(bins,1):
        name=f"{sub}-{i:02d}"; loc=sum(x[1] for x in b)
        open(f"audit_manifests/{name}.txt","w").write("\n".join(p for p,_ in b)+"\n")
        manifests.append((name,len(b),loc)); total+=loc
        for p,_ in b: allfiles.add(p)
# proofs
assert len(allfiles)==len(files)==len({p for p,_ in files}), "coverage gap/overlap!"
print(f"production files: {len(files)} | SLOC: {total} | batches: {len(manifests)} | target/batch: {TARGET}")
# subsystem summary
from collections import OrderedDict
summ=OrderedDict()
for name,nf,loc in manifests:
    sub=name.rsplit("-",1)[0]; s=summ.setdefault(sub,[0,0,0]); s[0]+=1; s[1]+=nf; s[2]+=loc
for sub,(nb,nf,loc) in summ.items():
    print(f"  {sub:13s} batches={nb:2d}  files={nf:4d}  loc={loc:6d}")
print("manifests -> audit_manifests/  (every file once; coverage 100%)")
