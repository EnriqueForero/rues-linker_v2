"""recall_bloqueo.py — recall de pares del bloqueo (L2) contra una verdad de grupos, por régimen.

Mide cuántos pares verdaderos (mismo grupo) quedaron entre los candidatos de ``candidates.db``: es el techo del recall
que ningún umbral posterior puede levantar. Fase 2 de la ruta: la compuerta del generador híbrido frente al LSH.

Uso:
    python scripts/recall_bloqueo.py --trabajo /tmp/banco_trabajo            # usa L1_prep/data.parquet y L2_*/candidates.db
    python scripts/recall_bloqueo.py --candidatos cand.db --datos L1.parquet --col-grupo ID_GROUP --col-regimen REGIMEN
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd


def pares_verdaderos(
    grupos: np.ndarray, *, max_grupo: int = 2000
) -> dict[str, set[tuple[int, int]]]:
    """Pares (i<j) del mismo grupo; el grupo mayor que ``max_grupo`` se omite (sería cuadrático)."""
    orden = np.argsort(grupos, kind="stable")
    g = grupos[orden]
    cortes = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    salida: set[tuple[int, int]] = set()
    for a, b in pairwise(cortes):
        m = np.sort(orden[a:b])
        if 1 < len(m) <= max_grupo:
            ii, jj = np.triu_indices(len(m), 1)
            salida.update(zip(m[ii].tolist(), m[jj].tolist(), strict=True))
    return {"TODOS": salida}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--trabajo",
        type=Path,
        help="carpeta de trabajo de una corrida (L1_prep, L2_lsh_candidates)",
    )
    p.add_argument("--candidatos", type=Path, help="candidates.db (si no se da --trabajo)")
    p.add_argument(
        "--datos",
        type=Path,
        help="parquet/csv de L1 con el grupo verdadero (si no se da --trabajo)",
    )
    p.add_argument("--col-grupo", default="ID_GROUP")
    p.add_argument(
        "--col-regimen",
        default="REGIMEN",
        help="columna de régimen por registro ('' = sin desglose)",
    )
    p.add_argument("--salida", type=Path, help="JSON con las cifras")
    args = p.parse_args(argv)
    candidatos = args.candidatos or (args.trabajo / "L2_lsh_candidates" / "candidates.db")
    datos = args.datos or (args.trabajo / "L1_prep" / "data.parquet")
    d = pd.read_parquet(datos) if datos.suffix == ".parquet" else pd.read_csv(datos, dtype=str)
    if args.col_grupo not in d.columns:
        raise SystemExit(
            f"{datos} no trae la columna {args.col_grupo!r}; columnas: {list(d.columns)}"
        )
    con = sqlite3.connect(str(candidatos))
    pares = pd.read_sql("SELECT idx_0, idx_1 FROM candidate_pairs", con)
    con.close()
    lo = np.minimum(pares["idx_0"].to_numpy(), pares["idx_1"].to_numpy())
    hi = np.maximum(pares["idx_0"].to_numpy(), pares["idx_1"].to_numpy())
    cand = set(zip(lo.tolist(), hi.tolist(), strict=True))
    verdad = pares_verdaderos(d[args.col_grupo].astype(str).to_numpy())["TODOS"]
    n = len(d)
    cifras = {
        "registros": n,
        "candidatos": len(cand),
        "candidatos_por_registro": round(len(cand) / n, 2),
        "pares_verdaderos": len(verdad),
        "recall_pares": round(len(verdad & cand) / max(1, len(verdad)), 4),
        "por_regimen": {},
    }
    if args.col_regimen and args.col_regimen in d.columns:
        reg = d[args.col_regimen].astype(str).to_numpy()
        for r in sorted(set(reg)):
            v = {q for q in verdad if reg[q[0]] == r and reg[q[1]] == r}
            if v:
                cifras["por_regimen"][r] = {
                    "pares_verdaderos": len(v),
                    "recall_pares": round(len(v & cand) / len(v), 4),
                }
    print(json.dumps(cifras, ensure_ascii=False, indent=2))
    if args.salida:
        args.salida.write_text(json.dumps(cifras, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
