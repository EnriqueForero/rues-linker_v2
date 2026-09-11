"""
utils.logger — record_linkage_pipeline

Componentes:
    - class CustomLogger  (origen: notebook celda [110])
    - function setup_logger  (origen: notebook celda [124])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime

import pytz

from .output import _text_for_encoding


class _EncodingSafeStreamHandler(logging.StreamHandler):
    """StreamHandler que escapa sólo lo que la consola no puede codificar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = self.stream
            encoding = getattr(stream, "encoding", None)
            if isinstance(encoding, str) and encoding:
                message = _text_for_encoding(message, encoding)
                terminator = _text_for_encoding(self.terminator, encoding)
            else:
                terminator = self.terminator
            stream.write(message + terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


class CustomLogger:
    """
    Logger personalizado V2.0 - Robusto y Unificado.

    Esta versión mejorada:
    - Centraliza el formato de los logs emitidos por rues-linker.
    - Respeta por completo la configuración de logging de la aplicación host.
    - Utiliza la zona horaria de Colombia ('America/Bogota') de forma consistente.
    - Mantiene la funcionalidad de añadir información de memoria a los logs críticos.

    Notas de diseño (v2.0.1):
        Una librería no debe borrar ni cerrar handlers del logger raíz: en
        Colab/IPython eso elimina la captura de la consola y, en aplicaciones,
        puede destruir handlers de auditoría del proceso anfitrión. Cada logger
        nombrado obtiene como máximo un handler propio y no propaga al root.
    """

    # Conservada por compatibilidad con callers/tests que resetean la bandera.
    # Ya no representa ni modifica el logger raíz.
    _logging_configured: bool = False

    def __init__(self, name: str, level: int = logging.INFO):
        """
        Al instanciar, obtiene un logger y se asegura de que la configuración
        global se haya ejecutado una sola vez.
        """
        # Obtener y configurar SOLO el logger específico de rues-linker.
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False
        self._setup_named_logger_once(self.logger)

        # Contadores para resúmenes
        self.log_counts: Counter[str] = Counter()

    @classmethod
    def _setup_global_logging_once(cls) -> None:
        """Compatibility no-op: never mutate the root logger.

        Kept because older integrations called this private method while
        resetting test state. New code configures only the named logger.
        """
        cls._logging_configured = True

    @classmethod
    def _setup_named_logger_once(cls, logger: logging.Logger) -> None:
        """Attach one rues-linker handler without touching host handlers."""

        cls._setup_global_logging_once()
        if logger.handlers:
            return

        class ColombiaFormatter(logging.Formatter):
            """Formatter personalizado para usar la hora de Bogotá."""

            colombia_tz = pytz.timezone("America/Bogota")

            def formatTime(self, record, datefmt=None):
                dt_colombia = datetime.fromtimestamp(record.created, tz=self.colombia_tz)
                if datefmt:
                    return dt_colombia.strftime(datefmt)
                return dt_colombia.strftime("%Y-%m-%d %H:%M:%S")

        handler = _EncodingSafeStreamHandler(sys.stdout)
        handler.setFormatter(
            ColombiaFormatter("%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s")
        )
        logger.addHandler(handler)

    def _log_with_memory(self, level_name: str, message: str, *args, **kwargs):
        """
        Método interno que añade info de memoria y delega el logging.
        Mantenemos esta funcionalidad intacta.
        """
        level = level_name.upper()
        self.log_counts[level] += 1

        # Añadir info de memoria para logs importantes
        if level in ["WARNING", "ERROR", "CRITICAL"]:
            try:
                # Import diferido para evitar ciclo memory <-> logger.
                from .memory import MemoryManager

                mem_info = MemoryManager.get_memory_status()
                message += f" [Memoria: {mem_info['used_gb']:.1f}GB/{mem_info['total_gb']:.1f}GB]"
            except Exception:
                pass  # No fallar si el monitoreo de memoria falla

        # Delegar el logging al logger de Python subyacente
        self.logger.log(logging.getLevelName(level), message, *args, **kwargs)

    # --- Métodos públicos para logging (interfaz sin cambios) ---
    def info(self, message: str, *args, **kwargs):
        self._log_with_memory("INFO", message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        self._log_with_memory("WARNING", message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        self._log_with_memory("ERROR", message, *args, **kwargs)

    def debug(self, message: str, *args, **kwargs):
        self._log_with_memory("DEBUG", message, *args, **kwargs)

    def get_summary(self) -> dict[str, int]:
        """Obtener resumen de logs emitidos."""
        return dict(self.log_counts)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = _EncodingSafeStreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    # El handler es propio; propagar al root duplicaría mensajes y haría que el
    # comportamiento dependiera de la aplicación anfitriona.
    logger.propagate = False
    return logger
