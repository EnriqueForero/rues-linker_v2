#!/usr/bin/env python
"""banco.py — Corre el banco de pruebas de rues-linker y deja la evidencia.

Una sola invocación, un solo conjunto de datos, un solo formato de salida:
así dos corridas cualesquiera son comparables campo por campo.

USO
    python scripts/banco.py --etiqueta base
    python scripts/banco.py --etiqueta ruidoso --perfil fuentes_ruidosas
    python scripts/banco.py --etiqueta extras --variables-extra CIUDAD TELEFONO EMAIL
    python scripts/banco.py --comparar base ruidoso

La evidencia queda en ``docs/evidencia/corrida_<etiqueta>.json``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage.evaluation.banco import (
    Corrida,
    EspecificacionBanco,
    correr_banco,
)
from record_linkage.evaluation.comparador import cargar_corrida, comparar

DATOS_POR_DEFECTO = RAIZ / "data" / "ground_truth" / "ground_truth_grande.csv"
EVIDENCIA = RAIZ / "docs" / "evidencia"


def construir_parser() -> argparse.ArgumentParser:
    """Define la interfaz de línea de comandos."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--etiqueta", help="nombre corto de la corrida")
    p.add_argument("--perfil", default="produccion_estandar", help="perfil de configuración")
    p.add_argument("--datos", type=Path, default=DATOS_POR_DEFECTO, help="CSV de referencia")
    p.add_argument("--confiables", nargs="*", default=[], help="fuentes que no se deduplican")
    p.add_argument(
        "--variables-extra",
        nargs="*",
        default=None,
        metavar="COLUMNA[:TIPO[:PESO]]",
        help="evidencia adicional, p. ej. TELEFONO:telefono_signed:0.10",
    )
    p.add_argument(
        "--ajuste",
        nargs="*",
        default=[],
        metavar="CLAVE=VALOR",
        help="sobrescritura puntual del perfil",
    )
    p.add_argument("--multicampo", default=None, help="perfil multicampo posterior")
    p.add_argument("--nota", default="", help="qué cambió en esta corrida")
    p.add_argument(
        "--comparar", nargs=2, metavar=("BASE", "NUEVA"), help="compara dos corridas ya guardadas"
    )
    p.add_argument(
        "--pliegue",
        type=int,
        default=None,
        help="evaluar solo un pliegue de grupos (validación fuera de muestra)",
    )
    p.add_argument("--pliegues", type=int, default=3, help="número total de pliegues")
    p.add_argument("--dir-trabajo", type=Path, default=Path("/tmp/banco_trabajo"))
    return p


def _parsear_variables(especificaciones: list[str] | None) -> tuple[object, ...] | None:
    """Convierte ``COLUMNA[:TIPO[:PESO]]`` en la forma que espera el scorer.

    Sin tipo ni peso se usan los valores por defecto de la librería, para que
    ``--variables-extra CIUDAD`` siga significando lo mismo que antes.
    """
    if not especificaciones:
        return None
    salida: list[object] = []
    for bruto in especificaciones:
        partes = bruto.split(":")
        if len(partes) == 1:
            salida.append(partes[0])
            continue
        entrada: dict[str, object] = {"column": partes[0], "type": partes[1]}
        if len(partes) > 2:
            entrada["weight"] = float(partes[2])
        else:
            entrada["weight"] = 0.05
        salida.append(entrada)
    return tuple(salida)


def _parsear_ajustes(pares: list[str]) -> dict[str, object]:
    """Convierte ``clave=valor`` en un diccionario con tipos inferidos."""
    salida: dict[str, object] = {}
    for par in pares:
        if "=" not in par:
            raise SystemExit(f"--ajuste espera CLAVE=VALOR, recibió {par!r}")
        clave, _, bruto = par.partition("=")
        if bruto.lower() in {"true", "false"}:
            salida[clave] = bruto.lower() == "true"
        else:
            try:
                salida[clave] = int(bruto)
            except ValueError:
                try:
                    salida[clave] = float(bruto)
                except ValueError:
                    salida[clave] = bruto
    return salida


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada."""
    args = construir_parser().parse_args(argv)

    if args.comparar:
        base, nueva = (cargar_corrida(EVIDENCIA, e) for e in args.comparar)
        informe = comparar(base, nueva)
        print(informe.resumen())
        return 0 if informe.pasa else 1

    if not args.etiqueta:
        raise SystemExit("--etiqueta es obligatoria (o use --comparar)")

    espec = EspecificacionBanco(
        etiqueta=args.etiqueta,
        datos=args.datos,
        perfil=args.perfil,
        confiables=frozenset(args.confiables),
        variables_extra=_parsear_variables(args.variables_extra),
        ajustes_perfil=_parsear_ajustes(args.ajuste) or None,
        perfil_multicampo=args.multicampo,
        pliegue=args.pliegue,
        pliegues=args.pliegues,
        dir_trabajo=args.dir_trabajo,
        dir_evidencia=EVIDENCIA,
        nota=args.nota,
    )
    corrida: Corrida = correr_banco(espec)
    print(corrida.resumen())
    destino = corrida.guardar(EVIDENCIA)
    print(f"\n💾 Evidencia: {destino.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
