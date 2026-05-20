"""add licitacion table for TED EU tenders

Plan: TED publica ~180k licitaciones públicas de España (oportunidades de
contratar con el sector público — NO subvenciones). Tabla separada porque
es otro tipo de oportunidad económica.

Revision ID: 0007_licitacion
Revises: 0006_empresa_cnae_inferido
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_licitacion"
down_revision: Union[str, Sequence[str], None] = "0006_empresa_cnae_inferido"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "licitacion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),  # 'ted'
        sa.Column("external_id", sa.String(64), nullable=False),  # publication-number
        sa.Column("titulo", sa.Text, nullable=False),
        sa.Column("descripcion", sa.Text),
        sa.Column("organismo", sa.Text),
        sa.Column("ccaa", sa.String(8)),
        sa.Column("provincia", sa.String(8)),
        sa.Column("nuts_code", sa.String(8)),  # ES511, ES617...
        sa.Column("ciudad", sa.Text),
        sa.Column("fecha_publicacion", sa.Date),
        sa.Column("fecha_limite", sa.Date),
        sa.Column("importe_total", sa.Numeric(18, 2)),
        sa.Column("moneda", sa.String(8), server_default="EUR"),
        sa.Column("tipo_procedimiento", sa.String(64)),  # open, restricted, negotiated...
        sa.Column("tipo_contrato", sa.String(64)),  # services, supplies, works
        sa.Column("cpv_codes", postgresql.ARRAY(sa.String(16))),  # Vocabulario Común de Contratación
        sa.Column("enlace_oficial", sa.Text),  # PDF en español
        sa.Column("raw_payload", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_licitacion_source_extid"),
    )
    op.create_index("ix_licitacion_fecha_limite", "licitacion", ["fecha_limite"])
    op.create_index("ix_licitacion_provincia", "licitacion", ["provincia"])
    op.create_index("ix_licitacion_ccaa", "licitacion", ["ccaa"])
    op.create_index("ix_licitacion_cpv", "licitacion", ["cpv_codes"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_licitacion_cpv", table_name="licitacion")
    op.drop_index("ix_licitacion_ccaa", table_name="licitacion")
    op.drop_index("ix_licitacion_provincia", table_name="licitacion")
    op.drop_index("ix_licitacion_fecha_limite", table_name="licitacion")
    op.drop_table("licitacion")
