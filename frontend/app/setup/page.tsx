"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, PharmacySetupPayload } from "@/lib/api";
import { Building2, UserPlus, CheckCircle2, AlertCircle } from "lucide-react";

export default function SetupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formData, setFormData] = useState<PharmacySetupPayload>({
    pharmacy_name: "",
    legal_name: "",
    gstin: "",
    drug_license_number: "",
    address_line1: "",
    city: "",
    state_code: "MH",
    pincode: "",
    phone: "",
    invoice_prefix: "INV",
    admin_name: "",
    admin_username: "admin",
    admin_password: "",
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setFormData((prev) => ({
      ...prev,
      [e.target.name]: e.target.value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await api.setupPharmacy({
        ...formData,
        gstin: formData.gstin?.trim() || undefined,
        legal_name: formData.legal_name?.trim() || undefined,
        drug_license_number: formData.drug_license_number?.trim() || undefined,
        address_line1: formData.address_line1?.trim() || undefined,
        city: formData.city?.trim() || undefined,
        pincode: formData.pincode?.trim() || undefined,
        phone: formData.phone?.trim() || undefined,
      });

      // Auto login as admin
      await api.login(formData.admin_username, formData.admin_password);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.detail || "Setup failed. Check inputs and server status.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 py-12 px-4 sm:px-6 lg:px-8 max-w-4xl mx-auto w-full">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8 sm:p-10 shadow-2xl">
        <div className="flex items-center gap-4 mb-8 pb-6 border-b border-slate-800">
          <div className="w-12 h-12 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-2xl flex items-center justify-center shrink-0">
            <Building2 className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">
              Pharmacy Initial Setup
            </h1>
            <p className="text-sm text-slate-400">
              Configure your store details and root administrator account.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl flex items-center gap-3 text-rose-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-8">
          <div>
            <h2 className="text-lg font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-slate-800 text-xs flex items-center justify-center text-slate-300">1</span>
              Pharmacy Business Profile
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Pharmacy Trade Name *
                </label>
                <input
                  type="text"
                  required
                  name="pharmacy_name"
                  placeholder="e.g. Apollo Medicals"
                  value={formData.pharmacy_name}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Legal Entity Name
                </label>
                <input
                  type="text"
                  name="legal_name"
                  placeholder="e.g. Apollo Retail Ventures Pvt Ltd"
                  value={formData.legal_name}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  GSTIN (15 characters)
                </label>
                <input
                  type="text"
                  name="gstin"
                  placeholder="27ABCDE1234F1Z5"
                  value={formData.gstin}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 uppercase transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Drug License Number
                </label>
                <input
                  type="text"
                  name="drug_license_number"
                  placeholder="e.g. DL-20B-123456"
                  value={formData.drug_license_number}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  State Code (2 digits / GST format)
                </label>
                <input
                  type="text"
                  name="state_code"
                  placeholder="27"
                  maxLength={2}
                  value={formData.state_code}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Invoice Number Prefix
                </label>
                <input
                  type="text"
                  name="invoice_prefix"
                  required
                  value={formData.invoice_prefix}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>
            </div>
          </div>

          <div className="pt-6 border-t border-slate-800">
            <h2 className="text-lg font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <span className="w-6 h-6 rounded-full bg-slate-800 text-xs flex items-center justify-center text-slate-300">2</span>
              Master Administrator Credentials
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Admin Full Name *
                </label>
                <input
                  type="text"
                  required
                  name="admin_name"
                  placeholder="e.g. Suresh Kumar"
                  value={formData.admin_name}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Username *
                </label>
                <input
                  type="text"
                  required
                  name="admin_username"
                  value={formData.admin_username}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">
                  Password (min 8 chars, 1 uppercase, 1 digit) *
                </label>
                <input
                  type="password"
                  required
                  name="admin_password"
                  placeholder="••••••••"
                  value={formData.admin_password}
                  onChange={handleChange}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-emerald-500 transition-colors"
                />
              </div>
            </div>
          </div>

          <div className="pt-4 flex justify-end">
            <button
              type="submit"
              disabled={loading}
              className="py-3 px-8 bg-emerald-600 hover:bg-emerald-500 active:scale-[0.98] disabled:opacity-50 text-on-primary font-medium rounded-xl flex items-center gap-2 shadow-xl shadow-emerald-950 transition-all cursor-pointer"
            >
              {loading ? (
                <span>Configuring Pharmacy...</span>
              ) : (
                <>
                  <CheckCircle2 className="w-5 h-5" />
                  <span>Initialize & Launch ERP</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
