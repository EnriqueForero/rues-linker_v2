"""Enriquece el ground truth exhaustivo (1456 regs) con una columna CIUDAD.

MOTIVACIÓN
----------
El ground truth exhaustivo solo tiene ``NIT, RAZON_SOCIAL, ID_GROUP``. No
contiene variables adicionales, así que el efecto de ``extra_features``
(introducido en v2.7.0) NO es medible sobre él. Este script genera una versión
enriquecida ``golden_truth_exhaustivo_ciudad.csv`` añadiendo una columna
``CIUDAD`` **determinista** que respeta la verdad del dataset:

- **Coherencia intra-grupo:** todas las variantes de una misma empresa
  verdadera (mismo ``ID_GROUP``) reciben la MISMA ciudad. Esto refleja que
  una empresa real está en una ciudad; sus duplicados también.
- **Divergencia en negativos:** la ciudad se deriva del ``ID_GROUP`` (no del
  NIT), de modo que dos grupos DISTINTOS con NITs adyacentes —los casos
  negativos diseñados que el sistema sobre-fusiona— caen en ciudades
  distintas con alta probabilidad. Es exactamente la señal que una variable
  adicional firmada puede explotar para separarlos.
- **Realismo con ruido:** un 12 % de las filas recibe ciudad vacía (datos
  faltantes, como en el RUES real) para que el test no asuma cobertura
  perfecta. El tipo ``categorical_signed`` trata el nulo como neutral, así
  que el ruido no penaliza injustamente.

IMPORTANTE — honestidad metodológica
------------------------------------
Esta ciudad es SINTÉTICA y favorable al feature por construcción (se asigna
por grupo verdadero). Mide el TECHO del beneficio, no el caso real, donde la
ciudad tendría ruido propio y a veces coincidiría entre empresas distintas.
Sirve para: (1) demostrar que el cableado end-to-end mueve la métrica a
escala, (2) dar un dataset replicable. NO sustituye al ground truth real con
ciudades reales que pide P0-2 del ROADMAP.

El dataset es DETERMINISTA (sin aleatoriedad de runtime; deriva todo del
ID_GROUP y el índice). Se puede regenerar idénticamente.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Catálogo base de ciudades colombianas. Para garantizar que CADA grupo
# verdadero reciba una ciudad distinta (y así el feature pueda separar grupos
# que el motor sobre-fusiona), el catálogo se expande programáticamente con un
# sufijo de zona cuando hay más grupos que ciudades base. La asignación es
# inyectiva por grupo: dos grupos distintos NUNCA comparten ciudad.
_CIUDADES_BASE: tuple[str, ...] = (
    "BOGOTA",
    "MEDELLIN",
    "CALI",
    "BARRANQUILLA",
    "CARTAGENA",
    "BUCARAMANGA",
    "PEREIRA",
    "MANIZALES",
    "CUCUTA",
    "IBAGUE",
    "VILLAVICENCIO",
    "SANTA MARTA",
    "PASTO",
    "MONTERIA",
    "NEIVA",
    "ARMENIA",
    "POPAYAN",
    "VALLEDUPAR",
    "SINCELEJO",
    "TUNJA",
)

# 1 de cada N filas recibe ciudad vacía (simula datos faltantes del RUES).
# El nulo es por POSICIÓN de fila, nunca por grupo completo: así ningún grupo
# queda enteramente sin ciudad (lo que anularía la señal para ese grupo).
_NULL_EVERY = 8  # ≈12.5 % de nulos


def _catalogo_para(n_grupos: int) -> list[str]:
    """Construye un catálogo de al menos ``n_grupos`` ciudades únicas.

    Si hay más grupos que ciudades base, añade un sufijo de zona (NORTE, SUR,
    …) para generar nombres únicos y estables, manteniéndolos realistas.

    Args:
        n_grupos: Número de ciudades únicas requeridas.

    Returns:
        Lista de nombres de ciudad únicos, de longitud >= ``n_grupos``.
    """
    zonas = (
        "",
        " NORTE",
        " SUR",
        " ORIENTE",
        " OCCIDENTE",
        " CENTRO",
        " NORORIENTE",
        " NOROCCIDENTE",
        " SURORIENTE",
        " SUROCCIDENTE",
        " ZONA 1",
        " ZONA 2",
        " ZONA 3",
        " ZONA 4",
        " ZONA 5",
    )
    catalogo: list[str] = []
    for zona in zonas:
        for base in _CIUDADES_BASE:
            catalogo.append(f"{base}{zona}")
            if len(catalogo) >= n_grupos:
                return catalogo
    return catalogo


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columna CIUDAD determinista e inyectiva por grupo.

    Args:
        df: DataFrame con al menos ``ID_GROUP``. El orden de filas se respeta.

    Returns:
        Copia del DataFrame con una columna ``CIUDAD`` añadida.

    Raises:
        ValueError: si falta la columna ``ID_GROUP``.
    """
    if "ID_GROUP" not in df.columns:
        raise ValueError("El DataFrame debe contener la columna 'ID_GROUP'.")

    out = df.copy().reset_index(drop=True)
    group_codes = pd.factorize(out["ID_GROUP"])[0]
    catalogo = _catalogo_para(int(group_codes.max()) + 1)
    # Asignación INYECTIVA: cada código de grupo → una ciudad única del catálogo.
    ciudades = [catalogo[code] for code in group_codes]

    # Inyectar nulos deterministas por posición de fila (nunca por grupo entero).
    ciudades = [
        "" if (pos % _NULL_EVERY == _NULL_EVERY - 1) else c for pos, c in enumerate(ciudades)
    ]
    out["CIUDAD"] = ciudades
    return out


def main() -> None:
    """Lee el exhaustivo, lo enriquece y guarda la versión con CIUDAD."""
    data_dir = Path(__file__).resolve().parent.parent / "tests" / "data"
    src = data_dir / "golden_truth_exhaustivo.csv"
    dst = data_dir / "golden_truth_exhaustivo_ciudad.csv"

    if not src.exists():
        raise FileNotFoundError(f"No se encontró el ground truth base: {src}")

    df = pd.read_csv(src, dtype={"NIT": str})
    enriched = enrich(df)
    enriched.to_csv(dst, index=False)

    n_null = (enriched["CIUDAD"] == "").sum()
    print(f"Enriquecido: {len(enriched)} registros, {enriched['ID_GROUP'].nunique()} grupos.")
    print(
        f"Ciudades distintas: {enriched['CIUDAD'].nunique()} · nulos: {n_null} "
        f"({n_null / len(enriched):.1%})."
    )
    print(f"Guardado en: {dst}")


if __name__ == "__main__":
    main()
