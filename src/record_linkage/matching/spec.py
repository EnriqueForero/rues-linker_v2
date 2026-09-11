"""matching.spec — declaración de variables y métodos de identificación.

Esta es la **API principal** del módulo `matching`. Permite declarar cada
variable que participa en la identificación de entidades, con su propio
método de comparación, normalizador, peso, y umbral de concordancia.

Reemplaza el patrón actual de `extra_features` (lista plana donde toda
columna se compara con el mismo método) por un modelo declarativo y
extensible que escala a empresas multinacionales con docenas de variables.

Diseño:

- ``VariableSpec`` describe **una** variable (NIT, RAZON_SOCIAL, TELEFONO,
  EMAIL, DIRECCION, CIUDAD, PAIS, etc.) con su método de comparación.
- ``MatchingProfile`` agrupa especificaciones + reglas de combinación
  (mínimo de concordancias, vetos, pesos globales).
- Los métodos de comparación están en ``matching.comparators`` (vectorizados).

Ejemplo de uso::

    from record_linkage.matching import (
        MatchingProfile, VariableSpec,
        ExactWithDV, JaroWinklerSigned, PhoneLastDigits,
        EmailDomainLocal, AddressTokenSet, CityNormalizedEqual,
    )

    profile = MatchingProfile(
        variables=[
            VariableSpec("NIT", ExactWithDV(),
                         weight=0.40, concordance_threshold=0.99,
                         vetoes_mismatch=True),
            VariableSpec("RAZON_SOCIAL", JaroWinklerSigned(),
                         weight=0.30, concordance_threshold=0.85),
            VariableSpec("TELEFONO", PhoneLastDigits(n=7),
                         weight=0.10, concordance_threshold=0.99),
            VariableSpec("EMAIL", EmailDomainLocal(),
                         weight=0.08, concordance_threshold=0.80),
            VariableSpec("DIRECCION", AddressTokenSet(),
                         weight=0.07, concordance_threshold=0.70),
            VariableSpec("CIUDAD", CityNormalizedEqual(),
                         weight=0.05, concordance_threshold=0.99),
        ],
        min_concordances_with_nit=2,
        min_concordances_without_nit=3,
        score_threshold=0.65,
    )

Filosofía:

- Cada comparador retorna similitud por par en ``[-1, +1]`` (firmado)
  o ``[0, 1]`` (no firmado). El módulo ``combiner`` agrega.
- Toda operación es vectorizada con numpy + rapidfuzz para escalar a
  millones de pares sin bucles Python.
- ``concordance_threshold`` define a partir de qué similitud la variable
  "concuerda" (γ_i = 1 en la nomenclatura Fellegi-Sunter). Esto permite
  el conteo de ``min_concordances``, que es el filtro de robustez clave
  para producción sin GT real calibrado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class Comparator(Protocol):
    """Protocolo que todo comparador de variables debe cumplir.

    Un comparador implementa una **función vectorizada** que compara dos
    arrays de valores (lado izquierdo y derecho de cada par candidato)
    y retorna similitud por par.

    Atributos requeridos:
        - ``name``: identificador único del comparador.
        - ``signed``: ``True`` si el rango es [-1,+1] (penaliza discrepancia),
          ``False`` si es [0,1] (solo premia coincidencia).
    """

    name: str
    signed: bool

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Compara pares de valores. Retorna array de similitudes por par."""
        ...


@dataclass
class VariableSpec:
    """Especificación de una variable participante en el matching.

    Args:
        name: Nombre de la columna en el DataFrame (ej. ``"RAZON_SOCIAL"``).
        comparator: Instancia de ``Comparator`` que define cómo comparar
            valores de esta variable (ej. ``JaroWinklerSigned()``).
        weight: Peso de esta variable en el score combinado. Los pesos se
            normalizan a suma=1 sobre el `MatchingProfile`. Default ``1.0``.
        concordance_threshold: Similitud mínima para considerar que la
            variable "concuerda" en un par (γ_i = 1 en Fellegi-Sunter).
            Default ``0.85`` para comparadores fuzzy, ``0.99`` para exact.
        vetoes_mismatch: Si ``True``, una discrepancia FUERTE en esta
            variable (similitud < ``veto_threshold``) **prohíbe** la fusión
            del par independiente del score global. Default ``False``.
            Útil para NIT: NITs distintos NO deben fusionarse aunque el
            nombre sea idéntico (filiales/sucursales son entidades distintas).
        veto_threshold: Similitud por debajo de la cual se aplica el veto.
            Solo relevante si ``vetoes_mismatch=True``. Default ``-0.5``
            (firmado) o ``0.0`` (no firmado). Una mismatch firmada de -1.0
            siempre veta.
        required_for_match: Si ``True``, la variable DEBE estar presente
            (no-null) en ambos registros para considerar fusión. Útil para
            CIUDAD en geo-blocking estricto. Default ``False``.
        alias: Nombre alternativo si el DataFrame de la otra fuente tiene
            columna con nombre distinto (ej. ``"TEL"`` vs ``"TELEFONO"``).
            Default ``None`` (usa ``name``).

    Notas de diseño:
        - El ``concordance_threshold`` define el γ_i = 1 (concuerda) vs
          γ_i = 0 (no concuerda) de Fellegi-Sunter clásico. Esto permite
          contar variables concordantes y aplicar reglas como "al menos K
          deben concordar". Más robusto que score global a ruido en una
          sola variable.
        - El veto bidireccional (`vetoes_mismatch`) es la pieza clave
          contra el modo de falla más común en producción sin etiquetas
          reales: dos empresas con nombre parecido y NITs distintos NO son
          la misma empresa, sin importar qué diga el score.
    """

    name: str
    comparator: Comparator
    weight: float = 1.0
    concordance_threshold: float = 0.85
    vetoes_mismatch: bool = False
    veto_threshold: float | None = None
    required_for_match: bool = False
    alias: str | None = None

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"weight debe ser >= 0, recibido {self.weight}")
        if not 0.0 <= self.concordance_threshold <= 1.0:
            raise ValueError(
                f"concordance_threshold ∈ [0,1], recibido {self.concordance_threshold}"
            )
        # veto_threshold default depende del signo del comparador
        if self.veto_threshold is None:
            self.veto_threshold = -0.5 if self.comparator.signed else 0.0

    def resolve_column(self, df: pd.DataFrame) -> str:
        """Resuelve el nombre de columna efectivo en el DataFrame.

        Si la columna ``self.name`` existe, la usa. Si no, intenta ``self.alias``.

        Raises:
            KeyError: si ni ``name`` ni ``alias`` están en el DataFrame.
        """
        if self.name in df.columns:
            return self.name
        if self.alias and self.alias in df.columns:
            return self.alias
        raise KeyError(
            f"VariableSpec({self.name}): ni '{self.name}' ni alias '{self.alias}' "
            f"están en el DataFrame (columnas: {list(df.columns)[:10]}...)"
        )


@dataclass
class MatchingProfile:
    """Perfil completo de matching: variables + reglas de combinación.

    Esta clase es el **contrato de matching de talla empresarial**: declara
    qué variables comparar, cómo, con qué peso, y cuándo concluir que dos
    registros son la misma entidad.

    Args:
        variables: Lista de ``VariableSpec``. Orden no importa.
        min_concordances_with_nit: Mínimo de variables que deben concordar
            (incluyendo NIT si está presente) para fusionar cuando AMBOS
            registros tienen NIT no-nulo. Default ``2``.
        min_concordances_without_nit: Mínimo de variables que deben
            concordar cuando al menos uno de los registros NO tiene NIT.
            Suele ser mayor que con NIT. Default ``3``.
        score_threshold: Score combinado mínimo para fusionar. Aplica
            adicionalmente al criterio de concordancias. Default ``0.65``.
        require_city_match: Si ``True``, fuerza ``CIUDAD`` concordante.
            Geo-blocking estricto, útil para reducir FP cuando hay nombres
            comunes en distintas ciudades. Default ``False``.

    Resultado del matching:
        El método ``score_pairs(...)`` retorna un DataFrame con:

        - ``score``: score combinado ponderado (rango aproximado [-1, +1]).
        - ``concordances``: número de variables concordantes (γ_total).
        - ``vetoed``: True si algún veto bidireccional se activó.
        - ``decision``: 'MATCH' / 'NON_MATCH' / 'VETO' / 'BELOW_K'.
        - ``score_<var>``: similitud por variable (para debugging).

    Diseño contra falsos positivos:
        Hoy el paquete fusiona ``KANGNAM PRIMEINC`` con ``KANGNAM TEXTILE``
        porque comparten prefijo y se compara solo con token_set_ratio
        global. Con este perfil:

        1. ``JaroWinklerSigned`` daría similitud ~0.55 (no 0.85+ que
           `token_set_ratio` daba).
        2. CIUDAD probablemente sería distinta → veto si ``required_for_match``.
        3. TELEFONO distinto (si presente) → veto.
        4. ``min_concordances_without_nit=3`` evitaría fusión basada solo
           en nombre.

        Tres mecanismos independientes apuntan al mismo error. Robustez por
        redundancia, no por un único umbral mágico.
    """

    variables: list[VariableSpec]
    min_concordances_with_nit: int = 2
    min_concordances_without_nit: int = 3
    score_threshold: float = 0.65
    require_city_match: bool = False
    name: str = "default"

    # Metadata de calibración (rellenado por `calibrate_em` si se usa)
    calibration_source: str = "uncalibrated"
    calibration_notes: str = ""

    def __post_init__(self) -> None:
        if not self.variables:
            raise ValueError("MatchingProfile requiere al menos 1 variable")
        names_seen: set[str] = set()
        for v in self.variables:
            if v.name in names_seen:
                raise ValueError(f"VariableSpec.name duplicado: {v.name}")
            names_seen.add(v.name)
        # Normalizar pesos
        total = sum(v.weight for v in self.variables)
        if total <= 0:
            raise ValueError("Suma de pesos debe ser > 0")
        self._normalized_weights = {v.name: v.weight / total for v in self.variables}

    @property
    def normalized_weights(self) -> dict[str, float]:
        """Pesos normalizados a suma=1. Inmutable."""
        return dict(self._normalized_weights)

    def get_variable(self, name: str) -> VariableSpec:
        """Recupera una VariableSpec por nombre. Raises KeyError si no existe."""
        for v in self.variables:
            if v.name == name:
                return v
        raise KeyError(f"VariableSpec '{name}' no está en este MatchingProfile")

    def to_dict(self) -> dict[str, Any]:
        """Serialización para auditoría/export (config_auditoria)."""
        return {
            "name": self.name,
            "calibration_source": self.calibration_source,
            "calibration_notes": self.calibration_notes,
            "min_concordances_with_nit": self.min_concordances_with_nit,
            "min_concordances_without_nit": self.min_concordances_without_nit,
            "score_threshold": self.score_threshold,
            "require_city_match": self.require_city_match,
            "variables": [
                {
                    "name": v.name,
                    "comparator": v.comparator.name,
                    "signed": v.comparator.signed,
                    "weight": v.weight,
                    "normalized_weight": self._normalized_weights[v.name],
                    "concordance_threshold": v.concordance_threshold,
                    "vetoes_mismatch": v.vetoes_mismatch,
                    "veto_threshold": v.veto_threshold,
                    "required_for_match": v.required_for_match,
                    "alias": v.alias,
                }
                for v in self.variables
            ],
        }
