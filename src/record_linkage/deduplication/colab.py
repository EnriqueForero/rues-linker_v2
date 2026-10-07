"""
deduplication.colab — utilidades de almacenamiento temporal para Google Colab.

Componentes:
    - class SQLiteJSONCache: mapping en disco con valores JSON (sin pickle).
    - class ColabOptimizedManager: raíz temporal propia, cachés en disco y
      reducción de memoria de un DataFrame (origen: notebook celda [155]).

Retirado en F2.8 (6 de octubre de 2026): ``deduplicate_large_dataset_colab``.
Nadie la importaba desde src/, scripts/ ni notebooks/ y deduplicaba por
chunks sin enlazar entidades entre chunks, así que dos duplicados en chunks
distintos quedaban en grupos distintos. El camino soportado para bases
grandes es ``api.dedupe``/``deduplicate_unified`` sobre la base completa.
``ColabOptimizedManager`` y ``SQLiteJSONCache`` se conservan: no tienen
llamadores en src/ (solo pruebas de seguridad de rutas y caché), y su retiro
no está declarado en el plan.
"""

from __future__ import annotations

import hmac
import inspect
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pandas as pd

from ..utils.logger import setup_logger
from ..utils.memory import MemoryManager


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
