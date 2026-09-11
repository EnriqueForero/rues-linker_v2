"""medir_kappa.py — Acuerdo inter-anotador (Cohen's kappa) entre dos etiquetadores.

La compuerta dura de la Fase 2 exige un F1 sobre datos reales **con kappa
reportado**. Este script calcula el Cohen's kappa entre las etiquetas de DOS
anotadores independientes sobre los pares en común, e imprime:

    - n de pares solapados (etiquetados por ambos),
    - % de acuerdo observado,
    - Cohen's kappa y su banda de interpretación,
    - la lista de DESACUERDOS (par_id + ambas etiquetas) para la sesión de
      resolución que exige el protocolo (docs/PROTOCOLO_GROUND_TRUTH.md).

Interpretación (Landis & Koch, la misma del protocolo):
    kappa > 0.80  → acuerdo casi perfecto → ground truth confiable.
    0.60 - 0.80   → sustancial → aceptable, revisar discrepancias.
    < 0.60        → el problema está mal definido → reescribir el MANUAL de
                    etiquetado, no el sistema.

Uso:
    python scripts/medir_kappa.py \\
        --labeler-a etiquetas_ana.csv \\
        --labeler-b etiquetas_juan.csv \\
        --report docs/kappa_reporte.md

Cada CSV debe tener al menos las columnas ``par_id`` y ``MISMO_GRUPO`` (la misma
estructura que produce ``generar_pares_para_etiquetar.py``). Solo se evalúan los
``par_id`` presentes y etiquetados en AMBOS archivos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def parse_label(v: object) -> bool | None:
    """Normaliza una etiqueta MISMO_GRUPO a bool. Acepta SI/NO/1/0/true/false/sí.

    Devuelve None si está vacía o no es interpretable (el par se ignora).
    """
    if pd.isna(v) or v == "":
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "sí", "si", "yes", "y", "verdadero"):
        return True
    if s in ("false", "0", "no", "n", "falso"):
        return False
    return None


def cohen_kappa(a: np.ndarray, b: np.ndarray) -> float:
    """Cohen's kappa entre dos secuencias de etiquetas alineadas.

    Implementado vía matriz de confusión (válido para cualquier nº de clases):
    kappa = (po - pe) / (1 - pe), donde po es el acuerdo observado y pe el
    esperado por azar. Casos degenerados (pe == 1): kappa = 1 si hay acuerdo
    total, 0 en otro caso (convención que coincide con scikit-learn).
    """
    a = np.asarray(a)
    b = np.asarray(b)
    n = len(a)
    if n == 0:
        return float("nan")
    clases = sorted(set(a.tolist()) | set(b.tolist()))
    pos = {c: i for i, c in enumerate(clases)}
    k = len(clases)
    cm = np.zeros((k, k), dtype=float)
    for x, y in zip(a, b, strict=True):
        cm[pos[x], pos[y]] += 1
    po = np.trace(cm) / n
    fila = cm.sum(axis=1) / n
    col = cm.sum(axis=0) / n
    pe = float((fila * col).sum())
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return float((po - pe) / (1.0 - pe))


def interpretar(kappa: float) -> str:
    """Banda de interpretación de Landis & Koch."""
    if np.isnan(kappa):
        return "indefinido (sin pares solapados)"
    if kappa > 0.80:
        return "casi perfecto — ground truth confiable"
    if kappa >= 0.60:
        return "sustancial — aceptable, revisar discrepancias"
    if kappa >= 0.40:
        return "moderado — el manual de etiquetado necesita aclararse"
    return "pobre — el problema está MAL DEFINIDO; reescribir el manual"


def cargar(path: Path, col_id: str, col_label: str) -> pd.DataFrame:
    """Carga un CSV de etiquetado, parsea la etiqueta y descarta los sin etiquetar."""
    df = pd.read_csv(path)
    for c in (col_id, col_label):
        if c not in df.columns:
            raise ValueError(f"Falta la columna '{c}' en {path}. Columnas: {list(df.columns)}")
    df = df[[col_id, col_label]].copy()
    df["__label"] = df[col_label].apply(parse_label)
    n_total = len(df)
    df = df[df["__label"].notna()].copy()
    print(f"  {path.name}: {len(df):,}/{n_total:,} pares etiquetados")
    return df[[col_id, "__label"]]


def main() -> int:
    p = argparse.ArgumentParser(description="Cohen's kappa entre dos etiquetadores")
    p.add_argument("--labeler-a", type=Path, required=True, help="CSV del anotador A")
    p.add_argument("--labeler-b", type=Path, required=True, help="CSV del anotador B")
    p.add_argument("--col-id", type=str, default="par_id", help="Columna clave del par")
    p.add_argument("--col-label", type=str, default="MISMO_GRUPO", help="Columna de etiqueta")
    p.add_argument("--report", type=Path, default=None, help="Ruta del reporte .md")
    args = p.parse_args()

    print("Cargando etiquetas de los dos anotadores...")
    a = cargar(args.labeler_a, args.col_id, args.col_label).rename(columns={"__label": "a"})
    b = cargar(args.labeler_b, args.col_id, args.col_label).rename(columns={"__label": "b"})

    comun = a.merge(b, on=args.col_id, how="inner")
    n = len(comun)
    if n == 0:
        print("❌ No hay par_id en común entre los dos archivos. Nada que comparar.")
        return 1

    acuerdo = float((comun["a"] == comun["b"]).mean())
    kappa = cohen_kappa(comun["a"].to_numpy(), comun["b"].to_numpy())
    banda = interpretar(kappa)
    desacuerdos = comun[comun["a"] != comun["b"]]

    print(f"\n{'=' * 56}\n  ACUERDO INTER-ANOTADOR\n{'=' * 56}")
    print(f"  Pares solapados (ambos etiquetaron): {n:,}")
    print(f"  Acuerdo observado                  : {acuerdo:.1%}")
    print(f"  Cohen's kappa                      : {kappa:.3f}")
    print(f"  Interpretacion                     : {banda}")
    print(f"  Desacuerdos a resolver             : {len(desacuerdos):,}")
    compuerta = bool((not np.isnan(kappa)) and kappa >= 0.80)
    print(f"\n  COMPUERTA (kappa >= 0.80): {'PASA' if compuerta else 'NO PASA'}")
    if not compuerta:
        print("  → Resolver desacuerdos en sesion y, si kappa sigue bajo, reescribir")
        print("    el MANUAL de etiquetado (no el sistema). Ver PROTOCOLO_GROUND_TRUTH.md.")

    if len(desacuerdos) > 0:
        print("\n  Primeros desacuerdos (par_id : A vs B):")
        for _i, r in desacuerdos.head(15).iterrows():
            print(f"    {r[args.col_id]} : A={r['a']}  B={r['b']}")

    if args.report:
        md = (
            "# Acuerdo inter-anotador (Cohen's kappa)\n\n"
            f"- Pares solapados: **{n:,}**\n"
            f"- Acuerdo observado: **{acuerdo:.1%}**\n"
            f"- Cohen's kappa: **{kappa:.3f}** ({banda})\n"
            f"- Desacuerdos a resolver: **{len(desacuerdos):,}**\n"
            f"- Compuerta (kappa >= 0.80): **{'PASA' if compuerta else 'NO PASA'}**\n\n"
            "## Desacuerdos\n\n"
            "Resolver en sesion conjunta; cada resolucion alimenta una aclaracion "
            "del manual de etiquetado.\n\n"
        )
        if len(desacuerdos) > 0:
            md += desacuerdos.rename(columns={"a": "anotador_A", "b": "anotador_B"}).to_markdown(
                index=False
            )
        args.report.write_text(md + "\n", encoding="utf-8")
        print(f"\n📄 reporte → {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
