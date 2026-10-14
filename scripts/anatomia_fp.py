"""Anatomía de los pares de L3 de una corrida del banco: TP y FP por régimen, NIT y fuente de candidatos.

Uso::

    python scripts/anatomia_fp.py --trabajo /tmp/banco_lsh_base
    python scripts/anatomia_fp.py --trabajo /tmp/banco_h_base --hibrido

Lee ``L1_prep/data.parquet`` y ``L3_scoring/scored.db`` del directorio de trabajo; con ``--hibrido`` regenera los candidatos
por vía (identificador, token, TF-IDF, llave) para atribuir cada TP y FP a la vía que lo propuso. Fue el instrumento que mostró que el
100 % de los falsos positivos del banco eran pares de contención con ``name_sim = 1,0`` (Fase 2, hallazgo 3).
"""

from __future__ import annotations

import argparse
import sqlite3
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


def pares_verdad(df: pd.DataFrame, max_grupo: int = 2000) -> pd.DataFrame:
    """Todos los pares de registros que comparten ``ID_GROUP`` (grupos de 2 a ``max_grupo`` filas)."""
    pares: list[tuple[int, int]] = []
    for idx in df.groupby("ID_GROUP").indices.values():
        if 2 <= len(idx) <= max_grupo:
            pares.extend(combinations(np.sort(idx).tolist(), 2))
    return pd.DataFrame(pares, columns=["idx_0", "idx_1"])


def _claves(m: np.ndarray) -> set[tuple[int, int]]:
    m = np.sort(np.asarray(m), axis=1)
    return set(zip(m[:, 0].tolist(), m[:, 1].tolist(), strict=True))


def analizar(dir_trabajo: Path, hibrido: bool, ejemplos: int) -> None:
    """Imprime la anatomía de los pares puntuados por L3 en ``dir_trabajo``."""
    df = pd.read_parquet(dir_trabajo / "L1_prep" / "data.parquet")
    con = sqlite3.connect(dir_trabajo / "L3_scoring" / "scored.db")
    sc = pd.read_sql("select * from scored_pairs", con)
    con.close()
    grupo = df["ID_GROUP"].to_numpy()
    regimen = df["REGIMEN"].to_numpy()
    nit_valido = df["NIT_VALID"].to_numpy().astype(bool)
    sc["tp"] = grupo[sc.idx_0] == grupo[sc.idx_1]
    sc["reg"] = np.where(regimen[sc.idx_0] == regimen[sc.idx_1], regimen[sc.idx_0], "MIXTO")
    sc["nits"] = nit_valido[sc.idx_0].astype(int) + nit_valido[sc.idx_1].astype(int)
    fp = sc[~sc.tp]
    print(f"\n#### {dir_trabajo}  pares L3={len(sc):,}  TP={int(sc.tp.sum()):,}  FP={len(fp):,}")
    print("\nPor régimen y número de NIT válidos (columnas: FP, TP):")
    tabla = sc.pivot_table(
        index=["reg", "nits"], columns="tp", values="score", aggfunc="count", fill_value=0
    )
    print(tabla.to_string())
    print("\nFP: nit_dist (0 = mismo NIT, -1 = sin comparar):")
    print(fp.nit_dist.clip(upper=5).value_counts().sort_index().to_string())
    cuantiles = [0.1, 0.25, 0.5, 0.75, 0.9]
    for columna in ("name_sim", "nit_sim", "score"):
        print(
            f"FP: {columna} por cuantiles {cuantiles}:",
            np.round(fp[columna].quantile(cuantiles).to_numpy(), 3),
        )

    pv = pares_verdad(df)
    pv["reg"] = np.where(regimen[pv.idx_0] == regimen[pv.idx_1], regimen[pv.idx_0], "MIXTO")
    pv["hit"] = (
        pv.merge(sc[["idx_0", "idx_1"]], how="left", indicator=True)["_merge"].eq("both").to_numpy()
    )
    print("\nRecall de PARES en L3 por régimen:")
    print(pv.groupby("reg")["hit"].agg(["mean", "sum", "count"]).to_string())

    for _, p in fp.sort_values("score", ascending=False).head(ejemplos).iterrows():
        a, b = int(p.idx_0), int(p.idx_1)
        print(
            f"  FP score={p.score:.3f} name={p.name_sim:.2f} nit_sim={p.nit_sim:.2f} dist={int(p.nit_dist)} | "
            f"{df.NOMBRE_LIMPIO[a]!r} NIT={df.NIT_BASE[a]!r} [{regimen[a]}] vs "
            f"{df.NOMBRE_LIMPIO[b]!r} NIT={df.NIT_BASE[b]!r} [{regimen[b]}]"
        )
    if not hibrido:
        return

    from record_linkage.config.profiles import PERFILES_BASE
    from record_linkage.engine.candidatos import ConfigCandidatosHibrido, generar_candidatos_hibrido

    cfg = ConfigCandidatosHibrido.desde_perfil(PERFILES_BASE["produccion_estandar"])
    por_fuente: dict[str, set[tuple[int, int]]] = {}
    for fuente, lote in generar_candidatos_hibrido(df, cfg):
        por_fuente.setdefault(fuente, set()).update(_claves(lote))
    fp_claves = set(zip(fp.idx_0.tolist(), fp.idx_1.tolist(), strict=True))
    tp_claves = set(zip(sc.idx_0.tolist(), sc.idx_1.tolist(), strict=True)) - fp_claves
    pv_claves = set(zip(pv.idx_0.tolist(), pv.idx_1.tolist(), strict=True))
    print("\nAtribución por vía de candidatos (un par puede venir de varias):")
    for fuente, claves in por_fuente.items():
        otras = set().union(*(v for f, v in por_fuente.items() if f != fuente))
        print(
            f"  {fuente:22s} candidatos={len(claves):>9,}  TP_L3={len(claves & tp_claves):>6,}  "
            f"FP_L3={len(claves & fp_claves):>6,}  verdad_cubierta={len(claves & pv_claves):>6,}/{len(pv_claves):,}  "
            f"FP_solo_por_esta={len((claves & fp_claves) - otras):,}  TP_solo_por_esta={len((claves & tp_claves) - otras):,}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--trabajo", required=True, type=Path, help="directorio de trabajo de scripts/banco.py"
    )
    parser.add_argument(
        "--hibrido", action="store_true", help="atribuir cada par a la vía del generador híbrido"
    )
    parser.add_argument(
        "--ejemplos", type=int, default=8, help="falsos positivos de mayor puntaje a listar"
    )
    args = parser.parse_args(argv)
    analizar(args.trabajo, args.hibrido, args.ejemplos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
