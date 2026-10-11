"""Apoyo de las pruebas de candidatos, clasificación, vínculos, web y validación del motor de vinculación (F3.1-C).

Estas etapas se pasan tablas entre sí: la ancla por grafías (``graf``), los registros de un destino (``prep``), los
pares candidatos, los pares por empresa (``L``) y las decisiones. Este módulo las arma a mano, con empresas
inventadas (las mismas marcas de ``apoyo_vinculacion``) y SOLO con las columnas que esas etapas leen. Los valores
esperados de las pruebas salen de correr el motor ORIGINAL (la celda ``%%writefile`` del notebook 10) con las
mismas tablas.

Trae también un COMPARADOR DE MENTIRA (``ComparadorFalso``): responde ``rasgos`` y ``palabra_comun`` con lo que
la prueba le fije, sin depender del puntaje real. Así cada regla y cada umbral de ``clasificar`` se ejerce justo
en su frontera. El original acepta el mismo comparador (duck typing).
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pandas as pd

#: Los 15 rasgos que devuelve ``Comparador.rasgos``, con el valor de «dos nombres sin nada en común».
RASGOS_NEUTROS: dict[str, Any] = {
    "UTILES_A": 1,
    "UTILES_B": 1,
    "SOBRA_A": "",
    "SOBRA_B": "",
    "COMPARTIDAS": "",
    "N_COMPARTIDAS": 0,
    "N_FUERTES": 0,
    "SOLO_NOMBRE_PILA": False,
    "SOBRAS_GENERICAS": False,
    "SIN_SOBRAS": False,
    "ERRATA": False,
    "TRUNCADA": False,
    "MARCAS_OK": True,
    "COMPACTO_IGUAL": False,
    "ABREVIATURA": False,
}


def rasgos(**cambios: Any) -> dict[str, Any]:
    """Rasgos de un par: los neutros con ``cambios`` encima (``rasgos(N_COMPARTIDAS=1, SIN_SOBRAS=True)``)."""
    desconocidos = set(cambios) - set(RASGOS_NEUTROS)
    assert not desconocidos, f"rasgos desconocidos: {sorted(desconocidos)}"
    return {**RASGOS_NEUTROS, **cambios}


class ComparadorFalso:
    """Comparador de mentira: ``rasgos(a, b, trunc)`` y ``palabra_comun(nombre)`` con lo fijado a mano.

    ``por_par`` mapea ``(superficie_a, superficie_b)`` a los rasgos del par. ``comunes`` son los nombres que
    ``palabra_comun`` declara frecuentes. ``llamadas`` guarda cada ``(a, b, trunc)`` recibido, en orden.
    """

    def __init__(
        self,
        por_par: dict[tuple[str, str], dict[str, Any]],
        comunes: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self.por_par = por_par
        self.comunes = frozenset(comunes)
        self.llamadas: list[tuple[str, str, bool]] = []

    def rasgos(self, a: str, b: str, trunc_a: bool) -> dict[str, Any]:
        self.llamadas.append((a, b, trunc_a))
        return self.por_par[(a, b)]

    def palabra_comun(self, nombre: str) -> bool:
        return nombre in self.comunes


class Grafia(NamedTuple):
    """Una grafía de la ancla ya deduplicada: lo que ``deduplicar_ancla`` y ``ejecutar`` dejan en ``graf``."""

    pais: str
    nombre: str
    norm: str
    iso: str = "ESP"
    alias: str = ""
    truncado: bool = False
    intermediario: bool = False
    id_comparable: str = ""
    dominio_web: str = ""
    dominio_email: str = ""
    tel: str = ""
    ciudad: str = ""


class Registro(NamedTuple):
    """Un registro de un destino ya preparado: lo que ``preparar_fuente`` deja en estas columnas."""

    id_registro: str
    nombre: str
    norm: str
    paises: str = "ESP"
    alias: str = ""
    grupo: str = ""
    grupo_norm: str = ""
    dominio_web: str = ""
    dominio_email: str = ""
    tel: str = ""
    id_comparable: str = ""
    ciudad: str = ""


def graf_de(filas: list[Grafia], vetos: set[tuple[int, int]] | None = None) -> pd.DataFrame:
    """Tabla ``graf`` (una fila por grafía, en el orden dado) con ``attrs["vetos"]`` si se piden."""
    graf = pd.DataFrame(
        {
            "CLAVE": [f"{f.pais}|{f.nombre}" for f in filas],
            "PAIS_ORIGINAL": [f.pais for f in filas],
            "NOMBRE_ORIGINAL": [f.nombre for f in filas],
            "PAIS_ISO3": [f.iso for f in filas],
            "NOMBRE_NORM": [f.norm for f in filas],
            "ALIAS_NORM": [f.alias for f in filas],
            "TRUNCADO": [f.truncado for f in filas],
            "INTERMEDIARIO": [f.intermediario for f in filas],
            "ID_COMPARABLE": [f.id_comparable for f in filas],
            "DOMINIO_WEB": [f.dominio_web for f in filas],
            "DOMINIO_EMAIL": [f.dominio_email for f in filas],
            "TEL_DIGITOS": [f.tel for f in filas],
            "CIUDAD_NORM": [f.ciudad for f in filas],
        }
    )
    if vetos is not None:
        graf.attrs["vetos"] = vetos
    return graf


def destino_de(filas: list[Registro], fuente: str = "ORBIS") -> pd.DataFrame:
    """Tabla ``prep`` de un destino (una fila por registro, en el orden dado)."""
    return pd.DataFrame(
        {
            "FUENTE": [fuente] * len(filas),
            "ID_REGISTRO": [f.id_registro for f in filas],
            "NOMBRE_ORIGINAL": [f.nombre for f in filas],
            "NOMBRE_NORM": [f.norm for f in filas],
            "ALIAS_NORM": [f.alias for f in filas],
            "GRUPO_ORIGINAL": [f.grupo for f in filas],
            "GRUPO_NORM": [f.grupo_norm for f in filas],
            "PAISES_EVIDENCIA": [f.paises for f in filas],
            "DOMINIO_WEB": [f.dominio_web for f in filas],
            "DOMINIO_EMAIL": [f.dominio_email for f in filas],
            "TEL_DIGITOS": [f.tel for f in filas],
            "ID_COMPARABLE": [f.id_comparable for f in filas],
            "CIUDAD_NORM": [f.ciudad for f in filas],
        }
    )


class Par(NamedTuple):
    """Un par que dejó ``generar_candidatos``: la grafía ``g``, el registro ``pos_b`` y sus superficies."""

    g: int
    pos_b: int
    sup_a: str = "A"
    sup_b: str = "B"
    tipo_a: str = "NOMBRE"
    tipo_b: str = "NOMBRE"
    score: float = 0.9


def pares_de(filas: list[Par]) -> pd.DataFrame:
    """Tabla de candidatos con las columnas y el orden que deja ``generar_candidatos``."""
    return pd.DataFrame(
        {
            "g": [f.g for f in filas],
            "SUP_A": [f.sup_a for f in filas],
            "TIPO_SUP_A": [f.tipo_a for f in filas],
            "SUP_B": [f.sup_b for f in filas],
            "SCORE_RUES_LINKER": [f.score for f in filas],
            "SIM_TOKENS_IDF": [0.5] * len(filas),
            "JARO_WINKLER": [0.8] * len(filas),
            "DIF_INFORMATIVA": [0.0] * len(filas),
            "USO_CONTENCION": [False] * len(filas),
            "pos_b": [f.pos_b for f in filas],
            "TIPO_SUP_B": [f.tipo_b for f in filas],
        }
    )


class ParAuto(NamedTuple):
    """Un par ya clasificado, antes de agrupar por empresa."""

    g: int
    pos_b: int
    nivel: str
    banda: str
    score: float = 0.9
    fuente: str = "ORBIS"
    motivo: str = ""


def pares_auto_de(filas: list[ParAuto]) -> pd.DataFrame:
    """Pares clasificados con las columnas que lee ``agregar_por_entidad``."""
    return pd.DataFrame(
        {
            "g": [f.g for f in filas],
            "pos_b": [f.pos_b for f in filas],
            "FUENTE_DESTINO": [f.fuente for f in filas],
            "NIVEL_AUTO": [f.nivel for f in filas],
            "BANDA_AUTO": [f.banda for f in filas],
            "SCORE_RUES_LINKER": [f.score for f in filas],
            "MOTIVO": [f.motivo for f in filas],
        }
    )


class FilaDecision(NamedTuple):
    """Una decisión como la deja ``leer_decisiones`` (solo los campos que cambian entre pruebas)."""

    tipo: str
    fuente: str
    clave_a: str
    clave_b: str
    decision: str
    nombre_b: str = ""
    autor: str = "Revisora Uno"
    archivo: str = "decisiones_1.csv"
    orden: int = 0


COLUMNAS_TABLA_DECISIONES = [
    "TIPO",
    "FUENTE",
    "CLAVE_A",
    "NOMBRE_A",
    "CLAVE_B",
    "NOMBRE_B",
    "DECISION",
    "AUTOR",
    "FECHA",
    "RAZON",
    "ARCHIVO",
    "ORDEN",
]


def tabla_decisiones(filas: list[FilaDecision]) -> pd.DataFrame:
    """Tabla de decisiones con las 12 columnas de ``leer_decisiones`` (sin leer archivos)."""
    return pd.DataFrame(
        [
            [f.tipo, f.fuente, f.clave_a, "", f.clave_b, f.nombre_b, f.decision, f.autor,
             "2026-10-01", "", f.archivo, f.orden]
            for f in filas
        ],
        columns=COLUMNAS_TABLA_DECISIONES,
    )  # fmt: skip
