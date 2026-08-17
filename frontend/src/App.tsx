import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";

import { Home } from "./routes/Home";
import { Sites } from "./routes/Sites";
import { SiteDetail } from "./routes/SiteDetail";
import { SiteSettings } from "./routes/SiteSettings";
import { Queue } from "./routes/Queue";
import { Activity } from "./routes/Activity";
import { Settings } from "./routes/Settings";
import { Advanced } from "./routes/Advanced";
import { TemplateManager } from "./routes/TemplateManager";
import { DryRunInspector } from "./routes/DryRunInspector";
import { LogDiff } from "./routes/LogDiff";
import { Library } from "./routes/Library";
import { Backup } from "./routes/Backup";
import { ImportViews } from "./routes/ImportViews";
import { MoreActions } from "./routes/MoreActions";
import { Maintenance } from "./routes/Maintenance";
import { PluginMetrics } from "./routes/PluginMetrics";
import { RebalanceCenter } from "./routes/RebalanceCenter";
import { ImportsCenter } from "./routes/ImportsCenter";
import { SiteActions } from "./routes/SiteActions";
import { SitePayloadActions } from "./routes/SitePayloadActions";
import { BatchOps } from "./routes/BatchOps";
import { Integrations } from "./routes/Integrations";
import { PoolsMacros } from "./routes/PoolsMacros";
import { Dedup } from "./routes/Dedup";
import { Vpn } from "./routes/Vpn";
import { Secrets } from "./routes/Secrets";
import { Users } from "./routes/Users";
import { AuthGate } from "./components/AuthGate";
import { AiTeach } from "./routes/AiTeach";
import { DomAnalyzer } from "./routes/DomAnalyzer";
import { CaptureWorkflow } from "./routes/CaptureWorkflow";

// v3.66.205 (T1): route-level code-splitting starts here. The read-only
// dashboard is the first lazily-loaded route — App.tsx wraps <Routes> in
// <Suspense> so subsequent Phase 2 tranches can convert their routes to
// lazy() without further shell changes.
const Dashboard = lazy(() => import("./routes/Dashboard"));
const Tools = lazy(() => import("./routes/Tools"));
// v3.66.206 (T2): history/logs/search tranche — second lazy route.
const History = lazy(() => import("./routes/History"));
const NeedsReview = lazy(() => import("./routes/NeedsReview"));
// v3.66.210 (T7): notify/tg/alerts tranche — third lazy route.
const Notifications = lazy(() => import("./routes/Notifications"));
// v3.66.211 (T8): fed/edge_deploy/pair tranche — /cluster lazy route.
const Cluster = lazy(() => import("./routes/Cluster"));
// v3.66.382 (FE cut): the Cut 8 / Phase 9 deferred write-surface UIs.
const Schedules = lazy(() => import("./routes/Schedules"));
const AlertRules = lazy(() => import("./routes/AlertRules"));
const BulkEnqueue = lazy(() => import("./routes/BulkEnqueue"));
const Budget = lazy(() => import("./routes/Budget"));
const AiAssist = lazy(() => import("./routes/AiAssist"));

// U7 routing — all five tabs are real. Settings has a sub-route
// for Advanced. PlaceholderRoute is no longer mounted (kept in the
// tree as dead code for now — U8 cleanup can drop it).
//
// v3.64.x D3 follow-up (U5): /logs/diff route mounts LogDiff, the
// side-by-side log comparison view. Reachable only from the Queue
// tab's Compare flow (JobErrorModal → pick second → navigate here).

export default function App() {
  return (
    <AuthGate>
      <Suspense fallback={null}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/history" element={<History />} />
        <Route path="/needs-review" element={<NeedsReview />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/cluster" element={<Cluster />} />
        <Route path="/sites" element={<Sites />} />
        <Route path="/sites/:siteId" element={<SiteDetail />} />
        <Route path="/sites/:siteId/settings" element={<SiteSettings />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/settings/advanced" element={<Advanced />} />
        <Route path="/templates" element={<TemplateManager />} />
        <Route path="/sites/:siteId/inspect" element={<DryRunInspector />} />
        <Route path="/sites/:siteId/actions" element={<SiteActions />} />
        <Route path="/sites/:siteId/payload-actions" element={<SitePayloadActions />} />
        <Route path="/logs/diff" element={<LogDiff />} />
        <Route path="/library" element={<Library />} />
        <Route path="/backup" element={<Backup />} />
        <Route path="/import-views" element={<ImportViews />} />
        <Route path="/more-actions" element={<MoreActions />} />
        <Route path="/maintenance" element={<Maintenance />} />
        <Route path="/plugins/metrics" element={<PluginMetrics />} />
        <Route path="/rebalance" element={<RebalanceCenter />} />
        <Route path="/imports" element={<ImportsCenter />} />
        <Route path="/batch-ops" element={<BatchOps />} />
        <Route path="/pools-macros" element={<PoolsMacros />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/dedup" element={<Dedup />} />
        <Route path="/vpn" element={<Vpn />} />
        <Route path="/secrets" element={<Secrets />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/users" element={<Users />} />
        <Route path="/ai-teach" element={<AiTeach />} />
        <Route path="/dom-analyzer" element={<DomAnalyzer />} />
        <Route path="/capture" element={<CaptureWorkflow />} />
        <Route path="/schedules" element={<Schedules />} />
        <Route path="/alerts" element={<AlertRules />} />
        <Route path="/bulk-enqueue" element={<BulkEnqueue />} />
        <Route path="/budget" element={<Budget />} />
        <Route path="/ai-assist" element={<AiAssist />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
    </AuthGate>
  );
}
