"""Phase 2 schema: tax_rates, categories, manufacturers, units, medicines, barcodes, unit_conversions, batches, stock_movements, stock_adjustments

Revision ID: 002
Revises: 001
Create Date: 2026-10-20 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tax_rates ─────────────────────────────────────────────────────────────
    op.create_table(
        "tax_rates",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rate_percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("cgst_percentage", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("sgst_percentage", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("igst_percentage", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("cess_percentage", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tax_rates"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_tax_rates_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_tax_rates_pharmacy_name"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_tax_rates_pharmacy_id", "tax_rates", ["pharmacy_id"])

    # ── categories ────────────────────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_categories_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_categories_pharmacy_name"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_categories_pharmacy_id", "categories", ["pharmacy_id"])

    # ── manufacturers ─────────────────────────────────────────────────────────
    op.create_table(
        "manufacturers",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_code", sa.String(50), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_manufacturers"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_manufacturers_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_manufacturers_pharmacy_name"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_manufacturers_pharmacy_id", "manufacturers", ["pharmacy_id"])

    # ── units ─────────────────────────────────────────────────────────────────
    op.create_table(
        "units",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("abbreviation", sa.String(20), nullable=False),
        sa.Column("is_fractional_allowed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_units"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_units_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_units_pharmacy_name"),
        sa.UniqueConstraint("pharmacy_id", "abbreviation", name="uq_units_pharmacy_abbr"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_units_pharmacy_id", "units", ["pharmacy_id"])

    # ── medicines ─────────────────────────────────────────────────────────────
    op.create_table(
        "medicines",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand_name", sa.String(255), nullable=True),
        sa.Column("generic_name", sa.String(500), nullable=False),
        sa.Column("strength", sa.String(100), nullable=True),
        sa.Column("dosage_form", sa.String(100), nullable=False),
        sa.Column("category_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("manufacturer_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("tax_rate_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("hsn_code", sa.String(20), nullable=False, server_default="3004"),
        sa.Column("schedule_type", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("requires_prescription", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("base_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column("reorder_level", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("reorder_quantity", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("storage_instructions", sa.String(255), nullable=True),
        sa.Column("side_effects", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_medicines"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_medicines_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"],
            name="fk_medicines_category_id_categories",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manufacturer_id"], ["manufacturers.id"],
            name="fk_medicines_manufacturer_id_manufacturers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"],
            name="fk_medicines_tax_rate_id_tax_rates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_unit_id"], ["units.id"],
            name="fk_medicines_base_unit_id_units",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_medicines_pharmacy_name", "medicines", ["pharmacy_id", "name"])
    op.create_index("ix_medicines_pharmacy_generic", "medicines", ["pharmacy_id", "generic_name"])
    op.create_index("ix_medicines_pharmacy_barcode", "medicines", ["pharmacy_id", "barcode"])

    # ── medicine_barcodes ─────────────────────────────────────────────────────
    op.create_table(
        "medicine_barcodes",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("barcode", sa.String(100), nullable=False),
        sa.Column("description", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_medicine_barcodes"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_medicine_barcodes_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_medicine_barcodes_medicine_id_medicines",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("pharmacy_id", "barcode", name="uq_medicine_barcodes_pharmacy_barcode"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_medicine_barcodes_medicine_id", "medicine_barcodes", ["medicine_id"])

    # ── medicine_unit_conversions ─────────────────────────────────────────────
    op.create_table(
        "medicine_unit_conversions",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("packaging_unit_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(12, 4), nullable=False),
        sa.Column("is_sales_unit", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_purchase_unit", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_default_dispensing_unit", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_medicine_unit_conversions"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_medicine_unit_conversions_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_medicine_unit_conversions_medicine_id_medicines",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["packaging_unit_id"], ["units.id"],
            name="fk_medicine_unit_conversions_packaging_unit_id_units",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("medicine_id", "packaging_unit_id", name="uq_medicine_unit_conversions_pair"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_medicine_unit_conversions_medicine_id", "medicine_unit_conversions", ["medicine_id"])

    # ── medicine_batches ──────────────────────────────────────────────────────
    op.create_table(
        "medicine_batches",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_number", sa.String(100), nullable=False),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("mrp", sa.Numeric(12, 2), nullable=False),
        sa.Column("purchase_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_per_base_unit", sa.Numeric(12, 4), nullable=False),
        sa.Column("default_selling_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("rack_location", sa.String(100), nullable=True),
        sa.Column("current_stock_base", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_medicine_batches"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_medicine_batches_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_medicine_batches_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "medicine_id", "batch_number", name="uq_medicine_batches_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_medicine_batches_pharmacy_exp", "medicine_batches", ["pharmacy_id", "expiry_date"])
    op.create_index("ix_medicine_batches_pharmacy_med", "medicine_batches", ["pharmacy_id", "medicine_id"])

    # ── stock_movements ───────────────────────────────────────────────────────
    op.create_table(
        "stock_movements",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("movement_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.String(100), nullable=True),
        sa.Column("reference_type", sa.String(50), nullable=True),
        sa.Column("quantity_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False, server_default="0.00"),
        sa.Column("total_cost", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_movements"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_stock_movements_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_stock_movements_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["medicine_batches.id"],
            name="fk_stock_movements_batch_id_batches",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_stock_movements_user_id_users",
            ondelete="SET NULL",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_stock_movements_pharmacy_med_time", "stock_movements", ["pharmacy_id", "medicine_id", "created_at"])
    op.create_index("ix_stock_movements_pharmacy_batch_time", "stock_movements", ["pharmacy_id", "batch_id", "created_at"])
    op.create_index("ix_stock_movements_type", "stock_movements", ["pharmacy_id", "movement_type"])

    # ── stock_adjustments ─────────────────────────────────────────────────────
    op.create_table(
        "stock_adjustments",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("adjustment_number", sa.String(50), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="POSTED"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("adjusted_by", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_adjustments"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_stock_adjustments_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["adjusted_by"], ["users.id"],
            name="fk_stock_adjustments_adjusted_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "adjustment_number", name="uq_stock_adjustments_num"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_stock_adjustments_pharmacy_id", "stock_adjustments", ["pharmacy_id"])

    # ── stock_adjustment_items ───────────────────────────────────────────────
    op.create_table(
        "stock_adjustment_items",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("adjustment_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("medicine_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("batch_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("system_stock_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("physical_stock_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("adjustment_qty_base", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False, server_default="0.00"),
        sa.Column("total_cost", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_stock_adjustment_items"),
        sa.ForeignKeyConstraint(
            ["adjustment_id"], ["stock_adjustments.id"],
            name="fk_stock_adjustment_items_adjustment_id_stock_adjustments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["medicine_id"], ["medicines.id"],
            name="fk_stock_adjustment_items_medicine_id_medicines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["medicine_batches.id"],
            name="fk_stock_adjustment_items_batch_id_batches",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_stock_adjustment_items_adjustment_id", "stock_adjustment_items", ["adjustment_id"])


def downgrade() -> None:
    op.drop_table("stock_adjustment_items")
    op.drop_table("stock_adjustments")
    op.drop_table("stock_movements")
    op.drop_table("medicine_batches")
    op.drop_table("medicine_unit_conversions")
    op.drop_table("medicine_barcodes")
    op.drop_table("medicines")
    op.drop_table("units")
    op.drop_table("manufacturers")
    op.drop_table("categories")
    op.drop_table("tax_rates")
