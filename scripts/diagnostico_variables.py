#!/usr/bin/env python
"""diagnostico_variables.py — Cuánto separa cada variable, y con qué medida.

Antes de declarar una variable como evidencia hay que saber dos cosas: qué tan
seguido coincide en pares que SÍ son el mismo ente, y qué tan seguido coincide
en pares que NO lo son. La diferencia entre esas dos cifras —la separación— es
lo único que hace útil a una variable. Una que coincide el 90 % de las veces
en los verdaderos y también el 88 % en los falsos no aporta nada.

La segunda cosa que este script mide es que **la separación depende de la
medida**, no solo de la variable. La geografía parece floja con comparación
exacta (40 pp) y es fuerte con contención de tokens (70 pp). Es la misma
columna.

Reproduce las cifras publicadas en `docs/BENCHMARK.md` §3 y en ADR-0005.

USO
    python scripts/diagnostico_variables.py
    python scripts/diagnostico_variables.py --datos otro_benchmark.csv

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.19.0
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

DATOS_POR_DEFECTO = RAIZ / "data" / "benchmark" / "benchmark_institucional.csv.gz"

#: Palabras que no aportan identidad y solo ensucian la comparación.
VACIAS = frozenset({"DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "D", "C", "DC"})

#: Semilla de los pares al azar: la tercera referencia que hace falta para
#: saber si la coincidencia en los negativos duros es alta o simplemente
#: normal en esa columna.
SEMILLA = 42
PARES_AL_AZAR = 20_000


def normalizar(texto: object) -> str:
    plano = unicodedata.normalize("NFKD", str(texto).upper())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", plano)).strip()


def tokens(texto: object) -> frozenset[str]:
    return frozenset(t for t in normalizar(texto).split() if t not in VACIAS)


def conjunto(texto: object) -> frozenset[str]:
    partes = (normalizar(p) for p in re.split(r"[|;]", str(texto)))
    return frozenset(p for p in partes if p)


# ── Las medidas que se ponen a competir ───────────────────────────────────

MEDIDAS: dict[str, Callable[[object, object], bool]] = {
    "exacta": lambda a, b: normalizar(a) == normalizar(b),
    "contencion": lambda a, b: (
        bool(tokens(a)) and bool(tokens(b)) and (tokens(a) <= tokens(b) or tokens(b) <= tokens(a))
    ),
    "jaccard=1": lambda a, b: (
        bool(conjunto(a)) and bool(conjunto(b)) and conjunto(a) == conjunto(b)
    ),
    "solapamiento=1": lambda a, b: (
        bool(conjunto(a))
        and bool(conjunto(b))
        and len(conjunto(a) & conjunto(b)) == min(len(conjunto(a)), len(conjunto(b)))
    ),
}


def _pares_dentro_de(bloques: Iterator[pd.DataFrame], columna: str) -> list[tuple[str, str]]:
    """Pares de valores no vacíos entre miembros de un mismo bloque."""
    salida: list[tuple[str, str]] = []
    for bloque in bloques:
        valores = [v for v in bloque[columna] if str(v).strip()]
        salida.extend(itertools.combinations(valores, 2))
    return salida


def pares_verdaderos(datos: pd.DataFrame, columna: str) -> list[tuple[str, str]]:
    """Pares del MISMO ente, en el estrato con datos reales."""
    real = datos[(datos["ESTRATO"] == "REAL") & (datos[columna].str.strip() != "")]
    return _pares_dentro_de((b for _, b in real.groupby("ID_GROUP")), columna)


def pares_negativos(datos: pd.DataFrame, columna: str) -> list[tuple[str, str]]:
    """Pares de entes DISTINTOS con nombre confundible: el contraste que importa.

    Comparar contra pares al azar subestima el problema, porque al azar los
    nombres no se parecen y el enlazador nunca los va a considerar. El negativo
    que cuenta es el que sí llega a la mesa.
    """
    negativos = datos[
        datos["CASO"].str.startswith("negativo_empresa") & (datos[columna].str.strip() != "")
    ].copy()
    firma = negativos["RAZON_SOCIAL"].map(
        lambda n: " ".join(sorted(sorted(normalizar(n).split(), key=len, reverse=True)[:2]))
    )
    return _pares_dentro_de((b for _, b in negativos.groupby(firma)), columna)


def pares_al_azar(datos: pd.DataFrame, columna: str) -> list[tuple[str, str]]:
    valores = datos.loc[datos[columna].str.strip() != "", columna].to_numpy()
    if len(valores) < 2:
        return []
    generador = np.random.default_rng(SEMILLA)
    izq = generador.integers(0, len(valores), PARES_AL_AZAR)
    der = generador.integers(0, len(valores), PARES_AL_AZAR)
    return [(valores[a], valores[b]) for a, b in zip(izq, der, strict=True) if a != b]


def tasa(pares: list[tuple[str, str]], medida: Callable[[object, object], bool]) -> float:
    return float(np.mean([medida(a, b) for a, b in pares])) if pares else float("nan")


#: Qué medidas tiene sentido probar en cada variable. Un teléfono no es un
#: conjunto y un CIIU no es un topónimo; probar todo contra todo produciría
#: cifras sin significado.
PLAN = {
    "DEPARTAMENTO": ("exacta", "contencion"),
    "MUNICIPIO": ("exacta", "contencion"),
    "CIIU": ("exacta", "jaccard=1", "solapamiento=1"),
    "TAMANO": ("exacta",),
}


def main() -> int:
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--datos", type=Path, default=DATOS_POR_DEFECTO)
    args = analizador.parse_args()

    datos = pd.read_csv(args.datos, dtype=str, keep_default_na=False)
    faltan = {"ESTRATO", "ID_GROUP", "CASO", "RAZON_SOCIAL"} - set(datos.columns)
    if faltan:
        raise SystemExit(f"al conjunto le faltan columnas: {sorted(faltan)}")

    print(f"\nPODER DE SEPARACIÓN POR VARIABLE Y MEDIDA  ·  {args.datos.name}")
    print("=" * 92)
    print(
        f"{'variable':<14s}{'medida':<16s}{'verdaderos':>12s}{'negativos':>11s}"
        f"{'al azar':>10s}{'separación':>13s}{'n verd.':>9s}{'n neg.':>8s}"
    )
    print("-" * 92)
    for columna, medidas in PLAN.items():
        if columna not in datos.columns:
            continue
        verdaderos = pares_verdaderos(datos, columna)
        negativos = pares_negativos(datos, columna)
        azar = pares_al_azar(datos, columna)
        for nombre in medidas:
            medida = MEDIDAS[nombre]
            v, n, a = tasa(verdaderos, medida), tasa(negativos, medida), tasa(azar, medida)
            print(
                f"{columna:<14s}{nombre:<16s}{v * 100:11.2f}%{n * 100:10.2f}%"
                f"{a * 100:9.2f}%{(v - n) * 100:12.1f} pp{len(verdaderos):9,d}{len(negativos):8,d}"
            )
        print("-" * 92)
    print(
        "\nLa separación es la columna que decide. Una variable sin separación no\n"
        "aporta evidencia por más veces que coincida.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
