import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiBootReadinessStatus } from "./AiBootReadinessStatus";

describe("AiBootReadinessStatus", () => {
  it.each([
    [{ state: "ready" }, "AI ready (GPU)"],
    [{ state: "retrying" }, "AI warming"],
    [{ state: "degraded", models: { text: { state: "ready" }, vision: { state: "failed" } } }, "Text ready; vision retrying"],
    [{ state: "degraded", error_code: "gpu_unavailable" }, "AI degraded: gpu_unavailable"],
    [{ state: "not_applicable" }, "AI boot warm not applicable"],
    [{ state: "stale" }, "AI readiness stale"],
    [undefined, "AI readiness unknown"],
  ])("renders %j as %s", (value, label) => {
    render(<AiBootReadinessStatus value={value} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });
});
