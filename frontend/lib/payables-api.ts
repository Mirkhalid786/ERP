/** Stage 5B preparation: supplier bill workflow, supplier payments, expenses. Amounts are decimal strings. */
import { fetchJson } from "@/lib/api";

export type Money = string;

export interface Allocation {
  id: number;
  invoice_id: number;
  invoice_number?: string | null;
  amount: Money;
  status: string;
  created_at: string;
}

export interface SupplierPayment {
  id: number;
  payment_number: string;
  supplier_id: number;
  supplier_name?: string | null;
  payment_date: string;
  method: string;
  cash_source?: string | null;
  shift_id?: number | null;
  amount: Money;
  allocated: Money;
  unallocated: Money;
  reference?: string | null;
  bank_account?: string | null;
  notes?: string | null;
  status: string;
  is_legacy: boolean;
  created_by_name?: string | null;
  created_at: string;
  reversed_by_name?: string | null;
  reversed_at?: string | null;
  reversal_reason?: string | null;
  allocations: Allocation[];
}

export interface VoucherDocument {
  kind: string;
  title: string;
  pharmacy: {
    name: string; legal_name?: string | null; address_line1?: string | null; city?: string | null;
    pincode?: string | null; phone?: string | null; email?: string | null; gstin?: string | null;
    drug_license_number?: string | null;
  };
  id: number;
  payment_number: string;
  status: string;
  is_reversed: boolean;
  payment_date: string;
  created_at: string;
  method: string;
  reference?: string | null;
  bank_account?: string | null;
  amount: Money;
  supplier?: { id: number; name: string; legal_name?: string | null; phone?: string | null; gstin?: string | null; address?: string | null } | null;
  allocations: { invoice_id: number; invoice_number?: string | null; amount: Money; status: string }[];
  allocated_total: Money;
  advance_unallocated: Money;
  payable_after: Money;
  recorded_by?: string | null;
  reversed_by?: string | null;
  reversal_reason?: string | null;
  notes?: string | null;
}

export interface OpenInvoice {
  id: number;
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  total: Money;
  paid: Money;
  returned: Money;
  outstanding: Money;
}

export interface SupplierBalance {
  supplier_id: number;
  supplier_name: string;
  invoiced: Money;
  returns: Money;
  paid: Money;
  unallocated_payments: Money;
  balance: Money;
  overdue_invoices: number;
}

export interface FinalizeCheck {
  errors: string[];
  warnings: string[];
  can_finalize: boolean;
  calculated_total: Money;
  declared_total: Money;
  discrepancy: Money;
}

export interface ExpenseCategory {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  account_code_hint?: string | null;
  is_active: boolean;
}

export interface Expense {
  id: number;
  expense_number: string;
  category_id: number;
  category_name?: string | null;
  expense_date: string;
  amount: Money;
  gst_amount: Money;
  payee: string;
  payee_gstin?: string | null;
  bill_number?: string | null;
  payment_method: string;
  cash_source?: string | null;
  shift_id?: number | null;
  reference?: string | null;
  notes?: string | null;
  status: string;
  created_by_name?: string | null;
  created_at: string;
  approved_by_name?: string | null;
  approved_at?: string | null;
  decision_note?: string | null;
  reversed_by_name?: string | null;
  reversed_at?: string | null;
  reversal_reason?: string | null;
}

export interface CreditNote {
  id: number;
  credit_note_number: string;
  supplier_id: number;
  supplier_name?: string | null;
  note_date: string;
  invoice_id?: number | null;
  invoice_number?: string | null;
  reason_code: string;
  taxable_amount: Money;
  cgst_amount: Money;
  sgst_amount: Money;
  igst_amount: Money;
  total_amount: Money;
  notes?: string | null;
  status: string;
  cancel_reason?: string | null;
}

export const CREDIT_NOTE_REASONS: [string, string][] = [
  ["RATE_DIFFERENCE", "Rate difference"], ["SCHEME_DISCOUNT", "Scheme / trade discount"], ["EXPIRY_CLAIM", "Expiry claim"],
  ["DAMAGE_CLAIM", "Damage claim"], ["SHORT_SUPPLY", "Short supply"], ["OTHER", "Other"],
];

export interface PaymentInput {
  idempotency_key: string;
  supplier_id: number;
  payment_date: string;
  method: string;
  cash_source?: string | null;
  amount: string;
  reference?: string | null;
  bank_account?: string | null;
  notes?: string | null;
  allocations?: { invoice_id: number; amount: string }[] | null;
  auto_allocate?: boolean;
  confirm_duplicate?: boolean;
}

export interface ExpenseInput {
  idempotency_key: string;
  category_id: number;
  expense_date: string;
  amount: string;
  gst_amount: string;
  payee: string;
  payee_gstin?: string | null;
  bill_number?: string | null;
  payment_method: string;
  cash_source?: string | null;
  reference?: string | null;
  notes?: string | null;
  confirm_duplicate?: boolean;
}

const post = <T>(url: string, body: unknown = {}) => fetchJson<T>(url, { method: "POST", body: JSON.stringify(body) });
const qs = (p: Record<string, string | number | undefined | null>) => {
  const q = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && q.set(k, String(v)));
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const PAYMENT_METHODS: [string, string][] = [
  ["BANK_TRANSFER", "Bank transfer (NEFT/RTGS/IMPS)"], ["UPI", "UPI"], ["CHEQUE", "Cheque"], ["CASH", "Cash"], ["CARD", "Card"],
];
export const REFERENCE_REQUIRED = ["BANK_TRANSFER", "UPI", "CHEQUE"];

export const payablesApi = {
  finalizeCheck: (id: number) => fetchJson<FinalizeCheck>(`/api/v1/purchases/invoices/${id}/finalize-check`),
  finalize: (id: number, acknowledge_warnings: boolean) => post(`/api/v1/purchases/invoices/${id}/finalize`, { acknowledge_warnings }),
  cancelInvoice: (id: number, reason: string) => post(`/api/v1/purchases/invoices/${id}/cancel`, { reason }),

  payments: (p: { supplier_id?: number; q?: string; method?: string; status?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) =>
    fetchJson<{ total: number; items: SupplierPayment[] }>(`/api/v1/supplier-payments${qs(p)}`),
  paymentDocument: (id: number | string) => fetchJson<VoucherDocument>(`/api/v1/supplier-payments/${id}/document`),
  paymentPdfUrl: (id: number | string) => `/api/v1/supplier-payments/${id}/document.pdf`,
  recordPayment: (body: PaymentInput) => post<SupplierPayment & { replayed: boolean }>("/api/v1/supplier-payments", body),
  allocate: (id: number, allocations: { invoice_id: number; amount: string }[] | null) =>
    post<SupplierPayment>(`/api/v1/supplier-payments/${id}/allocate`, { allocations }),
  reversePayment: (id: number, reason: string) => post<SupplierPayment>(`/api/v1/supplier-payments/${id}/reverse`, { reason }),
  openInvoices: (supplierId: number) => fetchJson<OpenInvoice[]>(`/api/v1/supplier-payments/open-invoices/${supplierId}`),
  balances: () => fetchJson<SupplierBalance[]>("/api/v1/supplier-payments/balances"),
  reconciliation: () =>
    fetchJson<{ ok: boolean; problems: string[]; invoices_checked: number; payments_checked: number }>("/api/v1/supplier-payments/reconciliation"),

  creditNotes: () => fetchJson<CreditNote[]>("/api/v1/supplier-credit-notes"),
  createCreditNote: (body: Record<string, unknown>) => post<CreditNote>("/api/v1/supplier-credit-notes", body),
  cancelCreditNote: (id: number, reason: string) => post<CreditNote>(`/api/v1/supplier-credit-notes/${id}/cancel`, { reason }),
  categories: () => fetchJson<ExpenseCategory[]>("/api/v1/expenses/categories"),
  saveCategory: (id: number | null, body: { code?: string; name: string; description?: string | null; account_code_hint?: string | null; is_active?: boolean }) =>
    fetchJson<ExpenseCategory>(id ? `/api/v1/expenses/categories/${id}` : "/api/v1/expenses/categories",
      { method: id ? "PUT" : "POST", body: JSON.stringify(body) }),
  expenses: (p: { date_from?: string; date_to?: string; category_id?: number; status?: string; limit?: number; offset?: number }) =>
    fetchJson<{ total: number; items: Expense[]; approved_total: Money; approved_by_category: { category: string; amount: Money; count: number }[] }>(
      `/api/v1/expenses${qs(p)}`),
  recordExpense: (body: ExpenseInput) => post<Expense & { replayed: boolean }>("/api/v1/expenses", body),
  approveExpense: (id: number, note?: string) => post<Expense>(`/api/v1/expenses/${id}/approve`, { note: note || null }),
  rejectExpense: (id: number, note: string) => post<Expense>(`/api/v1/expenses/${id}/reject`, { note }),
  reverseExpense: (id: number, reason: string) => post<Expense>(`/api/v1/expenses/${id}/reverse`, { reason }),
};
