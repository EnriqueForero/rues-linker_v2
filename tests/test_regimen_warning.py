"""Tests del guardián de mezcla CON_NIT/SIN_NIT en deduplicate_unified (v0.7.4).

Cierra la deuda de "afirmación de calidad sin verificación": el docstring
advertía que deduplicate_unified no debe usarse en datos mixtos, pero nada
lo detectaba en runtime. Ahora emite UserWarning.
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified


def _df_mixto(n_con_nit: int, n_sin_nit: int) -> pd.DataFrame:
    """DataFrame con una mezcla de regímenes (con y sin NIT)."""
    filas = []
    for i in range(n_con_nit):
        filas.append({"NIT": f"{900000000 + i}", "RAZON_SOCIAL": f"EMPRESA CON NIT {i}"})
    for i in range(n_sin_nit):
        filas.append({"NIT": "", "RAZON_SOCIAL": f"IMPORTADORA SIN NIT {i}"})
    return pd.DataFrame(filas)


def test_warning_en_mezcla_de_regimenes(tmp_path):
    """50/50 CON_NIT/SIN_NIT debe emitir UserWarning de mezcla."""
    df = _df_mixto(30, 30)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deduplicate_unified(
            df_input=df,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "out"),
        )
    mezcla = [w for w in caught if "mezcla de regímenes" in str(w.message)]
    assert mezcla, "Debió advertir sobre la mezcla CON_NIT/SIN_NIT"
    assert mezcla[0].category is UserWarning


def test_sin_warning_solo_con_nit(tmp_path):
    """Dataset 100% CON_NIT no debe advertir."""
    df = _df_mixto(50, 0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deduplicate_unified(
            df_input=df,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "out"),
        )
    mezcla = [w for w in caught if "mezcla de regímenes" in str(w.message)]
    assert not mezcla, "No debió advertir: dataset homogéneo CON_NIT"


def test_sin_warning_solo_sin_nit(tmp_path):
    """Dataset 100% SIN_NIT no debe advertir (homogéneo, aunque sea SIN_NIT)."""
    df = _df_mixto(0, 50)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deduplicate_unified(
            df_input=df,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "out"),
        )
    mezcla = [w for w in caught if "mezcla de regímenes" in str(w.message)]
    assert not mezcla, "No debió advertir: dataset homogéneo SIN_NIT"


def test_sin_warning_pocos_sin_nit(tmp_path):
    """Una fracción mínima de NIT vacío (<5%) no dispara la advertencia
    (ruido, no mezcla de regímenes)."""
    df = _df_mixto(98, 2)  # 2% sin NIT
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deduplicate_unified(
            df_input=df,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "out"),
        )
    mezcla = [w for w in caught if "mezcla de regímenes" in str(w.message)]
    assert not mezcla, "2% de NIT vacío es ruido, no mezcla de regímenes"
