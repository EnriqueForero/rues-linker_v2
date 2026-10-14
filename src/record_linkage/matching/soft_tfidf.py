"""Soft TF-IDF: similitud de nombres por tokens ponderados por IDF, con tolerancia a erratas entre tokens.

Es el comparador de nombre de la Fase 3. Resuelve las dos fallas medidas del comparador heredado
(``token_set_ratio``, ver ``docs/evidencia/fase2_anatomia_fp.md``):

1. **Contención no es identidad.** «RODRIGUEZ» dentro de «LAURA RODRIGUEZ» o «CAFE S» dentro de
   «INVERSIONES FENIX CAFE S S» valían 1,0; el 100 % de los falsos positivos del banco eran de esta forma.
   Aquí la similitud es un coseno: lo que un nombre trae y el otro no, resta.
2. **Las erratas no borran la evidencia.** El Jaccard por IDF exacto daba 0 a «TRIVENTA» vs «TRIVENTE».
   Aquí dos tokens cuentan como el mismo cuando su Jaro-Winkler supera ``umbral_token`` (0,90 por defecto),
   y aportan en proporción a ese parecido.

La fórmula es la de Cohen, Ravikumar y Fienberg (2003, «A comparison of string distance metrics for
name-matching tasks»), con un emparejamiento uno a uno entre tokens (mejor mutuo) para que un token de
un lado no se cobre dos veces::

    sim(A, B) = Σ w(a) · w(b) · s(a, b) / (‖w_A‖ · ‖w_B‖)     sobre los pares (a, b) emparejados

donde ``w`` es el IDF del token en el corpus (``construir_idf``) y ``s`` vale 1 para tokens iguales y el
Jaro-Winkler para los tolerados. Los tokens se tratan como conjunto: el orden no importa y un token
repetido dentro del mismo nombre cuenta una vez. Todo el cálculo es por lotes: tablas de tokens con
``explode``, emparejamientos con ``merge`` y una sola llamada a ``rapidfuzz`` en C++ para los tolerados.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from record_linkage.matching.idf import LONGITUD_MINIMA_TOKEN

if TYPE_CHECKING:
    from record_linkage.matching.idf import PesosIDF

__all__ = ["UMBRAL_TOKEN", "similitud_soft_tfidf"]

#: Jaro-Winkler mínimo para que dos tokens distintos cuenten como el mismo con errata.
UMBRAL_TOKEN = 0.90


def _tabla_tokens(
    nombres: np.ndarray, pesos_idf: PesosIDF, longitud_minima: int
) -> tuple[pd.DataFrame, np.ndarray]:
    """Tokens únicos de cada nombre con su IDF, y la norma ‖w‖ de cada nombre (0 si no tiene tokens)."""
    texto = pd.Series(nombres).fillna("").astype(str)
    tokens = texto.str.split().explode().dropna()
    tokens = tokens[tokens.str.len() >= longitud_minima]
    tabla = pd.DataFrame({"par": tokens.index.to_numpy(), "token": tokens.to_numpy()})
    tabla = tabla.drop_duplicates()
    columna = tabla["token"].map(pesos_idf.vocabulario)
    peso = np.where(
        columna.notna(),
        pesos_idf.pesos[columna.fillna(0).to_numpy(dtype=np.int64)],
        pesos_idf.idf_desconocido,
    )
    tabla["peso"] = peso.astype(np.float64)
    norma = np.sqrt(
        np.bincount(tabla["par"].to_numpy(), weights=tabla["peso"] ** 2, minlength=len(texto))
    )
    return tabla, norma


def _mejor_mutuo(cruce: pd.DataFrame) -> pd.DataFrame:
    """Emparejamiento uno a uno: cada token de A con su mejor b, y se conserva solo si es el mejor a de ese b."""
    por_a = cruce.loc[cruce.groupby(["par", "token_a"], sort=False)["s"].idxmax()]
    return por_a.loc[por_a.groupby(["par", "token_b"], sort=False)["s"].idxmax()]


def similitud_soft_tfidf(
    pesos_idf: PesosIDF,
    nombres_izq: np.ndarray,
    nombres_der: np.ndarray,
    *,
    umbral_token: float = UMBRAL_TOKEN,
    longitud_minima: int = LONGITUD_MINIMA_TOKEN,
) -> np.ndarray:
    """Similitud Soft TF-IDF en [0, 1] para un lote de pares de nombres ya limpios.

    Args:
        pesos_idf: IDF del corpus (:func:`record_linkage.matching.idf.construir_idf`), que fija cuánto
            pesa cada token; un token fuera del vocabulario pesa ``idf_desconocido``.
        nombres_izq: nombres del lado izquierdo de cada par.
        nombres_der: nombres del lado derecho, mismo largo.
        umbral_token: Jaro-Winkler mínimo entre dos tokens distintos para emparejarlos.
        longitud_minima: tokens más cortos no cuentan (el mismo criterio con que se construyó el IDF).

    Returns:
        Un valor por par: 1,0 para el mismo conjunto de tokens, 0,0 cuando algún lado no aporta tokens.

    Raises:
        ValueError: si los dos lados tienen largos distintos o ``umbral_token`` no está en (0, 1].
    """
    if len(nombres_izq) != len(nombres_der):
        raise ValueError(f"largos distintos: {len(nombres_izq)} vs {len(nombres_der)}")
    if not 0.0 < umbral_token <= 1.0:
        raise ValueError(f"umbral_token={umbral_token} debe estar en (0, 1].")
    n = len(nombres_izq)
    if n == 0 or pesos_idf.vacio:
        return np.zeros(n, dtype=np.float64)

    tabla_a, norma_a = _tabla_tokens(
        np.asarray(nombres_izq, dtype=object), pesos_idf, longitud_minima
    )
    tabla_b, norma_b = _tabla_tokens(
        np.asarray(nombres_der, dtype=object), pesos_idf, longitud_minima
    )

    exactos = tabla_a.merge(tabla_b, on=["par", "token"], suffixes=("_a", "_b"))
    aporte = np.bincount(
        exactos["par"].to_numpy(), weights=exactos["peso_a"] * exactos["peso_b"], minlength=n
    )

    restantes_a = tabla_a.merge(exactos[["par", "token"]], how="left", indicator=True)
    restantes_a = restantes_a[restantes_a["_merge"] == "left_only"].drop(columns="_merge")
    restantes_b = tabla_b.merge(exactos[["par", "token"]], how="left", indicator=True)
    restantes_b = restantes_b[restantes_b["_merge"] == "left_only"].drop(columns="_merge")
    if not restantes_a.empty and not restantes_b.empty:
        cruce = restantes_a.merge(restantes_b, on="par", suffixes=("_a", "_b"))
        if not cruce.empty:
            from rapidfuzz import process as rf_process
            from rapidfuzz.distance import JaroWinkler

            s = rf_process.cpdist(
                cruce["token_a"].tolist(),
                cruce["token_b"].tolist(),
                scorer=JaroWinkler.similarity,
                score_cutoff=umbral_token,
                dtype=np.float64,
                workers=-1,
            )
            cruce = cruce.assign(s=s)
            cruce = cruce[cruce["s"] >= umbral_token]
            if not cruce.empty:
                tolerados = _mejor_mutuo(cruce.reset_index(drop=True))
                aporte += np.bincount(
                    tolerados["par"].to_numpy(),
                    weights=tolerados["peso_a"] * tolerados["peso_b"] * tolerados["s"],
                    minlength=n,
                )

    denominador = norma_a * norma_b
    with np.errstate(divide="ignore", invalid="ignore"):
        similitud = np.where(denominador > 0, aporte / denominador, 0.0)
    return np.clip(similitud, 0.0, 1.0)
