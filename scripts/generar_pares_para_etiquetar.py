"""generar_pares_para_etiquetar.py — muestreo estratificado de pares para etiquetar.

OBJETIVO
    Producir ~1000-2000 pares que el USUARIO debe etiquetar manualmente
    (MISMO_GRUPO=True/False) sobre su muestra real del RUES. Estos pares
    se eligen DE FORMA NO ALEATORIA, sino estratificada por modos de
    falla conocidos, para maximizar el valor del trabajo de etiquetado:

        1. Alta confianza: par con score >= 0.90 — verificar si el sistema
           tiene FP sistemáticos en su zona "segura".
        2. Frontera: par con 0.65 <= score <= 0.72 — la zona donde se
           juega F1; los etiquetados aquí permiten elegir threshold
           óptimo con justicia.
        3. NIT idéntico, nombre disímil: typo P4 del dataset robusto.
        4. Nombre idéntico, NIT distinto: typo G del dataset robusto.
        5. NO clusterizados pero cercanos (score 0.50-0.64): potenciales
           FN que el threshold actual descarta.

USO
    # Paso 1: correr el pipeline sobre tu muestra real (sin etiquetar)
    python scripts/generar_pares_para_etiquetar.py \\
        --input /content/drive/MyDrive/rues_muestra_50k.csv \\
        --col-nit NIT --col-name RAZON_SOCIAL \\
        --salida /content/drive/MyDrive/pares_para_etiquetar.csv \\
        --n-pares 1500

    # Paso 2: el usuario abre `pares_para_etiquetar.csv` y llena las
    # columnas MISMO_GRUPO (True/False) y CASO_FRONTERA (opcional).

    # Paso 3: usar `medir_con_ground_truth.py` con el CSV etiquetado.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.deduplication.unified import (
    _build_deduplication_config,
    _prepare_for_deduplication,
)
from record_linkage.engine.scorer import VectorizedScorer
from record_linkage.pipeline.linkage_pipeline import RecordLinkagePipeline


def correr_pipeline_y_capturar_pares(
    df: pd.DataFrame, col_nit: str, col_name: str, mode: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Corre el pipeline y devuelve (df_prepared, pares_con_score).

    `pares_con_score` es un DataFrame con TODOS los pares candidatos que
    el LSH emitió, con su score calculado (incluso los que cayeron bajo
    el threshold). Esto permite estratificar por score.
    """
    config = _build_deduplication_config("deduplication_standard", mode, len(df))
    active = config["profile"]
    # Bajar score_threshold a 0.0 para capturar TODOS los pares scoreados.
    config["profiles"][active]["score_threshold"] = 0.0
    # Mantener filtros normales para no explotar memoria.

    df_prep = _prepare_for_deduplication(df, col_nit, col_name, mode, remove_top_words=20)

    pipeline = RecordLinkagePipeline(config, profile="deduplication_standard")
    with tempfile.TemporaryDirectory() as tmp:
        result = pipeline.run(
            sources={"DEDUP_SOURCE": df_prep},
            output_dir=tmp,
            source_priority=["DEDUP_SOURCE"],
            validate_data=False,
            generate_visualizations=False,
            show_progress=False,
        )
        # Recuperar scored_pairs si existe; sino, devolver correlativa
        scored = result.get("scored_pairs")
        if scored is None or len(scored) == 0:
            # Fallback: no exponer scored_pairs.
            scored = pd.DataFrame()
        return df_prep, scored


def _generar_pares_inline(
    df_prep: pd.DataFrame,
    n_max_pairs_per_record: int = 50,
) -> pd.DataFrame:
    """Fallback: generar pares con scoring directo cuando el pipeline no
    expone scored_pairs en su output.

    Esta versión recorre los bloques del LSH manualmente o, si fallan,
    usa un sample aleatorio limitado para tener algo con qué empezar.
    """
    from record_linkage.engine.lsh.nit_blocking import (
        NitBlockingConfig,
        block_by_nit_base,
    )

    nit_cfg = NitBlockingConfig()
    pairs_set: set[tuple[int, int]] = set()

    # 1. Pares por bloqueo NIT
    if "NIT_BASE" in df_prep.columns:
        for a, b in block_by_nit_base(df_prep, nit_column="NIT_BASE", config=nit_cfg):
            pairs_set.add((min(a, b), max(a, b)))

    # 2. Pares por mismo NOMBRE_LIMPIO (alto-recall, podría ser ruidoso)
    if "NOMBRE_LIMPIO" in df_prep.columns:
        by_name = df_prep.groupby("NOMBRE_LIMPIO").groups
        for _name, indices in by_name.items():
            idx_list = list(indices)
            if len(idx_list) < 2 or len(idx_list) > n_max_pairs_per_record:
                continue
            for i in range(len(idx_list)):
                for j in range(i + 1, len(idx_list)):
                    pairs_set.add((idx_list[i], idx_list[j]))

    if not pairs_set:
        return pd.DataFrame(columns=["idx_0", "idx_1"])

    pares = np.array(sorted(pairs_set))
    return pd.DataFrame({"idx_0": pares[:, 0], "idx_1": pares[:, 1]})


def scorear_pares(df_prep: pd.DataFrame, pares_df: pd.DataFrame) -> pd.DataFrame:
    """Score cada par con el scorer y devuelve un DataFrame ampliado."""
    profile = {
        "score_threshold": 0.0,
        "max_nit_distance": 99,
        "min_name_similarity": 0.0,
        "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
        "scoring_batch_size": 50_000,
    }
    sc = VectorizedScorer(profile)
    pares = pares_df[["idx_0", "idx_1"]].to_numpy()
    if len(pares) == 0:
        return pd.DataFrame()
    res = sc._score_batch_vectorized(pares, df_prep)
    return res


def muestrear_estratificado(
    scored: pd.DataFrame,
    n_pares: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Estratifica los pares scoreados en 5 categorías y muestrea de cada una."""

    estratos = []

    # 1. Alta confianza (score >= 0.90)
    s1 = scored[scored["score"] >= 0.90].copy()
    s1["ESTRATO"] = "alta_confianza"
    estratos.append(s1)

    # 2. Frontera (0.65 <= score < 0.72)
    s2 = scored[(scored["score"] >= 0.65) & (scored["score"] < 0.72)].copy()
    s2["ESTRATO"] = "frontera"
    estratos.append(s2)

    # 3. NIT idéntico (nit_dist == 0), nombre disímil (name_sim < 0.70)
    if "nit_dist" in scored.columns and "name_sim" in scored.columns:
        s3 = scored[(scored["nit_dist"] == 0) & (scored["name_sim"] < 0.70)].copy()
        s3["ESTRATO"] = "nit_identico_nombre_disimil"
        estratos.append(s3)

    # 4. Nombre alto (>= 0.95) pero NIT distinto (nit_dist > 0)
    if "nit_dist" in scored.columns and "name_sim" in scored.columns:
        s4 = scored[(scored["name_sim"] >= 0.95) & (scored["nit_dist"] > 0)].copy()
        s4["ESTRATO"] = "nombre_alto_nit_distinto"
        estratos.append(s4)

    # 5. Bajo el threshold pero cercano (0.50 - 0.64)
    s5 = scored[(scored["score"] >= 0.50) & (scored["score"] < 0.65)].copy()
    s5["ESTRATO"] = "potencial_fn"
    estratos.append(s5)

    cuota_por_estrato = n_pares // len(estratos)
    seleccion = []
    for est in estratos:
        if len(est) == 0:
            continue
        if len(est) <= cuota_por_estrato:
            seleccion.append(est)
        else:
            sample_idx = rng.choice(len(est), size=cuota_por_estrato, replace=False)
            seleccion.append(est.iloc[sample_idx])

    if not seleccion:
        return pd.DataFrame()
    out = pd.concat(seleccion, ignore_index=True)
    # Deduplicar por par
    out = out.drop_duplicates(subset=["idx_0", "idx_1"], keep="first")
    return out


def construir_plantilla(
    df_input: pd.DataFrame,
    muestra: pd.DataFrame,
    col_nit: str,
    col_name: str,
) -> pd.DataFrame:
    """Construye el CSV de etiquetado que el usuario debe llenar."""
    if len(muestra) == 0:
        return pd.DataFrame(
            columns=[
                "par_id",
                "nit_a",
                "razon_a",
                "nit_b",
                "razon_b",
                "score_sistema",
                "name_sim",
                "nit_dist",
                "ESTRATO",
                "MISMO_GRUPO",
                "CASO_FRONTERA",
                "NOTAS",
            ]
        )

    rows = []
    for i, r in muestra.reset_index(drop=True).iterrows():
        idx_a = int(r["idx_0"])
        idx_b = int(r["idx_1"])
        rows.append(
            {
                "par_id": f"P{i + 1:05d}",
                "nit_a": str(df_input.iloc[idx_a][col_nit]),
                "razon_a": str(df_input.iloc[idx_a][col_name]),
                "nit_b": str(df_input.iloc[idx_b][col_nit]),
                "razon_b": str(df_input.iloc[idx_b][col_name]),
                "score_sistema": float(r.get("score", 0.0)),
                "name_sim": float(r.get("name_sim", 0.0)),
                "nit_dist": int(r.get("nit_dist", -1)),
                "ESTRATO": str(r.get("ESTRATO", "")),
                # Columnas a llenar por el usuario:
                "MISMO_GRUPO": "",  # True / False
                "CASO_FRONTERA": "",  # opcional: A_, B_, P1_, etc.
                "NOTAS": "",  # texto libre
            }
        )
    df = pd.DataFrame(rows)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="CSV o parquet con tu muestra real"
    )
    parser.add_argument("--col-nit", type=str, default="NIT")
    parser.add_argument("--col-name", type=str, default="RAZON_SOCIAL")
    parser.add_argument("--salida", type=Path, default=Path("pares_para_etiquetar.csv"))
    parser.add_argument(
        "--n-pares", type=int, default=1500, help="Cantidad objetivo de pares a generar."
    )
    parser.add_argument(
        "--mode", type=str, default="BALANCEADO", choices=["AGRESIVO", "BALANCEADO", "CONSERVADOR"]
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="disk_based",
        choices=["default", "disk_based"],
        help="Motor LSH. 'disk_based' obligatorio para >1M regs (default).",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)

    print(f"📥 Leyendo {args.input}...")
    if args.input.suffix == ".parquet":
        df_input = pd.read_parquet(args.input)
    else:
        df_input = pd.read_csv(args.input, dtype={args.col_nit: str})
    df_input[args.col_nit] = df_input[args.col_nit].fillna("")
    print(f"   {len(df_input):,} registros, {df_input[args.col_nit].nunique():,} NITs únicos")

    print("⚙️  Preparando dataset y bloqueando candidatos...")
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            df_prep = _prepare_for_deduplication(
                df_input,
                args.col_nit,
                args.col_name,
                args.mode,
                remove_top_words=20,
            )
            pares_df = _generar_pares_inline(df_prep)
    finally:
        logging.disable(logging.NOTSET)
    print(f"   {len(pares_df):,} pares candidatos generados")

    if len(pares_df) == 0:
        print("❌ No se generaron pares. Verificar input.")
        return 1

    print("🎯 Scoreando pares para estratificar...")
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            scored = scorear_pares(df_prep, pares_df)
    finally:
        logging.disable(logging.NOTSET)
    print(f"   {len(scored):,} pares scoreados")

    print(f"📊 Muestreando ~{args.n_pares} pares estratificados...")
    muestra = muestrear_estratificado(scored, args.n_pares, rng)
    print(f"   {len(muestra):,} pares seleccionados")
    if len(muestra) > 0:
        print()
        print("Distribución por estrato:")
        print(muestra["ESTRATO"].value_counts().to_string())

    print("\n📝 Construyendo plantilla de etiquetado...")
    plantilla = construir_plantilla(df_input, muestra, args.col_nit, args.col_name)

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    plantilla.to_csv(args.salida, index=False)
    print(f"\n✅ {len(plantilla):,} pares escritos en: {args.salida}")
    print()
    print("SIGUIENTE PASO:")
    print(f"   1. Abre {args.salida} en Excel/Sheets")
    print("   2. Llena la columna MISMO_GRUPO con True / False")
    print("   3. Corre `medir_con_ground_truth.py` con el CSV etiquetado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
