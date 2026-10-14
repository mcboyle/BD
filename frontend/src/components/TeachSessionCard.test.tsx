import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiPost } from "@/lib/api-client";
import { TeachSessionCard } from "./TeachSessionCard";

// F035: the SPA had no control for the takeover teach session; the three
// endpoints were reachable only by a hand-made POST.

vi.mock("@/lib/api-client", () => ({ apiPost: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const URL_IN = "https://example.test/video/1";

function mount(initialUrl = "") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TeachSessionCard siteId="site 1" initialUrl={initialUrl} />
    </QueryClientProvider>,
  );
}

const button = (name: string) => screen.getByRole("button", { name });
const typeUrl = (value: string) =>
  fireEvent.change(screen.getByLabelText("Teach URL"), { target: { value } });

beforeEach(() => vi.clearAllMocks());

describe("TeachSessionCard", () => {
  it("starts a takeover only for an http(s) URL and only after confirm", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ok: true, message: "Manual download takeover started" });
    mount();
    expect(button("Take over to teach")).toBeDisabled();
    typeUrl("javascript:alert(1)");
    expect(button("Take over to teach")).toBeDisabled();
    expect(screen.getByText("Enter an http(s) URL to take over.")).toBeInTheDocument();
    typeUrl(`  ${URL_IN}  `);
    expect(button("Take over to teach")).toBeEnabled();

    fireEvent.click(button("Take over to teach"));
    expect(apiPost).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent(URL_IN);
    fireEvent.click(button("Confirm"));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith("/api/sites/site%201/take_over_url", { url: URL_IN });
    expect(await screen.findByRole("status", { name: "Teach session result" }))
      .toHaveTextContent("Manual download takeover started");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("backing out of the confirm sends nothing", () => {
    mount(URL_IN);
    fireEvent.click(button("Take over to teach"));
    fireEvent.click(button("Back"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("I'm Done posts take_over_done and lists what was learned", async () => {
    vi.mocked(apiPost).mockResolvedValue({
      ok: true,
      message: "Manual download finished, learned 2 role(s)",
      learned_summary: { row_selector: "div.card", dismiss_selectors: ["#x", ".y"] },
    });
    mount();
    fireEvent.click(button("I'm Done"));
    fireEvent.click(button("Confirm"));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith("/api/sites/site%201/take_over_done", {});
    const learned = await screen.findByLabelText("Learned selectors");
    const terms = within(learned).getAllByRole("term").map((t) => t.textContent);
    expect(terms).toEqual(["row_selector", "dismiss_selectors"]);
    expect(learned).toHaveTextContent("div.card");
    expect(learned).toHaveTextContent('["#x",".y"]');
    expect(screen.getByRole("status")).toHaveTextContent("learned 2 role(s)");
  });

  it("says so when Done learned nothing", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ok: true, message: "finished", learned_summary: {} });
    mount();
    fireEvent.click(button("I'm Done"));
    fireEvent.click(button("Confirm"));
    expect(await screen.findByText("No selectors were learned.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Learned selectors")).not.toBeInTheDocument();
  });

  it("Cancel teach is a yes/no confirm; No sends nothing, Yes posts take_over_cancel", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ok: true, message: "Manual download cancelled" });
    mount();
    fireEvent.click(button("Cancel teach"));
    expect(screen.getByRole("dialog")).toHaveTextContent("Cancel teaching session?");
    fireEvent.click(button("No, keep teaching"));
    expect(apiPost).not.toHaveBeenCalled();

    fireEvent.click(button("Cancel teach"));
    fireEvent.click(button("Yes, cancel teach"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith("/api/sites/site%201/take_over_cancel", {});
    expect(await screen.findByRole("status")).toHaveTextContent("Manual download cancelled");
    expect(screen.queryByLabelText("Learned selectors")).not.toBeInTheDocument();
    expect(screen.queryByText("No selectors were learned.")).not.toBeInTheDocument();
  });

  it("shows the server's refusal (ok:false on HTTP 200) as an alert, not a success", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ok: true, message: "Manual download takeover started" })
      .mockResolvedValueOnce({ ok: false, message: "No pending manual download" });
    mount(URL_IN);
    fireEvent.click(button("Take over to teach"));
    fireEvent.click(button("Confirm"));
    await screen.findByRole("status");

    fireEvent.click(button("I'm Done"));
    fireEvent.click(button("Confirm"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("No pending manual download");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows a transport error as an alert and replaces the earlier result", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ok: true, message: "Manual download cancelled" })
      .mockRejectedValueOnce(new Error("POST /api/sites/site%201/take_over_done → 404: Not found"));
    mount();
    fireEvent.click(button("Cancel teach"));
    fireEvent.click(button("Yes, cancel teach"));
    await screen.findByRole("status");

    fireEvent.click(button("I'm Done"));
    fireEvent.click(button("Confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("404: Not found");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
