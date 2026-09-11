"""matching.genericos — términos DECLARADOS como no distintivos (v0.22.0).

El IDF aprendido del corpus no distingue un genérico de una marca, y en
nombres de empresa se equivoca de forma sistemática. Medido sobre 105.705
razones sociales de destinatarios de exportación: ``IMPORTADORA`` aparece en
737 nombres y pesa 5,96 de IDF; ``ZELECTA`` aparece en 490 y pesa 6,37. La
marca pesa MENOS que el genérico — porque la frecuencia de una marca crece
con el número de variantes de la MISMA empresa, así que el corpus la castiga
justo por el motivo equivocado.

De ahí que estas listas sean un dato declarado y editable, no una inferencia.
Misma doctrina que ``matching.normalizadores.LOCALES`` y que
``paises.catalogo``.

Tres listas, tres papeles distintos — no las mezcle:

* ``SUFIJOS_INTERNACIONALES`` se BORRAN del nombre (formas legales).
* ``GENERICOS_ESTRUCTURALES`` y ``GENERICOS_SECTOR`` NO se borran: se les baja
  el peso. Borrar "COMERCIALIZADORA" destruye información; ponderarla a cero
  deja la decisión en el token que sí identifica.
* ``GENERICOS_GEOGRAFIA`` pesa poco siempre, pero solo cuenta como "ruido" al
  comparar dos nombres si el llamador lo pide (ver ``GEOGRAFIA_ES_RUIDO`` en
  ``flujo.importadores``): con geografía como ruido, ``ECOLAB`` y ``ECOLAB
  CHILE`` se unen — y también ``BARRY CALLEBAUT USA`` con ``BARRY CALLEBAUT
  CANADA``, que son dos sociedades distintas del mismo grupo.
"""

from __future__ import annotations

__all__ = [
    "GENERICOS_ESTRUCTURALES",
    "GENERICOS_GEOGRAFIA",
    "GENERICOS_SECTOR",
    "SUFIJOS_INTERNACIONALES",
    "genericos",
]

#: Formas legales internacionales que los diccionarios ES/EN de la librería no
#: traen. Se excluyen a propósito las siglas ambiguas de dos letras (IP, AS,
#: AB, PT, LP, OU, AD, DD): borrarlas destruiría tokens legítimos, y si de
#: verdad son genéricas en un corpus el peso IDF ya las neutraliza.
SUFIJOS_INTERNACIONALES: frozenset[str] = frozenset(
    {
        "BV",
        "NV",
        "SARL",
        "AG",
        "KG",
        "OY",
        "APS",
        "LDA",
        "JSC",
        "OOO",
        "ZOO",
        "PTE",
        "PVT",
        "SDN",
        "BHD",
        "DOO",
        "EOOD",
        "OOD",
        "SAC",
        "EIRL",
        "ZRT",
        "KFT",
        "DMCC",
        "FZE",
        "FZCO",
        "WLL",
        "SAE",
        "CA",
        "CV",
        "UAB",
        "TOO",
        "ULC",
        "PJSC",
        "OJSC",
        "CJSC",
        "LLP",
        "NPO",
        "COMPAAIA",
        "COMPAAAA",
    }
)

#: Conectores, giro de negocio y referencias de envío.
GENERICOS_ESTRUCTURALES: frozenset[str] = frozenset(
    {
        # conectores y artículos
        "DE",
        "DEL",
        "LA",
        "EL",
        "LOS",
        "LAS",
        "Y",
        "YO",
        "E",
        "O",
        "AND",
        "THE",
        "OF",
        "FOR",
        "AL",
        "DO",
        "DA",
        "EN",
        "A",
        "POR",
        "CON",
        "DI",
        "DU",
        "VAN",
        "DER",
        "DEN",
        # giro de negocio
        "TRADING",
        "TRADE",
        "TRADERS",
        "INTERNATIONAL",
        "INTERNACIONAL",
        "GROUP",
        "GRUPO",
        "HOLDING",
        "HOLDINGS",
        "IMPORT",
        "IMPORTS",
        "IMPORTACION",
        "IMPORTACIONES",
        "IMPORTADORA",
        "IMPORTADOR",
        "EXPORT",
        "EXPORTS",
        "EXPORTACION",
        "EXPORTACIONES",
        "EXPORTADORA",
        "EXPORTADOR",
        "GLOBAL",
        "SERVICE",
        "SERVICES",
        "SERVICIO",
        "SERVICIOS",
        "DISTRIBUIDORA",
        "DISTRIBUIDOR",
        "DISTRIBUTION",
        "DISTRIBUTORS",
        "DISTRIBUCIONES",
        "INVERSIONES",
        "INVERSION",
        "INVESTMENTS",
        "CORPORACION",
        "CORPORATION",
        "CORPORATE",
        "LOGISTICS",
        "LOGISTICA",
        "LOGISTIC",
        "COMERCIALIZADORA",
        "COMERCIAL",
        "COMMERCIAL",
        "COMERCIO",
        "COMMERCE",
        "INDUSTRIAL",
        "INDUSTRIA",
        "INDUSTRIAS",
        "INDUSTRIES",
        "INDUSTRY",
        "SOLUTIONS",
        "SOLUCIONES",
        "WHOLESALE",
        "WHOLESALER",
        "SUPPLY",
        "SUPPLIES",
        "SUMINISTROS",
        "MARKETING",
        "SYSTEMS",
        "SISTEMAS",
        "ENTERPRISE",
        "ENTERPRISES",
        "PARTNERS",
        "VENTURES",
        "WORLDWIDE",
        "PRODUCTS",
        "PRODUCTOS",
        "PRODUCTION",
        "COMPANY",
        "CIA",
        "CONSULTING",
        "CONSULTORES",
        "CONSULTORIA",
        "ASESORIAS",
        "REPRESENTACIONES",
        "DBA",
        "GENERAL",
        "MANUFACTURAS",
        "MANUFACTURING",
        "CONSTRUCCIONES",
        "PROYECTOS",
        "TRANSPORTES",
        "TRANSPORT",
        "SHIPPING",
        "CARGO",
        "FREIGHT",
        "AGENCIA",
        "AGENCY",
        "NEGOCIOS",
        "BUSINESS",
        "MARKET",
        "MARKETS",
        "STORE",
        "STORES",
        "SHOP",
        "TECH",
        "TECHNOLOGY",
        "TECNOLOGIA",
        # transportistas y referencias de envío: identifican un ENVÍO, no una empresa
        "FEDEX",
        "DHL",
        "UPS",
        "USPS",
        "TNT",
        "EXPRESS",
        "COURIER",
        "COURRIER",
        "AIRLINES",
        "AIRWAYS",
        "AWB",
        "HAWB",
        "GUIA",
        "TRACKING",
        "ORDER",
        "INVOICE",
        "REF",
        "REFERENCIA",
        "CONSIGNEE",
        "NOTIFY",
        "SHIP",
    }
)

#: Términos de SECTOR: se ponderan a la baja, pero NO cuentan como ruido al
#: comparar. Son lo único que separa "GREENWAY FARMS" de "GREENWAY COFFEE".
#: Si su base es de otro sector, reemplace esta lista entera.
GENERICOS_SECTOR: frozenset[str] = frozenset(
    {
        "FLOWERS",
        "FLOWER",
        "FLORAL",
        "FLORES",
        "FLOR",
        "BOUQUET",
        "BOUQUETS",
        "FARMS",
        "FARM",
        "FINCA",
        "COFFEE",
        "CAFE",
        "FRESH",
        "FRUIT",
        "FRUITS",
        "FRUTAS",
        "FOOD",
        "FOODS",
        "ALIMENTOS",
        "ENERGY",
        "PETROLEUM",
        "OIL",
        "TEXTIL",
        "TEXTILE",
        "TEXTILES",
        "CONFECCIONES",
        "PLASTIC",
        "PLASTICS",
        "QUIMICA",
        "QUIMICOS",
        "CHEMICAL",
        "CHEMICALS",
        "PHARMA",
        "FARMACEUTICA",
        "MINERALS",
        "MINERALES",
    }
)

#: Topónimos frecuentes. Ver la nota del encabezado sobre su doble papel.
GENERICOS_GEOGRAFIA: frozenset[str] = frozenset(
    {
        "COLOMBIA",
        "COLOMBIANA",
        "USA",
        "US",
        "AMERICA",
        "AMERICAN",
        "AMERICAS",
        "LATIN",
        "LATINO",
        "PERU",
        "MEXICO",
        "ECUADOR",
        "PANAMA",
        "CHILE",
        "BRASIL",
        "BRAZIL",
        "COSTA",
        "RICA",
        "GUATEMALA",
        "ESPANA",
        "SPAIN",
        "CANADA",
        "EUROPA",
        "EUROPE",
        "ASIA",
        "MIAMI",
        "FLORIDA",
        "NEW",
        "YORK",
        "ANDINA",
        "NACIONAL",
        "NATIONAL",
        "HONDURAS",
        "SALVADOR",
        "NICARAGUA",
        "VENEZUELA",
        "ARGENTINA",
        "URUGUAY",
        "PARAGUAY",
        "BOLIVIA",
        "DOMINICANA",
        "PUERTO",
        "ARUBA",
        "CURACAO",
        "ITALIA",
        "ITALY",
        "FRANCIA",
        "FRANCE",
        "ALEMANIA",
        "GERMANY",
        "HOLLAND",
        "CHINA",
        "JAPAN",
        "JAPON",
        "KOREA",
        "COREA",
        "INDIA",
        "RUSIA",
        "RUSSIA",
        "MONTREAL",
        "TORONTO",
        "BOGOTA",
        "MEDELLIN",
        "CALI",
        "BARRANQUILLA",
        "CARTAGENA",
        "QUITO",
        "GUAYAQUIL",
        "LIMA",
        "SANTIAGO",
        "BUENOS",
        "AIRES",
        "CARACAS",
        "MADRID",
        "BARCELONA",
        "LONDON",
        "PARIS",
        "AMSTERDAM",
        "DUBAI",
        "HOUSTON",
        "CHICAGO",
        "ANGELES",
        "FRANCISCO",
        "ATLANTA",
        "DALLAS",
        "BOSTON",
        "NORTE",
        "SOUTH",
        "NORTH",
        "EAST",
        "WEST",
        "CENTRAL",
        "ORIENTE",
        "OCCIDENTE",
    }
)


def genericos(
    *,
    sector: bool = True,
    geografia: bool = True,
    extra: frozenset[str] | set[str] | None = None,
) -> frozenset[str]:
    """Compone el conjunto de genéricos que se va a usar.

    Args:
        sector: incluir ``GENERICOS_SECTOR``.
        geografia: incluir ``GENERICOS_GEOGRAFIA``.
        extra: términos propios del dominio del llamador.

    Returns:
        El conjunto compuesto, en MAYÚSCULAS.
    """
    salida = set(GENERICOS_ESTRUCTURALES)
    if sector:
        salida |= GENERICOS_SECTOR
    if geografia:
        salida |= GENERICOS_GEOGRAFIA
    if extra:
        salida |= {str(t).strip().upper() for t in extra}
    return frozenset(salida)
