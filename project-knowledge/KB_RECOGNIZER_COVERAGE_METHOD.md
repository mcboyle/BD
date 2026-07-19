<!-- verified-against: v3.66.805 -->
# Recognizer player-family coverage — method (version-agnostic)

**Static project-knowledge doc.** This is the DURABLE *how*. The live baseline (per-family
distinct-domain counts, current deficit) and the currently-sourced target URLs are VOLATILE
and travel in the session pack (`KB_CORPUS_EXPANSION_AI6_AI4_*.md`, `STATE.json`, the
`CORPUS_CAPTURE_*` sheets). Read this for the method; read the pack for where things stand now.
Nothing here is version-specific, so it stays valid no matter how many unrelated sessions pass.

---

## 1. The binding principle
The recognizer (`tools/player_recognition.py`) is accurate per-capture; the open work is
**breadth** of player-family coverage, and it is gated by corpus **volume**, not by the
algorithm.

- **Binding metric = distinct registrable DOMAINS per player family. NOT capture count.**
  Recapturing a domain already in the corpus adds ZERO toward coverage — it teaches the model
  that one site, not the family. (A corpus can have hundreds of captures but only a few dozen
  distinct sites; that ratio is what matters.)
- **Floors:** ≥3 distinct domains/family = minimally trainable (train on 2, hold out 1);
  ≥5 = comfortable.
- **Compute the deficit** as Σ over families that actually appear in real captures of
  `max(0, floor − distinct_domains(family))`. Don't chase the recognizer's full registry —
  cover the families that show up in the wild; the long tail of single-domain families is the
  binding constraint.

## 2. The two coverage gates
- **FR-AI6 (structural embeddings):** the ML primitive is a structural fingerprint per capture
  — DOM-tag histogram + script-host set + class-prefix vector — scored by intra-family vs
  inter-family cosine separation. It only becomes trainable once enough families clear the
  domain floor. Until then, **capturing breadth is the entire job**; building the classifier
  earlier just overfits the one or two saturated families.
- **FR-AI4 (new-family discovery):** needs captures that classify `unknown` because of a REAL
  player the recognizer doesn't know — NOT gate/auth/no-player pages (those are *under-capture*;
  see FR-AI5 drift = capture-scope vs signature). Collect **≥2–3 captures of the SAME**
  unrecognized player before proposing a candidate new family + signature.

## 3. Finding actual distinct domains (every batch)
The hard part is pinning DIFFERENT real domains per player — not the vendor demo (which counts
once). Reliable methods, in order:

1. **Source-code search (most reliable, current-proof).** Search page source on
   **publicwww.com** (or nerdydata) for the player's unique asset string → live domains serving
   it. Durable string table:

   | family | source-search string |
   |---|---|
   | bitmovin | `cdn.bitmovin.com` |
   | shaka | `shaka-player.compiled.js` |
   | jwplayer | `jwpcdn.com` |
   | wistia | `fast.wistia.com/embed` |
   | plyr | `plyr.js` / `plyr__` |
   | flowplayer | `flowplayer.min.js` |
   | mediaelement | `mediaelement-and-player` |
   | theoplayer | `cdn.theoplayer.com` |
   | vidstack | `vidstack` / `<media-player` / `vds-player` |
   | media_chrome | `media-chrome` / `<media-controller` |
   | dplayer | `DPlayer.min.js` |
   | artplayer | `artplayer.js` |
   | playerjs | `playerjs.js` |
   | hlsjs | `hls.min.js` |
   | radiant_media_player | `rmp.min.js` |

2. **Tech-usage lookups.** `aguko.com/tech/<slug>` lists ~10 domains free (and
   `aguko.com/site/<domain>` shows one site's stack); BuiltWith / Wappalyzer / Sumble /
   SimilarTech / WebTechSurvey also list usage (often gated in search snippets).

3. **Vendor customer / case-study pages.** They name brands, **BUT: encoder ≠ player.** Many
   "customers" use only the vendor's *encoder* or an *in-app SDK*, so the public web page may
   serve a different player. Treat these as leads only; verify on capture.

## 4. Pruning captures to the intended family
The recognizer confirms family on capture — read `draft.recognition.player_family` and KEEP
only matches. **Expect ~20–30% misses** (sites swap players; demos differ from the library
default). Per-family tells for sanity-checking — `tools/player_recognition.py` is always the
ground truth; these are the general shapes:

- videojs `vjs-` / `videojs_preferred_res`
- jwplayer `jw-` + `jwpcdn.com` host + `jwplayer.*` storage
- theoplayer `THEOplayer.*` storage + `theo-` + `cdn.theoplayer.com`
- vidstack `<media-player>`/`<media-*>` + `vds-` + storage `vidstack::` **or `vds-player:`** (real runtime prefix: `vds-player:display-bg`/`font-family`/…). **The DEFAULT layout often has NO `<media-player>` element** — dense `vds-` markup + the `vidstack-*.js` bundle is then the tell. A *faint* incidental `vds-` trace is NOT enough on its own (needs the vidstack script OR `vds-player:` storage; e.g. optimizely.com carries ~50 `vds-` with no vidstack lib → native_custom). [refined v3.66.318 — vidstack-over-hls fix]
- media_chrome `<media-controller>` + `<media-*-button>` web components
- plyr `plyr__*` classes
- mediaelement `mejs__*` classes
- flowplayer `fp-*` + storage starting `flowplayer` (incl. `flowplayer/...`, `flowplayerTestStorage`)
- shaka `shaka-player*` script + DASH `.mpd` + EME/Widevine
- bitmovin `bitmovin*` classes + `bitmovinplayer-*` storage
- dplayer `dplayer-*` classes + danmaku UI
- artplayer `art-*` / `art-video-player` classes
- playerjs `pjsdiv` / `playerjs`
- hlsjs `hls.js` script + MSE + `.m3u8`, no brand classes
- radiant_media_player `rmp-*` + `rmp.min.js`

**Drift caveat:** Media Chrome, Plyr, Vidstack, and Mux Player are converging into Video.js v10
(Mux). Those families' signatures will shift over time — recheck their tells against the live
recognizer rather than trusting this list for them.

> **NOT YET IN THE RECOGNIZER — measured v3.66.805.** Four families listed above have
> **no signature** in `tools/player_recognition.py`: `dplayer`, `artplayer`, `playerjs`,
> `radiant_media_player`. Verified under both the family name and the tell string
> (`DPlayer`, `art-video-player`, `pjsdiv`, `rmp-`/`rmp.min`) — all zero occurrences.
> Their rows are RETAINED because they remain valid capture targets and the source-search
> strings in §3 still work; but a capture of one of these will classify `unknown`, and
> that unknown is an FR-AI4 seed (a real player the recognizer doesn't know), NOT a
> capture-scope miss. Do not read their presence in this table as recognizer support.

## 5. Verification loop (when a new batch arrives)
Label the new captures, map each to `(registrable_domain, family)`, then merge against the
volatile baseline in the pack:

```python
import sys, glob, os, json, zipfile, re
sys.path.insert(0, "tools")
import build_template_from_wacz as btw, player_recognition as pr
def load(w):
    z = zipfile.ZipFile(w); n = next(x for x in z.namelist() if x.endswith("capture.json")); return json.loads(z.read(n))
def reg(h):
    h = (h or "").lower().replace("www.", ""); p = h.split("."); return ".".join(p[-2:]) if len(p) >= 2 else h
for w in sorted(glob.glob("NEWDIR/*.wacz")):
    cap = load(w); dom = cap.get("dom_log") or []
    html = "\n".join(e.get("html", "") for e in dom if isinstance(e, dict) and isinstance(e.get("html"), str))
    html = (html + "\n" + btw._nodes_to_html(dom)).strip()
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)', html, re.I)
    ss = cap.get("storage_snapshot") or {}
    sk = list((ss.get("local_storage") or {}).keys()) + list((ss.get("session_storage") or {}).keys())
    rec = pr.detect(html, script_srcs=srcs, network=cap.get("network_log") or [], storage_keys=sk)
    print(os.path.basename(w), reg(cap.get("host")), rec["player_family"], rec.get("confidence"))
```

For each `(domain, family)`: NEW domain for that family → +1; a domain already present (incl. a
vendor demo) → +0; family ≠ intended → prune. Update the pack baseline, recompute the deficit;
a family clears at ≥3 distinct domains. Any `unknown` WITH a real `<video>`/MSE + media network
traffic (not a gate page) is an FR-AI4 seed. Only after several families clear the floor does
the FR-AI6 structural-embedding classifier become worth building on the §2 primitive.

## 6. Durable traps
- **Recaptures add zero** — always capture NEW domains; check the release/merge diff for net-new.
- **A vendor's demo domain counts once** — don't recapture it to "pad" a family.
- **A name collision is not the player** — e.g. `artplayer.org` is the HTML5 video player;
  `artplayer.com` is unrelated digital-signage. Confirm the slug/domain maps to the player.
- **A site's marketing domain may run a different player than its product** (e.g. a player
  vendor's own marketing site embedding a third-party player). Verify, don't assume.
- **encoder ≠ player** in vendor customer lists.
- **A redacted/minimal capture can lose a player's tells and classify `unknown`** even for a
  family the recognizer normally knows — verify against a richer capture; such unknowns can also
  seed FR-AI4.
- **A "frontier task" may already be shipped** — grep `CHANGELOG.md` + source for the relevant
  symbols before re-implementing a prior-session proposal.

## 7. Where the volatile state lives (do not duplicate here)
- Live per-family baseline, current deficit, and freshly-sourced target URLs:
  the session pack (`KB_CORPUS_EXPANSION_AI6_AI4_*.md`, `STATE.json`, `CORPUS_CAPTURE_*` sheets).
- Full capture corpus archive travels in the pack lineage (the consolidated captures+json zip).
  If the pack baseline is ever lost, regenerate it from that archive via the §5 loop.
