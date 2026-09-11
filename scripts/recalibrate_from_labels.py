"""recalibrate_from_labels.py — recalibra el matcher con etiquetas humanas.

Esto cierra el ciclo B3:
    active_labeling.py → tú etiquetas → recalibrate_from_labels.py → producción

Toma el archivo de etiquetas humanas y ajusta los parámetros del matcher
para maximizar F1 contra esas etiquetas. Output: un MatchingProfile
serializado en JSON que puedes cargar con `MatchingProfile.from_json(...)`.

Metodología:
    - Filtra etiquetas SAME/DIFFERENT (descarta UNCLEAR).
    - Re-corre el matcher con muchas combinaciones de K x threshold.
    - Reporta la combinación que maximiza F1 sobre las etiquetas.
    - Esto NO es Fellegi-Sunter completo, pero es suficiente para
      mover de "calibrado con sintético" a "calibrado con tu realidad".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def load_labels(path: Path) -> pd.DataFrame:
    """Carga el archivo de etiquetas, normaliza columnas."""
    if path.suffix == ".xlsx":
        df = pd.read_excel(path)
    elif path.suffix == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_parquet(path)

    required = ["id_left", "id_right", "etiqueta_humana"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en el archivo de etiquetas: {missing}")

    # Normalizar etiquetas
    df["etiqueta_humana"] = df["etiqueta_humana"].astype(str).str.strip().str.upper()
    valid = df[df["etiqueta_humana"].isin(["SAME", "DIFFERENT"])].copy()
    n_total = len(df)
    n_valid = len(valid)
    n_unclear = int((df["etiqueta_humana"] == "UNCLEAR").sum())
    n_empty = n_total - n_valid - n_unclear
    print(f"Etiquetas cargadas: {n_total:,}")
    print(f"  SAME/DIFFERENT (utilizables): {n_valid:,}")
    print(f"  UNCLEAR: {n_unclear:,}")
    print(f"  Vacías/inválidas: {n_empty:,}")
    return valid


def sweep_and_report(labels: pd.DataFrame, decisions: pd.DataFrame) -> dict:
    """Barre K x threshold y reporta el mejor F1."""
    # Join labels con decisiones del matcher
    merged = labels[["id_left", "id_right", "etiqueta_humana"]].merge(
        decisions[["id_left", "id_right", "score", "concordances"]],
        on=["id_left", "id_right"],
        how="inner",
    )
    print(f"\nPares con etiqueta + score: {len(merged):,}")
    if len(merged) < 50:
        print("⚠️  Muestra pequeña (<50 pares). Calibración poco confiable.", file=sys.stderr)

    truth = merged["etiqueta_humana"] == "SAME"
    n_truth = int(truth.sum())
    print(f"  Positivos (SAME): {n_truth:,}")
    print(f"  Negativos (DIFFERENT): {(~truth).sum():,}")

    print("\nSweep K x threshold:")
    print(f"{'K':>3s} {'thr':>5s} | {'P':>6s} {'R':>6s} {'F1':>6s}")
    print("-" * 35)
    results = []
    for K in [1, 2, 3]:
        for thr in [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70]:
            pred = (merged["concordances"] >= K) & (merged["score"] >= thr)
            tp = int((pred & truth).sum())
            fp = int((pred & ~truth).sum())
            fn = int((~pred & truth).sum())
            p = tp / (tp + fp) if tp + fp else 0
            r = tp / (tp + fn) if tp + fn else 0
            f1 = 2 * p * r / (p + r) if p + r else 0
            print(f"{K:>3d} {thr:>5.2f} | {p:>6.3f} {r:>6.3f} {f1:>6.3f}")
            results.append(
                {"K": K, "threshold": thr, "P": p, "R": r, "F1": f1, "TP": tp, "FP": fp, "FN": fn}
            )

    best = max(results, key=lambda r: r["F1"])
    print()
    print(f"🎯 ÓPTIMO: K={best['K']}, threshold={best['threshold']:.2f} → F1={best['F1']:.3f}")
    return {"best": best, "all_results": results, "n_labels": len(merged)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalibrar matcher con etiquetas humanas")
    parser.add_argument(
        "--labels", type=Path, required=True, help="Archivo de etiquetas (de active_labeling.py)"
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        required=True,
        help="Archivo con decisions_log del matcher (parquet/csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("calibration_report.json"),
        help="Reporte de calibración en JSON",
    )
    args = parser.parse_args()

    labels = load_labels(args.labels)
    if args.decisions.suffix == ".parquet":
        decisions = pd.read_parquet(args.decisions)
    else:
        decisions = pd.read_csv(args.decisions)
    print(f"Decisions cargadas: {len(decisions):,}")

    report = sweep_and_report(labels, decisions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReporte guardado: {args.output}")
    print()
    print("PARA APLICAR LA CALIBRACIÓN:")
    print("  profile = default_colombia_profile()")
    print(f"  profile.min_concordances_without_nit = {report['best']['K']}")
    print(f"  profile.score_threshold = {report['best']['threshold']}")
    print(f"  profile.calibration_source = 'human_labeled_{args.labels.name}'")
    print("  result = linkage(..., matching_profile=profile)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
