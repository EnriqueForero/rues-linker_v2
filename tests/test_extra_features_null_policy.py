"""Regresiones de nulos en extra_features firmadas.

Estos tests cubren la divergencia observada entre pandas 2.x y pandas 3.x:
NaN/None/pd.NA no pueden convertirse accidentalmente en valores válidos para
features firmadas. La política documentada es neutralidad: si algún lado es
nulo, la similitud firmada debe ser 0.0, no -1.0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from record_linkage.engine.scorer import VectorizedScorer


def test_to_clean_str_series_materializa_nulos_reales_como_vacio() -> None:
    values = np.array(["Bogota", np.nan, None, pd.NA, "nan", "<NA>"], dtype=object)

    cleaned = VectorizedScorer._to_clean_str_series(values)

    assert cleaned.tolist() == ["Bogota", "", "", "", "nan", "<NA>"]


def test_valid_mask_detecta_nulos_reales_y_strings_nullish() -> None:
    values = VectorizedScorer._to_clean_str_series(
        np.array(["Bogota", np.nan, None, pd.NA, "nan", "NULL", "<NA>", ""], dtype=object)
    )

    mask = VectorizedScorer._valid_mask(values.str.strip().str.upper())

    assert mask.tolist() == [True, False, False, False, False, False, False, False]


def test_categorical_signed_nulo_vs_valor_es_neutral_no_penalizacion() -> None:
    left = np.array(["Bogota", np.nan, None, pd.NA, "Cali"], dtype=object)
    right = np.array(["BOGOTA", "Cali", "Cali", "Cali", "Medellin"], dtype=object)

    sim = VectorizedScorer._feature_similarity_vectorized(left, right, "categorical_signed")

    assert sim.tolist() == [1.0, 0.0, 0.0, 0.0, -1.0]


def test_exact_signed_nulo_vs_valor_es_neutral_no_penalizacion() -> None:
    left = np.array(["123", np.nan, None, pd.NA, "123"], dtype=object)
    right = np.array(["123", "123", "123", "123", "456"], dtype=object)

    sim = VectorizedScorer._feature_similarity_vectorized(left, right, "exact_signed")

    assert sim.tolist() == [1.0, 0.0, 0.0, 0.0, -1.0]
