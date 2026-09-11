"""muestrear_rues_para_gt.py — Muestreo estratificado para ground truth real.

Toma un dataset grande (RUES completo, ~2M registros) y produce una muestra
estratificada apta para etiquetado humano. La estratificación evita el sesgo
de "muestreo aleatorio puro" que satura el cuerpo gordo de la distribución
(empresas con nombres limpios y NIT bien formado).

Estratos:
    - Longitud de razón social: corto / medio / largo / muy_largo
    - Presencia de sufijo societario (S.A.S., LTDA., S.A., E.U.): sí / no
    - Ciudad: presente / ausente (afecta el F1 significativamente)

USO
    python scripts/muestrear_rues_para_gt.py \\
        --input data/rues_completo.parquet \\
        --output data/muestra_rues_50k.parquet \\
        --n-total 50000 \\
        --col-name RAZON_SOCIAL --col-nit NIT --col-ciudad CIUDAD \\
        --seed 42
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SUFIJOS_REGEX = r"\b(?:S\.?A\.?S\.?|LTDA\.?|S\.?A\.?|LIMITADA|E\.?U\.?|CIA\.?|INC\.?)\b"


def muestrear_estratificado(
    df: pd.DataFrame,
    n_total: int,
    col_name: str,
    col_ciudad: str | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Muestreo estratificado proporcional por (long_nombre, sufijo, ciudad)."""
    work = df.copy()

    # Estrato 1: longitud del nombre
    long_serie = work[col_name].fillna("").astype(str).str.len()
    work["__len_bin"] = pd.cut(
        long_serie,
        bins=[-1, 20, 40, 60, 10000],
        labels=["corto", "medio", "largo", "muy_largo"],
    )

    # Estrato 2: presencia de sufijo societario
    work["__has_sufijo"] = (
        work[col_name].fillna("").astype(str).str.contains(SUFIJOS_REGEX, case=False, regex=True)
    )

    # Estrato 3: presencia de ciudad (si la columna existe)
    if col_ciudad and col_ciudad in work.columns:
        work["__has_ciudad"] = work[col_ciudad].notna() & (
            work[col_ciudad].astype(str).str.strip() != ""
        )
        stratum_cols = ["__len_bin", "__has_sufijo", "__has_ciudad"]
    else:
        stratum_cols = ["__len_bin", "__has_sufijo"]

    n_input = len(work)
    print(f"📥 Input: {n_input:,} registros")
    print(f"   Estratos: {stratum_cols}")

    # Mostrar tamaño de cada estrato
    counts = work.groupby(stratum_cols, observed=True).size().sort_values(ascending=False)
    print("\nDistribución de estratos (top 10):")
    for stratum, n in counts.head(10).items():
        print(f"   {stratum}: {n:,} ({n / n_input:.1%})")

    # Muestreo proporcional, al menos 1 por estrato no vacío
    def _muestra_estrato(g: pd.DataFrame) -> pd.DataFrame:
        n_estrato = max(1, round(n_total * len(g) / n_input))
        n_a_tomar = min(len(g), n_estrato)
        return g.sample(n_a_tomar, random_state=seed)

    sample = work.groupby(stratum_cols, observed=True, group_keys=False).apply(_muestra_estrato)
    aux_cols = [c for c in ["__len_bin", "__has_sufijo", "__has_ciudad"] if c in sample.columns]
    sample = sample.drop(columns=aux_cols).reset_index(drop=True)

    print(f"\n📤 Muestra final: {len(sample):,} registros ({len(sample) / n_input:.2%} del input)")
    return sample


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", required=True, help="Parquet o CSV del dataset completo")
    parser.add_argument("--output", required=True, help="Parquet de salida")
    parser.add_argument("--n-total", type=int, default=50_000)
    parser.add_argument("--col-name", default="RAZON_SOCIAL")
    parser.add_argument("--col-nit", default="NIT")
    parser.add_argument("--col-ciudad", default="CIUDAD")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ No existe: {input_path}", file=sys.stderr)
        return 1

    df = (
        pd.read_csv(input_path, dtype=str)
        if input_path.suffix == ".csv"
        else pd.read_parquet(input_path)
    )

    # Validar columnas
    for c in [args.col_name, args.col_nit]:
        if c not in df.columns:
            print(f"❌ Falta columna requerida: {c}", file=sys.stderr)
            print(f"   Columnas disponibles: {list(df.columns)}", file=sys.stderr)
            return 1

    sample = muestrear_estratificado(
        df,
        n_total=args.n_total,
        col_name=args.col_name,
        col_ciudad=args.col_ciudad if args.col_ciudad in df.columns else None,
        seed=args.seed,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(out_path, index=False, compression="snappy")
    print(f"💾 Guardado en: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
