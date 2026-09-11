"""engine.lsh.vectorized_minhash — generación de firmas MinHash vectorizada.

Nuevo en v2.3.0. Reemplaza el cuello de botella de ``DiskBasedLSHEngine``:
hasta v2.2.0 las firmas se generaban creando un objeto ``datasketch.MinHash``
por registro y llamando ``.update()`` n-grama por n-grama en un bucle Python.
Sobre 1.9M registros eso es ~1.9M objetos + decenas de millones de llamadas,
y consumía ~60 % del tiempo total del motor.

Esta implementación calcula las firmas por lotes con NumPy usando el mismo
esquema de hashing universal que datasketch (familia lineal
``h_i(x) = (a_i * x + b_i) mod p`` sobre enteros de 64 bits, con ``p`` el primo
de Mersenne 2^61−1 que usa datasketch). Las firmas resultantes son válidas para
LSH: dos textos similares comparten un mínimo por permutación con la misma
probabilidad ≈ Jaccard, igual que datasketch.

Determinismo: las permutaciones se derivan de una semilla fija (``seed=42``),
así que dos ejecuciones —o dos procesos distintos tras un reinicio de Colab—
producen firmas idénticas. Esto es necesario para que el checkpointing del
índice sea correcto (ver MIGRATION_LOG §13).

Author: Auditoría v2.3.0  Date: 2026-05-21  Version: 2.3.0
"""

from __future__ import annotations

import numpy as np

# Primo de Mersenne 2^61 - 1, el mismo que usa datasketch para el hashing.
_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH = (1 << 32) - 1
_HASH_RANGE = 1 << 32


def _mod_mersenne(y: np.ndarray) -> np.ndarray:
    """``y % (2^61 - 1)`` bit-exacto SIN división entera (v0.12.0). MUTA ``y``.

    La división de uint64 era la operación más cara del hot path (medido:
    7.7 s de los 8.3 s del bloque de permutaciones sobre 643K ventanas).
    Para un primo de Mersenne p = 2^61 − 1 vale la reducción clásica
    ``y ≡ (y & p) + (y >> 61)  (mod p)`` porque 2^61 ≡ 1 (mod p); para
    y < 2^64 el resultado queda < 2^61 + 8, así que UNA resta condicional
    de p basta. Operaciones in-place (el caller pasa un temporal recién
    creado) para minimizar tráfico de memoria: a esta escala el límite es
    el ancho de banda, no la ALU. Equivalencia verificada elemento a
    elemento contra ``%`` en tests/test_minhash_vectorizado.py.

    Args:
        y: array uint64 (cualquier forma). Se modifica in-place.

    Returns:
        Array uint64 con ``y % _MERSENNE_PRIME``, mismos valores que ``%``.
    """
    p = np.uint64(_MERSENNE_PRIME)
    r = y & p
    y >>= np.uint64(61)
    r += y
    np.subtract(r, p, out=r, where=r >= p)
    return r


class VectorizedMinHasher:
    """Genera firmas MinHash de un lote de textos de forma vectorizada.

    Las firmas son compatibles en semántica con ``datasketch.MinHash`` (misma
    familia de hash, mismo primo), por lo que sirven para bandas LSH idénticas.

    Args:
        num_perm: Número de permutaciones (longitud de la firma).
        ngram: Tamaño del n-grama de caracteres.
        seed: Semilla para las permutaciones. Fija por defecto (determinismo).
    """

    def __init__(self, num_perm: int = 128, ngram: int = 3, seed: int = 42) -> None:
        self.num_perm = int(num_perm)
        self.ngram = int(ngram)
        self.seed = int(seed)
        rng = np.random.RandomState(self.seed)
        # Coeficientes de la familia de hash lineal, igual que datasketch:
        # a en [1, p-1], b en [0, p-1].
        self._a = rng.randint(1, _MERSENNE_PRIME, size=self.num_perm, dtype=np.uint64)
        self._b = rng.randint(0, _MERSENNE_PRIME, size=self.num_perm, dtype=np.uint64)

    def _ngram_hashes(self, text: str) -> np.ndarray:
        """Devuelve los hashes base (uint32) de los n-gramas de un texto.

        Usa el mismo hash base que datasketch: sha1 truncado a 32 bits, vía el
        hash incorporado de Python sobre los bytes. Para vectorizar evitamos
        sha1 y usamos un hash polinómico estable de 32 bits sobre los bytes del
        n-grama (determinista entre procesos, a diferencia de ``hash()``).
        """
        n = len(text)
        if n < self.ngram:
            return np.empty(0, dtype=np.uint64)
        # Hash polinómico rolling estable de 32 bits por n-grama de caracteres.
        # Trabajamos sobre el string (caracteres), no bytes, para respetar ngram.
        grams = [text[i : i + self.ngram] for i in range(n - self.ngram + 1)]
        out = np.fromiter(
            (_stable_hash32(g) for g in grams),
            dtype=np.uint64,
            count=len(grams),
        )
        return out

    def signature(self, text: str, max_val: int | None = None) -> np.ndarray:
        """Firma MinHash de un solo texto (uint64, longitud num_perm).

        Si el texto es más corto que un n-grama, devuelve la firma de "vacío"
        (todos los valores al máximo), igual que el motor original.
        """
        fill = _MAX_HASH if max_val is None else int(max_val)
        base = self._ngram_hashes(text)
        if base.size == 0:
            return np.full(self.num_perm, fill, dtype=np.uint64)
        # Permutaciones: (a * x + b) mod p, luego mod 2^32, mínimo por columna.
        # base: (k,) ; a,b: (num_perm,) -> phv: (k, num_perm)
        phv = (np.outer(base, self._a) + self._b) % _MERSENNE_PRIME
        phv &= _MAX_HASH
        return phv.min(axis=0).astype(np.uint64)

    #: Textos por trozo del gather final (min por texto). Con ~30 ventanas
    #: por texto y 128 permutaciones, cada trozo materializa ~15 MB — cabe en
    #: caché y el límite pasa a ser tráfico secuencial. Medido en 2 vCPU:
    #: trozos de 8192 textos (~660 MB por pasada) rendían 3× menos.
    _SUB_LOTE = 512
    #: Permutaciones por bloque al construir la tabla de n-gramas únicos.
    _PERM_BLOQUE = 128

    def signatures_batch(self, texts: np.ndarray) -> np.ndarray:
        """Firmas de un lote de textos. Devuelve array (len(texts), num_perm).

        v0.12.0 (cierre del hallazgo H5 de la auditoría): hasta 0.11.x esta
        función iteraba texto a texto y ``_stable_hash32`` hasheaba BYTE a
        byte en Python puro — "vectorizado" de nombre, ~9.1K firmas/s medidas.
        Ahora los textos ASCII (la totalidad de NOMBRE_LIMPIO tras la
        normalización del pipeline) van por un camino 100% NumPy:

            1. Se concatenan los bytes UTF-8 de todo el sub-lote en un solo
               buffer y las ventanas de n-grama se materializan como índices
               (sin crear strings intermedios).
            2. El hash FNV-1a de cada ventana se calcula con k pases
               vectorizados sobre TODAS las ventanas a la vez — los mismos
               XOR/multiplicación/máscara que ``_stable_hash32``, bit a bit.
            3. Las permutaciones (a·x+b mod p, con el mismo wrap uint64 de
               2^64 que la versión previa) se evalúan por bloques y el mínimo
               por texto sale de ``np.minimum.reduceat`` sobre los segmentos.

        Los textos NO-ASCII (donde ventana de caracteres ≠ ventana de bytes)
        y los más cortos que el n-grama caen al camino escalar previo, que
        produce exactamente los mismos valores. PARIDAD BIT A BIT con 0.11.x
        garantizada y verificada en tests/test_minhash_vectorizado.py; los
        caches de firmas existentes siguen siendo válidos.
        """
        m = len(texts)
        out = np.empty((m, self.num_perm), dtype=np.uint64)
        if m == 0:
            return out

        # Coerción a str con las MISMAS reglas del camino 0.11.x.
        limpios: list[str] = []
        for t in texts:
            if not isinstance(t, str):
                t = "" if t is None or (isinstance(t, float) and np.isnan(t)) else str(t)
            limpios.append(t)

        # Dedup de textos idénticos (v0.12.0): en corpora multi-fuente la
        # repetición de razones sociales es alta; la firma de un texto es
        # función pura del texto, así que se computa UNA vez por único y se
        # dispersa. Bit-exacto por construcción.
        arr = np.asarray(limpios, dtype=object)
        unicos, inversa = np.unique(arr, return_inverse=True)
        if len(unicos) < m:
            firmas_unicas = self.signatures_batch(unicos)
            out[:] = firmas_unicas[inversa]
            return out

        k = self.ngram

        # ── Partición del lote: rápidos (ASCII y largo >= ngram) vs resto ──
        rapidos: list[int] = []
        encoded: list[bytes] = []
        for pos, t in enumerate(limpios):
            if len(t) >= k and t.isascii():
                rapidos.append(pos)
                encoded.append(t.encode("ascii"))
            elif len(t) < k:
                out[pos] = _MAX_HASH  # firma de "vacío", igual que signature()
            else:  # no-ASCII: camino escalar exacto (ventana de chars ≠ bytes)
                out[pos] = self.signature(t)

        if not rapidos:
            return out

        # ── Ventanas de n-grama de TODO el lote como índices sobre un buffer ──
        lens = np.fromiter((len(b) for b in encoded), dtype=np.int64, count=len(encoded))
        flat = np.frombuffer(b"".join(encoded), dtype=np.uint8)
        offsets = np.concatenate(([0], np.cumsum(lens)[:-1]))
        counts = lens - k + 1  # ventanas por texto (>= 1 garantizado aquí)
        total_win = int(counts.sum())
        starts = np.repeat(offsets, counts) + (
            np.arange(total_win, dtype=np.int64)
            - np.repeat(np.concatenate(([0], np.cumsum(counts)[:-1])), counts)
        )

        # ── FNV-1a vectorizado: k pases sobre todas las ventanas (bit-exacto
        # con _stable_hash32 para ASCII: mismos XOR/mult/máscara) ──
        h = np.full(total_win, 2166136261, dtype=np.uint64)
        for j in range(k):
            h ^= flat[starts + j].astype(np.uint64)
            h = (h * np.uint64(16777619)) & np.uint64(_MAX_HASH)

        # ── Factorización por n-grama ÚNICO (v0.12.0) ──────────────────────
        # El vocabulario real de trigramas de razones sociales es minúsculo
        # (miles) frente a las ventanas (millones): las 128 permutaciones se
        # evalúan UNA vez por trigrama único y luego se DISPERSAN por gather.
        # Esto convierte el costo aritmético O(W·num_perm) en O(U·num_perm)
        # con U ≪ W, y deja el resto como tráfico de memoria secuencial.
        # min por texto == min sobre los valores dispersados (misma multiset)
        # ⇒ bit-exacto con el camino por-texto.
        uniq_h, inv = np.unique(h, return_inverse=True)
        # Tras el `& _MAX_HASH` todo valor cabe en 32 bits: la tabla, el
        # gather y el reduceat corren en uint32 (MITAD de tráfico de memoria,
        # que es el límite real medido en 2 vCPU), y el resultado se asigna a
        # `out` (uint64) sin pérdida — bit-exacto.
        phv_u = np.empty((len(uniq_h), self.num_perm), dtype=np.uint32)
        for p0 in range(0, self.num_perm, self._PERM_BLOQUE):
            p1 = min(p0 + self._PERM_BLOQUE, self.num_perm)
            # Mismo wrap uint64 (mod 2^64) que np.outer previo; la reducción
            # de Mersenne reemplaza la división del `%` con valores idénticos.
            blk = _mod_mersenne(uniq_h[:, None] * self._a[None, p0:p1] + self._b[None, p0:p1])
            blk &= np.uint64(_MAX_HASH)
            phv_u[:, p0:p1] = blk.astype(np.uint32)

        # ── Mínimo por texto: gather desde la tabla caliente + reduceat ────
        # Se trocea por TEXTOS para acotar el temporal del gather (~15 MB).
        seg_starts_global = np.concatenate(([0], np.cumsum(counts)[:-1]))
        filas = np.asarray(rapidos, dtype=np.int64)
        n_rap = len(rapidos)
        paso = max(1, self._SUB_LOTE)
        for t0 in range(0, n_rap, paso):
            t1 = min(t0 + paso, n_rap)
            w0 = int(seg_starts_global[t0])
            w1 = int(seg_starts_global[t1 - 1] + counts[t1 - 1])
            g = phv_u[inv[w0:w1]]  # (ventanas_del_trozo, num_perm)
            seg_local = (seg_starts_global[t0:t1] - w0).astype(np.int64)
            out[filas[t0:t1]] = np.minimum.reduceat(g, seg_local, axis=0)

        return out


def _stable_hash32(s: str) -> int:
    """Hash determinista de 32 bits de un string (estable entre procesos).

    A diferencia de ``hash()`` de Python (randomizado por PYTHONHASHSEED), este
    hash es reproducible, requisito para que el índice LSH sea consistente tras
    reinicios de sesión. Hash polinómico tipo Java sobre los bytes UTF-8.
    """
    h = 2166136261
    for byte in s.encode("utf-8", errors="ignore"):
        h ^= byte
        h = (h * 16777619) & _MAX_HASH
    return int(h)
