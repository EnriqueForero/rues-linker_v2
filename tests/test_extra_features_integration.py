"""Test de integración de variables adicionales vía la API pública (v2.7.0).

Complementa ``test_scorer_extra_features.py`` (unidad) verificando que el
parámetro ``extra_features`` de ``deduplicate_unified`` (1) valida su entrada
con fail-fast, (2) se propaga de punta a punta hasta el scorer, y (3) reduce
la sobre-fusión sobre el dataset sintético P2 sin degradar el comportamiento
cuando no se usa.

El dataset P2 (``tests/data_sintetica/dataset_sintetico_p2_extra_features.csv``)
fue diseñado con casos negativos que SOLO una variable adicional puede separar
(CORONA-Bogotá vs CARVAJAL-Cali con NIT adyacente).
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import (
    _validate_extra_features,
    deduplicate_unified,
)
from record_linkage.evaluation.pairwise import evaluar_pares

P2 = Path(__file__).parent / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"


def _run(df: pd.DataFrame, extra_features=None):
    """Corre el pipeline silenciado y devuelve métricas pairwise."""
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                correlativa, _ = deduplicate_unified(
                    df_input=df[["NIT", "RAZON_SOCIAL", "CIUDAD", "TELEFONO"]].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                    extra_features=extra_features,
                )
    finally:
        logging.disable(logging.NOTSET)
    correlativa = correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return evaluar_pares(df["ID_GROUP"].to_numpy(), correlativa["ID_GRUPO"].to_numpy())


@pytest.fixture(scope="module")
def df_p2() -> pd.DataFrame:
    return pd.read_csv(P2, dtype={"NIT": str})


# ── Validación fail-fast ──────────────────────────────────────────────────


def test_columna_inexistente_falla_rapido() -> None:
    cols = pd.Index(["NIT", "RAZON_SOCIAL", "CIUDAD"])
    with pytest.raises(ValueError, match="no existe"):
        _validate_extra_features([{"column": "INEXISTENTE", "weight": 0.1}], cols)


def test_peso_invalido_falla() -> None:
    cols = pd.Index(["CIUDAD"])
    with pytest.raises(ValueError, match="weight"):
        _validate_extra_features([{"column": "CIUDAD", "weight": 0.0}], cols)
    with pytest.raises(ValueError, match="weight"):
        _validate_extra_features([{"column": "CIUDAD", "weight": -0.1}], cols)


def test_tipo_desconocido_falla() -> None:
    cols = pd.Index(["CIUDAD"])
    with pytest.raises(ValueError, match="desconocido"):
        _validate_extra_features([{"column": "CIUDAD", "weight": 0.1, "type": "no_existe"}], cols)


def test_falta_column_falla() -> None:
    cols = pd.Index(["CIUDAD"])
    with pytest.raises(ValueError, match="column"):
        _validate_extra_features([{"weight": 0.1}], cols)


def test_tipos_validos_no_fallan() -> None:
    cols = pd.Index(["CIUDAD", "TELEFONO"])
    # No debe levantar.
    _validate_extra_features(
        [
            {"column": "CIUDAD", "weight": 0.15, "type": "categorical_signed"},
            {"column": "TELEFONO", "weight": 0.10, "type": "exact_signed"},
        ],
        cols,
    )


# ── Efecto end-to-end sobre P2 ─────────────────────────────────────────────


def test_signed_no_introduce_falsos_positivos(df_p2) -> None:
    """Con CIUDAD signed, la precision sobre P2 llega a 1.0 (FP=0)."""
    m = _run(df_p2, [{"column": "CIUDAD", "weight": 0.15, "type": "categorical_signed"}])
    assert m.fp == 0, f"Esperaba 0 falsos positivos con CIUDAD signed, hubo {m.fp}"
    assert m.precision == pytest.approx(1.0, abs=1e-9)


def test_signed_mejora_o_iguala_precision_vs_sin_features(df_p2) -> None:
    """El tipo signed nunca empeora la precision respecto a no usar features."""
    base = _run(df_p2, None)
    signed = _run(df_p2, [{"column": "CIUDAD", "weight": 0.15, "type": "categorical_signed"}])
    assert signed.precision >= base.precision
    assert signed.f1 >= base.f1


def test_categorical_no_firmado_no_separa_negativos(df_p2) -> None:
    """El tipo no firmado (suma) NO reduce los FP: justifica el rediseño signed."""
    base = _run(df_p2, None)
    suma = _run(df_p2, [{"column": "CIUDAD", "weight": 0.15, "type": "categorical"}])
    assert suma.fp == base.fp, (
        "El tipo 'categorical' (solo suma) no debería cambiar los FP; "
        "si los cambia, revisar la lógica de no-op para discrepancias."
    )
