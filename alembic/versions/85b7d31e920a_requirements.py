"""Consent and institution-specific Post-UTME schedules.

Revision ID: 85b7d31e920a
Revises: 4e2ea2fec018
"""

from alembic import op
import sqlalchemy as sa

revision = "85b7d31e920a"
down_revision = "4e2ea2fec018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("courses", sa.Column("source_url", sa.String(500), nullable=True))
    op.add_column("courses", sa.Column("requirements_year", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("data_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "post_utme_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "university_id",
            sa.Integer(),
            sa.ForeignKey("universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            sa.Integer(),
            sa.ForeignKey("admission_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fee_ngn", sa.Numeric(12, 2), nullable=False),
        sa.Column("eligibility", sa.String(3000), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.UniqueConstraint("university_id", "cycle_id", name="uq_post_utme_university_cycle"),
        sa.CheckConstraint("end_date >= start_date", name="ck_post_utme_dates"),
        sa.CheckConstraint("fee_ngn >= 0", name="ck_post_utme_fee"),
    )


def downgrade():
    op.drop_table("post_utme_schedules")
    op.drop_column("users", "data_consent_at")
    op.drop_column("courses", "requirements_year")
    op.drop_column("courses", "source_url")
