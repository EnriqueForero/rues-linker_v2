"""paises.canonizador — grafías de país → nombre canónico + ISO 3166 (v0.22.0).

El campo "país" de una base de comercio exterior es, él mismo, un problema de
resolución de entidades: en la base de importadores de referencia había 529
grafías para 214 países. Se resuelve contra el catálogo declarado
(:mod:`record_linkage.paises.catalogo`) en dos pasadas —exacta y sin
puntuación— y lo que no mapea se MARCA, nunca se adivina.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..processing.saneamiento import ascii_mayusculas, sanear_texto
from .catalogo import (
    CATALOGO_PAISES,
    ETIQUETA_NO_PAIS,
    ETIQUETA_SIN_CLASIFICAR,
    ISO_NO_PAIS,
    ISO_SIN_CLASIFICAR,
    PATRONES_NO_PAIS,
)

__all__ = [
    "AliasAmbiguo",
    "ResultadoPaises",
    "canonizar_pais",
    "indice_paises",
    "sugerir_alias_pais",
]

Catalogo = Iterable[tuple[str, str, tuple[str, ...]]]


class AliasAmbiguo(ValueError):
    """Una misma grafía está declarada en dos países distintos."""

    def __init__(self, grafia: str, iso_a: str, iso_b: str) -> None:
        super().__init__(
            f"Qué pasó: la grafía '{grafia}' está declarada en dos países "
            f"({iso_a} y {iso_b}). Por qué importa: la canonización dejaría de "
            f"ser determinista y el resultado dependería del orden del catálogo. "
            f"Qué hacer: quítela de uno de los dos en CATALOGO_PAISES."
        )


@dataclass(frozen=True)
class ResultadoPaises:
    """Salida de :func:`canonizar_pais`.

    Attributes:
        tabla: DataFrame alineado con la serie de entrada, con las columnas
            ``PAIS_ISO3``, ``PAIS_FINAL``, ``PAIS_METODO`` y
            ``PAIS_NORMALIZADO``.
        n_grafias: grafías distintas en la entrada.
        n_canonicos: países canónicos distintos en la salida.
        sin_clasificar: grafías normalizadas que no mapearon a nada.
    """

    tabla: pd.DataFrame
    n_grafias: int
    n_canonicos: int
    sin_clasificar: pd.Series

    @property
    def cobertura(self) -> float:
        """Proporción de filas asignadas a un país o a una zona franca."""
        if self.tabla.empty:
            return 1.0
        return float((self.tabla["PAIS_METODO"] != "sin_clasificar").mean())


def _clave(texto: str) -> str:
    """Grafía → clave comparable: sin tildes, mayúsculas, espacios colapsados."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", t).strip()


def _sin_puntuacion(clave: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", clave)).strip()


def indice_paises(
    catalogo: Catalogo = CATALOGO_PAISES,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Construye los dos índices de búsqueda: exacto y sin puntuación.

    Raises:
        AliasAmbiguo: si una grafía aparece en dos países distintos.
    """
    exacto: dict[str, tuple[str, str]] = {}
    laxo: dict[str, tuple[str, str]] = {}
    for iso, canonico, alias in catalogo:
        for grafia in (canonico, *alias):
            k = _clave(grafia)
            if k in exacto and exacto[k][0] != iso:
                raise AliasAmbiguo(k, exacto[k][0], iso)
            exacto[k] = (iso, canonico)
            laxo.setdefault(_sin_puntuacion(k), (iso, canonico))
    return exacto, laxo


def canonizar_pais(
    serie: pd.Series,
    *,
    catalogo: Catalogo = CATALOGO_PAISES,
    patrones_no_pais: Iterable[str] = PATRONES_NO_PAIS,
    agrupar_no_pais: bool = True,
    agrupar_sin_clasificar: bool = True,
) -> ResultadoPaises:
    """Canoniza una columna de país. Lo que no mapea se marca, no se adivina.

    Args:
        serie: grafías crudas del país.
        catalogo: catálogo declarado; por defecto ``CATALOGO_PAISES``.
        patrones_no_pais: expresiones que identifican valores que no son un
            país (zonas francas). Vacío = desactivado.
        agrupar_no_pais: ``True`` reúne todas esas grafías bajo una etiqueta
            única; ``False`` conserva cada una y las marca igualmente.
        agrupar_sin_clasificar: ``True`` (histórico) pone la misma etiqueta
            ``SIN CLASIFICAR`` a toda grafía que no mapeó. ``False`` conserva
            la grafía dentro de la etiqueta (``SIN CLASIFICAR: NO DEFINIDO``),
            de modo que dos grafías distintas sin clasificar **no comparten**
            ``PAIS_FINAL``. Importa porque aguas abajo ``PAIS_FINAL`` es el
            campo categórico que veta la fusión: con la etiqueta única, la
            misma razón social bajo ``NO DEFINIDO`` y bajo ``SIN INFORMACION``
            se fusionaba en un solo grupo (medido en 0.22.2).

    Returns:
        ``ResultadoPaises``. La columna ``PAIS_METODO`` toma los valores
        ``catalogo_exacto``, ``catalogo_laxo``, ``zona_franca`` o
        ``sin_clasificar``: ese campo es la auditoría de la canonización.
    """
    normalizada = ascii_mayusculas(sanear_texto(serie))
    exacto, laxo = indice_paises(catalogo)
    laxa = normalizada.str.replace(r"[^A-Z0-9 ]", " ", regex=True)
    laxa = laxa.str.replace(r"\s+", " ", regex=True).str.strip()

    golpe_exacto = normalizada.map(exacto.get)
    golpe_laxo = laxa.map(laxo.get)
    patrones = list(patrones_no_pais)
    if patrones:
        es_no_pais = normalizada.str.match("|".join(patrones)).fillna(False).to_numpy()
    else:
        es_no_pais = np.zeros(len(normalizada), dtype=bool)

    iso3: list[str] = []
    canonico: list[str] = []
    metodo: list[str] = []
    for bruto, a, b, z in zip(normalizada, golpe_exacto, golpe_laxo, es_no_pais, strict=True):
        if isinstance(a, tuple):
            iso3.append(a[0])
            canonico.append(a[1])
            metodo.append("catalogo_exacto")
        elif isinstance(b, tuple):
            iso3.append(b[0])
            canonico.append(b[1])
            metodo.append("catalogo_laxo")
        elif z:
            iso3.append(ISO_NO_PAIS)
            canonico.append(ETIQUETA_NO_PAIS if agrupar_no_pais else bruto)
            metodo.append("zona_franca")
        else:
            iso3.append(ISO_SIN_CLASIFICAR)
            canonico.append(
                ETIQUETA_SIN_CLASIFICAR
                if agrupar_sin_clasificar
                else f"{ETIQUETA_SIN_CLASIFICAR}: {bruto}"
            )
            metodo.append("sin_clasificar")

    tabla = pd.DataFrame(
        {
            "PAIS_ISO3": iso3,
            "PAIS_FINAL": canonico,
            "PAIS_METODO": metodo,
            "PAIS_NORMALIZADO": normalizada.to_numpy(),
        },
        index=serie.index,
    )
    sin_clasificar = tabla.loc[tabla["PAIS_METODO"] == "sin_clasificar", "PAIS_NORMALIZADO"]
    return ResultadoPaises(
        tabla=tabla,
        n_grafias=int(serie.astype("string").nunique()),
        n_canonicos=int(
            tabla.loc[tabla["PAIS_METODO"] != "sin_clasificar", "PAIS_FINAL"].nunique()
        ),
        sin_clasificar=sin_clasificar,
    )


def sugerir_alias_pais(
    sin_clasificar: pd.Series,
    *,
    catalogo: Catalogo = CATALOGO_PAISES,
    maximo: int = 25,
) -> pd.DataFrame:
    """Para lo que no mapeó: el país del catálogo más parecido, como SUGERENCIA.

    Es una ayuda para editar el catálogo a mano, nunca una asignación
    automática. Los pares difíciles no se parecen (TURQUIA/TURKIYE) y varios
    de los que se parecen son países distintos (GUINEA/GUINEA-BISSAU): usarla
    como regla produciría errores silenciosos.
    """
    columnas = ["valor", "n_filas", "sugerencia", "similitud"]
    if sin_clasificar.empty:
        return pd.DataFrame(columns=columnas)
    from rapidfuzz import process as rf_process
    from rapidfuzz.distance import JaroWinkler

    canonicos = [c for _, c, _ in catalogo]
    conteo = sin_clasificar.value_counts().head(maximo)
    filas = []
    for valor, n in conteo.items():
        puntajes = rf_process.cdist([valor], canonicos, scorer=JaroWinkler.normalized_similarity)[0]
        k = int(np.argmax(puntajes))
        filas.append(
            {
                "valor": valor,
                "n_filas": int(n),
                "sugerencia": canonicos[k],
                "similitud": round(float(puntajes[k]), 3),
            }
        )
    return pd.DataFrame(filas, columns=columnas)
