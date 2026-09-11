"""verificar_determinismo.py — Verifica que el pipeline es determinista bit-a-bit.

Corre el pipeline N veces sobre el mismo input y verifica que produce el
mismo resultado (mismo hash de la columna ID_GRUPO). Fundamental para
auditabilidad y para detectar regresiones de aleatoriedad accidental.

USO
    python scripts/verificar_determinismo.py \\
        --input data/muestra_1k.parquet \\
        --runs 3 \\
        --col-nit NIT --col-name RAZON_SOCIAL
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import sys
import time
from pathlib import Path

import pandas as pd

# Permitir ejecución desde la raíz del repo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from record_linkage.deduplication.unified import deduplicate_unified


def _hash_columna(serie: pd.Series) -> str:
    """Hash determinista (SHA-1) de una serie. Indexada por posición."""
    h = hashlib.sha1()
    for v in serie.astype(str).tolist():
        h.update(v.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", required=True, help="CSV o Parquet de entrada")
    parser.add_argument("--runs", type=int, default=3, help="Cuántas corridas comparar")
    parser.add_argument("--col-nit", default="NIT")
    parser.add_argument("--col-name", default="RAZON_SOCIAL")
    parser.add_argument("--profile", default="deduplication_standard")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"❌ No existe: {path}", file=sys.stderr)
        return 1

    df = pd.read_csv(path, dtype=str) if path.suffix == ".csv" else pd.read_parquet(path)
    df[args.col_nit] = df[args.col_nit].fillna("")
    print(f"📥 Input: {len(df):,} registros — {path}")

    resultados = []
    for run in range(args.runs):
        buf = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            corr, _ = deduplicate_unified(
                df_input=df.copy(),
                col_nit=args.col_nit,
                col_name=args.col_name,
                mode="BALANCEADO",
                profile=args.profile,
                output_dir=f"/tmp/det_run_{run}",
            )
        elapsed = time.time() - t0
        corr_sorted = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
        h = _hash_columna(corr_sorted["ID_GRUPO"])
        resultados.append(h)
        print(f"  Run {run + 1}: hash={h[:16]}...  | {elapsed:.1f}s")

    todos_iguales = len(set(resultados)) == 1
    print()
    if todos_iguales:
        print(f"✅ DETERMINISTA: las {args.runs} corridas produjeron el mismo resultado.")
        return 0

    print(f"❌ NO DETERMINISTA: las {args.runs} corridas produjeron resultados distintos.")
    print("   Posibles causas:")
    print("   - Algún `random_state` sin fijar.")
    print("   - Orden no estable en un groupby o set().")
    print("   - Hash randomizado de Python (PYTHONHASHSEED).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
