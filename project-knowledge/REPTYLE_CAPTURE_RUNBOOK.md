<!-- verified-against: v3.66.185 (filename de-versioned; internal capture-seam version refs NOT re-reviewed — confirm before relying) -->
# Reptyle template — capture → promote runbook (v3.66.158)

Goal of item #1: exercise the **whole new pipeline on the live reptyle site**
(noVNC capture → build → normalize → review → promote), validate it produces a
working template, and catch any site drift since the gold template was made.
Everything below runs **on stash**. The chain CLIs are stdlib-only, so plain
`python3` is fine (no venv needed for build/normalize/promote).

The gold template already has the proven `api{base}` block and the modal
`row_selectors`. So the fresh capture supplies the **selectors / patterns /
resolutions**, and you merge the **proven api + row_selectors** back in from the
gold backup. `promote --enable` writes to
`templates/reviewed/app.reptyle.com.template.json` — i.e. **it overwrites the
gold** — so we promote to a *staging* dir first, diff, then swap.

---

## 0. Pre-flight

```bash
cd ~/BulkDownloader

# Apply the release (full tree, or the 153–157 rollup). Full tree overwrites
# tools/cockpit_console.py + ENDPOINT_CATALOG.md — exclude them if you have
# other live cockpit edits:
unzip -o /path/BulkDownloader_v3_66_158.zip            # or: ...153-157_rollup.zip
#   (add  -x tools/cockpit_console.py ENDPOINT_CATALOG.md  if you have live cockpit edits)
sudo systemctl restart bulkdownloader.service
curl -s localhost:5555/api/health -o /dev/null -w 'health %{http_code}\n'   # expect 200

# BACK UP THE GOLD — do not skip; promote will overwrite this path.
cp templates/reviewed/app.reptyle.com.template.json \
   templates/reviewed/app.reptyle.com.template.json.bak
ls -l templates/reviewed/app.reptyle.com.template.json.bak
```

## 1. DOM capture via noVNC (the part that needs stash)

In the cockpit → **Captures** → Start capture:

- **Label:** use a UNIQUE one, e.g. `reptyle_$(date +%s)` (a repeat label triggers
  the URL-memory hijack and sends you to the oauth login instead of your URL).
- **URL:** the movie page, e.g. `https://app.reptyle.com/movies/32088`
- `--autofill` if your profile has saved creds.

Open that task's **noVNC** pane — the browser now stays open (v3.66.153). In the
pane: **log in → press play → open the quality/download menu → open the download
modal** so the modal DOM and the `download-resolution` API call are captured.

When done, find the task's out dir in the task log (it prints the exact command)
and from a **second SSH shell**:

```bash
# the log prints this exact path; out_dir is cockpit_tasks/t_<id>/out
touch <out_dir>/FINISH      # save   (touch <out_dir>/CANCEL to discard)
```

Capture saves to `<out_dir>/<label>.wacz`. Set:

```bash
WACZ=<out_dir>/<label>.wacz
ls -l "$WACZ"
```

> **Cockpit one-click (v3.66.158):** instead of steps 2–3 below, you can click
> **build template** on the finished capture task in the Captures tab. It runs
> build + normalize in-process and the candidate appears in the **Template
> Review Workbench** with its status and review notes. Then resume at step 4
> (merge api + row_selectors) and step 5 (promote to staging). The CLI steps
> below are the equivalent manual path.

## 2. Build the rich draft

```bash
python3 tools/build_template_from_wacz.py "$WACZ"
# writes templates/drafts/app.reptyle.com.template-draft.json
DRAFT=templates/drafts/app.reptyle.com.template-draft.json

# sanity: full ladder + the observed API host should be present now
python3 - <<'PY'
import json,glob
d=json.load(open(sorted(glob.glob("templates/drafts/*.template-draft.json"))[-1]))
nd=d.get("network_discovery",{})
print("resolutions_seen:", nd.get("resolutions_seen"))
print("observed_api_hosts:", nd.get("observed_api_hosts"))
print("selectors present:", list((d.get("selectors") or {}).keys()))
PY
```

If `resolutions_seen` is short (e.g. only one rung), the master manifest body
wasn't captured — replay the capture and make sure the player actually starts
streaming so the `.m3u8`/`.mpd` master is fetched.

## 3. Normalize → review candidate

```bash
python3 tools/normalize_template_draft.py "$DRAFT"
# writes templates/review_candidates/app.reptyle.com.candidate.json
CAND=templates/review_candidates/app.reptyle.com.candidate.json

python3 - <<PY
import json
c=json.load(open("$CAND"))
print("status:", c.get("status"))
print("download.trigger:", (c.get("selectors") or {}).get("download",{}).get("trigger"))
print("resolutions:", c.get("resolutions"))
for w in c.get("warnings",[]): print("warn:", w)
PY
```

The api warning will name the observed host (`api2.reptyle.com`) — that's your
cue for the next step.

## 4. Review — merge the proven api + row_selectors from the gold backup

This reuses your **own proven values** (not guessed): the `api{base}` block and
the 12 modal-scoped `row_selectors` from the gold backup. It leaves the
capture-derived selectors / patterns / resolutions intact.

```bash
python3 - <<PY
import json
cand=json.load(open("$CAND"))
gold=json.load(open("templates/reviewed/app.reptyle.com.template.json.bak"))

cand["api"] = gold["api"]                                   # api2.reptyle.com block
cand["selectors"].setdefault("download",{})["row_selectors"] = \
    gold["selectors"]["download"]["row_selectors"]          # modal-scoped rows
cand["template_logic"] = gold.get("template_logic")         # optional, for parity

json.dump(cand, open("$CAND","w"), indent=2)
print("merged api + row_selectors + template_logic from gold")
PY
```

### Drift check — did the live site change vs gold?

```bash
python3 - <<PY
import json
c=json.load(open("$CAND")); g=json.load(open("templates/reviewed/app.reptyle.com.template.json.bak"))
for grp in ("login","player","quality","download"):
    cs=(c.get("selectors") or {}).get(grp,{}); gs=(g.get("selectors") or {}).get(grp,{})
    for k in set(cs)|set(gs):
        if k=="row_selectors": continue
        if cs.get(k)!=gs.get(k):
            print(f"[{grp}.{k}] DRIFT\n    capture: {cs.get(k)}\n    gold:    {gs.get(k)}")
print("resolutions capture:", c.get("resolutions"), "| gold:", g.get("resolutions"))
PY
```

If a selector drifted, the site changed — eyeball it and keep whichever is
correct (prefer the fresh capture if the site genuinely moved; keep gold's if
the capture missed an element). No drift = the pipeline reproduced the gold.

## 5. Promote to STAGING (never straight over the gold)

```bash
mkdir -p templates/_staged_review
python3 tools/promote_template.py "$CAND" --out-dir templates/_staged_review --enable
STAGED=templates/_staged_review/app.reptyle.com.template.json
ls -l "$STAGED"

# diff the staged template against the gold so you see exactly what changes
diff <(python3 -m json.tool templates/reviewed/app.reptyle.com.template.json.bak) \
     <(python3 -m json.tool "$STAGED") | sed -n '1,80p'
```

If promote refuses, read its message — it gates on BAD_TERMS in
network_patterns/api, blocking selector-lint, and requires
`download.{trigger|row_selectors}` + non-empty `resolutions`. The merge in step 4
supplies api + rows, so a clean capture should pass.

## 6. Swap in + verify

```bash
# only after the diff looks right:
cp "$STAGED" templates/reviewed/app.reptyle.com.template.json
sudo systemctl restart bulkdownloader.service

# runtime loads it?
python3 - <<'PY'
import sys; sys.path.insert(0,".")
from bulk_downloader import template_registry as R, template_assist as A
t=R.find_template_for_url("https://app.reptyle.com/movies/32088")
print("found:", bool(t), "status:", (t or {}).get("status"))
print("api url:", A.build_api_url(t,"download_resolution",movie_id=32088,resolution=1080))
print("resolutions:", A.preferred_resolutions(t))
ld=A.template_to_learned_download(t); print("trigger_selectors:", len(ld.get("trigger_selectors",[])), "row_selectors:", len(ld.get("row_selectors",[])))
PY
```

Then run a real reptyle download through the app to confirm end-to-end. If
anything's wrong, restore instantly:

```bash
cp templates/reviewed/app.reptyle.com.template.json.bak \
   templates/reviewed/app.reptyle.com.template.json
sudo systemctl restart bulkdownloader.service
```

---

### Proven values (reference — already pulled by step 4 from your backup)

`api`:
```json
{
  "base": "https://api2.reptyle.com/api/v1",
  "movie_watch": "/movie/{movie_id}/watch",
  "download_resolution": "/movie/{movie_id}/download-resolution/{resolution}",
  "trailer": "/movie/{movie_id}/trailer"
}
```

`selectors.download.row_selectors` (modal-scoped — `[role="dialog"]` and
`.ant-modal`, download/download-resolution hrefs + `button:has-text("2160|1440|1080|720")`):
12 entries, copied verbatim from the gold template.
