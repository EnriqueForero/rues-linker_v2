"""Flujo de deduplicación sin identificador, extremo a extremo (v0.22.1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
from record_linkage.flujo.importadores import preparar, registrar_locale
from record_linkage.matching.normalizadores import LOCALES


def _base() -> pd.DataFrame:
    """Base pequeña con los patrones reales: grafías, mojibake, país sucio."""
    filas = [
        # (razón social, país, fob) — la misma empresa en 4 grafías
        ("ACME TRADING LLC", "ESTADOS UNIDOS DE AMÉRICA", 100.0),
        ("ACME TRADING L.L.C.", "Estados Unidos", 50.0),
        ("ACME TRADING", "ESTADOS UNIDOS", 25.0),
        ("¿ACME TRADING¿ LLC", "estados unidos de america", 400.0),
        # Empresa distinta, mismo país
        ("BETA LOGISTICS INC", "ESTADOS UNIDOS", 70.0),
        # Misma grafía, otro país: NO puede fusionarse con las de arriba
        ("ACME TRADING LLC", "ALEMANIA", 30.0),
        # Destinatario reservado
        ("0", "PANAMÁ", 9_000.0),
        ("0", "Panama", 8_000.0),
        # Zona franca: no es un país
        ("GAMMA SAS", "ZONA FRANCA PERMANENTE BOGOTA", 10.0),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])


@pytest.fixture(scope="module")
def resultado():
    cfg = ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
    )
    return deduplicar_importadores(_base(), cfg)


def test_todas_las_invariantes_pasan(resultado):
    assert resultado.todo_ok, resultado.invariantes.to_string(index=False)


def test_una_fila_de_salida_por_fila_de_entrada(resultado):
    assert len(resultado.correlativa) == len(_base())


def test_grafias_de_la_misma_empresa_quedan_juntas(resultado):
    acme_usa = resultado.correlativa[
        (resultado.correlativa.RAZON_SOCIAL.str.contains("ACME"))
        & (resultado.correlativa.PAIS_ISO3 == "USA")
    ]
    assert acme_usa["ID_IMPORTADOR"].nunique() == 1
    assert len(acme_usa) == 4


def test_el_pais_es_bloqueo_duro(resultado):
    """La misma grafía en dos países son dos importadores. Es la definición."""
    acme = resultado.correlativa[resultado.correlativa.RAZON_SOCIAL == "ACME TRADING LLC"]
    assert acme["PAIS_ISO3"].nunique() == 2
    assert acme["ID_IMPORTADOR"].nunique() == 2


def test_id_global_reune_lo_que_el_pais_separo(resultado):
    acme = resultado.correlativa[resultado.correlativa.RAZON_SOCIAL == "ACME TRADING LLC"]
    assert acme["ID_EMPRESA_GLOBAL"].nunique() == 1


def test_empresas_distintas_no_se_fusionan(resultado):
    ids = resultado.correlativa.set_index("RAZON_SOCIAL")["ID_IMPORTADOR"]
    assert ids["BETA LOGISTICS INC"] != ids["ACME TRADING"]


def test_destinatario_reservado_no_se_fusiona_consigo_mismo(resultado):
    """Unir los "0" inventaría una empresa gigante que no existe."""
    ceros = resultado.correlativa[resultado.correlativa.RAZON_SOCIAL == "0"]
    assert len(ceros) == 2
    assert ceros["ID_IMPORTADOR"].nunique() == 2
    assert ceros["ID_IMPORTADOR"].str.startswith("SINNOMBRE").all()


def test_zona_franca_se_marca_y_no_se_toma_por_pais(resultado):
    gamma = resultado.correlativa[resultado.correlativa.RAZON_SOCIAL == "GAMMA SAS"]
    assert gamma["PAIS_METODO"].iloc[0] == "zona_franca"
    assert gamma["PAIS_ISO3"].iloc[0] == "ZZF"


def test_el_nombre_final_no_hereda_mojibake(resultado):
    """La grafía con mayor FOB está corrupta; no puede ser la etiqueta."""
    acme_usa = resultado.correlativa[
        (resultado.correlativa.RAZON_SOCIAL.str.contains("ACME"))
        & (resultado.correlativa.PAIS_ISO3 == "USA")
    ]
    final = acme_usa["RAZON_SOCIAL_FINAL"].iloc[0]
    assert "¿" not in final


def test_el_valor_economico_se_conserva(resultado):
    assert float(resultado.correlativa["FOB"].sum()) == pytest.approx(
        float(resultado.golden["FOB"].sum())
    )
    assert float(resultado.golden["FOB"].sum()) == pytest.approx(_base()["FOB"].sum())


def test_toda_asignacion_cumple_el_umbral(resultado):
    cfg = ConfigImportadores()
    assert (resultado.correlativa["SIM_AL_FINAL"] >= cfg.umbral_nombre - 1e-9).all()


def test_tablas_entregables_presentes(resultado):
    tablas = resultado.tablas()
    for nombre in ("CORRELATIVA", "GOLDEN", "PAISES", "REVISION", "METRICAS"):
        assert nombre in tablas


def test_columna_faltante_falla_con_mensaje_accionable():
    cfg = ConfigImportadores(col_razon_social="NO_EXISTE", col_pais="PAIS", verboso=False)
    with pytest.raises(KeyError, match="Qué hacer"):
        preparar(_base(), cfg)


def test_config_valida_sus_parametros():
    with pytest.raises(ValueError, match="umbral_nombre"):
        ConfigImportadores(umbral_nombre=1.5)
    with pytest.raises(ValueError, match="regla_nombre_final"):
        ConfigImportadores(regla_nombre_final="medoide")
    with pytest.raises(ValueError, match="cols_metricas"):
        ConfigImportadores(col_peso_economico="FOB", cols_metricas=())


def test_umbral_de_score_equivale_al_umbral_de_nombre():
    """Si no coincidieran, el umbral de score sería un número decorativo."""
    cfg = ConfigImportadores(umbral_nombre=0.84, peso_nombre=2.0, peso_pais=1.0)
    # score de un par con país concordante (1.0) y nombre exactamente en el umbral
    score = (cfg.peso_nombre * cfg.sim_minima + cfg.peso_pais * 1.0) / (
        cfg.peso_nombre + cfg.peso_pais
    )
    assert score == pytest.approx(cfg.umbral_score)


def test_registrar_locale_es_idempotente():
    cfg = ConfigImportadores(locale="PRUEBA_LOCALE")
    registrar_locale(cfg)
    primera = LOCALES["PRUEBA_LOCALE"]["sufijos"]
    registrar_locale(cfg)
    assert LOCALES["PRUEBA_LOCALE"]["sufijos"] == primera
    del LOCALES["PRUEBA_LOCALE"]


def test_geografia_como_ruido_es_una_perilla_real():
    """Con geografía como ruido se unen sucursales; sin ella, no."""
    base = pd.DataFrame(
        {
            "RAZON_SOCIAL": ["ECOLAB", "ECOLAB CHILE"],
            "PAIS": ["CHILE", "CHILE"],
        }
    )
    con = deduplicar_importadores(
        base,
        ConfigImportadores(
            col_razon_social="RAZON_SOCIAL",
            col_pais="PAIS",
            geografia_es_ruido=True,
            verboso=False,
        ),
    )
    sin = deduplicar_importadores(
        base,
        ConfigImportadores(
            col_razon_social="RAZON_SOCIAL",
            col_pais="PAIS",
            geografia_es_ruido=False,
            verboso=False,
        ),
    )
    assert con.correlativa["ID_IMPORTADOR"].nunique() == 1
    assert sin.correlativa["ID_IMPORTADOR"].nunique() == 2


def test_sensibilidad_y_recall_devuelven_tabla_aunque_esten_vacios(resultado):
    assert isinstance(resultado.extra["SENSIBILIDAD"], pd.DataFrame)
    assert isinstance(resultado.extra["RECALL_BLOQUEO"], pd.DataFrame)


def test_es_determinista():
    cfg = ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
    )
    a = deduplicar_importadores(_base(), cfg).correlativa
    b = deduplicar_importadores(_base(), cfg).correlativa
    pd.testing.assert_frame_equal(a, b)
