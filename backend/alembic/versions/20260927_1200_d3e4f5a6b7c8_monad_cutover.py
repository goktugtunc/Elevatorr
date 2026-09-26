"""monad cutover: Stellar -> Monad (EVM) schema

Wallets change (G… -> 0x…), so every chain-derived row is wiped; this migration is a
**cutover**, not a data migration. Öncesinde `scripts/backup.sh` (pg_dump) alınır (K10).
`downgrade()` is intentionally unsupported: restore from that dump instead.

Steps (03-backend-tasarim §7.2):
 1. TRUNCATE every domain table (RESTART IDENTITY CASCADE)
 2. DROP anchor_transactions, anchor_sessions (SEP-24 is gone)
 3. users: stellar_address -> wallet_address String(42); Numeric(30,7) -> Numeric(78,18)
 4. auth_nonces: public_key -> address String(42); + message, issued_at (SIWE)
 5. assets: network -> chain_id Integer; contract_id -> address; code -> symbol; - issuer; decimals no default
 6. agreements / balances / snapshots / listings / offers / trades: widths, renames, new columns
 7. pending_transactions: - unsigned_xdr; + EVM call columns, receipt columns; partial unique tx_hash
 8. indexer_state: - cursor; ledger -> block_number; + last_block_hash
 9. + failed_events, + faucet_claims

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_OLD_NUM = sa.Numeric(30, 7)
_NEW_NUM = sa.Numeric(78, 18)

_TRUNCATE_TABLES = (
    "users",
    "auth_nonces",
    "assets",
    "indexer_state",
    "anchor_sessions",
    "anchor_transactions",
    "follows",
    "interactions",
    "favorites",
    "listings",
    "notifications",
    "offers",
    "agreements",
    "agreement_balances",
    "agreement_value_snapshots",
    "conversations",
    "messages",
    "pending_transactions",
    "ratings",
    "trades",
)


def _widen(table: str, *columns: str, nullable: bool | None = None) -> None:
    for col in columns:
        op.alter_column(table, col, type_=_NEW_NUM, existing_type=_OLD_NUM, existing_nullable=nullable)


def _hash66(table: str, *columns: str) -> None:
    for col in columns:
        op.alter_column(table, col, type_=sa.String(66), existing_type=sa.String(64), existing_nullable=True)


def upgrade() -> None:
    # 1. wipe (wallet identities change; nothing is carried over) -----------------------------------
    op.execute(sa.text(f"TRUNCATE TABLE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))

    # 2. anchor (SEP-24) tables ----------------------------------------------------------------------
    op.drop_table("anchor_transactions")
    op.drop_table("anchor_sessions")

    # 3. users ---------------------------------------------------------------------------------------
    op.alter_column(
        "users",
        "stellar_address",
        new_column_name="wallet_address",
        type_=sa.String(42),
        existing_type=sa.String(56),
        existing_nullable=False,
    )
    op.execute(sa.text("ALTER INDEX ix_users_stellar_address RENAME TO ix_users_wallet_address"))
    _widen("users", "budget_amount", "min_capital", nullable=True)
    _widen("users", "managed_capital", nullable=False)

    # 4. auth_nonces (SIWE) --------------------------------------------------------------------------
    op.alter_column(
        "auth_nonces",
        "public_key",
        new_column_name="address",
        type_=sa.String(42),
        existing_type=sa.String(56),
        existing_nullable=False,
    )
    op.execute(sa.text("ALTER INDEX ix_auth_nonces_public_key RENAME TO ix_auth_nonces_address"))
    op.add_column("auth_nonces", sa.Column("message", sa.Text(), nullable=False))
    op.add_column("auth_nonces", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False))

    # 5. assets --------------------------------------------------------------------------------------
    op.drop_constraint("uq_assets_network_contract_id", "assets", type_="unique")
    op.drop_index("ix_assets_network_code", table_name="assets")
    op.alter_column(
        "assets",
        "network",
        type_=sa.Integer(),
        existing_type=sa.String(16),
        existing_nullable=False,
        postgresql_using="network::integer",
    )
    op.alter_column("assets", "network", new_column_name="chain_id", existing_type=sa.Integer(), existing_nullable=False)
    op.alter_column(
        "assets",
        "contract_id",
        new_column_name="address",
        type_=sa.String(42),
        existing_type=sa.String(56),
        existing_nullable=False,
    )
    op.alter_column("assets", "code", new_column_name="symbol", existing_type=sa.String(12), existing_nullable=False)
    op.drop_column("assets", "issuer")
    op.alter_column("assets", "decimals", server_default=None, existing_type=sa.Integer(), existing_nullable=False)
    op.create_unique_constraint("uq_assets_chain_id_address", "assets", ["chain_id", "address"])
    op.create_index("ix_assets_chain_id_symbol", "assets", ["chain_id", "symbol"], unique=False)

    # 6a. agreements ---------------------------------------------------------------------------------
    _widen("agreements", "principal", nullable=False)
    _widen(
        "agreements",
        "current_value",
        "high_water_value",
        "final_value",
        "profit",
        "trader_fee",
        "platform_fee",
        "customer_payout",
        nullable=True,
    )
    _hash66("agreements", "created_tx", "activate_tx", "cancel_tx", "settle_tx")
    op.alter_column(
        "agreements", "settled_by", type_=sa.String(42), existing_type=sa.String(56), existing_nullable=True
    )
    op.alter_column(
        "agreements",
        "last_event_ledger",
        new_column_name="last_event_block_number",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.add_column("agreements", sa.Column("platform_fee_bps", sa.Integer(), nullable=True))
    op.add_column("agreements", sa.Column("vault_address", sa.String(42), nullable=True))

    # 6b. agreement_balances / agreement_value_snapshots ---------------------------------------------
    _widen("agreement_balances", "balance", nullable=False)
    _widen("agreement_value_snapshots", "value", nullable=False)

    # 6c. listings -----------------------------------------------------------------------------------
    _widen("listings", "amount", "reserved_amount", "min_capital", nullable=True)
    _hash66("listings", "reserve_tx", "release_tx")

    # 6d. offers -------------------------------------------------------------------------------------
    _widen("offers", "amount", nullable=False)

    # 6e. trades -------------------------------------------------------------------------------------
    op.drop_constraint("uq_trades_tx_hash_onchain_seq", "trades", type_="unique")
    op.alter_column(
        "trades",
        "onchain_seq",
        new_column_name="log_index",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    _hash66("trades", "tx_hash")
    op.alter_column(
        "trades", "ledger", new_column_name="block_number", existing_type=sa.BigInteger(), existing_nullable=True
    )
    _widen("trades", "amount_in", "amount_out", nullable=False)
    _widen("trades", "value_after", nullable=True)
    op.create_unique_constraint("uq_trades_tx_hash_log_index", "trades", ["tx_hash", "log_index"])

    # 7. pending_transactions ------------------------------------------------------------------------
    op.drop_column("pending_transactions", "unsigned_xdr")
    op.add_column("pending_transactions", sa.Column("action", sa.String(32), nullable=False))
    op.add_column("pending_transactions", sa.Column("from_address", sa.String(42), nullable=False))
    op.add_column("pending_transactions", sa.Column("to_address", sa.String(42), nullable=False))
    op.add_column("pending_transactions", sa.Column("calldata", sa.Text(), nullable=False))
    op.add_column(
        "pending_transactions",
        sa.Column("value", sa.Numeric(78, 0), nullable=False, server_default="0"),
    )
    op.add_column("pending_transactions", sa.Column("gas", sa.BigInteger(), nullable=True))
    op.add_column("pending_transactions", sa.Column("chain_id", sa.Integer(), nullable=False))
    op.add_column("pending_transactions", sa.Column("listing_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_pending_transactions_listing_id_listings",
        "pending_transactions",
        "listings",
        ["listing_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_pending_transactions_listing_id", "pending_transactions", ["listing_id"], unique=False)
    op.add_column("pending_transactions", sa.Column("block_number", sa.BigInteger(), nullable=True))
    op.add_column("pending_transactions", sa.Column("block_hash", sa.String(66), nullable=True))
    op.add_column("pending_transactions", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("pending_transactions", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("pending_transactions", sa.Column("contract_error_code", sa.Integer(), nullable=True))
    op.add_column("pending_transactions", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    _hash66("pending_transactions", "tx_hash")
    op.drop_index("ix_pending_transactions_tx_hash", table_name="pending_transactions")
    op.create_index(
        "uq_pending_transactions_tx_hash",
        "pending_transactions",
        ["tx_hash"],
        unique=True,
        postgresql_where=sa.text("tx_hash IS NOT NULL"),
    )
    op.alter_column(
        "pending_transactions",
        "status",
        server_default="pending",
        existing_type=sa.String(32),
        existing_nullable=False,
        existing_server_default="built",
    )

    # 8. indexer_state -------------------------------------------------------------------------------
    op.drop_column("indexer_state", "cursor")
    op.alter_column(
        "indexer_state", "ledger", new_column_name="block_number", existing_type=sa.BigInteger(), existing_nullable=True
    )
    op.add_column("indexer_state", sa.Column("last_block_hash", sa.String(66), nullable=True))

    # 9. new tables ----------------------------------------------------------------------------------
    op.create_table(
        "failed_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_failed_events"),
        sa.UniqueConstraint("tx_hash", "log_index", name="uq_failed_events_tx_hash_log_index"),
    )
    op.create_index("ix_failed_events_next_retry_at", "failed_events", ["next_retry_at"], unique=False)

    op.create_table(
        "faucet_claims",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=True),
        sa.Column("amount", sa.Numeric(78, 18), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name="fk_faucet_claims_asset_id_assets", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_faucet_claims_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_faucet_claims"),
    )
    op.create_index(
        "ix_faucet_claims_user_asset_created", "faucet_claims", ["user_id", "asset_id", "created_at"], unique=False
    )


def downgrade() -> None:
    raise RuntimeError("monad_cutover is irreversible; restore from the pg_dump taken before the upgrade")
