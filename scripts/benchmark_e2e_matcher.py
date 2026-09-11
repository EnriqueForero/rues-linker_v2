"""benchmark_e2e_matcher.py — medición trazable E2E del matcher integrado.

Esto cumple el bloqueante B2 de la auditoría externa:
"Reproducir las cifras de forma trazable: un script que mida matcher-vs-scorer
sobre el clustering completo (no solo pares), con la misma metodología que la
Fase 1, para que el +F1 quede documentado y replicable."

Metodología (idéntica al smoke E2E previo):
    1. Cargar GT grande (12,427 registros, 3,486 grupos verdad).
    2. Particionar por fuente como en producción.
    3. Correr `linkage()` SIN matcher → métricas baseline.
    4. Correr `linkage()` CON matcher (profile colombia, K=2) → métricas refinadas.
    5. Comparar pairwise: TP, FP, FN, Precision, Recall, F1.
    6. Desglosar por régimen (CON_NIT, SIN_NIT).

NO usa pre-filtrado de pares. NO mide sobre subsets. Es el flujo completo
que un usuario de producción usaría.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from itertools import combinations
from pathlib import Path

import pandas as pd

# Configuración
GT_CSV = Path("/mnt/user-data/uploads/1779568140371_2026-05-23_B04_-_ground_truth_grande.csv")
WORK_BASELINE = Path("/tmp/bench_baseline")
WORK_MATCHER = Path("/tmp/bench_matcher")


def load_sources() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Carga GT y lo particiona por fuente."""
    gt = pd.read_csv(GT_CSV, dtype=str).fillna("")
    gt["ID_REGISTRO"] = gt["ID_REGISTRO"].astype(str)
    sources: dict[str, pd.DataFrame] = {}
    for fuente, sub in gt.groupby("FUENTE"):
        keep = sub[
            ["ID_REGISTRO", "NIT", "RAZON_SOCIAL", "CIUDAD", "TELEFONO", "DIRECCION", "EMAIL"]
        ].copy()
        sources[str(fuente)] = keep
    return gt, sources


def build_pairs_from_clusters(merged: pd.DataFrame, group_col: str) -> set[frozenset[str]]:
    """Genera pares intra-cluster a partir de una asignación."""
    pairs: set[frozenset[str]] = set()
    for _, sub in merged.groupby(group_col):
        ids = sub["ID_REGISTRO"].astype(str).tolist()
        if len(ids) >= 2:
            for a, b in combinations(sorted(ids), 2):
                pairs.add(frozenset((a, b)))
    return pairs


def metrics(truth: set, pred: set) -> dict[str, float]:
    tp = len(truth & pred)
    fp = len(pred - truth)
    fn = len(truth - pred)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "Precision": round(p, 4),
        "Recall": round(r, 4),
        "F1": round(f1, 4),
    }


def metrics_by_regime(gt: pd.DataFrame, truth_pairs: set, pred_pairs: set) -> dict[str, dict]:
    """Métricas separadas por régimen CON_NIT / SIN_NIT."""
    out = {}
    for reg in ["CON_NIT", "SIN_NIT"]:
        ids_reg = set(gt[gt["REGIMEN"] == reg]["ID_REGISTRO"])
        # Un par "es de este régimen" si AMBOS IDs lo son
        truth_reg = {p for p in truth_pairs if all(x in ids_reg for x in p)}
        pred_reg = {p for p in pred_pairs if all(x in ids_reg for x in p)}
        out[reg] = metrics(truth_reg, pred_reg)
    return out


def run_benchmark() -> dict:
    """Corre el benchmark completo."""
    print("=" * 80)
    print("BENCHMARK E2E: pipeline core vs pipeline core + matcher")
    print("=" * 80)
    print()

    # Setup
    gt, sources = load_sources()
    print(f"GT: {len(gt):,} registros, {gt['ID_GROUP'].nunique():,} grupos verdad")
    print(f"Fuentes: {list(sources.keys())}")
    print()

    from record_linkage import linkage

    # ── Run 1: BASELINE (sin matcher) ────────────────────────────────
    if WORK_BASELINE.exists():
        shutil.rmtree(WORK_BASELINE)
    WORK_BASELINE.mkdir(parents=True)

    print(f"[1/2] Baseline: linkage() sin matcher → {WORK_BASELINE}")
    t0 = time.time()
    result_baseline = linkage(
        sources=sources,
        col_name="RAZON_SOCIAL",
        col_nit="NIT",
        col_ciudad="CIUDAD",
        extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
        work_dir=str(WORK_BASELINE),
        profile="produccion_estandar",
    )
    t_baseline = time.time() - t0
    print(f"      Completado en {t_baseline:.1f}s")
    print(
        f"      Golden: {len(result_baseline['golden']):,} | "
        f"Correlativa: {len(result_baseline['correlative']):,} | "
        f"Clusters: {result_baseline['correlative']['ID_GRUPO'].nunique():,}"
    )
    print()

    # ── Run 2: CON MATCHER ───────────────────────────────────────────
    if WORK_MATCHER.exists():
        shutil.rmtree(WORK_MATCHER)
    WORK_MATCHER.mkdir(parents=True)

    print(f"[2/2] Con matcher: linkage(..., matching_profile='colombia') → {WORK_MATCHER}")
    print("      (default balanced: K_sin_nit=1, threshold=0.50)")
    t0 = time.time()
    result_matcher = linkage(
        sources=sources,
        col_name="RAZON_SOCIAL",
        col_nit="NIT",
        col_ciudad="CIUDAD",
        extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
        work_dir=str(WORK_MATCHER),
        profile="produccion_estandar",
        matching_profile="colombia",
        return_matcher_audit=True,
    )
    t_matcher = time.time() - t0
    print(f"      Completado en {t_matcher:.1f}s")
    print(
        f"      Golden: {len(result_matcher['golden']):,} | "
        f"Correlativa: {len(result_matcher['correlative']):,} | "
        f"Clusters: {result_matcher['correlative']['ID_GRUPO'].nunique():,}"
    )
    if "matcher_stats" in result_matcher:
        s = result_matcher["matcher_stats"]
        print(
            f"      Matcher: {s['n_pairs_evaluated']:,} pares evaluados → "
            f"{s['n_separated']:,} separados, {s['n_kept']:,} mantenidos"
        )
        print(f"      Tiempo matcher: {s['elapsed_sec']:.2f}s ({s['pairs_per_sec']:,.0f} pares/s)")
    print()

    # ── Métricas ────────────────────────────────────────────────────
    # Truth pairs (verdad)
    truth_pairs = set()
    for _, sub in gt.groupby("ID_GROUP"):
        ids = sub["ID_REGISTRO"].tolist()
        if len(ids) >= 2:
            for a, b in combinations(sorted(ids), 2):
                truth_pairs.add(frozenset((a, b)))

    # Predicted pairs (baseline y matcher)
    merged_b = gt.merge(
        result_baseline["correlative"][["ID_REGISTRO", "ID_GRUPO"]],
        on="ID_REGISTRO",
        how="left",
    ).dropna(subset=["ID_GRUPO"])
    merged_b["ID_GRUPO"] = merged_b["ID_GRUPO"].astype(int)
    pred_baseline = build_pairs_from_clusters(merged_b, "ID_GRUPO")

    merged_m = gt.merge(
        result_matcher["correlative"][["ID_REGISTRO", "ID_GRUPO"]],
        on="ID_REGISTRO",
        how="left",
    ).dropna(subset=["ID_GRUPO"])
    merged_m["ID_GRUPO"] = merged_m["ID_GRUPO"].astype(int)
    pred_matcher = build_pairs_from_clusters(merged_m, "ID_GRUPO")

    print("=" * 80)
    print("MÉTRICAS PAIRWISE (E2E real)")
    print("=" * 80)
    print()
    m_b = metrics(truth_pairs, pred_baseline)
    m_m = metrics(truth_pairs, pred_matcher)
    print(f"{'Métrica':<12s} {'Baseline':>12s} {'Matcher v3.2.0':>16s} {'Δ':>12s}")
    print("-" * 60)
    for k in ["TP", "FP", "FN", "Precision", "Recall", "F1"]:
        delta = m_m[k] - m_b[k]
        if k in ("Precision", "Recall", "F1"):
            print(f"{k:<12s} {m_b[k]:>12.4f} {m_m[k]:>16.4f} {delta:>+12.4f}")
        else:
            print(f"{k:<12s} {m_b[k]:>12,d} {m_m[k]:>16,d} {delta:>+12,d}")
    print()

    print("Desglose por régimen:")
    print("-" * 80)
    reg_b = metrics_by_regime(gt, truth_pairs, pred_baseline)
    reg_m = metrics_by_regime(gt, truth_pairs, pred_matcher)
    for reg in ["CON_NIT", "SIN_NIT"]:
        print(f"\n{reg}:")
        print(f"  {'':<12s} {'Baseline':>12s} {'Matcher v3.2.0':>16s} {'Δ':>12s}")
        for k in ["TP", "FP", "FN", "Precision", "Recall", "F1"]:
            delta = reg_m[reg][k] - reg_b[reg][k]
            if k in ("Precision", "Recall", "F1"):
                print(f"  {k:<12s} {reg_b[reg][k]:>12.4f} {reg_m[reg][k]:>16.4f} {delta:>+12.4f}")
            else:
                print(f"  {k:<12s} {reg_b[reg][k]:>12,d} {reg_m[reg][k]:>16,d} {delta:>+12,d}")

    # ── Persistir resultados ─────────────────────────────────────────
    report = {
        "metodologia": (
            "E2E sobre clustering completo. linkage() con y sin matching_profile. "
            "Truth: pares intra-grupo de ID_GROUP. Pred: pares intra-grupo de "
            "ID_GRUPO predicho."
        ),
        "dataset": {
            "file": str(GT_CSV),
            "n_registros": len(gt),
            "n_grupos_truth": gt["ID_GROUP"].nunique(),
            "n_pares_truth": len(truth_pairs),
        },
        "baseline": {
            "config": "linkage() sin matching_profile",
            "tiempo_seg": round(t_baseline, 1),
            "metricas_global": m_b,
            "metricas_por_regimen": reg_b,
        },
        "matcher": {
            "config": "linkage(..., matching_profile='colombia') con K_sin_nit=1, threshold=0.50 (default balanced v3.2.0)",
            "tiempo_seg": round(t_matcher, 1),
            "metricas_global": m_m,
            "metricas_por_regimen": reg_m,
            "matcher_stats": result_matcher.get("matcher_stats"),
        },
    }
    out_path = Path(__file__).parent.parent / "benchmark_e2e_report.json"
    with out_path.open("w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReporte JSON: {out_path}")
    return report


if __name__ == "__main__":
    report = run_benchmark()
    sys.exit(0)
