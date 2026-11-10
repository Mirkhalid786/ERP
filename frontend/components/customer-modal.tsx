"use client";

import { useState } from "react";
import { Btn, ErrorBox, Field, Modal, errorText, inputCls } from "@/components/sales-ui";
import { Customer, salesApi } from "@/lib/sales-api";

export function NewCustomerModal({ open, onClose, onCreated, canCredit }: { open: boolean; onClose: () => void; onCreated: (c: Customer) => void; canCredit: boolean }) {
  const [form, setForm] = useState({ name: "", mobile: "", credit_limit: "0", payment_terms_days: "30", whatsapp_consent: false, address: "", gstin: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const c = await salesApi.createCustomer({
        name: form.name, mobile: form.mobile || null, address: form.address || null, gstin: form.gstin || null,
        credit_limit: canCredit ? form.credit_limit || "0" : "0", payment_terms_days: Number(form.payment_terms_days || 30),
        whatsapp_consent: form.whatsapp_consent,
      });
      onCreated(c);
      setForm({ name: "", mobile: "", credit_limit: "0", payment_terms_days: "30", whatsapp_consent: false, address: "", gstin: "" });
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title="New customer" onClose={onClose}>
      <ErrorBox message={err} />
      <Field label="Name *"><input autoFocus className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
      <Field label="Mobile" hint="10-digit Indian mobile or +country code"><input className={inputCls} inputMode="tel" value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Credit limit ₹" hint={canCredit ? "0 = no udhaar without approval" : "Needs manager permission"}><input className={inputCls} disabled={!canCredit} value={form.credit_limit} onChange={(e) => setForm({ ...form, credit_limit: e.target.value })} /></Field>
        <Field label="Payment terms (days)"><input className={inputCls} value={form.payment_terms_days} onChange={(e) => setForm({ ...form, payment_terms_days: e.target.value })} /></Field>
      </div>
      <Field label="Address (optional)"><input className={inputCls} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field>
      <Field label="GSTIN (business customers only)"><input className={inputCls} value={form.gstin} onChange={(e) => setForm({ ...form, gstin: e.target.value.toUpperCase() })} /></Field>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={form.whatsapp_consent} onChange={(e) => setForm({ ...form, whatsapp_consent: e.target.checked })} />
        Customer agrees to receive bills and reminders on WhatsApp
      </label>
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={form.name.trim().length < 2} onClick={save}>Save customer</Btn>
      </div>
    </Modal>
  );
}
