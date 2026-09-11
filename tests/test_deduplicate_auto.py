"""Tests de deduplicate_auto — enrutamiento por régimen (v0.7.4).

deduplicate_auto separa CON_NIT/SIN_NIT y aplica el perfil óptimo a cada uno.
Sobre el GT mixto sube el F1 global de 0.563 (deduplicate_unified) a 0.907.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.auto import deduplicate_auto
from record_linkage.evaluation.pairwise import evaluar_pares

REPO_ROOT = Path(__file__).resolve().parents[1]
GT_PATH = REPO_ROOT / "tests" / "data" / "ground_truth_grande.csv"


def _df_con_nit(n: int) -> pd.DataFrame:
    filas = []
    # n pares idénticos (mismo NIT) → deben agruparse
    for i in range(n):
        nit = f"{900000000 + i}"
        filas.append({"NIT": nit, "RAZON_SOCIAL": f"EMPRESA {i} SAS"})
        filas.append({"NIT": nit, "RAZON_SOCIAL": f"EMPRESA {i} S.A.S."})
    return pd.DataFrame(filas)


def _df_sin_nit(n: int) -> pd.DataFrame:
    filas = []
    for i in range(n):
        filas.append({"NIT": "", "RAZON_SOCIAL": f"IMPORTADORA {i} CO LTD"})
        filas.append({"NIT": "", "RAZON_SOCIAL": f"IMPORTADORA {i} CORP"})
    return pd.DataFrame(filas)


def test_auto_homogeneo_con_nit(tmp_path):
    """Dataset 100% CON_NIT → ruta homogénea, sin separar."""
    df = _df_con_nit(10)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, stats = deduplicate_auto(df, output_dir=str(tmp_path / "o"))
    assert stats["routed"] == "homogeneo_con_nit"
    assert stats["n_sin_nit"] == 0
    assert (corr["REGIMEN_AUTO"] == "CON_NIT").all()


def test_auto_homogeneo_sin_nit(tmp_path):
    """Dataset 100% SIN_NIT → ruta homogénea con perfil conservador."""
    df = _df_sin_nit(10)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, stats = deduplicate_auto(df, output_dir=str(tmp_path / "o"))
    assert stats["routed"] == "homogeneo_sin_nit"
    assert stats["n_con_nit"] == 0
    assert (corr["REGIMEN_AUTO"] == "SIN_NIT").all()


def test_auto_mixto_separa_y_recombina(tmp_path):
    """Dataset mixto → separa, dedup cada régimen, recombina sin perder filas."""
    df = pd.concat([_df_con_nit(8), _df_sin_nit(8)], ignore_index=True)
    n_total = len(df)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, stats = deduplicate_auto(df, output_dir=str(tmp_path / "o"))
    assert stats["routed"] == "mixto"
    assert stats["n_con_nit"] == 16
    assert stats["n_sin_nit"] == 16
    # No se pierde ni se duplica ninguna fila al recombinar.
    assert len(corr) == n_total
    # Ambos regímenes presentes.
    assert set(corr["REGIMEN_AUTO"].unique()) == {"CON_NIT", "SIN_NIT"}


def test_auto_ids_globalmente_unicos(tmp_path):
    """Los IDs de grupo CON_NIT y SIN_NIT no colisionan (prefijo C/S)."""
    df = pd.concat([_df_con_nit(5), _df_sin_nit(5)], ignore_index=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_auto(df, output_dir=str(tmp_path / "o"))
    con_ids = set(corr[corr["REGIMEN_AUTO"] == "CON_NIT"]["ID_GRUPO"])
    sin_ids = set(corr[corr["REGIMEN_AUTO"] == "SIN_NIT"]["ID_GRUPO"])
    assert con_ids.isdisjoint(sin_ids), "IDs de grupo colisionan entre regímenes"
    assert all(str(x).startswith("C") for x in con_ids)
    assert all(str(x).startswith("S") for x in sin_ids)


def test_auto_preserva_original_index(tmp_path):
    """ORIGINAL_INDEX debe mapear al índice global del df de entrada."""
    df = pd.concat([_df_con_nit(5), _df_sin_nit(5)], ignore_index=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_auto(df, output_dir=str(tmp_path / "o"))
    assert set(corr["ORIGINAL_INDEX"]) == set(range(len(df)))


def test_auto_dataframe_vacio_lanza(tmp_path):
    with pytest.raises(ValueError, match="vacío"):
        deduplicate_auto(
            pd.DataFrame({"NIT": [], "RAZON_SOCIAL": []}), output_dir=str(tmp_path / "o")
        )


def test_auto_columnas_faltantes_lanza(tmp_path):
    with pytest.raises(ValueError, match="requeridas"):
        deduplicate_auto(pd.DataFrame({"otra": ["x"]}), output_dir=str(tmp_path / "o"))


@pytest.mark.slow
def test_auto_supera_baseline_en_gt_mixto(tmp_path):
    """Sobre el GT mixto completo, deduplicate_auto debe superar holgadamente
    el F1 0.563 de deduplicate_unified. Medido: F1 global 0.907."""
    if not GT_PATH.exists():
        pytest.skip(f"GT no encontrado en {GT_PATH}")
    gt = pd.read_csv(GT_PATH, dtype=str)
    gt["NIT"] = gt["NIT"].fillna("")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _stats = deduplicate_auto(
            df_input=gt.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="AGRESIVO",
            output_dir=str(tmp_path / "auto"),
        )
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    res = evaluar_pares(
        corr["ID_GROUP"].astype(str).to_numpy(),
        corr["ID_GRUPO"].astype(str).to_numpy(),
    )
    assert res.f1 >= 0.85, f"F1 global={res.f1:.3f} < 0.85 (esperado ~0.907)"
    assert res.precision >= 0.93, f"Precision={res.precision:.3f} < 0.93"
