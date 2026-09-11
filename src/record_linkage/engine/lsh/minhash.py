"""
engine.minhash — record_linkage_pipeline

Componentes:
    - class VectorizedMinHashGenerator  (origen: notebook celda [195])
    - function _splitmix64  (origen: notebook celda [195])
    - function _generate_signatures_vectorized  (origen: notebook celda [195])
    - function _validate_signatures_vectorized  (origen: notebook celda [195])
    - function _jaccard_real  (origen: notebook celda [195])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import time
from typing import Any

import h5py
import numpy as np
import pandas as pd
from tqdm import tqdm

from ._constants import _MERSENNE_PRIME
from .state import EngineState


class VectorizedMinHashGenerator:
    """
    Generador de firmas MinHash con hashing universal vectorizado.

    Reemplaza datasketch.MinHash con NumPy puro:
    - Sin objetos Python por registro (0 instancias vs 1.97M)
    - Sin hash criptográfico SHA-1 (SplitMix64 vectorizado)
    - Operaciones vectorizadas por registro (broadcasting NumPy)

    Pipeline por texto:
        texto → bytes → n-gram codes (uint64) → SplitMix64 → (a·h+b) mod p → min

    Atributos:
        VERSION:   Identificador para invalidar caché HDF5 de firmas antiguas
        _num_perm: Número de permutaciones (funciones hash independientes)
        _ngram:    Tamaño de n-gramas en bytes
        _a, _b:    Coeficientes de hash universal (deterministas vía seed)
    """

    VERSION = "v4.2.0-vectorized"

    def __init__(self, num_perm: int = 252, ngram: int = 2, seed: int = 1):
        """
        Args:
            num_perm: Número de permutaciones (debe coincidir con config LSH)
            ngram:    Tamaño de n-gramas (2 para el pipeline actual)
            seed:     Semilla para determinismo estricto (misma seed = mismas firmas)

        Raises:
            ValueError: Si num_perm < 1 o ngram fuera de [1, 5]
        """
        if num_perm < 1:
            raise ValueError(f"num_perm debe ser >= 1, recibido: {num_perm}")
        if ngram < 1 or ngram > 5:
            raise ValueError(f"ngram debe estar en [1, 5], recibido: {ngram}")

        self._num_perm = num_perm
        self._ngram = ngram
        self._seed = seed

        # ── Coeficientes de hash universal: h_i(x) = (a_i * x + b_i) mod p ──
        # Rango de 'a' calculado para evitar overflow uint64:
        #   max(a) * max(SplitMix64(code)) debe caber en uint64
        #   SplitMix64 produce valores en [0, 2^64-1], así que a * hash SIEMPRE
        #   desborda uint64 → el overflow mod 2^64 actúa como mixing adicional.
        #   Esto es idéntico a cómo datasketch maneja el overflow internamente.
        rng = np.random.RandomState(seed)
        self._a = rng.randint(1, (1 << 32), size=num_perm).astype(np.uint64)
        self._b = rng.randint(0, (1 << 32), size=num_perm).astype(np.uint64)

        self._max_val = np.iinfo(np.uint64).max
        self._empty_sig = np.full(num_perm, self._max_val, dtype=np.uint64)

    def _encode_ngrams(self, text: str) -> np.ndarray | None:
        """
        Codifica n-gramas de un texto como enteros uint64.

        Cada n-grama se convierte a su representación numérica
        concatenando los valores de bytes via bit-shift:
            "AB" → ord('A')<<8 | ord('B') = 16706

        Nota: opera sobre BYTES (no caracteres). Para texto ASCII limpio
        (que es NOMBRE_LIMPIO), bytes == caracteres. Para UTF-8 multi-byte,
        los n-gramas de bytes son más granulares — esto no afecta la calidad
        del MinHash (solo requiere consistencia interna).

        Args:
            text: Texto limpio (se asume UTF-8 válido).

        Returns:
            Array uint64 de códigos de n-gramas, o None si el texto
            es más corto que el tamaño del n-grama.
        """
        raw = text.encode("utf-8")
        n_bytes = len(raw)
        n = self._ngram

        if n_bytes < n:
            return None

        arr = np.frombuffer(raw, dtype=np.uint8).astype(np.uint64)

        # Codificación por shift de bytes — O(n_bytes), vectorizado
        if n == 2:
            return (arr[:-1] << np.uint64(8)) | arr[1:]
        elif n == 3:
            return (arr[:-2] << np.uint64(16)) | (arr[1:-1] << np.uint64(8)) | arr[2:]
        else:
            # General: polynomial byte encoding
            result = np.zeros(n_bytes - n + 1, dtype=np.uint64)
            for k in range(n):
                result |= arr[k : n_bytes - n + 1 + k] << np.uint64(8 * (n - 1 - k))
            return result

    def compute_signature(self, text: Any) -> np.ndarray:
        """
        Computa firma MinHash para un texto individual.

        Pipeline:
            1. Validar input
            2. Codificar n-gramas como uint64
            3. Dispersar con SplitMix64 (romper monotonía)
            4. Aplicar permutaciones universales (broadcasting)
            5. Reducir a mínimos por permutación

        Args:
            text: Texto a procesar (str, NaN, None, o cualquier tipo).

        Returns:
            Array uint64 de tamaño num_perm. Para inputs inválidos,
            retorna un array lleno de max_val (excluido de comparaciones LSH).
        """
        if not isinstance(text, str) or len(text) < self._ngram:
            return self._empty_sig.copy()

        codes = self._encode_ngrams(text)
        if codes is None or len(codes) == 0:
            return self._empty_sig.copy()

        # ── Pipeline vectorizado ──
        hashed_codes = _splitmix64(codes)

        # Broadcasting: (n_ngrams, 1) × (1, num_perm) → (n_ngrams, num_perm)
        # El overflow uint64 en a * hashed_codes es intencional (mixing)
        hashes = (self._a * hashed_codes[:, None] + self._b) % _MERSENNE_PRIME

        # Mínimo por columna (por permutación) → shape (num_perm,)
        return hashes.min(axis=0)

    def compute_batch(self, texts: np.ndarray) -> np.ndarray:
        """
        Genera firmas MinHash para un lote completo de textos.

        El loop externo es Python (necesario: strings de longitud variable),
        pero el trabajo pesado por texto es 100% NumPy:
        - SplitMix64: 3 operaciones vectorizadas sobre array de n-gramas
        - Permutaciones: un broadcasting (n_ngrams × num_perm)
        - Mínimo: una reducción .min(axis=0)

        Costo típico: ~35μs por texto (vs ~2,700μs con datasketch).

        Args:
            texts: Array de strings (puede contener NaN, None, no-strings).

        Returns:
            Array uint64 de shape (n_texts, num_perm).
        """
        n = len(texts)
        if n == 0:
            return np.empty((0, self._num_perm), dtype=np.uint64)

        signatures = np.empty((n, self._num_perm), dtype=np.uint64)

        # ── Cache locales para hot loop (evita lookups de atributo) ──
        a = self._a
        b = self._b
        prime = _MERSENNE_PRIME
        ngram = self._ngram
        max_val = self._max_val
        encode = self._encode_ngrams

        for i in range(n):
            text = texts[i]

            # Fast path: tipos inválidos (~0.1μs)
            if not isinstance(text, str) or len(text) < ngram:
                signatures[i] = max_val
                continue

            # Codificar n-gramas (~2μs)
            codes = encode(text)
            if codes is None or len(codes) == 0:
                signatures[i] = max_val
                continue

            # SplitMix64 + permutaciones + min (~30μs) — TODO vectorizado
            hashed = _splitmix64(codes)
            signatures[i] = ((a * hashed[:, None] + b) % prime).min(axis=0)

        return signatures


def _splitmix64(x: np.ndarray) -> np.ndarray:
    """
    SplitMix64: función hash bijective con excelente avalancha.

    Necesaria porque la codificación byte de n-gramas es monotónica
    (ej: "AB" < "AC" < "AD"), y una función hash lineal (a*x+b)
    preservaría ese orden → min(h(set)) dependería solo del mínimo
    del set, colapsando toda la información del MinHash.

    SplitMix64 rompe la monotonía: pequeños cambios en input generan
    cambios impredecibles en output. Es bijective (sin colisiones)
    y completamente vectorizable en NumPy.

    Referencia: Steele et al., SplitMix (2014).

    Args:
        x: Array uint64 de códigos de n-gramas.

    Returns:
        Array uint64 con valores pseudo-aleatorios bien distribuidos.
    """
    x = np.asarray(x, dtype=np.uint64)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    x = x ^ (x >> np.uint64(31))
    return x


def _generate_signatures_vectorized(self, df: pd.DataFrame) -> None:
    """
    Genera firmas MinHash vectorizadas y las almacena en HDF5.

    Reemplazo directo de DiskBasedLSHEngine._generate_signatures.
    Misma interfaz de entrada/salida, misma estructura HDF5.

    Cambios vs original:
    - VectorizedMinHashGenerator en lugar de datasketch.MinHash
    - SplitMix64 + hashing universal en lugar de SHA-1 + permutaciones
    - Versión HDF5 actualizada → invalida caché de firmas antiguas

    El archivo HDF5 resultante es 100% compatible con _build_lsh_index
    y _find_candidate_pairs (misma forma, dtype, y nombre de dataset).

    Args:
        df: DataFrame con columna 'NOMBRE_LIMPIO'.
    """
    # ── Verificar caché de firmas vectorizadas ──
    if self._signatures_file.exists():
        if _validate_signatures_vectorized(self, len(df)):
            self.logger.info("📝 Reutilizando firmas vectorizadas existentes")
            self.state = EngineState.SIGNATURES_READY
            return
        self.logger.info("🔄 Firmas antiguas incompatibles → regenerando...")
        self._signatures_file.unlink()

    n_records = len(df)
    self.logger.info(f"📝 Generando {n_records:,} firmas MinHash (vectorizado)...")
    start_time = time.time()

    # ── Inicializar generador (determinista vía seed=1) ──
    generator = VectorizedMinHashGenerator(num_perm=self._num_perm, ngram=self._ngram, seed=1)

    # ── Crear archivo HDF5 ──
    chunk_rows = min(self._chunk_size, n_records)
    with h5py.File(str(self._signatures_file), "w") as hf:
        signatures_ds = hf.create_dataset(
            "signatures",
            shape=(n_records, self._num_perm),
            dtype="uint64",
            chunks=(chunk_rows, self._num_perm),
            compression="gzip",
            compression_opts=1,
        )

        # Metadata para validación de caché
        hf.attrs["n_records"] = n_records
        hf.attrs["num_perm"] = self._num_perm
        hf.attrs["ngram"] = self._ngram
        hf.attrs["version"] = VectorizedMinHashGenerator.VERSION
        hf.attrs["seed"] = generator._seed

        # ── Procesar en chunks (controla memoria) ──
        texts = df["NOMBRE_LIMPIO"].values

        with tqdm(total=n_records, desc="Generando firmas", unit="rec") as pbar:
            for start in range(0, n_records, self._chunk_size):
                end = min(start + self._chunk_size, n_records)

                batch_sigs = generator.compute_batch(texts[start:end])
                signatures_ds[start:end] = batch_sigs
                pbar.update(end - start)

                # Limpieza periódica (cada 10 chunks)
                if (start // self._chunk_size) % 10 == 0:
                    del batch_sigs
                    gc.collect()

    elapsed = time.time() - start_time
    speed = n_records / elapsed if elapsed > 0 else 0
    self.metrics.signatures_generated = n_records
    self.metrics.time_signatures = elapsed
    self.state = EngineState.SIGNATURES_READY
    self.logger.info(
        f"✅ Firmas vectorizadas: {elapsed:.1f}s "
        f"({speed:,.0f} rec/s) — "
        f"archivo: {self._signatures_file.stat().st_size / (1024 * 1024):.1f} MB"
    )


def _validate_signatures_vectorized(self, expected_records: int) -> bool:
    """
    Valida archivo de firmas vectorizadas existente.

    Verifica versión (para invalidar firmas de datasketch),
    dimensiones, y parámetros de configuración.

    Args:
        expected_records: Número esperado de registros.

    Returns:
        True si el archivo es válido y compatible.
    """
    try:
        with h5py.File(str(self._signatures_file), "r") as hf:
            return (
                "signatures" in hf
                and hf.attrs.get("n_records", 0) == expected_records
                and hf.attrs.get("num_perm", 0) == self._num_perm
                and hf.attrs.get("ngram", 0) == self._ngram
                and hf.attrs.get("version", "") == VectorizedMinHashGenerator.VERSION
            )
    except Exception:
        return False


def _jaccard_real(t1, t2, n=2):
    s1 = set(t1[i : i + n] for i in range(len(t1) - n + 1))
    s2 = set(t2[i : i + n] for i in range(len(t2) - n + 1))
    return len(s1 & s2) / len(s1 | s2) if s1 | s2 else 0.0
