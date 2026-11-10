"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { AlertCircle, Loader2, X } from "lucide-react";
import { api, ApiError, CurrentUser, isUnauthorized } from "@/lib/api";
import { Navbar } from "@/components/Navbar";

// ── Auth ──────────────────────────────────────────────────────────────────────

export function useStaff() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  useEffect(() => {
    api
      .getCurrentUser()
      .then(setUser)
      .catch((err) => {
        // Only a genuine 401 ends the session. A 403/500/network error must
        // not log out a valid user — surface it and keep them on the page.
        if (isUnauthorized(err)) {
          router.push("/login");
          return;
        }
        setAuthError(errorText(err));
      });
  }, [router]);
  const can = useCallback((perm: string) => !!user?.permissions.includes(perm), [user]);
  return { user, can, authError };
}

export const errorText = (err: unknown): string =>
  err instanceof ApiError ? err.detail : err instanceof Error ? err.message : "Something went wrong";

// ── Layout ────────────────────────────────────────────────────────────────────

const TABS = [
  { href: "/pos", label: "POS", perm: "pos.access" },
  { href: "/sales", label: "Invoices", perm: "sales.view", exact: true },
  { href: "/sales/returns", label: "Returns", perm: "sales.refund" },
  { href: "/customers", label: "Customers", perm: "customers.view" },
  { href: "/receivables", label: "Receivables", perm: "customers.view" },
  { href: "/day-end", label: "Day-end", perm: "shifts.operate" },
  { href: "/reports/sales", label: "Sales reports", perm: "reports.view" },
  { href: "/reports/receivables", label: "Credit reports", perm: "reports.view" },
  { href: "/reports/purchases", label: "Purchase report", perm: "reports.view" },
  { href: "/settings/communications", label: "WhatsApp", perm: "messaging.configure" },
];

export function SalesNav({ user }: { user: CurrentUser | null }) {
  const pathname = usePathname();
  const tabs = TABS.filter((t) => user?.permissions.includes(t.perm));
  return (
    <nav className="flex gap-1 overflow-x-auto pb-1 -mx-1 px-1" aria-label="Sales sections">
      {tabs.map((t) => {
        const active = t.exact ? pathname === t.href || /^\/sales\/\d+/.test(pathname) : pathname.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap border transition ${
              active
                ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                : "text-slate-400 border-transparent hover:bg-slate-800 hover:text-slate-200"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function SalesShell({
  user, title, subtitle, icon, actions, children, wide = false,
}: {
  user: CurrentUser | null;
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className={`flex-1 w-full mx-auto px-4 sm:px-6 py-5 space-y-5 ${wide ? "max-w-[1500px]" : "max-w-7xl"}`}>
        <SalesNav user={user} />
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {icon && (
              <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl">{icon}</div>
            )}
            <div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">{title}</h1>
              {subtitle && <p className="text-sm text-slate-400">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
        {!user ? <Spinner label="Loading…" /> : children}
      </main>
    </div>
  );
}

// ── Primitives ────────────────────────────────────────────────────────────────

export function Card({ children, className = "", title, actions }: { children: ReactNode; className?: string; title?: string; actions?: ReactNode }) {
  return (
    <section className={`bg-slate-900/60 border border-slate-800 rounded-2xl p-4 ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-2 mb-3">
          {title && <h2 className="text-sm font-semibold text-slate-200">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone = "slate" }: { label: string; value: string; hint?: string; tone?: "slate" | "emerald" | "amber" | "rose" | "sky" }) {
  const tones = {
    slate: "text-white",
    emerald: "text-emerald-300",
    amber: "text-amber-300",
    rose: "text-rose-300",
    sky: "text-sky-300",
  };
  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-lg font-bold font-mono ${tones[tone]}`}>{value}</div>
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

type BtnVariant = "primary" | "secondary" | "danger" | "ghost";

export function Btn({
  children, onClick, variant = "secondary", disabled, busy, type = "button", className = "", title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: BtnVariant;
  disabled?: boolean;
  busy?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
}) {
  const styles: Record<BtnVariant, string> = {
    primary: "bg-emerald-500 hover:bg-emerald-400 text-slate-950 border-emerald-400",
    secondary: "bg-slate-800 hover:bg-slate-700 text-slate-100 border-slate-700",
    danger: "bg-rose-500/15 hover:bg-rose-500/25 text-rose-200 border-rose-500/40",
    ghost: "bg-transparent hover:bg-slate-800 text-slate-300 border-transparent",
  };
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || busy}
      className={`inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold border transition disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer ${styles[variant]} ${className}`}
    >
      {busy && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </button>
  );
}

export const inputCls =
  "w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500/40";

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-xs">
      <span className="block text-slate-400 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-slate-500 mt-1">{hint}</span>}
    </label>
  );
}

export function ErrorBox({ message, onClose }: { message: string | null; onClose?: () => void }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-2 bg-rose-500/10 border border-rose-500/30 text-rose-200 rounded-xl px-3 py-2 text-sm">
      <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
      <span className="flex-1">{message}</span>
      {onClose && (
        <button onClick={onClose} className="text-rose-300 hover:text-white" aria-label="Dismiss">
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

export function Notice({ children, tone = "sky" }: { children: ReactNode; tone?: "sky" | "amber" | "emerald" }) {
  const tones = {
    sky: "bg-sky-500/10 border-sky-500/30 text-sky-200",
    amber: "bg-amber-500/10 border-amber-500/30 text-amber-200",
    emerald: "bg-emerald-500/10 border-emerald-500/30 text-emerald-200",
  };
  return <div className={`border rounded-xl px-3 py-2 text-sm ${tones[tone]}`}>{children}</div>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-slate-400 text-sm">
      <Loader2 className="w-5 h-5 animate-spin text-emerald-400" />
      {label}
    </div>
  );
}

export function Modal({ open, title, onClose, children, wide = false }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-start sm:items-center justify-center p-3 overflow-y-auto" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl my-6`}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
          <h3 className="font-semibold text-white">{title}</h3>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-4 space-y-3">{children}</div>
      </div>
    </div>
  );
}

const BADGE: Record<string, string> = {
  PAID: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  PARTIALLY_PAID: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  UNPAID: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  VOIDED: "bg-slate-700/60 text-slate-300 border-slate-600",
  FINALIZED: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  PARTIAL: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  FULL: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  SENT: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  DELIVERED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  READ: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  FAILED: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  QUEUED: "bg-slate-700/60 text-slate-200 border-slate-600",
  PENDING: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  CANCELLED: "bg-slate-700/60 text-slate-400 border-slate-600",
  MANUAL_SHARE_OPENED: "bg-teal-500/15 text-teal-300 border-teal-500/30",
  OPEN: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  CLOSED: "bg-slate-700/60 text-slate-200 border-slate-600",
  REVIEWED: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  OVERDUE: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  RECORDED: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  VERIFIED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
};

const LABEL: Record<string, string> = {
  PARTIALLY_PAID: "Part paid",
  UNPAID: "Unpaid (credit)",
  MANUAL_SHARE_OPENED: "Opened in WhatsApp",
  PARTIAL: "Part returned",
  FULL: "Returned",
  RECORDED: "Not verified",
};

export function Badge({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide border whitespace-nowrap ${BADGE[value] || "bg-slate-800 text-slate-300 border-slate-700"}`}>
      {label || LABEL[value] || value.replace(/_/g, " ")}
    </span>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  if (total <= limit) return null;
  return (
    <div className="flex items-center justify-between text-xs text-slate-400 pt-3">
      <span>
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <div className="flex gap-2">
        <Btn variant="ghost" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Btn>
        <Btn variant="ghost" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Btn>
      </div>
    </div>
  );
}

export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: string | false }) {
  return (
    <div className="overflow-x-auto -mx-4 sm:mx-0">
      <table className="w-full text-sm min-w-[640px]">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500 border-b border-slate-800">
            {head.map((h) => (
              <th key={h} className={`px-3 py-2 font-semibold ${/amount|total|due|paid|balance|qty|value|₹/i.test(h) ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/70">{children}</tbody>
      </table>
      {empty && <div className="text-center text-slate-500 text-sm py-8">{empty}</div>}
    </div>
  );
}

export const td = "px-3 py-2 align-top";
export const tdNum = "px-3 py-2 text-right font-mono align-top";

/** Local date as YYYY-MM-DD (not UTC). */
export const isoToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
export const isoMonthStart = () => isoToday().slice(0, 8) + "01";

/**
 * Open a tab synchronously inside a click handler (so browsers do not treat it as a
 * popup), then point it at WhatsApp once the server has answered.
 */
export function openTabNow(): Window | null {
  try {
    return window.open("about:blank", "_blank");
  } catch {
    return null;
  }
}

export function sendTabTo(tab: Window | null, url?: string | null) {
  if (!url) {
    tab?.close();
    return;
  }
  if (tab && !tab.closed) {
    tab.opener = null;
    tab.location.href = url;
  } else {
    window.open(url, "_blank", "noopener");
  }
}
