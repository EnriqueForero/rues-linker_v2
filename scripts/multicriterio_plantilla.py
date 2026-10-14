#!/usr/bin/env python
"""multicriterio_plantilla.py — ¿Sirve la plantilla de ``data/ejemplos`` para una prueba MULTICRITERIO?

Envoltorio delgado (argumentos, impresión y determinismo en subprocesos) sobre
``record_linkage.evaluation.multicriterio``, que es la única implementación: ahí viven las
configuraciones por defecto, la validación, el aporte por variable, la medición de cada corrida
contra ``VERDAD_GRUPOS``, ``ETIQUETAS_PARES`` y la base, la regla de decisión y los hallazgos del
motor que hay que conocer para no medir mal (``MUNICIPIO`` se declara como ``CIUDAD``; las extras
se suman después de los filtros de nombre y de NIT; ``categorical`` premia el dato ausente; dicts,
no tuplas). Lectura: ``docs/PLANTILLA_BASE_REAL.md`` §5.1 y §8; el notebook 13 hace lo mismo con
una sola configuración editable.

Determinismo: ``--determinismo N`` vuelve a correr la configuración recomendada en N subprocesos
con ``PYTHONHASHSEED`` distinto y compara la huella de la partición (CLAUDE.md §5: dos llamadas
en el mismo proceso no prueban determinismo).

Uso::

    python scripts/multicriterio_plantilla.py --datos data/ejemplos \\
        --dir-trabajo /tmp/multicriterio --salida docs/evidencia/plantilla_base_real_multicriterio.json
    python scripts/multicriterio_plantilla.py --datos mi_base.xlsx --configuraciones mis_configs.json \\
        --dir-trabajo salida/multicriterio --salida salida/multicriterio.json --determinismo 0

``--configuraciones`` es un JSON con una lista de ``{"nombre", "descripcion", "extra_features":
[{"column", "weight", "type"}, ...], "ajustes_perfil": {...}}`` (``ajustes_perfil`` es opcional:
sobrescrituras del perfil como ``{"llaves_bloqueo": ["EMAIL"]}``, que hace candidatos a los pares
con el mismo correo). La primera configuración, sin extras ni ajustes, es la base contra la que se
comparan las demás. Sin el argumento se usan las ``CONFIGURACIONES`` del módulo. La medición exige
``VERDAD_GRUPOS`` y ``CASOS``; para una base sin verdad use el notebook 13 (``comparar_off_on``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage.evaluation.multicriterio import (
    COLUMNAS_CANONICAS,
    CONFIGURACIONES,
    Medicion,
    aporte_por_variable,
    caso_por_id,
    comparar_con_base,
    elegir_recomendada,
    es_base,
    leer_configuraciones,
    leer_etiquetas_de,
    marco_registros,
    medicion_como_dict,
    medir_configuracion,
    pares_de_prueba,
    tabla_aporte,
    tabla_resumen,
    validar_configuraciones,
)
from record_linkage.evaluation.plantilla_base_real import (
    leer_plantilla,
    tabla_markdown,
)


def verificar_determinismo(
    argv_base: list[str], nombre: str, repeticiones: int, dir_trabajo: Path
) -> dict[str, object]:
    """Corre ``--solo nombre`` en ``repeticiones`` subprocesos con ``PYTHONHASHSEED`` distinto y
    compara huella y cifras. Devuelve las huellas y si todas coinciden."""
    huellas, cifras = [], []
    for semilla in range(repeticiones):
        salida = dir_trabajo / f"determinismo_{semilla}.json"
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                *argv_base,
                "--solo",
                nombre,
                "--determinismo",
                "0",
                "--dir-trabajo",
                str(dir_trabajo / f"determinismo_{semilla}"),
                "--salida",
                str(salida),
            ],
            check=True,
            env={**os.environ, "PYTHONHASHSEED": str(semilla)},
            stdout=subprocess.DEVNULL,
        )
        corrida = json.loads(salida.read_text(encoding="utf-8"))["configuraciones"][0]
        huellas.append(corrida["huella_particion"])
        cifras.append(
            {
                "grupos_predichos": corrida["grupos_predichos"],
                **corrida["pares_verdad_grupos"],
                "casos_acertados": corrida["casos_acertados"],
            }
        )
    return {
        "configuracion": nombre,
        "subprocesos": repeticiones,
        "huellas": huellas,
        "cifras": cifras,
        "identico": len(set(huellas)) == 1 and all(c == cifras[0] for c in cifras),
    }


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--datos", type=Path, default=RAIZ / "data" / "ejemplos", help="carpeta de CSV o el .xlsx"
    )
    p.add_argument(
        "--etiquetas", type=Path, help="CSV/XLSX de pares etiquetados (por defecto el de --datos)"
    )
    p.add_argument(
        "--dir-trabajo",
        type=Path,
        default=Path(tempfile.gettempdir()) / "multicriterio_plantilla",
        help="un subdirectorio por configuración",
    )
    p.add_argument("--salida", type=Path, help="JSON con todas las cifras y configuraciones")
    p.add_argument(
        "--configuraciones", type=Path, help="JSON con las configuraciones (ver docstring)"
    )
    p.add_argument("--solo", help="correr solo la configuración con este nombre")
    p.add_argument(
        "--determinismo",
        type=int,
        default=2,
        help="subprocesos con PYTHONHASHSEED distinto para la recomendada (0 = no verificar)",
    )
    return p.parse_args(argv)


def _argv_para_subprocesos(args: argparse.Namespace) -> list[str]:
    argv = ["--datos", str(args.datos)]
    if args.etiquetas:
        argv += ["--etiquetas", str(args.etiquetas)]
    if args.configuraciones:
        argv += ["--configuraciones", str(args.configuraciones)]
    return argv


def _imprimir_medicion(m: Medicion) -> None:
    pares = m.pares or {}
    total_casos = len(m.tabla_casos) if m.tabla_casos is not None else 0
    print(
        f"\n{m.configuracion.nombre} — {m.configuracion.descripcion}\n  grupos {m.grupos_predichos}"
        + "".join(f" · {k}={v}" for k, v in pares.items())
        + f" · casos {m.casos_acertados}/{total_casos}"
    )
    if m.cambios_vs_base:
        c = m.cambios_vs_base
        ganados, perdidos = c["pares_ganados"], c["pares_perdidos"]
        assert isinstance(ganados, list) and isinstance(perdidos, list)
        print(
            f"  vs base: mejoran {c['casos_que_mejoran']} · empeoran {c['casos_que_empeoran']}"
            f" · pares ganados {len(ganados)} · perdidos {len(perdidos)}"
        )
    if m.columnas_extra_en_scorer:
        print(f"  columnas extra que vio el motor: {m.columnas_extra_en_scorer}")


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    libro = leer_plantilla(args.datos)
    if libro.verdad is None or libro.casos is None:
        raise SystemExit("La medición multicriterio exige las hojas VERDAD_GRUPOS y CASOS.")
    etiquetas = leer_etiquetas_de(args.datos, args.etiquetas)
    configuraciones = (
        leer_configuraciones(args.configuraciones) if args.configuraciones else CONFIGURACIONES
    )
    columnas = {c for df in libro.fuentes.values() for c in df.columns}
    validar_configuraciones(configuraciones, columnas)
    if args.solo:
        configuraciones = tuple(c for c in configuraciones if c.nombre == args.solo)
        if not configuraciones:
            raise SystemExit(f"No hay una configuración llamada {args.solo!r}.")
    casos = caso_por_id(libro.verdad)
    registros = marco_registros(libro)
    positivos, negativos = pares_de_prueba(libro.verdad, libro.casos, etiquetas)
    aporte = aporte_por_variable(registros, positivos, negativos)
    print(
        f"Fuentes: {', '.join(f'{f} ({len(df)})' for f, df in libro.fuentes.items())} · "
        f"positivos: {len(positivos)} · negativos difíciles: {len(negativos)} · etiquetas: "
        f"{len(etiquetas) if etiquetas is not None else 'no hay'}"
    )
    print("\nAporte de cada variable (comparador sobre los pares de prueba):")
    print(tabla_aporte(aporte))

    mediciones: list[Medicion] = []
    for config in configuraciones:
        m = medir_configuracion(libro, etiquetas, config, args.dir_trabajo / config.nombre)
        if mediciones and es_base(mediciones[0].configuracion):
            m.cambios_vs_base = comparar_con_base(mediciones[0], m, casos)
        mediciones.append(m)
        _imprimir_medicion(m)

    recomendada = elegir_recomendada(mediciones)
    print("\n" + tabla_resumen(mediciones))
    print(f"\nRecomendada (precisión antes que recall): {recomendada.configuracion.nombre}")
    assert recomendada.tabla_casos is not None
    print(tabla_markdown(recomendada.tabla_casos))

    determinismo = None
    if args.determinismo > 0 and not args.solo:
        determinismo = verificar_determinismo(
            _argv_para_subprocesos(args),
            recomendada.configuracion.nombre,
            args.determinismo,
            args.dir_trabajo,
        )
        huellas = determinismo["huellas"]
        assert isinstance(huellas, list)
        print(
            f"\nDeterminismo ({determinismo['subprocesos']} subprocesos, PYTHONHASHSEED distinto): "
            f"{'idéntico' if determinismo['identico'] else 'DISTINTO'} · huellas "
            f"{[str(h)[:12] for h in huellas]}"
        )

    if args.salida:
        import record_linkage

        salida = {
            "version": record_linkage.__version__,
            "fecha": date.today().isoformat(),
            "datos": str(args.datos),
            "registros": len(mediciones[0].correlativa),
            "grupos_verdaderos": int(libro.verdad["ID_GRUPO"].nunique()),
            "pares_de_prueba": {"positivos": len(positivos), "negativos_dificiles": len(negativos)},
            "columnas_canonicas": COLUMNAS_CANONICAS,
            "aporte_por_variable": aporte,
            "configuraciones": [medicion_como_dict(m, casos) for m in mediciones],
            "recomendada": recomendada.configuracion.nombre,
            "regla_de_decision": (
                "mayor precisión por pares contra VERDAD_GRUPOS; empate → mayor recall; "
                "empate → menos variables extra"
            ),
            "determinismo": determinismo,
        }
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nResultado guardado en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
