"use client";

/** Printable customer payment receipt: ?layout=thermal (80 mm) or ?layout=a4, &auto=1 prints. */
import { useParams } from "next/navigation";
import { useCallback } from "react";
import { DocView, PaymentDocPrintPage } from "@/components/payment-print";
import { fmtDate, fmtDateTime, num, salesApi } from "@/lib/sales-api";

export default function ReceiptPrintPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async (): Promise<DocView> => {
    const d = await salesApi.paymentDocument(id);
    const totals: DocView["totals"] = [["Allocated to invoices", d.allocated_total], ["Amount received", d.amount, true]];
    if (d.amount_tendered && num(d.amount_tendered) > 0) totals.push(["Tendered", d.amount_tendered]);
    if (num(d.change_returned) > 0) totals.push(["Change returned", d.change_returned]);
    if (num(d.advance_unapplied) > 0) totals.push(["Advance (unapplied)", d.advance_unapplied]);
    if (d.balance_after !== null && d.balance_after !== undefined) totals.push(["Balance outstanding after this payment", d.balance_after, true]);
    return {
      title: "Payment Receipt",
      number: d.receipt_number,
      dateText: fmtDateTime(d.received_at),
      status: d.status,
      isReversed: d.is_reversed,
      pharmacy: d.pharmacy,
      partyLabel: "Received from",
      partyName: d.customer?.name || "Walk-in customer",
      partySub: d.customer?.mobile ? `Mobile: ${d.customer.mobile.slice(-10)}` : undefined,
      meta: [
        ["Method", d.method],
        ...(d.reference ? [["Reference", d.reference] as [string, string]] : []),
        ...(d.received_by ? [["Received by", d.received_by] as [string, string]] : []),
      ],
      allocations: d.allocations.map((a) => ({ label: a.invoice_number, sub: fmtDate(a.invoice_date), amount: a.amount })),
      totals,
      backHref: d.customer ? `/customers/${d.customer.id}` : "/receivables",
    };
  }, [id]);
  return <PaymentDocPrintPage load={load} />;
}
