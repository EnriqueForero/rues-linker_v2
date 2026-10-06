"""pipeline.componentes — fábrica de los componentes de preparación (L1).

Por qué existe (F2.9): el ``Orchestrator`` construía una instancia de
``RecordLinkagePipeline`` solo para sacarle ``DataHandler``,
``TextProcessor(cleaning_mode)`` y ``NitProcessor``; nunca la ejecutaba. Esa
dependencia era lo único que impedía deprecar el pipeline heredado. Aquí
vive la fábrica, y la regla de qué ``cleaning_mode`` se aplica se escribe
una sola vez: la usan el ``Orchestrator`` y el propio ``RecordLinkagePipeline``
mientras ``dedupe()`` siga pasando por él.

La fábrica no muta la configuración que recibe (el pipeline heredado sí
escribía ``config["profile"]`` al construirse).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..processing.nit import NitProcessor
from ..processing.text import TextProcessor
from ..reporting.data_handler import DataHandler

MODO_LIMPIEZA_POR_DEFECTO = "BALANCEADO"


def modo_limpieza(config: Mapping[str, Any], perfil: str | None) -> str:
    """Modo de limpieza de nombres que corresponde a ``perfil`` en ``config``.

    Regla (la de ``RecordLinkagePipeline.__init__``, fix «BUG 1, Fase 3»):
    manda el ``cleaning_mode`` del perfil; si el perfil no lo declara (o no
    existe), el ``cleaning_mode`` del nivel superior de la configuración; y
    si tampoco está, ``BALANCEADO``.
    """
    perfiles = config.get("profiles") or {}
    del_perfil = (perfiles.get(perfil) or {}).get("cleaning_mode") if perfil else None
    if del_perfil:
        return str(del_perfil)
    return str(config.get("cleaning_mode") or MODO_LIMPIEZA_POR_DEFECTO)


@dataclass(frozen=True)
class ComponentesPreparacion:
    """Los tres componentes con los que L1 carga, consolida y limpia las fuentes.

    Attributes:
        data_handler: carga y consolida las fuentes (``load_sources``,
            ``consolidate_sources``).
        text_processor: limpieza de razones sociales en ``modo_limpieza``
            (``process_series``, ``derivar_nombre_bloqueo``).
        nit_processor: canonicalización de identificadores (``process_series``).
        modo_limpieza: el ``cleaning_mode`` resuelto con el que se construyó
            ``text_processor``; queda a la vista para el manifiesto y las pruebas.
    """

    data_handler: DataHandler
    text_processor: TextProcessor
    nit_processor: NitProcessor
    modo_limpieza: str


def fabricar_componentes(
    config: Mapping[str, Any], perfil: str | None = None
) -> ComponentesPreparacion:
    """Construye los componentes de preparación para ``config``.

    Args:
        config: configuración completa del pipeline (la misma que recibe el
            ``Orchestrator``). ``DataHandler`` y ``NitProcessor`` la reciben
            tal cual, por referencia.
        perfil: nombre del perfil cuyo ``cleaning_mode`` manda. Si es ``None``
            se usa ``config["profile"]`` (si existe).

    Returns:
        ``ComponentesPreparacion`` con los tres componentes recién creados.
    """
    nombre_perfil = perfil if perfil is not None else config.get("profile")
    modo = modo_limpieza(config, nombre_perfil)
    # DataHandler y NitProcessor esperan un dict mutable (los heredados lo
    # anotan así); la fábrica no lo copia para que vean lo mismo que L1.
    cfg: dict[str, Any] = config if isinstance(config, dict) else dict(config)
    return ComponentesPreparacion(
        data_handler=DataHandler(cfg),
        text_processor=TextProcessor(modo),
        nit_processor=NitProcessor(cfg),
        modo_limpieza=modo,
    )
