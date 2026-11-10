"use client";

/**
 * Branding context: the pharmacy's public identity (name, tagline, logos) and the effective appearance
 * (global defaults merged with the signed-in user's own choices), applied to <html> as theme variables.
 * Safe defaults are used until the server answers, so the app renders even before branding is configured.
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { ApiError, fetchJson } from "./api";
import { Appearance, DEFAULT_APPEARANCE, applyTheme, buildThemeVars, mergeAppearance, resolveMode } from "./theme";

export type AssetKind = "logo" | "logo_dark" | "logo_compact" | "favicon";
export const ASSET_KINDS: AssetKind[] = ["logo", "logo_dark", "logo_compact", "favicon"];

export interface PublicBranding {
  app_name: string;
  short_name: string;
  tagline: string;
  login_welcome: string;
  logo_fit: "contain" | "cover";
  text_only: boolean;
  appearance: Appearance;
  assets: Record<AssetKind, string | null>;
}

export interface MyAppearance {
  defaults: Appearance;
  override: Partial<Appearance>;
  effective: Appearance;
  customized: boolean;
}

export const DEFAULT_BRANDING: PublicBranding = {
  app_name: "My Pharmacy", short_name: "My Pharmacy", tagline: "Pharmacy billing, stock & accounts",
  login_welcome: "Welcome back. Sign in to continue.", logo_fit: "contain", text_only: false,
  appearance: DEFAULT_APPEARANCE, assets: { logo: null, logo_dark: null, logo_compact: null, favicon: null },
};

export const assetUrl = (kind: AssetKind, version: string | null | undefined) =>
  version ? `/api/v1/branding/assets/${kind}?v=${encodeURIComponent(version)}` : null;

const PUBLIC_ROUTES = ["/login", "/setup"];

interface BrandingCtx {
  brand: PublicBranding;
  appearance: Appearance;           // what is applied right now
  mine: MyAppearance | null;        // null when signed out (or on the login page)
  mode: "light" | "dark";
  refresh: () => Promise<void>;
  savePersonal: (patch: Partial<Appearance> | Record<string, unknown>, replace?: boolean) => Promise<MyAppearance>;
  resetPersonal: () => Promise<void>;
  setPreview: (a: Appearance | null) => void;
}

const Ctx = createContext<BrandingCtx | null>(null);

function useSystemDark() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark;
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const isPublic = PUBLIC_ROUTES.some((r) => pathname.startsWith(r)) || pathname.includes("/print");
  const [brand, setBrand] = useState<PublicBranding>(DEFAULT_BRANDING);
  const [mine, setMine] = useState<MyAppearance | null>(null);
  const [preview, setPreview] = useState<Appearance | null>(null);
  const systemDark = useSystemDark();
  const loadedFor = useRef<string | null>(null);

  const loadPublic = useCallback(async () => {
    try {
      const b = await fetchJson<PublicBranding>("/api/v1/branding/public");
      setBrand({ ...DEFAULT_BRANDING, ...b, appearance: mergeAppearance(DEFAULT_APPEARANCE, b.appearance || {}) });
    } catch { /* keep defaults: the app must render even if branding cannot be loaded */ }
  }, []);

  const loadMine = useCallback(async () => {
    try {
      setMine(await fetchJson<MyAppearance>("/api/v1/me/appearance"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMine(null);
    }
  }, []);

  useEffect(() => { loadPublic(); }, [loadPublic]);

  // Load the user's own appearance once per sign-in (pages behind the login), and forget it on public pages.
  useEffect(() => {
    if (PUBLIC_ROUTES.some((r) => pathname.startsWith(r))) { loadedFor.current = null; setMine(null); return; }
    if (loadedFor.current === null) { loadedFor.current = pathname; loadMine(); }
  }, [pathname, loadMine]);

  const appearance = useMemo<Appearance>(() => {
    if (preview) return preview;
    if (mine && !PUBLIC_ROUTES.some((r) => pathname.startsWith(r))) return mergeAppearance(brand.appearance, mine.override);
    return brand.appearance;
  }, [preview, mine, brand.appearance, pathname]);

  const mode = resolveMode(appearance.mode, systemDark);

  useEffect(() => {
    if (isPublic && pathname.includes("/print")) return;        // print pages keep white paper colours
    applyTheme({ vars: buildThemeVars(appearance, mode), mode, density: appearance.density });
  }, [appearance, mode, isPublic, pathname]);

  // Browser tab title and favicon follow the shared identity
  useEffect(() => {
    if (typeof document === "undefined") return;
    const page = document.title.includes("|") ? document.title.split("|")[0].trim() : "";
    document.title = page && page !== brand.app_name && !page.startsWith("Pharmacy ERP") ? `${page} | ${brand.app_name}` : brand.app_name;
    const href = assetUrl("favicon", brand.assets.favicon) || assetUrl("logo_compact", brand.assets.logo_compact);
    let link = document.querySelector<HTMLLinkElement>("link[rel='icon'][data-brand]");
    if (href) {
      if (!link) {
        link = document.createElement("link");
        link.rel = "icon";
        link.dataset.brand = "1";
        document.head.appendChild(link);
      }
      link.href = href;
    } else if (link) {
      link.remove();
    }
  }, [brand, pathname]);

  const refresh = useCallback(async () => { await Promise.all([loadPublic(), loadMine()]); }, [loadPublic, loadMine]);

  const savePersonal = useCallback(async (patch: Partial<Appearance> | Record<string, unknown>, replace = false) => {
    const out = await fetchJson<MyAppearance>("/api/v1/me/appearance", { method: replace ? "PUT" : "PATCH", body: JSON.stringify(patch) });
    setMine(out);
    return out;
  }, []);

  const resetPersonal = useCallback(async () => {
    setMine(await fetchJson<MyAppearance>("/api/v1/me/appearance", { method: "DELETE" }));
  }, []);

  const value = useMemo(() => ({ brand, appearance, mine, mode, refresh, savePersonal, resetPersonal, setPreview }),
    [brand, appearance, mine, mode, refresh, savePersonal, resetPersonal]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBranding(): BrandingCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useBranding must be used inside BrandingProvider");
  return ctx;
}

/** Logo with graceful fallback: dark-mode logo when available, compact logo for tight spaces, else initials. */
export function BrandLogo({ variant = "full", size = 40, className = "", brandOverride, modeOverride }: {
  variant?: "full" | "compact";
  size?: number;
  className?: string;
  brandOverride?: PublicBranding;
  modeOverride?: "light" | "dark";
}) {
  const ctx = useBranding();
  const brand = brandOverride || ctx.brand;
  const mode = modeOverride || ctx.mode;
  const [failed, setFailed] = useState<string | null>(null);
  const a = brand.assets;
  const pick: [AssetKind, string | null][] = variant === "compact"
    ? [["logo_compact", a.logo_compact], ...(mode === "dark" ? [["logo_dark", a.logo_dark] as [AssetKind, string | null]] : []), ["logo", a.logo]]
    : [...(mode === "dark" ? [["logo_dark", a.logo_dark] as [AssetKind, string | null]] : []), ["logo", a.logo], ["logo_compact", a.logo_compact]];
  const chosen = brand.text_only ? undefined : pick.find(([, v]) => v);
  const src = chosen ? assetUrl(chosen[0], chosen[1]) : null;
  if (src && failed !== src) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={src} alt={`${brand.app_name} logo`} onError={() => setFailed(src)}
        style={{ height: size, width: variant === "compact" ? size : "auto", maxWidth: variant === "compact" ? size : size * 4, objectFit: brand.logo_fit }}
        className={`shrink-0 ${className}`} />
    );
  }
  const initials = brand.short_name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "Rx";
  return (
    <div aria-hidden="true" style={{ width: size, height: size, fontSize: size * 0.38 }}
      className={`shrink-0 rounded-xl bg-emerald-500 text-[color:var(--app-on-primary)] font-bold flex items-center justify-center ${className}`}>
      {initials}
    </div>
  );
}
