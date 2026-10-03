import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadDiagnosticsBundle } from "./useGovernance";

describe("downloadDiagnosticsBundle", () => {
  const blob = new Blob(["diagnostics archive"], { type: "application/zip" });
  const createObjectURL = vi.fn(() => "blob:diagnostics-bundle");
  const revokeObjectURL = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(blob),
      headers: new Headers({ "Content-Disposition": 'attachment; filename="bundle.zip"' }),
    }));
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("clicks a connected anchor and removes it after the click", async () => {
    let anchor: HTMLAnchorElement | undefined;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      anchor = this;
      expect(this.isConnected, "diagnostics anchor must be attached during click").toBe(true);
      expect(this.href).toBe("blob:diagnostics-bundle");
      expect(this.download).toBe("bundle.zip");
      expect(revokeObjectURL).not.toHaveBeenCalled();
    });

    await downloadDiagnosticsBundle();

    expect(click).toHaveBeenCalledTimes(1);
    expect(anchor).toBeDefined();
    expect(anchor!.isConnected).toBe(false);
    expect(createObjectURL).toHaveBeenCalledExactlyOnceWith(blob);
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/diagnostics_bundle/download", {
      credentials: "same-origin",
    });
  });

  it("keeps the download URL valid through click return and revokes once on a later tick", async () => {
    const events: string[] = [];
    revokeObjectURL.mockImplementation(() => { events.push("revoke"); });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      events.push("click");
      expect(revokeObjectURL).not.toHaveBeenCalled();
      events.push("click returned");
    });

    await downloadDiagnosticsBundle();

    expect(events, "object URL must survive the download initiation tick").toEqual(["click", "click returned"]);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(events).toEqual(["click", "click returned", "revoke"]);
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:diagnostics-bundle");
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  });

  it("removes the anchor and revokes once even when click throws", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      throw new Error("click failed");
    });

    await expect(downloadDiagnosticsBundle()).rejects.toThrow("click failed");

    expect(document.querySelectorAll('a[href="blob:diagnostics-bundle"]'), "a failed click must not leave its anchor attached").toHaveLength(0);
    vi.runAllTimers();
    expect(revokeObjectURL, "a failed click must still release its object URL").toHaveBeenCalledExactlyOnceWith("blob:diagnostics-bundle");
  });

  it.each([
    { body: { error: "bundle unavailable" }, expected: "bundle unavailable" },
    { body: {}, expected: "download failed (503)" },
  ])("preserves failed-response behavior: $expected", async ({ body, expected }) => {
    const responseBlob = vi.fn();
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 503,
      json: vi.fn().mockResolvedValue(body),
      blob: responseBlob,
    } as unknown as Response);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await expect(downloadDiagnosticsBundle()).rejects.toThrow(expected);
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/diagnostics_bundle/download", {
      credentials: "same-origin",
    });
    expect(responseBlob).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revokeObjectURL).not.toHaveBeenCalled();
    console.info(`NEGATIVE-CONTROL ${JSON.stringify({ body, error: expected, downloads: 0 })}`);
  });
});
