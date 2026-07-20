# MOD-1 C-4..C-8 -- sandbox re-derivation (probe, don't declare)

<!-- verified-against: v3.66.805 -- 2026-07-20 autonomous sandbox probe -->

**Why this exists.** `BD_MOD1_COEXIST_PLAN.md` Phase 3 tags C-4..C-8 `[STASH]`
with the blanket justification *"Real display stack is stash-only (WireGuard
kmod absent in sandbox)."* That conflates two different claims. Only one is true.
Per `CLAUDE.md` 0, declaring a capability absent without testing it is the same
failure this codebase is organised against -- so each arm was re-derived **by
running it**, root + sudo + full network in this environment. Instrument and
predicate are stated per row; exits captured unpiped.

Three columns: **mechanism** = provable headless or under Xvfb here; **live** =
needs a real operator session/device; **impossible** = a named missing primitive.

| Arm | Plan intent | Column | Verdict |
| --- | --- | --- | --- |
| C-4 KasmVNC `.deb` | delivery mechanism | **impossible here (narrow)** | binary gated by GitHub session repo-scope; add_repo cross-owner rejected |
| C-4/C-6 serving loop | web viewer | **mechanism** | x11vnc+noVNC+websockify serving loop stands up, loopback-bound |
| C-5 lifecycle | registry binding | **mechanism** | real headful browser on X, registered `kind="vnc"`, counted, swept, torn down |
| C-7 egress | fail-closed | **mechanism** (policy) + **impossible** (live tunnel) | veth up/down/up proves iface-scoped drop; WireGuard kmod absent |
| C-8 fingerprint | counter-tell | **mechanism** measured; **magnitude is stash** | only the UA token differs on GPU-less HW; `navigator.webdriver=true` in BOTH |

Several moved from column 3 to column 1, exactly as the probe prompt predicted.

---

## C-4 -- KasmVNC provisioning (delivery mechanism)

**Genuinely blocked here, for a precise reason -- and it is not the one the plan
gives.** The plan says "display stack is stash-only." False: the display stack
installs and runs (see C-5/C-6). What is actually blocked is the *kasmtech
binary*:

```
$ curl -sSLf -o /tmp/kasm.deb \
    https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_noble_1.4.0_amd64.deb
exit=22   (HTTP 403)
$ curl -sL <same URL> | head -1
{"message":"GitHub access to this repository is not enabled for this session.
 Use add_repo to request access."}
```

This is the **GitHub session repo-scope gate**, not the outbound proxy: all
`github.com` traffic is scoped to `mcboyle/bd`. The documented remedy fails too --
`add_repo kasmtech/KasmVNC` returns *"cross-tier adds are not supported in v1 ...
session already has repos from owner(s) [mcboyle]."* So the kasmtech `.deb`
cannot be pulled in this session at all.

`KASMVNC_PULL_GUIDE.md` reports the install/config as VERIFIED in a
differently-scoped environment (HTTP 200, 2,254,804 bytes, `apt-get install`
exit 0, no kernel module). That is credible -- the deps are ordinary X libs --
but **it is not my measurement and I do not restate it as one.** What I *can*
prove in-sandbox is the equivalent web-delivery loop, below (C-6). Both the pull
guide and `ONLINE_PULL_LIST.md` endorse the apt `x11vnc+noVNC` stack as the
legitimate stand-in for headless checks.

**Do not record C-4 complete on an install.** The gate is a session an operator
can see and drive; that is stash/operator work regardless of provisioning.

---

## C-6 -- serving loop (web viewer mechanism)

**Column 1 -- proven end to end, one shell invocation.** Instrument: the apt
`x11vnc + noVNC + websockify` stack against `Xvfb :99`. Predicate: a loopback
HTTP endpoint actually serving the web client, plus a render.

```
Xvfb :99 -screen 0 1280x800x24        -> socket up
fluxbox on :99                        -> WM up
x11vnc -display :99 -rfbport 5900 -localhost   -> 127.0.0.1:5900 LISTEN
websockify --web=/usr/share/novnc 6080 localhost:5900 -> 0.0.0.0:6080 LISTEN
curl -sI http://127.0.0.1:6080/vnc.html  -> HTTP/1.1 200 OK
curl -s  .../vnc.html | grep title       -> <title>noVNC</title>
scrot /tmp/c6_render.png                  -> 23999-byte PNG of the display
```

x11vnc bound to `127.0.0.1` -- the plan's 4.1 loopback requirement, load-bearing
security not tidiness. This is the mechanism KasmVNC's own `/usr/share/kasmvnc/www`
would otherwise provide; the plan says KasmVNC *supersedes* this stack for
full-motion content, and that specific superiority (codec/latency) is the one
property this stand-in does not reproduce -- stated precisely, not "un-sandboxable."

---

## C-5 -- session lifecycle bound into the C-1 registry

**Column 1 -- proven with a real browser on a real display.** A headful chromium
(`headless=False` *requires* a display) launched on `:99`, then registered into
the C-1 transport registry as `kind="vnc"` and exercised through the cap/sweep
denominators built in C-1:

```
registered kind          : vnc
count(kind='vnc')        : 1
count(default=all xports): 1        # shared cap sees it
sweep sids (all xports)  : ['vnc-sess-1']
sweep sids (kind=cdp)    : []        # NOT miscounted as the other transport
after +1 cdp: count(all) : 2         # shared cap spans BOTH transports (plan 4.3)
post-close count(vnc)    : 0
post-close sids          : ['cdp-sess-1']   # teardown clean, cdp untouched
```

Launch -> attach -> teardown, bound into the unified registry, counted by the
shared cap, visible to the sweep. Not proven here: the wall-clock **idle-timeout**
reap (a timer, not a transport property) and the KasmVNC delivery itself (C-4).

---

## C-7 -- egress containment (KASM-T8)

**Split verdict, stated as two claims because they are two claims.**

*Mechanism (iface-scoped egress policy) -- column 1, proven.* Real netns + veth +
nftables, three-state up/down/up so the drop is attributable to the iface, not a
fluke. Instrument: TCP connect from inside the netns to a host listener.

```
ip netns add bdC7                 -> exit 0   (netns creation WORKS here)
veth pair host<->ns, nft masquerade
STATE 1  iface UP   -> CONNECT-OK ("OK")
STATE 2  iface DOWN -> CONNECT-FAIL  OSError [Errno 101] Network is unreachable
STATE 3  iface UP   -> CONNECT-OK   (proves it was the iface)
```

*Live tunnel -- genuinely impossible here.* The WireGuard kernel module is absent:

```
ip link add wgtest type wireguard -> exit 2  "Unknown device type"
```

`CAP_NET_ADMIN` + `/dev/net/tun` do **not** imply the kmod. veth proves
**"egress fails closed"**; it does **not** prove **"the VPN works."** Keep that
distinction in any claim -- the handshake arm is stash-only, for a named reason.

(`bd-netns-proof` was the codebase's own instance of this anti-pattern -- it
printed a hardcoded *"needs CAP_NET_ADMIN (stash-only)"* verdict. Now fixed: it
runs `ip netns add` and reports works / lacks-cap / undetermined. Live output:
*"netns creation here: works (created + deleted a throwaway netns)"*, exit 0.)

---

## C-8 -- fingerprint measurement (KASM-T10, the counter-tell)

**Column 1 mechanism, measured -- and the result is a caveat, not a win.**
Instrument: the same chromium (`/opt/pw-browsers/chromium-1194`, `executable_path`
pinned -- the box has rev 1194, the venv's playwright wanted 1228; CLAUDE.md 5's
two-pool trap, avoided). Predicate: exactly the properties a bot-check samples.

| property | headless | headful on Xvfb :99 | differs? |
| --- | --- | --- | --- |
| WebGL UNMASKED_VENDOR | Google Inc. (Google) | Google Inc. (Google) | no |
| WebGL UNMASKED_RENDERER | ANGLE ... SwiftShader | ANGLE ... SwiftShader | no |
| screen w/h/depth | 1280x720x24 | 1280x720x24 | no |
| hardwareConcurrency | 4 | 4 | no |
| **navigator.webdriver** | **true** | **true** | **no** |
| userAgent | ...**HeadlessChrome**/141... | ...**Chrome**/141... | **yes** |

**On this GPU-less host the only fingerprint delta is the UA token** -- the one
signal trivially spoofable *without* a display. The WebGL renderer is SwiftShader
in both modes because there is no GPU to expose, so the surface that would
actually distinguish live-X from headless collapses. Two honest consequences:

1. The **magnitude** that could "invert the case for B on some targets" is a
   **stash measurement** -- it needs real GPU hardware, where headful-on-X exposes
   a true renderer string that headless masks. I cannot reproduce that here, and
   I do not claim the delta is small in general from a GPU-less sample.
2. `navigator.webdriver=true` **persists in both modes.** Arch B does *not* clear
   the single strongest automation tell on its own. That is a real finding: the
   remote_vnc rationale rests on *input realism*, not on webdriver evasion.

---

## What the operator still owns (unchanged by this probe)

- C-4 live serving session an operator drives (KasmVNC binary is repo-scope
  gated here regardless).
- C-7 live WireGuard handshake (kmod absent).
- C-8 magnitude on real GPU hardware (the number that can invert the case).

Everything labelled *mechanism* above is proven in-sandbox with pasted output.
Unknown was treated as failing; no arm is recorded on "should work."
