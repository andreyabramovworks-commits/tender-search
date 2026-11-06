"""Add tender document registry and future analysis audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tender_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tender_id", sa.BigInteger(), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("url", sa.String(4000), nullable=False),
        sa.Column("access_status", sa.String(80), nullable=False),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("metadata", pg.JSONB(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tender_id", "url"),
    )
    op.create_index("ix_tender_documents_tender_id", "tender_documents", ["tender_id"])
    op.create_table(
        "tender_analyses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tender_id", sa.BigInteger(), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("status", sa.String(80), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tender_analyses_tender_id", "tender_analyses", ["tender_id"])


def downgrade():
    op.drop_table("tender_analyses")
    op.drop_table("tender_documents")
