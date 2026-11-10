"""Imported supplier bills: the original document, what was read from it, the reviewer's edits and the purchase
documents created from it. A bill import never changes stock, payables or the books by itself; only confirming it
creates the goods receipt / supplier bill drafts through the normal purchasing services."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow

DOCUMENT_BLOB = LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")      # up to 16 MB; uploads capped at 10 MB
LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class PurchaseBillImport(Base):
    __tablename__ = "purchase_bill_imports"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)        # EXTRACTED, FAILED, CONFIRMED, DISCARDED
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(DOCUMENT_BLOB, nullable=False)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(30))   # pdf-text, ocr-local, none
    extraction_json: Mapped[Optional[str]] = mapped_column(LONG_TEXT)       # Raw lines + parsed fields (untrusted)
    extraction_error: Mapped[Optional[str]] = mapped_column(Text)
    review_json: Mapped[Optional[str]] = mapped_column(LONG_TEXT)           # The reviewer's saved draft
    supplier_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="SET NULL"))
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100))
    invoice_date: Mapped[Optional[date]] = mapped_column(Date)
    grand_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    grn_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("goods_receipts.id", ondelete="SET NULL"))
    supplier_invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="SET NULL"))
    uploaded_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_purchase_bill_imports_pharmacy_status", "pharmacy_id", "status"),
        Index("ix_purchase_bill_imports_sha256", "pharmacy_id", "sha256"),
        Index("ix_purchase_bill_imports_invoice", "pharmacy_id", "supplier_id", "invoice_number"),
    )
