import { useEffect, useRef, useState } from "react";

// F4.5 (v3.66.219+): shared SSE primitive for SPA live panels.
//
// ONE module-level EventSource to /api/stream is shared across every
// consumer (Dashboard, History, …) via a refcounted singleton. This is the
// whole point of F4.5: an idle open tab should hold a single push stream
// instead of each panel polling on its own interval. When the stream is
// healthy, panels suppress their polling; on stream error we close and let
// each panel's poll fallback carry the load (mirrors useQueueStream's
// <=3-failure give-up).
//
// Named events emitted by /api/stream (see bulk_downloader/app.py::api_stream):
//   dashboard        — _dashboard_snapshot(), pushed initially + every 2.5s
//   status           — _status_snapshot(light=True), pushed initially
//   download_progress, queue_change — broker-published on mutation
//   error            — server-side stream error
// (": heartbeat" comments are ignored by EventSource natively.)

type Handler = (data: unknown) => void;

interface Sub {
  handlers: Record<string, Handler>;
  onConn?: (connected: boolean) => void;
}

const _subs = new Set<Sub>();
let _es: EventSource | null = null;
let _failures = 0;
let _connected = false;
// Event names the singleton currently has addEventListener wired for.
const _wired = new Set<string>();

function _emitConn(c: boolean) {
  if (c === _connected) return;
  _connected = c;
  _subs.forEach((s) => {
    try {
      s.onConn?.(c);
    } catch {
      /* ignore */
    }
  });
}

function _dispatch(event: string, raw: string) {
  let data: unknown = null;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    return; // malformed event payload, ignore
  }
  _subs.forEach((s) => {
    const h = s.handlers[event];
    if (h) {
      try {
        h(data);
      } catch {
        /* a bad handler must not kill the stream */
      }
    }
  });
}

function _ensureListener(event: string) {
  if (!_es || _wired.has(event)) return;
  _wired.add(event);
  _es.addEventListener(event, (e: MessageEvent) => _dispatch(event, e.data));
}

function _wireAll() {
  if (!_es) return;
  // Every event name any current subscriber cares about.
  const names = new Set<string>();
  _subs.forEach((s) => Object.keys(s.handlers).forEach((n) => names.add(n)));
  names.forEach(_ensureListener);
}

function _connect() {
  if (_es || typeof EventSource === "undefined") return;
  try {
    _es = new EventSource("/api/stream");
  } catch {
    _failures++;
    return;
  }
  _es.onopen = () => {
    _failures = 0;
    _emitConn(true);
  };
  _es.onerror = () => {
    _failures++;
    // EventSource auto-reconnects, but after several consecutive errors we
    // give up and let each panel's polling fallback take over.
    if (_failures > 3 && _es) {
      _es.close();
      _es = null;
      _wired.clear();
      _emitConn(false);
    }
  };
  _wireAll();
}

function _maybeClose() {
  if (_subs.size === 0 && _es) {
    _es.close();
    _es = null;
    _wired.clear();
    _emitConn(false);
  }
}

/**
 * Subscribe to the shared /api/stream EventSource. `handlers` maps SSE event
 * names to callbacks (called with the parsed JSON payload). Returns the live
 * `connected` flag so a panel can gate its polling fallback.
 *
 * Note: `handlers` is captured once on mount via a ref; provide stable
 * callbacks (e.g. closures over queryClient) — they need not be memoized
 * because we read them through the ref.
 */
export function useEventStream(handlers: Record<string, Handler>): {
  connected: boolean;
} {
  const [connected, setConnected] = useState(_connected);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const sub: Sub = {
      // Wrap so the latest handlers are always used without re-subscribing.
      handlers: new Proxy(
        {},
        {
          get: (_t, prop: string) => handlersRef.current[prop],
          ownKeys: () => Reflect.ownKeys(handlersRef.current),
          getOwnPropertyDescriptor: (_t, prop: string) =>
            prop in handlersRef.current
              ? { enumerable: true, configurable: true }
              : undefined,
        },
      ) as Record<string, Handler>,
      onConn: setConnected,
    };
    _subs.add(sub);
    _connect();
    _wireAll();
    setConnected(_connected);
    return () => {
      _subs.delete(sub);
      _maybeClose();
    };
  }, []);

  return { connected };
}

/**
 * Module-level live-stream state, for poll-gating in query hooks
 * (useDashboard / useHistory back off their interval while this is true and
 * resume polling when the shared stream drops). Reading a module flag from a
 * react-query `refetchInterval` callback re-evaluates each tick, so panels
 * transition between push and poll without prop-drilling the connected flag.
 */
export function isStreamConnected(): boolean {
  return _connected;
}

// Test-only reset of the module singleton (no-op in prod paths).
export function __resetEventStreamForTests() {
  if (_es) {
    try {
      _es.close();
    } catch {
      /* ignore */
    }
  }
  _es = null;
  _failures = 0;
  _connected = false;
  _wired.clear();
  _subs.clear();
}
