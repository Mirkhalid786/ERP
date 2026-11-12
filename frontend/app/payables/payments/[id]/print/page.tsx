"use client";

/** Printable supplier payment voucher: ?layout=thermal (80 mm) or ?layout=a4, &auto=1 prints. */
import { useParams } from "next/navigation";
import { useCallback } from "react";
import { DocView, PaymentDocPrintPage } from "@/components/payment-print";
import { payablesApi } from "@/lib/payables-api";
import { fmtDate, num } from "@/lib/sales-api";

export default function VoucherPrintPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(async (): Promise<DocView> => {
    const d = await payablesApi.paymentDocument(id);
    const totals: DocView["totals"] = [["Allocated to bills", d.allocated_total], ["Amount paid", d.amount, true]];
    if (num(d.advance_unallocated) > 0) totals.push(["Advance (unallocated)", d.advance_unallocated]);
    totals.push(["Payable outstanding after this payment", d.payable_after, true]);
    return {
      title: "Payment Voucher",
      number: d.payment_number,
      dateText: fmtDate(d.payment_date),
      status: d.status,
      isReversed: d.is_reversed,
      pharmacy: d.pharmacy,
      partyLabel: "Paid to",
      partyName: d.supplier?.name || "Supplier",
      partySub: d.supplier?.gstin ? `GSTIN: ${d.supplier.gstin}` : undefined,
      meta: [
        ["Method", d.method],
        ...(d.reference ? [["Reference", d.reference] as [string, string]] : []),
        ...(d.recorded_by ? [["Recorded by", d.recorded_by] as [string, string]] : []),
        ...(d.is_reversed && d.reversal_reason ? [["Reversal reason", d.reversal_reason] as [string, string]] : []),
      ],
      allocations: d.allocations.map((a) => ({ label: a.invoice_number || `Bill #${a.invoice_id}`, amount: a.amount })),
      totals,
      backHref: "/payables",
    };
  }, [id]);
  return <PaymentDocPrintPage load={load} />;
}
