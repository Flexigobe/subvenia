"""Modelos ORM para Plan 1: subvencion, search, search_result."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
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
