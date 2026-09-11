"""Test de calidad sobre el dataset sintético robusto (v2.9.0).

Este dataset (`tests/data/golden_truth_sintetico_robusto.csv`) está
diseñado para estresar **modos de falla conocidos** del scorer:

    - Sigla vs nombre completo (NIT idéntico, nombre disímil)
    - DV calculado vs declarado
    - Caracteres invisibles (zero-width, NBSP)
    - Token único compartido entre entidades distintas
    - Sufijos societarios confundibles entre grupos económicos
    - Phonetic key colisión sobre NITs distintos

A diferencia de `test_quality_golden.py` y `test_quality_exhaustivo.py`,
este dataset es **determinista y reproducible bit-a-bit** desde
`scripts/generar_dataset_robusto.py`. Los pisos de regresión están
calibrados a las métricas medidas con v2.9.0 — un retroceso significa
que algo se rompió.

Las cifras de referencia están en el CHANGELOG §[2.9.0] y se pueden
recalcular con:
    python scripts/generar_dataset_robusto.py --resumen
    pytest tests/test_quality_sintetico_robusto.py -s
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

CSV = Path(__file__).parent / "data" / "golden_truth_sintetico_robusto.csv"

# Pisos de regresión — medidos con v2.9.0. Margen 0.02 hacia abajo para
# tolerar fluctuaciones menores; degradaciones > 0.02 disparan falla.
F1_MIN = 0.91
PRECISION_MIN = 0.91
RECALL_MIN = 0.91


@pytest.fixture(scope="module")
def truth() -> pd.DataFrame:
    if not CSV.exists():
        pytest.skip(
            f"Dataset no encontrado: {CSV}. "
            "Regenérelo con: python scripts/generar_dataset_robusto.py"
        )
    df = pd.read_csv(CSV, dtype={"NIT": str})
    df["NIT"] = df["NIT"].fillna("")
    df["CASO_FRONTERA"] = df["CASO_FRONTERA"].fillna("")
    return df


@pytest.fixture(scope="module")
def resultado(truth):
    """Corre el pipeline una sola vez para todos los tests del módulo."""
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                corr, _ = deduplicate_unified(
                    df_input=truth[["NIT", "RAZON_SOCIAL"]].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                )
    finally:
        logging.disable(logging.NOTSET)
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return corr


def test_estructura_basica(truth) -> None:
    """El dataset existe y tiene la estructura esperada."""
    assert len(truth) > 500, "Dataset demasiado pequeño"
    assert truth["ID_GROUP"].nunique() > 100, "Pocos grupos"
    assert "CASO_FRONTERA" in truth.columns
    # Al menos un representante de cada tipo de caso frontera.
    tags = set(truth["CASO_FRONTERA"].unique())
    esperados = {
        "P1_sigla_vs_completo",
        "P2_historico_fusion",
        "P3_dv_calc_vs_decl",
        "P4_token_disimil",
        "P5_invisibles",
        "A_token_compartido",
        "B_nit_vecino",
        "C_phonetic_colision",
        "D_sufijo_confundible",
        "G_nombre_generico",
        "S_singleton",
    }
    faltantes = esperados - tags
    assert not faltantes, f"Casos frontera faltantes: {faltantes}"


def test_consistencia_ground_truth(truth) -> None:
    """Ningún NIT base aparece en dos grupos distintos (contradicción)."""

    def norm(n: str) -> str:
        return "".join(c for c in str(n) if c.isdigit())[:9]

    df = truth.copy()
    df["NIT_NORM"] = df["NIT"].apply(norm)
    nit_no_vacio = df[df["NIT_NORM"] != ""]
    multi = nit_no_vacio.groupby("NIT_NORM")["ID_GROUP"].nunique()
    contradictorios = multi[multi > 1]
    assert contradictorios.empty, (
        f"Ground truth contradictorio: NITs en múltiples grupos: {contradictorios.head().to_dict()}"
    )


def test_f1_no_retrocede(truth, resultado) -> None:
    m = evaluar_pares(truth["ID_GROUP"].to_numpy(), resultado["ID_GRUPO"].to_numpy())
    assert m.f1 >= F1_MIN, f"F1={m.f1:.4f} < piso {F1_MIN}. P={m.precision:.4f}, R={m.recall:.4f}"


def test_precision_no_retrocede(truth, resultado) -> None:
    m = evaluar_pares(truth["ID_GROUP"].to_numpy(), resultado["ID_GRUPO"].to_numpy())
    assert m.precision >= PRECISION_MIN, f"Precision={m.precision:.4f} < piso {PRECISION_MIN}"


def test_recall_no_retrocede(truth, resultado) -> None:
    m = evaluar_pares(truth["ID_GROUP"].to_numpy(), resultado["ID_GRUPO"].to_numpy())
    assert m.recall >= RECALL_MIN, f"Recall={m.recall:.4f} < piso {RECALL_MIN}"


def test_reporte_visible(truth, resultado, capsys) -> None:
    """Imprime un reporte detallado por tipo de caso frontera.

    Útil al iterar: corre `pytest -s tests/test_quality_sintetico_robusto.py::test_reporte_visible`
    para ver cómo se comporta el sistema en cada categoría.
    """
    from itertools import combinations

    m = evaluar_pares(truth["ID_GROUP"].to_numpy(), resultado["ID_GRUPO"].to_numpy())
    df = truth.copy()
    df["pred"] = resultado["ID_GRUPO"].values

    with capsys.disabled():
        print()
        print("=" * 60)
        print(f"GLOBAL: F1={m.f1:.3f}  P={m.precision:.3f}  R={m.recall:.3f}")
        print(f"        TP={m.tp}  FP={m.fp}  FN={m.fn}")
        print("=" * 60)
        print()
        print("Recall por tipo de caso positivo (intra-grupo):")
        print("-" * 60)
        tipos_pos = [
            "",
            "P1_sigla_vs_completo",
            "P2_historico_fusion",
            "P3_dv_calc_vs_decl",
            "P4_token_disimil",
            "P5_invisibles",
        ]
        for tag in tipos_pos:
            sub = df[df["CASO_FRONTERA"] == tag]
            tp = fn = 0
            for _gid, grupo in sub.groupby("ID_GROUP"):
                if len(grupo) < 2:
                    continue
                for i, j in combinations(grupo.index, 2):
                    if df.at[i, "pred"] == df.at[j, "pred"]:
                        tp += 1
                    else:
                        fn += 1
            if tp + fn > 0:
                etiqueta = tag if tag else "orgánico"
                r = tp / (tp + fn)
                print(f"  {etiqueta:<24}: TP={tp:>4}  FN={fn:>4}  R={r:.3f}")
        print()
        print("FP por tipo de caso negativo (deberían NO unirse):")
        print("-" * 60)
        tipos_neg = [
            "A_token_compartido",
            "B_nit_vecino",
            "C_phonetic_colision",
            "D_sufijo_confundible",
            "G_nombre_generico",
            "H_filial_pais",
        ]
        for tag in tipos_neg:
            sub = df[df["CASO_FRONTERA"] == tag]
            fp = correctos = 0
            for i, j in combinations(sub.index, 2):
                if df.at[i, "pred"] == df.at[j, "pred"]:
                    fp += 1
                else:
                    correctos += 1
            print(f"  {tag:<24}: {len(sub)} regs  FP={fp}  Correctos={correctos}")
