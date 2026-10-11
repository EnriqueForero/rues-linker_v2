"""Hojas ENLACES (los vínculos finales y las relaciones de propiedad) y CONTACTOS (cómo contactar a cada empresa).

ENLACES lista, por empresa y destino, cada par cuyo nivel final es EMPRESA, GRUPO o RELACION_PROPIEDAD, con
el vínculo principal primero. CONTACTOS saca, de los registros destino vinculados, un renglón por dato de
contacto (sitio web, teléfono, correo, dirección). El orden de columnas y de filas de ambas es contrato.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.constantes import ORDEN_NIVEL

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.tablas import EstadoTablas

__all__ = ["construir_contactos", "construir_enlaces"]

#: Columnas de ENLACES (nombres internos de la tabla de pares), en el orden de la hoja.
_COLUMNAS_ENLACES = [
    "ID_EXPORTADOR",
    "NOMBRE_EXPORTADOR",
    "PAIS_EXPORTADOR",
    "FUENTE_DESTINO",
    "ID_DESTINO",
    "NOMBRE_DESTINO",
    "PAIS_DESTINO",
    "NIVEL_FINAL",
    "CONFIANZA",
    "ESTADO",
    "ORIGEN",
    "ES_PRINCIPAL",
    "MOTIVO",
    "EVIDENCIA_EXTRA",
    "MISMO_PAIS",
    "GRAFIA_ANCLA",
    "SUP_A",
    "SUP_B",
    "COMPARTIDAS",
    "SOBRA_EXPORTADOR",
    "SOBRA_DESTINO",
    "SCORE_RUES_LINKER",
    "SIM_TOKENS_IDF",
    "JARO_WINKLER",
    "NIVEL_AUTO",
    "BANDA_AUTO",
    "DECISION",
    "AUTOR",
    "RAZON",
    "CIUDAD",
    "WEB",
    "MATRIZ",
    "ID_ENTIDAD",
]

#: Nombre que cada columna interna toma en la hoja ENLACES.
_RENOMBRES_ENLACES = {
    "FUENTE_DESTINO": "FUENTE",
    "NIVEL_FINAL": "NIVEL",
    "SUP_A": "NOMBRE_COMPARADO_EXPORTADOR",
    "SUP_B": "NOMBRE_COMPARADO_DESTINO",
    "GRAFIA_ANCLA": "GRAFIA_EXPORTADOR",
    "AUTOR": "REVISADO_POR",
    "RAZON": "RAZON_REVISION",
    "DECISION": "DECISION_REVISION",
    "CIUDAD": "CIUDAD_DESTINO",
    "WEB": "WEB_DESTINO",
    "MATRIZ": "MATRIZ_DESTINO",
    "ID_ENTIDAD": "ENTIDAD_DESTINO",
}

#: Niveles que entran a ENLACES (su orden dentro de un mismo destino es el de ``ORDEN_NIVEL``).
_NIVELES_DE_ENLACE = ["EMPRESA", "GRUPO", "RELACION_PROPIEDAD"]

_COLUMNAS_CONTACTOS = [
    "ID_EXPORTADOR",
    "NOMBRE_EXPORTADOR",
    "FUENTE",
    "ID_DESTINO",
    "NOMBRE_DESTINO",
    "NIVEL",
    "PAIS",
    "CIUDAD",
    "TIPO_CONTACTO",
    "VALOR_CONTACTO",
]

#: (tipo de contacto, columna de ``prep`` del destino) en el orden en que salen por vínculo.
_TIPOS_DE_CONTACTO = (
    ("SITIO_WEB", "WEB"),
    ("TELEFONO", "TELEFONO"),
    ("CORREO", "EMAIL"),
    ("DIRECCION", "DIRECCION"),
)


def construir_enlaces(estado: EstadoTablas) -> pd.DataFrame:
    """Hoja ENLACES: los pares vinculados y las relaciones de propiedad, con el principal primero.

    Ordena por empresa, destino, ``ES_PRINCIPAL`` (los principales antes), nivel (EMPRESA, GRUPO,
    RELACION_PROPIEDAD) y ``SCORE_RUES_LINKER`` de mayor a menor. ``FUENTE`` sale con la etiqueta del
    destino y ``ES_PRINCIPAL`` como ``SÍ``/``NO``. Sin pares, devuelve la tabla vacía con las mismas columnas.
    """
    # Hallazgo F3: la tabla conserva el índice de ``pares`` (sin ``reset_index``), a diferencia de las demás
    # hojas; no afecta al Excel ni a ``huella_enlaces`` (ignoran el índice) pero sí a quien use ``.loc``.
    pares = estado.pares
    enlaces = (
        pares[pares["NIVEL_FINAL"].isin(_NIVELES_DE_ENLACE)].copy()
        if len(pares)
        else pd.DataFrame(columns=_COLUMNAS_ENLACES)
    )
    if len(enlaces):
        enlaces["_o"] = enlaces["NIVEL_FINAL"].map(ORDEN_NIVEL)
        enlaces = enlaces.sort_values(
            ["ID_EXPORTADOR", "FUENTE_DESTINO", "ES_PRINCIPAL", "_o", "SCORE_RUES_LINKER"],
            ascending=[True, True, False, True, False],
            kind="stable",
        )
    enlaces = enlaces.reindex(columns=_COLUMNAS_ENLACES).rename(columns=_RENOMBRES_ENLACES)
    enlaces["FUENTE"] = enlaces["FUENTE"].map(estado.etiquetas).fillna(enlaces["FUENTE"])
    enlaces["ES_PRINCIPAL"] = np.where(enlaces["ES_PRINCIPAL"].astype(bool), "SÍ", "NO")
    return enlaces


def construir_contactos(estado: EstadoTablas) -> pd.DataFrame:
    """Hoja CONTACTOS: un renglón por dato de contacto de los registros destino vinculados.

    Solo cuentan los vínculos de nivel EMPRESA y el registro principal de cada grupo: los de otras filiales
    (Zelvora Polska, Zelvora Austria…) no sirven para contactar al exportador. De cada registro salen, en
    este orden, el sitio web, el teléfono, el correo y la dirección que no estén vacíos.
    """
    # Hallazgo F3: se descarta el texto 'nan' pero no 'None', 'n.a.' ni '-' (``unir_valores`` sí descarta
    # 'nan' y 'none'): un contacto 'None' o '-' llega a la hoja como si fuera un dato.
    vinc = estado.vinculos
    prep = estado.interno["prep"]
    vc = vinc[(vinc["NIVEL_FINAL"] == "EMPRESA") | vinc["ES_PRINCIPAL"]] if len(vinc) else vinc
    filas: list[dict[str, object]] = []
    r: Any  # fila de itertuples(): sus campos son las columnas de ``vinculos``
    for r in vc.itertuples(index=False) if len(vc) else []:
        pr = prep[r.FUENTE_DESTINO]
        for tipo, col in _TIPOS_DE_CONTACTO:
            v = str(pr[col].iat[r.pos_b]).strip()
            if v and v.lower() != "nan":
                filas.append(
                    {
                        "ID_EXPORTADOR": r.ID_EXPORTADOR,
                        "NOMBRE_EXPORTADOR": r.NOMBRE_EXPORTADOR,
                        "FUENTE": estado.etiquetas[r.FUENTE_DESTINO],
                        "ID_DESTINO": r.ID_DESTINO,
                        "NOMBRE_DESTINO": r.NOMBRE_DESTINO,
                        "NIVEL": r.NIVEL_FINAL,
                        "PAIS": r.PAIS_DESTINO,
                        "CIUDAD": r.CIUDAD,
                        "TIPO_CONTACTO": tipo,
                        "VALOR_CONTACTO": v,
                    }
                )
    return pd.DataFrame(filas, columns=_COLUMNAS_CONTACTOS)
