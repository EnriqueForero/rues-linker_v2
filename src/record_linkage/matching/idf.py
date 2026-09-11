"""record_linkage.matching.idf — Peso de un token por lo que informa (v0.18.0).

Contexto: bases de 2–5 M de registros en Colab Free (~12 GB RAM).

El problema que resuelve
------------------------
Comparar nombres por ``token_set_ratio`` trata todos los tokens igual. En un
padrón de empresas eso es falso de forma sistemática: ``CO``, ``LTD``,
``INC``, ``MARINE``, ``TECH``, ``LOGIS`` aparecen en miles de razones
sociales y no distinguen a nadie; el token que sí distingue es el que aparece
dos veces en todo el corpus.

Medido sobre ``ground_truth_grande.csv``, régimen SIN_NIT — donde el nombre
es la única evidencia disponible:

* ``HWANGJUNG TECH LLC`` vs ``HANJUNG TECH LLC`` → dos empresas distintas que
  comparten dos de tres tokens y el comparador plano las une.
* ``JINSHIN LOGIS CO.,LTD.`` vs ``SHINSHIN LOGIS CO.,LTD.`` → idem.

Ponderar cada token por ``log(N / df)`` invierte el peso: los genéricos valen
casi cero y la decisión queda en manos del token informativo, que es
exactamente donde un humano la toma.

Referencia: Spärck Jones, K. (1972), *A statistical interpretation of term
specificity and its application in retrieval*, Journal of Documentation 28(1).
La aplicación a record linkage aparece en Winkler, W. (1990), *String
comparator metrics and enhanced decision rules in the Fellegi-Sunter model*.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.18.0
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["PesosIDF", "construir_idf", "similitud_idf"]

#: Longitud mínima de un token para contarlo. Los de una letra son ruido de
#: puntuación ("S", "A" de "S.A.") y no aportan evidencia.
LONGITUD_MINIMA_TOKEN = 2


@dataclass(frozen=True)
class PesosIDF:
    """Mapa token → informatividad, más la matriz de incidencia del corpus.

    Attributes:
        vocabulario: token → índice de columna en la matriz.
        pesos: IDF por columna, alineado con ``vocabulario``.
        incidencia: matriz dispersa registros × vocabulario, con 1.0 donde el
            registro contiene el token. Se guarda binaria a propósito: el peso
            entra una sola vez al multiplicar por ``pesos``, de modo que la
            intersección ponderada es un producto matriz-vector y no una
            suma de cuadrados.
        peso_total: suma de IDF de los tokens de cada registro.
        idf_desconocido: peso que se asigna a un token fuera del vocabulario.
        documentos: tamaño del corpus con el que se calculó el IDF.
    """

    vocabulario: dict[str, int]
    pesos: np.ndarray
    incidencia: object  # scipy.sparse.csr_matrix
    peso_total: np.ndarray
    idf_desconocido: float
    documentos: int
    _cache: dict[str, object] = field(default_factory=dict, repr=False, compare=False)

    @property
    def vacio(self) -> bool:
        """True si el corpus no tenía tokens utilizables."""
        return not self.vocabulario


def construir_idf(nombres: pd.Series, *, longitud_minima: int = LONGITUD_MINIMA_TOKEN) -> PesosIDF:
    """Calcula el IDF de cada token del corpus y su matriz de incidencia.

    Todo el cálculo es vectorizado: ``explode`` produce la relación
    registro→token y ``value_counts`` la frecuencia documental. Sobre 4,4 M de
    nombres esto son segundos; una comprensión de Python sobre los pares
    —que es como estaba antes— eran horas.

    Args:
        nombres: razones sociales ya limpias, una por registro.
        longitud_minima: tokens más cortos se descartan.

    Returns:
        PesosIDF listo para :func:`similitud_idf`.

    Raises:
        ValueError: si ``longitud_minima`` es menor que 1.
    """
    if longitud_minima < 1:
        raise ValueError(f"longitud_minima={longitud_minima} debe ser >= 1.")
    from scipy import sparse

    total = len(nombres)
    texto = pd.Series(nombres).fillna("").astype(str)
    tokens = texto.str.split()
    largos = tokens.explode().dropna()
    largos = largos[largos.str.len() >= longitud_minima]

    if largos.empty:
        return PesosIDF(
            vocabulario={},
            pesos=np.zeros(0, dtype=np.float64),
            incidencia=sparse.csr_matrix((total, 0), dtype=np.float64),
            peso_total=np.zeros(total, dtype=np.float64),
            idf_desconocido=float(np.log(max(total, 2))),
            documentos=total,
        )

    # Frecuencia DOCUMENTAL: un token repetido dentro del mismo nombre cuenta
    # una vez. Sin esto, "SEGUROS SEGUROS DEL SUR" inflaría la frecuencia y
    # haría parecer genérico un token que no lo es.
    filas = largos.index.to_numpy()
    valores = largos.to_numpy()
    unicos = pd.DataFrame({"fila": filas, "token": valores}).drop_duplicates()
    frecuencia = unicos["token"].value_counts()

    vocabulario = {token: i for i, token in enumerate(frecuencia.index)}
    df_por_token = frecuencia.to_numpy(dtype=np.float64)
    # IDF suavizado: log((1 + N) / (1 + df)) + 1. Nunca es cero, así que un
    # token presente en todo el corpus sigue aportando un mínimo y la
    # similitud no se indefine cuando dos nombres solo comparten genéricos.
    pesos = np.log((1.0 + total) / (1.0 + df_por_token)) + 1.0

    columnas = unicos["token"].map(vocabulario).to_numpy(dtype=np.int64)
    incidencia = sparse.csr_matrix(
        (np.ones(len(columnas), dtype=np.float64), (unicos["fila"].to_numpy(), columnas)),
        shape=(total, len(vocabulario)),
    )
    peso_total = np.asarray(incidencia @ pesos).ravel()
    return PesosIDF(
        vocabulario=vocabulario,
        pesos=pesos,
        incidencia=incidencia,
        peso_total=peso_total,
        idf_desconocido=float(np.log(1.0 + total) + 1.0),
        documentos=total,
    )


def similitud_idf(pesos_idf: PesosIDF, izquierda: np.ndarray, derecha: np.ndarray) -> np.ndarray:
    """Jaccard ponderado por IDF para un lote de pares, vectorizado.

    ``sim = W(A ∩ B) / W(A ∪ B)`` con ``W`` = suma de IDF de los tokens. La
    intersección sale de multiplicar elemento a elemento las dos filas
    binarias de incidencia y proyectar sobre el vector de pesos; la unión, de
    ``W(A) + W(B) − W(A ∩ B)``. Todo en matrices dispersas: el costo es
    proporcional a los tokens presentes, no al tamaño del vocabulario.

    Args:
        pesos_idf: resultado de :func:`construir_idf`.
        izquierda: índices de registro del lado izquierdo de cada par.
        derecha: índices del lado derecho, mismo largo.

    Returns:
        Similitud en [0, 1] por par. Cero donde alguno de los dos nombres no
        aporta ningún token utilizable.

    Raises:
        ValueError: si los dos arreglos tienen largos distintos.
    """
    if len(izquierda) != len(derecha):
        raise ValueError(f"largos distintos: {len(izquierda)} vs {len(derecha)}")
    n = len(izquierda)
    if n == 0 or pesos_idf.vacio:
        return np.zeros(n, dtype=np.float64)

    izq = np.asarray(izquierda, dtype=np.int64)
    der = np.asarray(derecha, dtype=np.int64)
    filas_izq = pesos_idf.incidencia[izq]
    filas_der = pesos_idf.incidencia[der]
    interseccion = np.asarray(filas_izq.multiply(filas_der) @ pesos_idf.pesos).ravel()
    union = pesos_idf.peso_total[izq] + pesos_idf.peso_total[der] - interseccion
    with np.errstate(divide="ignore", invalid="ignore"):
        similitud = np.where(union > 0, interseccion / union, 0.0)
    return np.clip(similitud, 0.0, 1.0)
