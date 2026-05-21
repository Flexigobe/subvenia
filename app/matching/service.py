"""Servicio de matching: filter + analyzer + LLM Gemini.

Pipeline (todas las capas se ejecutan en cada búsqueda):
  1. SQL filter (filter.py): ~163.000 → 300 candidatos por CNAE compatible,
     ámbito geográfico, estado=abierta.
  2. Analyzer determinista (analyzer.py): añade match_reasons / exclusion_reasons
     basadas en heurística (regiones NUTS, tipos beneficiarios, patrones título).
     NO descarta — solo etiqueta como pista para el LLM.
  3. LLM Gemini (scorer_llm.py): lee descripción completa + bases reguladoras +
     condiciones de TODOS los 300 candidatos. Veredicto final con confidence.
     Solo aplicable si confidence >= 70.
  4. Ranking: aplicables primero por score desc, descartadas con motivo al final.

El LLM decide TODO el veredicto final. El analyzer regex es solo "asesor" del LLM.
Si Gemini no está disponible → fallback determinista (analyzer decide).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion
from app.matching.analyzer import Analysis, analyze
from app.matching.filter import Candidate, EmpresaProfile, find_candidates
from app.matching.scorer_llm import score_batch as llm_score_batch


@dataclass(frozen=True)
class RankedResult:
    subvencion: Subvencion
    score: int
    razon: str | None
    rank: int
    applicable: bool = True
    match_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    urgency_days: int = -1


def _compose_razon(analysis: Analysis) -> str | None:
    """Construye una razón humana corta combinando los motivos del análisis."""
    if not analysis.applicable and analysis.exclusion_reasons:
        return "❌ " + analysis.exclusion_reasons[0]
    if analysis.match_reasons:
        top = analysis.match_reasons[:2]
        return " · ".join(top)
    return None


# ────────────────────────────────────────────────────────────────────────
# POST-LLM BLACKLIST — para casos donde el LLM da falsos positivos obvios
# ────────────────────────────────────────────────────────────────────────
import re as _re

# Patrones que NUNCA aplican a empresas privadas con CNAE comercial/industrial.
# Si el TÍTULO o ORGANISMO de la subvención contiene cualquiera de estos
# patrones, forzamos applicable=false sin importar lo que diga el LLM.
_POST_LLM_BLACKLIST_PATTERNS = [
    # Becas, premios, ayudas individuales
    (r"\berasmus\b", "Erasmus+ es para movilidad estudiantil/docente, no para empresas comerciales"),
    (r"\bbeca\b(?!\s+excelencia\s+empresarial)", "Es una beca para personas físicas (estudiantes / investigadores)"),
    (r"\bbecas?\b", "Es una beca para personas físicas"),
    (r"\bbeatriz\s+galindo\b", "Programa Beatriz Galindo: solo investigadores universitarios"),
    (r"\bram[oó]n\s+y\s+cajal\b", "Programa Ramón y Cajal: solo investigadores"),
    (r"\bjuan\s+de\s+la\s+cierva\b", "Programa Juan de la Cierva: solo investigadores"),
    (r"\bmsca\s+(staff\s+exchanges|fellowships|doctoral\s+networks)\b", "MSCA es para movilidad de investigadores, no para empresas"),
    # Cooperación internacional
    (r"\beuropeaid\b", "EuropeAid es cooperación al desarrollo en países terceros"),
    (r"\btown\s+twinning\b", "Hermanamiento de ciudades — solo para municipios"),
    (r"\b(sri\s+lanka|maldivas|maldives|bosnia|tajikistan|cabo\s+verde|cape\s+verde)\b", "Cooperación al desarrollo en país tercero"),
    (r"\b(african\s+union|south\s+asia|asean|mena\s+region|sahel|caucasus|western\s+balkans)\b", "Cooperación al desarrollo regional"),
    (r"\bhuman\s+rights\s+(and|in|democracy)", "Programa de derechos humanos, no para empresas comerciales"),
    # ONGs / fundaciones / sindicatos
    (r"\bfundaci[oó]n\s+biodiversidad\b", "Fundación Biodiversidad: solo para ONGs medioambientales"),
    (r"\bsindicat\w*\b|\bmesa\s+(sectorial|negociaci[oó]n)\b", "Sindicato / mesa negociación: no para empresas"),
    # Conciertos / convenios nominativos
    (r"\bconvenio\s+nominativo\b", "Convenio nominativo a entidad específica"),
    (r"\bconcesi[oó]n\s+directa\b", "Concesión directa a entidad específica"),
    # Procurement / licitaciones (no son subvenciones)
    (r"\bvendors?\s+list\b|\bpre-?information\s+notice\b|\btender\s+", "Es un contrato público / licitación, no subvención"),
    # Deportistas / atletas individuales (becas disfrazadas)
    (r"\bdeportistas?\s+(individuales?|de\s+[eé]lite|j[oó]venes?|aficionad)", "Ayudas a deportistas individuales (no empresas)"),
    (r"\batletas?\s+(individuales?|de\s+[eé]lite)", "Ayudas a atletas individuales"),
    (r"\b(formaci[oó]n\s+no\s+acad[eé]mica|t[eé]cnicos?)\s+deportivos?\b", "Formación de técnicos deportivos individuales"),
    # Personas individuales con nombre propio (convenios nominativos)
    (r"\ba\s+la\s+(deportista|atleta|investigadora?|estudiante|persona)\s+[A-Z]", "Convenio nominativo a persona física"),
    # Clubes deportivos amateurs
    (r"\bclub\s+(de\s+)?(f[uú]tbol|baloncesto|tenis|padel|balonmano|deportiv\w+)\s+(aficionado|amateur|infantil|juvenil)", "Club deportivo aficionado/amateur"),
    (r"\bclub\s+deportivo\s+(amateur|aficionado)", "Club deportivo amateur"),
    # Premios artísticos/culturales individuales
    (r"\bpremio\s+(literario|de\s+pintura|de\s+narrativa|de\s+poes[ií]a|de\s+fotograf[ií]a|de\s+novela|de\s+ensayo)", "Premio artístico individual"),
    # Cofradías religiosas
    (r"\bcofrad[ií]a\b|\bhermandad\b|\bparroquia\b", "Entidad religiosa, no empresa"),
    # Casa real / fiestas patronales / festejos populares
    (r"\bfiestas\s+(patronales?|populares?|locales?)\b", "Fiestas locales, no para empresas"),
    # Toros / festejos taurinos
    (r"\b(festejos?\s+taurinos?|encierros?|corridas?\s+de\s+toros)\b", "Festejos taurinos locales"),

    # ════════════════════════════════════════════════════════════════════
    # Añadidos tras AUDIT exhaustivo — detectados como falsos positivos en
    # 35 perfiles de empresa testeados contra 13k subvenciones reales
    # ════════════════════════════════════════════════════════════════════

    # Convenios con asociación CONCRETA (no convocatoria abierta)
    # Patrón: "Convenio con [La/El] Asociación [Nombre]" o variantes
    (r"\bconvenio\s+(con|amb)\s+(la\s+|el\s+|l[ae]s?\s+)?(asociaci[oó]n|associaci[oó]|fundaci[oó]n|federaci[oó]n|federaci[oó]|club|peña|penya|hermandad|cofrad[ií]a|orden|colegi[oa]?|orfe[oó]n|coral|coro|banda|grupo|patronato|comisi[oó]n)\b",
     "Convenio nominativo con asociación/club/fundación concreta"),
    # Convenio + nombre propio (entidad con mayúsculas múltiples)
    (r"\bconveni[oó]?\s+con\s+(la\s+|el\s+)?[A-ZÑÁÉÍÓÚ][A-Za-zñáéíóú\.]+\s+[A-ZÑÁÉÍÓÚ][A-Za-zñáéíóú\.]+", "Convenio con entidad concreta nominal"),

    # Cooperación al desarrollo / proyectos internacionales
    (r"\b(cooperaci[oó]n\s+al\s+desarrollo|responsabilidad\s+social\s+y\s+cooperaci)", "Cooperación al desarrollo / RSC en países terceros"),
    (r"\b(consultor[ií]a|ayudas?)\s+(en\s+especie\s+)?(para\s+|en\s+)?[áa]frica\b", "Servicios/ayudas en África"),
    (r"\bafrica-?(eu|ue)\s+co-?fund|\beu-?african\s+union", "Cooperación EU-África"),
    (r"\bsmart\s+(climate|agriculture)\s+in\s+africa", "Agro inteligente en África"),

    # Becas doctorales / estancias en centros extranjeros
    (r"\b(estancias?\s+externas?|estancias?\s+(en\s+|de\s+investigaci[oó]n\s+en\s+))centros?\s+extranjeros?", "Estancias en centros extranjeros (investigadores)"),
    (r"\bayudas?\s+(para\s+(la\s+)?realizaci[oó]n\s+de\s+)?estancias?", "Estancias formativas"),
    (r"\bbecas?\s+predoctoral|\bbeca\s+predoctoral|\bayudas?\s+predoctoral", "Becas predoctorales"),
    (r"\bayudas?\s+postdoctoral|\bcontratos?\s+postdoctoral", "Postdoctorales"),
    (r"\bestudiantes?\s+(de\s+)?(doctorad|posgrad|master|m[aá]ster)", "Estudiantes posgrado"),

    # Mujeres víctimas violencia (ayudas individuales)
    (r"\bmujeres?\s+v[ií]ctimas?\s+(de\s+)?violencia", "Ayudas mujeres víctimas violencia género"),
    (r"\bviolencia\s+(de\s+g[eé]nero|contra\s+las\s+mujeres)", "Violencia género (ayudas individuales)"),

    # Subvención directa A AYUNTAMIENTO concreto
    (r"\bsubvenci[oó]n\s+(directa\s+|nominativa\s+)?al?\s+ayuntamiento\s+de\s+[a-záéíóúñ]", "Subvención directa a ayuntamiento concreto"),

    # Concesión a fundación/asociación con nombre propio
    (r"\bsubvenci[oó]n\s+(directa\s+)?(a\s+favor\s+(de\s+)?la?\s+|a\s+la?\s+)(fundaci[oó]n|asociaci[oó]n)\s+[A-Z]", "Subvención a fundación/asociación nominal"),

    # Decretos forales internos (Diputación Foral concreta)
    (r"\bdecreto\s+foral\s+\d", "Decreto foral interno"),

    # Convocatoria con códigos internos (suelen ser nominativas)
    (r"\bxii+\s+convocatoria|\bxii\s+convocatoria", "Convocatoria de antigüedad (revisar manualmente)"),

    # Servicios sociales / responsabilidad social
    (r"\bservicios\s+sociales?\s+(para\s+)?(ayuntamientos?|eatims?|entidades\s+locales)", "Servicios sociales para ayuntamientos"),

    # Resoluciones SG Salud Pública I+D+i
    (r"\bs\.?g\.?\s+(de\s+)?salud\s+p[uú]blica\s+e\s+i\+d\+i", "Investigación sanitaria pública"),

    # Acuerdo JGL / Junta de Gobierno Local
    (r"\bacuerdo\s+(de\s+)?(la\s+)?junta\s+de\s+gobierno\s+local|\bjgl\s+de\s+fecha", "Acuerdo interno municipal"),

    # iCapital Award y premios europeos a ciudades / regiones / instituciones
    (r"\b(european\s+capital\s+of\s+innovation|icapital|capital\s+europea\s+de\s+(la\s+)?innovaci)", "European Capital of Innovation: premio a ciudades, no empresas"),
    (r"\b(rising\s+innovator\s+(award|category)|innovation\s+award\s+category)", "Premio a ciudades/regiones innovadoras"),
    (r"\bhorizon[\s\-]+eic[\s\-]+\d{4}[\s\-]+(prize|icapital|award)", "Premio honorífico Horizon EIC"),

    # Premios institucionales nacionales (educación, ciencia)
    (r"\bpremios?\s+nacionales?\s+de\s+(innovaci[oó]n|dise[ñn]o|investigaci[oó]n|cultura|deportes|f[ií]sica|qu[ií]mica)", "Premio nacional honorífico (no para PYMEs)"),
    (r"\bpremio\s+rey\s+jaime", "Premios Rey Jaime I (investigación universitaria)"),

    # Awards para hospitales / centros educativos / instituciones
    (r"\b(hospital|university|school)\s+(award|prize)\b", "Award para institución educativa/sanitaria"),

    # Convocatorias específicas a ciudades / regiones (urbanismo)
    (r"\b(cities\s+and\s+regions|living\s+labs?\s+in\s+cities|circular\s+(cities|systemic))", "Para ciudades/regiones, no empresas privadas"),
    (r"\b(cities?\s+living\s+lab|urban\s+living\s+lab|community-?led)", "Iniciativa urbana / regional"),

    # Centros formación profesional / educación adultos
    (r"\b(centres?\s+of\s+vocational\s+excellence|vocational\s+education|adult\s+(education|learning))", "Centro formación profesional / educación adultos"),
    (r"\b(centro\s+de\s+formaci[oó]n\s+profesional|fp\s+excellence|escuelas?\s+t[eé]cnicas?)", "Centro formación profesional"),

    # Horizon Europe convocatorias específicas detectadas como falsos positivos
    (r"\b(persons\s+with\s+disabilities|social\s+protection\s+through\s+life)", "Convocatoria sobre discapacidad / protección social"),
    (r"\b(social\s+transformations?\s+(and\s+resilience)?|inclusive\s+societ|cohesion\s+and\s+ineq)", "Convocatoria transformaciones sociales"),
    (r"\b(one\s+health\s+approach|ecosystem\s+health|wild\s+species\s+health|emerging\s+stressors)", "Convocatoria One Health / ecosistemas (biomédica)"),
    (r"\bmicrobiome\b|\bvirome\b|\bgenom", "Microbioma / genómica (biotech)"),
    (r"\bdestination\s+earth\b|\bdigital\s+twin\s+earth\b", "Destination Earth (gemelo digital tierra)"),
    (r"\b(tandem\s+(technology|technologies|solar|photovoltaic)|tandem\s+pv|eupi-?pv|silicon\s+heterojunction)\b", "Tecnología fotovoltaica avanzada I+D"),
    (r"\bfarmers?[\s']+(profitability|resilience|sustainability)\b|enhancing\s+farmers?", "Agricultura sostenible para agricultores"),
    (r"\bdiversified\s+crops?\b|\bcrop\s+rotation\b|\bvalue\s+chains?\s+(agri|food)", "Cadena de valor agroalimentaria"),
    (r"\bliving\s+labs?\s+(driving|for|transformative|in|social)", "Living labs (investigación participativa)"),
    (r"\bairport\s+operations\b|\bnoise\s+in\s+(nearby|communities)", "Operaciones aeropuertos"),
    (r"\b(academic\s+intellectual\s+assets|university\s+industry\s+transfer)", "Transferencia académica universidad-industria"),
    (r"\b(knowledge\s+integration|inclusive\s+governance|community-?led\s+research)", "Investigación participativa académica"),

    # MSCA / talent específicos
    (r"\bmsca\s+(staff|doctoral|postdoctoral|cofund|fellowships)", "MSCA — investigadores universitarios"),
    (r"\bcofund\s+(programme|action)|\bco-?fund\s+programme", "Cofund — investigación universitaria"),

    # EIC / EIT específicos (NO accelerator que sí aplica a empresas)
    (r"\beit\s+(food|raw\s+materials|health|manufacturing|innoenergy)\s+business\s+plan", "EIT business plan (consorcio I+D)"),
    (r"\binnonext\s+|\beit\s+communit", "EIT community (no PYMEs sueltas)"),

    # Aeronáutica / aeroespacial específico
    (r"\b(aeronaut|aerospace)\s+(research|innovation|partnership)", "Aeronáutica/aeroespacial I+D"),
    (r"\bclean\s+aviation\b|\bclean\s+sky\b", "Clean Aviation (aeroespacial)"),
    (r"\bzero\s+emission\s+ship|\bmaritime\s+green\b", "Naval / marítimo I+D"),

    # Pillar IV NDICI etc
    (r"\bndici\s+|\bglobal\s+europe\s+thematic", "NDICI Global Europe (cooperación al desarrollo)"),

    # Palestina / Israel / Líbano / Siria / Irán / Turquía / Yemen / Egipto
    (r"\b(palestina|palestine|palestinian|israel|gaza|cisjordania|west\s+bank)", "Cooperación Oriente Medio"),
    (r"\b(l[ií]bano|lebanon|siria|syria|ir[áa]n|iran|t[uú]rqu[ií]a|turkey|yemen|egipto|egypt)", "Cooperación Oriente Medio/Norte África"),
    (r"\byesh\s+din|\boxfam\b|\bsave\s+the\s+children", "ONGs internacionales específicas"),

    # Activa Startups (es realmente para startups muy innovadoras, no comercio fontanería)
    (r"\bactiva\s+startups\b", "Activa Startups (startups deeptech)"),

    # Premios empresas locales municipales
    (r"\bpremios?\s+a\s+empresas\s+de\s+\w+|\bpremios?\s+empresariales?\s+municipal", "Premios municipales a empresas locales"),

    # Más Horizon Europe genéricos detectados
    (r"\bcivic\s+space\b|\benabling\s+civic\b|\bcivic\s+engagement", "Espacio cívico (derechos sociales)"),
    (r"\barctic\s+communities?|\bindigenous\s+(peoples?|communities)|\barctic\s+(zone|region)", "Comunidades árticas / pueblos indígenas"),
    (r"\bera\s+fellowships?|\bera\s+talents?\b", "ERA Fellowships (investigadores universitarios)"),
    (r"\beu\s+charter\s+of\s+fundamental\s+rights|\bfundamental\s+rights\s+charter", "Derechos fundamentales UE"),
    (r"\bawareness\s+(raising\s+)?(about|of)\s+(the\s+)?(arctic|indigenous|fundamental\s+rights|civic|democratic)", "Concienciación derechos / minorías"),
    (r"\binvolvement\s+of\s+(philanthropic|civil\s+society)|\bcivil\s+society\s+(organisations?|enga)", "Sociedad civil / filantrópicas"),

    # I+D fundamental (no aplicable a comercio)
    (r"\bera\s+chairs?|\beuropean\s+research\s+area", "ERA — área investigación europea"),
    (r"\bera-?net\s+cofund\b|\berc\s+(grant|advanced|starting|consolidator)", "ERC / ERA-NET (investigación)"),

    # Digital skills sectoriales específicas
    (r"\b(digital\s+skills?|advanced\s+(digital\s+)?skills?)\s+.*?(health|healthcare|medical|biomed)", "Digital skills para sanidad (sector salud)"),
    (r"\bai\s+(uptake|adoption|deployment)\s+(in|for)\s+(health|healthcare|medical|biomed)", "IA en sanidad"),

    # Ronda final — Horizon Europe falsos positivos detectados en GOBE
    (r"\bpyme[s]?\s+tur[ií]stic|sostenibilidad\s+pyme\s+tur", "PYMES turísticas (solo sector turismo)"),
    (r"(restaurar|restoraci[oó]n|recuperar)\s+(nuestros?\s+)?(oc[eé]anos?|mares?|aguas|marine|seas)", "Restauración océanos/mares (medio ambiente marino)"),
    (r"\bcomunidad\s+(para|para\s+)?(restaurar|recuperar)\b", "Restauración liderada por comunidad"),
    (r"\beye\s+(2026|2027|2028)\b|\beuropean\s+youth\s+event", "EYE European Youth Event (jóvenes)"),
    (r"\bmusic\s+programme\b|\bmusic\s+festival\s+programme", "Programa musical (cultura)"),
    (r"\bvalles\s+regionales\s+de\s+innovaci[oó]n|regional\s+innovation\s+valleys?", "Valles regionales innovación (consorcios regionales)"),
    (r"\bbio-?based\s+(pilots?|demonstration|industry|solutions?)|\bbioeconomy\b", "Bio-economía / pilotos bio (sector específico)"),
    (r"\b(salud|health)\s+del?\s+suelo|\bsoil\s+health\b", "Salud del suelo (agroambiental)"),
    (r"\btransformaci[oó]n\s+(ecol[oó]gica\s+y\s+digital)?\s+del?\s+(ecosistema|sistema)\s+energ[eé]tic|energy\s+ecosystem\s+resilien", "Transformación ecosistema energético (energía I+D)"),
    (r"\bai\s+data\s+(and\s+)?robotics\s+(partnership|boosting)|\b(industrial\s+leadership\s+in\s+)?ai,?\s+data\s+(and\s+)?robotics", "AI Data Robotics Partnership (IA industrial)"),
    (r"\bborn-?digital\s+heritage|\bdigital\s+cultural\s+heritage", "Patrimonio digital (museos/archivos)"),
    (r"\b(ethics|regulatory|pharmacovigilance|drug\s+safety)\s+(networks?|training|capacity)", "Farmacovigilancia / regulatorio (sanidad)"),
    (r"\btechnology\s+transfer\s+offices?|\bttos?\b\s+(strengthening|university)", "Oficinas Transferencia Tecnológica (universidades)"),
    (r"\bfrom\s+lab\s+to\s+market\b", "From lab to market (universidad-mercado)"),
    (r"\bsilos?\s+to\s+diversit|\bdiversification\s+strategies", "Diversificación agroalimentaria (agro)"),
    (r"\bsmall-?scale\s+(bio|agro|farming|food)\s+", "Pilotos pequeña escala agro/bio"),
    (r"\bsmes\s+and\s+startups\b|\bsmes\s+(working\s+with|involving)\s+academ", "Academia-SMEs colaboración (consorcios)"),
    (r"\bcapacity\s+development\s+related\s+to\b|\btraining\s+and\s+innovation\s+networks", "Capacity development / training networks (universidades)"),

    # Apoyo / convocatorias sectoriales adicionales detectadas
    (r"\bapoyo\s+a\s+la\s+competitividad\s+sostenible\s+de\s+las\s+pyme[s]?\s+(tur[ií]stic|agrar|industri|cultur)", "Apoyo PYMES sector específico (no comercio)"),

    # Mancomunidades concretas con nombre
    (r"\bsubvenci[oó]n\s+nominativa\s+(a\s+la\s+)?mancomunidad\b", "Subvención a mancomunidad concreta"),

    # ════════════════════════════════════════════════════════════════════
    # Ronda 4 — patrones de fugas detectados en test_matching_exhaustivo 35 perfiles
    # ════════════════════════════════════════════════════════════════════

    # ONG / fiestas / cofradías / hermandades (no son empresas)
    (r"\bcomisi[oó]n\s+de\s+fiesta", "Comisión de fiestas locales"),
    (r"\bpe[ñn]a\s+(flamenca|cultural|deportiva|taurina|recreativa)\b", "Peña local cultural/recreativa"),
    (r"\bsubv\.?\s+(directa\s+)?(asoc\.?|cofrad|hermand|peña|fundaci|federaci|club)\b", "Subvención directa a asoc/cofradía/peña"),
    (r"\b(corales?|orfe[oó]n|bandas?\s+de\s+m[uú]sica|grupo\s+de\s+danza|grupo\s+folk)\b", "Asociación musical/folclórica"),
    (r"\brep\.?\s+y\s+conservaci[oó]n\s+(de\s+)?templo|conservaci[oó]n\s+de\s+iglesi", "Templos/iglesias (patrimonio religioso)"),
    (r"\bromer[ií]a[s]?\b", "Romerías (festividades religiosas)"),

    # Discapacidad — ayudas individuales a personas
    (r"\bpersonas?\s+con\s+discapacid", "Ayudas a personas con discapacidad (individuales o entidades sociales)"),
    (r"\b(adaptaci[oó]n\s+(del\s+)?puesto\s+de\s+trabajo|empleo\s+protegido|centros?\s+especiales?\s+de\s+empleo|cee\b)", "Adaptación discapacidad / CEE"),
    (r"\bfomento\s+(de\s+)?empleo\s+(de\s+)?personas?\s+con\s+discapacid", "Fomento empleo discapacidad"),
    (r"\b(autismo|s[íi]ndrome\s+de\s+down|par[áa]lisis\s+cerebral|esclerosis|alzheimer|p[áa]rkinson)\b", "Discapacidad específica (asociaciones de pacientes)"),
    (r"\bsalud\s+mental\s+(la\s+|de\s+)", "Asociaciones salud mental (locales)"),

    # Natalidad / conciliación / RSC
    (r"\bnatalidad\b", "Ayudas a la natalidad (personas físicas)"),
    (r"\bconciliaci[oó]n\b", "Programas de conciliación (no empresa privada comercial)"),
    (r"\brespons\w*\.?\s*social\b", "Responsabilidad Social (programas públicos)"),
    (r"\brsc\b", "Responsabilidad Social Corporativa (programa público)"),
    (r"\bfamilias?\s+(numerosas?|monoparental|en\s+riesgo)", "Familias numerosas/vulnerables"),
    # Subvención directa con nombre propio (fundación/asociación/etc identificada)
    (r"\bsubv\.?\s+directa\b", "Subvención directa nominativa"),
    (r"\bfundaci[oó]n\s+[a-záéíóúñ][a-záéíóúñ]+", "Subvención a fundación nominal"),

    # Civic rights / civic space / human rights / democracia
    (r"\bcivic\s+space\b|\bcivic\s+engagement\b|\benabling\s+civic", "Espacio cívico (no empresa)"),
    (r"\bfundamental\s+rights\b|\bcharter\s+of\s+fundamental", "Derechos fundamentales UE"),
    (r"\b(eu\s+external\s+borders|external\s+border\s+management)", "Gestión fronteras UE (no empresa privada)"),
    (r"\bopen\s+topic\s+on\s+research\s+and\s+innovation", "Open topic R&I (consorcios académicos)"),
    (r"\b(awareness[\s\-]raising|community[\s\-]building|grassroots)", "Concienciación / movimiento comunitario"),

    # Horizon biomédico / cáncer / oncología (sólo biomédicas)
    (r"\bcancer\b", "Cáncer/oncología (investigación biomédica)"),
    (r"\bonco(?:log|metr)", "Oncología (investigación biomédica)"),
    (r"\b(equitable\s+health\s+outcomes|added\s+value\s+for\s+(cancer\s+)?patients?)", "Resultados sanitarios"),
    (r"\bcarcinogenic\s+substances?\b|\bliving\s+labs?\s+to\s+(monitor|mitigate)", "Sustancias carcinógenas (sanidad pública)"),
    (r"\bbiomarkers?\b", "Biomarcadores (investigación clínica)"),
    (r"\bfirst-?in-?human\s+(clinical\s+)?trials?\b|\bphase\s+1\s+(clinical\s+)?trial|\bclinical\s+(trial|research)", "Ensayos / investigación clínica"),
    (r"\b(tumour|tumor)s?\b", "Tumores (oncología)"),
    (r"\b(flavivirus|antimicrobial\s+resistance|amr|antibody|antibiotic)\b", "Microbiología / antimicrobianos"),
    (r"\b(multiple\s+sclerosis|myeloma|hematology|haematology)\b", "Hematología / esclerosis"),
    (r"\brare\s+diseases?\b|\berdera\b", "Enfermedades raras"),
    (r"\bdisease\s+(progression|response|treatment|prevention|management|surveillance)", "Enfermedad clínica"),
    (r"\bcomprehensive\s+cancer\s+infrastructure", "Infraestructuras oncológicas"),
    (r"\bpatient\s+(care|outcomes?|stratification|cohort|safety|consent)", "Atención al paciente (sanidad)"),
    (r"\bpredictive\s+biomarkers?\b", "Biomarcadores predictivos"),
    (r"\beuropean\s+partnership\s+on\s+(rare|cancer|health|brain|neurodeg)", "Partnerships sanitarios Horizon"),

    # Horizon defensa / militar
    (r"\bover-?the-?horizon\s+(sensing|radar|detection)", "Sensores radar de largo alcance (defensa)"),
    (r"\bseaps?\s+(\(|\b)|\bsingle\s+european\s+act|\b(?:functioning\s+of\s+)?seaps?\b", "SEAP defensa europea"),
    (r"\b(air|missile)\s+(and\s+missile\s+)?defen[cs]e\s+systems?", "Sistema defensa anti-misil"),
    (r"\b(naval\s+systems|tank\s+modernisation|munition|warhead|warship)", "Sistemas militares"),
    (r"\beuropean\s+defen[cs]e\s+(fund|industry|programme)", "Industria defensa europea"),

    # Horizon clima marino / océanos / Antártida
    (r"\bantarct(ic|ica)\b|\bsouthern\s+ocean\b", "Antártida / océano austral (investigación)"),
    (r"\b(carbon\s+sources?\s+and\s+sinks?|ocean\s+carbon|land\s+(use\s+)?carbon)", "Ciclo carbono terrestre/oceánico"),
    (r"\bseafood\s+supply\s+chain\b|\bcircular(ity)?\s+(of\s+)?seafood", "Acuicultura / circularidad seafood"),
    (r"\bcoral\s+reef\b|\bmarine\s+biodiv|\bmarine\s+protect", "Arrecifes / biodiversidad marina"),
    (r"\bclimate\s+(adaptation|resilien|mitigation)\b", "Adaptación / mitigación climática (consorcios públicos)"),
    (r"\b(coastal|riparian|freshwater|estuar|wetland)\s+(areas?\s+)?(resilien|sustain|tourism|restoration|protect|management)", "Áreas costeras/ribereñas (medioambiental)"),
    (r"\bcoastal\s+(and\s+freshwaters?\s+)?sustainable\s+tourism", "Turismo sostenible costero"),
    (r"\bwaterfront\s+(cities|areas)", "Waterfront ciudades costeras"),
    (r"\bcommunity-?driven\s+business\s+models?\b", "Modelos negocio comunitarios"),

    # Horizon AI health / digital health skills
    (r"\beehrxf\b|\belectronic\s+health\s+records?\s+(exchange|format)", "EEHRxF intercambio expedientes salud"),
    (r"\bvirtual\s+human\s+twins?\b|\bvhts?\b\s+(for|in)", "Virtual Human Twins (gemelos digitales sanidad)"),
    (r"\bdigital\s+health\s+(services?\s+and\s+systems?|infrastructure)", "Sistemas salud digital (sector salud)"),
    (r"\b(capacity\s+to\s+deploy|deploy.*?digital\s+health)", "Despliegue infraestructura salud digital"),
    (r"\bclinical\s+decision\s+support\b|\bdecision\s+support\s+in\s+(prevention|diagnos)", "Soporte decisión clínica"),
    (r"\bai\s+for\s+health\b|\bai\s+(uptake|deployment)\s+in\s+(health|healthcare)", "IA en sanidad"),

    # Horizon smart city / urban (extra patrones)
    (r"\burban\s+nature\b|\brestoration\s+of\s+urban\s+ecosystem", "Restauración naturaleza urbana"),
    (r"\bsmart\s+(region|cit|neighbourhood|districts?)", "Smart city/region (gestión territorial)"),
    (r"\bcircular\s+(cit|neighbourhood)", "Ciudad circular"),
    (r"\b(urban|city)\s+transport\s+networks?\b", "Redes transporte urbano"),

    # Horizon agro
    (r"\bcarbon\s+farming\b", "Carbon farming (agricultura sostenible)"),
    (r"\bcrop\s+rotation\b|\blivestock\s+(microbiome|sustainability)|\bsilvicultur", "Sistemas agrícolas / forestales sostenibles"),
    (r"\baquaculture\s+(sustainability|biodiversity)", "Acuicultura sostenible"),
    (r"\b(agroecolog|regenerative\s+(agriculture|farming))\b", "Agroecología / agricultura regenerativa"),

    # Otros que aún se filtran
    (r"\b(asociaciones?\s+sin\s+[áa]nimo\s+de\s+lucro|asoc\.\s+sin\s+[áa]nimo)", "Asociaciones sin ánimo de lucro"),
    (r"\b(consejo\s+(de\s+)?hermandades|junta\s+de\s+cofrad[ií]as)", "Consejo hermandades / junta cofradías"),
    (r"\bproyectos?\s+de\s+(igualdad|inclusi[oó]n|integraci[oó]n)\s+social", "Proyectos sociales (entidades sociales)"),

    # ════════════════════════════════════════════════════════════════════
    # POLÍTICA CERO FALSOS POSITIVOS — Horizon Europe "open topic" calls
    # de I+D fundamental que el LLM confunde con asociación de palabras.
    # Estas convocatorias requieren capacidad de I+D innovador (universidades,
    # startups deeptech, consorcios), NO son para PYMEs comerciales/industriales
    # tradicionales aunque el "tema" suene parecido.
    # ════════════════════════════════════════════════════════════════════

    # Water Resilience / I+D agua europea (no aplica a fontaneros/distribuidores)
    (r"\b(european\s+)?water\s+resilience\s+strategy", "EU Water Resilience Strategy: I+D hídrica para consorcios"),
    (r"\binnovative\s+solutions?\s+for\s+(the\s+)?(european\s+)?water", "I+D hídrica EU (no para comercio fontanería)"),
    (r"\bwater[\s\-](cycle|smart|industry)", "I+D hídrica EU"),

    # Energy / climate I+D (no para empresas no energéticas)
    (r"\b(climate\s+(adaptation|resilience|mitigation)\s+(strategy|research))", "I+D climática EU"),
    (r"\b(net[\s\-]?zero|carbon[\s\-]?neutral)\s+(industry|cities|emissions?)\s+(research|innovation)", "I+D descarbonización"),

    # Open topic / two-stage = típicamente I+D fundamental EU
    (r"^open\s+topic[:\s]", "Open topic Horizon: I+D fundamental (consorcios/universidades)"),
    (r"\btwo[\s\-]?stage\s+\(20\d{2}\)", "Convocatoria Horizon two-stage I+D"),

    # Eurostars / EU PYME Innovadora: requieren capacidad I+D real
    (r"\b(eurostars|asociaci[oó]n\s+europea\s+para\s+(las\s+)?pyme\s+innovador)", "Eurostars/EU PYME Innovadora: requiere proyecto I+D concreto"),
]


def _post_llm_blacklist_match(sub) -> str | None:
    """Devuelve un motivo de exclusión si la subvención matchea blacklist, None si no.

    Se llama TRAS el veredicto del LLM. Si retorna un motivo, fuerza applicable=false.
    """
    haystack = ((sub.titulo or "") + " " + (sub.organismo or "")).lower()
    for pattern, motivo in _POST_LLM_BLACKLIST_PATTERNS:
        if _re.search(pattern, haystack, _re.IGNORECASE):
            return motivo
    return None


# ════════════════════════════════════════════════════════════════════════
# SECTOR COMPATIBILITY MATRIX — análisis exhaustivo basado en CNAE-2009
# ════════════════════════════════════════════════════════════════════════
# Cada patrón en TITULO de la subvención mapea a una lista de PREFIJOS
# CNAE (2 dígitos = división, 3-4 = grupo/clase) compatibles. Si el CNAE
# del usuario NO empieza por ninguno de los prefijos → applicable=false.
#
# Cubrimos las 74 divisiones CNAE-2009 oficiales con sus palabras clave
# típicas en convocatorias BDNS reales y europeas.
# ════════════════════════════════════════════════════════════════════════

# ─── BLOQUE 1: Ferias ICEX sectoriales (la trampa más común) ───
_FERIAS_ICEX = [
    # Cuero, piel, calzado, marroquinería
    (r"\b(lineapelle|momad|micam|mipel|peleter[ií]a\b|cuero\b|calzado\s+(feria|show)|leather\s+show|footwear\s+(show|fair)|aec\s+calzado)",
     ["14", "15", "4641", "4642", "4772", "4669"],
     "Feria cuero/calzado/marroquinería"),
    # Cosmética, perfumería
    (r"\b(beauty\s*world|cosmoprof|stanpa|cosm[eé]tica\s+feria|perfumer[íi]a\s+feria|in-?cosmetics)",
     ["20", "2042", "4645", "4775", "9602"],
     "Feria cosmética/perfumería"),
    # Deporte, outdoor
    (r"\b(ispo|outdoor\s+(show|expo)|sport[s]?\s+(show|fair|expo)|fitness\s+(show|expo)|sports?\s+industry)",
     ["32", "3230", "4642", "4764", "93"],
     "Feria deporte/outdoor"),
    # Alimentación, bebidas
    (r"\b(alimentaria|foodex|anuga|sial\s+paris|fruit\s*attraction|fenavin|vinexpo|gulfood|prowein|barcelona\s+wine\s+week|sea\s+otter|salm[oó]n\s+expo)",
     ["01", "02", "03", "10", "11", "46", "47", "5610", "5630"],
     "Feria alimentación/bebidas"),
    # Turismo y MICE
    (r"\b(fitur|wtm\s+london|itb\s+berlin|imex|ibtm|feria\s+turismo|tourism\s+expo|travel\s+show)",
     ["49", "50", "51", "55", "56", "77", "79", "82", "90", "91"],
     "Feria turismo/MICE"),
    # Juguete
    (r"\b(expotoys|toys\s+(fair|show)|spielwarenmesse|juguete\s+feria|toy\s+industry)",
     ["32", "3240", "4665", "4765"],
     "Feria juguete"),
    # Automoción
    (r"\b(motortec|automechanika|equip\s+auto|autopromotec|automoci[oó]n\s+feria|automotive\s+(show|fair))",
     ["29", "30", "33", "45"],
     "Feria automoción"),
    # Joyería, óptica, relojería
    (r"\b(bijorhca|joyer[ií]a\s+feria|jewelry\s+(show|expo)|baselworld|mido\s+milan|inhorgenta|jck)",
     ["32", "4647", "4777"],
     "Feria joyería/óptica"),
    # Mueble, decoración, hábitat
    (r"\b(habitat\s+valencia|maison\s+et\s+objet|salone\s+del\s+mobile|imm\s+cologne|mueble\s+feria|interior\s+(design|fair))",
     ["16", "23", "25", "31", "47", "74"],
     "Feria mueble/decoración"),
    # Textil, moda, confección
    (r"\b(momad\s+metrop[oó]lis|texworld|premi[eè]re\s+vision|tranoi|the\s+brandery|moda\s+(barcelona|madrid)|fashion\s+(week|show)\s+(barcelona|madrid))",
     ["13", "14", "47"],
     "Feria textil/moda"),
    # Construcción, materiales
    (r"\b(batimat|construmat|rehabend|big\s+5|construction\s+(world|expo|show)|construtec)",
     ["23", "25", "41", "42", "43", "46"],
     "Feria construcción/materiales"),
    # Sanidad, farma
    (r"\b(arab\s+health|medica\s+d[uü]sseldorf|cphi|hospitalar|farmaforum|expoq[uí]mica\s+farma)",
     ["20", "21", "26", "32", "46", "47"],
     "Feria sanidad/farma"),
    # Industrial: maquinaria, manufactura
    (r"\b(hannover\s+messe|emo\s+milano|bauma|biemh\s+bilbao|midest|industrial\s+(show|fair))",
     ["24", "25", "26", "27", "28", "29", "30", "33"],
     "Feria industrial/maquinaria"),
    # Logística, transporte
    (r"\b(sil\s+barcelona|logimat|transport\s+logistic|trako)",
     ["49", "50", "51", "52", "53"],
     "Feria logística/transporte"),
    # Cerámica, baño (importante: incluye fontanería)
    (r"\b(cersaie|cevisama|ideobain|sha\s+show|bath\s+(fair|show))",
     ["23", "25", "4673", "4674", "47"],
     "Feria cerámica/baño/sanitarios"),
    # Tecnología, electrónica
    (r"\b(ces\s+las\s+vegas|mwc\s+barcelona|ifa\s+berlin|computex|electronica\s+m[uü]nich)",
     ["26", "27", "28", "61", "62", "63"],
     "Feria tecnología/electrónica"),
    # Audiovisual, broadcast
    (r"\b(ibc\s+amsterdam|nab\s+show|mip[ct]\b|broadcast\s+(expo|show))",
     ["26", "58", "59", "60", "61", "62", "73"],
     "Feria audiovisual/broadcast"),
    # Artes gráficas, impresión
    (r"\b(drupa|grafima|fespa|graphispag|print\s+(expo|fair))",
     ["17", "18", "28", "47"],
     "Feria artes gráficas/impresión"),
    # Editorial, libros
    (r"\b(frankfurt\s+book\s+fair|liber\s+madrid|book\s+expo|feria\s+libro\s+(madrid|guadalajara))",
     ["18", "47", "58", "63", "74", "85"],
     "Feria editorial/libros"),
    # Agricultura, ganadería
    (r"\b(fima|agritechnica|sima\s+paris|salima|expoaviga|smopyc|agro\s+(expo|fair))",
     ["01", "02", "03", "10", "20", "28", "46", "47"],
     "Feria agro/ganadería"),
    # Energía renovable
    (r"\b(genera\s+ifema|intersolar|wind\s+energy\s+(expo|hamburg)|hydrogen\s+expo)",
     ["27", "28", "33", "35", "42", "43", "71", "72"],
     "Feria energía renovable"),
]

# ─── BLOQUE 2: Horizon Europe — temas científicos específicos ───
_HORIZON_TEMAS = [
    # Oncología / sanidad / enfermedades
    (r"\b(cancer|onco|oncolog|carcinoma|tumour|tumor|chronic\s+(non-?communicable\s+)?disease|rare\s+disease|biomarker|clinical\s+trial|drug\s+development|patient(?:\s+|-))",
     ["20", "21", "26", "32", "62", "72", "86", "87"],
     "Convocatoria biomédica/sanidad: empresas biomédicas/farma/IT-salud"),
    # Salud digital, healthtech
    (r"\b(digital\s+health|e-?health|telemedicine|health\s+(tech|data)|virtual\s+human\s+twin|clinical\s+decision\s+support)",
     ["20", "21", "26", "32", "62", "63", "72", "86"],
     "Convocatoria health-tech: empresas IT-salud/biomedicina"),
    # Clima, océanos, biodiversidad
    (r"\b(climate|antarctic|ocean(?:ograph|ic)?|biodiversity|ecosystem\s+restoration|nature-positive|carbon\s+(farming|capture)|ghg\s+emission|maladaptation|extreme\s+events)",
     ["01", "02", "03", "20", "35", "36", "37", "38", "39", "72", "74", "75", "86", "91"],
     "Convocatoria clima/medioambiente: empresas I+D científica"),
    # Defensa, militar
    (r"\b(missile|defense|defence|military|interception\s+system|munitions?|tank\s+modernisation|naval\s+systems)",
     ["20", "25", "26", "27", "28", "29", "30", "62", "63", "71", "72"],
     "Convocatoria defensa: empresas defensa/I+D"),
    # Ciberseguridad
    (r"\b(cybersecurity\s+(applications?|tools?)|cyber\s+(defense|attack|threat)|security\s+(operations|incident)|information\s+security)",
     ["26", "61", "62", "63", "72"],
     "Convocatoria ciberseguridad: empresas IT/cyber"),
    # Vivienda, construcción sostenible
    (r"\b(housing|built4people|construction\s+material|building\s+renovation|deep\s+renovation|sustainable\s+building|affordable\s+housing)",
     ["16", "17", "22", "23", "25", "27", "28", "35", "36", "37", "38", "41", "42", "43", "46", "68", "71"],
     "Convocatoria vivienda/construcción sostenible"),
    # Agricultura
    (r"\b(agriculture|farming|agric[oó]la|ganader[ií]a|aquaculture|food\s+system|forestry|crop|livestock|silvicultur)",
     ["01", "02", "03", "10", "11", "20", "28", "46", "72", "74", "75"],
     "Convocatoria agro/ganadería/forestal"),
    # IA, ML
    (r"\b(artificial\s+intelligence|generative\s+ai|machine\s+learning|deep\s+learning|neural\s+network|llm\b|foundation\s+model)",
     ["26", "27", "61", "62", "63", "71", "72"],
     "Convocatoria IA/ML: empresas IT/software/I+D"),
    # CCAM / vehículo conectado
    (r"\b(ccam|autonomous\s+driving|connected\s+vehicle|smart\s+mobility|vehicle\s+(safety|emissions?))",
     ["26", "27", "28", "29", "45", "61", "62", "63", "71", "72"],
     "Convocatoria movilidad conectada"),
    # Cultura, creatividad, sociedad
    (r"\b(philanthropic|cultural\s+heritage|creative\s+(economy|industries?)|democracy|crime|migration|inequalit|social\s+cohesion|labour\s+mobility)",
     ["58", "59", "60", "63", "72", "73", "74", "85", "88", "90", "91", "94"],
     "Convocatoria social/cultural"),
    # Talento / I+D fundamental
    (r"\bnext\s+generation\s+(innovation\s+)?talents?\b",
     ["62", "63", "71", "72", "73", "74", "85"],
     "Programa talento I+D"),
    # Urbanismo, smart cities, economía circular
    (r"\b(urban\s+ecosystem|circular\s+cit|circular\s+economy|smart\s+city|smart\s+region|neighbourhood|urban\s+manufacturing|living\s+labs?\s+cities|zero\s+pollution)",
     ["20", "22", "23", "24", "25", "27", "28", "29", "35", "36", "37", "38", "41", "42", "43", "46", "61", "62", "63", "71", "72", "74", "81"],
     "Convocatoria economía circular/urbanismo/smart city: empresas industriales/I+D/IT"),
    # Photonics, semiconductores
    (r"\b(photonic|semiconductor|chips?\s+act|quantum\s+(computing|technology))",
     ["26", "27", "62", "63", "72"],
     "Convocatoria fotónica/semiconductores"),
    # Baterías, energía, hidrógeno
    (r"\b(batteries|battery\s+technology|hydrogen\s+(renewable|production)|electric\s+vehicle\s+(charging|infrastructure))",
     ["20", "27", "28", "29", "35", "42", "43", "71", "72"],
     "Convocatoria baterías/H2/energía"),
    # EURES
    (r"\beures\b",
     ["78", "79", "84"],
     "EURES es para agencias de empleo"),
    # MSCA (movilidad investigadores)
    (r"\bmsca\b(?!.*pyme)",
     ["72", "85"],
     "MSCA es movilidad investigadores universitarios"),
    # Spin-in defence
    (r"\bspin-?in\s+edf\b|\beuropean\s+defence\s+fund\b",
     ["20", "25", "26", "27", "28", "29", "30", "62", "63", "71", "72"],
     "European Defence Fund: empresas defensa"),
    # Reservation: si no fuera economía empresarial: solo ONG
    (r"\b(town\s+twinning|hermanamiento\s+ciudades)\b",
     [],  # vacío = nadie aplica
     "Hermanamiento ciudades: solo municipios"),
]

_SECTOR_TITLE_PATTERNS: list[tuple[str, list[str], str]] = _FERIAS_ICEX + _HORIZON_TEMAS


def _sector_blacklist(sub, cnae: str) -> str | None:
    """Si el título indica un sector específico Y el CNAE del usuario NO está
    en la lista permitida → devuelve motivo de exclusión."""
    haystack = ((sub.titulo or "") + " " + (sub.organismo or "")).lower()
    for pattern, allowed_prefixes, motivo in _SECTOR_TITLE_PATTERNS:
        if _re.search(pattern, haystack, _re.IGNORECASE):
            # ¿El CNAE del usuario tiene alguno de los prefijos permitidos?
            cnae_str = (cnae or "").strip()
            for prefix in allowed_prefixes:
                if cnae_str.startswith(prefix):
                    return None  # OK, sector compatible
            # Ningún prefijo encaja
            return motivo
    return None


async def rank_for(
    session: Session,
    perfil: EmpresaProfile,
    limit: int = 30,
) -> list[RankedResult]:
    """Matching exhaustivo: TODOS los candidatos del SQL filter pasan por el LLM."""
    candidates: list[Candidate] = find_candidates(session, perfil, limit=300)

    # Capa 2 — analyzer determinista: añade pistas para el LLM, NO descarta.
    analyzed: list[tuple[Candidate, Analysis]] = []
    for c in candidates:
        a = analyze(c.subvencion, perfil)
        analyzed.append((c, a))

    settings = get_settings()
    use_llm = bool(settings.gemini_api_key) and len(analyzed) > 0

    # Pre-split: las que pasan analyzer (no son becas/convenios/concesiones obvias)
    # vs las claramente descartadas. El analyzer regex es 99% fiable para casos obvios.
    pre_aplicables: list[tuple[Candidate, Analysis]] = []
    pre_no_aplicables: list[tuple[Candidate, Analysis]] = []
    for c, a in analyzed:
        if not a.applicable:
            pre_no_aplicables.append((c, a))
            continue
        # PRE-LLM BLACKLIST: descartamos AQUÍ obvios falsos positivos
        # (becas, EuropeAid, Erasmus, sindicatos, EURES, etc.) y subvenciones
        # sectoriales incompatibles con el CNAE del usuario. Así el LLM solo
        # ve candidatos que ya pasaron filtros deterministas.
        blk = _post_llm_blacklist_match(c.subvencion)
        if not blk:
            blk = _sector_blacklist(c.subvencion, perfil.cnae)
        if blk:
            # Marcar como no aplicable con el motivo del blacklist
            a.applicable = False
            a.exclusion_reasons = [blk]
            a.match_reasons = []
            pre_no_aplicables.append((c, a))
            continue
        pre_aplicables.append((c, a))

    if use_llm and pre_aplicables:
        # ────────────────────────────────────────────────────────────
        # Capa 3 — Gemini analiza los pre_aplicables con descripción
        # completa + requisitos estructurados. El analyzer regex ya
        # descartó las obviedades (becas museos, convenios nominativos,
        # concesiones directas, etc.) — el LLM no las vuelve a evaluar.
        # ────────────────────────────────────────────────────────────
        llm_candidates_only = [c for c, _ in pre_aplicables]
        llm_results = await llm_score_batch(perfil, llm_candidates_only)

        final_results: list[RankedResult] = []
        # 1) Pre_aplicables evaluados por el LLM (veredicto final)
        for (c, a), llm_result in zip(pre_aplicables, llm_results):
            llm_score, llm_razon, llm_applicable, llm_confidence, llm_requisitos = llm_result

            # El LLM tiene la última palabra. Si dice no aplicable, no aplicable.
            # Si dice aplicable pero el analyzer regex tenía duda, el LLM gana.
            razon = llm_razon or _compose_razon(a)
            match_reasons = list(a.match_reasons)
            exclusion_reasons = list(a.exclusion_reasons)

            # POST-LLM BLACKLIST: aunque el LLM diga "aplicable=true", forzamos
            # false si la subvención matchea patrones obvios (becas, cooperación
            # internacional, ONGs, ferias sectoriales con CNAE incompatible).
            blacklist_reason = None
            if llm_applicable:
                blacklist_reason = _post_llm_blacklist_match(c.subvencion)
                if not blacklist_reason:
                    blacklist_reason = _sector_blacklist(c.subvencion, perfil.cnae)
                if blacklist_reason:
                    llm_applicable = False
                    llm_razon = blacklist_reason
                    llm_score = max(0, 25)  # score visible pero bajo

            if llm_applicable:
                # Aplicable según LLM — montamos match_reasons con razón LLM primera
                if llm_razon:
                    match_reasons = [llm_razon] + list(llm_requisitos) + [
                        r for r in match_reasons if r != llm_razon
                    ]
                else:
                    match_reasons = list(llm_requisitos) + match_reasons
                exclusion_reasons = []  # LLM la valida — ignoramos warnings del regex
            else:
                # No aplicable según LLM — montamos exclusion_reasons
                if llm_razon:
                    exclusion_reasons = [llm_razon] + list(llm_requisitos) + [
                        r for r in exclusion_reasons if r != llm_razon
                    ]
                elif llm_confidence > 0 and llm_confidence < 70:
                    exclusion_reasons = [
                        f"Análisis IA: confianza baja ({llm_confidence}%) — no garantizamos encaje"
                    ] + exclusion_reasons
                # Si llm_confidence==0 significa que el LLM falló para ese candidato
                # → fallback al verdict del analyzer regex
                if llm_confidence == 0 and not a.applicable:
                    exclusion_reasons = list(a.exclusion_reasons) or exclusion_reasons
                match_reasons = []

            # Mostrar score real: en modo LLM, el score llm_score sustituye al determinista
            # excepto cuando el LLM falló (confidence=0).
            final_score = llm_score if llm_confidence > 0 else (c.score if a.applicable else max(0, c.score - 60))

            final_results.append(RankedResult(
                subvencion=c.subvencion,
                score=final_score,
                razon=razon,
                rank=0,
                applicable=llm_applicable if llm_confidence > 0 else a.applicable,
                match_reasons=tuple(match_reasons[:8]),
                exclusion_reasons=tuple(exclusion_reasons[:6]),
                urgency_days=a.urgency_days,
            ))
        # 2) Pre_no_aplicables (descartados por analyzer regex): mantenemos
        #    descartados sin gastar llamadas LLM en ellos.
        for c, a in pre_no_aplicables:
            final_results.append(RankedResult(
                subvencion=c.subvencion,
                score=max(0, c.score - 60),
                razon=_compose_razon(a),
                rank=0,
                applicable=False,
                match_reasons=tuple(a.match_reasons[:6]),
                exclusion_reasons=tuple(a.exclusion_reasons[:6]),
                urgency_days=a.urgency_days,
            ))
    else:
        # ──────────── Fallback offline: solo analyzer determinista ────────────
        final_results = []
        for c, a in analyzed:
            score = c.score
            if a.applicable:
                score = min(100, score + min(25, len(a.match_reasons) * 8))
            else:
                score = max(0, score - 60)
            final_results.append(RankedResult(
                subvencion=c.subvencion,
                score=score,
                razon=_compose_razon(a),
                rank=0,
                applicable=a.applicable,
                match_reasons=tuple(a.match_reasons[:8]),
                exclusion_reasons=tuple(a.exclusion_reasons[:6]),
                urgency_days=a.urgency_days,
            ))

    # Separar aplicables / descartadas, ordenar
    aplicables_final = sorted(
        [r for r in final_results if r.applicable],
        key=lambda x: -x.score,
    )
    no_aplicables_final = sorted(
        [r for r in final_results if not r.applicable],
        key=lambda x: x.subvencion.titulo or "",
    )

    cap_no_apl = max(0, min(50, int(limit * 0.8)))
    final = aplicables_final[:limit] + no_aplicables_final[:cap_no_apl]

    return [
        RankedResult(
            subvencion=r.subvencion, score=r.score, razon=r.razon, rank=i + 1,
            applicable=r.applicable,
            match_reasons=r.match_reasons,
            exclusion_reasons=r.exclusion_reasons,
            urgency_days=r.urgency_days,
        )
        for i, r in enumerate(final)
    ]
