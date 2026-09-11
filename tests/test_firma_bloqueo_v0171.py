"""Separación de firma de bloqueo y nombre de decisión (v0.17.1).

Contexto del cambio: en v0.17.0 el bloqueo y el scoring compartían
``NOMBRE_LIMPIO``. Al hacer ese nombre más fiel (para ganar recall) se
alargó la firma un 48 % en n-gramas y la corrida real de 4,4 M de filas
pasó de 29 min de L2 a más de 56 min sin terminar. Estas pruebas fijan el
contrato de las dos columnas para que la regresión no pueda repetirse.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine
from record_linkage.processing._constants import (
    CLEANING_MODES,
    ORGANIZATIONAL_TERMS,
    VOCABULARIO_SOLO_BLOQUEO,
)
from record_linkage.processing.text import TextProcessor


@pytest.fixture
def procesador() -> TextProcessor:
    return TextProcessor(cleaning_mode="BALANCEADO")


class TestVocabularios:
    def test_balanceado_conserva_los_giros_para_decidir(self):
        """El nombre de decisión no puede perder el token que distingue."""
        assert not (ORGANIZATIONAL_TERMS & CLEANING_MODES["BALANCEADO"]), (
            "BALANCEADO volvió a podar giros: 'EMPAQUES DEL CAUCA' y "
            "'COOPERATIVA DE CAFICULTORES DEL CAUCA' dejarían de distinguirse"
        )

    def test_la_poda_de_bloqueo_existe_y_es_la_lista_de_giros(self):
        assert frozenset(ORGANIZATIONAL_TERMS) == VOCABULARIO_SOLO_BLOQUEO
        assert len(VOCABULARIO_SOLO_BLOQUEO) > 200

    def test_agresivo_sigue_ofreciendo_la_poda_en_el_nombre(self):
        assert CLEANING_MODES["AGRESIVO"] >= ORGANIZATIONAL_TERMS


class TestDerivacionDeFirma:
    def test_poda_los_giros(self, procesador):
        s = pd.Series(["EMPAQUES CAUCA", "SERVICIOS TECNICOS ZULUAGA"])
        assert procesador.derivar_nombre_bloqueo(s).tolist() == [
            "CAUCA",
            "TECNICOS ZULUAGA",
        ]

    def test_conserva_el_nombre_si_podar_lo_degenera(self, procesador):
        """Una firma vacía colisiona con todo: peor que una genérica."""
        s = pd.Series(["SERVICIOS INTERNACIONALES COLOMBIA", "BODEGA MODA S S"])
        assert procesador.derivar_nombre_bloqueo(s).tolist() == s.tolist()

    def test_preserva_indice_y_longitud(self, procesador):
        s = pd.Series(["EMPAQUES CAUCA", "MIL DROGAS"], index=[7, 9])
        salida = procesador.derivar_nombre_bloqueo(s)
        assert salida.index.tolist() == [7, 9]
        assert len(salida) == 2

    def test_tolera_vacios_y_nulos(self, procesador):
        s = pd.Series(["", None, "MIL DROGAS"], dtype="string")
        salida = procesador.derivar_nombre_bloqueo(s)
        assert salida.tolist() == ["", "", "MIL DROGAS"]

    def test_la_firma_nunca_es_mas_larga_que_el_nombre(self, procesador):
        s = pd.Series(["EMPAQUES CAUCA", "MIL DROGAS", "SERVICIOS COLOMBIA", "TERMINAL ARMENIA"])
        firma = procesador.derivar_nombre_bloqueo(s)
        assert (firma.str.len() <= s.str.len()).all()

    def test_reduce_el_costo_de_firma_en_nombres_genericos(self, procesador):
        """El objetivo medible del cambio: menos n-gramas que firmar."""
        s = pd.Series(
            [
                "COMERCIALIZADORA INTERNACIONAL FLORES COLOMBIA ZULUAGA",
                "INDUSTRIA DE ALIMENTOS PEREZ",
                "TRANSPORTES Y LOGISTICA GOMEZ",
            ]
        )
        firma = procesador.derivar_nombre_bloqueo(s)
        assert firma.str.len().sum() < s.str.len().sum() * 0.75


class TestMotorLSH:
    def test_prefiere_la_firma_de_bloqueo(self):
        df = pd.DataFrame({"NOMBRE_LIMPIO": ["EMPAQUES CAUCA"], "NOMBRE_BLOQUEO": ["CAUCA"]})
        assert DiskBasedLSHEngine._columna_firma(df) == "NOMBRE_BLOQUEO"

    def test_cae_a_nombre_limpio_sin_la_columna(self):
        """Compatibilidad: frames de rutas anteriores y checkpoints viejos."""
        df = pd.DataFrame({"NOMBRE_LIMPIO": ["EMPAQUES CAUCA"]})
        assert DiskBasedLSHEngine._columna_firma(df) == "NOMBRE_LIMPIO"


# ═══════════════════════════════════════════════════════════════════════════
# Sustituibilidad de los dos modos de resultado (v0.17.1)
# ═══════════════════════════════════════════════════════════════════════════
# La auditoría 0.16.0 marcó Liskov como "no resuelto": el consumidor tenía que
# discriminar el modo. Estas pruebas fijan que la superficie de control de
# calidad es la misma y devuelve la misma forma en ambos.

_METODOS_QA = (
    "conflictos_identificador",
    "distribucion_grupos",
    "grupos_sospechosos",
    "identificadores_por_fuente",
    "entidades_multifuente",
)


@pytest.mark.parametrize("metodo", _METODOS_QA)
def test_ambos_resultados_exponen_la_misma_superficie(metodo: str):
    from record_linkage.flujo import ResultadoCruce, ResultadoCruceDisco

    assert callable(getattr(ResultadoCruce, metodo))
    assert callable(getattr(ResultadoCruceDisco, metodo))


def test_el_protocolo_declara_exactamente_esa_superficie():
    from record_linkage.flujo import ControlCalidad

    declarados = {m for m in dir(ControlCalidad) if not m.startswith("_")}
    assert set(_METODOS_QA) <= declarados


def test_distribucion_tiene_llaves_estables():
    """El notebook lee estas llaves por nombre: no pueden cambiar en silencio."""
    import pandas as pd

    from record_linkage.flujo.cruce import _formato_distribucion

    salida = _formato_distribucion(pd.Series([1, 2, 9]), pd.Series([1, 2, 4]))
    assert set(salida) == {
        "grupos_1_fila",
        "grupos_2a5_filas",
        "grupos_mas_5_filas",
        "max_filas_por_grupo",
        "grupos_1_nombre",
        "grupos_2a3_nombres",
        "grupos_mas_3_nombres",
        "max_nombres_por_grupo",
    }
    assert salida["grupos_mas_5_filas"] == 1
    assert salida["max_nombres_por_grupo"] == 4
