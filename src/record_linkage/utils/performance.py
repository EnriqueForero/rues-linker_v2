"""
utils.performance — record_linkage_pipeline

Componentes:
    - class PerformanceTracker  (origen: notebook celda [109])
    - function track_performance  (origen: notebook celda [109])
    - function measure_memory  (origen: notebook celda [109])
    - function retry_on_memory_error  (origen: notebook celda [109])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from functools import wraps

import numpy as np
import pandas as pd

from .memory import get_process_rss_bytes


class PerformanceTracker:
    """Clase para almacenar métricas de rendimiento."""

    def __init__(self):
        self.metrics = defaultdict(list)
        self.current_phase = None

    def add_metric(self, phase: str, duration: float, memory_mb: float | None = None):
        """Agregar métrica de rendimiento."""
        self.metrics[phase].append(
            {"duration": duration, "memory_mb": memory_mb, "timestamp": datetime.now()}
        )

    def get_summary(self) -> pd.DataFrame:
        """Obtener resumen de métricas."""
        summary_data = []
        for phase, measurements in self.metrics.items():
            durations = [m["duration"] for m in measurements]
            memory_values = [m["memory_mb"] for m in measurements if m["memory_mb"] is not None]

            summary_data.append(
                {
                    "Fase": phase,
                    "Ejecuciones": len(measurements),
                    "Tiempo_Total_s": sum(durations),
                    "Tiempo_Promedio_s": np.mean(durations),
                    "Tiempo_Min_s": min(durations),
                    "Tiempo_Max_s": max(durations),
                    "Memoria_Promedio_MB": np.mean(memory_values) if memory_values else None,
                }
            )

        return pd.DataFrame(summary_data)


def track_performance(phase_name: str, log_memory: bool = True):
    """
    Decorador mejorado que trackea performance y añade un potente sistema de
    monitoreo de memoria en tiempo real, directamente en el log.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__)
            logger.info(f"▶️  Iniciando: {phase_name}")

            # Import diferido para evitar ciclo performance <-> memory.
            from .memory import MemoryManager

            # --- Checkpoint de Memoria INICIAL ---
            mem_status_start = MemoryManager.get_memory_status()
            start_memory_mb = mem_status_start["process_mb"]
            logger.info(
                # <--- CÓDIGO RESTAURADO: Espera 'process_memory_gb' y 'memory_percent'.
                f"    💾 Estado Memoria Inicial: {mem_status_start['process_memory_gb']:.1f}GB usados por el proceso "
                f"({mem_status_start['memory_percent']:.1f}% del sistema)"
            )

            start_time = time.time()

            try:
                # Ejecutar la función de la fase
                result = func(*args, **kwargs)

                # --- Checkpoint de Memoria FINAL ---
                duration = time.time() - start_time
                mem_status_end = MemoryManager.get_memory_status()
                end_memory_mb = mem_status_end["process_mb"]
                memory_consumed_mb = end_memory_mb - start_memory_mb

                # Determinar el estado de salud de la memoria
                # <--- CÓDIGO RESTAURADO: Espera 'memory_percent'.
                system_percent = mem_status_end["memory_percent"]
                if system_percent >= 85:
                    health_status = "🔴 CRÍTICO"
                elif system_percent >= 70:
                    health_status = "🟡 ALTO"
                else:
                    health_status = "🟢 NORMAL"

                # Log de finalización enriquecido
                logger.info(f"✅ Completado: {phase_name} en {duration:.2f}s")
                logger.info(
                    # <--- CÓDIGO RESTAURADO: Espera 'process_memory_gb'.
                    f"   📈 Estado Memoria Final: {mem_status_end['process_memory_gb']:.1f}GB usados "
                    f"({system_percent:.1f}% del sistema) | "
                    f"Consumo en fase: {memory_consumed_mb:+.1f} MB | "
                    f"Salud del Sistema: {health_status}"
                )

                return result

            except Exception as e:
                logger.error(f"❌ Error en {phase_name}: {e!s}")
                raise

        return wrapper

    return decorator


def measure_memory(warn_threshold_mb: float = 500):
    """
    Decorador para medir y advertir sobre uso excesivo de memoria.

    Args:
        warn_threshold_mb: Umbral en MB para generar advertencia
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Garbage collection antes de medir
            gc.collect()

            # Memoria inicial
            mem_before = get_process_rss_bytes() / 1024**2

            # Ejecutar función
            result = func(*args, **kwargs)

            # Memoria final
            gc.collect()
            mem_after = get_process_rss_bytes() / 1024**2
            mem_used = mem_after - mem_before

            # Logging si excede umbral
            if abs(mem_used) > warn_threshold_mb:
                logger = logging.getLogger(func.__module__)
                logger.warning(
                    f"⚠️ {func.__name__} usó {mem_used:.1f} MB de memoria "
                    f"(antes: {mem_before:.1f} MB, después: {mem_after:.1f} MB)"
                )

            return result

        return wrapper

    return decorator


def retry_on_memory_error(max_retries: int = 3, reduce_batch_factor: float = 0.5):
    """
    Decorador para reintentar operaciones que fallan por memoria.

    Args:
        max_retries: Número máximo de reintentos
        reduce_batch_factor: Factor para reducir batch_size en cada reintento
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            last_error = None

            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except MemoryError as e:
                    retries += 1
                    last_error = e

                    # Liberar memoria
                    gc.collect()

                    # Reducir batch_size si está presente en kwargs
                    if "batch_size" in kwargs and retries < max_retries:
                        old_batch = kwargs["batch_size"]
                        kwargs["batch_size"] = int(old_batch * reduce_batch_factor)

                        logger = logging.getLogger(func.__module__)
                        logger.warning(
                            f"⚠️ MemoryError en {func.__name__}. "
                            f"Reintento {retries}/{max_retries} con batch_size "
                            f"reducido de {old_batch} a {kwargs['batch_size']}"
                        )

            # Si llegamos aquí, se agotaron los reintentos
            raise last_error

        return wrapper

    return decorator
