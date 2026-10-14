// F017 — /templates row bar: "list; create TEST template, edit, delete it".
// Drives the page against an in-memory draft store standing in for
// /api/template_manager{,/draft}, so the list the page shows is the store's.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { store, apiGetMock, apiPostMock, apiPutMock, apiDeleteMock } = vi.hoisted(() => {
  const store = new Map<string, Record<string, unknown>>();
  const entry = (file: string, t: Record<string, unknown>) => ({
    file,
    host: (t.host as string) ?? null,
    status: (t.status as string) ?? null,
    enabled: false,
    selectors: Object.keys((t.selectors as object) ?? {}),
    resolutions: (t.resolutions as number[]) ?? [],
    network_patterns: [],
    lint_warnings: [],
    has_blocking_lint: false,
  });
  const fileOf = (path: string) =>
    decodeURIComponent(path.split("?file=")[1] ?? "");
  return {
    store,
    apiGetMock: vi.fn(async (path: string) => {
      if (path === "/api/template_manager")
        return {
          ok: true,
          reviewed: [],
          drafts: [...store].map(([f, t]) => entry(f, t)),
        };
      if (path.startsWith("/api/template_manager/draft?file=")) {
        const f = fileOf(path);
        return store.has(f)
          ? { ok: true, file: f, template: store.get(f) }
          : { ok: false, error: "draft not found" };
      }
      if (path === "/api/templates") return { templates: [] };
      return {};
    }),
    apiPostMock: vi.fn(async (path: string, body: Record<string, unknown>) => {
      if (path === "/api/template_manager/draft") {
        const file = `${body.name}.template-draft.json`;
        if (store.has(file)) return { ok: false, error: "draft already exists" };
        store.set(file, {
          host: body.name,
          ...(body.template as object),
          status: "draft",
        });
        return { ok: true, created: file };
      }
      if (path === "/api/template/extract")
        return {
          ok: true,
          template: { row_selectors: ["a.dl"], min_resolution: 1080 },
          candidates: [],
        };
      return { ok: true };
    }),
    apiPutMock: vi.fn(async (_path: string, body: Record<string, unknown>) => {
      const file = body.file as string;
      if (!store.has(file)) return { ok: false, error: "draft not found" };
      store.set(file, { ...(body.template as object), status: "draft" });
      return { ok: true, updated: file };
    }),
    apiDeleteMock: vi.fn(async (path: string) => {
      const f = fileOf(path);
      return store.delete(f)
        ? { ok: true, deleted: f }
        : { ok: false, error: "draft not found" };
    }),
  };
});

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiDelete: apiDeleteMock,
  ApiError: class extends Error {},
}));

import { TemplateManager } from "@/routes/TemplateManager";
import { renderWired } from "@/test/wiredGateHarness";

const FILE = "test.example.com.template-draft.json";

beforeEach(() => {
  store.clear();
  for (const m of [apiGetMock, apiPostMock, apiPutMock, apiDeleteMock]) m.mockClear();
});

describe("F017 TemplateManager draft create / edit / delete", () => {
  it("creates a TEST draft, edits it, then deletes it", async () => {
    const user = userEvent.setup();
    renderWired(<TemplateManager />, "/templates");
    await screen.findByText("Drafts (0)");

    await user.click(screen.getByRole("button", { name: "New draft" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Draft name"), "test.example.com");
    fireEvent.change(within(dialog).getByLabelText("Draft template JSON"), {
      target: { value: '{"selectors": {"download": {"row_selectors": ["a.dl"]}}}' },
    });
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    expect(apiPostMock).toHaveBeenCalledWith("/api/template_manager/draft", {
      name: "test.example.com",
      template: { selectors: { download: { row_selectors: ["a.dl"] } } },
    });
    await screen.findByText("Drafts (1)");
    expect(screen.getByText("test.example.com")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Edit test.example.com" }));
    dialog = await screen.findByRole("dialog");
    const json = within(dialog).getByLabelText("Draft template JSON") as HTMLTextAreaElement;
    expect(JSON.parse(json.value).selectors.download.row_selectors).toEqual(["a.dl"]);
    const edited = { ...JSON.parse(json.value), resolutions: [1080] };
    fireEvent.change(json, { target: { value: JSON.stringify(edited) } });
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(apiPutMock).toHaveBeenCalledWith("/api/template_manager/draft", {
      file: FILE,
      template: edited,
    });
    await screen.findByText(/Res 1080/);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Delete test.example.com" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete draft?")).toBeInTheDocument();
    expect(apiDeleteMock).not.toHaveBeenCalled(); // confirm-gated
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(apiDeleteMock).toHaveBeenCalledWith(
      `/api/template_manager/draft?file=${encodeURIComponent(FILE)}`,
    );
    await screen.findByText("Drafts (0)");
    expect(screen.queryByText("test.example.com")).toBeNull();
    expect(store.size).toBe(0);
  });

  it("refuses invalid JSON in the editor without calling the API", async () => {
    const user = userEvent.setup();
    renderWired(<TemplateManager />, "/templates");
    await screen.findByText("Drafts (0)");
    await user.click(screen.getByRole("button", { name: "New draft" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Draft name"), "test.example.com");
    for (const bad of ["{not json", "[1, 2]"]) {
      fireEvent.change(within(dialog).getByLabelText("Draft template JSON"), {
        target: { value: bad },
      });
      await user.click(within(dialog).getByRole("button", { name: "Create" }));
      expect(within(dialog).getByRole("alert").textContent).toMatch(
        /Invalid JSON|must be a JSON object/,
      );
    }
    expect(apiPostMock).not.toHaveBeenCalledWith(
      "/api/template_manager/draft",
      expect.anything(),
    );
    expect(store.size).toBe(0);
  });

  it("shows the extracted draft and saves it as a draft", async () => {
    const user = userEvent.setup();
    renderWired(<TemplateManager />, "/templates");
    await screen.findByText("Drafts (0)");
    fireEvent.change(screen.getByLabelText("page html"), {
      target: { value: '<a class="dl" href="/dl/x_1080.mp4">x</a>' },
    });
    await user.type(screen.getByLabelText("page url"), "https://test.example.com/g");
    await user.click(screen.getByRole("button", { name: "Extract draft" }));
    const shown = await screen.findByLabelText("extracted draft");
    expect(shown.textContent).toContain("a.dl");
    expect(shown.textContent).toContain("1080");
    await user.click(screen.getByRole("button", { name: "Save as draft" }));
    expect(apiPostMock).toHaveBeenCalledWith("/api/template_manager/draft", {
      name: "test.example.com",
      template: { row_selectors: ["a.dl"], min_resolution: 1080 },
    });
    await screen.findByText("Drafts (1)");
    expect(store.has(FILE)).toBe(true);
  });
});
