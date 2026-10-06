"""Tests de equivalencia para vectorizaciones aplicadas al pipeline.

Cada test compara:
    - versión original con `.apply` (referencia)
    - versión vectorizada (candidata)

Si los resultados difieren para CUALQUIER entrada del dataset de prueba,
el test falla. Esto es la única red de seguridad antes de tocar el código
de producción.

Ejecutar:
    pytest tests/test_vectorization_equivalence.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ════════════════════════════════════════════════════════════════════
# Caso 1: SEVERIDAD por CONFIDENCE_SCORE
# Original (suite.py:1207):
#     low_confidence['CONFIDENCE_SCORE'].apply(
#         lambda x: 'ALTA' if x < 0.5 else 'MEDIA'
#     )
# ════════════════════════════════════════════════════════════════════


def _severidad_apply(score: float) -> str:
    return "ALTA" if score < 0.5 else "MEDIA"


def severidad_vectorizada(scores: pd.Series) -> pd.Series:
    """Versión vectorizada con np.where.

    Para series vacías, preserva el dtype original (igual que apply).
    """
    if len(scores) == 0:
        return scores.copy()
    return pd.Series(
        np.where(scores < 0.5, "ALTA", "MEDIA"),
        index=scores.index,
    )


@pytest.mark.parametrize(
    "scores",
    [
        pd.Series([0.1, 0.4, 0.5, 0.6, 0.74, 0.49999]),
        pd.Series([0.0, 1.0]),
        pd.Series([], dtype=float),
        pd.Series([0.5] * 100),
        pd.Series(np.random.RandomState(42).uniform(0, 1, 1000)),
    ],
)
def test_severidad_equivalente(scores: pd.Series) -> None:
    """La vectorización de SEVERIDAD debe ser idéntica al apply original."""
    referencia = scores.apply(_severidad_apply)
    candidata = severidad_vectorizada(scores)
    pd.testing.assert_series_equal(referencia, candidata, check_names=False)


# ════════════════════════════════════════════════════════════════════
# Caso 2: TRUE_GROUP — strip + UNKNOWN si NaN
# Original (evaluador_verdad.py::analyze_errors; antes ground_truth.py:459):
#     analysis_df[truth_col].apply(
#         lambda x: str(x).strip() if pd.notna(x) else 'UNKNOWN'
#     )
# ════════════════════════════════════════════════════════════════════


def _true_group_apply(x: object) -> str:
    return str(x).strip() if pd.notna(x) else "UNKNOWN"


def true_group_vectorizada(s: pd.Series) -> pd.Series:
    """Versión vectorizada: notna primero, luego astype(str).str.strip()."""
    mask_notna = s.notna()
    # Trabajar con string version solo para los notna
    result = pd.Series("UNKNOWN", index=s.index, dtype=object)
    if mask_notna.any():
        result.loc[mask_notna] = s.loc[mask_notna].astype(str).str.strip()
    return result


@pytest.mark.parametrize(
    "valores",
    [
        pd.Series(["  abc  ", "DEF", "  ghi"]),
        pd.Series(["abc", None, "def", np.nan, "  xy "]),
        pd.Series([1, 2.5, None, "text  "]),
        pd.Series([None, np.nan, pd.NA], dtype=object),
        pd.Series([], dtype=object),
    ],
)
def test_true_group_equivalente(valores: pd.Series) -> None:
    """TRUE_GROUP vectorizado debe coincidir con el apply original."""
    referencia = valores.apply(_true_group_apply)
    candidata = true_group_vectorizada(valores)
    pd.testing.assert_series_equal(referencia, candidata, check_names=False, check_dtype=False)


# ════════════════════════════════════════════════════════════════════
# Caso 3: Longitud de columna de listas
# Original (hybrid.py:147, 193):
#     df['found_terms_list'].apply(len)
# ════════════════════════════════════════════════════════════════════


def longitud_listas_vectorizada(s: pd.Series) -> pd.Series:
    """str.len() funciona sobre Series de listas en pandas ≥ 1.0."""
    return s.str.len()


@pytest.mark.parametrize(
    "lists",
    [
        pd.Series([[], ["a"], ["a", "b"], ["x", "y", "z"]]),
        pd.Series([[]] * 50),
        pd.Series([["único"]]),
        pd.Series([list(range(i)) for i in range(20)]),
    ],
)
def test_longitud_listas_equivalente(lists: pd.Series) -> None:
    """str.len() sobre columna de listas debe igualar apply(len)."""
    referencia = lists.apply(len)
    candidata = longitud_listas_vectorizada(lists)
    # str.len puede devolver Int64 nullable; convertir a int64 para comparar
    pd.testing.assert_series_equal(
        referencia.astype("int64"),
        candidata.astype("int64"),
        check_names=False,
    )


# ════════════════════════════════════════════════════════════════════
# Caso 4: Matriz de co-ocurrencia de fuentes por ID_GRUPO
# Original (suite.py:410-422):
#     grouped = correlative.groupby('ID_GRUPO')['SRC'].apply(set)
#     for src1 in sources:
#         for src2 in sources:
#             if src1 == src2:
#                 count = sum(src1 in g for g in grouped)
#             else:
#                 count = sum((src1 in g) and (src2 in g) for g in grouped)
#             matrix.loc[src1, src2] = count
# ════════════════════════════════════════════════════════════════════


def cooccurrence_original(correlative: pd.DataFrame) -> pd.DataFrame:
    """Implementación original con doble loop O(n²·g)."""
    sources = sorted(correlative["SRC"].unique())
    matrix = pd.DataFrame(0, index=sources, columns=sources)
    grouped = correlative.groupby("ID_GRUPO")["SRC"].apply(set)
    for src1 in sources:
        for src2 in sources:
            if src1 == src2:
                count = sum(src1 in g for g in grouped)
            else:
                count = sum((src1 in g) and (src2 in g) for g in grouped)
            matrix.loc[src1, src2] = count
    return matrix


def cooccurrence_vectorizada(correlative: pd.DataFrame) -> pd.DataFrame:
    """Versión vectorizada con pd.crosstab + producto matricial.

    Lógica:
        1. Crear matriz de presencia (grupos × fuentes): 1 si la fuente
           aparece en el grupo, 0 si no.
        2. Producto X.T @ X cuenta co-ocurrencias por par de fuentes.
        3. La diagonal cuenta cuántos grupos contienen cada fuente.

    Esto es O(n_grupos · n_fuentes) en lugar de O(n_fuentes² · n_grupos).
    """
    sources = sorted(correlative["SRC"].unique())
    # Matriz de presencia: filas=ID_GRUPO, cols=SRC, valor=1 si existe
    presencia = (
        pd.crosstab(correlative["ID_GRUPO"], correlative["SRC"])
        .reindex(columns=sources, fill_value=0)
        .clip(upper=1)  # presencia binaria, no conteo
    )
    # Producto matricial: matriz[i,j] = nº de grupos donde i y j coexisten
    co = presencia.T.values @ presencia.values
    matrix = pd.DataFrame(co, index=sources, columns=sources)
    return matrix


def _generar_correlative_aleatorio(n_grupos: int, fuentes: list[str], seed: int) -> pd.DataFrame:
    """Genera correlative sintético para tests."""
    rng = np.random.RandomState(seed)
    filas = []
    for gid in range(n_grupos):
        # Cada grupo tiene entre 1 y len(fuentes) fuentes distintas
        n_src = rng.randint(1, len(fuentes) + 1)
        srcs = rng.choice(fuentes, size=n_src, replace=False)
        for src in srcs:
            filas.append({"ID_GRUPO": gid, "SRC": src})
    return pd.DataFrame(filas)


@pytest.mark.parametrize(
    "correlative",
    [
        # Caso pequeño y manual
        pd.DataFrame(
            {
                "ID_GRUPO": [1, 1, 1, 2, 2, 3, 3, 3, 3],
                "SRC": ["A", "B", "C", "A", "B", "A", "B", "C", "D"],
            }
        ),
        # Caso sintético con 50 grupos, 4 fuentes
        _generar_correlative_aleatorio(50, ["RUES", "DIAN", "CRM", "SUPERSOCIEDADES"], 1),
        # Caso grande
        _generar_correlative_aleatorio(500, ["A", "B", "C", "D", "E"], 42),
    ],
)
def test_cooccurrence_equivalente(correlative: pd.DataFrame) -> None:
    """Matriz de co-ocurrencia vectorizada debe igualar el doble loop."""
    referencia = cooccurrence_original(correlative)
    candidata = cooccurrence_vectorizada(correlative)
    # Reindexar para asegurar mismo orden de columnas/filas
    candidata = candidata.reindex(index=referencia.index, columns=referencia.columns)
    pd.testing.assert_frame_equal(
        referencia.astype("int64"),
        candidata.astype("int64"),
        check_names=False,
    )
