"""record_linkage.exporters.escritor — El escritor único de la carpeta del estándar (F1.10).

Qué hace
--------
``escribir_resultado`` toma un ``ResultadoLinkage`` y deja UNA carpeta con la
forma exacta del estándar de salida (``ESTANDAR_SALIDA``)::

    <carpeta_salida>/<AAAA-MM-DD_HHMM>_<nombre>/
    ├── correlativa.parquet        una fila por registro de entrada (LA tabla)
    ├── golden.parquet             una fila por entidad (se omite si res.golden es None)
    ├── enlaces.parquet            solo vinculación (si res.enlaces no es None)
    ├── entidades_ids.parquet      crosswalk ID_ENTIDAD ↔ ID_GRUPO de esta corrida
    ├── revision.csv               pares por decidir (forma del archivo de decisiones)
    ├── diccionario.csv            tabla · columna · tipo · significado · origen · alias_es
    ├── manifest.json              contrato, versión, huellas, parámetros, conteos, métricas…
    ├── excel/                     correlativa.xlsx, golden.xlsx (o *_LEEME.xlsx si no caben)
    ├── figuras/                   las PNG que se le pasen
    └── _trabajo/                  L1…L5, si linkage() lo dejó aquí (borrable)

Reglas
------
* **Atómica.** Todo se escribe en ``<carpeta_salida>/.<nombre>.pendiente/`` y
  al final se renombra a la carpeta definitiva (un ``rename`` en el mismo
  sistema de archivos). Si algo falla a mitad, lo que el escritor escribió se
  borra y la excepción se relanza. La carpeta pendiente puede existir antes
  (``linkage(carpeta_salida=...)`` pone ``_trabajo/`` dentro para que el
  ``rename`` final la deje en su sitio); en ese caso un fallo conserva
  ``_trabajo/`` —son los checkpoints de L1…L5 y la siguiente corrida los
  reutiliza— y borra solo los artefactos del estándar. Es la única
  desviación respecto a «la pendiente se elimina», y es deliberada: borrar
  cuarenta minutos de cómputo por un disco lleno al escribir un Excel no es
  lo correcto. Si la pendiente trae un ``_trabajo/`` que NO es el de ``res``
  (``res.dir_trabajo`` apunta a otra parte), la escritura falla antes de
  tocar nada: publicarlo dejaría una carpeta cuyo manifiesto no describe lo
  que contiene.
* **Fiel.** ``revision.csv`` y ``diccionario.csv`` se escriben tal cual
  (``escribir_csv``): son los archivos que ``leer_resultado`` lee de vuelta y
  la forma del archivo de decisiones, y nada se repara en silencio. La
  neutralización de hoja de cálculo (``prepare_spreadsheet_data``: apóstrofo
  ante ``=``, ``+``, ``-``, ``@``) se aplica SOLO a lo que se abre en una
  hoja: los ``.xlsx`` y los alias ``.csv.gz`` de v1. Única limitación, del
  formato: un CSV no distingue la celda vacía del ausente; en el estándar la
  celda vacía ES el ausente y ``leer_resultado`` la devuelve como ``pd.NA``
  (una ``''`` en memoria vuelve como NA). Los textos ``NA``, ``null``,
  ``nan``… siguen siendo texto.
* **Sin rutas a la pendiente.** Tras el ``rename``, toda ruta del resultado
  que apuntaba a ``.<nombre>.pendiente/`` (``metricas['report_files']``,
  ``manifiesto['columnas_tecnicas']``, el ``origen`` de SCORE_PAR…) se
  reubica bajo la carpeta definitiva; en ``manifest.json`` esas rutas van
  RELATIVAS a la carpeta (``_trabajo/...``) y ``leer_resultado`` las resuelve
  contra la carpeta leída si ``_trabajo/`` sigue ahí.
* **Determinista.** Los parquet se escriben con ``pyarrow`` fijando el
  esquema del contrato y sin metadatos variables (sin el bloque ``pandas``
  con versiones): dos escrituras del mismo resultado producen los mismos
  bytes. Los ``.xlsx`` no son reproducibles byte a byte (openpyxl guarda la
  fecha de creación en ``docProps/core.xml``); el manifiesto registra su
  huella real igual.
* **Excel completo o nada.** ``correlativa.xlsx``/``golden.xlsx`` se escriben
  completos hasta ``LIMITE_FILAS_EXCEL`` filas de datos (1.048.576 filas de
  hoja menos el encabezado). Si no caben, NO se escribe un recorte: se
  escribe ``<tabla>_LEEME.xlsx`` que dice cuántas filas tiene el parquet y
  cómo abrirlo (pandas, DuckDB, Power Query), y el manifiesto lo lista en
  ``omitidos`` con su motivo. F1.11 refina (``informe_cruce.xlsx``).
* **Un solo punto de escritura.** Es el ÚNICO módulo que escribe la carpeta
  del estándar; ``tests/test_escritor.py`` lo verifica sobre ``src/``. L6
  (``reporting/strategies.py``) escribe sus alias de v1 a través de las
  primitivas de aquí (``escribir_parquet``, ``escribir_xlsx``,
  ``escribir_csv_gz_por_lotes``) con ``DeprecationWarning`` y una hoja
  ``LEEME`` que remite al archivo nuevo; desaparecen en
  ``VERSION_RETIRO_ALIAS_V1``.
* El resultado se valida (``validar(estricto=True)``) ANTES de escribir: una
  carpeta del estándar que incumple el contrato no se publica.
* **Un solo manifiesto (F1.12).** ``manifest.json`` es donde vive lo que
  antes escribía L6 en ``config_auditoria_<ts>.json/.txt``: ``parametros``
  trae la ``llamada`` (lo que se pidió a ``linkage()``) y los parámetros
  EFECTIVOS del motor (``perfil``, ``lsh``, ``scoring``, ``pesos``,
  ``prioridad_fuentes`` real del golden; ``config.auditoria.parametros_motor``
  los declara una sola vez), ``metricas`` sale de la verdad en disco
  (``candidates.db``, ``scored.db``, ``_trabajo/manifest.json``) y ``version``
  es la del paquete (``importlib.metadata``). ``config_auditoria.json`` queda
  en ``_trabajo/L6_reporting/`` como alias que remite aquí.

``leer_resultado`` hace el camino inverso: verifica las huellas del
manifiesto (falla con mensaje accionable si un artefacto cambió o falta),
lee las tablas y, con ``alias="es"``, renombra las columnas según
``diccionario.csv``.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import contrato
from ..config.auditoria import ParametrosMotor
from ..evaluation.banco import _fases_desde_manifiesto, _rss_por_fase
from ..pipeline.errores import EscrituraSalidaError, mensaje_accionable
from ..pipeline.metricas import metricas_de_corrida
from ..resultado import ResultadoLinkage
from ._spreadsheet import prepare_spreadsheet_data, validate_leaf_name

__all__ = [
    "LIMITE_FILAS_EXCEL",
    "NOMBRE_TRABAJO",
    "VERSION_RETIRO_ALIAS_V1",
    "Manifiesto",
    "carpeta_pendiente",
    "escribir_csv",
    "escribir_csv_gz",
    "escribir_csv_gz_por_lotes",
    "escribir_parquet",
    "escribir_resultado",
    "escribir_xlsx",
    "leeme_alias_v1",
    "leer_resultado",
]

#: Filas de DATOS que caben en una hoja de Excel: 1.048.576 filas de hoja
#: menos una de encabezado. Por encima no se recorta: se escribe el LEEME.
LIMITE_FILAS_EXCEL = 1_048_575

#: Versión en la que desaparecen los alias de v1 de L6 (``tabla_correlativa.*``,
#: ``golden_records.*``): dos versiones menores después de 0.23.0.
VERSION_RETIRO_ALIAS_V1 = "0.25.0"

#: Nombre de la subcarpeta de trabajo (L1…L5) dentro de la carpeta del estándar.
NOMBRE_TRABAJO = "_trabajo"

#: Nombre del manifiesto dentro de la carpeta.
NOMBRE_MANIFEST = "manifest.json"

_SUFIJO_PENDIENTE = ".pendiente"
_FILAS_POR_LOTE_CSV = 50_000
_ALIAS = {"es": "alias_es"}

#: Clave de ``parametros`` donde va lo que se pidió a ``linkage()``; el resto
#: de claves son las de ``config.auditoria.ParametrosMotor``.
_CLAVE_LLAMADA = "llamada"
_CLAVES_MOTOR: tuple[str, ...] = ("perfil", "lsh", "scoring", "pesos", "prioridad_fuentes")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Manifiesto
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Manifiesto:
    """Lo que ``manifest.json`` declara de una carpeta del estándar.

    Attributes:
        contrato: versión del contrato de salida (``contrato.VERSION_CONTRATO``).
        version: versión real del paquete que escribió la carpeta.
        nombre: el ``nombre`` con que se pidió la escritura.
        marca_tiempo: ISO 8601 (segundos) de la marca de tiempo de la carpeta.
        carpeta: ruta de la carpeta definitiva (en el JSON va solo su nombre:
            las rutas absolutas no sobreviven a un cambio de máquina).
        insumos: huella SHA-256 (16 hex, ``api._huella_dataset``), filas y
            columnas por fuente (``api._manifiesto`` → ``entradas``).
        artefactos: ``[{"ruta", "bytes", "sha256"}]`` de cada archivo escrito
            (rutas relativas a la carpeta; ``manifest.json`` no se lista a sí
            mismo y ``_trabajo/`` no es un artefacto).
        parametros: ``llamada`` (los de ``linkage(...)``, tal como se guardaron;
            es lo que ``corrida.hash_parametros`` resume) y los EFECTIVOS del
            motor (F1.12, ``config.auditoria.ParametrosMotor``): ``perfil``,
            ``lsh``, ``scoring``, ``pesos`` y ``prioridad_fuentes`` (la real
            del golden, L5). Si el resultado no trae la configuración (una
            ruta que no pasa por el Orchestrator), las claves del motor van
            vacías y ``omitidos`` lo declara.
        conteos: filas, grupos, entidades (con NIT / sin NIT), fuentes…
        invariantes: el ``ReporteValidacion`` (``ok`` y ``fallos``).
        metricas: lo que antes vivía en ``config_auditoria_*`` → ``metrics``:
            el bloque de ``pipeline.metricas.metricas_de_corrida`` (la misma
            función que usa ``Orchestrator._build_metrics``; desde la verdad
            en disco: ``candidatos``, ``pares_puntuados``, ``tasa_reduccion``,
            ``grupos_multifuente``, ``confianza_media``/``mediana``,
            ``segundos_total``, ``rss_pico_mib``), más los escalares de
            ``res.metricas`` (``n_registros``, ``n_fuentes``…).
        tiempos_por_fase: segundos por fase L1…L6 desde ``_trabajo/manifest.json``.
        rss_por_fase: pico de RSS (MiB) por fase, misma fuente.
        omitidos: ``[{"artefacto", "motivo"}]``: lo que no se escribió y por qué.
        renombres: colisiones de la fuente con el contrato (``<col>_FUENTE``).
        corrida: el resto del manifiesto de la corrida (función, timestamp,
            seed, hash de parámetros, versiones, reporte de ``completar``…).
    """

    contrato: str
    version: str
    nombre: str
    marca_tiempo: str
    carpeta: Path
    insumos: dict[str, Any] = field(default_factory=dict)
    artefactos: list[dict[str, Any]] = field(default_factory=list)
    parametros: dict[str, Any] = field(default_factory=dict)
    conteos: dict[str, Any] = field(default_factory=dict)
    invariantes: dict[str, Any] = field(default_factory=dict)
    metricas: dict[str, Any] = field(default_factory=dict)
    tiempos_por_fase: dict[str, float] = field(default_factory=dict)
    rss_por_fase: dict[str, float] = field(default_factory=dict)
    omitidos: list[dict[str, str]] = field(default_factory=list)
    renombres: dict[str, str] = field(default_factory=dict)
    corrida: dict[str, Any] = field(default_factory=dict)

    def llamada(self) -> dict[str, Any]:
        """Los parámetros de la llamada. Un manifiesto anterior a F1.12 traía
        ``parametros`` PLANOS (solo la llamada): se leen igual."""
        if _CLAVE_LLAMADA in self.parametros:
            return dict(self.parametros[_CLAVE_LLAMADA] or {})
        return dict(self.parametros)

    def configuracion(self) -> dict[str, Any]:
        """Los parámetros efectivos del motor (``perfil``, ``lsh``, ``scoring``,
        ``pesos``, ``prioridad_fuentes``); vacío en un manifiesto anterior a F1.12."""
        if _CLAVE_LLAMADA not in self.parametros:
            return {}
        return {k: v for k, v in self.parametros.items() if k != _CLAVE_LLAMADA}

    def a_dict(self) -> dict[str, Any]:
        return {
            "contrato": self.contrato,
            "version": self.version,
            "nombre": self.nombre,
            "marca_tiempo": self.marca_tiempo,
            "carpeta": self.carpeta.name,
            "insumos": self.insumos,
            "artefactos": list(self.artefactos),
            "parametros": self.parametros,
            "conteos": self.conteos,
            "invariantes": self.invariantes,
            "metricas": self.metricas,
            "tiempos_por_fase": self.tiempos_por_fase,
            "rss_por_fase": self.rss_por_fase,
            "omitidos": list(self.omitidos),
            "renombres": dict(self.renombres),
            "corrida": self.corrida,
        }

    @classmethod
    def desde_dict(cls, datos: Mapping[str, Any], carpeta: Path | None = None) -> Manifiesto:
        """Reconstruye el manifiesto desde el JSON. ``carpeta`` fija la ruta real."""
        faltan = [k for k in ("contrato", "version", "nombre", "marca_tiempo") if k not in datos]
        if faltan:
            raise EscrituraSalidaError(
                mensaje_accionable(
                    f"al manifiesto le faltan las claves {faltan}.",
                    "sin ellas no se sabe qué contrato ni qué versión produjo la carpeta.",
                    "verifique que manifest.json lo escribió escribir_resultado (F1.10) y "
                    "no otra herramienta.",
                )
            )
        return cls(
            contrato=str(datos["contrato"]),
            version=str(datos["version"]),
            nombre=str(datos["nombre"]),
            marca_tiempo=str(datos["marca_tiempo"]),
            carpeta=carpeta if carpeta is not None else Path(str(datos.get("carpeta", ""))),
            insumos=dict(datos.get("insumos") or {}),
            artefactos=list(datos.get("artefactos") or []),
            parametros=dict(datos.get("parametros") or {}),
            conteos=dict(datos.get("conteos") or {}),
            invariantes=dict(datos.get("invariantes") or {}),
            metricas=dict(datos.get("metricas") or {}),
            tiempos_por_fase=dict(datos.get("tiempos_por_fase") or {}),
            rss_por_fase=dict(datos.get("rss_por_fase") or {}),
            omitidos=list(datos.get("omitidos") or []),
            renombres=dict(datos.get("renombres") or {}),
            corrida=dict(datos.get("corrida") or {}),
        )

    def guardar(self, ruta: Path) -> None:
        ruta.write_text(
            json.dumps(self.a_dict(), indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def leer(cls, carpeta: Path) -> Manifiesto:
        ruta = carpeta / NOMBRE_MANIFEST
        if not ruta.is_file():
            raise EscrituraSalidaError(
                mensaje_accionable(
                    f"no existe {ruta}.",
                    "sin manifest.json no hay carpeta del estándar: no se puede saber qué "
                    "contrato, qué insumos ni qué artefactos la componen.",
                    "pase la carpeta <AAAA-MM-DD_HHMM>_<nombre> que dejó linkage("
                    "carpeta_salida=...) o escribir_resultado(); si la carpeta es de v1 "
                    "(L6_reporting/), léala con pandas directamente.",
                )
            )
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EscrituraSalidaError(
                mensaje_accionable(
                    f"{ruta} no es JSON válido ({exc}).",
                    "el manifiesto es la fuente de verdad de la carpeta.",
                    "la escritura fue atómica: si el archivo está truncado alguien lo editó "
                    "después; vuelva a escribir el resultado.",
                )
            ) from exc
        return cls.desde_dict(datos, carpeta=carpeta)

    def verificar(self) -> list[str]:
        """Compara cada artefacto listado con el disco. Vacío si todo coincide."""
        fallos: list[str] = []
        for art in self.artefactos:
            ruta = self.carpeta / str(art["ruta"])
            if not ruta.is_file():
                fallos.append(f"{art['ruta']}: falta.")
                continue
            tamano = ruta.stat().st_size
            if tamano != int(art["bytes"]):
                fallos.append(f"{art['ruta']}: {tamano} bytes y el manifiesto dice {art['bytes']}.")
                continue
            huella = _sha256_archivo(ruta)
            if huella != art["sha256"]:
                fallos.append(f"{art['ruta']}: la huella SHA-256 cambió.")
        return fallos


# ─────────────────────────────────────────────────────────────────────────────
# Primitivas de escritura (las usa este módulo y los alias de L6)
# ─────────────────────────────────────────────────────────────────────────────


def _sha256_archivo(ruta: Path) -> str:
    """SHA-256 PLANO del archivo (``sha256sum`` lo reproduce).

    No se usa ``pipeline.fingerprints.fingerprint_file`` a propósito: esa
    huella lleva un prefijo de protocolo para los checkpoints y nadie podría
    verificarla desde fuera de la librería.
    """
    hasher = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(8 * 1024 * 1024), b""):
            hasher.update(bloque)
    return hasher.hexdigest()


def _tabla_arrow(df: pd.DataFrame, columnas: Sequence[contrato.ColumnaContrato]) -> pa.Table:
    """``pa.Table`` con las columnas del contrato en su tipo y sin metadatos variables.

    El metadato ``contrato`` se estampa SOLO cuando se escribe con columnas del
    contrato: un parquet sin ellas (los alias de v1, con columnas técnicas) no
    tiene la forma del contrato y no debe decir que la tiene.
    """
    try:
        tabla = pa.Table.from_pandas(df, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError) as exc:
        raise EscrituraSalidaError(
            mensaje_accionable(
                f"pyarrow no pudo convertir la tabla a parquet: {exc}.",
                "una columna mezcla tipos (p. ej. texto y números en object) y parquet "
                "exige un tipo por columna.",
                "convierta esa columna de la fuente a texto (df[col] = df[col].astype('string')) "
                "antes de llamar a linkage().",
            )
        ) from exc
    por_nombre = {c.nombre: c for c in columnas}
    for i, nombre in enumerate(tabla.column_names):
        col = por_nombre.get(nombre)
        if col is None:
            continue
        try:
            columna = tabla.column(i).cast(col.tipo)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise EscrituraSalidaError(
                mensaje_accionable(
                    f"la columna {nombre} no se puede escribir como {col.tipo}: {exc}.",
                    f"el contrato {contrato.VERSION_CONTRATO} fija ese tipo.",
                    "el resultado pasó validar(); repórtelo con el manifiesto.",
                )
            ) from exc
        tabla = tabla.set_column(i, col.campo(), columna)
    metadatos = {"contrato": contrato.VERSION_CONTRATO} if columnas else None
    return tabla.replace_schema_metadata(metadatos)


def escribir_parquet(
    df: pd.DataFrame, ruta: Path, columnas: Sequence[contrato.ColumnaContrato] = ()
) -> None:
    """Escribe ``df`` como parquet determinista (snappy, sin metadatos variables).

    Con ``columnas`` del contrato fija sus tipos y estampa el metadato
    ``contrato``; sin ellas (alias de v1) no lo estampa.
    """
    _escribir_parquet(df, ruta, columnas)


def _escribir_parquet(
    df: pd.DataFrame, ruta: Path, columnas: Sequence[contrato.ColumnaContrato] = ()
) -> None:
    # Punto único de escritura de parquet; ``escribir_parquet`` es su nombre
    # público y las pruebas de atomicidad reemplazan este interno.
    pq.write_table(_tabla_arrow(df, columnas), ruta, compression="snappy")


def escribir_csv(df: pd.DataFrame, ruta: Path) -> None:
    """CSV UTF-8 FIEL a los datos, con ``\\n`` fijo (determinista).

    Es el CSV del estándar (``revision.csv``, ``diccionario.csv``): lo que se
    escribe es lo que ``leer_resultado`` devuelve, sin apóstrofos ante ``=``,
    ``+``, ``-`` o ``@``. La neutralización queda para ``escribir_xlsx`` y los
    alias ``.csv.gz`` (``escribir_csv_gz*``), que se abren en hoja de cálculo.

    Lo único que el CSV no conserva es la diferencia entre ``''`` y ausente:
    ambos se escriben como celda vacía y ``leer_resultado`` la devuelve como
    ``pd.NA`` (ver ``leer_resultado``).
    """
    df.to_csv(ruta, index=False, lineterminator="\n")


def escribir_csv_gz(df: pd.DataFrame, ruta: Path) -> None:
    """CSV comprimido (gzip) neutralizado, de una vez (alias de v1 desde memoria)."""
    with gzip.open(ruta, "wt", encoding="utf-8", newline="") as f_out:
        prepare_spreadsheet_data(df).to_csv(f_out, index=False)


def escribir_csv_gz_por_lotes(
    archivo: pq.ParquetFile, destino: Path, *, filas_por_lote: int = _FILAS_POR_LOTE_CSV
) -> int:
    """Vuelca un parquet a ``.csv.gz`` por lotes (RAM constante). Devuelve las filas."""
    filas = 0
    with gzip.open(destino, "wt", encoding="utf-8", newline="") as f_out:
        primero = True
        for lote in archivo.iter_batches(batch_size=filas_por_lote):
            trozo = lote.to_pandas()
            prepare_spreadsheet_data(trozo).to_csv(f_out, index=False, header=primero)
            filas += len(trozo)
            primero = False
            del trozo
    return filas


def escribir_xlsx(
    df: pd.DataFrame | None,
    ruta: Path,
    *,
    leeme: pd.DataFrame | None = None,
    hoja: str = "datos",
) -> None:
    """Escribe ``df`` en ``ruta`` neutralizado; con ``leeme`` esa hoja va PRIMERO.

    Con ``df=None`` el libro solo lleva la hoja ``LEEME`` (el caso «no cabe»).
    """
    if df is None and leeme is None:
        raise ValueError("escribir_xlsx: hace falta df, leeme o ambos.")
    with pd.ExcelWriter(ruta, engine="openpyxl") as escritor:
        if leeme is not None:
            leeme.to_excel(escritor, sheet_name="LEEME", index=False, header=False)
        if df is not None:
            prepare_spreadsheet_data(df).to_excel(escritor, sheet_name=hoja, index=False)


def _hoja_de_lineas(lineas: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({"LEEME": list(lineas)})


def leeme_alias_v1(archivo_nuevo: str) -> pd.DataFrame:
    """Hoja LEEME de un alias de v1: remite al archivo del estándar y avisa el retiro."""
    return _hoja_de_lineas(
        [
            "Este archivo es un ALIAS de v1 y se mantiene por compatibilidad.",
            f"El archivo nuevo es {archivo_nuevo} dentro de la carpeta del estándar "
            f"(<AAAA-MM-DD_HHMM>_<nombre>/, contrato {contrato.VERSION_CONTRATO}).",
            f"Este alias desaparece en rues-linker {VERSION_RETIRO_ALIAS_V1}.",
            "Los datos están en la hoja siguiente, sin cambios respecto a v1.",
        ]
    )


def _leeme_no_cabe(tabla: str, n_filas: int) -> pd.DataFrame:
    parquet = f"{tabla}.parquet"
    return _hoja_de_lineas(
        [
            f"{parquet} tiene {_miles(n_filas)} filas y una hoja de Excel admite "
            f"{_miles(LIMITE_FILAS_EXCEL + 1)} (incluido el encabezado).",
            "No se escribe un recorte: la tabla completa está en el parquet.",
            f"pandas: pd.read_parquet('{parquet}')",
            f"DuckDB: SELECT * FROM '{parquet}'",
            "Power Query (Excel 365): Datos → Obtener datos → De archivo → Parquet.",
        ]
    )


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


# ─────────────────────────────────────────────────────────────────────────────
# Rutas anidadas: reubicar tras el rename y relativizar en el manifiesto
# ─────────────────────────────────────────────────────────────────────────────


def _transformar_rutas(valor: Any, transformar: Callable[[str], str | None]) -> Any:
    """Aplica ``transformar`` a cada ``str``/``Path`` de una estructura anidada.

    Recorre dict, list y tuple y devuelve contenedores NUEVOS (el original no
    se toca). ``transformar`` devuelve la cadena nueva o ``None`` si esa no es
    una ruta que le interese; un ``Path`` sigue siendo ``Path``. Cualquier
    otro objeto (números, DataFrames…) se devuelve tal cual.
    """
    if isinstance(valor, Path):
        nuevo = transformar(str(valor))
        return valor if nuevo is None else Path(nuevo)
    if isinstance(valor, str):
        nuevo = transformar(valor)
        return valor if nuevo is None else nuevo
    if isinstance(valor, dict):
        return {k: _transformar_rutas(v, transformar) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_transformar_rutas(v, transformar) for v in valor]
    if isinstance(valor, tuple):
        return tuple(_transformar_rutas(v, transformar) for v in valor)
    return valor


def _bajo(ruta: str, base: str) -> bool:
    """``ruta`` es ``base`` o está debajo (límite en el separador: ``x.pendiente2``
    no está bajo ``x.pendiente``). Acepta ``/`` además de ``os.sep``: las rutas
    relativas del ``manifest.json`` son POSIX."""
    base = base.rstrip(os.sep + "/")
    return ruta == base or ruta.startswith(base + os.sep) or ruta.startswith(base + "/")


def _pares_de_prefijos(de: Path, a: Path, *, tambien_resuelta: bool) -> list[tuple[str, str]]:
    """``(de, a)`` tal cual y, si difiere, resuelta: L1…L6 guardan unas rutas
    como se las dieron (relativas) y otras absolutas (``Orchestrator``
    resuelve ``work_dir``)."""
    pares = [(str(de), str(a))]
    if tambien_resuelta and str(de.resolve()) != str(de):
        pares.append((str(de.resolve()), str(a.resolve())))
    return pares


def _reubicar_rutas(valor: Any, de: Path, a: Path, *, tambien_resuelta: bool = True) -> Any:
    """Copia de ``valor`` con toda ruta bajo ``de`` llevada bajo ``a``.

    Es lo que hace sobrevivir al ``rename`` de la pendiente a las rutas que
    el resultado guarda en memoria (``metricas['report_files']``,
    ``manifiesto['columnas_tecnicas']['quedan_en']``, ``origen`` de
    SCORE_PAR…). Con ``tambien_resuelta=False`` solo se compara el prefijo
    literal (p. ej. ``_trabajo`` relativo al leer una carpeta).
    """
    pares = _pares_de_prefijos(de, a, tambien_resuelta=tambien_resuelta)

    def transformar(ruta: str) -> str | None:
        for viejo, nuevo in pares:
            if _bajo(ruta, viejo):
                return nuevo + ruta[len(viejo) :]
        return None

    return _transformar_rutas(valor, transformar)


def _relativizar_rutas(valor: Any, base: Path) -> Any:
    """Copia de ``valor`` con toda ruta bajo ``base`` relativa a ella (POSIX).

    Es lo que va al ``manifest.json``: una ruta absoluta a la pendiente no
    sobrevive al ``rename``, y una absoluta a la carpeta no sobrevive a
    moverla ni a cambiar de máquina. ``base`` misma queda como ``"."``.
    """
    pares = _pares_de_prefijos(base, base, tambien_resuelta=True)

    def transformar(ruta: str) -> str | None:
        for viejo, _ in pares:
            if _bajo(ruta, viejo):
                resto = ruta[len(viejo) :].lstrip(os.sep + "/")
                return Path(resto).as_posix() if resto else "."
        return None

    return _transformar_rutas(valor, transformar)


# ─────────────────────────────────────────────────────────────────────────────
# escribir_resultado
# ─────────────────────────────────────────────────────────────────────────────


def _entidades_ids(correl: pd.DataFrame) -> pd.DataFrame:
    """Crosswalk ``ID_ENTIDAD ↔ ID_GRUPO`` de esta corrida (``RETIRADO_EN`` vacío)."""
    grupos = (
        correl.groupby("ID_GRUPO", sort=True)
        .agg(ID_ENTIDAD=("ID_ENTIDAD", "first"), N_REGISTROS=("ID_ENTIDAD", "size"))
        .reset_index()
    )
    return pd.DataFrame(
        {
            "ID_ENTIDAD": grupos["ID_ENTIDAD"].astype("string"),
            "ID_GRUPO": grupos["ID_GRUPO"].astype("int64"),
            "N_REGISTROS": grupos["N_REGISTROS"].astype("int64"),
            "RETIRADO_EN": pd.Series(pd.NA, index=grupos.index, dtype="string"),
        }
    )


def _conteos(res: ResultadoLinkage) -> dict[str, Any]:
    c = res.correlativa
    entidades = c["ID_ENTIDAD"].drop_duplicates()
    con_nit = int(entidades.str.startswith("NIT-").sum())
    conteos: dict[str, Any] = {
        "filas": len(c),
        "grupos": int(c["ID_GRUPO"].nunique()),
        "entidades": len(entidades),
        "entidades_con_nit": con_nit,
        "entidades_sin_nit": len(entidades) - con_nit,
        "fuentes": int(c["SRC"].nunique()) if "SRC" in c.columns else None,
        "filas_por_fuente": (
            {str(k): int(v) for k, v in c["SRC"].value_counts().items()}
            if "SRC" in c.columns
            else {}
        ),
        "golden": len(res.golden) if res.golden is not None else None,
        "enlaces": len(res.enlaces) if res.enlaces is not None else None,
        "revision": len(res.revision),
    }
    return conteos


def _parametros(res: ResultadoLinkage, omitidos: list[dict[str, str]]) -> dict[str, Any]:
    """``parametros`` del manifiesto: la llamada más los efectivos del motor.

    ``res.manifiesto["configuracion"]`` lo deja ``api.linkage``/``link``
    (``config.auditoria.parametros_motor``). Una ruta que no lo trae no se
    repara en silencio: las claves del motor van vacías y ``omitidos`` lo dice.
    """
    configuracion = res.manifiesto.get("configuracion")
    if not configuracion:
        configuracion = ParametrosMotor(None, {}, {}, {}, ()).a_dict()
        omitidos.append(
            {
                "artefacto": "parametros.perfil",
                "motivo": "el resultado no trae la configuración efectiva del motor "
                "(res.manifiesto['configuracion']: esta ruta no pasa por el "
                "Orchestrator). Sin ella la corrida no se reproduce desde el "
                "manifiesto. Use linkage(carpeta_salida=...) o deje "
                "res.manifiesto['configuracion'] = parametros_motor(config).a_dict() "
                "antes de escribir; F2 unifica los caminos.",
            }
        )
    return {_CLAVE_LLAMADA: dict(res.manifiesto.get("parametros") or {})} | {
        k: configuracion.get(k) for k in _CLAVES_MOTOR
    }


def _metricas(
    res: ResultadoLinkage, tiempos: Mapping[str, float], rss: Mapping[str, float]
) -> dict[str, Any]:
    """Métricas de la corrida desde la verdad en disco (ver ``Manifiesto.metricas``).

    El bloque es ``pipeline.metricas.metricas_de_corrida`` tal cual: la misma
    regla que ``Orchestrator._build_metrics`` entrega a L6 y al alias
    ``config_auditoria.json`` (escrita una vez; ``None`` —nunca 0— donde no
    se puede saber).
    """
    metricas = metricas_de_corrida(res.golden, res.correlativa, res.dir_trabajo, tiempos, rss)
    # Los escalares de res.metricas (n_registros, n_grupos, n_fuentes…); las
    # listas y tablas (report_files, matcher_decisions…) no son métricas.
    for clave, valor in res.metricas.items():
        if clave not in metricas and (valor is None or isinstance(valor, (bool, int, float, str))):
            metricas[clave] = valor
    return metricas


def _tiempos_y_rss(
    dir_trabajo: Path | None,
) -> tuple[dict[str, float], dict[str, float], str | None]:
    """Tiempos y RSS por fase desde ``_trabajo/manifest.json``, y el motivo si no hay.

    Sin ``_trabajo/`` o sin su manifiesto no hay motivo que declarar (el
    resultado no corrió L1…L5 aquí). Un manifiesto ilegible SÍ se declara:
    vacío sin motivo sería indistinguible de «no hubo _trabajo».
    """
    if dir_trabajo is None:
        return {}, {}, None
    ruta = Path(dir_trabajo) / "manifest.json"
    if not ruta.is_file():
        return {}, {}, None
    try:
        manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, f"{NOMBRE_TRABAJO}/manifest.json ilegible: {exc}"
    if not isinstance(manifiesto, dict):
        return (
            {},
            {},
            f"{NOMBRE_TRABAJO}/manifest.json no es un objeto JSON ({type(manifiesto).__name__}).",
        )
    return _fases_desde_manifiesto(manifiesto), _rss_por_fase(manifiesto), None


def _version_paquete() -> str:
    from .. import __version__

    return str(__version__)


def _registrar(artefactos: list[dict[str, Any]], carpeta: Path, ruta: Path) -> None:
    artefactos.append(
        {
            "ruta": ruta.relative_to(carpeta).as_posix(),
            "bytes": ruta.stat().st_size,
            "sha256": _sha256_archivo(ruta),
        }
    )


def _diccionario_completo(res: ResultadoLinkage, entidades_ids: pd.DataFrame) -> pd.DataFrame:
    base = res.diccionario
    if base is None or base.empty:
        completar = res.manifiesto.get("completar") or {}
        base = contrato.diccionario(
            {
                "correlativa": res.correlativa,
                "golden": res.golden,
                "enlaces": res.enlaces,
                "revision": res.revision,
            },
            columnas_fuente=completar.get("columnas_fuente", ()),
            renombres=completar.get("renombres"),
            renombres_canonicos=completar.get("renombres_canonicos"),
        )
    if "entidades_ids" in set(base["tabla"]):
        return base
    extra = contrato.diccionario({"entidades_ids": entidades_ids})
    return pd.concat([base, extra], ignore_index=True)


def _nombre_carpeta(nombre: str, marca_tiempo: datetime) -> str:
    return f"{marca_tiempo:%Y-%m-%d_%H%M}_{nombre}"


def carpeta_pendiente(carpeta_salida: Path, nombre: str) -> Path:
    """``<carpeta_salida>/.<nombre>.pendiente``: donde se escribe antes del ``rename``.

    Valida ``nombre`` (sin separadores ni rutas). ``linkage()`` la pide al
    empezar para poner ``_trabajo/`` dentro y fallar antes de L1 si el nombre
    no sirve.
    """
    validate_leaf_name(nombre, "nombre")
    return Path(carpeta_salida) / f".{nombre}{_SUFIJO_PENDIENTE}"


def _excel(
    res: ResultadoLinkage,
    carpeta: Path,
    excel: bool,
    artefactos: list[dict[str, Any]],
    omitidos: list[dict[str, str]],
) -> None:
    tablas: list[tuple[str, pd.DataFrame | None]] = [
        ("correlativa", res.correlativa),
        ("golden", res.golden),
    ]
    if not excel:
        for tabla, df in tablas:
            if df is not None:
                omitidos.append(
                    {"artefacto": f"excel/{tabla}.xlsx", "motivo": "se pidió excel=False."}
                )
        return
    dir_excel = carpeta / "excel"
    dir_excel.mkdir()
    for tabla, df in tablas:
        if df is None:
            omitidos.append({"artefacto": f"excel/{tabla}.xlsx", "motivo": f"res.{tabla} es None."})
            continue
        if len(df) > LIMITE_FILAS_EXCEL:
            ruta = dir_excel / f"{tabla}_LEEME.xlsx"
            escribir_xlsx(None, ruta, leeme=_leeme_no_cabe(tabla, len(df)))
            _registrar(artefactos, carpeta, ruta)
            omitidos.append(
                {
                    "artefacto": f"excel/{tabla}.xlsx",
                    "motivo": f"{_miles(len(df))} filas superan el límite de Excel "
                    f"({_miles(LIMITE_FILAS_EXCEL)} de datos); no se recorta, "
                    f"ver excel/{tabla}_LEEME.xlsx.",
                }
            )
            continue
        ruta = dir_excel / f"{tabla}.xlsx"
        escribir_xlsx(df, ruta, hoja=tabla)
        _registrar(artefactos, carpeta, ruta)


def _figuras_sin_choques(figuras: Sequence[Path]) -> list[Path]:
    """Las figuras sin repetir la misma ruta; falla si dos DISTINTAS se llaman igual.

    ``figuras/`` es plano: la segunda pisaría a la primera y el manifiesto
    listaría dos entradas para un archivo, y ``leer_resultado`` culparía al
    usuario de una edición que no hizo. La misma ruta dos veces no es un
    choque: se copia una vez.
    """
    unicas: list[Path] = []
    vistas: set[Path] = set()
    for figura in figuras:
        ruta = Path(figura)
        clave = ruta.resolve()
        if clave not in vistas:
            vistas.add(clave)
            unicas.append(ruta)
    repetidos = sorted(n for n, k in Counter(f.name for f in unicas).items() if k > 1)
    if repetidos:
        detalle = "; ".join(
            f"{n}: " + ", ".join(str(f) for f in unicas if f.name == n) for n in repetidos
        )
        raise EscrituraSalidaError(
            mensaje_accionable(
                f"dos figuras se llaman igual ({detalle}).",
                "figuras/ es plano: la segunda pisaría a la primera y el manifiesto no "
                "describiría lo que hay.",
                "renombre las PNG antes de pasarlas a figuras=.",
            )
        )
    return unicas


def _escribir_en(
    res: ResultadoLinkage,
    carpeta: Path,
    nombre: str,
    marca_tiempo: datetime,
    excel: bool,
    figuras: Sequence[Path],
    dir_trabajo_relativo: bool,
    definitiva: Path,
) -> Manifiesto:
    """Escribe todos los artefactos dentro de ``carpeta`` (la pendiente).

    El manifiesto declara ``definitiva`` como su carpeta (es el nombre que
    tendrá tras el ``rename``), aunque se escriba en la pendiente.
    """
    artefactos: list[dict[str, Any]] = []
    omitidos: list[dict[str, str]] = []
    figuras = _figuras_sin_choques(figuras)

    ruta = carpeta / "correlativa.parquet"
    _escribir_parquet(res.correlativa, ruta, contrato.CORRELATIVA)
    _registrar(artefactos, carpeta, ruta)

    if res.golden is not None:
        ruta = carpeta / "golden.parquet"
        _escribir_parquet(res.golden, ruta, contrato.GOLDEN)
        _registrar(artefactos, carpeta, ruta)
    else:
        omitidos.append(
            {
                "artefacto": "golden.parquet",
                "motivo": "res.golden es None (esta ruta no produce el golden en memoria).",
            }
        )

    if res.enlaces is not None:
        ruta = carpeta / "enlaces.parquet"
        _escribir_parquet(res.enlaces, ruta, contrato.ENLACES)
        _registrar(artefactos, carpeta, ruta)

    entidades_ids = _entidades_ids(res.correlativa)
    ruta = carpeta / "entidades_ids.parquet"
    _escribir_parquet(entidades_ids, ruta, contrato.ENTIDADES_IDS)
    _registrar(artefactos, carpeta, ruta)

    ruta = carpeta / "revision.csv"
    escribir_csv(res.revision, ruta)
    _registrar(artefactos, carpeta, ruta)

    ruta = carpeta / "diccionario.csv"
    escribir_csv(_diccionario_completo(res, entidades_ids), ruta)
    _registrar(artefactos, carpeta, ruta)

    _excel(res, carpeta, excel, artefactos, omitidos)

    if figuras:
        dir_figuras = carpeta / "figuras"
        dir_figuras.mkdir()
        for figura in figuras:
            origen = Path(figura)
            if not origen.is_file():
                omitidos.append({"artefacto": f"figuras/{origen.name}", "motivo": "no existe."})
                continue
            destino = dir_figuras / origen.name
            shutil.copy2(origen, destino)
            _registrar(artefactos, carpeta, destino)

    for omitido in res.manifiesto.get("omitidos") or ():
        if isinstance(omitido, dict):
            omitidos.append({str(k): str(v) for k, v in omitido.items()})

    completar = res.manifiesto.get("completar") or {}
    corrida = {
        k: v
        for k, v in res.manifiesto.items()
        if k not in ("entradas", "parametros", "configuracion", "omitidos")
    }
    corrida["dir_trabajo"] = str(res.dir_trabajo) if res.dir_trabajo is not None else None
    if dir_trabajo_relativo:
        # _trabajo/ vive en la pendiente: ninguna ruta absoluta a ella entra
        # al JSON (dir_trabajo → "_trabajo", quedan_en, origen de SCORE_PAR…).
        corrida = _relativizar_rutas(corrida, carpeta)
    tiempos, rss, motivo = _tiempos_y_rss(res.dir_trabajo)
    if motivo is not None:
        logger.warning("tiempos_por_fase y rss_por_fase quedan vacíos: %s", motivo)
        omitidos.append({"artefacto": "tiempos_por_fase", "motivo": motivo})
        omitidos.append({"artefacto": "rss_por_fase", "motivo": motivo})
    reporte = res.validar()
    parametros = _parametros(res, omitidos)
    manifiesto = Manifiesto(
        contrato=contrato.VERSION_CONTRATO,
        version=_version_paquete(),
        nombre=nombre,
        marca_tiempo=marca_tiempo.isoformat(timespec="seconds"),
        carpeta=definitiva,
        insumos=dict(res.manifiesto.get("entradas") or {}),
        artefactos=artefactos,
        parametros=parametros,
        conteos=_conteos(res),
        invariantes={"ok": reporte.ok, "fallos": list(reporte.fallos)},
        metricas=_metricas(res, tiempos, rss),
        tiempos_por_fase=tiempos,
        rss_por_fase=rss,
        omitidos=omitidos,
        renombres=dict(completar.get("renombres") or {}),
        corrida=corrida,
    )
    manifiesto.guardar(carpeta / NOMBRE_MANIFEST)
    return manifiesto


def _limpiar_pendiente(pendiente: Path, *, conservar_trabajo: bool) -> None:
    """Borra lo que el escritor pudo escribir.

    Con ``conservar_trabajo`` deja ``_trabajo/`` (son los checkpoints de
    ``res``, que vive ahí). ``escribir_resultado`` solo llega aquí con
    ``conservar_trabajo=False`` cuando ya comprobó que no hay ``_trabajo/``
    ajeno, así que en ese caso no hay nada que conservar.
    """
    if not pendiente.is_dir():
        return
    for hijo in pendiente.iterdir():
        if conservar_trabajo and hijo.name == NOMBRE_TRABAJO:
            continue
        if hijo.is_dir():
            shutil.rmtree(hijo, ignore_errors=True)
        else:
            hijo.unlink(missing_ok=True)
    if not any(pendiente.iterdir()):
        pendiente.rmdir()


def escribir_resultado(
    res: ResultadoLinkage,
    carpeta_salida: Path,
    nombre: str,
    *,
    marca_tiempo: datetime | None = None,
    excel: bool = True,
    figuras: Iterable[Path] = (),
) -> Manifiesto:
    """Escribe la carpeta del estándar, de forma atómica, y devuelve su manifiesto.

    Args:
        res: resultado de ``linkage()``/``link()``/``dedupe()`` (contrato 1.0).
            Se valida con ``validar(estricto=True)`` antes de escribir nada.
        carpeta_salida: carpeta padre; se crea si no existe.
        nombre: nombre simple (sin separadores) que cierra el nombre de la
            carpeta ``<AAAA-MM-DD_HHMM>_<nombre>``.
        marca_tiempo: marca de la carpeta; por defecto ``datetime.now()``
            (hora local, como los nombres de carpeta de v1).
        excel: si False no se escribe ``excel/`` y el manifiesto lo declara.
        figuras: rutas de PNG que se copian a ``figuras/``.

    Returns:
        ``Manifiesto`` (``.carpeta`` es la ruta definitiva). Si ``res.dir_trabajo``
        estaba dentro de la carpeta pendiente, ``res.dir_trabajo`` pasa a la
        definitiva y ``res.metricas``/``res.manifiesto`` se reemplazan por
        copias en las que toda ruta bajo la pendiente (``report_files`` de L6,
        ``columnas_tecnicas['quedan_en']``, el ``origen`` de SCORE_PAR…) queda
        bajo la definitiva; ``res.manifiesto['carpeta_salida']`` apunta a la
        carpeta.

    Raises:
        ContratoSalidaError: si ``res`` incumple el contrato.
        EscrituraSalidaError: si dos figuras distintas se llaman igual (``figuras/``
            es plano); si la carpeta definitiva ya existe (la marca tiene
            resolución de minuto: dos escrituras del mismo ``nombre`` en el
            mismo minuto chocan; la segunda falla sin tocar la primera y, si
            ``_trabajo/`` estaba en la pendiente, lo conserva para reanudar);
            o si la pendiente trae un ``_trabajo/`` que no es el de ``res``
            (una corrida interrumpida de ``linkage(carpeta_salida=...)``): no
            se publica como si fuera de esta corrida ni se borra.
        ValueError: si ``nombre`` no es un nombre simple.

    Dos corridas simultáneas con el mismo ``nombre`` en la misma
    ``carpeta_salida`` comparten la pendiente y no están soportadas.
    """
    carpeta_salida = Path(carpeta_salida)
    pendiente = carpeta_pendiente(carpeta_salida, nombre)
    res.validar(estricto=True)
    marca = marca_tiempo if marca_tiempo is not None else datetime.now()
    definitiva = carpeta_salida / _nombre_carpeta(nombre, marca)
    if definitiva.exists():
        raise EscrituraSalidaError(
            mensaje_accionable(
                f"la carpeta {definitiva} ya existe.",
                "el escritor nunca sobrescribe una corrida publicada: perdería la "
                "trazabilidad de la anterior.",
                "use otro nombre, otra marca_tiempo o borre la carpeta anterior a mano.",
            )
        )
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    trabajo_pendiente = pendiente / NOMBRE_TRABAJO
    trabajo_dentro = (
        res.dir_trabajo is not None
        and Path(res.dir_trabajo).resolve() == trabajo_pendiente.resolve()
    )
    if not trabajo_dentro and trabajo_pendiente.exists():
        donde = (
            f"tiene su trabajo en {res.dir_trabajo}"
            if res.dir_trabajo is not None
            else "no tiene dir_trabajo"
        )
        raise EscrituraSalidaError(
            mensaje_accionable(
                f"hay un {NOMBRE_TRABAJO}/ de otra corrida en {pendiente} y el resultado "
                f"que se quiere publicar {donde}.",
                "es de una corrida interrumpida de linkage(carpeta_salida=...); publicarlo "
                "con este resultado dejaría una carpeta cuyo manifiesto no describe los "
                "checkpoints que contiene.",
                f"reanude esa corrida con linkage(carpeta_salida={str(carpeta_salida)!r}, "
                f"nombre={nombre!r}) o borre {trabajo_pendiente} a mano; si el resultado "
                f"sí es de esa corrida, su dir_trabajo debe ser {trabajo_pendiente}.",
            )
        )
    # Restos de una escritura anterior interrumpida a la fuerza (kill): se
    # limpian antes de empezar; _trabajo/ se conserva solo si es el de res.
    _limpiar_pendiente(pendiente, conservar_trabajo=trabajo_dentro)
    pendiente.mkdir(parents=True, exist_ok=True)
    try:
        manifiesto = _escribir_en(
            res, pendiente, nombre, marca, excel, list(figuras), trabajo_dentro, definitiva
        )
        pendiente.rename(definitiva)
    except BaseException:
        _limpiar_pendiente(pendiente, conservar_trabajo=trabajo_dentro)
        raise
    if trabajo_dentro:
        # La pendiente ya no existe: todo lo que apuntaba a ella (dir_trabajo,
        # report_files de L6, quedan_en, origen de SCORE_PAR…) pasa a la definitiva.
        res.dir_trabajo = definitiva / NOMBRE_TRABAJO
        res.metricas = _reubicar_rutas(res.metricas, pendiente, definitiva)
        res.manifiesto = _reubicar_rutas(res.manifiesto, pendiente, definitiva)
        res.manifiesto["dir_trabajo"] = str(res.dir_trabajo)
    res.manifiesto["carpeta_salida"] = str(definitiva)
    return manifiesto


# ─────────────────────────────────────────────────────────────────────────────
# leer_resultado
# ─────────────────────────────────────────────────────────────────────────────


def _aplicar_alias(df: pd.DataFrame, diccionario: pd.DataFrame, tabla: str, alias: str) -> None:
    columna_alias = _ALIAS[alias]
    filas = diccionario[diccionario["tabla"] == tabla]
    mapa = {
        str(c): str(a)
        for c, a in zip(filas["columna"], filas[columna_alias], strict=True)
        if str(c) != str(a) and str(c) in df.columns
    }
    if mapa:
        df.rename(columns=mapa, inplace=True)


def leer_resultado(ruta: Path, alias: str | None = None) -> ResultadoLinkage:
    """Lee una carpeta del estándar y devuelve el ``ResultadoLinkage``.

    Verifica las huellas del manifiesto antes de leer: si un artefacto falta
    o cambió, falla con mensaje accionable. Con ``alias="es"`` renombra las
    columnas de cada tabla según ``diccionario.csv`` (``alias_es``); los
    archivos conservan los nombres de v1.

    ``manifiesto`` del resultado trae las claves de la corrida (``funcion``,
    ``parametros`` —la llamada—, ``configuracion`` —los efectivos del motor—,
    ``entradas``…) y, en ``manifest``, el manifiesto completo de la carpeta;
    ``metricas`` trae los conteos y las ``metricas`` del manifiesto. ``dir_trabajo`` apunta a ``_trabajo/`` si está dentro, y
    entonces las rutas que el JSON guarda relativas a ``_trabajo/`` vuelven
    absolutas, resueltas contra la carpeta leída (``manifest`` conserva el
    JSON tal cual).

    En ``revision.csv`` la celda vacía es el ausente: vuelve como ``pd.NA``
    (``na_values=[""]``); ``NA``, ``null``, ``nan``… son texto.
    """
    if alias is not None and alias not in _ALIAS:
        raise ValueError(
            mensaje_accionable(
                f"alias={alias!r} no existe.",
                "solo hay alias en español en el diccionario.",
                f"use alias='es' o None (nombres de v1). Disponibles: {sorted(_ALIAS)}.",
            )
        )
    carpeta = Path(ruta)
    man = Manifiesto.leer(carpeta)
    fallos = man.verificar()
    if fallos:
        raise EscrituraSalidaError(
            mensaje_accionable(
                "la carpeta no coincide con su manifest.json:\n"
                + "\n".join(f"  - {f}" for f in fallos)
                + "\n",
                "un artefacto editado o truncado ya no es el que produjo el motor y lo que "
                "se lea de él no es reproducible.",
                "no edite los archivos de la carpeta; vuelva a escribir el resultado con "
                "escribir_resultado() o linkage(carpeta_salida=...).",
            )
        )
    correlativa = pd.read_parquet(carpeta / "correlativa.parquet")
    golden = (
        pd.read_parquet(carpeta / "golden.parquet")
        if (carpeta / "golden.parquet").is_file()
        else None
    )
    enlaces = (
        pd.read_parquet(carpeta / "enlaces.parquet")
        if (carpeta / "enlaces.parquet").is_file()
        else None
    )
    revision = pd.read_csv(
        carpeta / "revision.csv", dtype="string", keep_default_na=False, na_values=[""]
    )
    if revision.empty:
        revision = contrato.revision_vacia()
    diccionario = pd.read_csv(carpeta / "diccionario.csv", dtype="string", keep_default_na=False)
    if alias is not None:
        _aplicar_alias(correlativa, diccionario, "correlativa", alias)
        if golden is not None:
            _aplicar_alias(golden, diccionario, "golden", alias)
        if enlaces is not None:
            _aplicar_alias(enlaces, diccionario, "enlaces", alias)
        _aplicar_alias(revision, diccionario, "revision", alias)
    trabajo = carpeta / NOMBRE_TRABAJO
    dir_trabajo = trabajo if trabajo.is_dir() else None
    manifiesto: dict[str, Any] = dict(man.corrida)
    if dir_trabajo is not None:
        manifiesto = _reubicar_rutas(
            manifiesto, Path(NOMBRE_TRABAJO), dir_trabajo, tambien_resuelta=False
        )
    manifiesto["entradas"] = man.insumos
    manifiesto["parametros"] = man.llamada()
    manifiesto["configuracion"] = man.configuracion()
    manifiesto["contrato"] = {"version": man.contrato}
    manifiesto["carpeta_salida"] = str(carpeta)
    manifiesto["dir_trabajo"] = str(dir_trabajo) if dir_trabajo is not None else None
    manifiesto["manifest"] = man.a_dict()
    metricas: dict[str, Any] = {
        "n_registros": man.conteos.get("filas"),
        "n_grupos": man.conteos.get("grupos"),
        **man.metricas,
        **man.conteos,
    }
    return ResultadoLinkage(
        correlativa=correlativa,
        golden=golden,
        metricas=metricas,
        manifiesto=manifiesto,
        enlaces=enlaces,
        revision=revision,
        diccionario=diccionario,
        dir_trabajo=dir_trabajo,
    )
