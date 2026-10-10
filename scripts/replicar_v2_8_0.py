"""replicar_v2_8_0.py — Réplica reproducible del fix P0-1 v2.8.0.

Ejecuta tres bloques de validación que cualquiera puede correr para
verificar las afirmaciones del CHANGELOG sin confiar en nuestra palabra:

    1. Genera un dataset sintético con casos canónicos del patrón "NIT
       idéntico, nombre disímil" — los pares que el fix está diseñado
       para recuperar.
    2. Corre el pipeline sobre el dataset sintético con y sin las
       perillas activas; imprime la composición de TP/FP/FN.
    3. Corre el pipeline sobre los dos ground truth oficiales (golden
       269 y exhaustivo 1456) y compara métricas pairwise contra los
       números reportados en el CHANGELOG.

Uso:
    python scripts/replicar_v2_8_0.py
    python scripts/replicar_v2_8_0.py --solo-sintetico
    python scripts/replicar_v2_8_0.py --salida /tmp/replica/

El script es **determinista**: mismo input → mismo output. No requiere
credenciales ni datos externos al repositorio.

Author: Claude (auditor)  Date: 2026-05-22  Version: 2.8.0
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

from record_linkage.deduplication.unified import (
    AjustesDeduplicacion,
    deduplicate_unified,
)
from record_linkage.evaluation.pairwise import evaluar_pares

# Repo root: scripts/ está al lado de tests/.
ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────────────
# 1. Dataset sintético P0-1: pares con NIT idéntico, nombre disímil
# ─────────────────────────────────────────────────────────────────────

_SYNTHETIC_CASES: list[dict] = [
    # Grupo 1 — PINTUCO (NIT 890900148, sin DV: se infiere 2)
    {"NIT": "890900148", "RAZON_SOCIAL": "AKZONOBEL PINTUCO", "ID_GROUP": 1},
    {"NIT": "890900148", "RAZON_SOCIAL": "PINTUKO", "ID_GROUP": 1},
    {"NIT": "890900148", "RAZON_SOCIAL": "COMPAÑIA GLOBAL DE PINTURAS", "ID_GROUP": 1},
    {"NIT": "890900148", "RAZON_SOCIAL": "PINTUCO GRUPO ORBIS", "ID_GROUP": 1},
    {"NIT": "890900148", "RAZON_SOCIAL": "AKZOMOBEL PINYUCO", "ID_GROUP": 1},  # typo
    # Grupo 2 — SANOFI (NIT 830010337)
    {"NIT": "830010337", "RAZON_SOCIAL": "SANOFI AVENTIS DE COLOMBIA", "ID_GROUP": 2},
    {"NIT": "830010337", "RAZON_SOCIAL": "SANFI", "ID_GROUP": 2},  # nombre corto disímil
    {"NIT": "830010337", "RAZON_SOCIAL": "MANUFACTURERA GENFAR GRUPO SANOFI", "ID_GROUP": 2},
    # Grupo 3 — EY (NIT 800220000)
    {"NIT": "800220000", "RAZON_SOCIAL": "EY COLOMBIA", "ID_GROUP": 3},
    {"NIT": "800220000", "RAZON_SOCIAL": "ERNST AND YOUNG EN LIQUIDACION", "ID_GROUP": 3},
    {"NIT": "800220000", "RAZON_SOCIAL": "ERNST YOUNG AUDIT", "ID_GROUP": 3},
    # Grupo 4 — caso negativo crítico: NITs distintos pero nombre parecido
    {"NIT": "900111111", "RAZON_SOCIAL": "CONSTRUCTORA BOLIVAR", "ID_GROUP": 4},
    {"NIT": "900222222", "RAZON_SOCIAL": "CONSTRUCTORA BOLIVAR DEL CARIBE", "ID_GROUP": 5},
    # Grupo 5 — par que YA funcionaba en v2.7.0 (no debe romperse)
    {"NIT": "830310331", "RAZON_SOCIAL": "EMPRESA ABC SAS", "ID_GROUP": 6},
    {"NIT": "830310331", "RAZON_SOCIAL": "EMPRESA ABC", "ID_GROUP": 6},
    # Grupo 6 — sin NIT (la perilla no debe afectarlos)
    {"NIT": "", "RAZON_SOCIAL": "EMPRESA SIN NIT UNO", "ID_GROUP": 7},
    {"NIT": "", "RAZON_SOCIAL": "EMPRESA SIN NIT UNO", "ID_GROUP": 7},
]


def generar_dataset_sintetico(out_path: Path) -> pd.DataFrame:
    """Genera y guarda el dataset sintético P0-1."""
    df = pd.DataFrame(_SYNTHETIC_CASES)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(
        f"  ✅ Dataset escrito: {out_path}  ({len(df)} registros, "
        f"{df['ID_GROUP'].nunique()} grupos verdad)"
    )
    return df


# ─────────────────────────────────────────────────────────────────────
# 2. Comparación con/sin perillas — sobre cualquier dataset
# ─────────────────────────────────────────────────────────────────────


def _correr(df: pd.DataFrame, override: bool, boost: float) -> pd.DataFrame:
    """Corre el pipeline con las perillas del fix sobreescritas en el perfil.

    F2.9: `deduplicate_unified` + `AjustesDeduplicacion` en lugar de
    `RecordLinkagePipeline` a mano. Misma partición que antes, medida sobre
    los dos ground truth con las perillas OFF y ON (la preparación directa no
    quitaba palabras frecuentes, pero L1 del pipeline las quita igual con el
    `remove_top_words` del perfil, así que no hay nada que compensar).
    """
    ajustes = AjustesDeduplicacion(
        perfil={
            "nit_identical_overrides_name_filter": override,
            "nit_identical_score_boost": boost,
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        corr, _ = deduplicate_unified(
            df, "NIT", "RAZON_SOCIAL", "BALANCEADO", output_dir=tmp, ajustes=ajustes
        )
        return corr


def _evaluar(df_truth: pd.DataFrame, corr: pd.DataFrame):
    corr = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return evaluar_pares(df_truth["ID_GROUP"].to_numpy(), corr["ID_GRUPO"].to_numpy())


def comparar_perillas(df_truth: pd.DataFrame, label: str) -> dict:
    """Imprime tabla F1/P/R con perillas OFF (v2.7.0) y ON (v2.8.0 default)."""
    print(f"\n=== {label} (n={len(df_truth)}, {df_truth['ID_GROUP'].nunique()} grupos) ===")
    base = df_truth[["NIT", "RAZON_SOCIAL"]].copy()

    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            corr_off = _correr(base.copy(), override=False, boost=0.0)
            corr_on = _correr(base.copy(), override=True, boost=0.05)
    finally:
        logging.disable(logging.NOTSET)

    m_off = _evaluar(df_truth, corr_off)
    m_on = _evaluar(df_truth, corr_on)

    print(f"  {'config':<20}  F1     P      R      TP   FP   FN")
    print(f"  {'-' * 60}")
    print(
        f"  {'v2.7.0 (OFF/0.0)':<20}  {m_off.f1:.3f}  {m_off.precision:.3f}  "
        f"{m_off.recall:.3f}  {m_off.tp:>4} {m_off.fp:>4} {m_off.fn:>4}"
    )
    print(
        f"  {'v2.8.0 (ON/0.05)':<20}  {m_on.f1:.3f}  {m_on.precision:.3f}  "
        f"{m_on.recall:.3f}  {m_on.tp:>4} {m_on.fp:>4} {m_on.fn:>4}"
    )
    delta_f1 = m_on.f1 - m_off.f1
    delta_p = m_on.precision - m_off.precision
    delta_r = m_on.recall - m_off.recall
    print(
        f"  {'Δ':<20}  {delta_f1:+.3f} {delta_p:+.3f} {delta_r:+.3f}  "
        f"{m_on.tp - m_off.tp:+>4} {m_on.fp - m_off.fp:+>4} "
        f"{m_on.fn - m_off.fn:+>4}"
    )

    return {"off": m_off, "on": m_on}


# ─────────────────────────────────────────────────────────────────────
# 3. Smoke del API público con extra_features (v2.7.0)
# ─────────────────────────────────────────────────────────────────────


def smoke_extra_features() -> None:
    """Reproduce el caso CORONA-Bogotá vs CARVAJAL-Cali del CHANGELOG v2.7.0."""
    print("\n=== Smoke v2.7.0: extra_features con tipo signed ===")
    df = pd.DataFrame(
        [
            {"NIT": "890300404", "RAZON_SOCIAL": "CORONA", "CIUDAD": "BOGOTA"},
            {"NIT": "890300405", "RAZON_SOCIAL": "CARVAJAL", "CIUDAD": "CALI"},
            {"NIT": "890300404", "RAZON_SOCIAL": "CORONA SA", "CIUDAD": "BOGOTA"},
        ]
    )
    # Sin extra_features
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                corr_sin, _ = deduplicate_unified(
                    df_input=df.copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                )
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                corr_con, _ = deduplicate_unified(
                    df_input=df.copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                    extra_features=[
                        {"column": "CIUDAD", "weight": 0.20, "type": "categorical_signed"}
                    ],
                )
    finally:
        logging.disable(logging.NOTSET)

    g_sin = corr_sin.sort_values("ORIGINAL_INDEX")["ID_GRUPO"].tolist()
    g_con = corr_con.sort_values("ORIGINAL_INDEX")["ID_GRUPO"].tolist()
    print(f"  Grupos sin CIUDAD signed: {g_sin}")
    print(f"  Grupos con CIUDAD signed: {g_con}")
    print(f"  CORONA y CARVAJAL en mismo grupo (sin CIUDAD): {g_sin[0] == g_sin[1]}")
    print(
        f"  CORONA y CARVAJAL en mismo grupo (con CIUDAD): "
        f"{g_con[0] == g_con[1]}  ← debe ser False (signed separa negativos)"
    )


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida",
        type=Path,
        default=ROOT / "tests" / "data_sintetica",
        help="Directorio donde escribir el dataset sintético P0-1.",
    )
    parser.add_argument(
        "--solo-sintetico",
        action="store_true",
        help="Si está activo, omite la corrida sobre los ground truth oficiales.",
    )
    args = parser.parse_args()

    print("═" * 60)
    print("  RÉPLICA v2.8.0 — Tratamiento privilegiado de NIT idéntico")
    print("═" * 60)

    print("\n[1/4] Generando dataset sintético P0-1...")
    out_path = args.salida / "dataset_p0_1_nit_identico.csv"
    df_synth = generar_dataset_sintetico(out_path)

    print("\n[2/4] Validando el fix sobre el dataset sintético...")
    comparar_perillas(df_synth, "DATASET SINTÉTICO P0-1")

    if not args.solo_sintetico:
        gt_exh = ROOT / "tests" / "data" / "golden_truth_exhaustivo.csv"
        gt_269 = ROOT / "tests" / "data" / "golden_truth.csv"

        if gt_exh.exists():
            print("\n[3/4] Validando sobre ground truth exhaustivo (1456 regs)...")
            df_exh = pd.read_csv(gt_exh, dtype={"NIT": str})
            df_exh["NIT"] = df_exh["NIT"].fillna("")
            comparar_perillas(df_exh, "EXHAUSTIVO")
        else:
            print(f"\n[3/4] ⚠️  No se encontró {gt_exh}, omitiendo.")

        if gt_269.exists():
            print("\n[4/4] Validando sobre golden truth 269...")
            df_269 = pd.read_csv(gt_269, dtype={"NIT": str})
            df_269["NIT"] = df_269["NIT"].fillna("")
            comparar_perillas(df_269, "GOLDEN 269")
        else:
            print(f"\n[4/4] ⚠️  No se encontró {gt_269}, omitiendo.")

    # Smoke v2.7.0
    smoke_extra_features()

    print("\n" + "═" * 60)
    print("  Réplica completada. Comparar contra CHANGELOG §[2.8.0].")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
