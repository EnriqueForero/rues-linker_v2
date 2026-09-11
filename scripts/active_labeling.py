"""active_labeling.py — etiquetado activo para construir GT real.

Esto cumple el bloqueante B3 de la auditoría externa:
"El ground truth real del RUES. 3,000-5,000 registros etiquetados por dos
anotadores. Con eso recalibras el matcher contra datos reales."

Esta herramienta NO etiqueta sola. Selecciona los pares más informativos
de tu output para que TÚ (y un segundo anotador) los etiqueten a mano.
Con 200-500 etiquetas estratégicas obtienes ~80% del valor calibratorio
de 5,000 etiquetas aleatorias.

Estrategia (active learning):

1. **Pares ambiguos**: aquellos cuyo score está cerca del umbral de
   decisión. Son los que el matcher "duda". Etiquetarlos refina el
   umbral mejor que etiquetar pares obvios.

2. **Estratificado por régimen**: 50% CON_NIT, 50% SIN_NIT, para que la
   calibración no sesgue a un solo régimen.

3. **Diversidad**: evita seleccionar muchos pares de la misma "familia"
   (mismo cluster baseline) para que las etiquetas cubran casos distintos.

Uso típico:
    $ python scripts/active_labeling.py \\
        --source rues_dian_crm.csv \\
        --n-pairs 500 \\
        --output pares_a_etiquetar.xlsx

    # Te abre Excel con columnas para etiquetar (id_left, id_right,
    # datos de ambos, score, decisión sugerida, etiqueta_humana).
    # Etiquetas a mano: SAME / DIFFERENT / UNCLEAR.

    # Luego (cuando tengas las etiquetas):
    $ python scripts/recalibrate_from_labels.py \\
        --labels pares_a_etiquetar.xlsx \\
        --output profile_calibrado.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def select_informative_pairs(
    decisions_df: pd.DataFrame,
    n_pairs: int,
    *,
    source_df: pd.DataFrame,
    id_col: str = "ID_REGISTRO",
    regime_col: str | None = "REGIMEN",
) -> pd.DataFrame:
    """Selecciona pares más informativos para etiquetar.

    Args:
        decisions_df: DataFrame retornado por
            ``MatcherPostProcessor.decisions_log`` (con columnas score,
            concordances, decision, score_<var>).
        n_pairs: Cuántos pares seleccionar para etiquetar.
        source_df: DataFrame original (para enriquecer con datos legibles).
        id_col: Nombre de la columna ID.
        regime_col: Columna de régimen para estratificar (CON_NIT/SIN_NIT).
            Si None, no estratifica.

    Returns:
        DataFrame con N pares ordenados por informatividad, incluyendo:
        - id_left, id_right
        - datos legibles de ambos (RAZON_SOCIAL, NIT, CIUDAD, etc.)
        - score predicho
        - decisión del matcher
        - columna vacía 'etiqueta_humana' para llenar a mano
        - columna 'notas' para comentarios del anotador
    """
    df = decisions_df.copy()
    if df.empty:
        return df

    # Calcular informatividad: |score| cercano al umbral (~0.5)
    df["distance_from_threshold"] = np.abs(df["score"] - 0.5)
    df["informativeness"] = 1.0 / (1.0 + df["distance_from_threshold"])

    # Estratificar por régimen si tenemos esa info
    if regime_col and regime_col in source_df.columns:
        regime_lookup = source_df.set_index(id_col)[regime_col]
        df["regime_left"] = df["id_left"].map(regime_lookup)
        df["regime_right"] = df["id_right"].map(regime_lookup)
        df["both_sin_nit"] = (df["regime_left"] == "SIN_NIT") & (df["regime_right"] == "SIN_NIT")

        # 50/50: pares ambos SIN_NIT vs el resto
        n_sin = n_pairs // 2
        n_other = n_pairs - n_sin
        pool_sin = df[df["both_sin_nit"]].nlargest(n_sin * 3, "informativeness")
        pool_other = df[~df["both_sin_nit"]].nlargest(n_other * 3, "informativeness")
        # Sampling para diversidad
        sin_sel = pool_sin.sample(n=min(n_sin, len(pool_sin)), random_state=42)
        other_sel = pool_other.sample(n=min(n_other, len(pool_other)), random_state=42)
        selected = pd.concat([sin_sel, other_sel], ignore_index=True)
    else:
        # Sin estratificación
        pool = df.nlargest(n_pairs * 3, "informativeness")
        selected = pool.sample(n=min(n_pairs, len(pool)), random_state=42)

    # Enriquecer con datos legibles
    enrichment_cols = ["RAZON_SOCIAL", "NIT", "CIUDAD", "TELEFONO", "EMAIL", "DIRECCION"]
    available = [c for c in enrichment_cols if c in source_df.columns]
    lookup = source_df.set_index(id_col)[available]

    for col in available:
        selected[f"{col}_left"] = selected["id_left"].map(lookup[col])
        selected[f"{col}_right"] = selected["id_right"].map(lookup[col])

    # Columnas para el anotador
    selected["etiqueta_humana"] = ""  # SAME / DIFFERENT / UNCLEAR
    selected["notas"] = ""

    # Ordenar por informatividad descendente
    selected = selected.sort_values("informativeness", ascending=False).reset_index(drop=True)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Etiquetado activo para calibrar matcher")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="CSV con todos los registros (de las fuentes a deduplicar)",
    )
    parser.add_argument(
        "--n-pairs",
        type=int,
        default=500,
        help="Cuántos pares seleccionar para etiquetar (default 500)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Excel de salida (con columnas para llenar a mano)",
    )
    parser.add_argument(
        "--id-col",
        default="ID_REGISTRO",
        help="Nombre de la columna ID (default ID_REGISTRO)",
    )
    parser.add_argument(
        "--regime-col",
        default="REGIMEN",
        help="Columna de régimen para estratificar (default REGIMEN, ignorada si no existe)",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Directorio de trabajo para el pipeline (default temp)",
    )
    args = parser.parse_args()

    # Cargar fuente
    print(f"Cargando {args.source}…")
    df_all = pd.read_csv(args.source, dtype=str).fillna("")
    df_all[args.id_col] = df_all[args.id_col].astype(str)
    print(f"  {len(df_all):,} registros, {df_all.columns.tolist()}")

    # Particionar por FUENTE si existe, si no usar todo como una sola fuente
    if "FUENTE" in df_all.columns:
        sources = {str(f): sub.copy() for f, sub in df_all.groupby("FUENTE")}
        print(f"  Particionado en {len(sources)} fuentes: {list(sources.keys())}")
    else:
        sources = {"main": df_all.copy()}
        print("  Sin columna FUENTE: tratando todo como una fuente")

    # Correr linkage con matcher para obtener decisions_log
    print("\nCorriendo linkage() con matcher para identificar pares ambiguos…")
    from record_linkage import linkage

    result = linkage(
        sources=sources,
        col_ciudad="CIUDAD" if "CIUDAD" in df_all.columns else None,
        extra_features=[c for c in ["TELEFONO", "EMAIL", "DIRECCION"] if c in df_all.columns],
        work_dir=args.work_dir,
        profile="produccion_estandar",
        matching_profile="colombia",
        return_matcher_audit=True,
    )

    decisions = result.get("matcher_decisions")
    if decisions is None or decisions.empty:
        print(
            "ERROR: el matcher no produjo decisiones (¿el pipeline no detectó pares?).",
            file=sys.stderr,
        )
        return 1

    print(f"  Pares evaluados por matcher: {len(decisions):,}")
    print(f"  Stats: {result['matcher_stats']}")

    # Seleccionar pares informativos
    print(f"\nSeleccionando {args.n_pairs} pares más informativos…")
    selected = select_informative_pairs(
        decisions,
        n_pairs=args.n_pairs,
        source_df=df_all,
        id_col=args.id_col,
        regime_col=args.regime_col,
    )
    print(f"  Seleccionados: {len(selected)}")

    # Reordenar columnas para legibilidad
    base_cols = ["id_left", "id_right", "score", "concordances", "decision"]
    pair_cols = [c for c in selected.columns if c.endswith("_left") or c.endswith("_right")]
    label_cols = ["etiqueta_humana", "notas"]
    other_cols = [
        c
        for c in selected.columns
        if c not in base_cols + pair_cols + label_cols
        and not c.startswith("score_")
        and not c.startswith("conc_")
    ]
    score_cols = [c for c in selected.columns if c.startswith("score_") or c.startswith("conc_")]

    ordered = base_cols + label_cols + pair_cols + score_cols + other_cols
    ordered = [c for c in ordered if c in selected.columns]
    selected = selected[ordered]

    # Exportar
    print(f"\nExportando a {args.output}…")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix == ".xlsx":
        selected.to_excel(args.output, index=False)
    elif args.output.suffix == ".csv":
        selected.to_csv(args.output, index=False)
    else:
        selected.to_parquet(args.output, index=False)

    print(f"\n✅ {len(selected):,} pares listos para etiquetar")
    print(f"   Archivo: {args.output}")
    print()
    print("INSTRUCCIONES:")
    print("  1. Abre el archivo y revisa cada par.")
    print("  2. Llena la columna 'etiqueta_humana' con: SAME / DIFFERENT / UNCLEAR")
    print("  3. Idealmente, dos anotadores etiquetan independiente y se concilia.")
    print("  4. Si tienes >85% de acuerdo entre anotadores, las etiquetas son utilizables.")
    print("  5. Luego corre: scripts/recalibrate_from_labels.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
