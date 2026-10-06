"""Excepciones específicas del pipeline, con mensaje accionable.

Regla 5 del CLAUDE.md: fail-fast con mensaje accionable, en el formato
«qué pasó · por qué importa · qué hacer». Cada excepción de este módulo lleva
los tres bloques en ``str(exc)``, para que quien la lea en Colab o en un log
sepa qué hacer sin abrir el código.

Este módulo no importa nada del paquete: cualquier capa (reporting, pipeline,
api) puede usarlo sin crear ciclos de importación.

Creado en F1.4 (contrato de L6). F1.2 y F1.9 definen aquí sus propias
excepciones (``ConsolidacionNitError``, ``ContratoSalidaError``); si llegan por
otra rama, se unifican en la integración bajo este mismo archivo.
"""

from __future__ import annotations

from collections.abc import Iterable


def mensaje_accionable(que_paso: str, por_que_importa: str, que_hacer: str) -> str:
    """Compone el mensaje en el formato fijo del repositorio."""

    return f"Qué pasó: {que_paso}\nPor qué importa: {por_que_importa}\nQué hacer: {que_hacer}"


class ErrorPipeline(Exception):
    """Base de las excepciones del pipeline. ``str(exc)`` es accionable."""


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
