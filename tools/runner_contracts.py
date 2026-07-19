#!/usr/bin/env python3
"""runner_contracts -- generate the three "contract" KB docs for the runner decomposition:
  RUNNER_PUBLIC_API.md   -- distinctive SiteRunner methods called outside runner.py
                            (the rename-unsafe surface) + the `_`-prefixed-but-public set
                            + the `runners` registry pattern.
  RUNNER_EVENT_VOCAB.md  -- the log_event kind vocabulary + the _BD_TO_APPRISE_EVENT
                            notify mapping (a dropped/renamed kind breaks SSE/notifications
                            silently -- this is the regression guard).
  RUNNER_TEST_COVERAGE.md-- per-unit test-file coverage (each cut's canary set) + the
                            structural-validation-only units (zero/low direct coverage).
AST + grep, stdlib-only, path-relative. Keep UNITS in sync with runner_struct.py.
"""
import ast, glob, collections, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "bulk_downloader")
RUNNER = os.path.join(PKG, "runner.py")

UNITS = collections.OrderedDict()
UNITS["transport"]={"_download_proxy_url","_do_direct_http_download","_try_multi_conn_download","_http_download","_http_download_parallel","_probe_size","_do_probe_fetch","_do_download","_looks_like_media","_probe_outcome","_integrity_size_ok","_promote_or_abort","_recommended_chunk_bytes","_observe_throughput","_current_cap_mbps"}
UNITS["extractors"]={"_try_jsonapi_extractor","_try_vixen_extractor","_try_dl8_extractor","_try_aylo_extractor","_try_library_extractor","_try_ytdlp_fallback","_try_deep_detect_fallback","_persist_deep_detect_selectors","_probe_for_higher_tier","_run_pre_scrape_action"}
UNITS["integrations"]={"_get_stash_client","_stash_dedup_check","_stash_scrape_preview","_stash_enrich_after_scan","_get_plex_client","_plex_enrich_after_scan","_get_jellyfin_client","_jellyfin_enrich_after_scan","_get_qb_client","_record_qb_outcome","qb_health","_try_qb_download","_get_jd_client","_read_cookies_for_jd","_record_jd_outcome","jd_health","_try_jd_download"}
UNITS["manual"]={"start_manual_download","finish_manual_download","cancel_manual_download","is_awaiting_manual_download"}
UNITS["auth"]={"login_async","start_manual_login","_poll_manual_cookies","start_captcha_solve_session","end_captcha_solve_session","finish_manual_login","verify_login_after_wizard","get_last_verify_result","cancel_manual_login_pending","is_awaiting_manual_login","_handle_auth_required","maybe_preemptive_relogin","_check_cookies_or_relogin","_cookie_age_hours","_check_redirect"}
UNITS["teach"]={"_teach_base_url","teach_verify","teach_test_download","teach_commit","teach_cancel","_draft_override_template","_override_suppresses_persist","_persist_learned_to_draft","_handle_auto_teach_check","_recover_selector"}
UNITS["scheduler"]={"_start_auto_retry","_stop_auto_retry","_parse_retry_schedule","_auto_retry_loop","_scan_subscriptions","_auto_retry_scan","start_scheduler","stop_scheduler","_sched_loop","_next_sched_dt","sched_next_str","_load_rl","_save_rl","_clear_rl","_maybe_drift_recover"}
UNITS["queue"]={"load_urls","reorder_urls","set_priority","bulk_priority","bulk_delete","bulk_approve","bulk_pause","bulk_resume","bulk_retry","bulk_reorder","bulk_url_transform","clear_completed","retry_failed","retry","clear","export_urls","_restore_queue","_drain_url_queue"}
UNITS["browser"]={"_pw_save","_context_options","_launch_args","_manual_profile_dir","_profile_dir","_launch_browser","_install_stealth","_apply_stealth_library_to_page","_warm_session"}
UNITS["challenge"]={"_has_captcha","_handle_captcha_check","_try_turnstile_solve","_try_captcha_solve","_try_turnstile_solve_LEGACY"}
UNITS["telemetry"]={"log_event","get_events","_install_event_listeners","_flush_fingerprint_observation","_pick_fastest_mirror","_build_mirror_urls","_extract_host","_classify_error","_handle_failure","_screenshot","_parse_hm","_fmt_dur"}
UNITS["integrity"]={"_dedup_hash_worker","_dedup_preflight","_verify_hash_or_quarantine","_verify_integrity_or_quarantine","_embed_metadata_if_mp4","_apply_quality_preference"}
UNITS["accounts"]={"_get_active_account","_rotate_account_if_available","_persist_account_state","_persist_pool_state","is_rate_limited","rl_remaining","trigger_rate_limit","_wait_rl_autostart"}
def unit_of(m):
    for u,ms in UNITS.items():
        if m in ms: return u
    return "core"
COMMON={'start','stop','clear','state','pause','resume','retry','update_config','start_scheduler','load','save','run','cancel'}

def main():
    t=ast.parse(open(RUNNER,encoding="utf-8").read())
    sr={b.name for n in t.body if isinstance(n,ast.ClassDef) and n.name=='SiteRunner'
        for b in n.body if isinstance(b,(ast.FunctionDef,ast.AsyncFunctionDef))}
    assign={m:unit_of(m) for m in sr}

    # ---- PUBLIC API ----
    ext=collections.Counter(); ext_where=collections.defaultdict(set)
    for f in glob.glob(PKG+'/*.py')+glob.glob(PKG+'/blueprints/*.py'):
        if f.endswith('runner.py'): continue
        try: tt=ast.parse(open(f).read())
        except (SyntaxError, UnicodeDecodeError): continue
        for node in ast.walk(tt):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) \
               and node.func.attr in sr and node.func.attr not in COMMON:
                ext[node.func.attr]+=1; ext_where[node.func.attr].add(os.path.basename(f))
    pub=sorted(x for x in ext if x!='__init__')
    privpub=sorted(x for x in pub if x.startswith('_'))
    out=["# RUNNER_PUBLIC_API.md\n",
         "SiteRunner is instantiated per site into a global `runners = {}` dict in `app.py`",
         "(keyed by `site_id`), which is dependency-injected into ~10 helper modules (perf_lab,",
         "capacity, dispatch_chain, circuit-review, ...). So the method API is broad and",
         "multi-module. The decomposition must preserve every method NAME (the snapshot's",
         "set-freeze enforces this); moving a method between mixins is safe (resolved via the",
         "instance), but RENAMING any name below breaks an external caller.\n",
         "via tools/runner_contracts.py.\n",
         f"## Distinctive methods called outside runner.py: {len(pub)}  (the rename-unsafe floor)",
         f"## Plus obvious lifecycle (matched-but-filtered as common names): start, stop, pause, resume, clear, state, retry, update_config\n",
         f"### `_`-prefixed but EXTERNALLY CALLED -- do NOT rename (despite the underscore): {privpub}\n",
         "| method | unit | ext calls | caller modules |","|---|---|---|---|"]
    for m,c in ext.most_common():
        if m=='__init__': continue
        out.append(f"| `{m}` | {assign[m]} | {c} | {', '.join(sorted(ext_where[m]))} |")
    open(os.path.join(ROOT,"RUNNER_PUBLIC_API.md"),"w").write("\n".join(out))

    # ---- EVENT VOCAB ----
    src=open(RUNNER).read()
    kinds=sorted(set(re.findall(r'log_event\(\s*"([a-z_]+)"',src)))
    # apprise map block
    ap_lines=[]
    m=re.search(r'_BD_TO_APPRISE_EVENT[^{]*\{(.+?)\n\}',src,re.S)
    if m:
        for k,v in re.findall(r'"([a-z_]+)"\s*:\s*"([a-z_]+)"',m.group(1)):
            ap_lines.append((k,v))
    ev=["# RUNNER_EVENT_VOCAB.md\n",
        "The event-kind vocabulary SiteRunner emits via `self.log_event(kind, ...)`, consumed",
        "downstream by the SSE/event stream (`get_events`) and the notification layer",
        "(`_BD_TO_APPRISE_EVENT` -> apprise). **Regression guard:** a cut that drops or renames",
        "a kind breaks notifications/SSE SILENTLY (no test names most of these). `log_event`",
        "lives in the telemetry unit, but the kind LITERALS are scattered across every unit",
        "(each caller owns its string) -- moving log_event does not change them; verify this",
        "list is unchanged after a cut that touches telemetry or any emitting unit.\n",
        "via tools/runner_contracts.py.\n",
        f"## log_event kinds ({len(kinds)})\n",
        ", ".join("`%s`"%k for k in kinds),
        "\n## _BD_TO_APPRISE_EVENT mapping (runner kind -> apprise event)\n",
        "| runner kind | apprise event |","|---|---|"]
    for k,v in ap_lines:
        ev.append(f"| `{k}` | `{v}` |")
    open(os.path.join(ROOT,"RUNNER_EVENT_VOCAB.md"),"w").write("\n".join(ev))

    # ---- TEST COVERAGE ----
    tf=glob.glob(os.path.join(ROOT,'tests','*.py'))
    srcs={f:open(f).read() for f in tf}
    DISTINCT=[m for m in sr if not m.startswith('__') and m not in COMMON and len(m)>6]
    unit_tests=collections.defaultdict(set); method_hit=collections.defaultdict(set)
    for m in DISTINCT:
        pat=re.compile(r'\b%s\b'%re.escape(m))
        for f,s in srcs.items():
            if pat.search(s):
                unit_tests[assign[m]].add(os.path.basename(f)); method_hit[m].add(os.path.basename(f))
    tc=["# RUNNER_TEST_COVERAGE.md\n",
        "Per-unit test coverage = the canary set each cut runs from the EXTRACTED zip.",
        "Counts = distinct test files NAMING one of the unit's methods (a floor: the worker",
        "path exercises many methods without naming them, so 'uncovered' != untested). Units",
        "with near-zero direct coverage must be validated STRUCTURALLY (import-smoke + the api",
        "snapshot + live operator check), not by sandbox tests.\n",
        "via tools/runner_contracts.py.\n",
        "## Coverage by unit (cut order = ascending risk)\n",
        "| unit | test files | flag |","|---|---|---|"]
    for u in sorted(unit_tests, key=lambda u:-len(unit_tests[u])):
        n=len(unit_tests[u]); flag="" if n>=5 else ("STRUCTURAL-ONLY" if n==0 else "low -- lean on snapshot")
        tc.append(f"| {u} | {n} | {flag} |")
    for u in UNITS:
        if u not in unit_tests:
            tc.append(f"| {u} | 0 | STRUCTURAL-ONLY |")
    uncovered=collections.defaultdict(list)
    for m in DISTINCT:
        if not method_hit.get(m): uncovered[assign[m]].append(m)
    tc.append("\n## Methods not directly named in any test (validate via worker-path canary or structurally)\n")
    for u in sorted(uncovered):
        tc.append(f"- **{u}**: {', '.join('`%s`'%m for m in sorted(uncovered[u]))}")
    open(os.path.join(ROOT,"RUNNER_TEST_COVERAGE.md"),"w").write("\n".join(tc))
    print(f"wrote RUNNER_PUBLIC_API.md ({len(pub)} ext methods), RUNNER_EVENT_VOCAB.md ({len(kinds)} kinds), RUNNER_TEST_COVERAGE.md")
    print(f"  rename-unsafe '_' methods: {privpub}")

if __name__=="__main__":
    main()
