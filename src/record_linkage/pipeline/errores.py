"""Excepciones específicas del pipeline con mensaje accionable.

Toda excepción que levanta el pipeline hacia el usuario sigue el formato de
la regla 5 de ``CLAUDE.md``: qué pasó, por qué importa, qué hacer. La función
:func:`mensaje_accionable` arma ese texto para que ningún módulo invente su
propio formato.

Este módulo es la base común de la fase F1 (contrato de salida): cada tarea
añade aquí sus excepciones concretas en vez de levantar ``RuntimeError`` o
``ValueError`` genéricos desde la librería. F1.13 define aquí
``ColapsoExactoError`` y ``CruceSinFuenteError``; si otras tareas llegan por
otra rama con las suyas, se unifican en la integración bajo este mismo
archivo.
"""

from __future__ import annotations

from collections.abc import Sequence


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


class ColapsoExactoError(ErrorPipeline):
    """El colapso exacto de duplicados no pudo decidir qué filas son idénticas.

    ``linkage(collapse_exact_duplicates=True)`` compara filas completas con la
    tabla hash de pandas. Una celda con una lista o un dict no es hashable y
    pandas levanta ``TypeError``. Hasta F1.13 ese error se atrapaba y la
    fuente seguía «con todas las filas»: el usuario pedía colapsar, no se
    colapsaba nada y ninguna cifra lo decía. Ahora la corrida falla aquí y el
    mensaje nombra la fuente y las columnas que impiden el colapso.

    Attributes:
        fuente: nombre de la fuente afectada.
        columnas: columnas con valores no hashables, en orden de la fuente
            (vacía si pandas falló por otra razón; entonces ``causa`` lo dice).
        causa: excepción original de pandas, si la hubo.
    """

    def __init__(
        self, fuente: str, columnas: Sequence[str], *, causa: BaseException | None = None
    ) -> None:
        self.fuente = fuente
        self.columnas = list(columnas)
        self.causa = causa
        lista = ", ".join(repr(c) for c in self.columnas)
        if self.columnas:
            que_paso = (
                f"la fuente '{fuente}' tiene valores no hashables (listas o dicts) "
                f"en la(s) columna(s) {lista}; el colapso exacto de duplicados "
                f"no puede comparar esas filas"
            )
        else:
            detalle = f"{type(causa).__name__}: {causa}" if causa is not None else "sin causa"
            que_paso = (
                f"pandas no pudo comparar las filas de la fuente '{fuente}' para el "
                f"colapso exacto de duplicados ({detalle})"
            )
        super().__init__(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "seguir sin colapsar entregaría una corrida que no hizo lo que se "
                    "pidió y cuyas cifras (processed_rows, INPUT_ROW_COUNT) serían "
                    "indistinguibles de una corrida sin duplicados"
                ),
                que_hacer=(
                    f"convierta esas columnas a texto antes de llamar (p. ej. "
                    f"df[{lista}] = df[{lista}].astype(str), o '|'.join(...) para "
                    f"listas) o desactive el colapso con "
                    f"collapse_exact_duplicates=False"
                ),
            )
        )


class CruceSinFuenteError(ErrorPipeline):
    """La correlativa de ``link()`` no permite atribuir cada registro a su tabla.

    ``link(df_a, df_b)`` cuenta entidades presentes en AMBAS tablas a partir
    de la columna ``SRC``. Hasta F1.13, si ``SRC`` faltaba o no traía las dos
    etiquetas, las métricas de cruce se rellenaban con ``-1`` y la corrida
    terminaba «bien». Un centinela numérico en una métrica es una degradación
    silenciosa: ahora se levanta esta excepción.

    Attributes:
        faltantes: etiquetas de fuente que no aparecen en ``SRC`` (vacío si la
            columna misma falta).
        columnas: columnas que sí trae la correlativa.
    """

    def __init__(
        self, *, faltantes: Sequence[str], columnas: Sequence[str], nombre_a: str, nombre_b: str
    ) -> None:
        self.faltantes = list(faltantes)
        self.columnas = list(columnas)
        if "SRC" not in self.columnas:
            que_paso = (
                f"la correlativa devuelta por linkage() no trae la columna SRC "
                f"(columnas: {self.columnas})"
            )
        else:
            que_paso = (
                f"la columna SRC de la correlativa no contiene la(s) fuente(s) "
                f"{self.faltantes} (se cruzaron '{nombre_a}' y '{nombre_b}')"
            )
        super().__init__(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "sin saber de qué tabla viene cada registro no se pueden contar "
                    "los grupos cruzados ni los pares A↔B; una cifra inventada "
                    "pasaría por resultado real"
                ),
                que_hacer=(
                    "esto es un defecto del motor, no de sus datos: conserve work_dir "
                    "y manifest.json y repórtelo con la versión de rues-linker; si "
                    "parcheó linkage() en una prueba, devuelva una correlativa con SRC"
                ),
            )
        )
