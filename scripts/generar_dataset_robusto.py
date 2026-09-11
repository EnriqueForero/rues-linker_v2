"""generar_dataset_robusto.py — Dataset sintético para pruebas robustas.

Construye un ground truth sintético determinista (`seed=42`) que cubre
**patrones reales** del registro mercantil colombiano y **casos frontera**
que estresan modos de falla específicos del scorer.

A diferencia de los oráculos ya existentes (que son sets pequeños de casos
hand-picked), este dataset es de tamaño operativo (~1000 registros, ~150
grupos) y refleja la distribución estadística realista de:

    - Tamaño de grupo (1-25 registros por entidad real)
    - Tipo de variación (typo / sufijo / sigla / sucursal / fusión)
    - Calidad del NIT (con DV / sin DV / con guion / con puntos)
    - Encoding (ASCII / UTF-8 con Ñ y tildes / mojibake controlado)
    - Patrones negativos críticos (token compartido pero entidad distinta)

PATRONES INCLUIDOS

1.  **Variaciones tipográficas**: typos QWERTY adyacentes, doble espacio,
    capitalización inconsistente, caracteres invisibles (zero-width).
2.  **Variaciones societarias**: SAS / SA / LTDA / EU / ZF / EN LIQUIDACION
    / EN REORGANIZACION.
3.  **Sucursales y plantas**: NIT base igual + 'SUCURSAL X', 'PLANTA Y',
    'OFICINA PRINCIPAL'. El golden marca todas como **mismo grupo**
    (deduplicación por entidad jurídica).
4.  **Sigla vs nombre completo**: 'EY' vs 'ERNST & YOUNG'; 'CCB' vs
    'CAMARA DE COMERCIO DE BOGOTA'. Mismo NIT, nombre totalmente disímil.
5.  **NITs con formato variado**: '900123456', '900.123.456', '900123456-7',
    '900-123456-7', '  900123456 '. Mismo grupo si normalizan al mismo NIT.
6.  **DV calculado vs declarado**: registros con DV explícito vs sin DV.
    El sistema debe unificar.
7.  **Empresas con nombres genéricos**: 'INVERSIONES SAS' — fácil de
    confundir si solo se mira el nombre.

CASOS FRONTERA NEGATIVOS (deben quedar en GRUPOS DISTINTOS)

A.  **Token compartido único**: 'BOLIVAR SEGUROS' vs 'BANCO BOLIVAR' —
    el LSH puede agruparlos por compartir 'BOLIVAR'.
B.  **NIT vecino + nombre disímil**: el clásico falso amigo de la
    deduplicación por NIT base.
C.  **Phonetic key colisión**: 'SOLER' vs 'SALER' tienen claves
    fonéticas parecidas pero son entidades distintas.
D.  **Colisión NIT_OK falsa**: dos empresas con NIT base distinto cuyo
    DV calculado coincide accidentalmente con un DV declarado.
E.  **Sufijo confundible**: 'CONSTRUCCIONES BOLIVAR SA' vs
    'CONSTRUCCIONES BOLIVAR LTDA' con NITs distintos (son grupos
    económicos diferentes, NO una misma empresa que cambió de tipo
    societario).

Uso:
    python scripts/generar_dataset_robusto.py
    python scripts/generar_dataset_robusto.py --salida custom/path.csv --n-grupos 200

Author: Claude (auditor)  Date: 2026-05-22  Version: 2.9.0
"""

from __future__ import annotations

import argparse
import random
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# Semilla maestra. Cualquier persona que corra el script con el mismo
# seed obtiene el mismo CSV bit-a-bit.
SEED = 42

# ─────────────────────────────────────────────────────────────────────
# Datos base: 80 empresas-semilla reales del tejido empresarial colombiano
# (combinación pública + sintética). Cada una genera 1-25 variantes.
# ─────────────────────────────────────────────────────────────────────
EMPRESAS_BASE: list[tuple[str, str, str | None]] = [
    # (nit_base_9_dig, nombre_canonico, sigla_opcional)
    # IMPORTANTE: cada NIT base DEBE ser único. Si dos entradas comparten
    # NIT base, sus variantes terminan en el mismo NIT_OK y los grupos se
    # fusionan, contaminando el ground truth. Validado por build_dataset.
    ("890900608", "BAVARIA", None),
    ("890903407", "GRUPO NUTRESA", "NUTRESA"),
    ("890903938", "AVIANCA", None),
    ("860005289", "ECOPETROL", None),
    ("890903408", "COMPAÑIA NACIONAL DE CHOCOLATES", "CHOCOLATES"),
    ("860007391", "FABRICA DE LICORES DE ANTIOQUIA", "FLA"),
    ("860002503", "BANCO DE BOGOTA", None),
    ("860003020", "BANCOLOMBIA", None),
    ("860007660", "GRUPO ARGOS", "ARGOS"),
    ("860020309", "CEMENTOS ARGOS", None),
    ("860007335", "GRUPO SURA", "SURA"),
    ("860040600", "ALMACENES EXITO", "EXITO"),
    ("860043186", "POSTOBON", None),
    ("860046645", "SEGUROS BOLIVAR", None),
    ("860500862", "FALABELLA DE COLOMBIA", None),
    ("860500863", "FALABELLA RETAIL", None),
    ("800152613", "CORPORACION FINANCIERA COLOMBIANA", "CORFICOLOMBIANA"),
    ("800183968", "EMPRESAS PUBLICAS DE MEDELLIN", "EPM"),
    ("899999063", "EMPRESA DE ACUEDUCTO Y ALCANTARILLADO DE BOGOTA", "EAAB"),
    ("899999115", "CODENSA", None),
    ("830053812", "TIGO UNE", None),
    ("830122398", "CLARO COLOMBIA", "CLARO"),
    ("800153993", "MOVISTAR COLOMBIA", "MOVISTAR"),
    ("860066942", "ASEGURADORA SOLIDARIA DE COLOMBIA", "SOLIDARIA"),
    ("860013571", "MAPFRE SEGUROS GENERALES DE COLOMBIA", "MAPFRE"),
    ("860031094", "COLPATRIA", None),
    ("860007738", "BANCO POPULAR", None),
    ("860034313", "BANCO DAVIVIENDA", "DAVIVIENDA"),
    ("860051705", "BANCO BBVA COLOMBIA", "BBVA"),
    ("860006797", "BANCO AV VILLAS", "AV VILLAS"),
    ("860007661", "INVERSIONES ARGOS", None),
    ("830094926", "CONSTRUCTORA BOLIVAR", None),
    ("860013838", "CONSTRUCTORA CONCONCRETO", "CONCONCRETO"),
    ("860013570", "ALPINA PRODUCTOS ALIMENTICIOS", "ALPINA"),
    ("860007662", "GRUPO COLPATRIA", None),
    ("800024725", "LADRILLERA SANTAFE", "SANTAFE"),
    ("860009800", "CAFAM CAJA DE COMPENSACION FAMILIAR", "CAFAM"),
    ("800027059", "COLSUBSIDIO", None),
    ("860035992", "COMPENSAR", None),
    ("899999230", "CAMARA DE COMERCIO DE BOGOTA", "CCB"),
    ("899999007", "FONDO NACIONAL DEL AHORRO", "FNA"),
    ("899999034", "SUPERINTENDENCIA DE INDUSTRIA Y COMERCIO", "SIC"),
    ("830067639", "ERNST AND YOUNG COLOMBIA", "EY"),
    ("860005290", "DELOITTE TOUCHE TOHMATSU", "DELOITTE"),
    ("860015985", "KPMG LIMITADA", "KPMG"),
    ("860020382", "PRICEWATERHOUSECOOPERS", "PWC"),
    ("890301884", "INDUSTRIAS HACEB", "HACEB"),
    ("890900841", "TERMOTASAJERO", None),
    ("890904713", "CARVAJAL EDUCACION", "CARVAJAL"),
    ("890904714", "CARVAJAL TECNOLOGIA Y SERVICIOS", None),
    ("890904715", "CARVAJAL EMPAQUES", None),
    ("890900148", "AKZONOBEL PINTUCO", "PINTUCO"),
    ("860506590", "COLGATE PALMOLIVE COMPAÑIA", "COLGATE"),
    ("860527167", "UNILEVER ANDINA COLOMBIA", "UNILEVER"),
    ("860506745", "PROCTER AND GAMBLE COLOMBIA", "P AND G"),
    ("860006266", "CASA EDITORIAL EL TIEMPO", "EL TIEMPO"),
    ("890900842", "CARACOL TELEVISION", "CARACOL"),
    ("890900843", "RCN TELEVISION", "RCN"),
    ("860006267", "EL COLOMBIANO", None),
    ("830012345", "GOOGLE COLOMBIA", "GOOGLE"),
    ("830012346", "AMAZON WEB SERVICES COLOMBIA", "AWS"),
    ("830012347", "MICROSOFT COLOMBIA", "MICROSOFT"),
    ("830012348", "ORACLE COLOMBIA", "ORACLE"),
    ("830012349", "SAP COLOMBIA", "SAP"),
    ("800194976", "INTERBOLSA COMISIONISTA DE BOLSA", "INTERBOLSA"),
    ("800194977", "INTERBOLSA SOCIEDAD ADMINISTRADORA DE INVERSION", None),
    ("860009608", "RENAULT SOFASA", "SOFASA"),
    ("860054956", "CHEVROLET GENERAL MOTORS COLMOTORES", "CHEVROLET"),
    ("860005114", "ICOLLANTAS", None),
    ("860005115", "ICOPAL DE COLOMBIA", "ICOPAL"),
    ("800006988", "EMPAQUES INDUSTRIALES COLOMBIANOS", "EICO"),
    ("800006989", "PAPELES DEL CAUCA", None),
    ("860003433", "AEROLINEAS DE COLOMBIA SAM", "SAM"),
    ("830029904", "VIVA AIR COLOMBIA", "VIVA"),
    ("900009998", "RAPPI", None),
    ("900009999", "PLATZI", None),
    ("900033307", "MERQUEO", None),
    ("900133308", "FRUBANA", None),
    ("900233307", "HABI", None),
    ("860001234", "CARULLA VIVERO", "CARULLA"),
    ("860001235", "OLIMPICA SUPERMERCADOS", "OLIMPICA"),
    ("860001236", "JUMBO COLOMBIA", "JUMBO"),
]


# ─────────────────────────────────────────────────────────────────────
# Generadores de variación
# ─────────────────────────────────────────────────────────────────────

SUFIJOS = [
    "S A S",
    "SAS",
    "S.A.S.",
    "S A",
    "SA",
    "S.A.",
    "LTDA",
    "LIMITADA",
    "S EN C",
    "E U",
    "EU",
    "ESAL",
    "SCA",
    "CIA",
    "Y CIA",
]
EXTRAS_DESCRIPTIVAS = [
    "EN LIQUIDACION",
    "EN REORGANIZACION",
    "EN INSOLVENCIA",
    "ZONA FRANCA",
    "ZF",
    "SUCURSAL COLOMBIA",
    "GRUPO EMPRESARIAL",
    "HOLDING",
    "COMPAÑIA",
    "EMPRESA",
]
SUCURSALES = [
    "SUCURSAL PRINCIPAL",
    "SUCURSAL BOGOTA",
    "SUCURSAL MEDELLIN",
    "SUCURSAL CALI",
    "SUCURSAL BARRANQUILLA",
    "PLANTA NORTE",
    "PLANTA SUR",
    "OFICINA CENTRAL",
    "AGENCIA EJE CAFETERO",
]

# Mapa QWERTY-ES de teclas adyacentes (para typos realistas)
QWERTY_ES_VECINOS = {
    "A": "SQZW",
    "B": "VGN",
    "C": "XVD",
    "D": "SFCE",
    "E": "WRDS",
    "F": "DGRC",
    "G": "FHTV",
    "H": "GJYB",
    "I": "UOKJ",
    "J": "HKNI",
    "K": "JLMU",
    "L": "KÑO",
    "M": "NJ",
    "N": "BMHJ",
    "O": "IPLK",
    "P": "OÑL",
    "Q": "WA",
    "R": "ETDF",
    "S": "AWDX",
    "T": "RYFG",
    "U": "YIJH",
    "V": "CBFG",
    "W": "QESA",
    "X": "ZCSD",
    "Y": "TUH",
    "Z": "AXS",
    "Ñ": "LP",
}


def _calcular_dv(nit_base: str) -> str:
    """Calcula DV del NIT colombiano (DIAN). nit_base = 9 dígitos."""
    pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    s = sum(int(d) * pesos[len(nit_base) - 1 - i] for i, d in enumerate(nit_base))
    r = s % 11
    return str(11 - r) if r > 1 else str(r)


def _typo_qwerty(s: str, rng: random.Random) -> str:
    """Cambia una letra por una tecla adyacente del teclado QWERTY-ES."""
    if not s:
        return s
    chars = list(s)
    posiciones = [i for i, c in enumerate(chars) if c.upper() in QWERTY_ES_VECINOS]
    if not posiciones:
        return s
    p = rng.choice(posiciones)
    original = chars[p].upper()
    sustituto = rng.choice(QWERTY_ES_VECINOS[original])
    chars[p] = sustituto if chars[p].isupper() else sustituto.lower()
    return "".join(chars)


def _swap_adyacente(s: str, rng: random.Random) -> str:
    """Intercambia dos letras adyacentes."""
    if len(s) < 3:
        return s
    i = rng.randint(0, len(s) - 2)
    return s[:i] + s[i + 1] + s[i] + s[i + 2 :]


def _omitir_letra(s: str, rng: random.Random) -> str:
    if len(s) < 3:
        return s
    i = rng.randint(1, len(s) - 2)
    return s[:i] + s[i + 1 :]


def _doble_letra(s: str, rng: random.Random) -> str:
    if len(s) < 2:
        return s
    i = rng.randint(0, len(s) - 1)
    return s[:i] + s[i] + s[i:]


def _quitar_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _doble_espacio(s: str, rng: random.Random) -> str:
    """Inserta un espacio extra entre dos palabras al azar."""
    partes = s.split(" ")
    if len(partes) < 2:
        return s
    i = rng.randint(0, len(partes) - 2)
    partes[i] = partes[i] + " "
    return " ".join(partes)


def _zero_width_space(s: str, rng: random.Random) -> str:
    """Inserta U+200B (zero-width space) — caso patológico real."""
    if len(s) < 3:
        return s
    i = rng.randint(1, len(s) - 1)
    return s[:i] + "\u200b" + s[i:]


def _formato_nit(nit: str, dv: str, modo: str) -> str:
    """Devuelve el NIT en distintos formatos válidos en el RUES."""
    if modo == "9dig":
        return nit
    if modo == "10dig":
        return nit + dv
    if modo == "guion":
        return f"{nit}-{dv}"
    if modo == "puntos":
        return f"{nit[:3]}.{nit[3:6]}.{nit[6:]}-{dv}"
    if modo == "espacios":
        return f"  {nit} "
    if modo == "guion_largo":
        return f"{nit}-{dv}-1"
    return nit


def _aplicar_typos(nombre: str, n_typos: int, rng: random.Random) -> str:
    """Aplica una cadena de typos realistas al nombre."""
    operaciones = [_typo_qwerty, _swap_adyacente, _omitir_letra, _doble_letra]
    out = nombre
    for _ in range(n_typos):
        op = rng.choice(operaciones)
        out = op(out, rng)
    return out


# ─────────────────────────────────────────────────────────────────────
# Generadores de variantes por empresa
# ─────────────────────────────────────────────────────────────────────


def generar_variantes_de_empresa(
    nit_base: str,
    nombre: str,
    sigla: str | None,
    n_var: int,
    rng: random.Random,
) -> list[tuple[str, str]]:
    """Genera n_var variantes (NIT, RAZON_SOCIAL) que pertenecen a la
    MISMA entidad jurídica."""
    dv = _calcular_dv(nit_base)
    variantes: list[tuple[str, str]] = []

    # Canónica: nombre exacto con DV
    variantes.append((nit_base + dv, nombre))

    if n_var <= 1:
        return variantes

    # Repertorio de transformaciones posibles
    transformaciones: list[str] = []
    transformaciones += ["mayusculas", "minusculas", "title"]
    transformaciones += ["agregar_sufijo"] * 3
    transformaciones += ["agregar_extra"] * 2
    transformaciones += ["nit_9dig", "nit_guion", "nit_puntos", "nit_espacios"]
    transformaciones += ["typo_1", "typo_2", "typo_3"]
    transformaciones += ["doble_espacio", "sin_tildes", "zero_width"]
    transformaciones += ["swap_orden"]
    if sigla:
        transformaciones += ["solo_sigla", "sigla_y_resto"] * 2
    transformaciones += ["sucursal"] * 2
    transformaciones += ["mojibake"]

    elegidas = rng.sample(transformaciones, min(n_var - 1, len(transformaciones)))

    for t in elegidas:
        if t == "mayusculas":
            variantes.append((nit_base + dv, nombre.upper()))
        elif t == "minusculas":
            variantes.append((nit_base + dv, nombre.lower()))
        elif t == "title":
            variantes.append((nit_base + dv, nombre.title()))
        elif t == "agregar_sufijo":
            suf = rng.choice(SUFIJOS)
            variantes.append((nit_base + dv, f"{nombre} {suf}"))
        elif t == "agregar_extra":
            ex = rng.choice(EXTRAS_DESCRIPTIVAS)
            variantes.append((nit_base + dv, f"{nombre} {ex}"))
        elif t == "nit_9dig":
            variantes.append((_formato_nit(nit_base, dv, "9dig"), nombre))
        elif t == "nit_guion":
            variantes.append((_formato_nit(nit_base, dv, "guion"), nombre))
        elif t == "nit_puntos":
            variantes.append((_formato_nit(nit_base, dv, "puntos"), nombre))
        elif t == "nit_espacios":
            variantes.append((_formato_nit(nit_base, dv, "espacios"), nombre))
        elif t == "typo_1":
            variantes.append((nit_base + dv, _aplicar_typos(nombre, 1, rng)))
        elif t == "typo_2":
            variantes.append((nit_base + dv, _aplicar_typos(nombre, 2, rng)))
        elif t == "typo_3":
            # Typo agresivo SIN sufijo, para asegurar que el filtro de
            # min_name_similarity lo rechace y solo el NIT lo salve.
            variantes.append((nit_base + dv, _aplicar_typos(nombre, 3, rng)))
        elif t == "doble_espacio":
            variantes.append((nit_base + dv, _doble_espacio(nombre, rng)))
        elif t == "sin_tildes":
            variantes.append((nit_base + dv, _quitar_tildes(nombre)))
        elif t == "zero_width":
            variantes.append((nit_base + dv, _zero_width_space(nombre, rng)))
        elif t == "swap_orden":
            palabras = nombre.split()
            if len(palabras) >= 3:
                rng.shuffle(palabras)
                variantes.append((nit_base + dv, " ".join(palabras)))
            else:
                variantes.append((nit_base + dv, nombre))
        elif t == "solo_sigla":
            assert sigla
            variantes.append((nit_base + dv, sigla))
        elif t == "sigla_y_resto":
            assert sigla
            suf = rng.choice(SUFIJOS)
            variantes.append((nit_base + dv, f"{sigla} {suf}"))
        elif t == "sucursal":
            suc = rng.choice(SUCURSALES)
            variantes.append((nit_base + dv, f"{nombre} {suc}"))
        elif t == "mojibake":
            # Aproxima encoding corrupto: 'Ñ' → 'N~', 'a' con tilde se vuelve mojibake
            corrupto = (
                nombre.replace("Ñ", "Nâ€")
                .replace("Á", "AÌ")
                .replace("É", "EÌ")
                .replace("Í", "IÌ")
                .replace("Ó", "OÌ")
                .replace("Ú", "UÌ")
            )
            variantes.append((nit_base + dv, corrupto))
    return variantes


# ─────────────────────────────────────────────────────────────────────
# Casos frontera explícitos (NEGATIVOS — deben quedar en grupos DISTINTOS)
# ─────────────────────────────────────────────────────────────────────


def generar_casos_frontera_negativos(
    rng: random.Random,
    base_group_id: int,
) -> list[dict]:
    """Casos diseñados para confundir al sistema. Cada par/triplete
    debe quedar en grupos DISTINTOS — son falsos amigos.

    IMPORTANTE: los NITs base de esta sección NO deben aparecer en
    EMPRESAS_BASE. Se usa el rango 855XXXXXX reservado.
    """
    casos: list[dict] = []
    gid = base_group_id

    # A. Token único compartido — confunde LSH y bloqueo por nombre.
    #    Tres entidades reales distintas con el token "BOLIVAR".
    for nit_base, nombre in [
        ("855000001", "SEGUROS GENERALES BOLIVAR DE COLOMBIA"),
        ("855000011", "CONSTRUCTORA BOLIVAR INVERSIONES"),
        ("855000021", "BANCO BOLIVAR FONDOS"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "A_token_compartido",
            }
        )
        gid += 1

    # B. NIT vecino (Lev dist=1) + nombre disímil — el bloqueo NIT base
    #    los une como candidatos, el scorer debe rechazarlos.
    for nit_base, nombre in [
        ("855001111", "INVERSIONES EL PORVENIR ANDINO"),
        ("855001112", "TRANSPORTES LA SABANA DEL SUR"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "B_nit_vecino",
            }
        )
        gid += 1

    # C. Phonetic key colisión — suenan parecido, NIT base distinto,
    #    resto del nombre disímil.
    for nit_base, nombre in [
        ("855002221", "SOLER CAPITAL INVERSIONES"),
        ("855003331", "SALER CONSULTORES JURIDICOS"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "C_phonetic_colision",
            }
        )
        gid += 1

    # D. Sufijo confundible — NITs distintos, nombre comparte casi todo
    #    excepto el sufijo societario.
    for nit_base, nombre in [
        ("855004441", "CONSTRUCCIONES MENDEZ SA"),
        ("855004451", "CONSTRUCCIONES MENDEZ LTDA"),
        ("855004461", "CONSTRUCCIONES MENDEZ SAS"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "D_sufijo_confundible",
            }
        )
        gid += 1

    # E. NITs cortos (entidades públicas o cooperativas).
    for nit_base, nombre in [
        ("855000010", "FONDO ESPECIAL UNO COOPERATIVO"),
        ("855000020", "FONDO ESPECIAL DOS COOPERATIVO"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "E_nit_corto",
            }
        )
        gid += 1

    # F. NIT vacío en ambos lados — no debe agruparse por nombre solo.
    for nombre in ["EMPRESA SIN REGISTRAR UNO LIMITADA", "EMPRESA SIN REGISTRAR DOS LIMITADA"]:
        casos.append(
            {"NIT": "", "RAZON_SOCIAL": nombre, "ID_GROUP": gid, "CASO_FRONTERA": "F_nit_vacio"}
        )
        gid += 1

    # G. Nombres genéricos cortos — fácil falso positivo si solo se mira
    #    el nombre.
    for nit_base in ["855005551", "855005561", "855005571"]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": "INVERSIONES SAS",
                "ID_GROUP": gid,
                "CASO_FRONTERA": "G_nombre_generico",
            }
        )
        gid += 1

    # H. Misma denominación, distinto país/filial.
    for nit_base, nombre in [
        ("855006661", "TOTAL ENERGIES COLOMBIA SUCURSAL"),
        ("855006671", "TOTAL ENERGIES ECUADOR FILIAL COLOMBIA"),
    ]:
        dv = _calcular_dv(nit_base)
        casos.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": nombre,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "H_filial_pais",
            }
        )
        gid += 1

    return casos


# ─────────────────────────────────────────────────────────────────────
# Casos frontera POSITIVOS (deben quedar en MISMO grupo aunque sea difícil)
# ─────────────────────────────────────────────────────────────────────


def generar_casos_frontera_positivos(
    rng: random.Random,
    base_group_id: int,
) -> list[dict]:
    """Casos diseñados como difíciles pero positivos. Cada lista
    consecutiva pertenece al mismo grupo (debe unificarse).

    IMPORTANTE: los NITs base de esta sección NO deben aparecer en
    EMPRESAS_BASE para evitar fusión accidental de grupos. Se usa el
    rango 850XXXXXX reservado para casos frontera.
    """
    casos: list[dict] = []
    gid = base_group_id

    # P1. Sigla vs nombre completo (NIT idéntico, nombre disímil).
    nit_base = "850000067"
    dv = _calcular_dv(nit_base)
    nit = nit_base + dv
    for nm in [
        "MONROE AND PARTNERS LLP",
        "MONROE",
        "MONROE PARTNERS",
        "MP ASESORES LIMITADA",
        "M AND P CONSULTORES",
        "MONROE LATAM",
    ]:
        casos.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": nm,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "P1_sigla_vs_completo",
            }
        )
    gid += 1

    # P2. Mismo NIT, denominación histórica que cambió tras fusión.
    nit_base = "850000148"
    dv = _calcular_dv(nit_base)
    nit = nit_base + dv
    for nm in [
        "SIGMA PINTURAS",
        "COMPAÑIA NACIONAL DE PIGMENTOS",
        "SIGMAKALON COLOMBIA",
        "SIGMA",
        "GRUPO SIGMA PIGMENTOS",
        "CNP PINTURAS",
    ]:
        casos.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": nm,
                "ID_GROUP": gid,
                "CASO_FRONTERA": "P2_historico_fusion",
            }
        )
    gid += 1

    # P3. DV calculado vs declarado — sin DV vs con DV vs con guion.
    # El sistema debe unirlos pese a que el AdvancedNitProcessor podría
    # calcular un DV distinto al declarado.
    nit_base = "850000345"
    dv = _calcular_dv(nit_base)
    casos.append(
        {
            "NIT": nit_base,
            "RAZON_SOCIAL": "INVERSIONES TRIDENTE",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P3_dv_calc_vs_decl",
        }
    )
    casos.append(
        {
            "NIT": nit_base + dv,
            "RAZON_SOCIAL": "INVERSIONES TRIDENTE SAS",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P3_dv_calc_vs_decl",
        }
    )
    casos.append(
        {
            "NIT": f"{nit_base}-{dv}",
            "RAZON_SOCIAL": "Inversiones Tridente S.A.S.",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P3_dv_calc_vs_decl",
        }
    )
    gid += 1

    # P4. Token único disímil — solo el NIT puede salvar el match.
    nit_base = "850000266"
    dv = _calcular_dv(nit_base)
    nit = nit_base + dv
    for nm in [
        "EDITORIAL PERIODISTAS ASOCIADOS DE COLOMBIA",
        "EL DIARIO DEL CARIBE",
        "EDIPACOL",
        "DIARIOS DEL CARIBE LIMITADA",
    ]:
        casos.append(
            {"NIT": nit, "RAZON_SOCIAL": nm, "ID_GROUP": gid, "CASO_FRONTERA": "P4_token_disimil"}
        )
    gid += 1

    # P5. Variantes con zero-width y caracteres invisibles.
    nit_base = "850000998"
    dv = _calcular_dv(nit_base)
    nit = nit_base + dv
    casos.append(
        {
            "NIT": nit,
            "RAZON_SOCIAL": "DELIVERY EXPRESS",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P5_invisibles",
        }
    )
    casos.append(
        {
            "NIT": nit,
            "RAZON_SOCIAL": "DELI\u200bVERY EXPRESS SAS",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P5_invisibles",
        }
    )
    casos.append(
        {
            "NIT": nit,
            "RAZON_SOCIAL": "DELIVERY\u00a0EXPRESS COLOMBIA",
            "ID_GROUP": gid,
            "CASO_FRONTERA": "P5_invisibles",
        }
    )
    gid += 1

    return casos


# ─────────────────────────────────────────────────────────────────────
# Composición final del dataset
# ─────────────────────────────────────────────────────────────────────


def _normalizar_nit_para_validacion(nit: str) -> str:
    """Normaliza el NIT a 9 dígitos puros para validación.

    Quita guiones, puntos, espacios, y trunca al inicio (los primeros 9
    dígitos son el NIT base; el resto es DV o variantes adicionales).
    Esta normalización es SOLO para la validación interna del ground
    truth — el sistema real bajo prueba usa su propio normalizador.
    """
    if not nit:
        return ""
    solo_dig = "".join(c for c in nit if c.isdigit())
    return solo_dig[:9]  # NIT base estándar colombiano


def _validar_integridad(df: pd.DataFrame) -> None:
    """Valida la integridad lógica del ground truth.

    Reglas que deben cumplirse SIEMPRE:
        1. Cada NIT (normalizado a 9 dígitos base) cuando no vacío debe
           pertenecer a UN solo grupo. Si un mismo NIT base aparece en
           dos ID_GROUP distintos, el ground truth se contradice.
        2. Los singletons (CASO_FRONTERA='S_singleton') tienen tamaño 1.
        3. Cada par/triplete de casos frontera negativos tiene ID_GROUP
           distinto entre sus miembros (es su definición misma).
        4. Cada serie de casos frontera positivos comparte ID_GROUP.
    """
    errors: list[str] = []

    # Regla 1: NIT normalizado → un único grupo.
    df_chk = df.copy()
    df_chk["NIT_NORM"] = df_chk["NIT"].apply(_normalizar_nit_para_validacion)
    nit_no_vacio = df_chk[df_chk["NIT_NORM"] != ""]
    nit_groups = nit_no_vacio.groupby("NIT_NORM")["ID_GROUP"].nunique()
    nits_multi = nit_groups[nit_groups > 1]
    if not nits_multi.empty:
        for nit in nits_multi.index[:5]:
            sub = df_chk[df_chk["NIT_NORM"] == nit][
                ["NIT", "RAZON_SOCIAL", "ID_GROUP", "CASO_FRONTERA"]
            ]
            errors.append(
                f"NIT base {nit} aparece en {nits_multi[nit]} grupos:\n" + sub.to_string()
            )

    # Regla 2: singletons.
    singletons = df[df["CASO_FRONTERA"] == "S_singleton"]
    if not singletons.empty:
        sizes = singletons.groupby("ID_GROUP").size()
        bad = sizes[sizes != 1]
        if not bad.empty:
            errors.append(f"Singletons con tamaño != 1: {bad.to_dict()}")

    # Regla 3: casos frontera negativos NO comparten grupo.
    for tag in [
        "A_token_compartido",
        "B_nit_vecino",
        "C_phonetic_colision",
        "D_sufijo_confundible",
        "E_nit_corto",
        "F_nit_vacio",
        "G_nombre_generico",
        "H_filial_pais",
    ]:
        sub = df[df["CASO_FRONTERA"] == tag]
        if sub.empty:
            continue
        n_records = len(sub)
        n_groups = sub["ID_GROUP"].nunique()
        if n_records != n_groups:
            errors.append(
                f"Caso frontera negativo '{tag}': {n_records} registros pero "
                f"solo {n_groups} grupos (deberían ser todos distintos)."
            )

    # Regla 4: casos frontera positivos comparten grupo (1 grupo por bloque
    # con mismo tag — solo verifica que cada NIT del bloque tenga 1 grupo).
    for tag in [
        "P1_sigla_vs_completo",
        "P2_historico_fusion",
        "P3_dv_calc_vs_decl",
        "P4_token_disimil",
        "P5_invisibles",
    ]:
        sub = df[df["CASO_FRONTERA"] == tag]
        if sub.empty:
            continue
        n_groups = sub["ID_GROUP"].nunique()
        if n_groups != 1:
            errors.append(
                f"Caso frontera positivo '{tag}': debería ser 1 grupo, pero hay {n_groups}."
            )

    if errors:
        raise ValueError("VALIDACIÓN DEL GROUND TRUTH FALLÓ:\n\n" + "\n\n".join(errors))


def construir_dataset(n_grupos_extra: int = 0, semilla: int = SEED) -> pd.DataFrame:
    """Construye el DataFrame completo."""
    rng = random.Random(semilla)
    filas: list[dict] = []

    # 1. Empresas base (variantes 'orgánicas')
    for gid, (nit_base, nombre, sigla) in enumerate(EMPRESAS_BASE, start=1):
        # Tamaño de grupo: distribución sesgada a grupos pequeños (realista)
        # 70 % grupos de 3-8 registros, 25 % de 9-15, 5 % de 16-25.
        roll = rng.random()
        if roll < 0.70:
            n_var = rng.randint(3, 8)
        elif roll < 0.95:
            n_var = rng.randint(9, 15)
        else:
            n_var = rng.randint(16, 25)
        variantes = generar_variantes_de_empresa(nit_base, nombre, sigla, n_var, rng)
        for nit, razon in variantes:
            filas.append(
                {
                    "NIT": nit,
                    "RAZON_SOCIAL": razon,
                    "ID_GROUP": gid,
                    "CASO_FRONTERA": "",
                }
            )

    # 2. Casos frontera negativos
    next_gid = max(f["ID_GROUP"] for f in filas) + 1
    casos_neg = generar_casos_frontera_negativos(rng, next_gid)
    filas.extend(casos_neg)

    # 3. Casos frontera positivos
    next_gid = max(f["ID_GROUP"] for f in filas) + 1
    casos_pos = generar_casos_frontera_positivos(rng, next_gid)
    filas.extend(casos_pos)

    # 4. Grupos singleton (empresas únicas, sin variantes). Importante
    #    para verificar que el sistema NO fuerce agrupación falsa.
    #    Rango 856XXXXXX reservado para singletons.
    next_gid = max(f["ID_GROUP"] for f in filas) + 1
    for k in range(20):
        nit_base = f"856{k * 137 + 100:06d}"
        dv = _calcular_dv(nit_base)
        filas.append(
            {
                "NIT": nit_base + dv,
                "RAZON_SOCIAL": f"EMPRESA UNICA NUMERO {k + 1:03d} SAS",
                "ID_GROUP": next_gid + k,
                "CASO_FRONTERA": "S_singleton",
            }
        )

    # 5. Grupos extra solicitados (escalado).
    #    Rango 857XXXXXX reservado para grupos escalados.
    if n_grupos_extra > 0:
        next_gid = max(f["ID_GROUP"] for f in filas) + 1
        for k in range(n_grupos_extra):
            nit_base = f"857{k * 47 + 1000:06d}"
            dv = _calcular_dv(nit_base)
            nombre = f"EMPRESA SINTETICA {k + 1:04d}"
            sigla = f"ES{k + 1:04d}" if k % 7 == 0 else None
            n_var = rng.randint(3, 10)
            variantes = generar_variantes_de_empresa(nit_base, nombre, sigla, n_var, rng)
            for nit, razon in variantes:
                filas.append(
                    {
                        "NIT": nit,
                        "RAZON_SOCIAL": razon,
                        "ID_GROUP": next_gid + k,
                        "CASO_FRONTERA": "X_escalado",
                    }
                )

    df = pd.DataFrame(filas)
    # Mezclar el orden de filas para que el sistema NO se beneficie del
    # ordenamiento natural por grupo.
    df = df.sample(frac=1.0, random_state=semilla).reset_index(drop=True)

    # Validación de integridad — si algo está mal, falla aquí (no se
    # publica un dataset inconsistente).
    _validar_integridad(df)
    return df


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida",
        type=Path,
        default=Path("tests/data/golden_truth_sintetico_robusto.csv"),
        help="Ruta del CSV de salida.",
    )
    parser.add_argument(
        "--n-grupos-extra",
        type=int,
        default=0,
        help="Grupos sintéticos adicionales (escalado). 0 = solo base.",
    )
    parser.add_argument(
        "--semilla",
        type=int,
        default=SEED,
        help="Semilla random (default: 42).",
    )
    parser.add_argument(
        "--resumen",
        action="store_true",
        help="Imprime estadística descriptiva del dataset generado.",
    )
    args = parser.parse_args()

    df = construir_dataset(args.n_grupos_extra, args.semilla)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.salida, index=False)

    print(f"✅ Dataset generado: {args.salida}")
    print(f"   Registros: {len(df):,}")
    print(f"   Grupos verdad: {df['ID_GROUP'].nunique():,}")
    print(f"   NITs únicos: {df['NIT'].nunique():,}")
    print(f"   NITs vacíos: {(df['NIT'] == '').sum():,}")

    if args.resumen:
        print()
        print("Distribución de tamaño de grupo:")
        print(df.groupby("ID_GROUP").size().describe().round(2))
        print()
        print("Distribución de casos frontera:")
        print(df["CASO_FRONTERA"].value_counts())
        print()
        print("Distribución de longitud NIT:")
        nl = df[df["NIT"] != ""]["NIT"].str.len()
        print(nl.value_counts().sort_index())
        print()
        print("Primeras 5 filas:")
        print(df.head().to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
