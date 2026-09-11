"""engine.cobertura — cobertura por estrellas sobre componentes conexas (v0.22.0).

``clusters_desde_decisiones`` agrupa por componentes conexas, que es
single-linkage: si ``a≈b`` y ``b≈c``, une ``a`` con ``c`` aunque no se
parezcan. Con identificador eso casi no importa —el veto corta el puente—,
pero deduplicando SOLO por nombre el encadenamiento es el modo de falla
dominante: medido sobre 211.949 destinatarios de exportación, produjo grupos
de 200 empresas distintas encadenadas por prefijos genéricos.

Este módulo reparte cada componente en estrellas y deja una garantía
verificable: **todo miembro queda a ≤ (1 − umbral) de SU líder**, que es
exactamente lo que una tabla correlativa afirma cuando dice "este nombre
original corresponde a este nombre final". Sin ella, la correlativa afirma
algo que el pipeline no comprobó nunca.

El líder se elige por cobertura (cubre a más miembros libres): es la
aproximación voraz estándar al *star cover*, determinista con los desempates
declarados. Referencia del problema: Wagstaff & Cardie (ICML 2000) para
clustering con restricciones a nivel de instancia.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

__all__ = ["ResultadoCobertura", "cobertura_estrella"]

ReglaLider = Literal["cobertura", "masa"]


class ResultadoCobertura:
    """Salida de :func:`cobertura_estrella`.

    Attributes:
        etiquetas: etiqueta de grupo por fila, 0-based y estable.
        es_lider: máscara del representante de cada grupo.
        similitud_al_lider: similitud FIRMADA de cada fila con su líder.
        n_grupos_antes: grupos que entraron (componentes conexas).
        n_grupos_despues: grupos que salieron.
    """

    __slots__ = (
        "es_lider",
        "etiquetas",
        "n_grupos_antes",
        "n_grupos_despues",
        "similitud_al_lider",
    )

    def __init__(
        self,
        etiquetas: np.ndarray,
        es_lider: np.ndarray,
        similitud_al_lider: np.ndarray,
        n_grupos_antes: int,
    ) -> None:
        self.etiquetas = etiquetas
        self.es_lider = es_lider
        self.similitud_al_lider = similitud_al_lider
        self.n_grupos_antes = int(n_grupos_antes)
        self.n_grupos_despues = len(np.unique(etiquetas)) if len(etiquetas) else 0

    @property
    def n_cortes(self) -> int:
        """Cuántos grupos añadió el refinamiento (encadenamientos rotos)."""
        return self.n_grupos_despues - self.n_grupos_antes

    def __repr__(self) -> str:  # pragma: no cover - representación
        return (
            f"ResultadoCobertura(grupos {self.n_grupos_antes} → "
            f"{self.n_grupos_despues}, cortes {self.n_cortes})"
        )


def _matriz_similitud(comparador, nombres: np.ndarray) -> np.ndarray:
    """Matriz k×k de similitud firmada. k es pequeño: una componente conexa."""
    k = len(nombres)
    a, b = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
    s = comparador.compare(nombres[a.ravel()], nombres[b.ravel()]).reshape(k, k)
    np.fill_diagonal(s, 1.0)
    return s


def cobertura_estrella(
    valores: np.ndarray,
    etiquetas: np.ndarray,
    masa: np.ndarray,
    comparador,
    *,
    similitud_minima: float,
    regla_lider: ReglaLider = "cobertura",
) -> ResultadoCobertura:
    """Parte cada componente en estrellas y nombra un líder por estrella.

    Args:
        valores: el texto comparado (p. ej. el nombre normalizado), por fila.
        etiquetas: componente conexa de cada fila (salida del clustering).
        masa: criterio de desempate, mayor es mejor (p. ej. nº de filas de
            origen, con el valor económico como decimal).
        comparador: objeto con ``compare(izq, der) -> np.ndarray`` firmado.
        similitud_minima: umbral en escala FIRMADA [−1, 1]. Un miembro entra
            en la estrella solo si alcanza este valor contra el líder.
        regla_lider: ``"cobertura"`` elige al que cubre a más miembros libres
            (medoide operativo, maximiza los grupos que sobreviven enteros);
            ``"masa"`` elige simplemente al de mayor masa.

    Returns:
        ``ResultadoCobertura``.

    Raises:
        ValueError: si los arreglos no tienen el mismo largo o el umbral está
            fuera de rango.
    """
    n = len(etiquetas)
    if not (len(valores) == len(masa) == n):
        raise ValueError(
            f"Qué pasó: largos distintos (valores={len(valores)}, "
            f"etiquetas={n}, masa={len(masa)}). Por qué importa: las tres "
            f"describen las MISMAS filas. Qué hacer: alinéelas antes de llamar."
        )
    if not -1.0 <= similitud_minima <= 1.0:
        raise ValueError(
            f"similitud_minima={similitud_minima} fuera de [-1, 1]. Es la escala "
            f"FIRMADA del comparador: un umbral de 0.84 en escala 0-1 "
            f"equivale a {2 * 0.84 - 1:.2f} aquí."
        )
    if regla_lider not in ("cobertura", "masa"):
        raise ValueError("regla_lider debe ser 'cobertura' o 'masa'.")
    if n == 0:
        vacio = np.zeros(0)
        return ResultadoCobertura(vacio.astype(np.int64), vacio.astype(bool), vacio, 0)

    valores = np.asarray(valores)
    nuevas = np.full(n, -1, dtype=np.int64)
    es_lider = np.zeros(n, dtype=bool)
    similitud = np.ones(n, dtype=np.float64)
    # Orden de desempate global: masa descendente, luego valor ascendente.
    orden = np.lexsort((valores, -np.asarray(masa, dtype=np.float64)))
    rango = np.empty(n, dtype=np.int64)
    rango[orden] = np.arange(n)

    siguiente = 0
    for _, posiciones in pd.Series(np.arange(n)).groupby(etiquetas):
        pos = posiciones.to_numpy()
        if len(pos) == 1:
            nuevas[pos] = siguiente
            es_lider[pos] = True
            siguiente += 1
            continue
        matriz = _matriz_similitud(comparador, valores[pos])
        cerca = matriz >= similitud_minima
        libres = np.ones(len(pos), dtype=bool)
        while libres.any():
            if regla_lider == "cobertura":
                alcance = np.where(libres, (cerca & libres[None, :]).sum(axis=1), -1)
                elegido = int(np.lexsort((rango[pos], -alcance))[0])
            else:
                candidatos = np.flatnonzero(libres)
                elegido = int(candidatos[np.argmin(rango[pos][candidatos])])
            miembros = np.union1d(np.flatnonzero(libres & cerca[elegido]), [elegido])
            nuevas[pos[miembros]] = siguiente
            similitud[pos[miembros]] = matriz[elegido, miembros]
            es_lider[pos[elegido]] = True
            libres[miembros] = False
            siguiente += 1
    return ResultadoCobertura(nuevas, es_lider, similitud, n_grupos_antes=len(np.unique(etiquetas)))
