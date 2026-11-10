"""Phase 3 schema: suppliers, purchase_orders, purchase_order_items, goods_receipts, goods_receipt_items, supplier_invoices, supplier_invoice_items, supplier_returns, supplier_return_items

Revision ID: 003
Revises: 002
Create Date: 2026-10-21 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── suppliers ─────────────────────────────────────────────────────────────
    op.create_table(
        "suppliers",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("drug_license_no", sa.String(100), nullable=True),
        sa.Column("contact_person", sa.String(100), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state", sa.String(100), nullable=True),
        sa.Column("pincode", sa.String(20), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_suppliers_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_suppliers_pharmacy_name"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_suppliers_pharmacy_id", "suppliers", ["pharmacy_id"])
    op.create_index("ix_suppliers_pharmacy_gstin", "suppliers", ["pharmacy_id", "gstin"])
    op.create_index("ix_suppliers_pharmacy_name", "suppliers", ["pharmacy_id", "name"])

    # ── purchase_orders ───────────────────────────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("po_number", sa.String(50), nullable=False),
        sa.Column("supplier_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discount_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("grand_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("approved_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_orders"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_purchase_orders_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"],
            name="fk_purchase_orders_supplier_id_suppliers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_purchase_orders_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"],
            name="fk_purchase_orders_approved_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"], ["users.id"],
            name="fk_purchase_orders_cancelled_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("pharmacy_id", "po_number", name="uq_purchase_orders_pharmacy_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_purchase_orders_pharmacy_id", "purchase_orders", ["pharmacy_id"])
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_supplier_status", "purchase_orders", ["pharmacy_id", "supplier_id", "status"])

    # ── purchase_order_items ──────────────────────────────────────────────────
    op.create_table(
        "purchase_order_items",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("po_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("packaging_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(12, 4), nullable=False, server_default="1.0000"),
        sa.Column("ordered_qty_pack", sa.Numeric(12, 2), nullable=False),
        sa.Column("ordered_qty_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("received_qty_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("purchase_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_rate_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_items"),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_orders.id"],
            name="fk_purchase_order_items_po_id_purchase_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_purchase_order_items_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_unit_id"], ["units.id"],
            name="fk_purchase_order_items_packaging_unit_id_units",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"],
            name="fk_purchase_order_items_tax_rate_id_tax_rates",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_purchase_order_items_po_id", "purchase_order_items", ["po_id"])
    op.create_index("ix_purchase_order_items_medicine_id", "purchase_order_items", ["medicine_id"])

    # ── goods_receipts ────────────────────────────────────────────────────────
    op.create_table(
        "goods_receipts",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grn_number", sa.String(50), nullable=False),
        sa.Column("supplier_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("po_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("supplier_challan_no", sa.String(100), nullable=True),
        sa.Column("supplier_invoice_no", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("total_received_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_accepted_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_rejected_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_free_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("grand_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("received_by", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goods_receipts"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_goods_receipts_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"],
            name="fk_goods_receipts_supplier_id_suppliers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_orders.id"],
            name="fk_goods_receipts_po_id_purchase_orders",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["received_by"], ["users.id"],
            name="fk_goods_receipts_received_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "grn_number", name="uq_goods_receipts_pharmacy_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_goods_receipts_pharmacy_id", "goods_receipts", ["pharmacy_id"])
    op.create_index("ix_goods_receipts_supplier_id", "goods_receipts", ["supplier_id"])
    op.create_index("ix_goods_receipts_po_id", "goods_receipts", ["po_id"])
    op.create_index("ix_goods_receipts_status", "goods_receipts", ["status"])
    op.create_index("ix_goods_receipts_sup_date", "goods_receipts", ["pharmacy_id", "supplier_id", "receipt_date"])

    # ── goods_receipt_items ───────────────────────────────────────────────────
    op.create_table(
        "goods_receipt_items",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("grn_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("po_item_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("batch_number", sa.String(100), nullable=False),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("packaging_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(12, 4), nullable=False, server_default="1.0000"),
        sa.Column("received_qty_pack", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("free_qty_pack", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("rejected_qty_pack", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("accepted_qty_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("rejected_qty_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("rejection_reason", sa.String(255), nullable=True),
        sa.Column("purchase_rate", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("mrp", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("selling_rate", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("cost_per_base_unit", sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("tax_rate_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("cgst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("sgst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("igst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("rack_location", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_goods_receipt_items"),
        sa.ForeignKeyConstraint(
            ["grn_id"], ["goods_receipts.id"],
            name="fk_goods_receipt_items_grn_id_goods_receipts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["po_item_id"], ["purchase_order_items.id"],
            name="fk_goods_receipt_items_po_item_id_purchase_order_items",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_goods_receipt_items_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["medicine_batches.id"],
            name="fk_goods_receipt_items_batch_id_medicine_batches",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_unit_id"], ["units.id"],
            name="fk_goods_receipt_items_packaging_unit_id_units",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"],
            name="fk_goods_receipt_items_tax_rate_id_tax_rates",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_goods_receipt_items_grn_id", "goods_receipt_items", ["grn_id"])
    op.create_index("ix_goods_receipt_items_medicine_id", "goods_receipt_items", ["medicine_id"])
    op.create_index("ix_goods_receipt_items_batch_id", "goods_receipt_items", ["batch_id"])

    # ── supplier_invoices ─────────────────────────────────────────────────────
    op.create_table(
        "supplier_invoices",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("supplier_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grn_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("po_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("is_tax_inclusive", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discount_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("cgst_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("sgst_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("igst_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("other_charges", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("round_off", sa.Numeric(6, 2), nullable=False, server_default="0.00"),
        sa.Column("calculated_grand_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("supplier_declared_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discrepancy_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discrepancy_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("payment_status", sa.String(20), nullable=False, server_default="UNPAID"),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_invoices"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_supplier_invoices_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"],
            name="fk_supplier_invoices_supplier_id_suppliers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grn_id"], ["goods_receipts.id"],
            name="fk_supplier_invoices_grn_id_goods_receipts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_orders.id"],
            name="fk_supplier_invoices_po_id_purchase_orders",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_supplier_invoices_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "supplier_id", "invoice_number", name="uq_supplier_invoices_pharmacy_sup_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_supplier_invoices_pharmacy_id", "supplier_invoices", ["pharmacy_id"])
    op.create_index("ix_supplier_invoices_supplier_id", "supplier_invoices", ["supplier_id"])
    op.create_index("ix_supplier_invoices_grn_id", "supplier_invoices", ["grn_id"])
    op.create_index("ix_supplier_invoices_status", "supplier_invoices", ["status"])
    op.create_index("ix_supplier_invoices_due", "supplier_invoices", ["pharmacy_id", "due_date"])

    # ── supplier_invoice_items ────────────────────────────────────────────────
    op.create_table(
        "supplier_invoice_items",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("invoice_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grn_item_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("batch_number", sa.String(100), nullable=False),
        sa.Column("packaging_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(12, 4), nullable=False, server_default="1.0000"),
        sa.Column("billed_qty_pack", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("billed_qty_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("free_qty_pack", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("purchase_rate", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("taxable_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_rate_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("cgst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("sgst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("igst_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_invoice_items"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["supplier_invoices.id"],
            name="fk_supplier_invoice_items_invoice_id_supplier_invoices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grn_item_id"], ["goods_receipt_items.id"],
            name="fk_supplier_invoice_items_grn_item_id_goods_receipt_items",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_supplier_invoice_items_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["medicine_batches.id"],
            name="fk_supplier_invoice_items_batch_id_medicine_batches",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_unit_id"], ["units.id"],
            name="fk_supplier_invoice_items_packaging_unit_id_units",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"],
            name="fk_supplier_invoice_items_tax_rate_id_tax_rates",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_supplier_invoice_items_invoice_id", "supplier_invoice_items", ["invoice_id"])
    op.create_index("ix_supplier_invoice_items_medicine_id", "supplier_invoice_items", ["medicine_id"])

    # ── supplier_returns ──────────────────────────────────────────────────────
    op.create_table(
        "supplier_returns",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("return_number", sa.String(50), nullable=False),
        sa.Column("supplier_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grn_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("invoice_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_return_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("debit_note_reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_returns"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_supplier_returns_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"],
            name="fk_supplier_returns_supplier_id_suppliers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grn_id"], ["goods_receipts.id"],
            name="fk_supplier_returns_grn_id_goods_receipts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["supplier_invoices.id"],
            name="fk_supplier_returns_invoice_id_supplier_invoices",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_supplier_returns_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "return_number", name="uq_supplier_returns_pharmacy_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_supplier_returns_pharmacy_id", "supplier_returns", ["pharmacy_id"])
    op.create_index("ix_supplier_returns_supplier_id", "supplier_returns", ["supplier_id"])
    op.create_index("ix_supplier_returns_status", "supplier_returns", ["status"])
    op.create_index("ix_supplier_returns_sup_date", "supplier_returns", ["pharmacy_id", "supplier_id", "return_date"])

    # ── supplier_return_items ─────────────────────────────────────────────────
    op.create_table(
        "supplier_return_items",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("return_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grn_item_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("invoice_item_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("packaging_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(12, 4), nullable=False, server_default="1.0000"),
        sa.Column("return_qty_pack", sa.Numeric(12, 2), nullable=False),
        sa.Column("return_qty_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost_base", sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("refund_rate", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("tax_rate_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_return_items"),
        sa.ForeignKeyConstraint(
            ["return_id"], ["supplier_returns.id"],
            name="fk_supplier_return_items_return_id_supplier_returns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grn_item_id"], ["goods_receipt_items.id"],
            name="fk_supplier_return_items_grn_item_id_goods_receipt_items",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_item_id"], ["supplier_invoice_items.id"],
            name="fk_supplier_return_items_invoice_item_id_supplier_invoice_items",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_supplier_return_items_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["medicine_batches.id"],
            name="fk_supplier_return_items_batch_id_medicine_batches",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_unit_id"], ["units.id"],
            name="fk_supplier_return_items_packaging_unit_id_units",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"],
            name="fk_supplier_return_items_tax_rate_id_tax_rates",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_supplier_return_items_return_id", "supplier_return_items", ["return_id"])
    op.create_index("ix_supplier_return_items_medicine_id", "supplier_return_items", ["medicine_id"])
    op.create_index("ix_supplier_return_items_batch_id", "supplier_return_items", ["batch_id"])


def downgrade() -> None:
    op.drop_table("supplier_return_items")
    op.drop_table("supplier_returns")
    op.drop_table("supplier_invoice_items")
    op.drop_table("supplier_invoices")
    op.drop_table("goods_receipt_items")
    op.drop_table("goods_receipts")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("suppliers")
