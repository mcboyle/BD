import { FileCode } from "lucide-react";

import { Badge } from "@/components/ui/badge";

// Cut 6.7 — site template badge. Shows the template a site is bound to; renders
// nothing when the site has no template.

export function SiteTemplateBadge({
  templateName,
}: {
  templateName?: string | null;
}) {
  if (!templateName) return null;

  return (
    <Badge variant="outline" glyph={<FileCode className="h-3 w-3" />}>
      {templateName}
    </Badge>
  );
}
