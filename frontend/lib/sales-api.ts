/**
 * Phase 4 API client: POS, sales invoices, payments, customers, returns,
 * cash shifts, reports and WhatsApp messaging.
 *
 * Money values arrive as decimal strings (e.g. "84.00"); use `money()` / `num()` to show or add them.
 */
import { fetchJson } from "@/lib/api";

export type Money = string;

export const num = (v: Money | number | null | undefined): number => {
  const n = Number(v ?? 0);
  return Math.abs(n) < 0.005 ? 0 : n; // never show "-0.00"
};
export const money = (v: Money | number | null | undefined): string =>
  `₹${num(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const newKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 32);
export const fmtDate = (v?: string | null) =>
  v ? new Date(v.length === 10 ? `${v}T00:00:00` : v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";
export const fmtDateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) q.set(k, String(v));
  });
  const s = q.toString();
  return s ? `?${s}` : "";
};
const post = <T>(url: string, body: unknown = {}) => fetchJson<T>(url, { method: "POST", body: JSON.stringify(body) });

// ── POS ───────────────────────────────────────────────────────────────────────

export interface UnitOption {
  unit_id: number;
  unit_name: string;
  conversion_factor: Money;
  is_base: boolean;
  is_default: boolean;
}

export interface SaleableBatch {
  batch_id: number;
  batch_number: string;
  expiry_date: string;
  stock_base: Money;
  mrp_per_base: Money;
  selling_rate_per_base: Money;
  rack_location?: string | null;
}

export interface PosMedicine {
  medicine_id: number;
  name: string;
  brand_name?: string | null;
  generic_name: string;
  strength?: string | null;
  dosage_form: string;
  manufacturer?: string | null;
  schedule_type: string;
  requires_prescription: boolean;
  tax_rate: Money;
  base_unit_id: number;
  base_unit_name: string;
  fractional_allowed: boolean;
  units: UnitOption[];
  total_stock_base: Money;
  batches: SaleableBatch[];
  matched_barcode: boolean;
}

export interface SaleLine {
  medicine_id: number;
  unit_id: number;
  quantity: string;
  batch_id?: number | null;
  discount_percent?: string;
}

export interface SalePaymentIn {
  method: string;
  amount?: string;
  tendered?: string;
  reference?: string;
}

export interface PrescriptionIn {
  prescription_reference: string;
  prescription_date?: string | null;
  prescriber_name: string;
  prescriber_registration_no?: string;
  prescriber_address?: string;
  patient_name: string;
  patient_address?: string;
}

export interface QuoteItem {
  cart_line: number;
  line_no: number;
  medicine_id: number;
  medicine_name: string;
  batch_id: number;
  batch_number: string;
  expiry_date: string;
  unit_name: string;
  quantity: Money;
  quantity_base: Money;
  mrp: Money;
  selling_rate: Money;
  gross_amount: Money;
  discount_amount: Money;
  taxable_amount: Money;
  tax_rate: Money;
  tax_amount: Money;
  line_total: Money;
}

export interface Quote {
  is_inter_state: boolean;
  items: QuoteItem[];
  gross_amount: Money;
  discount_total: Money;
  taxable_total: Money;
  cgst_total: Money;
  sgst_total: Money;
  igst_total: Money;
  tax_total: Money;
  round_off: Money;
  grand_total: Money;
  prescription_required: boolean;
  prescription_medicines: string[];
}

// ── Invoices ──────────────────────────────────────────────────────────────────

export interface InvoiceItem {
  id: number;
  line_no: number;
  medicine_id: number;
  batch_id: number;
  medicine_name: string;
  hsn_code?: string | null;
  schedule_type?: string | null;
  batch_number: string;
  expiry_date: string;
  unit_name: string;
  conversion_factor: Money;
  quantity: Money;
  quantity_base: Money;
  mrp: Money;
  selling_rate: Money;
  gross_amount: Money;
  discount_percent: Money;
  discount_amount: Money;
  taxable_amount: Money;
  tax_rate: Money;
  cgst_amount: Money;
  sgst_amount: Money;
  igst_amount: Money;
  tax_amount: Money;
  line_total: Money;
  returned_quantity_base: Money;
}

export interface PaymentSummary {
  id: number;
  receipt_number: string;
  method: string;
  source: string;
  amount: Money;
  allocated_to_invoice: Money;
  amount_tendered?: Money | null;
  change_returned: Money;
  reference?: string | null;
  verification_status: string;
  status: string;
  received_by_name?: string | null;
  received_at: string;
}

export interface Invoice {
  id: number;
  invoice_number: string;
  invoice_date: string;
  status: string;
  customer_id?: number | null;
  customer_name: string;
  customer_mobile?: string | null;
  customer_gstin?: string | null;
  is_inter_state: boolean;
  cashier_name?: string | null;
  gross_amount: Money;
  discount_total: Money;
  taxable_total: Money;
  cgst_total: Money;
  sgst_total: Money;
  igst_total: Money;
  tax_total: Money;
  round_off: Money;
  grand_total: Money;
  amount_paid: Money;
  paid_at_sale: Money;
  return_credit_applied: Money;
  returned_value: Money;
  refunded_amount: Money;
  written_off_amount: Money;
  balance_due: Money;
  payment_status: string;
  return_status: string;
  is_credit_sale: boolean;
  due_date?: string | null;
  overdue_days: number;
  prescription_required: boolean;
  has_prescription: boolean;
  reminders_paused: boolean;
  is_disputed: boolean;
  dispute_note?: string | null;
  notes?: string | null;
  print_count: number;
  voided_at?: string | null;
  void_reason?: string | null;
  replayed: boolean;
  change_due: Money;
  items: InvoiceItem[];
  payments: PaymentSummary[];
  refunds: { id: number; method: string; amount: Money; reference?: string | null; reason: string; refunded_at: string }[];
  returns: { id: number; return_number: string; total_amount: Money; refund_amount: Money; applied_to_balance: Money; credit_note_amount: Money; created_at: string }[];
}

export interface InvoiceListItem {
  id: number;
  invoice_number: string;
  invoice_date: string;
  status: string;
  customer_id?: number | null;
  customer_name: string;
  customer_mobile?: string | null;
  cashier_name?: string | null;
  grand_total: Money;
  amount_paid: Money;
  balance_due: Money;
  payment_status: string;
  return_status: string;
  is_credit_sale: boolean;
  due_date?: string | null;
  overdue_days: number;
  payment_methods: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit?: number;
  offset?: number;
}

export interface InvoiceFilters {
  q?: string;
  date_from?: string;
  date_to?: string;
  payment_method?: string;
  payment_status?: string;
  status?: string;
  customer_id?: number;
  overdue_only?: boolean;
  credit_only?: boolean;
  limit?: number;
  offset?: number;
}

// ── Customers ─────────────────────────────────────────────────────────────────

export interface Customer {
  id: number;
  name: string;
  mobile?: string | null;
  email?: string | null;
  address?: string | null;
  gstin?: string | null;
  business_name?: string | null;
  notes?: string | null;
  credit_limit: Money;
  payment_terms_days: number;
  whatsapp_consent: boolean;
  reminders_opt_out: boolean;
  preferred_language: string;
  is_active: boolean;
  outstanding_balance: Money;
  overdue_amount: Money;
  available_credit: Money;
  created_at: string;
}

export interface CustomerPayload {
  name: string;
  mobile?: string | null;
  email?: string | null;
  address?: string | null;
  gstin?: string | null;
  business_name?: string | null;
  notes?: string | null;
  credit_limit?: string;
  payment_terms_days?: number;
  whatsapp_consent?: boolean;
  reminders_opt_out?: boolean;
  is_active?: boolean;
}

export interface LedgerEntry {
  id: number;
  created_at: string;
  entry_type: string;
  reference: string;
  description: string;
  debit: Money;
  credit: Money;
  balance: Money;
  invoice_id?: number | null;
  payment_id?: number | null;
  reason?: string | null;
}

export interface CustomerStatement {
  customer: Customer;
  opening_balance: Money;
  closing_balance: Money;
  total_debits: Money;
  total_credits: Money;
  unapplied_credit: Money;
  entries: LedgerEntry[];
  open_invoices: InvoiceListItem[];
}

export interface PaymentResult {
  id: number;
  receipt_number: string;
  customer_id?: number | null;
  source?: string;
  method: string;
  amount: Money;
  amount_tendered?: Money | null;
  change_returned: Money;
  refunded_amount?: Money;
  unapplied_amount: Money;
  reference?: string | null;
  status?: string;
  received_at?: string;
  replayed: boolean;
  allocations: { invoice_id: number; amount: Money }[];
}

export interface PharmacyHeader {
  name: string;
  legal_name?: string | null;
  address_line1?: string | null;
  city?: string | null;
  pincode?: string | null;
  phone?: string | null;
  email?: string | null;
  gstin?: string | null;
  drug_license_number?: string | null;
}

export interface ReceiptDocument {
  kind: string;
  title: string;
  pharmacy: PharmacyHeader;
  id: number;
  receipt_number: string;
  status: string;
  is_reversed: boolean;
  source: string;
  received_at: string;
  method: string;
  reference?: string | null;
  amount: Money;
  amount_tendered?: Money | null;
  change_returned: Money;
  refunded_amount: Money;
  customer?: { id: number; name: string; mobile?: string | null; gstin?: string | null; address?: string | null } | null;
  allocations: { invoice_id: number; invoice_number: string; invoice_date: string; amount: Money }[];
  allocated_total: Money;
  advance_unapplied: Money;
  balance_after?: Money | null;
  advance_after?: Money | null;
  received_by?: string | null;
  notes?: string | null;
}

// ── Returns ───────────────────────────────────────────────────────────────────

export interface SalesReturn {
  id: number;
  return_number: string;
  invoice_id: number;
  invoice_number?: string | null;
  customer_name?: string | null;
  reason: string;
  tax_total: Money;
  total_amount: Money;
  applied_to_balance: Money;
  refund_amount: Money;
  credit_note_amount: Money;
  refund_method?: string | null;
  processed_by_name?: string | null;
  created_at: string;
  replayed: boolean;
  items: { id: number; medicine_name?: string | null; batch_number?: string | null; quantity_base: Money; condition: string; disposition: string; line_total: Money }[];
}

// ── Shifts ────────────────────────────────────────────────────────────────────

export interface Shift {
  id: number;
  user_id: number;
  user_name?: string | null;
  status: string;
  opened_at: string;
  closed_at?: string | null;
  opening_cash: Money;
  cash_sales: Money;
  cash_collections: Money;
  cash_refunds: Money;
  paid_out: Money;
  paid_in: Money;
  supplier_payments_paid?: Money;
  expenses_paid?: Money;
  expected_cash: Money;
  counted_cash?: Money | null;
  variance?: Money | null;
  variance_note?: string | null;
  needs_review: boolean;
  reviewed_by_name?: string | null;
  review_note?: string | null;
  non_cash_totals: Record<string, Money>;
  cash_movements: { id: number; movement_type: string; category: string; amount: Money; reason: string; reference?: string | null; created_at: string }[];
}

export interface DayEnd {
  date: string;
  bills: number;
  net_sales: Money;
  credit_given: Money;
  by_method: Record<string, { sales: Money; collections: Money; refunds: Money; net: Money; count: number }>;
  cash_outside_shifts: Money;
  shifts: Shift[];
}

// ── Messaging ─────────────────────────────────────────────────────────────────

export interface Message {
  id: number;
  mode: string;
  purpose: string;
  status: string;
  status_label: string;
  customer_id?: number | null;
  customer_name?: string | null;
  invoice_id?: number | null;
  invoice_number?: string | null;
  to_number: string;
  body: string;
  attempts: number;
  last_error?: string | null;
  share_url?: string | null;
  created_at: string;
  sent_at?: string | null;
  delivered_at?: string | null;
  read_at?: string | null;
}

export interface MessagingConfig {
  provider: {
    provider: string;
    cloud_api_configured: boolean;
    phone_number_id_set: boolean;
    access_token_set: boolean;
    app_secret_set: boolean;
    webhook_verify_token_set: boolean;
    api_version: string;
    templates: Record<string, string | null>;
    attach_invoice_pdf: boolean;
    default_mode: string;
  };
  settings: Record<string, string>;
  descriptions: Record<string, string>;
  placeholders: string[];
  template_param_order: Record<string, string[]>;
}

export interface ReminderCandidate {
  invoice_id: number;
  invoice_number: string;
  customer_name: string;
  purpose: string;
  due_date: string;
  balance_due: Money;
  eligible: boolean;
  reason?: string | null;
  message_id?: number | null;
}

export interface PrintRecord {
  copy_label: string;
  print_count: number;
  footer_note: string;
  receipt_footer?: string;
  website?: string;
  pharmacy: {
    name: string;
    legal_name?: string | null;
    address_line1?: string | null;
    city?: string | null;
    pincode?: string | null;
    phone?: string | null;
    email?: string | null;
    gstin?: string | null;
    drug_license_number?: string | null;
  };
}

// ── Methods ───────────────────────────────────────────────────────────────────

export const salesApi = {
  // POS
  search: (q: string) => fetchJson<PosMedicine[]>(`/api/v1/pos/search${qs({ q, limit: 15 })}`),
  quote: (items: SaleLine[], customer_id?: number | null) => post<Quote>("/api/v1/pos/quote", { items, customer_id }),
  finalize: (body: {
    idempotency_key: string;
    customer_id?: number | null;
    items: SaleLine[];
    payments: SalePaymentIn[];
    due_date?: string | null;
    credit_override_reason?: string | null;
    prescription?: PrescriptionIn | null;
    notes?: string | null;
  }) => post<Invoice>("/api/v1/sales/invoices", body),

  // Invoices
  invoices: (f: InvoiceFilters) => fetchJson<Paged<InvoiceListItem>>(`/api/v1/sales/invoices${qs({ ...f })}`),
  invoicesCsvUrl: (f: InvoiceFilters) => `/api/v1/sales/invoices/export.csv${qs({ ...f, limit: undefined, offset: undefined })}`,
  invoice: (id: number | string) => fetchJson<Invoice>(`/api/v1/sales/invoices/${id}`),
  recordPrint: (id: number | string, layout: "thermal" | "a4") =>
    post<PrintRecord>(`/api/v1/sales/invoices/${id}/print?layout=${layout}`),
  pdfUrl: (id: number | string) => `/api/v1/sales/invoices/${id}/pdf`,
  void: (id: number, reason: string, settlement: string) => post<Invoice>(`/api/v1/sales/invoices/${id}/void`, { reason, settlement }),
  prescription: (id: number) => fetchJson<PrescriptionIn & { verified_by_name?: string; created_at: string }>(`/api/v1/sales/invoices/${id}/prescription`),
  reminderControl: (id: number, body: { reminders_paused?: boolean; is_disputed?: boolean; dispute_note?: string }) =>
    fetchJson<Invoice>(`/api/v1/sales/invoices/${id}/reminders`, { method: "PATCH", body: JSON.stringify(body) }),
  sendWhatsApp: (id: number, body: { to_number?: string; mode?: string; consent_confirmed?: boolean; force_resend?: boolean }) =>
    post<Message>(`/api/v1/sales/invoices/${id}/whatsapp`, body),
  sendReminder: (id: number, mode?: string) => post<Message>(`/api/v1/sales/invoices/${id}/reminder`, { mode }),
  invoiceMessages: (id: number) => fetchJson<Message[]>(`/api/v1/sales/invoices/${id}/messages`),
  verifyPayment: (id: number, reason: string) => post(`/api/v1/sales/payments/${id}/verify`, { reason }),
  reversePayment: (id: number, reason: string) => post(`/api/v1/sales/payments/${id}/reverse`, { reason }),

  // Customers
  customers: (p: { q?: string; active_only?: boolean; with_balance_only?: boolean; limit?: number; offset?: number }) =>
    fetchJson<Paged<Customer>>(`/api/v1/customers${qs(p)}`),
  customer: (id: number | string) => fetchJson<Customer>(`/api/v1/customers/${id}`),
  createCustomer: (body: CustomerPayload) => post<Customer>("/api/v1/customers", body),
  updateCustomer: (id: number, body: Partial<CustomerPayload>) =>
    fetchJson<Customer>(`/api/v1/customers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  statement: (id: number | string, date_from?: string, date_to?: string) =>
    fetchJson<CustomerStatement>(`/api/v1/customers/${id}/statement${qs({ date_from, date_to })}`),
  reconcile: (id: number | string) => fetchJson<{ is_reconciled: boolean; difference: Money }>(`/api/v1/customers/${id}/reconcile`),
  receivePayment: (id: number, body: { idempotency_key: string; method: string; amount: string; tendered?: string; reference?: string; allocations?: { invoice_id: number; amount: string }[] | null; notes?: string }) =>
    post<PaymentResult>(`/api/v1/customers/${id}/payments`, body),
  applyCredit: (id: number) => post<{ applied: { invoice_number: string; amount: Money }[] }>(`/api/v1/customers/${id}/apply-credit`, {}),
  customerPayments: (p: { q?: string; customer_id?: number; method?: string; status?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) =>
    fetchJson<Paged<PaymentResult>>(`/api/v1/sales/payments${qs(p)}`),
  paymentDocument: (id: number | string) => fetchJson<ReceiptDocument>(`/api/v1/sales/payments/${id}/document`),
  paymentPdfUrl: (id: number | string) => `/api/v1/sales/payments/${id}/document.pdf`,
  adjustment: (id: number, body: { invoice_id: number; adjustment_type: string; amount: string; reason: string }) =>
    post(`/api/v1/customers/${id}/adjustments`, body),

  // Returns
  createReturn: (body: { idempotency_key: string; invoice_id: number; reason: string; items: { invoice_item_id: number; quantity_base: string; condition: string }[]; settlement: string; refund_method?: string | null; refund_reference?: string }) =>
    post<SalesReturn>("/api/v1/sales/returns", body),
  returns: (p: { q?: string; limit?: number; offset?: number }) => fetchJson<Paged<SalesReturn>>(`/api/v1/sales/returns${qs(p)}`),

  // Receivables
  receivables: (p: { q?: string; overdue_only?: boolean; limit?: number; offset?: number }) =>
    fetchJson<Paged<InvoiceListItem> & { total_outstanding: Money }>(`/api/v1/receivables${qs(p)}`),
  aging: () => fetchJson<{ as_of: string; totals: Record<string, Money>; grand_total: Money; customers: Record<string, Money | string | number>[] }>("/api/v1/receivables/aging"),

  // Shifts
  currentShift: () => fetchJson<{ shift: Shift | null }>("/api/v1/shifts/current"),
  openShift: (opening_cash: string) => post<Shift>("/api/v1/shifts", { opening_cash }),
  closeShift: (id: number, counted_cash: string, variance_note?: string) => post<Shift>(`/api/v1/shifts/${id}/close`, { counted_cash, variance_note }),
  cashMovement: (id: number, movement_type: string, category: string, amount: string, reason: string, reference?: string) =>
    post(`/api/v1/shifts/${id}/cash-movements`, { movement_type, category, amount, reason, reference: reference || null }),
  movementCategories: () =>
    fetchJson<{ categories: Record<string, Record<string, string>>; legacy_only: Record<string, Record<string, string>>; reference_required: string[] }>(
      "/api/v1/shifts/cash-movement-categories"),
  classifyMovement: (id: number, category: string, note: string) =>
    post(`/api/v1/shifts/cash-movements/${id}/classify`, { category, note }),
  reviewShift: (id: number, review_note: string) => post<Shift>(`/api/v1/shifts/${id}/review`, { review_note }),
  shifts: (day?: string) => fetchJson<Shift[]>(`/api/v1/shifts${qs({ day })}`),
  dayEnd: (day?: string) => fetchJson<DayEnd>(`/api/v1/shifts/day-end${qs({ day })}`),

  // Reports
  dashboard: () => fetchJson<{ today: Record<string, unknown>; month: Record<string, unknown>; outstanding_receivables: Money; overdue_receivables: Money; customers_with_balance: number }>("/api/v1/reports/sales/dashboard"),
  report: <T = unknown>(name: string, date_from?: string, date_to?: string) =>
    fetchJson<{ report: string; date_from: string; date_to: string; data: T }>(`/api/v1/reports/sales/${name}${qs({ date_from, date_to })}`),
  reportCsvUrl: (name: string, date_from?: string, date_to?: string) => `/api/v1/reports/sales/${name}${qs({ date_from, date_to, format: "csv" })}`,

  // Messaging
  messagingConfig: () => fetchJson<MessagingConfig>("/api/v1/messaging/config"),
  updateMessagingConfig: (values: Record<string, string>) =>
    fetchJson<MessagingConfig>("/api/v1/messaging/config", { method: "PUT", body: JSON.stringify({ values }) }),
  outbox: (p: { status?: string; purpose?: string; customer_id?: number; limit?: number; offset?: number }) =>
    fetchJson<Paged<Message>>(`/api/v1/messaging/outbox${qs(p)}`),
  retryMessage: (id: number) => post<Message>(`/api/v1/messaging/${id}/retry`),
  shareMessage: (id: number) => post<Message>(`/api/v1/messaging/${id}/share`),
  cancelMessage: (id: number) => post<Message>(`/api/v1/messaging/${id}/cancel`),
  scanReminders: (dry_run: boolean, on_date?: string) =>
    post<{ dry_run: boolean; results: ReminderCandidate[] }>("/api/v1/messaging/reminders/scan", { dry_run, on_date }),
};
