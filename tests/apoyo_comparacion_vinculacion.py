"""Apoyo de las pruebas de comparación, decisiones y deduplicación del motor de vinculación (F3.1-B).

Las pruebas de ``comparar.py`` y ``deduplicar.py`` no necesitan la configuración completa del ``Normalizador``
(locale global y catálogo de países): les basta uno que responda lo mismo en lo que usan estos módulos
—``utiles``, ``marcas``, ``compacto``, ``legales`` y ``genericos``— sobre un vocabulario fijo. ``NormalizadorDoble``
es el ``Normalizador`` real con ese vocabulario, así que no copia ninguna regla. Los valores esperados de las
pruebas salen de correr el motor ORIGINAL con el mismo vocabulario.

Todas las empresas son inventadas (las mismas marcas de ``apoyo_vinculacion``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from record_linkage.flujo.vinculacion.comparar import Comparador
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.decisiones import COLUMNAS_DECISION
from record_linkage.flujo.vinculacion.normalizar import Normalizador

#: Formas legales (palabras sueltas) que el doble retira de los nombres.
LEGALES = frozenset(
    {"SA", "SL", "SLU", "SAU", "SAS", "SRL", "GMBH", "INC", "LTD", "COOP", "SCOOP", "AG", "KG"}
)

#: Palabras que el doble declara genéricas (sector, geografía, forma de grupo).
GENERICOS = frozenset(
    {
        "ALIMENTOS", "ALIMENTARIAS", "ALIMENTACION", "EMPRESARIAL", "INDUSTRIAS", "CONSERVAS", "BEBIDAS",
        "LACTEOS", "CARNICOS", "CEREALES", "DULCES", "VINAGRES", "FOODS", "SALSAS", "PASTAS", "HOLDING",
        "IBERIA", "GROUP", "INTERNATIONAL", "EUROPE", "COOPERATIVA", "DEL", "SUR", "NORTE",
    }
)  # fmt: skip

#: Nombres normalizados de varias bases: el vocabulario con que se construye el ``Comparador``.
CORPUS = [
    "ZELVORA ALIMENTOS", "KRAMENTO FOODS", "TAVORIN CONSERVAS MARINAS", "TAVORIIN CONSERVAS MARINAS",
    "QUELMAR", "QUELMAR LACTEOS", "QUELMAR BEBIDAS", "QUELMAR CARNICOS",
    "KORIMBA QUESOS", "KORIMBA ACEITES", "PELUVIKA", "PLVK", "KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA",
    "KR EMP VAL ALIM IB", "ALFREDO ZELVORA", "ALFREDO KRAMENTO", "GALDIMAR", "GALDIMAR HOLDING IBERIA",
    "COOPERATIVA CONSERVAS CANTABRICO", "COOPERATIVA CONSERVAS CANTAB", "KL FOODS", "KLFOODS", "KRAMENTO 2",
    "KRAMENTO 3", "J MARTINEZ", "A MARTINEZ", "SU", "SUR", "KEV", "", "ZELVORA ALIMENTOS",
]  # fmt: skip

#: País de la ancla → ISO3 (lo que dejaría ``Normalizador.paises``).
ISO3 = {
    "España": "ESP",
    "Alemania": "DEU",
    "Francia": "FRA",
    "Italia": "ITA",
    "Suiza": "CHE",
    "": "",
}


class NormalizadorDoble(Normalizador):
    """El ``Normalizador`` real, sin su configuración pesada, sobre un vocabulario fijo.

    Hereda ``utiles``, ``marcas`` y ``compacto`` del real (la regla se escribe una sola vez) y solo fija
    ``legales`` y ``genericos``. No llama a ``super().__init__``: éste registra un locale global en la
    librería y arma el catálogo de países, que estas pruebas no necesitan.
    """

    def __init__(
        self, legales: frozenset[str] = LEGALES, genericos: frozenset[str] = GENERICOS
    ) -> None:
        self.legales = legales
        self.genericos = genericos


def config_prueba(clave_ancla: str = "EXPORTADORES", **cambios: Any) -> ConfigVinculacion:
    """``ConfigVinculacion`` mínima (ancla ``EXPORTADORES``); ``cambios`` pisa cualquier parámetro."""
    ancla = Fuente(
        clave=clave_ancla, archivo="no_se_lee.xlsx", col_nombre="Empresa", col_pais="País"
    )
    return ConfigVinculacion(ancla=ancla, **cambios)


def comparador(cfg: ConfigVinculacion | None = None, corpus: list[str] | None = None) -> Comparador:
    """``Comparador`` del paquete nuevo con el doble de normalizador y el vocabulario de prueba."""
    return Comparador(
        NormalizadorDoble(), CORPUS if corpus is None else corpus, cfg or config_prueba()
    )


class FilaAncla(NamedTuple):
    """Una fila de la ancla ya preparada: lo que ``preparar_fuente`` dejaría en estas columnas."""

    pais: str
    nombre: str
    norm: str
    alias: str = ""
    truncado: bool = False


class FilaDestino(NamedTuple):
    """Una fila de un destino ya preparada."""

    id_registro: str
    nombre: str
    norm: str
    paises: str = "ESP"


def prep_ancla(filas: list[FilaAncla]) -> pd.DataFrame:
    """Tabla ``prep`` de la ancla con las columnas que lee ``deduplicar_ancla``.

    ``ID_REGISTRO`` sigue la regla ``EXP-F<fila de Excel>`` (la primera fila de datos es la 2).
    """
    return pd.DataFrame(
        {
            "ID_REGISTRO": [f"EXP-F{k:04d}" for k in range(2, len(filas) + 2)],
            "PAIS_ORIGINAL": [f.pais for f in filas],
            "NOMBRE_ORIGINAL": [f.nombre for f in filas],
            "PAIS_ISO3": [ISO3[f.pais] for f in filas],
            "NOMBRE_NORM": [f.norm for f in filas],
            "ALIAS_NORM": [f.alias for f in filas],
            "TRUNCADO": [f.truncado for f in filas],
            "INTERMEDIARIO": [False] * len(filas),
        }
    )


def prep_destino(filas: list[FilaDestino]) -> pd.DataFrame:
    """Tabla ``prep`` de un destino con las columnas que lee ``deduplicar_destino``."""
    return pd.DataFrame(
        {
            "FUENTE": ["ORBIS"] * len(filas),
            "ID_REGISTRO": [f.id_registro for f in filas],
            "NOMBRE_ORIGINAL": [f.nombre for f in filas],
            "NOMBRE_NORM": [f.norm for f in filas],
            "PAISES_EVIDENCIA": [f.paises for f in filas],
        }
    )


def escribir_decisiones(ruta: Path, filas: list[tuple[str, str, str, str, str, str]]) -> Path:
    """CSV de decisiones con las filas ``(TIPO, FUENTE, CLAVE_A, CLAVE_B, DECISION, AUTOR)``."""
    tabla = pd.DataFrame(
        [[t, fu, a, "", b, "", d, au, "2026-10-01", ""] for t, fu, a, b, d, au in filas],
        columns=COLUMNAS_DECISION,
    )
    tabla.to_csv(ruta, index=False, encoding="utf-8-sig")
    return ruta
