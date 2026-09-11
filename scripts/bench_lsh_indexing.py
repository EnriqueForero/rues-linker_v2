#!/usr/bin/env python3
"""bench_lsh_indexing.py — Benchmark reproducible de la fase de indexación LSH.

Sprint 0.8.2, Tarea 2.3 — determinar empíricamente la mezcla CPU/IO de
``DiskBasedLSHEngine._index_band`` sobre el corpus REAL del usuario.

El plan asumía que la fase es CPU-bound (1.65x speedup paralelizando bandas).
Un mini-benchmark sintético sobre 200K filas dio 98.5% IO / 1.5% CPU, lo
que implicaría speedup ~1.01x. Pero el dataset sintético no captura:

    - Latencia real de Google Drive (escrituras SQLite remotas).
    - Tamaño de bucket (afecta INSERTs).
    - Contención de RAM con otros procesos del notebook.
    - Patrones de chunking del HDF5 con compresión gzip.

Este script corre el benchmark sobre el HDF5 de firmas YA GENERADO por una
corrida real previa, midiendo separadamente CPU (hash FNV) e IO (SQLite
insert + CREATE INDEX). Output: tabla CSV + interpretación textual.

Uso
---
::

    # 1) Localizar el signatures.h5 de una corrida anterior:
    python scripts/bench_lsh_indexing.py \\
        --signatures /content/drive/work/_lsh_index/signatures.h5 \\
        --out /content/bench_lsh_results.csv \\
        --bands 32

    # 2) Si solo tienes el DataFrame y quieres generar las firmas primero:
    python scripts/bench_lsh_indexing.py \\
        --df /content/df_preparado.parquet \\
        --num-perm 128 --ngram 3 \\
        --out /content/bench_lsh_results.csv

Veredicto
---------
Tras correr, el script imprime:

    VEREDICTO:
      CPU dominates    → Tarea 2.1 SÍ vale la pena (proceder con paralelización)
      IO dominates     → Tarea 2.1 NO vale la pena (parquearla en el plan)
      Mixed / unclear  → Pedir más datos (correr con --runs 3 para estabilizar)

Salida
------
- CSV con una fila por banda con: band_idx, n_records, t_hash_ms, t_io_ms,
  t_total_ms, cpu_pct, io_pct.
- Si se especifica ``--report``, también un .md humano-legible.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import h5py
import numpy as np

# Importar la función real de hashing del paquete — así medimos el código
# de producción, no una réplica.
from record_linkage.engine.lsh.disk_based import _hash_rows_stable

# ─────────────────────────────────────────────────────────────────────────
# Constantes del benchmark
# ─────────────────────────────────────────────────────────────────────────

#: Chunk size por defecto al leer signatures (espejo de DiskBasedLSHEngine).
DEFAULT_CHUNK_SIZE = 50_000

#: PRAGMAs SQLite que usa DiskBasedLSHEngine en producción. Replicarlos es
#: crítico porque la mezcla CPU/IO cambia drásticamente con journal_mode.
SQLITE_PRAGMAS = {
    "journal_mode": "DELETE",
    "synchronous": "OFF",
    "cache_size": -512000,
    "temp_store": "MEMORY",
    "mmap_size": 0,
    "page_size": 32768,
    "locking_mode": "EXCLUSIVE",
}


# ─────────────────────────────────────────────────────────────────────────
# Benchmark de una banda individual
# ─────────────────────────────────────────────────────────────────────────


def bench_band(
    signatures: h5py.Dataset,
    band_idx: int,
    rows_per_band: int,
    n_records: int,
    chunk_size: int,
    db_path: Path,
) -> dict:
    """Mide _index_band descomponiendo en CPU (hash) e IO (SQLite).

    El timing es estricto: ``time.perf_counter()`` antes/después de cada
    bloque, sin solapamientos. Cualquier setup (h5py open, sqlite connect)
    se hace FUERA del timing.

    Returns
    -------
    dict con keys: band_idx, n_records, t_hash_ms, t_io_ms,
    t_total_ms, cpu_pct, io_pct, entries.
    """
    # Recrear schema (estado limpio).
    conn = sqlite3.connect(str(db_path))
    for pragma, value in SQLITE_PRAGMAS.items():
        conn.execute(f"PRAGMA {pragma}={value}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lsh_buckets (band_id INTEGER, hash_value INTEGER, rid INTEGER)"
    )
    # Limpiar entries de bandas previas medidas en el mismo db.
    conn.execute("DELETE FROM lsh_buckets WHERE band_id = ?", (band_idx,))
    conn.commit()

    start_col = band_idx * rows_per_band
    end_col = start_col + rows_per_band

    # ─── CPU PHASE: leer slices + hashear ───────────────────────────────
    # Acumulamos hashes en memoria para separar limpiamente CPU de IO.
    # En producción esto se intercala, pero medirlo así nos da los costos
    # marginales puros — y la suma sigue siendo el costo total realista.
    t0 = time.perf_counter()
    chunks_hashes: list[tuple[np.ndarray, np.ndarray]] = []
    for start_row in range(0, n_records, chunk_size):
        end_row = min(start_row + chunk_size, n_records)
        # h5py read es IO de lectura — lo incluimos en "CPU" porque en la
        # paralelización propuesta también ocurriría en el worker, no en
        # main thread. Si quieres separarlo, se puede instrumentar más.
        band_data = signatures[start_row:end_row, start_col:end_col]
        hashes = _hash_rows_stable(band_data)
        rids = np.arange(start_row, end_row, dtype=np.int64)
        chunks_hashes.append((hashes, rids))
    t_cpu = time.perf_counter() - t0

    # ─── IO PHASE: insertar + crear índice ─────────────────────────────
    t0 = time.perf_counter()
    cur = conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    entries = 0
    for hashes, rids in chunks_hashes:
        data = [(int(band_idx), int(h), int(r)) for h, r in zip(hashes, rids, strict=False)]
        cur.executemany("INSERT INTO lsh_buckets VALUES (?, ?, ?)", data)
        entries += len(data)
    # CREATE INDEX por banda (mismo patrón que _index_band en producción).
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_band_{band_idx} "
        f"ON lsh_buckets (hash_value) WHERE band_id = {band_idx}"
    )
    conn.commit()
    t_io = time.perf_counter() - t0
    conn.close()

    total = t_cpu + t_io
    return {
        "band_idx": band_idx,
        "n_records": n_records,
        "rows_per_band": rows_per_band,
        "t_hash_ms": round(t_cpu * 1000, 2),
        "t_io_ms": round(t_io * 1000, 2),
        "t_total_ms": round(total * 1000, 2),
        "cpu_pct": round(100 * t_cpu / total, 1) if total > 0 else 0.0,
        "io_pct": round(100 * t_io / total, 1) if total > 0 else 0.0,
        "entries": entries,
    }


# ─────────────────────────────────────────────────────────────────────────
# Generación de firmas (cuando se pasa --df)
# ─────────────────────────────────────────────────────────────────────────


def generate_signatures_from_df(
    df_path: Path, num_perm: int, ngram: int, out_path: Path, chunk_size: int
) -> Path:
    """Genera signatures.h5 desde un Parquet/CSV. Solo se usa si el usuario
    no tiene un HDF5 previo a la mano."""
    import pandas as pd

    from record_linkage.engine.lsh.vectorized_minhash import VectorizedMinHasher

    print(f"[bench] Cargando DataFrame desde {df_path}…")
    if df_path.suffix == ".parquet":
        df = pd.read_parquet(df_path)
    elif df_path.suffix in (".csv", ".tsv"):
        df = pd.read_csv(df_path)
    else:
        sys.exit(f"Formato no reconocido: {df_path.suffix}")

    if "NOMBRE_LIMPIO" not in df.columns:
        sys.exit("El DataFrame debe tener la columna 'NOMBRE_LIMPIO'")

    n = len(df)
    print(f"[bench] Generando firmas para {n:,} registros (num_perm={num_perm})…")
    hasher = VectorizedMinHasher(num_perm=num_perm, ngram=ngram, seed=42)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(out_path), "w") as hf:
        ds = hf.create_dataset(
            "signatures",
            shape=(n, num_perm),
            dtype="uint64",
            chunks=(min(chunk_size, n), num_perm),
            compression="gzip",
            compression_opts=1,
        )
        hf.attrs["n_records"] = n
        hf.attrs["num_perm"] = num_perm
        hf.attrs["ngram"] = ngram

        texts = df["NOMBRE_LIMPIO"].values
        for s in range(0, n, chunk_size):
            e = min(s + chunk_size, n)
            ds[s:e] = hasher.signatures_batch(texts[s:e])
            print(f"[bench]   firmas {s:,}/{n:,}")
    print(f"[bench] Firmas guardadas en {out_path}")
    return out_path


# ─────────────────────────────────────────────────────────────────────────
# Veredicto e interpretación
# ─────────────────────────────────────────────────────────────────────────


def interpret(results: list[dict]) -> str:
    """Genera veredicto humano-legible sobre si paralelizar vale la pena."""
    avg_cpu = sum(r["cpu_pct"] for r in results) / len(results)
    avg_io = sum(r["io_pct"] for r in results) / len(results)
    total_s = sum(r["t_total_ms"] for r in results) / 1000

    if avg_cpu >= 40:
        veredicto = "CPU dominates"
        recomendacion = (
            "→ Tarea 2.1 (paralelizar bandas) SÍ vale la pena. "
            "Con N workers, speedup esperado ≈ 1 + (avg_cpu/100) x (N-1)."
        )
    elif avg_io >= 75:
        veredicto = "IO dominates"
        recomendacion = (
            "→ Tarea 2.1 NO vale la pena en este entorno. "
            "Paralelizar el hash (CPU) no acelera el IO (SQLite serial). "
            "Considera alternativas: SSD local en lugar de Google Drive, "
            "o batch INSERT más grande."
        )
    else:
        veredicto = "Mixed / unclear"
        recomendacion = (
            "→ Re-correr con --runs 3 para estabilizar mediciones. "
            "Si persiste, decisión judgment-call."
        )

    return (
        f"\n{'=' * 70}\n"
        f"VEREDICTO: {veredicto}\n"
        f"  CPU promedio: {avg_cpu:.1f}%\n"
        f"  IO  promedio: {avg_io:.1f}%\n"
        f"  Tiempo total {len(results)} bandas: {total_s:.1f}s\n"
        f"\n{recomendacion}\n"
        f"{'=' * 70}\n"
    )


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--signatures", type=Path, help="Path a signatures.h5 existente")
    g.add_argument("--df", type=Path, help="DataFrame Parquet/CSV (genera firmas)")
    p.add_argument("--num-perm", type=int, default=128, help="Permutaciones (si --df)")
    p.add_argument("--ngram", type=int, default=3, help="N-grama (si --df)")
    p.add_argument("--bands", type=int, default=32, help="Número de bandas a benchmarkear")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--out", type=Path, default=Path("bench_lsh_results.csv"))
    p.add_argument("--report", type=Path, default=None, help="Reporte .md opcional")
    args = p.parse_args()

    # 1. Conseguir signatures.h5
    if args.signatures:
        sig_path = args.signatures
        if not sig_path.exists():
            sys.exit(f"No existe: {sig_path}")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            sig_path = generate_signatures_from_df(
                args.df,
                args.num_perm,
                args.ngram,
                Path(tmp) / "signatures.h5",
                args.chunk_size,
            )

    # 2. Inspeccionar metadata del HDF5
    with h5py.File(str(sig_path), "r") as hf:
        ds = hf["signatures"]
        n_records, num_perm = ds.shape
        print(f"[bench] signatures.h5: {n_records:,} x {num_perm} (uint64)")

    if args.bands > num_perm:
        sys.exit(f"--bands={args.bands} > num_perm={num_perm}")

    rows_per_band = num_perm // args.bands

    # 3. Correr benchmark banda por banda
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        results = []
        with h5py.File(str(sig_path), "r") as hf:
            signatures = hf["signatures"]
            for b in range(args.bands):
                r = bench_band(signatures, b, rows_per_band, n_records, args.chunk_size, db_path)
                print(
                    f"[bench]   banda {b:2d}: total={r['t_total_ms']:8.1f} ms "
                    f"(CPU {r['cpu_pct']:5.1f}% / IO {r['io_pct']:5.1f}%)"
                )
                results.append(r)

    # 4. Guardar CSV
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"[bench] CSV guardado en {args.out}")

    # 5. Veredicto
    veredicto = interpret(results)
    print(veredicto)

    # 6. Reporte .md opcional
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        avg_cpu = sum(r["cpu_pct"] for r in results) / len(results)
        avg_io = sum(r["io_pct"] for r in results) / len(results)
        total_s = sum(r["t_total_ms"] for r in results) / 1000
        with args.report.open("w") as fh:
            fh.write(f"# Benchmark LSH indexing — {n_records:,} registros\n\n")
            fh.write(f"- Bandas: {args.bands}, rows_per_band: {rows_per_band}\n")
            fh.write(f"- Tiempo total simulado (32 bandas, secuencial): {total_s:.1f}s\n")
            fh.write(f"- Promedio CPU: {avg_cpu:.1f}% · IO: {avg_io:.1f}%\n\n")
            fh.write("| banda | n_records | rows | hash_ms | io_ms | total_ms | cpu% | io% |\n")
            fh.write("|---|---|---|---|---|---|---|---|\n")
            for r in results:
                fh.write(
                    f"| {r['band_idx']} | {r['n_records']:,} | {r['rows_per_band']} | "
                    f"{r['t_hash_ms']} | {r['t_io_ms']} | {r['t_total_ms']} | "
                    f"{r['cpu_pct']} | {r['io_pct']} |\n"
                )
            fh.write(veredicto)
        print(f"[bench] Reporte guardado en {args.report}")


if __name__ == "__main__":
    main()
