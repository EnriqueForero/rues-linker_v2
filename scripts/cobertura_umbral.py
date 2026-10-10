#!/usr/bin/env python
"""cobertura_umbral.py — Tabla de decisión del umbral de cobertura (F2.2, ADR-0011).

Compone ``docs/evidencia/cobertura_umbral.json`` a partir de las corridas de
los dos instrumentos, una por umbral del barrido, y aplica la regla de
``record_linkage.evaluation.cobertura_umbral.elegir_umbral`` (compuerta de F2 y
regla de Enrique). No corre nada: las corridas se producen con los scripts
que ya existen, para no duplicar su lógica, y se dejan FUERA de
``docs/evidencia/`` (ahí solo entra la tabla):

    E=/tmp/barrido_umbral
    for u in 0.70 0.75 0.80 0.84 0.88; do
      python scripts/banco.py --etiqueta u${u/./} --evidencia $E \\
          --datos data/benchmark/benchmark_institucional.csv.gz \\
          --dir-trabajo /tmp/banco_u${u/./} --ajuste cobertura_sin_identificador.umbral=$u
      python scripts/escala.py --etiqueta u${u/./} --tamanos 139k --evidencia $E \\
          --ajuste cobertura_sin_identificador.umbral=$u
    done
    python scripts/banco.py --etiqueta apagada --evidencia $E \\
        --datos data/benchmark/benchmark_institucional.csv.gz \\
        --ajuste cobertura_sin_identificador.activa=false
    python scripts/cobertura_umbral.py --evidencia $E --umbrales 0.70 0.75 0.80 0.84 0.88 \\
        --sin-estrellas apagada --nota "..."

La etiqueta de cada umbral es ``u<umbral sin punto>`` (``u070``) salvo que se
indique otra con ``--etiquetas 0.70=mi_etiqueta``. El script verifica que
cada corrida se midió con el umbral que se le atribuye (lo dice su
``especificacion.ajustes_perfil``) y falla si no.

USO
    python scripts/cobertura_umbral.py --evidencia /tmp/barrido_umbral
    python scripts/cobertura_umbral.py --evidencia /tmp/barrido_umbral --salida /tmp/tabla.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage.evaluation.cobertura_umbral import (
    armar_tabla,
    escribir_tabla,
    medida_banco_desde_corrida,
    puntos_desde_evidencia,
)
from record_linkage.evaluation.comparador import cargar_corrida

SALIDA_POR_DEFECTO = RAIZ / "docs" / "evidencia" / "cobertura_umbral.json"
UMBRALES_POR_DEFECTO = (0.70, 0.75, 0.80, 0.84, 0.88)


def etiqueta_de(umbral: float) -> str:
    """``0.70`` → ``u070``: la etiqueta con la que se corren banco y escala."""
    return "u" + f"{umbral:.2f}".replace(".", "")


def construir_parser() -> argparse.ArgumentParser:
    """Define la interfaz de línea de comandos."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--evidencia",
        type=Path,
        required=True,
        help="carpeta con corrida_<etiqueta>.json y escala_<etiqueta>.json del barrido",
    )
    p.add_argument(
        "--umbrales",
        nargs="+",
        type=float,
        default=list(UMBRALES_POR_DEFECTO),
        help=f"umbrales del barrido (defecto {' '.join(map(str, UMBRALES_POR_DEFECTO))})",
    )
    p.add_argument(
        "--etiquetas",
        nargs="*",
        default=[],
        metavar="UMBRAL=ETIQUETA",
        help="etiqueta distinta de u<umbral> para algún umbral",
    )
    p.add_argument("--tamano", default="139k", help="tamaño de escala.py que se lee")
    p.add_argument(
        "--sin-estrellas",
        default=None,
        metavar="ETIQUETA",
        help="corrida del banco con la perilla apagada, como referencia",
    )
    p.add_argument("--nota", default="", help="contexto de la medición")
    p.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO, help="JSON a escribir")
    return p


def _parsear_etiquetas(pares: list[str]) -> dict[float, str]:
    """``["0.75=mio"]`` → ``{0.75: "mio"}``; falla rápido si un par viene mal."""
    salida: dict[float, str] = {}
    for par in pares:
        umbral, separador, etiqueta = par.partition("=")
        try:
            if not separador or not etiqueta:
                raise ValueError("falta el '=' o la etiqueta")
            salida[float(umbral)] = etiqueta
        except ValueError as exc:
            raise SystemExit(
                f"--etiquetas espera UMBRAL=ETIQUETA (p. ej. 0.75=mio), recibió {par!r}: {exc}"
            ) from exc
    return salida


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada."""
    args = construir_parser().parse_args(argv)
    propias = _parsear_etiquetas(args.etiquetas)
    etiquetas = {u: propias.get(u, etiqueta_de(u)) for u in args.umbrales}
    puntos = puntos_desde_evidencia(args.evidencia, etiquetas, tamano=args.tamano)
    sin_estrellas = None
    if args.sin_estrellas:
        sin_estrellas = medida_banco_desde_corrida(
            cargar_corrida(args.evidencia, args.sin_estrellas)
        )
    tabla = armar_tabla(puntos, nota=args.nota, sin_estrellas=sin_estrellas)
    destino = escribir_tabla(tabla, args.salida)
    print(resumen(tabla))
    print(f"\n💾 Tabla de decisión: {destino}")
    return 0


def resumen(tabla: dict[str, Any]) -> str:
    """Una línea por umbral y la decisión, para pegar en el informe."""
    lineas = [
        f"{'umbral':>7}{'P':>8}{'R':>8}{'macroF1':>9}{'FPneg':>7}{'R SIN':>7}"
        f"{'P 139k':>8}{'FPneg139k':>11}{'mayor':>7}{'ent.':>6}{'pasa':>6}"
    ]
    for fila in tabla["umbrales"]:
        b, e = fila["banco"], fila["escala"]
        recall_sin = next((v for k, v in b["recall_por_regimen"].items() if k.startswith("SIN")), 0)
        mayor = e["grupo_mayor_sin_identificador"] if e else {}
        lineas.append(
            f"{fila['umbral']:>7.2f}{b['precision']:>8.4f}{b['recall']:>8.4f}"
            f"{b['macro_f1']:>9.4f}{b['fp_que_tocan_negativo']:>7}{recall_sin:>7.4f}"
            + (
                f"{e['precision']:>8.4f}{e['fp_que_tocan_negativo']:>11}"
                f"{mayor.get('filas', 0):>7}{mayor.get('entidades', 0):>6}"
                if e
                else f"{'—':>8}{'—':>11}{'—':>7}{'—':>6}"
            )
            + f"{'sí' if fila['pasa_compuerta'] else 'no':>6}"
        )
    d = tabla["decision"]
    lineas.append(f"\nELEGIDO {d['umbral']:.2f} — {d['motivo']}")
    for desc in d["descartados"]:
        lineas.append(f"  descartado {desc['umbral']:.2f}: {desc['motivo']}")
    return "\n".join(lineas)


if __name__ == "__main__":
    raise SystemExit(main())
