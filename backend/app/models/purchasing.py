"""
SQLAlchemy ORM models for Phase 3: Suppliers and Purchasing.

Covers:
  - Suppliers (Vendors with GSTIN, Drug License 20B/21B, credit terms)
  - Purchase Orders (Draft, Submitted, Approved, Cancelled - no stock effect)
  - Purchase Order Items
  - Goods Receipt Inward (GRN with batch creation, free goods, rejected quantities, stock ledger update)
  - Goods Receipt Items
  - Supplier Purchase Invoices (Supplier bills with dual tracking, tax breakdowns, duplicate check)
  - Supplier Invoice Items
  - Supplier Returns (Purchase returns / Debit notes with stock reduction and returnable quantity validation)
  - Supplier Return Items
"""
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean, DateTime, Date, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.inventory import PK_BIGINT, FK_BIGINT, utcnow


# ── Suppliers ──────────────────────────────────────────────────────────────────

class Supplier(Base):
    """
    Wholesale pharmaceutical distributor, C&F agent, or direct pharma manufacturer.
    Holds statutory tax and drug license details (Form 20B/21B) required for retail pharmacy procurement.
    """
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)                    # Trade / Display Name
    legal_name: Mapped[Optional[str]] = mapped_column(String(255))                    # Registered Legal Entity Name
    gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)          # 15-character Indian GSTIN
    state_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)       # 2-digit GST state code (e.g., '27')
    drug_license_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True) # e.g. Form 20B / 21B Wholesale License
    
    contact_person: Mapped[Optional[str]] = mapped_column(String(100))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(100))
    pincode: Mapped[Optional[str]] = mapped_column(String(20))
    
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False) # Credit period in days
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    purchase_orders: Mapped[List["PurchaseOrder"]] = relationship(back_populates="supplier")
    goods_receipts: Mapped[List["GoodsReceipt"]] = relationship(back_populates="supplier")
    invoices: Mapped[List["SupplierInvoice"]] = relationship(back_populates="supplier")
    returns: Mapped[List["SupplierReturn"]] = relationship(back_populates="supplier")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_suppliers_pharmacy_name"),
        Index("ix_suppliers_pharmacy_gstin", "pharmacy_id", "gstin"),
        Index("ix_suppliers_pharmacy_name", "pharmacy_id", "name"),
    )


# ── Purchase Orders ────────────────────────────────────────────────────────────

class PurchaseOrder(Base):
    """
    Formal procurement requisition placed with a supplier.
    Status lifecycle: DRAFT -> SUBMITTED -> APPROVED -> PARTIALLY_RECEIVED / RECEIVED / CANCELLED.
    IMPORTANT: Creating, submitting, or approving a purchase order does NOT mutate stock balances.
    """
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    po_number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False, index=True)
    # Valid statuses: DRAFT, SUBMITTED, APPROVED, PARTIALLY_RECEIVED, RECEIVED, CANCELLED
    
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approved_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="purchase_orders")
    items: Mapped[List["PurchaseOrderItem"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan")
    goods_receipts: Mapped[List["GoodsReceipt"]] = relationship(back_populates="purchase_order")
    invoices: Mapped[List["SupplierInvoice"]] = relationship(back_populates="purchase_order", foreign_keys="SupplierInvoice.po_id")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "po_number", name="uq_purchase_orders_pharmacy_num"),
        Index("ix_purchase_orders_supplier_status", "pharmacy_id", "supplier_id", "status"),
    )


class PurchaseOrderItem(Base):
    """Line item in a Purchase Order."""
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    packaging_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    
    ordered_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    ordered_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    received_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    purchase_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    tax_rate_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("tax_rates.id", ondelete="RESTRICT"), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255))

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="items")
    medicine: Mapped["Medicine"] = relationship()
    packaging_unit: Mapped["Unit"] = relationship()
    tax_rate: Mapped[Optional["TaxRate"]] = relationship()


# ── Goods Receipt Inward (GRN) ─────────────────────────────────────────────────

class GoodsReceipt(Base):
    """
    Physical inward inspection and receipt of pharmaceuticals into the pharmacy.
    Links to a Purchase Order (for partial/full fulfillment) or direct supplier inward.
    Posting this voucher updates `medicine_batches` and writes immutable `StockMovement` ledger entries.
    """
    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    grn_number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    po_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    supplier_challan_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    supplier_invoice_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False, index=True)
    # Valid statuses: DRAFT, POSTED, CANCELLED
    
    total_received_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_accepted_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_rejected_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_free_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    received_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="goods_receipts")
    purchase_order: Mapped[Optional["PurchaseOrder"]] = relationship(back_populates="goods_receipts")
    items: Mapped[List["GoodsReceiptItem"]] = relationship(back_populates="goods_receipt", cascade="all, delete-orphan")
    invoices: Mapped[List["SupplierInvoice"]] = relationship(back_populates="goods_receipt")
    returns: Mapped[List["SupplierReturn"]] = relationship(back_populates="goods_receipt")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "grn_number", name="uq_goods_receipts_pharmacy_num"),
        Index("ix_goods_receipts_sup_date", "pharmacy_id", "supplier_id", "receipt_date"),
    )


class GoodsReceiptItem(Base):
    """
    Detailed inward line item capturing batch, expiry, received/free/rejected counts, and costing.
    """
    __tablename__ = "goods_receipt_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    grn_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False, index=True)
    po_item_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("purchase_order_items.id", ondelete="SET NULL"), nullable=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    
    packaging_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    
    received_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # Paid pack qty
    free_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)     # Bonus pack qty
    rejected_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # Damaged/unfit pack qty
    
    accepted_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # Saleable base units added
    rejected_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # Rejected base units (not in saleable stock)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255))
    
    purchase_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    mrp: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    selling_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    cost_per_base_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False) # Allocated cost accounting for free goods
    
    tax_rate_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("tax_rates.id", ondelete="RESTRICT"), nullable=True)
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    igst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    rack_location: Mapped[Optional[str]] = mapped_column(String(100))

    goods_receipt: Mapped["GoodsReceipt"] = relationship(back_populates="items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped[Optional["MedicineBatch"]] = relationship()
    packaging_unit: Mapped["Unit"] = relationship()
    tax_rate: Mapped[Optional["TaxRate"]] = relationship()
    po_item: Mapped[Optional["PurchaseOrderItem"]] = relationship()


# ── Supplier Purchase Invoices ─────────────────────────────────────────────────

class SupplierInvoice(Base):
    """
    Supplier's commercial purchase bill.
    Prevents duplicate invoice numbers for the same supplier.
    Maintains supplier's declared bill totals vs pharmacy calculated totals to track discrepancies.
    """
    __tablename__ = "supplier_invoices"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    grn_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("goods_receipts.id", ondelete="SET NULL"), nullable=True, index=True)
    po_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_tax_inclusive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    igst_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    other_charges: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    round_off: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"), nullable=False)
    calculated_grand_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    # Supplier declared value snapshot
    supplier_declared_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discrepancy_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # calculated - declared
    discrepancy_notes: Mapped[Optional[str]] = mapped_column(Text)
    
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False, index=True) # DRAFT, POSTED (finalised), CANCELLED
    # Cache of active supplier payment allocations (see app.models.payables); never written directly
    payment_status: Mapped[str] = mapped_column(String(20), default="UNPAID", nullable=False) # UNPAID, PARTIALLY_PAID, PAID
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Duplicate guard: "<FY>:<number without spaces/punctuation>"; NULL once cancelled so a corrected
    # bill can be re-entered under the same number (the cancelled original is kept).
    number_key: Mapped[Optional[str]] = mapped_column(String(120))
    discrepancy_accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalized_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)
    replaces_invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="SET NULL"), nullable=True)
    
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="invoices")
    purchase_order: Mapped[Optional["PurchaseOrder"]] = relationship(back_populates="invoices", foreign_keys="SupplierInvoice.po_id")
    goods_receipt: Mapped[Optional["GoodsReceipt"]] = relationship(back_populates="invoices")
    items: Mapped[List["SupplierInvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    returns: Mapped[List["SupplierReturn"]] = relationship(back_populates="invoice", foreign_keys="SupplierReturn.invoice_id")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "supplier_id", "number_key", name="uq_supplier_invoices_number_key"),
        Index("ix_supplier_invoices_sup_num", "pharmacy_id", "supplier_id", "invoice_number"),
        Index("ix_supplier_invoices_due", "pharmacy_id", "due_date"),
    )


class SupplierInvoiceItem(Base):
    """Line item in a Supplier Purchase Invoice."""
    __tablename__ = "supplier_invoice_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    grn_item_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("goods_receipt_items.id", ondelete="SET NULL"), nullable=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="SET NULL"), nullable=True)
    
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    packaging_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    
    billed_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    billed_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    free_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    purchase_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    tax_rate_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("tax_rates.id", ondelete="RESTRICT"), nullable=True)
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    igst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)

    invoice: Mapped["SupplierInvoice"] = relationship(back_populates="items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped[Optional["MedicineBatch"]] = relationship()
    packaging_unit: Mapped["Unit"] = relationship()
    tax_rate: Mapped[Optional["TaxRate"]] = relationship()


# ── Supplier Purchase Returns (Debit Notes) ────────────────────────────────────

class SupplierReturn(Base):
    """
    Return of purchased stock back to the supplier (Debit Note voucher).
    Validates returnable quantities (cannot return more than accepted quantity minus already returned).
    Posting this voucher atomically reduces stock balance and records immutable RETURN_OUTWARD ledger entry.
    """
    __tablename__ = "supplier_returns"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    return_number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    grn_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("goods_receipts.id", ondelete="SET NULL"), nullable=True, index=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False, index=True) # DRAFT, POSTED, CANCELLED
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False) # DAMAGED, EXPIRED, NEAR_EXPIRY, EXCESS_SUPPLY, RECALLED, WRONG_ITEM, OTHER
    
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_return_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    debit_note_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True) # Generated debit note reference
    notes: Mapped[Optional[str]] = mapped_column(Text)
    
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="returns")
    goods_receipt: Mapped[Optional["GoodsReceipt"]] = relationship(back_populates="returns")
    invoice: Mapped[Optional["SupplierInvoice"]] = relationship(back_populates="returns")
    items: Mapped[List["SupplierReturnItem"]] = relationship(back_populates="supplier_return", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "return_number", name="uq_supplier_returns_pharmacy_num"),
        Index("ix_supplier_returns_sup_date", "pharmacy_id", "supplier_id", "return_date"),
    )


class SupplierReturnItem(Base):
    """Line item in a Supplier Return voucher."""
    __tablename__ = "supplier_return_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    return_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("supplier_returns.id", ondelete="CASCADE"), nullable=False, index=True)
    grn_item_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("goods_receipt_items.id", ondelete="SET NULL"), nullable=True)
    invoice_item_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoice_items.id", ondelete="SET NULL"), nullable=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    packaging_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    
    return_qty_pack: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    return_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost_base: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    refund_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False) # Refund rate per pack
    
    tax_rate_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("tax_rates.id", ondelete="RESTRICT"), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255))

    supplier_return: Mapped["SupplierReturn"] = relationship(back_populates="items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped["MedicineBatch"] = relationship()
    packaging_unit: Mapped["Unit"] = relationship()
    tax_rate: Mapped[Optional["TaxRate"]] = relationship()
    grn_item: Mapped[Optional["GoodsReceiptItem"]] = relationship()
