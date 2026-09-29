"""matching.bloqueo — bloqueo componible por tipo con medición PC/RR (F2.5).

Cada estrategia genera pares candidatos (índices posicionales ``i < j``); la
unión de estrategias es el conjunto candidato del motor. ``medir`` calcula,
contra un ground truth, la completitud de pares (PC) y la razón de reducción
(RR) por estrategia individual y combinada — el eje de recall del playbook
se gobierna aquí (R ≈ PC × R_match).

Estrategias:
    - ``LlaveExacta``: identificadores/teléfonos/emails normalizados.
    - ``LSHTexto``: MinHash-LSH de shingles de caracteres para nombres.
    - ``VecindarioOrdenado``: sorted-neighborhood para fecha/numérico.
    - ``RejillaGeo``: celdas geográficas con vecindad 3×3 (geohash-grid).
"""

from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Literal, Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class EstrategiaBloqueo(Protocol):
    """Protocolo de una estrategia de bloqueo."""

    nombre: str

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> np.ndarray:
        """Pares candidatos como array ``(m, 2)`` int64 con ``i < j``.

        Args:
            valores: columnas normalizadas por nombre de campo ('' = faltante;
                GEO entrega array ``(n, 2)`` float con NaN = faltante).
        """
        ...


class CandidateBudgetExceeded(ValueError):
    """El bloqueo excedería el presupuesto antes de reservar memoria masiva."""

    def __init__(self, estimados: int, max_pares: int, estrategia: str) -> None:
        self.estimados = int(estimados)
        self.max_pares = int(max_pares)
        self.estrategia = estrategia
        super().__init__(
            f"Qué pasó: {estrategia} excedería el presupuesto de candidatos "
            f"({estimados:,} > max_candidatos={max_pares:,}). Por qué importa: "
            "el límite se aplica antes de reservar los arrays para evitar un OOM. "
            "Qué hacer: use una llave más selectiva, aumente el umbral LSH, "
            "reduzca max_grupo o eleve max_candidatos solo si dispone de RAM."
        )


def _validar_presupuesto(actual: int, nuevos: int, max_pares: int | None, nombre: str) -> None:
    if max_pares is not None and actual + nuevos > max_pares:
        raise CandidateBudgetExceeded(actual + nuevos, max_pares, nombre)


def _canonizar(pares: list[tuple[int, int]] | list[np.ndarray]) -> np.ndarray:
    """Lista de pares → array (m,2) único con i<j; vacío → (0,2)."""
    if not pares:
        return np.empty((0, 2), dtype=np.int64)
    if isinstance(pares[0], np.ndarray):
        piezas = [np.sort(np.asarray(p, dtype=np.int64).reshape(-1, 2), axis=1) for p in pares]
    else:
        piezas = [np.sort(np.asarray(pares, dtype=np.int64).reshape(-1, 2), axis=1)]
    return _union_pares(piezas)


#: Pares pendientes a partir de los cuales se compacta la unión incremental.
_LOTE_COMPACTACION = 2_000_000


def _claves_de_pares(piezas: list[np.ndarray], n: int) -> np.ndarray:
    """Pares (i, j) con ``i < j < n`` → claves escalares ``i·n + j`` (int64)."""
    if not piezas:
        return np.empty(0, dtype=np.int64)
    return np.concatenate(
        [
            np.asarray(p[:, 0], dtype=np.int64) * n + np.asarray(p[:, 1], dtype=np.int64)
            for p in piezas
        ]
    )


def _pares_de_claves(claves: np.ndarray, n: int) -> np.ndarray:
    """Inversa de :func:`_claves_de_pares`: claves únicas y ordenadas → (m, 2)."""
    if len(claves) == 0:
        return np.empty((0, 2), dtype=np.int64)
    i, j = np.divmod(claves, n)
    return np.column_stack((i, j))


def _union_pares(piezas: list[np.ndarray]) -> np.ndarray:
    """Unión canónica de pares ``i < j`` por CLAVE ESCALAR.

    Devuelve exactamente lo que devolvía ``np.unique(np.vstack(piezas),
    axis=0)`` —los pares únicos ordenados por ``(i, j)``— con la mitad de
    memoria y sin el sort sobre vista estructurada. El orden coincide porque
    ``j < n`` garantiza ``i₁ < i₂ ⇒ i₁·n + j₁ < i₂·n + j₂``.

    v0.22.4: en la partición USA de la base real (54.669 nombres) la unión
    LSH apilaba ``vstack`` + vista estructurada + ``argsort`` sobre pares int64:
    era el primer pico de memoria de una corrida que terminó en OOM.
    """
    piezas = [np.asarray(p, dtype=np.int64).reshape(-1, 2) for p in piezas if len(p)]
    if not piezas:
        return np.empty((0, 2), dtype=np.int64)
    n = int(max(int(p.max()) for p in piezas)) + 1
    return _pares_de_claves(np.unique(_claves_de_pares(piezas, n)), n)


class _UnionIncremental:
    """Acumula claves de pares y compacta (``np.unique``) por lotes.

    Sin esto, la unión LSH guardaba TODOS los pares brutos de todas las bandas
    hasta el final: el mismo par muy similar aparece en la mayoría de las
    bandas, así que lo bruto puede ser varias veces lo único. La compactación
    amortizada (cuando lo pendiente iguala lo ya único) mantiene el pico en
    ~3× el tamaño de la unión final en vez de 1× lo bruto.
    """

    def __init__(self, n: int, lote: int = _LOTE_COMPACTACION) -> None:
        self.n = int(n)
        self.lote = int(lote)
        self.unicas = np.empty(0, dtype=np.int64)
        self.pendientes: list[np.ndarray] = []
        self.n_pendientes = 0

    def agregar(self, claves: np.ndarray) -> None:
        if len(claves) == 0:
            return
        self.pendientes.append(claves)
        self.n_pendientes += len(claves)
        if self.n_pendientes >= max(len(self.unicas), self.lote):
            self.compactar()

    def compactar(self) -> np.ndarray:
        if self.pendientes:
            self.unicas = np.unique(np.concatenate([self.unicas, *self.pendientes]))
            self.pendientes = []
            self.n_pendientes = 0
        return self.unicas

    def pares(self) -> np.ndarray:
        return _pares_de_claves(self.compactar(), self.n)


@lru_cache(maxsize=1024)
def _triangulo_superior(k: int) -> tuple[np.ndarray, np.ndarray]:
    """``np.triu_indices(k, 1)`` cacheado: la LSH lo pide miles de veces por banda."""
    a, b = np.triu_indices(k, k=1)
    return a, b


def _pares_grupo(indices: np.ndarray) -> np.ndarray:
    """Pares completos de un grupo, sin listas Python por cada par."""
    k = len(indices)
    if k < 2:
        return np.empty((0, 2), dtype=np.int64)
    a, b = np.triu_indices(k, k=1)
    return np.column_stack((indices[a], indices[b])).astype(np.int64, copy=False)


def _pares_conectividad(indices: np.ndarray) -> np.ndarray:
    """Árbol determinista O(k) que conserva conectividad de una cubeta grande."""
    idx = np.sort(np.asarray(indices, dtype=np.int64))
    if len(idx) < 2:
        return np.empty((0, 2), dtype=np.int64)
    return np.column_stack((idx[:-1], idx[1:]))


class LlaveExacta:
    """Pares dentro de cada valor idéntico no-faltante de una columna.

    Args:
        campo: nombre del campo (clave en ``valores``).
        max_grupo: tope de tamaño de grupo; grupos mayores se OMITEN y se
            registran en ``grupos_omitidos`` (una llave degenerada — p. ej.
            un placeholder que escapó — no debe producir O(n²) pares).
    """

    def __init__(
        self,
        campo: str,
        *,
        max_grupo: int = 2000,
        overflow: Literal["skip", "connect"] = "skip",
    ) -> None:
        if max_grupo < 2:
            raise ValueError("max_grupo debe ser >= 2.")
        if overflow not in {"skip", "connect"}:
            raise ValueError("overflow debe ser 'skip' o 'connect'.")
        self.campo = campo
        self.max_grupo = int(max_grupo)
        self.overflow = overflow
        self.nombre = f"llave_exacta[{campo}]"
        self.grupos_omitidos: list[tuple[str, int]] = []

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> np.ndarray:
        v = pd.Series(valores[self.campo])
        self.grupos_omitidos = []
        piezas: list[np.ndarray] = []
        n_pares = 0
        for clave, idx in v[v != ""].groupby(v[v != ""]).groups.items():
            ind = np.asarray(idx, dtype=np.int64)
            if len(ind) < 2:
                continue
            if len(ind) > self.max_grupo:
                self.grupos_omitidos.append((str(clave), len(ind)))
                if self.overflow == "skip":
                    continue
                nuevos = len(ind) - 1
                _validar_presupuesto(n_pares, nuevos, max_pares, self.nombre)
                piezas.append(_pares_conectividad(ind))
                n_pares += nuevos
                continue
            nuevos = len(ind) * (len(ind) - 1) // 2
            _validar_presupuesto(n_pares, nuevos, max_pares, self.nombre)
            piezas.append(_pares_grupo(ind))
            n_pares += nuevos
        return _canonizar(piezas)


def _bandas_optimas(umbral: float, num_perm: int) -> tuple[int, int]:
    """(n_bandas, filas_por_banda) que aproximan el umbral Jaccard pedido.

    Mismo criterio que ``datasketch.MinHashLSH`` (minimizar FP+FN integrando
    la curva S), implementado aquí para no depender de su API privada.
    Determinista; se cachea por (umbral, num_perm).
    """
    return _bandas_optimas_cached(round(float(umbral), 6), int(num_perm))


@lru_cache(maxsize=64)
def _bandas_optimas_cached(umbral: float, num_perm: int) -> tuple[int, int]:
    from scipy.integrate import quad

    mejor, mejor_error = (1, num_perm), float("inf")
    for b in range(1, num_perm + 1):
        if num_perm % b:
            continue
        r = num_perm // b

        def _fp(s: float, b: int = b, r: int = r) -> float:
            return 1.0 - (1.0 - s**r) ** b

        def _fn(s: float, b: int = b, r: int = r) -> float:
            return (1.0 - s**r) ** b

        fp, _ = quad(_fp, 0.0, umbral)
        fn, _ = quad(_fn, umbral, 1.0)
        error = fp * 0.5 + fn * 0.5
        if error < mejor_error:
            mejor, mejor_error = (b, r), error
    return mejor


class LSHTexto:
    """MinHash-LSH sobre shingles de caracteres (nombres y texto largo).

    v0.12.0 (cierre operativo de H2+H6): la versión previa creaba UN objeto
    ``datasketch.MinHash`` por registro y poblaba un ``MinHashLSH`` con claves
    string — el mismo patrón por-registro que el motor de disco eliminó en
    v2.3.0. A 200K filas eso agotaba la RAM de Colab (OOM reproducido en la
    auditoría). Ahora las firmas salen del ``VectorizedMinHasher`` (NumPy
    puro, ~40K firmas/s medidas en 2 vCPU) y el banding es un sort
    vectorizado por banda. Determinista (seed fija) e independiente del
    orden de inserción.

    Nota de contrato: el conjunto de candidatos NO es bit-idéntico al de
    datasketch (familia de hash distinta con el mismo umbral efectivo); la
    calidad del bloqueo se garantiza por los tests de F1/PC del motor, no
    por identidad de pares. ``max_grupo`` protege de cubetas degeneradas.
    """

    def __init__(
        self,
        campo: str,
        *,
        umbral: float = 0.4,
        permutaciones: int = 64,
        ngram: int = 3,
        max_grupo: int = 500,
        seed: int = 1,
        overflow: Literal["connect", "skip"] = "connect",
    ) -> None:
        # max_grupo=500: una cubeta LSH mayor es señal degenerada (nombres
        # cuasi-homogéneos) y generaría hasta k²/2 pares de baja precisión —
        # medido en la auditoría: cubetas de 32K miembros → 667M candidatos y
        # OOM. Los registros de cubetas gigantes quedan cubiertos por las
        # demás bandas/estrategias (llaves exactas, vecindario).
        if not (0.0 < umbral < 1.0):
            raise ValueError(f"umbral={umbral} fuera de (0, 1).")
        self.campo = campo
        self.umbral = float(umbral)
        self.permutaciones = int(permutaciones)
        self.ngram = int(ngram)
        self.max_grupo = int(max_grupo)
        self.seed = int(seed)
        if self.max_grupo < 2:
            raise ValueError("max_grupo debe ser >= 2.")
        if overflow not in {"connect", "skip"}:
            raise ValueError("overflow debe ser 'connect' o 'skip'.")
        self.overflow = overflow
        self.nombre = f"lsh_texto[{campo}]"
        self.grupos_degradados: list[int] = []

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> np.ndarray:
        from ..engine.lsh.vectorized_minhash import VectorizedMinHasher

        v = valores[self.campo]
        validos = np.flatnonzero(pd.Series(v).astype(str).str.len().to_numpy() > 0)
        if len(validos) < 2:
            return _canonizar([])

        # Padding " t " como el shingling previo (conserva bordes de palabra).
        textos = np.array([f" {v[i]} " for i in validos], dtype=object)
        hasher = VectorizedMinHasher(num_perm=self.permutaciones, ngram=self.ngram, seed=self.seed)
        firmas = hasher.signatures_batch(textos)  # (m, num_perm) uint64

        n_bandas, filas_banda = _bandas_optimas(self.umbral, self.permutaciones)
        # Los índices son posiciones en ``v``: n = len(v) acota j y la clave
        # i·n + j es única. Se acumulan claves (8 B/par) y no pares (16 B/par).
        union = _UnionIncremental(len(v))
        n_pares = 0
        self.grupos_degradados = []
        for b in range(n_bandas):
            banda = firmas[:, b * filas_banda : (b + 1) * filas_banda]
            # Hash de fila determinista y vectorizado (mezcla tipo FNV-64).
            codigo = np.full(len(banda), 2166136261, dtype=np.uint64)
            for c in range(banda.shape[1]):
                codigo ^= banda[:, c]
                codigo *= np.uint64(1099511628211)
            orden = np.argsort(codigo, kind="stable")
            cod_ord = codigo[orden]
            limites = np.flatnonzero(np.diff(cod_ord)) + 1
            for g in np.split(orden, limites):
                k = g.size
                if k < 2:
                    continue
                idx = np.sort(validos[g]).astype(np.int64, copy=False)
                if k > self.max_grupo:
                    self.grupos_degradados.append(int(k))
                    if self.overflow == "skip":
                        continue
                    claves = idx[:-1] * union.n + idx[1:]  # árbol de conectividad
                else:
                    # ``b`` es la banda del bucle exterior: no se pisa con el índice del par.
                    fila_par, col_par = _triangulo_superior(k)
                    claves = idx[fila_par] * union.n + idx[col_par]  # todos los pares del grupo
                _validar_presupuesto(n_pares, len(claves), max_pares, self.nombre)
                union.agregar(claves)
                n_pares += len(claves)
        return union.pares()


class VecindarioOrdenado:
    """Sorted-neighborhood: pares a distancia ≤ ventana en el orden del campo."""

    def __init__(self, campo: str, *, ventana: int = 3) -> None:
        if ventana < 1:
            raise ValueError(f"ventana={ventana} debe ser >= 1.")
        self.campo = campo
        self.ventana = int(ventana)
        self.nombre = f"vecindario[{campo}]"

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> np.ndarray:
        v = pd.Series(valores[self.campo])
        validos = np.flatnonzero((v != "").to_numpy())
        if len(validos) < 2:
            return _canonizar([])
        orden = validos[np.argsort(v.iloc[validos].to_numpy(), kind="stable")]
        piezas: list[np.ndarray] = []
        n_pares = 0
        for d in range(1, self.ventana + 1):
            nuevos = max(0, len(orden) - d)
            _validar_presupuesto(n_pares, nuevos, max_pares, self.nombre)
            if nuevos:
                piezas.append(np.column_stack((orden[:-d], orden[d:])))
                n_pares += nuevos
        return _canonizar(piezas)


class RejillaGeo:
    """Celdas geográficas de ~``celda_km`` con vecindad 3×3 (geohash-grid).

    ``campo`` debe entregar un array ``(n, 2)`` float [lat, lon] (NaN =
    faltante). Garantiza candidato para todo par a distancia ≤ celda_km.
    """

    _KM_POR_GRADO = 111.32

    def __init__(self, campo: str, *, celda_km: float = 1.0) -> None:
        if celda_km <= 0:
            raise ValueError(f"celda_km={celda_km} debe ser > 0.")
        self.campo = campo
        self.celda_km = float(celda_km)
        self.nombre = f"rejilla_geo[{campo}]"

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> np.ndarray:
        xy = np.asarray(valores[self.campo], dtype=np.float64)
        ok = ~np.isnan(xy).any(axis=1)
        idx = np.flatnonzero(ok)
        if len(idx) < 2:
            return _canonizar([])
        dlat = self.celda_km / self._KM_POR_GRADO
        lat_media = float(np.nanmean(xy[idx, 0]))
        dlon = self.celda_km / (self._KM_POR_GRADO * max(0.1, abs(np.cos(np.radians(lat_media)))))
        celdas: dict[tuple[int, int], list[int]] = {}
        for i in idx.tolist():
            c = (int(np.floor(xy[i, 0] / dlat)), int(np.floor(xy[i, 1] / dlon)))
            celdas.setdefault(c, []).append(i)
        piezas: list[np.ndarray] = []
        n_pares = 0
        for (cx, cy), miembros in celdas.items():
            miembros_arr = np.asarray(miembros, dtype=np.int64)
            nuevos = len(miembros_arr) * (len(miembros_arr) - 1) // 2
            _validar_presupuesto(n_pares, nuevos, max_pares, self.nombre)
            if nuevos:
                piezas.append(_pares_grupo(miembros_arr))
                n_pares += nuevos
            for ox, oy in ((0, 1), (1, -1), (1, 0), (1, 1)):  # 4 vecinos canónicos
                vecinos = celdas.get((cx + ox, cy + oy))
                if vecinos:
                    vecinos_arr = np.asarray(vecinos, dtype=np.int64)
                    nuevos = len(miembros_arr) * len(vecinos_arr)
                    _validar_presupuesto(n_pares, nuevos, max_pares, self.nombre)
                    izquierda = np.repeat(miembros_arr, len(vecinos_arr))
                    derecha = np.tile(vecinos_arr, len(miembros_arr))
                    piezas.append(np.column_stack((izquierda, derecha)))
                    n_pares += nuevos
        return _canonizar(piezas)


class BloqueoComponible:
    """Unión de estrategias (F2.5) con medición PC/RR por estrategia."""

    def __init__(self, estrategias: list[EstrategiaBloqueo]) -> None:
        if not estrategias:
            raise ValueError("BloqueoComponible requiere al menos una estrategia.")
        self.estrategias = list(estrategias)

    def pares(
        self,
        valores: dict[str, np.ndarray],
        *,
        max_pares: int | None = None,
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Unión canónica y pares por estrategia (para auditoría/medición)."""
        por_estrategia: dict[str, np.ndarray] = {}
        acumulados = 0
        for estrategia in self.estrategias:
            metodo = estrategia.pares
            acepta_limite = "max_pares" in inspect.signature(metodo).parameters
            restantes = None if max_pares is None else max_pares - acumulados
            pares = metodo(valores, max_pares=restantes) if acepta_limite else metodo(valores)
            acumulados += len(pares)
            _validar_presupuesto(0, acumulados, max_pares, "bloqueo combinado")
            por_estrategia[estrategia.nombre] = pares
        todos = [p for p in por_estrategia.values() if len(p)]
        if not todos:
            return np.empty((0, 2), dtype=np.int64), por_estrategia
        return _union_pares(todos), por_estrategia

    @staticmethod
    def medir(
        por_estrategia: dict[str, np.ndarray],
        union: np.ndarray,
        id_true: np.ndarray,
    ) -> dict[str, dict[str, float]]:
        """PC y RR contra un ground truth (columna de identidad verdadera).

        PC = fracción de pares verdaderos capturados; RR = 1 − candidatos /
        C(n, 2). Se reporta por estrategia y para la unión ("combinada").
        """
        n = len(id_true)
        s = pd.Series(id_true)
        verdaderos: set[tuple[int, int]] = set()
        for _, idx in s.groupby(s).groups.items():
            verdaderos.update(map(tuple, _pares_grupo(np.asarray(idx, dtype=np.int64)).tolist()))
        total_posibles = n * (n - 1) / 2.0
        salida: dict[str, dict[str, float]] = {}

        def _metricas(pares_arr: np.ndarray) -> dict[str, float]:
            cand = {(int(a), int(b)) for a, b in pares_arr}
            pc = (len(cand & verdaderos) / len(verdaderos)) if verdaderos else 1.0
            rr = 1.0 - (len(cand) / total_posibles if total_posibles else 0.0)
            return {
                "pc": round(pc, 6),
                "rr": round(rr, 6),
                "n_pares": float(len(cand)),
            }

        for nombre, arr in por_estrategia.items():
            salida[nombre] = _metricas(arr)
        salida["combinada"] = _metricas(union)
        return salida
