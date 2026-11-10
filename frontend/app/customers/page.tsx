"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Search, UserPlus, Users } from "lucide-react";
import { NewCustomerModal } from "@/components/customer-modal";
import {
  Badge, Btn, Card, ErrorBox, Pager, SalesShell, Table, errorText, inputCls, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { Customer, money, num, salesApi } from "@/lib/sales-api";

const LIMIT = 25;

export default function CustomersPage() {
  const { user, can } = useStaff();
  const [q, setQ] = useState("");
  const [withBalance, setWithBalance] = useState(false);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Customer[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => {
      salesApi.customers({ q: q.trim() || undefined, with_balance_only: withBalance, limit: LIMIT, offset })
        .then((r) => { setRows(r.items); setTotal(r.total); setError(null); })
        .catch((e) => setError(errorText(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [user, q, withBalance, offset, reload]);

  return (
    <SalesShell
      user={user}
      title="Customers"
      subtitle="Customer accounts, credit limits and udhaar balances"
      icon={<Users className="w-6 h-6" />}
      actions={can("customers.manage") && <Btn variant="primary" onClick={() => setAdding(true)}><UserPlus className="w-4 h-4" /> New customer</Btn>}
    >
      <Card>
        <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input className={`${inputCls} pl-9`} placeholder="Name, mobile or business" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-400">
            <input type="checkbox" checked={withBalance} onChange={(e) => { setWithBalance(e.target.checked); setOffset(0); }} /> With a balance only
          </label>
        </div>
      </Card>
      <ErrorBox message={error} />
      <Card title={`${total} customer(s)`}>
        <Table head={["Customer", "Mobile", "WhatsApp", "Credit limit", "Outstanding", "Overdue"]} empty={rows.length === 0 && "No customers found."}>
          {rows.map((c) => (
            <tr key={c.id} className="hover:bg-slate-800/30">
              <td className={td}>
                <Link href={`/customers/${c.id}`} className="text-emerald-300 hover:underline font-semibold">{c.name}</Link>
                {!c.is_active && <> <Badge value="CANCELLED" label="Inactive" /></>}
                {c.business_name && <div className="text-[11px] text-slate-500">{c.business_name}</div>}
              </td>
              <td className={`${td} font-mono text-xs`}>{c.mobile ? `+${c.mobile}` : "—"}</td>
              <td className={td}>{c.whatsapp_consent ? <Badge value="PAID" label={c.reminders_opt_out ? "Bills only" : "Yes"} /> : <span className="text-xs text-slate-500">No consent</span>}</td>
              <td className={tdNum}>{money(c.credit_limit)}</td>
              <td className={`${tdNum} ${num(c.outstanding_balance) > 0 ? "text-amber-300" : num(c.outstanding_balance) < 0 ? "text-sky-300" : ""}`}>
                {num(c.outstanding_balance) < 0 ? `${money(-num(c.outstanding_balance))} credit` : money(c.outstanding_balance)}
              </td>
              <td className={`${tdNum} ${num(c.overdue_amount) > 0 ? "text-rose-300" : "text-slate-500"}`}>{money(c.overdue_amount)}</td>
            </tr>
          ))}
        </Table>
        <Pager total={total} limit={LIMIT} offset={offset} onChange={setOffset} />
      </Card>
      <NewCustomerModal open={adding} canCredit={can("sales.credit_override")} onClose={() => setAdding(false)} onCreated={() => { setAdding(false); setReload((n) => n + 1); }} />
    </SalesShell>
  );
}
