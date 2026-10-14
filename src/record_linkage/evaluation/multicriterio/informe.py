"""Informe: las tablas que una persona mira, OFF contra ON en una llamada y la exportación.

:func:`resumen` y :func:`tabla_resumen` (una fila por configuración; ``LEYENDA_RESUMEN`` explica
cada columna), :func:`describir_libro` (qué cifras va a haber, antes de correr),
:func:`tabla_pares_que_cambian` (cada par que cambia de decisión con los valores de las dos filas),
:class:`ComparacionOffOn` y :func:`comparar_off_on` (lo que usa el notebook 13) y
:func:`exportar_comparacion` (Excel + JSON). Parte del paquete ``evaluation.multicriterio``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.evaluation.multicriterio.configuraciones import (
    CONFIGURACION_BASE,
    NOMBRE_BASE,
    VARIABLES_A_MEDIR,
    Configuracion,
    validar_configuraciones,
)
from record_linkage.evaluation.multicriterio.datos import (
    caso_por_id,
    marco_registros,
    pares_de_prueba,
)
from record_linkage.evaluation.multicriterio.medicion import (
    Medicion,
    aporte_como_tabla,
    aporte_por_variable,
    comparar_con_base,
    medicion_como_dict,
    medir_configuracion,
)
from record_linkage.evaluation.plantilla_base_real import LibroPlantilla

#: Las columnas que se muestran, lado a lado, de cada par que cambia de decisión.
COLUMNAS_AUDITORIA: tuple[str, ...] = (
    "RAZON_SOCIAL",
    "NIT",
    "MUNICIPIO",
    "TELEFONO",
    "EMAIL",
    "DIRECCION",
)
#: Qué significa cada columna del :func:`resumen`, en lenguaje llano (el notebook la imprime).
LEYENDA_RESUMEN = """Leyenda del resumen:
  GRUPOS = empresas distintas que el motor cree ver · PARES_PREDICHOS = parejas de registros que une
  PRECISION / RECALL / F1 / FP / FN = por pares contra VERDAD_GRUPOS (FP = parejas unidas que no son la
  misma empresa; FN = parejas de la misma empresa que quedaron separadas) · CASOS = casos de la hoja
  CASOS acertados · ETIQUETAS_P / ETIQUETAS_R = precision y recall contra ETIQUETAS_PARES (solo los
  pares etiquetados; por regimen y con intervalo de Wilson 95 %, el margen de error con pocos pares, en
  la tabla siguiente y en el JSON) · HUELLA = identificador de la particion: misma huella = resultado
  identico · regimen = con NIT en los dos registros (CON_ID), en ninguno (SIN_ID) o en uno (MIXTO)"""


# ---------------------------------------------------------------------------
# Tablas: resumen por configuración y pares que cambian con sus valores
# ---------------------------------------------------------------------------


def resumen(mediciones: Sequence[Medicion]) -> pd.DataFrame:
    """Una fila por configuración: grupos, pares y, si hay verdad o etiquetas, las cifras."""
    filas = []
    for m in mediciones:
        fila: dict[str, object] = {
            "CONFIGURACION": m.configuracion.nombre,
            "EXTRAS": m.configuracion.extras_en_texto(),
            "GRUPOS": m.grupos_predichos,
            "PARES_PREDICHOS": len(m.predichos),
        }
        if m.pares is not None:
            fila.update(
                {
                    "PRECISION": m.pares["precision"],
                    "RECALL": m.pares["recall"],
                    "F1": m.pares["f1"],
                    "FP": m.pares["fp"],
                    "FN": m.pares["fn"],
                }
            )
        if m.tabla_casos is not None:
            fila["CASOS"] = f"{m.casos_acertados}/{len(m.tabla_casos)}"
        if m.etiquetas is not None:
            global_ = m.etiquetas.get("global", {})
            assert isinstance(global_, dict)
            fila["ETIQUETAS_P"] = _redondeo4(global_.get("precision"))
            fila["ETIQUETAS_R"] = _redondeo4(global_.get("recall"))
        fila["HUELLA"] = m.huella[:12]
        filas.append(fila)
    return pd.DataFrame(filas)


def _redondeo4(valor: object) -> float | None:
    """Las cifras de pares salen con 4 decimales de ``metricas_pares``; las de etiquetas, con todos
    los que da el flotante. En la misma tabla se muestran igual."""
    return None if valor is None else round(float(str(valor)), 4)


def describir_libro(libro: LibroPlantilla, etiquetas: pd.DataFrame | None) -> str:
    """Una línea con las fuentes y, en lenguaje llano, qué cifras va a haber y cuáles no: sin
    VERDAD_GRUPOS no hay calidad (solo los pares que cambian); sin CASO/CASOS hay precisión y recall
    por pares pero no tabla de casos ni aporte; sin ETIQUETAS_PARES no hay ETIQUETAS_P/R."""
    lineas = [
        "Fuentes: "
        + ", ".join(f"{f} ({len(df)})" for f, df in libro.fuentes.items())
        + f" · con VERDAD_GRUPOS: {'sí' if libro.con_verdad else 'no'}"
        + f" · con CASOS: {'sí' if libro.con_casos else 'no'}"
        + " · etiquetas: "
        + (f"{len(etiquetas)} pares evaluables" if etiquetas is not None else "no hay")
    ]
    if not libro.con_verdad:
        lineas.append(
            "  Sin VERDAD_GRUPOS: no hay cifras de calidad (precisión, recall, casos); lea los pares "
            "que cambian de decisión, que es lo que una persona revisa."
        )
    elif not libro.con_casos:
        lineas.append(
            "  Sin hoja CASOS (o sin columna CASO en VERDAD_GRUPOS): hay precisión y recall por pares, "
            "pero no tabla de casos ni aporte por variable."
        )
    if etiquetas is None:
        lineas.append(
            "  Sin ETIQUETAS_PARES: no hay ETIQUETAS_P / ETIQUETAS_R ni tabla por régimen."
        )
    return "\n".join(lineas)


def tabla_resumen(mediciones: Sequence[Medicion]) -> str:
    """El resumen en Markdown (para la consola y la guía)."""
    tabla = resumen(mediciones)
    columnas = list(tabla.columns)
    lineas = ["| " + " | ".join(columnas) + " |", "|" + "---|" * len(columnas)]
    for fila in tabla.itertuples(index=False):
        celdas = []
        for columna, valor in zip(columnas, fila, strict=True):
            if columna in ("PRECISION", "RECALL", "F1", "ETIQUETAS_P", "ETIQUETAS_R"):
                celdas.append("" if valor is None or pd.isna(valor) else f"{float(valor):.4f}")
            elif columna == "CONFIGURACION":
                celdas.append(f"`{valor}`")
            else:
                celdas.append("" if valor is None else str(valor))
        lineas.append("| " + " | ".join(celdas) + " |")
    return "\n".join(lineas)


def _columnas_de_auditoria(otra: Medicion, registros: pd.DataFrame) -> list[str]:
    """Las de :data:`COLUMNAS_AUDITORIA` más las extras de la configuración, en ese orden, sin
    repetir y solo las que existen en los registros."""
    pedidas = list(COLUMNAS_AUDITORIA) + [
        str(f["column"]) for f in otra.configuracion.extra_features
    ]
    salida: list[str] = []
    for columna in pedidas:
        if columna in registros.columns and columna not in salida:
            salida.append(columna)
    return salida


def tabla_pares_que_cambian(
    base: Medicion,
    otra: Medicion,
    registros: pd.DataFrame,
    columnas: Sequence[str] | None = None,
    caso_por_id: pd.Series | None = None,
) -> pd.DataFrame:
    """Un par por fila: si las extras lo UNEN (``se une``) o lo SEPARAN (``se separa``), si es
    verdadero (``sí``/``no``; vacío sin verdad), el caso de cada lado y los valores de las dos filas
    en ``columnas`` (``RAZON_SOCIAL_1``, ``RAZON_SOCIAL_2``, ``TELEFONO_1``…). Es la tabla que una
    persona revisa a ojo; ``registros`` es :func:`marco_registros`."""
    columnas = list(columnas) if columnas is not None else _columnas_de_auditoria(otra, registros)
    faltan = [c for c in columnas if c not in registros.columns]
    if faltan:
        raise ValueError(
            f"Las columnas {faltan} no están en los registros ({list(registros.columns)})."
        )
    filas: list[dict[str, object]] = []
    for cambio, pares in (
        ("se une", otra.predichos - base.predichos),
        ("se separa", base.predichos - otra.predichos),
    ):
        for a, b in sorted(pares):
            fila: dict[str, object] = {"CAMBIO": cambio, "ID_1": a, "ID_2": b}
            if otra.verdaderos is None:
                fila["VERDADERO"] = ""
            else:
                fila["VERDADERO"] = "sí" if (a, b) in otra.verdaderos else "no"
            if caso_por_id is not None:
                fila["CASO_1"] = str(caso_por_id.get(a, ""))
                fila["CASO_2"] = str(caso_por_id.get(b, ""))
            for columna in columnas:
                fila[f"{columna}_1"] = _valor(registros, a, columna)
                fila[f"{columna}_2"] = _valor(registros, b, columna)
            filas.append(fila)
    encabezado = ["CAMBIO", "ID_1", "ID_2", "VERDADERO"]
    if caso_por_id is not None:
        encabezado += ["CASO_1", "CASO_2"]
    for columna in columnas:
        encabezado += [f"{columna}_1", f"{columna}_2"]
    return pd.DataFrame(filas, columns=encabezado)


def _valor(registros: pd.DataFrame, id_registro: str, columna: str) -> str:
    if id_registro not in registros.index:
        return ""
    valor = registros.at[id_registro, columna]
    return "" if valor is None or (isinstance(valor, float) and np.isnan(valor)) else str(valor)


# ---------------------------------------------------------------------------
# OFF contra ON en una llamada (lo que usa el notebook 13) y su exportación
# ---------------------------------------------------------------------------


@dataclass
class ComparacionOffOn:
    """El motor sin extras (``base``) y con ellas (``con``) sobre el mismo libro, más las tablas
    que una persona mira: ``resumen``, ``pares_que_cambian`` y, con CASO y CASOS, ``aporte``
    (``con.tabla_etiquetas`` trae P y R por régimen con Wilson 95 % si hubo etiquetas)."""

    libro: LibroPlantilla
    base: Medicion
    con: Medicion
    resumen: pd.DataFrame
    pares_que_cambian: pd.DataFrame
    aporte: pd.DataFrame | None

    @property
    def mediciones(self) -> tuple[Medicion, Medicion]:
        return (self.base, self.con)


def _variables_a_medir(
    extra_features: Sequence[dict[str, object]],
) -> tuple[tuple[str, str], ...]:
    """Las de :data:`VARIABLES_A_MEDIR` más las que la configuración declara con su tipo."""
    salida = list(VARIABLES_A_MEDIR)
    for f in extra_features:
        par = (str(f["column"]), str(f["type"]))
        if par not in salida:
            salida.append(par)
    return tuple(salida)


def comparar_off_on(
    libro: LibroPlantilla,
    etiquetas: pd.DataFrame | None,
    extra_features: Sequence[dict[str, object]],
    dir_trabajo: str | Path,
    nombre: str = "con_extras",
    ajustes_perfil: dict[str, object] | None = None,
) -> ComparacionOffOn:
    """Corre el motor sin extras y con ``extra_features`` (nombres de columna de la plantilla,
    ``MUNICIPIO`` incluido) sobre ``libro`` y arma las tablas. Valida antes de correr."""
    if not extra_features:
        raise ValueError(
            "extra_features está vacío: no hay nada que comparar con la base. Declare al menos "
            'una variable, p. ej. {"column": "EMAIL", "weight": 0.10, "type": "email_signed"}.'
        )
    config = Configuracion(
        nombre,
        "las variables extra declaradas",
        tuple(dict(f) for f in extra_features),
        dict(ajustes_perfil or {}),
    )
    columnas = {c for df in libro.fuentes.values() for c in df.columns}
    validar_configuraciones((CONFIGURACION_BASE, config), columnas)
    dir_trabajo = Path(dir_trabajo)
    base = medir_configuracion(libro, etiquetas, CONFIGURACION_BASE, dir_trabajo / NOMBRE_BASE)
    con = medir_configuracion(libro, etiquetas, config, dir_trabajo / nombre)
    casos = caso_por_id(libro.verdad)
    con.cambios_vs_base = comparar_con_base(base, con, casos)
    registros = marco_registros(libro)
    aporte = None
    if libro.con_casos:
        assert libro.verdad is not None and libro.casos is not None
        positivos, negativos = pares_de_prueba(libro.verdad, libro.casos, etiquetas)
        aporte = aporte_como_tabla(
            aporte_por_variable(registros, positivos, negativos, _variables_a_medir(extra_features))
        )
    return ComparacionOffOn(
        libro=libro,
        base=base,
        con=con,
        resumen=resumen((base, con)),
        pares_que_cambian=tabla_pares_que_cambian(base, con, registros, caso_por_id=casos),
        aporte=aporte,
    )


def _casos_lado_a_lado(base: Medicion, con: Medicion) -> pd.DataFrame | None:
    if base.tabla_casos is None or con.tabla_casos is None:
        return None
    izquierda = base.tabla_casos[["CASO", "ESPERADO", "ACERTO", "DETALLE"]].rename(
        columns={"ACERTO": "ACERTO_SIN_EXTRAS", "DETALLE": "DETALLE_SIN_EXTRAS"}
    )
    derecha = con.tabla_casos[["CASO", "ACERTO", "DETALLE"]].rename(
        columns={"ACERTO": "ACERTO_CON_EXTRAS", "DETALLE": "DETALLE_CON_EXTRAS"}
    )
    return izquierda.merge(derecha, on="CASO", how="left")


def exportar_comparacion(
    comparacion: ComparacionOffOn, carpeta: str | Path, nombre: str
) -> list[Path]:
    """Escribe ``<carpeta>/<nombre>.xlsx`` (hojas RESUMEN, PARES_QUE_CAMBIAN, CORRELATIVA_ON,
    CORRELATIVA_OFF y, con verdad, CASOS y APORTE; con las primitivas del estándar, que neutralizan
    fórmulas) y ``<carpeta>/<nombre>.json`` (cifras, ids y configuración; ningún dato de registro).
    Devuelve las rutas escritas."""
    from record_linkage import __version__
    from record_linkage.exporters.escritor import exportar_vistas

    vistas: dict[str, pd.DataFrame] = {
        "RESUMEN": comparacion.resumen,
        "PARES_QUE_CAMBIAN": comparacion.pares_que_cambian,
        "CORRELATIVA_ON": comparacion.con.correlativa,
        "CORRELATIVA_OFF": comparacion.base.correlativa,
    }
    casos = _casos_lado_a_lado(comparacion.base, comparacion.con)
    if casos is not None:
        vistas["CASOS"] = casos
    if comparacion.aporte is not None:
        vistas["APORTE"] = comparacion.aporte
    carpeta = Path(carpeta)
    escritas = exportar_vistas(vistas, carpeta, nombre, libro=True)
    casos_ids = caso_por_id(comparacion.libro.verdad)
    salida = {
        "version": __version__,
        "fecha": date.today().isoformat(),
        "registros": len(comparacion.base.correlativa),
        "fuentes": {f: len(df) for f, df in comparacion.libro.fuentes.items()},
        "con_verdad": comparacion.libro.con_verdad,
        "con_casos": comparacion.libro.con_casos,
        "configuraciones": [medicion_como_dict(m, casos_ids) for m in comparacion.mediciones],
        "pares_que_cambian": len(comparacion.pares_que_cambian),
    }
    ruta_json = carpeta / f"{nombre}.json"
    ruta_json.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    return [*escritas, ruta_json]
