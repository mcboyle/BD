<!-- verified-against: v3.66.805 -->
# TESTING-HARNESS ETHICS FRAME — settled, so it isn't relitigated

*Durable, version-agnostic. Read this before building or running any in-sandbox
testing harness (bd-corpus, bd-dltest, bd-runner-nav, bd-opv, bd-novnc, bd-proxy,
bd-vpnlab, or a new one). Its purpose is to record the ethical framework already
worked out with the operator so a fresh session doesn't re-argue settled questions —
and, equally, so no session drifts past the hard lines below.*

---

## What this is (and what it is NOT)

**What it is.** A written record of the ethics governing the *building and running of
in-sandbox test harnesses*: the activity of exercising BD's real detection /
redaction / download / admission code against sanctioned public endpoints and
synthetic fixtures, to prove behavior. The operator's standing instruction was:
"abide by the ethics we discussed" for this work rather than reasoning from the
product-runtime `AUTOMATION_POLICY.md` each time (that policy governs the *shipped
product's* autonomous behavior; harness-building is a different activity with the
same hard floors).

**What it is NOT.** It is **not** a loosening of any safety line, and it is **not** an
authorization to skip fresh judgment on genuinely new situations. On every dimension
that matters it is *at least as strict* as the automation policy, and stricter on two
(no solving service, no adult sites — see below). It settles the *recurring* questions
that kept coming up; it does not pre-authorize anything outside its stated scope.

**Why "bypass" is the wrong word.** Setting the product-runtime policy aside for
harness work removed *friction*, not *guardrails*. The load-bearing prohibitions were
kept verbatim and two were added. The net posture is more conservative, more explicit,
and now auditable in one place.

---

## The hard lines (non-negotiable — identical to or stricter than the automation policy)

1. **Challenges: DETECT → HAND OFF. Never solve.**
   Harnesses may detect a challenge (Turnstile / reCAPTCHA / hCaptcha / ALTCHA) and
   route it to a needs-review/handoff state. They **never** attempt to solve it, and
   never automate past it. This is a *subset* of the automation policy's already-narrow
   posture (which permits "attempt once, then pause and hand to the operator") — the
   harness frame doesn't even attempt; it only detects. *Why:* defeating a bot-check is
   circumventing an access control, out of scope permanently. Detection→handoff proves
   the seam works without ever crossing that line.

2. **Never a captcha/challenge SOLVING SERVICE.** (Added by this frame; explicit.)
   No 2captcha, anti-captcha, or any third-party solving API — excluded entirely, not
   configurable. *Why:* the whole point of the challenge line is human-in-the-loop
   handoff; a solving service is the exact circumvention the line forbids.

3. **Official vendor TEST endpoints only.** Challenge work uses only the vendors' own
   published test pages (Cloudflare Turnstile testing docs, Google reCAPTCHA demo,
   hCaptcha demo, ALTCHA) — never a real site's live challenge. *Why:* test endpoints
   are designed to be hit; a production challenge is someone's live access control.

4. **No DRM / stream-encryption circumvention.** Harnesses exercise recognizer
   *detection* of player families and manifests (is this DASH/HLS, which player), never
   defeat of encryption/DRM. Identical to the automation policy floor. *Why:* you can be
   legitimately logged in and the stream still encrypted — defeating that is a separate
   act, out of scope.

5. **All captured corpus is REDACTED; the redaction gate is exercised, not bypassed.**
   Every capture a harness produces runs through BD's real redaction pipeline
   (`scrub_headers` + `redact_capture`) and the residual scanner; a harness rejects a
   capture that doesn't come out clean. Only **synthetic** fixtures and **verified-clean
   redacted** captures are ever committed or circulated. Raw captures are handled locally,
   reported as kinds/counts, never values (see `CAPTURE_SHARING_POLICY.md`). *Why:* this
   is the automation policy's secret-redaction floor, applied to test artifacts too.

6. **Sources: public, non-adult, purpose-built only.**
   - Player demos (videojs, hls.js, shaka, dash.js, theoplayer, etc.) — built to be
     loaded.
   - Published-credential practice sites (the-internet, practicetestautomation,
     parabank, expandtesting) — **a login is NOT a challenge**; these ship fake
     credentials on fake data for authorized automation practice. Using them is
     "auth-as-authorization," exactly what the policy permits.
   - Public-domain / CC / vendor-official sample media (test-videos.co.uk, Apple bipbop,
     Mux, DASH-IF/Akamai) for download-path tests.
   - The full allowlist is `SANCTIONED_TEST_URLS.md`. Off-allowlist = refused.

7. **Adult registry / tube sites are OFF-LIMITS, always.** (Added by this frame;
   explicit.) Never test them live, never build fixtures that mimic them. *Why:*
   player-family seam detection is site-agnostic — synthetic fixtures cover the exact
   same code path — so there is zero technical need to touch them, and good reason not
   to. This is stricter than the automation policy, which is silent on site category.

8. **SSRF / egress floors hold in the harnesses too.** bd-runner-nav's SSRF negatives
   (127.0.0.1, 169.254.169.254 metadata, 10.x, [::1]) must be *refused* — they're the
   negative test, never fetched. bd-vpnlab proves egress fails closed when the tunnel
   drops. Identical to the automation policy's SSRF + VPN fail-closed floors.

---

## The distinction that resolves most questions

**Access vs. circumvention.** Using access the operator legitimately has — a login, a
paid account, the operator's own VPN, a site's own playback controls — is *using the
access you have*. Fabricating or circumventing the authentication itself, or automating
past a bot-check, is circumvention. Paywall / geo / login are **not** the boundary; the
boundary is the auth control and the challenge. This is the automation policy's central
principle, and it disposes of most "is this OK?" questions: if it's exercising a
legitimately-accessible surface or a purpose-built test endpoint, it's in scope; if its
purpose is to defeat a control, it's out.

**A login is not a challenge.** Practice-login sites with published credentials are
authorized-access practice, not access-control defeat. A captcha/bot-check is an
access control. The frame keeps the first and forbids soloing the second.

---

## What still gets fresh judgment (this doc doesn't waive it)

The frame settles the *recurring* questions above. It does **not** pre-authorize:
- A new **site category** not covered by the allowlist's bar — evaluate it against the
  hard lines before adding.
- Anything touching a **real user's data**, a **real challenge**, a **real VPN tunnel**,
  or a **real device** — those stay operator-gated (they're why F4.1/real-challenge/
  live-tunnel checks remain gated, not sandboxed).
- A **new capability** whose purpose or effect isn't clearly detection/redaction/
  download testing — reason about it freshly.

If a new situation isn't cleanly covered by the hard lines, raise it rather than
stretching the frame to fit.

---

## One-line test
*Is this exercising a legitimately-accessible or purpose-built test surface, with
detection-not-defeat, redaction-on, no solving service, no adult site?* If yes, it's in
scope and settled. If it's trying to defeat a control, or it's a genuinely new category,
it's out or it's a fresh question — don't relitigate the settled part, don't wave
through the new part.
