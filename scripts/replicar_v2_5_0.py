"""Script de replicación de resultados v2.5.0 (P0-1 Paso 1.1).

Ejecuta los benchmarks de calidad sobre el dataset sintético y los dos
ground truths (golden 269 + exhaustivo 1456), comparando v2.4.0 vs v2.5.0.

Uso en Colab:
    !pip install rues_linker-2.5.0-py3-none-any.whl
    !python scripts/replicar_v2_5_0.py

Uso local (desde el repo):
    python scripts/replicar_v2_5_0.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd

# Permitir ejecutar el script desde la raíz del repo sin instalar.
ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "src" / "record_linkage").exists():
    sys.path.insert(0, str(ROOT / "src"))

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares
from record_linkage.pipeline import _internal as _pi


def _correr_pipeline(df: pd.DataFrame, enable_nit_blocking: bool) -> pd.DataFrame:
    """Corre el pipeline con/sin bloqueo NIT, silenciando logs."""
    for _name, prof in _pi.DEDUPLICATION_PROFILES.items():
        prof["enable_nit_blocking"] = enable_nit_blocking

    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                correlativa, _ = deduplicate_unified(
                    df_input=df[["NIT", "RAZON_SOCIAL"]].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                )
    finally:
        logging.disable(logging.NOTSET)
    return correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)


def _evaluar(truth_csv: Path, etiqueta: str) -> None:
    """Compara el pipeline con y sin bloqueo NIT sobre un ground truth."""
    df = pd.read_csv(truth_csv, dtype={"NIT": str})
    df["NIT"] = df["NIT"].fillna("")
    print(f"\n{'=' * 70}")
    print(f"  {etiqueta}")
    print(f"  Archivo: {truth_csv.name}")
    print(f"  Registros: {len(df)} · Grupos verdad: {df['ID_GROUP'].nunique()}")
    print(f"{'=' * 70}")

    # SIN bloqueo NIT
    pred = _correr_pipeline(df, enable_nit_blocking=False)
    m_off = evaluar_pares(df["ID_GROUP"].to_numpy(), pred["ID_GRUPO"].to_numpy())

    # CON bloqueo NIT
    pred = _correr_pipeline(df, enable_nit_blocking=True)
    m_on = evaluar_pares(df["ID_GROUP"].to_numpy(), pred["ID_GRUPO"].to_numpy())

    print(f"\n  {'Configuración':<28}{'F1':>8}{'Precision':>12}{'Recall':>10}{'Grupos':>10}")
    print(f"  {'-' * 68}")
    print(
        f"  {'SIN bloqueo NIT (v2.4.0)':<28}"
        f"{m_off.f1:>8.3f}{m_off.precision:>12.3f}{m_off.recall:>10.3f}"
        f"{m_off.n_pred_groups:>10}"
    )
    print(
        f"  {'CON bloqueo NIT (v2.5.0)':<28}"
        f"{m_on.f1:>8.3f}{m_on.precision:>12.3f}{m_on.recall:>10.3f}"
        f"{m_on.n_pred_groups:>10}"
    )
    delta_f1 = m_on.f1 - m_off.f1
    delta_r = m_on.recall - m_off.recall
    delta_p = m_on.precision - m_off.precision
    print(f"  {'Δ':<28}{delta_f1:>+8.3f}{delta_p:>+12.3f}{delta_r:>+10.3f}")


def main() -> None:
    """Replica las mediciones de calidad de v2.5.0 sobre los datasets disponibles."""
    print("\n" + "=" * 70)
    print("  REPLICACIÓN DE RESULTADOS — rues-linker v2.5.0")
    print("  P0-1 Paso 1.1: Bloqueo por NIT base")
    print("=" * 70)

    base = Path(__file__).resolve().parent.parent / "tests"

    datasets = [
        (
            base / "data_sintetica" / "dataset_sintetico_p0_1.csv",
            "Dataset sintético P0-1 (24 regs, 9 grupos)",
        ),
        (base / "data" / "golden_truth.csv", "Golden 269 registros (84 grupos)"),
        (
            base / "data" / "golden_truth_exhaustivo.csv",
            "Golden EXHAUSTIVO (1456 regs, 137 grupos)",
        ),
    ]

    for csv, etiqueta in datasets:
        if csv.exists():
            _evaluar(csv, etiqueta)
        else:
            print(f"\n[SKIP] {csv} no existe.")

    print("\n" + "=" * 70)
    print("  REPLICACIÓN COMPLETA")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
