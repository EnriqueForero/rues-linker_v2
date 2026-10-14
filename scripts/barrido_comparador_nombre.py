"""Barrido de umbral a nivel de PAR sobre los candidatos de una corrida: legado contra Soft TF-IDF y sus variantes.

Uso::

    python scripts/barrido_comparador_nombre.py --trabajo /tmp/banco_lsh_base
    python scripts/barrido_comparador_nombre.py --trabajo /tmp/banco_h_base --umbrales 0.40 0.45 0.47

Puntúa TODOS los candidatos de ``L2_lsh_candidates/candidates.db`` con cada configuración del comparador (umbral 0, sin piso de
nombre) y, para cada umbral de puntaje, da precisión y recall de pares por régimen. También lista qué pares verdaderos acepta el legado
y pierde Soft TF-IDF (y al revés), con el tipo de variación sintética de cada lado si el banco trae ``VARIACION``. Es el instrumento de
la decisión de la Fase 3 (``docs/evidencia/fase3_comparador_nombre.md``).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from anatomia_fp import pares_verdad  # scripts/ comparten carpeta

from record_linkage.config.profiles import PERFILES_BASE
from record_linkage.engine.scorer import VectorizedScorer

CONFIGURACIONES: dict[str, dict[str, Any]] = {
    "legado": {"comparador_nombre": "legado"},
    "soft 0.90": {"comparador_nombre": "soft_tfidf"},
    "soft 0.85": {"comparador_nombre": "soft_tfidf", "soft_tfidf_umbral_token": 0.85},
    "soft 0.90 + compacta 0.95": {
        "comparador_nombre": "soft_tfidf",
        "similitud_compacta_min": 0.95,
    },
    "soft 0.85 + compacta 0.93": {
        "comparador_nombre": "soft_tfidf",
        "soft_tfidf_umbral_token": 0.85,
        "similitud_compacta_min": 0.93,
    },
    "legado + compacta 0.95": {"comparador_nombre": "legado", "similitud_compacta_min": 0.95},
}


def puntuar(
    df: pd.DataFrame, candidatos: np.ndarray, ajustes: dict[str, Any], lote: int = 100_000
) -> pd.DataFrame:
    """Puntúa todos los candidatos con el perfil estándar más ``ajustes``, sin umbral ni piso de nombre."""
    perfil = dict(PERFILES_BASE["produccion_estandar"])
    perfil.update({"score_threshold": 0.0, "min_name_similarity": 0.0})
    perfil.update(ajustes)
    scorer = VectorizedScorer(perfil)
    partes = [
        scorer._score_batch_vectorized(candidatos[a : a + lote], df)
        for a in range(0, len(candidatos), lote)
    ]
    return pd.concat(partes, ignore_index=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--trabajo", required=True, type=Path, help="directorio de trabajo de scripts/banco.py"
    )
    parser.add_argument(
        "--umbrales", type=float, nargs="+", default=[0.35, 0.40, 0.42, 0.45, 0.47, 0.50]
    )
    parser.add_argument("--ejemplos", type=int, default=12)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)

    df = pd.read_parquet(args.trabajo / "L1_prep" / "data.parquet")
    con = sqlite3.connect(args.trabajo / "L2_lsh_candidates" / "candidates.db")
    candidatos = pd.read_sql("select idx_0, idx_1 from candidate_pairs", con).to_numpy()
    con.close()
    grupo = df["ID_GROUP"].to_numpy()
    regimen = df["REGIMEN"].to_numpy()
    variacion = (
        df["VARIACION"].fillna("").to_numpy() if "VARIACION" in df.columns else np.full(len(df), "")
    )
    pv = pares_verdad(df)
    pv["reg"] = np.where(regimen[pv.idx_0] == regimen[pv.idx_1], regimen[pv.idx_0], "MIXTO")
    total = pv.groupby("reg").size()
    print(f"candidatos: {len(candidatos):,}  pares verdaderos: {total.to_dict()}")

    salidas: dict[str, pd.DataFrame] = {}
    for nombre, ajustes in CONFIGURACIONES.items():
        res = puntuar(df, candidatos, ajustes)
        res["tp"] = grupo[res.idx_0] == grupo[res.idx_1]
        res["reg"] = np.where(regimen[res.idx_0] == regimen[res.idx_1], regimen[res.idx_0], "MIXTO")
        salidas[nombre] = res
        print(f"\n{nombre}:  umbral  precisión  recall CON / SIN   FP")
        for u in args.umbrales:
            s = res[res.score >= u]
            tp, fp = int(s.tp.sum()), int((~s.tp).sum())
            r_con = s[s.tp & (s.reg == "CON_ID")].shape[0] / total.get("CON_ID", 1)
            r_sin = s[s.tp & (s.reg == "SIN_ID")].shape[0] / total.get("SIN_ID", 1)
            print(
                f"   {u:.2f}    {tp / max(tp + fp, 1):.4f}    {r_con:.4f} / {r_sin:.4f}   {fp:>7,}"
            )

    legado, soft = salidas["legado"], salidas["soft 0.85 + compacta 0.93"]
    m = legado.merge(soft, on=["idx_0", "idx_1"], suffixes=("_l", "_s"))
    umbral = 0.45

    def variaciones(sub: pd.DataFrame) -> dict[str, int]:
        a = pd.Series(np.concatenate([variacion[sub.idx_0], variacion[sub.idx_1]]))
        return a[a != ""].value_counts().head(8).to_dict()

    def muestra(sub: pd.DataFrame, titulo: str) -> None:
        print(f"\n{titulo}: {len(sub):,}; variaciones: {variaciones(sub)}")
        for _, p in sub.sample(min(args.ejemplos, len(sub)), random_state=1).iterrows():
            a, b = int(p.idx_0), int(p.idx_1)
            print(
                f"   legado={p.score_l:.2f} soft={p.score_s:.2f} name_l={p.name_sim_l:.2f} name_s={p.name_sim_s:.2f} | "
                f"{df.NOMBRE_LIMPIO[a]!r} ({variacion[a]}) vs {df.NOMBRE_LIMPIO[b]!r} ({variacion[b]})"
            )

    muestra(
        m[m.tp_l & (m.score_l >= umbral) & (m.score_s < umbral)],
        "VERDADEROS que el legado acepta y soft pierde",
    )
    muestra(m[m.tp_l & (m.score_l < umbral) & (m.score_s >= umbral)], "VERDADEROS que soft gana")
    muestra(m[~m.tp_l & (m.score_l >= umbral)], "FALSOS del legado")
    muestra(m[~m.tp_s & (m.score_s >= umbral)], "FALSOS de soft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
