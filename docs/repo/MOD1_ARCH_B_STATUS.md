# MOD-1 Arch B (remote_vnc / KasmVNC) -- status for the box

<!-- verified-against: v3.66.805 -- branch claude/validate-repo-bundle-rpims4 -->

**What this is.** The coexist plan's C-series (Arch B, KasmVNC captcha takeover)
is implemented and verified in the cloud sandbox. This records, per item, what
was **proven in-sandbox** vs. what **still needs the box** -- so nothing here is
read as "done on stash" that was only shown to hold in the sandbox (CLAUDE.md 9,
10). Re-run the box-only checks on stash before trusting them.

## Landed cuts (this branch, RED-first, guards 7/7 unchanged, no version bump)

| Cut | What | Key commit |
| --- | --- | --- |
| C-1 | transport-tagged takeover registry + sweep denominator (`kind="vnc"`) | (earlier) |
| C-2 | mode ladder `remote_vnc` + derived capability probe (seam) | (earlier) |
| C-3 | FE `remote_vnc` option | (earlier) |
| C-4 | KasmVNC install + **live serving session** (offline pack) | 26d4f3f |
| C-4b | wire the C-2 ladder into admission (kill the silent dead toggle) | 886ea72 |
| C-5 | `remote_vnc` transport: launch/attach/teardown as `kind="vnc"` | eacc371 |
| C-6 | cockpit KasmVNC viewer embed + effective-mode/reason readout | f0a7d91 |
| C-7 | egress containment (KASM-T8): unix-domain X + fail-closed netns | 692af55 |

## Proven in-sandbox (pasted output exists in the referenced docs/commits)

- **C-4 serving session** -- KasmVNC 1.4.0 installed offline; `kasmvncserver`
  serves `HTTP 200` from `Server: KasmVNC/4.0` on loopback `:8444`; its own web
  client renders. (Three provisioning footguns corrected: passwd path
  `$HOME/.kasmpasswd`, the `.de-was-selected` marker, and `-publicIP` to skip
  the ICE/STUN hang. See `MOD1_C4_C8_SANDBOX_PROBE.md`.)
- **C-5 lifecycle** -- a real headful browser on the Xvnc display, registered
  `kind="vnc"`, counted by the ONE shared cap, seen by the no-orphan sweep
  census, torn down clean; the derived probe flips available/unavailable with
  the live stack up/down.
- **C-6 readout** -- the cockpit poll (`/api/captcha/pending`) carries
  `mode=remote_vnc`, the downgrade reason, and the KasmVNC `vnc_url`; the FE
  renders an iframe to that URL (tsc + vitest green).
- **C-7 containment** -- the takeover browser launches inside a real
  `bd_takeover_*` netns; external TCP from inside the ns is **blocked**
  (fail-closed) while a unix socket inside the ns connects; KasmVNC opens no TCP
  X listener, only the loopback websocket. Fail-closed raises rather than
  launching uncontained.

## Still needs the box (do NOT record these as done on the strength of sandbox)

- **A human solve through the cockpit.** Install + serve + confine are proven;
  an operator actually seeing and driving a solve through KasmVNC is not a
  sandbox artifact -- run it on stash.
- **The live WireGuard tunnel (C-7).** The WireGuard **kernel module is absent**
  in-sandbox (`ip link add type wireguard` -> "Unknown device type"). The netns
  confinement and fail-closed policy are proven over default-drop/veth; the live
  wg0 handshake is stash-only. "Egress fails closed" is supportable; "the VPN
  tunnel works" is a box measurement.
- **C-8 (KASM-T10) fingerprint -- MEASURED ON STASH (2026-07-20).** The tool
  `tools/kasm_fingerprint_probe.py` was run on stash against the REAL KasmVNC
  takeover display (`:5`), and separately on Xvfb -- IDENTICAL results, which
  proves KasmVNC on stash is software-rendered. Verdict: the counter-tell does
  NOT materialize on this deployment. 3/18 properties differ and every one favors
  Arch B or is neutral:
  - `user_agent`: real `Chrome` (headful) vs `HeadlessChrome` (headless) -- live-X
    FIXES the headline headless tell.
  - `plugins_count`: 5 vs 0 -- live-X fixes the empty-plugins headless tell.
  - `canvas_hash`: differs (minor AA/font-hinting; not a tell either way).
  - `navigator.webdriver`: suppressed in BOTH by the anti-automation args.
  - WebGL renderer: software rasterizer in BOTH -> **no datacenter-GPU leak**.

  So on stash's software-rendered display, `remote_vnc` is MORE human than
  headless, not worse -- the fingerprint concern that could have inverted the case
  for Arch B is disproven here. The only scenario that would change this is a
  GPU-accelerated KasmVNC (VirtualGL/DRI3) on a host with a discrete GPU; re-run
  the tool there if that config is ever used. Command:
  `./venv/bin/python tools/kasm_fingerprint_probe.py --display :5 --json c8.json`.
- **The FE-artifact parity/route gates.** `gui_parity`, `route_index_in_sync`,
  `spa_wired_join`, `parity_abc`, `challenge_parity`, `idle_sweep` fail in the
  sandbox for a MISSING-ARTIFACT reason (identical on pristine HEAD -- generated
  inventory/manifest that needs the built FE). The C-6 diff adds no route, no
  `/api/` literal, and no config key, so it carries no regen obligation; still,
  re-run these on the box with a built FE to confirm.

## Operator config surface (all already FE-wired; C-6 adds no new key)

| Key | Meaning | Default |
| --- | --- | --- |
| `captcha_takeover_mode` | `visible` \| `remote` \| `remote_vnc` (requested) | `visible` |
| `captcha_takeover_enabled` | kill-switch, masters BOTH remote paths, fail-closed | off |
| `captcha_takeover_max_concurrent` | ONE shared cap across cdp + vnc | 2 |
| `novnc_url` | browser-reachable KasmVNC web-client URL (the C-6 iframe src) | "" |
| `captcha_vnc_display` | Xvnc display; forced to unix `:<n>` form (C-7) | `:5` |
| `captcha_vnc_websocket_port` | KasmVNC websocket port (probe + default viewer) | 8444 |
| `netns_isolation` | opt-in egress confinement for the takeover browser (C-7) | off |

`captcha_vnc_display` / `captcha_vnc_websocket_port` are read with safe
defaults (plain `.get`), not declared global_config keys -- front them with a
declared key + FE control when the operator needs to set them from the UI.

## Deploy note (overlay, CLAUDE.md 7)

This branch ADDS `bulk_downloader/takeover_vnc.py` and tests; it deletes nothing,
so the `unzip -o` overlay is sufficient -- no deploy-manifest orphan step needed
for this series. KasmVNC provisioning uses the offline `pack_kasm` deb set
(github release is repo-scope gated in the cloud env); on stash, either the pack
or the network `.deb` works.
