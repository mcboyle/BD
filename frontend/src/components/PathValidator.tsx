import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, CheckCircle2, AlertTriangle } from "lucide-react";

import { apiGet } from "@/lib/api-client";
import type { StorageValidateResponse } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Cut 4 — inline path validator.
//
// A read-only diagnosis for a download directory: GET /api/storage/validate
// reports exists / is-a-dir / writable / free space, a plain-language problem
// list, and a suggested fix. It NEVER creates or modifies the path (repair is a
// later, explicit action). Drop it under a directory input to give the operator
// a live "is this path usable" signal before they save.

function humanBytes(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return "unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(v >= 10 || u === 0 ? 0 : 1)} ${units[u]}`;
}

export interface PathValidatorProps {
  path: string;
  /** Debounce before validating, ms (default 400). */
  debounceMs?: number;
  className?: string;
}

export function PathValidator({
  path,
  debounceMs = 400,
  className,
}: PathValidatorProps) {
  const [debounced, setDebounced] = useState(path);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(path), debounceMs);
    return () => clearTimeout(id);
  }, [path, debounceMs]);

  const trimmed = debounced.trim();
  const { data, isLoading, isError } = useQuery<StorageValidateResponse>({
    queryKey: ["storage-validate", trimmed],
    queryFn: ({ signal }) =>
      apiGet<StorageValidateResponse>(
        `/api/storage/validate?path=${encodeURIComponent(trimmed)}`,
        signal,
      ),
    enabled: trimmed.length > 0,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  if (trimmed.length === 0) return null;

  if (isLoading) {
    return (
      <p
        className={cn("flex items-center gap-1.5 text-xs text-ink-3", className)}
        aria-busy
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        Checking path…
      </p>
    );
  }

  if (isError || !data) {
    return (
      <p className={cn("text-xs text-ink-3", className)}>
        Couldn't check this path.
      </p>
    );
  }

  const healthy = data.exists && data.is_dir && data.writable;

  return (
    <div className={cn("text-xs", className)} role="status" aria-live="polite">
      <p
        className={cn(
          "flex items-center gap-1.5 font-medium",
          healthy ? "text-green" : "text-amber-dim",
        )}
      >
        {healthy ? (
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        )}
        {healthy
          ? `Writable · ${humanBytes(data.free_bytes)} free`
          : "Path needs attention"}
      </p>
      {data.problems.length > 0 && (
        <ul className="mt-1 list-inside list-disc space-y-0.5 text-ink-2">
          {data.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {data.suggested_fix && !healthy && (
        <p className="mt-1 text-ink-3">Fix: {data.suggested_fix}</p>
      )}
    </div>
  );
}
