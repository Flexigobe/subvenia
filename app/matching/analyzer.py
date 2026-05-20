"""Análisis empresa-vs-subvención determinista y rico.

Para cada subvención candidata, examina los campos REALES disponibles
(tiposBeneficiarios, regiones NUTS, sectores BDNS, instrumentos, tamaños,
finalidad, fechas, importes) y produce:

- `applicable`: True/False — si la empresa cumple razonablemente los requisitos.
- `match_reasons`: lista de strings con motivos por los que LE TOCA.
- `exclusion_reasons`: lista de strings con motivos por los que NO LE TOCA.
- `confidence`: 0-100 confianza del análisis (alto = datos completos).
- `urgency_days`: días hasta el cierre (-1 si no se sabe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.db.models import Subvencion
from app.matching.filter import EmpresaProfile, _PROVINCIA_KEYWORDS

_NUTS3_TO_PROVINCIA: dict[str, str] = {
    "ES111": "15", "ES112": "27", "ES113": "32", "ES114": "36",
    "ES120": "33",
    "ES130": "39",
    "ES211": "01", "ES212": "20", "ES213": "48",
    "ES220": "31",
    "ES230": "26",
    "ES241": "22", "ES242": "44", "ES243": "50",
    "ES300": "28",
    "ES411": "05", "ES412": "09", "ES413": "24", "ES414": "34",
    "ES415": "37", "ES416": "40", "ES417": "42", "ES418": "47", "ES419": "49",
    "ES421": "02", "ES422": "13", "ES423": "16", "ES424": "19", "ES425": "45",
    "ES431": "06", "ES432": "10",
    "ES511": "08", "ES512": "17", "ES513": "25", "ES514": "43",
    "ES521": "03", "ES522": "12", "ES523": "46",
    "ES530": "07",
    "ES611": "04", "ES612": "11", "ES613": "14", "ES614": "18",
    "ES615": "21", "ES616": "23", "ES617": "29", "ES618": "41",
    "ES620": "30",
    "ES630": "51",
    "ES640": "52",
    "ES703": "35", "ES704": "35", "ES705": "35",
    "ES706": "38", "ES707": "38", "ES708": "38", "ES709": "38",
}

_TAMANO_LABELS = {
    "micro": "microempresa",
    "pequena": "pequeña empresa",
    "mediana": "mediana empresa",
    "grande": "empresa grande",
}


# ─── Heurística de título: patrones que excluyen categóricamente a empresas ───
# Cada patrón es (regex, motivo legible). Si el TÍTULO matchea cualquiera, la
# subvención NO aplica a empresas privadas. Mantenemos el record visible en la
# sección "descartadas" para que el usuario sepa qué se filtró y por qué.

_NOT_FOR_COMPANIES_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ── Becas y tesis: para personas físicas estudiantes/investigadores ──
    (re.compile(r"\bBECAS?\b.*(TESIS|DOCTORAL|ESTUDIO|ESTUDIANT|M[ÁA]STER|MASTER|GRADO|MOVILIDAD|UNIVERSITARI|UNIA |PR[ÁA]CTIC|MONITOR|RURAL|COLABORACI[ÓO]N)", re.IGNORECASE),
     "Beca para personas físicas (estudiantes/investigadores)"),
    (re.compile(r"\bBECAS?\s+(SANTANDER|ERASMUS|FULBRIGHT|CAJA|UNIA|URJC|UMH|UNED|UA )", re.IGNORECASE),
     "Beca personal de estudios"),
    (re.compile(r"\b(BECAS\s+(DE\s+)?(LA\s+)?(I+|II+)?\s*EDICI[ÓO]N|BECAS?\s+CON\s+DESTINO)", re.IGNORECASE),
     "Beca individual / nominal"),
    (re.compile(r"\bTESIS\b", re.IGNORECASE),
     "Para investigación individual (tesis)"),
    (re.compile(r"\bTRABAJOS?\s+FIN\s+DE\s+(GRADO|M[ÁA]STER)\b|\bTFG\b|\bTFM\b", re.IGNORECASE),
     "Para estudiantes (TFG/TFM)"),
    # ── Premios honoríficos individuales ──
    (re.compile(r"^PREMIO[S]?\s+(NACIONAL|LITERAR|GASTRON|FOTOGR|JOVENES|MARTA|VICENT|GLORIA|DEFENSA|ARTESAN|CINE|UNIVERS)", re.IGNORECASE),
     "Premio honorífico individual"),
    (re.compile(r"^PREMIO[S]?\s+(EN\s+EL\s+MARCO|GLORIA\s+FUERTES|NACIONALES?\s+DE\s+ARTESAN)", re.IGNORECASE),
     "Premio individual"),
    (re.compile(r"\bPREMIOS?\b.*(TRABAJOS?\s+FIN\s+DE|TESIS|UNIVERSITARI|ESTUDIANT|J[ÓO]VENES|ESCRIT|POES|NARRATIV)", re.IGNORECASE),
     "Premio académico/literario individual"),
    # ── Concesiones nominativas / convenios bilaterales (no abiertas a empresas) ──
    (re.compile(r"\bCONVENIO[S]?\b.*\bENTRE\b", re.IGNORECASE),
     "Convenio bilateral nominativo, no convocatoria abierta"),
    (re.compile(r"\bCONVENIO[S]?\s+(ESPEC[ÍI]FICO|DE\s+COLABORACI[ÓO]N|CON\s+EL|DE\s+ENCOMIENDA)", re.IGNORECASE),
     "Convenio nominativo"),
    (re.compile(r"^CONVENIS\b", re.IGNORECASE),
     "Convenio nominativo"),
    # ── Aportaciones / contribuciones / transferencias entre administraciones ──
    (re.compile(r"\bAPORTACI[ÓO]N(?:ES)?\b", re.IGNORECASE),
     "Aportación nominativa, no convocatoria"),
    (re.compile(r"\bCONTRIBUCI[ÓO]N(?:ES)?\s+FINANCIERA", re.IGNORECASE),
     "Contribución entre administraciones, no convocatoria"),
    (re.compile(r"\bSUBVENCI[ÓO]N\s+(NOMINATIVA|AL\s+(AYUNTAMIENTO|MUNICIPIO|CONSEJO|GOBIERNO|CONSORCIO|CSIC|INIA|UNIVERSIDAD|FUNDACI))", re.IGNORECASE),
     "Subvención directa nominativa a otra entidad"),
    # ── Cursos para profesorado / personal docente (no para empresas) ──
    (re.compile(r"\bCURSOS?\b.*\b(PROFESORADO|DOCENTES?|MAESTROS|EDUCATIV|COMPETENCIAS?\s+DIRECTIVAS?\b)", re.IGNORECASE),
     "Formación para profesorado, no para empresas"),
    (re.compile(r"\bFORMACI[ÓO]N\s+(PERMANENTE\s+DEL\s+PROFESORADO|ESPECIALIZADA\s+EN\s+[ÁA]MBITOS\s+CLAVE)", re.IGNORECASE),
     "Formación específica para personal docente"),
    (re.compile(r"\bAYUDAS?\s+AL\s+ESTUDIO\b", re.IGNORECASE),
     "Ayuda al estudio para personas físicas"),
    # ── Programas educativos / campamentos / actividades culturales para estudiantes ──
    (re.compile(r"\bCAMPUS\s+RURAL\b|\bAULA\s+J[ÚU]NIOR\b|\bESCUELA\s+DE\s+VERANO\b|\bCINE\s+ESCUELA\b", re.IGNORECASE),
     "Programa educativo para estudiantes"),
    (re.compile(r"\bPROGRAMAS?\s+(O\s+M[ÓO]DULOS?\s+)?DE\s+EDUCACI[ÓO]N\s+ESPECIALIZADA", re.IGNORECASE),
     "Programa educativo no aplicable a empresas privadas"),
    (re.compile(r"\bANIMACI[ÓO]N\s+A\s+LA\s+LECTURA", re.IGNORECASE),
     "Programa cultural local"),
    # ── Organizaciones sindicales / partidos políticos / víctimas ──
    (re.compile(r"\bORGANIZACIONES?\s+SINDICAL", re.IGNORECASE),
     "Solo organizaciones sindicales"),
    (re.compile(r"\bMEMORIA\s+DEMOCR[ÁA]TICA\b|\bV[ÍI]CTIMAS\s+DE\s+LA\s+(GUERRA|DICTADURA)", re.IGNORECASE),
     "Memoria democrática a víctimas"),
    # ── Religioso / ONGs solo ──
    (re.compile(r"\bCOOPERACI[ÓO]N\s+(AL\s+)?DESARROLLO\b.*\bONGD\b", re.IGNORECASE),
     "Solo ONGs de cooperación al desarrollo"),
    # ── Subvenciones a Cines/festivales locales nominativos ──
    (re.compile(r"\bFESTIVAL\s+(INTERNACIONAL\s+DE\s+CINE|DE\s+CINE\s+DE)\b", re.IGNORECASE),
     "Patrocinio de festival cultural local"),
    # ── Concesiones directas (todas son nominativas a entidad concreta) ──
    (re.compile(r"\bCONCESI[ÓO]N\s+DIRECTA\b|\bCONCESI[ÓO]N\s+DE\s+(UNA?\s+)?SUBVENCI[ÓO]N\s+DIRECTA\b", re.IGNORECASE),
     "Concesión directa a entidad específica (no convocatoria abierta)"),
    (re.compile(r"\bSUBVENC[IÍ]ON\s+DIRECTA\s+(A\s+LA\s+|AL\s+|A\s+EL\s+|A\s+)", re.IGNORECASE),
     "Subvención directa nominativa"),
    # ── Contribuciones a organismos internacionales (ONU, OMS, etc.) ──
    (re.compile(r"\bCONTRIBUCI[ÓO]N(?:ES)?\b", re.IGNORECASE),
     "Contribución a organismo internacional, no convocatoria"),
    # ── Subvenciones a CCAA / entidades concretas (nominativas) ──
    (re.compile(r"\bSUBVENCI[ÓO]N\s+A\s+LA\s+(COMUNIDAD\s+AUT[ÓO]NOMA|GENERALITAT|JUNTA|XUNTA|GOBIERNO|PRINCIPADO|CABILDO|DIPUTACI[ÓO]N|CONSELLER|DEPARTAMENTO|UNIVERSIDAD|FUNDACI[ÓO]N|ASOCIACI[ÓO]N|FEDERACI[ÓO]N|CONFEDERACI[ÓO]N)", re.IGNORECASE),
     "Subvención nominal a entidad específica"),
    (re.compile(r"\bSUBVENCI[ÓO]N\s+(COBERTURA\s+DE\s+GASTOS|A\s+ONG)", re.IGNORECASE),
     "Subvención nominativa a entidad"),
    # ── Olimpiadas / concursos escolares ──
    (re.compile(r"\bOLIMPIADAS?\b|\bCONCURSOS?\s+(ESCOLAR|UNIVERSITARI|JUVENIL)", re.IGNORECASE),
     "Concurso educativo, no para empresas"),
    # ── Patrocinio cultural específico ──
    (re.compile(r"\bPATRIMONIO\s+HIST[ÓO]RICO\b.*\b(AERON[ÁA]UTICO|FERROVIAR|MIL[IÍ]TAR|ECLES)", re.IGNORECASE),
     "Patrocinio cultural específico"),
    # ── Premios militares / nombres santos ──
    (re.compile(r"\bPREMIOS?\s+(VIRGEN|SAN\s+|SANTA\s+|SANTO\s+)", re.IGNORECASE),
     "Premio honorífico militar/religioso"),
    # ── Conservación patrimonio ──
    (re.compile(r"\bCONSERVACI[ÓO]N\s+(DEL\s+)?PATRIMONIO", re.IGNORECASE),
     "Conservación de patrimonio cultural"),
    # ── Servicios universitarios internos ──
    (re.compile(r"\bCOMEDOR\s+UNIVERSITARI|\bMATERIAL\s+ESCOLAR\b|\bAULA\s+MATINAL", re.IGNORECASE),
     "Servicio interno universitario/escolar"),
    # ── Fauna / especies / conservación medioambiental nominativa ──
    (re.compile(r"\b(OSO\s+PARDO|QUEBRANTAHUESOS|LINCE\s+IB[EÉ]RICO|VISÓN\s+EUROPEO|UROGALLO)\b", re.IGNORECASE),
     "Conservación de fauna específica"),
    # ── Cooperación al desarrollo nominativa ──
    (re.compile(r"\bCOOPERACI[ÓO]N\s+(AL\s+)?DESARROLLO\b.*\bAECID\b|\bAECID\b.*\bCONVENIO", re.IGNORECASE),
     "Cooperación al desarrollo nominativa"),
    # ── Asociaciones específicas ──
    (re.compile(r"\b(FEMP|FUNDACI[ÓO]N\s+ENAIRE|ICEX.*LINEAPELLE|AMUPARNA|RED\s+INNPULSO|AMETIC|CSIC|INIA)\b", re.IGNORECASE),
     "Subvención a asociación/entidad específica"),

    # ════════════════════════════════════════════════════════════════════
    # AÑADIDOS — DETECTADOS COMO FALSOS POSITIVOS REALES EN AUDITORÍA
    # ════════════════════════════════════════════════════════════════════

    # ── Asignación grupos políticos municipales ──
    (re.compile(r"\bgrupos?\s+(pol[ií]ticos?\s+)?municipales?\b.*\bayuntamiento", re.IGNORECASE),
     "Asignación económica a grupos políticos"),
    (re.compile(r"\basignaci[oó]n\s+econ[oó]mica\s+grupos?\s+municipales?\b", re.IGNORECASE),
     "Asignación económica grupos municipales"),

    # ── Fundación SEPI / Inserción laboral vulnerables ──
    (re.compile(r"\bfundaci[oó]n\s+sepi\b", re.IGNORECASE),
     "Fundación SEPI — programas formación profesionales jóvenes"),
    (re.compile(r"\binserci[oó]n\s+laboral\s+(de\s+|para\s+|colectivos?\s+|vulnerable)", re.IGNORECASE),
     "Programa inserción laboral colectivos vulnerables"),
    (re.compile(r"\bproyectos?\s+integrales?\s+colectivos?\s+vulnerab", re.IGNORECASE),
     "Proyectos vulnerables, no para empresas"),
    (re.compile(r"\bcolectivos?\s+vulnerab\w+\s+e?\s*incentivo", re.IGNORECASE),
     "Colectivos vulnerables"),

    # ── Talento Joven / Empleo Joven ──
    (re.compile(r"\btalento\s+joven\b", re.IGNORECASE),
     "Talento joven — programa para menores de 30 años"),
    (re.compile(r"\bayud(?:a|as)\s+(econ[oó]micas?\s+)?(?:destinadas?\s+)?al?\s+fomento\s+del\s+empleo.*?(joven|talento|menor)", re.IGNORECASE),
     "Fomento empleo joven (no para contratación adulta)"),

    # ── Religioso / Cofradías / Salesianos / Centros pastorales ──
    (re.compile(r"\bsalesians?\s+san\s+\w+", re.IGNORECASE),
     "Centro educativo religioso"),
    (re.compile(r"\bcaritas\b|\bca[uú]ritas\b", re.IGNORECASE),
     "Cáritas"),
    (re.compile(r"\bcofrad[ií]a\s+\w+|hermandad\s+\w+", re.IGNORECASE),
     "Cofradía / hermandad religiosa"),

    # ── Clubes deportivos amateurs / fundaciones culturales ──
    (re.compile(r"\bclub\s+(deportivo|balonmano|f[uú]tbol|bici|baloncesto|tenis|hockey|nataci[oó]n|atletismo|ciclista|esquiad)\s+", re.IGNORECASE),
     "Club deportivo (no empresa con actividad económica)"),
    (re.compile(r"\bsubv\.?\s+(comisi[oó]n\s+de\s+)?fiestas?\s+(san|santa|nuestra|virgen|sagrad)", re.IGNORECASE),
     "Comisión de fiestas patronales"),

    # ── Asociaciones específicas con nombres concretos ──
    (re.compile(r"\bbloque\s+n[ºo]\s*\d+/\d+", re.IGNORECASE),
     "Subvención municipal genérica (sin convocatoria abierta)"),
    (re.compile(r"\baprobaci[oó]n\s+pago\s+asignaci[oó]n", re.IGNORECASE),
     "Aprobación pago de asignación específica"),
    (re.compile(r"\bsubv[\.ención]+s?\s+(asociaci[oó]n|club|federaci[oó]n|peña|cofrad|hermandad|fundaci[oó]n|comisi[oó]n)", re.IGNORECASE),
     "Subvención nominativa a entidad específica"),

    # ── Centros docentes ──
    (re.compile(r"\bcompras\s+ciudadan|\bbonos?\s+(al\s+)?consumo\b", re.IGNORECASE),
     "Bonos al consumo para particulares"),

    # ── Convivencias / actividades vecinales ──
    (re.compile(r"\bconvivencias?\s+(cer[áa]mic|cultural|vecinal)", re.IGNORECASE),
     "Actividad vecinal local"),

    # ── Convenios con asociación VECINAL/CULTURAL/JUVENIL ──
    (re.compile(r"\b(convenio|conveni|conveni[uú]m)\b.*\b(asociaci[oó]n|associaci[óo])\s+(cultural|deportiv|vecinal|juvenil|festiv|carnaval|cazadores|musical|recreativa|gastron)", re.IGNORECASE),
     "Convenio con asociación local específica"),

    # ── Voluntariado ──
    (re.compile(r"\bvoluntariado\b", re.IGNORECASE),
     "Programa de voluntariado, no para empresas"),

    # ── Centros carnavalescos / casas culturales locales ──
    (re.compile(r"\bcentro\s+carnavalesco|\bcasa\s+de\s+(la\s+)?cultura\b", re.IGNORECASE),
     "Centro cultural local nominativo"),

    # ── Beneficiarios concretos por nombre ──
    (re.compile(r"\bsubvenci[oó]n\s+(nominativa\s+|directa\s+|para\s+la?\s+|a\s+la?\s+|al?\s+)(centro|casa|fundaci[oó]n|asociaci[oó]n|federaci[oó]n|consorci[oó]?|consejo|orden|colegio|abadía|catedral|monasterio|parroquia)\s+", re.IGNORECASE),
     "Subvención a entidad concreta (no convocatoria abierta)"),

    # ── Adhesiones / programas municipales internos ──
    (re.compile(r"\badhesiones?\s+al\s+programa\b|\bprograma\s+(de\s+)?desratizaci[oó]n", re.IGNORECASE),
     "Adhesión a programa municipal interno"),

    # ── UNED Senior / Universidad mayores ──
    (re.compile(r"\buned\s+senior\b|\buniversidad\s+(de\s+)?mayores?", re.IGNORECASE),
     "UNED Senior — programa para mayores"),

    # ── Bases reguladoras GENÉRICAS (sin convocatoria concreta abierta) ──
    (re.compile(r"^bases\s+reguladoras\b(?!.*concurrencia\s+competitiva)", re.IGNORECASE),
     "Bases reguladoras (la convocatoria concreta se publicará después)"),
    (re.compile(r"\bbases?\s+reguladoras?\b.*\bdeben\s+regir\b", re.IGNORECASE),
     "Bases reguladoras pendientes de convocatoria"),

    # ── Concurso de cartel anunciador / ilustración ──
    (re.compile(r"\bconcurso\s+(del\s+)?cartel\s+anunciador\b", re.IGNORECASE),
     "Concurso de cartel (artistas individuales)"),

    # ── Becas prácticas universidad ──
    (re.compile(r"\bpr[áa]cticas?\s+becadas?\b", re.IGNORECASE),
     "Prácticas becadas (estudiantes universitarios)"),

    # ── Premios formación profesional ──
    (re.compile(r"\bpremios?\s+(extraordinarios?\s+)?formaci[oó]n\s+profesional\b", re.IGNORECASE),
     "Premios formación profesional para alumnos"),

    # ════════════════════════════════════════════════════════════════════
    # CONVOCATORIAS DE TIPO SOCIAL / VULNERABLES (no para empresas)
    # ════════════════════════════════════════════════════════════════════

    # Ley Orgánica de violencia de género (art 27)
    (re.compile(r"\bl\.\s*o\.\s*1/2004\b|\bley\s+org[áa]nica\s+1/2004\b|\bart[íi]?culo\s+27\s+de\s+la\s+l\.?o\.?", re.IGNORECASE),
     "Ayudas a víctimas violencia de género (LO 1/2004)"),

    # Ayudas integración / emergencia social / atención benéfica
    (re.compile(r"\b(ayudas?|subvenciones?|atenciones?)\s+(de\s+)?(integraci[oó]n|emergencia\s+social|asistencial|ben[eé]fic)", re.IGNORECASE),
     "Ayudas sociales para personas en situación de vulnerabilidad"),
    (re.compile(r"\batenci[oó]n\s+(de\s+)?necesidades\s+sociales", re.IGNORECASE),
     "Atención de necesidades sociales individuales"),

    # Vivienda social / alquiler / hipoteca
    (re.compile(r"\bvivienda\s+(social|protegida|colectiva|joven|alquiler\s+social)", re.IGNORECASE),
     "Vivienda social/protegida para particulares"),
    (re.compile(r"\breforma\s+(y\s+mejora\s+de\s+)?(las\s+)?(condiciones\s+de\s+)?edificaci[oó]n\s+de\s+la\s+vivienda", re.IGNORECASE),
     "Ayuda a particulares para reforma vivienda"),

    # Romerías / fiestas patronales con santo específico
    (re.compile(r"\bromer[ií]a\s+(de|del)\s+", re.IGNORECASE),
     "Romería local / religiosa"),
    (re.compile(r"\bcorona\s+(del\s+)?santo|\bfieles?\s+de\b|\bcofrad[ií]a\s+de\b", re.IGNORECASE),
     "Acto religioso / cofradía local"),

    # Carnaval entities
    (re.compile(r"\bentidades?\s+(de\s+)?carnaval|\bcarnaval\s+\d{4}", re.IGNORECASE),
     "Entidades de Carnaval"),

    # Ferias ganaderas locales
    (re.compile(r"\bferia\s+(de\s+)?ganad[oa]|\bferia\s+(de\s+)?(tomate|patata|cebolla|garbanz|al\s+ganado)", re.IGNORECASE),
     "Feria agrícola/ganadera local"),

    # Distritos municipales (resoluciones internas de ayto)
    (re.compile(r"\bdistrito\s+(de\s+)?(usera|villa\s+verde|carabanchel|tetu[áa]n|chamart[ií]n|hortaleza|moratalaz|vallecas|barajas|salamanca|chamber[ií]|arganzuela|retiro)", re.IGNORECASE),
     "Subvención de distrito municipal específico"),

    # Familia / natalidad / dependientes
    (re.compile(r"\bnatalidad\b|\bfomento\s+de\s+la\s+natalidad", re.IGNORECASE),
     "Ayuda natalidad para familias"),
    (re.compile(r"\bayudas?\s+(a\s+)?(las\s+)?familias?\b", re.IGNORECASE),
     "Ayudas a familias (particulares)"),

    # Trabajadores autónomos individuales recién constituidos
    (re.compile(r"\bayudas?\s+a\s+trabajadores\s+(que\s+)?se\s+constituyen\s+por\s+cuenta\s+propia\b", re.IGNORECASE),
     "Ayuda al autónomo recién constituido (individual)"),

    # Escuelas infantiles primer ciclo
    (re.compile(r"\bescuelas?\s+infantiles?\s+(de\s+primer\s+ciclo|de\s+0\s+a\s+3)", re.IGNORECASE),
     "Escuelas infantiles primer ciclo educación"),

    # Programas medioambientales locales nominativos
    (re.compile(r"\bprogramas?\s+de\s+medio\s+ambiente\s+y\s+protecci[oó]n\s+animal", re.IGNORECASE),
     "Programa municipal medio ambiente/animales"),

    # Palomares y construcciones rurales tradicionales
    (re.compile(r"\bpalomares?\b|\bcorrales?\s+tradicional", re.IGNORECASE),
     "Conservación construcciones rurales tradicionales"),

    # Promoción lenguas cooficiales (euskera, gallego, catalán)
    (re.compile(r"\bpromoci[oó]n\s+(del\s+)?(euskera|euskara|gallego|galego|catal[áa]n|aranés)\b", re.IGNORECASE),
     "Promoción lengua cooficial (asociaciones locales)"),

    # Servicios sociales: oficinas marineras, cooperativas pesqueras
    (re.compile(r"\boficina\s+del\s+marinero\b|\bcofrad[ií]a\s+(de\s+)?pescadores?", re.IGNORECASE),
     "Cofradía/oficina pescadores (cooperativa específica)"),

    # Conv 2026 + nombre propio Ayuntamiento
    (re.compile(r"^conveni[oó]?\s+\d{4}\s*[-–]\s*ayuntamiento\s+de\s+", re.IGNORECASE),
     "Convenio con ayuntamiento específico"),
    (re.compile(r"\bdecreto\s+de\s+alcald[íi]a\b", re.IGNORECASE),
     "Decreto interno de alcaldía"),

    # Programas EU específicos no aplicables
    (re.compile(r"\bedtech\s+accelerator\b", re.IGNORECASE),
     "EdTech Accelerator (sector educación digital)"),
    (re.compile(r"\bpillar\s+(i|ii|iii|iv|v|vi)\s+", re.IGNORECASE),
     "Pillar Horizon Europe (línea científica específica)"),
    (re.compile(r"\bonline\s+harms\s+detection\b", re.IGNORECASE),
     "Civil Security 2027: detección daños online"),
    (re.compile(r"\bmonoclonal\s+antibodies\b|\bflavivirus|\bflaviviruses", re.IGNORECASE),
     "Investigación biomédica (anticuerpos monoclonales)"),
    (re.compile(r"\badvisory\s+support\s+.*?(radicalis|extrem|hate\s+speech)", re.IGNORECASE),
     "Programa contra radicalización/extremismo"),
    (re.compile(r"\b(analytical\s+capacity|sustainable\s+competitiveness)\s+.*?(agricultur|farming)\b", re.IGNORECASE),
     "Convocatoria agro analítica"),

    # Asociaciones de colectivos LGTBI / específicas
    (re.compile(r"\b(lesbian|gais?|trans(?:exuales|género)|bisexuales?|lgtbi+|lgbtq)\b", re.IGNORECASE),
     "Asociación LGTBI específica"),

    # Asociaciones culturales con nombre propio (psicólogos, abogados, ingenieros...)
    (re.compile(r"\bcoleg\.?\s*(psic|abog|ing|m[eé]dic|odont|veter|graf|fisi|ferr|aparej|topog|gestor|notar)", re.IGNORECASE),
     "Colegio profesional"),

    # FINANCIACIÓN FSE+ - prácticas profesionales jóvenes
    (re.compile(r"\bfse\+?\s+.*?\bj[oó]ven", re.IGNORECASE),
     "FSE+ — prácticas profesionales jóvenes"),
    (re.compile(r"\bimpulso\s+cont(?:rato)?\.\s*form(?:aci[oó]n)?\s*\.\s*pract", re.IGNORECASE),
     "Impulso contrato formación prácticas"),

    # Competiciones intermunicipales (federaciones deportivas)
    (re.compile(r"\bcompetici[oó]n\s+intermunicipal", re.IGNORECASE),
     "Competición intermunicipal (entidades locales)"),

    # AE / Asistencia Económica directa al ayuntamiento
    (re.compile(r"^ae\s+(directa|economica)\s+al\s+ayuntamiento\b", re.IGNORECASE),
     "Asistencia económica directa a ayuntamiento"),

    # ════════════════════════════════════════════════════════════════════
    # NOMINATIVAS multiidiomas (catalán/valenciano/gallego/euskera)
    # ════════════════════════════════════════════════════════════════════

    # Catalán/valenciano "Subvenció nominativa"
    (re.compile(r"\bsubvenci[oó]\s+nominativa\b|\bsubvencions?\s+nominativ\w+\b", re.IGNORECASE),
     "Subvenció nominativa (catalán/valenciano)"),

    # Conveni (catalán) + nombre concreto
    (re.compile(r"\bconveni\s+(amb|de\s+col[·.]?laboraci[oó]|club|associaci[oó]|fundaci[oó]|nominatiu)", re.IGNORECASE),
     "Conveni nominatiu (catalán)"),
    (re.compile(r"\bconveni\s+(amb\s+|de\s+col[·.]?laboraci[oó]\s+amb)", re.IGNORECASE),
     "Convenio bilateral (catalán)"),

    # Concesión directa nominativa
    (re.compile(r"\bconcesi[oó]n\s+(de\s+)?subvenci[oó]n\s+(directa\s+)?nominativa", re.IGNORECASE),
     "Concesión subvención directa nominativa"),

    # Concesión a entidad CONCRETA con nombre propio (formato "CLUB X", "PEÑA Y", "ASOCIACIÓN Z")
    (re.compile(r"\b(concesi[oó]n|otorgamiento|aprobaci[oó]n)\s+(subvenci[oó]n|ayuda|subvenci[oó]n\s+(directa|nominativa))\s+(a\s+)?(la\s+|el\s+|al\s+)?(club|pe[ñn]a|penya|sociedad|fundaci[oó]n|asociaci[oó]n|associaci[oó]|federaci[oó]n|federaci[oó]|cofrad[ií]a|hermandad|orden|abad[ií]a|catedral|monasterio|parroqui|comunidad|comisi[oó]n|patronato|consorci|colegi|orfe[oó]n|banda|coro|orquesta|empresa\s+municipal)\s+", re.IGNORECASE),
     "Concesión nominativa a entidad concreta"),

    # Concesión a club deportivo (cualquier deporte)
    (re.compile(r"\b(subvenci[oó]n|conveni[oó]?|concesi[oó]n)\b.*\bclub\s+(deportivo|hipico|patinage|patinatge|nautico|karat|judo|taekwon|aikido|esgrim|kayak|piragu|escalada|monta[ñn]ismo|alpinismo)", re.IGNORECASE),
     "Subvención a club deportivo específico"),

    # Subvención A LA <nombre propio mayúsculas>
    (re.compile(r"\bsubvenci[oó]n\s+a\s+la\s+entidad\b", re.IGNORECASE),
     "Subvención a entidad concreta nominal"),

    # Bandas de música / orquestas / coros / orfeones
    (re.compile(r"\b(banda\s+de\s+m[uú]sica|orfe[oó]n|orquesta\s+(filarm|sinf|de\s+c[áa]mara)|coro\s+(del|de\s+l)|coral\s+)", re.IGNORECASE),
     "Banda/coro/orquesta local"),
    (re.compile(r"\bcompensaci[oó]n\s+extraordinaria\s+a\s+la?\s+banda", re.IGNORECASE),
     "Compensación a banda musical local"),

    # Peñas flamencas, peñas culturales
    (re.compile(r"\bpe[ñn]a\s+(flamenca|cultural|deportiv|recreativa|gastron)", re.IGNORECASE),
     "Peña cultural/flamenca/recreativa local"),

    # Cartel Semana Santa / Cofradía / procesiones
    (re.compile(r"\bcartel\s+(semana\s+santa|cofrad|procesi|virgen)\b", re.IGNORECASE),
     "Cartel para Semana Santa / cofradía"),
    (re.compile(r"\bparticipaci[oó]n\s+de?\s*semana\s+santa\b", re.IGNORECASE),
     "Participación Semana Santa"),
    (re.compile(r"\bsemana\s+santa\b.*\b(pascu|procesi|cofrad|hermandad)", re.IGNORECASE),
     "Semana Santa / Pascua"),

    # Ayuntamiento de X (concreto) recibe la subvención (no convocatoria abierta)
    (re.compile(r"\b(subvenci[oó]n|ayuda|concesi[oó]n|otorgamiento|conveni[oó]?\s+colaboraci[oó]n)\s+(a\s+favor\s+del\s+|al\s+|para\s+(el\s+)?)?ayuntamiento\s+de\s+[a-záéíóúñ\.]+", re.IGNORECASE),
     "Subvención nominativa a Ayuntamiento concreto"),
    (re.compile(r"\bayuntamiento\s+de\s+[a-záéíóúñ\.]+\s+(para|por)\s+", re.IGNORECASE),
     "Convenio con ayuntamiento concreto"),

    # Federación deportiva específica (Real Federación, Federación Española)
    (re.compile(r"\b(real\s+)?federaci[oó]n\s+(española|de\s+\w+)\s+", re.IGNORECASE),
     "Federación deportiva específica"),

    # Asociaciones culturales/festeras/musicales
    (re.compile(r"\b(associaci[oó]|asociaci[oó]n)\s+(moros\s+i\s+cristians|cultural\s+|festera\s+|festiv|fallera|fallera|musical\s+|carnaval|sardanas|jotas)", re.IGNORECASE),
     "Asociación cultural/festera local"),

    # Concesión directa con código SAD/SD/etc. (deportes adaptados, etc.)
    (re.compile(r"\bsubvenci[oó]n\s+(directa\s+)?nominativa?\s+\w+\s+(cf|sd|sad|cd)\s+\d", re.IGNORECASE),
     "Subvención nominativa a club deportivo"),

    # Real Centro / Centro Real (familia real)
    (re.compile(r"\breal\s+(monasterio|patronato|centro|orden|capilla|colegiata)\b", re.IGNORECASE),
     "Real entidad nominal"),

    # Premios Gabriel Ferraté + nombre propio (premio individual)
    (re.compile(r"\bpremis?\s+\w+\s+\w+(\s+\w+)?\s+(i\s+|y\s+|a\s+la?\s+)", re.IGNORECASE),
     "Premio individual con nombre propio (catalán)"),
]


# ─── Whitelist: indicios fuertes de "esto SÍ es para empresas" ───
_FOR_COMPANIES_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(PYMES?|EMPRESAS?|INDUSTRIA|EMPRESARIAL|AUT[ÓO]NOMOS?)\b", re.IGNORECASE),
    re.compile(r"\b(INNOVACI[ÓO]N|I\+D|I\+D\+I|DIGITALIZACI[ÓO]N|TRANSFORMACI[ÓO]N\s+DIGITAL)\b", re.IGNORECASE),
    re.compile(r"\b(INTERNACIONALIZACI[ÓO]N|EXPORTACI[ÓO]N|COMPETITIVIDAD)\b", re.IGNORECASE),
    re.compile(r"\b(CDTI|ENISA|RED\.ES|ICEX|SEPI|INSTITUTO\s+DE\s+CR[ÉE]DITO)\b", re.IGNORECASE),
    re.compile(r"\b(KIT\s+DIGITAL|NEXT\s+GENERATION|PERTE)\b", re.IGNORECASE),
    re.compile(r"\b(LEADER|RURAL\s+EMPRESARIAL|EMPRENDIMIENTO)\b", re.IGNORECASE),
]


def _has_company_signals(titulo: str | None, organismo: str | None) -> int:
    """Devuelve cuántos signals positivos de "esto es para empresas" hay."""
    count = 0
    text = " ".join(filter(None, [titulo or "", organismo or ""]))
    for p in _FOR_COMPANIES_PATTERNS:
        if p.search(text):
            count += 1
    return count


_NOT_FOR_COMPANIES_ORGANISM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Universidades que conceden becas/premios suelen ser para alumnos/profesores
    (re.compile(r"^UNIVERSIDAD\b", re.IGNORECASE),
     "Convocatoria interna universitaria"),
]


def _title_excludes_companies(titulo: str | None, organismo: str | None) -> tuple[bool, str | None]:
    """Heurística de título/organismo para descartar convocatorias obviamente NO
    aplicables a empresas privadas (becas, premios literarios, convenios nominales,
    aportaciones entre administraciones).

    Devuelve (excluded, reason).
    """
    if titulo:
        for pattern, reason in _NOT_FOR_COMPANIES_PATTERNS:
            if pattern.search(titulo):
                return True, reason
    if organismo:
        # Para organismos universitarios: solo excluir si el TÍTULO ALSO mencion
        # ayudas/becas/premios/convenios — porque algunas universidades sí dan
        # contratos a empresas (servicios).
        for pattern, reason in _NOT_FOR_COMPANIES_ORGANISM_PATTERNS:
            if pattern.search(organismo) and titulo:
                low = titulo.upper()
                if any(kw in low for kw in ("BECA", "PREMIO", "AYUDA AL ESTUDIO", "MOVILIDAD", "CONVENIO ENTRE", "CONVENIS", "CONCESI", "RESOLUCI")):
                    return True, reason
    return False, None


@dataclass
class Analysis:
    applicable: bool
    match_reasons: list[str] = field(default_factory=list)
    exclusion_reasons: list[str] = field(default_factory=list)
    confidence: int = 0
    urgency_days: int = -1


def _is_no_profit_only(tipos_benef: list[dict[str, Any]]) -> bool:
    """True si TODOS los tipos de beneficiario son sin ánimo de lucro."""
    if not tipos_benef:
        return False
    for_profit_count = 0
    no_profit_count = 0
    for t in tipos_benef:
        text = (t.get("descripcion") or "").lower()
        is_no_profit = "no desarrollan actividad económica" in text or "no desarrollan actividad economica" in text
        is_no_profit = is_no_profit or any(term in text for term in (
            "asociaci", "fundaci", "ong", "no lucrativa", "sin ánimo de lucro",
            "comunidad de propietarios", "voluntariado", "sindical",
        ))
        is_for_profit = any(term in text for term in (
            "pyme", "pymes", "que desarrollan actividad económica",
            "que desarrollan actividad economica",
            "autónom", "sociedad mercantil", "empresario individual",
            "cooperativa", "trabajadores autónom",
        ))
        if is_no_profit and not is_for_profit:
            no_profit_count += 1
        elif is_for_profit:
            for_profit_count += 1
    return no_profit_count >= 1 and for_profit_count == 0


def _is_natural_persons_only(tipos_benef: list[dict[str, Any]]) -> bool:
    """True si la subvención es SOLO para personas físicas."""
    if not tipos_benef:
        return False
    has_natural = False
    has_juridica_with_activity = False
    for t in tipos_benef:
        text = (t.get("descripcion") or "").lower()
        if "personas físicas que no desarrollan actividad económica" in text \
                or "personas fisicas que no desarrollan actividad economica" in text:
            has_natural = True
        if any(t in text for t in ("pyme", "sociedad", "que desarrollan actividad")):
            has_juridica_with_activity = True
    return has_natural and not has_juridica_with_activity


def _is_only_self_employed(tipos_benef: list[dict[str, Any]]) -> bool:
    """True si solo acepta autónomos persona física."""
    if not tipos_benef:
        return False
    texts = " | ".join((t.get("descripcion") or "").lower() for t in tipos_benef)
    has_autonomo = "autónom" in texts
    has_societal = any(t in texts for t in ("pyme", "sociedad", "empresa mercantil", "persona jurídic"))
    return has_autonomo and not has_societal


def _matches_region(regiones: list[dict[str, Any]], perfil: EmpresaProfile) -> tuple[bool, str | None]:
    """Verifica si las regiones NUTS de la subvención incluyen la provincia del usuario."""
    if not regiones:
        return True, None
    user_prov = perfil.provincia
    matched_label = None
    nacional_found = False
    for r in regiones:
        desc = (r.get("descripcion") or "").upper()
        m = re.match(r"^(ES\d{3})", desc)
        if m:
            nuts3 = m.group(1)
            if _NUTS3_TO_PROVINCIA.get(nuts3) == user_prov:
                return True, r.get("descripcion")
        if "TODO EL MUNDO" in desc or "ESPAÑA" in desc or "NACIONAL" in desc:
            nacional_found = True
            matched_label = r.get("descripcion")
        province_keywords = _PROVINCIA_KEYWORDS.get(user_prov, [])
        if any(kw in desc for kw in province_keywords):
            return True, r.get("descripcion")
    if nacional_found:
        return True, matched_label
    return False, None


def _days_to_close(fecha_fin: date | None) -> int:
    if not fecha_fin:
        return -1
    return (fecha_fin - date.today()).days


def analyze(sub: Subvencion, perfil: EmpresaProfile) -> Analysis:
    """Análisis empresa-vs-subvención."""
    a = Analysis(applicable=True)
    rp = sub.raw_payload or {}
    confidence_signals = 0

    # ── HEURÍSTICA DE TÍTULO: descarta categorías no aplicables a empresas ──
    excluded, exclusion_reason = _title_excludes_companies(sub.titulo, sub.organismo)
    if excluded:
        a.applicable = False
        a.exclusion_reasons.append(exclusion_reason or "No aplicable a empresas privadas")
        confidence_signals += 1

    # ── Signals positivos de "para empresas": añadir razones de match ──
    company_signals = _has_company_signals(sub.titulo, sub.organismo)
    if company_signals >= 1 and a.applicable:
        a.match_reasons.append("Orientada a empresas / actividad económica")
        confidence_signals += 1

    # Tipos beneficiarios — DECISIÓN 100% DETERMINISTA basada en datos oficiales BDNS
    # (los 5 tipos canónicos detectados en BD real). NO usamos regex de título aquí.
    from app.matching.eligibility import is_eligible_by_official_beneficiarios

    tipos_benef = rp.get("tiposBeneficiarios") or []
    tipo_solic = getattr(perfil, "tipo_solicitante", "empresa") or "empresa"
    if tipos_benef:
        confidence_signals += 1
        is_elig, motivo = is_eligible_by_official_beneficiarios(tipos_benef, tipo_solic)
        if not is_elig:
            a.applicable = False
            a.exclusion_reasons.append(motivo)
        else:
            # Marca razón positiva (informa al LLM downstream)
            a.match_reasons.append(motivo if motivo != "no-official-data" and motivo != "tipos-no-reconocibles" else "Datos oficiales BDNS compatibles")

        # Verificación adicional: tamaño empresa
        tipos_str = " ".join((t.get("descripcion") or "").lower() for t in tipos_benef)
        has_pyme = "pyme" in tipos_str
        has_gran = "gran empresa" in tipos_str
        if tipo_solic == "empresa" and a.applicable and has_gran and not has_pyme:
            if perfil.tamano in ("micro", "pequena", "mediana"):
                a.applicable = False
                a.exclusion_reasons.append("Subvención solo para grandes empresas — tu empresa es PYME")

    # Regiones
    regiones = rp.get("regiones") or []
    if regiones:
        confidence_signals += 1
        matches, label = _matches_region(regiones, perfil)
        if matches:
            if label:
                a.match_reasons.append(f"Ámbito incluye tu provincia ({label[:50]})")
        else:
            a.applicable = False
            descs = ", ".join((r.get("descripcion") or "?")[:30] for r in regiones[:2])
            a.exclusion_reasons.append(f"Ámbito geográfico no incluye tu provincia ({descs})")

    # CNAE
    if perfil.cnae and sub.cnae_elegible:
        confidence_signals += 1
        from app.matching.filter import cnae_match_variants
        variants = set(cnae_match_variants(perfil.cnae))
        elegibles = set(sub.cnae_elegible)
        common = variants & elegibles
        if common:
            sector_match = ", ".join(list(common)[:3])
            a.match_reasons.append(f"Tu CNAE encaja con el sector elegible ({sector_match})")

    # Tamaño
    benef = sub.beneficiarios or {}
    tamanos = benef.get("tamanos") if isinstance(benef, dict) else None
    if tamanos and perfil.tamano:
        confidence_signals += 1
        if perfil.tamano in tamanos:
            label = _TAMANO_LABELS.get(perfil.tamano, perfil.tamano)
            a.match_reasons.append(f"Acepta tu tamaño ({label})")
        else:
            a.applicable = False
            permitidos = ", ".join(_TAMANO_LABELS.get(t, t) for t in tamanos)
            a.exclusion_reasons.append(f"Solo para {permitidos}, no {perfil.tamano}")

    # Fechas
    days_to_end = _days_to_close(sub.fecha_fin)
    a.urgency_days = days_to_end
    if days_to_end >= 0:
        confidence_signals += 1
        if days_to_end <= 7:
            a.match_reasons.append(f"⏰ Cierra en {days_to_end} días — urgente")
        elif days_to_end <= 30:
            a.match_reasons.append(f"Cierra en {days_to_end} días")
    elif sub.fecha_fin is None and rp.get("textFin"):
        a.match_reasons.append(f"Plazo abierto ({(rp.get('textFin') or '')[:60]})")

    # Instrumentos
    instrumentos = rp.get("instrumentos") or []
    if instrumentos:
        confidence_signals += 1
        instr_desc = (instrumentos[0].get("descripcion") or "").upper()
        if "PRÉSTAMO" in instr_desc or "PRESTAMO" in instr_desc:
            a.match_reasons.append("Tipo: préstamo (no donación)")
        elif "SUBVENCIÓN" in instr_desc:
            a.match_reasons.append("Tipo: subvención directa")

    if sub.importe_total:
        a.match_reasons.append(f"Presupuesto convocatoria: {sub.importe_total:,.0f}€")

    a.confidence = min(100, confidence_signals * 20)

    return a
