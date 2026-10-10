#!/usr/bin/env python
"""conformidad.py — Corre la suite de conformidad de 43 casos.

El banco (`scripts/banco.py`) dice CUÁNTO mejoró. Este dice QUÉ sabe hacer la
librería: cada caso frontera del catálogo aprueba o reprueba por separado, sin
promediarse con nada. Un comportamiento roto que afecta a cuatro casos es
invisible en un F1 sobre 46.000 pares, y aquí sale en rojo.

USO
    python scripts/conformidad.py                      # dedup + linkage, motor multicampo
    python scripts/conformidad.py --corroborar         # con levantamiento de veto (F3)
    python scripts/conformidad.py --camino dedupe_esquema --corroborar --etiqueta corroborado
    python scripts/conformidad.py --camino linkage     # los mismos 43 por api.linkage()
    python scripts/conformidad.py --camino flujo.cruce # … y por flujo.cruce (archivos + arrastre)
    python scripts/conformidad.py --camino todos
    python scripts/conformidad.py --escenario dedup
    python scripts/conformidad.py --importar ruta/Ground_Truth_Multicampo_v1.xlsx

Devuelve código 0 si todos los casos FIRMES aprueban, 1 si alguno reprueba.
Los casos que el catálogo marca `TP_DIFICIL` son deuda declarada y no
reprueban la corrida; se listan aparte para que no se olviden.

LOS TRES CAMINOS (F2.13)
    dedupe_esquema  `api.dedupe_esquema` con el esquema del DICCIONARIO del
                    libro (histórico; el único que tiene `--corroborar`).
    linkage         `api.linkage`, la fachada de producción: una fuente por
                    valor de SRC/FUENTE, confiables según la columna de
                    confianza del conjunto, CIUDAD + TELEFONO + EMAIL como
                    evidencia. Lo que se añadió a linkage() entre 0.12 y
                    0.22 no tenía veredicto por caso hasta aquí.
    flujo.cruce     `flujo.cruce.ejecutar_cruce` sobre las mismas fuentes
                    escritas a CSV, con columnas de arrastre (DIRECCION,
                    DEPARTAMENTO, SECTOR…) que viajan fuera del motor y se
                    re-adjuntan: comprueba que el re-adjunte no cambia el
                    grupo de nadie.
    `--corroborar` y `--con-geo` solo existen en el motor multicampo; pedirlos
    con otro camino (o con `todos`) es un error, no un flag que se ignora: la
    evidencia diría `corroborar: true` sobre un motor que no lo tiene, o
    callaría que linkage()/flujo.cruce nunca recibieron LATITUD/LONGITUD. Para
    la corrida corroborada del camino histórico se invoca el script aparte
    con `--camino dedupe_esquema`. Cada camino escribe su propia evidencia
    (`conformidad_<escenario>[_<camino>]_<etiqueta>.json`) y cada uno tiene su
    línea base: un caso que da distinto entre caminos se registra tal cual.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from record_linkage import __version__
from record_linkage.evaluation.banco import silenciar_pipeline
from record_linkage.evaluation.conformidad import (
    CAMINO_POR_DEFECTO,
    CAMINOS,
    ConjuntoConformidad,
    Informe,
    cargar_conjunto,
    evaluar_dedup,
    evaluar_linkage,
)
from record_linkage.pipeline.errores import ColumnasArrastreError, mensaje_accionable

#: Fuentes que el DICCIONARIO del libro declara confiables (NIT verificado).
#: Para `linkage()`/`flujo.cruce`, donde la confianza es por fuente, una
#: fuente es confiable si está aquí Y, cuando la hoja trae `ES_TRUSTED`, la
#: fila lo dice: las filas de RUES/SUPERSOCIEDADES marcadas `False` (el bloque
#: GEN, que SÍ debe deduplicarse) van a una fuente aparte `<SRC>_SIN_VERIFICAR`.
FUENTES_CONFIABLES = frozenset({"RUES", "SUPERSOCIEDADES"})
SUFIJO_SIN_VERIFICAR = "_SIN_VERIFICAR"

#: Evidencia que `linkage()` recibe además de NIT y nombre: la misma que el
#: esquema multicampo usa (CIUDAD va por `col_ciudad`). Los comparadores
#: `*_signed` canonicalizan formato (CLAUDE.md §5: el exacto no sirve para
#: teléfonos ni correos). Peso 0,10 cada una, sobre {name 0,5, nit 0,5}.
VARIABLES_EXTRA_LINKAGE: tuple[dict[str, Any], ...] = (
    {"column": "TELEFONO", "weight": 0.10, "type": "telefono_signed"},
    {"column": "EMAIL", "weight": 0.10, "type": "email_signed"},
)

#: Columnas que NO participan en la decisión y que `flujo.cruce` arrastra
#: fuera del motor y re-adjunta a la correlativa (F1.8). `REG_ID` va entre
#: ellas porque es la llave con la que se alinea la partición con la verdad.
COLUMNAS_ARRASTRE = ("REG_ID", "DIRECCION", "DEPARTAMENTO", "SECTOR")

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


# ═══════════════════════════════════════════════════════════════════════════
# Los tres caminos: cada uno devuelve ID_GRUPO por fila, alineado con `datos`
# ═══════════════════════════════════════════════════════════════════════════


def _particion_dedupe_esquema(datos: pd.DataFrame, con_geo: bool, corroborar: bool) -> pd.Series:
    """Motor multicampo con el esquema del DICCIONARIO (camino histórico)."""
    from record_linkage.api import dedupe_esquema

    datos = datos.copy()
    for columna in ("LATITUD", "LONGITUD"):
        datos[columna] = pd.to_numeric(datos[columna], errors="coerce")
    resultado = dedupe_esquema(datos, _esquema_conformidad(con_geo, corroborar))
    return pd.Series(resultado.correlativa["ID_GRUPO"].to_numpy(), index=datos.index)


def fuentes_por_confianza(
    datos: pd.DataFrame, col_fuente: str, col_confianza: str | None
) -> tuple[dict[str, pd.DataFrame], set[str]]:
    """Parte el conjunto en fuentes para `linkage()` respetando la confianza por fila.

    `linkage()` entiende la confianza por fuente (no deduplica dentro de una
    fuente confiable). El libro la declara por FILA: en el bloque GEN hay
    registros RUES con `ES_TRUSTED = False` que SÍ deben unirse entre sí.
    Marcar RUES entera como confiable reprobaría GEN; no marcarla dejaría C15
    (RUES–RUES no agrupa) sin la política que mide. La proyección fiel es
    separar las filas no verificadas en `<SRC>_SIN_VERIFICAR`.

    Returns:
        (fuentes, confiables): fuentes en orden de nombre y el subconjunto
        confiable. Sin `col_confianza`, confiables = `FUENTES_CONFIABLES`.
    """
    fuente = datos[col_fuente].astype(str)
    if col_confianza is not None:
        verificada = datos[col_confianza].astype(str).str.strip().str.lower() == "true"
        # Solo las fuentes declaradas confiables se parten; CRM nunca lo fue.
        fuente = fuente.where(
            verificada | ~fuente.isin(FUENTES_CONFIABLES), fuente + SUFIJO_SIN_VERIFICAR
        )
    # La columna de fuente sale del marco: el nombre de la fuente ya la lleva, y
    # `SRC` chocaría con la columna del mismo nombre que escribe el contrato.
    fuentes = {
        str(nombre): bloque.drop(columns=["_FUENTE", col_fuente]).reset_index(drop=True)
        for nombre, bloque in datos.assign(_FUENTE=fuente).groupby("_FUENTE", sort=True)
    }
    confiables = {n for n in fuentes if n in FUENTES_CONFIABLES}
    return fuentes, confiables


def _grupos_desde_correlativa(correlativa: pd.DataFrame, datos: pd.DataFrame) -> pd.Series:
    """ID_GRUPO por fila de `datos`, alineado por REG_ID (la correlativa puede venir reordenada)."""
    grupo = pd.Series(
        correlativa["ID_GRUPO"].to_numpy(), index=correlativa["REG_ID"].astype(str).to_numpy()
    )
    if grupo.index.has_duplicates:
        repetidos = sorted(grupo.index[grupo.index.duplicated()].unique().tolist())[:5]
        raise ValueError(
            mensaje_accionable(
                que_paso=f"la correlativa trae REG_ID repetidos (p. ej. {repetidos})",
                por_que_importa="la partición no se puede alinear con la verdad del conjunto",
                que_hacer=(
                    "compruebe que REG_ID es única por fila en cada fuente antes de "
                    "particionar (o pase `col_id` con una columna que sí lo sea)"
                ),
            )
        )
    return pd.Series(datos["REG_ID"].astype(str).map(grupo).to_numpy(), index=datos.index)


def _particion_linkage(
    datos: pd.DataFrame, col_fuente: str, col_confianza: str | None
) -> tuple[pd.Series, dict[str, Any]]:
    """`api.linkage()` con las fuentes del conjunto; devuelve también qué corrió."""
    from record_linkage.api import linkage

    fuentes, confiables = fuentes_por_confianza(datos, col_fuente, col_confianza)
    with tempfile.TemporaryDirectory(prefix="conformidad_linkage_") as trabajo, _en_silencio():
        resultado = linkage(
            sources=fuentes,
            trusted_sources=confiables,
            col_ciudad="CIUDAD",
            extra_features=list(VARIABLES_EXTRA_LINKAGE),
            work_dir=trabajo,
            skip_reporting=True,
        )
        correlativa = resultado.correlativa
    detalle = {
        "fuentes": {n: len(f) for n, f in fuentes.items()},
        "confiables": sorted(confiables),
        "variables_extra": list(VARIABLES_EXTRA_LINKAGE),
        "perfil": "produccion_estandar",
    }
    return _grupos_desde_correlativa(correlativa, datos), detalle


def _particion_flujo_cruce(
    datos: pd.DataFrame, col_fuente: str, col_confianza: str | None
) -> tuple[pd.Series, dict[str, Any]]:
    """`flujo.cruce.ejecutar_cruce` sobre CSV por fuente, con columnas de arrastre."""
    import json

    from record_linkage import ColumnType, SourceSpec
    from record_linkage.flujo import ConfigCruce, ejecutar_cruce

    fuentes, confiables = fuentes_por_confianza(datos, col_fuente, col_confianza)
    arrastre = [c for c in COLUMNAS_ARRASTRE if c in datos.columns]
    with tempfile.TemporaryDirectory(prefix="conformidad_cruce_") as temporal, _en_silencio():
        carpeta = Path(temporal)
        especificaciones = []
        for nombre, marco in fuentes.items():
            ruta = carpeta / f"{nombre}.csv"
            marco.to_csv(ruta, index=False)
            especificaciones.append(
                SourceSpec(
                    name=nombre,
                    path=ruta,
                    column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
                    optional_column_mapping={
                        "CIUDAD": "CIUDAD",
                        "TELEFONO": "TELEFONO",
                        "EMAIL": "EMAIL",
                        **{c: c for c in arrastre},
                    },
                    delimiter=",",
                    column_types={"NIT": ColumnType.IDENTIFIER},
                )
            )
        config = ConfigCruce(
            fuentes=especificaciones,
            workspace=carpeta / "salida",
            confiables=confiables,
            dir_trabajo=carpeta / "trabajo",
            col_ciudad="CIUDAD",
            variables_extra=list(VARIABLES_EXTRA_LINKAGE),
            filas_smoke=0,
            exportar_excel=False,
        )
        resultado = ejecutar_cruce(config)
        correlativa = resultado.correlativa
        manifiesto = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
        reporte_arrastre = manifiesto["parametros"]["columnas_arrastre"]
    faltan = sorted(set(arrastre) - set(reporte_arrastre["adjuntadas"]))
    if faltan or reporte_arrastre["omitidas"]:
        raise ColumnasArrastreError(
            f"flujo.cruce no re-adjuntó las columnas de arrastre {faltan} "
            f"(omitidas: {reporte_arrastre['omitidas']}); este camino mide justamente "
            "que el arrastre no cambia la partición, así que sin él la medición no es "
            "la pedida",
            que_hacer=(
                "no use esta evidencia; revise `parametros.columnas_arrastre` en el "
                "manifiesto de la corrida y el registro de flujo.cruce para ver por qué "
                "se omitieron, y vuelva a correr `--camino flujo.cruce` cuando las "
                "columnas de COLUMNAS_ARRASTRE existan en el conjunto"
            ),
        )
    detalle = {
        "fuentes": {n: len(f) for n, f in fuentes.items()},
        "confiables": sorted(confiables),
        "variables_extra": list(VARIABLES_EXTRA_LINKAGE),
        "perfil": config.perfil,
        "columnas_arrastre": reporte_arrastre,
    }
    return _grupos_desde_correlativa(correlativa, datos), detalle


@contextmanager
def _en_silencio() -> Iterator[None]:
    """Sin logging del motor, barras de progreso ni avisos: la salida es el informe.

    Las barras (tqdm) se apagan con ``TQDM_DISABLE`` solo mientras dura la
    corrida y el valor previo se restaura al salir: las pruebas cargan este
    script como módulo y fijarlo al importar apagaría tqdm para toda la sesión
    sin que ninguna otra prueba lo supiera. tqdm lee la variable al construir
    cada barra, no al importar, así que basta con fijarla aquí.
    """
    previo = os.environ.get("TQDM_DISABLE")
    os.environ["TQDM_DISABLE"] = "1"
    try:
        with silenciar_pipeline(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            yield
    finally:
        if previo is None:
            os.environ.pop("TQDM_DISABLE", None)
        else:
            os.environ["TQDM_DISABLE"] = previo


def _particion(
    camino: str,
    datos: pd.DataFrame,
    *,
    col_fuente: str,
    col_confianza: str | None,
    con_geo: bool,
    corroborar: bool,
) -> tuple[pd.Series, dict[str, Any]]:
    """Despacha al camino pedido. `corroborar` solo tiene sentido en el multicampo."""
    if camino == CAMINO_POR_DEFECTO:
        return _particion_dedupe_esquema(datos, con_geo, corroborar), {}
    if corroborar:
        raise ValueError(
            mensaje_accionable(
                que_paso=f"se pidió corroborar=True con camino={camino!r}",
                por_que_importa=(
                    "la corroboración por contacto (F3) vive solo en el motor multicampo "
                    f"({CAMINO_POR_DEFECTO}) y una evidencia con corroborar=true sobre "
                    "otro camino mentiría"
                ),
                que_hacer=(
                    "quite corroborar para este camino (C09 y C21 quedan en frontera) "
                    f"o use camino={CAMINO_POR_DEFECTO!r}"
                ),
            )
        )
    if camino == "linkage":
        return _particion_linkage(datos, col_fuente, col_confianza)
    if camino == "flujo.cruce":
        return _particion_flujo_cruce(datos, col_fuente, col_confianza)
    raise ValueError(f"camino desconocido {camino!r}; use uno de {list(CAMINOS)}.")


def correr_dedup(
    conjunto: ConjuntoConformidad,
    etiqueta: str,
    con_geo: bool,
    nota: str,
    corroborar: bool = False,
    camino: str = CAMINO_POR_DEFECTO,
) -> Informe:
    datos = conjunto.dedup_registros.copy()
    grupos, detalle = _particion(
        camino,
        datos,
        col_fuente="SRC",
        col_confianza="ES_TRUSTED",
        con_geo=con_geo,
        corroborar=corroborar,
    )
    informe = evaluar_dedup(
        conjunto,
        grupos.to_numpy(),
        etiqueta=etiqueta,
        version=__version__,
        nota=nota,
        corroborar=corroborar,
        camino=camino,
    )
    return _con_detalle(informe, detalle)


def correr_linkage(
    conjunto: ConjuntoConformidad,
    etiqueta: str,
    con_geo: bool,
    nota: str,
    corroborar: bool = False,
    camino: str = CAMINO_POR_DEFECTO,
) -> Informe:
    """Cruza A con B pasando las dos bases por el mismo motor.

    Se concatenan con una marca de lado y del clustering resultante se leen
    solo los pares que cruzan de A a B: un grupo que une dos registros de A no
    es un cruce, es una deduplicación interna, y este escenario no la mide.
    En los caminos `linkage`/`flujo.cruce` cada lado es una fuente (CRM y
    RUES, según FUENTE) y RUES es la confiable.
    """
    a = conjunto.linkage_a.rename(columns={"REG_ID_A": "REG_ID"}).copy()
    b = conjunto.linkage_b.rename(columns={"REG_ID_B": "REG_ID"}).copy()
    a["_LADO"], b["_LADO"] = "A", "B"
    # REG_ID se repite entre A y B (1…20 y 1…21): el motor necesita una llave única.
    a["REG_ID"], b["REG_ID"] = "A" + a["REG_ID"].astype(str), "B" + b["REG_ID"].astype(str)
    datos = pd.concat([a, b], ignore_index=True)
    grupos, detalle = _particion(
        camino,
        datos,
        col_fuente="FUENTE",
        col_confianza=None,
        con_geo=con_geo,
        corroborar=corroborar,
    )
    marco = datos.assign(_GRUPO=grupos.to_numpy())
    pares: set[tuple[str, str]] = set()
    for _, bloque in marco.groupby("_GRUPO"):
        izquierda = bloque.loc[bloque["_LADO"] == "A", "REG_ID"].str[1:]
        derecha = bloque.loc[bloque["_LADO"] == "B", "REG_ID"].str[1:]
        pares.update((x, y) for x in izquierda for y in derecha)
    informe = evaluar_linkage(
        conjunto,
        pares,
        etiqueta=etiqueta,
        version=__version__,
        nota=nota,
        corroborar=corroborar,
        camino=camino,
    )
    return _con_detalle(informe, detalle)


def _con_detalle(informe: Informe, detalle: dict[str, Any]) -> Informe:
    """La evidencia dice con qué fuentes, confiables y evidencia extra se corrió."""
    from dataclasses import replace

    return replace(informe, extra={**informe.extra, **detalle}) if detalle else informe


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
        help=(
            "añade LATITUD/LONGITUD al esquema (medido: baja la precisión en sedes "
            "urbanas). `--con-geo` solo existe en dedupe_esquema: linkage() y "
            "flujo.cruce no reciben coordenadas por este script"
        ),
    )
    analizador.add_argument(
        "--corroborar",
        action="store_true",
        help=(
            "activa F3: dos identificadores distintos se reúnen si email Y teléfono "
            "coinciden. C09 y C21 solo pasan con esto; el JSON de evidencia registra el flag"
        ),
    )
    analizador.add_argument(
        "--camino",
        choices=(*CAMINOS, "todos"),
        default=CAMINO_POR_DEFECTO,
        help=(
            "camino del motor que produce la partición (F2.13): dedupe_esquema "
            "(multicampo, histórico), linkage (api.linkage, producción), flujo.cruce "
            "(ejecutar_cruce sobre archivos con columnas de arrastre) o todos. "
            "La evidencia lleva el camino en el JSON y en el nombre del archivo. "
            "`todos` no admite `--corroborar` ni `--con-geo`; para la corrida "
            "corroborada use `--camino dedupe_esquema`"
        ),
    )
    analizador.add_argument("--nota", default="")
    analizador.add_argument("--evidencia", type=Path, default=RAIZ / "docs" / "evidencia")
    analizador.add_argument("--importar", type=Path, default=None)
    args = analizador.parse_args(argv)

    if args.importar is not None:
        importar(args.importar, args.datos)
        return 0

    caminos = list(CAMINOS) if args.camino == "todos" else [args.camino]
    if args.corroborar and any(c != CAMINO_POR_DEFECTO for c in caminos):
        analizador.error(
            "--corroborar solo existe en el camino dedupe_esquema. Qué pasó: se pidió "
            f"la corroboración por contacto (F3) para {[c for c in caminos if c != CAMINO_POR_DEFECTO]}. "
            "Por qué importa: linkage() y flujo.cruce no tienen esa perilla y una evidencia "
            "con corroborar=true sobre ellos mentiría. Qué hacer: quite --corroborar para "
            "esos caminos (C09 y C21 quedan en frontera) o use --camino dedupe_esquema."
        )

    if args.con_geo and any(c != CAMINO_POR_DEFECTO for c in caminos):
        analizador.error(
            "--con-geo solo existe en el camino dedupe_esquema. Qué pasó: se pidió "
            f"LATITUD/LONGITUD para {[c for c in caminos if c != CAMINO_POR_DEFECTO]}. "
            "Por qué importa: linkage() y flujo.cruce no reciben coordenadas por este "
            "script y la evidencia no lo registraría: se publicaría como medida con "
            "geo lo que no lo fue. Qué hacer: quite --con-geo o use --camino dedupe_esquema."
        )

    conjunto = cargar_conjunto(args.datos)
    informes = []
    for camino in caminos:
        if args.escenario in ("dedup", "ambos"):
            informes.append(
                correr_dedup(
                    conjunto, args.etiqueta, args.con_geo, args.nota, args.corroborar, camino
                )
            )
        if args.escenario in ("linkage", "ambos"):
            informes.append(
                correr_linkage(
                    conjunto, args.etiqueta, args.con_geo, args.nota, args.corroborar, camino
                )
            )

    for informe in informes:
        print(informe.resumen())
        destino = informe.guardar(args.evidencia)
        print(f"\n💾 Evidencia: {destino}\n")
    return 0 if all(i.pasa for i in informes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
