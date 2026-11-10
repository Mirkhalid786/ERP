"use client";

import { useEffect, useState, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Pill,
  Search,
  Plus,
  Filter,
  Barcode,
  AlertCircle,
  CheckCircle2,
  Package,
  Layers,
  ChevronRight,
  ChevronLeft,
  ChevronsLeft,
  ChevronsRight,
  Loader2,
  X,
  ExternalLink,
  ShieldAlert,
  ArrowRightLeft,
  Trash2,
  Tag,
  Building,
} from "lucide-react";
import {
  api,
  CurrentUser,
  MedicineItem,
  CategoryItem,
  ManufacturerItem,
  TaxRateItem,
  UnitItem,
  MedicineBarcodeItem,
  MedicineUnitConversionItem,
  MedicineBatchItem,
} from "@/lib/api";
import { Navbar } from "@/components/Navbar";

export default function MedicinesPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [medicines, setMedicines] = useState<MedicineItem[]>([]);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [manufacturers, setManufacturers] = useState<ManufacturerItem[]>([]);
  const [taxRates, setTaxRates] = useState<TaxRateItem[]>([]);
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters & Pagination
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<number | "all">("all");
  const [scheduleFilter, setScheduleFilter] = useState<string>("all");
  const [discontinuedFilter, setDiscontinuedFilter] = useState<"all" | "active" | "discontinued">("all");
  const [prescriptionOnly, setPrescriptionOnly] = useState(false);
  const [lowStockOnly, setLowStockOnly] = useState(false);

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [totalMedicines, setTotalMedicines] = useState(0);
  const [tableLoading, setTableLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Barcode quick search
  const [barcodeQuery, setBarcodeQuery] = useState("");
  const [barcodeSearchLoading, setBarcodeSearchLoading] = useState(false);
  const [barcodeError, setBarcodeError] = useState("");

  // Modals state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedMedicine, setSelectedMedicine] = useState<MedicineItem | null>(null);
  const [activeDetailTab, setActiveDetailTab] = useState<"info" | "barcodes" | "conversions" | "batches">("info");

  // Sub-items for selected medicine
  const [medBarcodes, setMedBarcodes] = useState<MedicineBarcodeItem[]>([]);
  const [medConversions, setMedConversions] = useState<MedicineUnitConversionItem[]>([]);
  const [medBatches, setMedBatches] = useState<MedicineBatchItem[]>([]);
  const [subLoading, setSubLoading] = useState(false);

  // Form states
  const [formData, setFormData] = useState({
    name: "",
    brand_name: "",
    generic_name: "",
    strength: "",
    dosage_form: "Tablet",
    category_id: "",
    manufacturer_id: "",
    tax_rate_id: "",
    hsn_code: "3004",
    schedule_type: "NONE",
    requires_prescription: false,
    base_unit_id: "",
    barcode: "",
    reorder_level: "20",
    reorder_quantity: "50",
    storage_instructions: "",
  });
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  // New alternate barcode form
  const [newBarcode, setNewBarcode] = useState({ barcode: "", description: "" });
  // New conversion form
  const [newConv, setNewConv] = useState({
    packaging_unit_id: "",
    conversion_factor: "10",
    is_sales_unit: true,
    is_purchase_unit: true,
  });

  const loadData = async () => {
    try {
      setLoading(true);
      const currentUser = await api.getMe();
      setUser(currentUser);

      const [catsRes, mfgsRes, taxesRes, unitsRes] = await Promise.all([
        api.getCategories(),
        api.getManufacturers(),
        api.getTaxRates(),
        api.getUnits(),
      ]);

      setCategories(catsRes);
      setManufacturers(mfgsRes);
      setTaxRates(taxesRes);
      setUnits(unitsRes);

      if (unitsRes.length > 0 && !formData.base_unit_id) {
        setFormData((prev) => ({ ...prev, base_unit_id: String(unitsRes[0].id) }));
      }
    } catch {
      router.replace("/login");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);

  // Reset pagination to page 1 whenever search query or filters change
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, categoryFilter, scheduleFilter, discontinuedFilter, prescriptionOnly, lowStockOnly]);

  // Fetch medicines with server-side pagination & filters
  const fetchMedicines = async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      setTableLoading(true);
      const res = await api.getMedicinesPage(
        {
          search: debouncedSearch.trim() || undefined,
          category_id: categoryFilter === "all" ? undefined : categoryFilter,
          schedule_type: scheduleFilter === "all" ? undefined : scheduleFilter,
          requires_prescription: prescriptionOnly ? true : undefined,
          discontinued:
            discontinuedFilter === "all"
              ? undefined
              : discontinuedFilter === "discontinued",
          low_stock_only: lowStockOnly ? true : undefined,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        },
        { signal: controller.signal }
      );
      setMedicines(res.items);
      setTotalMedicines(res.total);
    } catch (err: any) {
      if (err?.name === "AbortError" || err?.message?.includes("aborted")) {
        return; // Stale request cancelled; ignore
      }
      console.error("Failed to fetch medicines:", err);
    } finally {
      if (abortControllerRef.current === controller) {
        setTableLoading(false);
      }
    }
  };

  useEffect(() => {
    if (user) {
      fetchMedicines();
    }
  }, [user, debouncedSearch, categoryFilter, scheduleFilter, discontinuedFilter, prescriptionOnly, lowStockOnly, page, pageSize]);

  // Handle barcode quick scan
  const handleBarcodeLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!barcodeQuery.trim()) return;
    setBarcodeError("");
    setBarcodeSearchLoading(true);
    try {
      const med = await api.lookupBarcode(barcodeQuery.trim());
      openMedicineDetail(med);
      setBarcodeQuery("");
    } catch (err: any) {
      setBarcodeError(err.detail || "No medicine found matching this barcode");
    } finally {
      setBarcodeSearchLoading(false);
    }
  };

  // Open medicine details modal
  const openMedicineDetail = async (med: MedicineItem) => {
    setSelectedMedicine(med);
    setActiveDetailTab("info");
    setSubLoading(true);
    try {
      const [barcodes, convs, batches] = await Promise.all([
        api.getMedicineBarcodes(med.id),
        api.getMedicineConversions(med.id),
        api.getMedicineBatches(med.id),
      ]);
      setMedBarcodes(barcodes);
      setMedConversions(convs);
      setMedBatches(batches);
    } catch (err) {
      console.error(err);
    } finally {
      setSubLoading(false);
    }
  };

  // Handle create medicine submit
  const handleCreateMedicine = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setFormSubmitting(true);
    try {
      await api.createMedicine({
        name: formData.name.trim(),
        brand_name: formData.brand_name.trim() || undefined,
        generic_name: formData.generic_name.trim() || undefined,
        strength: formData.strength.trim() || undefined,
        dosage_form: formData.dosage_form.trim(),
        category_id: formData.category_id ? Number(formData.category_id) : undefined,
        manufacturer_id: formData.manufacturer_id ? Number(formData.manufacturer_id) : undefined,
        tax_rate_id: formData.tax_rate_id ? Number(formData.tax_rate_id) : undefined,
        hsn_code: formData.hsn_code.trim() || "3004",
        schedule_type: formData.schedule_type,
        requires_prescription: formData.requires_prescription,
        base_unit_id: Number(formData.base_unit_id),
        barcode: formData.barcode.trim() || undefined,
        reorder_level: Number(formData.reorder_level) || 0,
        reorder_quantity: Number(formData.reorder_quantity) || 0,
        storage_instructions: formData.storage_instructions.trim() || undefined,
      });

      setShowCreateModal(false);
      setFormData({
        name: "",
        brand_name: "",
        generic_name: "",
        strength: "",
        dosage_form: "Tablet",
        category_id: "",
        manufacturer_id: "",
        tax_rate_id: "",
        hsn_code: "3004",
        schedule_type: "NONE",
        requires_prescription: false,
        base_unit_id: units[0]?.id ? String(units[0].id) : "",
        barcode: "",
        reorder_level: "20",
        reorder_quantity: "50",
        storage_instructions: "",
      });
      await fetchMedicines();
    } catch (err: any) {
      setFormError(err.detail || "Failed to create medicine");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Handle add barcode to selected medicine
  const handleAddBarcode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMedicine || !newBarcode.barcode.trim()) return;
    try {
      const added = await api.addMedicineBarcode(selectedMedicine.id, {
        barcode: newBarcode.barcode.trim(),
        description: newBarcode.description.trim() || undefined,
      });
      setMedBarcodes((prev) => [...prev, added]);
      setNewBarcode({ barcode: "", description: "" });
    } catch (err: any) {
      alert(err.detail || "Could not add barcode");
    }
  };

  const handleDeleteBarcode = async (barcodeId: number) => {
    if (!selectedMedicine) return;
    try {
      await api.deleteMedicineBarcode(selectedMedicine.id, barcodeId);
      setMedBarcodes((prev) => prev.filter((b) => b.id !== barcodeId));
    } catch (err: any) {
      alert(err.detail || "Could not delete barcode");
    }
  };

  // Handle add conversion to selected medicine
  const handleAddConversion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMedicine || !newConv.packaging_unit_id) return;
    try {
      const added = await api.addMedicineConversion(selectedMedicine.id, {
        packaging_unit_id: Number(newConv.packaging_unit_id),
        conversion_factor: Number(newConv.conversion_factor),
        is_sales_unit: newConv.is_sales_unit,
        is_purchase_unit: newConv.is_purchase_unit,
      });
      setMedConversions((prev) => [...prev, added]);
      setNewConv({
        packaging_unit_id: "",
        conversion_factor: "10",
        is_sales_unit: true,
        is_purchase_unit: true,
      });
    } catch (err: any) {
      alert(err.detail || "Could not configure unit conversion");
    }
  };

  const handleDeleteConversion = async (conversionId: number) => {
    if (!selectedMedicine) return;
    try {
      await api.deleteMedicineConversion(selectedMedicine.id, conversionId);
      setMedConversions((prev) => prev.filter((c) => c.id !== conversionId));
    } catch (err: any) {
      alert(err.detail || "Could not remove unit conversion");
    }
  };

  const getScheduleBadge = (type: string) => {
    switch (type) {
      case "H":
        return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">Sched H</span>;
      case "H1":
        return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">Sched H1</span>;
      case "X":
        return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-red-600/20 text-red-400 border border-red-600/30">Sched X</span>;
      case "NARCOTIC":
        return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">Narcotic</span>;
      case "OTC":
        return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">OTC</span>;
      default:
        return <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400">Regular</span>;
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
        {/* Top Header & Fast Barcode Scan */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
              <Pill className="w-6 h-6 text-emerald-400" />
              Medicine Catalogue
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Authorized formulations, schedules, packaging conversions, and primary barcodes.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {user?.permissions.includes("medicines.manage") && (
              <a href="/medicines/import" className="px-3 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-xl text-xs font-semibold">
                Import catalogue
              </a>
            )}
            {/* Quick Barcode Scanner Form */}
            <form onSubmit={handleBarcodeLookup} className="relative flex items-center">
              <div className="relative">
                <Barcode className="w-4 h-4 text-emerald-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Scan or enter barcode..."
                  value={barcodeQuery}
                  onChange={(e) => setBarcodeQuery(e.target.value)}
                  className="pl-9 pr-3 py-2 bg-slate-900 border border-slate-700/80 rounded-xl text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 w-52 sm:w-64"
                />
              </div>
              <button
                type="submit"
                disabled={barcodeSearchLoading}
                className="ml-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-xs font-medium transition cursor-pointer border border-slate-700"
              >
                {barcodeSearchLoading ? "..." : "Lookup"}
              </button>
            </form>

            <button
              onClick={() => setShowCreateModal(true)}
              className="flex items-center gap-2 px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-xs rounded-xl shadow-lg shadow-emerald-500/20 transition cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              Add Medicine
            </button>
          </div>
        </div>

        {barcodeError && (
          <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs rounded-xl flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{barcodeError}</span>
            </div>
            <button onClick={() => setBarcodeError("")} className="hover:text-white">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Search & Filters Card */}
        <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-2xl backdrop-blur space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3">
            {/* Search */}
            <div className="lg:col-span-2 relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search brand (e.g. Dolo 650, Crocin), salt, strength, manufacturer..."
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
                className="w-full pl-9 pr-8 py-2 bg-slate-950/70 border border-slate-800 rounded-xl text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => {
                    setSearch("");
                    setPage(1);
                  }}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            {/* Category Filter */}
            <div>
              <select
                value={categoryFilter}
                onChange={(e) => {
                  setCategoryFilter(e.target.value === "all" ? "all" : Number(e.target.value));
                  setPage(1);
                }}
                className="w-full px-3 py-2 bg-slate-950/70 border border-slate-800 rounded-xl text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
              >
                <option value="all">All Categories</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Schedule Filter */}
            <div>
              <select
                value={scheduleFilter}
                onChange={(e) => {
                  setScheduleFilter(e.target.value);
                  setPage(1);
                }}
                className="w-full px-3 py-2 bg-slate-950/70 border border-slate-800 rounded-xl text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
              >
                <option value="all">All Schedules</option>
                <option value="H">Schedule H (Prescription)</option>
                <option value="H1">Schedule H1 (Monitored)</option>
                <option value="X">Schedule X (Psychotropic)</option>
                <option value="NARCOTIC">Narcotics</option>
                <option value="OTC">Over The Counter (OTC)</option>
                <option value="NONE">General Medicine</option>
              </select>
            </div>

            {/* Status / Marketed Filter */}
            <div>
              <select
                value={discontinuedFilter}
                onChange={(e) => {
                  setDiscontinuedFilter(e.target.value as "all" | "active" | "discontinued");
                  setPage(1);
                }}
                className="w-full px-3 py-2 bg-slate-950/70 border border-slate-800 rounded-xl text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
              >
                <option value="all">All Formulations</option>
                <option value="active">Actively Marketed</option>
                <option value="discontinued">Discontinued Only</option>
              </select>
            </div>

            {/* Checkbox toggles */}
            <div className="flex items-center gap-4 text-xs text-slate-300">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={prescriptionOnly}
                  onChange={(e) => {
                    setPrescriptionOnly(e.target.checked);
                    setPage(1);
                  }}
                  className="rounded border-slate-700 bg-slate-900 text-emerald-500 focus:ring-0"
                />
                <span>Rx Only</span>
              </label>

              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={lowStockOnly}
                  onChange={(e) => {
                    setLowStockOnly(e.target.checked);
                    setPage(1);
                  }}
                  className="rounded border-slate-700 bg-slate-900 text-amber-500 focus:ring-0"
                />
                <span className="text-amber-300">Low Stock</span>
              </label>
            </div>
          </div>
        </div>

        {/* Medicines Table */}
        <div className="bg-slate-900/40 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-900/90 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                <tr>
                  <th className="px-4 py-3.5">Medicine & Generic / Salt</th>
                  <th className="px-4 py-3.5">Dosage / Form</th>
                  <th className="px-4 py-3.5">Classification & Status</th>
                  <th className="px-4 py-3.5">Category & Manufacturer</th>
                  <th className="px-4 py-3.5">MRP & Unit</th>
                  <th className="px-4 py-3.5 text-right">Current Stock</th>
                  <th className="px-4 py-3.5 text-center">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {tableLoading && medicines.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                      <div className="flex flex-col items-center justify-center gap-2">
                        <Loader2 className="w-6 h-6 text-emerald-400 animate-spin" />
                        <span>Searching medicine catalogue...</span>
                      </div>
                    </td>
                  </tr>
                ) : medicines.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-slate-500">
                      <div className="max-w-md mx-auto space-y-1">
                        <p className="font-medium text-slate-400">No medicines match the selected filter criteria.</p>
                        <p className="text-[11px] text-slate-500">
                          Try adjusting your search terms, clearing schedule/category filters, or switching formulation status.
                        </p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  medicines.map((m) => {
                    const isLow = Number(m.current_stock_base) <= Number(m.reorder_level);
                    return (
                      <tr
                        key={m.id}
                        className="hover:bg-slate-850/40 transition group cursor-pointer"
                        onClick={() => openMedicineDetail(m)}
                      >
                        <td className="px-4 py-3">
                          <div className="font-semibold text-white group-hover:text-emerald-400 transition">
                            {m.name}
                          </div>
                          {m.brand_name && m.brand_name.toLowerCase() !== m.name.toLowerCase() && (
                            <div className="text-[10px] text-emerald-400 font-medium">
                              Brand: {m.brand_name}
                            </div>
                          )}
                          {m.generic_name && (
                            <div className="text-[11px] text-slate-400 line-clamp-2 max-w-sm mt-0.5">
                              {m.generic_name}
                            </div>
                          )}
                          {m.barcode && (
                            <div className="text-[10px] text-slate-500 font-mono flex items-center gap-1 mt-0.5">
                              <Barcode className="w-3 h-3" /> {m.barcode}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-medium text-slate-200">{m.dosage_form}</div>
                          {m.strength && <div className="text-[11px] text-slate-400">{m.strength}</div>}
                          {m.pack_size_label && (
                            <div className="text-[10px] text-slate-500 mt-0.5">{m.pack_size_label}</div>
                          )}
                        </td>
                        <td className="px-4 py-3 space-y-1">
                          <div className="flex flex-wrap items-center gap-1">
                            {getScheduleBadge(m.schedule_type)}
                            {m.is_discontinued ? (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                                Discontinued
                              </span>
                            ) : (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                                Active
                              </span>
                            )}
                          </div>
                          {m.requires_prescription && (
                            <div className="text-[10px] text-amber-400 font-medium">Rx Mandatory</div>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-slate-200">{m.category_name || "—"}</div>
                          <div className="text-[11px] text-slate-400 font-medium truncate max-w-[180px]">
                            {m.manufacturer_name || "—"}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          {m.mrp ? (
                            <div className="font-semibold text-slate-200">
                              ₹{Number(m.mrp).toFixed(2)}
                            </div>
                          ) : (
                            <div className="text-slate-500">—</div>
                          )}
                          <div className="text-[10px] text-slate-400 font-mono mt-0.5">
                            {m.base_unit_name} ({m.base_unit_abbr})
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className={`font-mono font-semibold ${isLow ? "text-amber-400" : "text-emerald-400"}`}>
                            {Number(m.current_stock_base).toFixed(2)}
                          </div>
                          {isLow && (
                            <div className="text-[10px] text-amber-500">
                              ≤ Reorder ({Number(m.reorder_level).toFixed(0)})
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                          <button
                            onClick={() => openMedicineDetail(m)}
                            className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-[11px] font-medium transition cursor-pointer"
                          >
                            Manage
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination Toolbar */}
          <div className="px-4 py-3 bg-slate-900/60 border-t border-slate-800 text-xs text-slate-400 flex flex-col sm:flex-row justify-between items-center gap-3">
            <div className="flex items-center gap-2">
              <span>
                Showing{" "}
                <span className="font-semibold text-slate-200">
                  {totalMedicines === 0 ? 0 : (page - 1) * pageSize + 1}
                </span>
                –
                <span className="font-semibold text-slate-200">
                  {Math.min(page * pageSize, totalMedicines)}
                </span>{" "}
                of{" "}
                <span className="font-semibold text-emerald-400">
                  {totalMedicines.toLocaleString()}
                </span>{" "}
                registered medicines
              </span>
              {tableLoading && <Loader2 className="w-3.5 h-3.5 text-emerald-400 animate-spin" />}
            </div>

            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 text-xs text-slate-400">
                <span>Per page:</span>
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="px-2 py-1 bg-slate-950/80 border border-slate-800 rounded-lg text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
                >
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                </select>
              </div>

              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPage(1)}
                  disabled={page <= 1 || tableLoading}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-300 transition"
                  title="First Page"
                >
                  <ChevronsLeft className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1 || tableLoading}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-300 transition"
                  title="Previous Page"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <span className="px-2 text-xs font-medium text-slate-300">
                  Page {page} of {Math.max(1, Math.ceil(totalMedicines / pageSize))}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(Math.ceil(totalMedicines / pageSize), p + 1))}
                  disabled={page >= Math.ceil(totalMedicines / pageSize) || tableLoading}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-300 transition"
                  title="Next Page"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage(Math.max(1, Math.ceil(totalMedicines / pageSize)))}
                  disabled={page >= Math.ceil(totalMedicines / pageSize) || tableLoading}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-300 transition"
                  title="Last Page"
                >
                  <ChevronsRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* ── MODAL: Create Medicine ────────────────────────────────────────────── */}
      <AnimatePresence>
        {showCreateModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm overflow-y-auto">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl my-8"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
                <div className="flex items-center gap-2">
                  <Pill className="w-5 h-5 text-emerald-400" />
                  <h2 className="text-base font-bold text-white">Add Medicine Formulation</h2>
                </div>
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="p-1 text-slate-400 hover:text-white rounded-lg"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form onSubmit={handleCreateMedicine} className="p-6 space-y-4 max-h-[80vh] overflow-y-auto">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs rounded-xl flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    <span>{formError}</span>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                  <div>
                    <label className="block text-slate-400 mb-1">Product / Trade Name *</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. Augmentin 625 Duo"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Brand Name</label>
                    <input
                      type="text"
                      placeholder="e.g. Augmentin"
                      value={formData.brand_name}
                      onChange={(e) => setFormData({ ...formData, brand_name: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div className="sm:col-span-2">
                    <label className="block text-slate-400 mb-1">Generic / Active Ingredients</label>
                    <input
                      type="text"
                      placeholder="e.g. Amoxicillin and Potassium Clavulanate"
                      value={formData.generic_name}
                      onChange={(e) => setFormData({ ...formData, generic_name: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Strength</label>
                    <input
                      type="text"
                      placeholder="e.g. 500mg + 125mg"
                      value={formData.strength}
                      onChange={(e) => setFormData({ ...formData, strength: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Dosage Form *</label>
                    <select
                      value={formData.dosage_form}
                      onChange={(e) => setFormData({ ...formData, dosage_form: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="Tablet">Tablet</option>
                      <option value="Capsule">Capsule</option>
                      <option value="Syrup">Syrup</option>
                      <option value="Suspension">Suspension</option>
                      <option value="Injection">Injection</option>
                      <option value="Ointment">Ointment / Cream</option>
                      <option value="Drops">Drops</option>
                      <option value="Inhaler">Inhaler</option>
                      <option value="Powder">Powder</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Category</label>
                    <select
                      value={formData.category_id}
                      onChange={(e) => setFormData({ ...formData, category_id: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="">Select Category...</option>
                      {categories.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Manufacturer</label>
                    <select
                      value={formData.manufacturer_id}
                      onChange={(e) => setFormData({ ...formData, manufacturer_id: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="">Select Manufacturer...</option>
                      {manufacturers.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Base Dispensing Unit *</label>
                    <select
                      value={formData.base_unit_id}
                      onChange={(e) => setFormData({ ...formData, base_unit_id: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      {units.map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.name} ({u.abbreviation})
                        </option>
                      ))}
                    </select>
                    <span className="text-[10px] text-slate-500">Stock balance is held in this unit.</span>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">GST Tax Slab</label>
                    <select
                      value={formData.tax_rate_id}
                      onChange={(e) => setFormData({ ...formData, tax_rate_id: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="">Select Tax Slab...</option>
                      {taxRates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.rate_percentage}%)
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">HSN Code</label>
                    <input
                      type="text"
                      placeholder="3004"
                      value={formData.hsn_code}
                      onChange={(e) => setFormData({ ...formData, hsn_code: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Primary Barcode</label>
                    <input
                      type="text"
                      placeholder="890..."
                      value={formData.barcode}
                      onChange={(e) => setFormData({ ...formData, barcode: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Drug Schedule</label>
                    <select
                      value={formData.schedule_type}
                      onChange={(e) => setFormData({ ...formData, schedule_type: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="NONE">General Medicine</option>
                      <option value="H">Schedule H</option>
                      <option value="H1">Schedule H1</option>
                      <option value="X">Schedule X</option>
                      <option value="G">Schedule G</option>
                      <option value="NARCOTIC">Narcotics & Psychotropics</option>
                      <option value="OTC">OTC</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-slate-400 mb-1">Reorder Level (Base Units)</label>
                    <input
                      type="number"
                      step="1"
                      value={formData.reorder_level}
                      onChange={(e) => setFormData({ ...formData, reorder_level: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div className="sm:col-span-2 flex items-center gap-2 pt-2">
                    <input
                      type="checkbox"
                      id="rxReq"
                      checked={formData.requires_prescription}
                      onChange={(e) => setFormData({ ...formData, requires_prescription: e.target.checked })}
                      className="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0"
                    />
                    <label htmlFor="rxReq" className="text-slate-300 font-medium">
                      Requires Registered Medical Practitioner Prescription (Rx)
                    </label>
                  </div>
                </div>

                <div className="pt-4 border-t border-slate-800 flex justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold rounded-xl"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-5 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl shadow-lg shadow-emerald-500/20"
                  >
                    {formSubmitting ? "Saving..." : "Save Medicine"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── MODAL / DRAWER: Medicine Details, Barcodes, Conversions & Batches ──── */}
      <AnimatePresence>
        {selectedMedicine && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm overflow-y-auto">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl overflow-hidden shadow-2xl my-8"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-bold text-white">{selectedMedicine.name}</h2>
                    {getScheduleBadge(selectedMedicine.schedule_type)}
                  </div>
                  <div className="text-xs text-slate-400">{selectedMedicine.generic_name || "No generic specified"}</div>
                </div>
                <button
                  onClick={() => setSelectedMedicine(null)}
                  className="p-1 text-slate-400 hover:text-white rounded-lg"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Sub-tabs */}
              <div className="flex border-b border-slate-800 px-6 gap-6 text-xs bg-slate-950/40">
                <button
                  onClick={() => setActiveDetailTab("info")}
                  className={`py-3 font-semibold border-b-2 transition ${
                    activeDetailTab === "info"
                      ? "border-emerald-400 text-emerald-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Formulation Details
                </button>
                <button
                  onClick={() => setActiveDetailTab("barcodes")}
                  className={`py-3 font-semibold border-b-2 transition flex items-center gap-1.5 ${
                    activeDetailTab === "barcodes"
                      ? "border-emerald-400 text-emerald-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Barcode className="w-3.5 h-3.5" />
                  Barcodes ({medBarcodes.length + (selectedMedicine.barcode ? 1 : 0)})
                </button>
                <button
                  onClick={() => setActiveDetailTab("conversions")}
                  className={`py-3 font-semibold border-b-2 transition flex items-center gap-1.5 ${
                    activeDetailTab === "conversions"
                      ? "border-emerald-400 text-emerald-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <ArrowRightLeft className="w-3.5 h-3.5" />
                  Packaging Conversions ({medConversions.length})
                </button>
                <button
                  onClick={() => setActiveDetailTab("batches")}
                  className={`py-3 font-semibold border-b-2 transition flex items-center gap-1.5 ${
                    activeDetailTab === "batches"
                      ? "border-emerald-400 text-emerald-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Package className="w-3.5 h-3.5" />
                  Batches & Stock ({medBatches.length})
                </button>
              </div>

              <div className="p-6 max-h-[70vh] overflow-y-auto text-xs">
                {activeDetailTab === "info" && (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Dosage Form & Strength</span>
                      <span className="font-semibold text-slate-200">
                        {selectedMedicine.dosage_form} {selectedMedicine.strength ? `(${selectedMedicine.strength})` : ""}
                      </span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Base Dispensing Unit</span>
                      <span className="font-semibold text-slate-200">
                        {selectedMedicine.base_unit_name} ({selectedMedicine.base_unit_abbr})
                      </span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Category</span>
                      <span className="font-semibold text-slate-200">{selectedMedicine.category_name || "—"}</span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Manufacturer</span>
                      <span className="font-semibold text-slate-200">{selectedMedicine.manufacturer_name || "—"}</span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">HSN Code & GST Rate</span>
                      <span className="font-semibold text-slate-200">
                        HSN {selectedMedicine.hsn_code || "3004"} | {selectedMedicine.tax_rate_percentage || "0"}% GST
                      </span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Current Base Unit Stock</span>
                      <span className="font-semibold text-emerald-400 font-mono text-sm">
                        {Number(selectedMedicine.current_stock_base).toFixed(2)} {selectedMedicine.base_unit_abbr}
                      </span>
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Reference MRP</span>
                      <span className="font-semibold text-slate-200">
                        {selectedMedicine.mrp ? `₹${Number(selectedMedicine.mrp).toFixed(2)}` : "Not specified"}
                      </span>
                      {selectedMedicine.pack_size_label && (
                        <span className="text-slate-400 block text-[11px] mt-0.5">{selectedMedicine.pack_size_label}</span>
                      )}
                    </div>
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                      <span className="text-slate-500 block">Commercial Status</span>
                      <span className="font-semibold text-slate-200 flex items-center gap-2 mt-0.5">
                        {selectedMedicine.is_discontinued ? (
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                            Discontinued Formulation
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            Actively Marketed
                          </span>
                        )}
                      </span>
                      {selectedMedicine.source_name && (
                        <span className="text-slate-500 block text-[10px] mt-1">
                          Source: {selectedMedicine.source_name} ({selectedMedicine.source_id || "ID"})
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {activeDetailTab === "barcodes" && (
                  <div className="space-y-4">
                    {/* Primary Barcode */}
                    <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800 flex items-center justify-between">
                      <div>
                        <div className="text-[10px] text-emerald-400 uppercase font-bold tracking-wider">Primary Barcode</div>
                        <div className="font-mono text-sm font-semibold text-white mt-0.5">
                          {selectedMedicine.barcode || "None assigned"}
                        </div>
                      </div>
                      <span className="text-slate-500 text-[11px]">Primary Catalog Identifier</span>
                    </div>

                    {/* Alternate Barcodes List */}
                    <div className="space-y-2">
                      <div className="text-slate-400 font-semibold">Alternate Packaging Barcodes</div>
                      {medBarcodes.length === 0 ? (
                        <div className="text-slate-500 py-3 text-center border border-dashed border-slate-800 rounded-xl">
                          No alternate barcodes mapped to this medicine.
                        </div>
                      ) : (
                        medBarcodes.map((b) => (
                          <div
                            key={b.id}
                            className="p-2.5 bg-slate-950/60 rounded-xl border border-slate-800 flex items-center justify-between"
                          >
                            <div>
                              <span className="font-mono font-semibold text-slate-200">{b.barcode}</span>
                              {b.description && <span className="ml-2 text-slate-400">({b.description})</span>}
                            </div>
                            <button
                              onClick={() => handleDeleteBarcode(b.id)}
                              className="text-slate-500 hover:text-rose-400 p-1"
                              title="Delete barcode"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))
                      )}
                    </div>

                    {/* Add alternate barcode form */}
                    <form onSubmit={handleAddBarcode} className="pt-2 border-t border-slate-800 flex gap-2">
                      <input
                        type="text"
                        required
                        placeholder="Alternate barcode string..."
                        value={newBarcode.barcode}
                        onChange={(e) => setNewBarcode({ ...newBarcode, barcode: e.target.value })}
                        className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                      />
                      <input
                        type="text"
                        placeholder="Description (e.g. Strip, Box)"
                        value={newBarcode.description}
                        onChange={(e) => setNewBarcode({ ...newBarcode, description: e.target.value })}
                        className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                      />
                      <button
                        type="submit"
                        className="px-3 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl whitespace-nowrap cursor-pointer"
                      >
                        Add Barcode
                      </button>
                    </form>
                  </div>
                )}

                {activeDetailTab === "conversions" && (
                  <div className="space-y-4">
                    <p className="text-slate-400">
                      Define packaging units that convert directly into base units (e.g. 1 Strip = 10 Tablets).
                    </p>

                    <div className="space-y-2">
                      {medConversions.length === 0 ? (
                        <div className="text-slate-500 py-3 text-center border border-dashed border-slate-800 rounded-xl">
                          No packaging conversions configured. Stock is tracked solely in base unit (
                          {selectedMedicine.base_unit_name}).
                        </div>
                      ) : (
                        medConversions.map((c) => (
                          <div
                            key={c.id}
                            className="p-3 bg-slate-950/60 rounded-xl border border-slate-800 flex items-center justify-between"
                          >
                            <div>
                              <div className="font-semibold text-slate-200">
                                1 {c.packaging_unit_name} = {Number(c.conversion_factor)} {selectedMedicine.base_unit_name}s
                              </div>
                              <div className="text-[10px] text-slate-500 mt-0.5 space-x-2">
                                {c.is_purchase_unit && <span className="text-blue-400">Purchase Unit</span>}
                                {c.is_sales_unit && <span className="text-emerald-400">Sales Unit</span>}
                              </div>
                            </div>
                            <button
                              onClick={() => handleDeleteConversion(c.id)}
                              className="text-slate-500 hover:text-rose-400 p-1"
                              title="Delete conversion"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        ))
                      )}
                    </div>

                    {/* Add Conversion Form */}
                    <form onSubmit={handleAddConversion} className="pt-2 border-t border-slate-800 space-y-3">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-slate-400 mb-1">Packaging Unit</label>
                          <select
                            required
                            value={newConv.packaging_unit_id}
                            onChange={(e) => setNewConv({ ...newConv, packaging_unit_id: e.target.value })}
                            className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                          >
                            <option value="">Select Unit...</option>
                            {units
                              .filter((u) => u.id !== selectedMedicine.base_unit_id)
                              .map((u) => (
                                <option key={u.id} value={u.id}>
                                  {u.name} ({u.abbreviation})
                                </option>
                              ))}
                          </select>
                        </div>

                        <div>
                          <label className="block text-slate-400 mb-1">
                            Conversion Factor (Base Units per Pack)
                          </label>
                          <input
                            type="number"
                            step="0.0001"
                            min="0.0001"
                            required
                            value={newConv.conversion_factor}
                            onChange={(e) => setNewConv({ ...newConv, conversion_factor: e.target.value })}
                            className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-slate-200 focus:outline-none focus:border-emerald-500"
                          />
                        </div>
                      </div>

                      <div className="flex items-center justify-between">
                        <div className="flex gap-4">
                          <label className="flex items-center gap-1.5 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={newConv.is_purchase_unit}
                              onChange={(e) => setNewConv({ ...newConv, is_purchase_unit: e.target.checked })}
                              className="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0"
                            />
                            <span className="text-slate-300">Allow in Purchases</span>
                          </label>

                          <label className="flex items-center gap-1.5 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={newConv.is_sales_unit}
                              onChange={(e) => setNewConv({ ...newConv, is_sales_unit: e.target.checked })}
                              className="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0"
                            />
                            <span className="text-slate-300">Allow in Sales (POS)</span>
                          </label>
                        </div>

                        <button
                          type="submit"
                          className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl cursor-pointer"
                        >
                          Save Conversion
                        </button>
                      </div>
                    </form>
                  </div>
                )}

                {activeDetailTab === "batches" && (
                  <div className="space-y-3">
                    {medBatches.length === 0 ? (
                      <div className="text-slate-500 py-6 text-center border border-dashed border-slate-800 rounded-xl">
                        No batches registered yet. Record opening stock or goods receipt to create a batch.
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left">
                          <thead className="bg-slate-950 text-slate-400 text-[10px] uppercase">
                            <tr>
                              <th className="px-3 py-2">Batch No</th>
                              <th className="px-3 py-2">Expiry</th>
                              <th className="px-3 py-2">MRP</th>
                              <th className="px-3 py-2">Rack</th>
                              <th className="px-3 py-2 text-right">Stock (Base)</th>
                              <th className="px-3 py-2">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800">
                            {medBatches.map((b) => (
                              <tr key={b.id} className="hover:bg-slate-950/40">
                                <td className="px-3 py-2 font-mono font-semibold text-white">{b.batch_number}</td>
                                <td className="px-3 py-2 text-slate-300">{b.expiry_date}</td>
                                <td className="px-3 py-2 font-mono text-slate-300">₹{Number(b.mrp).toFixed(2)}</td>
                                <td className="px-3 py-2 text-slate-400">{b.rack_location || "—"}</td>
                                <td className="px-3 py-2 text-right font-mono font-semibold text-emerald-400">
                                  {Number(b.current_stock_base).toFixed(2)}
                                </td>
                                <td className="px-3 py-2">
                                  {b.is_expired ? (
                                    <span className="px-2 py-0.5 rounded text-[10px] bg-rose-500/20 text-rose-300 font-bold">
                                      EXPIRED
                                    </span>
                                  ) : (
                                    <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 font-bold">
                                      ACTIVE
                                    </span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
