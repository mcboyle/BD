// TakeoverViewer — MOD-1 A-3: remote captcha takeover viewer.
//
// Renders the SOLVE browser's screencast (A-1 SSE) onto a canvas and forwards
// the operator's pointer/keyboard as an allowlisted CDP Input subset (A-2).
//
// Two FULL /cockpit/api/ literals so the parity scanner credits them spa_wired
// (a concatenated base var would not be counted):
//   GET  /cockpit/api/takeover/${sid}/screencast   — DEDICATED per-session
//        EventSource (NOT the /api/stream singleton, which would flood the
//        shared channel).
//   POST /cockpit/api/takeover/${sid}/input         — apiPost (CSRF-handled).
//
// Coordinate mapping (canvas -> page viewport) is the correctness lynchpin: the
// canvas is rendered at the source resolution and CSS-scaled, so pointer coords
// are divided back by the CSS/backing-store ratio before they are sent.

import { useCallback, useEffect, useRef } from "react";

import { apiPost } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

interface TakeoverViewerProps {
  sid: string;
  /** Source geometry the solve browser screencasts at. */
  width?: number;
  height?: number;
  /** MOD-1 C-6: the EFFECTIVE takeover mode from the poll. "remote_vnc" renders
   *  KasmVNC's own web client in an iframe (real X input) instead of the CDP
   *  screencast canvas; anything else keeps the Arch A canvas path. */
  mode?: string | null;
  /** KasmVNC web-client URL, present only on a remote_vnc session. */
  vncUrl?: string | null;
  /** Non-empty when the requested mode was downgraded; shown to the operator so
   *  the downgrade is never silent (plan 1.2). */
  reason?: string | null;
}

type CdpInput = Record<string, unknown>;

export function TakeoverViewer({
  sid,
  width = 1280,
  height = 720,
  mode,
  vncUrl,
  reason,
}: TakeoverViewerProps) {
  const isVnc = mode === "remote_vnc";
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Newest-wins: hold only the latest frame, drawn on the next animation frame.
  const pendingFrame = useRef<string | null>(null);
  const rafId = useRef<number | null>(null);

  // ── screencast in: dedicated EventSource, torn down on unmount ────────────
  // Arch B (remote_vnc) is driven through KasmVNC, so the CDP screencast
  // EventSource must NOT open for it (no shared-channel traffic, no CDP pump).
  useEffect(() => {
    if (!sid || isVnc) return;
    const url = `/cockpit/api/takeover/${sid}/screencast`;
    const es = new EventSource(url, { withCredentials: true });

    const draw = () => {
      rafId.current = null;
      const data = pendingFrame.current;
      pendingFrame.current = null;
      const canvas = canvasRef.current;
      if (!data || !canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const img = new Image();
      img.onload = () => ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      img.src = `data:image/jpeg;base64,${data}`;
    };

    const onFrame = (ev: MessageEvent) => {
      pendingFrame.current = ev.data as string;
      if (rafId.current == null) rafId.current = requestAnimationFrame(draw);
    };

    es.addEventListener("frame", onFrame as EventListener);
    return () => {
      es.removeEventListener("frame", onFrame as EventListener);
      es.close();
      if (rafId.current != null) cancelAnimationFrame(rafId.current);
      rafId.current = null;
    };
  }, [sid, isVnc]);

  // ── input out: map to the source viewport, POST the allowlisted event ─────
  const send = useCallback(
    (event: CdpInput) => {
      // Best-effort; a dropped input is not fatal (the operator retries).
      void apiPost<OkResult>(`/cockpit/api/takeover/${sid}/input`, event).catch(
        () => undefined,
      );
    },
    [sid],
  );

  const toViewport = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    // Map CSS pixels back to the source resolution the browser renders at.
    const x = Math.round(((e.clientX - rect.left) / rect.width) * canvas.width);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * canvas.height);
    return { x, y };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = toViewport(e);
    send({ type: "mouseMoved", x, y });
  };
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = toViewport(e);
    send({ type: "mousePressed", x, y, button: "left", clickCount: 1 });
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = toViewport(e);
    send({ type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    // A single printable character goes as insertText; everything else as a
    // key event. Navigation / shortcuts are rejected server-side (allowlist).
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      send({ type: "insertText", text: e.key });
    } else {
      send({ type: "keyDown", key: e.key, code: e.code });
    }
    e.preventDefault();
  };
  const onKeyUp = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    send({ type: "keyUp", key: e.key, code: e.code });
    e.preventDefault();
  };

  // MOD-1 C-6: the effective-mode banner. Always shown; when the requested mode
  // was downgraded, `reason` explains it (a silent downgrade is a lie by
  // omission, plan 1.2).
  const banner = (
    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-emerald-200">
        {isVnc ? "Remote VNC (KasmVNC)" : mode === "remote" ? "Remote (screencast)" : mode || "remote"}
      </span>
      {reason ? <span className="text-amber-300/80">downgraded: {reason}</span> : null}
      <span className="text-ink-3">
        {isVnc
          ? "Real input via KasmVNC — BD does not see keystrokes."
          : "Live solve — click and type to solve the challenge remotely."}
      </span>
    </div>
  );

  if (isVnc) {
    return (
      <div className="mt-2 rounded border border-border/60 bg-black/40 p-2">
        {banner}
        {vncUrl ? (
          <iframe
            src={vncUrl}
            title="Remote captcha solve view (KasmVNC)"
            aria-label="Remote captcha solve view — interact through KasmVNC"
            className="block h-[520px] w-full max-w-full rounded border-0"
            allow="clipboard-read; clipboard-write"
          />
        ) : (
          <div className="rounded bg-amber-500/10 p-3 text-xs text-amber-200">
            KasmVNC viewer URL is not set. Set <span className="font-mono">noVNC URL</span> in
            Settings to a browser-reachable address of the KasmVNC endpoint.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2 rounded border border-border/60 bg-black/40 p-2">
      {banner}
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        tabIndex={0}
        aria-label="Remote captcha solve view — click and type to interact"
        className="block w-full max-w-full cursor-crosshair rounded outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        onPointerMove={onPointerMove}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
      />
    </div>
  );
}

export default TakeoverViewer;
