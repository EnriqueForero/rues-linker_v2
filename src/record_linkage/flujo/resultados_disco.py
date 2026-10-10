"""Publicación y consulta de resultados Parquet sin expansión masiva en pandas.

La ingesta DuckDB produce representantes, mapas de expansión y, opcionalmente,
un payload por fila original. Este módulo es el *sink* de resultados: une esos
artefactos con la correlativa compacta, valida invariantes mediante SQL y
publica Parquet atómicos. La capa de matching no conoce detalles de exporte y
el notebook no necesita materializar millones de filas para hacer QA.

F2.5 — la generación tiene la forma del estándar
------------------------------------------------
``expandir_en_disco`` deja ``correlativa.parquet`` (expandida por DuckDB, con
``ID_REGISTRO`` reconstruido por fila) y ``golden.parquet`` (escrito por el
escritor del estándar, con ``INPUT_ROW_COUNT``) en una carpeta, verificados en
SQL. ``publicar_resultados_duckdb`` lo envuelve en el protocolo generacional
(pendiente → ``resultados.generations/<token>/`` → ``resultados.manifest.json``
→ retención) y acepta un ``completar`` que añade el resto de artefactos ANTES
del ``rename``. ``escribir_estandar_de_expansion`` es ese ``completar`` para
``flujo.cruce``: ``entidades_ids.parquet``, ``revision.csv``,
``diccionario.csv`` y ``manifest.json`` con las primitivas de
``exporters.escritor``, sin materializar la correlativa.

Por qué no ``escribir_resultado`` aquí: ese escritor parte de un
``ResultadoLinkage`` en memoria (valida, cuenta y deriva el crosswalk desde la
correlativa en pandas). En modo disco la correlativa expandida nunca entra a
pandas —ese es el contrato del modo—, así que lo que el escritor calcula
desde la tabla (conteos, crosswalk por grupo, columnas del diccionario) se
calcula aquí en SQL y se entrega a las MISMAS funciones del escritor que
arman el entregable: ``_entidades_ids_de_grupos`` (crosswalk) y
``_manifiesto_de`` (manifiesto: corrida, parámetros, tiempos y RSS por fase,
huellas, métricas del motor). Una regla, un sitio: lo que F2.4/F2.7 cambien
en ``exporters.escritor`` llega al modo disco sin copiar nada.
``leer_resultado`` lee una generación como cualquier carpeta del estándar. Lo
que ``validar()`` comprueba sobre la correlativa en pandas se comprueba sobre
el resultado COMPACTO (el que produjo el motor) y la expansión se verifica en
SQL: conteos por fuente, ``ORIGINAL_INDEX`` denso, ``ID_REGISTRO`` único, un
``ID_ENTIDAD`` por ``ID_GRUPO`` (los ``ENT-`` recalculados desde los
``ID_REGISTRO`` publicados), golden ↔ correlativa.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from numbers import Integral
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import contrato
from ..exporters.escritor import (
    NOMBRE_MANIFEST,
    Manifiesto,
    _entidades_ids_de_grupos,
    _manifiesto_de,
    _registrar,
    escribir_csv,
    escribir_parquet,
)
from ..ingestion import DuckDBCompactionResult, DuckDBIngestionSettings
from ..ingestion.duckdb import (
    _cleanup_directory,
    _cleanup_file,
    _cleanup_pending_directories,
    _fsync_directory,
    _fsync_file,
    _garbage_collect_generations,
    _ManifestFileLock,
    _relative_manifest_path,
    _validate_compaction_artifacts,
    _validate_dense_ids,
    _write_manifest_atomic,
)
from ..pipeline.errores import ContratoSalidaError, mensaje_accionable
from ..resultado import ResultadoLinkage

_MANIFEST_SCHEMA_VERSION = 1

#: ``ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo ordenados>``: la regla
#: de ``identidad.estable.asignar_id_entidad`` sin corrida anterior (``_sha16``,
#: ADR-0012) escrita en SQL para la expansión, donde los ``ID_REGISTRO``
#: publicados ya no son los que vio el motor. Por eso mismo una herencia desde
#: ``corrida_anterior`` no sobreviviría a la expansión: ``ConfigCruce`` la
#: rechaza con el motor DuckDB. El ``ORDER BY`` de DuckDB es binario, como ``sorted()`` de Python
#: sobre estos ids ASCII; ``sha256`` devuelve hex en minúsculas.
_ID_ENTIDAD_ENT_SQL = (
    """'ENT-' || left(sha256(string_agg("ID_REGISTRO", '|' ORDER BY "ID_REGISTRO")), 16)"""
)

#: Divergencia entre motores que el modo disco declara en ``manifest.json``
#: (``invariantes.divergencia_declarada``): ``expandir_en_disco`` aplica en SQL
#: la regla de ``METODO_UNION`` de ``salida.completar`` solo al caso
#: ``sin_pareja`` que la expansión cambia; un representante que es el ÚNICO
#: con la base del grupo (sus copias exactas colapsaron antes de ``completar``)
#: queda ``nombre`` aquí y ``identificador`` por pandas, que expande antes.
#: Medido en el banco de 30.486 filas: 346 filas diferían entre motores, 338
#: eran ``sin_pareja`` reconciliado y estas son las 8 residuales.
#: ``tests/test_disk_results_v016.py`` fija el caso; unificarlo exige ponderar
#: ``completar`` por copias, fuera de este módulo.
DIVERGENCIA_METODO_UNION = (
    "METODO_UNION: representante único con la base del grupo cuyas copias exactas "
    "colapsaron queda 'nombre' (pandas: 'identificador'); 8/30486 filas en el banco"
)

#: Qué hacer cuando una invariante de la expansión falla: nunca es del usuario.
_QUE_HACER_EXPANSION = (
    "no use esta generación (no se publica); reporte el caso con el manifiesto de "
    "_trabajo/ (correlativa compacta del motor y mapas de expansión de DuckDB)."
)

#: Lo que ``completar`` recibe, EN ESTE ORDEN posicional: (1) la carpeta
#: pendiente (donde escribir: existe y aún no se publicó), (2) la carpeta
#: definitiva (el nombre que tendrá tras el ``rename``: no existe todavía, solo
#: sirve para nombrarla en el manifiesto) y (3) la expansión verificada.
#: Devuelve ``{clave: nombre de archivo}`` para listar en el manifiesto
#: generacional. ``flujo.cruce._publicar_en_disco`` es el único llamador.
Completar = Callable[[Path, Path, "ExpansionDisco"], Mapping[str, str]]


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _configure_connection(connection: Any, settings: DuckDBIngestionSettings) -> None:
    connection.execute("SET memory_limit = ?", [settings.memory_limit])
    connection.execute("SET threads = ?", [settings.threads])
    # La correlativa lleva ORIGINAL_INDEX explícito. Forzar el orden físico de
    # casi un millón de filas anchas bloquea el spill del operador y puede
    # agotar incluso un memory_limit válido; las consultas que necesiten orden
    # deben usar esa columna, no depender del layout del Parquet.
    connection.execute("SET preserve_insertion_order = false")
    # Ver nota en ingestion/duckdb.py: en un kernel sin ipywidgets esta
    # opción lanza excepción. Es cosmética; nunca debe tumbar la corrida.
    with contextlib.suppress(Exception):
        connection.execute("SET enable_progress_bar = false")
    if settings.temp_directory is not None:
        temp_directory = Path(settings.temp_directory)
        temp_directory.mkdir(parents=True, exist_ok=True)
        connection.execute("SET temp_directory = ?", [str(temp_directory)])
    if settings.max_temp_directory_size is not None:
        connection.execute("SET max_temp_directory_size = ?", [settings.max_temp_directory_size])


@dataclass(frozen=True)
class TablaParquet:
    """Referencia inmutable a una tabla Parquet con materialización explícita."""

    path: Path
    rows: int
    columns: tuple[str, ...]

    def preview(self, rows: int = 100) -> pd.DataFrame:
        """Carga como máximo ``rows`` filas; nunca la tabla completa por accidente."""

        if isinstance(rows, bool) or not isinstance(rows, Integral):
            raise TypeError("rows debe ser un entero >= 1.")
        if rows < 1:
            raise ValueError("rows debe ser >= 1.")
        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                "SELECT * FROM read_parquet(?) LIMIT ?", [str(self.path), int(rows)]
            ).fetch_df()

    def to_pandas(self, *, max_rows: int | None = None) -> pd.DataFrame:
        """Materializa conscientemente y permite imponer un límite de seguridad."""

        if max_rows is not None:
            if isinstance(max_rows, bool) or not isinstance(max_rows, Integral):
                raise TypeError("max_rows debe ser un entero >= 1 o None.")
            if max_rows < 1:
                raise ValueError("max_rows debe ser >= 1 o None.")
            if self.rows > max_rows:
                raise MemoryError(
                    f"La tabla tiene {self.rows:,} filas y max_rows={max_rows:,}; "
                    "use preview() o una consulta DuckDB."
                )
        return pd.read_parquet(self.path)


@dataclass(frozen=True)
class PublicacionResultadosDisco:
    """Artefactos de una generación; rutas antiguas expiran según la retención."""

    golden: TablaParquet
    correlativa: TablaParquet
    entities: int
    source_rows: Mapping[str, int]
    manifest_path: Path
    generation_dir: Path
    generation: str

    def publicar_metadatos(self, payload: Mapping[str, Any]) -> Path:
        """Adjunta metadata JSON sólo si esta generación continúa siendo current."""

        if not isinstance(payload, Mapping):
            raise TypeError("payload de metadatos debe ser un Mapping.")
        lock = _ManifestFileLock(self.manifest_path)
        lock.acquire()
        pending: Path | None = None
        try:
            if not self.manifest_path.is_file():
                raise FileNotFoundError(self.manifest_path)
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("generation") != self.generation:
                raise RuntimeError(
                    "La generación ya no es la publicación actual; los metadatos no "
                    "pueden rebobinar el manifiesto a una corrida anterior."
                )
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict):
                raise RuntimeError("El manifiesto actual no contiene un mapa de artifacts válido.")

            token = uuid.uuid4().hex
            metadata_path = self.generation_dir / "metadatos_corrida.json"
            pending = self.generation_dir / f".metadatos_corrida.{token}.pending"
            with pending.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, metadata_path)
            pending = None
            _fsync_file(metadata_path)
            _fsync_directory(self.generation_dir)

            updated = dict(manifest)
            updated_artifacts = dict(artifacts)
            updated_artifacts["metadata"] = _relative_manifest_path(
                metadata_path, self.manifest_path.parent
            )
            updated["artifacts"] = updated_artifacts
            _write_manifest_atomic(
                self.manifest_path,
                updated,
                token=token,
                overwrite=True,
                _lock_held=True,
            )
            return metadata_path
        finally:
            if pending is not None:
                _cleanup_file(pending, purpose="metadatos pendientes")
            lock.release()

    def matriz_presencia(self) -> pd.DataFrame:
        """Matriz N×N de entidades compartidas, calculada en DuckDB."""

        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                """
                WITH presence AS (
                    SELECT DISTINCT ID_GRUPO, SRC
                    FROM read_parquet(?)
                )
                SELECT a.SRC AS FUENTE_A, b.SRC AS FUENTE_B,
                       count(DISTINCT a.ID_GRUPO)::BIGINT AS ENTIDADES_COMPARTIDAS
                FROM presence a
                JOIN presence b USING (ID_GRUPO)
                GROUP BY a.SRC, b.SRC
                ORDER BY a.SRC, b.SRC
                """,
                [str(self.correlativa.path)],
            ).fetch_df()

    def entidades_ausentes_de(self, source: str, *, limit: int = 100) -> pd.DataFrame:
        """Muestra entidades presentes en otras fuentes pero ausentes de ``source``."""

        if source not in self.source_rows:
            raise ValueError(
                f"Fuente desconocida {source!r}; use una de {sorted(self.source_rows)}."
            )
        if isinstance(limit, bool) or not isinstance(limit, Integral):
            raise TypeError("limit debe ser un entero >= 1.")
        if limit < 1:
            raise ValueError("limit debe ser >= 1.")
        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                """
                WITH presence AS (
                    SELECT DISTINCT ID_GRUPO, SRC
                    FROM read_parquet(?)
                ), absent AS (
                    SELECT ID_GRUPO
                    FROM presence
                    GROUP BY ID_GRUPO
                    HAVING count(*) FILTER (WHERE SRC = ?) = 0
                )
                SELECT g.*
                FROM read_parquet(?) g
                JOIN absent USING (ID_GRUPO)
                ORDER BY ID_GRUPO
                LIMIT ?
                """,
                [str(self.correlativa.path), str(source), str(self.golden.path), int(limit)],
            ).fetch_df()


def _assign_compact_ids(
    correlativa: pd.DataFrame,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
) -> None:
    required = {"SRC", "ORIGINAL_INDEX"}
    missing = required - set(correlativa.columns)
    if missing:
        raise RuntimeError(f"Correlativa compacta sin columnas requeridas: {sorted(missing)}.")
    source_values = correlativa["SRC"].astype("string")
    original_index = pd.to_numeric(correlativa["ORIGINAL_INDEX"], errors="raise").to_numpy()
    compact_ids: np.ndarray = np.full(len(correlativa), -1, dtype=np.int64)
    for source in source_order:
        if source not in compactaciones:
            raise KeyError(f"Falta compactación para la fuente {source!r}.")
        positions = np.flatnonzero(source_values.eq(source).fillna(False).to_numpy())
        if len(positions):
            positions = positions[np.argsort(original_index[positions], kind="stable")]
        expected = compactaciones[source].compact_rows
        if len(positions) != expected:
            raise RuntimeError(
                f"Fuente {source!r}: correlativa compacta={len(positions):,}, "
                f"representantes={expected:,}."
            )
        compact_ids[positions] = np.arange(expected, dtype=np.int64)
    if np.any(compact_ids < 0):
        unknown = correlativa.loc[compact_ids < 0, "SRC"].astype("string").unique().tolist()
        raise RuntimeError(f"La correlativa contiene fuentes no declaradas: {unknown}.")
    correlativa["__compact_record_id"] = compact_ids


def _union_expansion_sql(
    source_order: Sequence[str], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> str:
    queries = []
    source_offset = 0
    for order, source in enumerate(source_order):
        result = compactaciones[source]
        queries.append(
            "SELECT "
            f"{order}::INTEGER AS source_order, {_quote_literal(source)} AS source_name, "
            f"{source_offset}::BIGINT AS source_offset, "
            "source_row_id::BIGINT AS source_row_id, "
            "compact_record_id::BIGINT AS compact_record_id "
            f"FROM read_parquet({_quote_literal(result.expansion_map_path)})"
        )
        source_offset += result.input_rows
    return " UNION ALL ".join(queries)


def _union_payload_sql(
    source_order: Sequence[str], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> tuple[str | None, tuple[str, ...]]:
    queries: list[str] = []
    columns: list[str] = []
    for source in source_order:
        result = compactaciones[source]
        for column in result.payload_columns:
            if column not in columns:
                columns.append(column)
        if result.payload_path is not None:
            queries.append(
                "SELECT "
                f"{_quote_literal(source)} AS source_name, * "
                f"FROM read_parquet({_quote_literal(result.payload_path)})"
            )
    return (" UNION ALL BY NAME ".join(queries) if queries else None, tuple(columns))


def _validate_compaction_contracts(
    connection: Any,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
) -> dict[str, int]:
    if isinstance(source_order, (str, bytes)):
        raise TypeError("source_order debe ser una secuencia de nombres, no un string.")
    ordered = [str(source) for source in source_order]
    if not ordered or len(set(ordered)) != len(ordered):
        raise ValueError("source_order debe contener nombres únicos y al menos una fuente.")
    expected_keys = set(ordered)
    actual_keys = set(compactaciones)
    if actual_keys != expected_keys:
        raise ValueError(
            "compactaciones debe coincidir exactamente con source_order; "
            f"faltan={sorted(expected_keys - actual_keys)}, "
            f"sobran={sorted(actual_keys - expected_keys)}."
        )

    source_rows: dict[str, int] = {}
    for source in ordered:
        result = compactaciones[source]
        if result.source_name != source:
            raise ValueError(
                f"La clave {source!r} contiene una compactación de {result.source_name!r}."
            )
        if result.payload_columns and result.payload_path is None:
            raise RuntimeError(
                f"La compactación de {source!r} declara payload_columns sin payload_path."
            )
        _validate_compaction_artifacts(
            connection,
            source_name=source,
            compact_path=result.compact_path,
            expansion_path=result.expansion_map_path,
            payload_path=result.payload_path,
            input_rows=result.input_rows,
            compact_rows=result.compact_rows,
        )
        source_rows[source] = result.input_rows
    return source_rows


def _validate_final_correlation(
    connection: Any,
    path: Path,
    *,
    expected_rows: int,
    expected_source_rows: Mapping[str, int],
) -> None:
    stats = connection.execute(
        """
        SELECT count(*)::BIGINT,
               count(DISTINCT ORIGINAL_INDEX)::BIGINT,
               min(ORIGINAL_INDEX)::BIGINT,
               max(ORIGINAL_INDEX)::BIGINT,
               count(*) FILTER (WHERE ORIGINAL_INDEX IS NULL)::BIGINT,
               count(*) FILTER (WHERE ID_GRUPO IS NULL)::BIGINT
        FROM read_parquet(?)
        """,
        [str(path)],
    ).fetchone()
    null_indices = int(stats[4])
    null_groups = int(stats[5])
    if null_indices or null_groups:
        raise RuntimeError(
            "Publicación correlativa inválida: "
            f"indices_nulos={null_indices}, grupos_nulos={null_groups}."
        )
    _validate_dense_ids(
        label="correlativa.ORIGINAL_INDEX",
        rows=int(stats[0]),
        distinct_ids=int(stats[1]),
        minimum=None if stats[2] is None else int(stats[2]),
        maximum=None if stats[3] is None else int(stats[3]),
        expected=expected_rows,
    )

    actual_source_rows = {
        str(source): int(rows)
        for source, rows in connection.execute(
            "SELECT SRC, count(*)::BIGINT FROM read_parquet(?) GROUP BY SRC",
            [str(path)],
        ).fetchall()
    }
    expected = dict(expected_source_rows)
    if actual_source_rows != expected:
        raise RuntimeError(
            "Publicación correlativa alteró los conteos por fuente: "
            f"actual={actual_source_rows}, esperado={expected}."
        )


def _esquema_vacio(ruta: Path) -> pd.DataFrame:
    """DataFrame de 0 filas con los tipos del parquet (Arrow), sin leer datos.

    Sirve para el diccionario: ``contrato.diccionario`` nombra el tipo lógico
    de cada columna desde la Serie, y una Serie vacía de ``object`` diría
    ``null``; con ``ArrowDtype`` conserva el tipo del archivo.
    """
    return pq.read_schema(ruta).empty_table().to_pandas(types_mapper=pd.ArrowDtype)


def _validar_identidad_expandida(connection: Any, path: Path, columnas: Sequence[str]) -> None:
    """Lo que ``ResultadoLinkage.validar()`` exige de la identidad, en SQL.

    Solo las columnas presentes: el sink también publica tablas que no vienen
    del contrato (pruebas, herramientas).
    """
    if "ID_REGISTRO" in columnas:
        total, distintos, nulos = connection.execute(
            """
            SELECT count(*)::BIGINT, count(DISTINCT ID_REGISTRO)::BIGINT,
                   count(*) FILTER (WHERE ID_REGISTRO IS NULL)::BIGINT
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
        if int(nulos) or int(total) != int(distintos):
            raise ContratoSalidaError(
                [
                    mensaje_accionable(
                        "la correlativa expandida incumple ID_REGISTRO único y no nulo "
                        f"(filas={int(total)}, distintos={int(distintos)}, nulos={int(nulos)}).",
                        "sin un ID_REGISTRO por fila no hay trazabilidad al registro de "
                        "origen ni crosswalk estable.",
                        _QUE_HACER_EXPANSION,
                    )
                ]
            )
    if "ID_ENTIDAD" in columnas and "ID_GRUPO" in columnas:
        grupos_mixtos, entidades_partidas = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM (SELECT ID_GRUPO FROM read_parquet(?)
                 GROUP BY ID_GRUPO HAVING count(DISTINCT ID_ENTIDAD) <> 1
                    OR count(*) FILTER (WHERE ID_ENTIDAD IS NULL) > 0))::BIGINT,
              (SELECT count(*) FROM (SELECT ID_ENTIDAD FROM read_parquet(?)
                 GROUP BY ID_ENTIDAD HAVING count(DISTINCT ID_GRUPO) <> 1))::BIGINT
            """,
            [str(path), str(path)],
        ).fetchone()
        if int(grupos_mixtos) or int(entidades_partidas):
            raise ContratoSalidaError(
                [
                    mensaje_accionable(
                        "la correlativa expandida incumple un ID_GRUPO ↔ un ID_ENTIDAD "
                        f"(grupos con más de una entidad o nula={int(grupos_mixtos)}, "
                        f"entidades en más de un grupo={int(entidades_partidas)}).",
                        "ID_ENTIDAD es la identidad que se entrega aguas abajo; partida o "
                        "mezclada deja de ser comparable entre corridas.",
                        _QUE_HACER_EXPANSION,
                    )
                ]
            )


@dataclass(frozen=True)
class ExpansionDisco:
    """Lo que DuckDB dejó en ``carpeta`` al expandir, ya verificado en SQL.

    Attributes:
        carpeta: donde están ``correlativa.parquet`` y ``golden.parquet``.
        golden: referencia al golden (una fila por entidad, con ``INPUT_ROW_COUNT``).
        correlativa: referencia a la correlativa expandida (una fila por registro).
        n_grupos: ``ID_GRUPO`` distintos de la correlativa (``len(grupos)``).
        filas_por_fuente: filas publicadas por fuente (``SRC``).
        grupos: una fila por ``ID_GRUPO`` con ``N_REGISTROS`` y, si la
            correlativa los trae, ``ID_ENTIDAD`` y ``REGISTROS`` (la lista
            ordenada de ``ID_REGISTRO`` del grupo, F2.18): de aquí sale el
            crosswalk de la corrida (``escritor._entidades_ids_de_grupos`` →
            ``identidad.estable.crosswalk_de_corrida``) sin pasar la tabla por
            pandas.

    Es una clase nueva (F2.5) y va en español; ``PublicacionResultadosDisco``
    (``entities``, ``source_rows``) es anterior y conserva sus nombres porque
    la leen notebooks y pruebas de 0.16.0.
    """

    carpeta: Path
    golden: TablaParquet
    correlativa: TablaParquet
    n_grupos: int
    filas_por_fuente: Mapping[str, int]
    grupos: pd.DataFrame

    @property
    def esquema_correlativa(self) -> pd.DataFrame:
        """0 filas con los tipos publicados (para el diccionario)."""
        return _esquema_vacio(self.correlativa.path)

    @property
    def esquema_golden(self) -> pd.DataFrame:
        return _esquema_vacio(self.golden.path)


def expandir_en_disco(
    golden: pd.DataFrame,
    correlativa_compacta: pd.DataFrame,
    *,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
    carpeta: Path,
    settings: DuckDBIngestionSettings | None = None,
) -> ExpansionDisco:
    """Expande los representantes a una fila por registro y publica las dos tablas en ``carpeta``.

    * ``correlativa.parquet``: la escribe DuckDB (``COPY``) uniendo el mapa de
      expansión y el payload de cada fuente; ``ORIGINAL_INDEX`` pasa a ser la
      posición en la concatenación de las fuentes originales y, si la tabla
      trae ``ID_REGISTRO``, se reconstruye por fila como ``<SRC>-F<ORIGINAL_INDEX>``
      —la misma regla de ``salida.completar`` cuando no hay ``col_id``, que es
      el único caso de ``flujo.cruce``—; sin esto el ``ID_REGISTRO`` del
      representante se repetiría en cada fila que colapsó. ``METODO_UNION``
      deja de ser ``sin_pareja`` en los grupos de un representante que al
      expandir tienen más de una fila (``identificador`` si el grupo adoptó
      identificador, ``nombre`` si no: la regla de ``salida.completar``; medido
      en el banco de 30.486 filas, 338 de las 346 filas que diferían entre
      motores eran esto). Las 8 residuales son ``DIVERGENCIA_METODO_UNION``:
      ``escribir_estandar_de_expansion`` la declara en el manifiesto.
      ``ID_ENTIDAD``: los ``NIT-`` se conservan tal como los asignó el motor
      (valor por grupo); los ``ENT-`` se RECALCULAN en SQL con la regla de
      ``identidad.estable`` sin corrida anterior (``ENT-<sha16 de los
      ID_REGISTRO del grupo ordenados>``) sobre los ``ID_REGISTRO`` publicados: el motor los hashó
      sobre los representantes, ids que tras expandir no existen o son de otra
      fila, y la misma entrada daría ids distintos por motor. El golden recibe
      el mismo valor por ``ID_GRUPO``.
    * ``golden.parquet``: lo escribe ``exporters.escritor.escribir_parquet``
      con el esquema del contrato y ``INPUT_ROW_COUNT`` = filas expandidas por
      grupo (``RECORD_COUNT`` sigue contando representantes).
    * Invariantes, todas en SQL: contratos de compactación, conteos por
      fuente, ``ORIGINAL_INDEX`` denso y no nulo, ``ID_GRUPO`` no nulo,
      ``ID_REGISTRO`` único, un ``ID_ENTIDAD`` por ``ID_GRUPO``, golden con
      exactamente una fila por grupo de la correlativa.

    Si algo falla, lo escrito en ``carpeta`` se borra y la excepción sale.

    Raises:
        ContratoSalidaError: una invariante del contrato sobre la tabla
            publicada o el golden (``ID_REGISTRO``/``ID_ENTIDAD``, golden ↔
            correlativa); ``RuntimeError`` para los artefactos de compactación.
    """
    import duckdb

    effective_settings = settings or DuckDBIngestionSettings()
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        raise FileNotFoundError(f"La carpeta de expansión no existe: {carpeta}")
    correlativa_path = carpeta / "correlativa.parquet"
    golden_path = carpeta / "golden.parquet"
    compact_path = carpeta / ".correlativa_compacta.parquet"

    connection: Any | None = None
    listo = False
    try:
        connection = duckdb.connect(":memory:")
        _configure_connection(connection, effective_settings)
        expected_source_rows = _validate_compaction_contracts(
            connection, source_order, compactaciones
        )
        expected_rows = sum(expected_source_rows.values())

        correlativa_trabajo = correlativa_compacta.copy(deep=False)
        _assign_compact_ids(correlativa_trabajo, source_order, compactaciones)
        correlativa_trabajo.to_parquet(compact_path, index=False)
        expansion_sql = _union_expansion_sql(source_order, compactaciones)
        payload_sql, payload_columns = _union_payload_sql(source_order, compactaciones)

        original_index_sql = "CAST(e.source_offset + e.source_row_id AS BIGINT)"
        correlative_projection: list[str] = []
        for column in correlativa_trabajo.columns:
            if column == "__compact_record_id":
                continue
            if column == "ORIGINAL_INDEX":
                correlative_projection.append(f'{original_index_sql} AS "ORIGINAL_INDEX"')
            elif column == "ID_REGISTRO":
                # Regla de salida.completar sin col_id: <SRC>-F<ORIGINAL_INDEX>.
                correlative_projection.append(
                    f"c.SRC || '-F' || CAST({original_index_sql} AS VARCHAR) AS \"ID_REGISTRO\""
                )
            elif column == "METODO_UNION" and {"ID_GRUPO", "NIT_FINAL"} <= set(
                correlativa_trabajo.columns
            ):
                # Un representante solo en su grupo dijo ``sin_pareja``; si al
                # expandir el grupo tiene más de una fila (copias exactas de ese
                # registro), ya no lo es: es la misma regla de salida.completar
                # —``identificador`` si el grupo adoptó un identificador válido,
                # que en un grupo de un representante es el de ese registro;
                # ``nombre`` si no—, aplicada al único caso que la expansión cambia.
                correlative_projection.append(
                    "CASE WHEN c.\"METODO_UNION\" = 'sin_pareja' "
                    'AND count(*) OVER (PARTITION BY c."ID_GRUPO") > 1 '
                    'THEN CASE WHEN c."NIT_FINAL" IS NOT NULL AND c."NIT_FINAL" <> \'\' '
                    "THEN 'identificador' ELSE 'nombre' END "
                    'ELSE c."METODO_UNION" END AS "METODO_UNION"'
                )
            else:
                correlative_projection.append(f"c.{_quote_identifier(column)}")
        for column in payload_columns:
            if column not in correlativa_trabajo.columns:
                correlative_projection.append(f"p.{_quote_identifier(column)}")

        payload_join = (
            "LEFT JOIN payload p ON p.source_name = e.source_name "
            "AND p.source_row_id = e.source_row_id"
            if payload_sql
            else ""
        )
        ctes = [f"expansion AS ({expansion_sql})"]
        if payload_sql:
            ctes.append(f"payload AS ({payload_sql})")
        ctes.append(
            "filas AS (SELECT "
            + ", ".join(correlative_projection)
            + f" FROM expansion e JOIN read_parquet({_quote_literal(compact_path)}) c "
            "ON c.SRC = e.source_name AND c.__compact_record_id = e.compact_record_id "
            + payload_join
            + ")"
        )
        if {"ID_REGISTRO", "ID_GRUPO", "ID_ENTIDAD"} <= set(correlativa_trabajo.columns):
            # Los ENT- se recalculan sobre los ID_REGISTRO publicados (ver docstring).
            ctes.append(
                f'entidades AS (SELECT "ID_GRUPO", {_ID_ENTIDAD_ENT_SQL} AS "ID_ENTIDAD_PUBLICADA" '
                'FROM filas WHERE "ID_ENTIDAD" LIKE \'ENT-%\' GROUP BY "ID_GRUPO")'
            )
            seleccion = (
                'SELECT filas.* REPLACE (coalesce(entidades."ID_ENTIDAD_PUBLICADA", '
                'filas."ID_ENTIDAD") AS "ID_ENTIDAD") '
                'FROM filas LEFT JOIN entidades USING ("ID_GRUPO")'
            )
        else:
            seleccion = "SELECT * FROM filas"
        correlation_query = "WITH " + ", ".join(ctes) + " " + seleccion
        compression = effective_settings.parquet_compression.upper()
        connection.execute(
            "COPY ("
            + correlation_query
            + ") TO "
            + _quote_literal(correlativa_path)
            + f" (FORMAT PARQUET, COMPRESSION {compression})"
        )
        _validate_final_correlation(
            connection,
            correlativa_path,
            expected_rows=expected_rows,
            expected_source_rows=expected_source_rows,
        )
        correlativa_schema = tuple(
            str(row[0])
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(correlativa_path)]
            ).fetchall()
        )
        _validar_identidad_expandida(connection, correlativa_path, correlativa_schema)

        entidad_sql = (
            "any_value(ID_ENTIDAD) AS ID_ENTIDAD, " if "ID_ENTIDAD" in correlativa_schema else ""
        )
        # REGISTROS (F2.18): la lista ORDENADA de ID_REGISTRO por grupo, lo que
        # el crosswalk del estándar lleva por entidad (ADR-0012); el escritor la
        # despliega y aplica la misma regla que en pandas (crosswalk_de_corrida).
        registros_sql = (
            ", list(ID_REGISTRO ORDER BY ID_REGISTRO) AS REGISTROS"
            if "ID_REGISTRO" in correlativa_schema
            else ""
        )
        grupos = connection.execute(
            f"SELECT ID_GRUPO, {entidad_sql}count(*)::BIGINT AS N_REGISTROS{registros_sql} "
            "FROM read_parquet(?) GROUP BY ID_GRUPO ORDER BY ID_GRUPO",
            [str(correlativa_path)],
        ).fetch_df()
        source_rows = {
            str(source): int(rows)
            for source, rows in connection.execute(
                "SELECT SRC, count(*)::BIGINT FROM read_parquet(?) GROUP BY SRC ORDER BY SRC",
                [str(correlativa_path)],
            ).fetchall()
        }
        n_grupos = len(grupos)

        if "ID_GRUPO" not in golden.columns:
            raise ContratoSalidaError(
                [
                    mensaje_accionable(
                        "el golden no trae ID_GRUPO.",
                        "sin ella no hay forma de casar cada entidad con sus filas de la "
                        "correlativa ni de contar INPUT_ROW_COUNT.",
                        _QUE_HACER_EXPANSION,
                    )
                ]
            )
        golden_ids = golden["ID_GRUPO"]
        golden_nulls = int(golden_ids.isna().sum())
        golden_distinct = int(golden_ids.nunique(dropna=True))
        # Primero «difieren» si los conjuntos no coinciden; después la
        # unicidad por entidad (una fila no nula por grupo de la correlativa).
        mismatch = len(set(golden_ids.dropna().tolist()) ^ set(grupos["ID_GRUPO"].tolist()))
        if mismatch:
            raise ContratoSalidaError(
                [
                    mensaje_accionable(
                        f"golden y correlativa difieren en {mismatch} grupos.",
                        "una entidad sin filas, o filas sin entidad, rompen golden ↔ "
                        "correlativa, que es lo que el crosswalk y los reportes L6 suponen.",
                        _QUE_HACER_EXPANSION,
                    )
                ]
            )
        if golden_nulls or len(golden) != golden_distinct or golden_distinct != n_grupos:
            raise ContratoSalidaError(
                [
                    mensaje_accionable(
                        "el golden debe contener exactamente una fila no nula por entidad; "
                        f"filas={len(golden)}, IDs_distintos={golden_distinct}, "
                        f"grupos={n_grupos}, IDs_nulos={golden_nulls}.",
                        "un golden con entidades repetidas o nulas duplica o pierde "
                        "registros consolidados en el entregable.",
                        _QUE_HACER_EXPANSION,
                    )
                ]
            )
        conteos = grupos.set_index("ID_GRUPO")["N_REGISTROS"]
        golden_salida = golden.sort_values("ID_GRUPO", kind="stable").reset_index(drop=True)
        golden_salida["INPUT_ROW_COUNT"] = (
            golden_salida["ID_GRUPO"].map(conteos).fillna(0).astype("int64")
        )
        if "ID_ENTIDAD" in grupos.columns and "ID_ENTIDAD" in golden_salida.columns:
            # El motor asignó el ID_ENTIDAD del golden sobre representantes; la
            # correlativa publicada lo recalculó para los ENT-: un valor por grupo.
            entidad = grupos.set_index("ID_GRUPO")["ID_ENTIDAD"]
            golden_salida["ID_ENTIDAD"] = golden_salida["ID_GRUPO"].map(entidad).astype(str)
        escribir_parquet(golden_salida, golden_path, contrato.GOLDEN)
        golden_schema = tuple(str(c) for c in golden_salida.columns)
        listo = True
    finally:
        if connection is not None:
            connection.close()
        compact_path.unlink(missing_ok=True)
        if not listo:
            correlativa_path.unlink(missing_ok=True)
            golden_path.unlink(missing_ok=True)

    return ExpansionDisco(
        carpeta=carpeta,
        golden=TablaParquet(golden_path, len(golden_salida), golden_schema),
        correlativa=TablaParquet(correlativa_path, expected_rows, correlativa_schema),
        n_grupos=n_grupos,
        filas_por_fuente=MappingProxyType(source_rows),
        grupos=grupos,
    )


def _conteos_expansion(res: ResultadoLinkage, expansion: ExpansionDisco) -> dict[str, Any]:
    """``conteos`` del manifiesto con las mismas claves que el escritor, desde SQL."""
    entidades = expansion.grupos["ID_ENTIDAD"].drop_duplicates()
    con_nit = int(entidades.astype(str).str.startswith("NIT-").sum())
    return {
        "filas": expansion.correlativa.rows,
        "grupos": expansion.n_grupos,
        "entidades": len(entidades),
        "entidades_con_nit": con_nit,
        "entidades_sin_nit": len(entidades) - con_nit,
        "fuentes": len(expansion.filas_por_fuente),
        "filas_por_fuente": dict(expansion.filas_por_fuente),
        "golden": expansion.golden.rows,
        "enlaces": len(res.enlaces) if res.enlaces is not None else None,
        "revision": len(res.revision),
    }


def escribir_estandar_de_expansion(
    res: ResultadoLinkage,
    expansion: ExpansionDisco,
    *,
    nombre: str,
    definitiva: Path,
    insumos: Mapping[str, Any] | None = None,
    columnas_fuente: Sequence[str] = (),
    marca_tiempo: datetime | None = None,
) -> Manifiesto:
    """Completa ``expansion.carpeta`` hasta la forma del estándar y devuelve su manifiesto.

    ``res`` es el resultado COMPACTO que devolvió ``linkage()`` (el que el
    motor produjo y validó); de él salen la trazabilidad (``manifiesto``), las
    métricas del motor, ``revision`` y el diccionario base. Lo que depende de
    la tabla publicada (conteos, crosswalk, columnas del diccionario) sale de
    ``expansion``. ``insumos`` reemplaza ``res.manifiesto['entradas']`` en el
    manifiesto cuando las filas reales no son las que vio el motor (DuckDB
    colapsó duplicados exactos antes); ``columnas_fuente`` añade al diccionario
    las columnas de payload que el motor no vio.

    Lo que se omite se declara: ``excel/`` nunca se escribe en modo disco.

    Raises:
        ContratoSalidaError: si la correlativa expandida no trae ``ID_ENTIDAD``.
    """
    carpeta = expansion.carpeta
    if "ID_ENTIDAD" not in expansion.grupos.columns:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    "la correlativa expandida no trae ID_ENTIDAD.",
                    "sin ella no hay crosswalk (entidades_ids.parquet) ni carpeta del estándar.",
                    "publique con la correlativa que devuelve linkage() (contrato 1.0).",
                )
            ]
        )
    artefactos: list[dict[str, Any]] = []
    omitidos: list[dict[str, str]] = []
    _registrar(artefactos, carpeta, expansion.correlativa.path)
    _registrar(artefactos, carpeta, expansion.golden.path)

    # Crosswalk (F2.4 + F2.18): la tabla por grupo trae REGISTROS (lista
    # ordenada de ID_REGISTRO, calculada en SQL) y el escritor aplica la MISMA
    # función que en pandas (identidad.estable.crosswalk_de_corrida), así que
    # la generación lleva REGISTROS y sirve como corrida_anterior de una corrida
    # posterior con motor pandas (tests/test_flujo_cruce.py lo fija). Sin
    # retiros: el modo disco no recibe corrida_anterior (ConfigCruce lo
    # rechaza con error accionable; la expansión recalcula los ENT- por
    # contenido y la herencia no sobreviviría).
    entidades_ids = _entidades_ids_de_grupos(expansion.grupos)
    ruta = carpeta / "entidades_ids.parquet"
    escribir_parquet(entidades_ids, ruta, contrato.ENTIDADES_IDS)
    _registrar(artefactos, carpeta, ruta)

    ruta = carpeta / "revision.csv"
    escribir_csv(res.revision, ruta)
    _registrar(artefactos, carpeta, ruta)

    completar = res.manifiesto.get("completar") or {}
    diccionario = contrato.diccionario(
        {
            "correlativa": expansion.esquema_correlativa,
            "golden": expansion.esquema_golden,
            "enlaces": None,
            "revision": res.revision,
            "entidades_ids": entidades_ids,
        },
        columnas_fuente=[*completar.get("columnas_fuente", ()), *columnas_fuente],
        renombres=completar.get("renombres"),
        renombres_canonicos=completar.get("renombres_canonicos"),
    )
    ruta = carpeta / "diccionario.csv"
    escribir_csv(diccionario, ruta)
    _registrar(artefactos, carpeta, ruta)

    for tabla in ("correlativa", "golden"):
        omitidos.append(
            {
                "artefacto": f"excel/{tabla}.xlsx",
                "motivo": "modo_resultado='disco' no materializa la salida; lea el parquet "
                "con leer_resultado(), pandas o DuckDB.",
            }
        )
    filas = expansion.correlativa.rows
    manifiesto = _manifiesto_de(
        res,
        nombre=nombre,
        marca_tiempo=marca_tiempo if marca_tiempo is not None else datetime.now(),
        definitiva=definitiva,
        artefactos=artefactos,
        omitidos=omitidos,
        insumos=insumos,
        conteos=_conteos_expansion(res, expansion),
        # El motor midió sobre representantes; la tabla publicada tiene las filas reales.
        metricas_extra={
            "n_registros": filas,
            "n_grupos": expansion.n_grupos,
            "tasa_reduccion": 1 - expansion.n_grupos / filas if filas else None,
        },
        invariantes_extra={
            "validado_sobre": "resultado compacto del motor (modo disco)",
            "verificado_en_sql": [
                "conteos por fuente",
                "ORIGINAL_INDEX denso y no nulo",
                "ID_GRUPO no nulo",
                "ID_REGISTRO único",
                "un ID_ENTIDAD por ID_GRUPO",
                "ID_ENTIDAD ENT- recalculado desde los ID_REGISTRO publicados",
                "golden: una fila por grupo de la correlativa",
            ],
            # Lo que el modo disco calcula distinto del modo dataframe, dicho en
            # el manifiesto y no solo en un docstring (ver expandir_en_disco).
            "divergencia_declarada": DIVERGENCIA_METODO_UNION,
        },
    )
    manifiesto.guardar(carpeta / NOMBRE_MANIFEST)
    return manifiesto


def publicar_resultados_duckdb(
    golden: pd.DataFrame,
    correlativa_compacta: pd.DataFrame,
    *,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
    output_directory: str | Path,
    settings: DuckDBIngestionSettings | None = None,
    overwrite: bool = True,
    completar: Completar | None = None,
) -> PublicacionResultadosDisco:
    """Expande y publica una generación completa con un manifiesto atómico.

    ``completar(pendiente, definitiva, expansion)`` corre dentro de la
    generación pendiente, después de verificar las dos tablas y antes del
    ``rename``: lo que escriba se publica con ellas o no se publica (un fallo
    limpia la pendiente y conserva la generación anterior). Devuelve
    ``{clave: archivo}`` para listarlo en ``resultados.manifest.json``.
    """

    if not isinstance(overwrite, bool):
        raise TypeError("overwrite debe ser bool.")
    effective_settings = settings or DuckDBIngestionSettings()
    output_dir = Path(output_directory).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "resultados.manifest.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(
            f"Ya existe el manifiesto autoritativo {manifest_path}; "
            "use overwrite=True para publicar una nueva generación."
        )

    manifest_lock = _ManifestFileLock(manifest_path)
    manifest_lock.acquire()
    pending_dir: Path | None = None
    try:
        if manifest_path.exists() and not overwrite:
            raise FileExistsError(
                f"Ya existe el manifiesto autoritativo {manifest_path}; "
                "use overwrite=True para publicar una nueva generación."
            )
        _cleanup_pending_directories(output_dir, "resultados")
        token = uuid.uuid4().hex
        generations_dir = output_dir / "resultados.generations"
        generations_dir.mkdir(parents=True, exist_ok=True)
        pending_dir = output_dir / f".resultados.{token}.generation.pending"
        pending_dir.mkdir()
        generation_dir = generations_dir / token
    except Exception:
        manifest_lock.release()
        raise

    try:
        expansion = expandir_en_disco(
            golden,
            correlativa_compacta,
            source_order=source_order,
            compactaciones=compactaciones,
            carpeta=pending_dir,
            settings=effective_settings,
        )
        extra = dict(completar(pending_dir, generation_dir, expansion)) if completar else {}
        for archivo in sorted(pending_dir.iterdir()):
            if archivo.is_file():
                _fsync_file(archivo)
        _fsync_directory(pending_dir)
        os.replace(pending_dir, generation_dir)
        _fsync_directory(generations_dir)
        golden_path = generation_dir / expansion.golden.path.name
        correlativa_path = generation_dir / expansion.correlativa.path.name
        artifacts = {
            "golden": _relative_manifest_path(golden_path, output_dir),
            "correlativa": _relative_manifest_path(correlativa_path, output_dir),
        }
        for clave, nombre_archivo in extra.items():
            artifacts[str(clave)] = _relative_manifest_path(
                generation_dir / nombre_archivo, output_dir
            )
        manifest = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "kind": "duckdb_results",
            "generation": token,
            "artifacts": artifacts,
            "metrics": {
                "golden_rows": expansion.golden.rows,
                "correlativa_rows": expansion.correlativa.rows,
                "entities": expansion.n_grupos,
                "source_rows": dict(expansion.filas_por_fuente),
            },
            "schemas": {
                "golden": list(expansion.golden.columns),
                "correlativa": list(expansion.correlativa.columns),
            },
            "retention": {
                "previous_generations_to_keep": (effective_settings.previous_generations_to_keep)
            },
        }
        _write_manifest_atomic(
            manifest_path,
            manifest,
            token=token,
            overwrite=overwrite,
            _lock_held=True,
        )
        _garbage_collect_generations(
            generations_dir,
            current_generation=generation_dir,
            previous_to_keep=effective_settings.previous_generations_to_keep,
        )
        return PublicacionResultadosDisco(
            golden=TablaParquet(golden_path, expansion.golden.rows, expansion.golden.columns),
            correlativa=TablaParquet(
                correlativa_path, expansion.correlativa.rows, expansion.correlativa.columns
            ),
            entities=expansion.n_grupos,
            source_rows=expansion.filas_por_fuente,
            manifest_path=manifest_path,
            generation_dir=generation_dir,
            generation=token,
        )
    finally:
        if pending_dir is not None:
            _cleanup_directory(pending_dir, purpose="generación pendiente de resultados")
        manifest_lock.release()


__all__ = [
    "DIVERGENCIA_METODO_UNION",
    "ExpansionDisco",
    "PublicacionResultadosDisco",
    "TablaParquet",
    "escribir_estandar_de_expansion",
    "expandir_en_disco",
    "publicar_resultados_duckdb",
]
