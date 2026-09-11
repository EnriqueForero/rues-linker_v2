"""
deduplication.colab — record_linkage_pipeline

Componentes:
    - class ColabOptimizedManager  (origen: notebook celda [155])
    - function deduplicate_large_dataset_colab  (origen: notebook celda [155])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import hmac
import inspect
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import warnings
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pandas as pd

from ..utils.logger import setup_logger
from ..utils.memory import MemoryManager
from ..utils.output import safe_print as print
from .unified import _generate_non_trivial_connections, deduplicate_unified

_COLAB_STREAMING_MIN_ROWS = 1_000_000


class CrossChunkDeduplicationWarning(UserWarning):
    """El modo streaming conserva filas pero no enlaza entidades entre chunks."""


class SQLiteJSONCache(MutableMapping[str, Any]):
    """Disk-backed mapping that stores non-executable JSON in SQLite.

    Values read from the cache are retained and written back by ``sync`` or
    ``close``, preserving the mutable-value behavior of the previous
    ``shelve.open(..., writeback=True)`` API without loading pickle bytecode.
    Only JSON-compatible values are accepted intentionally.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = sqlite3.connect(self.path)
        self._writeback: dict[str, Any] = {}
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache_entries ("
            "cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
        )
        self._conn.commit()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ValueError("Caché cerrado")
        return self._conn

    @staticmethod
    def _encode(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "SQLiteJSONCache solo acepta valores compatibles con JSON "
                "(dict/list/str/int/float/bool/None)"
            ) from exc

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str):
            raise TypeError(f"La clave del caché debe ser str, no {type(key).__name__}")
        if "\x00" in key:
            raise ValueError("La clave del caché contiene NUL")
        return key

    def __getitem__(self, key: str) -> Any:
        key = self._validate_key(key)
        if key in self._writeback:
            return self._writeback[key]
        row = (
            self._connection()
            .execute("SELECT value_json FROM cache_entries WHERE cache_key=?", (key,))
            .fetchone()
        )
        if row is None:
            raise KeyError(key)
        value = json.loads(str(row[0]))
        self._writeback[key] = value
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        key = self._validate_key(key)
        encoded = self._encode(value)
        conn = self._connection()
        conn.execute(
            "INSERT INTO cache_entries(cache_key, value_json) VALUES (?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json",
            (key, encoded),
        )
        conn.commit()
        self._writeback[key] = value

    def __delitem__(self, key: str) -> None:
        key = self._validate_key(key)
        conn = self._connection()
        cursor = conn.execute("DELETE FROM cache_entries WHERE cache_key=?", (key,))
        if cursor.rowcount == 0:
            raise KeyError(key)
        conn.commit()
        self._writeback.pop(key, None)

    def __iter__(self) -> Iterator[str]:
        rows = (
            self._connection()
            .execute("SELECT cache_key FROM cache_entries ORDER BY cache_key")
            .fetchall()
        )
        return iter(str(row[0]) for row in rows)

    def __len__(self) -> int:
        row = self._connection().execute("SELECT COUNT(*) FROM cache_entries").fetchone()
        return int(row[0])

    def clear(self) -> None:
        conn = self._connection()
        conn.execute("DELETE FROM cache_entries")
        conn.commit()
        self._writeback.clear()

    def sync(self) -> None:
        """Persist in-place mutations of values previously returned by the cache."""

        conn = self._connection()
        encoded_items = [(key, self._encode(value)) for key, value in self._writeback.items()]
        with conn:
            conn.executemany(
                "INSERT INTO cache_entries(cache_key, value_json) VALUES (?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json",
                encoded_items,
            )

    def close(self) -> None:
        if self._conn is None:
            return
        conn = self._conn
        try:
            self.sync()
        finally:
            conn.close()
            self._conn = None
            self._writeback.clear()

    def __enter__(self) -> SQLiteJSONCache:
        self._connection()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class ColabOptimizedManager:
    """
    Gestor específico para las limitaciones de Google Colab
    con adaptación dinámica según el tamaño del dataset.
    """

    _TEMP_SESSION_PREFIX = ".rues_linker_colab_"
    _OWNERSHIP_MARKER = ".rues_linker_temp_owner.json"

    def __init__(self, temp_storage: str | Path | None = None):
        # Colab no garantiza una cantidad fija de RAM ni de disco. Las
        # decisiones se toman con MemoryManager y el temporal se mantiene en
        # el disco local rápido; fuera de Colab se usa el temporal del sistema.
        if temp_storage is None:
            local_root = (
                Path("/content") if Path("/content").is_dir() else Path(tempfile.gettempdir())
            )
            temp_storage = local_root / "rues_linker_temp_dedup"

        temp_root = Path(temp_storage).expanduser().absolute()
        if temp_root.is_symlink():
            raise ValueError("temp_storage no puede ser un enlace simbólico")
        temp_root.mkdir(parents=True, exist_ok=True)
        if temp_root.is_symlink() or not temp_root.is_dir():
            raise ValueError("temp_storage debe ser un directorio real")

        # ``temp_storage`` puede ser una carpeta preexistente del caller. No se
        # reclama su propiedad ni se borra su contenido: cada manager trabaja
        # en una sesión hija creada de forma exclusiva por ``mkdtemp``.
        self._temp_root = temp_root.resolve(strict=True)
        root_stat = self._temp_root.stat(follow_symlinks=False)
        self._temp_root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._temp_owner_token = secrets.token_hex(32)
        self.temp_storage = ""
        self._create_owned_temp_storage()
        self.chunk_strategies = {
            "small": {"batch_size": 50_000, "use_disk": False},
            "medium": {"batch_size": 30_000, "use_disk": True},
            "large": {"batch_size": 20_000, "use_disk": True, "aggressive_gc": True},
            "xlarge": {"batch_size": 15_000, "use_disk": True, "streaming_mode": True},
        }

    def _validated_temp_root(self) -> Path:
        """Return the configured root only while it is the original directory."""

        root = getattr(self, "_temp_root", None)
        root_identity_expected = getattr(self, "_temp_root_identity", None)
        if not isinstance(root, Path) or not isinstance(root_identity_expected, tuple):
            raise RuntimeError("El manager no tiene metadatos de propiedad temporal válidos")
        try:
            root_stat = root.stat(follow_symlinks=False)
            root_identity = (root_stat.st_dev, root_stat.st_ino)
            if (
                root.is_symlink()
                or not root.is_dir()
                or root.resolve(strict=True) != root
                or root_identity != root_identity_expected
            ):
                raise RuntimeError
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                "La raíz temporal cambió o ya no es un directorio seguro; se cancela la operación"
            ) from exc
        return root

    def _create_owned_temp_storage(self) -> None:
        """Create one private, marked session directory under the safe root."""

        root = self._validated_temp_root()
        session = Path(tempfile.mkdtemp(prefix=self._TEMP_SESSION_PREFIX, dir=root))
        if session.resolve(strict=True).parent != root:
            # ``mkdtemp`` must never escape ``dir``. Fail closed if the
            # filesystem changed concurrently; rmdir is safe because the
            # directory has just been created and must still be empty.
            try:
                session.rmdir()
            except OSError:
                pass
            raise RuntimeError("La sesión temporal escapó de la raíz configurada")

        marker = session / self._OWNERSHIP_MARKER
        payload = {
            "schema_version": 1,
            "owner_token": self._temp_owner_token,
        }
        try:
            with marker.open("x", encoding="utf-8") as marker_file:
                json.dump(payload, marker_file, sort_keys=True)
                marker_file.flush()
                os.fsync(marker_file.fileno())
            marker.chmod(0o600)
        except Exception:
            marker.unlink(missing_ok=True)
            try:
                session.rmdir()
            except OSError:
                pass
            raise

        self.temp_storage = str(session)

    def _validated_owned_temp_storage(self) -> tuple[Path, Path]:
        """Validate ownership and containment before a destructive cleanup."""

        root = self._validated_temp_root()
        target = Path(self.temp_storage)
        if target.is_symlink():
            raise RuntimeError("La sesión temporal fue reemplazada por un enlace simbólico")

        try:
            resolved_target = target.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("La sesión temporal ya no existe o no es accesible") from exc

        if (
            not resolved_target.is_dir()
            or resolved_target.parent != root
            or not resolved_target.name.startswith(self._TEMP_SESSION_PREFIX)
        ):
            raise RuntimeError("La ruta temporal no es una sesión hija propiedad de este manager")

        marker = resolved_target / self._OWNERSHIP_MARKER
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError("Falta el marcador de propiedad de la sesión temporal")
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("El marcador de propiedad temporal es inválido") from exc
        marker_token = marker_data.get("owner_token") if isinstance(marker_data, dict) else None
        if (
            not isinstance(marker_data, dict)
            or marker_data.get("schema_version") != 1
            or not isinstance(marker_token, str)
            or not hmac.compare_digest(marker_token, self._temp_owner_token)
        ):
            raise RuntimeError("La sesión temporal no pertenece a este manager")

        return root, resolved_target

    def auto_configure_for_dataset(self, n_records: int) -> dict[str, Any]:
        """
        Configura automáticamente los parámetros óptimos
        según el tamaño del dataset y memoria disponible.
        """
        current_memory = MemoryManager.get_memory_status()

        if n_records > 2_000_000 or current_memory["available_gb"] < 4:
            strategy = "xlarge"
        elif n_records > 1_000_000:
            strategy = "large"
        elif n_records > 500_000:
            strategy = "medium"
        else:
            strategy = "small"

        config: dict[str, Any] = self.chunk_strategies[strategy].copy()
        config.update(
            {
                "lsh_threshold": 0.8,  # Más estricto para reducir candidatos
                "score_threshold": 0.85,  # Más estricto para reducir memoria
                "enable_progress_bars": True,
                "checkpoint_frequency": 100_000,
                "temp_dir": self.temp_storage,
            }
        )

        return config

    def optimize_dataframe_memory(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimizar tipos de datos del DataFrame para usar menos memoria."""

        logger = setup_logger("memory_optimizer")
        initial_memory = df.memory_usage(deep=True).sum() / 1024**2

        # Optimizar tipos numéricos
        for col in df.select_dtypes(include=["int"]).columns:
            df[col] = pd.to_numeric(df[col], downcast="integer")

        for col in df.select_dtypes(include=["float"]).columns:
            df[col] = pd.to_numeric(df[col], downcast="float")

        # Convertir strings repetitivos a categorical
        for col in df.select_dtypes(include=["object"]).columns:
            num_unique_values = len(df[col].unique())
            num_total_values = len(df[col])
            if num_unique_values / num_total_values < 0.5:
                df[col] = df[col].astype("category")

        final_memory = df.memory_usage(deep=True).sum() / 1024**2
        logger.info(
            f"Memoria optimizada: {initial_memory:.1f}MB -> {final_memory:.1f}MB "
            f"({(1 - final_memory / initial_memory) * 100:.1f}% reducción)"
        )

        return df

    def create_disk_backed_cache(self, cache_name: str) -> SQLiteJSONCache:
        """Create a safe SQLite+JSON cache under the Colab temp directory.

        Legacy ``*.cache`` shelve files are deliberately never opened: they
        contain executable pickle payloads. This cache is temporary and fully
        derivable, so the safe migration is regeneration into ``*.sqlite3``.
        """

        if (
            not isinstance(cache_name, str)
            or not cache_name.strip()
            or cache_name in {".", ".."}
            or any(char in cache_name for char in ("/", "\\", "\x00"))
            or any(ord(char) < 32 for char in cache_name)
        ):
            raise ValueError("cache_name debe ser un nombre simple sin rutas")

        legacy_base = Path(self.temp_storage) / f"{cache_name}.cache"
        legacy_candidates = [
            legacy_base,
            Path(f"{legacy_base}.db"),
            Path(f"{legacy_base}.dat"),
            Path(f"{legacy_base}.dir"),
            Path(f"{legacy_base}.bak"),
        ]
        if any(path.exists() for path in legacy_candidates):
            setup_logger("colab_cache").warning(
                "Caché shelve legado ignorado por seguridad; se regenerará como SQLite+JSON"
            )

        cache_path = Path(self.temp_storage) / f"{cache_name}.cache.sqlite3"
        return SQLiteJSONCache(cache_path)

    def cleanup_temp_files(self) -> None:
        """Delete only this manager's verified session and create a fresh one.

        The caller-provided root and sibling files are never removed. On
        platforms without a symlink-safe ``rmtree`` implementation the
        cleanup fails closed instead of accepting a path-traversal risk.
        """

        root, target = self._validated_owned_temp_storage()
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError(
                "La plataforma no ofrece limpieza recursiva resistente a enlaces simbólicos"
            )

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        root_fd = os.open(root, flags)
        try:
            root_stat = root.stat(follow_symlinks=False)
            opened_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != (
                opened_stat.st_dev,
                opened_stat.st_ino,
            ):
                raise RuntimeError("La raíz temporal cambió durante la limpieza")

            # Python 3.11+ permite anclar el nombre al descriptor validado.
            # En 3.10, ``rmtree.avoids_symlink_attacks`` confirma que shutil
            # aplica internamente su recorrido seguro basado en descriptores.
            if "dir_fd" in inspect.signature(shutil.rmtree).parameters:
                # El branch solo corre en Python 3.11+, pero mypy se configura
                # con la API mínima 3.10, cuyos stubs aún no exponen dir_fd.
                shutil.rmtree(target.name, dir_fd=root_fd)  # type: ignore[call-arg]
            else:  # Python 3.10: rmtree is fd-safe but has no public dir_fd.
                shutil.rmtree(target)
        finally:
            os.close(root_fd)

        self._create_owned_temp_storage()


def deduplicate_large_dataset_colab(
    df: pd.DataFrame,
    nit_column: str = "NIT",
    name_column: str = "RAZON_SOCIAL",
    chunk_size: int | None = None,
    temp_dir: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Versión optimizada de deduplicación para datasets grandes en Google Colab.

    Procesa el dataset en chunks para mantener el uso de memoria bajo control.

    En modo streaming cada tabla correlativa parcial se persiste primero en
    Parquet y solo se materializa la tabla completa al construir el valor de
    retorno. Los identificadores de grupo e índices originales se vuelven
    globales antes de consolidar, evitando tanto pérdida de filas como
    colisiones accidentales entre chunks.

    Nota:
        La deduplicación se ejecuta de forma independiente dentro de cada
        chunk. Por tanto, dos duplicados ubicados en chunks distintos quedan en
        grupos distintos. Esta limitación conserva precisión y cobertura de
        filas, pero puede reducir recall respecto de una corrida global.
    """

    # Mantiene compatibilidad con managers personalizados/subclases cuyo
    # constructor histórico no recibe argumentos. Solo se propaga la ruta
    # cuando el caller la configuró explícitamente.
    colab_manager = (
        ColabOptimizedManager()
        if temp_dir is None
        else ColabOptimizedManager(temp_storage=temp_dir)
    )
    n_records = len(df)

    # Configurar automáticamente
    config = colab_manager.auto_configure_for_dataset(n_records)

    if chunk_size is None:
        chunk_size = config["batch_size"] * 10
    if chunk_size <= 0:
        raise ValueError("chunk_size debe ser un entero positivo")

    print("🚀 Deduplicación optimizada para Colab")
    print(f"   • Registros: {n_records:,}")
    print(f"   • Estrategia: {config}")
    print(f"   • Procesamiento en chunks de: {chunk_size:,}")

    # Optimizar memoria del DataFrame
    # Las conversiones de dtype reemplazan columnas completas. Una copia
    # superficial preserva el DataFrame del caller sin duplicar de entrada
    # todos sus buffers (pandas CoW separa solo las columnas modificadas).
    df_optimized = colab_manager.optimize_dataframe_memory(df.copy(deep=False))

    # Si el dataset es muy grande, procesar por chunks
    if n_records > _COLAB_STREAMING_MIN_ROWS and config.get("streaming_mode"):
        print("   • Modo streaming activado")
        warnings.warn(
            "El modo streaming deduplica dentro de cada chunk y consolida todas "
            "las filas, pero no enlaza duplicados ubicados en chunks distintos.",
            CrossChunkDeduplicationWarning,
            stacklevel=2,
        )

        # Dividir en chunks y persistir cada resultado. Retener la lista de
        # DataFrames duplicaba el pico de RAM y, peor aún, el código anterior
        # devolvía únicamente results[0].
        next_group_id = 0
        rows_written = 0
        position_column = "__RUES_COLAB_GLOBAL_POSITION__"
        while position_column in df_optimized.columns:
            position_column = "_" + position_column
        with tempfile.TemporaryDirectory(
            prefix="colab_consolidation_", dir=colab_manager.temp_storage
        ) as temp_name:
            parts_dir = Path(temp_name)
            for chunk_number, i in enumerate(range(0, n_records, chunk_size)):
                chunk_end = min(i + chunk_size, n_records)
                print(f"\n📦 Procesando chunk {chunk_number + 1}: registros {i:,} a {chunk_end:,}")

                chunk_df = df_optimized.iloc[i:chunk_end].copy()
                # El pipeline reinicia ORIGINAL_INDEX y puede reordenar la
                # correlativa. Esta columna viaja como dato normal y permite
                # reconstruir la posición global sin depender de ese detalle.
                chunk_df[position_column] = range(i, chunk_end)
                correlativa_chunk, conexiones_chunk = deduplicate_unified(
                    chunk_df,
                    col_nit=nit_column,
                    col_name=name_column,
                    mode="BALANCEADO",
                    profile="deduplication_colab_1M",
                    output_dir=f"{colab_manager.temp_storage}/chunk_{i}",
                    validate_against_legacy=False,
                )

                if len(correlativa_chunk) != len(chunk_df):
                    raise RuntimeError(
                        "La deduplicación de un chunk cambió su número de filas: "
                        f"entrada={len(chunk_df):,}, salida={len(correlativa_chunk):,}."
                    )
                if "ID_GRUPO" not in correlativa_chunk.columns:
                    raise RuntimeError("La tabla correlativa del chunk no contiene ID_GRUPO")
                if position_column not in correlativa_chunk.columns:
                    raise RuntimeError(
                        "La tabla correlativa no preservó el marcador de posición global"
                    )

                # Los motores reinician ID_GRUPO y ORIGINAL_INDEX en cada
                # llamada. Remapearlos evita que grupos no relacionados de dos
                # chunks parezcan la misma entidad al concatenar.
                correlativa_chunk = correlativa_chunk.copy()
                group_codes, local_groups = pd.factorize(correlativa_chunk["ID_GRUPO"], sort=False)
                if (group_codes < 0).any():
                    raise RuntimeError("La tabla correlativa contiene ID_GRUPO nulo")
                correlativa_chunk["ID_GRUPO"] = group_codes + next_group_id
                next_group_id += len(local_groups)

                global_positions = pd.to_numeric(
                    correlativa_chunk[position_column], errors="raise"
                ).astype("int64")
                positions_are_complete = (
                    len(global_positions) == chunk_end - i
                    and global_positions.nunique(dropna=False) == len(global_positions)
                    and int(global_positions.min()) == i
                    and int(global_positions.max()) == chunk_end - 1
                )
                if not positions_are_complete:
                    raise RuntimeError(
                        "El chunk no preservó exactamente sus posiciones globales: "
                        f"esperadas={i:,}..{chunk_end - 1:,}."
                    )
                correlativa_chunk["ORIGINAL_INDEX"] = global_positions

                correlativa_chunk.to_parquet(
                    parts_dir / f"correlativa_{chunk_number:06d}.parquet", index=False
                )
                rows_written += len(correlativa_chunk)

                del chunk_df, correlativa_chunk, conexiones_chunk
                gc.collect()

            if rows_written != n_records:
                raise RuntimeError(
                    "La consolidación por chunks quedó incompleta: "
                    f"entrada={n_records:,}, persistidas={rows_written:,}."
                )

            print("\n🔄 Consolidando resultados de chunks...")
            correlativa = pd.read_parquet(parts_dir)

        if len(correlativa) != n_records:
            raise RuntimeError(
                "La tabla correlativa consolidada quedó incompleta: "
                f"entrada={n_records:,}, salida={len(correlativa):,}."
            )

        correlativa = correlativa.sort_values("ORIGINAL_INDEX", kind="stable").reset_index(
            drop=True
        )
        correlativa = correlativa.drop(columns=[position_column])
        if "RECORD_COUNT" in correlativa.columns:
            correlativa["RECORD_COUNT"] = correlativa.groupby("ID_GRUPO")["ID_GRUPO"].transform(
                "size"
            )
        conexiones = _generate_non_trivial_connections(correlativa)
        scope = {
            "deduplication_scope": "within_chunk",
            "cross_chunk_linkage": False,
            "rows_consolidated": n_records,
        }
        correlativa.attrs.update(scope)
        conexiones.attrs.update(scope)
        return correlativa, conexiones

    else:
        # Procesar normalmente
        return deduplicate_unified(
            df_optimized,
            col_nit=nit_column,
            col_name=name_column,
            mode="BALANCEADO",
            profile="deduplication_colab_1M" if n_records > 500_000 else "deduplication_standard",
            output_dir=colab_manager.temp_storage,
        )
