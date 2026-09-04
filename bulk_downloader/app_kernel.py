"""bulk_downloader.app_kernel -- hoisted config kernel (DECOMP-R2a).

Pure-literal DAG leaf: the SHIPPED config constants and defaults, moved
VERBATIM out of app.py so blueprints/runner/tests can import them from a
clean leaf instead of reaching back into the app.py hub. No imports, no
side effects. app.py re-exports every name here, so
`from bulk_downloader.app import CFG_FIELDS` and `getattr(app, "DEFAULTS")`
still resolve to THESE objects (same identity).
"""

SESSION_IDLE_TTL = 8 * 3600     # 8 hours sliding
PAIRING_TTL = 5 * 60            # 5 min from generation
RATE_LIMIT_WINDOW = 5.0    # seconds
CFG_FIELDS=["name","login_url","username","password","user_field","pass_field","submit_btn","login_trigger",
            "success_url","cookie_file","trigger_selector","dl_selector","download_dir",
            "wait","delay","max_concurrent","max_retries","no_button_threshold","disk_threshold_gb","headless",
            "sched_enabled","sched_time","sched_repeat",
            "min_resolution","verify_integrity","prelogin_minutes",
            # Cut 665: yt-dlp download-engine tuning. ytdlp_concurrent_fragments
            # (2.2: parallel HLS/DASH segments; 0/1 => yt-dlp default of 1);
            # download_rate_limit (2.5: per-download bandwidth cap, a yt-dlp rate
            # string e.g. "2M"; "" => unlimited).
            "ytdlp_concurrent_fragments","download_rate_limit",
            # v3.47.7: auto-relogin toggle + interval
            "auto_relogin_enabled","auto_relogin_interval_hours",
            # MOD-1 F1.4 (v3.66.810): predictive relogin -- fire at fraction*median
            # of LEARNED session lifetimes. In CFG_FIELDS so a per-site setting
            # survives the _load_sites_config rebuild (was dropped before).
            "predictive_relogin_enabled","predictive_relogin_fraction",
            "filename_template","use_http_dl","chunk_size_mb","skip_if_exists",
            # dismiss_selectors is the PER-PAGE block (cookie / age / consent),
            # tried on every content URL. dismiss_selectors_login is the
            # post-login wall, fired ONCE in do_login -- v3.66.1016, item E.
            # Both are in CFG_FIELDS so a template-set value survives the
            # _load_sites_config rebuild, the same reason predictive_relogin_*
            # above are here.
            "dismiss_selectors","dismiss_selectors_login",
            # Phase 9: Cloudflare-resistance toggles. Real Chrome is opt-in;
            # the stealth and persistent-profile toggles default ON.
            "use_real_chrome","use_stealth","use_stealth_library","use_persistent_profile",
            # Phase 13: optional per-site network request log. Off by
            # default — when enabled the runner records every non-asset
            # response in the event log (used to find direct video URLs).
            "log_network",
            # Phase 15: behavioral anti-detection toggles.
            #  use_curl_cffi  — TLS-impersonating HTTP for direct downloads
            #  proxy          — "scheme://[user:pass@]host:port" for browser+httpx
            #  warmup_urls    — newline/comma list of URLs to visit before targets
            #  warmup_every   — seconds between warmups (0 = every URL, default 1800)
            #  captcha_provider — "2captcha" or "capsolver" (default 2captcha)
            #  captcha_api_key  — provider API key for Turnstile auto-solve
            "use_curl_cffi","proxy","warmup_urls","warmup_every",
            "captcha_provider","captcha_api_key","use_captcha_relay",
            # v3.66.324 (Phase 4 gap A4 / GAP1): per-site VPN routing block.
            # Resolved by vpn_runtime.is_vpn_required_for_site/get_tunnel_for_site;
            # the kill-switch gate keys off vpn_required. Categorize "general" ->
            # gui-safe, so they render in the schema-driven SiteSettings editor.
            "vpn_enabled","vpn_required","vpn_kill_switch_strict","vpn_tunnel_id",
            # Phase 17: download reliability.
            #  parallel_chunks   — N parallel HTTP Range requests for big files (1 = serial)
            #  parallel_min_size_mb — only use parallel when file is bigger than this
            #  mirror_subdomains — newline list to retry the URL with on failure
            #  verify_hash       — verify md5/sha1/sha256 if site advertises one
            #  min_size_pct      — reject if downloaded < N% of advertised size
            #  bandwidth_schedule_enabled / max_mbps_day / max_mbps_night /
            #  day_start / day_end — time-of-day speed cap
            #  auto_chunk_size   — dynamically tune chunk_size_mb from observed throughput
            "parallel_chunks","parallel_min_size_mb","mirror_subdomains",
            "verify_hash","min_size_pct","auto_chunk_size",
            "bandwidth_schedule_enabled","max_mbps","max_mbps_day","max_mbps_night",
            "day_start","day_end",
            # Phase 18: site & queue management.
            #  url_patterns — newline list of regexes; URLs matching any
            #                 route to this site (used by /api/quick_add and
            #                 /api/route_urls). Higher priority than
            #                 hostname matching.
            #  tags         — comma-separated free-form tags for sidebar
            #                 grouping/filtering.
            "url_patterns","tags",
            # Phase 19: when true, the FIRST login attempt for a site that
            # has no learned login selectors triggers Manual Login mode
            # automatically (instead of running the auto-login chain). The
            # FIRST download attempt for a site that has no learned download
            # selectors triggers takeover automatically. Once selectors are
            # learned, the flag is effectively a no-op until learning is
            # reset. Saves you a guaranteed-to-fail auto-attempt on a brand
            # new site, and forces capture of the right selectors first try.
            "auto_teach_first_run",
            # Phase 20: Integration Hooks.
            #  post_download_cmd       — shell command template; {path}, {url},
            #                            {site}, {filename}, {size},
            #                            {resolution}, {hash} placeholders.
            #  post_download_timeout   — seconds before SIGKILL (default 120)
            #  webhook_urls            — newline list of JSON POST endpoints
            #  webhook_events          — comma list of subscribed events
            #                            (default: completed,failed,low_disk)
            #  stash_enabled / _url / _api_key — Stash GraphQL scan trigger
            #  plex_enabled / _url / _token / _section_id — Plex refresh
            #  jellyfin_enabled / _url / _api_key — Jellyfin scan
            #  ha_enabled / _url / _token / _service / _events
            #                          / _message_template — HA notify
            #  spillover_dirs          — newline list of fallback download
            #                            dirs. First dir with free space
            #                            above spillover_threshold_pct wins.
            #  spillover_threshold_pct — % free below which a dir is
            #                            skipped (default 5.0)
            "post_download_cmd", "post_download_timeout",
            "webhook_urls", "webhook_events",
            "stash_enabled", "stash_url", "stash_api_key",
            "plex_enabled", "plex_url", "plex_token", "plex_section_id",
            "jellyfin_enabled", "jellyfin_url", "jellyfin_api_key",
            "ha_enabled", "ha_url", "ha_token", "ha_service",
            "ha_events", "ha_message_template",
            "spillover_dirs", "spillover_threshold_pct",
            # Phase 24.7: per-site push notification opt-outs. Default
            # all true — user can flip them off per-site for noisy ones.
            "push_on_review", "push_on_failures", "push_on_low_disk",
            # Phase 25.7: per-site accent color. Used to tint the site
            # card's status dot + progress bar + sidebar selection edge,
            # making it easier to spot which site is which at a glance
            # when 10+ sites are configured.
            "accent_color",
            # Phase 30: auto-retry stuck jobs. The runner has a background
            # scanner that bumps needs_review / failed jobs back to pending
            # on a configurable schedule. Capped via max_attempts so truly
            # broken URLs don't burn cycles forever.
            "auto_retry_review", "auto_retry_failed",
            "auto_retry_schedule", "auto_retry_max_attempts",
            # Phase 31: accounts management
            #   accounts          — list of {username, password, cookie_file,
            #                         cooldown_until?, last_failure?, label?}
            #   accounts_mode     — "failover" (rotate on auth failure)
            #                       or "round_robin" (rotate every N URLs)
            #   accounts_rotate_every — int, used in round_robin mode
            "accounts", "accounts_mode", "accounts_rotate_every",
            # Phase 61 (v3.43.16): yt-dlp fallback. When the normal flow
            # fails on a URL (captcha, no button, etc.), optionally try
            # yt-dlp as a last resort before marking needs_review.
            "use_ytdlp_fallback",
            # C6 (8.4): gallery-dl fallback. Tried after yt-dlp for sites
            # gallery-dl handles that yt-dlp doesn't. Opt-in, same as above.
            "use_gallerydl_fallback",
            # Phase 63 (v3.43.16): pre-emptive cookie re-login. When cookies
            # are older than cookie_max_age_hours, the auto-retry loop
            # triggers a manual login session BEFORE downloads start failing.
            "auto_preemptive_relogin", "cookie_max_age_hours",
            # Phase 65 (v3.43.16): per-site disk quota. Independent of
            # disk_threshold_gb (which is SYSTEM free space). This is a
            # USED-bytes cap so one site can't monopolize a shared volume.
            "site_quota_gb",
            # Phase 67 (v3.43.16): explicit quality preference order.
            # Comma-separated list of resolutions, e.g. "1080,720,best".
            # Overrides the min_resolution gate for sites with multiple
            # quality candidates.
            "quality_preference",
            # Phase 64 (v3.43.16): bandwidth-aware concurrency. When >0,
            # workers scale up/down to hit the target throughput (Mbps).
            "bandwidth_target_mbps",
            # Phase 66 (v3.43.16): cross-site filename duplicate detection.
            # Emits cross_site_dupe events when a downloaded filename
            # matches one from another site's history.
            "cross_site_dedup",
            # Phase 69 (v3.43.16): speculative mirror failover. Race HEAD
            # requests across all mirrors before download; pick fastest.
            "speculative_mirror_select",
            # Phase 70 (v3.43.16): conditional post-download pipeline.
            # List of {condition, command, name?, timeout?} rules.
            "post_download_pipeline", "post_download_pipeline_first_match",
            # Phase 72 (v3.43.16): retry once when ffprobe integrity fails
            # before quarantining. Recovers from transport hiccups.
            "retry_on_corruption",
            # Phase 73 (v3.43.16): RSS-style URL subscriptions. List of
            # {name, url, interval_hours, last_run_ts} dicts. The
            # auto_retry loop scans listings on schedule and imports new URLs.
            "subscriptions",
            # v3.43.16: session keep-alive opt-in flag. Default True via
            # DEFAULTS; per-site checkbox in the edit modal.
            "keep_alive_enabled",
            # v3.43.21: backend selector + JD connection details. Without
            # these in CFG_FIELDS the values would be dropped on reload.
            "backend", "jd_host", "jd_port",
            # v3.66.702 (JD-3, operator decision C): JD supported-hosts endpoint
            # path, promoted from an undeclared cfg key to a first-class GUI
            # field. In CFG_FIELDS so it survives reload (as jd_host/jd_port).
            "jd_supported_hosts_path",
            # v3.66.468 (WS2): per-site list of unpacked chromium extension dirs,
            # appended to the launch args as --load-extension /
            # --disable-extensions-except. In CFG_FIELDS so it survives reload;
            # config-file-managed (advanced/power-user knob, not a GUI control).
            "chromium_extensions",
            # v3.66.468 (WS2): per-site unpacked chromium extensions (list of
            # dirs) appended to the launch as --load-extension. Config-file
            # managed (advanced/power-user); in CFG_FIELDS so it survives reload.
            "chromium_extensions",
            # v3.43.26: qBittorrent connection details. Same reasoning.
            "qb_host", "qb_port", "qb_username", "qb_password",
            # v3.43.28: deep Stash integration. The basic stash_url/
            # stash_api_key already existed (v3.20); these flags
            # gate the newer pre-download dedup, scrape preview,
            # and post-download enrichment paths.
            "stash_deep_enabled", "stash_dedup_check",
            "stash_scrape_preview", "stash_studio_id",
            "stash_auto_studio", "stash_tags",
            # v3.43.29: deep Plex integration. Basic plex_url/
            # plex_token/plex_section_id already existed (v3.20);
            # these flags gate post-download match confirmation,
            # recently-added boost, and per-site collection routing.
            "plex_deep_enabled", "plex_match_confirm",
            "plex_recently_added_boost", "plex_collection",
            "plex_auto_create_collection",
            # v3.43.30: watch folder for .txt URL imports
            "watch_enabled", "watch_folder", "watch_poll_seconds",
            "watch_url_priority",
            # v3.43.33: AI-assisted login form detection. When the
            # built-in selector enumeration fails (or just to skip
            # the enumeration entirely), ask the local LLM to look
            # at the page and propose selectors.
            "ai_login_assist_enabled",
            # v3.43.37: deep Jellyfin integration. Mirrors Plex deep:
            # per-item refresh, match confirmation, collection
            # routing. Basic jellyfin_url/jellyfin_api_key already
            # exist (v3.20); these add the deep flags.
            "jellyfin_deep_enabled", "jellyfin_user_id",
            "jellyfin_match_confirm", "jellyfin_collection",
            "jellyfin_auto_create_collection",
            # v3.43.35: per-site account-pool cooldown duration.
            # 0/blank = use default (5 min). Sites known to throttle
            # hard can configure 3600 (1h).
            "account_cooldown_seconds",
            # v3.43.37: smart per-failure-class retry policy. When
            # True (default), retries use exponential backoff with
            # jitter and class-specific budgets (transient: 6, rate:
            # 10, auth: 3, permanent: 0). When False, falls back to
            # the legacy uniform schedule.
            "auto_retry_classify",
            # v3.43.41: per-site download windows. Gates worker
            # start-hours separately from the bandwidth_schedule's
            # speed-cap windows. window_active_hours is comma-
            # separated HH:MM-HH:MM ranges; window_action_outside
            # is "paused" (default) or "stopped".
            "window_enabled",
            "window_active_hours",
            "window_action_outside",
            # v3.43.42: storage tier migration. Auto-moves completed
            # files older than N days from the primary download_dir
            # to a cold storage tier (slow HDD, NAS, etc). Distinct
            # from spillover_dirs which is write-side overflow.
            "storage_tier_enabled",
            "storage_tier_dir",
            "storage_tier_age_days",
            "storage_tier_min_size_mb",
            "storage_tier_mode",
            # v3.43.44: auto-managed URL pattern fingerprint. Updated
            # automatically on successful download. Not user-editable;
            # surfaces through scoring as a +30 boost for candidates
            # matching prior-success hostnames or path prefixes.
            "url_fingerprint",
            # v3.43.64: MP4 metadata embedding. After every successful
            # download whose file is an .mp4 / .m4v / .mov / .m4a, the
            # runner writes title/performer/album/date/comment/cover
            # atoms via mutagen so Plex/Jellyfin/Stash pick up the
            # metadata without a separate post-processing pass.
            #
            #  embed_metadata             — master toggle (default True)
            #  embed_cover_art            — fetch + embed thumbnail
            #                                (default True; off saves
            #                                ~50-500KB per file)
            #  metadata_genre             — optional genre tag, free
            #                                text. Applied to every
            #                                download for this site.
            #  metadata_description       — optional long description
            #                                applied to every download.
            #  metadata_cover_timeout_s   — seconds to wait for the
            #                                thumbnail fetch before
            #                                giving up (default 10).
            "embed_metadata",
            "embed_cover_art",
            "metadata_genre",
            "metadata_description",
            "metadata_cover_timeout_s",
            # v3.43.65: 8K-first quality cascade — tier-probe + pre-
            # scrape action. Both opt-in per site.
            #
            # tier_probe_enabled — master toggle (default False)
            # tier_probe_pattern — regex with (?P<tier>...) group OR
            #                       known-pattern key like "vixen_network".
            #                       Default empty (no probing).
            # tier_probe_ladder  — comma-separated tier ladder, e.g.
            #                       "4320,2160,1080". Default empty
            #                       (uses tier_probe.DEFAULT_LADDER).
            # tier_probe_timeout_s    — per-HEAD timeout (default 5.0)
            # tier_probe_max_attempts — total HEAD cap (default 6)
            # pre_scrape_action  — dict {selector, wait_for, select}
            #                       describing a pre-scrape menu click
            #                       to force the player to load the
            #                       highest quality. Default empty.
            "tier_probe_enabled",
            "tier_probe_pattern",
            "tier_probe_ladder",
            "tier_probe_timeout_s",
            "tier_probe_max_attempts",
            "pre_scrape_action",
            # v3.43.66: Aylo network flashvars extractor toggles.
            # Default `use_aylo_extractor` is True so sites in the
            # Aylo brand list auto-benefit. The other three are
            # opt-in tuning knobs:
            #   aylo_force_format    — "" / "mp4" / "hls" (empty =
            #                           auto, prefers MP4 for resume)
            #   aylo_force_mp4       — legacy alias for the above
            #   aylo_premium_only    — skip non-premium variants
            "use_aylo_extractor",
            "aylo_force_format",
            "aylo_force_mp4",
            "aylo_premium_only",
            # v3.43.67: Vixen Media Group extractor. Auto-enables for
            # Vixen / Blacked / Tushy / Deeper / Slayed / Wifey /
            # MilfyMD / VixenPlus / BlackedRaw / TushyRaw.
            #   use_vixen_extractor — master toggle (default True)
            "use_vixen_extractor",
            # v3.43.68: HereSphere/DeoVR JSON API extractor. OFF by
            # default per site — needs to be turned on per-site by
            # the user (or by the auto-probe wizard step which finds
            # sites that respond to /heresphere or /deovr).
            #
            # use_jsonapi             — master toggle
            # jsonapi_url             — full endpoint base, e.g.
            #                            https://api.naughtyapi.com/heresphere
            # jsonapi_protocol        — "heresphere", "deovr", or ""
            #                            (auto-detect from response)
            # jsonapi_id_regex        — custom regex for scene-ID
            #                            extraction; empty = use heuristics
            # jsonapi_prefer_codec    — "h264" / "h265" / "" (DeoVR only)
            # jsonapi_timeout_s       — HTTP timeout for the API fetch
            "use_jsonapi",
            "jsonapi_url",
            "jsonapi_protocol",
            "jsonapi_id_regex",
            "jsonapi_prefer_codec",
            "jsonapi_timeout_s",
            # v3.43.69: dl8-video VR extractor + Badoink filename
            # prediction. Auto-enables for TmwVRnet, BadoinkVR, BabeVR,
            # VRCosplayX, 18VR, RealVR.
            #   use_dl8_extractor — master toggle (default True)
            #   dl8_predict_badoink_filenames — enable trailer-URL →
            #       member-area HEAD probing on Badoink-family hosts
            "use_dl8_extractor",
            "dl8_predict_badoink_filenames",
            # v3.43.70: apprise notifications. One global config —
            # the runner reads from app-level settings (not per-site)
            # because notifications are typically a deployment-level
            # concern. We surface the fields in CFG_FIELDS anyway so
            # the round-trip serialization works (UI may store them
            # as part of the site OR as global; we accept both).
            #
            # notify_apprise_enabled — master toggle (default False)
            # notify_apprise_urls    — multi-line apprise URL list
            # notify_<event>_mode    — "per_event" or "batched"
            # notify_<event>_batch   — events accumulated before flush
            # notify_<event>_wait_s  — max seconds in batch before flush
            "notify_apprise_enabled",
            "notify_apprise_urls",
            # Per-event policy fields — one set per ALL_EVENT_TYPES.
            # We list them explicitly here so they survive a save-load
            # cycle through the site config schema validator.
            "notify_download_done_mode",
            "notify_download_done_batch",
            "notify_download_done_wait_s",
            "notify_download_failed_mode",
            "notify_download_failed_batch",
            "notify_download_failed_wait_s",
            "notify_captcha_mode",
            "notify_auth_required_mode",
            "notify_disk_full_mode",
            "notify_queue_empty_mode",
            "notify_queue_paused_mode",
            "notify_queue_resumed_mode",
            "notify_server_start_mode",
            "notify_server_shutdown_mode",
            # v3.43.72: perceptual dedup.
            #   dedup_hash_on_done — auto-hash completed downloads
            #   dedup_distance     — Hamming distance threshold (0-32)
            #   dedup_db_path      — registry SQLite path
            #   dedup_policy       — keep_all / keep_largest / keep_first
            "dedup_hash_on_done",
            "dedup_distance",
            "dedup_db_path",
            "dedup_policy",
            # v3.43.73: Scrapling adaptive selectors + Turnstile bypass.
            # Both default False — opt-in per site until templates
            # accumulate enough fingerprints to justify enabling broadly.
            #   use_scrapling_recovery — recover broken learned selectors
            #     via Scrapling's content-based fingerprint matching
            #   use_scrapling_turnstile — detect Cloudflare Turnstile
            #     challenges and bypass via StealthyFetcher
            #   scrapling_recovery_min_score — match confidence threshold
            #     (0.0-1.0, default 0.6)
            "use_scrapling_recovery",
            "use_scrapling_turnstile",
            "scrapling_recovery_min_score",
            # v3.43.74: FlareSolverr (external Cloudflare-challenge
            # solver) + multi-connection chunked downloads.
            #
            # FlareSolverr is an external Docker service. BD POSTs the
            # target URL to its HTTP API and receives post-challenge
            # cookies + HTML. Alternative to v3.43.73's in-process
            # Scrapling StealthyFetcher. Endpoint defaults to
            # http://localhost:8191/v1 (the Docker default).
            #
            # Multi-conn opens N parallel byte-range connections for
            # large files. Default 4 connections; min file size for
            # activation is 100 MB to avoid overhead on small files.
            "use_flaresolverr",
            "flaresolverr_endpoint",
            "flaresolverr_timeout_s",
            "flaresolverr_max_timeout_ms",
            "use_multi_conn",
            "multi_conn_count",
            "multi_conn_min_size_mb",
            "multi_conn_timeout_s",
            # v3.43.75: four feature toggles.
            # Playlist fan-out — when set, listing URLs (categories,
            # model pages, playlists) auto-expand into all scene URLs.
            "use_playlist_extractor",
            "playlist_max_pages",
            # ROW 374: authenticated GUI scene discovery.  The listing URL
            # and bounded/polite controls persist per site so the operator can
            # resume a whole library, while the default action remains cheap
            # newest-N discovery.
            "crawler_listing_url",
            "crawler_newest_n",
            "crawler_max_pages",
            "crawler_max_scrolls",
            "crawler_delay_s",
            "crawler_title_fetch_limit",
            # yt-dlp download_archive interop — read/write the archive
            # file so BD coexists with yt-dlp on the same library.
            "use_ytdlp_archive",
            "ytdlp_archive_path",
            # MutationObserver lazy-player wait — fallback for sites
            # that lazy-load <video> elements after interaction.
            "use_mutation_observer",
            "mutation_observer_timeout_ms",
            # v3.43.76: three new feature groups.
            #
            # Phoenix catalog — when set, BD looks up unknown URLs in
            # the embedded brand catalog and surfaces smart-routing
            # suggestions. The catalog is offline; no network used.
            "use_phoenix_catalog",
            "use_smart_routing",
            # Bandwidth supervisor — token-bucket throttling. Global
            # + per-site rate caps in bytes-per-second. 0 = unlimited.
            "use_download_supervisor",
            "supervisor_global_bps",
            "supervisor_per_site_bps",   # bps just for THIS site
            # Thumbnail generation — auto-jpg per download via ffmpeg.
            # mode: "single" (one frame) or "sheet" (contact sheet).
            # dir_mode: "sidecar" (next to video) or "parallel"
            # (thumbs/ subfolder).
            "use_thumbnails",
            "thumbnail_mode",
            "thumbnail_dir_mode",
            "thumbnail_sheet_rows",
            "thumbnail_sheet_cols",
            # v3.43.77: Search-and-add by query.
            #   use_search_extractor       — opt-in per site
            #   search_url_pattern         — e.g. https://x/?s={query}
            #   search_result_selector     — CSS selector for results
            #                                 (optional; defaults to
            #                                 scene-link heuristic)
            "use_search_extractor",
            "search_url_pattern",
            "search_result_selector",
            # v3.43.81 Phase 161: v3.43.80 module-config flags. Each is
            # opt-in per site; defaults from DEFAULTS dict below.
            #  use_cluster_rate              — gate _process_one on cluster_rate.acquire_lease
            #  cluster_rate_max_concurrent   — cluster-wide cap (default 4)
            #  cluster_rate_lease_seconds    — auto-expiry safety (default 600)
            #  use_tpdb                      — TPDB enrichment hook after _update_job done
            #  tpdb_api_key                  — TPDB credential (secret; preserved on blank)
            #  use_subtitles                 — subliminal download hook after done
            #  subtitle_languages            — array of ISO-639-1 codes, default ["en"]
            #  use_priority_scoring          — sort pending URLs by queue_priority score
            #  dedup_hash_on_done            — perceptual-hash on completion (Phase 72)
            "use_cluster_rate", "cluster_rate_max_concurrent",
            "cluster_rate_lease_seconds",
            "use_tpdb", "tpdb_api_key",
            "use_subtitles", "subtitle_languages",
            "use_priority_scoring",
            "dedup_hash_on_done",
            ]
DEFAULTS={"wait":4,"delay":3,"max_concurrent":2,"max_retries":2,"no_button_threshold":5,"disk_threshold_gb":2.0,
          "sched_enabled":False,"sched_time":"","sched_repeat":"once",
          "min_resolution":1080,"verify_integrity":True,"prelogin_minutes":15,
          # Cut 665: inert by default (segment-parallel off => yt-dlp's 1/fragment;
          # rate cap empty => unlimited). Opt-in per site.
          "ytdlp_concurrent_fragments":0,"download_rate_limit":"",
          "filename_template":"{filename}{ext}","use_http_dl":True,"chunk_size_mb":4,"skip_if_exists":True,
          "dismiss_selectors":"",
          # v3.47.7: auto-relogin controls. Surfaced as a per-site
          # checkbox + interval in the config UI. When enabled, the
          # cookie_relogin scheduler triggers a fresh login pass every
          # auto_relogin_interval_hours hours OR when cookies expire,
          # whichever comes first. Default ON because cookies do expire
          # and the old behavior (silent failure on expiry) caused the
          # "login screen reappears even though it said online" bug.
          "auto_relogin_enabled":True,
          # MOD-1 F1.4: predictive relogin off by default (byte-identical to the
          # pre-cut behaviour where the key was absent); fraction 0.8 mirrors
          # relogin_predict.DEFAULT_FRACTION.
          "predictive_relogin_enabled":False,"predictive_relogin_fraction":0.8,
          "auto_relogin_interval_hours":12,
          "use_real_chrome":False,"use_stealth":True,"use_persistent_profile":True,
          # v3.43.56: opt-in playwright-stealth library integration.
          # When True AND the library is installed (pip install
          # playwright-stealth), workers apply the library's
          # comprehensive evasions to every new page on top of the
          # built-in STEALTH_JS. Defaults False because:
          #   1) most sites don't need it (STEALTH_JS alone is enough)
          #   2) library is an optional dep — fail-open if missing
          # Enable for Cloudflare / Datadome / PerimeterX sites.
          "use_stealth_library":False,
          # v3.43.16: headless workers default to ON. Was implicitly False
          # because the config.get("headless", False) call sites defaulted
          # there, which meant workers opened VISIBLE Chrome windows
          # alongside the takeover. With Chrome's profile-sharing behavior
          # on Windows, those visible windows can end up sharing the same
          # OS-level download dir as the takeover, bypassing the worker's
          # Playwright download routing. Workers now default headless;
          # set headless=False in site config only for debugging.
          # v3.43.16: keep-alive defaults ON. Background thread heartbeats
          # every 5 min and triggers a relogin 10 min before predicted
          # session expiry. Per-site opt-out via UI checkbox or this field.
          "keep_alive_enabled":True,
          "headless":True,
          # Phase 41.6: separate persistent profile for manual login/teach
          # sessions. Enables password manager extensions to be installed once
          # and persist across sessions. Isolated from worker profiles.
          "manual_use_persistent_profile":True,
          "log_network":False,
          "use_curl_cffi":True,"proxy":"","warmup_urls":"","warmup_every":1800,
          "captcha_provider":"2captcha","captcha_api_key":"",
          "parallel_chunks":1,"parallel_min_size_mb":100,"mirror_subdomains":"",
          "verify_hash":True,"min_size_pct":5.0,"auto_chunk_size":False,
          "bandwidth_schedule_enabled":False,"max_mbps":0,
          "max_mbps_day":0,"max_mbps_night":0,
          "day_start":"09:00","day_end":"17:00",
          "url_patterns":"","tags":"",
          "auto_teach_first_run":True,
          # Phase 20: hooks default to off; user opts in per-site.
          "post_download_cmd":"","post_download_timeout":120,
          "webhook_urls":"","webhook_events":"completed,failed,low_disk",
          "stash_enabled":False,"stash_url":"","stash_api_key":"",
          "plex_enabled":False,"plex_url":"","plex_token":"","plex_section_id":"",
          "jellyfin_enabled":False,"jellyfin_url":"","jellyfin_api_key":"",
          "ha_enabled":False,"ha_url":"","ha_token":"","ha_service":"notify",
          "ha_events":"failed,low_disk",
          "ha_message_template":"[{site}] {event}: {filename} {message}",
          "spillover_dirs":"","spillover_threshold_pct":5.0,
          # Phase 24.7: push opt-ins default on so mobile users get
          # notified of actionable events without configuration.
          "push_on_review":True,"push_on_failures":True,"push_on_low_disk":True,
          # Phase 25.7: empty means "use theme default"
          "accent_color":"",
          # Phase 30: auto-retry off by default. Schedule "1h,4h,24h" is a
          # reasonable progression for transient site issues without
          # hammering. Max 3 attempts so total elapsed = ~29h before giving
          # up (1h + 4h + 24h = 29h).
          "auto_retry_review":False, "auto_retry_failed":False,
          "auto_retry_schedule":"1h,4h,24h", "auto_retry_max_attempts":3,
          # Phase 31: accounts default empty; mode is failover (legacy)
          "accounts":[], "accounts_mode":"failover", "accounts_rotate_every":50,
          # Phase 61 (v3.43.16): yt-dlp fallback is opt-in. Most users don't
          # have yt-dlp installed and we don't want to spam the event log
          # with "yt-dlp not on PATH" warnings.
          "use_ytdlp_fallback":False, "use_gallerydl_fallback":False,
          # Phase 63: pre-emptive re-login off by default. Threshold 168h
          # (1 week) — most session cookies last weeks-to-months; this is a
          # conservative refresh that triggers well before expiry.
          "auto_preemptive_relogin":False, "cookie_max_age_hours":168.0,
          # Phase 65: no quota by default (None means "no cap"). The runner
          # treats falsy values as "no quota check needed". Distinct from
          # disk_threshold_gb which is system free-space.
          "site_quota_gb":None,
          # Phase 67: quality preference order. Empty means "pick the
          # highest-scoring candidate" (legacy behavior).
          #
          # v3.43.54: default was "2160,1080,720" (4K-first).
          # v3.43.65: flipped to "4320,3160,2880,2160,1440,1080,720" —
          # 8K-first cascade. Rationale: Matt's hardware (768GB RAM,
          # dual Xeon Platinum, GPU, fast E:\\ drive) chews through 8K
          # HEVC fine, and the queue runs against sites that serve 8K
          # natively (wowgirls, ultrafilms, several VIP4K-network).
          # The cascade falls through gracefully — if a site doesn't
          # offer 8K, the next available tier wins. Sites that prefer
          # something different (e.g. wifi-limited deployments wanting
          # 1080p first to save bandwidth) can override per-site.
          # Special value "best" short-circuits the cascade and picks
          # the single highest-scoring candidate.
          "quality_preference":"4320,3160,2880,2160,1440,1080,720",
          # Phase 64: 0 = disabled (legacy static max_concurrent behavior)
          "bandwidth_target_mbps": 0.0,
          # Phase 66: opt-in cross-site filename dedup (off by default —
          # most users with one site config don't need this; emitting
          # extra events on multi-site setups is the only side effect).
          "cross_site_dedup": False,
          # Phase 69: speculative mirror failover off by default. HEAD
          # requests are cheap but introduce up to 5s of "did anyone win"
          # delay if all mirrors are slow.
          "speculative_mirror_select": False,
          # Phase 70: empty pipeline by default. Existing post_download_cmd
          # still runs; pipeline is additive.
          "post_download_pipeline": [],
          "post_download_pipeline_first_match": False,
          # Phase 72: retry on corruption off by default. Wasted bandwidth
          # vs always-quarantine; user opts in if their network drops
          # often.
          "retry_on_corruption": False,
          # Phase 73: empty subscriptions list by default.
          "subscriptions": [],
          # v3.43.21: download backend selector. "teach" = current
          # Playwright/teach-mode flow. "jd" = forward to a local
          # JDownloader 2 instance via its Remote API for sites where
          # JD has working plugins (brazzers, bangbros, adulttime,
          # xnxx, xempire, evilangel, filthykings, etc.). BulkDownloader
          # still owns the session (cookies via session keeper), but JD
          # does the actual file transfer. On JD failure (auth,
          # unreachable, plugin broken), the runner falls back to the
          # teach-based flow automatically.
          # v3.43.26: "qbittorrent" added. Routes torrents/magnets
          # through a local qB instance. NOTE: torrent/magnet URLs
          # auto-route to qB regardless of this setting (a magnet
          # can't be HTTP-downloaded), so set this to "qbittorrent"
          # only when the site primarily serves torrent URLs and you
          # want all of them to take that path.
          "backend": "teach",
          "jd_host": "127.0.0.1",
          "jd_port": 3128,
          # v3.43.26: qBittorrent connection. Defaults match a stock
          # local install with Web UI on default port; user fills in
          # creds in the edit modal.
          "qb_host": "127.0.0.1",
          "qb_port": 8080,
          "qb_username": "admin",
          "qb_password": "",
          # v3.43.28: deep Stash integration toggles. Default OFF so
          # existing sites with just the basic v3.20 scan trigger
          # don't suddenly start hitting GraphQL on every URL. User
          # opts in per-site via the edit modal.
          "stash_deep_enabled": False,
          "stash_dedup_check": False,
          "stash_scrape_preview": False,
          "stash_studio_id": "",
          "stash_auto_studio": False,
          "stash_tags": "",
          # v3.43.29: deep Plex integration toggles. Same pattern as
          # Stash — default OFF, opt-in per-site.
          "plex_deep_enabled": False,
          "plex_match_confirm": False,
          "plex_recently_added_boost": False,
          "plex_collection": "",
          "plex_auto_create_collection": False,
          # v3.43.30: watch folder for .txt URL imports. Default
          # disabled; user opts in per-site with a folder path.
          "watch_enabled": False,
          "watch_folder": "",
          "watch_poll_seconds": 30,
          "watch_url_priority": "normal",
          # v3.43.33: AI-assisted login form detection. Default OFF —
          # opt-in per-site. Requires global AI assist to also be
          # enabled in Settings, AND the configured endpoint to be
          # reachable.
          "ai_login_assist_enabled": False,
          # v3.43.37: deep Jellyfin integration. Same pattern as Stash
          # and Plex deep — default OFF, opt-in per-site.
          "jellyfin_deep_enabled": False,
          "jellyfin_user_id": "",
          "jellyfin_match_confirm": False,
          "jellyfin_collection": "",
          "jellyfin_auto_create_collection": False,
          # v3.43.35: per-site account-pool cooldown duration in
          # seconds. 0 = use the module default (5 min).
          "account_cooldown_seconds": 0,
          # v3.43.37: smart retry classification on by default. Sites
          # benefit immediately without any config change. Turn off
          # explicitly to revert to the legacy uniform schedule.
          "auto_retry_classify": True,
          # v3.43.41: per-site download windows. Default OFF — opt-in
          # per-site. window_active_hours empty == no restriction
          # even when enabled (defensive).
          "window_enabled": False,
          "window_active_hours": "",
          "window_action_outside": "paused",
          # v3.43.42: storage tier defaults. Default OFF — opt-in
          # per-site. Empty dir = no migration. Mode 'move' is the
          # standard, 'dry_run' for testing the policy.
          "storage_tier_enabled": False,
          "storage_tier_dir": "",
          "storage_tier_age_days": 30,
          "storage_tier_min_size_mb": 0,
          "storage_tier_mode": "move",
          # v3.43.44: auto-managed; populated as downloads succeed
          "url_fingerprint": {},
          # v3.43.64: MP4 metadata defaults. Embedding is ON by default
          # — Matt's preference for "show me more, not less" — but every
          # piece is per-site toggleable. Cover-art fetching is also ON
          # but the timeout is short enough (10s) to not block downloads
          # if a thumbnail CDN is slow.
          "embed_metadata": True,
          "embed_cover_art": True,
          "metadata_genre": "",
          "metadata_description": "",
          "metadata_cover_timeout_s": 10.0,
          # v3.43.65: tier-probe defaults. All OFF by default — opt-in
          # per site. Probing is fail-open so leaving the pattern empty
          # is a guaranteed no-op even when tier_probe_enabled is True.
          "tier_probe_enabled": False,
          "tier_probe_pattern": "",
          "tier_probe_ladder": "",  # empty → uses DEFAULT_LADDER
          "tier_probe_timeout_s": 5.0,
          "tier_probe_max_attempts": 6,
          # v3.43.65: empty dict means "no pre-scrape action". When
          # populated, must have at least a "selector" key.
          "pre_scrape_action": {},
          # v3.43.66: Aylo network flashvars extractor. Default True
          # — even if the user's site isn't an Aylo brand the gate
          # is a cheap hostname check, so leaving this on is free.
          # When the URL IS an Aylo brand, this short-circuits the
          # find_best_download scoring path with the variant the
          # flashvars block declares is best.
          "use_aylo_extractor": True,
          "aylo_force_format": "",  # "" = auto, "mp4" or "hls" to force
          "aylo_force_mp4": False,  # legacy alias
          "aylo_premium_only": False,
          # v3.43.67: Vixen extractor. Default True for the brand-
          # matched hosts; for non-matched hosts the gate is a cheap
          # hostname check so leaving on is free.
          "use_vixen_extractor": True,
          # v3.43.68: HereSphere/DeoVR JSON API. Off by default —
          # only enable per-site after the probe confirms support.
          "use_jsonapi": False,
          "jsonapi_url": "",
          "jsonapi_protocol": "",  # "" = auto-detect from response shape
          "jsonapi_id_regex": "",
          "jsonapi_prefer_codec": "",  # "" = prefer h265 at equal tier
          "jsonapi_timeout_s": 15.0,
          # v3.43.69: dl8-video VR. Default True — same cheap hostname
          # gate as Aylo/Vixen, free to leave on.
          "use_dl8_extractor": True,
          # Badoink prediction defaults True too. On non-Badoink dl8
          # hosts (TmwVRnet) the flag is ignored — the standard dl8
          # path always runs.
          "dl8_predict_badoink_filenames": True,
          # v3.43.70: apprise notifications. OFF by default — needs
          # explicit opt-in + at least one URL. The dispatcher itself
          # is constructed at startup but stays disabled until the
          # user adds URLs and flips the toggle.
          "notify_apprise_enabled": False,
          "notify_apprise_urls": "",  # multi-line text
          # Per-event policy. "" means "use DEFAULT_POLICY from
          # notify_apprise module". Per-event values stored as
          # strings so the JSON serializer doesn't care.
          "notify_download_done_mode": "",      # default: batched
          "notify_download_done_batch": "",     # default: 10
          "notify_download_done_wait_s": "",    # default: 120
          "notify_download_failed_mode": "",    # default: batched
          "notify_download_failed_batch": "",   # default: 5
          "notify_download_failed_wait_s": "",  # default: 60
          "notify_captcha_mode": "",            # default: per_event
          "notify_auth_required_mode": "",      # default: per_event
          "notify_disk_full_mode": "",          # default: per_event
          "notify_queue_empty_mode": "",        # default: per_event
          "notify_queue_paused_mode": "",       # default: per_event
          "notify_queue_resumed_mode": "",      # default: per_event
          "notify_server_start_mode": "",       # default: per_event
          "notify_server_shutdown_mode": "",    # default: per_event
          # v3.43.72: perceptual dedup. On by default — the worker
          # spawns a daemon thread per completed download to compute
          # the pHash. Fail-open: missing videohash / ffmpeg / corrupt
          # file all silently become no-ops, so on-by-default is safe.
          "dedup_hash_on_done": True,
          "dedup_distance": 4,         # bits; 0=identical, 4=same scene
          "dedup_db_path": "video_hashes.db",
          "dedup_policy": "keep_all",  # keep_all / keep_largest / keep_first
          # v3.43.73: Scrapling. Both opt-in (default False). Recovery
          # is the safer of the two — pure read; no external request.
          # Turnstile bypass launches a separate Chromium via Scrapling's
          # StealthyFetcher, so it costs CPU + ~10s per challenge.
          "use_scrapling_recovery": False,
          "use_scrapling_turnstile": False,
          "scrapling_recovery_min_score": 0.6,
          # v3.43.74: FlareSolverr + multi-connection. Both default OFF
          # — opt-in per site. The endpoint default points at the
          # Docker default port for FlareSolverr; users running it on
          # a different host or port override here.
          "use_flaresolverr": False,
          "flaresolverr_endpoint": "http://localhost:8191/v1",
          "flaresolverr_timeout_s": 60.0,
          "flaresolverr_max_timeout_ms": 60000,
          # Multi-conn: 4 parallel byte-range connections, min file
          # size 100MB to activate. Servers that don't support Range
          # auto-fall-through to single-conn.
          "use_multi_conn": False,
          "multi_conn_count": 4,
          "multi_conn_min_size_mb": 100,
          "multi_conn_timeout_s": 30.0,
          # v3.43.75: playlist fan-out — default OFF per site (opt-in).
          # When ON and the user adds a listing URL, BD navigates,
          # extracts scene URLs, and queues them all. max_pages
          # controls pagination walking (1 = first page only).
          "use_playlist_extractor": False,
          "playlist_max_pages": 1,
          # ROW 374: GUI discovery defaults.  Zero newest_n is the explicit
          # whole-library mode; 50 is the intentionally cheap default.
          "crawler_listing_url": "",
          "crawler_newest_n": 50,
          "crawler_max_pages": 5,
          "crawler_max_scrolls": 8,
          "crawler_delay_s": 1.0,
          "crawler_title_fetch_limit": 50,
          # v3.43.75: yt-dlp download_archive interop. Default OFF;
          # opt-in per site with the path to the user's archive.
          "use_ytdlp_archive": False,
          "ytdlp_archive_path": "",
          # v3.43.75: MutationObserver lazy-player wait. Default OFF;
          # enable for sites that lazy-load their <video>.
          "use_mutation_observer": False,
          "mutation_observer_timeout_ms": 15000,
          # v3.43.76: PhoenixAdult catalog + smart routing. Both ON
          # by default — the catalog is offline so this is essentially
          # free, and smart-routing suggestions are non-destructive
          # (only show up in the UI when a NEW unknown URL matches a
          # catalog entry).
          "use_phoenix_catalog": True,
          "use_smart_routing": True,
          # v3.43.76: bandwidth supervisor. OFF by default — most
          # users don't want bandwidth caps. When opted in, set
          # supervisor_global_bps + supervisor_per_site_bps to the
          # desired rates in bytes-per-second (0 = unlimited).
          "use_download_supervisor": False,
          "supervisor_global_bps": 0,           # 0 = unlimited
          "supervisor_per_site_bps": 0,         # 0 = unlimited
          # v3.43.76: thumbnail generation. OFF by default — opt in
          # per site. Defaults to single-frame mode in sidecar dir.
          "use_thumbnails": False,
          "thumbnail_mode": "single",           # "single" or "sheet"
          "thumbnail_dir_mode": "sidecar",      # "sidecar" or "parallel"
          "thumbnail_sheet_rows": 3,
          "thumbnail_sheet_cols": 3,
          # v3.43.77: Search-and-add by query. Off by default — opt
          # in per site. Templates can set `search_url_pattern` for
          # well-known sites without per-deployment config.
          "use_search_extractor": False,
          "search_url_pattern": "",       # e.g. https://x.com/?s={query}
          "search_result_selector": "",   # optional CSS selector
          # v3.43.81 Phase 161: v3.43.80 module-config defaults. All
          # off; existing installs see no behaviour change until they
          # toggle a flag in the UI.
          "use_cluster_rate": False,
          "cluster_rate_max_concurrent": 4,
          "cluster_rate_lease_seconds": 600,
          "use_tpdb": False,
          "tpdb_api_key": "",
          "use_subtitles": False,
          "subtitle_languages": ["en"],
          "use_priority_scoring": False,
          }
_app_cfg = {
    "global_max_concurrent": 0,  # 0 = uncapped
    "global_daily_byte_budget": 0,  # Cut 8: cross-site daily byte cap; 0 = uncapped
    # v3.43.31: per-domain rate limiting. Both 0 = disabled (no
    # throttling); user enables via Settings → Rate limiting.
    "rate_limit_global_concurrent": 0,
    "rate_limit_global_per_sec": 0.0,
    "rate_limit_domain_overrides": {},
    # v3.43.43: AI provider selection + cloud-provider API key.
    # Defaults preserve the historic Ollama behavior.
    "ai_provider": "ollama",
    "ai_endpoint": "http://localhost:11434",
    "ai_model_vision": "qwen2.5vl:7b",
    "ai_model_text": "qwen2.5:7b",
    "ai_api_key": "",
    "ai_enabled": False,
}
# P6-8: frozen snapshot of the SHIPPED global-config defaults, captured BEFORE
# _load_app_config() mutates _app_cfg in place. Served read-only at
# /api/global_config/defaults so the Settings page can badge any value the
# operator has changed from its default. dict() copy = immune to later updates.
_APP_CFG_DEFAULTS = dict(_app_cfg)
__all__ = [
    "SESSION_IDLE_TTL",
    "PAIRING_TTL",
    "RATE_LIMIT_WINDOW",
    "CFG_FIELDS",
    "DEFAULTS",
    "_app_cfg",
    "_APP_CFG_DEFAULTS",
]
