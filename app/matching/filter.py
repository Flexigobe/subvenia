"""Filtro SQL + pre-ranking determinista para candidatos de subvención."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Subvencion

# Mapeo simplificado provincia INE → CCAA
_PROVINCIA_TO_CCAA: dict[str, str] = {
    "01": "PV", "02": "CM", "03": "VC", "04": "AN", "05": "CL", "06": "EX",
    "07": "IB", "08": "CT", "09": "CL", "10": "EX", "11": "AN", "12": "VC",
    "13": "CM", "14": "AN", "15": "GA", "16": "CM", "17": "CT", "18": "AN",
    "19": "CM", "20": "PV", "21": "AN", "22": "AR", "23": "AN", "24": "CL",
    "25": "CT", "26": "RI", "27": "GA", "28": "MD", "29": "AN", "30": "MC",
    "31": "NC", "32": "GA", "33": "AS", "34": "CL", "35": "CN", "36": "GA",
    "37": "CL", "38": "CN", "39": "CB", "40": "CL", "41": "AN", "42": "CL",
    "43": "CT", "44": "AR", "45": "CM", "46": "VC", "47": "CL", "48": "PV",
    "49": "CL", "50": "AR", "51": "CE", "52": "ML",
}

# Palabras-clave por provincia para inferir provincia desde el texto del organismo
# (BDNS no rellena CCAA/provincia estructuradas en records locales/autonómicos). Usadas
# para evitar polución cruzada: un usuario en Barcelona no debería ver una local de Gran
# Canaria como su top result.
_PROVINCIA_KEYWORDS: dict[str, list[str]] = {
    "01": ["ÁLAVA", "ALAVA", "ARABA", "VITORIA", "PAÍS VASCO", "PAIS VASCO", "EUSKADI"],
    "02": ["ALBACETE", "CASTILLA-LA MANCHA", "CASTILLA LA MANCHA"],
    "03": ["ALICANTE", "ALACANT", "COMUNIDAD VALENCIANA", "VALENCIANA", "VALENCIANO", "GENERALITAT VALENCIANA"],
    "04": ["ALMERÍA", "ALMERIA", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "05": ["ÁVILA", "AVILA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "06": ["BADAJOZ", "EXTREMADURA", "JUNTA DE EXTREMADURA"],
    "07": ["BALEARES", "MALLORCA", "ILLES BALEARS", "BALEARS", "EIVISSA", "MENORCA", "FORMENTERA", "GOVERN BALEAR"],
    "08": ["BARCELONA", "BCN", "CATALUÑA", "CATALUNYA", "CATALAN", "CATALANA", "GENERALITAT DE CATALUNYA"],
    "09": ["BURGOS", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "10": ["CÁCERES", "CACERES", "EXTREMADURA", "JUNTA DE EXTREMADURA"],
    "11": ["CÁDIZ", "CADIZ", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "12": ["CASTELLÓN", "CASTELLON", "CASTELLÓ", "CASTELLO", "COMUNIDAD VALENCIANA", "VALENCIANA", "GENERALITAT VALENCIANA"],
    "13": ["CIUDAD REAL", "CASTILLA-LA MANCHA", "CASTILLA LA MANCHA"],
    "14": ["CÓRDOBA", "CORDOBA", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "15": ["A CORUÑA", "CORUÑA", "CORUNA", "GALICIA", "GALEGO", "GALLEGO", "XUNTA DE GALICIA"],
    "16": ["CUENCA", "CASTILLA-LA MANCHA", "CASTILLA LA MANCHA"],
    "17": ["GIRONA", "GERONA", "CATALUÑA", "CATALUNYA", "GENERALITAT DE CATALUNYA"],
    "18": ["GRANADA", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "19": ["GUADALAJARA", "CASTILLA-LA MANCHA", "CASTILLA LA MANCHA"],
    "20": ["GUIPÚZCOA", "GUIPUZCOA", "GIPUZKOA", "DONOSTIA", "SAN SEBASTIÁN", "SAN SEBASTIAN", "PAÍS VASCO", "PAIS VASCO", "EUSKADI"],
    "21": ["HUELVA", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "22": ["HUESCA", "ARAGÓN", "ARAGON", "GOBIERNO DE ARAGÓN", "GOBIERNO DE ARAGON"],
    "23": ["JAÉN", "JAEN", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "24": ["LEÓN", "LEON", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "25": ["LLEIDA", "LÉRIDA", "LERIDA", "CATALUÑA", "CATALUNYA", "GENERALITAT DE CATALUNYA"],
    "26": ["LA RIOJA", "RIOJA", "GOBIERNO DE LA RIOJA"],
    "27": ["LUGO", "GALICIA", "GALEGO", "GALLEGO", "XUNTA DE GALICIA"],
    "28": ["MADRID", "COMUNIDAD DE MADRID"],
    "29": ["MÁLAGA", "MALAGA", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "30": ["MURCIA", "REGIÓN DE MURCIA", "REGION DE MURCIA"],
    "31": ["NAVARRA", "NAFARROA", "PAMPLONA", "GOBIERNO DE NAVARRA"],
    "32": ["OURENSE", "ORENSE", "GALICIA", "GALEGO", "GALLEGO", "XUNTA DE GALICIA"],
    "33": ["ASTURIAS", "OVIEDO", "ASTURIANO", "ASTURIANA", "PRINCIPADO DE ASTURIAS"],
    "34": ["PALENCIA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "35": ["LAS PALMAS", "GRAN CANARIA", "FUERTEVENTURA", "LANZAROTE", "CANARIAS", "GOBIERNO DE CANARIAS"],
    "36": ["PONTEVEDRA", "VIGO", "GALICIA", "GALEGO", "GALLEGO", "XUNTA DE GALICIA"],
    "37": ["SALAMANCA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "38": ["TENERIFE", "S/C TENERIFE", "SANTA CRUZ DE TENERIFE", "LA PALMA", "LA GOMERA", "EL HIERRO", "CANARIAS", "GOBIERNO DE CANARIAS"],
    "39": ["CANTABRIA", "SANTANDER", "GOBIERNO DE CANTABRIA"],
    "40": ["SEGOVIA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "41": ["SEVILLA", "SEVILLE", "ANDALUCÍA", "ANDALUCIA", "JUNTA DE ANDALUCÍA", "JUNTA DE ANDALUCIA"],
    "42": ["SORIA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "43": ["TARRAGONA", "CATALUÑA", "CATALUNYA", "GENERALITAT DE CATALUNYA"],
    "44": ["TERUEL", "ARAGÓN", "ARAGON", "GOBIERNO DE ARAGÓN", "GOBIERNO DE ARAGON"],
    "45": ["TOLEDO", "CASTILLA-LA MANCHA", "CASTILLA LA MANCHA"],
    "46": ["VALENCIA", "VALÈNCIA", "COMUNIDAD VALENCIANA", "VALENCIANA", "GENERALITAT VALENCIANA"],
    "47": ["VALLADOLID", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "48": ["VIZCAYA", "BIZKAIA", "BILBAO", "PAÍS VASCO", "PAIS VASCO", "EUSKADI"],
    "49": ["ZAMORA", "CASTILLA Y LEÓN", "CASTILLA Y LEON", "JUNTA DE CASTILLA Y LEÓN", "JUNTA DE CASTILLA Y LEON"],
    "50": ["ZARAGOZA", "ARAGÓN", "ARAGON", "GOBIERNO DE ARAGÓN", "GOBIERNO DE ARAGON"],
    "51": ["CEUTA"],
    "52": ["MELILLA"],
}


@dataclass(frozen=True)
class EmpresaProfile:
    cnae: str
    tamano: str  # micro|pequena|mediana|grande
    provincia: str  # código INE 2 dígitos
    finalidad: list[str] = field(default_factory=list)
    # Tipo de solicitante — usa los 5 tipos oficiales BDNS:
    #   "empresa"      = empresa privada / autónomo con actividad económica (default)
    #   "ong"          = asociación / fundación / club deportivo / cofradía sin lucro
    #   "particular"   = persona física sin actividad económica (becas, vivienda)
    #   "ayuntamiento" = entidad pública local
    #   "investigacion" = universidad / centro investigación / consorcio I+D
    tipo_solicitante: str = "empresa"

    @property
    def ccaa(self) -> str | None:
        return _PROVINCIA_TO_CCAA.get(self.provincia)


# Solo los nombres distintivos de cada provincia (nombre + variantes ortográficas +
# capital diferente del nombre de provincia). NO incluye CCAA (compartidas) ni términos
# genéricos. Se usa para post-filtrar records que pertenecen explícitamente a OTRA
# provincia (ej. "Cámara de Comercio de Toledo" para un usuario de Barcelona).
_PROVINCIA_DISTINCTIVE: dict[str, list[str]] = {
    "01": ["ÁLAVA", "ALAVA", "ARABA", "VITORIA"],
    "02": ["ALBACETE"],
    "03": ["ALICANTE", "ALACANT"],
    "04": ["ALMERÍA", "ALMERIA"],
    "05": ["ÁVILA", "AVILA"],
    "06": ["BADAJOZ"],
    "07": ["BALEARES", "MALLORCA", "ILLES BALEARS", "BALEARS", "MENORCA", "EIVISSA", "FORMENTERA"],
    "08": ["BARCELONA"],
    "09": ["BURGOS"],
    "10": ["CÁCERES", "CACERES"],
    "11": ["CÁDIZ", "CADIZ", "JEREZ"],
    "12": ["CASTELLÓN", "CASTELLON", "CASTELLÓ", "CASTELLO"],
    "13": ["CIUDAD REAL"],
    "14": ["CÓRDOBA", "CORDOBA"],
    "15": ["A CORUÑA", "CORUÑA", "CORUNA", "LA CORUÑA"],
    "16": ["CUENCA"],
    "17": ["GIRONA", "GERONA"],
    "18": ["GRANADA"],
    "19": ["GUADALAJARA"],
    "20": ["GUIPÚZCOA", "GUIPUZCOA", "GIPUZKOA", "DONOSTIA", "SAN SEBASTIÁN", "SAN SEBASTIAN"],
    "21": ["HUELVA"],
    "22": ["HUESCA"],
    "23": ["JAÉN", "JAEN"],
    "24": ["LEÓN"],  # cuidado: 'LEON' solo conflicta con CASTILLA Y LEÓN, dejamos LEÓN con acento
    "25": ["LLEIDA", "LÉRIDA", "LERIDA"],
    "26": ["LA RIOJA", "RIOJA"],
    "27": ["LUGO"],
    "28": ["MADRID"],
    "29": ["MÁLAGA", "MALAGA"],
    "30": ["MURCIA"],
    "31": ["NAVARRA", "NAFARROA", "PAMPLONA"],
    "32": ["OURENSE", "ORENSE"],
    "33": ["ASTURIAS", "OVIEDO"],
    "34": ["PALENCIA"],
    "35": ["LAS PALMAS", "GRAN CANARIA", "FUERTEVENTURA", "LANZAROTE"],
    "36": ["PONTEVEDRA", "VIGO"],
    "37": ["SALAMANCA"],
    "38": ["TENERIFE", "SANTA CRUZ DE TENERIFE", "LA PALMA", "LA GOMERA", "EL HIERRO"],
    "39": ["CANTABRIA", "SANTANDER"],
    "40": ["SEGOVIA"],
    "41": ["SEVILLA"],
    "42": ["SORIA"],
    "43": ["TARRAGONA"],
    "44": ["TERUEL"],
    "45": ["TOLEDO"],
    "46": ["VALENCIA", "VALÈNCIA", "ORIHUELA", "ALCOY"],  # Alcoy/Orihuela son ciudades de Alicante pero conflictan menos
    "47": ["VALLADOLID"],
    "48": ["VIZCAYA", "BIZKAIA", "BILBAO"],
    "49": ["ZAMORA"],
    "50": ["ZARAGOZA"],
    "51": ["CEUTA"],
    "52": ["MELILLA"],
}


_LOCAL_ORG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^C[ÁA]MARA(?:\s+OFICIAL)?\s+DE\s+COMERCIO(?:\s*,?\s*INDUSTRIA[A-ZÁÉÍÓÚÑ\s,]*?)?\s+DE\s+(.+)$"),
    re.compile(r"^AYUNTAMIENTO\s+DE\s+(.+)$"),
    re.compile(r"^DIPUTACI[ÓO]N(?:\s+PROVINCIAL)?\s+DE\s+(.+)$"),
    re.compile(r"^CABILDO(?:\s+INSULAR)?\s+DE\s+(.+)$"),
    re.compile(r"^CONSEJO\s+INSULAR\s+DE\s+(.+)$"),
    re.compile(r"^CONSORCIO\s+(?:DE\s+)?(.+)$"),
    re.compile(r"^MANCOMUNIDAD\s+(?:DE\s+)?(.+)$"),
]


def _is_local_org_other_province(organismo: str | None, user_keywords: list[str]) -> bool:
    """Detecta organismos típicos de gobierno local (Cámara de Comercio, Ayuntamiento,
    Diputación, Cabildo, Consejo Insular, Consorcio, Mancomunidad) que mencionan una
    localidad concreta distinta de la del usuario. Las "Cámara de Comercio de España"
    y similares nacionales se mantienen."""
    if not organismo:
        return False
    upper = organismo.upper().strip()
    for pattern in _LOCAL_ORG_PATTERNS:
        m = pattern.match(upper)
        if not m:
            continue
        location = m.group(1).strip()
        if location.startswith("ESPAÑA") or location.startswith("ESPANA"):
            return False  # Cámara de España, Consorcio de España... = nacional
        if any(kw.upper() in location for kw in user_keywords):
            return False  # localidad coincide con la del usuario
        return True
    return False


def _mentions_other_province(text: str | None, other_distinctive: set[str]) -> bool:
    """Detecta si `text` menciona explícitamente otra provincia con el patrón
    "DE/EN <provincia>" (ej. "Cámara de Comercio DE TOLEDO", "para empresas DE MÁLAGA").
    Evita falsos positivos como "MINISTERIO DE INDUSTRIA" (INDUSTRIA no es provincia) o
    "ICEX ESPAÑA" (ESPAÑA tampoco)."""
    if not text:
        return False
    upper = f" {text.upper()} "
    for kw in other_distinctive:
        if f" DE {kw} " in upper or f" DE {kw}." in upper or f" DE {kw}," in upper:
            return True
        if f" EN {kw} " in upper or f" EN {kw}." in upper or f" EN {kw}," in upper:
            return True
        if f" PARA {kw} " in upper or f" PARA {kw}." in upper:
            return True
    return False


@dataclass(frozen=True)
class Candidate:
    subvencion: Subvencion
    score: int  # 0-100


# CNAE-2009: rangos de divisiones numéricas (2 dígitos) → letra de sector.
# BDNS guarda `cnae_elegible` mezclando formatos: letras ('J'), dotted ('62.0', '88.9')
# y prefijos numéricos ('6', '62', '620', '6201'). El matching debe cubrir los tres.
_DIVISION_TO_SECTOR_RANGES: list[tuple[str, int, int]] = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39),
    ("F", 41, 43), ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63),
    ("K", 64, 66), ("L", 68, 68), ("M", 69, 75), ("N", 77, 82), ("O", 84, 84),
    ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93), ("S", 94, 96), ("T", 97, 98),
    ("U", 99, 99),
]


def _cnae_division_to_sector(division_2_digits: str) -> str | None:
    if not division_2_digits.isdigit() or len(division_2_digits) != 2:
        return None
    n = int(division_2_digits)
    for letter, lo, hi in _DIVISION_TO_SECTOR_RANGES:
        if lo <= n <= hi:
            return letter
    return None


def cnae_match_variants(cnae: str) -> list[str]:
    """Devuelve todas las cadenas que podrían aparecer en `cnae_elegible` y deberían
    matchear con `cnae`. Cubre prefijos numéricos, variantes con puntos, y la letra
    de sector CNAE-2009 (BDNS frecuentemente solo guarda la letra)."""
    variants: set[str] = set()
    if not cnae or not cnae.isdigit():
        return []
    for n in range(1, len(cnae) + 1):
        variants.add(cnae[:n])
    if len(cnae) >= 3:
        variants.add(f"{cnae[:2]}.{cnae[2:3]}")  # 6201 → '62.0'
    if len(cnae) >= 4:
        variants.add(f"{cnae[:2]}.{cnae[2:]}")   # 6201 → '62.01'
        variants.add(f"{cnae[:3]}.{cnae[3:]}")   # 6201 → '620.1'
    sector = _cnae_division_to_sector(cnae[:2])
    if sector:
        variants.add(sector)
    return list(variants)


def _compute_score(sub: Subvencion, perfil: EmpresaProfile) -> int:
    """Score determinista 0-100 basado en:
    - CNAE exacto: +40 ; CNAE prefijo/dotted: +30 ; sector letter: +20 ; vacío: +20
    - Finalidad solapada (cualquiera): +30
    - Cercanía a fecha_fin: hasta +20 (más cerca = más score)
    - Tamaño elegible: +10
    """
    score = 0

    cnae_list = sub.cnae_elegible or []
    if perfil.cnae in cnae_list:
        score += 40
    elif not cnae_list:
        score += 20
    else:
        variants = set(cnae_match_variants(perfil.cnae))
        sector = _cnae_division_to_sector(perfil.cnae[:2])
        if any(v in cnae_list for v in variants if v != sector and "." not in v):
            score += 30  # prefijo numérico ('62', '620')
        elif any("." in v and v in cnae_list for v in variants):
            score += 25  # dotted ('62.0', '62.01')
        elif sector and sector in cnae_list:
            score += 20  # solo letra de sector

    if set(perfil.finalidad) & set(sub.finalidad or []):
        score += 30

    if sub.fecha_fin:
        days_to_end = (sub.fecha_fin - date.today()).days
        if days_to_end >= 0:
            # Bonus de urgencia capado a +10 — antes era +20 y dominaba el ranking,
            # haciendo que records sin match temático subieran al Top 3 solo por cerrar pronto.
            urgency = max(0, 10 - (days_to_end // 14))
            score += min(10, urgency)

    benef = sub.beneficiarios or {}
    if perfil.tamano in benef.get("tamanos", []):
        score += 10

    return min(100, max(0, score))


def find_candidates(session: Session, perfil: EmpresaProfile, limit: int = 30) -> list[Candidate]:
    """Filtra y pre-rankea las subvenciones más relevantes para `perfil`.

    Filtros SQL aplicados:
    - fecha_fin >= hoy (o NULL) — fuente de verdad de "abierto", el flag `estado`
      de BDNS es poco fiable
    - cnae_elegible contiene el CNAE del perfil O está vacío
    - finalidad solapa con la del perfil (o es lenient: vacía / 'otros')
    - ámbito 'estatal' o 'ue' siempre; autonómico si CCAA coincide o organismo
      contiene keywords de la provincia; local si organismo contiene keywords
      de la provincia (evita polución cruzada de ayuntamientos lejanos).
    """
    # El flag BDNS `estado='abierta'` no es fiable (muchos records marcados 'cerrada'
    # siguen aceptando solicitudes). Usamos fecha_fin como fuente de verdad.
    today = date.today()
    stmt = select(Subvencion).where(
        (Subvencion.fecha_fin.is_(None)) | (Subvencion.fecha_fin >= today)
    )

    # Excluir records regulatorios (ordenanzas, reglamentos, bases reguladoras, decretos, leyes)
    # — son marcos legales, no convocatorias accionables.
    stmt = stmt.where(
        ~Subvencion.titulo.op("~*")(r"^(ordenanza|reglamento|bases reguladoras|real decreto|decreto|ley )")
    )

    # CNAE: matching jerárquico CNAE-2009. BDNS guarda cnae_elegible mezclando formatos:
    # letras de sector ('J' = "Información y comunicaciones", que incluye 6201), códigos
    # con puntos ('62.0', '62.01'), o prefijos numéricos puros ('6', '62', '620', '6201').
    # Generamos todas las variantes razonables a partir del CNAE del usuario y matcheamos
    # contra cualquiera. También aceptamos records sin cnae_elegible (wildcard genérico).
    cnae_variants = cnae_match_variants(perfil.cnae)
    stmt = stmt.where(
        (Subvencion.cnae_elegible.overlap(cnae_variants))
        | (func.cardinality(Subvencion.cnae_elegible) == 0)
    )

    # Finalidad: lenient — solapa con la del perfil, O record sin finalidad clasificada
    # (cardinality 0), O finalidad clasificada como ['otros'] (record genérico sin tema claro).
    # Los no-matches obtienen score bajo en _compute_score y caen al final del ranking.
    if perfil.finalidad:
        stmt = stmt.where(
            (Subvencion.finalidad.overlap(perfil.finalidad))
            | (func.cardinality(Subvencion.finalidad) == 0)
            | (Subvencion.finalidad.contains(["otros"]))
        )

    # Ámbito: estatal y UE siempre visibles. Autonómico y local se filtran por provincia
    # inferida del texto del organismo (BDNS no rellena CCAA/provincia estructurada en
    # records locales). Si el organismo menciona la provincia del usuario o su CCAA →
    # incluir; si menciona otra provincia → excluir. Records sin organismo se incluyen
    # como fallback conservador (perdemos algo de precisión pero ganamos recall).
    province_keywords = _PROVINCIA_KEYWORDS.get(perfil.provincia, [])
    organismo_matches_province = or_(
        Subvencion.organismo.is_(None),
        *[Subvencion.organismo.ilike(f"%{kw}%") for kw in province_keywords],
    ) if province_keywords else None

    ccaa = perfil.ccaa
    ambito_branches = [
        Subvencion.ambito == "estatal",
        Subvencion.ambito == "ue",
    ]

    if organismo_matches_province is not None:
        ambito_branches.append(
            and_(Subvencion.ambito == "local", organismo_matches_province)
        )
        if ccaa:
            ambito_branches.append(
                and_(
                    Subvencion.ambito == "autonomico",
                    or_(Subvencion.ccaa == ccaa, organismo_matches_province),
                )
            )
        else:
            ambito_branches.append(
                and_(Subvencion.ambito == "autonomico", organismo_matches_province)
            )
    else:
        # Sin keywords (provincia desconocida) — no filtramos por organismo.
        ambito_branches.append(Subvencion.ambito == "local")
        if ccaa:
            ambito_branches.append(
                and_(Subvencion.ambito == "autonomico", Subvencion.ccaa == ccaa)
            )

    stmt = stmt.where(or_(*ambito_branches))

    rows = session.execute(stmt.limit(500)).scalars().all()

    # Post-filtro: excluir records cuyo organismo o título mencione explícitamente OTRA
    # provincia con el patrón "DE/EN/PARA <provincia>". Cubre el caso de records con
    # ambito='estatal' que en realidad son programas locales de Cámaras de Comercio.
    other_distinctive: set[str] = set()
    for prov_code, kws in _PROVINCIA_DISTINCTIVE.items():
        if prov_code != perfil.provincia:
            other_distinctive.update(kws)
    user_keywords = _PROVINCIA_KEYWORDS.get(perfil.provincia, [])

    filtered_rows = [
        sub for sub in rows
        if not _mentions_other_province(sub.organismo, other_distinctive)
        and not _mentions_other_province(sub.titulo, other_distinctive)
        and not _is_local_org_other_province(sub.organismo, user_keywords)
    ]

    candidates = [Candidate(subvencion=sub, score=_compute_score(sub, perfil)) for sub in filtered_rows]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
