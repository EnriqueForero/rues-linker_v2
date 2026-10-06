"""record_linkage.pipeline.errores — Excepciones específicas con mensaje accionable (F1).

Todo error que llega al usuario dice tres cosas, en este orden: **qué pasó**,
**por qué importa** y **qué hacer**. Un ``RuntimeError("falló")`` después de
cuarenta minutos de cómputo no es un mensaje: es una adivinanza. Las
excepciones de este módulo son específicas para que el llamador pueda
capturarlas por tipo y, a la vez, llevan el texto completo para quien lo lea
en un registro.

Uso::

    raise ContratoSalidaError(["ID_REGISTRO no es único (3 repetidos)"])

    raise ErrorRuesLinker(
        mensaje_accionable(
            "no existe L3_scoring/scored.db",
            "sin pares puntuados SCORE_PAR queda nulo",
            "corra linkage() con work_dir persistente",
        )
    )
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "ContratoSalidaError",
    "ErrorRuesLinker",
    "mensaje_accionable",
]


def mensaje_accionable(que_paso: str, por_que_importa: str, que_hacer: str) -> str:
    """Compone el formato único de los mensajes de error de la librería."""
    return f"Qué pasó: {que_paso} Por qué importa: {por_que_importa} Qué hacer: {que_hacer}"


class ErrorRuesLinker(Exception):
    """Base de las excepciones propias de rues-linker."""


class ContratoSalidaError(ErrorRuesLinker):
    """El resultado no cumple el contrato de salida (``contrato.VERSION_CONTRATO``).

    Attributes:
        fallos: cada incumplimiento, uno por línea, tal como lo produjo
            ``ResultadoLinkage.validar()``.
    """

    def __init__(self, fallos: Sequence[str]) -> None:
        self.fallos: list[str] = list(fallos)
        detalle = "\n".join(f"  - {f}" for f in self.fallos)
        super().__init__(
            mensaje_accionable(
                f"el resultado incumple el contrato de salida en {len(self.fallos)} punto(s):\n"
                f"{detalle}\n",
                "lo que se entrega aguas abajo (parquet, Excel, crosswalk) dejaría de ser "
                "comparable entre corridas y entre flujos.",
                "si el resultado viene de linkage()/dedupe()/link() es un defecto del motor o "
                "de salida.completar: repórtelo con el manifiesto; si lo construyó a mano, "
                "corrija las columnas que se listan.",
            )
        )
