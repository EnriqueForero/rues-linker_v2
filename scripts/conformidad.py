#!/usr/bin/env python
"""conformidad.py — Corre la suite de conformidad de 43 casos.

El banco (`scripts/banco.py`) dice CUÁNTO mejoró. Este dice QUÉ sabe hacer la
librería: cada caso frontera del catálogo aprueba o reprueba por separado, sin
promediarse con nada. Un comportamiento roto que afecta a cuatro casos es
invisible en un F1 sobre 46.000 pares, y aquí sale en rojo.

USO
    python scripts/conformidad.py                      # dedup + linkage
    python scripts/conformidad.py --escenario dedup
    python scripts/conformidad.py --etiqueta mi_cambio --esquema multicampo_completo
    python scripts/conformidad.py --importar ruta/Ground_Truth_Multicampo_v1.xlsx

Devuelve código 0 si todos los casos FIRMES aprueban, 1 si alguno reprueba.
Los casos que el catálogo marca `TP_DIFICIL` son deuda declarada y no
reprueban la corrida; se listan aparte para que no se olviden.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage import __version__
from record_linkage.evaluation.conformidad import (
    CAMINO_POR_DEFECTO,
    ConjuntoConformidad,
    Informe,
    cargar_conjunto,
    evaluar_dedup,
    evaluar_linkage,
)

#: Hojas del libro original y su nombre de archivo. Importar es una operación
#: explícita y trazable: el conjunto NO se regenera en cada corrida.
HOJAS = (
    "DEDUP_REGISTROS",
    "DEDUP_PARES",
    "LINKAGE_BASE_A",
    "LINKAGE_BASE_B",
    "LINKAGE_GROUND_TRUTH",
    "CATALOGO_CASOS",
    "DICCIONARIO",
)


def _esquema_conformidad(con_geo: bool = False, corroborar: bool = False):
    """Esquema del conjunto, según su propio DICCIONARIO.

    Los tipos no se infieren: la hoja DICCIONARIO del libro original ya
    declara qué es cada columna, y respetarlo es parte del contrato. Los pesos
    son los del preset `esquema_multicampo_completo`, que es con el que se
    midió la línea base que publica la hoja LEEME.
    """
    from record_linkage.matching.campos import (
        CampoSpec,
        CorroboracionVeto,
        EsquemaCampos,
        TipoCampo,
    )

    campos = [
        CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
        CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
        CampoSpec("TELEFONO", TipoCampo.TELEFONO, peso=1.5),
        CampoSpec("EMAIL", TipoCampo.EMAIL, peso=1.5),
        CampoSpec("DIRECCION", TipoCampo.DIRECCION, peso=1.0),
        CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
    ]
    if con_geo:
        campos.append(CampoSpec("LATITUD", TipoCampo.GEO, peso=0.5, columna_lon="LONGITUD"))
    # F3: dos NIT distintos pueden ser el mismo ente —un dígito mal capturado
    # (C09), una reestructuración societaria (C21)—. Activar la corroboración
    # los reúne y las seis trampas de falso positivo siguen aprobando.
    #
    # Sobre min_corroborantes=2, honestamente: el conjunto de conformidad NO
    # distingue entre 1 y 2; medido, las dos configuraciones dan F1 = 1,0000 y
    # ninguna trampa se cae. Lo que sostiene a MC04 (gmail compartido) y MC05
    # (call center) no es el número de corroborantes, sino que sus
    # comparadores ya devuelven 0 ante valores de baja entropía, más la
    # exigencia de similitud de nombre. Se elige 2 por criterio de riesgo
    # —exigir que dos canales independientes coincidan antes de levantar el
    # veto del identificador— no porque esta medición lo respalde. Cuál de los
    # dos conviene en datos reales está pendiente de medir (C35).
    corroboracion = CorroboracionVeto(
        campos_corroborantes=("EMAIL", "TELEFONO"),
        umbral_campo=0.99,
        min_corroborantes=2,
        umbral_nombre_empresa=0.95,
        activa=corroborar,
    )
    sufijo = ("_geo" if con_geo else "") + ("_corroborado" if corroborar else "")
    return EsquemaCampos(campos=campos, nombre="conformidad" + sufijo, corroboracion=corroboracion)


def importar(libro: Path, destino: Path) -> None:
    """Extrae el libro a CSV versionados. Operación explícita, no automática."""
    import hashlib
    import json

    destino.mkdir(parents=True, exist_ok=True)
    manifiesto: dict = {"origen": libro.name, "hojas": {}}
    for hoja in HOJAS:
        marco = pd.read_excel(libro, sheet_name=hoja, dtype=str).fillna("")
        ruta = destino / f"{hoja.lower()}.csv"
        marco.to_csv(ruta, index=False)
        manifiesto["hojas"][hoja.lower()] = {
            "filas": len(marco),
            "columnas": list(marco.columns),
            "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest()[:16],
        }
    (destino / "manifiesto.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"✅ Importado a {destino} ({len(HOJAS)} hojas)")


def correr_dedup(
    conjunto: ConjuntoConformidad,
    etiqueta: str,
    con_geo: bool,
    nota: str,
    corroborar: bool = False,
) -> Informe:
    from record_linkage.api import dedupe_esquema

    datos = conjunto.dedup_registros.copy()
    for columna in ("LATITUD", "LONGITUD"):
        datos[columna] = pd.to_numeric(datos[columna], errors="coerce")
    resultado = dedupe_esquema(datos, _esquema_conformidad(con_geo, corroborar))
    grupos = resultado.correlativa["ID_GRUPO"].to_numpy()
    return evaluar_dedup(
        conjunto,
        grupos,
        etiqueta=etiqueta,
        version=__version__,
        nota=nota,
        corroborar=corroborar,
        camino=CAMINO_POR_DEFECTO,
    )


def correr_linkage(
    conjunto: ConjuntoConformidad,
    etiqueta: str,
    con_geo: bool,
    nota: str,
    corroborar: bool = False,
) -> Informe:
    """Cruza A con B pasando las dos bases por el mismo motor.

    Se concatenan con una marca de origen y del clustering resultante se leen
    solo los pares que cruzan de A a B: un grupo que une dos registros de A no
    es un cruce, es una deduplicación interna, y este escenario no la mide.
    """
    from record_linkage.api import dedupe_esquema

    a = conjunto.linkage_a.rename(columns={"REG_ID_A": "REG_ID"}).copy()
    b = conjunto.linkage_b.rename(columns={"REG_ID_B": "REG_ID"}).copy()
    a["_LADO"], b["_LADO"] = "A", "B"
    datos = pd.concat([a, b], ignore_index=True)
    for columna in ("LATITUD", "LONGITUD"):
        datos[columna] = pd.to_numeric(datos[columna], errors="coerce")

    resultado = dedupe_esquema(datos, _esquema_conformidad(con_geo, corroborar))
    marco = resultado.correlativa
    pares: set[tuple[str, str]] = set()
    for _, bloque in marco.groupby("ID_GRUPO"):
        izquierda = bloque.loc[bloque["_LADO"] == "A", "REG_ID"].astype(str)
        derecha = bloque.loc[bloque["_LADO"] == "B", "REG_ID"].astype(str)
        pares.update((x, y) for x in izquierda for y in derecha)
    return evaluar_linkage(
        conjunto,
        pares,
        etiqueta=etiqueta,
        version=__version__,
        nota=nota,
        corroborar=corroborar,
        camino=CAMINO_POR_DEFECTO,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. `argv` permite correrlo desde una prueba sin subproceso."""
    analizador = argparse.ArgumentParser(description=__doc__)
    # El conjunto se ancla a la raíz del repositorio, no a la ubicación del
    # paquete: este script vive en el repositorio y siempre sabe dónde está.
    # Con el paquete instalado, la constante del módulo apuntaría a
    # site-packages y el conjunto quedaría inalcanzable.
    analizador.add_argument("--datos", type=Path, default=RAIZ / "data" / "conformidad")
    analizador.add_argument("--etiqueta", default="base")
    analizador.add_argument("--escenario", choices=("dedup", "linkage", "ambos"), default="ambos")
    analizador.add_argument(
        "--con-geo",
        action="store_true",
        help="añade LATITUD/LONGITUD al esquema (medido: baja la precisión en sedes urbanas)",
    )
    analizador.add_argument(
        "--corroborar",
        action="store_true",
        help=(
            "activa F3: dos identificadores distintos se reúnen si email Y teléfono "
            "coinciden. C09 y C21 solo pasan con esto; el JSON de evidencia registra el flag"
        ),
    )
    analizador.add_argument("--nota", default="")
    analizador.add_argument("--evidencia", type=Path, default=RAIZ / "docs" / "evidencia")
    analizador.add_argument("--importar", type=Path, default=None)
    args = analizador.parse_args(argv)

    if args.importar is not None:
        importar(args.importar, args.datos)
        return 0

    conjunto = cargar_conjunto(args.datos)
    informes = []
    if args.escenario in ("dedup", "ambos"):
        informes.append(
            correr_dedup(conjunto, args.etiqueta, args.con_geo, args.nota, args.corroborar)
        )
    if args.escenario in ("linkage", "ambos"):
        informes.append(
            correr_linkage(conjunto, args.etiqueta, args.con_geo, args.nota, args.corroborar)
        )

    for informe in informes:
        print(informe.resumen())
        destino = informe.guardar(args.evidencia)
        print(f"\n💾 Evidencia: {destino}\n")
    return 0 if all(i.pasa for i in informes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
