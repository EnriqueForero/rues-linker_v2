"""matching.nombre_idf — comparador de razones sociales sin identificador (v0.22.0).

Cuando NO hay NIT, el nombre es la única evidencia y los comparadores de
cadena solos fallan de tres formas medidas sobre 211.949 destinatarios de
exportación colombianos:

1. **Imán genérico.** Si "A está contenido en B" cuenta como evidencia,
   cualquier nombre corto absorbe todo lo que lo mencione: ``INTERNATIONAL``
   se llevó 157 razones sociales distintas a un mismo grupo; ``MQE``, 214.
2. **Prefijo compartido.** Jaro-Winkler premia el prefijo común, y en un
   padrón de empresas el prefijo es justo la parte genérica:
   ``COMERCIALIZADORA ATLANTA C.A.`` vs ``COMERCIALIZADORA ATLANTIC C.A.``
   da JW 0,972 y son dos empresas.
3. **Palabra de sector como identidad.** ``KA DK FLOWERS`` y ``E FLOWERS`` se
   unían por compartir "FLOWERS", que aparece en 2.739 nombres del corpus.

La respuesta es medir la informatividad de lo COMPARTIDO, no el parecido de
las cadenas: ``sim = alfa · S_tokens + (1 − alfa) · JaroWinkler``, donde
``S_tokens`` es Jaccard ponderado por IDF —simétrico, no premia la
contención— salvo cuando lo que sobra en el nombre largo es demostrablemente
ruido.

Referencias: Spärck Jones (1972) para el IDF; Winkler (1990) para el uso de
comparadores de cadena en Fellegi-Sunter.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

import numpy as np
import pandas as pd
from rapidfuzz import process as rf_process
from rapidfuzz.distance import JaroWinkler

from .idf import PesosIDF, construir_idf

__all__ = [
    "SimilitudNombre",
    "comparador_desde_corpus",
    "contencion_idf",
    "diferencia_informativa",
    "frecuencia_distintiva",
    "jaccard_idf",
    "marcar_informativos",
    "max_idf_compartido",
    "neutralizar_genericos",
    "piso_por_frecuencia",
]


def neutralizar_genericos(
    pesos_idf: PesosIDF, terminos: Iterable[str], *, peso: float = 1.0
) -> PesosIDF:
    """Baja a ``peso`` el IDF de los términos declarados genéricos.

    Conserva el IDF crudo en el objeto devuelto: la prueba de distintividad
    de :class:`SimilitudNombre` lo necesita para distinguir una palabra de
    sector (que sí informa al comparar) de un genérico estructural.

    Args:
        pesos_idf: resultado de ``matching.idf.construir_idf``.
        terminos: términos a neutralizar (se comparan en MAYÚSCULAS).
        peso: peso destino. 1.0 es el mínimo del IDF suavizado.

    Returns:
        Un ``PesosIDF`` nuevo; el original no se modifica.
    """
    if peso <= 0:
        raise ValueError(f"peso={peso} debe ser > 0 (el IDF suavizado nunca es cero).")
    nuevos = pesos_idf.pesos.copy()
    tocados = 0
    for token in terminos:
        i = pesos_idf.vocabulario.get(str(token).strip().upper())
        if i is not None and nuevos[i] > peso:
            nuevos[i] = peso
            tocados += 1
    nuevo = replace(
        pesos_idf,
        pesos=nuevos,
        peso_total=np.asarray(pesos_idf.incidencia @ nuevos).ravel(),
    )
    object.__setattr__(nuevo, "_pesos_crudos", pesos_idf.pesos.copy())
    object.__setattr__(nuevo, "_neutralizados", tocados)
    return nuevo


def marcar_informativos(
    pesos_idf: PesosIDF,
    genericos: Iterable[str],
    *,
    longitud_minima: int = 3,
    ignorar_numericos: bool = True,
) -> np.ndarray:
    """Máscara de los tokens que SÍ identifican a una empresa.

    Los tokens puramente numéricos se excluyen por regla, no por umbral: una
    guía de transporte ("FEDEX 453927848470") identifica un ENVÍO, y su IDF es
    máximo justo por ser única — exactamente al revés de lo que conviene.
    """
    if longitud_minima < 1:
        raise ValueError("longitud_minima debe ser >= 1.")
    mascara = np.zeros(len(pesos_idf.pesos), dtype=bool)
    declarados = {str(g).strip().upper() for g in genericos}
    for token, i in pesos_idf.vocabulario.items():
        if len(token) < longitud_minima or token in declarados:
            continue
        if ignorar_numericos and token.isdigit():
            continue
        mascara[i] = True
    return mascara


#: Piso absoluto de documentos para corpus pequeños: por debajo de esto, una
#: fracción del 1 % deja de tener sentido estadístico.
MINIMO_DOCUMENTOS_DISTINTIVO = 5


def frecuencia_distintiva(documentos: int, fraccion: float) -> int:
    """Convierte una fracción del corpus a un número de documentos.

    El piso se declara como FRACCIÓN y no como conteo absoluto porque el IDF
    ya es relativo (``log(N/df)``): un token presente en 1.000 nombres es
    genérico en un corpus de 100.000 y es *todo el corpus* en uno de 1.000.
    Fijar el umbral en absoluto hace que el comparador se comporte distinto
    según el tamaño de la base sin que nadie lo haya pedido — se detectó
    escribiendo la prueba de regresión del modo de falla "palabra de sector
    como identidad", que pasaba en producción y fallaba en el corpus sintético.
    """
    if not 0.0 < fraccion <= 1.0:
        raise ValueError(f"fraccion={fraccion} fuera de (0, 1].")
    return max(MINIMO_DOCUMENTOS_DISTINTIVO, round(fraccion * documentos))


def piso_por_frecuencia(pesos_idf: PesosIDF, frecuencia_maxima: int) -> float:
    """IDF de un token que aparece ``frecuencia_maxima`` veces en el corpus.

    Traduce un piso de informatividad a unidades interpretables: "lo que el
    par comparte debe ser al menos tan distintivo como un token que aparece
    como mucho F veces".
    """
    if frecuencia_maxima < 1:
        raise ValueError("frecuencia_maxima debe ser >= 1.")
    return float(np.log((1.0 + pesos_idf.documentos) / (1.0 + frecuencia_maxima)) + 1.0)


def _peso_interseccion(w: PesosIDF, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
    return np.asarray(w.incidencia[izq].multiply(w.incidencia[der]) @ w.pesos).ravel()


def jaccard_idf(w: PesosIDF, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
    """``W(A∩B) / W(A∪B)``: simétrico; no premia que un nombre contenga al otro."""
    izq = np.asarray(izq, dtype=np.int64)
    der = np.asarray(der, dtype=np.int64)
    if len(izq) == 0 or w.vacio:
        return np.zeros(len(izq), dtype=np.float64)
    inter = _peso_interseccion(w, izq, der)
    union = w.peso_total[izq] + w.peso_total[der] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.clip(np.where(union > 0, inter / union, 0.0), 0.0, 1.0)


def contencion_idf(w: PesosIDF, izq: np.ndarray, der: np.ndarray) -> np.ndarray:
    """``W(A∩B) / min(W(A), W(B))``: 1.0 si el nombre corto está dentro del largo."""
    izq = np.asarray(izq, dtype=np.int64)
    der = np.asarray(der, dtype=np.int64)
    if len(izq) == 0 or w.vacio:
        return np.zeros(len(izq), dtype=np.float64)
    inter = _peso_interseccion(w, izq, der)
    menor = np.minimum(w.peso_total[izq], w.peso_total[der])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.clip(np.where(menor > 0, inter / menor, 0.0), 0.0, 1.0)


def _incidencia_informativa(w: PesosIDF, informativos: np.ndarray):
    inc = getattr(w, "_inc_informativa", None)
    if inc is None:
        from scipy import sparse

        inc = (w.incidencia @ sparse.diags(informativos.astype(np.float64))).tocsr()
        inc.eliminate_zeros()
        object.__setattr__(w, "_inc_informativa", inc)
        object.__setattr__(w, "_n_informativos", np.asarray(inc.sum(axis=1)).ravel())
    return inc


def diferencia_informativa(
    w: PesosIDF, informativos: np.ndarray, izq: np.ndarray, der: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``(|A Δ B|, |A ∩ B|)`` contando SOLO tokens informativos."""
    izq = np.asarray(izq, dtype=np.int64)
    der = np.asarray(der, dtype=np.int64)
    if len(izq) == 0 or w.vacio:
        ceros = np.zeros(len(izq), dtype=np.float64)
        return ceros, ceros.copy()
    inc = _incidencia_informativa(w, informativos)
    n = w._n_informativos
    comun = np.asarray(inc[izq].multiply(inc[der]).sum(axis=1)).ravel()
    return n[izq] + n[der] - 2.0 * comun, comun


def max_idf_compartido(
    w: PesosIDF, informativos: np.ndarray, izq: np.ndarray, der: np.ndarray
) -> np.ndarray:
    """IDF CRUDO del token informativo más distintivo que el par comparte.

    Usa el IDF anterior a neutralizar genéricos: mide si lo compartido de
    verdad identifica, sin confundir una palabra de sector con un conector.
    """
    izq = np.asarray(izq, dtype=np.int64)
    der = np.asarray(der, dtype=np.int64)
    if len(izq) == 0 or w.vacio:
        return np.zeros(len(izq), dtype=np.float64)
    inc_w = getattr(w, "_inc_inf_ponderada", None)
    if inc_w is None:
        from scipy import sparse

        crudos = getattr(w, "_pesos_crudos", w.pesos)
        efectivos = np.where(informativos, crudos, 0.0).reshape(1, -1)
        inc_w = w.incidencia.multiply(sparse.csr_matrix(efectivos)).tocsr()
        inc_w.eliminate_zeros()
        object.__setattr__(w, "_inc_inf_ponderada", inc_w)
    m = inc_w[izq].multiply(w.incidencia[der]).max(axis=1)
    return np.asarray(m.todense()).ravel() if hasattr(m, "todense") else np.asarray(m).ravel()


class SimilitudNombre:
    """Comparador firmado de razones sociales: informatividad + cadena.

    Cumple el protocolo ``matching.spec.Comparator``, así que se pasa a un
    ``CampoSpec(comparador=...)`` como cualquier otro.

    ``sim = alfa · S_tokens + (1 − alfa) · JaroWinkler``, llevada a [−1, 1].

    ``S_tokens`` es :func:`jaccard_idf` salvo cuando el par supera las TRES
    puertas de contención, cada una cerrando una falla medida:

    1. ``diferencia_informativa ≤ max_diferencia_informativa`` — lo que sobra
       en el nombre largo debe ser ruido declarado. Sin ella, "MQE" absorbió
       214 razones sociales distintas.
    2. ``tokens informativos compartidos ≥ min_informativos_compartidos`` — un
       nombre sin identidad propia no puede fusionarse por estar contenido en
       otro. Sin ella, "INTERNATIONAL" absorbió 22.
    3. ``max IDF crudo compartido ≥ piso`` — lo compartido debe ser
       distintivo. Sin ella, "KA DK FLOWERS" y "E FLOWERS" se unían por
       compartir "FLOWERS", presente en 2.739 de 105.705 nombres.

    Args:
        pesos_idf: IDF del corpus, idealmente ya pasado por
            :func:`neutralizar_genericos`.
        vocabulario: los nombres normalizados del corpus, en el MISMO orden
            con el que se construyó ``pesos_idf``.
        genericos_estructurales: términos que cuentan como ruido al comparar.
        alfa: peso de la evidencia de tokens frente a la de cadena.
        prefix_weight: peso del prefijo en Jaro-Winkler (0–0,25).
        max_diferencia_informativa: puerta 1.
        min_informativos_compartidos: puerta 2.
        fraccion_max_distintivo: puerta 3, como FRACCIÓN del corpus. Un token
            presente en más de esa fracción de los nombres no basta para
            fusionar por contención. 0 = puerta desactivada.
        longitud_minima_token: mínimo para considerar un token informativo.
        ignorar_numericos: excluir tokens de solo dígitos.
    """

    name = "razon_social_idf_jw"
    signed = True

    def __init__(
        self,
        pesos_idf: PesosIDF,
        vocabulario,
        *,
        genericos_estructurales: Iterable[str] = (),
        alfa: float = 0.25,
        prefix_weight: float = 0.10,
        max_diferencia_informativa: int = 0,
        min_informativos_compartidos: int = 1,
        fraccion_max_distintivo: float = 0.01,
        longitud_minima_token: int = 3,
        ignorar_numericos: bool = True,
    ) -> None:
        if not 0.0 <= alfa <= 1.0:
            raise ValueError(f"alfa={alfa} fuera de [0, 1].")
        if not 0.0 <= prefix_weight <= 0.25:
            raise ValueError(f"prefix_weight={prefix_weight} fuera de [0, 0.25].")
        if max_diferencia_informativa < 0:
            raise ValueError("max_diferencia_informativa debe ser >= 0.")
        if min_informativos_compartidos < 0:
            raise ValueError("min_informativos_compartidos debe ser >= 0.")
        self.pesos_idf = pesos_idf
        self.indice = pd.Index(vocabulario)  # get_indexer: hashtable en C
        self.alfa = float(alfa)
        self.prefix_weight = float(prefix_weight)
        self.max_diferencia_informativa = int(max_diferencia_informativa)
        self.min_informativos_compartidos = int(min_informativos_compartidos)
        self.informativos = marcar_informativos(
            pesos_idf,
            genericos_estructurales,
            longitud_minima=longitud_minima_token,
            ignorar_numericos=ignorar_numericos,
        )
        self.fraccion_max_distintivo = float(fraccion_max_distintivo)
        self.frecuencia_max_distintivo = (
            frecuencia_distintiva(pesos_idf.documentos, fraccion_max_distintivo)
            if fraccion_max_distintivo
            else 0
        )
        self.piso_distintivo = (
            piso_por_frecuencia(pesos_idf, self.frecuencia_max_distintivo)
            if self.frecuencia_max_distintivo
            else 0.0
        )

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados tienen un nombre real (contrato v0.12.0)."""
        return (pd.Series(left).astype(str).str.len() > 0).to_numpy() & (
            pd.Series(right).astype(str).str.len() > 0
        ).to_numpy()

    def partes(self, left: np.ndarray, right: np.ndarray) -> pd.DataFrame:
        """Desglose por par — el insumo de auditoría cuando un grupo sale mal.

        Returns:
            DataFrame con ``sim_tokens``, ``jaro_winkler``, ``dif_informativa``
            y ``uso_contencion``.
        """
        izq = pd.Series(left).astype(str).to_numpy()
        der = pd.Series(right).astype(str).to_numpy()
        jw = rf_process.cpdist(
            izq.tolist(),
            der.tolist(),
            scorer=JaroWinkler.normalized_similarity,
            dtype=np.float64,
            workers=-1,
            scorer_kwargs={"prefix_weight": self.prefix_weight},
        )
        ii = self.indice.get_indexer(izq)
        jj = self.indice.get_indexer(der)
        conocidos = (ii >= 0) & (jj >= 0)
        tokens = np.zeros(len(izq), dtype=np.float64)
        diferencia = np.full(len(izq), np.inf)
        contenido = np.zeros(len(izq), dtype=bool)
        if conocidos.any():
            iv, jv = ii[conocidos], jj[conocidos]
            jaccard = jaccard_idf(self.pesos_idf, iv, jv)
            contencion = contencion_idf(self.pesos_idf, iv, jv)
            dif, comun = diferencia_informativa(self.pesos_idf, self.informativos, iv, jv)
            admite = dif <= self.max_diferencia_informativa
            admite &= comun >= self.min_informativos_compartidos
            if self.piso_distintivo > 0.0:
                admite &= (
                    max_idf_compartido(self.pesos_idf, self.informativos, iv, jv)
                    >= self.piso_distintivo
                )
            tokens[conocidos] = np.where(admite, np.maximum(contencion, jaccard), jaccard)
            diferencia[conocidos] = dif
            contenido[conocidos] = admite
        return pd.DataFrame(
            {
                "sim_tokens": tokens,
                "jaro_winkler": jw,
                "dif_informativa": diferencia,
                "uso_contencion": contenido,
            }
        )

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Similitud firmada en [−1, 1] por par."""
        if len(left) == 0:
            return np.zeros(0, dtype=np.float64)
        d = self.partes(left, right)
        mezcla = self.alfa * d["sim_tokens"] + (1.0 - self.alfa) * d["jaro_winkler"]
        return np.where(self.valid_mask(left, right), 2.0 * mezcla.to_numpy() - 1.0, 0.0)


def comparador_desde_corpus(
    nombres: Iterable[str],
    *,
    genericos_neutralizados: Iterable[str],
    genericos_estructurales: Iterable[str],
    peso_token_generico: float = 1.0,
    alfa: float = 0.25,
    prefix_weight: float = 0.10,
    max_diferencia_informativa: int = 0,
    min_informativos_compartidos: int = 1,
    fraccion_max_distintivo: float = 0.01,
    longitud_minima_token: int = 3,
    ignorar_numericos: bool = True,
) -> SimilitudNombre:
    """Construye el :class:`SimilitudNombre` de un corpus, en un solo sitio.

    Es la receta completa —vocabulario ordenado, IDF del corpus, genéricos
    neutralizados, comparador— que ``flujo.importadores`` y la cobertura por
    estrellas de L5 (F2.1, ``engine.cobertura``) comparten: una regla se
    escribe una vez. El vocabulario se ordena con clave total para que el
    comparador sea el mismo entre procesos (la semilla de hash de Python no
    interviene).

    Args:
        nombres: razones sociales ya normalizadas, SIN nulos (repetidas o
            no: se deduplican aquí). El IDF se calcula sobre los nombres
            distintos, que es lo que ``importadores`` medía desde 0.22.0.
        genericos_neutralizados: términos cuyo IDF baja a
            ``peso_token_generico`` (:func:`neutralizar_genericos`).
        genericos_estructurales: términos que cuentan como ruido al comparar
            (puerta 1 de :class:`SimilitudNombre`).
        peso_token_generico: peso destino de los genéricos neutralizados.
        alfa: peso de la evidencia de tokens frente a la de cadena.
        prefix_weight: peso del prefijo en Jaro-Winkler.
        max_diferencia_informativa: puerta 1 de :class:`SimilitudNombre`.
        min_informativos_compartidos: puerta 2.
        fraccion_max_distintivo: puerta 3.
        longitud_minima_token: mínimo para considerar un token informativo.
        ignorar_numericos: excluir tokens de solo dígitos.

    Returns:
        El comparador; su IDF queda en ``comparador.pesos_idf``.
    """
    vocabulario = pd.Series(sorted(pd.Series(nombres).unique()))
    pesos = construir_idf(vocabulario.reset_index(drop=True))
    pesos = neutralizar_genericos(pesos, genericos_neutralizados, peso=peso_token_generico)
    return SimilitudNombre(
        pesos,
        vocabulario.to_numpy(),
        genericos_estructurales=genericos_estructurales,
        alfa=alfa,
        prefix_weight=prefix_weight,
        max_diferencia_informativa=max_diferencia_informativa,
        min_informativos_compartidos=min_informativos_compartidos,
        fraccion_max_distintivo=fraccion_max_distintivo,
        longitud_minima_token=longitud_minima_token,
        ignorar_numericos=ignorar_numericos,
    )
