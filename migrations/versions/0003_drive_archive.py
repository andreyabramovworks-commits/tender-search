"""Add Google Drive archive metadata for tender documents."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tender_documents", sa.Column("archive_url", sa.String(4000), nullable=True))
    op.add_column(
        "tender_documents",
        sa.Column("archive_status", sa.String(80), nullable=False, server_default="pending"),
    )


def downgrade():
    op.drop_column("tender_documents", "archive_status")
    op.drop_column("tender_documents", "archive_url")
