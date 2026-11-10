"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Boxes,
  Layers,
  Calendar,
  AlertTriangle,
  Clock,
  ArrowDownRight,
  ArrowUpRight,
  Plus,
  CheckCircle2,
  Search,
  Filter,
  DollarSign,
  Package,
  FileText,
  ShieldCheck,
  AlertOctagon,
  ArrowRightLeft,
  X,
  Building,
} from "lucide-react";
import {
  api,
  CurrentUser,
  MedicineItem,
  MedicineBatchItem,
  StockMovementItem,
  StockAdjustmentResponse,
  LowStockAlertItem,
  ExpiryAlertItem,
  InventoryValuation,
  UnitItem,
  MedicineUnitConversionItem,
} from "@/lib/api";
import { Navbar } from "@/components/Navbar";

export default function InventoryPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [medicines, setMedicines] = useState<MedicineItem[]>([]);
  const [batches, setBatches] = useState<MedicineBatchItem[]>([]);
  const [movements, setMovements] = useState<StockMovementItem[]>([]);
  const [adjustments, setAdjustments] = useState<StockAdjustmentResponse[]>([]);
  const [lowStockAlerts, setLowStockAlerts] = useState<LowStockAlertItem[]>([]);
  const [nearExpiryAlerts, setNearExpiryAlerts] = useState<ExpiryAlertItem[]>([]);
  const [expiredAlerts, setExpiredAlerts] = useState<ExpiryAlertItem[]>([]);
  const [valuation, setValuation] = useState<InventoryValuation | null>(null);
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Active Tab
  const [activeTab, setActiveTab] = useState<
    "ledger" | "opening" | "adjustments" | "low_stock" | "expiry"
  >("ledger");

  // Filters for movements
  const [movementMedFilter, setMovementMedFilter] = useState<string>("all");
  const [movementTypeFilter, setMovementTypeFilter] = useState<string>("all");

  // Opening Stock form state
  const [openingForm, setOpeningForm] = useState({
    medicine_id: "",
    batch_number: "",
    manufacturing_date: "",
    expiry_date: "",
    packaging_unit_id: "",
    quantity_in_pack: "10",
    mrp: "100",
    purchase_rate: "70",
    default_selling_rate: "100",
    rack_location: "RACK-A1",
    notes: "Initial inventory takeover count",
  });
  const [openingConversions, setOpeningConversions] = useState<MedicineUnitConversionItem[]>([]);
  const [openingSubmitting, setOpeningSubmitting] = useState(false);
  const [openingSuccess, setOpeningSuccess] = useState("");
  const [openingError, setOpeningError] = useState("");

  // Adjustment form state
  const [adjForm, setAdjForm] = useState({
    reason_code: "DAMAGE_BREAKAGE",
    notes: "Physical stock audit discrepancy adjustment",
    medicine_id: "",
    batch_id: "",
    physical_stock_base: "",
  });
  const [adjBatches, setAdjBatches] = useState<MedicineBatchItem[]>([]);
  const [adjSubmitting, setAdjSubmitting] = useState(false);
  const [adjSuccess, setAdjSuccess] = useState("");
  const [adjError, setAdjError] = useState("");

  // Expiry alert threshold
  const [expiryDays, setExpiryDays] = useState(90);

  const loadData = async () => {
    try {
      setLoading(true);
      const currentUser = await api.getMe();
      setUser(currentUser);

      const [
        medsRes,
        batchesRes,
        movsRes,
        adjsRes,
        lowRes,
        nearRes,
        expRes,
        valRes,
        unitsRes,
      ] = await Promise.all([
        api.getMedicines({ limit: 200 }),
        api.getBatches({ limit: 200 }),
        api.getStockMovements({ limit: 100 }),
        api.getStockAdjustments(50, 0),
        api.getLowStockAlerts(),
        api.getNearExpiryAlerts(expiryDays),
        api.getExpiredBatches(),
        api.getInventoryValuation(),
        api.getUnits(),
      ]);

      setMedicines(medsRes);
      setBatches(batchesRes);
      setMovements(movsRes);
      setAdjustments(adjsRes);
      setLowStockAlerts(lowRes);
      setNearExpiryAlerts(nearRes);
      setExpiredAlerts(expRes);
      setValuation(valRes);
      setUnits(unitsRes);

      if (medsRes.length > 0 && !openingForm.medicine_id) {
        setOpeningForm((prev) => ({ ...prev, medicine_id: String(medsRes[0].id) }));
        loadConversionsForOpening(medsRes[0].id);
      }
    } catch {
      router.replace("/login");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [expiryDays]);

  const loadConversionsForOpening = async (medId: number) => {
    try {
      const convs = await api.getMedicineConversions(medId);
      setOpeningConversions(convs);
    } catch {
      setOpeningConversions([]);
    }
  };

  const handleOpeningMedChange = async (medIdStr: string) => {
    setOpeningForm((prev) => ({ ...prev, medicine_id: medIdStr, packaging_unit_id: "" }));
    if (medIdStr) {
      await loadConversionsForOpening(Number(medIdStr));
    }
  };

  const handleAdjustmentMedChange = async (medIdStr: string) => {
    setAdjForm((prev) => ({ ...prev, medicine_id: medIdStr, batch_id: "", physical_stock_base: "" }));
    if (medIdStr) {
      try {
        const b = await api.getMedicineBatches(Number(medIdStr));
        setAdjBatches(b);
        if (b.length > 0) {
          setAdjForm((prev) => ({
            ...prev,
            medicine_id: medIdStr,
            batch_id: String(b[0].id),
            physical_stock_base: String(b[0].current_stock_base),
          }));
        }
      } catch {
        setAdjBatches([]);
      }
    } else {
      setAdjBatches([]);
    }
  };

  const handleOpeningStockSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setOpeningError("");
    setOpeningSuccess("");
    setOpeningSubmitting(true);
    try {
      await api.createOpeningStock({
        medicine_id: Number(openingForm.medicine_id),
        batch_number: openingForm.batch_number.trim(),
        manufacturing_date: openingForm.manufacturing_date || undefined,
        expiry_date: openingForm.expiry_date,
        packaging_unit_id: openingForm.packaging_unit_id ? Number(openingForm.packaging_unit_id) : undefined,
        quantity_in_pack: Number(openingForm.quantity_in_pack),
        mrp: Number(openingForm.mrp),
        purchase_rate: Number(openingForm.purchase_rate) || undefined,
        default_selling_rate: Number(openingForm.default_selling_rate) || undefined,
        rack_location: openingForm.rack_location.trim() || undefined,
        notes: openingForm.notes.trim() || undefined,
      });

      setOpeningSuccess(`Opening stock registered successfully for batch ${openingForm.batch_number.toUpperCase()}`);
      setOpeningForm({
        medicine_id: medicines[0]?.id ? String(medicines[0].id) : "",
        batch_number: "",
        manufacturing_date: "",
        expiry_date: "",
        packaging_unit_id: "",
        quantity_in_pack: "10",
        mrp: "100",
        purchase_rate: "70",
        default_selling_rate: "100",
        rack_location: "RACK-A1",
        notes: "Initial inventory takeover count",
      });
      await loadData();
    } catch (err: any) {
      setOpeningError(err.detail || "Failed to record opening stock");
    } finally {
      setOpeningSubmitting(false);
    }
  };

  const handleAdjustmentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAdjError("");
    setAdjSuccess("");
    setAdjSubmitting(true);
    try {
      const selectedBatch = adjBatches.find((b) => b.id === Number(adjForm.batch_id));
      if (!selectedBatch) {
        throw new Error("Please select a batch to adjust");
      }

      await api.createStockAdjustment({
        reason_code: adjForm.reason_code,
        notes: adjForm.notes.trim() || undefined,
        items: [
          {
            medicine_id: Number(adjForm.medicine_id),
            batch_id: Number(adjForm.batch_id),
            physical_stock_base: Number(adjForm.physical_stock_base),
            notes: `Adjusted batch ${selectedBatch.batch_number} to count ${adjForm.physical_stock_base}`,
          },
        ],
      });

      setAdjSuccess("Stock adjustment voucher successfully posted and ledger updated.");
      await loadData();
    } catch (err: any) {
      setAdjError(err.detail || err.message || "Failed to post adjustment");
    } finally {
      setAdjSubmitting(false);
    }
  };

  // Filtered movements
  const filteredMovements = useMemo(() => {
    return movements.filter((m) => {
      const matchMed = movementMedFilter === "all" || String(m.medicine_id) === movementMedFilter;
      const matchType = movementTypeFilter === "all" || m.movement_type === movementTypeFilter;
      return matchMed && matchType;
    });
  }, [movements, movementMedFilter, movementTypeFilter]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col">
        <Navbar user={user} />
        <div className="flex-1 flex items-center justify-center">
          <div className="w-8 h-8 rounded-full border-2 border-emerald-500/30 border-t-emerald-400 animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col text-slate-100">
      <Navbar
        user={user}
        lowStockCount={lowStockAlerts.length}
        nearExpiryCount={nearExpiryAlerts.length + expiredAlerts.length}
      />

      <main className="flex-1 p-4 sm:p-6 max-w-7xl mx-auto w-full space-y-6">
        {/* Header & KPI Summary */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              <Boxes className="w-6 h-6 text-emerald-400" />
              Inventory & Batch Ledger
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Immutable stock movement audit trail, opening balances, physical adjustments, and expiry surveillance.
            </p>
          </div>
        </div>

        {/* Valuation & Alert Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-2xl flex items-center gap-4">
            <div className="p-3 bg-emerald-500/10 text-emerald-400 rounded-xl">
              <DollarSign className="w-6 h-6" />
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Total Stock Cost Value</div>
              <div className="text-lg font-bold text-white font-mono">
                ₹{Number(valuation?.total_cost_valuation || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
              </div>
              <div className="text-[10px] text-slate-500">
                {valuation?.active_batches_count || 0} active batches
              </div>
            </div>
          </div>

          <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-2xl flex items-center gap-4">
            <div className="p-3 bg-blue-500/10 text-blue-400 rounded-xl">
              <Package className="w-6 h-6" />
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Base Units on Hand</div>
              <div className="text-lg font-bold text-white font-mono">
                {Number(valuation?.total_base_units || 0).toFixed(0)}
              </div>
              <div className="text-[10px] text-slate-500">Tracked in standard units</div>
            </div>
          </div>

          <div
            onClick={() => setActiveTab("low_stock")}
            className="p-4 bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 rounded-2xl flex items-center gap-4 cursor-pointer transition"
          >
            <div className={`p-3 rounded-xl ${lowStockAlerts.length > 0 ? "bg-amber-500/10 text-amber-400" : "bg-slate-800 text-slate-400"}`}>
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Low Stock SKUs</div>
              <div className={`text-lg font-bold font-mono ${lowStockAlerts.length > 0 ? "text-amber-400" : "text-slate-200"}`}>
                {lowStockAlerts.length} Medicines
              </div>
              <div className="text-[10px] text-slate-500">≤ Reorder thresholds</div>
            </div>
          </div>

          <div
            onClick={() => setActiveTab("expiry")}
            className="p-4 bg-slate-900/60 hover:bg-slate-850/60 border border-slate-800 rounded-2xl flex items-center gap-4 cursor-pointer transition"
          >
            <div className={`p-3 rounded-xl ${expiredAlerts.length > 0 ? "bg-rose-500/10 text-rose-400" : "bg-purple-500/10 text-purple-400"}`}>
              <Clock className="w-6 h-6" />
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Expiry Surveillance</div>
              <div className={`text-lg font-bold font-mono ${expiredAlerts.length > 0 ? "text-rose-400" : "text-white"}`}>
                {expiredAlerts.length} Expired / {nearExpiryAlerts.length} Near
              </div>
              <div className="text-[10px] text-slate-500">Requires isolation / alert</div>
            </div>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-slate-800 space-x-2 text-xs font-semibold overflow-x-auto">
          <button
            onClick={() => setActiveTab("ledger")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "ledger"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileText className="w-4 h-4" />
            Stock Ledger Movements
          </button>

          {user?.permissions?.includes("stock.adjust") && (
            <>
              <button
                onClick={() => setActiveTab("opening")}
                className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
                  activeTab === "opening"
                    ? "border-emerald-400 text-emerald-400"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                <Plus className="w-4 h-4" />
                Record Opening Stock
              </button>

              <button
                onClick={() => setActiveTab("adjustments")}
                className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
                  activeTab === "adjustments"
                    ? "border-emerald-400 text-emerald-400"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                <ShieldCheck className="w-4 h-4" />
                Physical Stock Adjustments
              </button>
            </>
          )}

          <button
            onClick={() => setActiveTab("low_stock")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "low_stock"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <AlertTriangle className="w-4 h-4" />
            Low Stock Alerts ({lowStockAlerts.length})
          </button>

          <button
            onClick={() => setActiveTab("expiry")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "expiry"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <AlertOctagon className="w-4 h-4" />
            Expiry Alerts ({nearExpiryAlerts.length + expiredAlerts.length})
          </button>
        </div>

        {/* ── TAB 1: Stock Movements Ledger ───────────────────────────────────── */}
        {activeTab === "ledger" && (
          <div className="space-y-4">
            {/* Filter controls */}
            <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-2xl flex flex-wrap gap-4 text-xs">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-slate-400 mb-1">Filter by Medicine</label>
                <select
                  value={movementMedFilter}
                  onChange={(e) => setMovementMedFilter(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                >
                  <option value="all">All Medicines</option>
                  {medicines.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="w-48">
                <label className="block text-slate-400 mb-1">Movement Type</label>
                <select
                  value={movementTypeFilter}
                  onChange={(e) => setMovementTypeFilter(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                >
                  <option value="all">All Types</option>
                  <option value="OPENING_STOCK">Opening Stock</option>
                  <option value="GOODS_RECEIPT">Goods Receipt (Purchase)</option>
                  <option value="ADJUSTMENT_IN">Adjustment In (+)</option>
                  <option value="ADJUSTMENT_OUT">Adjustment Out (-)</option>
                </select>
              </div>
            </div>

            {/* Movements Ledger Table */}
            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                    <tr>
                      <th className="px-4 py-3">Timestamp</th>
                      <th className="px-4 py-3">Medicine & Batch</th>
                      <th className="px-4 py-3">Type & Reference</th>
                      <th className="px-4 py-3 text-right">Qty (Base)</th>
                      <th className="px-4 py-3 text-right">Balance After</th>
                      <th className="px-4 py-3 text-right">Unit Cost</th>
                      <th className="px-4 py-3 text-right">Total Cost</th>
                      <th className="px-4 py-3">Reason / Audit Trail</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-slate-300">
                    {filteredMovements.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-8 text-center text-slate-500">
                          No stock ledger movements recorded yet.
                        </td>
                      </tr>
                    ) : (
                      filteredMovements.map((mov) => {
                        const isInflow = Number(mov.quantity_base) >= 0;

                        return (
                          <tr key={mov.id} className="hover:bg-slate-850/40 transition">
                            <td className="px-4 py-3 font-mono text-slate-400 whitespace-nowrap">
                              {new Date(mov.created_at).toLocaleString("en-IN", {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </td>
                            <td className="px-4 py-3">
                              <div className="font-semibold text-white">{mov.medicine_name}</div>
                              <div className="text-[10px] text-slate-400 font-mono">
                                Batch: <span className="text-slate-300">{mov.batch_number || "—"}</span>
                              </div>
                            </td>
                            <td className="px-4 py-3">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  isInflow
                                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                                    : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                                }`}
                              >
                                {mov.movement_type}
                              </span>
                              {mov.reference_id && (
                                <div className="text-[10px] text-slate-500 font-mono mt-0.5">
                                  Ref: {mov.reference_id}
                                </div>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold">
                              <span className={isInflow ? "text-emerald-400" : "text-rose-400"}>
                                {isInflow ? "+" : "-"}
                                {Math.abs(Number(mov.quantity_base)).toFixed(2)}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-white">
                              {Number(mov.balance_after_base).toFixed(2)}
                            </td>
                            <td className="px-4 py-3 text-right font-mono text-slate-400">
                              ₹{Number(mov.unit_cost).toFixed(2)}
                            </td>
                            <td className="px-4 py-3 text-right font-mono text-slate-300">
                              ₹{Number(mov.total_cost).toFixed(2)}
                            </td>
                            <td className="px-4 py-3">
                              <div className="text-slate-300 truncate max-w-xs">{mov.reason || "—"}</div>
                              {mov.notes && <div className="text-[10px] text-slate-500">{mov.notes}</div>}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 2: Opening Stock Intake Wizard ─────────────────────────────── */}
        {activeTab === "opening" && user?.permissions?.includes("stock.adjust") && (
          <div className="max-w-2xl bg-slate-900/60 border border-slate-800 rounded-2xl p-6 backdrop-blur shadow-2xl">
            <div className="flex items-center gap-3 pb-4 border-b border-slate-800 mb-4">
              <Plus className="w-5 h-5 text-emerald-400" />
              <div>
                <h2 className="text-base font-bold text-white">Record Opening Stock Intake</h2>
                <p className="text-xs text-slate-400">
                  Record documented opening inventory balance for existing stocks with immutable provenance.
                </p>
              </div>
            </div>

            {openingSuccess && (
              <div className="p-3 mb-4 bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs rounded-xl flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{openingSuccess}</span>
              </div>
            )}

            {openingError && (
              <div className="p-3 mb-4 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs rounded-xl flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{openingError}</span>
              </div>
            )}

            <form onSubmit={handleOpeningStockSubmit} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-400 mb-1 font-semibold">Medicine Formulation *</label>
                <select
                  required
                  value={openingForm.medicine_id}
                  onChange={(e) => handleOpeningMedChange(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                >
                  <option value="">Select Medicine...</option>
                  {medicines.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.dosage_form}) — Base: {m.base_unit_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-slate-400 mb-1">Batch Number *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. BATCH-2026-A"
                    value={openingForm.batch_number}
                    onChange={(e) => setOpeningForm({ ...openingForm, batch_number: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 uppercase font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Expiry Date *</label>
                  <input
                    type="date"
                    required
                    value={openingForm.expiry_date}
                    onChange={(e) => setOpeningForm({ ...openingForm, expiry_date: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Packaging Unit</label>
                  <select
                    value={openingForm.packaging_unit_id}
                    onChange={(e) => setOpeningForm({ ...openingForm, packaging_unit_id: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  >
                    <option value="">Base Dispensing Unit</option>
                    {openingConversions.map((c) => (
                      <option key={c.packaging_unit_id} value={c.packaging_unit_id}>
                        {c.packaging_unit_name} (×{Number(c.conversion_factor)} base units)
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Quantity in Pack / Units *</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    required
                    value={openingForm.quantity_in_pack}
                    onChange={(e) => setOpeningForm({ ...openingForm, quantity_in_pack: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Maximum Retail Price (MRP) ₹ *</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    required
                    value={openingForm.mrp}
                    onChange={(e) => setOpeningForm({ ...openingForm, mrp: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Purchase / Acquisition Rate ₹</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={openingForm.purchase_rate}
                    onChange={(e) => setOpeningForm({ ...openingForm, purchase_rate: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Default Selling Rate ₹</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={openingForm.default_selling_rate}
                    onChange={(e) => setOpeningForm({ ...openingForm, default_selling_rate: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Rack / Shelf Location</label>
                  <input
                    type="text"
                    placeholder="e.g. SHELF-B2"
                    value={openingForm.rack_location}
                    onChange={(e) => setOpeningForm({ ...openingForm, rack_location: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 uppercase focus:outline-none focus:border-emerald-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Audit Provenance / Stock Takeover Notes</label>
                <textarea
                  rows={2}
                  value={openingForm.notes}
                  onChange={(e) => setOpeningForm({ ...openingForm, notes: e.target.value })}
                  placeholder="Reference verification voucher, invoice origin, or physical count sheet ID..."
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="submit"
                  disabled={openingSubmitting}
                  className="px-6 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl shadow-lg shadow-emerald-500/20 cursor-pointer"
                >
                  {openingSubmitting ? "Posting..." : "Commit Opening Stock"}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* ── TAB 3: Physical Stock Adjustments ─────────────────────────────────── */}
        {activeTab === "adjustments" && user?.permissions?.includes("stock.adjust") && (
          <div className="space-y-6">
            <div className="max-w-2xl bg-slate-900/60 border border-slate-800 rounded-2xl p-6 backdrop-blur shadow-2xl">
              <div className="flex items-center gap-3 pb-4 border-b border-slate-800 mb-4">
                <ShieldCheck className="w-5 h-5 text-emerald-400" />
                <div>
                  <h2 className="text-base font-bold text-white">Post Stock Audit Adjustment Voucher</h2>
                  <p className="text-xs text-slate-400">
                    Adjust book stock balance to match verified physical count with documented reason and row locks.
                  </p>
                </div>
              </div>

              {adjSuccess && (
                <div className="p-3 mb-4 bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs rounded-xl flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  <span>{adjSuccess}</span>
                </div>
              )}

              {adjError && (
                <div className="p-3 mb-4 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs rounded-xl flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>{adjError}</span>
                </div>
              )}

              <form onSubmit={handleAdjustmentSubmit} className="space-y-4 text-xs">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-slate-400 mb-1 font-semibold">Adjustment Reason *</label>
                    <select
                      required
                      value={adjForm.reason_code}
                      onChange={(e) => setAdjForm({ ...adjForm, reason_code: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="DAMAGE_BREAKAGE">Damage / Breakage in Store</option>
                      <option value="EXPIRY">Expired Product Isolation</option>
                      <option value="THEFT">Shrinkage / Pilferage</option>
                      <option value="AUDIT_DISCREPANCY">Audit Discrepancy (Count Shortage)</option>
                      <option value="PHYSICAL_AUDIT_SURPLUS">Physical Audit Surplus (Found Stock)</option>
                      <option value="CORRECTION">Ledger Correction</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1 font-semibold">Medicine Formulation *</label>
                    <select
                      required
                      value={adjForm.medicine_id}
                      onChange={(e) => handleAdjustmentMedChange(e.target.value)}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="">Select Medicine...</option>
                      {medicines.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1 font-semibold">Batch to Adjust *</label>
                    <select
                      required
                      value={adjForm.batch_id}
                      onChange={(e) => {
                        const bId = e.target.value;
                        const b = adjBatches.find((item) => item.id === Number(bId));
                        setAdjForm({
                          ...adjForm,
                          batch_id: bId,
                          physical_stock_base: b ? String(b.current_stock_base) : "",
                        });
                      }}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="">Select Batch...</option>
                      {adjBatches.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.batch_number} (System: {Number(b.current_stock_base).toFixed(2)})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1 font-semibold">
                      Actual Physical Stock (Base Units) *
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      required
                      placeholder="Verified physical count"
                      value={adjForm.physical_stock_base}
                      onChange={(e) => setAdjForm({ ...adjForm, physical_stock_base: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Voucher Audit Notes</label>
                  <input
                    type="text"
                    value={adjForm.notes}
                    onChange={(e) => setAdjForm({ ...adjForm, notes: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div className="pt-2 flex justify-end">
                  <button
                    type="submit"
                    disabled={adjSubmitting}
                    className="px-6 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl shadow-lg shadow-emerald-500/20 cursor-pointer"
                  >
                    {adjSubmitting ? "Posting..." : "Commit Adjustment Voucher"}
                  </button>
                </div>
              </form>
            </div>

            {/* Adjustments history table */}
            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <div className="p-4 border-b border-slate-800 font-semibold text-white text-xs">
                Historical Adjustment Vouchers ({adjustments.length})
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-900 text-slate-400 text-[10px] uppercase">
                    <tr>
                      <th className="px-4 py-2.5">Voucher Number</th>
                      <th className="px-4 py-2.5">Date</th>
                      <th className="px-4 py-2.5">Reason Code</th>
                      <th className="px-4 py-2.5">Status</th>
                      <th className="px-4 py-2.5">Items Count</th>
                      <th className="px-4 py-2.5">Notes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-300">
                    {adjustments.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 py-6 text-center text-slate-500">
                          No stock adjustments posted yet.
                        </td>
                      </tr>
                    ) : (
                      adjustments.map((a) => (
                        <tr key={a.id} className="hover:bg-slate-850/40">
                          <td className="px-4 py-2.5 font-mono font-semibold text-white">{a.adjustment_number}</td>
                          <td className="px-4 py-2.5 font-mono text-slate-400">
                            {new Date(a.created_at).toLocaleDateString("en-IN")}
                          </td>
                          <td className="px-4 py-2.5 font-semibold text-amber-400">{a.reason_code}</td>
                          <td className="px-4 py-2.5">
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300">
                              {a.status}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 font-mono">{a.items.length} items</td>
                          <td className="px-4 py-2.5 text-slate-400">{a.notes || "—"}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 4: Low Stock Alerts ─────────────────────────────────────────── */}
        {activeTab === "low_stock" && (
          <div className="space-y-4">
            <div className="bg-amber-500/10 border border-amber-500/20 p-4 rounded-2xl flex items-center justify-between text-xs text-amber-300">
              <div className="flex items-center gap-3">
                <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400" />
                <span>
                  <strong>Low Stock Monitoring:</strong> The following medicines have base dispensing stock at or
                  below their configured reorder levels. Prepare purchase replenishment orders.
                </span>
              </div>
            </div>

            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                    <tr>
                      <th className="px-4 py-3">Medicine Formulation</th>
                      <th className="px-4 py-3">Dosage Form</th>
                      <th className="px-4 py-3 text-right">Current Stock</th>
                      <th className="px-4 py-3 text-right">Reorder Level</th>
                      <th className="px-4 py-3 text-right">Shortage Deficit</th>
                      <th className="px-4 py-3">Base Unit</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-slate-300">
                    {lowStockAlerts.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                          All medicines currently maintain stock above reorder thresholds.
                        </td>
                      </tr>
                    ) : (
                      lowStockAlerts.map((a) => {
                        const deficit = Number(a.reorder_level) - Number(a.current_stock_base);
                        return (
                          <tr key={a.medicine_id} className="hover:bg-slate-850/40 transition">
                            <td className="px-4 py-3">
                              <div className="font-semibold text-white">{a.medicine_name}</div>
                              {a.generic_name && (
                                <div className="text-[11px] text-slate-400">{a.generic_name}</div>
                              )}
                            </td>
                            <td className="px-4 py-3">{a.dosage_form}</td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-rose-400">
                              {Number(a.current_stock_base).toFixed(2)}
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-slate-300">
                              {Number(a.reorder_level).toFixed(2)}
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-amber-400">
                              {deficit > 0 ? `-${deficit.toFixed(2)}` : "0.00"}
                            </td>
                            <td className="px-4 py-3 font-mono text-slate-400">
                              {a.base_unit_name} ({a.base_unit_abbr})
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 5: Expiry Alerts ────────────────────────────────────────────── */}
        {activeTab === "expiry" && (
          <div className="space-y-6">
            {/* Filter */}
            <div className="flex items-center justify-between bg-slate-900/60 border border-slate-800 p-4 rounded-2xl text-xs">
              <span className="text-slate-300">
                Near-expiry cutoff threshold: <strong>{expiryDays} days</strong>
              </span>
              <div className="flex gap-2">
                {[30, 60, 90, 180].map((d) => (
                  <button
                    key={d}
                    onClick={() => setExpiryDays(d)}
                    className={`px-3 py-1 rounded-xl text-xs font-semibold cursor-pointer transition ${
                      expiryDays === d
                        ? "bg-emerald-500 text-slate-950"
                        : "bg-slate-800 text-slate-400 hover:text-white"
                    }`}
                  >
                    {d} Days
                  </button>
                ))}
              </div>
            </div>

            {/* Expired Batches Section */}
            {expiredAlerts.length > 0 && (
              <div className="space-y-3">
                <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs rounded-xl flex items-center gap-2">
                  <AlertOctagon className="w-4 h-4 shrink-0 text-rose-400" />
                  <span>
                    <strong>Statutory Alert:</strong> {expiredAlerts.length} batch(es) have passed expiry dates and
                    contain stock. They must be quarantined immediately to prevent illegal dispensing under the Drugs
                    & Cosmetics Act.
                  </span>
                </div>

                <div className="bg-slate-900/40 border border-rose-500/30 rounded-2xl overflow-hidden shadow-xl">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-rose-950/40 text-rose-300 font-semibold border-b border-rose-500/20 uppercase tracking-wider text-[10px]">
                        <tr>
                          <th className="px-4 py-3">Medicine</th>
                          <th className="px-4 py-3">Batch Number</th>
                          <th className="px-4 py-3">Expiry Date</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3 text-right">Remaining Stock</th>
                          <th className="px-4 py-3">Shelf Location</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 text-slate-300">
                        {expiredAlerts.map((a) => (
                          <tr key={a.batch_id} className="hover:bg-rose-950/20 transition">
                            <td className="px-4 py-3 font-semibold text-white">{a.medicine_name}</td>
                            <td className="px-4 py-3 font-mono font-semibold text-rose-300">{a.batch_number}</td>
                            <td className="px-4 py-3 font-mono text-rose-400">{a.expiry_date}</td>
                            <td className="px-4 py-3">
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300">
                                EXPIRED ({Math.abs(a.days_to_expiry)}d ago)
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-rose-400">
                              {Number(a.current_stock_base).toFixed(2)} {a.base_unit_abbr}
                              {a.stock_value !== undefined && (
                                <div className="text-[11px] font-normal text-slate-400">₹{Number(a.stock_value).toLocaleString("en-IN", { minimumFractionDigits: 2 })} at cost</div>
                              )}
                            </td>
                            <td className="px-4 py-3 font-mono text-slate-300">{a.rack_location || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {/* Near Expiry Batches */}
            <div className="space-y-3">
              <div className="text-slate-300 text-xs font-semibold">
                Batches Expiring Within {expiryDays} Days ({nearExpiryAlerts.length})
              </div>

              <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                      <tr>
                        <th className="px-4 py-3">Medicine</th>
                        <th className="px-4 py-3">Batch Number</th>
                        <th className="px-4 py-3">Expiry Date</th>
                        <th className="px-4 py-3">Days Remaining</th>
                        <th className="px-4 py-3 text-right">Remaining Stock</th>
                        <th className="px-4 py-3">Shelf Location</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60 text-slate-300">
                      {nearExpiryAlerts.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                            No active batches expiring within {expiryDays} days.
                          </td>
                        </tr>
                      ) : (
                        nearExpiryAlerts.map((a) => (
                          <tr key={a.batch_id} className="hover:bg-slate-850/40 transition">
                            <td className="px-4 py-3 font-semibold text-white">{a.medicine_name}</td>
                            <td className="px-4 py-3 font-mono font-semibold text-amber-300">{a.batch_number}</td>
                            <td className="px-4 py-3 font-mono text-slate-300">{a.expiry_date}</td>
                            <td className="px-4 py-3">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  a.days_to_expiry <= 30
                                    ? "bg-rose-500/20 text-rose-300"
                                    : "bg-amber-500/20 text-amber-300"
                                }`}
                              >
                                {a.days_to_expiry} days left
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right font-mono font-semibold text-amber-400">
                              {Number(a.current_stock_base).toFixed(2)} {a.base_unit_abbr}
                              {a.stock_value !== undefined && (
                                <div className="text-[11px] font-normal text-slate-400">₹{Number(a.stock_value).toLocaleString("en-IN", { minimumFractionDigits: 2 })} at cost</div>
                              )}
                            </td>
                            <td className="px-4 py-3 font-mono text-slate-300">{a.rack_location || "—"}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
