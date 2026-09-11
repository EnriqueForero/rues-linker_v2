"""Réplica reproducible de los números de calidad de v2.7.0.

Variables adicionales FIRMADAS (P2 Camino #1 + último ítem del ROADMAP).

Ejecuta tres comparaciones A/B (sin features vs con CIUDAD ``categorical_signed``)
sobre los datasets disponibles y reporta precision / recall / F1. Es
determinista: los mismos números se reproducen entre corridas.

Uso (desde la raíz del proyecto)::

    pip install -e ".[dev]"
    python scripts/enriquecer_ground_truth_ciudad.py   # genera el dataset grande
    python scripts/replicar_v2_7_0.py

Datasets:
    1. Sintético P2 (28 regs)  — tests/data_sintetica/dataset_sintetico_p2_extra_features.csv
    2. Exhaustivo enriquecido (1456 regs, CIUDAD sintética) —
       tests/data/golden_truth_exhaustivo_ciudad.csv (generar con el script de enriquecimiento)

ADVERTENCIA: la CIUDAD del dataset 2 es sintética y favorable al feature por
construcción (ver scripts/enriquecer_ground_truth_ciudad.py). Mide el TECHO del
beneficio, no el caso real. No sustituye un ground truth con ciudades reales.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

ROOT = Path(__file__).resolve().parent.parent
P2 = ROOT / "tests" / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"
EXHAUSTIVO_CIUDAD = ROOT / "tests" / "data" / "golden_truth_exhaustivo_ciudad.csv"

SIGNED = [{"column": "CIUDAD", "weight": 0.15, "type": "categorical_signed"}]


def _run(df: pd.DataFrame, cols: list[str], extra) -> object:
    """Corre el pipeline silenciado y evalúa contra ID_GROUP."""
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                out, _ = deduplicate_unified(
                    df_input=df[cols].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                    extra_features=extra,
                )
    finally:
        logging.disable(logging.NOTSET)
    out = out.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return evaluar_pares(df["ID_GROUP"].to_numpy(), out["ID_GRUPO"].to_numpy())


def _fila(nombre: str, m) -> str:
    return f"  {nombre:28s} P={m.precision:.3f}  R={m.recall:.3f}  F1={m.f1:.3f}  FP={m.fp}"


def main() -> None:
    print("=" * 68)
    print(" RÉPLICA v2.7.0 — Variables adicionales firmadas (categorical_signed)")
    print("=" * 68)

    # ── Dataset 1: sintético P2 ──
    df2 = pd.read_csv(P2, dtype={"NIT": str})
    cols2 = ["NIT", "RAZON_SOCIAL", "CIUDAD", "TELEFONO"]
    print(f"\n[1] Sintético P2 ({len(df2)} regs, {df2['ID_GROUP'].nunique()} grupos)")
    print(_fila("sin extra_features", _run(df2, cols2, None)))
    print(_fila("CIUDAD signed w=0.15", _run(df2, cols2, SIGNED)))

    # ── Dataset 2: exhaustivo enriquecido ──
    if EXHAUSTIVO_CIUDAD.exists():
        df1 = pd.read_csv(EXHAUSTIVO_CIUDAD, dtype={"NIT": str})
        df1["NIT"] = df1["NIT"].fillna("")
        cols1 = ["NIT", "RAZON_SOCIAL", "CIUDAD"]
        print(f"\n[2] Exhaustivo enriquecido ({len(df1)} regs, {df1['ID_GROUP'].nunique()} grupos)")
        print("    (CIUDAD sintética — mide el TECHO del beneficio, no el caso real)")
        print(_fila("sin extra_features", _run(df1, cols1, None)))
        print(_fila("CIUDAD signed w=0.15", _run(df1, cols1, SIGNED)))
    else:
        print(f"\n[2] Exhaustivo enriquecido AUSENTE: {EXHAUSTIVO_CIUDAD.name}")
        print("    Generar con: python scripts/enriquecer_ground_truth_ciudad.py")

    print("\n" + "=" * 68)
    print(" Números esperados (deterministas):")
    print("   P2:          P 0.800 → 1.000 | F1 0.421 → 0.486 | FP 2 → 0  (w=0.15)")
    print("   Exhaustivo:  P 0.886 → 0.966 | F1 0.863 → 0.896 | FP 1053 → 281  (w=0.15)")
    print("=" * 68)


if __name__ == "__main__":
    main()
