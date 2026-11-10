"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  Ban, BellRing, FileDown, FileText, MessageCircle, PauseCircle, Printer, Receipt, RotateCcw, ShieldCheck, Wallet,
} from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, Spinner, Table, errorText, inputCls, openTabNow, sendTabTo, td, tdNum,
  useStaff,
} from "@/components/sales-ui";
import { Invoice, Message, PrescriptionIn, fmtDate, fmtDateTime, money, newKey, num, salesApi } from "@/lib/sales-api";
import { LedgerBadge, useLedgerStatus } from "@/components/ledger-status";

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user, can } = useStaff();
  const [inv, setInv] = useState<Invoice | null>(null);
  const ledger = useLedgerStatus(inv ? [`SALE:${inv.id}`, ...(inv.status === "VOIDED" ? [`SALE_VOID:${inv.id}`] : [])] : [], can("accounting.view"));
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [modal, setModal] = useState<"" | "whatsapp" | "pay" | "void" | "dispute" | "rx">("");

  const load = useCallback(async () => {
    try {
      setInv(await salesApi.invoice(id));
      setMessages(await salesApi.invoiceMessages(Number(id)));
    } catch (e) {
      setError(errorText(e));
    }
  }, [id]);

  useEffect(() => { if (user) load(); }, [user, load]);

  const act = async (fn: () => Promise<unknown>, success?: string) => {
    setError(null);
    try {
      await fn();
      if (success) setInfo(success);
      await load();
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (!inv) {
    return (
      <SalesShell user={user} title="Invoice" icon={<Receipt className="w-6 h-6" />}>
        {error ? <ErrorBox message={error} /> : <Spinner label="Loading invoice…" />}
      </SalesShell>
    );
  }

  const open = inv.status === "FINALIZED";
  const due = num(inv.balance_due) > 0;
  const ledgerKeys = [`SALE:${inv.id}`, ...(inv.status === "VOIDED" ? [`SALE_VOID:${inv.id}`] : [])];

  return (
    <SalesShell
      user={user}
      title={inv.invoice_number}
      subtitle={`${fmtDateTime(inv.invoice_date)} · billed by ${inv.cashier_name || "—"}`}
      icon={<Receipt className="w-6 h-6" />}
      actions={
        <>
          <Btn onClick={() => window.open(`/sales/${inv.id}/print?layout=thermal&auto=1`, "_blank")}><Printer className="w-4 h-4" /> Receipt</Btn>
          <Btn onClick={() => window.open(`/sales/${inv.id}/print?layout=a4&auto=1`, "_blank")}><FileText className="w-4 h-4" /> A4</Btn>
          <a href={salesApi.pdfUrl(inv.id)} className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold border bg-slate-800 border-slate-700"><FileDown className="w-4 h-4" /> PDF</a>
          {open && can("messaging.send") && <Btn onClick={() => setModal("whatsapp")}><MessageCircle className="w-4 h-4" /> WhatsApp</Btn>}
        </>
      }
    >
      <ErrorBox message={error} onClose={() => setError(null)} />
      {info && <Notice tone="emerald">{info}</Notice>}

      <div className="flex flex-wrap gap-2 items-center">
        {ledgerKeys.map((k) => <LedgerBadge key={k} status={ledger[k]} />)}
        {inv.status === "VOIDED" ? <Badge value="VOIDED" /> : <Badge value={inv.payment_status} />}
        {inv.return_status !== "NONE" && <Badge value={inv.return_status} />}
        {inv.is_credit_sale && <Badge value="CREDIT" label="Credit sale" />}
        {inv.overdue_days > 0 && open && <Badge value="OVERDUE" label={`${inv.overdue_days} days overdue`} />}
        {inv.is_disputed && <Badge value="FAILED" label="Disputed" />}
        {inv.reminders_paused && <Badge value="CANCELLED" label="Reminders paused" />}
        <span className="text-xs text-slate-500">Printed {inv.print_count}×</span>
      </div>
      {inv.status === "VOIDED" && <Notice tone="amber">Voided {fmtDateTime(inv.voided_at)}: {inv.void_reason}</Notice>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="Customer">
          <div className="text-white font-semibold">
            {inv.customer_id ? <Link className="hover:underline text-emerald-300" href={`/customers/${inv.customer_id}`}>{inv.customer_name}</Link> : inv.customer_name}
          </div>
          <div className="text-sm text-slate-400">{inv.customer_mobile ? `+${inv.customer_mobile}` : "No mobile"}</div>
          {inv.customer_gstin && <div className="text-xs text-slate-500">GSTIN {inv.customer_gstin}</div>}
          {inv.due_date && <div className="text-sm text-slate-300 mt-2">Due {fmtDate(inv.due_date)}</div>}
          {inv.prescription_required && (
            <div className="mt-2 text-xs text-slate-400">
              Prescription recorded{can("prescriptions.view") && inv.has_prescription && (
                <> · <button className="text-emerald-300 hover:underline" onClick={() => setModal("rx")}>view details</button></>
              )}
            </div>
          )}
        </Card>

        <Card title="Amounts">
          <dl className="text-sm space-y-1">
            {[
              ["Gross (incl. GST)", inv.gross_amount], ["Discount", -num(inv.discount_total)], ["Taxable value", inv.taxable_total],
              ...(inv.is_inter_state ? [["IGST", inv.igst_total]] : [["CGST", inv.cgst_total], ["SGST", inv.sgst_total]]),
              ["Round off", inv.round_off],
            ].map(([l, v]) => <div key={l} className="flex justify-between text-slate-400"><dt>{l}</dt><dd className="font-mono">{money(v)}</dd></div>)}
            <div className="flex justify-between text-white font-bold border-t border-slate-800 pt-1"><dt>Invoice total</dt><dd className="font-mono">{money(inv.grand_total)}</dd></div>
          </dl>
        </Card>

        <Card title="Settlement">
          <dl className="text-sm space-y-1">
            <div className="flex justify-between text-slate-400"><dt>Payments applied</dt><dd className="font-mono">{money(inv.amount_paid)}</dd></div>
            {num(inv.return_credit_applied) > 0 && <div className="flex justify-between text-slate-400"><dt>Returns credited</dt><dd className="font-mono">{money(inv.return_credit_applied)}</dd></div>}
            {num(inv.written_off_amount) > 0 && <div className="flex justify-between text-slate-400"><dt>Adjusted / written off</dt><dd className="font-mono">{money(inv.written_off_amount)}</dd></div>}
            {num(inv.refunded_amount) > 0 && <div className="flex justify-between text-slate-400"><dt>Refunded</dt><dd className="font-mono">{money(inv.refunded_amount)}</dd></div>}
            <div className={`flex justify-between font-bold border-t border-slate-800 pt-1 ${due ? "text-amber-300" : "text-emerald-300"}`}><dt>Balance due</dt><dd className="font-mono">{money(inv.balance_due)}</dd></div>
          </dl>
          <div className="flex flex-wrap gap-2 mt-3">
            {open && due && inv.customer_id && can("payments.receive") && <Btn variant="primary" onClick={() => setModal("pay")}><Wallet className="w-4 h-4" /> Collect payment</Btn>}
            {open && due && inv.customer_id && can("messaging.send") && (
              <Btn onClick={() => {
                const tab = openTabNow();
                act(async () => {
                  try {
                    sendTabTo(tab, (await salesApi.sendReminder(inv.id)).share_url);
                  } catch (e) {
                    sendTabTo(tab, null);
                    throw e;
                  }
                }, "Reminder created. In manual mode WhatsApp opened for you to send it.");
              }}><BellRing className="w-4 h-4" /> Reminder</Btn>
            )}
            {open && due && can("payments.receive") && (
              <>
                <Btn variant="ghost" onClick={() => act(() => salesApi.reminderControl(inv.id, { reminders_paused: !inv.reminders_paused }))}><PauseCircle className="w-4 h-4" /> {inv.reminders_paused ? "Resume" : "Pause"} reminders</Btn>
                <Btn variant="ghost" onClick={() => (inv.is_disputed ? act(() => salesApi.reminderControl(inv.id, { is_disputed: false })) : setModal("dispute"))}>{inv.is_disputed ? "Clear dispute" : "Mark disputed"}</Btn>
              </>
            )}
          </div>
        </Card>
      </div>

      <Card title="Items" actions={open && can("sales.refund") && (
        <Link href={`/sales/returns?invoice=${inv.id}`} className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline"><RotateCcw className="w-3 h-3" /> Return items</Link>
      )}>
        <Table head={["Medicine", "Batch / Exp", "Qty", "MRP", "Rate", "Disc", "GST", "Amount"]}>
          {inv.items.map((it) => (
            <tr key={it.id}>
              <td className={td}><div className="text-white">{it.medicine_name}</div>{it.schedule_type && it.schedule_type !== "NONE" && <span className="text-[10px] text-amber-300">Sch {it.schedule_type}</span>}</td>
              <td className={`${td} text-xs text-slate-400`}>{it.batch_number}<br />{fmtDate(it.expiry_date)}</td>
              <td className={tdNum}>{num(it.quantity)} {it.unit_name}{num(it.returned_quantity_base) > 0 && <div className="text-[10px] text-violet-300">{num(it.returned_quantity_base)} returned</div>}</td>
              <td className={tdNum}>{money(it.mrp)}</td>
              <td className={tdNum}>{money(it.selling_rate)}</td>
              <td className={tdNum}>{money(it.discount_amount)}</td>
              <td className={tdNum}>{num(it.tax_rate)}%<div className="text-[10px] text-slate-500">{money(it.tax_amount)}</div></td>
              <td className={tdNum}>{money(it.line_total)}</td>
            </tr>
          ))}
        </Table>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Payments">
          <Table head={["Receipt", "Method", "When", "Applied ₹", "Check"]} empty={inv.payments.length === 0 && "No payments yet."}>
            {inv.payments.map((p) => (
              <tr key={`${p.id}-${p.received_at}`}>
                <td className={`${td} font-mono text-xs`}>{p.receipt_number}{p.status === "REVERSED" && <div><Badge value="CANCELLED" label="Reversed" /></div>}</td>
                <td className={td}>{p.method}{p.reference && <div className="text-[11px] text-slate-500">Ref {p.reference}</div>}{num(p.change_returned) > 0 && <div className="text-[11px] text-slate-500">Tendered {money(p.amount_tendered)} · change {money(p.change_returned)}</div>}</td>
                <td className={`${td} text-xs text-slate-400`}>{fmtDateTime(p.received_at)}<div>{p.received_by_name}</div></td>
                <td className={tdNum}>{money(p.allocated_to_invoice)}</td>
                <td className={td}>
                  {p.verification_status === "NOT_APPLICABLE" ? <span className="text-xs text-slate-500">—</span> : <Badge value={p.verification_status} />}
                  {p.verification_status === "RECORDED" && can("payments.receive") && p.status !== "REVERSED" && (
                    <button className="block text-[11px] text-emerald-300 hover:underline mt-1" onClick={() => {
                      const note = prompt("How was it verified? (e.g. matched in bank app)");
                      if (note) act(() => salesApi.verifyPayment(p.id, note), "Payment marked verified");
                    }}><ShieldCheck className="w-3 h-3 inline" /> Mark verified</button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
          {inv.refunds.length > 0 && (
            <div className="mt-3 text-sm">
              <div className="text-xs text-slate-500 mb-1">Refunds</div>
              {inv.refunds.map((r) => <div key={r.id} className="flex justify-between text-slate-300"><span>{r.method} · {fmtDateTime(r.refunded_at)}</span><span className="font-mono">{money(r.amount)}</span></div>)}
            </div>
          )}
          {inv.returns.length > 0 && (
            <div className="mt-3 text-sm">
              <div className="text-xs text-slate-500 mb-1">Returns</div>
              {inv.returns.map((r) => <div key={r.id} className="flex justify-between text-slate-300"><span className="font-mono">{r.return_number}</span><span className="font-mono">{money(r.total_amount)}</span></div>)}
            </div>
          )}
        </Card>

        <Card title="WhatsApp messages">
          {messages.length === 0 ? <div className="text-sm text-slate-500">None sent.</div> : (
            <ul className="space-y-2">
              {messages.map((m) => (
                <li key={m.id} className="border border-slate-800 rounded-lg p-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-300">{m.purpose.replace(/_/g, " ").toLowerCase()} → +{m.to_number}</span>
                    <Badge value={m.status} />
                  </div>
                  <div className="text-[11px] text-slate-500">{m.status_label} · {fmtDateTime(m.created_at)}{m.last_error && ` · ${m.last_error}`}</div>
                  <div className="flex gap-3 mt-1">
                    {m.share_url && <a className="text-xs text-emerald-300 hover:underline" href={m.share_url} target="_blank" rel="noopener noreferrer">Open in WhatsApp</a>}
                    {m.status === "FAILED" && can("messaging.send") && <button className="text-xs text-emerald-300 hover:underline" onClick={() => act(() => salesApi.retryMessage(m.id), "Retried")}>Retry</button>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {open && can("sales.cancel") && (
        <div className="flex justify-end">
          <Btn variant="danger" onClick={() => setModal("void")}><Ban className="w-4 h-4" /> Void invoice</Btn>
        </div>
      )}

      <WhatsAppModal inv={inv} open={modal === "whatsapp"} onClose={() => setModal("")} onDone={(msg) => { setModal(""); setInfo(msg); load(); }} />
      <PayModal inv={inv} open={modal === "pay"} onClose={() => setModal("")} onDone={(msg) => { setModal(""); setInfo(msg); load(); }} />
      <VoidModal inv={inv} open={modal === "void"} onClose={() => setModal("")} onDone={() => { setModal(""); setInfo("Invoice voided. Stock and money were reversed."); load(); }} />
      <DisputeModal open={modal === "dispute"} onClose={() => setModal("")} onSave={(note) => { setModal(""); act(() => salesApi.reminderControl(inv.id, { is_disputed: true, dispute_note: note })); }} />
      {modal === "rx" && <RxModal invoiceId={inv.id} onClose={() => setModal("")} />}
    </SalesShell>
  );
}

function WhatsAppModal({ inv, open, onClose, onDone }: { inv: Invoice; open: boolean; onClose: () => void; onDone: (m: string) => void }) {
  const [to, setTo] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [needForce, setNeedForce] = useState(false);
  useEffect(() => { if (open) { setTo(inv.customer_mobile ? `+${inv.customer_mobile}` : ""); setErr(null); setNeedForce(false); } }, [open, inv.customer_mobile]);
  const send = async (force: boolean) => {
    setBusy(true);
    setErr(null);
    const tab = openTabNow();
    try {
      const msg = await salesApi.sendWhatsApp(inv.id, { to_number: to || undefined, consent_confirmed: consent, force_resend: force });
      if (msg.mode === "MANUAL" && msg.share_url) {
        sendTabTo(tab, msg.share_url);
        onDone("WhatsApp opened with the message. Press Send there — the app cannot confirm delivery in manual mode.");
      } else {
        sendTabTo(tab, null);
        onDone(`WhatsApp status: ${msg.status_label}`);
      }
    } catch (e) {
      sendTabTo(tab, null);
      const text = errorText(e);
      setErr(text);
      setNeedForce(text.includes("already sent"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title={`Send ${inv.invoice_number} on WhatsApp`} onClose={onClose}>
      <ErrorBox message={err} />
      <Field label="WhatsApp number" hint="Indian 10-digit numbers get +91 automatically">
        <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} inputMode="tel" />
      </Field>
      <Notice>The message has the invoice number, total, amount paid and balance — no medicine names. Without the WhatsApp Cloud API configured, this opens WhatsApp for you to press Send.</Notice>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        The customer asked for / agrees to receive this on WhatsApp
      </label>
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        {needForce && <Btn busy={busy} onClick={() => send(true)}>Send again</Btn>}
        <Btn variant="primary" busy={busy} disabled={!to} onClick={() => send(false)}>Send</Btn>
      </div>
    </Modal>
  );
}

function PayModal({ inv, open, onClose, onDone }: { inv: Invoice; open: boolean; onClose: () => void; onDone: (m: string) => void }) {
  const [form, setForm] = useState({ method: "CASH", amount: "", tendered: "", reference: "", key: newKey() });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setForm({ method: "CASH", amount: inv.balance_due, tendered: "", reference: "", key: newKey() }); setErr(null); } }, [open, inv.balance_due]);
  const save = async () => {
    if (!inv.customer_id) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await salesApi.receivePayment(inv.customer_id, {
        idempotency_key: form.key, method: form.method, amount: form.amount,
        tendered: form.method === "CASH" && form.tendered ? form.tendered : undefined,
        reference: form.reference || undefined, allocations: [{ invoice_id: inv.id, amount: form.amount }],
      });
      onDone(`Receipt ${r.receipt_number} for ${money(r.amount)} recorded${num(r.change_returned) ? ` · give change ${money(r.change_returned)}` : ""}.`);
    } catch (e) {
      setErr(errorText(e));      // same key on retry: cannot post twice
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title={`Collect payment · due ${money(inv.balance_due)}`} onClose={onClose}>
      <ErrorBox message={err} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="Method">
          <select className={inputCls} value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Amount ₹"><input className={`${inputCls} font-mono`} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
        {form.method === "CASH" ? (
          <Field label="Cash tendered (for change)"><input className={`${inputCls} font-mono`} value={form.tendered} onChange={(e) => setForm({ ...form, tendered: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
        ) : (
          <Field label="Reference / UTR"><input className={inputCls} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
        )}
      </div>
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={!(num(form.amount) > 0)} onClick={save}>Record payment</Btn>
      </div>
    </Modal>
  );
}

function VoidModal({ inv, open, onClose, onDone }: { inv: Invoice; open: boolean; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [settlement, setSettlement] = useState("REFUND");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await salesApi.void(inv.id, reason, settlement);
      onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title={`Void ${inv.invoice_number}`} onClose={onClose}>
      <ErrorBox message={err} />
      <Notice tone="amber">Stock returns to its batches and the bill is kept as VOIDED with your reason. Payments of {money(inv.amount_paid)} are refunded or kept as customer credit.</Notice>
      <Field label="Reason *"><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <Field label="Money already paid">
        <select className={inputCls} value={settlement} onChange={(e) => setSettlement(e.target.value)}>
          <option value="REFUND">Refund to customer (same method)</option>
          {inv.customer_id && <option value="CUSTOMER_CREDIT">Keep as customer credit</option>}
        </select>
      </Field>
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="danger" busy={busy} disabled={reason.trim().length < 5} onClick={save}>Void invoice</Btn>
      </div>
    </Modal>
  );
}

function DisputeModal({ open, onClose, onSave }: { open: boolean; onClose: () => void; onSave: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <Modal open={open} title="Mark invoice as disputed" onClose={onClose}>
      <Field label="What is disputed?" hint="Reminders stop while an invoice is disputed"><textarea className={inputCls} rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      <div className="flex justify-end gap-2"><Btn variant="ghost" onClick={onClose}>Cancel</Btn><Btn variant="primary" disabled={note.trim().length < 3} onClick={() => onSave(note)}>Save</Btn></div>
    </Modal>
  );
}

function RxModal({ invoiceId, onClose }: { invoiceId: number; onClose: () => void }) {
  const [rx, setRx] = useState<(PrescriptionIn & { verified_by_name?: string }) | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { salesApi.prescription(invoiceId).then(setRx).catch((e) => setErr(errorText(e))); }, [invoiceId]);
  return (
    <Modal open title="Prescription details (confidential)" onClose={onClose}>
      <ErrorBox message={err} />
      {rx && (
        <dl className="text-sm grid grid-cols-2 gap-2">
          {[["Rx ref", rx.prescription_reference], ["Rx date", fmtDate(rx.prescription_date)], ["Doctor", rx.prescriber_name], ["Reg. no.", rx.prescriber_registration_no],
            ["Doctor address", rx.prescriber_address], ["Patient", rx.patient_name], ["Patient address", rx.patient_address], ["Verified by", rx.verified_by_name]]
            .map(([l, v]) => <div key={l}><dt className="text-xs text-slate-500">{l}</dt><dd className="text-slate-200">{v || "—"}</dd></div>)}
        </dl>
      )}
      <div className="text-[11px] text-slate-500">Viewing is recorded in the audit log.</div>
    </Modal>
  );
}
