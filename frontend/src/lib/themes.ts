// v3.64.x desktop-shell + themes restoration.
//
// 31 named themes carried forward from the legacy `/` UI (the dropdown
// the operator referred to as "what happened to the themes"). Each
// theme overrides the SPA's CSS-variable palette in index.css. The
// `is_dark` flag tells the rest of the app whether to add the Tailwind
// `.dark` class to <html> — Tailwind's dark-mode variant uses that
// class, so Components that key off `dark:` utilities (cards, focus
// rings, etc.) need it set correctly even when the theme is named.
//
// Sourced from `bulk_downloader/static/app.css` lines ~12, 52, and
// 1824–2810 (the `[data-theme="…"]` blocks). The mapping from legacy
// var names to SPA var names is:
//
//   bg0 → bg                bg1 → surface              bg2 → surface-2
//   text → ink              muted2 → ink-2             muted → ink-3
//   primary → primary       primary-bg → primary-soft
//   green → green           gbg → green-soft
//   amber → amber           abg → amber-soft
//   red → red               rbg → red-soft
//
// `hairline` is derived from the legacy `border` value at α 0.45.
// SPA tokens the legacy palette didn't expose (--bg3, --border2,
// --shadows, --glow-primary, --font, --mono) are simply not ported —
// the SPA's index.css handles them generically.

export interface ThemeDef {
  /** Display name shown in the picker. */
  label: string;
  /** CSS variables to apply to <html>. */
  vars: Record<string, string>;
  /** Whether the theme is dark (drives Tailwind `.dark` class). */
  isDark: boolean;
}

// Special mode keys. The string union accepted by useTheme is
// `system | ThemeId`. `system` resolves to dark/light at runtime via
// prefers-color-scheme.
export type ThemeMode = "system" | ThemeId;

// All theme IDs as a const tuple so we can derive the union type AND
// iterate at runtime for the picker.
export const THEME_IDS = [
  "dark", "light",
  "console", "editorial", "midnight", "nord", "solarized", "paper",
  "monochrome", "terminal", "ember", "forest", "rose", "crimson",
  "daylight", "neon", "dracula", "gruvbox", "tokyo", "mocha",
  "latte", "onedark", "monokai", "everforest", "ayu", "sepia",
  "steel", "mint", "wine", "synthwave", "carbon",
] as const;

export type ThemeId = (typeof THEME_IDS)[number];

export const THEMES: Record<ThemeId, ThemeDef> = {
  dark: {
    label: "Default dark",
    isDark: true,
    vars: {
      bg: "#050608", surface: "#08090d", "surface-2": "#0c0d12",
      ink: "#e6e6e7", "ink-2": "#a3aab8", "ink-3": "#7a8194",
      primary: "#6e79e3", "primary-soft": "#1a1340",
      green: "#10b981", "green-soft": "#064e3b",
      amber: "#f59e0b", "amber-soft": "#451a03",
      red: "#ef4444", "red-soft": "#450a0a",
      hairline: "rgba(28,29,34,0.45)",
    },
  },
  light: {
    label: "Default light",
    isDark: false,
    vars: {
      bg: "#eaeef3", surface: "#ffffff", "surface-2": "#f6f9fc",
      ink: "#0a2540", "ink-2": "#4b5563", "ink-3": "#6b7280",
      primary: "#635bff", "primary-soft": "#f0effe",
      green: "#059669", "green-soft": "#d1fae5",
      amber: "#d97706", "amber-soft": "#fef3c7",
      red: "#dc2626", "red-soft": "#fee2e2",
      hairline: "rgba(227,232,238,0.45)",
    },
  },
  console: {
    label: "Console", isDark: true,
    vars: {
      bg: "#0c0d0f", surface: "#141518", "surface-2": "#1c1d21",
      ink: "#d6d8dd", "ink-2": "#a7a9b2", "ink-3": "#7d7f88",
      primary: "#ffb020", "primary-soft": "#241a0d",
      green: "#9bd83a", "green-soft": "#2a341d",
      amber: "#ffb020", "amber-soft": "#3a2e19",
      red: "#ff5757", "red-soft": "#3a2022",
      hairline: "rgba(38,39,44,0.45)",
    },
  },
  editorial: {
    label: "Editorial", isDark: false,
    vars: {
      bg: "#f4f1ea", surface: "#fffdf8", "surface-2": "#f1ece0",
      ink: "#2a2622", "ink-2": "#5c5650", "ink-3": "#857d70",
      primary: "#bf4d2c", "primary-soft": "#f6e3da",
      green: "#5c7355", "green-soft": "#e8eae1",
      amber: "#b8893a", "amber-soft": "#f5eddd",
      red: "#bf4d2c", "red-soft": "#f6e4db",
      hairline: "rgba(230,224,210,0.45)",
    },
  },
  midnight: {
    label: "Midnight", isDark: true,
    vars: {
      bg: "#080b15", surface: "#10162a", "surface-2": "#1a2036",
      ink: "#e4e8f4", "ink-2": "#aab2d0", "ink-3": "#8b93b4",
      primary: "#34e3d4", "primary-soft": "#0e2b35",
      green: "#34e3d4", "green-soft": "#163745",
      amber: "#ffc24b", "amber-soft": "#36322f",
      red: "#ff6b8a", "red-soft": "#362439",
      hairline: "rgba(34,42,68,0.45)",
    },
  },
  nord: {
    label: "Nord", isDark: true,
    vars: {
      bg: "#2e3440", surface: "#3b4252", "surface-2": "#434c5e",
      ink: "#eceff4", "ink-2": "#c8ced8", "ink-3": "#a7b0c0",
      primary: "#88c0d0", "primary-soft": "#3b4a5a",
      green: "#a3be8c", "green-soft": "#4c565b",
      amber: "#ebcb8b", "amber-soft": "#57585b",
      red: "#bf616a", "red-soft": "#504756",
      hairline: "rgba(67,76,94,0.45)",
    },
  },
  solarized: {
    label: "Solarized", isDark: true,
    vars: {
      bg: "#002b36", surface: "#073642", "surface-2": "#0b4856",
      ink: "#a7b6b6", "ink-2": "#93a1a1", "ink-3": "#7a9091",
      primary: "#268bd2", "primary-soft": "#0a3d4a",
      green: "#859900", "green-soft": "#1b4637",
      amber: "#b58900", "amber-soft": "#234337",
      red: "#dc322f", "red-soft": "#29353f",
      hairline: "rgba(12,74,88,0.45)",
    },
  },
  paper: {
    label: "Paper", isDark: false,
    vars: {
      bg: "#ffffff", surface: "#ffffff", "surface-2": "#f3f5f8",
      ink: "#14171f", "ink-2": "#4b5563", "ink-3": "#6b7280",
      primary: "#2563eb", "primary-soft": "#eef2ff",
      green: "#059669", "green-soft": "#dcf0ea",
      amber: "#d97706", "amber-soft": "#faecdc",
      red: "#dc2626", "red-soft": "#fae1e1",
      hairline: "rgba(231,233,238,0.45)",
    },
  },
  monochrome: {
    label: "Monochrome", isDark: true,
    vars: {
      bg: "#0f0f0f", surface: "#181818", "surface-2": "#242424",
      ink: "#ededed", "ink-2": "#b4b4b4", "ink-3": "#8a8a8a",
      primary: "#ededed", "primary-soft": "#2a2a2a",
      green: "#cdcdcd", "green-soft": "#353535",
      amber: "#9a9a9a", "amber-soft": "#2d2d2d",
      red: "#7a7a7a", "red-soft": "#282828",
      hairline: "rgba(38,38,38,0.45)",
    },
  },
  terminal: {
    label: "Terminal", isDark: true,
    vars: {
      bg: "#050a06", surface: "#08160b", "surface-2": "#0d2110",
      ink: "#5bff8f", "ink-2": "#42c46e", "ink-3": "#2f9a52",
      primary: "#33ff66", "primary-soft": "#0a2412",
      green: "#33ff66", "green-soft": "#0f3b1a",
      amber: "#ffe14d", "amber-soft": "#303616",
      red: "#ff5a5a", "red-soft": "#302118",
      hairline: "rgba(18,48,24,0.45)",
    },
  },
  ember: {
    label: "Ember", isDark: true,
    vars: {
      bg: "#14100e", surface: "#1f1813", "surface-2": "#2a201a",
      ink: "#f0e6dc", "ink-2": "#cdb9a6", "ink-3": "#b09a86",
      primary: "#ff7a4d", "primary-soft": "#2e1c12",
      green: "#8fbf6a", "green-soft": "#313321",
      amber: "#ffb547", "amber-soft": "#43311b",
      red: "#ff5d5d", "red-soft": "#43231f",
      hairline: "rgba(46,36,28,0.45)",
    },
  },
  forest: {
    label: "Forest", isDark: true,
    vars: {
      bg: "#0d1410", surface: "#131f18", "surface-2": "#1a2c20",
      ink: "#e3ede4", "ink-2": "#aec0b4", "ink-3": "#8fa896",
      primary: "#4ade80", "primary-soft": "#10240f",
      green: "#4ade80", "green-soft": "#1c3e29",
      amber: "#facc6b", "amber-soft": "#383b25",
      red: "#f87171", "red-soft": "#382c26",
      hairline: "rgba(30,51,38,0.45)",
    },
  },
  rose: {
    label: "Rosé Pine", isDark: true,
    vars: {
      bg: "#191724", surface: "#1f1d2e", "surface-2": "#26233a",
      ink: "#e0def4", "ink-2": "#b3aed0", "ink-3": "#908caa",
      primary: "#ebbcba", "primary-soft": "#2a2030",
      green: "#9ccfd8", "green-soft": "#333949",
      amber: "#f6c177", "amber-soft": "#41373a",
      red: "#eb6f92", "red-soft": "#402a3e",
      hairline: "rgba(42,39,64,0.45)",
    },
  },
  crimson: {
    label: "Crimson", isDark: true,
    vars: {
      bg: "#100808", surface: "#1b0e0e", "surface-2": "#261414",
      ink: "#f3e4e4", "ink-2": "#d0adad", "ink-3": "#b89494",
      primary: "#f5333f", "primary-soft": "#2a1010",
      green: "#4fb286", "green-soft": "#232821",
      amber: "#f5a623", "amber-soft": "#3e2611",
      red: "#f5333f", "red-soft": "#3e1416",
      hairline: "rgba(46,24,24,0.45)",
    },
  },
  daylight: {
    label: "Daylight", isDark: false,
    vars: {
      bg: "#f3f5f8", surface: "#ffffff", "surface-2": "#eef1f5",
      ink: "#1b2430", "ink-2": "#3b4554", "ink-3": "#5e6878",
      primary: "#0e9e96", "primary-soft": "#e0f4f2",
      green: "#0e9e96", "green-soft": "#ddf1f0",
      amber: "#d98c1f", "amber-soft": "#faefe0",
      red: "#d6453f", "red-soft": "#f9e5e4",
      hairline: "rgba(226,230,236,0.45)",
    },
  },
  neon: {
    label: "Neon", isDark: true,
    vars: {
      bg: "#07060d", surface: "#110d1f", "surface-2": "#1a1430",
      ink: "#ece6ff", "ink-2": "#b6abdc", "ink-3": "#9588c4",
      primary: "#ff2bd1", "primary-soft": "#2a0a24",
      green: "#2bf0ff", "green-soft": "#153143",
      amber: "#ffd23f", "amber-soft": "#372d24",
      red: "#ff3b6b", "red-soft": "#37142b",
      hairline: "rgba(36,26,68,0.45)",
    },
  },
  dracula: {
    label: "Dracula", isDark: true,
    vars: {
      bg: "#282a36", surface: "#2d2f3d", "surface-2": "#383a4d",
      ink: "#f8f8f2", "ink-2": "#c4c8e0", "ink-3": "#9ea3c4",
      primary: "#bd93f9", "primary-soft": "#2a2440",
      green: "#50fa7b", "green-soft": "#334f47",
      amber: "#f1fa8c", "amber-soft": "#4c4f4a",
      red: "#ff5555", "red-soft": "#4f3541",
      hairline: "rgba(56,58,77,0.45)",
    },
  },
  gruvbox: {
    label: "Gruvbox", isDark: true,
    vars: {
      bg: "#282828", surface: "#32302f", "surface-2": "#3c3836",
      ink: "#ebdbb2", "ink-2": "#bdae93", "ink-3": "#a89984",
      primary: "#fabd2f", "primary-soft": "#3a3024",
      green: "#b8bb26", "green-soft": "#47462e",
      amber: "#fe8019", "amber-soft": "#533d2b",
      red: "#fb4934", "red-soft": "#523430",
      hairline: "rgba(60,56,54,0.45)",
    },
  },
  tokyo: {
    label: "Tokyo Night", isDark: true,
    vars: {
      bg: "#1a1b26", surface: "#1f2335", "surface-2": "#292e42",
      ink: "#c0caf5", "ink-2": "#a9b1d6", "ink-3": "#828bb8",
      primary: "#7aa2f7", "primary-soft": "#1c2336",
      green: "#9ece6a", "green-soft": "#333e3d",
      amber: "#e0af68", "amber-soft": "#3e393d",
      red: "#f7768e", "red-soft": "#423043",
      hairline: "rgba(41,46,66,0.45)",
    },
  },
  mocha: {
    label: "Catppuccin Mocha", isDark: true,
    vars: {
      bg: "#1e1e2e", surface: "#28283c", "surface-2": "#313244",
      ink: "#cdd6f4", "ink-2": "#bac2de", "ink-3": "#a6adc8",
      primary: "#cba6f7", "primary-soft": "#2a2440",
      green: "#a6e3a1", "green-soft": "#3c464c",
      amber: "#f9e2af", "amber-soft": "#49464e",
      red: "#f38ba8", "red-soft": "#48384d",
      hairline: "rgba(49,50,68,0.45)",
    },
  },
  latte: {
    label: "Catppuccin Latte", isDark: false,
    vars: {
      bg: "#eff1f5", surface: "#ffffff", "surface-2": "#e6e9ef",
      ink: "#4c4f69", "ink-2": "#5c5f77", "ink-3": "#6c6f85",
      primary: "#8839ef", "primary-soft": "#f0e7fc",
      green: "#40a02b", "green-soft": "#e4f2e1",
      amber: "#df8e1d", "amber-soft": "#fbefdf",
      red: "#d20f39", "red-soft": "#f9dde3",
      hairline: "rgba(220,224,232,0.45)",
    },
  },
  onedark: {
    label: "One Dark", isDark: true,
    vars: {
      bg: "#282c34", surface: "#2f343d", "surface-2": "#3a3f4b",
      ink: "#d7dae0", "ink-2": "#b6bcc8", "ink-3": "#9aa0ac",
      primary: "#61afef", "primary-soft": "#22303f",
      green: "#98c379", "green-soft": "#404b47",
      amber: "#e5c07b", "amber-soft": "#4c4a47",
      red: "#e06c75", "red-soft": "#4b3d46",
      hairline: "rgba(58,63,75,0.45)",
    },
  },
  monokai: {
    label: "Monokai", isDark: true,
    vars: {
      bg: "#272822", surface: "#31322a", "surface-2": "#3b3c33",
      ink: "#f8f8f2", "ink-2": "#c4bfa8", "ink-3": "#a59f85",
      primary: "#a6e22e", "primary-soft": "#2c3320",
      green: "#a6e22e", "green-soft": "#444e2b",
      amber: "#fd971f", "amber-soft": "#524228",
      red: "#f92672", "red-soft": "#513036",
      hairline: "rgba(59,60,51,0.45)",
    },
  },
  everforest: {
    label: "Everforest", isDark: true,
    vars: {
      bg: "#2d353b", surface: "#343f44", "surface-2": "#3d484d",
      ink: "#d3c6aa", "ink-2": "#b8c0b4", "ink-3": "#9da9a0",
      primary: "#a7c080", "primary-soft": "#2e3a32",
      green: "#a7c080", "green-soft": "#46544e",
      amber: "#dbbc7f", "amber-soft": "#4f534d",
      red: "#e67e80", "red-soft": "#50494e",
      hairline: "rgba(61,72,77,0.45)",
    },
  },
  ayu: {
    label: "Ayu Mirage", isDark: true,
    vars: {
      bg: "#1f2430", surface: "#232834", "surface-2": "#2d3340",
      ink: "#cbccc6", "ink-2": "#b0b4ba", "ink-3": "#8a909c",
      primary: "#ffcc66", "primary-soft": "#33302a",
      green: "#a6cc70", "green-soft": "#38423e",
      amber: "#ffad66", "amber-soft": "#463d3c",
      red: "#f28779", "red-soft": "#44373f",
      hairline: "rgba(45,51,64,0.45)",
    },
  },
  sepia: {
    label: "Sepia", isDark: false,
    vars: {
      bg: "#f1e7d3", surface: "#f8f1e2", "surface-2": "#ece0c6",
      ink: "#433422", "ink-2": "#6a5942", "ink-3": "#8a7559",
      primary: "#a06a2c", "primary-soft": "#efe2c8",
      green: "#6b7d4f", "green-soft": "#e4e1cd",
      amber: "#b8893a", "amber-soft": "#efe2ca",
      red: "#a5482c", "red-soft": "#ecd9c9",
      hairline: "rgba(224,210,184,0.45)",
    },
  },
  steel: {
    label: "Steel", isDark: true,
    vars: {
      bg: "#0f1620", surface: "#162130", "surface-2": "#1f2d40",
      ink: "#dde6f0", "ink-2": "#aab8c8", "ink-3": "#8696aa",
      primary: "#3b82f6", "primary-soft": "#142840",
      green: "#22c55e", "green-soft": "#183b37",
      amber: "#f59e0b", "amber-soft": "#3a352a",
      red: "#ef4444", "red-soft": "#392733",
      hairline: "rgba(31,45,64,0.45)",
    },
  },
  mint: {
    label: "Mint", isDark: false,
    vars: {
      bg: "#eef6f1", surface: "#ffffff", "surface-2": "#e9f4ee",
      ink: "#16302a", "ink-2": "#3e5650", "ink-3": "#5a7268",
      primary: "#0f9d6b", "primary-soft": "#dcf3e9",
      green: "#0f9d6b", "green-soft": "#ddf1ea",
      amber: "#d98c1f", "amber-soft": "#faefe0",
      red: "#dc4b3f", "red-soft": "#fae6e4",
      hairline: "rgba(220,233,226,0.45)",
    },
  },
  wine: {
    label: "Wine", isDark: true,
    vars: {
      bg: "#1a0c12", surface: "#26121c", "surface-2": "#341a28",
      ink: "#f0e2e8", "ink-2": "#d4bbc6", "ink-3": "#bd9aa8",
      primary: "#d4a843", "primary-soft": "#2e1822",
      green: "#7ab38a", "green-soft": "#332c2e",
      amber: "#d4a843", "amber-soft": "#422a22",
      red: "#e0607a", "red-soft": "#441e2b",
      hairline: "rgba(58,30,44,0.45)",
    },
  },
  synthwave: {
    label: "Synthwave", isDark: true,
    vars: {
      bg: "#1a0b2e", surface: "#241241", "surface-2": "#321a55",
      ink: "#f4e9ff", "ink-2": "#cdb8e8", "ink-3": "#b39ad6",
      primary: "#ff4d8d", "primary-soft": "#2e1844",
      green: "#36e0c8", "green-soft": "#273357",
      amber: "#ffb43d", "amber-soft": "#472c40",
      red: "#ff4d6d", "red-soft": "#471b48",
      hairline: "rgba(58,33,104,0.45)",
    },
  },
  carbon: {
    label: "Carbon", isDark: true,
    vars: {
      bg: "#161616", surface: "#1c1c1c", "surface-2": "#262626",
      ink: "#f4f4f4", "ink-2": "#c6c6c6", "ink-3": "#a8a8a8",
      primary: "#0f62fe", "primary-soft": "#16213c",
      green: "#42be65", "green-soft": "#223628",
      amber: "#f1c21b", "amber-soft": "#3e371c",
      red: "#fa4d56", "red-soft": "#402425",
      hairline: "rgba(38,38,38,0.45)",
    },
  },
};

/** Resolve a ThemeMode (which may be "system") to a concrete ThemeId. */
export function resolveTheme(mode: ThemeMode, prefersDark: boolean): ThemeId {
  if (mode === "system") return prefersDark ? "dark" : "light";
  return mode;
}

/** Get the ThemeDef for a resolved ThemeId. */
export function themeFor(id: ThemeId): ThemeDef {
  return THEMES[id];
}

/** Type guard. */
export function isThemeMode(v: unknown): v is ThemeMode {
  if (v === "system") return true;
  if (typeof v !== "string") return false;
  return (THEME_IDS as readonly string[]).includes(v);
}
