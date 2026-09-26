"""listings: capital is locked in the vault at listing time

The customer now deposits the capital into the vault when the listing goes up
(`reserve`), so a capital listing is always backed by real funds. The listing
carries the on-chain reservation it is funded from; `draft` is the state before
that deposit is confirmed.

Revision ID: b1c2d3e4f5a6
Revises: a780a7ffa848
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a780a7ffa848"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("reservation_id", sa.BigInteger(), nullable=True))
    op.add_column("listings", sa.Column("reserved_amount", sa.Numeric(30, 7), nullable=True))
    op.add_column("listings", sa.Column("reserve_tx", sa.String(64), nullable=True))
    op.add_column("listings", sa.Column("release_tx", sa.String(64), nullable=True))
    op.create_index("ix_listings_reservation_id", "listings", ["reservation_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_listings_reservation_id", table_name="listings")
    op.drop_column("listings", "release_tx")
    op.drop_column("listings", "reserve_tx")
    op.drop_column("listings", "reserved_amount")
    op.drop_column("listings", "reservation_id")
