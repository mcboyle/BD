import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/ui/card";

// Placeholder route used by Sites / Queue / Activity / Settings until
// U4–U7 ship them. The AppShell renders so the tab bar works and
// navigation between tabs is fully functional; only the page body
// is a stub.

export interface PlaceholderRouteProps {
  title: string;
  unit: string;
}

export function PlaceholderRoute({ title, unit }: PlaceholderRouteProps) {
  return (
    <AppShell title={title}>
      <Card className="bg-surface-2 p-6">
        <p className="text-sm font-medium text-ink">{title} tab</p>
        <p className="mt-1 text-sm text-ink-3">
          Ships in {unit}. The Home tab is live now — switch tabs to
          confirm navigation works.
        </p>
      </Card>
    </AppShell>
  );
}
