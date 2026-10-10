"""F2.13 — ``silenciar_pipeline`` calla el INFO del motor y restaura el estado global.

Qué se protege
--------------
``evaluation.banco.silenciar_pipeline`` lo usan el banco (``correr_banco``,
``silencioso=True`` por defecto) y ``scripts/conformidad.py``. Desde F2.13
calla con ``logging.disable(INFO)``, que es **estado global del proceso**: si
la restauración se rompiera, todo lo que corra después (pytest incluido)
perdería el INFO sin que nada lo viera. Aquí se exige:

1. Dentro del contexto, un registrador construido al vuelo como los
   ``CustomLogger`` del motor (INFO, ``propagate=False``) deja pasar solo
   WARNING y superiores.
2. Al salir, ``logging.root.manager.disable`` vuelve **exactamente** al valor
   previo (no a NOTSET a ciegas) y el INFO vuelve a llegar.
3. La restauración ocurre aunque el cuerpo del ``with`` levante una excepción.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-10  ·  Version: 0.22.4
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from record_linkage.evaluation.banco import silenciar_pipeline

NOMBRE_REGISTRADOR = "record_linkage.prueba_f213"


class _Acumulador(logging.Handler):
    """Guarda el nivel de cada registro que le llega."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.niveles: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.niveles.append(record.levelname)


@pytest.fixture
def sin_rastro() -> Iterator[None]:
    """Deja ``logging.disable`` y el registrador de prueba como estaban."""
    tope_previo = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(tope_previo)
        registrador = logging.getLogger(NOMBRE_REGISTRADOR)
        registrador.handlers.clear()
        registrador.setLevel(logging.NOTSET)
        registrador.propagate = True


def _registrador_como_el_motor() -> tuple[logging.Logger, _Acumulador]:
    """Imita un ``CustomLogger``: se crea al vuelo, INFO y sin propagación."""
    registrador = logging.getLogger(NOMBRE_REGISTRADOR)
    registrador.handlers.clear()
    registrador.setLevel(logging.INFO)
    registrador.propagate = False
    acumulador = _Acumulador()
    registrador.addHandler(acumulador)
    return registrador, acumulador


@pytest.mark.usefixtures("sin_rastro")
def test_dentro_solo_pasa_el_warning_y_al_salir_vuelve_el_info() -> None:
    logging.disable(logging.NOTSET)
    with silenciar_pipeline():
        registrador, acumulador = _registrador_como_el_motor()
        registrador.info("ruido del motor")
        registrador.warning("esto sí importa")
        assert acumulador.niveles == ["WARNING"]

    assert logging.root.manager.disable == logging.NOTSET
    registrador.info("ya se oye otra vez")
    assert acumulador.niveles == ["WARNING", "INFO"]


@pytest.mark.usefixtures("sin_rastro")
@pytest.mark.parametrize("tope_previo", [logging.NOTSET, logging.DEBUG, logging.CRITICAL])
def test_restaura_exactamente_el_tope_previo(tope_previo: int) -> None:
    """No vuelve a NOTSET a ciegas: devuelve el valor que había (p. ej. DEBUG). Y un
    contexto cuyo contrato es «callar» no destapa nada: con un tope previo mayor que
    INFO (CRITICAL) lo conserva (pulido F2, hallazgo 16 de F2.13)."""
    logging.disable(tope_previo)
    with silenciar_pipeline():
        assert logging.root.manager.disable == max(tope_previo, logging.INFO)
    assert logging.root.manager.disable == tope_previo


@pytest.mark.usefixtures("sin_rastro")
def test_restaura_aunque_el_cuerpo_falle() -> None:
    logging.disable(logging.NOTSET)
    nivel_orquestador = logging.getLogger("Orchestrator").level
    with pytest.raises(RuntimeError, match="falla simulada"), silenciar_pipeline():
        raise RuntimeError("falla simulada")
    assert logging.root.manager.disable == logging.NOTSET
    assert logging.getLogger("Orchestrator").level == nivel_orquestador
