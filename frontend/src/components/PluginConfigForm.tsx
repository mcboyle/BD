import { cn } from "@/lib/utils";

// v3.66.498 O1 — config-schema -> auto-GUI. Presentational: the field list is the
// normalized form model from /api/plugins/config `schemas` (server-side
// plugins.plugin_config_schemas), so a plugin gets a config form with zero
// per-plugin UI code. Values + onChange are owned by the caller (Maintenance).

export interface PluginConfigField {
  name: string;
  type: "text" | "number" | "checkbox" | "select";
  label: string;
  default?: unknown;
  required: boolean;
  enum: string[];
  help?: string;
}

export type PluginConfigValues = Record<string, string | number | boolean>;

function fieldValue(
  field: PluginConfigField,
  values: PluginConfigValues,
): string | number | boolean {
  const v = values[field.name];
  if (v !== undefined) return v;
  if (field.default !== undefined && field.default !== null) {
    return field.default as string | number | boolean;
  }
  return field.type === "checkbox" ? false : "";
}

export function PluginConfigForm({
  fields,
  values,
  onChange,
}: {
  fields: PluginConfigField[];
  values: PluginConfigValues;
  onChange: (name: string, value: string | number | boolean) => void;
}) {
  if (fields.length === 0) {
    return <p className="text-sm text-muted-foreground">No configurable options.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {fields.map((f) => {
        const id = `plugincfg-${f.name}`;
        const val = fieldValue(f, values);
        return (
          <div key={f.name} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-sm font-medium">
              {f.label}
              {f.required ? <span className="text-destructive"> *</span> : null}
            </label>
            {f.type === "checkbox" ? (
              <input
                id={id}
                type="checkbox"
                checked={Boolean(val)}
                onChange={(e) => onChange(f.name, e.target.checked)}
              />
            ) : f.type === "select" ? (
              <select
                id={id}
                className={cn("rounded border px-2 py-1 text-sm")}
                value={String(val)}
                onChange={(e) => onChange(f.name, e.target.value)}
              >
                {f.enum.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                type={f.type === "number" ? "number" : "text"}
                className={cn("rounded border px-2 py-1 text-sm")}
                value={String(val)}
                onChange={(e) =>
                  onChange(
                    f.name,
                    f.type === "number" ? Number(e.target.value) : e.target.value,
                  )
                }
              />
            )}
            {f.help ? (
              <span className="text-xs text-muted-foreground">{f.help}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
