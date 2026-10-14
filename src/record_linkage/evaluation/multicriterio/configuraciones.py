"""Configuraciones de variables extra: qué se mide y cómo se escribe para el motor.

Una :class:`Configuracion` es una corrida del motor (nombre, descripción, ``extra_features`` y
``ajustes_perfil``); ``CONFIGURACIONES`` son las doce que ``scripts/multicriterio_plantilla.py``
mide por defecto y ``VARIABLES_A_MEDIR`` los comparadores con los que se mide el aporte de cada
variable. Las configuraciones se escriben con el nombre de columna de la plantilla (``MUNICIPIO``) y
:func:`a_columnas_del_motor` / :func:`ajustes_del_motor` las traducen a lo que el motor ve
(``CIUDAD``). :func:`validar_configuraciones` falla rápido con mensaje accionable,
:func:`leer_configuraciones` lee un JSON y :func:`descripcion_tipos` lista los comparadores
registrados, incluidos los ``tipo_*`` del puente de campos, que este módulo registra al importarse.
Parte del paquete ``evaluation.multicriterio``; la lectura de conjunto está en su ``__init__``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import record_linkage.matching.puente_campos  # noqa: F401  — registra los comparadores tipo_*
from record_linkage.matching.comparadores_extra import obtener, tipos_disponibles

#: Columnas que el motor renombra en la ingesta (``col_ciudad="MUNICIPIO"`` → ``CIUDAD``). Las
#: configuraciones se escriben con el nombre de la plantilla; el motor recibe el canónico.
COLUMNAS_CANONICAS: dict[str, str] = {"MUNICIPIO": "CIUDAD"}
NOMBRE_BASE = "A_base"


@dataclass(frozen=True)
class Configuracion:
    """Una corrida del motor: nombre, para qué sirve y sus variables extra (vacío = base)."""

    nombre: str
    descripcion: str
    extra_features: tuple[dict[str, object], ...] = ()
    #: Sobrescrituras del perfil (``linkage(ajustes_perfil=...)``), p. ej. llaves de bloqueo.
    ajustes_perfil: dict[str, object] = field(default_factory=dict)

    def como_dict(self) -> dict[str, object]:
        return {
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "extra_features": [dict(f) for f in self.extra_features],
            "ajustes_perfil": dict(self.ajustes_perfil),
        }

    def extras_en_texto(self) -> str:
        """``EMAIL:0.1 (email_signed), TELEFONO:0.1 (telefono_signed)`` o ``—``."""
        texto = ", ".join(f"{f['column']}:{f['weight']} ({f['type']})" for f in self.extra_features)
        if self.ajustes_perfil:
            texto += f" + {self.ajustes_perfil}"
        return texto or "—"


def es_base(config: Configuracion) -> bool:
    """La base es la configuración sin variables extra ni ajustes: el motor tal cual."""
    return not config.extra_features and not config.ajustes_perfil


def variable(columna: str, peso: float, tipo: str) -> dict[str, object]:
    """Una variable extra en la forma que acepta ``linkage()``: ``{"column", "weight", "type"}``."""
    return {"column": columna, "weight": peso, "type": tipo}


CONFIGURACION_BASE = Configuracion(
    NOMBRE_BASE, "sin variables extra: lo que hace el motor hoy (§5 de la guía)"
)
CONTACTO = (variable("EMAIL", 0.10, "email_signed"), variable("TELEFONO", 0.10, "telefono_signed"))
GEO = (
    variable("MUNICIPIO", 0.05, "categoria_tolerante_signed"),
    variable("DIRECCION", 0.05, "tipo_direccion"),
)

#: Las configuraciones que se miden por defecto. A-F son las pedidas; G-J atribuyen el efecto a
#: cada variable por separado y muestran qué pasa con las que casi no tienen datos; K y L prueban
#: el correo como llave de bloqueo.
CONFIGURACIONES: tuple[Configuracion, ...] = (
    CONFIGURACION_BASE,
    Configuracion("B_contacto", "correo y teléfono firmados, 0,10 cada uno", CONTACTO),
    Configuracion(
        "C_contacto_geo", "B más municipio (tolerante) y dirección, 0,05 cada uno", CONTACTO + GEO
    ),
    Configuracion(
        "D_contacto_geo_alto",
        "como C con pesos altos (0,15/0,15/0,10/0,10): cuándo dominan las extras",
        (
            variable("EMAIL", 0.15, "email_signed"),
            variable("TELEFONO", 0.15, "telefono_signed"),
            variable("MUNICIPIO", 0.10, "categoria_tolerante_signed"),
            variable("DIRECCION", 0.10, "tipo_direccion"),
        ),
    ),
    Configuracion(
        "E_solo_geo",
        "solo municipio tolerante 0,10: coincide en 7 de 25 negativos, ¿premia mal?",
        (variable("MUNICIPIO", 0.10, "categoria_tolerante_signed"),),
    ),
    Configuracion(
        "F_no_firmados",
        "las mismas variables de C con comparadores NO firmados (solo premian, nunca castigan)",
        (
            variable("EMAIL", 0.10, "categorical"),
            variable("TELEFONO", 0.10, "categorical"),
            variable("MUNICIPIO", 0.05, "categorical"),
            variable("DIRECCION", 0.05, "token_set_ratio"),
        ),
    ),
    Configuracion(
        "G_solo_email", "solo correo firmado 0,10", (variable("EMAIL", 0.10, "email_signed"),)
    ),
    Configuracion(
        "H_solo_telefono",
        "solo teléfono firmado 0,10",
        (variable("TELEFONO", 0.10, "telefono_signed"),),
    ),
    Configuracion(
        "I_solo_direccion", "solo dirección 0,10", (variable("DIRECCION", 0.10, "tipo_direccion"),)
    ),
    Configuracion(
        "J_todas",
        "C más departamento, CIIU (solapamiento) y sitio web, 0,05 cada uno",
        CONTACTO
        + GEO
        + (
            variable("DEPARTAMENTO", 0.05, "categoria_tolerante_signed"),
            variable("CIIU", 0.05, "conjunto_signed"),
            variable("SITIO_WEB", 0.05, "exact_signed"),
        ),
    ),
    Configuracion(
        "K_llave_email",
        "B más EMAIL como llave de bloqueo (candidatos por correo idéntico): ¿se une «sin NIT "
        "pero mismo correo»?",
        CONTACTO,
        {"llaves_bloqueo": ["EMAIL"]},
    ),
    Configuracion(
        "L_llave_email_pesos_altos",
        "K con correo y teléfono a 0,30: ¿puede el contacto cargar solo con un par sin NIT?",
        (variable("EMAIL", 0.30, "email_signed"), variable("TELEFONO", 0.30, "telefono_signed")),
        {"llaves_bloqueo": ["EMAIL"]},
    ),
)

#: Con qué comparador se mide el aporte de cada variable (y, para contraste, el exacto).
VARIABLES_A_MEDIR: tuple[tuple[str, str], ...] = (
    ("EMAIL", "email_signed"),
    ("EMAIL", "categorical_signed"),
    ("TELEFONO", "telefono_signed"),
    ("TELEFONO", "categorical_signed"),
    ("DIRECCION", "tipo_direccion"),
    ("DIRECCION", "categorical_signed"),
    ("MUNICIPIO", "categoria_tolerante_signed"),
    ("MUNICIPIO", "categorical_signed"),
    ("DEPARTAMENTO", "categoria_tolerante_signed"),
    ("CIIU", "conjunto_signed"),
    ("SITIO_WEB", "exact_signed"),
)


# ---------------------------------------------------------------------------
# Tipos de comparador y configuraciones: validación y traducción de columnas
# ---------------------------------------------------------------------------


def descripcion_tipos() -> pd.DataFrame:
    """Una fila por comparador registrado: ``TIPO``, ``FIRMADO`` y ``DESCRIPCION`` (del registro
    mismo, no de una lista copiada a mano). Incluye los ``tipo_*`` del puente de campos."""
    filas = []
    for nombre in tipos_disponibles():
        comparador = obtener(nombre)
        if comparador is None:  # pragma: no cover — tipos_disponibles sale del mismo registro
            continue
        filas.append(
            {
                "TIPO": nombre,
                "FIRMADO": "sí (−1..+1: castiga la discrepancia)" if comparador.firmado else "no",
                "DESCRIPCION": comparador.descripcion,
            }
        )
    return pd.DataFrame(filas, columns=["TIPO", "FIRMADO", "DESCRIPCION"])


def a_columnas_del_motor(
    extra_features: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Las variables extra con el nombre de columna que el motor ve tras la ingesta.

    El motor renombra ``MUNICIPIO`` a ``CIUDAD`` (``col_ciudad``) antes de validar las variables
    extra; declarar ``MUNICIPIO`` lanza ``ValueError``. Devuelve copias: no toca la configuración.
    """
    return [
        {**f, "column": COLUMNAS_CANONICAS.get(str(f["column"]), str(f["column"]))}
        for f in extra_features
    ]


def ajustes_del_motor(ajustes_perfil: dict[str, object]) -> dict[str, object]:
    """Los ajustes del perfil con las llaves de bloqueo traducidas a columnas canónicas."""
    salida = dict(ajustes_perfil)
    llaves = salida.get("llaves_bloqueo")
    if isinstance(llaves, list | tuple) and llaves:
        salida["llaves_bloqueo"] = [COLUMNAS_CANONICAS.get(str(c), str(c)) for c in llaves]
    return salida


def _validar_variable(nombre: str, f: object, columnas: set[str], tipos: set[str]) -> None:
    if not isinstance(f, dict):
        raise ValueError(
            f"{nombre}: la variable {f!r} no es un dict; cada una es "
            "{'column', 'weight', 'type'} (una tupla no sirve: el motor la rechaza)."
        )
    faltan = [k for k in ("column", "weight", "type") if k not in f]
    if faltan:
        raise ValueError(
            f"{nombre}: a la variable {f} le faltan {faltan}; cada una es "
            "{'column', 'weight', 'type'} (una tupla no sirve: el motor la rechaza)."
        )
    if str(f["column"]) not in columnas:
        raise ValueError(
            f"{nombre}: la columna {f['column']!r} no está en las fuentes "
            f"({sorted(columnas)}). Escriba el nombre de la plantilla (MUNICIPIO, no CIUDAD)."
        )
    peso = f["weight"]
    if isinstance(peso, bool) or not isinstance(peso, int | float) or float(peso) <= 0:
        raise ValueError(f"{nombre}: el peso de {f['column']} debe ser un número > 0.")
    if str(f["type"]) not in tipos:
        raise ValueError(
            f"{nombre}: el tipo {f['type']!r} no está registrado. Tipos: {sorted(tipos)}."
        )


def validar_configuraciones(configuraciones: Sequence[Configuracion], columnas: set[str]) -> None:
    """Fail-fast: nombres únicos, tipos registrados, pesos positivos y columnas que existen en
    las fuentes (con el nombre de la plantilla). Una llave de bloqueo inexistente también falla:
    el motor la omitiría con un aviso que la medición no vería."""
    tipos = set(tipos_disponibles())
    nombres = [c.nombre for c in configuraciones]
    if len(set(nombres)) != len(nombres):
        raise ValueError(f"Hay configuraciones con el mismo nombre: {nombres}")
    for config in configuraciones:
        llaves = config.ajustes_perfil.get("llaves_bloqueo", [])
        for llave in llaves if isinstance(llaves, list | tuple) else []:
            if str(llave) not in columnas:
                raise ValueError(
                    f"{config.nombre}: la llave de bloqueo {llave!r} no está en las fuentes; el "
                    "motor la omitiría con un aviso que la medición no vería."
                )
        for f in config.extra_features:
            _validar_variable(config.nombre, f, columnas, tipos)


def leer_configuraciones(ruta: str | Path) -> tuple[Configuracion, ...]:
    """Las configuraciones de un JSON (lista de ``{"nombre", "descripcion", "extra_features",
    "ajustes_perfil"}``; los dos últimos son opcionales)."""
    ruta = Path(ruta)
    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(crudo, list) or not crudo:
        raise ValueError(f"{ruta} debe ser una lista no vacía de configuraciones.")
    salida = []
    for i, c in enumerate(crudo):
        if not isinstance(c, dict) or "nombre" not in c:
            raise ValueError(f"{ruta}: la configuración {i} no es un objeto con 'nombre'.")
        salida.append(
            Configuracion(
                str(c["nombre"]),
                str(c.get("descripcion", "")),
                tuple(dict(f) for f in c.get("extra_features", [])),
                dict(c.get("ajustes_perfil", {})),
            )
        )
    return tuple(salida)
