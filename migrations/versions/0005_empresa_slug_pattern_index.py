"""add text_pattern_ops index on empresa.slug for fast LIKE prefix searches

Sin este índice especializado, el btree default sobre slug NO se usa con
`slug LIKE 'X%'` y la query degenera a Seq Scan sobre 7M filas (>25min).
Con `text_pattern_ops` el index sí se aplica y la query baja a <100ms.

Revision ID: 0005_slug_pattern
Revises: be65eec05f89
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_slug_pattern"
down_revision: Union[str, Sequence[str], None] = "be65eec05f89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_empresa_slug_pattern "
        "ON empresa USING btree (slug text_pattern_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_empresa_slug_pattern")
