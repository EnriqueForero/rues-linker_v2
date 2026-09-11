"""generar_ground_truth_grande.py — Ground truth sintético riguroso y grande.

Construye un ground truth sintético DETERMINISTA (`seed=42`) de gran tamaño
(~15.000 registros) con **verdad conocida por construcción**: cada registro
deriva de una empresa-semilla, por lo que sabemos exactamente qué registros
son la misma entidad (mismo `ID_GROUP`).

================================================================================
ADVERTENCIA DE VALIDEZ — LÉASE ANTES DE CONFIAR EN LAS MÉTRICAS
================================================================================
Este es un ground truth SINTÉTICO. Mide qué tan bien el sistema maneja los
TIPOS DE VARIACIÓN QUE ESTE SCRIPT PROGRAMA (typos, sufijos, formatos de NIT,
intermediarios, etc.). NO sustituye un ground truth real etiquetado por humanos
sobre datos de producción. Un F1 alto aquí es NECESARIO pero NO SUFICIENTE para
garantizar calidad en producción. Ver docs/PROTOCOLO_GROUND_TRUTH.md.
================================================================================

QUÉ APORTA SOBRE LOS DATASETS EXISTENTES
    - Tamaño: ~15.000 registros (vs 660-1456 de los oráculos previos).
    - Multi-variable: NIT, RAZON_SOCIAL, CIUDAD, TELEFONO, DIRECCION, EMAIL.
      Permite medir el aporte marginal de cada variable.
    - DOS regímenes en un solo dataset:
        * CON_NIT: empresas colombianas con NIT (régimen RUES/DIAN).
        * SIN_NIT: importadores estilo Corea, solo nombre + ciudad.
      Marcado en la columna REGIMEN para poder medir cada uno por separado.
    - Casos frontera negativos a escala (deben quedar separados).

ESQUEMA DE SALIDA (una columna por variable; vacío donde no aplica)
    ID_REGISTRO, ID_GROUP, REGIMEN, NIT, RAZON_SOCIAL, CIUDAD,
    TELEFONO, DIRECCION, EMAIL, FUENTE, CASO

Uso:
    python scripts/generar_ground_truth_grande.py
    python scripts/generar_ground_truth_grande.py --salida data/gt.csv --factor 2

Author: Claude (auditor)   Date: 2026-05-23   Version: 2.14.0
"""

from __future__ import annotations

import argparse
import random
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# Reutilizamos las funciones de variación ya probadas del generador robusto.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generar_dataset_robusto import (
    EMPRESAS_BASE,
    SUFIJOS,
    _aplicar_typos,
    _calcular_dv,
    _doble_espacio,
    _formato_nit,
    _quitar_tildes,
    _zero_width_space,
)

SEED = 42

# ─────────────────────────────────────────────────────────────────────
# Catálogos auxiliares para las variables nuevas
# ─────────────────────────────────────────────────────────────────────

# Ciudades colombianas con variantes de escritura (para el régimen CON_NIT).
CIUDADES_CO = [
    ["BOGOTA", "BOGOTÁ", "BOGOTA DC", "BOGOTA D.C.", "SANTAFE DE BOGOTA"],
    ["MEDELLIN", "MEDELLÍN", "MEDELLIN ANTIOQUIA"],
    ["CALI", "SANTIAGO DE CALI", "CALI VALLE"],
    ["BARRANQUILLA", "BARRANQUILLA ATLANTICO"],
    ["CARTAGENA", "CARTAGENA DE INDIAS"],
    ["BUCARAMANGA", "BUCARAMANGA SANTANDER"],
    ["PEREIRA", "PEREIRA RISARALDA"],
    ["MANIZALES"],
    ["CUCUTA", "CÚCUTA", "SAN JOSE DE CUCUTA"],
    ["IBAGUE", "IBAGUÉ"],
]

# Ciudades coreanas con variantes (para el régimen SIN_NIT, estilo Corea).
CIUDADES_KR = [
    ["SEOUL", "SEUL", "SEÚL", "SEOUL SI"],
    ["INCHEON", "INCHON"],
    ["BUSAN", "PUSAN"],
    ["GYEONGGI-DO", "GYEONGGI DO", "GYEONGGIDO"],
    ["DAEGU", "TAEGU"],
    ["GWANGJU", "KWANGJU"],
]

# Nombres de importadores coreanos base (régimen SIN_NIT).
IMPORTADORES_KR = [
    "NENOVA",
    "WORLD FLORA",
    "ARES3",
    "JIREH TRADE",
    "DAEDONG GARDENING",
    "SOIREE FLOWER",
    "GREEN FLORAL",
    "EL ROI FIORI",
    "ALETALO",
    "SNOWFOX BRANDING",
    "KANGNAM TRADING",
    "GOEUN FLOWER",
    "DONGKWANG FLOWER",
    "BIT FLOWER",
    "MULTIFLORA",
    "SECUI",
    "ZELECTA TRADING",
    "KOREA MIDLAND",
    "COWORK COFFEE",
    "DONG SUH FOODS",
]

# Sufijos típicos de importadores coreanos.
SUFIJOS_KR = ["CO LTD", "CO., LTD", "CO. LTD", "CO.,LTD.", "CORP", "CORPORATION", "INC", "LLC", ""]

# Intermediarios/exportadores que se REPITEN (causan falsa fusión si no se maneja).
INTERMEDIARIOS_KR = [
    "ELITE EXPORTS INTERNATIONAL INC Y/O",
    "COMMERCIAL ZELECTA TRADING GROUP CORP -",
    "CORPORACION BELEKO S.A. -",
]

CALLES = ["CALLE", "CARRERA", "AVENIDA", "DIAGONAL", "TRANSVERSAL", "CRA", "CL", "AV"]

# Componentes para fabricar nombres de empresa sintéticos adicionales. Permiten
# escalar el número de GRUPOS (no solo el tamaño), que es lo correcto para un
# ground truth riguroso: muchos grupos pequeños, no pocos grupos enormes.
_RAIZ_NOMBRE = [
    "INVERSIONES",
    "COMERCIALIZADORA",
    "DISTRIBUIDORA",
    "INDUSTRIAS",
    "CONSTRUCTORA",
    "AGROPECUARIA",
    "TRANSPORTES",
    "SERVICIOS",
    "SOLUCIONES",
    "TECNOLOGIA",
    "ALIMENTOS",
    "TEXTILES",
    "QUIMICOS",
    "LOGISTICA",
    "MANUFACTURAS",
    "PRODUCTOS",
    "IMPORTACIONES",
    "EXPORTACIONES",
    "CONSULTORES",
    "PROYECTOS",
]
_NUCLEO_NOMBRE = [
    "ANDINA",
    "DEL CARIBE",
    "DEL PACIFICO",
    "BOLIVAR",
    "SANTANDER",
    "CAFETERA",
    "TROPICAL",
    "NACIONAL",
    "CONTINENTAL",
    "DEL VALLE",
    "ATLANTICO",
    "PACIFICO",
    "CENTRAL",
    "ORIENTAL",
    "DEL NORTE",
    "DEL SUR",
    "MILENIO",
    "FUTURO",
    "PROGRESO",
    "HORIZONTE",
    "CUMBRE",
    "ALIANZA",
    "VERTICE",
    "INTEGRAL",
]


def fabricar_empresas_sinteticas(n: int, rng: random.Random) -> list[tuple[str, str, str | None]]:
    """Fabrica n empresas sintéticas con NIT base único (rango 700-799).

    Devuelve tuplas (nit_base, nombre, sigla) compatibles con EMPRESAS_BASE.
    Los NITs usan el rango 7XXXXXXXX, disjunto de EMPRESAS_BASE (8XX/9XX) y de
    los rangos reservados para casos frontera, evitando colisiones de grupo.
    """
    vistos: set[str] = set()
    out: list[tuple[str, str, str | None]] = []
    intentos = 0
    while len(out) < n and intentos < n * 20:
        intentos += 1
        nit = f"7{rng.randint(10_000_000, 99_999_999)}"
        if nit in vistos:
            continue
        vistos.add(nit)
        raiz = rng.choice(_RAIZ_NOMBRE)
        nucleo = rng.choice(_NUCLEO_NOMBRE)
        nombre = f"{raiz} {nucleo}"
        sigla = None
        if rng.random() < 0.25:
            sigla = "".join(w[0] for w in nombre.split())
        out.append((nit, nombre, sigla))
    return out


# ─────────────────────────────────────────────────────────────────────
# Generadores de variables
# ─────────────────────────────────────────────────────────────────────


def _variar_ciudad(opciones: list[str], rng: random.Random, ruido: bool) -> str:
    """Elige una variante de ciudad; con ruido, puede quitar tildes."""
    c = rng.choice(opciones)
    if ruido and rng.random() < 0.3:
        c = _quitar_tildes(c)
    return c


def _gen_telefono(rng: random.Random, base: str | None = None, ruido: bool = False) -> str:
    """Genera o varía un teléfono. Si base se da, produce una variante de formato."""
    if base is None:
        num = f"{rng.randint(300, 320)}{rng.randint(1000000, 9999999)}"
    else:
        num = base
    if not ruido:
        return num
    estilo = rng.choice(["plano", "guion", "espacio", "prefijo", "paren"])
    if estilo == "plano":
        return num
    if estilo == "guion":
        return f"{num[:3]}-{num[3:6]}-{num[6:]}"
    if estilo == "espacio":
        return f"{num[:3]} {num[3:]}"
    if estilo == "prefijo":
        return f"+57{num}"
    return f"({num[:3]}){num[3:]}"


def _gen_direccion(rng: random.Random, base: tuple | None = None, ruido: bool = False) -> str:
    """Genera o varía una dirección colombiana."""
    if base is None:
        via = rng.choice(CALLES)
        n1, n2, n3 = rng.randint(1, 180), rng.randint(1, 99), rng.randint(1, 99)
        return f"{via} {n1} # {n2} - {n3}"
    via, n1, n2, n3 = base
    if ruido:
        # Variar abreviatura de la vía (CALLE <-> CL, CARRERA <-> CRA)
        equiv = {"CALLE": "CL", "CARRERA": "CRA", "AVENIDA": "AV", "CL": "CALLE", "CRA": "CARRERA"}
        if rng.random() < 0.4 and via in equiv:
            via = equiv[via]
        sep = rng.choice([" # ", " No ", " Nro ", " #", "#"])
        return f"{via} {n1}{sep}{n2} - {n3}"
    return f"{via} {n1} # {n2} - {n3}"


def _gen_email(
    nombre: str, rng: random.Random, base: str | None = None, ruido: bool = False
) -> str:
    """Genera un email derivado del nombre."""
    if base is None:
        slug = "".join(ch for ch in _quitar_tildes(nombre).lower() if ch.isalnum())[:12]
        dom = rng.choice(["gmail.com", "hotmail.com", "outlook.com", "empresa.com.co"])
        base = f"{slug}@{dom}"
    if ruido and rng.random() < 0.3:
        # Variación menor: mayúsculas o punto extra
        local, _, dom = base.partition("@")
        if rng.random() < 0.5:
            return f"{local.upper()}@{dom}"
        return f"{local}.co@{dom}"
    return base


# ─────────────────────────────────────────────────────────────────────
# Construcción de grupos
# ─────────────────────────────────────────────────────────────────────


def _variar_nombre(nombre: str, sigla: str | None, rng: random.Random) -> str:
    """Produce una variante realista de un nombre de empresa."""
    estrategia = rng.choices(
        ["typo", "sufijo", "sigla", "sucursal", "tildes", "espacios", "invisible", "limpio"],
        weights=[20, 25, 8, 10, 10, 8, 4, 15],
    )[0]
    base = nombre
    if estrategia == "sigla" and sigla:
        base = sigla
    elif estrategia == "sucursal":
        base = f"{nombre} {rng.choice(['SUCURSAL', 'PLANTA', 'OFICINA PRINCIPAL', 'AGENCIA'])}"
        if rng.random() < 0.5:
            base += f" {rng.choice(['BOGOTA', 'NORTE', 'SUR', 'CENTRO', '1', '2'])}"

    if estrategia in ("sufijo", "limpio", "sigla", "sucursal"):
        suf = rng.choice(SUFIJOS)
        cand = f"{base} {suf}".strip()
    else:
        cand = base

    if estrategia == "typo":
        cand = _aplicar_typos(cand, rng.randint(1, 2), rng)
    elif estrategia == "tildes":
        cand = _quitar_tildes(cand)
    elif estrategia == "espacios":
        cand = _doble_espacio(cand, rng)
    elif estrategia == "invisible":
        cand = _zero_width_space(cand, rng)
    return cand


def construir_con_nit(
    empresas: list[tuple[str, str, str | None]],
    factor: int,
    rng: random.Random,
    start_gid: int,
) -> list[dict]:
    """Genera registros del régimen CON_NIT a partir de una lista de empresas."""
    filas: list[dict] = []
    gid = start_gid
    rid = 0
    for nit_base, nombre, sigla in empresas:
        gid += 1
        dv = _calcular_dv(nit_base)
        # Variables canónicas del grupo (se varían por registro).
        ciudad_opts = rng.choice(CIUDADES_CO)
        tel_base = _gen_telefono(rng)
        via = rng.choice(CALLES)
        dir_base = (via, rng.randint(1, 180), rng.randint(1, 99), rng.randint(1, 99))
        email_base = _gen_email(nombre, rng)
        # Tamaño de grupo: distribución sesgada a grupos pequeños, cola larga.
        n_var = rng.choices([1, 2, 3, 4, 6, 10], weights=[30, 32, 20, 10, 5, 3])[0]
        n_var = max(1, n_var + (factor - 1))
        for _ in range(n_var):
            rid += 1
            ruido = rng.random() < 0.6
            filas.append(
                {
                    "ID_REGISTRO": f"CN{rid:06d}",
                    "ID_GROUP": gid,
                    "REGIMEN": "CON_NIT",
                    "NIT": _formato_nit(
                        nit_base, dv, rng.choice(["plano", "guion", "puntos", "dv", "espacios"])
                    ),
                    "RAZON_SOCIAL": _variar_nombre(nombre, sigla, rng),
                    "CIUDAD": _variar_ciudad(ciudad_opts, rng, ruido),
                    "TELEFONO": _gen_telefono(rng, tel_base, ruido),
                    "DIRECCION": _gen_direccion(rng, dir_base, ruido),
                    "EMAIL": _gen_email(nombre, rng, email_base, ruido),
                    "FUENTE": rng.choice(["RUES", "DIAN", "CRM", "SUPERSOCIEDADES"]),
                    "CASO": "positivo_con_nit",
                }
            )
    return filas


def fabricar_importadores_sinteticos(n: int, rng: random.Random) -> list[str]:
    """Fabrica n nombres de importadores coreanos sintéticos únicos."""
    raices = [
        "DAE",
        "HAN",
        "KOR",
        "SEO",
        "JIN",
        "MIN",
        "SUN",
        "YOO",
        "KANG",
        "PARK",
        "LEE",
        "CHOI",
        "JUNG",
        "HWANG",
        "SHIN",
        "WOO",
        "BAEK",
        "NAM",
        "OH",
        "RYU",
    ]
    sufijos = [
        "FLOWER",
        "TRADING",
        "FOODS",
        "GLOBAL",
        "TECH",
        "BIO",
        "CHEM",
        "STEEL",
        "TEXTILE",
        "MARINE",
        "LOGIS",
        "FRESH",
        "GREEN",
        "STAR",
        "PRIME",
    ]
    vistos: set[str] = set()
    out: list[str] = []
    intentos = 0
    while len(out) < n and intentos < n * 20:
        intentos += 1
        nombre = f"{rng.choice(raices)}{rng.choice(raices).lower().capitalize()} {rng.choice(sufijos)}".upper()
        if nombre in vistos:
            continue
        vistos.add(nombre)
        out.append(nombre)
    return out


def construir_sin_nit(
    importadores: list[str], factor: int, rng: random.Random, start_gid: int
) -> list[dict]:
    """Genera registros del régimen SIN_NIT (estilo Corea: nombre + ciudad)."""
    filas: list[dict] = []
    gid = start_gid
    rid = 0
    for nombre in importadores:
        gid += 1
        ciudad_opts = rng.choice(CIUDADES_KR)
        n_var = rng.choices([1, 2, 3, 5, 8], weights=[25, 30, 22, 15, 8])[0]
        n_var = max(1, n_var + (factor - 1))
        for _ in range(n_var):
            rid += 1
            ruido = rng.random() < 0.6
            suf = rng.choice(SUFIJOS_KR)
            rs = f"{nombre} {suf}".strip()
            if ruido:
                rs = _aplicar_typos(rs, 1, rng) if rng.random() < 0.4 else _doble_espacio(rs, rng)
            filas.append(
                {
                    "ID_REGISTRO": f"SN{rid:06d}",
                    "ID_GROUP": gid,
                    "REGIMEN": "SIN_NIT",
                    "NIT": "",
                    "RAZON_SOCIAL": rs,
                    "CIUDAD": _variar_ciudad(ciudad_opts, rng, ruido),
                    "TELEFONO": "",
                    "DIRECCION": "",
                    "EMAIL": "",
                    "FUENTE": "IMPORTACIONES",
                    "CASO": "positivo_sin_nit",
                }
            )
    return filas


def construir_negativos_sin_nit(rng: random.Random, start_gid: int) -> list[dict]:
    """Casos frontera negativos del régimen SIN_NIT.

    El intermediario compartido NO debe fusionar las empresas. Cada empresa
    tras el 'Y/O' es un grupo DISTINTO.
    """
    filas: list[dict] = []
    gid = start_gid
    rid = 0
    for inter in INTERMEDIARIOS_KR:
        for nombre in rng.sample(IMPORTADORES_KR, k=8):
            gid += 1  # cada combinación intermediario+empresa es su PROPIO grupo
            ciudad_opts = rng.choice(CIUDADES_KR)
            # 1-2 registros por grupo (poca repetición)
            for _ in range(rng.randint(1, 2)):
                rid += 1
                suf = rng.choice(SUFIJOS_KR)
                rs = f"{inter} {nombre} {suf}".strip()
                filas.append(
                    {
                        "ID_REGISTRO": f"NG{rid:06d}",
                        "ID_GROUP": gid,
                        "REGIMEN": "SIN_NIT",
                        "NIT": "",
                        "RAZON_SOCIAL": rs,
                        "CIUDAD": _variar_ciudad(ciudad_opts, rng, True),
                        "TELEFONO": "",
                        "DIRECCION": "",
                        "EMAIL": "",
                        "FUENTE": "IMPORTACIONES",
                        "CASO": "negativo_intermediario",
                    }
                )
    return filas


def construir_negativos_genericos(rng: random.Random, start_gid: int) -> list[dict]:
    """Negativos por nombre genérico compartido (X CORPORATION vs Y CORPORATION)."""
    filas: list[dict] = []
    gid = start_gid
    rid = 0
    prefijos = [
        "SECUI",
        "MULTIFLORA",
        "CK",
        "KS",
        "ALPHA",
        "BETA",
        "OMEGA",
        "DELTA",
        "GLOBAL",
        "PRIME",
    ]
    for p in prefijos:
        gid += 1
        rid += 1
        ciudad_opts = rng.choice(CIUDADES_KR)
        filas.append(
            {
                "ID_REGISTRO": f"NG9{rid:05d}",
                "ID_GROUP": gid,
                "REGIMEN": "SIN_NIT",
                "NIT": "",
                "RAZON_SOCIAL": f"{p} CORPORATION",
                "CIUDAD": _variar_ciudad(ciudad_opts, rng, False),
                "TELEFONO": "",
                "DIRECCION": "",
                "EMAIL": "",
                "FUENTE": "IMPORTACIONES",
                "CASO": "negativo_generico",
            }
        )
    return filas


# ─────────────────────────────────────────────────────────────────────
# Validación de integridad del ground truth
# ─────────────────────────────────────────────────────────────────────


def validar(df: pd.DataFrame) -> None:
    """Comprueba invariantes del ground truth (Fail Fast)."""
    errores: list[str] = []
    if df["ID_REGISTRO"].duplicated().any():
        errores.append("ID_REGISTRO duplicado.")
    if df["RAZON_SOCIAL"].str.strip().eq("").any():
        errores.append("Hay RAZON_SOCIAL vacía.")
    # En CON_NIT, todos los registros del mismo grupo deben normalizar al mismo NIT base.
    con = df[df["REGIMEN"] == "CON_NIT"].copy()
    con["NIT_NORM"] = con["NIT"].str.replace(r"[^0-9]", "", regex=True).str[:9]
    por_grupo = con.groupby("ID_GROUP")["NIT_NORM"].nunique()
    if (por_grupo > 1).any():
        malos = por_grupo[por_grupo > 1].index.tolist()[:5]
        errores.append(f"Grupos CON_NIT con NIT base inconsistente: {malos}")
    # Ningún ID_GROUP debe mezclar regímenes.
    if (df.groupby("ID_GROUP")["REGIMEN"].nunique() > 1).any():
        errores.append("Hay ID_GROUP que mezcla CON_NIT y SIN_NIT.")
    if errores:
        raise ValueError("Ground truth inválido:\n  - " + "\n  - ".join(errores))


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera ground truth sintético grande.")
    parser.add_argument("--salida", default="data/ground_truth/ground_truth_grande.csv")
    parser.add_argument("--factor", type=int, default=1, help="Suma al tamaño base de cada grupo.")
    parser.add_argument(
        "--empresas-extra",
        type=int,
        default=900,
        help="Empresas CON_NIT sintéticas adicionales (escala nº de grupos).",
    )
    parser.add_argument(
        "--importadores-extra",
        type=int,
        default=180,
        help="Importadores SIN_NIT sintéticos adicionales.",
    )
    parser.add_argument("--semilla", type=int, default=SEED)
    args = parser.parse_args()

    rng = random.Random(args.semilla)

    # Empresas CON_NIT: las reales (EMPRESAS_BASE) + sintéticas adicionales.
    empresas = list(EMPRESAS_BASE) + fabricar_empresas_sinteticas(args.empresas_extra, rng)
    # Importadores SIN_NIT: los base estilo Corea + sintéticos.
    importadores = list(IMPORTADORES_KR) + fabricar_importadores_sinteticos(
        args.importadores_extra, rng
    )

    filas: list[dict] = []
    filas += construir_con_nit(empresas, args.factor, rng, start_gid=0)
    gid_max = max(f["ID_GROUP"] for f in filas)
    filas += construir_sin_nit(importadores, args.factor, rng, start_gid=gid_max)
    gid_max = max(f["ID_GROUP"] for f in filas)
    filas += construir_negativos_sin_nit(rng, start_gid=gid_max)
    gid_max = max(f["ID_GROUP"] for f in filas)
    filas += construir_negativos_genericos(rng, start_gid=gid_max)

    df = pd.DataFrame(filas)
    # Normalizar texto unicode (NFC) para consistencia.
    for col in ("RAZON_SOCIAL", "CIUDAD"):
        df[col] = df[col].map(lambda s: unicodedata.normalize("NFC", str(s)))

    # Mezclar el orden (un ground truth no debe venir agrupado).
    df = df.sample(frac=1.0, random_state=args.semilla).reset_index(drop=True)

    validar(df)

    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(salida, index=False)

    # Resumen
    print(f"✅ Ground truth generado: {salida}")
    print(f"   Registros : {len(df):,}")
    print(f"   Grupos    : {df['ID_GROUP'].nunique():,}")
    print(f"   Regímenes : {df['REGIMEN'].value_counts().to_dict()}")
    print(f"   Casos     : {df['CASO'].value_counts().to_dict()}")
    print(
        f"   Variables : NIT {(df['NIT'].str.len() > 0).sum():,} no vacíos · "
        f"CIUDAD {(df['CIUDAD'].str.len() > 0).sum():,} · "
        f"TEL {(df['TELEFONO'].str.len() > 0).sum():,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
