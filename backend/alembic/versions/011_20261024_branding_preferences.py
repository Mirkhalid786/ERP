"""Branding and appearance: uploaded brand images (brand_assets) and per-user appearance
preferences (user_preferences). Adds two empty tables only; no existing data or permission is changed.
Shared branding text is stored in the existing settings table (branding.* keys) and needs no schema change.

Revision ID: 011
Revises: 010
Create Date: 2026-10-24 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIGINT = sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.create_table('brand_assets',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', BIGINT, nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('content_type', sa.String(length=50), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('data', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=False),
    sa.Column('uploaded_by', BIGINT, nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_brand_assets_pharmacy_id_pharmacies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_brand_assets_uploaded_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_brand_assets')),
    sa.UniqueConstraint('pharmacy_id', 'kind', name='uq_brand_assets_pharmacy_kind')
    )
    op.create_table('user_preferences',
    sa.Column('user_id', BIGINT, nullable=False),
    sa.Column('appearance_json', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_preferences'))
    )


def downgrade() -> None:
    op.drop_table('user_preferences')
    op.drop_table('brand_assets')
