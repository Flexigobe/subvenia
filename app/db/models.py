"""Modelos ORM para Plan 1: subvencion, search, search_result."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Subvencion(Base):
    __tablename__ = "subvencion"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(Enum("bdns", "eu", name="source_enum"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    organismo: Mapped[str | None] = mapped_column(Text)
    ambito: Mapped[str] = mapped_column(
        Enum("estatal", "autonomico", "local", "ue", name="ambito_enum"), nullable=False
    )
    ccaa: Mapped[str | None] = mapped_column(String(64))
    fecha_inicio: Mapped[date | None] = mapped_column(Date)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    importe_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    importe_max_beneficiario: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    porcentaje: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    beneficiarios: Mapped[dict | None] = mapped_column(JSONB)
    cnae_elegible: Mapped[list[str]] = mapped_column(ARRAY(String(8)), default=list, nullable=False)
    finalidad: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    enlace_oficial: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    estado: Mapped[str] = mapped_column(
        Enum("abierta", "cerrada", "proximamente", name="estado_enum"),
        nullable=False,
        default="abierta",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_subvencion_source_extid"),)


class Search(Base):
    __tablename__ = "search"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nif: Mapped[str] = mapped_column(String(16), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(Text)
    cnae: Mapped[str] = mapped_column(String(8), nullable=False)
    tamano: Mapped[str] = mapped_column(
        Enum("micro", "pequena", "mediana", "grande", name="tamano_enum"), nullable=False
    )
    provincia: Mapped[str] = mapped_column(String(2), nullable=False)  # código INE 2 dígitos
    finalidad: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list[SearchResult]] = relationship(back_populates="search", cascade="all, delete-orphan")


class SearchResult(Base):
    __tablename__ = "search_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search.id", ondelete="CASCADE"), nullable=False
    )
    subvencion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subvencion.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    razon: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    search: Mapped[Search] = relationship(back_populates="results")
    subvencion: Mapped[Subvencion] = relationship()


class BdnsCatalog(Base):
    __tablename__ = "bdns_catalog"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[dict | list] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AlertSubscription(Base):
    __tablename__ = "alert_subscription"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    perfil: Mapped[dict] = mapped_column(JSONB, nullable=False)
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertSent(Base):
    __tablename__ = "alert_sent"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alert_subscription.id", ondelete="CASCADE"),
        nullable=False,
    )
    subvencion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subvencion.id", ondelete="CASCADE"),
        nullable=False,
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("subscription_id", "subvencion_id", name="uq_alert_sub_subv"),
    )


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        Enum("pending", "sent", "dead", name="outbox_status_enum"),
        default="pending",
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Empresa(Base):
    """Empresa española extraída de BORME oficial.

    Plan 5 reemplaza el bloqueado NIF→razón social pivotando a razón social
    como input primario, con autocomplete desde esta tabla.
    """

    __tablename__ = "empresa"
    # Índice especializado para `LIKE 'X%'` sobre slug — sin esto el btree default
    # cae a Seq Scan sobre 7M filas. Ver migration 0005_slug_pattern.
    __table_args__ = (
        Index("ix_empresa_slug_pattern", "slug", postgresql_ops={"slug": "text_pattern_ops"}),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Slug normalizado (lowercase, sin acentos, sin sufijos S.L./S.A.) — buscable rápido
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    razon_social: Mapped[str] = mapped_column(Text, nullable=False)
    # Provincia INE de 2 dígitos (08 = Barcelona, 28 = Madrid, etc.). Nullable porque
    # algunos PDFs BORME no exponen la provincia explícitamente — usamos la del archivo.
    provincia: Mapped[str | None] = mapped_column(String(2), index=True)
    domicilio: Mapped[str | None] = mapped_column(Text)
    objeto_social: Mapped[str | None] = mapped_column(Text)
    # Registro Mercantil hoja — único por empresa. Formato típico: "S 8, H A 197635, I/A 1"
    # Lo almacenamos crudo, pero la clave única es la parte H X NNNNN. Nullable durante
    # backfill (algunas entradas no la exponen aún).
    hoja_rm: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    capital_social: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    fecha_constitucion: Mapped[date | None] = mapped_column(Date)
    fecha_ultima_act: Mapped[date | None] = mapped_column(Date)
    # Lista de actos JSONB: [{"fecha": "2025-05-08", "tipo": "Constitución", "detalle": "..."}]
    actos: Mapped[list | None] = mapped_column(JSONB)
    estado: Mapped[str] = mapped_column(
        Enum("activa", "disuelta", "concursal", name="empresa_estado_enum"),
        default="activa",
        nullable=False,
    )
    # Texto BORME crudo de la entrada — útil para debug y futuro re-parse
    raw_text: Mapped[str | None] = mapped_column(Text)
    # CNAE inferido y cacheado: pre-computado para todas las empresas para evitar
    # latencia en autocomplete. El campo se rellena con bulk script + on-demand
    # via cnae_inferer.infer_cnae(objeto_social) o infer_cnae(razon_social).
    cnae_inferido: Mapped[str | None] = mapped_column(String(4), index=True)
    cnae_inferido_label: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Licitacion(Base):
    """Licitación pública (contrato sector público) — origen TED EU.

    NO son subvenciones: son oportunidades de venderle servicios/obras/suministros
    al sector público. TED expone ~180k licitaciones de España desde 2016.
    """

    __tablename__ = "licitacion"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_licitacion_source_extid"),
        Index("ix_licitacion_fecha_limite", "fecha_limite"),
        Index("ix_licitacion_provincia", "provincia"),
        Index("ix_licitacion_ccaa", "ccaa"),
        Index("ix_licitacion_cpv", "cpv_codes", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    organismo: Mapped[str | None] = mapped_column(Text)
    ccaa: Mapped[str | None] = mapped_column(String(8))
    provincia: Mapped[str | None] = mapped_column(String(8))
    nuts_code: Mapped[str | None] = mapped_column(String(8))
    ciudad: Mapped[str | None] = mapped_column(Text)
    fecha_publicacion: Mapped[date | None] = mapped_column(Date)
    fecha_limite: Mapped[date | None] = mapped_column(Date)
    importe_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    moneda: Mapped[str | None] = mapped_column(String(8), default="EUR")
    tipo_procedimiento: Mapped[str | None] = mapped_column(String(64))
    tipo_contrato: Mapped[str | None] = mapped_column(String(64))
    cpv_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)))
    enlace_oficial: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
