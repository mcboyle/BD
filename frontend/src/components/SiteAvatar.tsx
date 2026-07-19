import { cn } from "@/lib/utils";

// Site avatar — colored square chip with the first letter of the
// site name. Used in by-site lists, attention rows, queue rows.
// Color comes from the backend's deterministic palette so the same
// site always wears the same color (mockup signature).

export interface SiteAvatarProps {
  name: string;
  color: string;
  size?: "sm" | "md";
  className?: string;
}

export function SiteAvatar({
  name,
  color,
  size = "md",
  className,
}: SiteAvatarProps) {
  const letter = (name || "?").charAt(0).toUpperCase();
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center rounded-md font-semibold text-white",
        size === "sm" ? "h-6 w-6 text-xs" : "h-8 w-8 text-sm",
        className,
      )}
      style={{ backgroundColor: color }}
      aria-hidden
    >
      {letter}
    </span>
  );
}
