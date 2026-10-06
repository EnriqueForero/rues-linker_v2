"""Excepciones específicas del pipeline (L1…L6) con mensaje accionable.

Regla 5 de ``CLAUDE.md`` y regla 6 del plan: fail-fast, y el mensaje dice
**qué pasó, por qué importa y qué hacer**. Este módulo es el punto único donde
viven las excepciones específicas del pipeline, para que el llamador pueda
distinguir «la consolidación por NIT reventó» de «el golden no cumple su
contrato» sin inspeccionar cadenas. Cada tarea de la fase F1 añade aquí sus
excepciones concretas en vez de levantar ``RuntimeError`` o ``ValueError``
genéricos desde la librería.

Convenciones
------------
* Toda excepción hereda de :class:`ErrorPipeline` (que es un ``RuntimeError``:
  los ``except RuntimeError`` del código heredado la siguen viendo).
* El texto se arma con :func:`mensaje_accionable`, que es la única definición
  del formato de tres secciones. No se escribe a mano.
* Cuando el error envuelve otro, se levanta con ``raise ... from causa`` y la
  causa se cita en el texto: el usuario de Colab ve el mensaje, no el
  ``traceback`` completo.
"""

from __future__ import annotations


def mensaje_accionable(que_paso: str, por_que_importa: str, que_hacer: str) -> str:
    """Devuelve el texto de un error en el formato de la regla 5.

    Las etiquetas son literales (``Qué pasó`` · ``Por qué importa`` ·
    ``Qué hacer``) para que las pruebas y los lectores las encuentren siempre
    iguales.

    Args:
        que_paso: hecho observado, con cifras o nombres concretos.
        por_que_importa: consecuencia para el entregable si se ignora.
        que_hacer: remedio concreto (comando, parámetro, archivo).
    """
    return f"Qué pasó: {que_paso}\nPor qué importa: {por_que_importa}\nQué hacer: {que_hacer}"


class ErrorPipeline(RuntimeError):
    """Base de las excepciones específicas del pipeline L1…L6."""


class GoldenInvalidoError(ErrorPipeline):
    """El golden no cumple su contrato: columnas ajenas, métricas nulas o grupos repetidos.

    Lo levanta :func:`record_linkage.golden.metricas.verificar_golden` antes de
    que el orquestador persista ``golden.parquet``.
    """


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
            mensaje_accionable(
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


class MuestreoReportesError(ErrorPipeline):
    """La muestra que alimenta los reportes L6 no representa al insumo.

    Hasta F1.3 ``EnhancedReportingSuite`` muestreaba con
    ``groupby("SRC").apply(sample)``: con pandas 3 la columna de agrupación
    desaparecía del marco y las filas con ``SRC`` NaN se descartaban. Como el
    golden trae ``SRC`` NaN casi siempre, con más de 30.000 filas la muestra
    quedaba vacía y tres artefactos se omitían con un WARNING que nadie lee.
    Ahora la suite falla aquí: un insumo con filas que produce una muestra
    vacía, o una muestra a la que le faltan columnas del insumo, es una
    degradación, no un caso borde.
    """

    @classmethod
    def desde_muestra(
        cls,
        tabla: str,
        *,
        n_origen: int | None,
        n_muestra: int,
        columnas_perdidas: list[str],
        causa: str | None = None,
    ) -> MuestreoReportesError:
        """Arma el mensaje con lo que se midió del insumo y de la muestra."""
        if n_muestra == 0:
            origen = f"{n_origen:,} filas" if n_origen is not None else "filas"
            que_paso = f"el insumo '{tabla}' tiene {origen} y la muestra para reportes quedó vacía"
        else:
            que_paso = (
                f"la muestra de '{tabla}' ({n_muestra:,} filas) perdió columnas del insumo: "
                f"{', '.join(columnas_perdidas)}"
            )
        if causa:
            que_paso += f" (la carga registró: {causa})"
        return cls(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "los reportes L6 (tarjeta de calidad, heatmap de intersección, casos "
                    "problemáticos) se calculan sobre esa muestra; con una muestra vacía o "
                    "incompleta se omiten o mienten, y la entrega saldría degradada sin que "
                    "nadie lo vea"
                ),
                que_hacer=(
                    "revise el insumo (tipo, columnas, ruta) y el registro de carga; si el "
                    "insumo es correcto, el defecto está en reporting._muestreo o en la "
                    "lectura del archivo y debe corregirse, no silenciarse"
                ),
            )
        )


class GoldenSinTiparError(ErrorPipeline):
    """Una métrica del golden no admite el tipo del contrato (nulos o valores ajenos).

    Lo levanta :func:`record_linkage.golden.tipos.tipar_golden` en modo
    estricto: un conteo con nulos o un ``REQUIRES_REVIEW`` que no es 0/1 no se
    puede convertir a ``int64``/``bool`` sin inventar un valor.
    """
