"""Initial schema migration.

Revision ID: 001_initial
Revises:
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("upload_hash", sa.Text, nullable=False),
        sa.Column("settlement_id", sa.Text, nullable=False),
        sa.Column("timestamp", sa.Text, nullable=False),
        sa.Column("decision_state", sa.Text, nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("record_hash", sa.Text, nullable=False),
        sa.Column("prev_hash", sa.Text, nullable=True),
    )
    op.create_index("idx_audit_upload_hash", "audit_log", ["upload_hash"])
    op.create_index("idx_audit_settlement_id", "audit_log", ["settlement_id"])


def downgrade() -> None:
    op.drop_index("idx_audit_settlement_id", "audit_log")
    op.drop_index("idx_audit_upload_hash", "audit_log")
    op.drop_table("audit_log")
