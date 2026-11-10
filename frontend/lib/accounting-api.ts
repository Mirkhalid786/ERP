/** Phase 5A accounting API. Amounts are decimal strings; never use floats for arithmetic beyond display checks. */
import { fetchJson } from "@/lib/api";

export type Money = string;

export interface Account {
  id: number;
  code: string;
  name: string;
  account_type: string;
  normal_balance: string;
  parent_id?: number | null;
  is_group: boolean;
  system_key?: string | null;
  party_type?: string | null;
  is_control: boolean;
  is_active: boolean;
  description?: string | null;
  has_postings: boolean;
  balance: Money;
}

export interface Period {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  status: string;
  entries: number;
  closed_by?: string | null;
  closed_at?: string | null;
  reopened_by?: string | null;
  reopen_reason?: string | null;
}

export interface FiscalYear {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  status: string;
  periods: Period[];
}

export interface JournalLine {
  id?: number;
  account_id: number;
  account_code?: string | null;
  account_name?: string | null;
  debit: Money;
  credit: Money;
  party_type?: string | null;
  party_id?: number | null;
  party_name?: string | null;
  description?: string | null;
}

export interface Journal {
  id: number;
  entry_number?: string | null;
  status: string;
  entry_date: string;
  period_name?: string | null;
  source_type: string;
  reference?: string | null;
  narration: string;
  total_debit: Money;
  total_credit: Money;
  created_by_name?: string | null;
  posted_by_name?: string | null;
  posted_at?: string | null;
  reversal_of_id?: number | null;
  reversal_of_number?: string | null;
  reversed_by_entry_id?: number | null;
  reversed_by_number?: string | null;
  reversal_reason?: string | null;
  lines: JournalLine[];
}

export interface JournalListItem {
  id: number;
  entry_number?: string | null;
  status: string;
  entry_date: string;
  source_type: string;
  reference?: string | null;
  narration: string;
  total_debit: Money;
}

export interface TrialBalanceRow {
  account_id: number;
  code: string;
  name: string;
  account_type: string;
  opening_debit: Money;
  opening_credit: Money;
  debit: Money;
  credit: Money;
  closing_debit: Money;
  closing_credit: Money;
}

export interface OpeningLine {
  account_id: number;
  debit: Money;
  credit: Money;
  party_type?: string | null;
  party_id?: number | null;
  document_number?: string | null;
  document_date?: string | null;
  due_date?: string | null;
  notes?: string | null;
}

export interface OpeningBatch {
  id: number;
  status: string;
  opening_date: string;
  source_description: string;
  post_difference_to_suspense: boolean;
  journal_entry_id?: number | null;
  lines: OpeningLine[];
  preview: {
    total_debit: Money;
    total_credit: Money;
    difference: Money;
    errors: string[];
    warnings: string[];
    reconciliation: { item: string; entered: Money; erp: Money; difference: Money; basis: string }[];
    can_post: boolean;
  };
}

export interface Settlement {
  id: number;
  settlement_number: string;
  clearing_key: string;
  bank_account_id: number;
  settlement_date: string;
  gross_amount: Money;
  fee_amount: Money;
  fee_gst_amount: Money;
  net_amount: Money;
  reference?: string | null;
  status: string;
  reversal_reason?: string | null;
}

export interface BankRecon {
  account: { id: number; code: string; name: string };
  as_of: string;
  book_balance: Money;
  statement_movements: Money;
  matched_lines: number;
  unmatched_book: { journal_line_id: number; entry_number: string; entry_date: string; narration: string; amount: Money }[];
  unmatched_statement: { id: number; statement_date: string; description: string; reference?: string | null; amount: Money }[];
  note: string;
}

export interface PostingIssue {
  id: number;
  source_key: string;
  source_type: string;
  event_date?: string | null;
  status: string;
  error: string;
  attempts: number;
  last_attempt_at: string;
  resolved_at?: string | null;
}

export interface PostingReconciliation {
  set_up: boolean;
  ok: boolean;
  checked: number;
  books_start_date?: string;
  missing: { source_key: string; date: string; narration: string }[];
  mismatched: { source_key: string; entry_number: string; narration: string }[];
  unbuildable: { source_key: string; error: string }[];
  open_issues: PostingIssue[];
  controls: { control: string; gl: Money; operational: Money; ok: boolean; differences: { party_id: number | null; gl: Money; operational: Money }[] }[];
}

export interface CutoverSummary {
  first_activity?: string | null;
  last_activity?: string | null;
  sections: Record<string, { item: string; count: number; first?: string | null; last?: string | null; total?: Money | null }[]>;
  balances_now: { customer_khata_outstanding: Money; stock_value_at_cost: Money };
  customer_balances: { customer_id: number; name: string; balance: Money }[];
  supplier_balances: { supplier_id: number; name: string; balance: Money }[];
  cash: { open_shifts: { shift_id: number; opened_at: string; expected_cash: Money }[];
          last_closed_shift?: { shift_id: number; closed_at: string; counted_cash: Money } | null; note: string };
  bank: string;
  goods_received_not_billed: { received_value: Money; matched_by_bills: Money; unbilled_value: Money; grns_without_finalised_bill: string[] };
  test_masters: Record<string, number>;
  note: string;
}

const json = (method: string, body?: unknown) => ({ method, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
const qs = (p: Record<string, string | number | undefined | null>) => {
  const q = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && q.set(k, String(v)));
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const accountingApi = {
  status: () => fetchJson<{ is_set_up: boolean; books_start_date?: string; missing_system_accounts: string[]; opening_batch?: { id: number; status: string } | null }>("/api/v1/accounting/status"),
  setup: (books_start_date: string) => fetchJson("/api/v1/accounting/setup", json("POST", { books_start_date })),
  accounts: () => fetchJson<Account[]>("/api/v1/accounting/accounts"),
  createAccount: (body: { code: string; name: string; account_type: string; parent_id?: number | null; is_group?: boolean; description?: string }) =>
    fetchJson<Account>("/api/v1/accounting/accounts", json("POST", body)),
  updateAccount: (id: number, body: { name?: string; code?: string; description?: string; is_active?: boolean }) =>
    fetchJson<Account>(`/api/v1/accounting/accounts/${id}`, json("PUT", body)),
  deleteAccount: (id: number) => fetchJson(`/api/v1/accounting/accounts/${id}`, json("DELETE")),
  periods: () => fetchJson<FiscalYear[]>("/api/v1/accounting/periods"),
  createFiscalYear: (start_date: string, end_date: string) => fetchJson("/api/v1/accounting/fiscal-years", json("POST", { start_date, end_date })),
  closePeriod: (id: number) => fetchJson(`/api/v1/accounting/periods/${id}/close`, json("POST")),
  reopenPeriod: (id: number, reason: string) => fetchJson(`/api/v1/accounting/periods/${id}/reopen`, json("POST", { reason })),
  journals: (p: { status?: string; source_type?: string; date_from?: string; date_to?: string; q?: string; limit?: number; offset?: number }) =>
    fetchJson<{ total: number; items: JournalListItem[] }>(`/api/v1/accounting/journals${qs(p)}`),
  journal: (id: number) => fetchJson<Journal>(`/api/v1/accounting/journals/${id}`),
  saveDraft: (id: number | null, body: { idempotency_key?: string; entry_date: string; narration: string; reference?: string; lines: JournalLine[] }) =>
    fetchJson<Journal>(id ? `/api/v1/accounting/journals/${id}` : "/api/v1/accounting/journals", json(id ? "PUT" : "POST", body)),
  deleteDraft: (id: number) => fetchJson(`/api/v1/accounting/journals/${id}`, json("DELETE")),
  postDraft: (id: number) => fetchJson<Journal>(`/api/v1/accounting/journals/${id}/post`, json("POST")),
  reverse: (id: number, reason: string, reversal_date?: string) => fetchJson<Journal>(`/api/v1/accounting/journals/${id}/reverse`, json("POST", { reason, reversal_date })),
  opening: () => fetchJson<OpeningBatch | null>("/api/v1/accounting/opening-balances"),
  saveOpening: (id: number | null, body: { source_description: string; post_difference_to_suspense: boolean; lines: OpeningLine[] }) =>
    fetchJson<OpeningBatch>(id ? `/api/v1/accounting/opening-balances/${id}` : "/api/v1/accounting/opening-balances", json(id ? "PUT" : "POST", body)),
  postOpening: (id: number, acknowledge_warnings: boolean) =>
    fetchJson<{ entry_number: string }>(`/api/v1/accounting/opening-balances/${id}/post`, json("POST", { acknowledge_warnings })),
  trialBalance: (date_from?: string, date_to?: string) =>
    fetchJson<{ rows: TrialBalanceRow[]; totals: Record<string, Money>; is_balanced: boolean; date_to: string }>(`/api/v1/accounting/trial-balance${qs({ date_from, date_to })}`),
  trialBalanceCsv: (date_from?: string, date_to?: string) => `/api/v1/accounting/trial-balance${qs({ date_from, date_to, format: "csv" })}`,
  ledger: (id: number, date_from?: string, date_to?: string) =>
    fetchJson<{ account: { code: string; name: string }; opening_balance: Money; closing_balance: Money; entries: { entry_id: number; entry_number: string; entry_date: string; narration: string; description?: string; debit: Money; credit: Money; balance: Money; status: string }[] }>(
      `/api/v1/accounting/ledger/${id}${qs({ date_from, date_to })}`),
  postingIssues: (status = "OPEN") => fetchJson<PostingIssue[]>(`/api/v1/accounting/postings/issues${qs({ status })}`),
  postingReconciliation: () => fetchJson<PostingReconciliation>("/api/v1/accounting/postings/reconciliation"),
  retryPostings: (source_key?: string) =>
    fetchJson<{ results: Record<string, string>; summary: Record<string, number> }>("/api/v1/accounting/postings/retry",
      json("POST", { source_key: source_key || null, include_missing: true })),
  cutoverSummary: () => fetchJson<CutoverSummary>("/api/v1/accounting/historical-summary"),
  report: <T = any>(name: string, p: { date_from?: string; date_to?: string; account_id?: number }) =>
    fetchJson<T>(`/api/v1/accounting/reports/${name}${qs(p)}`),
  reportCsvUrl: (name: string, p: { date_from?: string; date_to?: string; account_id?: number }) =>
    `/api/v1/accounting/reports/${name}${qs({ ...p, format: "csv" })}`,
  caPackUrl: (date_from: string, date_to: string) => `/api/v1/accounting/export/ca-pack${qs({ date_from, date_to })}`,
  closeYear: (id: number) => fetchJson(`/api/v1/accounting/fiscal-years/${id}/close`, json("POST")),
  reopenYear: (id: number, reason: string) => fetchJson(`/api/v1/accounting/fiscal-years/${id}/reopen`, json("POST", { reason })),
  settlements: () => fetchJson<{ items: Settlement[]; clearing: { clearing_key: string; account: string; balance: Money }[] }>("/api/v1/accounting/settlements"),
  createSettlement: (body: Record<string, unknown>) => fetchJson<Settlement>("/api/v1/accounting/settlements", json("POST", body)),
  reverseSettlement: (id: number, reason: string) => fetchJson(`/api/v1/accounting/settlements/${id}/reverse`, json("POST", { reason })),
  importStatement: (account_id: number, rows: { statement_date: string; description: string; reference?: string; amount: string }[]) =>
    fetchJson<{ added: number; skipped_duplicates: number; auto_matched: number }>("/api/v1/accounting/bank/statements", json("POST", { account_id, rows })),
  matchLine: (id: number, journal_line_id: number | null) =>
    fetchJson(`/api/v1/accounting/bank/statement-lines/${id}/match`, json("POST", { journal_line_id })),
  autoMatch: (account_id: number) => fetchJson<{ matched: number }>(`/api/v1/accounting/bank/${account_id}/auto-match`, json("POST")),
  bankReconciliation: (account_id: number) => fetchJson<BankRecon>(`/api/v1/accounting/bank/${account_id}/reconciliation`),
  integrity: () => fetchJson<{ ok: boolean; entries_checked: number; lines_checked: number; total_debit: Money; total_credit: Money; problems: { entry: string; problem: string }[] }>("/api/v1/accounting/integrity"),
};

/** Paise-exact sum of decimal strings (avoids float rounding in the balance indicator). */
export const sumPaise = (values: (string | number | null | undefined)[]) =>
  values.reduce<number>((s, v) => s + Math.round(Number(v || 0) * 100), 0);
