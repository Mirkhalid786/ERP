"""
SQLAlchemy ORM model for document number sequences.

One row per (pharmacy, document type, period). The row is locked with
SELECT ... FOR UPDATE while a number is issued, so concurrent requests are
serialised and can never receive the same number.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.inventory import PK_BIGINT, FK_BIGINT, utcnow


class DocumentSequence(Base):
    __tablename__ = "document_sequences"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)    # "PO", "GRN", "PR", "ADJ"
    period_key: Mapped[str] = mapped_column(String(50), nullable=False)  # Number prefix, e.g. "PO-202610"
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "doc_type", "period_key", name="uq_document_sequences_key"),
    )
