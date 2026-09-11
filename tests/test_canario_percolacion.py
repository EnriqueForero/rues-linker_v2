"""Canario de percolación (F0.7, v0.8.0) — guardián del modo de fallo LSH.

El incidente datasketch 2.0 (CHANGELOG [0.7.6]) se manifestó como un clúster
gigante de 563 registros en SIN_NIT ANTES de que las métricas agregadas lo
explicaran: cuando el grafo de candidatos percola, el síntoma primario es el
tamaño del componente máximo. Este test lo congela como gate permanente sobre
la ruta de producción (``deduplicate_auto``): el clúster predicho más grande
por régimen no puede exceder ``K_PERCOLACION`` veces el grupo verdadero más
grande de ese régimen.

Calibración medida el 2026-07-12 (datasketch 1.10.0, GT 12.427):
  - CON_NIT: true_max=11 → límite 22; pred_max observado=18 (holgura 4).
  - SIN_NIT: true_max=9  → límite 18; pred_max observado=9  (holgura 9).
K_PERCOLACION=2 es ajustable SOLO con acta (playbook, sección 2).

Marcado ``slow`` (~150 s: una corrida completa de deduplicate_auto).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.auto import deduplicate_auto

REPO_ROOT = Path(__file__).resolve().parents[1]
GT_PATH = REPO_ROOT / "tests" / "data" / "ground_truth_grande.csv"

#: Multiplicador de percolación: clúster_predicho_max ≤ K x grupo_true_max.
#: Ajustable solo con acta (playbook §2). Calibrado 2026-07-12: pasa con
#: holgura (CON_NIT 18/22, SIN_NIT 9/18) y habría detectado el clúster de
#: 563 del incidente datasketch (563 ≫ 22).
K_PERCOLACION = 2


@pytest.mark.canario
@pytest.mark.slow
@pytest.mark.skipif(not GT_PATH.exists(), reason="ground truth grande no presente")
def test_canario_percolacion_por_regimen(tmp_path: Path) -> None:
    """El clúster máximo por régimen no excede Kx el grupo verdadero máximo."""
    df = pd.read_csv(GT_PATH, dtype=str)
    df["NIT"] = df["NIT"].fillna("")
    df = df.reset_index(drop=True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_auto(
            df_input=df.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "canario"),
        )
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    assert len(corr) == len(df), "La correlativa perdió o duplicó registros."

    for regimen in ("CON_NIT", "SIN_NIT"):
        mask = (df["REGIMEN"] == regimen).to_numpy()
        true_max = int(df.loc[mask].groupby("ID_GROUP").size().max())
        pred_max = int(corr.loc[mask, "ID_GRUPO"].value_counts().max())
        limite = K_PERCOLACION * true_max
        assert pred_max <= limite, (
            f"PERCOLACIÓN en {regimen}: clúster máximo predicho={pred_max} "
            f"excede el límite {limite} (= {K_PERCOLACION} x grupo verdadero "
            f"máximo {true_max}). Patrón del incidente datasketch 2.0 "
            f"(CHANGELOG [0.7.6]): revisar dependencias LSH y percolación "
            f"del grafo de candidatos ANTES de mirar métricas agregadas."
        )
