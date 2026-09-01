#!/bin/bash
# Turn a QA'd Codex worktree into a proper cut: own worktree off current main,
# the worker's diff applied, the release trio written by ME (workers are
# forbidden to touch it), the row closed in the register with the header
# recomputed by the gate's own function, regen LAST, then verify.
# It does NOT ship -- shipping is a separate, serialized step.
#   usage: bd-integrate-row.sh <row> <version> <slug> <changelog-title>
set -u
ROWS_IN="$1"; VER="$2"; SLUG="$3"; TITLE="$4"
# ROWS_IN may name SEVERAL rows that land as ONE cut. The operator ruled
# 2026-08-25 that same-class rows (183/184: wired-gates asserting over source
# formatting) ship under one safety contract rather than one cut each, because
# the serial integration lane -- not the parallel builds -- is the bottleneck.
ROW="${ROWS_IN%% *}"   # first row names the artifacts and the PR body

R=/home/mboyle/BulkDownloader
CW=/home/mboyle/bd-codex-wt/row$ROW
NW=/home/mboyle/bd-cuts/cut/${VER##*.}-$SLUG
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
say(){ echo "$(date -u +%H:%M:%S) [row $ROW -> v$VER] $*"; }

# THE VERSION IS DERIVED FROM CURRENT MAIN, NOT FROM THE QUEUE PLAN. The plan
# hardcodes 1247..1250 in SPECS order, but cuts fail and retry out of order --
# so a retried cut could be handed a version LOWER than what main already
# carries. Prepending a lower version makes the changelog descend, and
# bd-precut refuses ("top entry is vX, not vY"). Asking main is the only
# number that is true at the moment the cut is built.
git -C "$R" fetch --quiet origin 2>/dev/null
MAIN_VER=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null \
           | sed -n 's/^__version__ = "\(.*\)"/\1/p' | head -1)
if [ -n "$MAIN_VER" ]; then
  NEXT="${MAIN_VER%.*}.$(( ${MAIN_VER##*.} + 1 ))"
  # CLAMP UP ONLY. The re-derive exists so a RETRIED cut cannot be handed a
  # version BELOW main (that makes the changelog descend and bd-precut refuses).
  # It must NOT clamp DOWN: a batch integrate deliberately assigns 1283,1284,1285
  # ahead of main, and forcing every one to main+1 collapsed them onto a single
  # number -- three candidates claimed v1282 on 2026-08-26.
  if [ "${VER##*.}" -gt "${NEXT##*.}" ] 2>/dev/null; then
    say "version $VER is ahead of main+1 ($NEXT) -- honouring the assigned batch number"
  elif [ "$NEXT" != "$VER" ]; then
    say "version re-derived from main: $VER -> $NEXT (main is $MAIN_VER)"
    VER="$NEXT"
    NW=/home/mboyle/bd-cuts/cut/${VER##*.}-$SLUG
  fi
fi

# THE VERSION IS A CLAIM AND MUST BE EXCLUSIVE. Nothing serialised this: on
# 2026-08-26 rows 282, 286 and 289 each derived v3.66.1282 from an unmoved main,
# three candidates claimed one number, and the second ship adopted the first's PR
# body into a MERGED PR (#548). Refuse a number another candidate already holds.
for _d in /home/mboyle/bd-cuts/cut/${VER##*.}-*; do
  [ -d "$_d" ] || continue
  [ "$_d" = "$NW" ] && continue
  say "v$VER ALREADY CLAIMED by $_d -- refusing a second candidate on one version"; exit 13
done
if ! _ls=$(git -C "$R" ls-remote --heads origin "cut/${VER##*.}-*" 2>/dev/null); then
  say "cannot read remote branches -- version claim UNMEASURED, refusing"; exit 13
fi
_taken=$(printf '%s\n' "$_ls" | sed 's#.*refs/heads/##' | grep -v "^cut/${VER##*.}-$SLUG$" | head -1)
[ -z "$_taken" ] || { say "v$VER already claimed on the remote by $_taken -- refusing"; exit 13; }

for r in $ROWS_IN; do
  grep -q 'QA_RC=0' "$CC/row$r.qa.log" 2>/dev/null || { say "row $r QA not green -- refusing"; exit 2; }
done

# 1. the worker's diff, from explicitly listed paths only (never git add -A)
for r in $ROWS_IN; do
  W2=/home/mboyle/bd-codex-wt/row$r
  # MERGE-BASE DIFF, NOT `status` + `--cached`. A worker that COMMITS its work has
  # an almost-empty status, so the old path silently reduced row 294 from 10 files
  # to 4 and dropped a 383-line test plus four mutant specs. `--cached` is also
  # relative to the worktree's HEAD, which is a delta of a delta on a stale base.
  # add -N first: untracked files are invisible to every form of git diff.
  NEW2=$(git -C "$W2" status --porcelain 2>/dev/null | grep '^??' \
         | grep -vE 'venv|node_modules|\.pyc|__pycache__' | sed 's/^?? //')
  [ -n "$NEW2" ] && git -C "$W2" add -N -- $NEW2 2>/dev/null
  BASE2=$(git -C "$W2" merge-base HEAD origin/main 2>/dev/null)
  [ -n "$BASE2" ] || { say "row $r: cannot resolve merge-base -- UNKNOWN, refusing"; exit 3; }
  P2=$(git -C "$W2" diff "$BASE2" --name-only -- . ':(exclude)venv' ':(exclude)frontend/node_modules' \
    ':(exclude)bulk_downloader/__init__.py' ':(exclude)tests/test_settings_center_slice4.py' \
    ':(exclude)CHANGELOG.md' ':(exclude)PIN_INDEX.json' \
    ':(exclude)project-knowledge/STATIC_KB_MANIFEST.json')
  [ -z "$P2" ] && { say "row $r changed nothing against ${BASE2:0:7} -- refusing"; exit 3; }
  git -C "$W2" diff "$BASE2" --binary -- . ':(exclude)venv' ':(exclude)frontend/node_modules' \
    ':(exclude)bulk_downloader/__init__.py' ':(exclude)tests/test_settings_center_slice4.py' \
    ':(exclude)CHANGELOG.md' ':(exclude)PIN_INDEX.json' \
    ':(exclude)project-knowledge/STATIC_KB_MANIFEST.json' > "$A/row$r.worker.patch"
  [ -s "$A/row$r.worker.patch" ] || { say "row $r empty patch -- refusing"; exit 4; }
  say "row $r: $(echo $P2 | wc -w) path(s)"
done

# 2. fresh cut worktree off CURRENT main
git -C "$R" fetch --quiet origin
rm -rf "$NW"; git -C "$R" worktree prune
# An ABORTED earlier attempt leaves the branch behind, and `worktree add -b`
# then dies with "a branch named ... already exists" -- which is how row 242
# failed at 19:03 after its 18:23 run was interrupted mid-queue. Delete the
# stale branch only when it is NOT checked out anywhere and has NOT been pushed,
# so this can never discard someone's real work.
BR="cut/${VER##*.}-$SLUG"
if git -C "$R" show-ref --verify --quiet "refs/heads/$BR"; then
  if git -C "$R" ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1; then
    say "branch $BR exists ON THE REMOTE -- refusing to recreate"; exit 5
  fi
  say "removing stale local branch $BR from an aborted run"
  git -C "$R" branch -D "$BR" >/dev/null 2>&1 || { say "could not delete $BR"; exit 5; }
fi
git -C "$R" worktree add --quiet -b "$BR" "$NW" origin/main || exit 5
ln -sfn "$R/venv" "$NW/venv"; ln -sfn "$R/frontend/node_modules" "$NW/frontend/node_modules" 2>/dev/null
for r in $ROWS_IN; do
  # --3way, NOT a plain apply. Each worker's patch was generated against the main
  # that existed when it was dispatched, and main moves under them as the queue
  # merges. Four rows (121, 176, 27, 175) each append a shard entry to ci.yml and
  # to test_..._939_ci_gate_shards..., so once the first lands, a strict apply of
  # the next fails on context it cannot find. --3way falls back to a blob-level
  # merge using the objects both trees already share, which resolves an additive
  # collision instead of refusing it. A REAL conflict still fails here, loudly.
  # THE REGISTER IS MERGED BY ROW, NEVER BY PATCH. A worker's IMPROVEMENT_BACKLOG
  # edit is written against the main it was dispatched on, and every merge since
  # has rewritten row statuses around it -- so a textual patch conflicts almost
  # by construction (row 241 died exactly here). Strip the register out of the
  # patch and re-apply the worker's OWN row lines by identity afterwards.
  if git -C "$NW" apply --numstat "$A/row$r.worker.patch" 2>/dev/null | grep -q 'IMPROVEMENT_BACKLOG.md'; then
    python3 /home/mboyle/bd-register-merge.py "$NW" "/home/mboyle/bd-codex-wt/row$r" \
      >> "$A/row$r.register.log" 2>&1 || { say "row $r register merge failed"; exit 6; }
    git -C "/home/mboyle/bd-codex-wt/row$r" diff \
      "$(git -C "/home/mboyle/bd-codex-wt/row$r" merge-base HEAD origin/main)" --binary \
      -- . ':(exclude)venv' ':(exclude)frontend/node_modules' \
    ':(exclude)bulk_downloader/__init__.py' ':(exclude)tests/test_settings_center_slice4.py' \
    ':(exclude)CHANGELOG.md' ':(exclude)PIN_INDEX.json' \
    ':(exclude)project-knowledge/STATIC_KB_MANIFEST.json' ':(exclude)project-knowledge/IMPROVEMENT_BACKLOG.md' > "$A/row$r.worker.patch"
    say "row $r: register merged by row; patch reduced to the rest"
    [ -s "$A/row$r.worker.patch" ] || { git -C "$NW" add -A -- project-knowledge; continue; }
  fi
  if ! git -C "$NW" apply --index --3way "$A/row$r.worker.patch" 2>"$A/row$r.apply.err"; then
    # An append-only registry conflict is textual, not semantic -- both entries
    # belong. Resolve those by union and continue; anything else still refuses.
    if python3 /home/mboyle/bd-union-resolve.py "$NW" >>"$A/row$r.apply.err" 2>&1; then
      say "row $r: append-only registry conflict resolved by union"
    else
      say "row $r patch did not apply to current main even 3-way -- refusing"
      tail -4 "$A/row$r.apply.err" | sed 's/^/    /'
      exit 6
    fi
  fi
done
say "worker diff applied onto $(git -C "$R" rev-parse --short origin/main)"

# RE-DERIVE THE GATE-SCOPE PINS FROM THE ASSEMBLED CUT, not from any member row.
# The 1173 gate pins a COUNT and a DIGEST over tests/gate_scope_baseline.txt.
# Every row that classifies a gate deletes one baseline line, so a 5-row cut
# moves both pins five times and no member could have known the total: row 297
# pinned 1251, row 310 pinned 1250, row 292 pinned 1251 -- three rows, three
# different "correct" answers, guaranteed to conflict with each other. Same
# lesson as bd-union-resolve's _collapse_pins: union is right for a LIST and
# wrong for a SCALAR; a scalar has to be recomputed. Non-fatal by design -- a
# tree without these files is not an error, and the gate still judges the result.
python3 /home/mboyle/bd-repin-baseline.py "$NW" 2>&1 | sed 's/^/  /' || true

# 3. the release trio -- MINE, never the worker's
python3 - "$NW" "$VER" "$TITLE" "$ROW" "$CC/row$ROW.txt" <<'PY'
import sys, pathlib, re
nw, ver, title, row, report = sys.argv[1:6]
nw = pathlib.Path(nw)
p = nw/"bulk_downloader/__init__.py"; s = p.read_text(encoding="utf-8")
s2 = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{ver}"', s, count=1)
assert s2 != s, "version not rewritten"; p.write_text(s2, encoding="utf-8")
p = nw/"tests/test_settings_center_slice4.py"; s = p.read_text(encoding="utf-8")
s2 = re.sub(r'assert __version__ == "[^"]+"', f'assert __version__ == "{ver}"', s, count=1)
assert s2 != s, "pin not rewritten"; p.write_text(s2, encoding="utf-8")
c = nw/"CHANGELOG.md"; L = c.read_text(encoding="utf-8").split("\n")
i = next(k for k,l in enumerate(L) if l.startswith("## "))
body = [f"## v{ver} - {title}", ""]
# A ROW NEED NOT HAVE A WORKER REPORT. Row 228 came from a raw patch held from
# an earlier session, so codex-cuts/row228.txt did not exist and the changelog
# builder died on FileNotFoundError -- an integrator that assumes every row was
# produced the same way. Missing report means a thinner entry, not a failure.
_rp = pathlib.Path(report)
_lines = _rp.read_text(encoding="utf-8", errors="replace").split("\n")[-400:] if _rp.is_file() else []
for line in _lines:
    t = line.strip()
    if t.startswith("- ") and 20 < len(t) < 300 and all(ord(ch) < 128 for ch in t):
        body.append(t if len(t) <= 78 else t[:77])
        if len(body) > 14: break
body.append("")
assert len(body) > 3, "no changelog body extracted from the worker report"
L[i:i] = body
c.write_text("\n".join(L), encoding="utf-8")
print(f"  trio written: v{ver}, changelog {len(body)} lines")
PY
[ $? -ne 0 ] && { say "trio write failed"; exit 7; }

# 4. close the row, header recomputed by the gate's own function
for r in $ROWS_IN; do
python3 - "$NW" "$r" "$VER" <<'PY'
import sys, pathlib, re
sys.path.insert(0, str(pathlib.Path(sys.argv[1])/"project-knowledge"))
from build_current_overlay import derive_backlog
nw, row, ver = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
p = nw/"project-knowledge/IMPROVEMENT_BACKLOG.md"; s = p.read_text(encoding="utf-8")
pat = re.compile(rf"^\|\s*{row}\s*\|\s*([^|]+)\|", re.M)
m = pat.search(s)
assert m, f"row {row} not in the register"
# KEEP_OPEN_ROWS lets a cut ship WITHOUT closing its row. Rows 243 and 245 are
# operator-ruled to stay OPEN because only HALF their acceptance ships, and the
# row-174 transfer gate additionally REFUSES if they are closed. Without this the
# integrator closes them on every attempt and prepush rejects the cut -- it cost
# three manual re-opens today.
_keep = {r.strip() for r in __import__("os").environ.get("KEEP_OPEN_ROWS", "").split(",") if r.strip()}
if row in _keep:
    print(f"  row {row} left OPEN by KEEP_OPEN_ROWS (only part of its acceptance ships)")
elif m.group(1).strip().startswith("OPEN"):
    s = s[:m.start(1)] + f" CLOSED @{ver.split('.')[-1]} " + s[m.end(1):]
r,o,d,_ = derive_backlog(s)
s = re.sub(r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=[0-9a-f]{64} -->",
           f"<!-- canonical-task-register schema=1 rows={r} open={o} ids-sha256={d} -->", s, count=1)
p.write_text(s, encoding="utf-8")
r2,o2,d2,_ = derive_backlog(p.read_text(encoding="utf-8"))
assert (r,o,d)==(r2,o2,d2)
print(f"  row {row} closed; rows={r} open={o}")
PY
[ $? -ne 0 ] && { say "register update failed for row $r"; exit 8; }
done

# 5. regen LAST, then commit
cd "$NW" || exit 9
if ! venv/bin/python toolchain/bin/bd-regen-order --work "$NW" > "$A/row$ROW.regen.log" 2>&1; then
  # TWO FROZEN LEDGERS ARE ALLOWED TO MOVE, AND ONLY WHEN THIS CUT MOVED THEM.
  # A cut that wires SPA behaviour changes endpoint REACHABILITY, so the dark
  # count drifts off its pin and regen stops -- v3.66.1247 hit exactly this
  # (dark=97 against a pinned 101, i.e. four endpoints became reachable).
  # bd-regen-order's own --declare-reach / --declare-surface are the sanctioned
  # re-pins. They are used ONLY here, and the before/after numbers are written
  # into the changelog, because a re-pin that does not say what moved is
  # indistinguishable from a wiring regression being frozen in.
  REACH=$(grep -oE 'dark=[0-9]+ but the ledger pins [0-9]+' "$A/row$ROW.regen.log" | head -1)
  if [ -n "$REACH" ]; then
    say "reachability moved: $REACH -- re-pinning with --declare-reach"
    venv/bin/python toolchain/bin/bd-regen-order --work "$NW" --declare-reach >> "$A/row$ROW.regen.log" 2>&1 \
      || { say "--declare-reach failed"; tail -5 "$A/row$ROW.regen.log"; exit 10; }
    python3 - "$NW" "$VER" "$REACH" <<'PYR'
import sys, pathlib
nw, ver, reach = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
c = nw/"CHANGELOG.md"; L = c.read_text(encoding="utf-8").split("\n")
i = next(k for k,l in enumerate(L) if l.startswith(f"## v{ver}"))
j = next((k for k in range(i+1, len(L)) if L[k].startswith("## ")), len(L))
L[j-1:j-1] = [
 f"- ENDPOINT REACHABILITY RE-PINNED, AND THE MOVE IS NAMED: {reach}.",
 "  This cut wires SPA behaviour, so endpoints that the ledger recorded as dark",
 "  are now reached. The ledger is re-frozen with bd-regen-order --declare-reach,",
 "  which is the sanctioned declaration; the count is stated here because a",
 "  silent re-pin cannot be told apart from a wiring regression frozen in."]
c.write_text("\n".join(L), encoding="utf-8")
print("  reachability re-pin declared")
PYR
    venv/bin/python toolchain/bin/bd-regen-order --work "$NW" >> "$A/row$ROW.regen.log" 2>&1 \
      || { say "regen still failing after re-pin"; tail -6 "$A/row$ROW.regen.log"; exit 10; }
  else
    say "regen FAILED"; tail -6 "$A/row$ROW.regen.log"; exit 10
  fi
fi
# FROZEN CI GATE COUNT. A cut that declares a new gate grows the population and
# the exact pin refuses until it is re-derived. 262 hit this at 161->170 and 268
# at 170->171, and NINE more queued cuts each add exactly one gate -- without
# this the lane halts nine more times on a bookkeeping number.
#
# THE PIN'S OWN COMMENT STATES THE ONLY SAFE CASE, and this honours it verbatim:
#   "Do not raise this number to silence a failure whose set is NOT empty;
#    that would be hiding a real gap."
# So this re-pins ONLY when the refusal reports BOTH sets empty -- meaning the
# declared SET is already correct and only the count is stale. A non-empty set is
# a real gap and still fails, loudly.
GATEC=$(env -u BD_INSTALL_DIR venv/bin/python -m pytest \
          tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py -q -p no:randomly 2>&1 \
        | grep -oE 'declared [0-9]+ gates, expected exactly [0-9]+; missing from CI: \[\]; extra in CI: \[\]' | head -1)
if [ -n "$GATEC" ]; then
  HAVE=$(printf '%s' "$GATEC" | grep -oE 'declared [0-9]+' | grep -oE '[0-9]+')
  WANT=$(printf '%s' "$GATEC" | grep -oE 'expected exactly [0-9]+' | grep -oE '[0-9]+')
  say "gate count moved $WANT -> $HAVE with BOTH sets empty -- re-pinning"
  python3 - "$NW" "$VER" "$WANT" "$HAVE" <<'PYG'
import sys, pathlib, re
nw, ver, want, have = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
g = nw/"tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py"
t = g.read_text(encoding="utf-8")
old = f"_EXPECTED_DECLARED_GATE_COUNT = {want}"
assert t.count(old) == 1, f"pin anchor occurs {t.count(old)} times, expected 1"
t = t.replace(old, f"_EXPECTED_DECLARED_GATE_COUNT = {have}", 1)
g.write_text(t, encoding="utf-8")
c = nw/"CHANGELOG.md"; L = c.read_text(encoding="utf-8").split("\n")
i = next(k for k,l in enumerate(L) if l.startswith(f"## v{ver}"))
j = next((k for k in range(i+1, len(L)) if L[k].startswith("## ")), len(L))
L[j-1:j-1] = [
 f"- CI GATE COUNT RE-PINNED, AND THE MOVE IS NAMED: {want} -> {have}.",
 "  This cut declares a new gate, so the declared population grew. The refusal",
 "  reported 'missing from CI: []; extra in CI: []' -- the SET was already",
 "  correct and only the count was stale, which is the case that pin exists to",
 "  allow. A non-empty set is a real gap and is NOT re-pinned here."]
c.write_text("\n".join(L), encoding="utf-8")
print(f"  gate count re-pinned {want} -> {have}, move named in the changelog")
PYG
fi

grep -q "$VER" PIN_INDEX.json || { say "PIN_INDEX does not carry $VER after regen"; exit 11; }

# FROZEN IMPORT-GRAPH BASELINE. A cut that legitimately adds an import edge fails
# test_import_graph_no_new_edges until the frozen baseline is re-derived. Row 121
# hit this: its worker CORRECTLY refused to touch a frozen artifact and said so.
# Re-baselining is the integrator's job and is pre-authorized ONLY when declared
# in the changelog, so the new edges are printed and written into the entry --
# a silent re-baseline is how a real unwanted dependency gets laundered in.
if ! env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest \
     tests/test_import_graph_no_new_edges.py -q >"$A/row$ROW.importgraph.log" 2>&1; then
  NEW=$(grep -oE "'[a-z_.]+' -> '[a-z_.]+'|[a-z_][a-z_.]* -> [a-z_][a-z_.]*" "$A/row$ROW.importgraph.log" | sort -u | head -8)
  if [ -z "$NEW" ]; then say "import-graph gate failed but named no edge -- refusing"; exit 12; fi
  say "import graph: new edge(s) --"; printf '%s\n' "$NEW" | sed 's/^/      /'
  # --declare-edges is the sanctioned entry point (bd-regen-order:222). Do not
  # hand-roll it: it also passes --shrink, because THIS FLAG IS the declaration
  # that the edge change is intended, and the gate otherwise refuses a shrink.
  if venv/bin/python toolchain/bin/bd-regen-order --work "$PWD" --declare-edges >/dev/null 2>&1; then
    say "import-graph baseline re-derived"
  else
    say "no baseline regenerator found -- leaving the gate red for manual handling"; exit 12
  fi
  python3 - "$PWD" "$VER" "$NEW" <<'PYX'
import sys, pathlib
nw, ver, edges = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
c = nw/"CHANGELOG.md"; L = c.read_text(encoding="utf-8").split("\n")
i = next(k for k,l in enumerate(L) if l.startswith(f"## v{ver}"))
j = next((k for k in range(i+1, len(L)) if L[k].startswith("## ")), len(L))
note = ["- FROZEN IMPORT-GRAPH BASELINE RE-DERIVED, AND THE EDGES ARE NAMED HERE"
        " rather than absorbed silently:"]
for e in edges.split("\n"):
    if e.strip(): note.append(f"    {e.strip()[:74]}")
note.append("  The gate is a boundary, so a re-baseline that does not say what moved")
note.append("  is indistinguishable from an unwanted dependency being laundered in.")
L[j-1:j-1] = note
c.write_text("\n".join(L), encoding="utf-8")
print("  import-graph re-baseline declared in the changelog")
PYX
  git add -u -- CHANGELOG.md . 2>/dev/null
fi
git add -- $(git status --porcelain=v1 | grep -v 'node_modules\|venv' | awk '{print $NF}')
git commit -q -m "v$VER $TITLE"
say "candidate $(git rev-parse --short HEAD) tree $(git rev-parse --short HEAD^{tree}) parent $(git rev-parse --short HEAD~1)"
# HAND THE ACTUAL VERSION BACK TO THE CALLER. The queue plans a version, but
# this script may re-derive it from main; without this the queue would then
# verify and push a branch name that does not exist.
printf '%s\n%s\n' "$VER" "$NW" > "$A/.integrated-$ROW"
say "NEXT: bd-verify-cut.sh $NW ${VER##*.}-final, then ship through the merge lane"
