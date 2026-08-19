"""add target move_online

Revision ID: c4d9f2a81e03
Revises: b3c8e1a92f04
Create Date: 2026-07-21 14:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d9f2a81e03"
down_revision: str | Sequence[str] | None = "b3c8e1a92f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add move_online column to targets."""
    op.add_column(
        "targets",
        sa.Column(
            "move_online", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    """Remove move_online column from targets."""
    op.drop_column("targets", "move_online")
