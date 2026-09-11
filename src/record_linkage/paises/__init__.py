"""record_linkage.paises — canonización de países contra un catálogo declarado.

Uso típico::

    from record_linkage.paises import canonizar_pais

    resultado = canonizar_pais(df["PAIS_DESTINO"])
    df = df.join(resultado.tabla)
    print(f"{resultado.n_grafias} grafías → {resultado.n_canonicos} canónicos")
"""

from .canonizador import (
    AliasAmbiguo,
    ResultadoPaises,
    canonizar_pais,
    indice_paises,
    sugerir_alias_pais,
)
from .catalogo import (
    CATALOGO_PAISES,
    ETIQUETA_NO_PAIS,
    ETIQUETA_SIN_CLASIFICAR,
    ISO_NO_PAIS,
    ISO_SIN_CLASIFICAR,
    PATRONES_NO_PAIS,
)

__all__ = [
    "CATALOGO_PAISES",
    "ETIQUETA_NO_PAIS",
    "ETIQUETA_SIN_CLASIFICAR",
    "ISO_NO_PAIS",
    "ISO_SIN_CLASIFICAR",
    "PATRONES_NO_PAIS",
    "AliasAmbiguo",
    "ResultadoPaises",
    "canonizar_pais",
    "indice_paises",
    "sugerir_alias_pais",
]
