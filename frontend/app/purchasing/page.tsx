"use client";

import { MedicinePicker, MedicineUnitOption, loadPickedMedicine } from "@/components/medicine-picker";
import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  ShoppingCart,
  Truck,
  Package,
  FileText,
  Boxes,
  RotateCcw,
  Plus,
  Search,
  Filter,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Ban,
  DollarSign,
  Eye,
  AlertCircle,
  X,
  Calendar,
  Layers,
  ArrowRight,
  ShieldAlert,
  Percent,
  Check,
  Building2,
  Hash,
  Sparkles,
  ClipboardCheck,
  RefreshCw,
  ExternalLink,
  ChevronRight,
  Tag,
  Warehouse,
  FileCheck,
  Trash2,
  Receipt,
  FileSpreadsheet,
  ArrowDownRight,
  ArrowUpRight,
  Copy,
} from "lucide-react";
import {
  api,
  CurrentUser,
  SupplierItem,
  MedicineItem,
  UnitItem,
  TaxRateItem,
  MedicineBatchItem,
  PurchaseOrderResponse,
  GoodsReceiptResponse,
  SupplierInvoiceResponse,
  SupplierReturnResponse,
  SupplierCreditNoteResponse,
  DirectPurchaseItemPayload,
  DirectPurchaseCreatePayload,
  SupplierReturnCreatePayload,
  SupplierCreditNoteCreatePayload,
  isInvoicePosted,
} from "@/lib/api";
import { Navbar } from "@/components/Navbar";

type PurchasingTab = "all_purchases" | "drafts" | "posted" | "returns" | "orders" | "suppliers";
type PurchaseFilterStatus = "ALL" | "DRAFT" | "POSTED" | "CANCELLED";

interface DirectPurchaseLineItem {
  id: string; // client temporary key
  medicine_id: string;
  medicine_name: string;
  generic_name?: string;
  manufacturer_name?: string;
  batch_number: string;
  manufacturing_date: string;
  expiry_date: string;
  packaging_unit_id: string;
  packaging_unit_name?: string;
  purchase_qty_pack: string;
  free_qty_pack: string;
  purchase_rate: string;
  discount_percent: string;
  tax_rate_id: string;
  mrp: string;
  selling_rate: string;
  rack_location: string;
  unit_options?: MedicineUnitOption[];
}

export default function PurchasingPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Active top-level tab
  const [activeTab, setActiveTab] = useState<PurchasingTab>("all_purchases");
  const [statusFilter, setStatusFilter] = useState<PurchaseFilterStatus>("ALL");

  // Master Data
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [taxRates, setTaxRates] = useState<TaxRateItem[]>([]);

  // Records
  const [invoices, setInvoices] = useState<SupplierInvoiceResponse[]>([]);
  const [orders, setOrders] = useState<PurchaseOrderResponse[]>([]);
  const [receipts, setReceipts] = useState<GoodsReceiptResponse[]>([]);
  const [returns, setReturns] = useState<SupplierReturnResponse[]>([]);
  const [creditNotes, setCreditNotes] = useState<SupplierCreditNoteResponse[]>([]);

  // Filter States
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");

  // Modals & Drawers
  const [isDirectPurchaseModalOpen, setIsDirectPurchaseModalOpen] = useState(false);
  const [isDetailDrawerOpen, setIsDetailDrawerOpen] = useState(false);
  const [selectedInvoice, setSelectedInvoice] = useState<SupplierInvoiceResponse | null>(null);

  // Cancel Modal
  const [isCancelModalOpen, setIsCancelModalOpen] = useState(false);
  const [cancelTargetInvoice, setCancelTargetInvoice] = useState<SupplierInvoiceResponse | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelError, setCancelError] = useState<string | null>(null);

  // Returns / Credit Notes Modals
  const [isReturnModalOpen, setIsReturnModalOpen] = useState(false);
  const [isCreditNoteModalOpen, setIsCreditNoteModalOpen] = useState(false);
  const [returnsSubTab, setReturnsSubTab] = useState<"physical" | "financial">("physical");

  // Advance PO Modal
  const [isPoModalOpen, setIsPoModalOpen] = useState(false);

  // Form Submitting & Error
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  // ── Direct Purchase Entry Form State ──────────────────────────────────────
  const [directPurchaseForm, setDirectPurchaseForm] = useState<{
    draft_id?: number;
    supplier_id: string;
    invoice_number: string;
    invoice_date: string;
    due_date: string;
    payment_terms_days: number;
    is_tax_inclusive: boolean;
    other_charges: string;
    round_off: string;
    notes: string;
    po_id?: number;
    items: DirectPurchaseLineItem[];
  }>({
    supplier_id: "",
    invoice_number: "",
    invoice_date: new Date().toISOString().slice(0, 10),
    due_date: "",
    payment_terms_days: 30,
    is_tax_inclusive: false,
    other_charges: "0.00",
    round_off: "0.00",
    notes: "",
    items: [],
  });

  // ── Advance PO Form State ──────────────────────────────────────────────────
  const [poForm, setPoForm] = useState<{
    supplier_id: string;
    order_date: string;
    expected_delivery_date: string;
    notes: string;
    items: {
      medicine_id: string;
      packaging_unit_id: string;
      ordered_qty_pack: string;
      purchase_rate: string;
      discount_percent: string;
      tax_rate_id: string;
      notes: string;
    }[];
  }>({
    supplier_id: "",
    order_date: new Date().toISOString().slice(0, 10),
    expected_delivery_date: "",
    notes: "",
    items: [],
  });

  // ── Return Form State (Debit Note) ─────────────────────────────────────────
  const [retForm, setRetForm] = useState<{
    supplier_id: string;
    invoice_id: string;
    grn_id: string;
    return_date: string;
    reason_code: string;
    debit_note_reference: string;
    notes: string;
    items: {
      medicine_id: string;
      batch_id: string;
      packaging_unit_id: string;
      return_qty_pack: string;
      refund_rate: string;
      tax_rate_id: string;
      reason: string;
    }[];
  }>({
    supplier_id: "",
    invoice_id: "",
    grn_id: "",
    return_date: new Date().toISOString().slice(0, 10),
    reason_code: "DAMAGED",
    debit_note_reference: "",
    notes: "",
    items: [],
  });

  // ── Financial Credit Note Form State ───────────────────────────────────────
  const [cnForm, setCnForm] = useState<{
    supplier_id: string;
    invoice_id: string;
    credit_note_number: string;
    credit_note_date: string;
    credit_amount: string;
    reason: string;
    notes: string;
  }>({
    supplier_id: "",
    invoice_id: "",
    credit_note_number: "",
    credit_note_date: new Date().toISOString().slice(0, 10),
    credit_amount: "",
    reason: "RATE_DIFFERENCE",
    notes: "",
  });

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    let currentUser: CurrentUser;
    try {
      currentUser = await api.getCurrentUser();
      setUser(currentUser);
    } catch (err: any) {
      if (err?.status === 401 || err?.statusCode === 401) {
        router.push("/login");
        return;
      }
      setPageError(err?.detail || err?.message || "Failed to authenticate session.");
      setLoading(false);
      return;
    }

    const perms = currentUser.permissions || [];
    if (!perms.includes("purchases.view")) {
      if (perms.includes("pos.access")) {
        router.replace("/pos");
      } else if (perms.includes("stock.view")) {
        router.replace("/inventory");
      } else {
        router.replace("/medicines");
      }
      return;
    }

    try {
      const [sups, unts, taxes, bills, pos, grns, rets, credNotes] = await Promise.all([
        api.getSuppliers(),
        api.getUnits(),
        api.getTaxRates(),
        api.getSupplierInvoices(),
        api.getPurchaseOrders(),
        api.getGoodsReceipts(),
        api.getSupplierReturns().catch(() => [] as SupplierReturnResponse[]),
        api.getSupplierCreditNotes().catch(() => [] as SupplierCreditNoteResponse[]),
      ]);

      setSuppliers(sups);
      setUnits(unts);
      setTaxRates(taxes);
      setInvoices(bills);
      setOrders(pos);
      setReceipts(grns);
      setReturns(rets);
      setCreditNotes(credNotes);
      setPageError(null);

      // Refresh drawer if open
      if (selectedInvoice) {
        const fresh = bills.find((b) => b.id === selectedInvoice.id);
        if (fresh) setSelectedInvoice(fresh);
      }
    } catch (err: any) {
      if (err?.status === 401 || err?.statusCode === 401) {
        router.push("/login");
        return;
      }
      console.error("Failed to load purchasing data:", err);
      setPageError(err?.detail || err?.message || "Failed to load purchasing data.");
    } finally {
      setLoading(false);
    }
  };

  // Helper: selected supplier for direct purchase
  const selectedDirectSupplier = useMemo(() => {
    const id = parseInt(directPurchaseForm.supplier_id);
    return suppliers.find((s) => s.id === id) || null;
  }, [directPurchaseForm.supplier_id, suppliers]);

  // Update due date when supplier or invoice date changes
  useEffect(() => {
    if (selectedDirectSupplier && directPurchaseForm.invoice_date) {
      const days = selectedDirectSupplier.payment_terms_days ?? 30;
      const base = new Date(directPurchaseForm.invoice_date);
      if (!isNaN(base.getTime())) {
        base.setDate(base.getDate() + days);
        setDirectPurchaseForm((prev) => ({
          ...prev,
          payment_terms_days: days,
          due_date: base.toISOString().slice(0, 10),
        }));
      }
    }
  }, [selectedDirectSupplier, directPurchaseForm.invoice_date]);

  // ── Computed Stats ──────────────────────────────────────────────────────────
  const kpis = useMemo(() => {
    const totalPurchases = invoices.length;
    const draftsCount = invoices.filter((i) => i.status === "DRAFT").length;
    const postedCount = invoices.filter((i) => isInvoicePosted(i.status)).length;
    const cancelledCount = invoices.filter((i) => i.status === "CANCELLED").length;

    const totalPostedValue = invoices
      .filter((i) => isInvoicePosted(i.status))
      .reduce((sum, i) => sum + Number(i.calculated_grand_total || i.supplier_declared_total || 0), 0);

    const totalPayableOutstanding = invoices
      .filter((i) => isInvoicePosted(i.status))
      .reduce((sum, i) => sum + Number(i.outstanding_amount || 0), 0);

    const totalAdvanceOrders = orders.filter((o) => o.status !== "CANCELLED" && o.status !== "RECEIVED").length;
    const totalReturnsCount = returns.length + creditNotes.length;

    return {
      totalPurchases,
      draftsCount,
      postedCount,
      cancelledCount,
      totalPostedValue,
      totalPayableOutstanding,
      totalAdvanceOrders,
      totalReturnsCount,
    };
  }, [invoices, orders, returns, creditNotes]);

  // ── Filtered Invoices ───────────────────────────────────────────────────────
  const filteredInvoices = useMemo(() => {
    return invoices.filter((inv) => {
      // Tab filter
      if (activeTab === "drafts" && inv.status !== "DRAFT") return false;
      if (activeTab === "posted" && !isInvoicePosted(inv.status)) return false;

      // Status chip filter
      if (statusFilter === "DRAFT" && inv.status !== "DRAFT") return false;
      if (statusFilter === "POSTED" && !isInvoicePosted(inv.status)) return false;
      if (statusFilter === "CANCELLED" && inv.status !== "CANCELLED") return false;

      // Supplier filter
      if (selectedSupplierId !== "all" && String(inv.supplier_id) !== selectedSupplierId) return false;

      // Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const num = (inv.invoice_number || "").toLowerCase();
        const sup = (inv.supplier_name || "").toLowerCase();
        const grn = (inv.grn_number || "").toLowerCase();
        return num.includes(q) || sup.includes(q) || grn.includes(q);
      }

      return true;
    });
  }, [invoices, activeTab, statusFilter, selectedSupplierId, searchQuery]);

  // ── Direct Purchase Calculations ───────────────────────────────────────────
  const directPurchaseCalculations = useMemo(() => {
    let subtotal = 0;
    let totalDiscount = 0;
    let taxableAmount = 0;
    let cgstTotal = 0;
    let sgstTotal = 0;
    let igstTotal = 0;
    let taxTotal = 0;

    const isTaxInclusive = directPurchaseForm.is_tax_inclusive;
    const supState = selectedDirectSupplier?.state_code || "";
    // Assume pharmacy state is 27 (or match state)
    const isInterstate = supState && supState !== "27";

    const lineCalculations = directPurchaseForm.items.map((it) => {
      const qty = Number(it.purchase_qty_pack || 0);
      const rate = Number(it.purchase_rate || 0);
      const discPercent = Number(it.discount_percent || 0);
      const gross = qty * rate;
      const discAmt = gross * (discPercent / 100);
      const net = gross - discAmt;

      const taxObj = taxRates.find((t) => String(t.id) === it.tax_rate_id);
      const taxRatePct = taxObj ? Number(taxObj.rate_percentage) : 0;

      let lineTaxable = 0;
      let lineTax = 0;
      let lineCgst = 0;
      let lineSgst = 0;
      let lineIgst = 0;
      let lineTotal = 0;

      if (isTaxInclusive) {
        lineTotal = net;
        lineTaxable = net / (1 + taxRatePct / 100);
        lineTax = lineTotal - lineTaxable;
      } else {
        lineTaxable = net;
        lineTax = lineTaxable * (taxRatePct / 100);
        lineTotal = lineTaxable + lineTax;
      }

      if (isInterstate) {
        lineIgst = lineTax;
      } else {
        lineCgst = lineTax / 2;
        lineSgst = lineTax / 2;
      }

      subtotal += gross;
      totalDiscount += discAmt;
      taxableAmount += lineTaxable;
      cgstTotal += lineCgst;
      sgstTotal += lineSgst;
      igstTotal += lineIgst;
      taxTotal += lineTax;

      return {
        gross,
        discAmt,
        lineTaxable,
        lineTax,
        lineTotal,
      };
    });

    const otherCharges = Number(directPurchaseForm.other_charges || 0);
    const roundOff = Number(directPurchaseForm.round_off || 0);
    const grandTotal = Math.max(0, taxableAmount + taxTotal + otherCharges + roundOff);

    return {
      subtotal,
      totalDiscount,
      taxableAmount,
      cgstTotal,
      sgstTotal,
      igstTotal,
      taxTotal,
      otherCharges,
      roundOff,
      grandTotal,
      lineCalculations,
    };
  }, [directPurchaseForm, selectedDirectSupplier, taxRates]);

  // ── Direct Purchase Handlers ───────────────────────────────────────────────
  const handleOpenNewPurchase = () => {
    const defaultSupplier = suppliers.length > 0 ? String(suppliers[0].id) : "";
    const defaultUnit = units.length > 0 ? String(units[0].id) : "";
    const defaultTax = taxRates.length > 0 ? String(taxRates[0].id) : "";

    setDirectPurchaseForm({
      supplier_id: defaultSupplier,
      invoice_number: `BILL-${Date.now().toString().slice(-6)}`,
      invoice_date: new Date().toISOString().slice(0, 10),
      due_date: "",
      payment_terms_days: 30,
      is_tax_inclusive: false,
      other_charges: "0.00",
      round_off: "0.00",
      notes: "",
      items: [
        {
          id: `line-${Date.now()}-1`,
          medicine_id: "",
          medicine_name: "",
          batch_number: `B-${Date.now().toString().slice(-4)}`,
          manufacturing_date: "",
          expiry_date: new Date(Date.now() + 730 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
          packaging_unit_id: defaultUnit,
          purchase_qty_pack: "10",
          free_qty_pack: "0",
          purchase_rate: "100.00",
          discount_percent: "0",
          tax_rate_id: defaultTax,
          mrp: "140.00",
          selling_rate: "130.00",
          rack_location: "RACK-A1",
        },
      ],
    });
    setFormError(null);
    setIsDirectPurchaseModalOpen(true);
  };

  const handleAddMedicineLine = () => {
    const defaultUnit = units.length > 0 ? String(units[0].id) : "";
    const defaultTax = taxRates.length > 0 ? String(taxRates[0].id) : "";
    setDirectPurchaseForm((prev) => ({
      ...prev,
      items: [
        ...prev.items,
        {
          id: `line-${Date.now()}-${prev.items.length + 1}`,
          medicine_id: "",
          medicine_name: "",
          batch_number: "",
          manufacturing_date: "",
          expiry_date: new Date(Date.now() + 730 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
          packaging_unit_id: defaultUnit,
          purchase_qty_pack: "10",
          free_qty_pack: "0",
          purchase_rate: "50.00",
          discount_percent: "0",
          tax_rate_id: defaultTax,
          mrp: "70.00",
          selling_rate: "65.00",
          rack_location: "RACK-A1",
        },
      ],
    }));
  };

  const handleDuplicateLineForNewBatch = (index: number) => {
    const src = directPurchaseForm.items[index];
    setDirectPurchaseForm((prev) => {
      const nextItems = [...prev.items];
      nextItems.splice(index + 1, 0, {
        ...src,
        id: `line-${Date.now()}-${Math.random().toString().slice(-4)}`,
        batch_number: "",
        expiry_date: new Date(Date.now() + 730 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
        purchase_qty_pack: "10",
        free_qty_pack: "0",
      });
      return { ...prev, items: nextItems };
    });
  };

  const handleRemoveLine = (index: number) => {
    if (directPurchaseForm.items.length <= 1) {
      alert("A purchase invoice must contain at least one medicine line.");
      return;
    }
    setDirectPurchaseForm((prev) => ({
      ...prev,
      items: prev.items.filter((_, i) => i !== index),
    }));
  };

  const handleSavePurchase = async (postNow: boolean) => {
    setFormError(null);
    if (!directPurchaseForm.supplier_id) {
      setFormError("Please select a supplier.");
      return;
    }
    if (!directPurchaseForm.invoice_number.trim()) {
      setFormError("Supplier Invoice Number is required.");
      return;
    }
    if (directPurchaseForm.items.length === 0) {
      setFormError("Please add at least one medicine line.");
      return;
    }

    for (let i = 0; i < directPurchaseForm.items.length; i++) {
      const it = directPurchaseForm.items[i];
      if (!it.medicine_id) {
        setFormError(`Line #${i + 1}: Please select a medicine.`);
        return;
      }
      if (!it.batch_number.trim()) {
        setFormError(`Line #${i + 1}: Batch number is required.`);
        return;
      }
      if (!it.expiry_date) {
        setFormError(`Line #${i + 1}: Expiry date is required.`);
        return;
      }
      if (Number(it.purchase_qty_pack) <= 0) {
        setFormError(`Line #${i + 1}: Purchase quantity must be greater than zero.`);
        return;
      }
    }

    setSubmitting(true);
    try {
      const payload: DirectPurchaseCreatePayload = {
        supplier_id: parseInt(directPurchaseForm.supplier_id),
        invoice_number: directPurchaseForm.invoice_number.trim(),
        invoice_date: directPurchaseForm.invoice_date,
        due_date: directPurchaseForm.due_date || undefined,
        payment_terms_days: directPurchaseForm.payment_terms_days,
        is_tax_inclusive: directPurchaseForm.is_tax_inclusive,
        other_charges: directPurchaseForm.other_charges || 0,
        round_off: directPurchaseForm.round_off || 0,
        supplier_declared_total: directPurchaseCalculations.grandTotal.toFixed(2),
        notes: directPurchaseForm.notes || undefined,
        po_id: directPurchaseForm.po_id,
        post_now: postNow,
        items: directPurchaseForm.items.map((it) => ({
          medicine_id: parseInt(it.medicine_id),
          packaging_unit_id: parseInt(it.packaging_unit_id),
          quantity: it.purchase_qty_pack,
          free_quantity: it.free_qty_pack || 0,
          purchase_rate: it.purchase_rate,
          discount_percent: it.discount_percent || 0,
          tax_rate_id: it.tax_rate_id ? parseInt(it.tax_rate_id) : undefined,
          batch_number: it.batch_number.trim().toUpperCase(),
          manufacturing_date: it.manufacturing_date || undefined,
          expiry_date: it.expiry_date,
          mrp: it.mrp || (Number(it.purchase_rate) * 1.3).toFixed(2),
          selling_rate: it.selling_rate || (Number(it.purchase_rate) * 1.25).toFixed(2),
          rack_location: it.rack_location || undefined,
        })),
      };

      const result = await api.createOrPostDirectPurchase(payload);
      setIsDirectPurchaseModalOpen(false);
      await loadAll();
      setSelectedInvoice(result);
      setIsDetailDrawerOpen(true);
    } catch (err: any) {
      setFormError(err.detail || err.message || "Failed to save purchase entry.");
    } finally {
      setSubmitting(false);
    }
  };

  // Convert advance PO to Direct Purchase
  const handleConvertPoToDirectPurchase = async (po: PurchaseOrderResponse) => {
    try {
      setSubmitting(true);
      const prefill = await api.getDirectPurchasePrefillFromPo(po.id);

      const items: DirectPurchaseLineItem[] = prefill.items.map((it, idx) => ({
        id: `po-line-${idx}`,
        medicine_id: String(it.medicine_id),
        medicine_name: it.medicine_name || `Medicine #${it.medicine_id}`,
        batch_number: `B-${Date.now().toString().slice(-4)}`,
        manufacturing_date: "",
        expiry_date: new Date(Date.now() + 730 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
        packaging_unit_id: String(it.packaging_unit_id),
        packaging_unit_name: it.packaging_unit_name,
        purchase_qty_pack: String(it.purchase_qty_pack),
        free_qty_pack: "0",
        purchase_rate: String(it.purchase_rate),
        discount_percent: String(it.discount_percent || 0),
        tax_rate_id: it.tax_rate_id ? String(it.tax_rate_id) : "",
        mrp: String((Number(it.purchase_rate) * 1.35).toFixed(2)),
        selling_rate: String((Number(it.purchase_rate) * 1.25).toFixed(2)),
        rack_location: "RACK-A1",
      }));

      setDirectPurchaseForm({
        supplier_id: String(prefill.supplier_id),
        invoice_number: `BILL-${Date.now().toString().slice(-6)}`,
        invoice_date: new Date().toISOString().slice(0, 10),
        due_date: "",
        payment_terms_days: 30,
        is_tax_inclusive: false,
        other_charges: "0.00",
        round_off: "0.00",
        notes: `Converted from advance Purchase Order ${prefill.po_number}. ${prefill.notes || ""}`.trim(),
        po_id: prefill.po_id,
        items,
      });

      setFormError(null);
      setIsDirectPurchaseModalOpen(true);
    } catch (err: any) {
      alert(err.detail || err.message || "Failed to load Purchase Order details for conversion.");
    } finally {
      setSubmitting(false);
    }
  };

  // Post existing draft purchase
  const handlePostDraft = async (invoice: SupplierInvoiceResponse) => {
    if (!confirm(`Are you sure you want to post purchase ${invoice.invoice_number}? This will add stock to inventory and record supplier payable liability.`)) {
      return;
    }
    setSubmitting(true);
    try {
      const updated = await api.postDraftPurchase(invoice.id);
      await loadAll();
      setSelectedInvoice(updated);
    } catch (err: any) {
      alert(err.detail || err.message || "Failed to post draft purchase.");
    } finally {
      setSubmitting(false);
    }
  };

  // Delete draft purchase
  const handleDeleteDraft = async (invoice: SupplierInvoiceResponse) => {
    if (!confirm(`Permanently delete draft purchase ${invoice.invoice_number}? This action cannot be undone.`)) {
      return;
    }
    setSubmitting(true);
    try {
      await api.deleteDraftPurchase(invoice.id);
      setIsDetailDrawerOpen(false);
      setSelectedInvoice(null);
      await loadAll();
    } catch (err: any) {
      alert(err.detail || err.message || "Failed to delete draft purchase.");
    } finally {
      setSubmitting(false);
    }
  };

  // Cancel posted purchase (Controlled reversal)
  const handleOpenCancelModal = (invoice: SupplierInvoiceResponse) => {
    setCancelTargetInvoice(invoice);
    setCancelReason("");
    setCancelError(null);
    setIsCancelModalOpen(true);
  };

  const handleConfirmCancelPurchase = async () => {
    if (!cancelTargetInvoice) return;
    if (!cancelReason.trim()) {
      setCancelError("Please enter a cancellation reason.");
      return;
    }
    setSubmitting(true);
    setCancelError(null);
    try {
      const cancelled = await api.cancelPostedPurchase(cancelTargetInvoice.id, cancelReason.trim());
      setIsCancelModalOpen(false);
      setCancelTargetInvoice(null);
      await loadAll();
      setSelectedInvoice(cancelled);
    } catch (err: any) {
      setCancelError(err.detail || err.message || "Failed to cancel purchase.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Open Return (Debit Note) Modal ─────────────────────────────────────────
  const handleOpenReturnModal = (invoice: SupplierInvoiceResponse) => {
    const items = (invoice.items || []).map((it) => ({
      medicine_id: String(it.medicine_id),
      batch_id: String(it.batch_id || ""),
      packaging_unit_id: String(it.packaging_unit_id),
      return_qty_pack: "1",
      refund_rate: String(it.purchase_rate),
      tax_rate_id: it.tax_rate_id ? String(it.tax_rate_id) : "",
      reason: "Damaged / Broken packaging",
    }));

    setRetForm({
      supplier_id: String(invoice.supplier_id),
      invoice_id: String(invoice.id),
      grn_id: invoice.grn_id ? String(invoice.grn_id) : "",
      return_date: new Date().toISOString().slice(0, 10),
      reason_code: "DAMAGED",
      debit_note_reference: `DN-${Date.now().toString().slice(-6)}`,
      notes: `Return against invoice ${invoice.invoice_number}`,
      items,
    });

    setFormError(null);
    setIsReturnModalOpen(true);
  };

  const handleSubmitReturn = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const payload: SupplierReturnCreatePayload = {
        supplier_id: parseInt(retForm.supplier_id),
        grn_id: retForm.grn_id ? parseInt(retForm.grn_id) : undefined,
        return_date: retForm.return_date,
        reason_code: retForm.reason_code,
        debit_note_reference: retForm.debit_note_reference || undefined,
        notes: retForm.notes || undefined,
        items: retForm.items.map((it) => ({
          medicine_id: parseInt(it.medicine_id),
          batch_id: parseInt(it.batch_id),
          packaging_unit_id: parseInt(it.packaging_unit_id),
          return_qty_pack: it.return_qty_pack,
          refund_rate: it.refund_rate,
          tax_rate_id: it.tax_rate_id ? parseInt(it.tax_rate_id) : undefined,
          reason: it.reason || undefined,
        })),
      };
      await api.createSupplierReturn(payload);
      setIsReturnModalOpen(false);
      await loadAll();
    } catch (err: any) {
      setFormError(err.detail || err.message || "Failed to record supplier return.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Open Financial Credit Note Modal ───────────────────────────────────────
  const handleOpenCreditNoteModal = (invoice: SupplierInvoiceResponse) => {
    setCnForm({
      supplier_id: String(invoice.supplier_id),
      invoice_id: String(invoice.id),
      credit_note_number: `CN-${Date.now().toString().slice(-6)}`,
      credit_note_date: new Date().toISOString().slice(0, 10),
      credit_amount: "",
      reason: "RATE_DIFFERENCE",
      notes: `Credit note for rate adjustment on bill ${invoice.invoice_number}`,
    });
    setFormError(null);
    setIsCreditNoteModalOpen(true);
  };

  const handleSubmitCreditNote = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!cnForm.credit_amount || Number(cnForm.credit_amount) <= 0) {
      setFormError("Credit amount must be greater than zero.");
      return;
    }
    setSubmitting(true);
    try {
      const payload: SupplierCreditNoteCreatePayload = {
        supplier_id: parseInt(cnForm.supplier_id),
        credit_note_number: cnForm.credit_note_number.trim(),
        note_date: cnForm.credit_note_date,
        invoice_id: cnForm.invoice_id ? parseInt(cnForm.invoice_id) : undefined,
        reason_code: cnForm.reason,
        taxable_amount: cnForm.credit_amount,
        total_amount: cnForm.credit_amount,
        notes: cnForm.notes || undefined,
      };
      await api.createSupplierCreditNote(payload);
      setIsCreditNoteModalOpen(false);
      await loadAll();
    } catch (err: any) {
      setFormError(err.detail || err.message || "Failed to issue supplier credit note.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Advance PO Creation Handler ───────────────────────────────────────────
  const handleSaveAdvancePo = async () => {
    setFormError(null);
    if (!poForm.supplier_id) {
      setFormError("Please select a supplier.");
      return;
    }
    if (poForm.items.length === 0) {
      setFormError("Please add at least one medicine to the purchase order.");
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        supplier_id: parseInt(poForm.supplier_id),
        order_date: poForm.order_date,
        expected_delivery_date: poForm.expected_delivery_date || undefined,
        notes: poForm.notes || undefined,
        items: poForm.items.map((it) => ({
          medicine_id: parseInt(it.medicine_id),
          packaging_unit_id: parseInt(it.packaging_unit_id),
          ordered_qty_pack: it.ordered_qty_pack,
          purchase_rate: it.purchase_rate,
          discount_percent: it.discount_percent || 0,
          tax_rate_id: it.tax_rate_id ? parseInt(it.tax_rate_id) : undefined,
          notes: it.notes || undefined,
        })),
      };
      await api.createPurchaseOrder(payload);
      setIsPoModalOpen(false);
      await loadAll();
    } catch (err: any) {
      setFormError(err.detail || err.message || "Failed to create advance purchase order.");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col">
        <Navbar user={user} />
        <div className="flex-1 flex items-center justify-center min-h-[60vh]">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-slate-400">Loading Purchasing Workspace…</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 p-4 sm:p-6 max-w-7xl mx-auto w-full space-y-6">
      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800/80 pb-5">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="p-2.5 bg-emerald-500/10 rounded-xl border border-emerald-500/20 text-emerald-400">
              <Receipt className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                Purchasing & Supplier Invoices
                <span className="text-xs px-2.5 py-0.5 font-medium rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  Direct Entry System
                </span>
              </h1>
              <p className="text-xs text-slate-400 mt-0.5">
                Primary pharmacy purchase workflow: Enter bill once → Save & Post → Instant stock & liability update
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/purchasing/bill-import")}
            className="flex items-center gap-2 px-3.5 py-2 text-xs font-medium rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:bg-slate-800 transition"
          >
            <FileSpreadsheet className="w-4 h-4 text-slate-400" />
            Import Supplier Bill
          </button>
          <button
            id="btn-new-purchase"
            onClick={handleOpenNewPurchase}
            className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-950/40 transition active:scale-95"
          >
            <Plus className="w-4 h-4" />
            New Purchase / Enter Supplier Invoice
          </button>
        </div>
      </div>

      {/* ── Error Banner ──────────────────────────────────────────────────── */}
      {pageError && (
        <div className="flex items-center justify-between p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-sm">
          <div className="flex items-center gap-2.5">
            <AlertCircle className="w-5 h-5 shrink-0 text-rose-400" />
            <span>{pageError}</span>
          </div>
          <button
            onClick={loadAll}
            className="px-3 py-1 bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 text-xs font-medium rounded-lg transition"
          >
            Retry
          </button>
        </div>
      )}

      {/* ── KPI Stat Cards ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <div
          onClick={() => {
            setActiveTab("all_purchases");
            setStatusFilter("ALL");
          }}
          className={`p-3.5 rounded-xl border cursor-pointer transition ${
            activeTab === "all_purchases" && statusFilter === "ALL"
              ? "bg-slate-900 border-emerald-500/50 shadow-md"
              : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] text-slate-400 font-medium">All Purchases</div>
          <div className="text-xl font-bold text-white mt-1">{kpis.totalPurchases}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Bills & invoices entered</div>
        </div>

        <div
          onClick={() => {
            setActiveTab("drafts");
            setStatusFilter("DRAFT");
          }}
          className={`p-3.5 rounded-xl border cursor-pointer transition ${
            activeTab === "drafts" || statusFilter === "DRAFT"
              ? "bg-slate-900 border-amber-500/50 shadow-md"
              : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] text-amber-400 font-medium flex items-center gap-1.5">
            <Clock className="w-3 h-3" /> Draft Purchases
          </div>
          <div className="text-xl font-bold text-white mt-1">{kpis.draftsCount}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Not yet posted to stock</div>
        </div>

        <div
          onClick={() => {
            setActiveTab("posted");
            setStatusFilter("POSTED");
          }}
          className={`p-3.5 rounded-xl border cursor-pointer transition ${
            activeTab === "posted" || statusFilter === "POSTED"
              ? "bg-slate-900 border-emerald-500/50 shadow-md"
              : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] text-emerald-400 font-medium flex items-center gap-1.5">
            <CheckCircle2 className="w-3 h-3" /> Posted Purchases
          </div>
          <div className="text-xl font-bold text-white mt-1">{kpis.postedCount}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">₹{kpis.totalPostedValue.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
        </div>

        <div
          onClick={() => {
            setActiveTab("all_purchases");
            setStatusFilter("POSTED");
          }}
          className="p-3.5 rounded-xl border bg-slate-900/60 border-slate-800 hover:border-slate-700 cursor-pointer transition"
        >
          <div className="text-[11px] text-rose-400 font-medium flex items-center gap-1.5">
            <DollarSign className="w-3 h-3" /> Outstanding Payable
          </div>
          <div className="text-xl font-bold text-white mt-1">₹{kpis.totalPayableOutstanding.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Unpaid supplier liability</div>
        </div>

        <div
          onClick={() => setActiveTab("returns")}
          className={`p-3.5 rounded-xl border cursor-pointer transition ${
            activeTab === "returns"
              ? "bg-slate-900 border-purple-500/50 shadow-md"
              : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] text-purple-400 font-medium flex items-center gap-1.5">
            <RotateCcw className="w-3 h-3" /> Returns & Credits
          </div>
          <div className="text-xl font-bold text-white mt-1">{kpis.totalReturnsCount}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Debit & credit notes</div>
        </div>

        <div
          onClick={() => setActiveTab("orders")}
          className={`p-3.5 rounded-xl border cursor-pointer transition ${
            activeTab === "orders"
              ? "bg-slate-900 border-blue-500/50 shadow-md"
              : "bg-slate-900/60 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] text-blue-400 font-medium flex items-center gap-1.5">
            <ShoppingCart className="w-3 h-3" /> Advance POs
          </div>
          <div className="text-xl font-bold text-white mt-1">{kpis.totalAdvanceOrders}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Optional advance orders</div>
        </div>
      </div>

      {/* ── Main Navigation Tabs ───────────────────────────────────────────── */}
      <div className="flex items-center gap-1 border-b border-slate-800/80">
        <button
          onClick={() => {
            setActiveTab("all_purchases");
            setStatusFilter("ALL");
          }}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "all_purchases"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Receipt className="w-4 h-4" />
          All Purchases ({invoices.length})
        </button>
        <button
          onClick={() => {
            setActiveTab("drafts");
            setStatusFilter("DRAFT");
          }}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "drafts"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Clock className="w-4 h-4" />
          Draft Purchases ({kpis.draftsCount})
        </button>
        <button
          onClick={() => {
            setActiveTab("posted");
            setStatusFilter("POSTED");
          }}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "posted"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <CheckCircle2 className="w-4 h-4" />
          Posted Purchases ({kpis.postedCount})
        </button>
        <button
          onClick={() => setActiveTab("returns")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "returns"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <RotateCcw className="w-4 h-4" />
          Purchase Returns & Credit Notes ({returns.length + creditNotes.length})
        </button>
        <button
          onClick={() => setActiveTab("orders")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "orders"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <ShoppingCart className="w-4 h-4" />
          Optional Purchase Orders ({orders.length})
        </button>
        <button
          onClick={() => setActiveTab("suppliers")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition ${
            activeTab === "suppliers"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Building2 className="w-4 h-4" />
          Supplier Directory ({suppliers.length})
        </button>
      </div>

      {/* ── View 1 & 2 & 3: Purchases List ─────────────────────────────────── */}
      {(activeTab === "all_purchases" || activeTab === "drafts" || activeTab === "posted") && (
        <div className="space-y-4">
          {/* Controls: Search, Supplier Filter & Status Chips */}
          <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3 bg-slate-900/60 p-3 rounded-xl border border-slate-800/80">
            <div className="flex items-center gap-3 flex-1">
              <div className="relative flex-1 max-w-sm">
                <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search by Invoice #, Supplier, or GRN…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-emerald-500/60"
                />
              </div>

              <select
                value={selectedSupplierId}
                onChange={(e) => setSelectedSupplierId(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-emerald-500/60"
              >
                <option value="all">All Suppliers</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            {activeTab === "all_purchases" && (
              <div className="flex items-center gap-1.5 overflow-x-auto text-xs">
                <span className="text-[11px] text-slate-500 font-medium mr-1">Status:</span>
                {[
                  { id: "ALL", label: "All" },
                  { id: "DRAFT", label: "Draft" },
                  { id: "POSTED", label: "Posted (In Stock)" },
                  { id: "CANCELLED", label: "Cancelled" },
                ].map((chip) => (
                  <button
                    key={chip.id}
                    onClick={() => setStatusFilter(chip.id as PurchaseFilterStatus)}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition ${
                      statusFilter === chip.id
                        ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                        : "bg-slate-950 text-slate-400 hover:text-slate-200 border border-slate-800"
                    }`}
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Purchases Table */}
          <div className="bg-slate-900/60 rounded-xl border border-slate-800/80 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="py-3 px-4">Invoice #</th>
                    <th className="py-3 px-4">Supplier</th>
                    <th className="py-3 px-4">Bill Date</th>
                    <th className="py-3 px-4">Due Date</th>
                    <th className="py-3 px-4 text-center">Items</th>
                    <th className="py-3 px-4 text-right">Total Amount</th>
                    <th className="py-3 px-4 text-right">Outstanding</th>
                    <th className="py-3 px-4 text-center">Stock Status</th>
                    <th className="py-3 px-4 text-center">Payment</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-200">
                  {filteredInvoices.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="py-12 text-center text-slate-500">
                        <div className="flex flex-col items-center gap-2">
                          <Receipt className="w-8 h-8 text-slate-600 stroke-1" />
                          <p>No purchase records found matching your filters.</p>
                          <button
                            onClick={handleOpenNewPurchase}
                            className="mt-2 text-xs text-emerald-400 hover:text-emerald-300 font-medium"
                          >
                            + Enter New Supplier Purchase
                          </button>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    filteredInvoices.map((inv) => {
                      const grandTotal = Number(inv.calculated_grand_total || inv.supplier_declared_total || 0);
                      const paidAmt = Number(inv.paid_amount || 0);
                      const outstanding = Number(inv.outstanding_amount ?? (grandTotal - paidAmt));
                      const isDraft = inv.status === "DRAFT";
                      const isPosted = isInvoicePosted(inv.status);
                      const isCancelled = inv.status === "CANCELLED";

                      return (
                        <tr
                          key={inv.id}
                          className="hover:bg-slate-800/40 transition cursor-pointer group"
                          onClick={() => {
                            setSelectedInvoice(inv);
                            setIsDetailDrawerOpen(true);
                          }}
                        >
                          <td className="py-3 px-4 font-medium text-white group-hover:text-emerald-400 transition">
                            <div className="flex items-center gap-1.5">
                              <span>{inv.invoice_number}</span>
                              {inv.po_number && (
                                <span className="text-[10px] text-slate-500 font-normal">
                                  ({inv.po_number})
                                </span>
                              )}
                            </div>
                            {inv.grn_number && (
                              <div className="text-[10px] text-slate-500">GRN: {inv.grn_number}</div>
                            )}
                          </td>
                          <td className="py-3 px-4">
                            <div className="font-medium text-slate-200">{inv.supplier_name || `Supplier #${inv.supplier_id}`}</div>
                          </td>
                          <td className="py-3 px-4 text-slate-400">{inv.invoice_date}</td>
                          <td className="py-3 px-4 text-slate-400">{inv.due_date || "—"}</td>
                          <td className="py-3 px-4 text-center">
                            <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono">
                              {inv.items?.length || 0}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-right font-semibold text-white">
                            ₹{grandTotal.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                          </td>
                          <td className="py-3 px-4 text-right font-medium">
                            {isPosted ? (
                              <span className={outstanding > 0 ? "text-amber-400" : "text-emerald-400"}>
                                ₹{outstanding.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                              </span>
                            ) : (
                              <span className="text-slate-500">—</span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-center">
                            {isDraft && (
                              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/30">
                                Draft (No Stock)
                              </span>
                            )}
                            {isPosted && (
                              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1 justify-center w-fit mx-auto">
                                <Check className="w-3 h-3" /> In Stock
                              </span>
                            )}
                            {isCancelled && (
                              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/30">
                                Cancelled
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-center">
                            {isDraft ? (
                              <span className="text-slate-500 text-[11px]">Unposted</span>
                            ) : (
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                                  inv.payment_status === "PAID"
                                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                                    : inv.payment_status === "PARTIALLY_PAID"
                                    ? "bg-purple-500/10 text-purple-400 border border-purple-500/30"
                                    : "bg-slate-800 text-slate-400"
                                }`}
                              >
                                {inv.payment_status || "UNPAID"}
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                            <div className="flex items-center justify-end gap-1.5">
                              {isDraft && (
                                <>
                                  <button
                                    onClick={() => handlePostDraft(inv)}
                                    title="Post Purchase (Update Stock & Payable)"
                                    className="p-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 transition"
                                  >
                                    <Check className="w-3.5 h-3.5" />
                                  </button>
                                  <button
                                    onClick={() => handleDeleteDraft(inv)}
                                    title="Delete Draft"
                                    className="p-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" />
                                  </button>
                                </>
                              )}
                              {isPosted && (
                                <>
                                  <button
                                    onClick={() => handleOpenReturnModal(inv)}
                                    title="Return Medicines (Debit Note)"
                                    className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                                  >
                                    <RotateCcw className="w-3.5 h-3.5" />
                                  </button>
                                  <button
                                    onClick={() => handleOpenCancelModal(inv)}
                                    title="Cancel Purchase (Reversal)"
                                    className="p-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition"
                                  >
                                    <Ban className="w-3.5 h-3.5" />
                                  </button>
                                </>
                              )}
                              <button
                                onClick={() => {
                                  setSelectedInvoice(inv);
                                  setIsDetailDrawerOpen(true);
                                }}
                                title="View Details"
                                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── View 4: Purchase Returns & Credit Notes ────────────────────────── */}
      {activeTab === "returns" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setReturnsSubTab("physical")}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  returnsSubTab === "physical"
                    ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                Physical Returns / Debit Notes ({returns.length})
              </button>
              <button
                onClick={() => setReturnsSubTab("financial")}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  returnsSubTab === "financial"
                    ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                Financial Credit Notes ({creditNotes.length})
              </button>
            </div>
          </div>

          {returnsSubTab === "physical" ? (
            <div className="bg-slate-900/60 rounded-xl border border-slate-800/80 overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="py-3 px-4">Return #</th>
                    <th className="py-3 px-4">Debit Note Ref</th>
                    <th className="py-3 px-4">Supplier</th>
                    <th className="py-3 px-4">Return Date</th>
                    <th className="py-3 px-4">Reason</th>
                    <th className="py-3 px-4 text-right">Total Refund</th>
                    <th className="py-3 px-4 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-200">
                  {returns.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-slate-500">
                        No supplier returns recorded.
                      </td>
                    </tr>
                  ) : (
                    returns.map((ret) => (
                      <tr key={ret.id} className="hover:bg-slate-800/40">
                        <td className="py-3 px-4 font-medium text-white">{ret.return_number}</td>
                        <td className="py-3 px-4 text-slate-400">{ret.debit_note_reference || "—"}</td>
                        <td className="py-3 px-4">{ret.supplier_name || `Supplier #${ret.supplier_id}`}</td>
                        <td className="py-3 px-4 text-slate-400">{ret.return_date}</td>
                        <td className="py-3 px-4">
                          <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px]">
                            {ret.reason_code}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right font-semibold text-emerald-400">
                          ₹{Number(ret.total_return_amount).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                        </td>
                        <td className="py-3 px-4 text-center">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                              ret.status === "POSTED"
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                                : "bg-slate-800 text-slate-400"
                            }`}
                          >
                            {ret.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="bg-slate-900/60 rounded-xl border border-slate-800/80 overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="py-3 px-4">Credit Note #</th>
                    <th className="py-3 px-4">Supplier</th>
                    <th className="py-3 px-4">Invoice #</th>
                    <th className="py-3 px-4">Date</th>
                    <th className="py-3 px-4">Reason</th>
                    <th className="py-3 px-4 text-right">Credit Amount</th>
                    <th className="py-3 px-4 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-200">
                  {creditNotes.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-slate-500">
                        No financial credit notes recorded.
                      </td>
                    </tr>
                  ) : (
                    creditNotes.map((cn) => (
                      <tr key={cn.id} className="hover:bg-slate-800/40">
                        <td className="py-3 px-4 font-medium text-white">{cn.credit_note_number}</td>
                        <td className="py-3 px-4">{cn.supplier_name || `Supplier #${cn.supplier_id}`}</td>
                        <td className="py-3 px-4 text-slate-400">{cn.invoice_number || `Invoice #${cn.invoice_id}`}</td>
                        <td className="py-3 px-4 text-slate-400">{cn.note_date}</td>
                        <td className="py-3 px-4">
                          <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px]">
                            {cn.reason_code}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right font-semibold text-emerald-400">
                          ₹{Number(cn.total_amount).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                        </td>
                        <td className="py-3 px-4 text-center">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                              cn.status === "ACTIVE"
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                                : "bg-rose-500/10 text-rose-400 border border-rose-500/30"
                            }`}
                          >
                            {cn.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── View 5: Optional Purchase Orders ───────────────────────────────── */}
      {activeTab === "orders" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between bg-slate-900/60 p-3 rounded-xl border border-slate-800/80">
            <div>
              <h3 className="text-sm font-semibold text-white">Advance Purchase Orders</h3>
              <p className="text-xs text-slate-400">
                Optional ordering workflow for placing advance orders. Convert to direct purchase when goods arrive.
              </p>
            </div>
            <button
              onClick={() => {
                setPoForm({
                  supplier_id: suppliers.length > 0 ? String(suppliers[0].id) : "",
                  order_date: new Date().toISOString().slice(0, 10),
                  expected_delivery_date: "",
                  notes: "",
                  items: [
                    {
                      medicine_id: "",
                      packaging_unit_id: units.length > 0 ? String(units[0].id) : "",
                      ordered_qty_pack: "10",
                      purchase_rate: "50.00",
                      discount_percent: "0",
                      tax_rate_id: "",
                      notes: "",
                    },
                  ],
                });
                setIsPoModalOpen(true);
              }}
              className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              Create Advance PO
            </button>
          </div>

          <div className="bg-slate-900/60 rounded-xl border border-slate-800/80 overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                <tr>
                  <th className="py-3 px-4">PO Number</th>
                  <th className="py-3 px-4">Supplier</th>
                  <th className="py-3 px-4">Date</th>
                  <th className="py-3 px-4 text-center">Items</th>
                  <th className="py-3 px-4 text-right">Grand Total</th>
                  <th className="py-3 px-4 text-center">Status</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-200">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-slate-500">
                      No advance purchase orders found.
                    </td>
                  </tr>
                ) : (
                  orders.map((po) => (
                    <tr key={po.id} className="hover:bg-slate-800/40">
                      <td className="py-3 px-4 font-medium text-white">{po.po_number}</td>
                      <td className="py-3 px-4">{po.supplier_name || `Supplier #${po.supplier_id}`}</td>
                      <td className="py-3 px-4 text-slate-400">{po.order_date}</td>
                      <td className="py-3 px-4 text-center">
                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[11px] font-mono">
                          {po.items?.length || 0}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right font-semibold text-white">
                        ₹{Number(po.grand_total).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="px-2.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-500/10 text-blue-400 border border-blue-500/30">
                          {po.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={() => handleConvertPoToDirectPurchase(po)}
                          className="px-2.5 py-1 rounded bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 text-xs font-medium transition"
                        >
                          Convert to Direct Purchase
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── View 6: Suppliers Directory ─────────────────────────────────────── */}
      {activeTab === "suppliers" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {suppliers.map((s) => (
            <div key={s.id} className="p-4 bg-slate-900/60 rounded-xl border border-slate-800 space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <h4 className="font-semibold text-white text-sm">{s.name}</h4>
                  {s.legal_name && <p className="text-xs text-slate-400">{s.legal_name}</p>}
                </div>
                <span className="px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-300">
                  {s.payment_terms_days ? `${s.payment_terms_days} Days Terms` : "Net 30"}
                </span>
              </div>
              <div className="text-xs text-slate-400 space-y-1">
                {s.gstin && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-500">GSTIN:</span>
                    <span className="font-mono text-slate-200">{s.gstin}</span>
                  </div>
                )}
                {s.phone && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-500">Phone:</span>
                    <span>{s.phone}</span>
                  </div>
                )}
                {s.city && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-500">Location:</span>
                    <span>
                      {s.city}, {s.state || ""}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      </main>

      {/* ── Direct Purchase Entry Modal ────────────────────────────────────── */}
      <AnimatePresence>
        {isDirectPurchaseModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm overflow-y-auto">
            <motion.div
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              className="w-full max-w-6xl max-h-[92vh] flex flex-col bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden my-6"
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between p-5 border-b border-slate-800 bg-slate-950/70">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-emerald-500/10 text-emerald-400 rounded-xl border border-emerald-500/20">
                    <Receipt className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="text-lg font-bold text-white flex items-center gap-2">
                      New Purchase / Enter Supplier Invoice
                      {directPurchaseForm.po_id && (
                        <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/30">
                          Linked to PO #{directPurchaseForm.po_id}
                        </span>
                      )}
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Enter invoice details & medicine batches. Save as draft or post directly to inventory.
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setIsDirectPurchaseModalOpen(false)}
                  className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Modal Body */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {formError && (
                  <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
                    <span>{formError}</span>
                  </div>
                )}

                {/* Section 1: Supplier & Invoice Details */}
                <div className="bg-slate-950/50 p-4 rounded-xl border border-slate-800 space-y-4">
                  <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                    <Building2 className="w-4 h-4 text-emerald-400" />
                    1. Supplier & Invoice Details
                  </h4>

                  <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">
                        Supplier <span className="text-rose-400">*</span>
                      </label>
                      <select
                        value={directPurchaseForm.supplier_id}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            supplier_id: e.target.value,
                          }))
                        }
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                      >
                        <option value="">Select Supplier</option>
                        {suppliers.map((s) => (
                          <option key={s.id} value={String(s.id)}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">
                        Supplier Invoice # <span className="text-rose-400">*</span>
                      </label>
                      <input
                        type="text"
                        placeholder="e.g. INV-9842"
                        value={directPurchaseForm.invoice_number}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            invoice_number: e.target.value,
                          }))
                        }
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">
                        Invoice Date <span className="text-rose-400">*</span>
                      </label>
                      <input
                        type="date"
                        value={directPurchaseForm.invoice_date}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            invoice_date: e.target.value,
                          }))
                        }
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">
                        Due Date
                      </label>
                      <input
                        type="date"
                        value={directPurchaseForm.due_date}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            due_date: e.target.value,
                          }))
                        }
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                      />
                    </div>
                  </div>

                  {/* Auto-populated Supplier Badge / Card */}
                  {selectedDirectSupplier && (
                    <div className="flex flex-wrap items-center gap-4 p-3 bg-slate-900/90 rounded-lg border border-slate-800 text-xs">
                      <div>
                        <span className="text-slate-500">GSTIN:</span>{" "}
                        <span className="font-mono text-emerald-400 font-medium">
                          {selectedDirectSupplier.gstin || "Unregistered"}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500">Phone:</span>{" "}
                        <span className="text-slate-300">{selectedDirectSupplier.phone || "—"}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">Payment Terms:</span>{" "}
                        <span className="text-slate-300">
                          {selectedDirectSupplier.payment_terms_days ?? 30} Days
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500">Address:</span>{" "}
                        <span className="text-slate-300">
                          {selectedDirectSupplier.city ? `${selectedDirectSupplier.city}, ${selectedDirectSupplier.state || ""}` : "—"}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                {/* Section 2: Medicine Entries */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                      <Boxes className="w-4 h-4 text-emerald-400" />
                      2. Medicine Entries ({directPurchaseForm.items.length} lines)
                    </h4>
                    <button
                      type="button"
                      onClick={handleAddMedicineLine}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 transition"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      Add Medicine Line
                    </button>
                  </div>

                  {/* Items List */}
                  <div className="space-y-3">
                    {directPurchaseForm.items.map((line, idx) => {
                      const lineCalc = directPurchaseCalculations.lineCalculations[idx] || { lineTotal: 0 };
                      return (
                        <div
                          key={line.id}
                          className="p-4 bg-slate-950/70 rounded-xl border border-slate-800 space-y-3 hover:border-slate-700 transition"
                        >
                          <div className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-2 flex-1">
                              <span className="w-6 h-6 rounded-full bg-slate-800 text-slate-300 text-[11px] font-mono flex items-center justify-center shrink-0">
                                {idx + 1}
                              </span>
                              <div className="flex-1 max-w-xl">
                                <MedicinePicker
                                  value={line.medicine_id}
                                  placeholder="Search 252k+ medicine catalogue by brand, salt, composition…"
                                  onChange={async (id, picked) => {
                                    setDirectPurchaseForm((prev) => {
                                      const nextItems = [...prev.items];
                                      nextItems[idx] = {
                                        ...nextItems[idx],
                                        medicine_id: id,
                                        medicine_name: picked?.label || "",
                                        packaging_unit_id: picked?.default_unit_id ? String(picked.default_unit_id) : nextItems[idx].packaging_unit_id,
                                        unit_options: picked?.units || [],
                                      };
                                      return { ...prev, items: nextItems };
                                    });
                                  }}
                                />
                              </div>
                            </div>

                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => handleDuplicateLineForNewBatch(idx)}
                                title="Add another batch of this medicine"
                                className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                              >
                                <Copy className="w-3 h-3 text-slate-400" />
                                + Batch
                              </button>
                              <button
                                type="button"
                                onClick={() => handleRemoveLine(idx)}
                                title="Remove line"
                                className="p-1.5 text-slate-400 hover:text-rose-400 rounded hover:bg-rose-500/10 transition"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </div>
                          </div>

                          {/* Line item inputs grid */}
                          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2.5 text-xs pt-1">
                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">Unit</label>
                              <select
                                value={line.packaging_unit_id}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], packaging_unit_id: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
                              >
                                {(line.unit_options && line.unit_options.length > 0
                                  ? line.unit_options.map((u) => ({ id: u.unit_id, name: u.label }))
                                  : units
                                ).map((u) => (
                                  <option key={u.id} value={String(u.id)}>
                                    {u.name}
                                  </option>
                                ))}
                              </select>
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">
                                Batch # <span className="text-rose-400">*</span>
                              </label>
                              <input
                                type="text"
                                placeholder="B2026-01"
                                value={line.batch_number}
                                onChange={(e) => {
                                  const val = e.target.value.toUpperCase();
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], batch_number: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white font-mono uppercase"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">
                                Expiry Date <span className="text-rose-400">*</span>
                              </label>
                              <input
                                type="date"
                                value={line.expiry_date}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], expiry_date: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">Purchase Qty</label>
                              <input
                                type="number"
                                min="1"
                                value={line.purchase_qty_pack}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], purchase_qty_pack: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white font-semibold"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">Free Qty</label>
                              <input
                                type="number"
                                min="0"
                                value={line.free_qty_pack}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], free_qty_pack: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">Rate (₹)</label>
                              <input
                                type="number"
                                step="0.01"
                                min="0"
                                value={line.purchase_rate}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], purchase_rate: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white font-semibold"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">Disc %</label>
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="0.1"
                                value={line.discount_percent}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], discount_percent: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
                              />
                            </div>

                            <div>
                              <label className="block text-[10px] text-slate-400 mb-0.5">GST Rate</label>
                              <select
                                value={line.tax_rate_id}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  setDirectPurchaseForm((prev) => {
                                    const nextItems = [...prev.items];
                                    nextItems[idx] = { ...nextItems[idx], tax_rate_id: val };
                                    return { ...prev, items: nextItems };
                                  });
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
                              >
                                {taxRates.map((t) => (
                                  <option key={t.id} value={String(t.id)}>
                                    {t.name} ({t.rate_percentage}%)
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>

                          {/* Line total & batch metadata row */}
                          <div className="flex items-center justify-between text-xs pt-1 border-t border-slate-800/60 text-slate-400">
                            <div className="flex items-center gap-4">
                              <span>
                                MRP:{" "}
                                <input
                                  type="number"
                                  step="0.01"
                                  value={line.mrp}
                                  onChange={(e) => {
                                    const val = e.target.value;
                                    setDirectPurchaseForm((prev) => {
                                      const nextItems = [...prev.items];
                                      nextItems[idx] = { ...nextItems[idx], mrp: val };
                                      return { ...prev, items: nextItems };
                                    });
                                  }}
                                  className="w-20 bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-white text-xs ml-1"
                                />
                              </span>
                              <span>
                                Rack:{" "}
                                <input
                                  type="text"
                                  value={line.rack_location}
                                  onChange={(e) => {
                                    const val = e.target.value;
                                    setDirectPurchaseForm((prev) => {
                                      const nextItems = [...prev.items];
                                      nextItems[idx] = { ...nextItems[idx], rack_location: val };
                                      return { ...prev, items: nextItems };
                                    });
                                  }}
                                  className="w-24 bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-white text-xs ml-1"
                                />
                              </span>
                            </div>

                            <div className="flex items-center gap-2">
                              <span className="text-slate-400">Line Total:</span>
                              <span className="font-bold text-emerald-400 text-sm">
                                ₹{lineCalc.lineTotal.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Section 3: Summary Calculations */}
                <div className="bg-slate-950/80 p-5 rounded-xl border border-slate-800 grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label className="block text-xs font-medium text-slate-400 mb-1">Invoice Notes</label>
                    <textarea
                      rows={3}
                      placeholder="Add any supplier bill or delivery notes…"
                      value={directPurchaseForm.notes}
                      onChange={(e) =>
                        setDirectPurchaseForm((prev) => ({
                          ...prev,
                          notes: e.target.value,
                        }))
                      }
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs text-white focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between text-slate-400">
                      <span>Subtotal (Gross):</span>
                      <span className="text-white font-mono">
                        ₹{directPurchaseCalculations.subtotal.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>Item Discounts:</span>
                      <span className="text-rose-400 font-mono">
                        -₹{directPurchaseCalculations.totalDiscount.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>Taxable Value:</span>
                      <span className="text-white font-mono">
                        ₹{directPurchaseCalculations.taxableAmount.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>GST Breakdown:</span>
                      <span className="text-emerald-400 font-mono">
                        +₹{directPurchaseCalculations.taxTotal.toFixed(2)}
                      </span>
                    </div>

                    <div className="flex items-center justify-between text-slate-400 pt-1">
                      <span>Additional / Freight Charges:</span>
                      <input
                        type="number"
                        step="0.01"
                        value={directPurchaseForm.other_charges}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            other_charges: e.target.value,
                          }))
                        }
                        className="w-24 bg-slate-900 border border-slate-700 rounded px-2 py-0.5 text-white font-mono text-right text-xs"
                      />
                    </div>

                    <div className="flex items-center justify-between text-slate-400">
                      <span>Round Off:</span>
                      <input
                        type="number"
                        step="0.01"
                        value={directPurchaseForm.round_off}
                        onChange={(e) =>
                          setDirectPurchaseForm((prev) => ({
                            ...prev,
                            round_off: e.target.value,
                          }))
                        }
                        className="w-24 bg-slate-900 border border-slate-700 rounded px-2 py-0.5 text-white font-mono text-right text-xs"
                      />
                    </div>

                    <div className="flex justify-between items-center text-sm font-bold text-white pt-2 border-t border-slate-800">
                      <span>Final Invoice Total:</span>
                      <span className="text-lg text-emerald-400 font-mono">
                        ₹{directPurchaseCalculations.grandTotal.toFixed(2)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Modal Footer Actions */}
              <div className="flex items-center justify-between p-4 border-t border-slate-800 bg-slate-950/80">
                <button
                  type="button"
                  onClick={() => setIsDirectPurchaseModalOpen(false)}
                  className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white rounded-lg transition"
                >
                  Cancel
                </button>

                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => handleSavePurchase(false)}
                    className="px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
                  >
                    {submitting ? "Saving…" : "Save Draft"}
                  </button>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => handleSavePurchase(true)}
                    className="flex items-center gap-2 px-5 py-2 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-950/50 transition active:scale-95"
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    {submitting ? "Posting…" : "Save & Post Purchase"}
                  </button>
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── Purchase Detail Drawer / Modal ─────────────────────────────────── */}
      <AnimatePresence>
        {isDetailDrawerOpen && selectedInvoice && (
          <div className="fixed inset-0 z-50 flex items-center justify-end bg-black/70 backdrop-blur-sm">
            <motion.div
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="w-full max-w-3xl h-full bg-slate-900 border-l border-slate-800 flex flex-col overflow-hidden shadow-2xl"
            >
              {/* Drawer Header */}
              <div className="p-5 border-b border-slate-800 bg-slate-950/80 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-lg font-bold text-white">
                      Purchase {selectedInvoice.invoice_number}
                    </h3>
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        isInvoicePosted(selectedInvoice.status)
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                          : selectedInvoice.status === "DRAFT"
                          ? "bg-amber-500/10 text-amber-400 border border-amber-500/30"
                          : selectedInvoice.status === "CANCELLED"
                          ? "bg-rose-500/10 text-rose-400 border border-rose-500/30"
                          : "bg-slate-500/10 text-slate-400 border border-slate-500/30"
                      }`}
                    >
                      {isInvoicePosted(selectedInvoice.status)
                        ? "Posted (In Stock)"
                        : selectedInvoice.status === "DRAFT"
                        ? "Draft"
                        : selectedInvoice.status === "CANCELLED"
                        ? "Cancelled"
                        : selectedInvoice.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Supplier: {selectedInvoice.supplier_name || `Supplier #${selectedInvoice.supplier_id}`}
                  </p>
                </div>

                <button
                  onClick={() => setIsDetailDrawerOpen(false)}
                  className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Drawer Content */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {/* Meta Summary Cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                  <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                    <div className="text-slate-500 text-[10px]">Invoice Date</div>
                    <div className="font-medium text-white mt-0.5">{selectedInvoice.invoice_date}</div>
                  </div>
                  <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                    <div className="text-slate-500 text-[10px]">Due Date</div>
                    <div className="font-medium text-white mt-0.5">{selectedInvoice.due_date || "—"}</div>
                  </div>
                  <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                    <div className="text-slate-500 text-[10px]">Inventory Receipt</div>
                    <div className="font-medium text-emerald-400 mt-0.5">
                      {selectedInvoice.grn_number ? `GRN ${selectedInvoice.grn_number}` : "Internal Direct"}
                    </div>
                  </div>
                  <div className="p-3 bg-slate-950/60 rounded-xl border border-slate-800">
                    <div className="text-slate-500 text-[10px]">Outstanding Balance</div>
                    <div className="font-bold text-amber-400 mt-0.5">
                      ₹{Number(selectedInvoice.outstanding_amount || 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                    </div>
                  </div>
                </div>

                {/* Medicine Items Table */}
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                    Medicines & Batches ({selectedInvoice.items?.length || 0})
                  </h4>
                  <div className="bg-slate-950/60 rounded-xl border border-slate-800 overflow-hidden">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[10px]">
                        <tr>
                          <th className="py-2.5 px-3">Medicine</th>
                          <th className="py-2.5 px-3">Batch</th>
                          <th className="py-2.5 px-3 text-right">Qty</th>
                          <th className="py-2.5 px-3 text-right">Free</th>
                          <th className="py-2.5 px-3 text-right">Rate</th>
                          <th className="py-2.5 px-3 text-right">Total</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 text-slate-200">
                        {(selectedInvoice.items || []).map((it) => (
                          <tr key={it.id}>
                            <td className="py-2.5 px-3 font-medium text-white">
                              {it.medicine_name || `Medicine #${it.medicine_id}`}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-slate-400">{it.batch_number}</td>
                            <td className="py-2.5 px-3 text-right font-semibold">{it.billed_qty_pack}</td>
                            <td className="py-2.5 px-3 text-right text-slate-400">{it.free_qty_pack || 0}</td>
                            <td className="py-2.5 px-3 text-right">₹{Number(it.purchase_rate).toFixed(2)}</td>
                            <td className="py-2.5 px-3 text-right font-semibold text-emerald-400">
                              ₹{Number(it.line_total).toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Financial Summary */}
                <div className="p-4 bg-slate-950/70 rounded-xl border border-slate-800 space-y-2 text-xs">
                  <div className="flex justify-between text-slate-400">
                    <span>Subtotal:</span>
                    <span className="font-mono text-white">₹{Number(selectedInvoice.subtotal).toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>Discounts:</span>
                    <span className="font-mono text-rose-400">-₹{Number(selectedInvoice.discount_total).toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>GST Tax:</span>
                    <span className="font-mono text-emerald-400">+₹{Number(selectedInvoice.tax_total).toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between items-center text-sm font-bold text-white pt-2 border-t border-slate-800">
                    <span>Grand Total:</span>
                    <span className="text-emerald-400 font-mono">
                      ₹{Number(selectedInvoice.calculated_grand_total || selectedInvoice.supplier_declared_total).toFixed(2)}
                    </span>
                  </div>
                </div>

                {/* Accounting & Audit Note */}
                <div className="p-3 bg-slate-950/40 rounded-xl border border-slate-800/80 text-[11px] text-slate-400 space-y-1">
                  <div className="font-semibold text-slate-300">Audit & Accounting References:</div>
                  <div>Inventory GRN: {selectedInvoice.grn_number || "Internal receipt source"}</div>
                  <div>Supplier Liability: Accounts Payable posted upon bill finalization</div>
                  {selectedInvoice.finalized_at && <div>Finalized at: {selectedInvoice.finalized_at}</div>}
                  {selectedInvoice.cancelled_at && (
                    <div className="text-rose-400 font-medium">
                      Cancelled at: {selectedInvoice.cancelled_at} (Reason: {selectedInvoice.cancel_reason})
                    </div>
                  )}
                </div>
              </div>

              {/* Drawer Footer Actions */}
              <div className="p-4 border-t border-slate-800 bg-slate-950/80 flex items-center justify-between">
                <div>
                  {selectedInvoice.status === "DRAFT" && (
                    <button
                      onClick={() => handleDeleteDraft(selectedInvoice)}
                      className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition"
                    >
                      Delete Draft
                    </button>
                  )}
                  {isInvoicePosted(selectedInvoice.status) && (
                    <button
                      onClick={() => handleOpenCancelModal(selectedInvoice)}
                      className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition"
                    >
                      Cancel Purchase
                    </button>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {selectedInvoice.status === "DRAFT" && (
                    <button
                      onClick={() => handlePostDraft(selectedInvoice)}
                      className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg transition"
                    >
                      <Check className="w-3.5 h-3.5" />
                      Post Purchase Now
                    </button>
                  )}
                  {isInvoicePosted(selectedInvoice.status) && (
                    <>
                      <button
                        onClick={() => handleOpenCreditNoteModal(selectedInvoice)}
                        className="px-3 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                      >
                        Financial Credit Note
                      </button>
                      <button
                        onClick={() => handleOpenReturnModal(selectedInvoice)}
                        className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-lg bg-purple-600 hover:bg-purple-500 text-white shadow-lg transition"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        Return Medicines
                      </button>
                    </>
                  )}
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── Cancellation Modal (Safeguarded) ───────────────────────────────── */}
      <AnimatePresence>
        {isCancelModalOpen && cancelTargetInvoice && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-rose-500/10 text-rose-400 rounded-xl border border-rose-500/20">
                  <ShieldAlert className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Cancel Posted Purchase</h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Invoice {cancelTargetInvoice.invoice_number}
                  </p>
                </div>
              </div>

              <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-300 text-xs leading-relaxed">
                Controlled cancellation reverses inventory stock, removes supplier payable liability, and reverses accounting entries.
                <br />
                <span className="font-semibold text-amber-200">
                  Safeguard: If any stock from this purchase has already been sold or consumed, cancellation is strictly blocked.
                </span>
              </div>

              {cancelError && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs">
                  <p className="font-semibold">Cancellation Blocked:</p>
                  <p className="mt-1">{cancelError}</p>
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Cancellation Reason <span className="text-rose-400">*</span>
                </label>
                <textarea
                  rows={3}
                  required
                  placeholder="e.g. Invoiced in error, duplicate bill, or supplier recalled invoice…"
                  value={cancelReason}
                  onChange={(e) => setCancelReason(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs text-white focus:outline-none focus:border-rose-500"
                />
              </div>

              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setIsCancelModalOpen(false)}
                  className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={handleConfirmCancelPurchase}
                  className="px-4 py-2 text-xs font-semibold rounded-lg bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-950/50 transition"
                >
                  {submitting ? "Processing Reversal…" : "Confirm Cancellation"}
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── Return / Debit Note Modal ──────────────────────────────────────── */}
      <AnimatePresence>
        {isReturnModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2">
                  <RotateCcw className="w-5 h-5 text-purple-400" />
                  <h3 className="text-base font-bold text-white">Record Supplier Return / Debit Note</h3>
                </div>
                <button onClick={() => setIsReturnModalOpen(false)} className="text-slate-400 hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {formError && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs">
                  {formError}
                </div>
              )}

              <form onSubmit={handleSubmitReturn} className="space-y-4 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Debit Note Reference</label>
                  <input
                    type="text"
                    value={retForm.debit_note_reference}
                    onChange={(e) => setRetForm({ ...retForm, debit_note_reference: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Reason Code</label>
                  <select
                    value={retForm.reason_code}
                    onChange={(e) => setRetForm({ ...retForm, reason_code: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  >
                    <option value="DAMAGED">Damaged / Broken Packaging</option>
                    <option value="EXPIRED">Near Expiry / Expired</option>
                    <option value="WRONG_ITEM">Wrong Item Shipped</option>
                    <option value="OVER_STOCK">Overstock Return</option>
                    <option value="RECALL">Manufacturer Recall</option>
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Return Notes</label>
                  <textarea
                    rows={2}
                    value={retForm.notes}
                    onChange={(e) => setRetForm({ ...retForm, notes: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  />
                </div>

                <div className="flex justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setIsReturnModalOpen(false)}
                    className="px-4 py-2 text-slate-400 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-semibold"
                  >
                    {submitting ? "Saving…" : "Post Supplier Return"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── Financial Credit Note Modal ────────────────────────────────────── */}
      <AnimatePresence>
        {isCreditNoteModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2">
                  <Receipt className="w-5 h-5 text-emerald-400" />
                  <h3 className="text-base font-bold text-white">Record Financial Credit Note</h3>
                </div>
                <button onClick={() => setIsCreditNoteModalOpen(false)} className="text-slate-400 hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {formError && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs">
                  {formError}
                </div>
              )}

              <form onSubmit={handleSubmitCreditNote} className="space-y-4 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Credit Note #</label>
                  <input
                    type="text"
                    required
                    value={cnForm.credit_note_number}
                    onChange={(e) => setCnForm({ ...cnForm, credit_note_number: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Credit Amount (₹) <span className="text-rose-400">*</span></label>
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    required
                    placeholder="e.g. 500.00"
                    value={cnForm.credit_amount}
                    onChange={(e) => setCnForm({ ...cnForm, credit_amount: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white font-semibold"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Reason</label>
                  <select
                    value={cnForm.reason}
                    onChange={(e) => setCnForm({ ...cnForm, reason: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  >
                    <option value="RATE_DIFFERENCE">Rate Difference</option>
                    <option value="DISCOUNT_ADJUSTMENT">Special Scheme / Discount Adjustment</option>
                    <option value="SHORTAGE">Shortage Claim</option>
                    <option value="DAMAGE_CLAIM">Damage Claim without physical return</option>
                    <option value="OTHER">Other Financial Settlement</option>
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Notes</label>
                  <textarea
                    rows={2}
                    value={cnForm.notes}
                    onChange={(e) => setCnForm({ ...cnForm, notes: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  />
                </div>

                <div className="flex justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setIsCreditNoteModalOpen(false)}
                    className="px-4 py-2 text-slate-400 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold"
                  >
                    {submitting ? "Saving…" : "Post Credit Note"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* ── Advance PO Creation Modal ──────────────────────────────────────── */}
      <AnimatePresence>
        {isPoModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-xl bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2">
                  <ShoppingCart className="w-5 h-5 text-blue-400" />
                  <h3 className="text-base font-bold text-white">Create Advance Purchase Order</h3>
                </div>
                <button onClick={() => setIsPoModalOpen(false)} className="text-slate-400 hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {formError && (
                <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs">
                  {formError}
                </div>
              )}

              <div className="space-y-3 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Supplier</label>
                  <select
                    value={poForm.supplier_id}
                    onChange={(e) => setPoForm({ ...poForm, supplier_id: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  >
                    {suppliers.map((s) => (
                      <option key={s.id} value={String(s.id)}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Order Date</label>
                  <input
                    type="date"
                    value={poForm.order_date}
                    onChange={(e) => setPoForm({ ...poForm, order_date: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Medicine</label>
                  <MedicinePicker
                    value={poForm.items[0]?.medicine_id || ""}
                    onChange={(id) => {
                      setPoForm((prev) => {
                        const items = [...prev.items];
                        if (items[0]) items[0].medicine_id = id;
                        return { ...prev, items };
                      });
                    }}
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-400 mb-1">Quantity</label>
                    <input
                      type="number"
                      min="1"
                      value={poForm.items[0]?.ordered_qty_pack || "10"}
                      onChange={(e) => {
                        const val = e.target.value;
                        setPoForm((prev) => {
                          const items = [...prev.items];
                          if (items[0]) items[0].ordered_qty_pack = val;
                          return { ...prev, items };
                        });
                      }}
                      className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-slate-400 mb-1">Rate (₹)</label>
                    <input
                      type="number"
                      step="0.01"
                      value={poForm.items[0]?.purchase_rate || "50"}
                      onChange={(e) => {
                        const val = e.target.value;
                        setPoForm((prev) => {
                          const items = [...prev.items];
                          if (items[0]) items[0].purchase_rate = val;
                          return { ...prev, items };
                        });
                      }}
                      className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-white"
                    />
                  </div>
                </div>

                <div className="flex justify-end gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setIsPoModalOpen(false)}
                    className="px-4 py-2 text-slate-400 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={handleSaveAdvancePo}
                    className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold"
                  >
                    {submitting ? "Saving…" : "Save Advance PO"}
                  </button>
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
