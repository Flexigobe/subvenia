"""Infiere código CNAE-2009 a partir de texto libre (objeto social o razón social).

Estrategia en dos pasos:
1. Reglas curadas (`_CNAE_RULES`) de alta precisión, regex en español + inglés.
2. Fallback tokenizado sobre el catálogo CNAE-2009 (data/cnae_2009.json).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


# (cnae, label, [regex patterns]) — primer match gana, más específicos arriba.
_CNAE_RULES: list[tuple[str, str, list[str]]] = [
    # ═══════════════════════════════════════════════════════════════════════
    # PRIORIDAD MÁXIMA — Manufactura/industria específica (debe matchear ANTES
    # que patrones genéricos como "arrendamiento" o "alquiler" que aparecen
    # accidentalmente en objetos sociales de empresas industriales.
    # ═══════════════════════════════════════════════════════════════════════
    ("2363", "Fabricación elementos hormigón",
        [r"\bformigons?\b", r"\bhormigon(?:es)?\b", r"\bfabrica\w*\s+de\s+hormig",
         r"\bprefabricados?\s+de\s+hormig", r"\bbetón\b"]),
    ("2351", "Fabricación cemento",
        [r"\bcementera\b", r"\bfabrica\w*\s+de\s+cemento\b"]),
    ("0812", "Extracción gravas y arenas",
        [r"\baridos\b", r"\bgrava\s+(y|i)\s+arena", r"\bcanteras?\b", r"\bquarry\b",
         r"\bextraccion\s+(de\s+)?(arena|grava|aridos)"]),
    ("2370", "Corte piedra ornamental",
        [r"\bpiedras?\s+(natural|ornament|marmol)", r"\bmarbres?\b", r"\bmarmoles?\b", r"\bgranitos?\b"]),
    ("2511", "Estructuras metálicas",
        [r"\bestructur\w*\s+metalic", r"\bcalderer", r"\bmetalisteria\b"]),
    ("2562", "Mecanizado metal",
        [r"\bmecaniz(?:ado|acion)\b", r"\btorneria\b", r"\bdecolet"]),
    ("2410", "Siderurgia / Acero",
        [r"\bsiderurgi", r"\bacer(?:os|er)", r"\bsteel\s+(works|fabric|industri)"]),
    ("2825", "Maquinaria frío/ventilación",
        [r"\brefrigeraci[oó]\b", r"\bfrigorific", r"\bcamaras?\s+frigorif"]),
    ("2120", "Especialidades farmacéuticas",
        [r"\blaboratorio\s+farmaceut", r"\bfarmaceutic.*fabric"]),
    # Catalán hormigón explícito
    ("2363", "Fabricación hormigón (catalán)",
        [r"\bformigons?\s+(per|i|pels|de)\b", r"\bformigons?\s*$"]),

    # Construcción e instalaciones
    ("4329", "Otras instalaciones en obras de construcción", [r"\binstalacion(es)? en obras\b"]),
    ("4321", "Instalaciones eléctricas", [r"\binstalacion(es)? electric", r"\belectricista"]),
    ("4322", "Fontanería, calefacción, aire acondicionado",
        [r"\bfontaneria\b", r"\bcalefaccion\b", r"\baire acondicionado\b", r"\bclimatizacion\b",
         r"\bhvac\b", r"\bplumbing\b"]),
    ("4331", "Revocamiento", [r"\brevocamiento\b", r"\byesero"]),
    ("4332", "Instalación carpintería", [r"\bcarpinteria\b", r"\bcarpenter"]),
    ("4333", "Revestimiento suelos y paredes", [r"\bparquet", r"\bsolados", r"\bazulejos", r"\btiling"]),
    ("4334", "Pintura y acristalamiento", [r"\bpintor", r"\bpainting\b", r"\bacristalamiento\b"]),
    ("4120", "Construcción de edificios", [r"\bconstruccion de edificios\b", r"\bconstructora\b"]),
    ("4110", "Promoción inmobiliaria",
        [r"\bpromocion inmobiliaria\b", r"\bpromotora inmobiliari", r"\bdesarrollo inmobiliario\b",
         r"\breal estate development\b"]),
    ("4211", "Construcción de carreteras",
        [r"\bcarreteras\b", r"\bautopistas\b", r"\basfalto\b", r"\bobra civil\b"]),
    ("4399", "Construcción especializada", [r"\bconstruccion especializada\b"]),
    # Comercio
    ("4674", "Comercio mayor de ferretería, fontanería, calefacción",
        [r"\bferreter", r"\bproductos de ferreteria\b", r"\bsuministros industriales\b"]),
    ("4752", "Comercio menor de ferretería, pintura y vidrio",
        [r"\bcomercio al por menor de ferreter"]),
    ("4711", "Supermercado", [r"\bsupermercado\b", r"\balimentacion en general\b", r"\bgrocery\b"]),
    ("4690", "Comercio al por mayor no especializado",
        [r"\bcomercio al por mayor.*no especializ", r"\bcomercio por mayor\b", r"\bcomercio al por mayor\b",
         r"\bwholesale\b", r"\bmayorista\b", r"\bimport(?:acion)?\b", r"\bexport(?:acion)?\b",
         r"\bdistribuidora?\b"]),
    ("4791", "Comercio menor por correspondencia/Internet",
        [r"\bcomercio electronico\b", r"\be-?commerce\b", r"\bventa online\b", r"\bventa por internet\b",
         r"\btienda online\b", r"\bdropshipping\b", r"\bmarketplace\b", r"\bonline store\b"]),
    ("4799", "Otros comercios al por menor", [r"\bcomercio al por menor\b"]),
    # Hostelería
    ("5610", "Restaurantes",
        [r"\brestaurante", r"\bhosteleria\b", r"\bservicio de comidas\b", r"\bpizzeria\b",
         r"\bhamburgues", r"\bgastronomi", r"\bcocina\b", r"\bfood truck\b"]),
    ("5621", "Catering", [r"\bcatering\b"]),
    ("5630", "Bares",
        [r"\bbar\b", r"\bcafeteria\b", r"\bcoffee\b", r"\bcoffeeshop\b", r"\bdiscoteca\b", r"\bpub\b"]),
    ("5510", "Hoteles", [r"\bhotel\b", r"\bhostal\b", r"\bpension\b", r"\balojamiento\b", r"\bresort\b"]),
    ("5520", "Alojamientos turísticos", [r"\bapartamento.*turistico", r"\bairbnb\b", r"\bbed and breakfast\b"]),
    # TIC / Software
    ("6201", "Programación informática",
        [r"\bprogramacion informatica\b", r"\bdesarrollo de software\b",
         r"\bdesarrollo de aplicaciones\b", r"\bsoftware\b", r"\bsoftware development\b",
         r"\bdevelopment\b", r"\bcoding\b", r"\bdeveloper", r"\bprogramming\b",
         r"\bweb development\b", r"\bmobile app", r"\bapp development\b",
         r"\bartificial intelligence\b", r"\bmachine learning\b", r"\bdeep learning\b",
         r"\binteligencia artificial\b", r"\bai\b", r"\bia\b"]),
    ("6202", "Consultoría informática",
        [r"\bconsultoria informatica\b", r"\bconsultoria tecnologica\b",
         r"\bit consulting\b", r"\btech consulting\b"]),
    ("6203", "Gestión de recursos informáticos", [r"\bgestion de sistemas informaticos\b"]),
    ("6209", "Otros servicios informáticos",
        [r"\bservicios informaticos\b", r"\bservicios tecnologicos\b",
         r"\binformatica\b", r"\btic\b", r"\btech solutions\b",
         r"\btech\b", r"\btechnology\b", r"\btecnologia\b"]),
    ("6311", "Hosting y procesamiento de datos",
        [r"\bhosting\b", r"\bcentro de datos\b", r"\bproceso de datos\b",
         r"\bdata center\b", r"\bcloud\b", r"\bdatacenter\b", r"\bdata analytics\b",
         r"\bbig data\b", r"\banalytics\b"]),
    ("6312", "Portales web",
        [r"\bportal web\b", r"\bportales web\b", r"\bsaas\b", r"\bplataforma online\b",
         r"\bweb platform\b"]),
    ("5829", "Edición de software / videojuegos",
        [r"\bedicion de software\b", r"\bedicion de videojuegos\b", r"\bvideogame", r"\bgaming\b",
         r"\bgame studio\b"]),
    ("6010", "Radiodifusión", [r"\bradiodifusion\b", r"\bradio\b"]),
    ("6020", "Televisión", [r"\btelevision\b", r"\btv\b"]),
    ("6190", "Telecomunicaciones",
        [r"\btelecomunicaciones\b", r"\btelefonia\b", r"\btelecom", r"\boperador telecom"]),
    # Electrónica
    ("2611", "Componentes electrónicos",
        [r"\bcomponentes electronic", r"\bchips\b", r"\bcircuitos integrados\b",
         r"\bintegrated circuits\b", r"\bsemiconductor", r"\bmicrochip",
         r"\belectronic", r"\belectronica\b", r"\biot\b"]),
    ("2620", "Ordenadores y periféricos", [r"\bordenador.*fabri", r"\bhardware fabricacion\b"]),
    ("2630", "Equipos telecomunicaciones",
        [r"\btelecommunications equipment\b", r"\bequipos de telecomunicaci", r"\bantenas\b"]),
    # I+D
    ("7211", "Biotecnología",
        [r"\bbiotech", r"\bbiotecnologia\b", r"\bbiotechnology\b", r"\bgenom", r"\bgenetic"]),
    ("7219", "I+D ciencias naturales y técnicas",
        [r"\binvestigacion y desarrollo\b", r"\bi\+d\b", r"\bi mas d\b",
         r"\bresearch\b", r"\br&d\b", r"\binnovation\b", r"\binnovacion\b",
         r"\blabs?\b", r"\blaboratorio\b", r"\bresearch lab\b"]),
    ("7220", "I+D ciencias sociales", [r"\binvestigacion social\b"]),
    # Servicios profesionales
    ("6910", "Servicios jurídicos",
        [r"\babogad", r"\bservicios juridicos\b", r"\bdespacho de abogados\b", r"\blegal\b",
         r"\blawyer", r"\blaw firm\b", r"\blaw office\b", r"\bprocurador", r"\bnotaria\b",
         r"\bnotary\b"]),
    ("6920", "Contabilidad, asesoría fiscal",
        [r"\basesoria fiscal\b", r"\bcontabilidad\b", r"\bauditoria\b", r"\bgestoria\b",
         r"\baccounting\b", r"\baccountant", r"\btax advisor"]),
    ("7022", "Consultoría empresarial",
        [r"\bconsultoria de gestion\b", r"\bconsultoria empresarial\b",
         r"\bconsultoria de negocios\b", r"\bconsulting\b", r"\bconsultores\b",
         r"\bmanagement\b", r"\bbusiness advisor", r"\bstrategy"]),
    ("7311", "Publicidad / Marketing",
        [r"\bagencia de publicidad\b", r"\bpublicidad\b", r"\bmarketing\b", r"\badvertising\b",
         r"\bagency\b", r"\bmarketing digital\b",
         r"\bdigital marketing\b", r"\bbranding\b", r"\bcommunications\b"]),
    ("7410", "Diseño",
        [r"\bdiseno especializado\b", r"\bdesign\b", r"\bgraphic design\b", r"\bui ux\b",
         r"\bui/ux\b", r"\bdiseno grafico\b", r"\bdiseno industrial\b"]),
    ("7420", "Fotografía", [r"\bfotografia\b", r"\bfotografo", r"\bphotographer", r"\bphotography\b"]),
    ("7430", "Traducción", [r"\btraduccion\b", r"\btranslation\b", r"\binterpretes\b"]),
    ("7111", "Arquitectura",
        [r"\barquitectura\b", r"\barquitecto", r"\barchitect", r"\barchitecture\b"]),
    ("7112", "Ingeniería",
        [r"\bingenieria\b", r"\bingeniero", r"\bengineering\b", r"\bingenieros\b"]),
    ("7120", "Ensayos y análisis técnicos", [r"\bensayos tecnicos\b", r"\banalisis tecnicos\b", r"\btesting\b"]),
    ("7500", "Veterinaria", [r"\bveterinari", r"\bvet\s+clinic\b"]),
    # Industria alimentaria
    ("1071", "Pan y panadería", [r"\bpanaderia\b", r"\bfabricacion de pan\b", r"\bhorno\b"]),
    ("1102", "Vinos", [r"\bbodega\b", r"\bvinos\b", r"\bwinery\b", r"\bwines\b"]),
    ("1105", "Cerveza", [r"\bcerveza\b", r"\bcerveceria\b", r"\bbeer\b", r"\bbrewery\b"]),
    # Transporte
    ("4932", "Taxi", [r"\btaxi\b", r"\bvtc\b", r"\bcabify\b", r"\buber\b"]),
    ("4941", "Transporte de mercancías por carretera",
        [r"\btransporte de mercanc", r"\btransporte por carretera\b", r"\btransportes\b",
         r"\bfreight\b", r"\btrucking\b", r"\bshipping\b", r"\bcamion"]),
    ("4942", "Mudanzas", [r"\bmudanzas\b", r"\bmoving services\b"]),
    ("5210", "Almacenamiento",
        [r"\balmacenamiento\b", r"\blogistica\b", r"\blogistics\b", r"\bdeposito\b",
         r"\bwarehousing\b", r"\bwarehouse\b", r"\bfulfillment\b"]),
    ("5229", "Otras anexas al transporte", [r"\btransport\b", r"\btransporte\b"]),
    ("5320", "Mensajería", [r"\bmensajeria\b", r"\bpaqueteria\b", r"\bcourier\b", r"\benvios\b", r"\bdelivery\b"]),
    # Energía
    ("3511", "Producción energía eléctrica",
        [r"\benergia electrica\b", r"\bproduccion electrica\b", r"\brenovable",
         r"\bsolar\b", r"\beolic", r"\bphotovoltaic\b", r"\bfotovoltaic", r"\brenewable energy\b"]),
    ("3514", "Comercio de energía", [r"\bcomercializacion de energia\b", r"\benergy trading\b"]),
    # Inmobiliario
    ("6820", "Alquiler inmobiliario",
        [r"\balquiler de inmuebles\b", r"\barrendamiento\b", r"\bpatrimonial\b"]),
    ("6810", "Compraventa inmuebles",
        [r"\bcompraventa de inmuebles\b", r"\bcompraventa de bienes inmobiliari"]),
    ("6831", "Agente propiedad inmobiliaria",
        [r"\bintermediacion inmobiliaria\b", r"\bagentes inmobiliarios\b",
         r"\binmobiliaria\b", r"\breal estate agency\b"]),
    ("6832", "Administración fincas",
        [r"\badministracion de fincas\b", r"\bgestion de comunidades\b"]),
    # Educación
    ("8553", "Autoescuelas", [r"\bautoescuela\b", r"\bcarnet\b"]),
    ("8559", "Otra educación",
        [r"\bense.anza\b", r"\bformacion\b", r"\bacademia\b", r"\bschool of\b",
         r"\bacademy\b", r"\bclases de idiomas\b", r"\blanguage school\b"]),
    # Salud
    ("8610", "Hospitales", [r"\bhospital\b", r"\bclinica privada\b"]),
    ("8623", "Odontología", [r"\bodontolog", r"\bdental\b", r"\bclinica dental\b", r"\bdentist"]),
    ("8690", "Otras sanitarias",
        [r"\bsanitari", r"\bsalud\b", r"\bmedical\b", r"\bhealthcare\b",
         r"\bfisioterapia\b", r"\bpsicologia\b", r"\bpodologia\b"]),
    # Farmacia
    ("4773", "Farmacia",
        [r"\bfarmacia\b", r"\bfarma\b", r"\bpharmacy\b", r"\bdrugstore\b"]),
    # Banca / seguros
    ("6419", "Banca", [r"\bbanco\b", r"\bbanca\b", r"\bbank\b", r"\bbanking\b", r"\bcaja\b"]),
    ("6499", "Otros servicios financieros",
        [r"\bservicios financieros\b", r"\bfintech\b", r"\bfinancial services\b",
         r"\bcrypto\b", r"\bblockchain\b", r"\bcriptomoneda"]),
    ("6512", "Seguros", [r"\bseguros\b", r"\binsurance\b", r"\baseguradora\b"]),
    ("6622", "Correduría seguros", [r"\bcorredur.a de seguros\b", r"\binsurance broker"]),
    # Holding
    ("7010", "Sedes centrales / Holding",
        [r"\bsede central\b", r"\bholding\b", r"\bcartera de sociedades\b",
         r"\btenencia de participaciones\b", r"\binversion\b", r"\binvestment\b",
         r"\bventure capital\b", r"\bprivate equity\b", r"\bventure\b"]),
    # Agricultura
    ("0150", "Producción agrícola combinada",
        [r"\bexplotacion agricola\b", r"\bagricultura\b", r"\bagricola\b", r"\bagrarias?\b",
         r"\bfarming\b", r"\bagro\b"]),
    # Servicios personales
    ("9602", "Peluquería / estética",
        [r"\bpeluqueria\b", r"\bestetica\b", r"\bbarberia\b", r"\bbarber\b",
         r"\bhair salon\b", r"\bbeauty salon\b", r"\bspa\b"]),
    # Automoción
    ("4511", "Venta de coches", [r"\bconcesionario\b", r"\bcar dealer\b"]),
    ("4520", "Taller mecánico", [r"\btaller\b", r"\bauto repair\b", r"\bmecanico\b"]),
    # Gimnasios
    ("9313", "Gimnasios", [r"\bgimnasio\b", r"\bfitness\b", r"\bgym\b", r"\bcrossfit\b"]),
    # Asociaciones
    ("9499", "Asociaciones", [r"\basociaci", r"\bong\b", r"\bfundacion\b", r"\bnonprofit\b", r"\bcharity\b"]),
    # Admin pública
    ("8411", "Administración Pública", [r"\badministracion publica\b", r"\bayuntamiento\b"]),
    # Catch-all
    ("4799", "Comercio menor", [r"\bcomercio al por menor\b", r"\bretail\b", r"\btienda\b", r"\bshop\b", r"\bstore\b"]),
    # ─── Razones sociales con palabras genéricas que dan pistas de actividad ───
    ("4690", "Comercio mayor diversificado",
        [r"\bdistribuidor", r"\bcomercializadora\b", r"\bsuministros\b",
         r"\bsupplies\b", r"\btrading\b", r"\btrade\b"]),
    ("4791", "E-commerce",
        [r"\bonline\b", r"\bweb\b.*\b(tienda|shop|store|sales)", r"\bdigital store"]),
    ("4711", "Alimentación",
        [r"\balimentacion\b", r"\balimentos\b", r"\bfood\b(?!.*delivery)", r"\bgrocery\b"]),
    ("4520", "Taller / Reparación",
        [r"\breparacion\b", r"\brepairs?\b", r"\bservice center\b"]),
    ("5510", "Hotel",
        [r"\bhotel\b", r"\bhostal\b", r"\bhostel\b", r"\balojamiento\b", r"\bresort\b"]),
    ("5520", "Apartamentos turísticos",
        [r"\bapartamentos turistic", r"\bvacation rental", r"\brural rental", r"\bcasa rural"]),
    ("6810", "Inmobiliaria / Alquiler",
        [r"\binmobiliari", r"\breal estate\b", r"\balquiler\b", r"\barrendamiento\b",
         r"\brealty\b", r"\bproperty\b"]),
    # Gestión y administración de fincas (CNAE 6832) — incluye términos en
    # catalán (FINQUES, COMUNITATS), valenciano y castellano.
    ("6832", "Gestión y administración de fincas",
        [r"\bfincas?\b", r"\bfinques?\b", r"\badministrador(?:a|es)?\s+de\s+(fincas|finques)",
         r"\bgestio(?:n)?\s+(de\s+)?(fincas|finques)\b", r"\bcomunitat(?:s)?\s+de\s+propietaris\b",
         r"\bcomunidades?\s+de\s+propietarios\b", r"\bproperty management\b"]),
    ("6920", "Asesoría",
        [r"\basesoria\b", r"\bgestoria\b", r"\bcontable\b", r"\baccounting\b",
         r"\bbookkeep", r"\btax advisor", r"\bsetting up\b"]),
    ("6910", "Abogados / Jurídico",
        [r"\babogad", r"\blaw\b", r"\blegal\b", r"\bjuridic", r"\bnotari"]),
    ("7022", "Consultoría empresarial",
        [r"\bconsultor", r"\bconsulting\b", r"\bbusiness advisor"]),
    ("7311", "Publicidad / Marketing",
        [r"\bpublicidad\b", r"\bmarketing\b", r"\badvertising\b", r"\bagencia de comunic",
         r"\bmedia agency\b", r"\bcampa.as publicitarias\b", r"\bbranding\b"]),
    ("7320", "Estudios de mercado", [r"\bestudios? de mercado\b", r"\bmarket research\b"]),
    ("7410", "Diseño",
        [r"\bdise.o\b", r"\bdesign\b", r"\binterior design", r"\binteriores?\b"]),
    ("7420", "Fotografía",
        [r"\bfotograf", r"\bphotograph", r"\bestudio fotograf"]),
    ("7430", "Traducción",
        [r"\btraduccion", r"\btranslation\b", r"\bsubtitul"]),
    ("7711", "Alquiler de vehículos", [r"\bcar rental\b", r"\balquiler de coches", r"\brent-a-car\b"]),
    ("7820", "ETT / Trabajo temporal",
        [r"\bett\b", r"\btrabajo temporal\b", r"\btempor(?:al|ary) work agency\b"]),
    ("8121", "Limpieza", [r"\blimpieza\b", r"\bcleaning\b", r"\bjanitor", r"\bmaid service\b"]),
    ("8130", "Jardinería",
        [r"\bjardiner", r"\bgardening\b", r"\blandscap", r"\bpaisajis"]),
    ("8559", "Formación / Academia",
        [r"\bacademia\b", r"\bformacion\b", r"\btraining\b", r"\beducation\b",
         r"\bcursos\b", r"\bautoescuela\b"]),
    ("8690", "Servicios sanitarios",
        [r"\bclinica\b", r"\bmedical\b", r"\bsanitaria\b", r"\bcentro medico\b",
         r"\bhealthcare\b", r"\bmedicina\b", r"\bfisioterap"]),
    ("8730", "Residencia mayores",
        [r"\bresidencia\b.*\bmayores\b", r"\bnursing home", r"\bgeriatric"]),
    ("8810", "Servicios sociales para mayores",
        [r"\bservicios sociales\b", r"\bayuda a domicilio"]),
    ("4332", "Carpintería",
        [r"\bcarpinter", r"\bcarpenter", r"\bmadera\b.*\b(taller|fabric)"]),
    ("4329", "Instalaciones",
        [r"\binstalaciones?\b(?!\s+(electric|de\s+aire))", r"\binstaller"]),
    ("4399", "Construcción especializada / Reformas",
        [r"\breformas?\b", r"\bobras?\b(?!.*civil)", r"\brehabilit"]),
    ("0150", "Agricultura/Ganadería",
        [r"\bagrosector", r"\bagroindustri", r"\bganaderi", r"\blivestock\b"]),
    ("2611", "Componentes electrónicos",
        [r"\bcircuitos integrados", r"\bintegrated circuits\b", r"\bsemiconductor",
         r"\bchip\b", r"\bmicroprocesador", r"\belectronic components"]),
    ("2670", "Instrumentos de óptica",
        [r"\bopticos?\b", r"\boptometr", r"\boptics?\b"]),
    ("3320", "Instalación de maquinaria",
        [r"\binstalacion de maquinari", r"\bmachinery installation"]),
    ("2229", "Plásticos", [r"\bplastic", r"\binyeccion plast", r"\binjection mold"]),
    ("2511", "Estructuras metálicas",
        [r"\bestructuras met[aá]lic", r"\bmetal\b.*\bstructur", r"\bsteel structur"]),
    ("2562", "Mecanizado", [r"\bmecanizado\b", r"\bmachining\b", r"\bcnc\b"]),
    ("1071", "Panadería / Pastelería",
        [r"\bpanaderia\b", r"\bbakery\b", r"\bpasteler", r"\brepostería\b", r"\bdulcer"]),
    ("1101", "Bebidas / Destilería",
        [r"\bdestiler", r"\bbodega\b", r"\bwinery\b", r"\bbrewery\b", r"\bcerveza"]),
    ("4322", "Climatización",
        [r"\bclimat", r"\bventilaci.n\b", r"\bcalefacci"]),
    ("9521", "Reparación electrónica de consumo",
        [r"\brepair.*electronic", r"\brepair.*phone", r"\bmovil.*reparaci"]),
    ("9522", "Reparación electrodomésticos",
        [r"\belectrodomestic"]),
    ("9329", "Actividades recreativas",
        [r"\brecreativ", r"\bentertainment\b", r"\beventos\b", r"\bevents agency\b"]),
    # ─── Construcción especializada ──────────────────────────────
    ("4329", "Instalaciones diversas",
        [r"\bcanaletas?\b", r"\bcanalons?\b", r"\bchimeneas?\b", r"\bxemeneies?\b",
         r"\bguniajes?\b", r"\baislamientos?\b"]),
    ("4211", "Construcción carreteras / asfalto",
        [r"\basfaltos?\b", r"\bpavimentaciones?\b", r"\bafirmados?\b", r"\bobras\s+publicas\b"]),
    ("4334", "Pintura / acabados",
        [r"\bpinturas?\b", r"\blacados?\b", r"\bbarnizados?\b",
         r"\bdecoraciones?\s+(de\s+)?(pintura|interior)\b"]),
    ("4391", "Cubiertas / tejados",
        [r"\bcubiertas?\b", r"\btejados?\b", r"\bteulades?\b", r"\bimpermeabiliz"]),
    ("4332", "Carpintería madera / metálica",
        [r"\bcarpinter.a\s+(de\s+)?(madera|metalica|aluminio|pvc)?\b",
         r"\baluminios?\b", r"\bpvc\b"]),
    ("4322", "Climatización / fontanería",
        [r"\bfontaner.a\b", r"\binstalaciones?\s+termicas?\b", r"\bsanitarios?\b.*\binstalaciones?\b"]),
    ("4321", "Instalaciones eléctricas",
        [r"\belectro\s+", r"\binstaladores?\s+electricos?\b", r"\binstalaciones?\s+electricas?\b"]),
    # ─── Comercio minorista por categoría ─────────────────────────
    ("4719", "Comercio menor diversificado",
        [r"\bgrandes\s+almacenes\b", r"\balmacenes?\b(?!\s+mayoristas)"]),
    ("4711", "Supermercado / Alimentación",
        [r"\bsupermercados?\b", r"\bsuper\b", r"\bcomestibles?\b"]),
    ("4722", "Carnicería / Charcutería",
        [r"\bcarnicer.as?\b", r"\bcharcuter.as?\b", r"\bembutidos?\b", r"\bjamones?\b", r"\bmataderos?\b"]),
    ("4723", "Pescadería", [r"\bpescader.as?\b", r"\bmariscos?\b"]),
    ("4724", "Panadería minorista",
        [r"\bpanader.as?\b(?!.*industrial)", r"\bbolleria\b", r"\bobrador\b"]),
    ("4725", "Bebidas", [r"\bbodegas?\b(?!.*winery)", r"\blicores?\b"]),
    ("4731", "Gasolinera", [r"\bgasolineras?\b", r"\bestaciones?\s+de\s+servicio\b"]),
    ("4741", "Comercio menor informática",
        [r"\binformatica\b.*\bventa\b", r"\bordenadores?\b.*\bventa\b"]),
    ("4751", "Comercio menor textil",
        [r"\bmoda\b", r"\btextil\b.*\bventa\b", r"\bboutique\b", r"\bropas?\b"]),
    ("4759", "Muebles / electrodomésticos",
        [r"\bmuebles?\b(?!.*fabric)", r"\bmobiliario\b"]),
    ("4761", "Librerías", [r"\blibrer.as?\b", r"\bpaper.r.as?\b", r"\bestancos?\b"]),
    ("4771", "Comercio menor ropa", [r"\btiendas?\s+de\s+ropa\b", r"\bzapater.as?\b"]),
    ("4773", "Farmacia / Parafarmacia",
        [r"\bfarmacias?\b", r"\bparafarmacias?\b", r"\bbotic.s?\b"]),
    ("4774", "Comercio menor productos médicos",
        [r"\bortopedi.s?\b", r"\bortopedicos?\b"]),
    # ─── Servicios profesionales adicionales ──────────────────────
    ("7120", "Ensayos / Análisis técnicos",
        [r"\bcertificaciones?\b", r"\bcertify\b", r"\bensayos?\s+tecnicos?\b",
         r"\binspecciones?\s+tecnicas?\b", r"\bcontroles?\s+de\s+calidad\b", r"\btasacion(?:es)?\b",
         r"\btinsa\b"]),
    ("6831", "Agencia inmobiliaria",
        [r"\bagencia\s+inmobiliari", r"\bapi\b.*\binmobiliari", r"\bcorredor\s+de\s+fincas?\b"]),
    # ─── Alimentación industrial ──────────────────────────────────
    ("1013", "Productos cárnicos elaborados",
        [r"\bembutidos?\s+(de|y|industria)\b", r"\bjamones?\s+(industria|fabric|seleccion)\b",
         r"\bproductos\s+carnicos?\b"]),
    ("1011", "Mataderos / cárnico",
        [r"\bmataderos?\s+industria"]),
    ("1071", "Panadería / Pastelería industrial",
        [r"\bpanader.a\s+industrial\b", r"\bpasteler.a\s+industrial\b", r"\brepostería\s+industrial\b"]),
    ("1052", "Helados", [r"\bhelados?\b", r"\bgelater"]),
    ("1051", "Lácteos", [r"\bquesos?\b(?!.*tienda)", r"\blacteos?\b", r"\bleche\s+(de|fabrica)\b"]),
    ("1083", "Café / té procesado", [r"\btorrefactos?\b", r"\bcafe\s+(tueste|industrial)"]),
    # ─── Manufactura adicional ────────────────────────────────────
    ("2599", "Productos metálicos diversos",
        [r"\bmetalisteria\b", r"\btalleres?\s+met.licos?\b", r"\bferralla\b",
         r"\bcerrajer.as?\b"]),
    ("2511", "Estructuras metálicas para construcción",
        [r"\bestructuras\s+met.licas\b", r"\bcalderer.a\b"]),
    ("3030", "Aeronáutica", [r"\baerospaci.l\b", r"\baeronautic", r"\baerospace\b"]),
    ("2910", "Vehículos motor", [r"\bautomocion\b", r"\bautomotive\b", r"\bautomovil(?:es)?\b.*\bfabric"]),
    ("2932", "Componentes vehículos",
        [r"\bcomponentes?\s+(de|para)?\s+(auto|vehicul)", r"\bauto\s+parts\b",
         r"\bficosa\b", r"\bgestamp\b"]),  # marcas grandes españolas auto
    # ─── Turismo / Hostelería ─────────────────────────────────────
    ("7911", "Agencia de viajes", [r"\bagencia\s+de\s+viajes\b", r"\btravel\s+agency\b"]),
    ("7912", "Operador turístico",
        [r"\btour\s+operator\b", r"\btui\b", r"\boperador\s+turistico\b",
         r"\bglobalia\b", r"\bbarcelo\s+(travel|viajes)\b"]),
    ("5520", "Apartamentos turísticos / Cámping",
        [r"\bcamping\b", r"\bvacation\s+rental\b", r"\bbungalow", r"\bglamping\b"]),
    # ─── Industria textil / cuero ─────────────────────────────────
    ("1411", "Confección cuero / peletería",
        [r"\bpeletero?s?\b", r"\bcuero\s+(fabric|industria)\b", r"\bcurtidor"]),
    ("1413", "Confección prendas vestir",
        [r"\bconfecciones?\s+textil", r"\bconfecciones?\b.*\bropa\b", r"\bmodista\b"]),
    ("1512", "Marroquinería", [r"\bmarroquin", r"\bbolsos?\s+industria"]),
    ("1520", "Calzado", [r"\bcalzados?\b", r"\bzapatos?\s+fabric"]),
    # ─── Energía / utilities ──────────────────────────────────────
    ("3511", "Energía eléctrica",
        [r"\benergias?\s+renovables?\b", r"\bphotovoltaic", r"\bfotovoltaic",
         r"\beolica\b", r"\bsolar\s+(energ|fotov)\b", r"\bgeneraci.n\s+electric"]),
    ("3522", "Distribución gas", [r"\bgas\s+(natural|distribuc)\b"]),
    ("3600", "Agua / Saneamiento",
        [r"\baguas?\b(?!.*minerales)", r"\bdepuracion\b", r"\bsaneamiento\b"]),
    # ─── Transporte / Logística ───────────────────────────────────
    ("4941", "Transporte mercancías por carretera",
        [r"\btransportes?\b(?!.*pasajeros|aereos)", r"\blog.stica\b", r"\bdistribuidoras?\s+transport",
         r"\bcamiones?\s+(transporte|mercanc)"]),
    ("4939", "Transporte pasajeros", [r"\bautocares?\b", r"\bautobuses?\b.*\bservicios?\b"]),
    ("5224", "Estiba / portuaria", [r"\bestiba\b", r"\bportuari", r"\bpuertos?\b.*\bservicios?\b"]),
    ("5310", "Postal", [r"\bcorreos?\b.*\bservicios?\b", r"\bmensajer.as?\b", r"\bpaqueter.a\b"]),
    # ─── Servicios financieros adicionales ────────────────────────
    ("6630", "Gestoras inversión",
        [r"\bgestoras?\b(?!\s+de\s+(fincas|propied))", r"\bsicav\b", r"\bsgiic\b"]),
    ("6420", "Holdings",
        [r"\bpatrimonial\b", r"\binmuebles?\s+sa\b", r"\bpatrimoni.l\b", r"\bpatromi"]),
    # ─── Educación adicional ──────────────────────────────────────
    ("8520", "Educación primaria", [r"\bcolegios?\b(?!.*profesional)", r"\bescuela\b.*\bprimaria\b"]),
    ("8531", "Educación secundaria", [r"\binstitutos?\b.*\beducacion\b", r"\beso\b.*\binstituto\b"]),
    ("8551", "Escuelas deportivas",
        [r"\bescuela\s+(de\s+)?(futbol|tenis|deport|baloncesto)\b"]),
    ("8552", "Escuelas artísticas",
        [r"\bescuela\s+(de\s+)?(musica|arte|dibujo|pintura)\b", r"\bconservatorios?\b"]),
    ("8553", "Autoescuelas", [r"\bautoescuelas?\b", r"\bcarn.t\s+de\s+conducir\b"]),
    # ─── Salud adicional ──────────────────────────────────────────
    ("8623", "Odontología", [r"\bdental(?:es)?\b", r"\bodontolog", r"\bdentistas?\b"]),
    ("8622", "Especialidades médicas",
        [r"\boftal", r"\bcardiol", r"\bdermatol", r"\bpodolog"]),
    ("8690", "Otros servicios sanitarios",
        [r"\benfermer.a\b", r"\bvacunac"]),
    # ─── Software y tecnología (más específicos) ─────────────────
    ("6201", "Desarrollo de software",
        [r"\bdesarrollo\s+(de\s+)?software\b", r"\bdesarrolladores\b",
         r"\bprogramaci.n\b", r"\bsoftware\s+development\b", r"\bcoding\b"]),
    ("6202", "Consultoría tecnológica",
        [r"\bit\s+consulting\b", r"\bconsultor.a\s+inform.tica\b", r"\bdevops\b",
         r"\bcloud\s+(architect|consulting|migration)\b"]),
    ("6203", "Gestión de recursos informáticos", [r"\bhosting\b", r"\bdatacenter\b"]),
    ("6209", "Otros servicios informáticos",
        [r"\binform.tica\b(?!.*industria)", r"\bsistemas?\s+inform"]),
    ("5829", "Edición software",
        [r"\bsaas\b", r"\bplatform\b.*\bsoftware\b", r"\bedicion\s+software\b"]),
    # ─── Servicios varios ─────────────────────────────────────────
    ("8010", "Seguridad privada",
        [r"\bseguridad\s+privada\b", r"\bvigilancia\b", r"\bsecuritas?\b", r"\bprosegur\b"]),
    ("8122", "Limpieza industrial",
        [r"\blimpieza\s+(industria|profesion|edificios)"]),
    ("8129", "Otras limpiezas",
        [r"\bdesinfeccion\b", r"\bdesinsectacion\b", r"\bplagas\b"]),
    ("9601", "Lavandería / Tintorería",
        [r"\blavander.as?\b", r"\btintorer.as?\b"]),
    ("9603", "Funerarias", [r"\bfunerar", r"\btanatorios?\b"]),
    ("9609", "Otros servicios personales",
        [r"\btatuajes?\b", r"\bpiercings?\b"]),
    # ─── Agricultura específica ───────────────────────────────────
    ("0111", "Cultivo cereales", [r"\bcereal(?:es)?\b.*\bproduc"]),
    ("0121", "Viñedos", [r"\bvi.edos?\b", r"\buvas?\b.*\bproduc", r"\bvinos?\b.*\bproduc"]),
    ("0125", "Frutos secos", [r"\balmendros?\b", r"\bavellanas?\b"]),
    ("0146", "Porcino", [r"\bporcinos?\b", r"\bcerdos?\b.*\bgranja\b"]),
    ("0147", "Avicultura", [r"\bavicultura\b", r"\bgranja\s+de\s+aves\b", r"\bhuevos?\b.*\bproduc"]),
    ("0161", "Servicios apoyo agricultura",
        [r"\bagricola\b.*\bservicios?\b", r"\bcoseachadoras?\b", r"\bmaquinaria\s+agric"]),
    ("0312", "Acuicultura", [r"\bacuicultura\b", r"\bpiscifactor"]),
    # ─── Pesca ────────────────────────────────────────────────────
    ("0311", "Pesca marítima", [r"\bpesquer", r"\bbarcos?\s+pesqueros?\b", r"\barmador"]),
    # ─── Construcción adicional ───────────────────────────────────
    ("4399", "Construcción especializada",
        [r"\bserviconstar\b", r"\bgrupo\s+constructora\b", r"\bconstrucciones\b(?!.*madrid)"]),
    # ─── Servicios diversos comunes en BORME ──────────────────────
    ("7740", "Arrendamiento propiedad intelectual",
        [r"\bfranquici"]),
    ("8230", "Organización eventos",
        [r"\beventos?\b.*\borganiz", r"\bcatering\s+y\s+eventos\b"]),
    ("7912", "Operador turístico (extra)",
        [r"\bbarcelona\s+pro\s+service\b"]),  # ejemplo del audit
    # ─── Más patterns observados en BORME/Empresite ──────────────
    ("4520", "Taller / Neumáticos",
        [r"\bneum.ticos?\b", r"\bllantas?\b.*\bmontaj", r"\bauto\s+repair\b"]),
    ("4322", "Riego / Instalaciones agua",
        [r"\briegos?\b", r"\baspersores?\b", r"\bgoteo\b.*\binstalac"]),
    ("3320", "Montaje maquinaria / Instalaciones industriales",
        [r"\bmontajes?\b(?!.*musicales)", r"\bmontadores?\b"]),
    ("4321", "Electricidad / Iluminación",
        [r"\belectrollar\b", r"\bilum?inacion\b", r"\belectroservicios?\b",
         r"\belectro\b.*\b(comerc|venta|distribu|industr)"]),
    ("1071", "Panadería tradicional (catalán)",
        [r"\bforns?\b", r"\bpanaderia\s+tradicional\b", r"\bobradores?\b"]),
    ("5610", "Comida rápida / Restaurante express",
        [r"\bquick\s+meals?\b", r"\bfast\s+food\b", r"\bcomida\s+r.pida\b"]),
    ("6810", "Inmobiliaria (catalán/inglés)",
        [r"\bimmo\b", r"\bimmobili", r"\bbienes\s+ra.ces\b"]),
    ("6202", "Tech / Soluciones digitales",
        [r"\bsoluci(?:ones|on|ó)\b.*\b(digit|tech|info)", r"\bplanyver\b",
         r"\bplaning\b.*\bdigital", r"\bbusiness\s+intelligence\b", r"\bbi\b.*\bservicios"]),
    ("6920", "Asesoría fiscal / contable",
        [r"\basesoramiento\b.*\bfiscal", r"\bdespacho\s+(fiscal|contable)"]),
    ("7022", "Consulting / Servicios negocio",
        [r"\bbusiness\s+(service|services)\b", r"\bmanagement\s+consult"]),
    ("4399", "Construcción / Obras genéricas",
        [r"\bgrupo\s+constructor\b", r"\bedificaciones?\b", r"\bedificios?\b.*\bconstruccion"]),
    ("4321", "Servicios eléctricos / Electrodomésticos",
        [r"\belectro\b\s+\w+", r"\bservicios?\s+electricos?\b"]),
    ("4791", "Comercio online",
        [r"\be-?commerce\b", r"\btienda\s+online\b", r"\bventa\s+on\s*line\b"]),
    ("0162", "Servicios animales / Veterinaria",
        [r"\bveterinari", r"\banimales?\b.*\b(servicios?|clini)", r"\bclinica\s+animal"]),
    ("9521", "Reparación informática",
        [r"\breparacion\b.*\b(ordenadores?|computers?)", r"\bservicios?\s+tecnicos?\s+inform"]),
    # ─── Patterns muy genéricos: si el nombre menciona estos términos
    # SUELE indicar actividad de servicios o construcción ──────────
    ("4399", "Construcción especializada (extra)",
        [r"\binstalaciones?\b", r"\bmantenimient(?:o|os)?\b", r"\bestructuras?\b"]),
    ("8299", "Apoyo administrativo",
        [r"\boutsourcing\b", r"\badministrative\s+support\b", r"\bback\s*office\b"]),
    # ─── PATRONES adicionales del audit (lote 3) ─────────────────────
    # Holdings / Carteras / Inversiones
    ("6420", "Holding / Cartera de valores",
        [r"\bcarteras?\b(?!\s+de\s+(viajes|conductor))", r"\binversiones?\b", r"\binvestments?\b",
         r"\binvest\b", r"\bventures?\b", r"\bcapital\b(?!.*humano)", r"\bspv\b",
         r"\bequity\b", r"\bpatrimoni\w+\s+(sl|sa)\b", r"\bholdings?\b(?!.*partner)"]),
    # Promotores / Inmobiliario
    ("4110", "Promoción inmobiliaria",
        [r"\bpromotor(?:a|es)\b", r"\bpromociones?\b", r"\bpromoci\b", r"\bproperties\b"]),
    # Eventos
    ("8230", "Organización de eventos / Congresos",
        [r"\beventos?\b", r"\bevents?\b", r"\bcongresos?\b", r"\bferias?\b", r"\bbodas?\b.*\borganiz",
         r"\bwedding\s+planner\b"]),
    # Materiales construcción
    ("2331", "Azulejos / Baldosas",
        [r"\bmosaicos?\b", r"\bmosaics?\b", r"\bbaldosas?\b", r"\bazulejos?\b", r"\bgresites?\b"]),
    ("2370", "Piedra / Mármol",
        [r"\bm.rmol(?:es)?\b", r"\bgranitos?\b", r"\bpiedra\s+natural\b"]),
    # Joyería
    ("1820", "Joyería / Bisutería",
        [r"\bjoyas?\b", r"\bjoyer.as?\b", r"\bgemstones?\b", r"\bbisuter.as?\b"]),
    # Servicios sociales
    ("8810", "Servicios sociales",
        [r"\bintegracion\s+social\b", r"\bservicios?\s+sociales?\b", r"\binclusi.n\s+social\b"]),
    # Advisory / consulting
    ("7022", "Consultoría empresarial (extra)",
        [r"\badvisory\b", r"\badvisors?\b", r"\bsolutions?\s+(group|sl|sa)\b",
         r"\bconsult\w*\b", r"\basesores?\b", r"\bassessors?\b", r"\bnasco\b.*\basesor",
         r"\bsolutions?\s+empresarial\b", r"\bsoluciones?\s+empresarial\b",
         r"\bpartners\b(?!\s+health)"]),
    # Telecomunicaciones / TI
    ("6110", "Telecomunicaciones",
        [r"\btelematica\b", r"\bnetworks?\b", r"\btelecom\w*\b"]),
    # Investigación científica
    ("7211", "Investigación I+D biomédica",
        [r"\btherapeutics?\b", r"\bbiotech\s+(research|innov)\b", r"\bpharma\s+(research|innov)\b",
         r"\bclinical\s+research\b"]),
    # Caucho / plásticos
    ("2219", "Productos caucho",
        [r"\bcauchos?\b"]),
    ("2929", "Otros vehículos",
        [r"\biveco\b", r"\bvolvo\s+(camiones?|trucks)\b"]),
    # Bebidas / zumos
    ("1032", "Zumos / Frutas en conserva",
        [r"\bzumer.as?\b", r"\bzumos?\s+(industria|fabric)", r"\bsuperfru\w+\b"]),
    # Energía renovable específica
    ("3511", "Eólica (extra)",
        [r"\bibereolica\b", r"\bvoltan\b.*\benergia\b", r"\bsolar\s+\w+\s+(sa|sl)\b",
         r"\brenewables?\b"]),
    # Logística / Supply chain
    ("5229", "Logística / Supply chain",
        [r"\bsupply\s+chain\b", r"\blogistic\w*\b", r"\bfreight\b"]),
    # Restaurante variantes
    ("5610", "Take away / Delivery",
        [r"\btake\s*away\b", r"\bdelivery\s+(food|comida)\b"]),
    # Transporte específico
    ("4932", "Taxis",
        [r"\bteletaxi\b", r"\btaxis?\s+(serv|coopera)", r"\bvtc\b"]),
    ("5022", "Transporte marítimo de pasajeros",
        [r"\bcharter\s+(naval|barco|yates?)", r"\bnaval\s+charter\b"]),
    # Salud (catalán/idiomas)
    ("8690", "Salud / Salut",
        [r"\bsalut\b", r"\bsa[uú]de\b", r"\bclinica\s+\w+\s+sl\b"]),
    # Ingeniería técnica
    ("7112", "Ingeniería / Servicios técnicos",
        [r"\btechnical\s+services?\b", r"\bservicios?\s+t.cnicos?\b(?!.*reparac)", r"\bingenieria\b"]),
    # Beauty / estética (extra)
    ("9602", "Beauty / Spa (extra)",
        [r"\bbeauty\b", r"\bsupernova\s+beauty\b"]),
    # Cripto / fintech (extra)
    ("6499", "Fintech / Crypto (extra)",
        [r"\bquantum\s+\w+\s+(partners|capital)\b", r"\bblockchain\b"]),
    # Escuela / educación privada
    ("8531", "Escuela privada",
        [r"\bschool\b(?!.*business)", r"\bcollege\b", r"\bacademy\b(?!.*hair)"]),
    # Servicios digitales
    ("6202", "Servicios digitales / IT consultoría (extra)",
        [r"\bdigital\s+services\b", r"\bvelada\s+digital\b",
         r"\bbluetab\b", r"\btech\s+solutions?\b"]),
    # Tour / Aventura
    ("9329", "Actividades recreativas (extra)",
        [r"\bsurf\s+(boards|escuela|center)", r"\bcrab\s+surf\b", r"\baventura\b.*\bturism"]),
    # Auxiliares específicos
    ("8211", "Servicios secretariado / admin",
        [r"\bauxtegra\b", r"\bservicios?\s+(de\s+)?secretari", r"\bsecretar"]),
    # Genéricos finales — más permisivos
    ("8299", "Otros servicios apoyo a empresas",
        [r"\bsoluciones?\b", r"\bsolutions?\b", r"\bservicios?\s+empresarial",
         r"\bgrupo\b\s+\w+\s+(sl|sa)\b"]),
    # ─── Multiidioma — catalán / gallego / euskera ───
    # Catalán
    ("4520", "Taller / Reparación",
        [r"\btaller(?:s)?\b", r"\breparaci[oó]n?\b"]),
    ("4391", "Construcción cubiertas / Reformas",
        [r"\bobres?\b", r"\bconstrucci[oó]\b"]),
    ("4519", "Venta vehículos",
        [r"\bconcessionari\b", r"\bvenda\s+de\s+cotxes\b"]),
    ("5610", "Restaurante",
        [r"\brestaurant\b", r"\bmenjar\b"]),
    ("5630", "Bar",
        [r"\bcafeteria\b", r"\bbar\s+de\b"]),
    # Gallego
    ("4520", "Taller / Reparación",
        [r"\btalleres?\b"]),
    ("5610", "Restaurante",
        [r"\brestauración\b", r"\bcomidas?\b"]),
    # Euskera
    ("5610", "Jatetxe / Restaurante",
        [r"\bjatetxe\b"]),
    ("4799", "Tienda / Denda",
        [r"\bdenda\b"]),
    # ─── Tipos jurídicos comunes que dan pista de actividad ───
    ("9411", "Asociación profesional",
        [r"\bcolegio\s+(profesional|de\s+)", r"\bassociació\s+professional\b"]),

    # ════════════════════════════════════════════════════════════════════
    # BLOQUE EXPANSIÓN MASIVA — multiidioma + industrias específicas
    # Para reducir el % que cae al fallback "Otros servicios" 8299
    # ════════════════════════════════════════════════════════════════════

    # ── HORMIGÓN, CEMENTO, ÁRIDOS (catalán + castellano + gallego) ──
    ("2363", "Fabricación elementos hormigón",
        [r"\bformigons?\b", r"\bhormigon(?:es)?\b", r"\bhormigon\s+(prep|preparado)\b",
         r"\bcementos?\b(?!.*tienda)", r"\bcement\b", r"\bcemento\s+pretensado\b",
         r"\bprefabricados\s+de\s+hormig", r"\bbetón\b"]),
    ("0812", "Extracción gravas y arenas",
        [r"\baridos\b", r"\bgravas?\s+(y\s+arenas?|extrac)", r"\bcantera\b", r"\bquarry\b",
         r"\bextraccion\s+(de\s+)?(arena|grava)"]),
    ("2351", "Fabricación cemento",
        [r"\bcementera\b", r"\bfabrica(cion)?\s+de\s+cemento\b"]),
    ("2370", "Corte piedra ornamental",
        [r"\bpiedras?\s+(natural|ornament|marmol)", r"\bmarbres?\b", r"\bmarmoles?\b", r"\bgranitos?\b"]),

    # ── METAL, MECÁNICA, MAQUINARIA ──
    ("2511", "Estructuras metálicas",
        [r"\bestructur.*metalic", r"\bcalderer", r"\bmetalisteria\b", r"\bmetalist[ée]r"]),
    ("2562", "Mecanizado metal",
        [r"\bmecaniz", r"\btorneria\b", r"\bdecolet", r"\bdecoletaje\b", r"\bmetal mechanical\b"]),
    ("2511", "Cerrajería",
        [r"\bcerrajeri", r"\bserraller", r"\blocksmith\b", r"\bferreteri.*industri"]),
    ("2599", "Otros productos metálicos",
        [r"\bproductos?\s+metalic", r"\bfabrica.*metal"]),
    ("2410", "Siderurgia / Acero",
        [r"\bsiderurgi", r"\bacer(?:os|er)", r"\bsteel\s+(works|fabric|industri)"]),
    ("2825", "Maquinaria frío/ventilación",
        [r"\brefrigeracio\b", r"\brefrigeracion\b", r"\bfrigorific", r"\bcamaras?\s+frigorif",
         r"\bventilacion\s+industri", r"\baire\s+industrial\b"]),
    ("2899", "Maquinaria fines especiales",
        [r"\bmaquinaria(?:s)?\b", r"\bmachinery\b", r"\bequipos?\s+industri",
         r"\bmaquinas?\s+(industri|herrami)"]),
    ("2829", "Maquinaria uso general",
        [r"\bcompresor", r"\bbombas?\s+industri", r"\bvalvulas?\s+industri"]),
    ("3320", "Instalación maquinaria industrial",
        [r"\binstalacion(es)?\s+industriales?\b", r"\bmontajes?\s+industri",
         r"\bmontadores?\s+industri", r"\binstalaci\w+\s+de\s+maquinaria\b"]),

    # ── VEHÍCULOS (más detalle) ──
    ("4540", "Motocicletas (concesionario)",
        [r"\bmotos?\b(?!\s+r[uú]ed)", r"\bmotorcycle", r"\bscooters?\b", r"\bmoto\s+sport\b",
         r"\bbiciclet"]),
    ("4511", "Concesionario coches",
        [r"\bconcesionari", r"\bautomocion\s+(?:concesi|venta)", r"\bventa\s+de\s+vehicul",
         r"\bcar dealer", r"\bautomobiles?\s+venta"]),
    ("4520", "Taller mecánica/electricidad coche",
        [r"\belectromecani", r"\bauto(?:mecanic|mecan)", r"\bmecanic.*aut", r"\btaller mecanic",
         r"\bcar repair", r"\bauto repair", r"\bworkshop\s+mechanic"]),

    # ── AGRICULTURA, PESCA, GANADERÍA ──
    ("0111", "Cereales y leguminosas",
        [r"\bcereal", r"\btrigo\b", r"\bcebada\b", r"\bavena\b", r"\bsemil"]),
    ("0121", "Viticultura / Uvas",
        [r"\bviticult", r"\bvineyard", r"\bvinya\b", r"\buvas?\b\s*(produc|cultiv)",
         r"\bvinhedo\b"]),
    ("0210", "Silvicultura / Forestal",
        [r"\bforestal", r"\bsilvicult", r"\bforestry\b", r"\bbosques?\s+(gestion|explo)"]),
    ("0311", "Pesca marítima",
        [r"\bpesca\s+(maritim|altur|extrac)", r"\bbarco\s+de\s+pesca", r"\bfishing\s+boat"]),
    ("0322", "Acuicultura agua dulce",
        [r"\bacuicultur", r"\bpiscicult", r"\baquaculture", r"\bsalmonicult"]),

    # ── ALIMENTACIÓN INDUSTRIAL ──
    ("1011", "Carne (matadero)",
        [r"\bmatadero\b", r"\bdespiece\s+(de\s+)?carne", r"\bslaughter"]),
    ("1013", "Productos cárnicos",
        [r"\bcharcuter", r"\bsalchicheri", r"\bembutidos?\s+(taller|fabric|industri)",
         r"\bxarcuteri", r"\bxarcuter"]),
    ("1020", "Pescado conservas",
        [r"\bconservas?\s+(pescado|de\s+pescado|maritim)", r"\bahumados\s+pescado",
         r"\bsalazones?\b"]),
    ("1052", "Heladería industrial",
        [r"\bheladeri", r"\bhelados?\s+(fabric|industri)", r"\bice\s+cream\s+fabric"]),
    ("1085", "Comidas preparadas",
        [r"\bcomidas?\s+preparadas?", r"\bplatos?\s+(preparad|cocinad|listos)",
         r"\bready\s+meal"]),
    ("1086", "Productos dietéticos",
        [r"\bdiet[ée]tic", r"\bsupplements?\s+(food|nutri)", r"\bnutricion\s+(suplem|fabric)"]),
    ("1102", "Vinos",
        [r"\bvinos?\b(?!\s+(tienda|venta))", r"\bbodega\b", r"\bcellars?\b",
         r"\belaboracion\s+vinos?", r"\bcellers?\b"]),
    ("1107", "Bebidas no alcohólicas",
        [r"\brefrescos?\b", r"\bbebidas?\s+(refrescant|isoton|gasificada|sin\s+alcohol)"]),
    ("1106", "Malta",
        [r"\bmalta\b", r"\bmalteri"]),
    ("1105", "Cerveza",
        [r"\bcervecer", r"\bcervezas?\b", r"\bbrewery", r"\bbrewing\b"]),

    # ── TEXTIL, CONFECCIÓN ──
    ("1310", "Hilados textil",
        [r"\bhilados?\b", r"\bhilatura", r"\bspinning\s+textile"]),
    ("1320", "Tejeduría textil",
        [r"\btejedur", r"\btejidos?\b", r"\bweaving", r"\bfabricacion\s+tejidos?\b"]),
    ("1391", "Géneros de punto",
        [r"\bpunto\s+(fabric|industri|prendas)", r"\bknitting"]),
    ("1412", "Ropa de trabajo",
        [r"\bropa\s+(trabajo|laboral|profesional)", r"\bvestuario\s+laboral",
         r"\bworkwear", r"\buniformes?\s+(fabric|industri)"]),
    ("1413", "Prendas exteriores",
        [r"\bconfeccion", r"\btextil\s+(fabric|industri|confecc)",
         r"\bprendas?\s+(de\s+)?vestir\s+(fabric|confecc)", r"\bclothing\s+manufact"]),
    ("1414", "Ropa interior",
        [r"\bropa\s+interior", r"\bcorseteria", r"\blencer"]),
    ("1419", "Otras prendas y accesorios",
        [r"\bbisuteri", r"\baccesorios?\s+(moda|vestir|fabric)", r"\bcomplementos?\s+moda"]),

    # ── QUÍMICA, PLÁSTICOS ──
    ("2014", "Productos químicos orgánicos básicos",
        [r"\bquimicas?\s+(industri|fabric|basica)", r"\bchemical\s+industri"]),
    ("2030", "Pinturas y barnices",
        [r"\bpinturas?\b(?!\s+(tienda|venta|obra))", r"\bbarnices?\b", r"\besmaltes?\b",
         r"\bpaints?\s+manufact"]),
    ("2041", "Jabones y detergentes",
        [r"\bjabones?\s+(fabric|industri)", r"\bdetergentes?\b", r"\blimpiez.*fabric"]),
    ("2042", "Cosméticos",
        [r"\bcosmetic", r"\bperfumeri.*fabric", r"\bperfumes?\s+(fabric|industri)",
         r"\bbeauty\s+products"]),
    ("2059", "Otros productos químicos",
        [r"\bproductos?\s+quimicos?\b"]),
    ("2110", "Productos farmacéuticos base",
        [r"\bfarmaceutic.*fabric", r"\bquimica\s+farmaceutic", r"\bapi\s+farmaceutic"]),
    ("2120", "Especialidades farmacéuticas",
        [r"\blaboratorio\s+farmaceut", r"\bpharm(?:aceutic)?al\s+(?:labor|fabric|industri)"]),
    ("2211", "Neumáticos",
        [r"\bneumaticos?\b", r"\btyre\s+(manufact|fabric)", r"\bllantas?\s+(fabric|industri)"]),
    ("2221", "Placas plástico",
        [r"\bplastic\s+(fabric|industri)", r"\bplasticos?\b(?!\s+(tienda|venta))",
         r"\bextrusion\s+plastic"]),
    ("2222", "Envases plástico",
        [r"\benvas.*plastic", r"\benvas.*flex", r"\bpackag.*plastic", r"\benvases?\s+(fabric|industri)"]),

    # ── SECTOR INMOBILIARIO + LIMPIEZA + JARDINERÍA ──
    ("8121", "Limpieza general edificios",
        [r"\blimpieza\s+(general|integral|edifici)", r"\blimpiezas\s+industri",
         r"\bcleaning\s+(services|industri|building)", r"\bnetej.*industrial"]),
    ("8129", "Limpieza industrial especializada",
        [r"\blimpieza\s+industri", r"\blimpieza\s+especializ", r"\bfacility\s+services\b"]),
    ("8130", "Jardinería paisajística",
        [r"\bjardiner", r"\bpaisajism", r"\blandscap", r"\bgardening\b",
         r"\bjardiniers?\b"]),
    ("8110", "Servicios integrales edificios",
        [r"\bfacility\s+management\b", r"\bgestion\s+integral\s+edif",
         r"\bbuilding\s+services"]),

    # ── SEGURIDAD ──
    ("8010", "Seguridad privada",
        [r"\bseguridad\s+privada", r"\bvigilanc", r"\bsecurity\s+service",
         r"\bguardias?\s+de\s+seguridad", r"\bseguretat\s+priv"]),
    ("8020", "Sistemas seguridad/alarmas",
        [r"\balarmas?\b", r"\bsistemas?\s+(de\s+)?seguridad", r"\bcctv\b",
         r"\bvideovigilanc"]),

    # ── EDUCACIÓN ──
    ("8510", "Educación preescolar",
        [r"\bguarderi", r"\bescuela\s+infantil", r"\bllar\s+infants?",
         r"\bpreschool", r"\bbressol\b"]),
    ("8520", "Educación primaria",
        [r"\beducacion\s+primari", r"\bcolegio\s+(?!profesional)"]),
    ("8531", "Educación secundaria general",
        [r"\bsecundari", r"\binstituto\b", r"\bbachillerato\b"]),
    ("8532", "Educación secundaria técnica",
        [r"\bformacion\s+profesional", r"\bfp\s+(grado|superior)", r"\bvocational"]),
    ("8541", "Educación postsecundaria",
        [r"\buniversi", r"\bacademia\b", r"\bescuela\s+superior",
         r"\bbusiness\s+school"]),
    ("8551", "Educación deportiva/recreativa",
        [r"\bescuela\s+(deport|natac|baile)", r"\bsport\s+school",
         r"\bdance\s+(school|academy)"]),
    ("8552", "Educación cultural",
        [r"\bescuela\s+(musica|arte|teatr)", r"\bart\s+school",
         r"\bmusic\s+school"]),
    ("8559", "Otros tipos educación",
        [r"\bclases?\s+particulares?", r"\bacademias?\s+(idioma|ingles|aleman)",
         r"\bschool\s+languag", r"\bidiomas?\s+(academ|escuela)",
         r"\bstudent\s+(travel|service|housing)"]),

    # ── SALUD ──
    ("8610", "Hospital",
        [r"\bhospital(?!\s+(de\s+dia|veteri))"]),
    ("8621", "Medicina general",
        [r"\bclinica\s+(general|familiar|medic)",
         r"\bmedicina\s+general", r"\bfamily\s+medicine"]),
    ("8622", "Medicina especializada",
        [r"\bclinica\s+(dental|estetic|cardio|neurol|trauma|fisio)",
         r"\boftalmolog", r"\botorrinol", r"\bginecolog", r"\bcardiolog",
         r"\btraumatolog", r"\bdermatolog"]),
    ("8623", "Dentista",
        [r"\bdent(?:ista|al)\b", r"\bclinica\s+dental", r"\bodontolog"]),
    ("8690", "Otras actividades sanitarias",
        [r"\bfisioterap", r"\bpsicolog\b", r"\bpodol", r"\bnutricion(?:al|ist)\b",
         r"\bquiropract", r"\bosteop"]),

    # ── DEPORTE ──
    ("9311", "Instalaciones deportivas",
        [r"\bgimnasi", r"\bgym\b", r"\bcrossfit", r"\binstalaci\w+\s+deport",
         r"\bpadel\s+club", r"\btennis\s+club", r"\bnatacion\s+club"]),
    ("9312", "Clubes deportivos",
        [r"\bclub\s+(deportivo|esportiu|de\s+f[uú]tbol|de\s+baloncesto)"]),

    # ── CULTURA / OCIO ──
    ("9001", "Artes escénicas",
        [r"\bteatro\b", r"\bdanza\b", r"\bopera\b", r"\bcompañia\s+(teatro|baile)",
         r"\bperforming\s+arts"]),
    ("9003", "Creación artística",
        [r"\bcreaci.n\s+artist", r"\bartistic\s+creation", r"\bestudio\s+(de\s+)?arte"]),
    ("9101", "Bibliotecas y archivos",
        [r"\bbibliotec", r"\barchiv\s+historic", r"\blibrary"]),
    ("9102", "Museos",
        [r"\bmuseo", r"\bmuseum", r"\bgalleries\b", r"\bgaleria\s+(arte|pintura)"]),
    ("9200", "Juegos de azar y apuestas",
        [r"\bcasino", r"\bapuestas?\b", r"\bsalon\s+de\s+juego", r"\bbingo\b",
         r"\bgambling", r"\bbetting"]),

    # ── TRANSPORTE Y LOGÍSTICA ──
    ("4920", "Transporte mercancías ferroviario",
        [r"\bferroviar.*mercanc", r"\brail\s+freight"]),
    ("4931", "Transporte terrestre urbano de pasajeros",
        [r"\btransporte\s+(de\s+)?(pasaj|viajer).*urban", r"\bautobus\s+urban"]),
    ("4932", "Taxi",
        [r"\btaxis?\b", r"\bvtc\b", r"\bchauffeur"]),
    ("4939", "Transporte pasajeros otros",
        [r"\bautocares?\b", r"\bautobus\s+(?!urban)", r"\bcoach\s+(service|company)"]),
    ("5121", "Transporte aéreo pasajeros",
        [r"\baerolinea\b", r"\bairline"]),
    ("5210", "Almacenamiento",
        [r"\balmacenamiento\b", r"\bwarehous", r"\bstorage\s+service"]),
    ("5224", "Manipulación de mercancías",
        [r"\bmanipulaci.n\s+(de\s+)?mercanc", r"\bcargo\s+handl"]),
    ("5320", "Otras actividades postales y correos",
        [r"\bmensajeri", r"\bcourier", r"\bdelivery\s+service",
         r"\bpaquet.*urgent", r"\benvi.s\s+urgent"]),

    # ── PROFESIONALES (más detalle) ──
    ("6810", "Inmobiliaria/Alquiler propio",
        [r"\binmobiliari", r"\breal\s+estate", r"\bimmobiliar",
         r"\bgestion\s+patrimoni", r"\bpatrimoni\s+(empresa|familiar|gestion)"]),
    ("6920", "Asesoría fiscal/contable",
        [r"\basesori.*fiscal", r"\bgestori", r"\basesori.*contab",
         r"\btax\s+adv", r"\baccountancy", r"\bgestoria\b"]),
    ("7311", "Publicidad agencia",
        [r"\bagencia\s+(de\s+)?publicidad", r"\badvertising\s+agency",
         r"\bmarketing\s+(agencia|agency|digital)"]),
    ("7320", "Estudios mercado opinión",
        [r"\bestudios?\s+(de\s+)?mercado", r"\bmarket\s+research"]),
    ("7410", "Diseño especializado",
        [r"\bdiseño\s+(industri|grafic|product|web)", r"\bdesign\s+studio",
         r"\bgrafic\s+design", r"\bgrafico\s+diseño"]),
    ("7420", "Fotografía",
        [r"\bfotograf", r"\bphotograph", r"\bestudi.*fotograf"]),
    ("7500", "Veterinaria",
        [r"\bveterinar", r"\bclinic.*veterin"]),
    ("7990", "Otros servicios reservas/turismo",
        [r"\bagencia\s+(de\s+)?viajes?", r"\btravel\s+agency", r"\btour\s+operator"]),

    # ── ENERGÍA Y AGUA ──
    ("3511", "Producción energía",
        [r"\bproduccion\s+(de\s+)?electricidad", r"\benergia\s+(electric|renovable|solar|eolic)",
         r"\bsolar\s+(energy|fotov|park|installation)", r"\bphotovoltaic",
         r"\beolic", r"\bparque\s+(eolic|solar)", r"\bplanta\s+(fotov|solar|eolic)"]),
    ("3514", "Comercialización electricidad",
        [r"\bcomercializadora\s+(electric|energi)", r"\benergy\s+trading",
         r"\bsuministro\s+electric"]),
    ("3520", "Gas",
        [r"\bgas\s+(natural|comercial|industri|envasado)", r"\bbutano\b", r"\bpropano\b"]),
    ("3600", "Agua tratamiento",
        [r"\btratamiento\s+aguas?", r"\bdepuracion\s+aguas?",
         r"\bdesalinizad", r"\bwater\s+treatment"]),
    ("3811", "Recogida residuos no peligrosos",
        [r"\brecogida\s+(de\s+)?residuos?", r"\bwaste\s+management",
         r"\bgestion\s+residuos?", r"\breciclaj"]),

    # ── PALABRAS GENÉRICAS QUE INDICAN HOGAR/CASA → INMOBILIARIA ──
    ("6810", "Inmobiliaria genérica",
        [r"\bhogar\b(?!\s+(famil|residenc))", r"\bhouse\b", r"\bhome\s+(real|estate|properti)",
         r"\bvivienda\s+(empresa|sociedad)", r"\bvivendes\b"]),

    # ── EMPRESAS MUNICIPALES Y PÚBLICAS ──
    ("8411", "Administración pública general",
        [r"\bempresa\s+municipal", r"\bsociedad\s+municipal",
         r"\bempresa\s+publica", r"\bayuntamiento\s+de"]),
    ("8412", "Administración programas sociales",
        [r"\bvivienda\s+y\s+suelo", r"\bsuelo\s+municipal",
         r"\bvivienda\s+social"]),

    # ── UTE / CONSORCIOS / GRUPOS CONSTRUCTORAS ──
    ("4120", "UTE construcción",
        [r"\bute\b(?!\s+ley)", r"\bunion\s+temporal", r"\bjoint\s+venture\s+(construction|obra)",
         r"\bagroman\b", r"\bobrascon\b", r"\bhuarte\s+lain\b", r"\bferrovial\b",
         r"\bobras?\s+publicas?"]),

    # ── COMERCIO ESPECIALIZADO (más detalle) ──
    ("4711", "Hipermercados",
        [r"\bhiperm", r"\bhypermarket"]),
    ("4719", "Otros comercios no especializados",
        [r"\bgrandes\s+almacenes", r"\bcorte\s+ingles"]),
    ("4721", "Fruta y verdura",
        [r"\bfruteri", r"\bverduras?\s+(tienda|venta)", r"\bfruit\s+shop"]),
    ("4722", "Carnicería",
        [r"\bcarniceri", r"\bbutchery"]),
    ("4723", "Pescadería",
        [r"\bpescaderi", r"\bfishmonger"]),
    ("4724", "Pan/dulces tienda",
        [r"\bpanaderi\s+(tienda|venta)", r"\bbakery\s+shop",
         r"\bpasteleri.*tienda", r"\bcake\s+shop"]),
    ("4725", "Bebidas",
        [r"\bvinotec", r"\bbodeg.*tienda", r"\bwine\s+shop"]),
    ("4729", "Otros alimentos",
        [r"\balimentaci.n\s+(tienda|venta|fresca)", r"\bgrocery"]),
    ("4730", "Combustibles",
        [r"\bgasolinera", r"\bestacion\s+de\s+servicio", r"\bpetrol\s+station",
         r"\bgas\s+station"]),
    ("4741", "Equipos informática tienda",
        [r"\binformatica\s+tienda", r"\bordenador.*tienda", r"\bcomputer\s+shop"]),
    ("4742", "Equipos telecomunicaciones tienda",
        [r"\bmoviles?\s+tienda", r"\btelefoni.*tienda", r"\bphone\s+shop"]),
    ("4743", "Equipos audio/video tienda",
        [r"\baudio.*tienda", r"\belectronica\s+consumo"]),
    ("4751", "Textiles tienda",
        [r"\btextil.*tienda", r"\bfabric\s+shop"]),
    ("4753", "Alfombras y revestimientos",
        [r"\balfombras?\b", r"\bcarpets?\b"]),
    ("4754", "Aparatos uso doméstico",
        [r"\belectrodomestic", r"\baparatos?\s+(uso\s+)?domestic", r"\bhome\s+appliance"]),
    ("4761", "Libros tienda",
        [r"\bliberi", r"\bllibreri", r"\bbook\s+(shop|store)"]),
    ("4762", "Periódicos tienda",
        [r"\bquiosc\b", r"\bestancos?\b"]),
    ("4771", "Ropa tienda",
        [r"\btienda\s+(de\s+)?ropa", r"\bclothing\s+(shop|store)",
         r"\bboutique\b"]),
    ("4772", "Calzado tienda",
        [r"\bzapateri", r"\btienda\s+(de\s+)?zapatos", r"\bshoe\s+shop"]),
    ("4773", "Productos farmacéuticos (farmacia)",
        [r"\bfarmaci\b", r"\bpharmacy"]),
    ("4774", "Productos médicos/ortopédicos",
        [r"\bortopedi.*tienda", r"\borthopaedic\s+shop"]),
    ("4775", "Cosméticos tienda",
        [r"\bperfumeri.*tienda", r"\bdrugstore"]),
    ("4776", "Plantas y mascotas",
        [r"\bjardiner.*tienda", r"\bgarden\s+(center|centre)",
         r"\bmascot.*tienda", r"\bpet\s+shop"]),
    ("4777", "Joyería",
        [r"\bjoyeri", r"\brelojeri", r"\bjewelry"]),
    ("4778", "Otros comercios nuevos",
        [r"\bjugueteri", r"\btoy\s+shop", r"\bart\s+supply",
         r"\bestancos?\b", r"\btabaco\s+venta"]),

    # ── HOSTELERÍA EXTRA (catalán + cocinas) ──
    ("5610", "Restaurante especializado",
        [r"\bsushi\b", r"\bramen\b", r"\bceviche", r"\btapas\s+(restaurant|bar)",
         r"\bpaella\s+(restaurant)", r"\bbrasseri", r"\btaperia",
         r"\bmarisqueri", r"\basador", r"\bgrill\s+restaurant",
         r"\bsteakhouse", r"\bgastroteca"]),
    ("5621", "Catering eventos",
        [r"\beventos?\s+catering", r"\bbanquetes?", r"\bbodas?\s+catering"]),

    # ── CATALANES adicionales ──
    ("2363", "Hormigones (catalán)", [r"\bformigon", r"\bformigons\b"]),
    ("4520", "Taller (catalán)", [r"\btallers?\b"]),
    ("4399", "Reformas (catalán)", [r"\brebaixos?\b", r"\bobres?\s+i\s+reformas?",
                                     r"\bservei\s+integral.*construc"]),
    ("8121", "Limpieza (catalán)", [r"\bnetej", r"\bnete j a"]),
    ("4711", "Supermercado (catalán)", [r"\bsupermercats?\b", r"\bbotigues?\s+alimen"]),
    ("9311", "Gimnasio (catalán)", [r"\bgimnas\b", r"\bcrossfits?\b(?:\s+barcelona|\s+ales)?"]),

    # ── GALLEGOS y EUSKERA ──
    ("4711", "Supermercado (gallego/euskera)",
        [r"\bsupermercat\b", r"\bdendak?\s+aliment"]),
    ("0311", "Pesca (gallego)",
        [r"\bpescador", r"\bbarcos\s+pesc"]),

    # ── PALABRAS QUE FALTABAN (vistas en muestra) ──
    ("5630", "Bares (heladeria/bar)",
        [r"\bhelad.*bar", r"\bcocteler"]),
    ("8553", "Autoescuela",
        [r"\bautoescuela", r"\bdriving\s+school"]),
    ("9602", "Peluquería y estética",
        [r"\bpeluqueri", r"\bestetic.*centro", r"\bbarberi", r"\bbarber\s+shop",
         r"\bhairdress", r"\bsalon\s+belleza", r"\bspa\b"]),
    ("9603", "Pompas fúnebres",
        [r"\bpompas\s+funebres", r"\bfunerari", r"\bfuneral\s+service"]),
    ("9604", "Mantenimiento físico",
        [r"\bspa\s+wellness", r"\bsauna\b", r"\bcentro\s+(?:de\s+)?bienestar",
         r"\bwellness\s+center"]),

    # Catch-all SOLO si no hay nada — devolvemos None (None significa "no inferido")
    # y el caller decide si poner default o dejar vacío.
]


@lru_cache(maxsize=1)
def _catalog_index() -> list[tuple[str, str, set[str]]]:
    """Carga el catálogo CNAE y devuelve (code, description, tokens normalizados)."""
    from app.lib.cnae_catalog import _load_catalog
    out: list[tuple[str, str, set[str]]] = []
    stops = {
        "de", "la", "el", "y", "o", "u", "para", "por", "con", "sin", "en", "los", "las",
        "del", "al", "and", "of", "the", "a", "for", "to",
        "sl", "sa", "sll", "slu", "scp", "ncop", "etc",
    }
    for entry in _load_catalog():
        text = _normalize(entry.description + " " + (entry.keywords or ""))
        tokens = {t for t in re.split(r"[^a-z0-9]+", text) if t and len(t) >= 3 and t not in stops}
        out.append((entry.code, entry.description, tokens))
    return out


def _catalog_fallback(text: str) -> tuple[str, str] | None:
    """Tokeniza `text` y devuelve el CNAE del catálogo con más tokens coincidentes."""
    normalized = _normalize(text)
    stops = {
        "de", "la", "el", "y", "o", "u", "para", "por", "con", "sin", "en", "los", "las",
        "del", "al", "and", "of", "the", "a", "for", "to",
        "sl", "sa", "sll", "slu", "scp", "ncop", "etc",
    }
    input_tokens = {t for t in re.split(r"[^a-z0-9]+", normalized) if t and len(t) >= 3 and t not in stops}
    if not input_tokens:
        return None

    best_score = 0
    best_entry: tuple[str, str] | None = None
    for code, description, cat_tokens in _catalog_index():
        common = input_tokens & cat_tokens
        if not common:
            continue
        score = len(common)
        if score > best_score:
            best_score = score
            best_entry = (code, description)
    return best_entry


@lru_cache(maxsize=1)
def _compiled_rules() -> list[tuple[str, str, list[re.Pattern[str]]]]:
    """Pre-compila todos los regex patterns para acelerar el bulk inference.

    El bulk de 7M empresas se ejecuta ~150 regex × 7M = ~1B regex evals; usar
    re.compile() one-time vs. re.search(str_pattern) cada vez da ~30% speedup.
    """
    compiled = []
    for cnae, label, patterns in _CNAE_RULES:
        compiled.append((cnae, label, [re.compile(p) for p in patterns]))
    return compiled


def infer_cnae(text: str | None, *, allow_catalog_fallback: bool = True) -> tuple[str, str] | None:
    """Devuelve (cnae_code, descripcion) si encuentra un match razonable.

    Si `allow_catalog_fallback=False` (modo bulk), saltamos el catalog tokenizer
    que es lento. El caller puede hacer ese fallback selectivamente.
    """
    if not text or len(text.strip()) < 3:
        return None
    normalized = _normalize(text)

    for cnae, label, patterns in _compiled_rules():
        for pattern in patterns:
            if pattern.search(normalized):
                return (cnae, label)

    if allow_catalog_fallback:
        return _catalog_fallback(text)
    return None


def infer_cnae_or_default(text: str | None, default: tuple[str, str] = ("8299", "Otros servicios de apoyo a empresas")) -> tuple[str, str]:
    """Como infer_cnae pero SIEMPRE devuelve un par (code, label).

    Si no hay match razonable, devuelve el `default` — útil cuando el caller
    necesita pre-rellenar un formulario y no puede dejar campo vacío.
    """
    result = infer_cnae(text)
    if result:
        return result
    return default
