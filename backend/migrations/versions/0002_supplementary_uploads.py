"""Supplementary roster uploads: proposal kind and previously submitted students.

Revision ID: 0002_supplementary_uploads
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_supplementary_uploads"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roster_proposals") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=20), server_default="FULL", nullable=False))
        batch.add_column(sa.Column("previously_submitted_roll_numbers", sa.JSON(), server_default="[]", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("roster_proposals") as batch:
        batch.drop_column("previously_submitted_roll_numbers")
        batch.drop_column("kind")
