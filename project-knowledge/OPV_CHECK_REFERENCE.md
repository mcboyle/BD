<!-- verified-against: v3.66.805 -->
# OPV check reference — what each bd-opv check proves

*Generated from the live bd-opv registry (the source of truth). Runner: `bd-opv`;
`bd-opv --only OPV-X` for one, `bd-opv --list` for the registry. Live PASS/GATED
counts are in STATE `opv_session`, never hard-coded here. Most browser/netns/notify
checks need `bd-sbcap` provisioned first.*

**24 sandbox** + **1 gated** checks registered.

| Check | Class | What it proves |
|---|---|---|
| **OPV-HEALTH** | sandbox | /api/health responds and reports a version. The smoke gate every deploy confirms -- now confirmable in-sandbox. |
| **OPV-METRICS** | sandbox | BD's /metrics endpoint serves valid Prometheus exposition format. Boots the app, GETs /metrics (documented-unauthenticated), and parses the body with the OFFICIAL prometheus_c… |
| **OPV-BASE** | sandbox | OPV-BASE: baselines_snapshot writes a valid JSON baseline (CLI). Needs a populated DB; in-sandbox with no live DB this SKIPs (precondition), it does not FAIL. |
| **OPV-SOAK** | sandbox | a bounded in-process soak proves the app has no pathological per-request memory growth under sustained load, and freezegun time-warp drives the admission-window clock. Fires N… |
| **OPV-F4.3** | sandbox | OPV-F4.3: enqueue is denied with required_scope=admin, while an admin token can issue exactly one child token. Fully API -- test client only. |
| **OPV-CSRF** | sandbox | a state-changing API call without a CSRF token is refused; with one it is accepted. Confirms the global CSRF guard. API-only. |
| **OPV-FLOOR** | sandbox | the capture redaction FLOOR holds and the secrets-store status surface is value-free. scan_floor_secrets returns [] for a clean capture, FLAGS a raw JWT credential, and -- the… |
| **OPV-VPNKILL** | sandbox | the VPN egress-fails-closed behavior, proven end-to-end. TWO layers: (A) the real vpn_kill_switch state machine -- fresh kill sets killed, callback fires exactly once on idemp… |
| **OPV-NOTIFY** | sandbox | the real apprise notify pipeline delivers a TAGGED event end-to-end to a local SMTP sink. Stands up an aiosmtpd sink, drives BD's real notify_apprise.send([mailto://...], tag=… |
| **OPV-PUSH** | sandbox | the web-push (VAPID) pipeline works end-to-end in-sandbox against the real push.py -- VAPID keypair generates (base64url pubkey), a JS-shaped PushSubscription stores + lists, … |
| **OPV-QR** | sandbox | the mobile-pairing QR is decodable end-to-end. Encodes a pairing URL with the SAME qrcode lib + params app_pair uses, rasters it, and decodes it back with pyzbar (real libzbar… |
| **OPV-RECOG** | sandbox | the real recognizer fires against a live public player demo, headless, in-sandbox — proving the corpus-pull path (bd-corpus) works end-to-end. Picks a demo that renders its se… |
| **OPV-FIXTURE** | sandbox | a SYNTHETIC fixture site exercises the full download + challenge-detect->handoff flow against BD's real captcha_relay code. Serves practice-listing pages + four challenge page… |
| **OPV-F1.1** | sandbox | the download-window retry gate snaps a retry that would fire while the site's window is CLOSED forward to the next window-open, and leaves an in-window retry (or a window-disa… |
| **OPV-F1.3** | sandbox | the cookie-expiry admission gate holds an opt-in site with an all-expired dated jar (reason 'cookies_expired') and admits when a dated cookie is still live or only session coo… |
| **OPV-F1.4** | sandbox | the predictive-relogin decision fires proactively at ~fraction*median(learned session lifetimes) -- BEFORE the session would expire -- and returns None ('no opinion', caller f… |
| **OPV-F2.6** | sandbox | the DOM-Analyzer workbench evaluates operator selectors against a (redacted) DOM and pins a REVIEW-ONLY draft. Drives the real selector_playground.evaluate_selectors + dom_ana… |
| **OPV-F3.2** | sandbox | the drift->AI-repair sweep is inert when AI is unavailable, and when AI proposes a replacement selector it lands a REVIEW-ONLY draft (status='draft_review_required', never ena… |
| **OPV-F3.3** | sandbox | the template canary replays synthetic HAR fixtures HTTP-FREE (real synthetic_tests.run_all with an injected fixtures root -- the module is documented as making no HTTP request… |
| **OPV-B2** | sandbox | the draft-test override's load-bearing SAFETY invariants hold. I1 -- a normal run (no override) never leaks draft hints. I2 -- the enabled-only matcher (find_template_for_url … |
| **OPV-A11Y** | sandbox | the real cockpit renders under a HEADED chromium on Xvfb :99 and axe-core runs a WCAG 2.1 A/AA scan against it. PASS = the app served + the page rendered + axe completed (the … |
| **OPV-OCR** | sandbox | the cockpit visually renders to real pixels. Loads /cockpit/reports in a HEADED chromium, screenshots it, and runs tesseract OCR over the image -- if it reads back a body of r… |
| **OPV-RENDER** | sandbox | the cockpit-shell computed-layout squish-regression gate (render_check.py, the @347/348 lesson). Serves the real cockpit_console blueprint, drives headless Chromium, and measu… |
| **OPV-PICK** | sandbox | a real element-pick click derives a stable selector. Injects dom_overlay.picker_script() into a synthetic headless page, fires a click, and asserts inspect_pick.build_selector… |
| **OPV-F4.1** | gated |  |
