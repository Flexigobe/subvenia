"""Daily BORME ingester: pulls sumario, downloads each province PDF, parses, upserts."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Empresa
from app.sync.borme_fetcher import fetch_pdf, fetch_sumario
from app.sync.borme_parser import extract_pdf_text, parse_pdf_text

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_PDFS = 5
_HEADERS = {"Accept": "application/pdf", "User-Agent": "subvenciones-app/0.5"}


def _merge_into_existing(existing: Empresa, parsed: dict[str, Any]) -> None:
    """Update `existing` in place with newer data from `parsed`. Conservative: only
    overwrites fields when the new data has a value, and `fecha_constitucion` /
    `objeto_social` / `capital_social` are never overwritten if already set (they
    come from the original Constitución entry and don't change)."""
    # Merge actos: append new ones not already present (by tipo)
    new_actos = parsed.get("actos") or []
    existing_actos = existing.actos or []
    existing_tipos = {a.get("tipo") for a in existing_actos}
    merged_actos = list(existing_actos)
    for a in new_actos:
        if a.get("tipo") not in existing_tipos:
            merged_actos.append(a)
            existing_tipos.add(a.get("tipo"))
    if merged_actos != existing_actos:
        existing.actos = merged_actos

    # Update fecha_ultima_act to the more recent
    new_fecha = parsed.get("fecha_ultima_act")
    if new_fecha and (existing.fecha_ultima_act is None or new_fecha > existing.fecha_ultima_act):
        existing.fecha_ultima_act = new_fecha

    # Estado: escalate disuelta/concursal but never revert from disuelta to activa
    new_estado = parsed.get("estado") or "activa"
    if new_estado in ("disuelta", "concursal") and existing.estado != new_estado:
        existing.estado = new_estado

    # Domicilio: newer entry wins if it has one
    if parsed.get("domicilio"):
        existing.domicilio = parsed["domicilio"]

    # Provincia: only set if currently None
    if existing.provincia is None and parsed.get("provincia"):
        existing.provincia = parsed["provincia"]

    # raw_text: keep the most recent for debug
    if parsed.get("raw_text"):
        existing.raw_text = parsed["raw_text"]

    # Constitution-only fields: only set if currently unset
    if existing.fecha_constitucion is None and parsed.get("fecha_constitucion"):
        existing.fecha_constitucion = parsed["fecha_constitucion"]
    if existing.objeto_social is None and parsed.get("objeto_social"):
        existing.objeto_social = parsed["objeto_social"]
    if existing.capital_social is None and parsed.get("capital_social"):
        existing.capital_social = parsed["capital_social"]


def _upsert_entry(session: Session, parsed: dict[str, Any]) -> str:
    """Insert or update an empresa by hoja_rm. Returns 'created', 'updated', or 'skipped'."""
    hoja_rm = parsed.get("hoja_rm")
    if not hoja_rm:
        return "skipped"
    existing = session.execute(
        select(Empresa).where(Empresa.hoja_rm == hoja_rm)
    ).scalar_one_or_none()
    if existing is None:
        session.add(Empresa(
            slug=parsed["slug"],
            razon_social=parsed["razon_social"],
            provincia=parsed.get("provincia"),
            domicilio=parsed.get("domicilio"),
            objeto_social=parsed.get("objeto_social"),
            hoja_rm=hoja_rm,
            capital_social=parsed.get("capital_social"),
            fecha_constitucion=parsed.get("fecha_constitucion"),
            fecha_ultima_act=parsed.get("fecha_ultima_act"),
            actos=parsed.get("actos"),
            estado=parsed.get("estado") or "activa",
            raw_text=parsed.get("raw_text"),
        ))
        return "created"
    _merge_into_existing(existing, parsed)
    return "updated"


async def _process_pdf(
    session: Session,
    client: httpx.AsyncClient,
    pdf_meta: dict,
    semaphore: asyncio.Semaphore,
) -> dict[str, int]:
    """Download + parse one province PDF, upsert entries. Returns local stats."""
    stats = {"created": 0, "updated": 0, "skipped_no_hoja": 0, "entries": 0}
    async with semaphore:
        pdf_bytes = await fetch_pdf(pdf_meta["url_pdf"], client=client)
    if not pdf_bytes:
        raise RuntimeError(f"PDF download failed: {pdf_meta['identificador']}")
    text = extract_pdf_text(pdf_bytes)
    if not text:
        return stats  # empty PDF — count nothing
    entries = parse_pdf_text(text, provincia_code=pdf_meta.get("provincia"))
    stats["entries"] = len(entries)
    for entry in entries:
        outcome = _upsert_entry(session, entry)
        if outcome == "created":
            stats["created"] += 1
        elif outcome == "updated":
            stats["updated"] += 1
        else:
            stats["skipped_no_hoja"] += 1
    return stats


async def sync_day(session: Session, target: date) -> dict[str, int]:
    """Ingest one day of BORME-A entries into the empresa table.

    For a given date, fetch BORME sumario, then for each province PDF:
    download → extract text → parse entries → upsert into empresa table.

    Upserts by `hoja_rm` (UNIQUE). If empresa already exists:
      - Merge `actos` (append new ones, keep existing)
      - Update `fecha_ultima_act` to the more recent
      - Update `estado` if new acto indicates disolución/concurso
      - Update `domicilio` if newer entry has one (older domicilio is stale)
      - DON'T overwrite `fecha_constitucion`, `objeto_social`, `capital_social` if already set
    If empresa is new: insert all fields.

    Skips weekends + holidays (fetch_sumario returns [] → 0 stats).
    Returns: {"created": N, "updated": M, "skipped_no_hoja": K, "errors": E,
              "total_pdfs": P, "total_entries": T}
    """
    items = await fetch_sumario(target)
    if not items:
        logger.info("BORME sync %s: no items (likely weekend/holiday)", target)
        return {
            "created": 0, "updated": 0, "skipped_no_hoja": 0, "errors": 0,
            "total_pdfs": 0, "total_entries": 0,
        }

    totals = {
        "created": 0, "updated": 0, "skipped_no_hoja": 0, "errors": 0,
        "total_pdfs": 0, "total_entries": 0,
    }
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PDFS)

    async with httpx.AsyncClient(timeout=60.0, headers=_HEADERS) as client:
        for pdf_meta in items:
            totals["total_pdfs"] += 1
            try:
                stats = await _process_pdf(session, client, pdf_meta, semaphore)
                totals["created"] += stats["created"]
                totals["updated"] += stats["updated"]
                totals["skipped_no_hoja"] += stats["skipped_no_hoja"]
                totals["total_entries"] += stats["entries"]
                session.commit()
                logger.info(
                    "BORME %s %s (%s): +%d / ~%d / skip %d",
                    target, pdf_meta["identificador"], pdf_meta.get("titulo", "")[:30],
                    stats["created"], stats["updated"], stats["skipped_no_hoja"],
                )
            except Exception as exc:
                totals["errors"] += 1
                logger.warning(
                    "BORME %s: error processing %s: %s",
                    target, pdf_meta["identificador"], exc,
                )
                session.rollback()

    return totals
