<!-- verified-against: v3.66.805 -->
# DRM / EME DETECTION — charter-review decision (S3.1)

*Durable, version-agnostic. Belongs in the static KB alongside `TESTING_ETHICS_FRAME.md`
and `OPERATOR_POLICY_DECISIONS.md`. Purpose: settle the detect-vs-refuse question for
DRM/EME so a fresh session doesn't re-argue it — and, equally, so no session drifts past
the hard line at the bottom.*

**Status: APPROVED (operator-confirmed). Supersedes the roadmap's "S3.1 DRM/EME DETECTION —
detect->refuse only" parenthetical with "detect + label only."**

---

## The question

The forward roadmap scoped S3.1 as "DRM/EME DETECTION (charter review first — detect->refuse
only)." The operator revised the ask: **detect only, NOT refusal** — surface DRM as an
informational label rather than adding a hard block/abort gate.

## The decision

S3.1 is a **detection / recognizer extension**: detect EME + CDM-DRM and encrypted-manifest
signals, surface them as a **read-only label / inventory** (recognizer report + a read-only
route). **No blocking/refusal gate. No decryption path — now or ever.**

## Why this is inside the charter as it already stands (not a loosening)

The charter already splits this exactly along the detect/circumvent line, in three places:

- **`PROJECT_CHARTER.md` (No access-control bypass):** challenge systems "may be detected and
  logged"; the out-of-scope item is automation "designed to **defeat** DRM."
- **`AUTOMATION_POLICY.md` (floor):** "No DRM / stream-encryption **circumvention**" — BD captures
  the site-provided playback the authenticated session is served; defeating the encryption is a
  separate act, out of scope. The surrounding posture: the floor "never limits the operator's
  legitimate access — it draws the line only at circumventing a control or leaking a secret."
- **`TESTING_ETHICS_FRAME.md` (#4):** recognizer **detection** of player families and manifests,
  "never defeat of encryption/DRM."

So DRM *detection* is already the established posture — the same family as the recognizer
identifying DASH/HLS/player, and challenges being "detected and logged." Nothing new is licensed
by adding a DRM label.

## Why dropping the refusal is correct (and the better call)

A refusal gate would be **paternalistic and redundant**. BD **structurally cannot** circumvent
DRM — the crypto is the barrier, not a BD gate. A "refuse" gate therefore prevents zero
circumvention (there is none to prevent); it only blocks the operator from attempting, which is
exactly the paternalism the automation-policy posture rejects ("none of it limits the operator's
legitimate access"). Point BD at DRM content with no refusal gate and the pipeline simply yields
encrypted, unusable segments — the natural failure. No decryption happens. "Detect only" grants
**no infringing capability**; it swaps "BD blocks you" for "BD labels it and lets you see it won't
produce usable output." Same shape as the charter's challenge handling (detect + log + hand off,
not hard-refuse).

## THE HARD LINE (non-negotiable — the charter's existing floor, restated)

Detection stays a **read-only signal**. It never decrypts, strips, extracts keys, requests a
license, drives a CDM, or otherwise defeats DRM. **S3.1 = detect + label, full stop.** The moment
a decryption/stripping path attaches — Widevine CDM or key extraction, L3 decrypt, a license
proxy, `mp4decrypt`/Bento4-with-keys, a Widevine device — it is the charter's hard floor,
**out of scope permanently**, and removing the refusal gate does **not** license adding one. This
is not a new constraint; it is the line the charter already draws harder than strictly needed, and
it is what keeps "detect only" definitionally inside the charter.

## The distinction that makes detection valuable (bake into the classifier)

Two things are commonly conflated; the detector must separate them, because they are on opposite
sides of the charter line:

- **Downloadable encrypted playback** — HLS `AES-128` / `SAMPLE-AES` with a **fetchable key**
  (`#EXT-X-KEY ... URI=`) the authenticated session is served. This is site-provided playback,
  **in scope**, and yt-dlp already downloads it natively. NOT the DRM category.
- **CDM-DRM** — EME + a Content Decryption Module: Widevine (`com.widevine.alpha`, UUID
  `edef8ba9-79d6-4ace-a3c8-27dcd51d21ed`), PlayReady (`com.microsoft.playready*`,
  `9a04f079-9840-4286-ab92-e65be0885f95`), FairPlay (`com.apple.fps*` /
  `com.apple.streamingkeydelivery`). The **un-circumventable** category.
- (**Clear Key** `org.w3.clearkey` is EME but keys are in the clear — label distinctly; it is not
  a protection to defeat.)

The value of detect-only is telling the operator **which kind** a target is — "downloadable AES you
already hold the key for" vs "CDM-DRM, expect unusable output, BD won't and can't strip it." Genuine
capability-mapping (same spirit as the extractor sites-affected map), zero circumvention.

## Scope of the S3.1 cut (buildable spec)

> **SHIPPED — status added v3.66.805.** This spec is no longer forward-looking.
> `bulk_downloader/drm_detect.py` exists and implements the classifier with all
> four categories (`none`, `downloadable-aes`, `clearkey`, `cdm-drm`); its module
> docstring cites this decision by name. `classify_protection` also appears in
> `deep_detect/manifests.py` and `netlog_classify.py`. The hard line held: the
> module is detection-only and states so. NOT yet present: a dedicated
> `/api/drm/status` route — the label is surfaced through the recognizer path
> rather than its own endpoint, so the read-only-route half of the spec below
> remains the one outstanding piece.

**Signals detected (classification only):**
1. **EME API usage** — a page-context hook records `navigator.requestMediaKeySystemAccess(keySystem)`
   calls and the resolved key-system string. Read-only observation of the page's own API calls
   (injected via the existing Playwright drive path); it never calls a CDM or requests a license.
2. **DASH `<ContentProtection>`** — `schemeIdUri` / system UUID + presence of `<cenc:pssh>` in the MPD.
3. **HLS `#EXT-X-KEY` / `#EXT-X-SESSION-KEY`** — `METHOD` + `KEYFORMAT` + whether `URI=` is a
   fetchable key (-> downloadable-aes) vs a FairPlay/Widevine key-server format (-> cdm-drm).
4. **ISO-BMFF boxes** (when init/segment bytes are already in hand) — `pssh` / `sinf` / `schm` /
   `tenc` / `encv`|`enca` sample entries -> CENC encryption present.

**Output:** a pure classifier `classify_protection(...) -> {system, category ∈
{none, downloadable-aes, clearkey, cdm-drm}, evidence}` in a detection module (candidate:
`bulk_downloader/drm_detect.py`, or folded into the recognizer). Surfaced as a **read-only** label
on the recognizer report + a **read-only route** (candidate `/api/drm/status` or a per-target field).
No blocking gate; read-only GET, so it passes route parity without FE wiring (as 648/649 did).

**Explicitly NOT built:** no CDM, no license request/proxy, no key extraction, no decryption, no
`mp4decrypt`/Bento4-with-keys, no Widevine device/L3. Ever.

**Tests (synthetic fixtures only — per `TESTING_ETHICS_FRAME` #6/#7; registry/adult sites never
touched):** classifier unit tests over synthetic MPD snippets (Widevine/PlayReady ContentProtection),
HLS playlists (`AES-128 URI=` -> downloadable; `KEYFORMAT="com.apple.streamingkeydelivery"` ->
cdm-drm; ClearKey), and key-system strings -> assert `{system, category}`; a synthetic page that
calls `requestMediaKeySystemAccess` against a no-op -> assert the hook records it; the read-only
route registered + shape. No live DRM endpoint is needed or permitted — classification is
byte/string-level, so synthetic fixtures cover the exact code path.

## What this supersedes

The roadmap line "S3.1 DRM/EME DETECTION — detect->refuse only" is replaced by
"S3.1 DRM/EME DETECTION — **detect + label only**, read-only, no decryption." Everything else in
the charter is unchanged; the Bucket-A trio (no circumvention, no redistribution, credential floor)
remains in force and this decision sits squarely inside it.
