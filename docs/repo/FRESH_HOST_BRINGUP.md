# Fresh Linux host bring-up — bare Ubuntu 24.04 to a green `./capture.sh`

<!-- verified-against: v3.66.1024 -->

**OPERATOR-facing, not an agent contract.** `CLAUDE.md` is the only agent-facing
contract and this does not compete with it (see its section 8). This is a
runbook for standing up a second deploy host beside `test4`.

**Scope.** A physical or virtual Linux box that will run the service and
`./capture.sh`. Not the Claude Code cloud container — that is
`docs/repo/ENVIRONMENT_PROVISIONING.md`, a different machine with different
constraints.

---

## What `scripts/provision_test_host.sh` does and does not do

`CLAUDE.md` calls it "the one command that takes a fresh Ubuntu 24.04 box to a
green `./capture.sh`". That is accurate **for the provisioning surface**, and it
is worth being precise about the three things it does not cover, because a
transition is provisioning **plus service plus data** and it only does the first.

| | who does it |
| --- | --- |
| clone the repo | **nobody** — the script ships inside the checkout and never clones |
| install the systemd service | **`capture.sh` does**, at its step [4], via `install_service.sh` |
| migrate operator state | **nobody** — see the migration list below |
| install the AI backend | **nobody** — `install_ai_ollama.sh` is a separate step; see [3b] below. Required only when the migrated `app_config.json` carries `ai_enabled: true`, and then it IS required: L17 hard-fails the capture without it |

Since v3.66.1028 it DOES install `requirements-test.txt` (graded steps
[4c]/[4d]) — the capture's own gates hard-require it, and the first fresh host
failed its first capture on exactly that gap while the old box passed only via
hand-installed packages.

Consequence worth expecting: after provisioning and before the first capture,
**nothing is listening on :5555**. That is correct, not a fault. The first
`./capture.sh` installs and starts the service.

The script itself is idempotent, is deliberately not `set -e` (failed steps are
recorded and the run continues; the verdict at the end is the gate), and grades
each row OK / WARN / FAIL / UNKNOWN — where **UNKNOWN blocks**. Proceed only on
`VERDICT: READY`, and read every WARN row: a WARN is a capability the host does
not have.

## Preconditions it does not create

- **A checkout at `~/BulkDownloader`.** The provisioner works at any path, but
  `capture.sh` defaults `BD_HOME` to `$HOME/BulkDownloader` and `cd`s there. A
  checkout elsewhere provisions fine and then captures a different tree, or
  fatals. Put it at the default path or export `BD_HOME`.
- **Root or sudo.** Neither → fatal, exit 2.
- **Ubuntu 24.04 package names specifically.** `libgtk-3-0t64` is a t64 name and
  `python3.12` is not in 22.04's default archive. On an older release the
  affected group's apt transaction fails into a FAIL/WARN row — loudly, not
  silently. *Unverified: 22.04 and Debian behaviour is derived from package
  names, not measured.*
- **`curl`.** An unlisted assumption: it is in no `bd_system_pkgs` group, yet
  `capture.sh`'s readiness probes and `install_service.sh`'s serving check all
  use it. Present on standard Ubuntu Server, absent on minimal images. Install
  it with `git` in step 1 below.
- **Network egress** to the apt archive, PyPI, the npm registry and the
  Playwright CDN. Every failure reaches the verdict.

## The graph pin

`capture.sh` step [2b] compares a rebuilt source graph against a pin under
`/var/lib/bulkdownloader/validation/` — **outside the repo**, so `git reset
--hard` never delivers it and a fresh box has none. With `BD_REQUIRE_GRAPH_HASH`
unset the MISSING branch prints `UNKNOWN -- optional check not armed` and
**returns 0**: the capture goes green with the graph never checked.

The provisioner arms it (step [8/9]) and then re-runs the gate's own
`--check-hash` **as the invoking user**, because writing a pin proves a write,
not that `capture.sh` can read and match it. After that the gate is live, and it
must be **re-pinned after every source change** — `scripts/deploy.sh` does that
automatically at its step [7]; a by-hand deploy does not, and the next capture
goes red on drift.

## Operator state to migrate — the list that loses data if you miss it

Everything below lives in the install root unless noted. **Stop the service
before copying the database**, so the WAL is checkpointed.

| item | path | note |
| --- | --- | --- |
| history database | `downloader_history.db` | copy the `-wal` and `-shm` sidecars under the **same basename** or the tail of history is silently dropped |
| site configs | `sites_config.json` | can embed **absolute** `download_dir` / `cookie_file` paths pointing at the old box — audit after copying |
| global config | `app_config.json` | |
| credential vault | `secrets.json`, `secrets_meta.json` | |
| session cookies | `cookies/`, `*.cookies.json` | |
| learned selectors | `learned/` | **neither tracked nor gitignored** — `git clean -fd` destroys it |
| custom templates | `user_templates.json` | **neither tracked nor gitignored** |
| extension pairings | `vault_tokens.json` | **neither tracked nor gitignored** |
| push keys, recorder state | `state/`, `vapid_keys.json` | `vapid_keys.json` is a private key |
| GUI env editor file | `.env` | carries `BD_PORT`; see the port caveat below |
| macros | `macros/` | |
| browser profiles | `profiles/` | Chromium user-data dirs that can carry live logins |
| plugin registry | `plugins/plugins.registry.json` | the directory itself is tracked; only the registry is state |
| per-user config **outside** the repo | `~/.config/bulk-downloader/` | `vpn/tunnels.json`, `widgets.json` |
| downloaded media | wherever each site's `download_dir` points | read the real values out of `sites_config.json` -- and **create** those directories on the new box; the runbook's first live run found the configured one absent |
| **capture corpus** | `captures/` -- **repo-relative, inside the install root** | the largest item on the list (2.5GB / 924 files on the first migration) and the easiest to strand: the heavy data-layer collectors search REPO-RELATIVE dirs (`captures/`, `offline_out/`, ...), so a corpus parked at `~/captures` is structurally invisible and every capture-analytics route reports an EMPTY store with nothing warning you. Copy with `rsync -a` (mtimes are load-bearing: the collectors bound work to the NEWEST files). It is gitignored, so `git status` says nothing either way |

**THE IN-APP BACKUP DOES NOT CONTAIN YOUR HISTORY DATABASE.**
`bulk_downloader/backup.py`'s `BACKUP_TARGETS` lists `queue.db` and its
sidecars — a filename that is not the live database. The live one is
`downloader_history.db`, and no `downloader_history*` entry appears anywhere in
that file. Migrate the database by hand; do not rely on the backup button for
it.

**Do NOT copy:** `venv/`, `frontend/dist/`, `__pycache__`, `reports/*`,
`tools/deployed_version.txt`, the `/var/lib/` graph pin, `logs/`, or the systemd
unit files — all regenerated on the new box. Do run `systemctl cat
bulkdownloader` on the old box first, to catch any hand-added drop-in.

## The ordered bring-up

```bash
# ── 0. OLD box: quiesce and export ──────────────────────────────────
sudo systemctl stop bulkdownloader bulkdownloader-ai-ready
systemctl cat bulkdownloader                  # note any hand-added drop-in
cd ~/BulkDownloader && tar czf /tmp/bd_state.tar.gz \
    downloader_history.db downloader_history.db-wal downloader_history.db-shm \
    sites_config.json app_config.json secrets.json secrets_meta.json \
    vault_tokens.json user_templates.json vapid_keys.json .env \
    cookies/ learned/ state/ macros/ profiles/ plugins/plugins.registry.json 2>/dev/null
tar czf /tmp/bd_userconfig.tar.gz -C ~ .config/bulk-downloader 2>/dev/null

# ── 1. NEW box: the checkout the provisioner cannot make ────────────
sudo apt-get update && sudo apt-get install -y git curl
git clone <origin-url> ~/BulkDownloader        # THIS path -- capture.sh defaults to it
cd ~/BulkDownloader

# ── 2. the one command ──────────────────────────────────────────────
./scripts/provision_test_host.sh               # proceed only on VERDICT: READY

# ── 3. restore state BEFORE the first capture ───────────────────────
tar xzf /tmp/bd_state.tar.gz -C ~/BulkDownloader
tar xzf /tmp/bd_userconfig.tar.gz -C ~
rsync -a mboyle@<old-box-ip>:~/BulkDownloader/captures/ ~/BulkDownloader/captures/
grep -E 'download_dir|cookie_file' sites_config.json   # audit old-box absolute paths
grep BD_PORT .env 2>/dev/null                          # see the port caveat
mkdir -p "$(python3 -c 'import json;print(json.load(open("sites_config.json"))["sites"][0]["download_dir"])' 2>/dev/null || echo ~/d)"

# ── 3b. the AI backend, IF the migrated config expects one ──────────
# app_config.json ships ai_enabled from the old box. When it is true and
# ollama is absent, live test L17 is a hard capture FAIL (L18/L19 WARN)
# -- measured on the first fresh-host run. ~11GB of model pulls.
grep -q '"ai_enabled": true' app_config.json && ./install_ai_ollama.sh

# ── 4. the gate (also what installs and starts the service) ─────────
export DISPLAY=:99
./capture.sh --workers=$(nproc) > /tmp/capture.log 2>&1; echo "exit=$?"
```

Restoring state **before** the first capture means the service `capture.sh`
installs boots straight onto the real data, and its first-boot migrations run
exactly as an in-place upgrade would.

## Optional: remote-teach, and the :99 seam

`install_remote_teach.sh` (x11vnc + noVNC on :6080 for manual-teach from any
LAN device) is not part of the provisioner and the bring-up works without it.
If you install it, know the seam: the provisioner starts a RAW `Xvfb :99`
process, and remote-teach installs its own `bd-xvfb` systemd unit for the same
display -- the unit FAILS to bind while the raw process lives, while the other
three units (openbox/x11vnc/novnc) happily attach to the raw server. The
installer reports exactly that (`bd-xvfb : failed`). Resolution: kill the raw
Xvfb, `systemctl reset-failed bd-xvfb && systemctl start bd-xvfb`, restart
bd-openbox/bd-x11vnc -- the UNIT is the durable owner (it survives reboot; the
raw process does not). Measured on the first fresh-host install, 2026-08-11.
The installer also restarts `bulkdownloader` to deliver its DISPLAY drop-in.

## Two traps specific to a second host

**The port.** The app derives it from `BD_PORT`, but `capture.sh` **hardcodes**
`localhost:5555` in its probes. If the migrated `.env` sets a non-default
`BD_PORT`, capture's readiness checks fail against 5555 and report the app as
not serving.

**`release_root` in the policy doc.** `project-knowledge/OPERATOR_POLICY_DECISIONS.md`
records the old box's path and `tests/test_graph_source_hash_release_gate.py`
asserts the doc says it. Nothing executes that path — but if you update the doc
to the new box, that test changes in the same cut or CI goes red.

## Reaching the old box from the new one

The migration is a copy old -> new, so the new host needs SSH trust to the old.
Run this ON THE NEW BOX, so bring-up can pull rather than requiring a second
session on the old one:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 2>/dev/null
ssh-copy-id mboyle@<old-box-ip>
ssh mboyle@<old-box-ip> 'echo OK from $(hostname) as $(whoami)'   # prove it
```

The third line is not ceremony: `ssh-copy-id` exiting 0 means it wrote a file,
not that key auth works.

Claude Code on the new host goes in AFTER `provision_test_host.sh`, which
installs the node the CLI needs:

```bash
npm install -g @anthropic-ai/claude-code && claude --version
```

## Run the two boxes in parallel, and why

Do not cut over. Until the new host has produced a green `./capture.sh` against
the real corpus, the old one is the only machine that has -- so it is both the
rollback and, more usefully, the CONTROL.

That second role is worth the disk it costs. v3.66.1024 recorded a session
reporting two test files as "pre-existing order-dependent failures, proven by
changing one variable in the same directory" -- a sound comparison that answered
a question nobody had asked, because the variable that mattered was set on both
sides. With two hosts, "is this the code or the machine?" is a same-day
measurement instead of an argument.

## The standing three-host topology, and the master session

Everything above describes a MIGRATION -- two hosts, transitional, old as
control until the new one is proven. This section describes the arrangement to
settle into afterwards, and it is a different thing: three hosts with fixed
roles, and one Claude session that drives all three.

| host | hostname | machine-id(sha256/12) | role |
| --- | --- | --- | --- |
| `.164` | `test5` | `7b4ea932c297` | **master** -- the driving session, the `main` tree, AND the live service |
| `.85` | `test4` | `102b31c04e7b` | **candidate** -- pre-merge trees, capture lanes |
| `.249` | `test6` | `1d60f39bd8d6` | inhabited -- **no longer the clean host** |
| `.84` | `test7` | `5b29e22f94aa` | **clean** -- carries the bring-up proof, taken 2026-08-12 |

**FOUR hosts since 2026-08-12, and the clean role MOVED.** All four were rebuilt
to one spec that night and re-measured at 48 cores each; the earlier 86/44/64
spread is gone, and with it the reason a cross-host comparison could never
settle anything. `.249` came back from that rebuild INHABITED -- same
machine-id, so the install persisted -- which voided the proof it held and could
not be retaken there. `.84` was the only bare machine left, so the role moved to
it.

`.84` came up named plain `test` and was renamed `test7` the same night: a bare
`test` is a PREFIX of every other label in this fleet, and this file already
records what it cost when two boxes answered to `test4`. The rename is safe
precisely because the identity that matters is the machine-id, which survives it
-- that is the whole reason the digest is quoted and the hostname is not.
`deploy_fleet.sh` matches its label with `=`, not a prefix test, so the old name
was not a live hazard; it was a legibility one.

**The identities are MEASURED, 2026-08-11 at v3.66.1032, by ssh + `sha256sum
/etc/machine-id` from the master.** The first draft of this table carried no
digests and the surrounding prose named `.85` as test5 -- it is test4;
`7b4ea932c297` is the MASTER. A session handed that mapping would drive "test5"
at `.85` and be on the wrong box, which is precisely what "quote the digest,
never the hostname" exists to prevent. The rule protects nothing if the
digest-to-address table is itself wrong, so it is now derived rather than
asserted:

```bash
for ip in 164 85 249; do
  printf '%s  ' "10.0.70.$ip"
  ssh -o BatchMode=yes mboyle@10.0.70.$ip \
      'printf "%-6s " "$(hostname)"; sha256sum /etc/machine-id | cut -c1-12'
done
```

**`.249` was verified genuinely bare** at the same reading: no repo, no venv, no
ffmpeg/ffprobe/yt-dlp/streamlink, no Xvfb, no graph pin, no corpus. `nvidia-smi`
and `node`/`npm` are present from the image. That is the state the bring-up
proof requires, and it is the reason rule 2 exists.

**Why the controller lives on a box rather than in the cloud sandbox.** A cloud
container cannot reach any of them: measured 2026-08-11 from a session at
v3.66.1030 -- no `ssh` binary, no keys, port 22 on a `10.0.70.x` address
unreachable, egress is HTTPS-through-a-proxy to allowlisted hosts only. Nothing
about that is fixable from the sandbox side, because the boxes are behind the
operator's LAN. Moving the controller inside the network solves it outright, and
costs nothing: the master pays its context-orientation once and gets direct
access for it, instead of paying the same and then asking the operator to relay
every result by hand.

### Three rules, in order of what their failure costs

**1. Work in `~/BulkDownloader` on every host -- and on the master that path IS
production.** OPERATOR DECISION, 2026-08-11: one canonical checkout per box, the
same path the deploy uses. An earlier draft of this rule mandated a separate
`~/work/BD`; **that directory was never created on any of the three hosts**, so
the rule's own first command failed for the first session handed it.

The hazard the old rule pointed at is real and does not go away by renaming the
directory. On the master, `systemctl show` gives
`WorkingDirectory=/home/mboyle/BulkDownloader`, `ExecStart` runs that tree's
`venv/bin/python downloader_ui.py`, and `Restart=on-failure` -- so **a restart
serves whatever the tree currently holds.**

What follows is therefore not "do not work there" but: **the tree may sit on
`origin/main`, and must never be LEFT anywhere else.** A tree on `main` is what a
deploy would produce anyway; the exposure is entirely in the TRANSIENT states --
a `git stash`, a detached branch tip, a half-finished `bd-regen-order`. Keep
those short and restore `main` before stepping away.

Measured precedent, v3.66.1031: a session ran five stash/pop cycles, two branch
checkouts, six full pytest runs and four regens in that tree, then a `git pull`
carried it 1030 -> 1032 while the service ran 1030. Nothing broke. Nothing
protected it either, and a single `on-failure` restart in any of those windows
would have served a tree nobody deployed.

**2. Never inhabit the clean host.** `.249` exists to prove that bare Ubuntu ->
green `./capture.sh` runs with zero hand-fixes -- the open item this whole
runbook was written to serve. A venv, a clone, an apt package installed to make
something work, or any residue at all voids the proof it is there to produce.
Drive it over SSH; do not live on it. When the proof is taken, it is taken once
and recorded; the host does not become a general-purpose machine afterwards
without being reimaged first.

**3. Scope the SSH credentials to the job.** The master needs key auth to the
other two. Use a dedicated keypair for it rather than the operator's own, on the
same reasoning as the capture allowlist: the blast radius of an agent holding
unrestricted SSH to two hosts is larger than the task requires. The setup and
the proof-it-works line are in "Reaching the old box from the new one" above --
`ssh-copy-id` exiting 0 means it wrote a file, not that key auth works.

### What the candidate host is actually for

Not redundancy. It is where a tree gets run BEFORE it merges.

The argument is a measurement, not a preference. At v3.66.977 a cut put a live
PyPI call inside the unit suite; every container band was green because the
container's environment hid it, and only a box run surfaced it -- after the
merge. Fixing it cost a complete second cut: RED tests, implementation, a
mutation battery, a 39-file band, a PR and a CI cycle. A candidate run before
the merge would have cost one capture. That asymmetry is the whole case, and it
recurs for any defect whose trigger is environmental rather than logical.

So: candidate first, merge second. The master can drive `.85` on a branch tip
without deploying anything to `.164`.

**Item 48 makes the candidate workflow more urgent, not less.** The obvious
reading is the opposite -- if the suite's failing set rotates, why trust a
candidate run at all? Because a rotating baseline is exactly what makes a
SINGLE post-merge box run useless as evidence: you cannot tell a real regression
from a member of the rotation. Running the tip on `.85` before the merge gives a
second host and a second sample, which is the only way to tell "this cut broke
it" from "this is the rotation". Until 48 has a mechanism, treat any single
green box run -- candidate or master -- as one sample of a distribution rather
than as a verdict.

### What this arrangement does NOT need

A capture-publishing tool. An earlier plan had the boxes pushing sanitised
capture artifacts to a branch so a cloud session could read them by `git fetch`,
and that was the right answer while the controller was in the cloud and blind.
With the master on the LAN it reads captures over SSH directly, so the tool
leaves the critical path. It is still worth building the day a cloud session
needs box visibility -- it is not worth building first.

### Status

**PARTIALLY VERIFIED as of v3.66.1033.** Split it, because the two halves have
very different standing:

MEASURED from the master, 2026-08-11 -- ssh key auth to `.85` and `.249` (exit
0 both), passwordless `sudo -n true` on all three (`(ALL) NOPASSWD: ALL`), the
three identities in the table above, `.249` bare, and the master's own service
running against `~/BulkDownloader`. `.85` carries a duplicate `NOPASSWD: ALL`
sudoers entry -- harmless, but something wrote that rule twice.

STILL A PLAN -- the candidate-before-merge workflow has never been exercised.
The original text below was written by a session that could not reach any host;
the network measurement it rests on is real.

### THE BRING-UP PROOF IS TAKEN, 2026-08-12

**Host `.84`/`test7`, machine-id `5b29e22f94aa`, at `bb37142` (v3.66.1047).**

```
scripts/provision_test_host.sh   VERDICT: READY   311s, 21 rows, ALL OK,
                                 0 WARN, exit 0
./capture.sh --workers=48        CAPTURE VERDICT: PASS, exit 0
                                 unit 15656 pass / 0 fail / 0 error / 26 skip
                                 live    29 pass / 0 fail / 7 warn
```

**Zero hand-fixes, and that is MEASURED rather than claimed:** `git status
--porcelain` returned 0 lines throughout. The entire sequence was `git clone`
then `./scripts/provision_test_host.sh`. **Step 1's `apt-get install -y git
curl` was never run** -- both were already on the image -- so on this base image
the runbook's own first command is optional, and the proof did not use it.

**Run it TWICE. The second run is the record.** The first capture returned the
same PASS with identical counts, but the 2.5GB corpus was rsync'd onto the host
WHILE it ran -- `captures/` went from 0 files to 924 underneath a live run.
Three things were established before re-running, and they are what make the
first result redundant rather than wrong: `capture.sh` references the corpus
directory nowhere; the live checks reference it nowhere; and the only two unit
tests that resolve a real `PROJECT_ROOT / "captures"`
(`test_analyzer_endpoints.py`, `test_pin_host_filename_fallback.py`) STAGE their
own synthetic artifact and address it by exact filename, so corpus size never
enters an assertion. The re-run on a stable tree returned byte-identical counts,
which settles it by measurement rather than by that argument. A proof that needs
an argument about timing is weaker than one that does not.

**All 7 live WARNs are absence-of-operator-state, not capability gaps:**
L5/L6/L8/L9 (no sites, auth-health or cookie files configured), L18/L19 (AI
assist disabled by config), L30 (no VPN tunnels).

**L17 did NOT fail, and this file predicted that it would.** Section [3b] says
L17 hard-fails the capture when `ai_enabled` is true and ollama is absent. But
`app_config.json` is UNTRACKED, so a fresh clone has none, and both readers
(`app.py`, `app_global_config.py`) default `ai_enabled` to False. **Step [3b] is
a MIGRATION step, not a bring-up step:** a bring-up that migrates no operator
state needs no AI backend at all, and the ~11GB of model pulls it warns about
are not on this path.

WHAT THE RUN FOUND, recorded here as this section asks:

- **`streamlink` was installed by nothing.** `live_recorder.py` probes it as the
  PREFERRED backend and falls back to ffmpeg; no package group, manifest or
  installer had ever named it, so the whole fleet silently ran the fallback.
  Fixed at v3.66.1048, with a gate that derives the backend names from
  `_detect_backends` by AST so a third backend cannot fall outside it.
- **The capture corpus does not survive a rebuild** and is not restored by
  `deploy.sh`. `.249` and `.84` both had zero files in `captures/` while `.164`
  and `.85` had 924. Copy it with `rsync -a` -- mtimes are load-bearing.
- **`~/.config/bd/hosts` DID survive the 2026-08-12 rebuild**, contrary to the
  expectation recorded elsewhere that it would not. Verify rather than assume it
  either way.

## Saying which box a reading came from

With two hosts live, a capture is about a **machine** as well as a commit.

**The hostname alone is not an identity, and that is not hypothetical.** The
second box came up named `test4` like the first, so captures from the two were
byte-indistinguishable until it was renamed. v3.66.1025 added a
`--- host identity ---` section to `01_sysinfo.log` carrying the hostname AND a
truncated sha256 of `/etc/machine-id` — stable across reboots, renames and
address changes, and hashed because the bundle is shipped to third parties.

So quote the commit and that digest together when reporting a result, the same
way `CLAUDE.md` section 2b requires a commit for a finding. Two captures either
carry the same digest or they do not; a matching hostname proves nothing. Nothing in the tree branches on a hostname (re-derived at v3.66.1024:
the only `gethostname` / `platform.node` hit in tracked sources is
`live_tests/harness.py`, interpolated into a report string), so the two boxes
differ only in their state and their environment — which is exactly what makes
the old one a useful control while the new one is being proven.
