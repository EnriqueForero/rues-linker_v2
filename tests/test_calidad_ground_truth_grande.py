"""Test de regresión de calidad sobre el ground truth grande (régimen CON_NIT).

Congela la línea base medida en la Fase 1 (v2.14.0) para detectar regresiones
de calidad en versiones futuras. Solo se congela CON_NIT porque es estable y
confiable; SIN_NIT está pendiente de recalibración (ver docs/FASE1_LINEA_BASE.md)
y sus números aún no son una referencia válida.

El dataset es sintético y determinista (seed=42). Si cambia el generador, estos
umbrales deben revisarse.

Marcado como `slow`: usa una submuestra para mantener el tiempo razonable en CI.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

# El ground truth grande vive versionado en tests/data/ (ver excepción del
# .gitignore). Antes se leía de data/ground_truth/, que NO se versiona, por lo
# que el test se saltaba en CI; ahora corre siempre.
GT_PATH = Path(__file__).resolve().parents[1] / "tests" / "data" / "ground_truth_grande.csv"
if not GT_PATH.exists():  # respaldo: ubicación legada no versionada
    GT_PATH = (
        Path(__file__).resolve().parents[1] / "data" / "ground_truth" / "ground_truth_grande.csv"
    )

# Cotas inferiores de la línea base medida en v2.14.0 (con margen de holgura
# para no romper por variación menor). Medido: F1 0.976 sobre submuestra CON_NIT.
F1_MIN_CON_NIT = 0.92
PRECISION_MIN_CON_NIT = 0.95


@pytest.mark.skipif(not GT_PATH.exists(), reason="ground truth grande no presente")
def test_calidad_con_nit_no_regresa(tmp_path):
    """El F1 del régimen CON_NIT no debe caer por debajo de la cota base."""
    df = pd.read_csv(GT_PATH, dtype=str)
    con = df[df["REGIMEN"] == "CON_NIT"].copy()

    # Submuestra de grupos COMPLETOS (no romper pares) para acotar el tiempo.
    grupos = con["ID_GROUP"].drop_duplicates().sample(400, random_state=42)
    con = con[con["ID_GROUP"].isin(grupos)].reset_index(drop=True)
    con["NIT"] = con["NIT"].fillna("")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_unified(
            df_input=con.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="BALANCEADO",
            profile="deduplication_standard",
            output_dir=str(tmp_path),
        )
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    res = evaluar_pares(con["ID_GROUP"].to_numpy(), corr["ID_GRUPO"].to_numpy())

    assert res.f1 >= F1_MIN_CON_NIT, f"F1 CON_NIT regresó: {res.f1:.3f} < {F1_MIN_CON_NIT}"
    assert res.precision >= PRECISION_MIN_CON_NIT, (
        f"Precision CON_NIT regresó: {res.precision:.3f} < {PRECISION_MIN_CON_NIT}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
