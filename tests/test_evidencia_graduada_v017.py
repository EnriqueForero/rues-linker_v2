"""Identificador como evidencia graduada (v0.17.0).

Cubre el rescate del veto L3 y las clases de equivalencia del cannot-link:
con tolerancia 0 la semántica v0.14.0 se preserva bit a bit; con d > 0 las
variantes de captura (distancia OSA ≤ d + nombre cohesivo) dejan de partir
grupos, y las entidades realmente distintas se siguen separando.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.cannot_link import aplicar_cannot_link_identificador
from record_linkage.engine.scorer import VectorizedScorer


def _scorer(**perfil_extra):
    perfil = {
        "score_threshold": 0.45,
        "min_name_similarity": 0.30,
        "max_nit_distance": 3,
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        "veto_nit_base_distinto": True,
        **perfil_extra,
    }
    return VectorizedScorer(profile=perfil, config={})


def _lote(nombres_a, nombres_b, nits_a, nits_b):
    data_0 = pd.DataFrame({"NIT_BASE": nits_a, "NIT_VALID": [True] * len(nits_a)})
    data_1 = pd.DataFrame({"NIT_BASE": nits_b, "NIT_VALID": [True] * len(nits_b)})
    sims = np.array([1.0 if a == b else 0.5 for a, b in zip(nombres_a, nombres_b, strict=True)])
    return data_0, data_1, sims


class TestVetoGraduado:
    def test_con_tolerancia_cero_el_veto_es_binario(self):
        scorer = _scorer()
        d0, d1, sims = _lote(["ACME SAS"], ["ACME SAS"], ["900123456"], ["900123457"])
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=sims)
        assert veto.tolist() == [True]
        assert scorer._rescates_veto_nit == 0

    def test_digitacion_con_nombre_identico_se_rescata(self):
        scorer = _scorer(tolerancia_digitacion_identificador=2)
        d0, d1, sims = _lote(["ACME SAS"], ["ACME SAS"], ["900123456"], ["900123457"])
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=sims)
        assert veto.tolist() == [False]
        assert scorer._rescates_veto_nit == 1

    def test_transposicion_cuenta_una_edicion(self):
        scorer = _scorer(tolerancia_digitacion_identificador=1)
        d0, d1, sims = _lote(["BETA LTDA"], ["BETA LTDA"], ["900123456"], ["900123546"])
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=sims)
        assert veto.tolist() == [False]

    def test_nombre_disimil_no_se_rescata(self):
        scorer = _scorer(tolerancia_digitacion_identificador=2)
        d0, d1, sims = _lote(["ACME SAS"], ["GLOBAL PARTS SA"], ["900123456"], ["900123457"])
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=sims)
        assert veto.tolist() == [True]

    def test_bases_lejanas_se_vetan_aunque_el_nombre_coincida(self):
        # Homónimos con NIT distinto: el caso institucional que el veto existe
        # para proteger. Distancia 5 >> tolerancia.
        scorer = _scorer(tolerancia_digitacion_identificador=2)
        d0, d1, sims = _lote(
            ["COMERCIALIZADORA XYZ"],
            ["COMERCIALIZADORA XYZ"],
            ["900111222"],
            ["835974466"],
        )
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=sims)
        assert veto.tolist() == [True]

    def test_sin_name_similarities_no_hay_rescate(self):
        scorer = _scorer(tolerancia_digitacion_identificador=2)
        d0, d1, _ = _lote(["A"], ["A"], ["900123456"], ["900123457"])
        veto = scorer._veto_nit_base_distinto(d0, d1, name_similarities=None)
        assert veto.tolist() == [True]

    def test_tolerancia_invalida_falla_temprano(self):
        with pytest.raises(ValueError, match="tolerancia_digitacion_identificador"):
            _scorer(tolerancia_digitacion_identificador=7)
        with pytest.raises(ValueError, match="similitud_nombre_rescate"):
            _scorer(similitud_nombre_rescate_identificador=0.0)


def _correlativa_conflicto(nombres=None):
    nombres = nombres or [
        "DISTRIBUIDORA ANDINA SAS",
        "DISTRIBUIDORA ANDINA S A S",
        "DISTRIBUIDORA ANDINA",
    ]
    return pd.DataFrame(
        {
            "ID_GRUPO": [7, 7, 7],
            "NIT_BASE": ["800111222", "800111223", "800111222"],
            "NIT_VALID": [True, True, True],
            "NOMBRE_LIMPIO": nombres,
        }
    )


class TestCannotLinkGraduado:
    def test_tolerancia_cero_parte_como_v0140(self):
        df = _correlativa_conflicto()
        salida, rep = aplicar_cannot_link_identificador(df)
        assert rep.grupos_en_conflicto == 1
        assert salida["ID_GRUPO"].nunique() == 2
        assert rep.identificadores_fusionados_por_tolerancia == 0

    def test_variante_de_captura_preserva_el_grupo(self):
        df = _correlativa_conflicto()
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        assert rep.grupos_en_conflicto == 0
        assert salida["ID_GRUPO"].nunique() == 1
        assert rep.identificadores_fusionados_por_tolerancia == 1
        assert not rep.hubo_cambios

    def test_nombres_disimiles_siguen_partiendo(self):
        df = _correlativa_conflicto(
            nombres=["DISTRIBUIDORA ANDINA SAS", "FERRETERIA EL MARTILLO", "OTRO"]
        )
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        assert rep.grupos_en_conflicto == 1
        assert salida["ID_GRUPO"].nunique() == 2

    def test_bases_lejanas_siguen_partiendo(self):
        df = _correlativa_conflicto()
        df.loc[1, "NIT_BASE"] = "913579246"
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        assert rep.grupos_en_conflicto == 1
        assert salida["ID_GRUPO"].nunique() == 2

    def test_puente_invalido_se_aisla_igual(self):
        df = pd.DataFrame(
            {
                "ID_GRUPO": [3, 3, 3],
                "NIT_BASE": ["800111222", "", "913579246"],
                "NIT_VALID": [True, False, True],
                "NOMBRE_LIMPIO": ["A ANDINA", "PUENTE", "B MARTILLO"],
            }
        )
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        assert rep.puentes_aislados == 1
        assert salida["ID_GRUPO"].nunique() == 3

    def test_transitividad_de_clases(self):
        # A~B (dist 1) y B~C (dist 1) pero A~C dist 2: una sola clase con d=1
        # vía transitividad del union-find.
        df = pd.DataFrame(
            {
                "ID_GRUPO": [5, 5, 5],
                "NIT_BASE": ["900000001", "900000011", "900000111"],
                "NIT_VALID": [True, True, True],
                "NOMBRE_LIMPIO": ["GAMMA GROUP SAS"] * 3,
            }
        )
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=1, similitud_nombre_rescate=0.9
        )
        assert rep.grupos_en_conflicto == 0
        assert salida["ID_GRUPO"].nunique() == 1

    def test_sin_columna_nombre_no_hay_rescate(self):
        df = _correlativa_conflicto().drop(columns=["NOMBRE_LIMPIO"])
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        assert rep.grupos_en_conflicto == 1
        assert salida["ID_GRUPO"].nunique() == 2

    def test_determinismo_entre_corridas(self):
        df = pd.concat([_correlativa_conflicto()] * 3, ignore_index=True)
        df["ID_GRUPO"] = [7, 7, 7, 9, 9, 9, 11, 11, 11]
        df.loc[4, "NIT_BASE"] = "555000111"  # grupo 9 con base lejana → parte
        a, _ = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        b, _ = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.85
        )
        pd.testing.assert_frame_equal(a, b)

    def test_tolerancia_invalida_falla(self):
        with pytest.raises(ValueError, match="tolerancia_digitacion"):
            aplicar_cannot_link_identificador(_correlativa_conflicto(), tolerancia_digitacion=9)


class TestGuardiaDeMarca:
    """Caso real RUES: prefijo genérico largo + marca distinta ≠ variante."""

    def test_prefijo_generico_con_marca_distinta_no_se_rescata(self):
        df = pd.DataFrame(
            {
                "ID_GRUPO": [1, 1],
                "NIT_BASE": ["900290777", "900295477"],
                "NIT_VALID": [True, True],
                "NOMBRE_LIMPIO": [
                    "DISTRIBUIDORA Y COMERCIALIZADORA ML LTDA",
                    "DISTRIBUIDORA Y COMERCIALIZADORA TITANS LIMITADA",
                ],
            }
        )
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.80
        )
        assert rep.grupos_en_conflicto == 1
        assert salida["ID_GRUPO"].nunique() == 2

    def test_misma_marca_si_se_rescata(self):
        df = pd.DataFrame(
            {
                "ID_GRUPO": [1, 1],
                "NIT_BASE": ["900290777", "900290778"],
                "NIT_VALID": [True, True],
                "NOMBRE_LIMPIO": [
                    "DISTRIBUIDORA Y COMERCIALIZADORA TITANS LTDA",
                    "DISTRIBUIDORA Y COMERCIALIZADORA TITANS LIMITADA",
                ],
            }
        )
        salida, rep = aplicar_cannot_link_identificador(
            df, tolerancia_digitacion=2, similitud_nombre_rescate=0.80
        )
        assert rep.grupos_en_conflicto == 0
        assert salida["ID_GRUPO"].nunique() == 1

    def test_scorer_tambien_aplica_la_guardia(self):
        scorer = _scorer(tolerancia_digitacion_identificador=2)
        data_0 = pd.DataFrame(
            {
                "NIT_BASE": ["900290777"],
                "NIT_VALID": [True],
                "NOMBRE_LIMPIO": ["DISTRIBUIDORA Y COMERCIALIZADORA ML LTDA"],
            }
        )
        data_1 = pd.DataFrame(
            {
                "NIT_BASE": ["900295477"],
                "NIT_VALID": [True],
                "NOMBRE_LIMPIO": ["DISTRIBUIDORA Y COMERCIALIZADORA TITANS LIMITADA"],
            }
        )
        sims = np.array([0.86])
        veto = scorer._veto_nit_base_distinto(data_0, data_1, name_similarities=sims)
        assert veto.tolist() == [True]


# ═══════════════════════════════════════════════════════════════════════════
# Observabilidad de memoria por fase (v0.17.0) — restituida desde 0.14.1
# ═══════════════════════════════════════════════════════════════════════════


class TestCronometroConRSS:
    """El pico de RSS por fase es lo que convierte un OOM en diagnóstico."""

    def test_registra_pico_por_fase_y_cierra_el_hilo(self):
        import threading

        from record_linkage.flujo.cruce import _Cronometro

        with _Cronometro() as crono:
            with crono.fase("carga"):
                lastre = [0] * 2_000_000  # fuerza un pico medible
                assert len(lastre) == 2_000_000
                del lastre
        assert "carga" in crono.fases
        assert crono.rss_mib.get("carga", 0) > 0
        assert not [h for h in threading.enumerate() if h.name == "rues-linker-rss"]

    def test_cerrar_es_idempotente(self):
        from record_linkage.flujo.cruce import _Cronometro

        crono = _Cronometro()
        crono.cerrar()
        crono.cerrar()  # no debe lanzar

    def test_tabla_incluye_columna_de_pico(self):
        from record_linkage.flujo.cruce import _tabla_tiempos

        salida = _tabla_tiempos(
            {
                "segundos_por_fase": {"cruce": 10.0, "exportes": 2.0},
                "segundos_total": 12.0,
                "pico_rss_mib_por_fase": {"cruce": 4096.0, "exportes": 1024.0},
            }
        )
        assert "MEMORIA POR FASE" in salida
        assert "4,096 MiB" in salida

    def test_tabla_degrada_sin_psutil(self):
        from record_linkage.flujo.cruce import _tabla_tiempos

        salida = _tabla_tiempos({"segundos_por_fase": {"cruce": 10.0}, "segundos_total": 10.0})
        assert "TIEMPO POR FASE" in salida
        assert "MiB" not in salida
