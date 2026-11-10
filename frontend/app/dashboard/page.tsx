"use client";

import { useBranding } from "@/lib/branding";
import { DASHBOARD_IDS, DASHBOARD_SECTIONS } from "@/components/theme-editor";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  api,
  CurrentUser,
  SettingItem,
  RoleItem,
  UserItem,
  MedicineItem,
  InventoryValuation,
  LowStockAlertItem,
  ExpiryAlertItem,
  StockMovementItem,
  isUnauthorized,
  apiErrorMessage,
} from "@/lib/api";
import {
  Shield,
  Users,
  Settings,
  Building,
  CheckCircle,
  Database,
  Layers,
  Pill,
  Boxes,
  AlertTriangle,
  Clock,
  DollarSign,
  ArrowRight,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { Navbar } from "@/components/Navbar";

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [settingsList, setSettingsList] = useState<SettingItem[]>([]);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [health, setHealth] = useState<{ status: string; database?: string } | null>(null);

  // Phase 2 stats
  const [medicines, setMedicines] = useState<MedicineItem[]>([]);
  const [valuation, setValuation] = useState<InventoryValuation | null>(null);
  const [lowStockAlerts, setLowStockAlerts] = useState<LowStockAlertItem[]>([]);
  const [nearExpiryAlerts, setNearExpiryAlerts] = useState<ExpiryAlertItem[]>([]);
  const [expiredAlerts, setExpiredAlerts] = useState<ExpiryAlertItem[]>([]);
  const [recentMovements, setRecentMovements] = useState<StockMovementItem[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDashboard() {
      try {
        const currentUser = await api.getMe();
        setUser(currentUser);

        const perms = currentUser.permissions || [];
        const canViewDashboard = perms.some((p: string) =>
          ["settings.manage", "reports.view", "audit.view"].includes(p)
        );

        if (!canViewDashboard) {
          if (perms.includes("pos.access")) {
            router.replace("/pos");
          } else if (perms.includes("purchases.view")) {
            router.replace("/purchasing");
          } else if (perms.includes("stock.view")) {
            router.replace("/inventory");
          } else {
            router.replace("/medicines");
          }
          return;
        }

        const canSettings = perms.includes("settings.manage");
        const canRoles = perms.includes("roles.manage");
        const canUsers = perms.includes("users.manage");
        const canValuation = perms.includes("reports.view");

        const [h, s, r, u, meds, val, low, near, exp, movs] = await Promise.allSettled([
          api.getHealth(),
          canSettings ? api.getSettings() : Promise.resolve([]),
          canRoles ? api.getRoles() : Promise.resolve([]),
          canUsers ? api.getUsers() : Promise.resolve([]),
          api.getMedicines({ limit: 10 }),
          canValuation ? api.getInventoryValuation() : Promise.resolve(null),
          api.getLowStockAlerts(),
          api.getNearExpiryAlerts(90),
          api.getExpiredBatches(),
          api.getStockMovements({ limit: 5 }),
        ]);

        if (h.status === "fulfilled") setHealth(h.value);
        if (s.status === "fulfilled") setSettingsList(s.value);
        if (r.status === "fulfilled") setRoles(r.value);
        if (u.status === "fulfilled") setUsers(u.value);
        if (meds.status === "fulfilled") setMedicines(meds.value);
        if (val.status === "fulfilled") setValuation(val.value);
        if (low.status === "fulfilled") setLowStockAlerts(low.value);
        if (near.status === "fulfilled") setNearExpiryAlerts(near.value);
        if (exp.status === "fulfilled") setExpiredAlerts(exp.value);
        if (movs.status === "fulfilled") setRecentMovements(movs.value);
      } catch (err) {
        // Only a genuine 401 ends the session; other errors (403/500/network)
        // must not log out a valid user — show the error on the page instead.
        if (isUnauthorized(err)) {
          router.replace("/login");
          return;
        }
        setLoadError(apiErrorMessage(err, "Failed to load the dashboard. Please try again."));
      } finally {
        setLoading(false);
      }
    }
    loadDashboard();
  }, [router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col">
        <Navbar user={user} />
        <div className="flex-1 flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-500"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-screen bg-slate-950 text-slate-100">
      <Navbar
        user={user}
        lowStockCount={lowStockAlerts.length}
        nearExpiryCount={nearExpiryAlerts.length + expiredAlerts.length}
      />

      {/* Main Content */}
      <main className="flex-1 p-4 sm:p-6 max-w-7xl mx-auto w-full flex flex-col gap-6">
        {loadError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200" role="alert">
            {loadError}
          </div>
        )}
        {/* Sections can be hidden or reordered per user (My Appearance); their content and data are unchanged */}
        <DashSection id="kpis">
        {/* Phase 2 Inventory KPIs Banner */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Link
            href="/inventory"
            className="bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 p-5 rounded-2xl flex items-center gap-4 transition group"
          >
            <div className="p-3 bg-emerald-500/10 text-emerald-400 rounded-xl group-hover:scale-105 transition-transform">
              <DollarSign className="w-6 h-6" />
            </div>
            <div>
              <div className="text-xs text-slate-400">Total Inventory Cost</div>
              <div className="text-lg font-bold text-white font-mono">
                ₹{Number(valuation?.total_cost_valuation || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
              </div>
              <div className="text-[11px] text-emerald-400/80">
                {valuation?.active_batches_count || 0} active batches
              </div>
            </div>
          </Link>

          <Link
            href="/medicines"
            className="bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 p-5 rounded-2xl flex items-center gap-4 transition group"
          >
            <div className="p-3 bg-blue-500/10 text-blue-400 rounded-xl group-hover:scale-105 transition-transform">
              <Pill className="w-6 h-6" />
            </div>
            <div>
              <div className="text-xs text-slate-400">Medicines Catalogue</div>
              <div className="text-lg font-bold text-white font-mono">
                {medicines.length} Formulations
              </div>
              <div className="text-[11px] text-blue-400/80">Catalogued & Scheduled</div>
            </div>
          </Link>

          <Link
            href="/inventory"
            className="bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 p-5 rounded-2xl flex items-center gap-4 transition group"
          >
            <div
              className={`p-3 rounded-xl group-hover:scale-105 transition-transform ${
                lowStockAlerts.length > 0 ? "bg-amber-500/10 text-amber-400" : "bg-slate-800 text-slate-400"
              }`}
            >
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div>
              <div className="text-xs text-slate-400">Low Stock SKUs</div>
              <div
                className={`text-lg font-bold font-mono ${
                  lowStockAlerts.length > 0 ? "text-amber-400" : "text-white"
                }`}
              >
                {lowStockAlerts.length} Shortages
              </div>
              <div className="text-[11px] text-amber-400/80">≤ Reorder thresholds</div>
            </div>
          </Link>

          <Link
            href="/inventory"
            className="bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 p-5 rounded-2xl flex items-center gap-4 transition group"
          >
            <div
              className={`p-3 rounded-xl group-hover:scale-105 transition-transform ${
                expiredAlerts.length > 0
                  ? "bg-rose-500/10 text-rose-400"
                  : nearExpiryAlerts.length > 0
                  ? "bg-purple-500/10 text-purple-400"
                  : "bg-slate-800 text-slate-400"
              }`}
            >
              <Clock className="w-6 h-6" />
            </div>
            <div>
              <div className="text-xs text-slate-400">Expiry Surveillance</div>
              <div
                className={`text-lg font-bold font-mono ${
                  expiredAlerts.length > 0 ? "text-rose-400" : "text-white"
                }`}
              >
                {expiredAlerts.length} Expired / {nearExpiryAlerts.length} Near
              </div>
              <div className="text-[11px] text-rose-400/80">Quarantine & alerts</div>
            </div>
          </Link>
        </div>

        </DashSection>

        <DashSection id="quick_actions">
        {/* Quick Operations Strip */}
        <div className="p-4 bg-gradient-to-r from-slate-900 via-slate-900/90 to-slate-900/70 border border-slate-800 rounded-2xl flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="font-semibold text-white text-sm">Quick Inventory Workflows</div>
            <div className="text-xs text-slate-400">Jump directly into daily dispensary and cataloguing tasks</div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <Link
              href="/medicines"
              className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl flex items-center gap-1.5 border border-slate-700 transition"
            >
              <Plus className="w-3.5 h-3.5 text-emerald-400" />
              New Medicine
            </Link>

            <Link
              href="/inventory"
              className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl flex items-center gap-1.5 border border-slate-700 transition"
            >
              <Boxes className="w-3.5 h-3.5 text-blue-400" />
              Opening Stock Intake
            </Link>

            <Link
              href="/inventory"
              className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl flex items-center gap-1.5 border border-slate-700 transition"
            >
              <ShieldCheck className="w-3.5 h-3.5 text-purple-400" />
              Stock Adjustment
            </Link>

            <Link
              href="/masters"
              className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl flex items-center gap-1.5 border border-slate-700 transition"
            >
              <Database className="w-3.5 h-3.5 text-amber-400" />
              GST Tax Masters
            </Link>
          </div>
        </div>

        </DashSection>

        <DashSection id="recent_activity">
        {/* Recent Ledger Movements */}
        <div className="bg-slate-900/40 border border-slate-800 rounded-2xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <Boxes className="w-4 h-4 text-emerald-400" />
              Recent Immutable Stock Ledger Movements
            </h2>
            <Link href="/inventory" className="text-xs text-emerald-400 hover:underline flex items-center gap-1">
              View full ledger <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-900 text-slate-400 text-[10px] uppercase font-semibold">
                <tr>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2">Medicine</th>
                  <th className="px-3 py-2">Batch</th>
                  <th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2 text-right">Quantity (Base)</th>
                  <th className="px-3 py-2 text-right">Balance After</th>
                  <th className="px-3 py-2">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80 text-slate-300">
                {recentMovements.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                      No stock movements recorded yet.
                    </td>
                  </tr>
                ) : (
                  recentMovements.map((mov) => {
                    const isInflow = Number(mov.quantity_base) >= 0;
                    return (
                      <tr key={mov.id} className="hover:bg-slate-850/40">
                        <td className="px-3 py-2 font-mono text-slate-400">
                          {new Date(mov.created_at).toLocaleTimeString("en-IN", {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </td>
                        <td className="px-3 py-2 font-medium text-white">{mov.medicine_name}</td>
                        <td className="px-3 py-2 font-mono text-slate-300">{mov.batch_number || "—"}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              isInflow
                                ? "bg-emerald-500/20 text-emerald-300"
                                : "bg-rose-500/20 text-rose-300"
                            }`}
                          >
                            {mov.movement_type}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono font-semibold">
                          <span className={isInflow ? "text-emerald-400" : "text-rose-400"}>
                            {isInflow ? "+" : "-"}
                            {Math.abs(Number(mov.quantity_base)).toFixed(2)}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-white font-semibold">
                          {Number(mov.balance_after_base).toFixed(2)}
                        </td>
                        <td className="px-3 py-2 text-slate-400 truncate max-w-xs">{mov.reason || "—"}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        </DashSection>

        {(roles.length > 0 || settingsList.length > 0) && (
        <DashSection id="system">
        {/* Roles & System Settings (Phase 1 Preserved) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Shield className="w-5 h-5 text-emerald-400" />
              Role-Based Access Control (RBAC)
            </h2>
            <div className="space-y-3">
              {roles.map((r) => (
                <div
                  key={r.id}
                  className="p-4 bg-slate-950/50 border border-slate-800/80 rounded-xl flex items-start justify-between gap-4"
                >
                  <div>
                    <div className="font-medium text-slate-200">{r.name}</div>
                    <div className="text-xs text-slate-400 mt-1">{r.description}</div>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {r.permissions.slice(0, 5).map((p) => (
                        <span
                          key={p}
                          className="px-2 py-0.5 bg-slate-800 text-slate-300 rounded text-[10px]"
                        >
                          {p}
                        </span>
                      ))}
                      {r.permissions.length > 5 && (
                        <span className="px-2 py-0.5 bg-slate-800/60 text-slate-500 rounded text-[10px]">
                          +{r.permissions.length - 5} more
                        </span>
                      )}
                    </div>
                  </div>
                  {r.is_system_role && (
                    <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500 bg-slate-800/50 px-2 py-1 rounded">
                      System
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Configured Settings */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Settings className="w-5 h-5 text-emerald-400" />
              Operational & Statutory Settings
            </h2>
            <div className="space-y-3 max-h-[380px] overflow-y-auto pr-2">
              {settingsList.map((s) => (
                <div
                  key={s.setting_key}
                  className="p-3 bg-slate-950/50 border border-slate-800/80 rounded-xl flex items-center justify-between text-sm"
                >
                  <div className="max-w-[70%]">
                    <div className="font-mono text-xs text-slate-300">{s.setting_key}</div>
                    <div className="text-[11px] text-slate-500 truncate">{s.description}</div>
                  </div>
                  <span className="font-mono text-xs px-2.5 py-1 bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 rounded-md">
                    {s.setting_value || "—"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        </DashSection>
        )}

        <DashSection id="status">
        {/* Phase 2 verification banner */}
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-2xl flex items-center gap-3 text-sm text-emerald-300">
          <CheckCircle className="w-5 h-5 shrink-0" />
          <span>
            <strong>Phase 2 Active:</strong> Medicine catalogue, packaging conversions, batch & expiry management,
            immutable inventory ledger with opening stock, physical audit adjustments, and GST tax masters are online.
          </span>
        </div>
        </DashSection>
      </main>
    </div>
  );
}

function DashSection({ id, children }: { id: string; children: React.ReactNode }) {
  const { appearance } = useBranding();
  if (appearance.dashboard_hidden.includes(id)) return null;
  const order = appearance.dashboard_order.indexOf(id);
  return <section aria-label={DASHBOARD_SECTIONS[id]} style={{ order: order === -1 ? DASHBOARD_IDS.indexOf(id) + 10 : order }}>{children}</section>;
}
