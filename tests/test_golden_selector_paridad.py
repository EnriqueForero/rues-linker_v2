"""Paridad del selector de golden record vectorizado (v2.4.0).

El `AdvancedValueSelector` ganó una API por lotes (`select_best_name_batch`,
`select_best_nit_batch`) que reemplaza los `groupby().apply()` del generator.
Estos tests garantizan que la versión batch produce EXACTAMENTE el mismo
resultado que aplicar el método individual grupo por grupo, sobre el ground
truth exhaustivo (1460 registros, 173 grupos, con casos negativos y NITs con
errores deliberados; reconstruido con scripts/reconstruir_golden_sets.py).

Nota sobre determinismo: en v2.4.0 el desempate final (empate exacto de
frecuencia y longitud) pasó de `max(set(...))` —no determinista— a un desempate
alfabético explícito. Ambos métodos (individual y batch) usan ahora ese mismo
criterio, así que la paridad es exacta y reproducible.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.golden.selector import AdvancedValueSelector

GROUND_TRUTH = Path(__file__).parent / "data" / "golden_truth_exhaustivo.csv"
SP_MAP = {"RUES": 1, "SUPERSOCIEDADES": 2, "CRM": 3, "EXPORTACIONES": 4}


@pytest.fixture(scope="module")
def df_grupos() -> pd.DataFrame:
    """Ground truth exhaustivo con fuentes sintéticas deterministas."""
    df = pd.read_csv(GROUND_TRUTH, dtype={"NIT": str})
    df["NIT_OK"] = df["NIT"]
    rng = np.random.RandomState(42)
    df["SRC"] = rng.choice(list(SP_MAP), size=len(df))
    return df.rename(columns={"ID_GROUP": "ID_GRUPO"})


def test_paridad_nombres(df_grupos: pd.DataFrame) -> None:
    """select_best_name_batch == select_best_name por grupo, para todos los grupos."""
    sel = AdvancedValueSelector(SP_MAP)
    batch = sel.select_best_name_batch(df_grupos, "ID_GRUPO")
    diffs = []
    for gid, g in df_grupos.groupby("ID_GRUPO"):
        individual = sel.select_best_name(g)
        if batch[gid] != individual:
            diffs.append((gid, individual, batch[gid]))
    assert not diffs, f"{len(diffs)} grupos con nombre distinto: {diffs[:5]}"


def test_paridad_nits(df_grupos: pd.DataFrame) -> None:
    """select_best_nit_batch == select_best_nit por grupo, para todos los grupos."""
    sel = AdvancedValueSelector(SP_MAP)
    batch = sel.select_best_nit_batch(df_grupos, "ID_GRUPO")
    diffs = []
    for gid, g in df_grupos.groupby("ID_GRUPO"):
        individual = sel.select_best_nit(g)
        if str(batch[gid]) != str(individual):
            diffs.append((gid, individual, batch[gid]))
    assert not diffs, f"{len(diffs)} grupos con NIT distinto: {diffs[:5]}"


def test_batch_es_determinista(df_grupos: pd.DataFrame) -> None:
    """Dos llamadas batch sobre el mismo input dan el mismo resultado."""
    sel = AdvancedValueSelector(SP_MAP)
    a = sel.select_best_name_batch(df_grupos, "ID_GRUPO")
    b = sel.select_best_name_batch(df_grupos, "ID_GRUPO")
    assert (a == b).all()


def test_singleton_devuelve_su_nombre() -> None:
    """Un grupo de un solo registro devuelve su propio nombre y NIT."""
    sel = AdvancedValueSelector(SP_MAP)
    df = pd.DataFrame(
        {
            "ID_GRUPO": [99],
            "RAZON_SOCIAL": ["EMPRESA UNICA SAS"],
            "SRC": ["RUES"],
            "NIT_OK": ["900123456"],
        }
    )
    assert sel.select_best_name_batch(df, "ID_GRUPO")[99] == "EMPRESA UNICA SAS"
    assert sel.select_best_nit_batch(df, "ID_GRUPO")[99] == "900123456"


def test_nit_sin_validos_devuelve_vacio() -> None:
    """Grupo sin NITs válidos (solo letras) → cadena vacía."""
    sel = AdvancedValueSelector(SP_MAP)
    df = pd.DataFrame(
        {
            "ID_GRUPO": [1, 1],
            "RAZON_SOCIAL": ["A", "B"],
            "SRC": ["RUES", "CRM"],
            "NIT_OK": ["ABC", "XYZ"],
        }
    )
    assert sel.select_best_nit_batch(df, "ID_GRUPO")[1] == ""


def test_prioridad_de_fuente(df_grupos: pd.DataFrame) -> None:
    """El nombre elegido proviene de la fuente de mayor prioridad cuando hay varias.

    Verificación indirecta: si un grupo tiene RUES (prio 1), el nombre elegido
    debe coincidir con la selección individual que respeta esa prioridad. Ya
    cubierto por test_paridad_nombres; aquí afirmamos el invariante explícito en
    un grupo construido a mano.
    """
    sel = AdvancedValueSelector(SP_MAP)
    df = pd.DataFrame(
        {
            "ID_GRUPO": [1, 1, 1],
            "RAZON_SOCIAL": ["NOMBRE CRM", "NOMBRE RUES OFICIAL", "OTRO CRM"],
            "SRC": ["CRM", "RUES", "CRM"],
            "NIT_OK": ["900111", "900111", "900111"],
        }
    )
    # RUES es prioridad 1 y es único en su prioridad → su nombre gana.
    assert sel.select_best_name_batch(df, "ID_GRUPO")[1] == "NOMBRE RUES OFICIAL"
