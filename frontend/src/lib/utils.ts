import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn helper — combine class names and let tailwind-merge
// dedupe conflicts (e.g. `p-4` overriding `p-2`). Imported by every
// component file under src/components/ui/.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
