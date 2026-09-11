"""engine.lsh.cache — Cache persistente de firmas MinHash entre corridas.

v0.7.2 (Sprint 0.8.2, Tarea 2.2): permite reutilizar firmas MinHash cuando
el contenido del dataset es bit-a-bit idéntico al de una corrida anterior.

Motivación
----------
Generar firmas MinHash de 1.97M registros toma ~6 min en la corrida real.
Cuando se itera (calibración con Optuna, ajustes de umbrales, debugging),
el dataset suele ser EL MISMO entre corridas — solo cambian parámetros
POSTERIORES a la generación de firmas. Cachear las firmas indexadas por
hash del contenido elimina ese trabajo redundante.

Diferencia con la reutilización HDF5 existente
----------------------------------------------
``DiskBasedLSHEngine`` ya reutiliza ``signatures.h5`` cuando existe en el
work_dir y ``_validate_signatures_file`` pasa. Esa validación es solo por
número de registros — no detecta cambios en orden, en nombres limpios, o
si cambiaste de fuente. Es válido para checkpointing dentro de UNA corrida.

Este cache es global (cross-corrida, cross-work_dir) y se invalida por
hash del CONTENIDO de ``NOMBRE_LIMPIO`` + parámetros de hashing. Es seguro
incluso si reordenas filas o reusas el work_dir para otro dataset.

Diseño
------
- Key SHA256 truncado a 16 chars del contenido + (num_perm, ngram, seed).
- Cada entry es un ``.npy`` (numpy save) — formato estable, multi-versión.
- Eviction LRU por ``max_size_gb`` (default 5 GB).
- Thread-safe a nivel de archivo (operaciones atómicas via rename).
- Opt-in: si ``cache_dir`` no se proporciona, no se hace nada.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Constantes de diseño (NO arbitrarias)
# ───────────────────────────────────────────────────────────────────────────

#: Longitud del cache key en caracteres hex (8 bytes = 16 hex chars).
#: SHA256 truncado a 64 bits — espacio de 1.8 × 10^19, colisión despreciable.
_KEY_HEX_LEN = 16

#: Default conservador para max_size_gb. 5 GB caben ~20 datasets de 1.97M
#: con num_perm=128 (cada firma pesa ~1 KB en uint64 comprimido a npy).
_DEFAULT_MAX_SIZE_GB = 5.0


class MinHashCache:
    """Cache persistente de firmas MinHash, invalidable por hash del input.

    El cache vive en disco como archivos ``.npy`` nombrados por su key.
    El acceso es por contenido + parámetros: el mismo DataFrame produce
    siempre la misma key, sin importar el work_dir o la sesión.

    Parameters
    ----------
    cache_dir : Path
        Directorio donde se guardan las firmas. Se crea si no existe.
        Recomendado: un path persistente entre corridas (NO el work_dir
        de un experimento, que se borra).
    max_size_gb : float, default 5.0
        Tamaño máximo del cache en gigabytes. Cuando se excede, se evictan
        los archivos menos recientemente usados (LRU por mtime).

    Attributes
    ----------
    hits : int
        Contador de aciertos desde la creación de la instancia.
    misses : int
        Contador de fallos desde la creación de la instancia.

    Examples
    --------
    >>> cache = MinHashCache(Path("~/.cache/rues-linker/minhash").expanduser())
    >>> sigs = cache.get(df, num_perm=128, ngram=3, seed=42)
    >>> if sigs is None:
    ...     sigs = generate_signatures(df, ...)  # 6 min
    ...     cache.put(df, num_perm=128, ngram=3, seed=42, signatures=sigs)
    >>> # Próxima corrida con mismo df → 0.5s
    """

    #: Versión del formato del cache. Bump cuando cambie el layout del .npy
    #: o el algoritmo de key — para invalidar entries viejas automáticamente.
    FORMAT_VERSION = "v2"

    def __init__(
        self,
        cache_dir: Path | str,
        max_size_gb: float = _DEFAULT_MAX_SIZE_GB,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_gb = max_size_gb
        self.hits = 0
        self.misses = 0

    # ─────────────────────────────────────────────────────────────────────
    # Cálculo de key (determinístico, independiente del orden de exploración)
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_key(
        df: pd.DataFrame,
        num_perm: int,
        ngram: int,
        seed: int,
        column: str = "NOMBRE_LIMPIO",
    ) -> str:
        """Calcula la key del cache para un DataFrame + parámetros.

        El hash combina:
          - Los parámetros del hashing (num_perm, ngram, seed) → si alguno
            cambia, la key cambia, así que NUNCA se reusa una firma de otro
            esquema.
          - Las versiones de datasketch y de esta librería (v0.8.0, F0.3):
            datasketch 2.0 demostró que un major de la dependencia produce
            hashvalues distintas con el mismo input+seed (CHANGELOG [0.7.6]);
            la versión propia cubre cambios en el shingling. Un cache
            generado bajo otro esquema jamás se reusa.
          - El número total de filas (descarta colisiones triviales entre
            datasets de tamaños distintos).
          - La huella completa de la columna, por chunks acotados. No se
            muestrean filas ni se truncan valores.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame con la columna ``column``.
        num_perm, ngram, seed : int
            Parámetros del hasher.
        column : str
            Nombre de la columna usada para el hashing.

        Returns
        -------
        str
            Key hexadecimal de ``_KEY_HEX_LEN`` caracteres.

        Raises
        ------
        KeyError
            Si ``column`` no existe en el DataFrame.
        """
        if column not in df.columns:
            raise KeyError(
                f"Columna '{column}' no encontrada para construir key del cache. "
                f"Columnas disponibles: {list(df.columns)}"
            )

        n = len(df)

        h = hashlib.sha256()
        # Versiones que definen el ESQUEMA de las firmas (F0.3, v0.8.0).
        import datasketch  # local: barato (pocas llamadas por corrida)

        try:
            from importlib.metadata import version as _pkg_version

            _rl_version = _pkg_version("rues-linker")
        except Exception:  # pragma: no cover - checkout sin instalar
            _rl_version = "0.0.0+sin.instalar"

        # Parámetros que afectan la firma — orden estable.
        h.update(
            f"fmt={MinHashCache.FORMAT_VERSION};"
            f"ds={datasketch.__version__};rl={_rl_version};"
            f"perm={num_perm};ngram={ngram};seed={seed};"
            f"n={n};col={column};".encode()
        )

        from ...pipeline.fingerprints import fingerprint_dataframe

        h.update(fingerprint_dataframe(df.loc[:, [column]]).encode("ascii"))

        return h.hexdigest()[:_KEY_HEX_LEN]

    # ─────────────────────────────────────────────────────────────────────
    # API pública: get / put / clear / stats
    # ─────────────────────────────────────────────────────────────────────

    def _path_for(self, key: str) -> Path:
        return self.cache_dir / f"signatures_{key}.npy"

    def get(
        self,
        df: pd.DataFrame,
        num_perm: int,
        ngram: int,
        seed: int,
        column: str = "NOMBRE_LIMPIO",
    ) -> np.ndarray | None:
        """Recupera firmas del cache si existen para este DataFrame.

        Returns
        -------
        ndarray or None
            Las firmas si hay hit; ``None`` si miss o si el archivo está
            corrupto (se elimina silenciosamente y se devuelve None para
            que el caller regenere).
        """
        key = self.compute_key(df, num_perm, ngram, seed, column)
        path = self._path_for(key)
        if not path.exists():
            self.misses += 1
            return None

        try:
            sigs = np.load(path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            # Archivo corrupto: evictar y tratar como miss.
            logger.warning(
                "Cache corrupto en %s (%s). Se elimina y se tratará como miss.",
                path,
                exc,
            )
            with _suppress_oserror():
                path.unlink()
            self.misses += 1
            return None

        # Validación de shape: el archivo podría ser viejo si el num_perm
        # cambió pero la key no (no debería pasar porque num_perm va EN la
        # key, pero defensiva — barato).
        if sigs.ndim != 2 or sigs.shape[1] != num_perm:
            logger.warning(
                "Cache shape inválido (%s, esperaba num_perm=%d). Evictando.",
                sigs.shape,
                num_perm,
            )
            with _suppress_oserror():
                path.unlink()
            self.misses += 1
            return None

        # Bump mtime para LRU (toca el archivo sin modificar contenido).
        with _suppress_oserror():
            os.utime(path, None)
        self.hits += 1
        return sigs

    def put(
        self,
        df: pd.DataFrame,
        num_perm: int,
        ngram: int,
        seed: int,
        signatures: np.ndarray,
        column: str = "NOMBRE_LIMPIO",
    ) -> str:
        """Guarda firmas en el cache. Devuelve la key generada.

        La escritura es atómica: primero se escribe a un archivo temporal
        en el mismo dir y luego se hace ``rename``. Esto evita dejar archivos
        a medio escribir si el proceso muere durante la escritura — un
        siguiente ``get`` vería el archivo viejo o nada, nunca un .npy
        truncado.
        """
        key = self.compute_key(df, num_perm, ngram, seed, column)
        final_path = self._path_for(key)

        # Escritura atómica: temp en mismo filesystem → rename (POSIX atomic).
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"signatures_{key}_", suffix=".npy.tmp", dir=str(self.cache_dir)
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                np.save(fh, signatures, allow_pickle=False)
            os.replace(tmp_name, final_path)
        except Exception:
            # Limpiar el temp si algo falla antes del rename.
            with _suppress_oserror():
                os.unlink(tmp_name)
            raise

        self._evict_if_needed()
        return key

    def clear(self) -> int:
        """Elimina todas las entries del cache. Devuelve cuántas eliminó."""
        n = 0
        for p in self.cache_dir.glob("signatures_*.npy"):
            with _suppress_oserror():
                p.unlink()
                n += 1
        return n

    def stats(self) -> dict[str, float | int]:
        """Devuelve estadísticas del cache (hits, misses, hit_rate, tamaño)."""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total > 0 else 0.0,
            "size_gb": self._current_size_gb(),
            "n_entries": sum(1 for _ in self.cache_dir.glob("signatures_*.npy")),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Eviction (LRU por mtime)
    # ─────────────────────────────────────────────────────────────────────

    def _current_size_gb(self) -> float:
        total_bytes = 0
        for p in self.cache_dir.glob("signatures_*.npy"):
            with _suppress_oserror():
                total_bytes += p.stat().st_size
        return total_bytes / (1024**3)

    def _evict_if_needed(self) -> int:
        """Si el tamaño excede max_size_gb, evicta entries LRU. Devuelve N evictadas."""
        size_gb = self._current_size_gb()
        if size_gb <= self.max_size_gb:
            return 0

        # Lista de (mtime, path) ordenada del más viejo al más nuevo.
        entries = []
        for p in self.cache_dir.glob("signatures_*.npy"):
            with _suppress_oserror():
                entries.append((p.stat().st_mtime, p.stat().st_size, p))
        entries.sort(key=lambda t: t[0])

        evicted = 0
        bytes_to_free = int((size_gb - self.max_size_gb * 0.9) * (1024**3))
        freed = 0
        for _mtime, size, path in entries:
            if freed >= bytes_to_free:
                break
            with _suppress_oserror():
                path.unlink()
                freed += size
                evicted += 1
        return evicted


# ───────────────────────────────────────────────────────────────────────────
# Util interno: context manager para suprimir OSError silenciosamente
# (operaciones de filesystem que pueden fallar por race conditions benignas).
# ───────────────────────────────────────────────────────────────────────────

from contextlib import contextmanager


@contextmanager
def _suppress_oserror():
    """Silencia OSError. Se usa en operaciones idempotentes (unlink, utime)."""
    try:
        yield
    except OSError as exc:
        logger.debug("OSError suprimido en operación de cache: %s", exc)
