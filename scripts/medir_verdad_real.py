"""medir_verdad_real.py — precisión y recall de una corrida REAL contra pares etiquetados por personas.

Es el instrumento de la Fase 1 (docs/DIAGNOSTICO_Y_RUTA_2026-10-13.md): se corre fuera del repositorio, sobre la
correlativa de una corrida real y un archivo de etiquetas hecho con docs/PROTOCOLO_GROUND_TRUTH.md. Al repositorio solo
entran las cifras (el JSON de salida), nunca los datos.

Uso:
    python scripts/medir_verdad_real.py --etiquetas etiquetas_revisadas.xlsx \\
        --correlativa salida/correlativa.parquet --col-nit NIT --salida docs/evidencia/verdad_real_2026-10-20.json

Con dos anotadores (columna ANOTADOR en el archivo, o dos archivos con --etiquetas-b) imprime además el kappa y los
desacuerdos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage.evaluation.verdad_real import (
    acuerdo_entre_anotadores,
    leer_etiquetas,
    medir_verdad_real,
)


def _leer_correlativa(ruta: Path) -> pd.DataFrame:
    if ruta.suffix.lower() == ".parquet":
        return pd.read_parquet(ruta)
    if ruta.suffix.lower() in (".xlsx", ".xlsm"):
        return pd.read_excel(ruta, dtype=str)
    return pd.read_csv(ruta, dtype=str)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--etiquetas", type=Path, required=True, help="CSV/XLSX con ID_1, ID_2, IS_MATCH (o alias)"
    )
    p.add_argument("--etiquetas-b", type=Path, help="segundo anotador (opcional) para el kappa")
    p.add_argument(
        "--correlativa",
        type=Path,
        required=True,
        help="parquet/csv/xlsx de la corrida (una fila por registro)",
    )
    p.add_argument("--col-id", default="ID_REGISTRO")
    p.add_argument("--col-grupo", default="ID_GRUPO")
    p.add_argument(
        "--col-nit", default=None, help="columna del identificador para separar regímenes"
    )
    p.add_argument("--salida", type=Path, help="JSON con las cifras (sin datos)")
    args = p.parse_args(argv)

    etiquetas = leer_etiquetas(args.etiquetas)
    correlativa = _leer_correlativa(args.correlativa)
    res = medir_verdad_real(
        etiquetas, correlativa, col_id=args.col_id, col_grupo=args.col_grupo, col_nit=args.col_nit
    )
    print(
        f"Etiquetas: {res.n_etiquetados:,} pares ({res.n_descartados} dudosos descartados) · {res.archivo}"
    )
    print(res.tabla().to_string(index=False))
    for nota in res.notas:
        print(f"  nota: {nota}")

    acuerdo = None
    if args.etiquetas_b is not None:
        acuerdo = acuerdo_entre_anotadores(etiquetas, leer_etiquetas(args.etiquetas_b))
    elif "ANOTADOR" in etiquetas.columns and etiquetas["ANOTADOR"].nunique() == 2:
        a, b = (
            etiquetas[etiquetas["ANOTADOR"] == x] for x in sorted(etiquetas["ANOTADOR"].unique())
        )
        acuerdo = acuerdo_entre_anotadores(a, b)
    if acuerdo is not None:
        print(
            f"Acuerdo entre anotadores: {acuerdo.n_comunes} pares en común · observado {acuerdo.acuerdo_observado:.3f} · "
            f"kappa {acuerdo.kappa:.3f} ({acuerdo.lectura}) · desacuerdos {len(acuerdo.desacuerdos)}"
        )
    if args.salida:
        salida = res.como_dict()
        if acuerdo is not None:
            salida["acuerdo"] = {
                "n_comunes": acuerdo.n_comunes,
                "acuerdo_observado": acuerdo.acuerdo_observado,
                "kappa": acuerdo.kappa,
                "desacuerdos": len(acuerdo.desacuerdos),
            }
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Cifras guardadas en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
