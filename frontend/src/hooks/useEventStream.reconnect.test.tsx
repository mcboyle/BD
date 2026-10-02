import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useEventStream,
  isStreamConnected,
  __resetEventStreamForTests,
} from "./useEventStream";

// O1671 r8c: after >3 consecutive SSE errors the shared singleton closed the
// EventSource and never reconnected, so every panel stayed on its polling
// fallback until a fresh subscriber happened to mount. The give-up must now
// schedule a backoff reconnect while anyone is still subscribed.

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {
    this.closed = true;
  }
}

const latest = () =>
  FakeEventSource.instances[FakeEventSource.instances.length - 1];

function failUntilGiveUp(es: FakeEventSource) {
  act(() => {
    for (let i = 0; i < 4; i++) es.onerror?.();
  });
}

describe("useEventStream reconnect after give-up", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    __resetEventStreamForTests();
  });

  afterEach(() => {
    __resetEventStreamForTests();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reconnects with backoff after >3 errors and live updates recover", () => {
    const { result } = renderHook(() => useEventStream({ dashboard: () => {} }));
    const first = latest();
    act(() => first.onopen?.());
    expect(result.current.connected).toBe(true);

    failUntilGiveUp(first);
    expect(first.closed).toBe(true);
    expect(result.current.connected).toBe(false);
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    const second = latest();
    expect(second.closed).toBe(false);
    act(() => second.onopen?.());
    expect(result.current.connected).toBe(true);
    expect(isStreamConnected()).toBe(true);
  });

  it("a reconnected stream gets a fresh error budget", () => {
    renderHook(() => useEventStream({ dashboard: () => {} }));
    failUntilGiveUp(latest());
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    const second = latest();
    // One transient error on the new stream must not trip the give-up again.
    act(() => second.onerror?.());
    expect(second.closed).toBe(false);
  });

  it("keeps retrying when the reconnect attempt fails too", () => {
    renderHook(() => useEventStream({ dashboard: () => {} }));
    failUntilGiveUp(latest());
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    failUntilGiveUp(latest());
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(FakeEventSource.instances).toHaveLength(3);
  });

  it("does not reconnect once every subscriber has unmounted", () => {
    const { unmount } = renderHook(() =>
      useEventStream({ dashboard: () => {} }),
    );
    failUntilGiveUp(latest());
    unmount();
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
