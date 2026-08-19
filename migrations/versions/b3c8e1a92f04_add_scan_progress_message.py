"""add scan progress_message

Revision ID: b3c8e1a92f04
Revises: 205db5f6ad0b
Create Date: 2026-07-21 13:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c8e1a92f04"
down_revision: str | Sequence[str] | None = "205db5f6ad0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add progress_message column to scans."""
    op.add_column("scans", sa.Column("progress_message", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove progress_message column from scans."""
    op.drop_column("scans", "progress_message")
