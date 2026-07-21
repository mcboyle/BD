import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KPICard } from "@/components/KPICard";
import {
  CATEGORIES,
  WIDGETS,
  WIDGETS_BY_ID,
  type WidgetData,
} from "@/lib/widgetCatalog";

const COMPLETE_DATA = {
  done_today: 12,
  done_today_delta: "+3",
  done_today_extra: "9 videos, 3 images",
  done_hour: 4,
  done_hour_delta: "+1",
  bytes_today_fmt: "1.2 GB",
  bytes_today_breakdown: "1.0 GB video",
  files_hour: 8,
  files_hour_spark: [1, 3, 2, 8],
  files_hour_delta: "+2",
  throughput_fmt: "8 MB/s",
  throughput_delta: "+12%",
  throughput_spark: [2, 4, 6, 8],
  success_rate: 98,
  avg_speed_fmt: "2 MB/s",
  avg_speed_breakdown: "per worker",
  avg_size_fmt: "150 MB",
  avg_quality_label: "1080p",
  avg_quality_breakdown: "80% HD",
  queue_depth: 7,
  queue_depth_delta: "-2",
  workers_active: 3,
  workers_total: 4,
  disk_free_fmt: "500 GB",
  disk_used_pct: 50,
  bandwidth_fmt: "12 MB/s",
  bandwidth_spark: [3, 6, 9, 12],
  action_req: 1,
  action_req_breakdown: "1 login",
  stuck: 1,
  stuck_breakdown: "1 stalled",
  failures_hr: 2,
  failures_hr_delta: "-1",
  retries_pending: 3,
  retries_extra: "next in 5m",
  cpu_pct: 25,
  ram_pct: 40,
  cpu_cores: 8,
  ram_total_fmt: "32 GB",
  gpu_util_pct: 15,
  gpu_mem_pct: 20,
  gpu_name: "Test GPU",
  gpu_mem_total_fmt: "12 GB",
  eta_clear_fmt: "20m",
  eta_clear_extra: "at current rate",
  cookies_oldest_days: 14,
  cookies_oldest_site: "Example",
  sites_running: 2,
  sites_total: 3,
  sites_breakdown: "2 active",
  lib_total: 1200,
  lib_total_extra: "all media",
  lib_size_fmt: "2 TB",
  lib_size_extra: "across 3 sites",
  lib_watched_pct: 35,
  lib_watched_extra: "420 watched",
  lib_unrated: 90,
  lib_unrated_extra: "needs review",
  lib_missing: 2,
  lib_missing_extra: "on disk",
  lib_recent: 42,
  lib_recent_spark: [2, 5, 8, 13],
  lib_top_studio: "Example Studio",
  lib_top_studio_extra: "120 files",
  audit_recent: 4,
  audit_recent_extra: "last 24h",
  success_rate_24h: 96,
  success_rate_24h_extra: "240 completed",
  error_top_cluster: "HTTP 429",
  error_top_cluster_extra: "6 occurrences",
  captcha_24h: 1,
  captcha_24h_extra: "1 site",
  cookie_warnings: 2,
  cookie_warnings_extra: "renew soon",
  sites_need_attention: 1,
  sites_need_attention_extra: "Example",
  active_alerts: 2,
  active_alerts_extra: "last 24h",
} satisfies Required<WidgetData>;

const NULL_HEAVY_DATA = Object.fromEntries(
  Object.keys(COMPLETE_DATA).map((key) => [key, null]),
) as WidgetData;

describe("dashboard widget catalog contract", () => {
  it("contains exactly 36 unique widget IDs in valid, unique categories", () => {
    const widgetIds = WIDGETS.map((widget) => widget.id);
    const categoryIds = CATEGORIES.map((category) => category.id);
    const validCategories = new Set(categoryIds);

    expect(WIDGETS).toHaveLength(36);
    expect(new Set(widgetIds)).toHaveLength(36);
    expect(new Set(categoryIds)).toHaveLength(categoryIds.length);
    expect(WIDGETS.every((widget) => validCategories.has(widget.cat))).toBe(true);
    expect(Object.keys(WIDGETS_BY_ID).sort()).toEqual([...widgetIds].sort());
    for (const widget of WIDGETS) {
      expect(WIDGETS_BY_ID[widget.id]).toBe(widget);
    }
  });

  it.each([
    ["complete", COMPLETE_DATA],
    ["null-heavy", NULL_HEAVY_DATA],
  ] as const)("totally evaluates every spec with %s data", (_name, data) => {
    const specs = WIDGETS.map((widget) => widget.spec(data));

    expect(specs).toHaveLength(36);
    for (const spec of specs) {
      expect(spec).toBeDefined();
      expect(spec.label.trim()).not.toBe("");
    }
  });

  it.each([
    ["complete", COMPLETE_DATA],
    ["null-heavy", NULL_HEAVY_DATA],
  ] as const)("renders a KPICard for every spec with %s data", (_name, data) => {
    render(
      <>
        {WIDGETS.map((widget) => (
          <div key={widget.id} data-testid={`catalog-widget-${widget.id}`}>
            <KPICard spec={widget.spec(data)} />
          </div>
        ))}
      </>,
    );

    for (const widget of WIDGETS) {
      expect(screen.getByTestId(`catalog-widget-${widget.id}`)).toHaveTextContent(
        widget.spec(data).label,
      );
    }
  });
});
