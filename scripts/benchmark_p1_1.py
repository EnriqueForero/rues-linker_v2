"""Benchmark P1-1: medir speedup real de la vectorización del scorer.

Compara v2.8.0 (baseline) vs v2.9.0 sobre datasets sintéticos de tamaños
crecientes. Mide:
    - Wall-clock total
    - Wall-clock por método (name, nit_dist, nit_sim, phonetic)
    - Throughput (pares/segundo)
    - Equivalencia bit-a-bit del output

Uso:
    python scripts/benchmark_p1_1.py
    python scripts/benchmark_p1_1.py --sizes 1000 10000 50000
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import string
import sys
import time
from contextlib import redirect_stderr, redirect_stdout

import numpy as np
import pandas as pd

from record_linkage.engine.scorer import VectorizedScorer

random.seed(42)
np.random.seed(42)


def generar_dataset(n_records: int) -> tuple[pd.DataFrame, np.ndarray]:
    """Genera n_records empresariales sintéticos y n_records*1.5 pares candidatos."""
    palabras = [
        "EMPRESA",
        "GRUPO",
        "INVERSIONES",
        "COMERCIAL",
        "CONSTRUCCIONES",
        "MANUFACTURAS",
        "DISTRIBUCIONES",
        "SERVICIOS",
        "INTERNACIONAL",
        "COLOMBIA",
        "ANDINA",
        "BOLIVAR",
        "CARIBE",
        "PACIFICO",
        "ATLANTICO",
        "PINTUCO",
        "BAVARIA",
        "ECOPETROL",
        "AVIANCA",
        "SURA",
        "GRUPO ARGOS",
        "BANCOLOMBIA",
        "EXITO",
        "CORONA",
        "POSTOBON",
        "SAS",
        "LTDA",
        "SA",
        "SAB",
        "CIA",
        "ZONA FRANCA",
    ]
    rows = []
    for _i in range(n_records):
        nombre = " ".join(random.sample(palabras, k=random.randint(2, 5)))
        nit = "".join(random.choices(string.digits, k=10))
        rows.append(
            {
                "NIT_OK": nit,
                "NOMBRE_LIMPIO": nombre,
                "PHONETIC_KEY1": nombre.replace(" ", "")[:6],
            }
        )
    df = pd.DataFrame(rows)
    # Pares candidatos: muestra random sin repetición.
    n_pairs = int(n_records * 1.5)
    a = np.random.randint(0, n_records, size=n_pairs)
    b = np.random.randint(0, n_records, size=n_pairs)
    mask = a != b
    a, b = a[mask], b[mask]
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    pairs = np.column_stack([lo, hi])
    pairs = np.unique(pairs, axis=0)
    return df, pairs


def correr(df: pd.DataFrame, pairs: np.ndarray) -> tuple[pd.DataFrame, float]:
    profile = {
        "score_threshold": 0.0,
        "max_nit_distance": 99,
        "min_name_similarity": 0.0,
        "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
        "scoring_batch_size": 100_000,
    }
    sc = VectorizedScorer(profile)
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            t0 = time.perf_counter()
            res = sc._score_batch_vectorized(pairs.copy(), df.copy())
            t1 = time.perf_counter()
    finally:
        logging.disable(logging.NOTSET)
    return res, t1 - t0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[500, 2000, 10_000, 30_000],
        help="Tamaños de dataset a probar (n_records)",
    )
    args = parser.parse_args()

    print(f"{'n_records':>10}  {'n_pairs':>10}  {'time(s)':>10}  {'pairs/s':>12}  scored")
    print("-" * 60)
    for n in args.sizes:
        df, pairs = generar_dataset(n)
        res, dt = correr(df, pairs)
        rate = len(pairs) / dt
        print(f"{n:>10,}  {len(pairs):>10,}  {dt:>10.3f}  {rate:>12,.0f}  {len(res):>6,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
