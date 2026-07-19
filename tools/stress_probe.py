"""Stress probe — load, concurrency, soak, leak detection.

This is NOT a security scanner. It looks for non-functional cliffs:
throughput collapse, lock contention, memory creep, file-descriptor
leaks, and DB lock storms.

Sections:
  S.1   Sustained read throughput on /api/logs/tail
  S.2   Sustained write throughput on /api/sites (create + delete cycle)
  S.3   Concurrent reads — threaded test_client burst
  S.4   Concurrent writes — DB lock contention via parallel writers
  S.5   Memory snapshot under load (RSS via psutil if available, else
        gc.get_objects() delta as a proxy)
  S.6   File descriptor leak check — open FD count before/after
  S.7   Endpoint-by-endpoint p50/p95/p99 latency snapshot
  S.8   Soak test — run for --duration seconds (default 60) and check
        no degradation in latency or memory

Flags:
  --fast            — reduced iteration counts (CI gate)
  --duration N      — soak length in seconds (default 60)

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/stress_probe.py [--fast] [--duration 300]
"""
from __future__ import annotations
import gc
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

opts = L.parse_flags()
FAST = opts["fast"]
SOAK_DURATION = max(5, opts["duration"]) if not FAST else 5

rep = L.Report("STRESS")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_stress_")

# Optional psutil — best-effort RSS sampling
try:
    import psutil  # type: ignore
    _PROC = psutil.Process(os.getpid())
    def _rss_mb() -> float:
        return _PROC.memory_info().rss / 1024 / 1024
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    def _rss_mb() -> float:
        return 0.0


def _open_fd_count() -> int:
    """Best-effort: count open FDs on Linux/macOS, fall back to 0 on Windows."""
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        try:
            return _PROC.num_fds()  # type: ignore  # POSIX-only on psutil
        except Exception:
            return 0


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    k = (len(data) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(data) - 1)
    return data[f] + (data[c] - data[f]) * (k - f)


# Iteration counts: --fast cuts everything by ~10x
N_READS  = 200  if FAST else 2000
N_WRITES = 50   if FAST else 500
N_THREADS = 4   if FAST else 10
N_CONCURRENT_PER_THREAD = 25 if FAST else 100

# ─── S.1: Sustained read throughput ────────────────────────────────────
rep.section(f"S.1 — Sustained read throughput ({N_READS} GETs /api/logs/tail)")
# Seed a log file so tail has something to return
log_dir = os.path.join(BD_HOME, "logs")
os.makedirs(log_dir, exist_ok=True)
with open(os.path.join(log_dir, "bulk_downloader.log"), "w") as f:
    for i in range(1000):
        f.write(f"seed line {i}\n")

t0 = time.perf_counter()
status_codes: dict[int, int] = {}
for _ in range(N_READS):
    r = client.get("/api/logs/tail?lines=20")
    status_codes[r.status_code] = status_codes.get(r.status_code, 0) + 1
elapsed = time.perf_counter() - t0
rps = N_READS / elapsed if elapsed > 0 else 0
if status_codes.get(200, 0) == N_READS:
    rep.F("ok", f"{N_READS} reads in {elapsed:.2f}s ({rps:.0f} RPS)")
else:
    rep.F("warn", f"non-200 responses during read burst",
          f"counts={status_codes}")

# Throughput floor: synthetic test_client is generous; expect at least
# 500 RPS even on a slow CI runner. A regression below this floor likely
# means a new lock or N+1 query.
if rps < (50 if FAST else 200):
    rep.F("bug", f"throughput floor breached: {rps:.0f} RPS < expected")

# ─── S.2: Sustained write throughput ───────────────────────────────────
rep.section(f"S.2 — Sustained write throughput ({N_WRITES} create+delete)")
t0 = time.perf_counter()
create_codes: dict[int, int] = {}
delete_codes: dict[int, int] = {}
created_sids: list[str] = []

# Pre-fetch CSRF token (reused — same session)
csrf_r = client.get("/api/csrf")
tok = (csrf_r.get_json() or {}).get("csrf_token", "") if csrf_r.status_code == 200 else ""
hdrs = {"X-CSRF-Token": tok} if tok else {}

for i in range(N_WRITES):
    r = client.post("/api/sites",
                    json={"name": f"stress_w_{i}"},
                    headers=hdrs)
    create_codes[r.status_code] = create_codes.get(r.status_code, 0) + 1

# Find sids
for sid, s in list(bd_app.s_cfg.items()):
    nm = s.get("name") or ""
    if nm.startswith("stress_w_"):
        created_sids.append(sid)

# Delete them
for sid in created_sids:
    r = client.delete(f"/api/sites/{sid}", headers=hdrs)
    delete_codes[r.status_code] = delete_codes.get(r.status_code, 0) + 1

elapsed = time.perf_counter() - t0
rep.F("ok" if create_codes.get(200, 0) == N_WRITES else "warn",
      f"{N_WRITES} creates in {elapsed:.2f}s",
      f"create_codes={create_codes} delete_codes={delete_codes}")

# ─── S.3: Concurrent reads — threaded burst ────────────────────────────
rep.section(f"S.3 — Concurrent reads ({N_THREADS} threads × "
            f"{N_CONCURRENT_PER_THREAD} reads)")
errors: list[Exception] = []
read_latencies: list[float] = []
lock = threading.Lock()

def burst():
    local_lat = []
    try:
        for _ in range(N_CONCURRENT_PER_THREAD):
            t = time.perf_counter()
            client.get("/api/logs/tail?lines=10")
            local_lat.append(time.perf_counter() - t)
    except Exception as ex:
        errors.append(ex)
    with lock:
        read_latencies.extend(local_lat)

threads = [threading.Thread(target=burst, daemon=True) for _ in range(N_THREADS)]
t0 = time.perf_counter()
for t in threads: t.start()
for t in threads: t.join(timeout=60)
elapsed = time.perf_counter() - t0

still_alive = [t for t in threads if t.is_alive()]
if still_alive:
    rep.F("bug", f"{len(still_alive)} threads still alive after 60s")
if errors:
    rep.F("bug", f"{len(errors)} thread errors during burst",
          f"first={errors[0]!r}")
total = N_THREADS * N_CONCURRENT_PER_THREAD
if not errors and not still_alive:
    rep.F("ok", f"{total} concurrent reads in {elapsed:.2f}s "
          f"({total/elapsed:.0f} RPS)")

if read_latencies:
    p50 = _percentile(read_latencies, 50) * 1000
    p99 = _percentile(read_latencies, 99) * 1000
    rep.F("ok" if p99 < 500 else "warn",
          f"read latency p50={p50:.1f}ms p99={p99:.1f}ms")

# ─── S.4: Concurrent writes — DB lock contention ───────────────────────
rep.section(f"S.4 — Concurrent DB writes ({N_THREADS} writers)")
write_errors: list[Exception] = []
write_done = [0]
write_lock = threading.Lock()

def writer(idx: int):
    try:
        for i in range(20 if FAST else 50):
            with bd_db.db_conn() as cx:
                cx.execute(
                    "INSERT INTO history (url, site_id, site_name, "
                    "filename, status) VALUES (?, ?, ?, ?, ?)",
                    (f"https://example.com/t{idx}_{i}.mp4",
                     f"stress_sid_{idx}",
                     f"stress_writer_{idx}",
                     f"file_{idx}_{i}.mp4", "ok"))
                cx.commit()
        with write_lock:
            write_done[0] += 1
    except Exception as ex:
        write_errors.append(ex)

t0 = time.perf_counter()
threads = [threading.Thread(target=writer, args=(i,), daemon=True)
           for i in range(N_THREADS)]
for t in threads: t.start()
for t in threads: t.join(timeout=60)
elapsed = time.perf_counter() - t0

if write_errors:
    # SQLite raises OperationalError on locked DB — known issue under
    # extreme contention. Single-user app so this is "should never happen
    # in practice" but worth flagging.
    sql_locks = sum(1 for e in write_errors if "locked" in str(e).lower())
    if sql_locks:
        rep.F("warn", f"{sql_locks}/{len(write_errors)} writes hit 'database locked'")
    rep.F("bug", f"{len(write_errors)} write errors during contention",
          f"first={write_errors[0]!r}")
else:
    rep.F("ok", f"{write_done[0]}/{N_THREADS} writer threads finished cleanly "
          f"in {elapsed:.2f}s")

# ─── S.5: Memory snapshot ──────────────────────────────────────────────
rep.section("S.5 — Memory snapshot")
gc.collect()
rss_before = _rss_mb()
obj_before = len(gc.get_objects())

# Stress: 200 rounds of create + lookup + delete
csrf_r = client.get("/api/csrf")
tok = (csrf_r.get_json() or {}).get("csrf_token", "") if csrf_r.status_code == 200 else ""
hdrs = {"X-CSRF-Token": tok} if tok else {}
mem_iters = 100 if FAST else 200
for i in range(mem_iters):
    client.post("/api/sites", json={"name": f"mem_{i}"}, headers=hdrs)
    client.get("/api/sites_list")
    # find + delete to keep s_cfg from growing unboundedly
    for sid, s in list(bd_app.s_cfg.items()):
        if s.get("name") == f"mem_{i}":
            client.delete(f"/api/sites/{sid}", headers=hdrs)
            break

gc.collect()
rss_after = _rss_mb()
obj_after = len(gc.get_objects())

if HAS_PSUTIL:
    drift = rss_after - rss_before
    if drift > 50:
        rep.F("warn", f"RSS grew {drift:+.1f} MB across {mem_iters} cycles",
              "may indicate a leak — re-run with larger iteration count")
    else:
        rep.F("ok", f"RSS stable: {rss_before:.1f} → {rss_after:.1f} MB "
              f"({drift:+.1f})")
else:
    rep.F("info", "psutil not installed — RSS check skipped "
          "(pip install psutil)")

obj_drift = obj_after - obj_before
if obj_drift > 5000:
    rep.F("warn", f"GC objects grew by {obj_drift:+d} "
          f"({obj_before} → {obj_after})")
else:
    rep.F("ok", f"GC object count stable: {obj_drift:+d}")

# ─── S.6: File descriptor leak ─────────────────────────────────────────
rep.section("S.6 — File descriptor accounting")
fd_before = _open_fd_count()
fd_iters = 50 if FAST else 200
for _ in range(fd_iters):
    client.get("/api/logs/tail?lines=5")
    client.get("/api/csrf")
fd_after = _open_fd_count()
if fd_before == 0:
    rep.F("info", "FD count unavailable on this platform (Windows or sandboxed)")
elif fd_after - fd_before > 10:
    rep.F("warn", f"FD count grew {fd_before} → {fd_after} "
          f"(+{fd_after - fd_before}) — possible leak")
else:
    rep.F("ok", f"FD stable: {fd_before} → {fd_after} "
          f"({fd_after - fd_before:+d})")

# ─── S.7: Per-endpoint latency snapshot ────────────────────────────────
rep.section("S.7 — Per-endpoint latency p50/p95/p99")
ENDPOINTS = [
    ("GET",  "/api/csrf",            None),
    ("GET",  "/api/sites_list",      None),
    ("GET",  "/api/logs/tail?lines=10", None),
    ("GET",  "/api/global_config",   None),
    ("GET",  "/api/dev/enabled",     None),
    ("GET",  "/api/history?limit=20", None),
]
lat_iters = 20 if FAST else 100
for method, path, _ in ENDPOINTS:
    latencies = []
    for _ in range(lat_iters):
        t = time.perf_counter()
        client.open(path, method=method)
        latencies.append((time.perf_counter() - t) * 1000)
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    badge = "ok"
    if p99 > 200:
        badge = "warn"
    if p99 > 1000:
        badge = "bug"
    rep.F(badge,
          f"{method} {path:32s}  p50={p50:6.2f}  p95={p95:6.2f}  p99={p99:6.2f} ms")

# ─── S.8: Soak test ────────────────────────────────────────────────────
rep.section(f"S.8 — Soak test ({SOAK_DURATION}s)")
rss_at_start = _rss_mb()
soak_start = time.perf_counter()
soak_lats: list[float] = []
soak_errors = 0
while time.perf_counter() - soak_start < SOAK_DURATION:
    t = time.perf_counter()
    try:
        client.get("/api/csrf")
        client.get("/api/logs/tail?lines=5")
        client.get("/api/sites_list")
    except Exception:
        soak_errors += 1
    soak_lats.append((time.perf_counter() - t) * 1000)
soak_elapsed = time.perf_counter() - soak_start
gc.collect()
rss_at_end = _rss_mb()

if soak_errors:
    rep.F("bug", f"{soak_errors} errors during {SOAK_DURATION}s soak")
else:
    first_q = soak_lats[:len(soak_lats)//4] or [0.0]
    last_q  = soak_lats[-len(soak_lats)//4:] or [0.0]
    first_avg = statistics.mean(first_q)
    last_avg = statistics.mean(last_q)
    drift_pct = ((last_avg - first_avg) / max(first_avg, 0.001)) * 100
    rep.F("ok",
          f"{len(soak_lats)} iters in {soak_elapsed:.1f}s",
          f"first-quartile avg {first_avg:.2f}ms → last-quartile avg "
          f"{last_avg:.2f}ms ({drift_pct:+.0f}%)")
    if drift_pct > 50:
        rep.F("warn", f"latency drift during soak: +{drift_pct:.0f}%")

if HAS_PSUTIL:
    drift = rss_at_end - rss_at_start
    rep.F("ok" if drift < 30 else "warn",
          f"soak RSS drift: {rss_at_start:.1f} → {rss_at_end:.1f} MB "
          f"({drift:+.1f} MB)")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
