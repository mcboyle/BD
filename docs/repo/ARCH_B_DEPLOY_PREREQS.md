# Arch B (remote_vnc / KasmVNC captcha takeover) -- stash deploy prerequisites

<!-- verified-against: v3.66.807 -->

Everything stash needs to run the `remote_vnc` takeover path. Split into what is
**required to start Arch B at all** vs the **optional C-7 egress hardening**.
The BD release zip does NOT bundle any of the system packages below -- they are
separate installs.

---

## 1. BD application (you already have this if 807 is deployed)

- **BD v3.66.807** unzipped into `~/BulkDownloader` and the service restarted.
- **Service venv** at `~/BulkDownloader/venv` with the Python deps
  (`requirements.txt`: flask, playwright, ...). cloud-setup.sh builds this.
- **Chromium in the venv** -- the takeover browser:
  ```bash
  ./venv/bin/python -m playwright install --with-deps chromium
  ```
  (cloud-setup runs this; re-run if `No module named 'playwright'` or a
  "browser not found" error appears. Always use `./venv/bin/python`, NOT system
  `python3` -- playwright lives in the venv.)

## 2. KasmVNC display stack (REQUIRED for remote_vnc)

The Xvnc display the takeover browser renders on, plus KasmVNC's own web client
that the cockpit iframe points at. Deps are ordinary X/graphics libs -- **no
kernel module**, so it installs on any host.

**Offline pack (verified this session, no network):**
```bash
unzip pack_kasm.zip -d /opt && sh /opt/kasmpack/install_kasm.sh
```
**or network (stash has normal internet; only my sandbox was repo-scope blocked):**
```bash
curl -sSLf -o /tmp/kasm.deb \
  https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_noble_1.4.0_amd64.deb
sudo apt-get install -y /tmp/kasm.deb
```

What lands: `Xkasmvnc` (the X server, registers as `Xvnc`), `kasmvncserver`,
`kasmvncpasswd`, and `/usr/share/kasmvnc/www` (the web client for C-6).

**Three footguns (all hit + solved this session):**
1. The password file is `$HOME/.kasmpasswd`, NOT `$HOME/.vnc/kasmpasswd`. Create
   the write-user as the invoking OS user first, or the server drops to an
   interactive prompt that EOF-loops under automation:
   `echo -e "PW\nPW\n" | kasmvncpasswd -u $(id -un) -wo $HOME/.kasmpasswd`
2. Touch `$HOME/.vnc/.de-was-selected` or it prompts for a Desktop Environment.
   Do NOT use `-select-de` (it rejects fluxbox).
3. Start it with `-publicIP 127.0.0.1` or it hangs ~60s on WebRTC/STUN then dies:
   ```bash
   kasmvncserver :5 -websocketPort 8444 -disableBasicAuth -sslOnly 0 -publicIP 127.0.0.1
   ```
   Verify: `ss -lnt | grep 8444` and `curl -sI http://127.0.0.1:8444/` -> 200.

*(A lighter stand-in exists -- `apt install x11vnc novnc websockify xvfb` -- and
works for headless checks, but KasmVNC is the real Arch B delivery. Use KasmVNC
on stash.)*

## 3. BD config (turn Arch B on)

Set in the cockpit / settings:

| Key | Value | Note |
| --- | --- | --- |
| `captcha_takeover_enabled` | `true` | the kill-switch -- masters BOTH remote paths (safety-off default) |
| `captcha_takeover_mode` | `remote_vnc` | requested transport |
| `captcha_takeover_max_concurrent` | e.g. `2` | ONE shared cap across cdp + vnc |
| `captcha_vnc_display` | `:5` | must match where kasmvncserver is serving |
| `captcha_vnc_websocket_port` | `8444` | must match `-websocketPort` |
| `novnc_url` | e.g. `https://stash.example:8444/` | **browser-reachable** KasmVNC URL -- the cockpit iframe src; the operator's browser must be able to reach it |

The derived probe (`takeover_vnc.probe_endpoint`) checks the Xvnc process is
alive AND the websocket port answers before it promotes; if either is down,
`remote_vnc` transparently downgrades to `remote` (with a visible reason). So a
mis-set display/port fails safe, not silently.

## 4. C-7 egress containment (OPTIONAL -- only if `netns_isolation: true`)

Only needed if you want the takeover browser's egress confined (wg0 sole route,
fail-closed). Off by default; the takeover works without it.

- `iproute2` (`ip netns`) and `nftables` -- `sudo apt-get install -y iproute2 nftables`
- **`CAP_NET_ADMIN`** and `/dev/net/tun` for the service (netns create).
- For a **live wg0 tunnel**: `wireguard-tools` + the **WireGuard kernel module**
  (`sudo apt-get install -y wireguard`; `modprobe wireguard`). This is the one
  piece absent in my sandbox -- so "egress fails closed" is proven, but the live
  tunnel handshake is stash-only and unverified by me. Confirm on stash:
  `ip link add wgtest type wireguard && ip link del wgtest` (no "Unknown device type").
- Set `netns_isolation: true` in config to arm it.

Without this section, Arch B still runs -- the browser just isn't egress-confined.

## 5. C-8 fingerprint check (diagnostic, not a runtime dep)

Needs only an X display + the venv chromium (section 1). Point it at the display
the takeover uses:
```bash
./venv/bin/python tools/kasm_fingerprint_probe.py --display :5 --json ~/c8.json
```

---

## Minimum to START Arch B

Sections **1 + 2 + 3**. Section 4 is optional hardening; section 5 is a probe.
A headless-no-GPU stash is fine -- the takeover renders in software, which leaks
no GPU (a good fingerprint outcome, per C-8).

---

## Full one-liner: install KasmVNC + start it + run the C-8 measurement

Paste as one block from `~/BulkDownloader`. Idempotent-ish, handles all three
footguns, measures on the REAL KasmVNC display (what the takeover browser uses).

```bash
cd ~/BulkDownloader && \
# 1. install KasmVNC if absent (network .deb; swap for the offline pack if preferred)
{ command -v kasmvncserver >/dev/null || { curl -sSLf -o /tmp/kasm.deb \
    https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_noble_1.4.0_amd64.deb \
    && sudo apt-get install -y /tmp/kasm.deb; }; } && \
# 2. configure (footguns: real passwd path, DE marker, xauth, keep-alive session)
mkdir -p ~/.vnc && touch ~/.Xauthority ~/.vnc/.de-was-selected && \
printf 'exec sleep infinity\n' > ~/.vnc/xstartup && chmod +x ~/.vnc/xstartup && \
{ [ -s ~/.kasmpasswd ] || printf 'bdpass123\nbdpass123\n' | kasmvncpasswd -u "$(id -un)" -wo ~/.kasmpasswd; } && \
# 3. (re)start Xkasmvnc on :5; -publicIP avoids the ~60s STUN hang
kasmvncserver -kill :5 >/dev/null 2>&1; \
kasmvncserver :5 -websocketPort 8444 -disableBasicAuth -sslOnly 0 -publicIP 127.0.0.1 && \
for i in $(seq 1 40); do ss -lnt 2>/dev/null | grep -q ':8444' && break; sleep 0.5; done && \
echo "kasmvnc up: $(ss -lnt | grep -q ':8444' && echo yes || echo NO)" && \
# 4. ensure chromium in the venv, run C-8 on the real KasmVNC display
./venv/bin/python -m playwright install chromium >/dev/null 2>&1; \
./venv/bin/python tools/kasm_fingerprint_probe.py --display :5 --json ~/c8.json
```

Then send `~/c8.json`. To leave KasmVNC running for the actual takeover, skip the
`-kill` on reruns; to stop it: `kasmvncserver -kill :5`.

**Password note:** `bdpass123` is a throwaway for the write-user the server
requires; the HTTP endpoint runs with `-disableBasicAuth` and is loopback-bound,
so it is not a live credential. Change it and drop `-disableBasicAuth` +
loopback-bind (`novnc_url`/`wg0`) before exposing the pane to a real operator.
