"use client";

/** Ledger status of business documents (posted / pending / failed), for users who can view accounting. */
import { useEffect, useState } from "react";
import { fetchJson } from "@/lib/api";

export interface LedgerStatus { status: string; entry_number?: string; error?: string }

export function useLedgerStatus(keys: string[], enabled: boolean): Record<string, LedgerStatus> {
  const [map, setMap] = useState<Record<string, LedgerStatus>>({});
  const joined = keys.join(",");
  useEffect(() => {
    if (!enabled || !joined) return;
    fetchJson<Record<string, LedgerStatus>>(`/api/v1/accounting/postings/status?keys=${encodeURIComponent(joined)}`)
      .then(setMap).catch(() => setMap({}));
  }, [joined, enabled]);
  return map;
}

const STYLE: Record<string, [string, string]> = {
  POSTED: ["Posted", "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"],
  REVERSED: ["Posted · reversed", "bg-slate-500/10 text-slate-300 border-slate-500/20"],
  PENDING: ["Not yet posted", "bg-amber-500/10 text-amber-300 border-amber-500/20"],
  FAILED: ["Posting failed", "bg-rose-500/10 text-rose-300 border-rose-500/20"],
  NOT_APPLICABLE: ["Not posted (n/a)", "bg-slate-800 text-slate-400 border-slate-700"],
  NOT_SET_UP: ["Books not set up", "bg-slate-800 text-slate-400 border-slate-700"],
};

export function LedgerBadge({ status }: { status?: LedgerStatus }) {
  if (!status) return null;
  const [label, cls] = STYLE[status.status] || [status.status, "bg-slate-800 text-slate-400 border-slate-700"];
  return (
    <span title={status.error || status.entry_number || ""}
      className={`inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${cls}`}>
      {label}{status.entry_number ? ` ${status.entry_number}` : ""}
    </span>
  );
}
