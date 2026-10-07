"""F2.9 — la fábrica de componentes de preparación (``pipeline/componentes.py``).

El ``Orchestrator`` sacaba ``DataHandler``, ``TextProcessor(cleaning_mode)`` y
``NitProcessor`` de una instancia de ``RecordLinkagePipeline`` que no usaba
para nada más. La fábrica es ese único punto; ``RecordLinkagePipeline`` queda
deprecado (avisa al construirse) y ``deduplicate_unified``, que todavía lo
usa como motor, lo construye sin que el usuario de ``dedupe()`` reciba un
aviso que no puede atender.
"""

from __future__ import annotations

import copy
import warnings

import pandas as pd
import pytest

from record_linkage.deduplication.unified import (
    AjustesDeduplicacion,
    deduplicate_unified,
)
from record_linkage.evaluation.banco import huella_particion
from record_linkage.pipeline import orchestrator as modulo_orquestador
from record_linkage.pipeline.componentes import (
    ComponentesPreparacion,
    fabricar_componentes,
    modo_limpieza,
)
from record_linkage.pipeline.linkage_pipeline import RecordLinkagePipeline
from record_linkage.processing.nit import NitProcessor
from record_linkage.processing.text import TextProcessor
from record_linkage.reporting.data_handler import DataHandler


def _config(perfil: str = "p", **extra: object) -> dict:
    cfg: dict = {"profile": perfil, "profiles": {perfil: {}}}
    cfg.update(extra)
    return cfg


# ─────────────────────────────────────────────────────────────────────
# modo_limpieza: la regla del cleaning_mode, escrita una sola vez
# ─────────────────────────────────────────────────────────────────────


def test_modo_limpieza_el_perfil_manda_sobre_el_nivel_superior() -> None:
    cfg = _config(cleaning_mode="CONSERVADOR")
    cfg["profiles"]["p"]["cleaning_mode"] = "AGRESIVO"
    assert modo_limpieza(cfg, "p") == "AGRESIVO"


def test_modo_limpieza_cae_al_nivel_superior_y_luego_a_balanceado() -> None:
    assert modo_limpieza(_config(cleaning_mode="CONSERVADOR"), "p") == "CONSERVADOR"
    assert modo_limpieza(_config(), "p") == "BALANCEADO"
    assert modo_limpieza({}, None) == "BALANCEADO"
    # Un valor vacío o None cuenta como NO declarado (lo declara el docstring):
    # el pipeline heredado devolvía la clave tal cual y construía
    # TextProcessor(None); la fábrica cae al nivel siguiente.
    cfg = _config(cleaning_mode="CONSERVADOR")
    cfg["profiles"]["p"]["cleaning_mode"] = None
    assert modo_limpieza(cfg, "p") == "CONSERVADOR"
    cfg["profiles"]["p"]["cleaning_mode"] = ""
    cfg["cleaning_mode"] = None
    assert modo_limpieza(cfg, "p") == "BALANCEADO"
    assert "None" in (modo_limpieza.__doc__ or "")  # la conducta está declarada


def test_modo_limpieza_de_un_perfil_que_no_existe_usa_el_nivel_superior() -> None:
    assert modo_limpieza(_config(cleaning_mode="AGRESIVO"), "no_existe") == "AGRESIVO"


# ─────────────────────────────────────────────────────────────────────
# fabricar_componentes
# ─────────────────────────────────────────────────────────────────────


def test_fabricar_componentes_entrega_los_tres_componentes() -> None:
    cfg = _config()
    cfg["profiles"]["p"]["cleaning_mode"] = "AGRESIVO"
    comps = fabricar_componentes(cfg)
    assert isinstance(comps, ComponentesPreparacion)
    assert isinstance(comps.data_handler, DataHandler)
    assert isinstance(comps.text_processor, TextProcessor)
    assert isinstance(comps.nit_processor, NitProcessor)
    assert comps.modo_limpieza == "AGRESIVO"
    assert comps.text_processor.cleaning_mode == "AGRESIVO"
    # Los componentes ven la misma configuración que el llamador.
    assert comps.data_handler.config is cfg


def test_fabricar_componentes_no_muta_la_configuracion() -> None:
    cfg = {"profiles": {}}
    antes = dict(cfg)
    fabricar_componentes(cfg)
    assert cfg == antes
    assert "profile" not in cfg


def test_fabricar_componentes_con_perfil_explicito() -> None:
    cfg = _config()
    cfg["profiles"]["otro"] = {"cleaning_mode": "CONSERVADOR"}
    assert fabricar_componentes(cfg, perfil="otro").modo_limpieza == "CONSERVADOR"
    assert fabricar_componentes(cfg).modo_limpieza == "BALANCEADO"


def test_la_fabrica_y_el_pipeline_heredado_producen_el_mismo_modo() -> None:
    """Una regla una sola vez: el pipeline heredado también pasa por la fábrica."""
    cfg = _config(cleaning_mode="CONSERVADOR")
    cfg["profiles"]["p"]["cleaning_mode"] = "AGRESIVO"
    heredado = RecordLinkagePipeline(cfg, profile="p", _uso_interno=True)
    fabrica = fabricar_componentes(cfg, perfil="p")
    assert (
        heredado.text_processor.cleaning_mode == fabrica.text_processor.cleaning_mode == "AGRESIVO"
    )


# ─────────────────────────────────────────────────────────────────────
# Orchestrator: usa la fábrica, no el pipeline heredado
# ─────────────────────────────────────────────────────────────────────


def test_orchestrator_ya_no_importa_el_pipeline_heredado() -> None:
    assert not hasattr(modulo_orquestador, "RecordLinkagePipeline")
    assert not hasattr(modulo_orquestador.Orchestrator, "pipeline")
    assert modulo_orquestador.fabricar_componentes is fabricar_componentes


def test_orchestrator_fabrica_sus_componentes_perezosamente(tmp_path) -> None:
    fuentes = {"A": pd.DataFrame({"NIT": ["900"], "RAZON_SOCIAL": ["EMPRESA INVENTADA"]})}
    cfg = _config(perfil="p")
    cfg["profiles"]["p"]["cleaning_mode"] = "AGRESIVO"
    orq = modulo_orquestador.Orchestrator(cfg, fuentes, str(tmp_path))
    assert orq._componentes is None
    comps = orq.componentes
    assert isinstance(comps, ComponentesPreparacion)
    assert comps.text_processor.cleaning_mode == "AGRESIVO"
    assert orq.componentes is comps  # se crea una sola vez


# ─────────────────────────────────────────────────────────────────────
# RecordLinkagePipeline: deprecado, pero sin ruido para dedupe()
# ─────────────────────────────────────────────────────────────────────


def test_record_linkage_pipeline_avisa_al_construirse() -> None:
    with pytest.warns(DeprecationWarning, match="RecordLinkagePipeline"):
        RecordLinkagePipeline(_config())


def test_record_linkage_pipeline_no_avisa_en_uso_interno() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        RecordLinkagePipeline(_config(), _uso_interno=True)


def _df_chico() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "NIT": ["900100200", "900100200", "800300400", "800300400"],
            "RAZON_SOCIAL": [
                "FERRETERIA INVENTADA SAS",
                "FERRETERIA INVENTADA S.A.S.",
                "PANADERIA FICTICIA LTDA",
                "PANADERIA FICTICIA LIMITADA",
            ],
        }
    )


def test_deduplicate_unified_no_emite_el_aviso_de_deprecacion(tmp_path) -> None:
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        deduplicate_unified(_df_chico(), output_dir=str(tmp_path))
    avisos = [
        str(w.message)
        for w in capturados
        if issubclass(w.category, DeprecationWarning) and "RecordLinkagePipeline" in str(w.message)
    ]
    assert avisos == []


# ─────────────────────────────────────────────────────────────────────
# AjustesDeduplicacion: la perilla que les faltaba a los scripts
# ─────────────────────────────────────────────────────────────────────


def test_ajustes_motor_desconocido_falla_con_mensaje_accionable(tmp_path) -> None:
    with pytest.raises(ValueError, match="Qué hacer") as exc:
        deduplicate_unified(
            _df_chico(), output_dir=str(tmp_path), ajustes=AjustesDeduplicacion(motor="turbo")
        )
    assert "turbo" in str(exc.value)
    assert "disk_based" in str(exc.value)


def test_ajustes_perfil_llega_al_perfil_activo(tmp_path) -> None:
    """Con ``score_threshold`` imposible nada se une por nombre; sí por NIT.
    Con ``nit_identical_overrides_name_filter`` apagado y umbral imposible,
    no se une nada: la perilla del perfil se aplicó de verdad."""
    df = pd.DataFrame(
        {
            "NIT": ["", "", "", ""],
            "RAZON_SOCIAL": [
                "FERRETERIA INVENTADA SAS",
                "FERRETERIA INVENTADA S.A.S.",
                "PANADERIA FICTICIA LTDA",
                "PANADERIA FICTICIA LIMITADA",
            ],
        }
    )
    normal, _ = deduplicate_unified(df, output_dir=str(tmp_path / "a"))
    estricta, _ = deduplicate_unified(
        df,
        output_dir=str(tmp_path / "b"),
        ajustes=AjustesDeduplicacion(perfil={"score_threshold": 1.01}),
    )
    assert normal["ID_GRUPO"].nunique() == 2
    assert estricta["ID_GRUPO"].nunique() == 4


def test_ajustes_por_defecto_son_inertes(tmp_path) -> None:
    """``AjustesDeduplicacion()`` deja la misma partición que no pasar ajustes.

    La huella (``evaluation.banco.huella_particion``) compara las dos corridas.
    Medido: en 4 filas la huella no distingue ``default`` de ``disk_based``,
    así que la inercia de ``aplicar()`` se afirma además sobre la configuración
    misma — es lo que fallaría si escribiera ``linkage_engine_class`` con
    ``motor=None``.
    """
    sin_ajustes, _ = deduplicate_unified(_df_chico(), output_dir=str(tmp_path / "a"))
    con_ajustes, _ = deduplicate_unified(
        _df_chico(), output_dir=str(tmp_path / "b"), ajustes=AjustesDeduplicacion()
    )
    huella_sin = huella_particion(sin_ajustes["ORIGINAL_INDEX"], sin_ajustes["ID_GRUPO"])
    huella_con = huella_particion(con_ajustes["ORIGINAL_INDEX"], con_ajustes["ID_GRUPO"])
    assert huella_sin == huella_con

    config = {"profile": "p", "profiles": {"p": {"score_threshold": 0.5}}}
    antes = copy.deepcopy(config)
    AjustesDeduplicacion().aplicar(config)
    assert config == antes
