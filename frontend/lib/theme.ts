/**
 * Theme engine.
 *
 * The UI is written with Tailwind palette classes (bg-slate-950, text-emerald-400, ...). Tailwind v4 compiles those
 * to CSS variables (--color-slate-950 ...), so a theme is applied by re-pointing those variables at runtime:
 *   slate   -> the theme's neutral surfaces/text (inverted for light mode)
 *   emerald -> primary brand colour      teal/cyan -> secondary      violet/purple/indigo/fuchsia -> accent
 *   sky/blue -> info      amber/yellow -> warning      rose/red -> error      green -> success
 * Every screen therefore follows the theme without per-page colour code. Print documents opt out (printResetVars).
 * Custom colours are validated as #RRGGBB on the server and here before they reach CSS.
 */
import { PALETTE, SHADES, Shade } from "./palette";

export type Mode = "light" | "dark" | "system";
export type ColorKey =
  | "primary" | "secondary" | "accent" | "background" | "surface" | "sidebar"
  | "text" | "muted" | "border" | "success" | "warning" | "error" | "info";

export interface Appearance {
  preset: string;
  mode: Mode;
  colors: Partial<Record<ColorKey, string>>;
  density: "comfortable" | "compact";
  nav: "sidebar" | "top";
  sidebar_collapsed: boolean;
  dashboard_hidden: string[];
  dashboard_order: string[];
}

export const DEFAULT_APPEARANCE: Appearance = {
  preset: "emerald", mode: "light", colors: {}, density: "comfortable", nav: "sidebar",
  sidebar_collapsed: false, dashboard_hidden: [], dashboard_order: [],
};

type Scale = Record<Shade, string>;

export interface Preset {
  id: string;
  name: string;
  description: string;
  primary: string;           // palette family or #hex
  secondary: string;
  accent: string;
  neutral: string;           // palette family for surfaces and text
  tint?: number;             // % of the primary colour mixed into dark surfaces
  mode?: "light" | "dark";   // the mode this preset is designed for (applied when the preset is picked)
  contrast?: boolean;        // high-contrast surfaces and text
  primaryLight?: string;     // primary override in light mode
  swatch: [string, string, string];
}

export const PRESETS: Preset[] = [
  { id: "emerald", name: "Emerald Green + White", description: "Fresh pharmacy green on clean white (default)", primary: "emerald", secondary: "teal", accent: "violet", neutral: "slate", mode: "light", swatch: ["#10b981", "#ffffff", "#0f172a"] },
  { id: "blue", name: "Blue + White", description: "Calm clinical blue", primary: "blue", secondary: "sky", accent: "violet", neutral: "slate", mode: "light", swatch: ["#3b82f6", "#ffffff", "#0f172a"] },
  { id: "teal", name: "Teal + White", description: "Balanced teal and white", primary: "teal", secondary: "cyan", accent: "indigo", neutral: "slate", mode: "light", swatch: ["#14b8a6", "#ffffff", "#0f172a"] },
  { id: "purple", name: "Purple + White", description: "Modern violet accents", primary: "violet", secondary: "fuchsia", accent: "sky", neutral: "zinc", mode: "light", swatch: ["#8b5cf6", "#ffffff", "#18181b"] },
  { id: "slate", name: "Slate / Neutral", description: "Understated graphite, no strong colour", primary: "#475569", secondary: "gray", accent: "sky", neutral: "gray", mode: "light", swatch: ["#475569", "#ffffff", "#111827"] },
  { id: "rose", name: "Rose / Burgundy", description: "Warm burgundy and rose", primary: "#be123c", secondary: "rose", accent: "amber", neutral: "stone", mode: "light", swatch: ["#be123c", "#ffffff", "#1c1917"] },
  { id: "high_contrast", name: "High contrast", description: "Maximum legibility for accessibility", primary: "#facc15", primaryLight: "#0b3d91", secondary: "#22d3ee", accent: "#f472b6", neutral: "neutral", contrast: true, mode: "dark", swatch: ["#facc15", "#000000", "#ffffff"] },
  { id: "dark_emerald", name: "Dark Emerald", description: "Deep green-tinted dark surfaces", primary: "emerald", secondary: "teal", accent: "violet", neutral: "slate", tint: 7, mode: "dark", swatch: ["#10b981", "#04140f", "#e2e8f0"] },
  { id: "dark_blue", name: "Dark Blue", description: "Midnight blue dark mode", primary: "blue", secondary: "sky", accent: "violet", neutral: "slate", tint: 8, mode: "dark", swatch: ["#3b82f6", "#050b1d", "#e2e8f0"] },
  { id: "dark_neutral", name: "Dark Neutral", description: "Charcoal greys with a subtle accent", primary: "#a1a1aa", secondary: "zinc", accent: "sky", neutral: "neutral", mode: "dark", swatch: ["#a1a1aa", "#0a0a0a", "#e5e5e5"] },
];

export const COLOR_LABELS: Record<ColorKey, string> = {
  primary: "Primary brand", secondary: "Secondary brand", accent: "Accent", background: "Page background",
  surface: "Cards & panels", sidebar: "Sidebar", text: "Text", muted: "Muted text", border: "Borders",
  success: "Success", warning: "Warning", error: "Error", info: "Information",
};

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHex = (v: unknown): v is string => typeof v === "string" && HEX.test(v);

export function presetById(id: string): Preset {
  return PRESETS.find((p) => p.id === id) || PRESETS[0];
}

/** A full 50–950 scale from one colour (used for custom colours and hex presets). */
function derived(hex: string): Scale {
  const tint = (p: number) => `color-mix(in oklab, ${hex} ${p}%, white)`;
  const shade = (p: number) => `color-mix(in oklab, ${hex} ${p}%, black)`;
  return { 50: tint(8), 100: tint(16), 200: tint(30), 300: tint(50), 400: tint(75), 500: hex, 600: shade(85), 700: shade(70), 800: shade(55), 900: shade(42), 950: shade(28) };
}

function scaleOf(spec: string): Scale {
  return isHex(spec) ? derived(spec) : PALETTE[spec] || PALETTE.slate;
}

// In light mode a class written for dark backgrounds (text-emerald-300, bg-emerald-950) is re-pointed to the shade
// that has the same role on white.
const LIGHT_COLOR: Record<Shade, Shade> = { 50: 950, 100: 900, 200: 800, 300: 800, 400: 700, 500: 600, 600: 700, 700: 800, 800: 200, 900: 100, 950: 50 };

function neutralScale(p: Preset, light: boolean): Record<Shade, string> & { white: string } {
  const n = scaleOf(p.neutral);
  if (p.contrast) {
    return light
      ? { 50: "#000", 100: "#000", 200: "#000", 300: "#0a0a0a", 400: "#171717", 500: "#262626", 600: "#404040", 700: "#525252", 800: "#737373", 900: "#ffffff", 950: "#ffffff", white: "#000000" }
      : { 50: "#fff", 100: "#fff", 200: "#fff", 300: "#fff", 400: "#f5f5f5", 500: "#e5e5e5", 600: "#d4d4d4", 700: "#a3a3a3", 800: "#737373", 900: "#000000", 950: "#000000", white: "#ffffff" };
  }
  if (light) {
    return { 50: n[950], 100: n[900], 200: n[800], 300: n[700], 400: n[600], 500: n[500], 600: n[400], 700: n[300], 800: n[200], 900: "#ffffff", 950: n[50], white: n[900] };
  }
  const tinted = { ...n } as Scale;
  if (p.tint) {
    const base = scaleOf(p.primary)[500];
    for (const s of [700, 800, 900, 950] as Shade[]) tinted[s] = `color-mix(in oklab, ${n[s]} ${100 - p.tint}%, ${base})`;
  }
  return { ...tinted, white: "#ffffff" };
}

export function resolveMode(mode: Mode, systemDark: boolean): "light" | "dark" {
  return mode === "system" ? (systemDark ? "dark" : "light") : mode;
}

const FAMILY_ROLES: { role: ColorKey | "secondary" | "accent"; families: string[]; preset: (p: Preset) => string }[] = [
  { role: "primary", families: ["emerald"], preset: (p) => p.primary },
  { role: "secondary", families: ["teal", "cyan"], preset: (p) => p.secondary },
  { role: "accent", families: ["violet", "purple", "indigo", "fuchsia"], preset: (p) => p.accent },
  { role: "info", families: ["sky", "blue"], preset: () => "" },
  { role: "warning", families: ["amber", "yellow"], preset: () => "" },
  { role: "error", families: ["rose", "red"], preset: () => "" },
  { role: "success", families: ["green"], preset: () => "" },
];

/** Every CSS variable a theme sets on <html>. */
export function buildThemeVars(a: Appearance, resolved: "light" | "dark"): Record<string, string> {
  const p = presetById(a.preset);
  const light = resolved === "light";
  const colors = Object.fromEntries(Object.entries(a.colors || {}).filter(([, v]) => isHex(v))) as Partial<Record<ColorKey, string>>;
  const vars: Record<string, string> = {};
  const put = (family: string, scale: Scale, remap: boolean) => {
    for (const s of SHADES) vars[`--color-${family}-${s}`] = scale[remap && light ? LIGHT_COLOR[s] : s];
  };

  for (const { role, families, preset } of FAMILY_ROLES) {
    let spec = colors[role as ColorKey] || preset(p);
    if (role === "primary" && light && p.primaryLight && !colors.primary) spec = p.primaryLight;
    for (const fam of families) put(fam, spec ? scaleOf(spec) : PALETTE[fam], true);
  }

  const n = neutralScale(p, light);
  for (const s of SHADES) vars[`--color-slate-${s}`] = n[s];
  vars["--color-white"] = n.white;
  vars["--color-black"] = "#000000";     // real black (overlays, paper previews) in every theme

  if (colors.background) vars["--color-slate-950"] = colors.background;
  if (colors.surface) vars["--color-slate-900"] = colors.surface;
  if (colors.border) { vars["--color-slate-800"] = colors.border; vars["--color-slate-700"] = colors.border; }
  if (colors.text) { vars["--color-white"] = colors.text; vars["--color-slate-100"] = colors.text; vars["--color-slate-200"] = colors.text; }
  if (colors.muted) { vars["--color-slate-400"] = colors.muted; vars["--color-slate-500"] = colors.muted; }
  vars["--app-sidebar"] = colors.sidebar || (light ? "#ffffff" : "var(--color-slate-900)");
  vars["--app-on-primary"] = p.contrast && !light ? "#000000" : "#ffffff";
  return vars;
}

/** Original Tailwind colours, for print documents (white paper, black text) whatever the screen theme is. */
export function printResetVars(): Record<string, string> {
  const vars: Record<string, string> = { "--color-white": "#ffffff", "--color-black": "#000000" };
  for (const [fam, scale] of Object.entries(PALETTE)) for (const s of SHADES) vars[`--color-${fam}-${s}`] = scale[s];
  return vars;
}

// ── Contrast (WCAG) for custom colours ──────────────────────────────────────────
function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function contrastRatio(a: string, b: string): number | null {
  if (!isHex(a) || !isHex(b)) return null;
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

/** Warnings for custom colour combinations that would be hard to read. */
export function contrastWarnings(a: Appearance, resolved: "light" | "dark"): string[] {
  const c = a.colors || {};
  const bg = c.background || (resolved === "light" ? "#f8fafc" : "#020617");
  const surface = c.surface || (resolved === "light" ? "#ffffff" : "#0f172a");
  const out: string[] = [];
  const check = (fg: string | undefined, back: string, label: string, min: number) => {
    const r = fg ? contrastRatio(fg, back) : null;
    if (r !== null && r < min) out.push(`${label} has a contrast of ${r.toFixed(1)}:1 (at least ${min}:1 is recommended).`);
  };
  check(c.text, bg, "Text on the page background", 4.5);
  check(c.text, surface, "Text on cards", 4.5);
  check(c.muted, surface, "Muted text on cards", 3);
  check(c.primary, "#ffffff", "White text on the primary colour", 3);
  return out;
}

export function mergeAppearance(defaults: Appearance, override: Partial<Appearance>): Appearance {
  return { ...DEFAULT_APPEARANCE, ...defaults, ...override, colors: override.colors ?? defaults.colors ?? {} };
}

// ── Applying and caching (the cache lets the next page load paint in the right theme before any request) ──
export const THEME_CACHE_KEY = "erp.theme.v1";

export interface AppliedTheme {
  vars: Record<string, string>;
  mode: "light" | "dark";
  density: string;
}

export function applyTheme(t: AppliedTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  // Switch colours instantly: without this every element with a colour transition fades to the new theme
  root.classList.add("theme-switching");
  for (const [k, v] of Object.entries(t.vars)) root.style.setProperty(k, v);
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
  root.dataset.mode = t.mode;
  root.dataset.density = t.density;
  root.style.colorScheme = t.mode;
  try { localStorage.setItem(THEME_CACHE_KEY, JSON.stringify(t)); } catch { /* storage unavailable: theme still applied */ }
}

/** Inline <head> script: re-applies the cached theme before first paint (no flash of the wrong theme). */
export const NO_FLASH_SCRIPT = `(function(){try{var t=JSON.parse(localStorage.getItem(${JSON.stringify(THEME_CACHE_KEY)})||"null");if(!t||!t.vars)return;var r=document.documentElement;for(var k in t.vars){if(/^--[a-z0-9-]+$/.test(k))r.style.setProperty(k,String(t.vars[k]))}r.dataset.mode=t.mode==="dark"?"dark":"light";r.dataset.density=t.density==="compact"?"compact":"comfortable";r.style.colorScheme=r.dataset.mode}catch(e){}})();`;
