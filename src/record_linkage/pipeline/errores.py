"""Excepciones específicas del pipeline con mensaje accionable.

Toda excepción que levanta el pipeline hacia el usuario sigue el formato de
la regla 5 de ``CLAUDE.md``: qué pasó, por qué importa, qué hacer. La función
:func:`mensaje_accionable` arma ese texto para que ningún módulo invente su
propio formato.

Este módulo es la base común de la fase F1 (contrato de salida): cada tarea
añade aquí sus excepciones concretas en vez de levantar ``RuntimeError`` o
``ValueError`` genéricos desde la librería.
"""

from __future__ import annotations


def mensaje_accionable(que_paso: str, por_que_importa: str, que_hacer: str) -> str:
    """Devuelve el texto de un error en el formato de la regla 5.

    Args:
        que_paso: hecho observado, con cifras o nombres concretos.
        por_que_importa: consecuencia para el entregable si se ignora.
        que_hacer: remedio concreto (comando, parámetro, archivo).
    """
    return f"Qué pasó: {que_paso}\nPor qué importa: {por_que_importa}\nQué hacer: {que_hacer}"


class ErrorPipeline(Exception):
    """Base de las excepciones del pipeline de ``record_linkage``."""


class GoldenInvalidoError(ErrorPipeline):
    """El golden no cumple su contrato: columnas ajenas, métricas nulas o grupos repetidos.

    Lo levanta :func:`record_linkage.golden.metricas.verificar_golden` antes de
    que el orquestador persista ``golden.parquet``.
    """
