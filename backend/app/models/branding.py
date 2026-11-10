"""Branding: uploaded brand images and per-user appearance preferences.

Shared text branding (application name, tagline, theme defaults ...) lives in the `settings`
table under `branding.*` keys; the pharmacy's official identity stays on `pharmacies`.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow

# MEDIUMBLOB on MySQL (up to 16 MB; uploads are capped far lower by the service)
IMAGE_BLOB = LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


class BrandAsset(Base):
    """One uploaded brand image per pharmacy and kind (logo, logo_dark, logo_compact, favicon).

    Stored in the database so it is included in every backup and survives container rebuilds.
    """
    __tablename__ = "brand_assets"
    __table_args__ = (UniqueConstraint("pharmacy_id", "kind", name="uq_brand_assets_pharmacy_kind"),)

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(IMAGE_BLOB, nullable=False)
    uploaded_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UserPreference(Base):
    """A user's own appearance choices. Only the fields the user explicitly set are stored;
    everything else follows the pharmacy's global defaults."""
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    appearance_json: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
