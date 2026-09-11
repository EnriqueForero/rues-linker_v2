"""Tests de no-regresión del Sprint 0.9.0 — pipeline E2E vs ground truth grande.

Cubre la pieza A del sprint: harness E2E que mide el pipeline contra el GT
sintético `ground_truth_grande.csv` y compara contra los umbrales medidos
en `tests/data/baseline_v0_9_0.json`.

Cómo funciona
-------------
1. El script `scripts/medir_baseline_v0_9_0.py` se corrió UNA VEZ contra
   el GT completo (12,427 filas / 3,486 grupos) y guardó P/R/F1 por slice
   (régimen, caso, fuente) con margen de tolerancia.
2. Estos tests vuelven a correr el pipeline (en este entorno) y comparan
   contra esos umbrales. Si una métrica cae por debajo, el test falla con
   diff explícito.

Marcados como `slow` por defecto: corren la pipeline real sobre 12K filas
(~90s). Para CI rápido, ejecutar solo con `-m "not slow"`.

Regenerar baseline
------------------
Cuando se haga un cambio INTENCIONAL que altera las métricas (ej. nueva
calibración del SIN_NIT), re-correr::

    python scripts/medir_baseline_v0_9_0.py --out tests/data/baseline_v0_9_0.json

y commitear el JSON nuevo junto al cambio.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

# ─────────────────────────────────────────────────────────────────────────
# Paths estables (resueltos a partir de este archivo)
# ─────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
GT_PATH = REPO_ROOT / "tests" / "data" / "ground_truth_grande.csv"
BASELINE_PATH = REPO_ROOT / "tests" / "data" / "baseline_v0_9_0.json"


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def baseline() -> dict:
    """Carga umbrales fijados por el script de baseline."""
    if not BASELINE_PATH.exists():
        pytest.skip(
            f"Baseline no encontrado en {BASELINE_PATH}. "
            "Regenerar con: python scripts/medir_baseline_v0_9_0.py"
        )
    with BASELINE_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def gt_and_predictions(baseline: dict, tmp_path_factory) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Corre el pipeline UNA VEZ por suite — cada test slicea sobre las
    mismas predicciones (90s una sola vez en lugar de por test).

    Limpia el output_dir antes de correr para no contaminar con
    checkpoints stale (bug detectado durante la medición del baseline).
    """
    if not GT_PATH.exists():
        pytest.skip(f"GT no encontrado en {GT_PATH}")

    gt = pd.read_csv(GT_PATH, dtype=str)
    gt["NIT"] = gt["NIT"].fillna("")

    output_dir = tmp_path_factory.mktemp("baseline_e2e")
    # tmp_path_factory ya garantiza que el dir es nuevo; no hace falta rmtree.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_unified(
            df_input=gt.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode=baseline["mode"],
            profile=baseline["profile"],
            output_dir=str(output_dir),
        )

    # Contrato: el pipeline preserva ORIGINAL_INDEX como el índice de gt.
    assert len(corr) == len(gt), (
        f"deduplicate_unified devolvió {len(corr)} filas pero el GT tiene "
        f"{len(gt)}. Es el bug del checkpoint stale — revisar el output_dir."
    )
    return gt, corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)


def _slice_filter(gt: pd.DataFrame, label: str) -> pd.Index:
    """Convierte un label de baseline (`regimen=CON_NIT`, `fuente=RUES`, …)
    en los índices del gt que pertenecen al slice."""
    if label == "global":
        return gt.index
    key, value = label.split("=", 1)
    return gt[gt[key.upper()] == value].index


def _eval_slice(gt: pd.DataFrame, corr: pd.DataFrame, idx: pd.Index):
    """P/R/F1 sobre un slice. Devuelve dict con measured + counts."""
    sub_gt = gt.loc[idx]
    sub_pred = corr[corr["ORIGINAL_INDEX"].isin(idx)].sort_values("ORIGINAL_INDEX")
    res = evaluar_pares(
        sub_gt["ID_GROUP"].astype(str).to_numpy(),
        sub_pred["ID_GRUPO"].astype(str).to_numpy(),
    )
    return res


# ─────────────────────────────────────────────────────────────────────────
# Tests parametrizados — uno por slice informativo del baseline
# ─────────────────────────────────────────────────────────────────────────


def _informative_slices_from(baseline: dict) -> list[str]:
    """Lista de labels con datos suficientes (saltea negativos sin pares)."""
    return [label for label, info in baseline["slices"].items() if info.get("informative")]


# IMPORTANTE: los tests se parametrizan en COLLECT time. Para eso, el
# baseline JSON tiene que existir antes de pytest collect. Si no existe,
# la lista vendrá vacía y todos los tests se marcan como skip.
try:
    _BASELINE_FOR_PARAM = json.loads(BASELINE_PATH.read_text())
    _INFORMATIVE_SLICES = _informative_slices_from(_BASELINE_FOR_PARAM)
except (FileNotFoundError, json.JSONDecodeError):
    _BASELINE_FOR_PARAM = None
    _INFORMATIVE_SLICES = []


@pytest.mark.slow
@pytest.mark.skipif(
    not _INFORMATIVE_SLICES,
    reason="Baseline no encontrado; regenerar con scripts/medir_baseline_v0_9_0.py",
)
@pytest.mark.parametrize("slice_label", _INFORMATIVE_SLICES)
def test_no_regresion_por_slice(
    slice_label: str,
    baseline: dict,
    gt_and_predictions: tuple[pd.DataFrame, pd.DataFrame],
):
    """Cada slice del baseline debe seguir cumpliendo sus umbrales.

    Si esto falla, hay TRES posibilidades:
        1. Regresión real → revertir el cambio o documentar la nueva línea base.
        2. Cambio intencional en calibración → regenerar el baseline JSON.
        3. Variación del entorno (RNG no fijado, fuente externa cambió) →
           ampliar las tolerances en el script de baseline.
    """
    gt, corr = gt_and_predictions
    info = baseline["slices"][slice_label]
    th = info["thresholds"]

    idx = _slice_filter(gt, slice_label)
    res = _eval_slice(gt, corr, idx)

    failures = []
    if res.f1 < th["f1_min"]:
        failures.append(
            f"F1={res.f1:.3f} < {th['f1_min']:.3f} (baseline {info['measured']['f1']:.3f})"
        )
    if res.precision < th["precision_min"]:
        failures.append(
            f"Precision={res.precision:.3f} < {th['precision_min']:.3f} "
            f"(baseline {info['measured']['precision']:.3f})"
        )
    if res.recall < th["recall_min"]:
        failures.append(
            f"Recall={res.recall:.3f} < {th['recall_min']:.3f} "
            f"(baseline {info['measured']['recall']:.3f})"
        )

    assert not failures, (
        f"Regresión en slice '{slice_label}' (n={res.n_records}):\n  "
        + "\n  ".join(failures)
        + f"\n\nContext: tp={res.tp} fp={res.fp} fn={res.fn}"
        + "\n\nSi fue un cambio intencional, regenerar baseline con:\n"
        + "  python scripts/medir_baseline_v0_9_0.py --out tests/data/baseline_v0_9_0.json"
    )


# ─────────────────────────────────────────────────────────────────────────
# Tests rápidos sobre el JSON del baseline (no corren pipeline)
# ─────────────────────────────────────────────────────────────────────────


def test_baseline_json_existe_y_parsea():
    """El JSON debe estar comiteado en el repo."""
    assert BASELINE_PATH.exists(), (
        f"Baseline JSON faltante en {BASELINE_PATH}. "
        "Ejecutar `python scripts/medir_baseline_v0_9_0.py` y commitear."
    )
    data = json.loads(BASELINE_PATH.read_text())
    assert "slices" in data
    assert data["slices"], "El baseline JSON no tiene slices"


def test_baseline_tiene_slice_critico_con_nit():
    """`regimen=CON_NIT` debe estar siempre — es el régimen confiable."""
    data = json.loads(BASELINE_PATH.read_text())
    assert "regimen=CON_NIT" in data["slices"]
    con_nit = data["slices"]["regimen=CON_NIT"]
    assert con_nit.get("informative"), "CON_NIT debe ser informativo"
    # Sanity check del orden de magnitud — F1 CON_NIT histórico ≥ 0.95.
    assert con_nit["measured"]["f1"] >= 0.93, (
        f"F1 CON_NIT histórico bajó a {con_nit['measured']['f1']}. "
        "Esto NO es un test de pipeline — es validación del baseline guardado. "
        "Si el cambio es intencional, ajustar el assert."
    )


def test_baseline_documenta_problema_sin_nit():
    """`regimen=SIN_NIT` debe estar para CONGELAR (y exponer) el problema actual.

    F1 SIN_NIT está alrededor de 0.22 en v0.7.x. Esto NO es un objetivo —
    es deuda técnica conocida. El test fija el suelo para que no empeore
    hasta que se recalibre. Cuando se recalibre, este número subirá y
    actualizaremos el baseline.
    """
    data = json.loads(BASELINE_PATH.read_text())
    sin_nit = data["slices"].get("regimen=SIN_NIT")
    assert sin_nit is not None, "SIN_NIT debe estar en el baseline para trazabilidad"
    assert sin_nit.get("informative")
    # No assertamos un valor alto — solo que existe y se mide.
    measured_f1 = sin_nit["measured"]["f1"]
    assert 0.0 <= measured_f1 <= 1.0


def test_baseline_tolerance_es_razonable():
    """Las tolerancias no deben ser absurdamente grandes (sería un sello de goma)."""
    data = json.loads(BASELINE_PATH.read_text())
    tol = data["tolerance"]
    assert 0.0 < tol["f1"] <= 0.05, f"F1 tolerance excesiva: {tol['f1']}"
    assert 0.0 < tol["precision"] <= 0.05, f"Precision tolerance excesiva: {tol['precision']}"
    assert 0.0 < tol["recall"] <= 0.05, f"Recall tolerance excesiva: {tol['recall']}"
