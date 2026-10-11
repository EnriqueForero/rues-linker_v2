"""Apoyo de las pruebas de ``flujo.vinculacion.tablas``: el estado de una corrida real sobre empresas inventadas.

``construir_tablas`` recibe el estado completo de una corrida (``interno``): las fuentes preparadas, las
grafías de la ancla, los pares candidatos con su nivel final, las decisiones… Casi todo eso lo producen
etapas que viven en otros módulos del motor; aquí se reconstruye desde ``tests/data_sintetica/vinculacion_tablas/``.

Cómo se generaron esos archivos (no se regeneran en las pruebas)
----------------------------------------------------------------
Con el motor ORIGINAL (la celda ``%%writefile`` del notebook 10, commit bab03c2): se escribieron los insumos
de ``apoyo_vinculacion.construir_insumos(carpeta, n_aleatorias=0)`` (solo los casos manuales, semilla 42), se
configuró el escenario ``base`` con ``apoyo_vinculacion.configurar`` y se corrió ``ejecutar`` con
``construir_tablas`` sustituida por un envoltorio que copia sus argumentos y llama a la original. De esa
captura (justo antes de ``construir_tablas``) se guardó cada pieza del ``interno`` (``prep``, ``crudos`` con el
índice reiniciado, ``graf``, ``L``, ``P``, ``un_a``, ``pend_a``, ``ent_dest``, ``un_dest``, ``dec``, ``ref``,
``disc``), los ``val`` y los avisos como parquet (``*.parquet``, sin ``attrs``: los vetos son un conjunto y no
caben), y lo que no es una tabla (``ent``, ``ent_auto``, ``uniones_emp``, los vetos de ``graf.attrs`` y los
avisos) en ``estado.json``. Todas las empresas son inventadas. Regenerarlos exige el motor original (fuera del
repositorio) y vuelve a fijar los valores esperados de ``test_vinculacion_tablas.py``.

Qué se reconstruye aquí y no se guarda: la configuración (``apoyo_vinculacion.configurar`` con archivos
vacíos: ``construir_tablas`` solo lee sus huellas SHA-256), el ``Normalizador`` y el ``Comparador``
(se construyen con el paquete nuevo, igual que ``ejecutar``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, NamedTuple

import apoyo_vinculacion as av
import numpy as np
import pandas as pd

import record_linkage.flujo.vinculacion as paquete
from record_linkage.flujo.vinculacion.comparar import Comparador
from record_linkage.flujo.vinculacion.config import ConfigVinculacion
from record_linkage.flujo.vinculacion.normalizar import Normalizador
from record_linkage.flujo.vinculacion.tablas import construir_tablas

CARPETA_FIXTURE = Path(__file__).resolve().parent / "data_sintetica" / "vinculacion_tablas"

#: Duración que se le pasa a ``construir_tablas`` (aparece en PARAMETROS, que las huellas ignoran).
SEGUNDOS = 12.5


class Corrida(NamedTuple):
    """Lo que recibe ``construir_tablas``: configuración, estado interno, validación y avisos."""

    cfg: ConfigVinculacion
    interno: dict
    val: pd.DataFrame
    avisos: list

    def copia(self) -> Corrida:
        """Copia profunda de las tablas (el ``Normalizador`` y el ``Comparador`` se comparten)."""
        interno = {
            k: v if k in ("nz", "comp") else copy.deepcopy(v) for k, v in self.interno.items()
        }
        return Corrida(self.cfg, interno, self.val.copy(), list(self.avisos))

    def construir(self) -> Any:
        """``construir_tablas`` sobre una copia de esta corrida: el ``Resultado``."""
        c = self.copia()
        return construir_tablas(c.cfg, c.interno, c.val, c.avisos, SEGUNDOS)


def _leer(nombre: str) -> pd.DataFrame:
    return pd.read_parquet(CARPETA_FIXTURE / f"{nombre}.parquet")


def _configurar(carpeta: Path) -> ConfigVinculacion:
    """El escenario ``base`` sobre archivos vacíos con los nombres de la corrida original."""
    archivos = [
        carpeta / n
        for n in ("ancla.xlsx", "descargas.xlsx", "decisiones_1.csv", "decisiones_2.csv")
    ]
    for a in archivos:
        a.write_bytes(b"")
    ins = av.InsumosVinculacion(carpeta, *archivos)
    return av.configurar(paquete, ins, escenario="base")


def cargar_corrida(carpeta_trabajo: Path) -> Corrida:
    """Reconstruye la corrida guardada en ``CARPETA_FIXTURE``; escribe sus archivos vacíos en ``carpeta_trabajo``."""
    cfg = _configurar(carpeta_trabajo)
    estado = json.loads((CARPETA_FIXTURE / "estado.json").read_text(encoding="utf-8"))
    claves = [cfg.ancla.clave, *(d.clave for d in cfg.destinos)]
    prep = {k: _leer(f"prep_{k}") for k in claves}
    graf = _leer("graf")
    graf.attrs["vetos"] = {tuple(v) for v in estado["vetos"]}
    graf.attrs["desconocidas"] = [tuple(v) for v in estado["desconocidas"]]
    nz = Normalizador(cfg)
    corpus = pd.concat(
        [prep[k][c] for k in prep for c in ("NOMBRE_NORM", "ALIAS_NORM", "GRUPO_NORM")]
    ).tolist()
    interno = {
        "nz": nz,
        "comp": Comparador(nz, corpus, cfg),
        "prep": prep,
        "crudos": {k: _leer(f"crudo_{k}") for k in claves},
        "graf": graf,
        "ent": np.array(estado["ent"], dtype=np.int64),
        "ent_auto": np.array(estado["ent_auto"], dtype=np.int64),
        "L": _leer("pares"),
        "P": _leer("candidatos"),
        "un_a": _leer("uniones_ancla"),
        "pend_a": _leer("pendientes_ancla"),
        "uniones_emp": [tuple(u) for u in estado["uniones_emp"]],
        "ent_dest": {d.clave: _leer(f"entidades_{d.clave}") for d in cfg.destinos},
        "un_dest": {d.clave: _leer(f"uniones_{d.clave}") for d in cfg.destinos},
        "dec": _leer("decisiones"),
        "ref": _leer("referencia"),
        "disc": _leer("discrepancias"),
    }
    return Corrida(cfg, interno, _leer("validacion"), list(estado["avisos"]))


def huellas(resultado: Any) -> dict[str, str]:
    """Huella de cada tabla del resultado (``apoyo_vinculacion.huella_tabla``), sin lo que cambia por corrida.

    De PARAMETROS se quitan la versión de la librería y las filas con la huella de un archivo
    (``sha256 ·``, ``decisiones ·``, ``referencia ·``): aquí los archivos están vacíos. De CONTROL_QA, la
    fila de la versión de la librería.
    """
    salida = {}
    for nombre, tabla in resultado.tablas.items():
        if nombre == "PARAMETROS":
            texto = tabla["PARAMETRO"].astype(str)
            tabla = tabla[
                ~texto.str.startswith(("sha256 ·", "decisiones ·", "referencia ·"))
                & (texto != "rues_linker")
            ]
        if nombre == "CONTROL_QA":
            tabla = tabla[~tabla["CONTROL"].astype(str).str.startswith("rues-linker ≥")]
        salida[nombre] = av.huella_tabla(tabla, nombre)
    salida["resumen"] = av.huella_tabla(resultado.resumen)
    salida["por_pais"] = av.huella_tabla(resultado.por_pais)
    return salida


def fila_de(tabla: pd.DataFrame, columna: str, valor: object) -> pd.Series:
    """La ÚNICA fila de ``tabla`` con ``tabla[columna] == valor`` (falla si hay cero o más de una)."""
    filas = tabla[tabla[columna] == valor]
    assert len(filas) == 1, f"{len(filas)} filas con {columna}={valor!r}"
    return filas.iloc[0]


# ── variantes de la corrida: la misma corrida con UNA cosa cambiada ─────────────────
def posicion(corrida: Corrida, fuente: str, id_registro: str) -> int:
    """Posición, en la tabla ``prep`` de ``fuente``, del registro con ese ``ID_REGISTRO``."""
    return corrida.interno["prep"][fuente]["ID_REGISTRO"].tolist().index(id_registro)


def sin_puntaje(corrida: Corrida, pares: list[tuple[str, str]]) -> Corrida:
    """Deja sin ``SCORE_RUES_LINKER`` los pares ``(fuente, id_registro)``.

    Así llegan a ``construir_tablas`` los pares que solo existen por una decisión de revisión: la tabla
    los puntúa para dejar constancia.
    """
    c = corrida.copia()
    pares_tabla = c.interno["L"]
    for fuente, id_registro in pares:
        mascara = (pares_tabla["FUENTE_DESTINO"] == fuente) & (
            pares_tabla["pos_b"] == posicion(c, fuente, id_registro)
        )
        assert mascara.sum() == 1, (fuente, id_registro)
        pares_tabla.loc[mascara, "SCORE_RUES_LINKER"] = np.nan
    return c


def sin_pares(corrida: Corrida) -> Corrida:
    """La corrida sin ningún par ancla × destino (la tabla de pares vacía, con sus columnas)."""
    c = corrida.copia()
    c.interno["L"] = c.interno["L"].iloc[:0].copy()
    return c


def sin_destinos(corrida: Corrida) -> Corrida:
    """Un cruce sin destinos (solo deduplicación): sin pares, candidatos, entidades ni validación."""
    c = corrida.copia()
    cfg = copy.deepcopy(c.cfg)
    cfg.destinos = []
    interno = c.interno
    interno["L"] = interno["L"].iloc[:0].copy()
    interno["P"] = interno["P"].iloc[:0].copy()
    interno["disc"] = interno["disc"].iloc[:0].copy()
    interno["ent_dest"], interno["un_dest"] = {}, {}
    return Corrida(cfg, interno, c.val.iloc[:0].copy(), c.avisos)


def sin_decisiones(corrida: Corrida) -> Corrida:
    """La corrida sin ninguna decisión de revisión leída (las tablas ``dec`` y ``ref`` vacías)."""
    c = corrida.copia()
    c.interno["dec"] = c.interno["dec"].iloc[:0].copy()
    return c


def con_marca(corrida: Corrida, marca: str) -> Corrida:
    """La corrida con otra ``marca_sin_validar`` (``''`` la apaga: ninguna decisión es asistida)."""
    c = corrida.copia()
    cfg = copy.deepcopy(c.cfg)
    cfg.marca_sin_validar = marca
    return Corrida(cfg, c.interno, c.val, c.avisos)


def con_pendiente(corrida: Corrida, clave_a: str, clave_b: str) -> Corrida:
    """La corrida con un posible duplicado más entre dos grafías de la ancla (por su ``CLAVE`` ``país|nombre``)."""
    c = corrida.copia()
    claves = c.interno["graf"]["CLAVE"].tolist()
    pos_a, pos_b = sorted((claves.index(clave_a), claves.index(clave_b)))
    nueva = pd.DataFrame(
        [[pos_a, pos_b, "nombres muy parecidos (score 0.90)"]], columns=["pos_a", "pos_b", "MOTIVO"]
    )
    c.interno["pend_a"] = pd.concat([c.interno["pend_a"], nueva], ignore_index=True)
    return c


def con_registro(corrida: Corrida, fuente: str, id_registro: str, **campos: str) -> Corrida:
    """La corrida con otros valores en columnas de ``prep`` (WEB, TELEFONO, EMAIL, DIRECCION…) de UN registro."""
    c = corrida.copia()
    prep = c.interno["prep"][fuente]
    fila = prep["ID_REGISTRO"] == id_registro
    assert fila.sum() == 1, id_registro
    for columna, valor in campos.items():
        prep.loc[fila, columna] = valor
    return c


def con_destino(corrida: Corrida, clave: str, **atributos: Any) -> Corrida:
    """La corrida con otros atributos en la ``Fuente`` destino ``clave`` (``etiqueta``, ``nombres_contexto``…)."""
    c = corrida.copia()
    cfg = copy.deepcopy(c.cfg)
    destino = next(d for d in cfg.destinos if d.clave == clave)
    for atributo, valor in atributos.items():
        setattr(destino, atributo, valor)
    return Corrida(cfg, c.interno, c.val, c.avisos)


def con_ancla(
    corrida: Corrida, valores: dict[tuple[str, int], object] | None = None, **atributos: Any
) -> Corrida:
    """La corrida con otros atributos en la ``Fuente`` ancla y/o otros valores en la ancla tal como se leyó.

    ``valores`` va ``{(columna, FILA_EXCEL): valor}`` y cambia ``crudos[ancla]`` (la que lee ``construir_tablas``).
    """
    c = corrida.copia()
    cfg = copy.deepcopy(c.cfg)
    for atributo, valor in atributos.items():
        setattr(cfg.ancla, atributo, valor)
    crudo = c.interno["crudos"][cfg.ancla.clave]
    for (columna, fila_excel), valor in (valores or {}).items():
        crudo[columna] = crudo[columna].astype(
            object
        )  # como lo deja la lectura: cualquier valor cabe
        crudo.loc[crudo["FILA_EXCEL"] == fila_excel, columna] = valor
    return Corrida(cfg, c.interno, c.val, c.avisos)


def sin_clave_de_grafia(
    corrida: Corrida, grafia_ancla: str, clave_a_dec: str | None = None
) -> Corrida:
    """Borra ``CLAVE_GRAFIA`` de los pares de esa grafía y fija su ``CLAVE_A_DEC`` (ausente si es ``None``).

    Así llegan a ``construir_tablas`` los pares que no traen la clave de su grafía: la tabla la recupera de la
    decisión o, si no hay decisión, de la grafía que representa a la empresa.
    """
    c = corrida.copia()
    pares = c.interno["L"]
    mascara = pares["GRAFIA_ANCLA"] == grafia_ancla
    assert mascara.any(), grafia_ancla
    pares.loc[mascara, "CLAVE_GRAFIA"] = ""
    pares.loc[mascara, "CLAVE_A_DEC"] = np.nan if clave_a_dec is None else clave_a_dec
    return c


def par_de_decision_de_otra_grafia(corrida: Corrida, grafia_ancla: str, otra_clave: str) -> Corrida:
    """Pasa los pares de ``grafia_ancla`` a la grafía ``otra_clave`` de la MISMA empresa y los deja sin puntaje.

    Así llega a ``construir_tablas`` un par que solo existe por una decisión sobre una grafía que NO es la
    representante de su empresa, vinculado como GRUPO de confianza MEDIA.
    """
    c = corrida.copia()
    pares = c.interno["L"]
    clave_nueva = otra_clave
    posicion_nueva = c.interno["graf"]["CLAVE"].tolist().index(clave_nueva)
    mascara = pares["GRAFIA_ANCLA"] == grafia_ancla
    assert mascara.any(), grafia_ancla
    pares.loc[mascara, "g"] = posicion_nueva
    pares.loc[mascara, "CLAVE_GRAFIA"] = clave_nueva
    pares.loc[mascara, "GRAFIA_ANCLA"] = clave_nueva.split("|", 1)[1]
    pares.loc[mascara, "SCORE_RUES_LINKER"] = np.nan
    pares.loc[mascara, ["NIVEL_FINAL", "CONFIANZA"]] = ["GRUPO", "MEDIA"]
    pares.loc[mascara, "ESTADO"] = "VINCULADO (automático B · revisar)"
    return c


def con_par(corrida: Corrida, fuente: str, id_registro: str, **campos: Any) -> Corrida:
    """La corrida con otros valores en columnas del PAR (``L``) de UN registro destino: ``CONFIANZA``, ``NIVEL_FINAL``…"""
    c = corrida.copia()
    pares = c.interno["L"]
    mascara = (pares["FUENTE_DESTINO"] == fuente) & (
        pares["pos_b"] == posicion(c, fuente, id_registro)
    )
    assert mascara.sum() == 1, (fuente, id_registro)
    for columna, valor in campos.items():
        pares.loc[mascara, columna] = valor
    return c


def con_grafia(corrida: Corrida, clave: str, **campos: Any) -> Corrida:
    """La corrida con otros valores en columnas de UNA grafía de la ancla (``graf``): ``TRUNCADO``, ``NOMBRE_NORM``…"""
    c = corrida.copia()
    graf = c.interno["graf"]
    mascara = graf["CLAVE"] == clave
    assert mascara.sum() == 1, clave
    for columna, valor in campos.items():
        graf.loc[mascara, columna] = valor
    return c


def sin_pais_en_destino(corrida: Corrida, fuente: str, iso3: str) -> Corrida:
    """La corrida en que los registros de ``fuente`` dejan de tener evidencia del país ``iso3``."""
    c = corrida.copia()
    prep = c.interno["prep"][fuente]
    prep["PAISES_EVIDENCIA"] = [
        "|".join(p for p in v.split("|") if p != iso3) for v in prep["PAISES_EVIDENCIA"]
    ]
    return c


def con_decision(corrida: Corrida, clave_a: str, clave_b: str, **campos: Any) -> Corrida:
    """La corrida con otros valores (``AUTOR``, ``DECISION``…) en la decisión DUPLICADO de ese par de grafías."""
    c = corrida.copia()
    dec = c.interno["dec"]
    mascara = (
        (dec["TIPO"] == "DUPLICADO") & (dec["CLAVE_A"] == clave_a) & (dec["CLAVE_B"] == clave_b)
    )
    assert mascara.sum() == 1, (clave_a, clave_b)
    for columna, valor in campos.items():
        dec.loc[mascara, columna] = valor
    return c


def con_decision_repetida(corrida: Corrida, clave_a: str, clave_b: str, **campos: Any) -> Corrida:
    """La corrida con una SEGUNDA decisión DUPLICADO sobre el mismo par (al final de ``dec``) con otros valores."""
    c = corrida.copia()
    dec = c.interno["dec"]
    mascara = (
        (dec["TIPO"] == "DUPLICADO") & (dec["CLAVE_A"] == clave_a) & (dec["CLAVE_B"] == clave_b)
    )
    assert mascara.sum() == 1, (clave_a, clave_b)
    nueva = dec[mascara].copy()
    for columna, valor in campos.items():
        nueva[columna] = valor
    c.interno["dec"] = pd.concat([dec, nueva], ignore_index=True)
    return c


def con_union_de_destino(
    corrida: Corrida, fuente: str, id_a: str, id_b: str, regla: str
) -> Corrida:
    """La corrida con una regla de unión más entre dos registros de ``fuente`` (al final de ``un_dest``)."""
    c = corrida.copia()
    union = pd.DataFrame(
        {
            "pos_a": [posicion(c, fuente, id_a)],
            "pos_b": [posicion(c, fuente, id_b)],
            "REGLA": [regla],
        }
    )
    c.interno["un_dest"][fuente] = pd.concat(
        [c.interno["un_dest"][fuente], union], ignore_index=True
    )
    return c


def con_empresas_unidas(corrida: Corrida, clave_a: str, clave_b: str, regla: str) -> Corrida:
    """Las empresas de dos grafías de la ancla pasan a ser UNA, unidas por ``regla`` (``uniones_emp``).

    Así llega a ``construir_tablas`` una fusión por empresa común (dos grafías que son la misma empresa que un
    mismo registro destino), que las pruebas no obtienen de la corrida guardada. Las dos empresas no deben
    tener pares ni decisiones.
    """
    c = corrida.copia()
    graf, ent = c.interno["graf"], c.interno["ent"]
    claves = graf["CLAVE"].tolist()
    pos_a, pos_b = claves.index(clave_a), claves.index(clave_b)
    union = (int(graf["_COMP"].iat[pos_a]), int(graf["_COMP"].iat[pos_b]), regla)
    ent[ent == ent[pos_b]] = ent[pos_a]
    c.interno["uniones_emp"] = [*c.interno["uniones_emp"], union]
    return c


def con_pares_puntuados(corrida: Corrida, puntaje: float) -> Corrida:
    """La corrida en que todos los pares traen ``SCORE_RUES_LINKER`` (a los que no lo traían se les pone ``puntaje``)."""
    c = corrida.copia()
    pares = c.interno["L"]
    pares["SCORE_RUES_LINKER"] = pares["SCORE_RUES_LINKER"].fillna(puntaje)
    return c


def sin_columnas_de_pares(corrida: Corrida, *columnas: str) -> Corrida:
    """La corrida con la tabla de pares (``L``) sin esas columnas: la tabla las repone o las calcula."""
    c = corrida.copia()
    c.interno["L"] = c.interno["L"].drop(columns=list(columnas))
    return c


def sin_nada_que_deduplicar_ni_revisar(corrida: Corrida) -> Corrida:
    """Un cruce sin destinos en que cada grafía de la ancla es su propia empresa, sin pendientes ni decisiones."""
    c = sin_destinos(corrida)
    interno = c.interno
    interno["ent"] = np.arange(len(interno["ent"]))
    for clave in ("pend_a", "un_a", "dec"):
        interno[clave] = interno[clave].iloc[:0].copy()
    return c
