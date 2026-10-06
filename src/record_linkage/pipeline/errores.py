"""Excepciones específicas del pipeline con mensaje accionable.

Toda excepción que levanta el pipeline hacia el usuario sigue el formato de
la regla 5 de ``CLAUDE.md``: qué pasó, por qué importa, qué hacer. La función
:func:`mensaje_accionable` arma ese texto para que ningún módulo invente su
propio formato.

Este módulo es la base común de la fase F1 (contrato de salida): cada tarea
añade aquí sus excepciones concretas en vez de levantar ``RuntimeError`` o
``ValueError`` genéricos desde la librería. F1.1, F1.2 y F1.4 definen aquí
las suyas (``GoldenInvalidoError``, ``ConsolidacionNitError``,
``EstrategiaFallo``, ``ArtefactoObligatorioError``); si llegan por otra rama,
se unifican en la integración bajo este mismo archivo.
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


class ColumnasArrastreError(ErrorPipeline):
    """Las columnas de arrastre no se pudieron re-adjuntar a la correlativa.

    Las columnas que no participan en la decisión (TELEFONO, EMAIL,
    DEPARTAMENTO…) salen del motor antes del cruce y vuelven al final por
    posición (``ORIGINAL_INDEX = offset de la fuente + fila original``). Hasta
    F1.8, si esa alineación no cuadraba, ``flujo.cruce`` escribía un WARNING y
    entregaba la correlativa SIN las columnas del usuario, mientras el
    manifiesto declaraba la unión planificada como si hubiera ocurrido. Ahora
    la corrida falla aquí: un entregable al que le faltan columnas que el
    usuario pidió no es un entregable.

    Attributes:
        fuente: fuente cuya alineación falló, o ``None`` si falló el total.
        esperadas: filas que debía tener la parte (o la unión).
        observadas: filas que realmente tenía.
        que_hacer: remedio con el que se construyó el mensaje. Por defecto es
            el de la alineación de parquets derramados
            (:attr:`QUE_HACER_POR_DEFECTO`); los caminos donde ese remedio no
            aplica (DuckDB, ``separar_columnas_extra=False``) pasan el suyo.
    """

    #: Remedio para el camino pandas con separación: la alineación posicional
    #: de los parquets derramados en ``dir_trabajo/columnas_extra`` no cuadró.
    QUE_HACER_POR_DEFECTO = (
        "no use este resultado; borre el directorio de trabajo "
        "(`dir_trabajo/columnas_extra`) y vuelva a ejecutar; si se repite, "
        "reporte el caso con el manifiesto y el registro de la corrida, o "
        "desactive la separación con `ConfigCruce(separar_columnas_extra=False)` "
        "para que las columnas viajen por el motor"
    )

    def __init__(
        self,
        que_paso: str,
        *,
        fuente: str | None = None,
        esperadas: int | None = None,
        observadas: int | None = None,
        que_hacer: str | None = None,
    ):
        self.fuente = fuente
        self.esperadas = esperadas
        self.observadas = observadas
        self.que_hacer = self.QUE_HACER_POR_DEFECTO if que_hacer is None else que_hacer
        super().__init__(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "la correlativa saldría SIN las columnas de arrastre que usted pidió "
                    "(teléfono, correo, departamento…) y el manifiesto no podría "
                    "declarar qué se adjuntó; una entrega incompleta se publicaría como "
                    "si fuera completa"
                ),
                que_hacer=self.que_hacer,
            )
        )
