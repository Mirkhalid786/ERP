"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Truck,
  Building2,
  Phone,
  Mail,
  MapPin,
  FileText,
  ShieldCheck,
  Plus,
  Search,
  Filter,
  CheckCircle2,
  XCircle,
  Edit2,
  Trash2,
  ExternalLink,
  X,
  CreditCard,
  Clock,
  AlertCircle,
  Eye,
} from "lucide-react";
import { api, CurrentUser, SupplierItem, SupplierCreatePayload, isUnauthorized, apiErrorMessage } from "@/lib/api";
import { Navbar } from "@/components/Navbar";

const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const ADDITIONAL_FIELDS = ["drug_license_no", "email", "payment_terms_days", "credit_limit", "city", "state", "pincode", "address", "notes", "is_active"];
const supInput = (error?: string) =>
  `w-full bg-slate-950 border rounded-xl px-3 py-2 text-white focus:outline-none ${error ? "border-rose-500/70 focus:border-rose-500" : "border-slate-800 focus:border-emerald-500/50"}`;

function SupplierField({ id, label, required, error, hint, children }: {
  id: string; label: string; required?: boolean; error?: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-slate-300 mb-1">
        {label} {required ? <span className="text-rose-400">*</span> : <span className="text-slate-600 font-normal">(optional)</span>}
      </label>
      {children}
      {error ? <p role="alert" className="mt-1 text-[11px] text-rose-400">{error}</p> : hint ? <p className="mt-1 text-[11px] text-slate-500">{hint}</p> : null}
    </div>
  );
}

export default function SuppliersPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "active" | "inactive">("all");

  // Modal states
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<SupplierItem | null>(null);
  const [viewingSupplier, setViewingSupplier] = useState<SupplierItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [showMore, setShowMore] = useState(false);

  // Form State
  const initialForm: SupplierCreatePayload = {
    name: "",
    legal_name: "",
    gstin: "",
    state_code: "",
    drug_license_no: "",
    contact_person: "",
    phone: "",
    email: "",
    address: "",
    city: "",
    state: "",
    pincode: "",
    payment_terms_days: 30,
    credit_limit: "0.00",
    notes: "",
    is_active: true,
  };
  const [formData, setFormData] = useState<SupplierCreatePayload>(initialForm);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const currentUser = await api.getCurrentUser();
      setUser(currentUser);

      const perms = currentUser.permissions || [];
      if (!perms.includes("suppliers.manage") && !perms.includes("purchases.view")) {
        if (perms.includes("pos.access")) {
          router.replace("/pos");
        } else if (perms.includes("stock.view")) {
          router.replace("/inventory");
        } else {
          router.replace("/medicines");
        }
        return;
      }

      const data = await api.getSuppliers();
      setSuppliers(data);
    } catch (err) {
      // Only a genuine 401 ends the session; other errors stay on the page.
      if (isUnauthorized(err)) {
        router.push("/login");
        return;
      }
      setLoadError(apiErrorMessage(err, "Failed to load suppliers. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  const filteredSuppliers = useMemo(() => {
    return suppliers.filter((s) => {
      if (activeFilter === "active" && !s.is_active) return false;
      if (activeFilter === "inactive" && s.is_active) return false;

      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        s.name.toLowerCase().includes(q) ||
        (s.legal_name && s.legal_name.toLowerCase().includes(q)) ||
        (s.gstin && s.gstin.toLowerCase().includes(q)) ||
        (s.phone && s.phone.includes(q)) ||
        (s.city && s.city.toLowerCase().includes(q))
      );
    });
  }, [suppliers, searchQuery, activeFilter]);

  const kpis = useMemo(() => {
    const total = suppliers.length;
    const active = suppliers.filter((s) => s.is_active).length;
    const totalCredit = suppliers.reduce((sum, s) => sum + Number(s.credit_limit || 0), 0);
    const avgTerms = total > 0 ? Math.round(suppliers.reduce((sum, s) => sum + s.payment_terms_days, 0) / total) : 0;
    return { total, active, totalCredit, avgTerms };
  }, [suppliers]);

  const setField = <K extends keyof SupplierCreatePayload>(key: K, value: SupplierCreatePayload[K]) => {
    setFormData((f) => ({ ...f, [key]: value }));
    setFieldErrors((e) => { if (!e[key as string]) return e; const n = { ...e }; delete n[key as string]; return n; });
  };
  // GSTIN: spaces removed, upper-case; once it is a complete valid GSTIN its first two digits are the state code
  const cleanGstin = (formData.gstin || "").replace(/[\s-]/g, "").toUpperCase();
  const gstinState = GSTIN_RE.test(cleanGstin) ? cleanGstin.slice(0, 2) : "";
  const setGstin = (raw: string) => {
    const v = raw.replace(/[\s-]/g, "").toUpperCase();
    setField("gstin", v);
    setFieldErrors((e) => {
      const n = { ...e };
      if (v && v.length >= 15 && !GSTIN_RE.test(v)) n.gstin = "This is not a valid GSTIN (format 27AABCA1234F1Z5)";
      else delete n.gstin;
      return n;
    });
  };
  const clientErrors = (): Record<string, string> => {
    const e: Record<string, string> = {};
    if (formData.name.trim().length < 2) e.name = "Enter the trade name (at least 2 characters)";
    if (cleanGstin && !GSTIN_RE.test(cleanGstin)) e.gstin = "A GSTIN has 15 characters, e.g. 27AABCA1234F1Z5. Leave it blank if the supplier has none.";
    const sc = (formData.state_code || "").trim();
    if (!gstinState && sc && !/^\d{1,2}$/.test(sc)) e.state_code = "Use the 2-digit GST state code, e.g. 27";
    return e;
  };

  const openCreateModal = () => {
    setEditingSupplier(null);
    setFormData(initialForm);
    setFormError(null);
    setFieldErrors({});
    setShowMore(false);
    setIsFormOpen(true);
  };

  const openEditModal = (supplier: SupplierItem) => {
    setEditingSupplier(supplier);
    setFormData({
      name: supplier.name,
      legal_name: supplier.legal_name || "",
      gstin: supplier.gstin || "",
      state_code: supplier.state_code || "",
      drug_license_no: supplier.drug_license_no || "",
      contact_person: supplier.contact_person || "",
      phone: supplier.phone || "",
      email: supplier.email || "",
      address: supplier.address || "",
      city: supplier.city || "",
      state: supplier.state || "",
      pincode: supplier.pincode || "",
      payment_terms_days: supplier.payment_terms_days,
      credit_limit: String(supplier.credit_limit || 0),
      notes: supplier.notes || "",
      is_active: supplier.is_active,
    });
    setFormError(null);
    setFieldErrors({});
    setShowMore(false);
    setIsFormOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const local = clientErrors();
    setFieldErrors(local);
    if (Object.keys(local).length) return;
    setSubmitting(true);
    // The state code follows a valid GSTIN; blank fields are sent blank and stored as "not provided"
    const payload = { ...formData, gstin: cleanGstin, state_code: gstinState || (formData.state_code || "").trim() };

    try {
      if (editingSupplier) {
        await api.updateSupplier(editingSupplier.id, payload);
      } else {
        await api.createSupplier(payload);
      }
      setIsFormOpen(false);
      await loadData();
    } catch (err: any) {
      const fields: Record<string, string> = err?.fields || {};
      if (Object.keys(fields).length) {
        setFieldErrors(fields);
        const hidden = Object.keys(fields).filter((k) => ADDITIONAL_FIELDS.includes(k));
        if (hidden.length) setShowMore(true);
        setFormError("Please correct the highlighted field" + (Object.keys(fields).length > 1 ? "s" : "") + ".");
      } else {
        setFormError(err.detail || err.message || "Failed to save supplier");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Are you sure you want to delete supplier '${name}'?`)) return;
    try {
      await api.deleteSupplier(id);
      await loadData();
    } catch (err: any) {
      alert(err.detail || err.message || "Failed to delete supplier");
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm font-medium text-slate-400">Loading suppliers...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6">
        {loadError && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200" role="alert">
            {loadError}
          </div>
        )}
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl">
                <Truck className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-2xl font-bold tracking-tight text-white">Suppliers & Distributors</h1>
                <p className="text-sm text-slate-400">
                  Manage vendor masters, GSTIN, wholesale drug licences (20B/21B), and credit terms
                </p>
              </div>
            </div>
          </div>

          <button
            onClick={openCreateModal}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 text-slate-950 font-semibold text-sm hover:from-emerald-400 hover:to-teal-400 transition-all shadow-lg shadow-emerald-500/20 active:scale-[0.98] cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            <span>Add Supplier</span>
          </button>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-4 backdrop-blur">
            <div className="text-xs font-medium text-slate-400">Total Suppliers</div>
            <div className="text-2xl font-bold text-white mt-1">{kpis.total}</div>
            <div className="text-[11px] text-emerald-400 mt-1 flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>{kpis.active} active vendors</span>
            </div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-4 backdrop-blur">
            <div className="text-xs font-medium text-slate-400">Active Rate</div>
            <div className="text-2xl font-bold text-emerald-400 mt-1">
              {kpis.total > 0 ? Math.round((kpis.active / kpis.total) * 100) : 0}%
            </div>
            <div className="text-[11px] text-slate-500 mt-1">Procurement operational</div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-4 backdrop-blur">
            <div className="text-xs font-medium text-slate-400">Total Credit Limit</div>
            <div className="text-2xl font-bold text-teal-400 mt-1">
              ₹{kpis.totalCredit.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
            </div>
            <div className="text-[11px] text-slate-500 mt-1">Combined vendor credit</div>
          </div>

          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-4 backdrop-blur">
            <div className="text-xs font-medium text-slate-400">Avg Credit Terms</div>
            <div className="text-2xl font-bold text-blue-400 mt-1">{kpis.avgTerms} Days</div>
            <div className="text-[11px] text-slate-500 mt-1">Standard settlement cycle</div>
          </div>
        </div>

        {/* Filter & Search Bar */}
        <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-3 sm:p-4 flex flex-col sm:flex-row items-center gap-3 justify-between">
          <div className="relative w-full sm:w-80">
            <Search className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by name, GSTIN, phone, city..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-emerald-500/50"
            />
          </div>

          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="flex bg-slate-950 p-1 border border-slate-800 rounded-xl text-xs font-medium">
              <button
                onClick={() => setActiveFilter("all")}
                className={`px-3 py-1.5 rounded-lg transition-colors ${
                  activeFilter === "all" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"
                }`}
              >
                All ({suppliers.length})
              </button>
              <button
                onClick={() => setActiveFilter("active")}
                className={`px-3 py-1.5 rounded-lg transition-colors ${
                  activeFilter === "active" ? "bg-emerald-500/20 text-emerald-400" : "text-slate-400 hover:text-white"
                }`}
              >
                Active
              </button>
              <button
                onClick={() => setActiveFilter("inactive")}
                className={`px-3 py-1.5 rounded-lg transition-colors ${
                  activeFilter === "inactive" ? "bg-rose-500/20 text-rose-400" : "text-slate-400 hover:text-white"
                }`}
              >
                Inactive
              </button>
            </div>
          </div>
        </div>

        {/* Suppliers Table */}
        <div className="bg-slate-900/80 border border-slate-800/80 rounded-2xl overflow-hidden backdrop-blur shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-950/60 text-slate-400 text-xs font-semibold uppercase tracking-wider border-b border-slate-800">
                <tr>
                  <th className="px-5 py-3.5">Supplier Details</th>
                  <th className="px-4 py-3.5">GSTIN & DL</th>
                  <th className="px-4 py-3.5">Contact Person</th>
                  <th className="px-4 py-3.5">Credit Terms</th>
                  <th className="px-4 py-3.5">Status</th>
                  <th className="px-5 py-3.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {filteredSuppliers.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-12 text-slate-500">
                      <Truck className="w-8 h-8 mx-auto mb-2 opacity-40" />
                      No suppliers found matching your query
                    </td>
                  </tr>
                ) : (
                  filteredSuppliers.map((s) => (
                    <tr key={s.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-5 py-4">
                        <div className="font-semibold text-white">{s.name}</div>
                        {s.legal_name && (
                          <div className="text-xs text-slate-400 truncate max-w-xs">{s.legal_name}</div>
                        )}
                        {s.city && (
                          <div className="text-[11px] text-slate-500 flex items-center gap-1 mt-0.5">
                            <MapPin className="w-3 h-3 text-slate-500" />
                            <span>
                              {s.city}
                              {s.state ? `, ${s.state}` : ""}
                            </span>
                          </div>
                        )}
                      </td>

                      <td className="px-4 py-4">
                        {s.gstin ? (
                          <div className="inline-flex items-center gap-1 font-mono text-xs bg-slate-950 border border-slate-800 px-2 py-0.5 rounded text-slate-300">
                            <span className="text-emerald-400 font-semibold">{s.state_code || s.gstin.slice(0, 2)}</span>
                            <span>{s.gstin.slice(2)}</span>
                          </div>
                        ) : (
                          <span className="text-xs text-slate-500 italic">Unregistered</span>
                        )}
                        {s.drug_license_no && (
                          <div className="text-[11px] text-teal-400/90 font-mono mt-1 truncate max-w-[200px]" title={s.drug_license_no}>
                            DL: {s.drug_license_no}
                          </div>
                        )}
                      </td>

                      <td className="px-4 py-4">
                        <div className="text-xs font-medium text-slate-200">
                          {s.contact_person || <span className="text-slate-500 italic">—</span>}
                        </div>
                        {s.phone && (
                          <div className="text-xs text-slate-400 flex items-center gap-1 mt-0.5">
                            <Phone className="w-3 h-3 text-slate-500" />
                            <span>{s.phone}</span>
                          </div>
                        )}
                      </td>

                      <td className="px-4 py-4">
                        <div className="text-xs font-medium text-slate-200 flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5 text-slate-500" />
                          <span>{s.payment_terms_days} Days Net</span>
                        </div>
                        <div className="text-[11px] text-slate-500 mt-0.5">
                          Limit: ₹{Number(s.credit_limit || 0).toLocaleString("en-IN")}
                        </div>
                      </td>

                      <td className="px-4 py-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                            s.is_active
                              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-400 border-rose-500/20"
                          }`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${s.is_active ? "bg-emerald-400" : "bg-rose-400"}`} />
                          {s.is_active ? "Active" : "Inactive"}
                        </span>
                      </td>

                      <td className="px-5 py-4 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => setViewingSupplier(s)}
                            className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
                            title="View Details"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => openEditModal(s)}
                            className="p-1.5 text-slate-400 hover:text-emerald-400 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
                            title="Edit"
                          >
                            <Edit2 className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(s.id, s.name)}
                            className="p-1.5 text-slate-400 hover:text-rose-400 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
                            title="Delete"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </main>

      {/* Create / Edit Modal */}
      <AnimatePresence>
        {isFormOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col max-h-[90vh]"
            >
              <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
                <div className="flex items-center gap-2.5">
                  <div className="p-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-lg">
                    <Truck className="w-4 h-4" />
                  </div>
                  <h2 className="text-lg font-bold text-white">
                    {editingSupplier ? "Edit Supplier" : "Register New Supplier"}
                  </h2>
                </div>
                <button
                  onClick={() => setIsFormOpen(false)}
                  className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form onSubmit={handleSubmit} noValidate className="p-6 overflow-y-auto space-y-4 flex-1 text-sm">
                {formError && (
                  <div role="alert" className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{formError}</span>
                  </div>
                )}
                <p className="text-xs text-slate-500">Only the trade name is required. Everything else can be added later.</p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <SupplierField id="sup-name" label="Display / Trade Name" required error={fieldErrors.name}>
                    <input id="sup-name" type="text" required autoFocus value={formData.name}
                      onChange={(e) => setField("name", e.target.value)} placeholder="e.g. Apollo Pharma Distributors"
                      aria-invalid={!!fieldErrors.name} className={supInput(fieldErrors.name)} />
                  </SupplierField>
                  <SupplierField id="sup-legal" label="Registered Legal Entity Name" error={fieldErrors.legal_name}>
                    <input id="sup-legal" type="text" value={formData.legal_name || ""}
                      onChange={(e) => setField("legal_name", e.target.value)} placeholder="e.g. Apollo Healthcare Pvt Ltd"
                      className={supInput(fieldErrors.legal_name)} />
                  </SupplierField>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="sm:col-span-2">
                    <SupplierField id="sup-gstin" label="GSTIN" error={fieldErrors.gstin}
                      hint={gstinState ? `Registered in state ${gstinState}` : "Optional · 15 characters, e.g. 27AABCA1234F1Z5"}>
                      <input id="sup-gstin" type="text" value={formData.gstin || ""}
                        onChange={(e) => setGstin(e.target.value)} placeholder="Leave blank if not GST-registered"
                        maxLength={20} aria-invalid={!!fieldErrors.gstin} className={`${supInput(fieldErrors.gstin)} font-mono uppercase`} />
                    </SupplierField>
                  </div>
                  <SupplierField id="sup-state" label="State Code" error={fieldErrors.state_code}
                    hint={gstinState ? "From the GSTIN" : "Optional, e.g. 27"}>
                    <input id="sup-state" type="text" inputMode="numeric" value={gstinState || formData.state_code || ""}
                      onChange={(e) => setField("state_code", e.target.value.replace(/\D/g, ""))} placeholder="e.g. 27"
                      maxLength={2} readOnly={!!gstinState} aria-invalid={!!fieldErrors.state_code}
                      className={`${supInput(fieldErrors.state_code)} font-mono ${gstinState ? "opacity-70" : ""}`} />
                  </SupplierField>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <SupplierField id="sup-phone" label="Phone" error={fieldErrors.phone}>
                    <input id="sup-phone" type="tel" value={formData.phone || ""}
                      onChange={(e) => setField("phone", e.target.value)} placeholder="9876543210" className={supInput(fieldErrors.phone)} />
                  </SupplierField>
                  <SupplierField id="sup-contact" label="Contact Person" error={fieldErrors.contact_person}>
                    <input id="sup-contact" type="text" value={formData.contact_person || ""}
                      onChange={(e) => setField("contact_person", e.target.value)} placeholder="Ramesh Kumar" className={supInput(fieldErrors.contact_person)} />
                  </SupplierField>
                </div>

                <div className="border border-slate-800 rounded-xl">
                  <button type="button" onClick={() => setShowMore(!showMore)} aria-expanded={showMore}
                    className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-slate-300 hover:bg-slate-800/40 rounded-xl">
                    <span>Additional details <span className="font-normal text-slate-500">— drug licence, email, payment terms, credit limit, address, notes, status</span></span>
                    <span aria-hidden="true">{showMore ? "−" : "+"}</span>
                  </button>
                  {showMore && (
                    <div className="px-4 pb-4 space-y-4">
                      <SupplierField id="sup-dl" label="Drug Licence Details (Form 20B / 21B)" error={fieldErrors.drug_license_no}>
                        <input id="sup-dl" type="text" value={formData.drug_license_no || ""}
                          onChange={(e) => setField("drug_license_no", e.target.value)} placeholder="e.g. MH-TZ1-20B-112233, MH-TZ1-21B-112234"
                          className={supInput(fieldErrors.drug_license_no)} />
                      </SupplierField>
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <SupplierField id="sup-email" label="Email" error={fieldErrors.email}>
                          <input id="sup-email" type="email" value={formData.email || ""}
                            onChange={(e) => setField("email", e.target.value)} placeholder="vendor@pharma.com" className={supInput(fieldErrors.email)} />
                        </SupplierField>
                        <SupplierField id="sup-terms" label="Payment Terms (Days)" error={fieldErrors.payment_terms_days}>
                          <input id="sup-terms" type="number" min={0} max={365} value={formData.payment_terms_days}
                            onChange={(e) => setField("payment_terms_days", parseInt(e.target.value) || 0)} className={supInput(fieldErrors.payment_terms_days)} />
                        </SupplierField>
                        <SupplierField id="sup-credit" label="Credit Limit (₹)" error={fieldErrors.credit_limit}>
                          <input id="sup-credit" type="number" min={0} step="0.01" value={formData.credit_limit}
                            onChange={(e) => setField("credit_limit", e.target.value)} className={supInput(fieldErrors.credit_limit)} />
                        </SupplierField>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <SupplierField id="sup-city" label="City" error={fieldErrors.city}>
                          <input id="sup-city" type="text" value={formData.city || ""} onChange={(e) => setField("city", e.target.value)}
                            placeholder="Mumbai" className={supInput(fieldErrors.city)} />
                        </SupplierField>
                        <SupplierField id="sup-statename" label="State" error={fieldErrors.state}>
                          <input id="sup-statename" type="text" value={formData.state || ""} onChange={(e) => setField("state", e.target.value)}
                            placeholder="Maharashtra" className={supInput(fieldErrors.state)} />
                        </SupplierField>
                        <SupplierField id="sup-pin" label="Pincode" error={fieldErrors.pincode}>
                          <input id="sup-pin" type="text" inputMode="numeric" value={formData.pincode || ""} onChange={(e) => setField("pincode", e.target.value)}
                            placeholder="400001" className={supInput(fieldErrors.pincode)} />
                        </SupplierField>
                      </div>
                      <SupplierField id="sup-address" label="Full Address" error={fieldErrors.address}>
                        <textarea id="sup-address" rows={2} value={formData.address || ""} onChange={(e) => setField("address", e.target.value)}
                          placeholder="Warehouse / Office address..." className={supInput(fieldErrors.address)} />
                      </SupplierField>
                      <SupplierField id="sup-notes" label="Notes / Instructions" error={fieldErrors.notes}>
                        <textarea id="sup-notes" rows={2} value={formData.notes || ""} onChange={(e) => setField("notes", e.target.value)}
                          placeholder="Special delivery schedules, bank details..." className={supInput(fieldErrors.notes)} />
                      </SupplierField>
                      <div className="flex items-center gap-2">
                        <input type="checkbox" id="is_active_checkbox" checked={formData.is_active}
                          onChange={(e) => setField("is_active", e.target.checked)}
                          className="rounded border-slate-700 text-emerald-500 focus:ring-emerald-500" />
                        <label htmlFor="is_active_checkbox" className="text-xs text-slate-300">
                          Active supplier (available for PO placement and inward receipt)
                        </label>
                      </div>
                    </div>
                  )}
                </div>

                <div className="pt-4 border-t border-slate-800 flex items-center justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setIsFormOpen(false)}
                    className="px-4 py-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 font-medium transition-colors cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="px-5 py-2 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 text-slate-950 font-semibold hover:from-emerald-400 hover:to-teal-400 transition-all cursor-pointer disabled:opacity-50"
                  >
                    {submitting ? "Saving..." : editingSupplier ? "Update Supplier" : "Create Supplier"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* View Detail Modal */}
      <AnimatePresence>
        {viewingSupplier && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg p-6 shadow-2xl relative"
            >
              <button
                onClick={() => setViewingSupplier(null)}
                className="absolute top-4 right-4 text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>

              <div className="flex items-center gap-3 mb-4">
                <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl">
                  <Building2 className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-white">{viewingSupplier.name}</h3>
                  <div className="text-xs text-slate-400">{viewingSupplier.legal_name || "No legal name recorded"}</div>
                </div>
              </div>

              <div className="space-y-3 text-xs divide-y divide-slate-800/60">
                <div className="pt-2 grid grid-cols-2 gap-2">
                  <div>
                    <span className="text-slate-500 block">GSTIN</span>
                    <span className="font-mono text-slate-200">{viewingSupplier.gstin || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">State Code</span>
                    <span className="font-mono text-slate-200">{viewingSupplier.state_code || "—"}</span>
                  </div>
                </div>

                <div className="pt-2">
                  <span className="text-slate-500 block">Drug License Details</span>
                  <span className="font-mono text-teal-400">{viewingSupplier.drug_license_no || "—"}</span>
                </div>

                <div className="pt-2 grid grid-cols-2 gap-2">
                  <div>
                    <span className="text-slate-500 block">Contact Person</span>
                    <span className="text-slate-200">{viewingSupplier.contact_person || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Phone</span>
                    <span className="text-slate-200">{viewingSupplier.phone || "—"}</span>
                  </div>
                </div>

                <div className="pt-2">
                  <span className="text-slate-500 block">Email</span>
                  <span className="text-slate-200">{viewingSupplier.email || "—"}</span>
                </div>

                <div className="pt-2 grid grid-cols-2 gap-2">
                  <div>
                    <span className="text-slate-500 block">Payment Terms</span>
                    <span className="text-slate-200">{viewingSupplier.payment_terms_days} Days Credit</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Credit Limit</span>
                    <span className="text-slate-200">
                      ₹{Number(viewingSupplier.credit_limit || 0).toLocaleString("en-IN")}
                    </span>
                  </div>
                </div>

                <div className="pt-2">
                  <span className="text-slate-500 block">Address</span>
                  <span className="text-slate-300">
                    {viewingSupplier.address ? `${viewingSupplier.address}, ` : ""}
                    {viewingSupplier.city ? `${viewingSupplier.city}, ` : ""}
                    {viewingSupplier.state ? `${viewingSupplier.state} ` : ""}
                    {viewingSupplier.pincode ? `- ${viewingSupplier.pincode}` : ""}
                  </span>
                </div>

                {viewingSupplier.notes && (
                  <div className="pt-2">
                    <span className="text-slate-500 block">Notes</span>
                    <span className="text-slate-400 italic">{viewingSupplier.notes}</span>
                  </div>
                )}
              </div>

              <div className="mt-6 flex items-center justify-end gap-2">
                <button
                  onClick={() => {
                    const sup = viewingSupplier;
                    setViewingSupplier(null);
                    openEditModal(sup);
                  }}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-xl transition-colors cursor-pointer"
                >
                  Edit Supplier
                </button>
                <button
                  onClick={() => setViewingSupplier(null)}
                  className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-semibold rounded-xl transition-colors cursor-pointer"
                >
                  Close
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
