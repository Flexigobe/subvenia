"""add cnae_inferido + cnae_inferido_label cached columns to empresa

Permite pre-calcular el CNAE estimado de cada empresa una vez (bulk script)
y leerlo en O(1) desde el autocomplete sin necesidad de re-ejecutar el inferer
en cada request.

Revision ID: 0006_empresa_cnae_inferido
Revises: 0005_slug_pattern
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_empresa_cnae_inferido"
down_revision: Union[str, Sequence[str], None] = "0005_slug_pattern"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("empresa", sa.Column("cnae_inferido", sa.String(4), nullable=True))
    op.add_column("empresa", sa.Column("cnae_inferido_label", sa.String(255), nullable=True))
    op.create_index("ix_empresa_cnae_inferido", "empresa", ["cnae_inferido"])


def downgrade() -> None:
    op.drop_index("ix_empresa_cnae_inferido", table_name="empresa")
    op.drop_column("empresa", "cnae_inferido_label")
    op.drop_column("empresa", "cnae_inferido")
