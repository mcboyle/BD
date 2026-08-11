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
