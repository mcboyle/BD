#!/usr/bin/env python3
"""runner_seams -- generate the two "seam" KB docs for the runner decomposition:
  RUNNER_IMPORT_MAP.md     -- per target-module: which module-level names it must import
                              (util-funcs / classes / consts / 3p), so each cut's import
                              block is mechanical and the mixin->runner import CYCLE is
                              avoided (anything a mixin needs must come from runner_util,
                              not runner.py).
  RUNNER_STATE_CONTRACT.md -- the instance-attribute "state bus" shared across mixins:
                              cross-cutting attrs (>=4 units), __init__-set vs lazy,
                              and the methods __init__ calls at construction.
AST-only, stdlib-only, path-relative. EDIT the UNITS sets if the inventory changes.
"""
import ast, builtins, collections, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "bulk_downloader", "runner.py")
BUILTINS = set(dir(builtins))

# --- curated grouping DERIVED from runner_struct.py (the source of truth, whose
# comment reads "EDIT if the method inventory changes"). v3.66.766: imported, not
# hand-copied, so the two advisory nav-aid tools cannot drift. runner_struct's
# analysis is guarded under `if __name__ == "__main__"`, so importing it for UNITS
# has no side effects.
import importlib.util as _ilu
_STRUCT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner_struct.py")
_spec = _ilu.spec_from_file_location("_runner_struct_units", _STRUCT_PATH)
_rs = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rs)
UNITS = _rs.UNITS
def unit_of(m):
    for u,ms in UNITS.items():
        if m in ms: return u
    return "core"

# names that MUST be sourced from runner_util (the kernel) to avoid mixin->runner cycle
KERNEL = {"_ts","_resolve_safe","_check_video_magic_bytes","resolve_url_attribute",
          "gate_candidate_url","_bump_learned_stat","_bump_per_selector",
          "_maybe_demote_selectors","record_bandwidth","get_bandwidth_history",
          "_bw_history","_GATE_URL_ATTRS","_RUNTIME_NAV_REJECTIONS",
          "DEFAULT_MIN_RESOLUTION","_BD_TO_APPRISE_EVENT"}
STAYS_CORE = {"_global_sem","_global_sem_size","_global_sem_lock",
              "set_global_concurrent_cap","get_global_concurrent_cap","DEFAULT_MAX_CONCURRENT"}

def main():
    tree = ast.parse(open(RUNNER, encoding="utf-8").read())
    mod_funcs=set(); mod_classes=set(); mod_imported=set(); mod_assigned=set()
    init_node=None; sr_nodes={}
    for n in tree.body:
        if isinstance(n,ast.FunctionDef): mod_funcs.add(n.name)
        elif isinstance(n,ast.ClassDef):
            mod_classes.add(n.name)
            if n.name=="SiteRunner":
                for b in n.body:
                    if isinstance(b,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        sr_nodes[b.name]=b
                        if b.name=="__init__": init_node=b
        elif isinstance(n,ast.ImportFrom):
            for a in n.names: mod_imported.add(a.asname or a.name)
        elif isinstance(n,ast.Import):
            for a in n.names: mod_imported.add((a.asname or a.name).split('.')[0])
        elif isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name): mod_assigned.add(t.id)
        elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):
            mod_assigned.add(n.target.id)
    MOD_NS=mod_funcs|mod_classes|mod_imported|mod_assigned
    names=set(sr_nodes); assign={m:unit_of(m) for m in names}

    def locals_of(fn):
        loc=set(a.arg for a in list(fn.args.posonlyargs)+list(fn.args.args)+list(fn.args.kwonlyargs))
        if fn.args.vararg: loc.add(fn.args.vararg.arg)
        if fn.args.kwarg: loc.add(fn.args.kwarg.arg)
        for x in ast.walk(fn):
            if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Store): loc.add(x.id)
            if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x is not fn: loc.add(x.name)
            if isinstance(x,(ast.Import,ast.ImportFrom)):
                for a in x.names: loc.add(a.asname or a.name.split('.')[0])
        return loc
    unit_needs=collections.defaultdict(set)
    for m,fn in sr_nodes.items():
        loc=locals_of(fn)
        for x in ast.walk(fn):
            if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Load):
                if x.id in loc or x.id in BUILTINS or x.id=="self": continue
                if x.id in MOD_NS: unit_needs[assign[m]].add(x.id)

    # ---- RUNNER_IMPORT_MAP.md ----
    out=["# RUNNER_IMPORT_MAP.md\n",
         "Per target-module: the module-level names its methods reference, so each cut's",
         "import block is mechanical. **Cycle rule:** a mixin must NOT `from .runner import X`",
         "(runner imports the mixin -> cycle). Anything a mixin needs that lives in runner.py",
         "today must move to `runner_util.py` (the kernel: imported by all, imports nothing back).",
         "Names tagged [KERNEL] go to runner_util; [CORE] stay in runner.py (worker-coupled).\n",
         "via tools/runner_seams.py.\n"]
    for u in list(UNITS)+["core"]:
        nd=unit_needs.get(u,set())
        if not nd: continue
        ufn=sorted(nd & mod_funcs); kls=sorted(nd & mod_classes)
        cst=sorted(nd & mod_assigned); imp=sorted(nd & mod_imported)
        out.append(f"\n## {u}")
        if ufn: out.append(f"- from `runner_util`: {', '.join('`%s`'%x for x in ufn)}")
        if kls: out.append(f"- classes: {', '.join('`%s`'%x for x in kls)}"
                           + ("  **(rewrite `SiteRunner._foo`->`self._foo` to avoid importing SiteRunner)**" if 'SiteRunner' in kls else ""))
        if cst:
            tagged=[f"`{x}`"+(" [KERNEL]" if x in KERNEL else " [CORE]" if x in STAYS_CORE else "") for x in cst]
            out.append(f"- module consts: {', '.join(tagged)}")
        if imp: out.append(f"- 3p/stdlib (import from original source): {', '.join('`%s`'%x for x in imp)}")
    open(os.path.join(ROOT,"RUNNER_IMPORT_MAP.md"),"w").write("\n".join(out))

    # ---- RUNNER_STATE_CONTRACT.md ----
    attr_rw=collections.defaultdict(lambda:[set(),set()])
    for m,fn in sr_nodes.items():
        u=assign[m]
        for x in ast.walk(fn):
            if isinstance(x,ast.Attribute) and isinstance(x.value,ast.Name) and x.value.id=="self":
                if isinstance(x.ctx,ast.Store): attr_rw[x.attr][1].add(u)
                elif isinstance(x.ctx,ast.Load): attr_rw[x.attr][0].add(u)
    # separate data-attrs from method-name reads
    method_names=set(names)
    data_attrs={a:(r,w) for a,(r,w) in attr_rw.items() if a not in method_names}
    init_attrs=set(); init_calls=set()
    for x in ast.walk(init_node):
        if isinstance(x,ast.Attribute) and isinstance(x.value,ast.Name) and x.value.id=="self" and isinstance(x.ctx,ast.Store):
            init_attrs.add(x.attr)
        if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and isinstance(x.func.value,ast.Name) and x.func.value.id=="self":
            init_calls.add(x.func.attr)
    shared=sorted([(a,sorted(r),sorted(w)) for a,(r,w) in data_attrs.items() if len(set(r)|set(w))>=4],
                  key=lambda t:-len(set(t[1])|set(t[2])))
    sc=["# RUNNER_STATE_CONTRACT.md\n",
        "The instance-attribute **state bus** that mixins share through `self`. Mixins hold no",
        "`__init__`; all state is created by the single `SiteRunner.__init__` (+ lazy first-touch).",
        "Correctness is preserved on extraction regardless of which mixin reads/writes an attr,",
        "but THESE are the cross-cutting attrs to treat as the inter-mixin API.\n",
        "via tools/runner_seams.py.\n",
        f"## Cross-cutting attrs (touched by >=4 units) -- {len(shared)}\n",
        "| attr | written by | read by |","|---|---|---|"]
    for a,r,w in shared:
        sc.append(f"| `self.{a}` | {', '.join(w) or '(lazy/none)'} | {', '.join(r) or '-'} |")
    sc.append(f"\n## Construction\n- `__init__` sets **{len(init_attrs)}** attrs directly; "
              f"**{len(data_attrs)-len([a for a in data_attrs if a in init_attrs])}** data attrs are LAZY (first-set outside __init__).")
    sc.append(f"- `__init__` CALLS these methods during construction (their mixins must be bases when their cut lands): "
              + ", ".join('`%s`'%m for m in sorted(init_calls & names)) + ".")
    sc.append(f"- total distinct `self` data-attrs: {len(data_attrs)} (excludes method-name refs).")
    sc.append("\n## On-disk persistence surface (restart format-contracts; pure motion preserves them)\n"
              "- `rl_{site_id}.json` (BD_HOME) -- rate-limit state -> **scheduler** (`_load_rl/_save_rl/_clear_rl`)\n"
              "- account_state / pool_state -> **accounts** (`_persist_account_state/_persist_pool_state`)\n"
              "- `{DRAFTS_DIR}/...` learned-selector drafts -> **teach** (`_persist_learned_to_draft`)\n"
              "- `{SCREENSHOTS_DIR}/...` failure screenshots -> **telemetry** (`_screenshot`)\n"
              "- `{site_id}.json` JD cookies -> **integrations** (`_read_cookies_for_jd`)\n"
              "- download dir (`config['download_dir']`) -> **transport/core**")
    sc.append("\n## Concurrency surface\n"
              "- `self._lock` (main) guards the shared mutable state (`self.jobs`, `self._url_queue`); "
              "acquired by methods across many units -- a cut must not change locking order.\n"
              "- `self._worker_heartbeats_lock`, plus `threading.Event`s "
              "(`_ready/_closed/_session_ok/_manual_snapshot_stop`). 12 `threading.Thread` spawns "
              "(13 thread-target methods incl. `_worker_loop/_watchdog_loop/_sched_loop`).\n"
              "- module-level: `_bw_lock` (bandwidth, -> runner_util), `_global_sem_lock` (cap, STAYS core).")
    sc.append("\n## Cross-unit exception contract\n"
              "- `_HTTPDownloadFailed` -- primary download-failure exception, raised ~15x "
              "(extractors+transport), caught ~5x (transport/core). Both sides import it from its "
              "source module (not runner), so motion is safe; it is a control-flow contract spanning units.\n"
              "- `_DownloadTruncated` (raised/caught within transport). `VPNRequiredError` (caught 1x, "
              "fail-closed Track-K; raised in egress modules).")
    sc.append("\n## Config input\n"
              "- `self.config` is the central shared input: **145** distinct keys read across all units "
              "(top: name, download_dir, learned, user_agent, cookie_file, accounts, ...). Tracked separately "
              "by `tools/config_surface_inventory.py` -- the site-template schema.")
    sc.append("\n## Job-record schema (the per-URL state dict in `self.jobs`, mutated by every unit)\n"
              "Central mutator: `_update_job` (core, ~432 lines) -- also externally called. Common fields:\n"
              "`status` (most-touched), `message`, `priority`, `retries`/`auto_retry_count`/`corruption_retries`,\n"
              "`retry_after`/`next_auto_retry_at`/`last_progress_at`/`ts`, `auto_teach_seen`, `force_download`,\n"
              "`custom_headers`, `thumbnail`, `_run_id`. A cut must not change these keys (frontend + persistence read them).")
    open(os.path.join(ROOT,"RUNNER_STATE_CONTRACT.md"),"w").write("\n".join(sc))
    print("wrote RUNNER_IMPORT_MAP.md + RUNNER_STATE_CONTRACT.md")
    print(f"  cross-cutting attrs(>=4 units): {len(shared)} | __init__ sets {len(init_attrs)} | __init__ calls {sorted(init_calls & names)}")

if __name__=="__main__":
    main()
