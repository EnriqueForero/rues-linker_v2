"""processing.saneamiento — limpieza previa a normalizar (v0.22.0).

Existe porque `matching.normalizadores` asume texto ya legible, y las bases de
comercio exterior no lo son: traen entidades HTML sin decodificar
(``&#147``), mojibake de doble codificación, espacios no separables, códigos
de cliente antepuestos y siglas partidas en letras sueltas (``O.M.G`` →
``O M G``). Normalizar sin sanear produce tokens basura que el IDF trata como
altamente informativos, que es exactamente al revés de lo que conviene.

Orden que importa (y que costó una corrida entera descubrir): la conversión
de puntuación a espacio va SIEMPRE después de plegar tildes. El patrón
``[^\\w\\s()]`` se evalúa con semántica ASCII —pandas 3 delega la regex en
Arrow/RE2—, así que aplicarlo sobre texto acentuado borra la "É" de
"AMÉRICA". Medido: canonizar países cayó del 100 % al 57 % de cobertura sin
ningún error visible.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

import pandas as pd

__all__ = [
    "PRELIMPIEZA_COMERCIO_EXTERIOR",
    "ascii_mayusculas",
    "sanear_texto",
    "unir_iniciales",
]

#: Reemplazos (regex, sustituto) habituales en destinatarios de exportación.
#: Son un DEFECTO, no una obligación: `sanear_texto` acepta otra lista.
PRELIMPIEZA_COMERCIO_EXTERIOR: tuple[tuple[str, str], ...] = (
    (r"&#\d{2,5};?", " "),  # entidad HTML numérica
    (r"^\s*\(?\s*[0-9][0-9A-Za-z]{0,14}\s*\)?\s*[-–:]\s*", ""),  # código/NIT inicial
    (r"\bC\s*/\s*O\b", " "),  # "care of"
    (r"\bA\s*/\s*C\b", " "),  # "account of"
)

#: Espacios Unicode que no son " " y rompen el conteo de tokens.
_ESPACIOS_RAROS = r"[   ​-‍﻿]"

#: Controles C0/C1: llegan de exportaciones mal codificadas.
_CONTROLES = r"[\x00-\x1f\x7f-\x9f]"

#: Una a seis letras sueltas seguidas: "O M G" → "OMG", "U S" → "US".
_RE_INICIALES = re.compile(r"(?<![A-Z0-9])((?:[A-Z]\s+){1,6}[A-Z])(?![A-Z0-9])")


def sanear_texto(
    serie: pd.Series,
    *,
    prelimpieza: Iterable[tuple[str, str]] = PRELIMPIEZA_COMERCIO_EXTERIOR,
) -> pd.Series:
    """Decodifica entidades HTML, quita controles y aplica ``prelimpieza``.

    Args:
        serie: texto crudo.
        prelimpieza: pares (patrón regex, sustituto) aplicados en orden.

    Returns:
        Serie de tipo ``string``, sin nulos, con espacios colapsados.
    """
    s = serie.astype("string").fillna("")
    s = s.map(lambda t: html.unescape(t) if "&" in t else t)
    s = s.str.replace(_ESPACIOS_RAROS, " ", regex=True)
    s = s.str.replace(_CONTROLES, " ", regex=True)
    for patron, sustituto in prelimpieza:
        s = s.str.replace(patron, sustituto, regex=True)
    return s.str.replace(r"\s+", " ", regex=True).str.strip()


def ascii_mayusculas(serie: pd.Series) -> pd.Series:
    """Pliega tildes a ASCII y pasa a MAYÚSCULAS, colapsando espacios."""
    return (
        serie.astype("string")
        .fillna("")
        .str.normalize("NFKD")
        .str.encode("ascii", errors="ignore")
        .str.decode("ascii")
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def unir_iniciales(serie: pd.Series, *, activo: bool = True) -> pd.Series:
    """Puntuación → espacio y reunión de iniciales sueltas.

    ``"O.M.G QUIMICOS C.A"`` → ``"OMG QUIMICOS CA"``. Sin esto, el nombre
    queda con tokens de una letra que el filtro de longitud descarta, y dos
    empresas distintas terminan compartiendo su único token informativo.
    Medido: "O M G QUIMICOS C A" y "RG QUIMICOS C A" se fusionaban.

    Args:
        serie: texto YA en ASCII/mayúsculas (ver :func:`ascii_mayusculas`).
        activo: ``False`` deja la puntuación convertida pero no une iniciales.
    """
    s = (
        serie.astype("string")
        .fillna("")
        .str.replace(r"[^\w\s()]", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    if not activo:
        return s
    return s.map(lambda t: _RE_INICIALES.sub(lambda m: m.group(1).replace(" ", ""), t)).astype(
        "string"
    )
