"""Resultado completo de una corrida de vinculación (tablas, resumen, controles y avisos)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO

import pandas as pd

from record_linkage.flujo.vinculacion.config import ConfigVinculacion

__all__ = ["Resultado"]


@dataclass
class Resultado:
    """Salida completa: tablas del Excel, resumen, validación, invariantes y avisos."""

    cfg: ConfigVinculacion
    tablas: dict
    resumen: pd.DataFrame
    por_pais: pd.DataFrame
    validacion: pd.DataFrame
    invariantes: pd.DataFrame
    calidad: pd.DataFrame
    avisos: list
    parametros: pd.DataFrame
    segundos: float
    internos: dict = field(default_factory=dict)

    @property
    def todo_ok(self) -> bool:
        """True si ninguna invariante CRÍTICA falla (las advertencias no bloquean)."""
        return (
            bool((self.invariantes["ESTADO"] != "FALLA").all()) if len(self.invariantes) else True
        )

    def texto(self) -> str:
        """Resumen legible: indicadores, validación contra la referencia, invariantes y primeros avisos."""
        partes = [self.resumen[["SECCION", "INDICADOR", "VALOR"]].to_string(index=False)]
        if len(self.validacion):
            partes.append("\nValidación del motor automático contra la referencia:")
            partes.append(self.validacion.to_string(index=False))
        malos = self.invariantes[~self.invariantes["CUMPLE"]]
        partes.append(
            f"\nInvariantes: {int(self.invariantes['CUMPLE'].sum())}/{len(self.invariantes)} cumplen"
        )
        if len(malos):
            partes.append(malos.to_string(index=False))
        partes.extend(f"  ⚠️  {aviso}" for aviso in self.avisos[:20])
        return "\n".join(partes)

    def imprimir(self, salida: TextIO | None = None) -> None:
        """Escribe ``texto()`` en ``salida`` (por defecto la salida estándar)."""
        (salida or sys.stdout).write(self.texto() + "\n")
