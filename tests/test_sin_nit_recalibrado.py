"""Test de no-regresión del perfil SIN_NIT recalibrado (v0.7.4).

Congela la mejora: el perfil `deduplication_sin_nit_conservador` recalibrado
contra ground truth da F1=0.645 (P=0.907) sobre los 2342 registros SIN_NIT,
frente al baseline previo de F1=0.217 (sobre-fusión con deduplicate_unified
estándar).

Si este test falla, alguien degradó el perfil SIN_NIT. La mejora es un límite
de DATOS, no de calibración (ningún umbral supera F1~0.65 sin destruir
precision — ver docs/DEUDA_SIN_NIT.md).

Marcado `slow` (corre el pipeline sobre 2342 filas, ~10s).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

REPO_ROOT = Path(__file__).resolve().parents[1]
GT_PATH = REPO_ROOT / "tests" / "data" / "ground_truth_grande.csv"

# Cotas con holgura sobre lo medido (P=0.907, R=0.501, F1=0.645).
# Margen: -0.03 para absorber jitter sin enmascarar una degradación real.
F1_MIN = 0.61
PRECISION_MIN = 0.87
RECALL_MIN = 0.47


@pytest.fixture(scope="module")
def sin_nit_predictions(tmp_path_factory):
    if not GT_PATH.exists():
        pytest.skip(f"GT no encontrado en {GT_PATH}")
    gt = pd.read_csv(GT_PATH, dtype=str)
    gt["NIT"] = gt["NIT"].fillna("")
    sin = gt[gt["REGIMEN"] == "SIN_NIT"].copy().reset_index(drop=True)

    out = tmp_path_factory.mktemp("sin_nit")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_unified(
            df_input=sin.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="AGRESIVO",
            profile="deduplication_sin_nit_conservador",
            output_dir=str(out),
        )
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return sin, corr


@pytest.mark.slow
def test_sin_nit_perfil_recalibrado_no_regresa(sin_nit_predictions):
    """El perfil SIN_NIT debe mantener F1>=0.61, P>=0.87, R>=0.47."""
    sin, corr = sin_nit_predictions
    res = evaluar_pares(
        sin["ID_GROUP"].astype(str).to_numpy(),
        corr["ID_GRUPO"].astype(str).to_numpy(),
    )
    fails = []
    if res.f1 < F1_MIN:
        fails.append(f"F1={res.f1:.3f} < {F1_MIN}")
    if res.precision < PRECISION_MIN:
        fails.append(f"Precision={res.precision:.3f} < {PRECISION_MIN}")
    if res.recall < RECALL_MIN:
        fails.append(f"Recall={res.recall:.3f} < {RECALL_MIN}")
    assert not fails, (
        "Regresión en perfil SIN_NIT:\n  "
        + "\n  ".join(fails)
        + f"\n  (TP={res.tp} FP={res.fp} FN={res.fn})"
        + "\n  El perfil deduplication_sin_nit_conservador fue degradado. "
        "Ver docs/DEUDA_SIN_NIT.md."
    )


@pytest.mark.slow
def test_sin_nit_mejora_sobre_baseline(sin_nit_predictions):
    """Confirma que el perfil supera holgadamente el baseline previo (0.217)."""
    sin, corr = sin_nit_predictions
    res = evaluar_pares(
        sin["ID_GROUP"].astype(str).to_numpy(),
        corr["ID_GRUPO"].astype(str).to_numpy(),
    )
    assert res.f1 > 0.40, (
        f"F1={res.f1:.3f} no supera el baseline previo (0.217) con margen. "
        "La recalibración del Sprint v0.7.4 se perdió."
    )


def test_perfil_sin_nit_tiene_umbral_calibrado():
    """Verificación estática (rápida): el perfil tiene el umbral 0.78 calibrado."""
    from record_linkage.pipeline._internal import DEDUPLICATION_PROFILES

    p = DEDUPLICATION_PROFILES["deduplication_sin_nit_conservador"]
    assert p["min_name_similarity"] == 0.78, (
        f"min_name_similarity={p['min_name_similarity']}, esperado 0.78 "
        "(óptimo F1 medido en v0.7.4)"
    )
    assert p["score_threshold"] == 0.78
