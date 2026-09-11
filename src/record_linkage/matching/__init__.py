"""matching — arquitectura multi-variable con métodos por variable.

API pública para definir cómo se identifica una entidad combinando
múltiples variables con métodos de comparación distintos. Reemplaza el
patrón de ``extra_features`` plano por un modelo declarativo.

Uso típico::

    from record_linkage.matching import (
        VariableMatcher, default_colombia_profile,
    )

    matcher = VariableMatcher(default_colombia_profile())
    scored = matcher.score_pairs(candidate_pairs, source_df)

Para definir tu propio profile::

    from record_linkage.matching import (
        MatchingProfile, VariableSpec,
        ExactWithDV, JaroWinklerSigned,
    )

    profile = MatchingProfile(
        variables=[
            VariableSpec("MY_ID", ExactWithDV(), weight=0.5, vetoes_mismatch=True),
            VariableSpec("MY_NAME", JaroWinklerSigned(), weight=0.5),
        ],
        min_concordances_with_nit=2,
        min_concordances_without_nit=2,
        score_threshold=0.6,
    )

Ver ``spec.py`` para documentación detallada de ``VariableSpec`` y
``MatchingProfile``. Ver ``comparators.py`` para el catálogo completo.
"""

from __future__ import annotations

# v0.20.0 — El puente registra los comparadores de cada TipoCampo en el
# catálogo que consume el scorer de producción. Se importa aquí, y no bajo
# demanda, para que `tipos_disponibles()` diga la verdad completa desde el
# primer momento: un catálogo que crece según quién lo haya importado antes es
# una fuente de errores silenciosos en validación de configuración.
from . import puente_campos as _puente_campos
from .combiner import (
    DECISION_BELOW_K,
    DECISION_BELOW_T,
    DECISION_MATCH,
    DECISION_MISSING_REQUIRED,
    DECISION_VETO,
    VariableMatcher,
)
from .comparators import (
    AddressTokenSet,
    CategoricalSigned,
    CityNormalizedEqual,
    EmailDomainLocal,
    ExactOrZero,
    ExactSigned,
    ExactWithDV,
    JaroWinklerSigned,
    PhoneLastDigits,
    TokenSetSigned,
    TokenSortSigned,
)
from .pipeline_integration import (
    MatcherPostProcessor,
    apply_matcher_to_linkage_result,
)
from .spec import MatchingProfile, VariableSpec

__all__ = [
    "DECISION_BELOW_K",
    "DECISION_BELOW_T",
    "DECISION_MATCH",
    "DECISION_MISSING_REQUIRED",
    "DECISION_VETO",
    "AddressTokenSet",
    "CategoricalSigned",
    # Comparadores - Geo y dirección
    "CityNormalizedEqual",
    "EmailDomainLocal",
    "ExactOrZero",
    "ExactSigned",
    # Comparadores - Identificadores
    "ExactWithDV",
    # Comparadores - Nombres
    "JaroWinklerSigned",
    # Integración con pipeline
    "MatcherPostProcessor",
    # Spec
    "MatchingProfile",
    # Comparadores - Contacto
    "PhoneLastDigits",
    "TokenSetSigned",
    "TokenSortSigned",
    # Combiner
    "VariableMatcher",
    "VariableSpec",
    "apply_matcher_to_linkage_result",
    # Profiles
    "default_colombia_profile",
    "default_colombia_profile_conservative",
    "default_colombia_profile_recall",
    "default_international_profile",
]


def default_colombia_profile() -> MatchingProfile:
    """Profile por defecto BALANCEADO (mejor F1) para entidades colombianas.

    Calibración: óptimo de F1 global medido E2E sobre el GT sintético
    grande (12,427 registros).

    Parámetros óptimos por barrido empírico:
        - min_concordances_with_nit=2
        - min_concordances_without_nit=1  (NO subir a 2 sin medir)
        - score_threshold=0.50

    Métricas E2E REPRODUCIBLES (correr `scripts/benchmark_e2e_matcher.py`):

        ┌─────────────────────────┬────────┬──────────┬──────────┐
        │ Config                  │ F1 glb │ F1 SIN_NIT│ P SIN_NIT│
        ├─────────────────────────┼────────┼──────────┼──────────┤
        │ Baseline (sin matcher)  │ 0.873  │ 0.629    │ 0.559    │
        │ Este profile (K=1,t=0.5)│ 0.908  │ 0.732    │ 0.790    │
        │ Conservador (K=2,t=0.4) │ 0.876  │ 0.445    │ 0.964    │
        └─────────────────────────┴────────┴──────────┴──────────┘

    NOTA SOBRE AUDITORÍA EXTERNA:
        Una revisión posterior señaló que K=1 sobre
        pares aleatorios da F1=0.08. La medición es correcta para pares
        aleatorios pero NO refleja el flujo E2E real (donde el matcher actúa
        como post-procesador de un baseline ya filtrado por LSH+scorer).
        Sobre clustering completo, K=1 con threshold=0.5 da el mejor F1.
        Si necesitas precision casi perfecta (regulación, KYC), usar
        `default_colombia_profile_conservative()` con K=2.

    ADVERTENCIA: todas las cifras son sobre GT sintético. Para producción
    sobre datos reales (RUES/DIAN/CRM), ejecutar `scripts/active_labeling.py`
    para etiquetar 500 pares ambiguos y recalibrar.
    """
    profile = MatchingProfile(
        name="default_colombia_balanced",
        variables=[
            VariableSpec(
                "NIT",
                ExactWithDV(),
                weight=0.35,
                concordance_threshold=0.99,
                vetoes_mismatch=True,
                veto_threshold=-0.5,
            ),
            VariableSpec(
                "RAZON_SOCIAL",
                JaroWinklerSigned(prefix_weight=0.1),
                weight=0.30,
                concordance_threshold=0.78,
                vetoes_mismatch=False,
            ),
            VariableSpec(
                "TELEFONO",
                PhoneLastDigits(n=7),
                weight=0.10,
                concordance_threshold=0.99,
                vetoes_mismatch=False,
            ),
            VariableSpec(
                "EMAIL",
                EmailDomainLocal(domain_weight=0.7),
                weight=0.08,
                concordance_threshold=0.60,
                vetoes_mismatch=False,
            ),
            VariableSpec(
                "DIRECCION",
                AddressTokenSet(),
                weight=0.10,
                concordance_threshold=0.65,
                vetoes_mismatch=False,
            ),
            VariableSpec(
                "CIUDAD",
                CityNormalizedEqual(),
                weight=0.07,
                concordance_threshold=0.99,
                vetoes_mismatch=False,
            ),
        ],
        min_concordances_with_nit=2,
        min_concordances_without_nit=1,
        score_threshold=0.50,
        require_city_match=False,
    )
    profile.calibration_source = "synthetic_optimized"
    profile.calibration_notes = (
        "BALANCEADO: maximiza F1 global. Calibrado por barrido E2E "
        "sobre GT sintético. Mejor F1: 0.908 (vs baseline 0.873). "
        "Mejor F1 SIN_NIT: 0.732 (vs 0.629). NO calibrado contra datos reales."
    )
    return profile


def default_colombia_profile_conservative() -> MatchingProfile:
    """Profile CONSERVADOR (maximiza Precision) para casos de alto riesgo.

    Apto para regulación, KYC, anti-fraude — donde un falso positivo
    (fusionar dos empresas distintas) es muy costoso.

    Parámetros:
        - min_concordances_without_nit=2 (exige 2 variables concordantes)
        - score_threshold=0.40

    Métricas E2E (sobre GT sintético):
        - F1 global: 0.876 (apenas mejor que baseline 0.873)
        - **Precision SIN_NIT: 0.964** (vs baseline 0.559) ← lo destacable
        - Recall SIN_NIT: 0.290 (cae mucho)

    Recomendado SOLO cuando el costo de un FP supere claramente el de
    perder coincidencias verdaderas.
    """
    profile = default_colombia_profile()
    profile.name = "default_colombia_conservative"
    profile.min_concordances_without_nit = 2
    profile.score_threshold = 0.40
    profile.calibration_source = "synthetic_conservative"
    profile.calibration_notes = (
        "CONSERVADOR: maximiza Precision (>0.99 en SIN_NIT) a costa de Recall. "
        "Usar en KYC, anti-fraude, regulación. NO calibrado con datos reales."
    )
    return profile


def default_colombia_profile_recall() -> MatchingProfile:
    """Profile AGRESIVO (maximiza Recall) para descubrimiento exploratorio.

    Apto para enriquecimiento de datos, análisis de mercado, cuando es
    aceptable revisar manualmente algunos falsos positivos.

    Parámetros:
        - min_concordances_without_nit=1
        - score_threshold=0.40

    Métricas E2E (sobre GT sintético):
        - F1 global: 0.897
        - Recall SIN_NIT: 0.714 (mejor recall que el balanced)
        - Precision SIN_NIT: 0.684 (menor que el balanced 0.790)

    Recomendado cuando perder coincidencias es peor que tener falsos
    positivos (con revisión humana posterior).
    """
    profile = default_colombia_profile()
    profile.name = "default_colombia_recall"
    profile.min_concordances_without_nit = 1
    profile.score_threshold = 0.40
    profile.calibration_source = "synthetic_recall"
    profile.calibration_notes = (
        "AGRESIVO: maximiza Recall (0.714 SIN_NIT) con Precision moderada "
        "(0.684 SIN_NIT). Usar para enriquecimiento con revisión humana."
    )
    return profile


def default_international_profile() -> MatchingProfile:
    """Profile genérico internacional (sin NIT, sin abreviaturas locales).

    Apropiado para conciliación internacional donde el ID fiscal NO está
    disponible o no es comparable entre jurisdicciones. Apoya la decisión
    casi exclusivamente en NAME + EMAIL + CITY.

    Más conservador que el profile Colombia: exige 3+ concordancias.
    """
    profile = MatchingProfile(
        name="default_international",
        variables=[
            VariableSpec(
                "NAME",
                JaroWinklerSigned(prefix_weight=0.1),
                weight=0.50,
                concordance_threshold=0.85,
                vetoes_mismatch=False,
                alias="RAZON_SOCIAL",
            ),
            VariableSpec(
                "EMAIL",
                EmailDomainLocal(domain_weight=0.75),
                weight=0.25,
                concordance_threshold=0.70,
                vetoes_mismatch=False,
            ),
            VariableSpec(
                "CITY",
                CityNormalizedEqual(),
                weight=0.15,
                concordance_threshold=0.99,
                vetoes_mismatch=False,
                alias="CIUDAD",
            ),
            VariableSpec(
                "COUNTRY",
                CategoricalSigned(),
                weight=0.10,
                concordance_threshold=0.99,
                vetoes_mismatch=True,
                veto_threshold=-0.5,
                alias="PAIS",
            ),
        ],
        min_concordances_with_nit=2,
        min_concordances_without_nit=3,
        score_threshold=0.55,
    )
    profile.calibration_source = "heuristic_international"
    profile.calibration_notes = (
        "Heurística sin calibración. Apto para conciliación internacional "
        "donde no hay ID fiscal compartido. Recalibrar con EM en producción."
    )
    return profile
