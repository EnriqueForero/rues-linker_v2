"""Generación de pares acotada a la política de fuentes (v0.17.3).

Contexto: hasta 0.17.2 cada bucket materializaba sus ``n(n-1)/2`` pares y
DESPUÉS los filtraba. En RUES × Exportaciones el 99,56 % de los registros
pertenece a una fuente confiable (sin deduplicación interna), así que un
bucket lleno enumeraba 124.750 pares para conservar ~997. Medido sobre el
universo real, la fase de candidatos costaba 36 min. Estas pruebas fijan que
la generación nueva produce EXACTAMENTE los mismos pares.
"""

from __future__ import annotations

import numpy as np
import pytest

from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine
from record_linkage.engine.lsh.trusted import TrustedSourceLSHEngine

FUENTES = np.array(["CRM", "EXPORTACIONES", "RUES"])


def _pares_de_referencia(ids, codigos_bucket, permite) -> set[tuple[int, int]]:
    """Implementación anterior: enumerar todo y filtrar. Es el oráculo."""
    salida = set()
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if permite(FUENTES[codigos_bucket[i]], FUENTES[codigos_bucket[j]]):
                salida.add((min(ids[i], ids[j]), max(ids[i], ids[j])))
    return salida


def _motor_y_regla(clase: str):
    if clase == "trusted":
        motor = TrustedSourceLSHEngine(profile={}, config={}, trusted_sources={"RUES"})
        return motor, lambda a, b: not (a == b and a == "RUES")
    motor = DiskBasedLSHEngine(profile={}, config={})
    return motor, lambda a, b: a != b


@pytest.mark.parametrize("clase", ["base", "trusted"])
@pytest.mark.parametrize("semilla", [0, 1, 2, 3, 4])
def test_paridad_exacta_con_la_generacion_anterior(clase: str, semilla: int) -> None:
    motor, regla = _motor_y_regla(clase)
    politica = motor._matriz_politica_fuentes(FUENTES, cross_source_only=True)
    rng = np.random.default_rng(semilla)
    codigos_globales = np.zeros(5_000, dtype=np.int16)
    for _ in range(60):
        n = int(rng.integers(2, 45))
        ids = rng.choice(5_000, size=n, replace=False)
        # Proporción realista: casi todo de la fuente confiable.
        codigos_bucket = rng.choice(3, size=n, p=[0.03, 0.05, 0.92]).astype(np.int16)
        codigos_globales[ids] = codigos_bucket
        obtenido = set(motor._generate_bucket_pairs(ids.tolist(), codigos_globales, politica))
        esperado = _pares_de_referencia(ids.tolist(), codigos_bucket, regla)
        assert obtenido == esperado


def test_la_fuente_confiable_no_se_deduplica_internamente() -> None:
    motor = TrustedSourceLSHEngine(profile={}, config={}, trusted_sources={"RUES"})
    politica = motor._matriz_politica_fuentes(FUENTES, cross_source_only=True)
    codigos = np.array([2, 2, 2], dtype=np.int16)  # las tres de RUES
    assert motor._generate_bucket_pairs([0, 1, 2], codigos, politica) == []


def test_una_fuente_no_confiable_si_se_deduplica() -> None:
    motor = TrustedSourceLSHEngine(profile={}, config={}, trusted_sources={"RUES"})
    politica = motor._matriz_politica_fuentes(FUENTES, cross_source_only=True)
    codigos = np.array([1, 1, 1], dtype=np.int16)  # EXPORTACIONES
    assert set(motor._generate_bucket_pairs([0, 1, 2], codigos, politica)) == {
        (0, 1),
        (0, 2),
        (1, 2),
    }


def test_los_cruces_entre_fuentes_siempre_pasan() -> None:
    motor = TrustedSourceLSHEngine(profile={}, config={}, trusted_sources={"RUES"})
    politica = motor._matriz_politica_fuentes(FUENTES, cross_source_only=True)
    codigos = np.array([2, 1], dtype=np.int16)  # RUES + EXPORTACIONES
    assert set(motor._generate_bucket_pairs([0, 1], codigos, politica)) == {(0, 1)}


def test_sin_politica_devuelve_todos_los_pares() -> None:
    motor = DiskBasedLSHEngine(profile={}, config={})
    assert set(motor._generate_bucket_pairs([5, 3, 9])) == {(3, 5), (3, 9), (5, 9)}


def test_no_materializa_los_pares_vetados() -> None:
    """El objetivo medible: el costo debe seguir a los pares EMITIDOS.

    Con un bucket lleno de la fuente confiable más dos ajenos, la versión
    anterior construía arreglos de 124.750 elementos; ésta debe emitir ~997
    pares sin pasar por ese pico.
    """
    motor = TrustedSourceLSHEngine(profile={}, config={}, trusted_sources={"RUES"})
    politica = motor._matriz_politica_fuentes(FUENTES, cross_source_only=True)
    codigos = np.full(500, 2, dtype=np.int16)
    codigos[:2] = 1
    pares = motor._generate_bucket_pairs(list(range(500)), codigos, politica)
    assert len(pares) == 2 * 498 + 1
    assert len(pares) < 1_000, "no debe acercarse a los 124.750 del bucket completo"
