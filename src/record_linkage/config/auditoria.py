"""record_linkage.config.auditoria — Los parámetros efectivos del motor, declarados una vez (F1.12).

Qué es
------
El bloque ``parametros{perfil, lsh, scoring, pesos, prioridad_fuentes}`` que
``manifest.json`` (``exporters.escritor.Manifiesto``) declara de una corrida y
que el alias de v1 ``config_auditoria.json`` (``reporting.strategies.
ConfigAuditStrategy``) repite. Hasta F1.12 ese bloque lo construía solo la
estrategia de L6, con ``orchestrator_version: "8.5"`` fijo y leyendo la
prioridad de fuentes de ``source_quality_weights`` sin la segunda mitad de la
regla del ``Orchestrator`` (si el perfil no trae pesos —``produccion_estandar``
no los trae— el golden usa el ORDEN de las fuentes): la auditoría decía ``{}``
mientras el golden usaba otra cosa.

Reglas
------
* Las claves que se leen del perfil (``CLAVES_LSH``, ``CLAVES_SCORING``,
  ``CLAVE_PESOS``, ``CLAVE_PRIORIDAD``) se nombran AQUÍ y en ningún otro
  sitio; ``Orchestrator.prioridad_fuentes`` llama a
  :func:`prioridad_del_perfil` para la mitad de la regla que depende del
  perfil.
* :func:`parametros_motor` recibe la prioridad REAL (la del ``Orchestrator``,
  que conoce las fuentes) cuando el llamador la tiene; sin ella, devuelve la
  del perfil y, si el perfil no trae pesos, una tupla vacía: no inventa.
* Una clave que el perfil no trae se escribe como ``None``: el manifiesto
  describe lo que había, no lo repara.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CLAVES_LSH",
    "CLAVES_SCORING",
    "CLAVE_PESOS",
    "CLAVE_PRIORIDAD",
    "ParametrosMotor",
    "parametros_motor",
    "perfil_activo",
    "prioridad_del_perfil",
]

#: Parámetros del bloqueo (L2) que describen la corrida.
CLAVES_LSH: tuple[str, ...] = (
    "lsh_permutations",
    "lsh_threshold",
    "lsh_ngram",
    "cross_source_only",
)
#: Parámetros del scoring (L3) que describen la corrida.
CLAVES_SCORING: tuple[str, ...] = ("score_threshold", "min_name_similarity", "max_nit_distance")
#: Pesos de las variables del scoring.
CLAVE_PESOS = "weights"
#: La clave del perfil cuyo ORDEN de claves es la prioridad de fuentes del
#: golden (``PARTIAL_CONFIG_KEYS`` en ``profiles.py``: los valores solo
#: desempatan). ``source_priorities`` del nivel superior no la lee nadie.
CLAVE_PRIORIDAD = "source_quality_weights"


@dataclass(frozen=True)
class ParametrosMotor:
    """Lo que el motor usó de verdad, en la forma del manifiesto.

    Attributes:
        perfil: nombre del perfil activo (``config["profile"]``), o ``None``.
        lsh: ``CLAVES_LSH`` → valor del perfil (``None`` si no la trae).
        scoring: ``CLAVES_SCORING`` → valor del perfil.
        pesos: ``weights`` del perfil.
        prioridad_fuentes: la del golden (L5), en orden.
    """

    perfil: str | None
    lsh: dict[str, Any]
    scoring: dict[str, Any]
    pesos: dict[str, Any]
    prioridad_fuentes: tuple[str, ...]

    def a_dict(self) -> dict[str, Any]:
        return {
            "perfil": self.perfil,
            "lsh": dict(self.lsh),
            "scoring": dict(self.scoring),
            "pesos": dict(self.pesos),
            "prioridad_fuentes": list(self.prioridad_fuentes),
        }


def perfil_activo(config: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any]]:
    """``(nombre, parámetros)`` del perfil activo de un ``config`` del Orchestrator."""
    nombre = config.get("profile")
    perfiles = config.get("profiles") or {}
    perfil = perfiles.get(nombre) if nombre is not None else None
    return (str(nombre) if nombre is not None else None), (perfil or {})


def prioridad_del_perfil(perfil: Mapping[str, Any]) -> list[str]:
    """Prioridad de fuentes que el perfil declara: las claves de
    ``source_quality_weights`` en su orden; vacía si no las trae."""
    pesos = perfil.get(CLAVE_PRIORIDAD) or {}
    return [str(k) for k in pesos]


def parametros_motor(
    config: Mapping[str, Any], *, prioridad_fuentes: Sequence[str] | None = None
) -> ParametrosMotor:
    """Los parámetros efectivos del motor a partir del ``config`` que corrió.

    Args:
        config: el ``config`` del ``Orchestrator`` (``crear_config_orchestrator``),
            con ``profile`` y ``profiles[profile]`` ya con los overrides.
        prioridad_fuentes: la REAL del golden (``Orchestrator.prioridad_fuentes``)
            si el llamador la tiene; si no, la del perfil (vacía si el perfil
            no trae ``source_quality_weights``).
    """
    nombre, perfil = perfil_activo(config)
    prioridad = (
        list(prioridad_fuentes) if prioridad_fuentes is not None else prioridad_del_perfil(perfil)
    )
    return ParametrosMotor(
        perfil=nombre,
        lsh={k: perfil.get(k) for k in CLAVES_LSH},
        scoring={k: perfil.get(k) for k in CLAVES_SCORING},
        pesos=dict(perfil.get(CLAVE_PESOS) or {}),
        prioridad_fuentes=tuple(str(f) for f in prioridad),
    )
