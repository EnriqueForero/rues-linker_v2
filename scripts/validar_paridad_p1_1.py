"""Validador de paridad P1-1: compara scorer actual contra oráculo capturado."""

from __future__ import annotations

import logging
import os
import pickle
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.engine.scorer import VectorizedScorer

# Resolver ruta relativa al repo para ejecutar desde cualquier cwd.
_REPO = Path(__file__).resolve().parent.parent
ORACULO = _REPO / "tests" / "data" / "oraculo_scorer_p1_1.pkl"
TOL = 1e-9


def main() -> int:
    if not ORACULO.exists():
        print(f"❌ Oráculo no encontrado: {ORACULO}")
        print("   Genéralo con: python scripts/capturar_oraculo_p1_1.py")
        return 2
    try:
        with ORACULO.open("rb") as f:
            payload = pickle.load(f)
    except (TypeError, AttributeError, ModuleNotFoundError, ImportError) as e:
        # Pickle incompatible con la versión actual de pandas/dependencias.
        print(f"❌ No se pudo cargar el oráculo: {type(e).__name__}: {e}")
        print(f"   Pandas instalado: {pd.__version__}")
        print("   El oráculo fue generado con otra versión cuyos dtypes/clases")
        print("   no son compatibles. Regenera ejecutando:")
        print("       python scripts/capturar_oraculo_p1_1.py")
        return 2
    df = payload["df"]
    pairs = payload["pairs"]
    profiles = payload["profiles"]
    capturas = payload["capturas"]

    print(f"Comparando contra oráculo: {len(pairs)} pares x {len(profiles)} perfiles")
    print(f"Tolerancia: {TOL}")
    print()

    n_diff_total = 0
    for name, prof in profiles.items():
        logging.disable(logging.CRITICAL)
        try:
            with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
                sc = VectorizedScorer(prof)
                res_new = sc._score_batch_vectorized(pairs.copy(), df.copy())
        finally:
            logging.disable(logging.NOTSET)
        res_new = res_new.sort_values(["idx_0", "idx_1"]).reset_index(drop=True)
        res_old = capturas[name]

        if len(res_new) != len(res_old):
            print(f"  ❌ {name}: distinto número de pares ({len(res_new)} vs {len(res_old)})")
            n_diff_total += abs(len(res_new) - len(res_old))
            continue

        n_diff = 0
        for col in ["idx_0", "idx_1", "score", "name_sim", "nit_dist"]:
            if col not in res_new.columns or col not in res_old.columns:
                print(
                    f"  ❌ {name}: columna {col} faltante (new={col in res_new.columns} "
                    f"old={col in res_old.columns})"
                )
                n_diff += 1
                continue
            new_vals = res_new[col].to_numpy()
            old_vals = res_old[col].to_numpy()
            if col in ("idx_0", "idx_1", "nit_dist"):
                if not np.array_equal(new_vals, old_vals):
                    n_diff += int((new_vals != old_vals).sum())
                    print(f"  ❌ {name}.{col}: {n_diff} diferencias")
            else:
                diffs = np.abs(new_vals - old_vals)
                n = int((diffs > TOL).sum())
                if n > 0:
                    n_diff += n
                    idx_bad = np.where(diffs > TOL)[0][:5]
                    print(
                        f"  ❌ {name}.{col}: {n} valores fuera de tolerancia. "
                        f"Ejemplos (idx_local, new, old): "
                        + ", ".join(f"({i}, {new_vals[i]:.6f}, {old_vals[i]:.6f})" for i in idx_bad)
                    )

        if n_diff == 0:
            print(f"  ✅ {name}: paridad bit-a-bit ({len(res_new)} pares)")
        else:
            n_diff_total += n_diff
            print(f"  ❌ {name}: {n_diff} valores divergen")
    print()
    if n_diff_total == 0:
        print("✅ PARIDAD COMPLETA — la versión vectorizada reproduce el oráculo exactamente.")
        return 0
    else:
        print(f"❌ {n_diff_total} divergencias totales — la vectorización ROMPE PARIDAD.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
