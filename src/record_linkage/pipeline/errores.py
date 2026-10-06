"""Errores del pipeline (L1…L6) con mensaje accionable.

Regla 5 de ``CLAUDE.md`` y regla 6 del plan: fail-fast, y el mensaje dice
**qué pasó, por qué importa y qué hacer**. Este módulo es el punto único donde
viven las excepciones específicas del pipeline, para que el llamador pueda
distinguir «la consolidación por NIT reventó» de «el checkpoint no es
reutilizable» sin inspeccionar cadenas.

Convenciones
------------
* Toda excepción hereda de :class:`ErrorPipeline` (que es un ``RuntimeError``:
  los ``except RuntimeError`` del código heredado la siguen viendo).
* El texto se arma con :func:`formatear_mensaje_accionable`, que es la única
  definición del formato de tres secciones. No se escriben a mano.
* Cuando el error envuelve otro, se levanta con ``raise ... from causa`` y la
  causa se cita en el texto: el usuario de Colab ve el mensaje, no el
  ``traceback`` completo.
"""

from __future__ import annotations


def formatear_mensaje_accionable(que_paso: str, por_que_importa: str, que_hacer: str) -> str:
    """Compone el mensaje de tres secciones que exige la casa.

    Las etiquetas son literales (``Qué pasó`` · ``Por qué importa`` ·
    ``Qué hacer``) para que las pruebas y los lectores las encuentren siempre
    iguales.
    """
    return f"Qué pasó: {que_paso}\nPor qué importa: {por_que_importa}\nQué hacer: {que_hacer}"


class ErrorPipeline(RuntimeError):
    """Base de los errores específicos del pipeline L1…L6."""


class ConsolidacionNitError(ErrorPipeline):
    """La consolidación final por NIT de L5 falló; la corrida no puede continuar.

    Hasta F1.2 el orquestador atrapaba cualquier excepción de
    ``consolidate_groups_by_nit_balanced``, escribía una advertencia y
    entregaba el golden y la correlativa SIN consolidar, con ``L5_golden``
    marcada ``DONE``. Eso es exactamente «reparar en silencio»: la entrega
    salía degradada —grupos que debían fundirse por NIT seguían separados— y
    nadie lo veía en una corrida de cuarenta minutos cuyo registro nadie lee
    entero. Ahora la corrida falla aquí, L5 no se persiste y el usuario sabe
    qué pasó.
    """

    @classmethod
    def desde_causa(
        cls,
        causa: BaseException,
        *,
        n_golden: int | None = None,
        n_correlativa: int | None = None,
    ) -> ConsolidacionNitError:
        """Construye el error a partir de la excepción original.

        El llamador debe levantarlo con ``raise ... from causa`` para que
        ``__cause__`` quede encadenado; aquí solo se arma el texto.
        """
        tamanos = ""
        if n_golden is not None and n_correlativa is not None:
            tamanos = f" sobre {n_golden:,} golden records y {n_correlativa:,} filas correlativas"
        return cls(
            formatear_mensaje_accionable(
                que_paso=(
                    "la consolidación final por NIT de L5 "
                    f"(consolidate_groups_by_nit_balanced){tamanos} lanzó "
                    f"{type(causa).__name__}: {causa}"
                ),
                por_que_importa=(
                    "sin ese paso el golden y la correlativa quedan SIN consolidar "
                    "(entidades con el mismo NIT siguen separadas) y una entrega "
                    "degradada se publicaría como si fuera completa; por eso la fase "
                    "L5_golden NO se marca DONE y L6 no corre"
                ),
                que_hacer=(
                    "revise la causa encadenada (`__cause__`) y el registro de la "
                    "corrida; corrija el dato o el perfil que la provocó y vuelva a "
                    "ejecutar — L1…L4 se reutilizan del checkpoint y solo L5 se repite"
                ),
            )
        )
