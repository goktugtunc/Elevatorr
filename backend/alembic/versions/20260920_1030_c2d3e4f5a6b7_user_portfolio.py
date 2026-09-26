"""users.portfolio: trader'ın geçmişini kendi cümleleriyle anlattığı bölüm

İstatistikler zincirden geliyor ama yatırımcı sayıların arkasındaki hikâyeyi de
görmek istiyor: hangi işlemler yapıldı, nasıl bir yol izlendi.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("portfolio", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "portfolio")
