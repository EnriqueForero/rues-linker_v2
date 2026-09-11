"""Replicación de resultados v2.6.0 — P0-1 Pasos 1.1, 1.2 y 1.3.

Mide:
  - Paso 1.1 (bloqueo NIT, v2.5.0): efecto en F1/P/R sobre 3 datasets.
  - Paso 1.2 (bloqueo nombre, v2.6.0): A/B con/sin bloqueo de nombre.
  - Paso 1.3 (re-barrido de umbrales): barrido de score_threshold sobre los
    dos golden, mostrando que el umbral actual (0.68) es óptimo en promedio.

Uso en Colab:
    !pip install rues_linker-2.6.0-py3-none-any.whl
    !python scripts/replicar_v2_6_0.py

Uso local:
    python scripts/replicar_v2_6_0.py
"""

from __future__ import annotations

import copy
import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "src" / "record_linkage").exists():
    sys.path.insert(0, str(ROOT / "src"))

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares
from record_linkage.pipeline import _internal as _pi

# Snapshot de los perfiles originales para no contaminar entre corridas.
_ORIGINAL_PROFILES = copy.deepcopy(_pi.DEDUPLICATION_PROFILES)


def _correr(
    df: pd.DataFrame,
    *,
    enable_nit_blocking: bool = True,
    enable_name_blocking: bool = False,
    score_threshold: float | None = None,
) -> pd.DataFrame:
    """Corre el pipeline con flags y umbral configurables."""
    _pi.DEDUPLICATION_PROFILES.clear()
    _pi.DEDUPLICATION_PROFILES.update(copy.deepcopy(_ORIGINAL_PROFILES))
    for _name, prof in _pi.DEDUPLICATION_PROFILES.items():
        prof["enable_nit_blocking"] = enable_nit_blocking
        prof["enable_name_blocking"] = enable_name_blocking
        if score_threshold is not None:
            prof["score_threshold"] = score_threshold

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


def _seccion_p0_1_paso_1_1(datasets: list[tuple[Path, str]]) -> None:
    """Paso 1.1 — bloqueo NIT base."""
    print("\n" + "=" * 78)
    print("  P0-1 Paso 1.1 — BLOQUEO POR NIT BASE (v2.5.0)")
    print("=" * 78)
    for csv, etiqueta in datasets:
        if not csv.exists():
            print(f"  [SKIP] {csv.name}")
            continue
        df = pd.read_csv(csv, dtype={"NIT": str})
        df["NIT"] = df["NIT"].fillna("")
        print(f"\n  {etiqueta}")
        print(f"  Registros: {len(df)} · Grupos verdad: {df['ID_GROUP'].nunique()}")
        print(f"  {'Configuración':<28}{'F1':>8}{'Precision':>12}{'Recall':>10}")
        print(f"  {'-' * 60}")
        pred_off = _correr(df, enable_nit_blocking=False)
        m_off = evaluar_pares(df["ID_GROUP"].to_numpy(), pred_off["ID_GRUPO"].to_numpy())
        pred_on = _correr(df, enable_nit_blocking=True)
        m_on = evaluar_pares(df["ID_GROUP"].to_numpy(), pred_on["ID_GRUPO"].to_numpy())
        print(
            f"  {'SIN bloqueo NIT (v2.4.0)':<28}"
            f"{m_off.f1:>8.3f}{m_off.precision:>12.3f}{m_off.recall:>10.3f}"
        )
        print(
            f"  {'CON bloqueo NIT (v2.5.0)':<28}"
            f"{m_on.f1:>8.3f}{m_on.precision:>12.3f}{m_on.recall:>10.3f}"
        )
        print(
            f"  {'Δ':<28}{m_on.f1 - m_off.f1:>+8.3f}"
            f"{m_on.precision - m_off.precision:>+12.3f}"
            f"{m_on.recall - m_off.recall:>+10.3f}"
        )


def _seccion_p0_1_paso_1_2(datasets: list[tuple[Path, str]]) -> None:
    """Paso 1.2 — bloqueo multi-pasada por nombre (A/B)."""
    print("\n" + "=" * 78)
    print("  P0-1 Paso 1.2 — BLOQUEO MULTI-PASADA POR NOMBRE (v2.6.0)")
    print("  Default: OFF. El módulo está implementado y testeado, pero no")
    print("  mueve métricas en estos datasets (ver MIGRATION_LOG §16).")
    print("=" * 78)
    for csv, etiqueta in datasets:
        if not csv.exists():
            continue
        df = pd.read_csv(csv, dtype={"NIT": str})
        df["NIT"] = df["NIT"].fillna("")
        print(f"\n  {etiqueta}")
        print(f"  {'Configuración':<32}{'F1':>8}{'Precision':>12}{'Recall':>10}")
        print(f"  {'-' * 62}")
        pred_off = _correr(df, enable_nit_blocking=True, enable_name_blocking=False)
        m_off = evaluar_pares(df["ID_GROUP"].to_numpy(), pred_off["ID_GRUPO"].to_numpy())
        pred_on = _correr(df, enable_nit_blocking=True, enable_name_blocking=True)
        m_on = evaluar_pares(df["ID_GROUP"].to_numpy(), pred_on["ID_GRUPO"].to_numpy())
        print(
            f"  {'Solo bloqueo NIT (v2.5.0)':<32}"
            f"{m_off.f1:>8.3f}{m_off.precision:>12.3f}{m_off.recall:>10.3f}"
        )
        print(
            f"  {'NIT + nombre (v2.6.0 opt-in)':<32}"
            f"{m_on.f1:>8.3f}{m_on.precision:>12.3f}{m_on.recall:>10.3f}"
        )
        print(
            f"  {'Δ':<32}{m_on.f1 - m_off.f1:>+8.3f}"
            f"{m_on.precision - m_off.precision:>+12.3f}"
            f"{m_on.recall - m_off.recall:>+10.3f}"
        )


def _seccion_p0_1_paso_1_3(datasets: list[tuple[Path, str]]) -> None:
    """Paso 1.3 — barrido de score_threshold."""
    print("\n" + "=" * 78)
    print("  P0-1 Paso 1.3 — BARRIDO DE score_threshold (v2.6.0)")
    print("  Conclusión: thr=0.68 (default actual) es ÓPTIMO en promedio")
    print("  sobre los dos golden. Subirlo favorece al exhaustivo pero")
    print("  degrada al de 269. NO se aplica cambio global.")
    print("=" * 78)
    valid = [
        (csv, lbl)
        for csv, lbl in datasets
        if (csv.exists() and "exhaustivo" in str(csv)) or "golden_truth.csv" in str(csv)
    ]
    valid = [(csv, lbl) for csv, lbl in datasets if csv.exists()]
    if len(valid) < 2:
        print("  [SKIP] Se necesitan al menos los dos golden para el barrido.")
        return

    thresholds = [0.65, 0.68, 0.70, 0.72, 0.75]
    print(f"\n  {'thr':>5}", end="")
    for _, lbl in valid:
        print(f"  {lbl[:24]:<24}", end="")
    print()
    print(f"  {'-' * (5 + 26 * len(valid))}")
    for thr in thresholds:
        print(f"  {thr:>5.2f}", end="")
        for csv, _ in valid:
            df = pd.read_csv(csv, dtype={"NIT": str})
            df["NIT"] = df["NIT"].fillna("")
            pred = _correr(df, score_threshold=thr)
            m = evaluar_pares(df["ID_GROUP"].to_numpy(), pred["ID_GRUPO"].to_numpy())
            print(f"  F1={m.f1:.3f} P={m.precision:.3f} R={m.recall:.3f}    ", end="")
        print()


def main() -> None:
    print("\n" + "=" * 78)
    print("  REPLICACIÓN DE RESULTADOS — rues-linker v2.6.0")
    print("  P0-1 Pasos 1.1, 1.2 y 1.3")
    print("=" * 78)

    base = Path(__file__).resolve().parent.parent / "tests"
    datasets = [
        (base / "data_sintetica" / "dataset_sintetico_p0_1.csv", "Sintético P0-1 (24)"),
        (base / "data" / "golden_truth.csv", "Golden 269"),
        (base / "data" / "golden_truth_exhaustivo.csv", "Golden exhaustivo 1456"),
    ]

    _seccion_p0_1_paso_1_1(datasets)
    _seccion_p0_1_paso_1_2(datasets)
    _seccion_p0_1_paso_1_3(datasets)

    print("\n" + "=" * 78)
    print("  REPLICACIÓN COMPLETA")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
