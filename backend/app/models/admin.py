"""Phase 6/7 administration: backup history."""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow


class BackupRecord(Base):
    """One database backup file: who/when, size, SHA-256 checksum and verification result."""
    __tablename__ = "backup_history"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)          # MANUAL, SCHEDULED
    status: Mapped[str] = mapped_column(String(20), nullable=False)           # RUNNING, COMPLETED, FAILED
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    tables: Mapped[Optional[int]] = mapped_column()
    total_rows: Mapped[Optional[int]] = mapped_column(BigInteger)
    manifest_json: Mapped[Optional[str]] = mapped_column(Text)               # Row count per table at dump time
    alembic_revision: Mapped[Optional[str]] = mapped_column(String(32))
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    verify_status: Mapped[Optional[str]] = mapped_column(String(20))         # PASSED, FAILED
    verify_detail: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("file_name", name="uq_backup_history_file"),
        CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name="status_valid"),
    )
