"""Create tenders, change history and run audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tenders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("raw", pg.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tender_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tender_id", sa.BigInteger(), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tender_versions_tender_id", "tender_versions", ["tender_id"])
    op.create_table(
        "runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("summary", pg.JSONB(), nullable=False),
    )
    op.create_table(
        "query_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("records", sa.BigInteger(), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.UniqueConstraint("run_id", "category", "query", "mode", "scope"),
    )
    op.create_index("ix_query_runs_run_id", "query_runs", ["run_id"])


def downgrade():
    op.drop_table("query_runs")
    op.drop_table("runs")
    op.drop_table("tender_versions")
    op.drop_table("tenders")
