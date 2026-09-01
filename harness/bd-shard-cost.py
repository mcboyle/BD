#!/usr/bin/env python3
"""Measure the per-FILE cost of every CI gate shard, so a repack is derived.

Ruled 2026-08-31 (ruling 41): "durations come from the API; derive the packing,
do not guess it". The API gives per-JOB seconds, which is enough to see that the
slowest shard runs 273s against a 127s mean -- but not enough to repack, because
a job's time is fixed setup plus the sum of its files. This measures the file
half locally and models the fixed half from the API, then packs longest-first.

  usage: bd-shard-cost.py measure <worktree> <shards.json> <out.json>
         bd-shard-cost.py pack <out.json> <api-jobs.tsv> <shards.json>

MEASUREMENT IS NOT PACKING. `measure` writes raw per-file seconds and never
decides anything; `pack` reads them. Keeping them apart means a repack can be
re-derived from a stored measurement without paying for it twice.
"""
import json, pathlib, re, subprocess, sys, time


def measure(worktree, shards_path, out_path):
    shards = json.loads(pathlib.Path(shards_path).read_text())
    files = sorted({f for v in shards.values() for f in v})
    missing = [f for f in files if not (pathlib.Path(worktree) / f).is_file()]
    if missing:
        raise SystemExit(f"UNKNOWN: {len(missing)} shard file(s) absent from the tree: {missing[:5]}")
    print(f"measuring {len(files)} file(s) from {len(shards)} shard(s)", flush=True)
    cost = {}
    # One pytest per file: xdist's per-test durations under-report import and
    # module-scope fixture cost, which is most of what a gate file spends.
    for i, rel in enumerate(files, 1):
        t0 = time.time()
        cp = subprocess.run(
            ["env", "-u", "BD_INSTALL_DIR", "bash", "-c",
             f"BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest {rel} -q -p no:randomly "
             f"--timeout=600 -x --no-header"],
            cwd=worktree, capture_output=True, text=True)
        dt = time.time() - t0
        cost[rel] = {"seconds": round(dt, 2), "rc": cp.returncode}
        print(f"  [{i:3d}/{len(files)}] {dt:6.1f}s rc={cp.returncode} {rel}", flush=True)
        pathlib.Path(out_path).write_text(json.dumps(cost, indent=1))
    print(f"wrote {out_path}")


def pack(cost_path, api_tsv, shards_path):
    cost = json.loads(pathlib.Path(cost_path).read_text())
    shards = json.loads(pathlib.Path(shards_path).read_text())
    api = {}
    for line in pathlib.Path(api_tsv).read_text().splitlines():
        name, secs = line.split("\t")
        m = re.match(r"gate-suites \((.+)\)$", name)
        if m:
            api[m.group(1)] = int(secs)
    common = [s for s in shards if s in api]
    if not common:
        raise SystemExit("UNKNOWN: no shard name is present in both ci.yml and the API")
    # Fixed per-job overhead: the intercept of api_seconds against local content
    # cost, estimated from the cheapest shard, floored at zero.
    ratios = []
    for s in common:
        local = sum(cost[f]["seconds"] for f in shards[s] if f in cost)
        if local > 0:
            ratios.append((api[s], local, s))
    ratios.sort(key=lambda r: r[1])
    overhead = max(0.0, min(a - l for a, l, _ in ratios))
    scale = (sum(a for a, _, _ in ratios) - overhead * len(ratios)) / sum(l for _, l, _ in ratios)
    print(f"model: ci_seconds ~= {overhead:.0f}s fixed + {scale:.2f} x local_seconds")
    lanes = len(common)
    items = sorted(((cost[f]["seconds"] * scale, f) for f in cost), reverse=True)
    bins = [[0.0, []] for _ in range(lanes)]
    for w, f in items:                       # longest processing time first
        bins.sort(key=lambda b: b[0])
        bins[0][0] += w
        bins[0][1].append(f)
    before = max(api[s] for s in common)
    after = max(b[0] for b in bins) + overhead
    print(f"critical path: {before}s now -> {after:.0f}s packed ({lanes} lanes), "
          f"saving {before - after:.0f}s per CI run")
    return bins, overhead


if __name__ == "__main__":
    if sys.argv[1] == "measure":
        measure(*sys.argv[2:5])
    elif sys.argv[1] == "pack":
        bins, _ = pack(*sys.argv[2:5])
        json.dump([{"seconds": round(b[0], 1), "files": sorted(b[1])} for b in bins],
                  open("/tmp/claude-1000/-home-mboyle-BulkDownloader/0aa0f1fc-8c14-44f7-8f14-d9d5aed65afd/scratchpad/packed.json", "w"), indent=1)
        print("wrote packed.json")
    else:
        raise SystemExit(__doc__)
