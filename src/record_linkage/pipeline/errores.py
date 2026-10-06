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

from collections.abc import Iterable, Sequence


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
    degradación, no un caso borde. También la levanta directamente
    ``reporting._muestreo.muestra_estratificada`` (``n <= 0`` o más estratos
    que ``n``), y los cargadores de L6 la relanzan en vez de tragarla.
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


class TiemposPorFaseError(ErrorPipeline):
    """``metrics["phase_times"]`` no tiene la forma que el orquestador produce.

    Lo levanta :func:`record_linkage.reporting._fases.tiempos_por_fase` cuando
    el mapeo no lo es, una fase no tiene etiqueta (una fase nueva, o un
    productor que sigue usando ``load_validate``/``scoring_time``) o un valor
    no es numérico. Es un defecto del productor de métricas, no un dato
    ausente: los consumidores de L6 lo relanzan en vez de tragarlo con
    ``except Exception`` y seguir sin tiempos.
    """


class EstrategiaFallo(ErrorPipeline):
    """Una estrategia de L6 lanzó mientras generaba sus artefactos.

    La relanza ``BaseReportingStrategy.execute`` con la causa encadenada
    (``__cause__``). Quien la captura (``Orchestrator._run_L6``) decide: si la
    estrategia es obligatoria, la corrida falla con
    :class:`ArtefactoObligatorioError`; si es opcional, la omisión queda en el
    manifiesto (``L6_reporting.meta.omitidos``). Nunca se convierte en ``[]``
    en silencio: eso es lo que escondía corridas sin sus entregables.

    Attributes:
        nombre: nombre descriptivo de la estrategia (``strategy.name``).
        causa: la excepción original.
    """

    def __init__(self, nombre: str, causa: BaseException):
        self.nombre = nombre
        self.causa = causa
        super().__init__(
            mensaje_accionable(
                que_paso=(
                    f"la estrategia de reportes «{nombre}» falló con "
                    f"{type(causa).__name__}: {causa}"
                ),
                por_que_importa=(
                    "sus artefactos no se escribieron; si son obligatorios la corrida no "
                    "tiene entregable, si son opcionales el informe queda incompleto"
                ),
                que_hacer=(
                    "revise la causa encadenada (__cause__) y el log de L6; corrija el "
                    "dato o la dependencia y vuelva a generar los reportes con "
                    "Orchestrator.export_reports()"
                ),
            )
        )


class ArtefactoObligatorioError(ErrorPipeline):
    """Falta al menos un artefacto obligatorio de L6: la corrida FALLA.

    Los obligatorios están declarados en ``reporting.contrato_l6``. Que falte
    uno significa que la corrida no tiene entregable completo, y eso no se
    repara en silencio escribiendo lo que sí salió.

    Attributes:
        faltantes: nombres o patrones de los artefactos obligatorios ausentes.
        output_dir: carpeta de L6 donde se buscaron.
    """

    def __init__(
        self,
        faltantes: Iterable[str],
        output_dir: object,
        *,
        detalle: str | None = None,
    ):
        self.faltantes = tuple(faltantes)
        self.output_dir = output_dir
        que_paso = f"faltan artefactos obligatorios de L6 en {output_dir}: " + ", ".join(
            self.faltantes
        )
        if detalle:
            que_paso += f" ({detalle})"
        super().__init__(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "sin ellos la corrida no tiene entregable (correlativa, golden o "
                    "auditoría de configuración); L6 NO se marca como completada y "
                    "ningún consumidor debe leer esta carpeta como resultado válido"
                ),
                que_hacer=(
                    "revise el log de L6 y la causa encadenada; corrija (espacio en "
                    "disco, permisos, dependencia faltante) y vuelva a generar con "
                    "Orchestrator.export_reports() o repita run(): L1…L5 se reutilizan "
                    "desde el checkpoint"
                ),
            )
        )


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
            aplica (DuckDB, ``separar_columnas_extra=False``) pasan
            :attr:`QUE_HACER_ENTREGA_INCOMPLETA`. Los dos remedios viven aquí,
            juntos: quien añada un tercer camino los encuentra en un sitio.
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

    #: Remedio cuando la ENTREGA no trae una columna que la fuente aportó
    #: (DuckDB o ``separar_columnas_extra=False``): ahí no hay parquets
    #: derramados que realinear ni separación que desactivar.
    QUE_HACER_ENTREGA_INCOMPLETA = (
        "no use este resultado; revise el registro de la fase L5 y de la publicación "
        "para ver dónde se perdió la columna; si usa DuckDB, verifique "
        "`payload_columns` en el manifiesto de ingesta (`ingesta_duckdb/`) y, si "
        "se repite, reporte el caso con el manifiesto y el registro de la corrida"
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
        if self.columnas:
            lista = ", ".join(repr(c) for c in self.columnas)
            que_paso = (
                f"la fuente '{fuente}' tiene valores no hashables (listas o dicts) "
                f"en la(s) columna(s) {lista}; el colapso exacto de duplicados "
                f"no puede comparar esas filas"
            )
            # El remedio debe poder pegarse tal cual: df[['A', 'B']] indexa una
            # lista de columnas; df['A', 'B'] indexaría una tupla (KeyError).
            que_hacer = (
                f"convierta esas columnas a texto antes de llamar (p. ej. "
                f"df[{self.columnas!r}] = df[{self.columnas!r}].astype(str), o "
                f"'|'.join(...) para listas) o desactive el colapso con "
                f"collapse_exact_duplicates=False"
            )
        else:
            detalle = f"{type(causa).__name__}: {causa}" if causa is not None else "sin causa"
            que_paso = (
                f"pandas no pudo comparar las filas de la fuente '{fuente}' para el "
                f"colapso exacto de duplicados ({detalle})"
            )
            que_hacer = (
                "revise la causa citada; convierta a texto las columnas con objetos "
                "(listas o dicts) o desactive el colapso con "
                "collapse_exact_duplicates=False"
            )
        super().__init__(
            mensaje_accionable(
                que_paso=que_paso,
                por_que_importa=(
                    "seguir sin colapsar entregaría una corrida que no hizo lo que se "
                    "pidió y cuyas cifras (processed_rows, INPUT_ROW_COUNT) serían "
                    "indistinguibles de una corrida sin duplicados"
                ),
                que_hacer=que_hacer,
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


class EscrituraSalidaError(ErrorPipeline):
    """La carpeta del estándar no se pudo escribir o leer de forma consistente.

    La levanta ``exporters.escritor`` (F1.10) cuando el nombre no sirve de
    carpeta, la definitiva ya existe, la pendiente trae un ``_trabajo/`` de
    otra corrida, dos figuras se llaman igual o un artefacto no coincide con
    su manifiesto. Nunca deja una carpeta a medias: la pendiente se limpia.
    """


class ContratoSalidaError(ErrorPipeline):
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


class ColumnasTecnicasError(ErrorPipeline):
    """No se pudieron recuperar las columnas técnicas que el contrato retiró.

    Desde F1.9 ``NIT_BASE``, ``NIT_VALID``, ``NIT_OK``, ``NOMBRE_LIMPIO``… no
    viajan en la correlativa entregada: quedan en el checkpoint de L5 del
    ``dir_trabajo``. :mod:`record_linkage.salida.tecnicas` las vuelve a pegar
    y levanta esto cuando no hay de dónde (sin ``dir_trabajo``, sin parquet,
    sin la columna pedida) o cuando no puede alinearlas sin adivinar.
    """
