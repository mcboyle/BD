#!/usr/bin/env python3
"""runner_struct -- regenerate RUNNER_CALLGRAPH.md + RUNNER_MODULE_MAP.md from
bulk_downloader/runner.py (AST-only, stdlib-only, path-relative).

  python3 tools/runner_struct.py            # writes both docs next to the repo root + kb/runner_struct.json

If the SiteRunner method inventory changes, EDIT the UNITS sets + UNIT_MODULE table
below (the curated grouping) and rerun. Outputs are advisory navigation aids; the
binding gate is tools/runner_api_snapshot.py.
"""
import ast, collections, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "bulk_downloader", "runner.py")
OUT_CALLGRAPH = os.path.join(ROOT, "RUNNER_CALLGRAPH.md")
OUT_MAP = os.path.join(ROOT, "RUNNER_MODULE_MAP.md")
OUT_JSON = os.path.join(ROOT, "kb", "runner_struct.json")

# ---- curated grouping (EDIT if the method inventory changes) -----------------
UNITS = collections.OrderedDict()
UNITS["transport"] = {"_download_proxy_url","_do_direct_http_download","_try_multi_conn_download","_http_download","_http_download_parallel","_probe_size","_do_probe_fetch","_do_download","_looks_like_media","_probe_outcome","_integrity_size_ok","_promote_or_abort","_recommended_chunk_bytes","_observe_throughput","_current_cap_mbps"}
UNITS["extractors"] = {"_try_jsonapi_extractor","_try_vixen_extractor","_try_dl8_extractor","_try_aylo_extractor","_try_library_extractor","_try_ytdlp_fallback","_try_deep_detect_fallback","_persist_deep_detect_selectors","_probe_for_higher_tier","_run_pre_scrape_action"}
UNITS["integrations"] = {"_get_stash_client","_stash_dedup_check","_stash_scrape_preview","_stash_enrich_after_scan","_get_plex_client","_plex_enrich_after_scan","_get_jellyfin_client","_jellyfin_enrich_after_scan","_get_qb_client","_record_qb_outcome","qb_health","_try_qb_download","_get_jd_client","_read_cookies_for_jd","_record_jd_outcome","jd_health","_try_jd_download"}
UNITS["manual"] = {"start_manual_download","finish_manual_download","cancel_manual_download","is_awaiting_manual_download"}
UNITS["auth"] = {"login_async","start_manual_login","_poll_manual_cookies","start_captcha_solve_session","end_captcha_solve_session","finish_manual_login","verify_login_after_wizard","get_last_verify_result","cancel_manual_login_pending","is_awaiting_manual_login","_handle_auth_required","maybe_preemptive_relogin","_check_cookies_or_relogin","_cookie_age_hours","_check_redirect"}
UNITS["teach"] = {"_teach_base_url","teach_verify","teach_test_download","teach_commit","teach_cancel","_draft_override_template","_override_suppresses_persist","_persist_learned_to_draft","_handle_auto_teach_check","_recover_selector"}
UNITS["scheduler"] = {"_start_auto_retry","_stop_auto_retry","_parse_retry_schedule","_auto_retry_loop","_scan_subscriptions","_auto_retry_scan","start_scheduler","stop_scheduler","_sched_loop","_next_sched_dt","sched_next_str","_load_rl","_save_rl","_clear_rl","_maybe_drift_recover"}
UNITS["queue"] = {"load_urls","reorder_urls","set_priority","bulk_priority","bulk_delete","bulk_approve","bulk_pause","bulk_resume","bulk_retry","bulk_reorder","bulk_url_transform","clear_completed","retry_failed","retry","clear","export_urls","_restore_queue","_drain_url_queue"}
UNITS["browser"] = {"_pw_save","_context_options","_launch_args","_manual_profile_dir","_profile_dir","_launch_browser","_install_stealth","_apply_stealth_library_to_page","_warm_session"}
UNITS["challenge"] = {"_has_captcha","_handle_captcha_check","_try_turnstile_solve","_try_captcha_solve","_try_turnstile_solve_LEGACY"}
UNITS["telemetry"] = {"log_event","get_events","_install_event_listeners","_flush_fingerprint_observation","_pick_fastest_mirror","_build_mirror_urls","_extract_host","_classify_error","_handle_failure","_screenshot","_parse_hm","_fmt_dur"}
UNITS["integrity"] = {"_dedup_hash_worker","_dedup_preflight","_verify_hash_or_quarantine","_verify_integrity_or_quarantine","_embed_metadata_if_mp4","_apply_quality_preference"}
UNITS["accounts"] = {"_get_active_account","_rotate_account_if_available","_persist_account_state","_persist_pool_state","is_rate_limited","rl_remaining","trigger_rate_limit","_wait_rl_autostart"}

UNIT_MODULE = {
 "util":("runner_util.py","-","pure helpers + bandwidth ledger + global cap + learned-stat bumpers (module funcs, re-exported)"),
 "manual":("runner_manual.py","ManualMixin","manual-download session lifecycle (+ _ManualDownloadSession class)"),
 "challenge":("runner_challenge.py","ChallengeMixin","captcha/turnstile detection + solve handoff"),
 "teach":("runner_teach.py","TeachMixin","teach/learn selector drafts + auto-teach"),
 "integrity":("runner_integrity.py","IntegrityMixin","dedup, hash/integrity verify, metadata embed, quality pref"),
 "extractors":("runner_extractors.py","ExtractorsMixin","per-site extractors + ytdlp/deep-detect fallback"),
 "browser":("runner_browser.py","BrowserMixin","Playwright context/launch/profile/stealth/warm"),
 "queue":("runner_queue.py","QueueMixin","URL queue: load/reorder/priority/bulk/clear/export"),
 "integrations":("runner_integrations.py","IntegrationsMixin","stash/plex/jellyfin/qbittorrent/jdownloader"),
 "auth":("runner_auth.py","AuthMixin","login (async+manual), cookie/relogin, auth-required, redirect"),
 "transport":("runner_transport.py","TransportMixin","HTTP download engine: proxy/direct/multi-conn/parallel/probe/promote (VPN/Track-K)"),
 "accounts":("runner_accounts.py","AccountsMixin","account/pool rotation + persist; rate-limit predicates"),
 "scheduler":("runner_scheduler.py","SchedulerMixin","auto-retry loops, subscriptions, scheduler, rl persistence"),
 "telemetry":("runner_telemetry.py","TelemetryMixin","events/log, mirror pick, error classify/handle/screenshot (owns _RETRY_DELAYS_BY_KIND)"),
 "core":("runner.py","SiteRunner (stays)","__init__ + lifecycle + worker loop + orchestration (irreducible)"),
}

def unit_of(m):
    for u, members in UNITS.items():
        if m in members:
            return u
    return "core"

def main():
    tree = ast.parse(open(RUNNER, encoding="utf-8").read())
    module_funcs = {n.name:(n.lineno,n.end_lineno) for n in tree.body if isinstance(n,ast.FunctionDef)}
    SR={}; sr_nodes={}; MS={}
    def _span(b):
        lo=b.lineno
        if b.decorator_list:
            lo=min(lo, min(d.lineno for d in b.decorator_list))  # include decorator lines
        return (lo, b.end_lineno)
    for node in tree.body:
        if isinstance(node,ast.ClassDef) and node.name=="SiteRunner":
            for b in node.body:
                if isinstance(b,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    SR[b.name]=_span(b); sr_nodes[b.name]=b
        elif isinstance(node,ast.ClassDef) and node.name=="_ManualDownloadSession":
            for b in node.body:
                if isinstance(b,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    MS[b.name]=_span(b)
    names=set(SR)
    def self_calls(n):
        return {x.func.attr for x in ast.walk(n) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and isinstance(x.func.value,ast.Name) and x.func.value.id=="self"} & names
    def mod_calls(n):
        return {x.func.id for x in ast.walk(n) if isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id in module_funcs}
    outbound={m:self_calls(n) for m,n in sr_nodes.items()}
    modcalls={m:mod_calls(n) for m,n in sr_nodes.items()}
    inbound=collections.defaultdict(set)
    for c,cs in outbound.items():
        for t in cs: inbound[t].add(c)
    assign={m:unit_of(m) for m in names}
    # checks
    declared=set(); dup=[]
    for u,ms in UNITS.items():
        for x in ms:
            if x in declared: dup.append(x)
            declared.add(x)
    assert not dup, f"overlapping assignment: {dup}"
    assert not (declared-names), f"declared-but-absent: {declared-names}"
    span=lambda m:SR[m][1]-SR[m][0]+1
    um=collections.defaultdict(list)
    for m in names: um[assign[m]].append(m)
    summary=[]
    for u in list(UNITS)+["core"]:
        ms=um.get(u,[])
        if not ms: continue
        lines=sum(span(m) for m in ms)
        oe=sum(len(outbound[m]) for m in ms); intra=sum(len(outbound[m]&set(ms)) for m in ms)
        otc=len({m for m in ms for t in outbound[m] if assign[t]=="core"})
        inc=len({c for m in ms for c in inbound[m] if assign.get(c)=="core"})
        summary.append([u,len(ms),lines,round(100*intra/oe) if oe else 0,inc,otc])
    body=sorted([r for r in summary if r[0]!="core"],key=lambda r:(r[4],r[5],r[2]))
    order=[r[0] for r in body]+["core"]

    # ---- callgraph md
    out=["# RUNNER_CALLGRAPH.md (generated)\n",
         f"runner.py SiteRunner={len(names)} methods, {len(module_funcs)} module funcs, _ManualDownloadSession={len(MS)} methods. via tools/runner_struct.py.\n",
         "method | lines | calls(self) [mod:..] | called-by\n"]
    for u in order:
        ms=sorted(um.get(u,[]),key=lambda m:SR[m][0])
        if not ms: continue
        out.append(f"\n## {u} ({len(ms)} methods, {sum(span(m) for m in ms)} lines)\n")
        out.append("| method | lines | calls (self) | called-by |"); out.append("|---|---|---|---|")
        for m in ms:
            lo,hi=SR[m]; cs=sorted(outbound[m]); mc=sorted(modcalls[m])
            cstr=", ".join(cs) if cs else "-"
            if mc: cstr+=f"  [mod: {', '.join(mc)}]"
            cb=", ".join(sorted(inbound.get(m,[]))) or "-"
            out.append(f"| `{m}` | {lo}-{hi} ({hi-lo+1}) | {cstr} | {cb} |")
    open(OUT_CALLGRAPH,"w").write("\n".join(out))

    # ---- module map md
    risk={r[0]:("LOW" if (r[4]<=2 and r[5]<=2) else ("HIGH" if r[5]>=5 else "MED")) for r in summary}
    mp=["# RUNNER_MODULE_MAP.md\n","Target layout + complete method->module index. via tools/runner_struct.py. status=PENDING|DONE@vX.\n",
        "## Target modules (leaf-first)\n","| order | unit | module | mixin | #meth | #lines | risk | status |","|---|---|---|---|---|---|---|---|"]
    for i,r in enumerate(body,1):
        u=r[0]; mod,mix,_=UNIT_MODULE[u]; mp.append(f"| {i} | {u} | `{mod}` | `{mix}` | {r[1]} | {r[2]} | {risk[u]} | PENDING |")
    cu=[r for r in summary if r[0]=="core"][0]
    mp.append(f"| - | core | `runner.py` | SiteRunner (stays) | {cu[1]} | {cu[2]} | n/a | n/a |")
    mp.append("\n## Complete method -> module index\n")
    for u in order:
        mod,mix,resp=UNIT_MODULE[u]; ms=sorted(um.get(u,[]),key=lambda m:SR[m][0])
        mp.append(f"\n### {u} -> `{mod}`  ({resp})")
        for m in ms:
            lo,hi=SR[m]; mp.append(f"- `{m}`  (L{lo}-{hi})")
    open(OUT_MAP,"w").write("\n".join(mp))

    os.makedirs(os.path.dirname(OUT_JSON),exist_ok=True)
    json.dump({"assign":assign,"summary":summary,"module_funcs":{k:list(v) for k,v in module_funcs.items()}},open(OUT_JSON,"w"),indent=1)
    print(f"wrote RUNNER_CALLGRAPH.md, RUNNER_MODULE_MAP.md, kb/runner_struct.json")
    print(f"  SiteRunner methods={len(names)} | core stays={cu[1]} methods/{cu[2]} lines")

if __name__=="__main__":
    main()
