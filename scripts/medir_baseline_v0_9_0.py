#!/usr/bin/env python3
"""medir_baseline_v0_9_0.py — Baseline del Sprint 0.9.0 contra GT grande.

Mide el pipeline calibrado (``produccion_calibrada``) contra
``data/ground_truth/ground_truth_grande.csv`` desglosado por:

    - Régimen (CON_NIT / SIN_NIT)
    - Caso (positivo_con_nit / positivo_sin_nit / negativo_intermediario / negativo_generico)

Output: JSON con las cotas que después se congelan en
``tests/test_baseline_v0_9_0.py`` como umbrales de no-regresión.

Uso
---
::

    python scripts/medir_baseline_v0_9_0.py \\
        --gt data/ground_truth/ground_truth_grande.csv \\
        --out tests/data/baseline_v0_9_0.json \\
        --profile deduplication_standard \\
        --mode BALANCEADO

El script imprime una tabla por régimen y caso, y guarda el JSON. Los
umbrales del test se calculan con un margen de holgura (-0.02 sobre F1
medido, -0.03 sobre precision) para no romper por variación menor.

Reproducibilidad
----------------
- ``seed=42`` en todos los muestreos del propio GT (no toca el pipeline).
- Profile fijo en argumentos.
- No usa caché de pipeline (cada corrida es independiente).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import pandas as pd

# Asegurar que el paquete del repo se importa, no uno instalado.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

# ─────────────────────────────────────────────────────────────────────────
# Margen de holgura para los umbrales del test
# ─────────────────────────────────────────────────────────────────────────
# Estos valores se restan de las cifras MEDIDAS para fijar el piso del
# test de no-regresión. Holgura conservadora — el objetivo es que el test
# falle por degradación REAL, no por jitter numérico de la corrida.
F1_TOLERANCE = 0.02
PRECISION_TOLERANCE = 0.03
RECALL_TOLERANCE = 0.03


def _slice_metrics_for_subset(
    subset_df: pd.DataFrame,
    corr_df: pd.DataFrame,
    label: str,
) -> dict | None:
    """Mide P/R/F1 sobre el subset alineado con las predicciones.

    ``subset_df`` es una porción de ``gt`` (filtrada por régimen o caso).
    ``corr_df`` es la salida completa del pipeline. Se intersectan por
    ``ORIGINAL_INDEX`` (índice original del GT) — solo se evalúan filas
    que el subset incluye.
    """
    if subset_df.empty:
        return None

    # Las predicciones del pipeline conservan ORIGINAL_INDEX como pointer
    # al índice del DataFrame de entrada. Como pasamos el GT en orden,
    # ese índice coincide con la posición original del subset.
    indices = subset_df.index.to_numpy()
    pred_subset = corr_df[corr_df["ORIGINAL_INDEX"].isin(indices)].copy()
    pred_subset = pred_subset.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    if len(pred_subset) != len(subset_df):
        # Si el pipeline no devuelve todos los registros (no debería ocurrir
        # con deduplicate_unified, pero defensivo), avisamos.
        print(
            f"[baseline] WARNING: {label}: pred tiene {len(pred_subset)}/{len(subset_df)} "
            f"filas — métricas pueden ser inexactas."
        )

    true_groups = subset_df["ID_GROUP"].astype(str).to_numpy()
    pred_groups = pred_subset["ID_GRUPO"].astype(str).to_numpy()
    res = evaluar_pares(true_groups, pred_groups)
    return {
        "label": label,
        "n_records": int(res.n_records),
        "n_true_groups": int(res.n_true_groups),
        "n_pred_groups": int(res.n_pred_groups),
        "true_pairs": int(res.true_pairs),
        "pred_pairs": int(res.pred_pairs),
        "tp": int(res.tp),
        "fp": int(res.fp),
        "fn": int(res.fn),
        "precision": round(float(res.precision), 4),
        "recall": round(float(res.recall), 4),
        "f1": round(float(res.f1), 4),
    }


def _print_table(rows: list[dict]) -> None:
    """Imprime tabla legible al stdout."""
    header = (
        f"{'subset':40s} {'N':>6s} {'TP':>6s} {'FP':>6s} {'FN':>6s} {'P':>6s} {'R':>6s} {'F1':>6s}"
    )
    print(header)
    print("─" * len(header))
    for r in rows:
        if r is None:
            continue
        print(
            f"{r['label']:40s} "
            f"{r['n_records']:6d} "
            f"{r['tp']:6d} {r['fp']:6d} {r['fn']:6d} "
            f"{r['precision']:6.3f} {r['recall']:6.3f} {r['f1']:6.3f}"
        )


def _to_test_thresholds(rows: list[dict]) -> dict:
    """Convierte mediciones brutas en umbrales para el test de no-regresión.

    Para cada slice se calcula el piso aceptable:
        f1_min        = max(0, f1 - F1_TOLERANCE)
        precision_min = max(0, precision - PRECISION_TOLERANCE)
        recall_min    = max(0, recall - RECALL_TOLERANCE)

    No se ponen pisos cuando ``tp + fp + fn == 0`` (no hay pares relevantes;
    el slice no es informativo y el test debe saltárselo).
    """
    out = {}
    for r in rows:
        if r is None:
            continue
        if r["tp"] + r["fp"] + r["fn"] == 0:
            out[r["label"]] = {"informative": False, "reason": "no pairs"}
            continue
        out[r["label"]] = {
            "informative": True,
            "measured": {
                "precision": r["precision"],
                "recall": r["recall"],
                "f1": r["f1"],
            },
            "thresholds": {
                "precision_min": round(max(0.0, r["precision"] - PRECISION_TOLERANCE), 4),
                "recall_min": round(max(0.0, r["recall"] - RECALL_TOLERANCE), 4),
                "f1_min": round(max(0.0, r["f1"] - F1_TOLERANCE), 4),
            },
            "context": {
                "n_records": r["n_records"],
                "tp": r["tp"],
                "fp": r["fp"],
                "fn": r["fn"],
            },
        }
    return out


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--gt",
        type=Path,
        default=REPO_ROOT / "data" / "ground_truth" / "ground_truth_grande.csv",
        help="Path al ground truth grande",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "tests" / "data" / "baseline_v0_9_0.json",
        help="JSON con los umbrales del baseline",
    )
    p.add_argument("--profile", default="deduplication_standard")
    p.add_argument(
        "--mode", default="BALANCEADO", choices=["CONSERVADOR", "BALANCEADO", "AGRESIVO"]
    )
    p.add_argument(
        "--sample-n",
        type=int,
        default=None,
        help="Si está, submuestrea N grupos COMPLETOS (no parte pares). "
        "Útil para baseline rápido en local. Default: todo el GT.",
    )
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    if not args.gt.exists():
        sys.exit(f"❌ GT no encontrado: {args.gt}")

    print(f"[baseline] Cargando GT desde {args.gt}…")
    gt = pd.read_csv(args.gt, dtype=str)
    print(f"[baseline] GT cargado: {len(gt):,} filas, {gt['ID_GROUP'].nunique():,} grupos")
    print(f"[baseline] Fuentes:  {gt['FUENTE'].value_counts().to_dict()}")
    print(f"[baseline] Casos:    {gt['CASO'].value_counts().to_dict()}")
    print(f"[baseline] Regímenes: {gt['REGIMEN'].value_counts().to_dict()}")

    if args.sample_n is not None:
        rng = pd.Series(gt["ID_GROUP"].unique()).sample(args.sample_n, random_state=args.seed)
        gt = gt[gt["ID_GROUP"].isin(rng)].reset_index(drop=True)
        print(f"[baseline] Submuestreado a {len(gt):,} filas, {gt['ID_GROUP'].nunique():,} grupos")

    gt["NIT"] = gt["NIT"].fillna("")

    # Correr el pipeline UNA SOLA VEZ sobre todo el GT. Después se computan
    # las métricas por slice (régimen, caso, fuente) intersectando.
    # IMPORTANTE: limpiar output_dir antes — `deduplicate_unified` reusa
    # checkpoints (`intermediate_checkpoints/`, `lsh_candidates.db`,
    # `signatures.h5`) cuando existen. Sin limpiar, una corrida con
    # `--sample-n 300` deja state que contamina la corrida completa
    # posterior (síntoma observado: output trunca a 1131 registros).
    import shutil

    output_dir = "/tmp/baseline_v0_9_0"
    if Path(output_dir).exists():
        shutil.rmtree(output_dir)
    print(f"[baseline] Corriendo pipeline profile={args.profile} mode={args.mode}…")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_unified(
            df_input=gt.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode=args.mode,
            profile=args.profile,
            output_dir=output_dir,
        )
    print(f"[baseline] Pipeline OK: {len(corr):,} registros, {corr['ID_GRUPO'].nunique():,} grupos")

    # ─── Slices a medir ─────────────────────────────────────────────────
    rows = []

    # Global
    rows.append(_slice_metrics_for_subset(gt, corr, "global"))

    # Por régimen
    for regimen in gt["REGIMEN"].unique():
        if pd.isna(regimen):
            continue
        subset = gt[gt["REGIMEN"] == regimen]
        rows.append(_slice_metrics_for_subset(subset, corr, f"regimen={regimen}"))

    # Por caso
    for caso in gt["CASO"].unique():
        if pd.isna(caso):
            continue
        subset = gt[gt["CASO"] == caso]
        rows.append(_slice_metrics_for_subset(subset, corr, f"caso={caso}"))

    # Por fuente
    for fuente in sorted(gt["FUENTE"].dropna().unique()):
        subset = gt[gt["FUENTE"] == fuente]
        rows.append(_slice_metrics_for_subset(subset, corr, f"fuente={fuente}"))

    # ─── Reportar y persistir ──────────────────────────────────────────
    print()
    _print_table(rows)
    print()

    output = {
        "version": "0.7.3",
        "sprint": "0.9.0",
        "profile": args.profile,
        "mode": args.mode,
        "gt_path": str(args.gt.relative_to(REPO_ROOT))
        if args.gt.is_relative_to(REPO_ROOT)
        else str(args.gt),
        "gt_rows": len(gt),
        "gt_groups": int(gt["ID_GROUP"].nunique()),
        "tolerance": {
            "f1": F1_TOLERANCE,
            "precision": PRECISION_TOLERANCE,
            "recall": RECALL_TOLERANCE,
        },
        "slices": _to_test_thresholds(rows),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)
    print(f"[baseline] Umbrales escritos en {args.out}")


if __name__ == "__main__":
    main()
