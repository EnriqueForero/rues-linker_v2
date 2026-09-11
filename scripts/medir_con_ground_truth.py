"""medir_con_ground_truth.py — mide v2.10.0 contra tus pares etiquetados.

Toma el CSV producido por `generar_pares_para_etiquetar.py` (después de
que el usuario llene la columna `MISMO_GRUPO`), corre el pipeline sobre
el dataset original y reporta:

    - Métricas globales: F1, P, R, TP, FP, FN sobre los pares etiquetados.
    - Métricas por estrato (alta_confianza, frontera, etc.).
    - Métricas por categoría (CASO_FRONTERA si el usuario etiquetó).
    - Distribución de errores: lista de FP y FN concretos para revisión.

USO
    python scripts/medir_con_ground_truth.py \\
        --input /content/drive/MyDrive/rues_muestra_50k.csv \\
        --pares-etiquetados /content/drive/MyDrive/pares_etiquetados.csv \\
        --col-nit NIT --col-name RAZON_SOCIAL \\
        --reporte /content/drive/MyDrive/reporte_calidad.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd


def cargar_pares_etiquetados(path: Path) -> pd.DataFrame:
    """Carga el CSV de etiquetado y valida que tenga las columnas mínimas."""
    df = pd.read_csv(path)
    requeridas = {"par_id", "nit_a", "razon_a", "nit_b", "razon_b", "MISMO_GRUPO"}
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise ValueError(f"Columnas faltantes en {path}: {faltantes}. Esperadas: {requeridas}")

    # Normalizar MISMO_GRUPO a bool. Acepta True/False/yes/no/sí/1/0.
    def _parse_bool(v):
        if pd.isna(v) or v == "":
            return None
        s = str(v).strip().lower()
        if s in ("true", "1", "sí", "si", "yes", "y", "verdadero"):
            return True
        if s in ("false", "0", "no", "n", "falso"):
            return False
        return None

    df["MISMO_GRUPO_bool"] = df["MISMO_GRUPO"].apply(_parse_bool)
    sin_etiquetar = df["MISMO_GRUPO_bool"].isna().sum()
    if sin_etiquetar > 0:
        print(f"⚠️  Hay {sin_etiquetar} pares SIN ETIQUETAR — se ignoran.")
    df = df[df["MISMO_GRUPO_bool"].notna()].copy()
    df["MISMO_GRUPO_bool"] = df["MISMO_GRUPO_bool"].astype(bool)
    print(f"✅ {len(df):,} pares etiquetados válidos.")
    return df


def correr_sistema(
    df_input: pd.DataFrame,
    col_nit: str,
    col_name: str,
    mode: str,
    engine: str = "disk_based",
) -> pd.DataFrame:
    """Corre v2.10.0 con engine_type forzado y devuelve la correlativa.

    v2.10.0: usa el pipeline directamente (no `deduplicate_unified`) para
    forzar `engine_type=disk_based` independientemente del tamaño del
    dataset. Esto es CRÍTICO para que los resultados de la calibración
    coincidan con el comportamiento de producción (>1M registros usan
    disk_based automáticamente; aquí se fuerza para muestras más chicas).
    """
    from record_linkage.deduplication.unified import (
        _build_deduplication_config,
        _prepare_for_deduplication,
    )
    from record_linkage.pipeline.linkage_pipeline import RecordLinkagePipeline

    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            config = _build_deduplication_config("deduplication_standard", mode, len(df_input))
            config["linkage_engine_class"] = engine
            active = config["profiles"][config["profile"]]
            df_prep = _prepare_for_deduplication(
                df_input[[col_nit, col_name]].copy(),
                col_nit,
                col_name,
                mode,
                remove_top_words=int(active.get("remove_top_words", 20)),
            )
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
                corr = result.get("correlative_table").copy()
    finally:
        logging.disable(logging.NOTSET)
    return corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)


def evaluar_pares_etiquetados(
    df_input: pd.DataFrame,
    correlativa: pd.DataFrame,
    pares_etiquetados: pd.DataFrame,
    col_nit: str,
    col_name: str,
) -> tuple[pd.DataFrame, dict]:
    """Para cada par etiquetado, mira si el sistema lo unió o no.

    Devuelve:
        - DataFrame con columna `prediccion_sistema` añadida.
        - Diccionario con métricas globales.
    """
    # Mapear (nit, razon) → grupo predicho
    df_with_pred = df_input.copy()
    df_with_pred["pred_group"] = correlativa["ID_GRUPO"].values
    df_with_pred["clave"] = (
        df_with_pred[col_nit].astype(str) + "||" + df_with_pred[col_name].astype(str)
    )
    clave_to_group = dict(zip(df_with_pred["clave"], df_with_pred["pred_group"], strict=False))

    predicciones = []
    no_encontrados = 0
    for _i, r in pares_etiquetados.iterrows():
        k_a = f"{r['nit_a']}||{r['razon_a']}"
        k_b = f"{r['nit_b']}||{r['razon_b']}"
        g_a = clave_to_group.get(k_a)
        g_b = clave_to_group.get(k_b)
        if g_a is None or g_b is None:
            predicciones.append(None)
            no_encontrados += 1
        else:
            predicciones.append(g_a == g_b)

    pares_etiquetados = pares_etiquetados.copy()
    pares_etiquetados["prediccion_sistema"] = predicciones
    if no_encontrados > 0:
        print(
            f"⚠️  {no_encontrados} pares no se encontraron en el dataset "
            f"(¿quizás strip de espacios?). Se descartan."
        )
    pares_etiquetados = pares_etiquetados[pares_etiquetados["prediccion_sistema"].notna()].copy()
    pares_etiquetados["prediccion_sistema"] = pares_etiquetados["prediccion_sistema"].astype(bool)

    truth = pares_etiquetados["MISMO_GRUPO_bool"].to_numpy()
    pred = pares_etiquetados["prediccion_sistema"].to_numpy()
    tp = int(((truth) & (pred)).sum())
    fp = int(((~truth) & (pred)).sum())
    fn = int(((truth) & (~pred)).sum())
    tn = int(((~truth) & (~pred)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    metricas = {
        "n_pares": len(pares_etiquetados),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }
    return pares_etiquetados, metricas


def reporte_por_estrato(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula métricas desglosadas por estrato."""
    if "ESTRATO" not in df.columns:
        return pd.DataFrame()
    rows = []
    for estrato, sub in df.groupby("ESTRATO"):
        truth = sub["MISMO_GRUPO_bool"].to_numpy()
        pred = sub["prediccion_sistema"].to_numpy()
        tp = int(((truth) & (pred)).sum())
        fp = int(((~truth) & (pred)).sum())
        fn = int(((truth) & (~pred)).sum())
        tn = int(((~truth) & (~pred)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        rows.append(
            {
                "estrato": estrato,
                "n": len(sub),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
            }
        )
    return pd.DataFrame(rows)


def _nit_valido(valor: object) -> bool:
    """Heurística transparente de NIT válido: >= 5 dígitos tras quitar separadores.

    Los NITs colombianos tienen ~9-10 dígitos; vacío, 'nan', '0' o texto corto
    cuentan como inválidos. Deliberadamente conservadora y documentada para que
    la asignación de régimen sea auditable.
    """
    if valor is None:
        return False
    digitos = "".join(ch for ch in str(valor) if ch.isdigit())
    return len(digitos) >= 5


def reporte_por_regimen(df: pd.DataFrame) -> pd.DataFrame:
    """Métricas desglosadas por régimen CON_NIT / SIN_NIT.

    Régimen del par = CON_NIT si AMBOS registros tienen NIT válido; SIN_NIT en
    otro caso. Es el desglose que más importa: el sistema acierta casi perfecto
    con NIT y sufre sin él, así que un F1 global puede ocultar un SIN_NIT pobre.
    """
    if not {"nit_a", "nit_b"}.issubset(df.columns):
        return pd.DataFrame()
    work = df.copy()
    con_nit = work["nit_a"].map(_nit_valido) & work["nit_b"].map(_nit_valido)
    work["__regimen"] = con_nit.map({True: "CON_NIT", False: "SIN_NIT"})
    rows = []
    for regimen, sub in work.groupby("__regimen"):
        truth = sub["MISMO_GRUPO_bool"].to_numpy()
        pred = sub["prediccion_sistema"].to_numpy()
        tp = int((truth & pred).sum())
        fp = int((~truth & pred).sum())
        fn = int((truth & ~pred).sum())
        tn = int((~truth & ~pred).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        rows.append(
            {
                "regimen": regimen,
                "n": len(sub),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="CSV o parquet de tu muestra real"
    )
    parser.add_argument(
        "--pares-etiquetados",
        type=Path,
        required=True,
        help="CSV producido por generar_pares_para_etiquetar.py con columna MISMO_GRUPO llena",
    )
    parser.add_argument("--col-nit", type=str, default="NIT")
    parser.add_argument("--col-name", type=str, default="RAZON_SOCIAL")
    parser.add_argument("--mode", type=str, default="BALANCEADO")
    parser.add_argument(
        "--engine",
        type=str,
        default="disk_based",
        choices=["default", "disk_based"],
        help="Motor LSH. 'disk_based' obligatorio para >1M regs (default).",
    )
    parser.add_argument(
        "--reporte", type=Path, default=None, help="Si se especifica, escribe reporte Markdown"
    )
    parser.add_argument(
        "--errores-csv",
        type=Path,
        default=None,
        help="Si se especifica, escribe CSV con FP y FN para revisión manual",
    )
    args = parser.parse_args()

    print(f"📥 Cargando muestra desde {args.input}...")
    if args.input.suffix == ".parquet":
        df_input = pd.read_parquet(args.input)
    else:
        df_input = pd.read_csv(args.input, dtype={args.col_nit: str})
    df_input[args.col_nit] = df_input[args.col_nit].fillna("")
    print(f"   {len(df_input):,} registros")

    print(f"📥 Cargando pares etiquetados desde {args.pares_etiquetados}...")
    pares = cargar_pares_etiquetados(args.pares_etiquetados)

    print(f"⚙️  Corriendo v2.10.0 sobre la muestra completa (engine={args.engine})...")
    corr = correr_sistema(df_input, args.col_nit, args.col_name, args.mode, engine=args.engine)
    print(f"   {corr['ID_GRUPO'].nunique():,} grupos predichos")

    print("📊 Evaluando contra etiquetas manuales...")
    pares_pred, metricas = evaluar_pares_etiquetados(
        df_input, corr, pares, args.col_nit, args.col_name
    )

    # ────────────── Reporte en consola ──────────────
    print()
    print("=" * 60)
    print("MÉTRICAS GLOBALES (vs etiquetas manuales)")
    print("=" * 60)
    print(f"  N pares evaluados : {metricas['n_pares']:>6,}")
    print(f"  Precision         : {metricas['precision']:.3f}")
    print(f"  Recall            : {metricas['recall']:.3f}")
    print(f"  F1                : {metricas['f1']:.3f}")
    print(
        f"  TP / FP / FN / TN : {metricas['tp']} / {metricas['fp']} / "
        f"{metricas['fn']} / {metricas['tn']}"
    )

    rep_estrato = reporte_por_estrato(pares_pred)
    if not rep_estrato.empty:
        print()
        print("=" * 60)
        print("MÉTRICAS POR ESTRATO")
        print("=" * 60)
        print(rep_estrato.to_string(index=False))

    rep_regimen = reporte_por_regimen(pares_pred)
    if not rep_regimen.empty:
        print()
        print("=" * 60)
        print("MÉTRICAS POR RÉGIMEN (CON_NIT / SIN_NIT)")
        print("=" * 60)
        print(rep_regimen.to_string(index=False))

    # ────────────── Reporte Markdown opcional ──────────────
    if args.reporte:
        args.reporte.parent.mkdir(parents=True, exist_ok=True)
        with args.reporte.open("w") as f:
            f.write("# Reporte de calidad sobre producción real\n\n")
            f.write(f"**Dataset:** `{args.input.name}` ({len(df_input):,} registros)\n\n")
            f.write(
                f"**Etiquetas:** `{args.pares_etiquetados.name}` "
                f"({metricas['n_pares']:,} pares)\n\n"
            )
            f.write(f"**Modo:** `{args.mode}`\n\n")
            f.write("## Métricas globales\n\n")
            f.write("| Métrica   | Valor |\n|---|---:|\n")
            for k in ("precision", "recall", "f1"):
                f.write(f"| {k.title()} | {metricas[k]:.3f} |\n")
            for k in ("tp", "fp", "fn", "tn"):
                f.write(f"| {k.upper()} | {metricas[k]} |\n")
            if not rep_estrato.empty:
                f.write("\n## Métricas por estrato\n\n")
                f.write(rep_estrato.to_markdown(index=False))
                f.write("\n")
            if not rep_regimen.empty:
                f.write("\n## Métricas por régimen (CON_NIT / SIN_NIT)\n\n")
                f.write(rep_regimen.to_markdown(index=False))
                f.write("\n")
        print(f"\n📝 Reporte escrito: {args.reporte}")

    # ────────────── CSV de errores opcional ──────────────
    if args.errores_csv:
        errores = pares_pred[
            pares_pred["MISMO_GRUPO_bool"] != pares_pred["prediccion_sistema"]
        ].copy()
        errores["TIPO_ERROR"] = errores.apply(
            lambda r: "FP" if r["prediccion_sistema"] else "FN", axis=1
        )
        cols_relevantes = [
            "par_id",
            "TIPO_ERROR",
            "nit_a",
            "razon_a",
            "nit_b",
            "razon_b",
            "score_sistema",
            "name_sim",
            "nit_dist",
            "ESTRATO",
            "MISMO_GRUPO",
            "prediccion_sistema",
            "CASO_FRONTERA",
            "NOTAS",
        ]
        cols_disponibles = [c for c in cols_relevantes if c in errores.columns]
        args.errores_csv.parent.mkdir(parents=True, exist_ok=True)
        errores[cols_disponibles].to_csv(args.errores_csv, index=False)
        print(f"📝 Errores escritos: {args.errores_csv} ({len(errores)} casos)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
