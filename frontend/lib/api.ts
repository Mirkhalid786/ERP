/**
 * Typed API Client for Pharmacy ERP Frontend
 * 
 * Interacts with FastAPI backend via Next.js proxy (/api/v1/...).
 * Uses credentials: 'include' for HTTP-only cookie session handling.
 */

// ── Auth & System ─────────────────────────────────────────────────────────────

export interface PharmacySetupPayload {
  pharmacy_name: string;
  legal_name?: string;
  gstin?: string;
  drug_license_number?: string;
  address_line1?: string;
  city?: string;
  state_code?: string;
  pincode?: string;
  phone?: string;
  invoice_prefix: string;
  admin_name: string;
  admin_username: string;
  admin_password: string;
}

export interface PharmacyResponse {
  id: number;
  name: string;
  legal_name?: string;
  gstin?: string;
  drug_license_number?: string;
  address_line1?: string;
  city?: string;
  state_code?: string;
  pincode?: string;
  phone?: string;
  invoice_prefix: string;
  is_setup_complete: boolean;
  created_at: string;
}

export interface CurrentUser {
  user_id: number;
  username: string;
  name: string;
  role_id: number;
  role: string;
  pharmacy_id: number;
  permissions: string[];
  last_login_at?: string;
}

export interface SetupStatus {
  setup_required: boolean;
}

export interface SettingItem {
  setting_key: string;
  setting_value?: string;
  description?: string;
  updated_at: string;
}

export interface UserItem {
  id: number;
  name: string;
  username: string;
  role_id: number;
  role_name: string;
  is_active: boolean;
  last_login_at?: string;
  created_at: string;
}

export interface RoleItem {
  id: number;
  name: string;
  description?: string;
  is_system_role: boolean;
  permissions: string[];
}

// ── Masters: Tax, Categories, Manufacturers, Units ─────────────────────────────

export interface TaxRateItem {
  id: number;
  pharmacy_id: number;
  name: string;
  rate_percentage: string | number;
  cgst_percentage: string | number;
  sgst_percentage: string | number;
  igst_percentage: string | number;
  cess_percentage: string | number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaxRateCreatePayload {
  name: string;
  rate_percentage: number;
  cgst_percentage?: number;
  sgst_percentage?: number;
  igst_percentage?: number;
  cess_percentage?: number;
  is_active?: boolean;
}

export interface TaxCalculationResult {
  taxable_amount: string;
  is_tax_inclusive: boolean;
  rate_percentage: string;
  cgst_rate: string;
  cgst_amount: string;
  sgst_rate: string;
  sgst_amount: string;
  igst_rate: string;
  igst_amount: string;
  total_tax: string;
  gross_amount: string;
}

export interface CategoryItem {
  id: number;
  pharmacy_id: number;
  name: string;
  description?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreatePayload {
  name: string;
  description?: string;
  is_active?: boolean;
}

export interface ManufacturerItem {
  id: number;
  pharmacy_id: number;
  name: string;
  short_code?: string;
  contact_email?: string;
  contact_phone?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ManufacturerCreatePayload {
  name: string;
  short_code?: string;
  contact_email?: string;
  contact_phone?: string;
  is_active?: boolean;
}

export interface UnitItem {
  id: number;
  pharmacy_id: number;
  name: string;
  abbreviation: string;
  is_fractional_allowed: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UnitCreatePayload {
  name: string;
  abbreviation: string;
  is_fractional_allowed?: boolean;
  is_active?: boolean;
}

// ── Medicines Catalogue ────────────────────────────────────────────────────────

export interface MedicineItem {
  id: number;
  pharmacy_id: number;
  name: string;
  brand_name?: string;
  generic_name?: string;
  strength?: string;
  dosage_form: string;
  category_id?: number;
  category_name?: string;
  manufacturer_id?: number;
  manufacturer_name?: string;
  tax_rate_id?: number;
  tax_rate_percentage?: string | number;
  hsn_code?: string;
  schedule_type: string;
  requires_prescription: boolean;
  base_unit_id: number;
  base_unit_name?: string;
  base_unit_abbr?: string;
  barcode?: string;
  reorder_level: string | number;
  reorder_quantity: string | number;
  storage_instructions?: string;
  side_effects?: string;
  mrp?: string | number;
  is_discontinued?: boolean;
  source_name?: string;
  source_id?: string;
  pack_size_label?: string;
  current_stock_base: string | number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface MedicineCreatePayload {
  name: string;
  brand_name?: string;
  generic_name?: string;
  strength?: string;
  dosage_form: string;
  category_id?: number;
  manufacturer_id?: number;
  tax_rate_id?: number;
  hsn_code?: string;
  schedule_type?: string;
  requires_prescription?: boolean;
  base_unit_id: number;
  barcode?: string;
  reorder_level?: number;
  reorder_quantity?: number;
  storage_instructions?: string;
  side_effects?: string;
  is_active?: boolean;
}

export interface MedicineBarcodeItem {
  id: number;
  pharmacy_id: number;
  medicine_id: number;
  barcode: string;
  packaging_unit_id?: number;
  description?: string;
  created_at: string;
}

export interface MedicineBarcodeCreatePayload {
  barcode: string;
  packaging_unit_id?: number;
  description?: string;
}

export interface MedicineUnitConversionItem {
  id: number;
  medicine_id: number;
  packaging_unit_id: number;
  packaging_unit_name?: string;
  packaging_unit_abbr?: string;
  conversion_factor: string | number;
  barcode?: string;
  is_sales_unit: boolean;
  is_purchase_unit: boolean;
  created_at: string;
  updated_at: string;
}

export interface MedicineUnitConversionCreatePayload {
  packaging_unit_id: number;
  conversion_factor: number;
  barcode?: string;
  is_sales_unit?: boolean;
  is_purchase_unit?: boolean;
}

// ── Batches ────────────────────────────────────────────────────────────────────

export interface MedicineBatchItem {
  id: number;
  pharmacy_id: number;
  medicine_id: number;
  medicine_name?: string;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  mrp: string | number;
  purchase_rate: string | number;
  cost_per_base_unit: string | number;
  default_selling_rate: string | number;
  rack_location?: string;
  current_stock_base: string | number;
  is_expired: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface MedicineBatchCreatePayload {
  medicine_id: number;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  mrp: number;
  purchase_rate?: number;
  cost_per_base_unit?: number;
  default_selling_rate?: number;
  rack_location?: string;
  is_active?: boolean;
}

// ── Inventory Ledger, Opening Stock, Adjustments & Alerts ─────────────────────

export interface OpeningStockPayload {
  medicine_id: number;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  packaging_unit_id?: number;
  quantity_in_pack: number;
  mrp: number;
  purchase_rate?: number;
  default_selling_rate?: number;
  rack_location?: string;
  notes?: string;
}

export interface StockMovementItem {
  id: number;
  pharmacy_id: number;
  medicine_id: number;
  medicine_name?: string;
  batch_id: number;
  batch_number?: string;
  movement_type: string;
  reference_id?: string;
  reference_type?: string;
  quantity_base: string | number;
  balance_after_base: string | number;
  unit_cost: string | number;
  total_cost: string | number;
  reason?: string;
  notes?: string;
  user_id?: number;
  created_at: string;
}

export interface StockAdjustmentItemDetail {
  id: number;
  medicine_id: number;
  medicine_name?: string;
  batch_id: number;
  batch_number?: string;
  system_stock_base: string | number;
  physical_stock_base: string | number;
  adjustment_qty_base: string | number;
  unit_cost: string | number;
  total_cost: string | number;
  notes?: string;
}

export interface StockAdjustmentResponse {
  id: number;
  pharmacy_id: number;
  adjustment_number: string;
  reason_code: string;
  status: string;
  notes?: string;
  adjusted_by?: number;
  created_at: string;
  items: StockAdjustmentItemDetail[];
}

export interface StockAdjustmentCreatePayload {
  reason_code: string;
  notes?: string;
  items: {
    medicine_id: number;
    batch_id: number;
    physical_stock_base: number;
    notes?: string;
  }[];
}

export interface LowStockAlertItem {
  medicine_id: number;
  medicine_name: string;
  generic_name?: string;
  dosage_form: string;
  reorder_level: string | number;
  current_stock_base: string | number;
  base_unit_name: string;
  base_unit_abbr: string;
}

export interface ExpiryAlertItem {
  batch_id: number;
  medicine_id: number;
  medicine_name: string;
  batch_number: string;
  expiry_date: string;
  days_to_expiry: number;
  current_stock_base: string | number;
  base_unit_abbr?: string;
  rack_location?: string;
  is_expired: boolean;
  cost_per_base_unit?: string | number;
  stock_value?: string | number;
}

export interface InventoryValuation {
  active_batches_count: number;
  total_base_units: string;
  total_cost_valuation: string;
}

// ── Phase 3: Suppliers & Purchasing ──────────────────────────────────────────

export interface SupplierItem {
  id: number;
  pharmacy_id: number;
  name: string;
  legal_name?: string;
  gstin?: string;
  state_code?: string;
  drug_license_no?: string;
  contact_person?: string;
  phone?: string;
  email?: string;
  address?: string;
  city?: string;
  state?: string;
  pincode?: string;
  payment_terms_days: number;
  credit_limit: string | number;
  notes?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SupplierCreatePayload {
  name: string;
  legal_name?: string;
  gstin?: string;
  state_code?: string;
  drug_license_no?: string;
  contact_person?: string;
  phone?: string;
  email?: string;
  address?: string;
  city?: string;
  state?: string;
  pincode?: string;
  payment_terms_days?: number;
  credit_limit?: number | string;
  notes?: string;
  is_active?: boolean;
}

export interface PurchaseOrderItemDetail {
  id: number;
  po_id: number;
  medicine_id: number;
  medicine_name?: string;
  packaging_unit_id: number;
  packaging_unit_name?: string;
  conversion_factor: string | number;
  ordered_qty_pack: string | number;
  ordered_qty_base: string | number;
  received_qty_base: string | number;
  purchase_rate: string | number;
  discount_percent: string | number;
  tax_rate_id?: number;
  tax_rate_percentage?: string | number;
  tax_amount: string | number;
  line_total: string | number;
  notes?: string;
}

export interface PurchaseOrderItemPayload {
  medicine_id: number;
  packaging_unit_id: number;
  ordered_qty_pack: number | string;
  purchase_rate: number | string;
  discount_percent?: number | string;
  tax_rate_id?: number;
  notes?: string;
}

export interface PurchaseOrderResponse {
  id: number;
  pharmacy_id: number;
  po_number: string;
  supplier_id: number;
  supplier_name?: string;
  order_date: string;
  expected_delivery_date?: string;
  status: string;
  subtotal: string | number;
  discount_total: string | number;
  tax_total: string | number;
  grand_total: string | number;
  notes?: string;
  created_by: number;
  approved_by?: number;
  approved_at?: string;
  cancelled_by?: number;
  cancelled_at?: string;
  created_at: string;
  updated_at: string;
  lifecycle_stage?: string;
  goods_receipts_count?: number;
  invoices_count?: number;
  total_ordered_base?: string | number;
  total_received_base?: string | number;
  cancel_reason?: string;
  items: PurchaseOrderItemDetail[];
}

export interface PurchaseOrderLifecycleResponse {
  po: PurchaseOrderResponse;
  goods_receipts: GoodsReceiptResponse[];
  invoices: {
    id: number;
    invoice_number: string;
    invoice_date: string;
    due_date: string;
    status: string;
    payment_status: string;
    calculated_grand_total: string | number;
    supplier_declared_total: string | number;
    discrepancy_amount: string | number;
    paid_amount: string | number;
    grn_id?: number;
    po_id?: number;
    items_count: number;
  }[];
  lifecycle_stage: string;
  total_ordered_base: string | number;
  total_received_base: string | number;
  fulfillment_percent: number;
  posted_grns_count: number;
  posted_invs_count: number;
}

export interface PurchaseOrderCreatePayload {
  supplier_id: number;
  order_date: string;
  expected_delivery_date?: string;
  notes?: string;
  items: PurchaseOrderItemPayload[];
}

export interface GoodsReceiptItemDetail {
  id: number;
  grn_id: number;
  po_item_id?: number;
  medicine_id: number;
  medicine_name?: string;
  batch_id?: number;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  packaging_unit_id: number;
  packaging_unit_name?: string;
  conversion_factor: string | number;
  received_qty_pack: string | number;
  free_qty_pack: string | number;
  rejected_qty_pack: string | number;
  accepted_qty_base: string | number;
  rejected_qty_base: string | number;
  rejection_reason?: string;
  purchase_rate: string | number;
  discount_percent: string | number;
  mrp: string | number;
  selling_rate: string | number;
  cost_per_base_unit: string | number;
  tax_rate_id?: number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  tax_amount: string | number;
  line_total: string | number;
  rack_location?: string;
}

export interface GoodsReceiptItemPayload {
  po_item_id?: number;
  medicine_id: number;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  packaging_unit_id: number;
  received_qty_pack: number | string;
  free_qty_pack?: number | string;
  rejected_qty_pack?: number | string;
  rejection_reason?: string;
  purchase_rate: number | string;
  discount_percent?: number | string;
  mrp: number | string;
  selling_rate: number | string;
  tax_rate_id?: number;
  rack_location?: string;
}

export interface GoodsReceiptResponse {
  id: number;
  pharmacy_id: number;
  grn_number: string;
  supplier_id: number;
  supplier_name?: string;
  po_id?: number;
  po_number?: string;
  receipt_date: string;
  supplier_challan_no?: string;
  supplier_invoice_no?: string;
  status: string;
  total_received_base: string | number;
  total_accepted_base: string | number;
  total_rejected_base: string | number;
  total_free_base: string | number;
  subtotal: string | number;
  tax_total: string | number;
  grand_total: string | number;
  notes?: string;
  received_by: number;
  posted_at?: string;
  created_at: string;
  updated_at: string;
  items: GoodsReceiptItemDetail[];
}

export interface GoodsReceiptCreatePayload {
  supplier_id: number;
  po_id?: number;
  receipt_date: string;
  supplier_challan_no?: string;
  supplier_invoice_no?: string;
  notes?: string;
  items: GoodsReceiptItemPayload[];
}

export interface SupplierInvoiceItemDetail {
  id: number;
  invoice_id: number;
  grn_item_id?: number;
  medicine_id: number;
  medicine_name?: string;
  batch_id?: number;
  batch_number: string;
  packaging_unit_id: number;
  packaging_unit_name?: string;
  conversion_factor: string | number;
  billed_qty_pack: string | number;
  billed_qty_base: string | number;
  free_qty_pack: string | number;
  purchase_rate: string | number;
  discount_percent: string | number;
  discount_amount: string | number;
  taxable_amount: string | number;
  tax_rate_id?: number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  tax_amount: string | number;
  line_total: string | number;
}

export interface SupplierInvoiceItemPayload {
  grn_item_id?: number;
  medicine_id: number;
  batch_number: string;
  packaging_unit_id: number;
  billed_qty_pack: number | string;
  free_qty_pack?: number | string;
  purchase_rate: number | string;
  discount_percent?: number | string;
  tax_rate_id?: number;
}

export interface SupplierInvoiceResponse {
  id: number;
  pharmacy_id: number;
  invoice_number: string;
  supplier_id: number;
  supplier_name?: string;
  grn_id?: number;
  grn_number?: string;
  po_id?: number;
  po_number?: string;
  invoice_date: string;
  received_date: string;
  due_date: string;
  payment_terms_days: number;
  is_tax_inclusive: boolean;
  currency: string;
  subtotal: string | number;
  discount_total: string | number;
  cgst_total: string | number;
  sgst_total: string | number;
  igst_total: string | number;
  tax_total: string | number;
  other_charges: string | number;
  round_off: string | number;
  calculated_grand_total: string | number;
  supplier_declared_total: string | number;
  discrepancy_amount: string | number;
  discrepancy_notes?: string;
  status: string;
  payment_status: string;
  paid_amount: string | number;
  returned_amount?: string | number;
  outstanding_amount?: string | number;
  discrepancy_accepted?: boolean;
  finalized_by_name?: string | null;
  finalized_at?: string | null;
  cancelled_by_name?: string | null;
  cancelled_at?: string | null;
  cancel_reason?: string | null;
  replaces_invoice_id?: number | null;
  notes?: string;
  created_by: number;
  created_by_name?: string | null;
  created_at: string;
  updated_at: string;
  items: SupplierInvoiceItemDetail[];
}

/**
 * Shared helper that treats POSTED and FINALIZED as equivalent completed statuses
 * for supplier invoices, preserving backward compatibility.
 */
export function isInvoicePosted(status?: string | null): boolean {
  if (!status) return false;
  return status === "POSTED" || status === "FINALIZED";
}

export interface SupplierInvoiceCreatePayload {
  supplier_id: number;
  invoice_number: string;
  grn_id?: number;
  po_id?: number;
  invoice_date: string;
  received_date: string;
  due_date?: string;
  payment_terms_days?: number;
  is_tax_inclusive?: boolean;
  other_charges?: number | string;
  round_off?: number | string;
  supplier_declared_total: number | string;
  discrepancy_notes?: string;
  notes?: string;
  items: SupplierInvoiceItemPayload[];
}

export interface SupplierReturnItemDetail {
  id: number;
  return_id: number;
  grn_item_id?: number;
  invoice_item_id?: number;
  medicine_id: number;
  medicine_name?: string;
  batch_id: number;
  batch_number?: string;
  packaging_unit_id: number;
  packaging_unit_name?: string;
  conversion_factor: string | number;
  return_qty_pack: string | number;
  return_qty_base: string | number;
  unit_cost_base: string | number;
  refund_rate: string | number;
  tax_rate_id?: number;
  tax_amount: string | number;
  total_amount: string | number;
  reason?: string;
}

export interface SupplierReturnItemPayload {
  grn_item_id?: number;
  invoice_item_id?: number;
  medicine_id: number;
  batch_id: number;
  packaging_unit_id: number;
  return_qty_pack: number | string;
  refund_rate: number | string;
  tax_rate_id?: number;
  reason?: string;
}

export interface SupplierReturnResponse {
  id: number;
  pharmacy_id: number;
  return_number: string;
  supplier_id: number;
  supplier_name?: string;
  grn_id?: number;
  grn_number?: string;
  invoice_id?: number;
  invoice_number?: string;
  return_date: string;
  status: string;
  reason_code: string;
  subtotal: string | number;
  tax_total: string | number;
  total_return_amount: string | number;
  debit_note_reference?: string;
  notes?: string;
  created_by: number;
  posted_at?: string;
  created_at: string;
  updated_at: string;
  items: SupplierReturnItemDetail[];
}

export interface SupplierReturnCreatePayload {
  supplier_id: number;
  grn_id?: number;
  invoice_id?: number;
  return_date: string;
  reason_code: string;
  debit_note_reference?: string;
  notes?: string;
  items: SupplierReturnItemPayload[];
}

export interface DirectPurchaseItemPayload {
  medicine_id: number;
  packaging_unit_id: number;
  quantity: number | string;
  free_quantity?: number | string;
  purchase_rate: number | string;
  discount_percent?: number | string;
  tax_rate_id?: number;
  batch_number: string;
  manufacturing_date?: string;
  expiry_date: string;
  mrp: number | string;
  selling_rate?: number | string;
  rack_location?: string;
}

export interface DirectPurchaseCreatePayload {
  supplier_id: number;
  invoice_number: string;
  invoice_date: string;
  due_date?: string;
  payment_terms_days?: number;
  is_tax_inclusive?: boolean;
  po_id?: number;
  other_charges?: number | string;
  round_off?: number | string;
  supplier_declared_total?: number | string;
  notes?: string;
  post_now?: boolean;
  items: DirectPurchaseItemPayload[];
}

export interface SupplierCreditNoteResponse {
  id: number;
  pharmacy_id: number;
  supplier_id: number;
  supplier_name?: string;
  credit_note_number: string;
  note_date: string;
  invoice_id?: number;
  invoice_number?: string;
  reason_code: string;
  taxable_amount: string | number;
  cgst_amount: string | number;
  sgst_amount: string | number;
  igst_amount: string | number;
  total_amount: string | number;
  notes?: string;
  status: string;
  created_by: number;
  created_at: string;
  cancelled_by?: number;
  cancelled_at?: string;
  cancel_reason?: string;
}

export interface SupplierCreditNoteCreatePayload {
  supplier_id: number;
  credit_note_number: string;
  note_date: string;
  invoice_id?: number;
  reason_code: string;
  taxable_amount: number | string;
  cgst_amount?: number | string;
  sgst_amount?: number | string;
  igst_amount?: number | string;
  total_amount: number | string;
  notes?: string;
}

// ── HTTP Client Core ──────────────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  detail: string;
  /** Field name -> message, for request-validation errors (422) */
  fields: Record<string, string>;

  constructor(status: number, detail: string, fields: Record<string, string> = {}) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.fields = fields;
    this.name = "ApiError";
  }
}

const FIELD_LABELS: Record<string, string> = { gstin: "GSTIN", state_code: "State code", name: "Name" };

/** Turn FastAPI/Pydantic validation details into readable text plus per-field messages (never raw JSON). */
function describeDetail(detail: unknown): { text: string; fields: Record<string, string> } {
  if (typeof detail === "string") return { text: detail, fields: {} };
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    const lines: string[] = [];
    for (const e of detail as { loc?: (string | number)[]; msg?: string }[]) {
      const field = String(e?.loc?.[e.loc.length - 1] ?? "");
      const msg = String(e?.msg ?? "Invalid value").replace(/^Value error, /, "");
      if (field && !(field in fields)) fields[field] = msg;
      const label = FIELD_LABELS[field] || field.replace(/_/g, " ");
      lines.push(field && field !== "body" ? `${label.charAt(0).toUpperCase() + label.slice(1)}: ${msg}` : msg);
    }
    return { text: lines.join(" · ") || "Some values are not valid", fields };
  }
  if (detail && typeof detail === "object") {
    // {message, errors?, warnings?} from business checks (e.g. finalising a bill)
    const d = detail as { message?: unknown; errors?: unknown; warnings?: unknown };
    const parts = [d.message, ...(Array.isArray(d.errors) ? d.errors : []), ...(Array.isArray(d.warnings) ? d.warnings : [])]
      .filter((p) => typeof p === "string" && p) as string[];
    if (parts.length) return { text: parts.join(" · "), fields: {} };
  }
  return { text: "The request could not be completed", fields: {} };
}

const CSRF_COOKIE_NAME = "pharmacy_csrf";
const CSRF_SAFE_METHODS = ["GET", "HEAD", "OPTIONS"];

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

export async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  // The backend rejects state-changing requests unless they echo the CSRF cookie
  const csrfToken = CSRF_SAFE_METHODS.includes(method) ? null : readCookie(CSRF_COOKIE_NAME);

  const res = await fetch(url, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(options.headers || {}),
    },
  });

  if (!res.ok) {
    let errorDetail = res.statusText;
    let fields: Record<string, string> = {};
    try {
      const data = await res.json();
      if (data && data.detail) {
        const d = describeDetail(data.detail);
        errorDetail = d.text;
        fields = d.fields;
      }
    } catch {
      // fallback to statusText
    }
    throw new ApiError(res.status, errorDetail, fields);
  }

  if (res.status === 204) {
    return {} as T;
  }

  return res.json();
}

export async function fetchJsonWithHeaders<T>(url: string, options: RequestInit = {}): Promise<{ data: T; headers: Headers }> {
  const method = (options.method || "GET").toUpperCase();
  const csrfToken = CSRF_SAFE_METHODS.includes(method) ? null : readCookie(CSRF_COOKIE_NAME);

  const res = await fetch(url, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(options.headers || {}),
    },
  });

  if (!res.ok) {
    let errorDetail = res.statusText;
    let fields: Record<string, string> = {};
    try {
      const data = await res.json();
      if (data && data.detail) {
        const d = describeDetail(data.detail);
        errorDetail = d.text;
        fields = d.fields;
      }
    } catch {
      // fallback to statusText
    }
    throw new ApiError(res.status, errorDetail, fields);
  }

  if (res.status === 204) {
    return { data: {} as T, headers: res.headers };
  }

  const data = await res.json();
  return { data, headers: res.headers };
}

// ── Exported API Methods ──────────────────────────────────────────────────────

export const api = {
  // Auth & System
  getSetupStatus: () => fetchJson<SetupStatus>("/api/v1/setup/status"),
  setupPharmacy: (payload: PharmacySetupPayload) =>
    fetchJson<PharmacyResponse>("/api/v1/setup", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  login: (username: string, password: string) =>
    fetchJson<{ message: string; user_id: number; username: string; name: string; role: string; pharmacy_id: number }>(
      "/api/v1/auth/login",
      {
        method: "POST",
        body: JSON.stringify({ username, password }),
      }
    ),
  logout: () =>
    fetchJson<{ message: string }>("/api/v1/auth/logout", {
      method: "POST",
    }),
  getMe: () => fetchJson<CurrentUser>("/api/v1/auth/me"),
  getCurrentUser: () => fetchJson<CurrentUser>("/api/v1/auth/me"),
  getHealth: () => fetchJson<{ status: string; database?: string }>("/api/v1/health/ready"),
  getSettings: () => fetchJson<SettingItem[]>("/api/v1/settings"),
  getRoles: () => fetchJson<RoleItem[]>("/api/v1/roles"),
  getUsers: () => fetchJson<UserItem[]>("/api/v1/users"),

  // Tax Rates
  getTaxRates: (activeOnly = true) =>
    fetchJson<TaxRateItem[]>(`/api/v1/tax-rates?active_only=${activeOnly}`),
  createTaxRate: (payload: TaxRateCreatePayload) =>
    fetchJson<TaxRateItem>("/api/v1/tax-rates", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTaxRate: (id: number, payload: Partial<TaxRateCreatePayload>) =>
    fetchJson<TaxRateItem>(`/api/v1/tax-rates/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  calculateTax: (amount: number, rate: number, inclusive = true, interstate = false) =>
    fetchJson<TaxCalculationResult>(
      `/api/v1/tax-rates/calculate?amount=${amount}&rate_percentage=${rate}&is_tax_inclusive=${inclusive}&is_inter_state=${interstate}`,
      { method: "POST" }
    ),

  // Categories
  getCategories: (activeOnly = true) =>
    fetchJson<CategoryItem[]>(`/api/v1/categories?active_only=${activeOnly}`),
  createCategory: (payload: CategoryCreatePayload) =>
    fetchJson<CategoryItem>("/api/v1/categories", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCategory: (id: number, payload: Partial<CategoryCreatePayload>) =>
    fetchJson<CategoryItem>(`/api/v1/categories/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  // Manufacturers
  getManufacturers: (activeOnly = true) =>
    fetchJson<ManufacturerItem[]>(`/api/v1/manufacturers?active_only=${activeOnly}`),
  createManufacturer: (payload: ManufacturerCreatePayload) =>
    fetchJson<ManufacturerItem>("/api/v1/manufacturers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateManufacturer: (id: number, payload: Partial<ManufacturerCreatePayload>) =>
    fetchJson<ManufacturerItem>(`/api/v1/manufacturers/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  // Units
  getUnits: (activeOnly = true) =>
    fetchJson<UnitItem[]>(`/api/v1/units?active_only=${activeOnly}`),
  createUnit: (payload: UnitCreatePayload) =>
    fetchJson<UnitItem>("/api/v1/units", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateUnit: (id: number, payload: Partial<UnitCreatePayload>) =>
    fetchJson<UnitItem>(`/api/v1/units/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  // Medicines
  getMedicines: (
    params: {
      search?: string;
      category_id?: number;
      manufacturer_id?: number;
      schedule_type?: string;
      requires_prescription?: boolean;
      active_only?: boolean;
      discontinued?: boolean;
      low_stock_only?: boolean;
      limit?: number;
      offset?: number;
    } = {},
    options?: RequestInit
  ) => {
    const q = new URLSearchParams();
    if (params.search) q.set("search", params.search);
    if (params.category_id) q.set("category_id", String(params.category_id));
    if (params.manufacturer_id) q.set("manufacturer_id", String(params.manufacturer_id));
    if (params.schedule_type) q.set("schedule_type", params.schedule_type);
    if (params.requires_prescription !== undefined) q.set("requires_prescription", String(params.requires_prescription));
    if (params.active_only !== undefined) q.set("active_only", String(params.active_only));
    if (params.discontinued !== undefined) q.set("discontinued", String(params.discontinued));
    if (params.low_stock_only !== undefined) q.set("low_stock_only", String(params.low_stock_only));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<MedicineItem[]>(`/api/v1/medicines?${q.toString()}`, options);
  },
  getMedicinesPage: async (
    params: {
      search?: string;
      category_id?: number;
      manufacturer_id?: number;
      schedule_type?: string;
      requires_prescription?: boolean;
      active_only?: boolean;
      discontinued?: boolean;
      low_stock_only?: boolean;
      limit?: number;
      offset?: number;
    } = {},
    options?: RequestInit
  ): Promise<{ items: MedicineItem[]; total: number }> => {
    const q = new URLSearchParams();
    if (params.search) q.set("search", params.search);
    if (params.category_id) q.set("category_id", String(params.category_id));
    if (params.manufacturer_id) q.set("manufacturer_id", String(params.manufacturer_id));
    if (params.schedule_type) q.set("schedule_type", params.schedule_type);
    if (params.requires_prescription !== undefined) q.set("requires_prescription", String(params.requires_prescription));
    if (params.active_only !== undefined) q.set("active_only", String(params.active_only));
    if (params.discontinued !== undefined) q.set("discontinued", String(params.discontinued));
    if (params.low_stock_only !== undefined) q.set("low_stock_only", String(params.low_stock_only));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    const { data, headers } = await fetchJsonWithHeaders<MedicineItem[]>(`/api/v1/medicines?${q.toString()}`, options);
    const totalHeader = headers.get("x-total-count") || headers.get("X-Total-Count");
    const total = totalHeader ? parseInt(totalHeader, 10) : data.length;
    return {
      items: data,
      total: isNaN(total) ? data.length : total,
    };
  },
  getMedicine: (id: number) => fetchJson<MedicineItem>(`/api/v1/medicines/${id}`),
  createMedicine: (payload: MedicineCreatePayload) =>
    fetchJson<MedicineItem>("/api/v1/medicines", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMedicine: (id: number, payload: Partial<MedicineCreatePayload>) =>
    fetchJson<MedicineItem>(`/api/v1/medicines/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  lookupBarcode: (barcode: string) =>
    fetchJson<MedicineItem>(`/api/v1/medicines/lookup/barcode/${encodeURIComponent(barcode)}`),
  getMedicineBarcodes: (medicineId: number) =>
    fetchJson<MedicineBarcodeItem[]>(`/api/v1/medicines/${medicineId}/barcodes`),
  addMedicineBarcode: (medicineId: number, payload: MedicineBarcodeCreatePayload) =>
    fetchJson<MedicineBarcodeItem>(`/api/v1/medicines/${medicineId}/barcodes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteMedicineBarcode: (medicineId: number, barcodeId: number) =>
    fetchJson<void>(`/api/v1/medicines/${medicineId}/barcodes/${barcodeId}`, {
      method: "DELETE",
    }),
  getMedicineConversions: (medicineId: number) =>
    fetchJson<MedicineUnitConversionItem[]>(`/api/v1/medicines/${medicineId}/conversions`),
  addMedicineConversion: (medicineId: number, payload: MedicineUnitConversionCreatePayload) =>
    fetchJson<MedicineUnitConversionItem>(`/api/v1/medicines/${medicineId}/conversions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteMedicineConversion: (medicineId: number, conversionId: number) =>
    fetchJson<void>(`/api/v1/medicines/${medicineId}/conversions/${conversionId}`, {
      method: "DELETE",
    }),
  getMedicineBatches: (medicineId: number) =>
    fetchJson<MedicineBatchItem[]>(`/api/v1/medicines/${medicineId}/batches`),

  // Batches
  getBatches: (params: {
    medicine_id?: number;
    active_only?: boolean;
    non_expired_only?: boolean;
    limit?: number;
    offset?: number;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.medicine_id) q.set("medicine_id", String(params.medicine_id));
    if (params.active_only !== undefined) q.set("active_only", String(params.active_only));
    if (params.non_expired_only !== undefined) q.set("non_expired_only", String(params.non_expired_only));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<MedicineBatchItem[]>(`/api/v1/batches?${q.toString()}`);
  },
  getBatch: (id: number) => fetchJson<MedicineBatchItem>(`/api/v1/batches/${id}`),
  createBatch: (payload: MedicineBatchCreatePayload) =>
    fetchJson<MedicineBatchItem>("/api/v1/batches", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateBatch: (id: number, payload: Partial<MedicineBatchCreatePayload>) =>
    fetchJson<MedicineBatchItem>(`/api/v1/batches/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteBatch: (id: number) =>
    fetchJson<void>(`/api/v1/batches/${id}`, {
      method: "DELETE",
    }),

  // Inventory
  createOpeningStock: (payload: OpeningStockPayload) =>
    fetchJson<StockMovementItem>("/api/v1/inventory/opening-stock", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getStockMovements: (params: {
    medicine_id?: number;
    batch_id?: number;
    movement_type?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.medicine_id) q.set("medicine_id", String(params.medicine_id));
    if (params.batch_id) q.set("batch_id", String(params.batch_id));
    if (params.movement_type) q.set("movement_type", params.movement_type);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<StockMovementItem[]>(`/api/v1/inventory/movements?${q.toString()}`);
  },
  createStockAdjustment: (payload: StockAdjustmentCreatePayload) =>
    fetchJson<StockAdjustmentResponse>("/api/v1/inventory/adjustments", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getStockAdjustments: (limit = 50, offset = 0) =>
    fetchJson<StockAdjustmentResponse[]>(`/api/v1/inventory/adjustments?limit=${limit}&offset=${offset}`),
  getLowStockAlerts: () => fetchJson<LowStockAlertItem[]>("/api/v1/inventory/alerts/low-stock"),
  getNearExpiryAlerts: (days = 90) => fetchJson<ExpiryAlertItem[]>(`/api/v1/inventory/alerts/near-expiry?days=${days}`),
  getExpiredBatches: () => fetchJson<ExpiryAlertItem[]>("/api/v1/inventory/alerts/expired"),
  getInventoryValuation: () => fetchJson<InventoryValuation>("/api/v1/inventory/valuation"),

  // Phase 3: Suppliers
  getSuppliers: (params: { search?: string; active_only?: boolean; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.search) q.set("search", params.search);
    if (params.active_only !== undefined) q.set("active_only", String(params.active_only));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<SupplierItem[]>(`/api/v1/suppliers?${q.toString()}`);
  },
  getSupplier: (id: number) => fetchJson<SupplierItem>(`/api/v1/suppliers/${id}`),
  createSupplier: (payload: SupplierCreatePayload) =>
    fetchJson<SupplierItem>("/api/v1/suppliers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSupplier: (id: number, payload: Partial<SupplierCreatePayload>) =>
    fetchJson<SupplierItem>(`/api/v1/suppliers/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSupplier: (id: number) =>
    fetchJson<void>(`/api/v1/suppliers/${id}`, {
      method: "DELETE",
    }),

  // Phase 3: Purchase Orders
  getPurchaseOrders: (params: { supplier_id?: number; status?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.supplier_id) q.set("supplier_id", String(params.supplier_id));
    if (params.status) q.set("status", params.status);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<PurchaseOrderResponse[]>(`/api/v1/purchases/orders?${q.toString()}`);
  },
  getPurchaseOrder: (id: number) => fetchJson<PurchaseOrderResponse>(`/api/v1/purchases/orders/${id}`),
  createPurchaseOrder: (payload: PurchaseOrderCreatePayload) =>
    fetchJson<PurchaseOrderResponse>("/api/v1/purchases/orders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updatePurchaseOrder: (id: number, payload: PurchaseOrderCreatePayload) =>
    fetchJson<PurchaseOrderResponse>(`/api/v1/purchases/orders/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getPurchaseOrderLifecycle: (id: number) =>
    fetchJson<PurchaseOrderLifecycleResponse>(`/api/v1/purchases/orders/${id}/lifecycle`),
  updatePurchaseOrderStatus: (id: number, status: string, reason?: string) =>
    fetchJson<PurchaseOrderResponse>(`/api/v1/purchases/orders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status, reason }),
    }),

  // Phase 3: Goods Receipts
  getGoodsReceipts: (params: { supplier_id?: number; status?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.supplier_id) q.set("supplier_id", String(params.supplier_id));
    if (params.status) q.set("status", params.status);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<GoodsReceiptResponse[]>(`/api/v1/purchases/receipts?${q.toString()}`);
  },
  getGoodsReceipt: (id: number) => fetchJson<GoodsReceiptResponse>(`/api/v1/purchases/receipts/${id}`),
  createGoodsReceipt: (payload: GoodsReceiptCreatePayload) =>
    fetchJson<GoodsReceiptResponse>("/api/v1/purchases/receipts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  postGoodsReceipt: (id: number) =>
    fetchJson<GoodsReceiptResponse>(`/api/v1/purchases/receipts/${id}/post`, {
      method: "POST",
    }),

  // Phase 3: Supplier Invoices
  getSupplierInvoices: (params: { supplier_id?: number; payment_status?: string; status?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.supplier_id) q.set("supplier_id", String(params.supplier_id));
    if (params.payment_status) q.set("payment_status", params.payment_status);
    if (params.status) q.set("status", params.status);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<SupplierInvoiceResponse[]>(`/api/v1/purchases/invoices?${q.toString()}`);
  },
  getSupplierInvoice: (id: number) => fetchJson<SupplierInvoiceResponse>(`/api/v1/purchases/invoices/${id}`),
  createSupplierInvoice: (payload: SupplierInvoiceCreatePayload) =>
    fetchJson<SupplierInvoiceResponse>("/api/v1/purchases/invoices", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  finalizeCheckSupplierInvoice: (id: number) =>
    fetchJson<{
      errors: string[];
      warnings: string[];
      can_finalize: boolean;
      calculated_total: string | number;
      declared_total: string | number;
      discrepancy: string | number;
    }>(`/api/v1/purchases/invoices/${id}/finalize-check`),
  finalizeSupplierInvoice: (id: number, acknowledgeWarnings = true) =>
    fetchJson<SupplierInvoiceResponse>(`/api/v1/purchases/invoices/${id}/finalize`, {
      method: "POST",
      body: JSON.stringify({ acknowledge_warnings: acknowledgeWarnings }),
    }),
  cancelSupplierInvoice: (id: number, reason: string) =>
    fetchJson<SupplierInvoiceResponse>(`/api/v1/purchases/invoices/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  // Phase 3: Supplier Returns
  getSupplierReturns: (params: { supplier_id?: number; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.supplier_id) q.set("supplier_id", String(params.supplier_id));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<SupplierReturnResponse[]>(`/api/v1/purchases/returns?${q.toString()}`);
  },
  getSupplierReturn: (id: number) => fetchJson<SupplierReturnResponse>(`/api/v1/purchases/returns/${id}`),
  createSupplierReturn: (payload: SupplierReturnCreatePayload) =>
    fetchJson<SupplierReturnResponse>("/api/v1/purchases/returns", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  postSupplierReturn: (id: number) =>
    fetchJson<SupplierReturnResponse>(`/api/v1/purchases/returns/${id}/post`, {
      method: "POST",
    }),

  // Phase 3 Redesign: Direct Purchasing
  createOrPostDirectPurchase: (payload: DirectPurchaseCreatePayload) =>
    fetchJson<SupplierInvoiceResponse>("/api/v1/purchases/direct", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  postDraftPurchase: (id: number) =>
    fetchJson<SupplierInvoiceResponse>(`/api/v1/purchases/direct/${id}/post`, {
      method: "POST",
    }),
  deleteDraftPurchase: (id: number) =>
    fetchJson<{ success: boolean; message: string }>(`/api/v1/purchases/direct/${id}`, {
      method: "DELETE",
    }),
  cancelPostedPurchase: (id: number, reason: string) =>
    fetchJson<SupplierInvoiceResponse>(`/api/v1/purchases/direct/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  getDirectPurchasePrefillFromPo: (poId: number) =>
    fetchJson<{
      supplier_id: number;
      po_id: number;
      po_number: string;
      order_date: string;
      notes: string;
      items: any[];
    }>(`/api/v1/purchases/direct/from-po/${poId}`),

  // Supplier Credit Notes (Financial)
  getSupplierCreditNotes: (params: { supplier_id?: number; invoice_id?: number; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.supplier_id) q.set("supplier_id", String(params.supplier_id));
    if (params.invoice_id) q.set("invoice_id", String(params.invoice_id));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return fetchJson<SupplierCreditNoteResponse[]>(`/api/v1/purchases/returns/credit-notes?${q.toString()}`);
  },
  createSupplierCreditNote: (payload: SupplierCreditNoteCreatePayload) =>
    fetchJson<SupplierCreditNoteResponse>("/api/v1/purchases/returns/credit-notes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelSupplierCreditNote: (id: number, reason: string) =>
    fetchJson<SupplierCreditNoteResponse>(`/api/v1/purchases/returns/credit-notes/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
