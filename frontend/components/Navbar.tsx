"use client";

/**
 * Application shell navigation. Two styles, chosen per user in My Appearance:
 *   sidebar (default) — fixed, collapsible left sidebar on wide screens, drawer on small screens
 *   top               — the original horizontal bar
 * The links and the permission rules that decide which links a user sees are the same in both.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  Pill,
  Boxes,
  Truck,
  ShoppingCart,
  Database,
  LogOut,
  ScanBarcode,
  Receipt,
  UserCog,
  BookOpen,
  Wallet,
  ShieldCheck,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";
import { CurrentUser, api } from "@/lib/api";
import { BrandLogo, useBranding } from "@/lib/branding";

interface NavbarProps {
  user: CurrentUser | null;
  lowStockCount?: number;
  nearExpiryCount?: number;
}

interface NavItem { label: string; href: string; icon: LucideIcon; match?: string[]; badge?: number }

export function Navbar({ user, lowStockCount = 0, nearExpiryCount = 0 }: NavbarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { brand, appearance, mine, savePersonal } = useBranding();
  const [drawer, setDrawer] = useState(false);
  const sidebar = appearance.nav === "sidebar";
  const collapsed = sidebar && appearance.sidebar_collapsed;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.shell = sidebar ? (collapsed ? "sidebar-collapsed" : "sidebar") : "top";
    return () => { delete root.dataset.shell; };
  }, [sidebar, collapsed]);

  useEffect(() => { setDrawer(false); }, [pathname]);

  const handleLogout = async () => {
    try {
      await api.logout();
      router.push("/login");
    } catch {
      router.push("/login");
    }
  };

  const toggleCollapsed = () => {
    if (mine) savePersonal({ sidebar_collapsed: !collapsed }).catch(() => undefined);
  };

  const canSeeDashboard = user?.permissions.some((p) => ["settings.manage", "reports.view", "audit.view"].includes(p)) ?? false;
  const homeHref = canSeeDashboard ? "/dashboard" : (user?.permissions.includes("pos.access") ? "/pos" : (user?.permissions.includes("sales.view") ? "/sales" : "/medicines"));

  const navItems: NavItem[] = [
    ...(canSeeDashboard ? [{ label: "Dashboard", href: "/dashboard", icon: LayoutDashboard }] : []),
    ...(user?.permissions.includes("pos.access") ? [{ label: "POS", href: "/pos", icon: ScanBarcode }] : []),
    ...(user?.permissions.includes("sales.view")
      ? [{ label: "Sales", href: "/sales", icon: Receipt, match: ["/sales", "/customers", "/receivables", "/day-end", "/reports", "/settings/communications"] }]
      : []),
    ...(user?.permissions.includes("medicines.view") ? [{ label: "Medicines", href: "/medicines", icon: Pill }] : []),
    ...(user?.permissions.includes("stock.view")
      ? [{
          label: "Inventory",
          href: "/inventory",
          icon: Boxes,
          badge: lowStockCount + nearExpiryCount > 0 ? lowStockCount + nearExpiryCount : undefined,
        }]
      : []),
    ...((user?.permissions.includes("suppliers.manage") || user?.permissions.includes("purchases.view"))
      ? [{ label: "Suppliers", href: "/suppliers", icon: Truck }]
      : []),
    ...(user?.permissions.includes("purchases.view") ? [{ label: "Purchasing", href: "/purchasing", icon: ShoppingCart }] : []),
    ...(user?.permissions.includes("settings.manage") ? [{ label: "Masters & Tax", href: "/masters", icon: Database }] : []),
    ...(["supplier_payments.view", "purchases.invoice_finalize", "expenses.view", "expenses.create"].some((p) => user?.permissions.includes(p))
      ? [{
          label: "Payables",
          href: ["supplier_payments.view", "purchases.invoice_finalize"].some((p) => user?.permissions.includes(p)) ? "/payables" : "/expenses",
          icon: Wallet,
          match: ["/payables", "/expenses"],
        }]
      : []),
    ...(user?.permissions.includes("accounting.view") ? [{ label: "Accounts", href: "/accounting", icon: BookOpen }] : []),
    ...(user?.permissions.includes("users.manage") ? [{ label: "Users", href: "/users", icon: UserCog }] : []),
    ...(["settings.manage", "backups.manage", "audit.view"].some((p) => user?.permissions.includes(p))
      ? [{ label: "Admin", href: "/admin", icon: ShieldCheck, match: ["/admin", "/settings/branding"] }] : []),
  ];

  const isItemActive = (item: { href: string; match?: string[] }) =>
    (item.match || [item.href]).some((h) => pathname === h || (h !== "/dashboard" && pathname.startsWith(h) && !(h === "/settings" && pathname.startsWith("/settings/appearance"))));

  const badge = (item: NavItem) => item.badge !== undefined && item.badge > 0 && (
    <span className="ml-auto px-1.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px] font-bold border border-amber-500/30" aria-label={`${item.badge} alerts`}>
      {item.badge}
    </span>
  );

  // A wide logo (no compact mark uploaded) usually carries the name itself, so it is shown on its own;
  // otherwise the compact mark (or initials) sits next to the short name and tagline.
  const logoOnly = !brand.text_only && !!(brand.assets.logo || brand.assets.logo_dark) && !brand.assets.logo_compact;
  const brandBlock = (compact: boolean) => (
    <Link href={homeHref} className="flex items-center gap-3 group min-w-0" aria-label={`${brand.app_name} home`}>
      <BrandLogo variant={compact || !logoOnly ? "compact" : "full"} size={compact ? 36 : 38} className="transition-transform group-hover:scale-105" />
      {!compact && !logoOnly && (
        <div className="min-w-0">
          <div className="font-bold text-white text-base tracking-tight truncate">{brand.short_name}</div>
          {brand.tagline && <div className="text-[11px] text-slate-400 truncate">{brand.tagline}</div>}
        </div>
      )}
    </Link>
  );

  const userBlock = (compact: boolean) => (
    <div className={`flex items-center gap-2 ${compact ? "flex-col" : ""}`}>
      {!compact && (
        <div className="flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2 bg-slate-950/60 border border-slate-800 rounded-xl">
          <div className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <div className="text-xs font-semibold text-slate-200 leading-tight truncate">{user?.name || "Staff"}</div>
            <div className="text-[10px] text-slate-500 leading-tight truncate">{user?.role || ""}</div>
          </div>
        </div>
      )}
      <Link href="/settings/appearance" title="My appearance" aria-label="My appearance"
        className={`p-2.5 rounded-xl border transition-all ${pathname.startsWith("/settings/appearance") ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" : "text-slate-400 border-transparent hover:text-slate-200 hover:bg-slate-800/60"}`}>
        <Palette className="w-4 h-4" />
      </Link>
      <button onClick={handleLogout} title="Sign out" aria-label="Sign out"
        className="p-2.5 text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 rounded-xl transition-all cursor-pointer">
        <LogOut className="w-4 h-4" />
      </button>
    </div>
  );

  const verticalLinks = (compact: boolean) => (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {navItems.map((item) => {
        const Icon = item.icon;
        const active = isItemActive(item);
        return (
          <Link key={item.href} href={item.href} title={compact ? item.label : undefined} aria-label={compact ? item.label : undefined}
            aria-current={active ? "page" : undefined}
            className={`relative flex items-center gap-3 rounded-xl text-sm font-medium transition-all border ${compact ? "justify-center p-2.5" : "px-3 py-2.5"} ${
              active
                ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20"
                : "text-slate-400 border-transparent hover:text-slate-200 hover:bg-slate-800/60"
            }`}>
            {active && !compact && <span className="absolute left-0 top-2 bottom-2 w-1 rounded-r bg-emerald-500" aria-hidden="true" />}
            <Icon className="w-4 h-4 shrink-0" />
            {!compact && <span className="truncate">{item.label}</span>}
            {!compact && badge(item)}
            {compact && item.badge ? <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-amber-400" aria-hidden="true" /> : null}
          </Link>
        );
      })}
    </nav>
  );

  // Small screens (both styles): compact top bar plus a drawer
  const mobileBar = (
    <header className={`app-shell-nav ${sidebar ? "lg:hidden" : "min-[1400px]:hidden"} border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-30 px-4 py-2.5 flex items-center justify-between gap-3`}>
      {brandBlock(false)}
      <button onClick={() => setDrawer(true)} aria-label="Open navigation" aria-expanded={drawer}
        className="p-2.5 rounded-xl text-slate-300 hover:bg-slate-800 border border-slate-800">
        <Menu className="w-5 h-5" />
      </button>
    </header>
  );

  const mobileDrawer = drawer && (
    <div className={`app-shell-nav fixed inset-0 z-50 ${sidebar ? "lg:hidden" : "min-[1400px]:hidden"}`} role="dialog" aria-modal="true" aria-label="Navigation">
      <button className="absolute inset-0 bg-black/50" aria-label="Close navigation" onClick={() => setDrawer(false)} />
      <aside className="absolute left-0 top-0 bottom-0 w-72 max-w-[85vw] p-4 flex flex-col gap-4 border-r border-slate-800 overflow-y-auto"
        style={{ background: "var(--app-sidebar)" }}>
        <div className="flex items-center justify-between gap-2">
          {brandBlock(false)}
          <button onClick={() => setDrawer(false)} aria-label="Close navigation" className="p-2 rounded-lg text-slate-400 hover:bg-slate-800"><X className="w-5 h-5" /></button>
        </div>
        {verticalLinks(false)}
        <div className="mt-auto pt-3 border-t border-slate-800">{userBlock(false)}</div>
      </aside>
    </div>
  );

  if (sidebar) {
    return (
      <>
        <aside aria-label="Sidebar"
          className={`app-shell-nav hidden lg:flex fixed left-0 top-0 bottom-0 z-30 flex-col gap-4 border-r border-slate-800 py-4 overflow-y-auto overflow-x-hidden transition-[width] ${collapsed ? "px-2.5" : "px-3.5"}`}
          style={{ width: collapsed ? "var(--app-sidebar-w-collapsed)" : "var(--app-sidebar-w)", background: "var(--app-sidebar)" }}>
          <div className={`flex ${collapsed ? "justify-center" : "px-1"}`}>{brandBlock(collapsed)}</div>
          {verticalLinks(collapsed)}
          <div className="mt-auto pt-3 border-t border-slate-800 space-y-2">
            {userBlock(collapsed)}
            <button onClick={toggleCollapsed} disabled={!mine}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={`w-full flex items-center gap-2 rounded-xl text-xs text-slate-500 hover:text-slate-200 hover:bg-slate-800/60 p-2 ${collapsed ? "justify-center" : ""}`}>
              {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <><PanelLeftClose className="w-4 h-4" /> Collapse</>}
            </button>
          </div>
        </aside>
        {mobileBar}
        {mobileDrawer}
      </>
    );
  }

  return (
    <>
      <header className="app-shell-nav hidden min-[1400px]:block border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-30 px-4 sm:px-6 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
          <div className="flex items-center gap-6 min-w-0">
            {brandBlock(false)}
            <nav aria-label="Main" className="flex items-center gap-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = isItemActive(item);
                return (
                  <Link key={item.href} href={item.href} aria-current={isActive ? "page" : undefined}
                    className={`relative px-3 py-2 rounded-xl text-sm font-medium transition-all flex items-center gap-2 ${
                      isActive ? "text-emerald-400 bg-emerald-500/10 border border-emerald-500/20" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                    }`}>
                    <Icon className="w-4 h-4" />
                    <span>{item.label}</span>
                    {badge(item)}
                    {isActive && (
                      <motion.div layoutId="nav-active" className="absolute inset-0 rounded-xl border border-emerald-500/30 pointer-events-none"
                        transition={{ type: "spring", stiffness: 350, damping: 30 }} />
                    )}
                  </Link>
                );
              })}
            </nav>
          </div>
          {userBlock(false)}
        </div>
      </header>
      {mobileBar}
      {mobileDrawer}
    </>
  );
}
