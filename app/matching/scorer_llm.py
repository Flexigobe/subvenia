"""LLM scoring con Gemini — análisis exhaustivo con checklist de requisitos.

El filtro determinista (filter.py + analyzer.py) reduce ~163k subvenciones a
~300 candidatos. Este módulo lee el TEXTO COMPLETO de cada candidato (descripción,
bases reguladoras, beneficiarios, sectores, requisitos) y devuelve por candidato:

  - applicable: True/False (estricto: solo True con confidence >= 70)
  - score: 0-100
  - razon: por qué encaja o no
  - requisitos_cumplidos: lista de [{requisito, cumplido}]
  - confidence: 0-100 (qué tan seguro está el LLM)

Pipeline:
1. Si gemini_api_key vacía → modo offline (determinista).
2. Cache 7 días por (perfil_hash, subvencion_id) — gratis repetir misma empresa.
3. Batch 5 candidatos × N paralelos con response_mime_type=JSON.
4. Validación post-LLM: si confidence < 70 → applicable=False.
5. Si Gemini falla / timeout → fallback determinista marcado con confidence=0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from app.matching.filter import Candidate, EmpresaProfile

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """# IDENTIDAD
Eres un asesor SENIOR especializado en subvenciones públicas españolas y europeas para SMEs.
Tienes 15 años de experiencia en CDTI, ENISA y la Comisión Europea. Tu trabajo consiste en
detectar con PRECISIÓN QUIRÚRGICA si una empresa concreta puede solicitar una subvención.

# REGLAS DE COMPORTAMIENTO
1. **Eres ESTRICTO**: ante CUALQUIER duda razonable → applicable=false. Es mucho peor recomendar
   una subvención que la empresa NO puede pedir (frustración, tiempo perdido, daño reputacional)
   que descartar una marginal.
2. **Razonas paso a paso**: para cada subvención evalúas 5 dimensiones (destinatario, sector,
   geografía, tamaño, plazo) ANTES de dar el veredicto.
3. **Eres explícito con el sector**: si la subvención menciona un sector concreto, lo comparas
   con el CNAE de la empresa. Si no coinciden y la subvención NO es transversal → false.

# PERFIL DE LA EMPRESA QUE EVALÚAS
- Tipo jurídico: SOCIEDAD MERCANTIL PRIVADA con actividad económica (NO ONG, NO persona física)
- CNAE: {cnae}  ← OBSERVA QUÉ SECTOR ES ESTE CÓDIGO
- Tamaño: {tamano}  (micro <10 emp / pequena 10-49 / mediana 50-249 / grande 250+)
- Provincia: {provincia} (CCAA: {ccaa})
- Finalidades de interés expresadas por la empresa: {finalidad}

# CRITERIOS DE DESCARTE (applicable=false si CUALQUIERA aplica)

## A — Destinatario incorrecto
- Becas a personas físicas (tesis doctoral, máster, formación individual, estudios)
- Premios honoríficos a individuos (literarios, fotográficos, militares, religiosos)
- Convenios bilaterales nominativos: "ENTRE X y Y", "concesión directa a ENTIDAD CONCRETA"
- Subvenciones a otras administraciones públicas (ayuntamientos, diputaciones, CCAA)
- Solo para ONGs / fundaciones / asociaciones sin ánimo de lucro
- Solo para personas físicas autónomas (la empresa es sociedad jurídica)
- Solo para empresas de OTRA provincia/CCAA distinta a la del perfil
- **Programa "Beatriz Galindo" / "Ramón y Cajal" / "Juan de la Cierva"**: SIEMPRE
  son para investigadores universitarios — descartar siempre para empresas
- **Subvenciones a sindicatos / mesa sectorial / mesa negociación**: descartar
- **"Fundación Biodiversidad"** (organismo): subvenciones a ONGs medioambientales
  — descartar para empresas, salvo que la convocatoria expresamente abra a SMEs

## A bis — COOPERACIÓN INTERNACIONAL (descartar SIEMPRE para empresas españolas comunes)
La empresa del perfil es una PYME que busca subvenciones para SU actividad en España/UE.
Las siguientes son cooperación al desarrollo a terceros países o asuntos internacionales —
NO son para que una empresa española normal lo solicite. Descarta SIEMPRE:

- **EuropeAid** (cualquier identificador EuropeAid/...) — DG INTPA / cooperación al desarrollo
- "Sri Lanka", "Maldives", "Bosnia and Herzegovina", "Tajikistan", "Egypt", "Tunisia",
  "Cabo Verde", "Cape Verde", "African Union", "Latin America", "ASEAN", "MENA",
  "Indo-Pacific", "Sahel", "Caucasus", "Western Balkans", "Eastern Partnership"
- "Human Rights and Democracy in [país tercero]"
- "Civil Society support to [país]"
- "Town Twinning" (hermanamiento de ciudades)
- "Vendors' list", "Pre-information notice", "Tender" (son contratos públicos, no subvenciones)

## B — Sector incompatible (la regla MÁS importante)
La subvención menciona un sector específico distinto al CNAE de la empresa → applicable=false.

### Mapeo de sectores incompatibles típicos:
| Empresa CNAE | Subvenciones que NO aplican |
|---|---|
| 21xx Farmacéutica | Agro, ganadería, pesca, audiovisual, turismo, derechos humanos, soil/agricultura sostenible |
| 26xx Electrónica/circuitos | Agro, hostelería, cultura, cooperación al desarrollo |
| 47xx Comercio menor | I+D farmacéutico, automoción industrial, aeroespacial |
| 41-43xx Construcción | Innovación digital de salud, biotech, oncología |
| 56xx Restauración | Industria pesada, química, manufactura, I+D fundamental |
| 62xx Software/IT | Agricultura tradicional, ganadería |
| 1071 Panadería | Smart cities, IA, blockchain, espacio |

### Subvenciones TRANSVERSALES (aplican a CUALQUIER sector legítimo):
- Kit Digital (Red.es) — digitalización SMEs
- CDTI Neotec / Cervera / PID — I+D+i empresarial (verifica si tu CNAE encaja)
- ENISA — préstamos participativos
- Ayudas a la contratación (SEPE)
- Eficiencia energética / autoconsumo (IDAE) — aplica a TODA empresa
- Next Generation EU / PRTR genérico

## B-1 — TRAMPA CRÍTICA: ICEX ferias agrupadas son SECTORIALES, no transversales
**ICEX NO es transversal**. Cada feria de ICEX se organiza con una "entidad colaboradora"
de un sector concreto. Mira el TÍTULO de la feria y deduce el sector:

| Feria / palabra clave en título | Sector REAL | Aplica a CNAE |
|---|---|---|
| LINEAPELLE, MOMAD, MICAM, MIPEL, AEC | Cuero, piel, calzado, marroquinería | 1411, 1412, 1419, 152x, 4641, 4642 |
| BEAUTY WORLD, COSMOPROF, STANPA, COSMETICA, PERFUMERIA | Perfumería, cosmética | 2042, 4645, 4775 |
| ISPO, OUTDOOR, SPORT, FITNESS | Deporte, outdoor | 3230, 4642, 4764 |
| ALIMENTARIA, FOODEX, ANUGA, SIAL, FRUITAATRACTION, FENAVIN, VINEXPO | Alimentación, vino, bebidas | 10xx, 11xx, 4631-4639 |
| FITUR, WTM, ITB, FERIA TURISMO | Turismo | 55xx, 79xx |
| EXPOTOYS, TOYS | Juguete | 3240, 4665 |
| MOTORTEC, AUTOMECHANIKA | Automoción | 29xx, 4530-4532 |
| BATIMAT, CONSTRUMAT, REHABEND | Construcción, materiales | 4xxx (construcción), 4673 (materiales) |
| BIJORHCA, MIDO, JUNWEX | Joyería, óptica | 3212, 4647 |
| IBTM, CONVENTA | Eventos / MICE | 7990, 8230 |
| ARCO, ART BASEL | Arte | 9003 |

**Si el CNAE de la empresa NO ENCAJA con el sector de la feria → applicable=false**.
Razón típica: "ICEX ferias agrupadas son sectoriales, esta feria es de [sector X] y la
empresa es de [sector Y]: no encaja".

## B-2 — Horizon Europa / clusters CL1-CL6 (TRAMPA)
Las convocatorias del programa Horizon Europa (HORIZON-, EIC, MSCA, EIT-, CL1-CL6)
son MUY específicas. NO basta con que "acepte PYMEs". El TEMA debe encajar con el
CNAE de la empresa. Ejemplos:
- "Cancer research" → solo empresas biomédicas/farma (21xx, 26xx subset)
- "Climate change Antarctica" → centros investigación oceanográfica
- "AI for healthcare" → empresas IT + sanidad
- "Defence / military" → empresas defensa (3030, 2540)
- "Agriculture in Africa" → cooperación al desarrollo agro
**Si tu empresa NO tiene relación con el tema científico → applicable=false**, aunque
"acepte PYMEs". Es ABSURDO recomendar Horizon de cáncer a un comercio de fontanería.

## C — Tamaño / plazos
- Solo "grandes empresas" y la empresa es PYME → false (o viceversa)
- Plazo cerrado → false
- Requisitos formales imposibles (ej. 5 años antigüedad y empresa nueva) → false

# CRITERIOS DE APROBACIÓN (applicable=true requiere los 5)
1. ✅ Empresa privada con ánimo de lucro es beneficiaria elegible
2. ✅ Sector coincide CON el CNAE, O la subvención es transversal
3. ✅ Ámbito geográfico incluye la provincia/CCAA
4. ✅ Tamaño dentro del rango permitido
5. ✅ Plazo abierto

Si dudas en CUALQUIERA → applicable=false.

# EJEMPLOS DE RAZONAMIENTO (few-shot)

## Ejemplo 1: empresa farma CNAE 2120, subvención eólica
Razonamiento: "Eólica" es energía renovable. ¿Aplica a farma? Solo si es para autoconsumo
energético de la propia fábrica farmacéutica. Si la descripción menciona "desarrollo de
tecnologías eólicas para offshore" → NO (es para empresas del sector eólico, no usuarios).
Si menciona "subvención a empresas que invierten en autoconsumo solar/eólico" → SÍ
(es transversal, IDAE).
→ Veredicto típico: applicable=false (la mayoría son para empresas del sector eólico).

## Ejemplo 2: empresa comercio menor CNAE 4799, Kit Digital
Razonamiento: Kit Digital es transversal, abierto a todas las PYMEs con menos de 250
empleados. CNAE 4799 entra en el segmento elegible. Provincia no relevante (ámbito
estatal).
→ Veredicto: applicable=true, score=85.

## Ejemplo 3: empresa software CNAE 6201, subvención investigación cáncer
Razonamiento: Investigación oncológica requiere infraestructura biomédica y permisos
clínicos. Una empresa de software NO puede ser beneficiaria directa salvo que la
convocatoria mencione explícitamente "desarrollo de software médico" como línea elegible.
→ Veredicto: applicable=false en la mayoría de casos.

## Ejemplo 4: empresa de tecnología CNAE 6201, EIC Accelerator
Razonamiento: EIC Accelerator es para startups innovadoras de cualquier sector tech.
CNAE 6201 (programación) es exactamente el perfil objetivo.
→ Veredicto: applicable=true, score=95.

## Ejemplo 5 (TRAMPA ICEX): comercio mayorista ferretería CNAE 4674, ICEX feria LINEAPELLE
Razonamiento: LINEAPELLE es la feria del sector CUERO/PIEL/CALZADO organizada con AEC
(Asociación Española de Componentes del Calzado). La empresa vende ferretería, fontanería
y calefacción — NO tiene producto de cuero ni calzado que exponer. Aunque la convocatoria
ICEX dice "CNAE 46 elegible" (cualquier comercio al por mayor), el FILTRO REAL es la
adecuación sectorial al PRODUCTO de la feria. AEC seleccionará empresas de calzado.
→ Veredicto: applicable=false, score=20, razon="LINEAPELLE es feria de cuero/calzado
(AEC); la empresa vende ferretería: sector incompatible".

## Ejemplo 6 (TRAMPA Horizon): comercio mayorista CNAE 4674, Horizon "Cancer research"
Razonamiento: La convocatoria es para clínicas/biotech/farma con datos clínicos. Un
comercio de ferretería NO puede ser beneficiario de investigación oncológica aunque
sea PYME. El presupuesto enorme (115M€) no la hace transversal — es solo para sector
sanidad.
→ Veredicto: applicable=false, score=15, razon="Horizon Cancer research requiere
expertise biomédica; empresa de ferretería no encaja".

## Ejemplo 7 (TRAMPA Horizon clima): comercio CNAE 4674, "Climate Action / Antarctica"
Razonamiento: Estudios climáticos son para centros de investigación oceanográfica,
NO para empresas comerciales. "Acepta PYMEs" es genérico pero el sector exigido es
ciencia climática.
→ Veredicto: applicable=false, score=10.

# SUBVENCIONES A EVALUAR
{items}

# FORMATO DE RESPUESTA
Devuelve un JSON array con un objeto por subvención (mismo orden de entrada).
Cada objeto debe tener EXACTAMENTE estos campos:

{{
  "id": "<id idéntico al de entrada>",
  "applicable": true | false,
  "score": <0-49 si false; 70-100 si true con encaje claro; 50-69 si encaje débil — pero
           recuerda: si dudas, mejor false>,
  "confidence": <0-100 — qué tan seguro estás del veredicto; <80 = mejor false>,
  "razon": "<frase clara máx. 250 chars: POR QUÉ aplica o no, mencionando el SECTOR>",
  "requisitos": [
    "Sector <X> vs subvención <Y>: cumplido|no cumplido",
    "Tamaño PYME: cumplido|no cumplido",
    "Ámbito geográfico: cumplido|no cumplido",
    "Plazo abierto: cumplido|no cumplido"
  ]
}}

Responde EXCLUSIVAMENTE con el JSON array. Sin markdown, sin texto adicional, sin
explicaciones fuera del array. El JSON debe ser parseable directamente."""


# (score, razon, applicable, confidence, requisitos_json, expires_unix)
_cache: dict[str, tuple[int, str | None, bool, int, str, float]] = {}
_CACHE_TTL = 7 * 86400
_BATCH_SIZE = 8       # Más candidatos por llamada (Paid Tier soporta prompts largos)
_TIMEOUT_S = 45.0
_MIN_CONFIDENCE = 90  # Umbral muy estricto — solo "aplicable" si el LLM tiene 90%+ confianza.
                      # Política: cero falsos positivos. Preferimos descartar oportunidades
                      # marginales antes que mostrar una subvención que la empresa NO pueda pedir.
_MAX_PARALLEL_BATCHES = 40  # Paid Tier: 10.000 RPM, 40 concurrent margen amplio


_PROVINCIA_CCAA: dict[str, str] = {
    "01": "País Vasco", "02": "Castilla-La Mancha", "03": "Comunidad Valenciana",
    "04": "Andalucía", "05": "Castilla y León", "06": "Extremadura", "07": "Baleares",
    "08": "Cataluña", "09": "Castilla y León", "10": "Extremadura", "11": "Andalucía",
    "12": "Comunidad Valenciana", "13": "Castilla-La Mancha", "14": "Andalucía",
    "15": "Galicia", "16": "Castilla-La Mancha", "17": "Cataluña", "18": "Andalucía",
    "19": "Castilla-La Mancha", "20": "País Vasco", "21": "Andalucía", "22": "Aragón",
    "23": "Andalucía", "24": "Castilla y León", "25": "Cataluña", "26": "La Rioja",
    "27": "Galicia", "28": "Madrid", "29": "Andalucía", "30": "Murcia",
    "31": "Navarra", "32": "Galicia", "33": "Asturias", "34": "Castilla y León",
    "35": "Canarias", "36": "Galicia", "37": "Castilla y León", "38": "Canarias",
    "39": "Cantabria", "40": "Castilla y León", "41": "Andalucía", "42": "Castilla y León",
    "43": "Cataluña", "44": "Aragón", "45": "Castilla-La Mancha", "46": "Comunidad Valenciana",
    "47": "Castilla y León", "48": "País Vasco", "49": "Castilla y León", "50": "Aragón",
    "51": "Ceuta", "52": "Melilla",
}


def _perfil_hash(perfil: EmpresaProfile) -> str:
    blob = f"{perfil.cnae}|{perfil.tamano}|{perfil.provincia}|{','.join(sorted(perfil.finalidad))}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cnae_description(cnae: str) -> str:
    """Devuelve la descripción legible del CNAE para el prompt del LLM."""
    try:
        from app.lib.cnae_catalog import get_by_code
        entry = get_by_code(cnae)
        if entry:
            return entry.description
    except Exception:
        pass
    return f"CNAE {cnae}"


def _cache_key(perfil_hash: str, sub_id: str) -> str:
    return f"{perfil_hash}:{sub_id}"


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _build_item_context(idx: int, c: Candidate) -> str:
    """Contexto rico para una subvención — incluye TODO lo relevante para decidir.

    Incluye descripción larga, beneficiarios, sectores, regiones, instrumentos,
    tamaños elegibles, fechas, importes. Hasta 2500 chars por subvención.
    """
    sub = c.subvencion
    rp = sub.raw_payload or {}
    lines = [f"--- SUBVENCIÓN {idx + 1} ---"]
    lines.append(f"id: {sub.id}")
    lines.append(f"título: {(sub.titulo or '')[:400]}")
    if sub.organismo:
        lines.append(f"organismo: {sub.organismo[:150]}")
    lines.append(f"ámbito: {sub.ambito}" + (f" / {sub.ccaa}" if sub.ccaa else ""))

    if sub.fecha_inicio:
        lines.append(f"fecha apertura: {sub.fecha_inicio.isoformat()}")
    if sub.fecha_fin:
        lines.append(f"fecha cierre: {sub.fecha_fin.isoformat()}")
    if sub.importe_total:
        lines.append(f"presupuesto total: {int(sub.importe_total):,}€")
    if sub.importe_max_beneficiario:
        lines.append(f"importe máximo por beneficiario: {int(sub.importe_max_beneficiario):,}€")
    if sub.porcentaje:
        lines.append(f"porcentaje subvencionado: {sub.porcentaje}%")
    if sub.finalidad:
        lines.append(f"finalidades clasificadas: {sub.finalidad}")
    if sub.cnae_elegible:
        lines.append(f"CNAE elegibles: {sub.cnae_elegible[:20]}")

    # Tipos beneficiarios — CRÍTICO para decidir
    tipos = rp.get("tiposBeneficiarios") or []
    if tipos:
        descs = [(t.get("descripcion") or "")[:180] for t in tipos[:6]]
        lines.append(f"TIPOS BENEFICIARIOS ELEGIBLES: {' | '.join(descs)}")
    else:
        # Sin datos oficiales BDNS → marca explícita para que el LLM sea ULTRA-estricto
        lines.append("⚠️ TIPOS BENEFICIARIOS NO DECLARADOS OFICIALMENTE — SÉ EXTREMADAMENTE ESTRICTO: la subvención solo aplica si su título/descripción menciona EXPLÍCITAMENTE 'empresas', 'pymes', 'autónomos' o 'sector económico'. Si menciona 'asociaciones', 'familias', 'particulares', 'vecinos', 'mayores', 'jóvenes', 'estudiantes', 'cooperativa religiosa', 'club deportivo', 'cofradía', 'banda', 'coro', 'museo', 'biblioteca', 'voluntariado' → applicable=false. Si menciona un AYUNTAMIENTO CONCRETO como beneficiario → applicable=false. Si es 'concesión directa' o 'convenio con [entidad concreta]' → applicable=false.")

    # Tamaños elegibles — CRÍTICO
    tamanos = (sub.beneficiarios or {}).get("tamanos") if sub.beneficiarios else None
    if tamanos:
        lines.append(f"tamaños empresa elegibles: {tamanos}")

    # Sectores BDNS
    sectores = rp.get("sectores") or []
    if sectores:
        descs = [(s.get("descripcion") or "")[:120] for s in sectores[:10]]
        lines.append(f"sectores: {' | '.join(descs)}")

    # Regiones (NUTS-3) — CRÍTICO para ámbito geográfico
    regiones = rp.get("regiones") or []
    if regiones:
        descs = [(r.get("descripcion") or "")[:120] for r in regiones[:8]]
        lines.append(f"regiones elegibles: {' | '.join(descs)}")

    # Instrumentos
    instrumentos = rp.get("instrumentos") or []
    if instrumentos:
        descs = [(i.get("descripcion") or "")[:100] for i in instrumentos[:4]]
        lines.append(f"tipos de ayuda: {' | '.join(descs)}")

    # Descripción larga (la parte clave) — truncada para batch eficiente
    if sub.descripcion:
        lines.append(f"DESCRIPCIÓN: {sub.descripcion[:800]}")

    # EU extras (tipos de acción UE)
    eu_extra = rp.get("_eu_extra") or {}
    if eu_extra.get("typesOfAction"):
        lines.append(f"tipos de acción UE: {eu_extra['typesOfAction']}")

    return "\n".join(lines)


async def _score_one_batch(
    model, perfil: EmpresaProfile, batch: list[tuple[int, Candidate]]
) -> dict[int, tuple[int, str | None, bool, int, str]]:
    """Llamada Gemini para un batch. Devuelve dict idx → (score, razon, applicable, confidence, requisitos_json)."""
    items_text = "\n\n".join(_build_item_context(local_i, c) for local_i, (_, c) in enumerate(batch))
    cnae_desc = _cnae_description(perfil.cnae)
    prompt = _PROMPT_TEMPLATE.format(
        cnae=f"{perfil.cnae} ({cnae_desc})",
        tamano=perfil.tamano,
        provincia=perfil.provincia,
        ccaa=_PROVINCIA_CCAA.get(perfil.provincia, "—"),
        finalidad=perfil.finalidad or "(empresarial general)",
        items=items_text,
    )
    try:
        generation_config = {
            "temperature": 0.05,  # casi determinista
            "response_mime_type": "application/json",
        }
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=generation_config,
            ),
            timeout=_TIMEOUT_S,
        )
        text = _strip_markdown_fences(resp.text or "")
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
        out: dict[int, tuple[int, str | None, bool, int, str]] = {}
        for (orig_idx, candidate), item in zip(batch, parsed):
            try:
                score = max(0, min(100, int(item.get("score", candidate.score))))
            except (TypeError, ValueError):
                score = candidate.score
            try:
                confidence = max(0, min(100, int(item.get("confidence", 50))))
            except (TypeError, ValueError):
                confidence = 50
            applicable_raw = bool(item.get("applicable", True))
            # Umbral estricto: confidence baja → no aplicable
            applicable = applicable_raw and confidence >= _MIN_CONFIDENCE
            razon = item.get("razon")
            if razon is not None:
                razon = str(razon)[:280] or None
            requisitos = item.get("requisitos") or []
            req_json = json.dumps(requisitos, ensure_ascii=False)[:1200]
            out[orig_idx] = (score, razon, applicable, confidence, req_json)
        for orig_idx, candidate in batch:
            out.setdefault(orig_idx, (candidate.score, None, True, 0, "[]"))
        return out
    except Exception as exc:
        logger.warning("Gemini batch failed (%s); fallback determinista", exc)
        return {orig_idx: (candidate.score, None, True, 0, "[]") for orig_idx, candidate in batch}


async def score_batch(
    perfil: EmpresaProfile, candidates: list[Candidate]
) -> list[tuple[int, str | None, bool, int, list[str]]]:
    """Pipeline en 2 capas:

    Capa 1 — Gemini 2.5 FLASH analiza todos los candidatos (rápido, ~3s/batch).
    Capa 2 — Gemini 2.5 PRO re-analiza los candidatos AMBIGUOS (confidence 50-79)
             del Flash. Pro es más capaz y resuelve dudas. Solo se invoca para
             ~10-20% de candidatos típicamente. Coste extra ~5x pero precisión 2x.

    Si GEMINI_API_KEY no está → modo offline (score determinista, conf=0).
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return [(c.score, None, True, 0, []) for c in candidates]

    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        flash_model = genai.GenerativeModel(settings.gemini_model)
        # Pro fallback model — más caro pero mucho más capaz para casos límite
        pro_model = genai.GenerativeModel("gemini-2.5-pro")
    except Exception as exc:
        logger.warning("Failed to init Gemini (%s); fallback determinista", exc)
        return [(c.score, None, True, 0, []) for c in candidates]
    model = flash_model  # capa 1 default

    ph = _perfil_hash(perfil)
    now = time.time()
    results: list[tuple[int, str | None, bool, int, list[str]] | None] = [None] * len(candidates)
    to_score: list[tuple[int, Candidate]] = []

    for idx, c in enumerate(candidates):
        key = _cache_key(ph, str(c.subvencion.id))
        cached = _cache.get(key)
        if cached and cached[5] > now:
            try:
                reqs = json.loads(cached[4]) if cached[4] else []
            except Exception:
                reqs = []
            results[idx] = (cached[0], cached[1], cached[2], cached[3], reqs)
        else:
            to_score.append((idx, c))

    # Batches en paralelo con semáforo para limitar concurrencia
    batches = [to_score[i : i + _BATCH_SIZE] for i in range(0, len(to_score), _BATCH_SIZE)]
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_BATCHES)

    async def _score_with_sem(batch):
        async with semaphore:
            return await _score_one_batch(model, perfil, batch)

    batch_results = await asyncio.gather(
        *[_score_with_sem(b) for b in batches],
        return_exceptions=False,
    )

    for batch_out in batch_results:
        for orig_idx, (score, razon, applicable, conf, req_json) in batch_out.items():
            try:
                reqs = json.loads(req_json) if req_json else []
            except Exception:
                reqs = []
            results[orig_idx] = (score, razon, applicable, conf, reqs)
            sub_id = str(candidates[orig_idx].subvencion.id)
            _cache[_cache_key(ph, sub_id)] = (score, razon, applicable, conf, req_json, now + _CACHE_TTL)

    for i, r in enumerate(results):
        if r is None:
            results[i] = (candidates[i].score, None, True, 0, [])

    # ────────────────────────────────────────────────────────────
    # Capa 2 — Re-evaluación con Gemini 2.5 Pro para AMBIGUOS
    # ────────────────────────────────────────────────────────────
    # Detectar candidatos donde Flash dudó: confidence 40-79.
    # Solo si Flash dijo "aplicable=true" pero con duda baja — para evitar
    # mantener falsos positivos. Si Flash dijo "aplicable=false" con alta
    # confianza, lo respetamos sin re-evaluar.
    ambiguous_idx: list[int] = []
    for i, r in enumerate(results):
        score, razon, applicable, conf, reqs = r  # type: ignore[misc]
        if applicable and 40 <= conf < 80:
            ambiguous_idx.append(i)
        elif not applicable and 40 <= conf < 60:
            # También re-evaluamos los descartados con duda baja, por si Flash
            # los descartó por error
            ambiguous_idx.append(i)

    if ambiguous_idx:
        logger.info("Gemini Pro re-evaluating %d ambiguous candidates", len(ambiguous_idx))
        pro_to_score = [(idx, candidates[idx]) for idx in ambiguous_idx]
        pro_batches = [pro_to_score[i : i + _BATCH_SIZE] for i in range(0, len(pro_to_score), _BATCH_SIZE)]
        pro_semaphore = asyncio.Semaphore(_MAX_PARALLEL_BATCHES // 2)  # menos paralelo (rate limit Pro)

        async def _score_with_pro(batch):
            async with pro_semaphore:
                return await _score_one_batch(pro_model, perfil, batch)

        pro_results = await asyncio.gather(*[_score_with_pro(b) for b in pro_batches], return_exceptions=False)
        for batch_out in pro_results:
            for orig_idx, (score, razon, applicable, conf, req_json) in batch_out.items():
                try:
                    reqs = json.loads(req_json) if req_json else []
                except Exception:
                    reqs = []
                # Solo aceptar el veredicto de Pro si tiene alta confidence
                if conf >= 70:
                    results[orig_idx] = (score, razon, applicable, conf, reqs)
                    sub_id = str(candidates[orig_idx].subvencion.id)
                    _cache[_cache_key(ph, sub_id)] = (score, razon, applicable, conf, req_json, now + _CACHE_TTL)

    return results  # type: ignore[return-value]
