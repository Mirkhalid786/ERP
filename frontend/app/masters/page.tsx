"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Database,
  Calculator,
  Percent,
  Layers,
  Building,
  Ruler,
  Plus,
  CheckCircle2,
  AlertCircle,
  X,
  FileCheck,
} from "lucide-react";
import {
  api,
  CurrentUser,
  TaxRateItem,
  CategoryItem,
  ManufacturerItem,
  UnitItem,
  TaxCalculationResult,
  isUnauthorized,
  apiErrorMessage,
} from "@/lib/api";
import { Navbar } from "@/components/Navbar";

export default function MastersPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [taxRates, setTaxRates] = useState<TaxRateItem[]>([]);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [manufacturers, setManufacturers] = useState<ManufacturerItem[]>([]);
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Active Tab
  const [activeTab, setActiveTab] = useState<"taxes" | "categories" | "manufacturers" | "units">("taxes");

  // Calculator State
  const [calcAmount, setCalcAmount] = useState("100");
  const [calcRate, setCalcRate] = useState("12");
  const [calcInclusive, setCalcInclusive] = useState(true);
  const [calcInterstate, setCalcInterstate] = useState(false);
  const [calcResult, setCalcResult] = useState<TaxCalculationResult | null>(null);
  const [calcLoading, setCalcLoading] = useState(false);

  // Modal forms
  const [showTaxModal, setShowTaxModal] = useState(false);
  const [taxForm, setTaxForm] = useState({ name: "", rate_percentage: "12", cess_percentage: "0" });

  const [showCatModal, setShowCatModal] = useState(false);
  const [catForm, setCatForm] = useState({ name: "", description: "" });

  const [showMfgModal, setShowMfgModal] = useState(false);
  const [mfgForm, setMfgForm] = useState({ name: "", short_code: "", contact_email: "", contact_phone: "" });

  const [showUnitModal, setShowUnitModal] = useState(false);
  const [unitForm, setUnitForm] = useState({ name: "", abbreviation: "", is_fractional_allowed: false });

  const [formError, setFormError] = useState("");
  const [formSubmitting, setFormSubmitting] = useState(false);

  const loadData = async () => {
    try {
      setLoading(true);
      const currentUser = await api.getMe();
      setUser(currentUser);

      const perms = currentUser.permissions || [];
      if (!perms.includes("settings.manage")) {
        if (perms.includes("pos.access")) {
          router.replace("/pos");
        } else if (perms.includes("stock.view")) {
          router.replace("/inventory");
        } else {
          router.replace("/medicines");
        }
        return;
      }

      const [taxes, cats, mfgs, u] = await Promise.all([
        api.getTaxRates(false),
        api.getCategories(false),
        api.getManufacturers(false),
        api.getUnits(false),
      ]);

      setTaxRates(taxes);
      setCategories(cats);
      setManufacturers(mfgs);
      setUnits(u);
    } catch (err) {
      // Only a genuine 401 ends the session; other errors stay on the page.
      if (isUnauthorized(err)) {
        router.replace("/login");
        return;
      }
      setLoadError(apiErrorMessage(err, "Failed to load masters data. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Run tax calculation simulator
  const runTaxCalculation = async () => {
    if (!calcAmount || Number(calcAmount) <= 0) return;
    setCalcLoading(true);
    try {
      const res = await api.calculateTax(
        Number(calcAmount),
        Number(calcRate),
        calcInclusive,
        calcInterstate
      );
      setCalcResult(res);
    } catch (err: any) {
      console.error(err);
    } finally {
      setCalcLoading(false);
    }
  };

  useEffect(() => {
    runTaxCalculation();
  }, [calcAmount, calcRate, calcInclusive, calcInterstate]);

  // Tax Rate creation
  const handleCreateTax = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormSubmitting(true);
    try {
      await api.createTaxRate({
        name: taxForm.name.trim(),
        rate_percentage: Number(taxForm.rate_percentage),
        cess_percentage: Number(taxForm.cess_percentage) || 0,
      });
      setShowTaxModal(false);
      setTaxForm({ name: "", rate_percentage: "12", cess_percentage: "0" });
      await loadData();
    } catch (err: any) {
      setFormError(err.detail || "Failed to create tax rate");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Category creation
  const handleCreateCategory = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormSubmitting(true);
    try {
      await api.createCategory({
        name: catForm.name.trim(),
        description: catForm.description.trim() || undefined,
      });
      setShowCatModal(false);
      setCatForm({ name: "", description: "" });
      await loadData();
    } catch (err: any) {
      setFormError(err.detail || "Failed to create category");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Manufacturer creation
  const handleCreateMfg = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormSubmitting(true);
    try {
      await api.createManufacturer({
        name: mfgForm.name.trim(),
        short_code: mfgForm.short_code.trim() || undefined,
        contact_email: mfgForm.contact_email.trim() || undefined,
        contact_phone: mfgForm.contact_phone.trim() || undefined,
      });
      setShowMfgModal(false);
      setMfgForm({ name: "", short_code: "", contact_email: "", contact_phone: "" });
      await loadData();
    } catch (err: any) {
      setFormError(err.detail || "Failed to create manufacturer");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Unit creation
  const handleCreateUnit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormSubmitting(true);
    try {
      await api.createUnit({
        name: unitForm.name.trim(),
        abbreviation: unitForm.abbreviation.trim().toUpperCase(),
        is_fractional_allowed: unitForm.is_fractional_allowed,
      });
      setShowUnitModal(false);
      setUnitForm({ name: "", abbreviation: "", is_fractional_allowed: false });
      await loadData();
    } catch (err: any) {
      setFormError(err.detail || "Failed to create unit");
    } finally {
      setFormSubmitting(false);
    }
  };

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
      <Navbar user={user} />

      <main className="flex-1 p-4 sm:p-6 max-w-7xl mx-auto w-full space-y-6">
        {loadError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200" role="alert">
            {loadError}
          </div>
        )}
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              <Database className="w-6 h-6 text-emerald-400" />
              Inventory Masters & Tax Configuration
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Configurable GST slabs, categories, pharmaceutical manufacturers, and measurement units.
            </p>
          </div>
        </div>

        {/* Tab Buttons */}
        <div className="flex border-b border-slate-800 space-x-2 text-xs font-semibold overflow-x-auto">
          <button
            onClick={() => setActiveTab("taxes")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "taxes"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Percent className="w-4 h-4" />
            GST Tax Rates ({taxRates.length})
          </button>

          <button
            onClick={() => setActiveTab("categories")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "categories"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Layers className="w-4 h-4" />
            Therapeutic Categories ({categories.length})
          </button>

          <button
            onClick={() => setActiveTab("manufacturers")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "manufacturers"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Building className="w-4 h-4" />
            Manufacturers ({manufacturers.length})
          </button>

          <button
            onClick={() => setActiveTab("units")}
            className={`pb-3 px-3 border-b-2 transition whitespace-nowrap flex items-center gap-2 ${
              activeTab === "units"
                ? "border-emerald-400 text-emerald-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Ruler className="w-4 h-4" />
            Units of Measure ({units.length})
          </button>
        </div>

        {/* ── TAB 1: GST Tax Rates & Live Calculation Simulator ────────────────── */}
        {activeTab === "taxes" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Tax Rates Master Table */}
            <div className="lg:col-span-2 space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400 font-semibold uppercase tracking-wider">
                  Configured GST Tax Slabs
                </span>
                <button
                  onClick={() => setShowTaxModal(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-xl transition cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add Tax Slab
                </button>
              </div>

              <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                    <tr>
                      <th className="px-4 py-3">Slab Name</th>
                      <th className="px-4 py-3 text-right">Total Rate</th>
                      <th className="px-4 py-3 text-right">CGST</th>
                      <th className="px-4 py-3 text-right">SGST</th>
                      <th className="px-4 py-3 text-right">IGST</th>
                      <th className="px-4 py-3 text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-slate-300">
                    {taxRates.map((t) => (
                      <tr key={t.id} className="hover:bg-slate-850/40 transition">
                        <td className="px-4 py-3 font-semibold text-white">{t.name}</td>
                        <td className="px-4 py-3 text-right font-mono font-bold text-emerald-400">
                          {Number(t.rate_percentage).toFixed(2)}%
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-slate-400">
                          {Number(t.cgst_percentage).toFixed(2)}%
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-slate-400">
                          {Number(t.sgst_percentage).toFixed(2)}%
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-blue-400">
                          {Number(t.igst_percentage).toFixed(2)}%
                        </td>
                        <td className="px-4 py-3 text-center">
                          {t.is_active ? (
                            <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 font-semibold">
                              Active
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-500">
                              Inactive
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Interactive GST Calculation Simulator */}
            <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 backdrop-blur shadow-2xl space-y-4">
              <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
                <Calculator className="w-5 h-5 text-emerald-400" />
                <div>
                  <h3 className="text-sm font-bold text-white">GST Calculation Simulator</h3>
                  <div className="text-[11px] text-slate-400">
                    Verified pure Decimal arithmetic (ROUND_HALF_UP)
                  </div>
                </div>
              </div>

              <div className="space-y-3 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Base Price / Transaction Amount ₹</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={calcAmount}
                    onChange={(e) => setCalcAmount(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Tax Slab</label>
                  <select
                    value={calcRate}
                    onChange={(e) => setCalcRate(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  >
                    {taxRates.map((t) => (
                      <option key={t.id} value={t.rate_percentage}>
                        {t.name} ({t.rate_percentage}%)
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={calcInclusive}
                      onChange={(e) => setCalcInclusive(e.target.checked)}
                      className="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0"
                    />
                    <span className="text-slate-300">
                      <strong>Tax-Inclusive</strong> (Retail MRP Mode)
                    </span>
                  </label>

                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={calcInterstate}
                      onChange={(e) => setCalcInterstate(e.target.checked)}
                      className="rounded border-slate-700 bg-slate-950 text-blue-500 focus:ring-0"
                    />
                    <span className="text-slate-300">
                      <strong>Inter-State Supply</strong> (IGST Only)
                    </span>
                  </label>
                </div>

                {/* Calculation Output Card */}
                {calcResult && (
                  <div className="mt-4 p-4 bg-slate-950/80 border border-slate-800 rounded-xl space-y-2 font-mono">
                    <div className="flex justify-between text-slate-400">
                      <span>Taxable Value:</span>
                      <span className="text-white font-semibold">₹{calcResult.taxable_amount}</span>
                    </div>

                    {!calcInterstate ? (
                      <>
                        <div className="flex justify-between text-slate-400">
                          <span>CGST ({calcResult.cgst_rate}%):</span>
                          <span className="text-slate-300">₹{calcResult.cgst_amount}</span>
                        </div>
                        <div className="flex justify-between text-slate-400">
                          <span>SGST ({calcResult.sgst_rate}%):</span>
                          <span className="text-slate-300">₹{calcResult.sgst_amount}</span>
                        </div>
                      </>
                    ) : (
                      <div className="flex justify-between text-slate-400">
                        <span>IGST ({calcResult.igst_rate}%):</span>
                        <span className="text-blue-300">₹{calcResult.igst_amount}</span>
                      </div>
                    )}

                    <div className="flex justify-between text-slate-400 border-t border-slate-800 pt-1.5">
                      <span>Total GST:</span>
                      <span className="text-emerald-400 font-bold">₹{calcResult.total_tax}</span>
                    </div>

                    <div className="flex justify-between text-slate-200 border-t border-slate-800 pt-1.5 text-sm font-bold">
                      <span>Gross Invoice Total:</span>
                      <span className="text-emerald-400">₹{calcResult.gross_amount}</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 2: Categories ───────────────────────────────────────────────── */}
        {activeTab === "categories" && (
          <div className="space-y-4 max-w-4xl">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 font-semibold uppercase tracking-wider">
                Therapeutic & OTC Categories
              </span>
              <button
                onClick={() => setShowCatModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-xl transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Category
              </button>
            </div>

            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="px-4 py-3">Category Name</th>
                    <th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {categories.map((c) => (
                    <tr key={c.id} className="hover:bg-slate-850/40 transition">
                      <td className="px-4 py-3 font-semibold text-white">{c.name}</td>
                      <td className="px-4 py-3 text-slate-400">{c.description || "—"}</td>
                      <td className="px-4 py-3 text-center">
                        {c.is_active ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 font-semibold">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-500">
                            Inactive
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── TAB 3: Manufacturers ────────────────────────────────────────────── */}
        {activeTab === "manufacturers" && (
          <div className="space-y-4 max-w-4xl">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 font-semibold uppercase tracking-wider">
                Pharmaceutical Manufacturers & Marketers
              </span>
              <button
                onClick={() => setShowMfgModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-xl transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Manufacturer
              </button>
            </div>

            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="px-4 py-3">Company Name</th>
                    <th className="px-4 py-3">Short Code</th>
                    <th className="px-4 py-3">Contact Email</th>
                    <th className="px-4 py-3">Phone</th>
                    <th className="px-4 py-3 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {manufacturers.map((m) => (
                    <tr key={m.id} className="hover:bg-slate-850/40 transition">
                      <td className="px-4 py-3 font-semibold text-white">{m.name}</td>
                      <td className="px-4 py-3 font-mono text-slate-400">{m.short_code || "—"}</td>
                      <td className="px-4 py-3 text-slate-400">{m.contact_email || "—"}</td>
                      <td className="px-4 py-3 text-slate-400">{m.contact_phone || "—"}</td>
                      <td className="px-4 py-3 text-center">
                        {m.is_active ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 font-semibold">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-500">
                            Inactive
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── TAB 4: Units ────────────────────────────────────────────────────── */}
        {activeTab === "units" && (
          <div className="space-y-4 max-w-4xl">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 font-semibold uppercase tracking-wider">
                Dispensing and Packaging Units
              </span>
              <button
                onClick={() => setShowUnitModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs rounded-xl transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                Add Unit
              </button>
            </div>

            <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="px-4 py-3">Unit Name</th>
                    <th className="px-4 py-3">Abbreviation</th>
                    <th className="px-4 py-3">Fractional Splitting Allowed?</th>
                    <th className="px-4 py-3 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300">
                  {units.map((u) => (
                    <tr key={u.id} className="hover:bg-slate-850/40 transition">
                      <td className="px-4 py-3 font-semibold text-white">{u.name}</td>
                      <td className="px-4 py-3 font-mono font-bold text-slate-300">{u.abbreviation}</td>
                      <td className="px-4 py-3">
                        {u.is_fractional_allowed ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 font-semibold">
                            Yes (e.g. ml, gm)
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 font-semibold">
                            Indivisible Whole Unit (e.g. Tab, Cap)
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {u.is_active ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 font-semibold">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-500">
                            Inactive
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>

      {/* ── MODALS FOR MASTERS CREATION ───────────────────────────────────────── */}

      {/* Tax Rate Modal */}
      <AnimatePresence>
        {showTaxModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
                <h3 className="text-sm font-bold text-white">Create GST Tax Slab</h3>
                <button onClick={() => setShowTaxModal(false)} className="text-slate-400 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <form onSubmit={handleCreateTax} className="p-6 space-y-4 text-xs">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 rounded-xl">
                    {formError}
                  </div>
                )}
                <div>
                  <label className="block text-slate-400 mb-1">Slab Name *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. GST 12%"
                    value={taxForm.name}
                    onChange={(e) => setTaxForm({ ...taxForm, name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Total Rate Percentage (%) *</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    required
                    value={taxForm.rate_percentage}
                    onChange={(e) => setTaxForm({ ...taxForm, rate_percentage: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 font-mono focus:outline-none focus:border-emerald-500"
                  />
                  <span className="text-[10px] text-slate-500 mt-1 block">
                    Auto-splits into 50% CGST and 50% SGST for intra-state transactions.
                  </span>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowTaxModal(false)}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl"
                  >
                    {formSubmitting ? "Saving..." : "Save Slab"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* Category Modal */}
      <AnimatePresence>
        {showCatModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
                <h3 className="text-sm font-bold text-white">Create Category</h3>
                <button onClick={() => setShowCatModal(false)} className="text-slate-400 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <form onSubmit={handleCreateCategory} className="p-6 space-y-4 text-xs">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 rounded-xl">
                    {formError}
                  </div>
                )}
                <div>
                  <label className="block text-slate-400 mb-1">Category Name *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Antibiotics"
                    value={catForm.name}
                    onChange={(e) => setCatForm({ ...catForm, name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Description</label>
                  <textarea
                    rows={2}
                    value={catForm.description}
                    onChange={(e) => setCatForm({ ...catForm, description: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowCatModal(false)}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl"
                  >
                    {formSubmitting ? "Saving..." : "Save Category"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* Manufacturer Modal */}
      <AnimatePresence>
        {showMfgModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
                <h3 className="text-sm font-bold text-white">Create Manufacturer</h3>
                <button onClick={() => setShowMfgModal(false)} className="text-slate-400 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <form onSubmit={handleCreateMfg} className="p-6 space-y-4 text-xs">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 rounded-xl">
                    {formError}
                  </div>
                )}
                <div>
                  <label className="block text-slate-400 mb-1">Company / Manufacturer Name *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Cipla Limited"
                    value={mfgForm.name}
                    onChange={(e) => setMfgForm({ ...mfgForm, name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Short Code</label>
                  <input
                    type="text"
                    placeholder="e.g. CIPLA"
                    value={mfgForm.short_code}
                    onChange={(e) => setMfgForm({ ...mfgForm, short_code: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 uppercase font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Contact Email</label>
                  <input
                    type="email"
                    value={mfgForm.contact_email}
                    onChange={(e) => setMfgForm({ ...mfgForm, contact_email: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Contact Phone</label>
                  <input
                    type="text"
                    value={mfgForm.contact_phone}
                    onChange={(e) => setMfgForm({ ...mfgForm, contact_phone: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowMfgModal(false)}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl"
                  >
                    {formSubmitting ? "Saving..." : "Save Manufacturer"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* Unit Modal */}
      <AnimatePresence>
        {showUnitModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
                <h3 className="text-sm font-bold text-white">Create Unit of Measure</h3>
                <button onClick={() => setShowUnitModal(false)} className="text-slate-400 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <form onSubmit={handleCreateUnit} className="p-6 space-y-4 text-xs">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 rounded-xl">
                    {formError}
                  </div>
                )}
                <div>
                  <label className="block text-slate-400 mb-1">Unit Name *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Tablet, Bottle, Milliliter"
                    value={unitForm.name}
                    onChange={(e) => setUnitForm({ ...unitForm, name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Abbreviation *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. TAB, BTL, ML"
                    value={unitForm.abbreviation}
                    onChange={(e) => setUnitForm({ ...unitForm, abbreviation: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 uppercase font-mono focus:outline-none focus:border-emerald-500"
                  />
                </div>

                <div className="flex items-center gap-2 pt-2">
                  <input
                    type="checkbox"
                    id="fractAllowed"
                    checked={unitForm.is_fractional_allowed}
                    onChange={(e) => setUnitForm({ ...unitForm, is_fractional_allowed: e.target.checked })}
                    className="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0"
                  />
                  <label htmlFor="fractAllowed" className="text-slate-300">
                    Allow fractional quantities (e.g. liquids, powders, syrups)
                  </label>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowUnitModal(false)}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl"
                  >
                    {formSubmitting ? "Saving..." : "Save Unit"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
