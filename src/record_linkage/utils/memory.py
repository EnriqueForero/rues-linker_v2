"""
utils.memory — record_linkage_pipeline

Componentes:
    - class MemoryManager  (origen: notebook celda [110])
    - class AdaptiveMemoryManager  (origen: notebook celda [110])
    - function memoria_disponible  (origen: notebook celda [85])
    - function limpiar_memoria  (origen: notebook celda [85])
    - function mostrar_memoria  (origen: notebook celda [85])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

import pandas as pd
import psutil

from .output import safe_print as print


def get_process_rss_bytes() -> int:
    """Return the current process RSS without making monitoring fatal.

    ``psutil.Process().memory_info()`` can raise ``NoSuchProcess`` in short-lived
    PID namespaces (for example some hosted notebook and CI containers) even
    though the Python process is still running.  Memory telemetry is useful,
    but it must never abort the linkage pipeline, so we use two local fallbacks.
    """

    try:
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass

    # Linux/Colab: /proc/self is namespace-aware and reports current RSS.
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            raise OSError("os.sysconf no está disponible")
        return resident_pages * int(sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError, KeyError):
        pass

    # Windows no expone /proc ni resource.getrusage. Consultar el working set
    # del proceso actual mediante la API estable del sistema evita convertir
    # un fallo transitorio de psutil en un RSS ficticio de cero.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            process = kernel32.GetCurrentProcess()
            ok = psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            if ok:
                return int(counters.WorkingSetSize)
        except (AttributeError, OSError, ValueError):
            pass

    # Portable Unix fallback. ru_maxrss is a peak (not current) value, but it
    # is preferable to failing or inventing a value. macOS reports bytes;
    # Linux/BSD report KiB.
    try:
        # Importación dinámica: ``resource`` no existe en Windows y sus
        # atributos tampoco forman parte de los stubs de esa plataforma.
        import importlib

        resource: Any = importlib.import_module("resource")
        max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return max_rss if sys.platform == "darwin" else max_rss * 1024
    except (ImportError, OSError, ValueError):
        return 0


class RSSSampler:
    """Muestrea el RSS del proceso con un ciclo de vida explícito.

    El hilo es deliberadamente de un solo uso: esto evita reinicios ambiguos y
    hace verificable que cada ``start()`` tenga su ``stop()`` y ``join()``.
    La telemetría nunca debe abortar el pipeline; una lectura puntual fallida
    se ignora y la siguiente muestra vuelve a intentarse.
    """

    def __init__(
        self,
        interval_seconds: float = 0.05,
        reader: Callable[[], int] = get_process_rss_bytes,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds debe ser mayor que cero")
        self._interval_seconds = float(interval_seconds)
        self._reader = reader
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_bytes = 0
        self._lock = threading.Lock()

    def _sample_once(self) -> None:
        try:
            value = max(0, int(self._reader()))
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        with self._lock:
            self._peak_bytes = max(self._peak_bytes, value)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self._sample_once()

    def start(self) -> RSSSampler:
        """Inicia el hilo y registra una muestra inmediata."""
        if self._thread is not None:
            raise RuntimeError("RSSSampler es de un solo uso")
        self._sample_once()
        self._thread = threading.Thread(
            target=self._run,
            name="rues-linker-rss-sampler",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Solicita la terminación; no sustituye a ``join()``."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        """Espera la terminación y falla si el hilo siguiera vivo."""
        if self._thread is None:
            return
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise RuntimeError("El hilo de muestreo RSS no terminó")

    def close(self) -> None:
        """Detiene y espera el hilo incluso si ``stop`` cambia en el futuro."""
        try:
            self.stop()
        finally:
            self.join()

    @property
    def peak_bytes(self) -> int:
        with self._lock:
            return self._peak_bytes

    @property
    def peak_mib(self) -> float:
        return self.peak_bytes / (1024**2)

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def __enter__(self) -> RSSSampler:
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class MemoryManager:
    """Gestor proactivo de memoria para Google Colab."""

    @staticmethod
    def get_memory_status() -> dict[str, float]:
        """
        Versión CORREGIDA que retorna todas las claves necesarias
        para evitar KeyErrors en otras partes del sistema.
        """
        memory = psutil.virtual_memory()
        # Calcular memoria del proceso en MB. La telemetría tiene fallback para
        # PID namespaces donde psutil puede lanzar NoSuchProcess espuriamente.
        process_mb = get_process_rss_bytes() / (1024**2)

        return {
            "total_gb": memory.total / (1024**3),
            "available_gb": memory.available / (1024**3),
            "used_gb": (memory.total - memory.available) / (1024**3),
            # <--- CORRECCIÓN 1: Se entrega la clave 'memory_percent'.
            "memory_percent": memory.percent,
            "process_mb": process_mb,
            # <--- CORRECCIÓN 2: Se entrega la clave 'process_memory_gb'.
            "process_memory_gb": process_mb / 1024,
        }

    @staticmethod
    def check_memory_availability(required_gb: float = 2.0) -> bool:
        status = MemoryManager.get_memory_status()
        return status["available_gb"] >= required_gb

    @staticmethod
    def optimize_memory():
        """Fuerza recolección de basura y mide la RAM realmente liberada.

        Mide el RSS del proceso antes y después de ``gc.collect()`` con
        psutil. No estima un valor ficticio ni toca internals privados de
        pandas, que pueden cambiar entre versiones sin aviso.
        """
        before_mb = MemoryManager.get_memory_status()["process_mb"]
        collected = gc.collect()
        status_after = MemoryManager.get_memory_status()
        liberado_mb = max(0.0, before_mb - status_after["process_mb"])
        return {
            "objects_collected": collected,
            "rss_liberado_mb": round(liberado_mb, 1),
            "available_gb_after": status_after["available_gb"],
        }

    @staticmethod
    def monitor_and_warn(
        logger: Any | None = None,
        warning_pct: float = 80.0,
        critical_pct: float = 92.0,
    ):
        """Advierte (y recolecta) según el % de RAM usada de la máquina.

        v0.12.0: umbrales porcentuales explícitos. La versión previa leía
        constantes absolutas en GB vía ``globals()`` que jamás existían en
        este módulo (siempre caía al default) y que no escalaban entre
        máquinas de distinto tamaño.
        """
        status = MemoryManager.get_memory_status()
        if status["memory_percent"] > critical_pct:
            message = (
                f"🚨 MEMORIA CRÍTICA: {status['used_gb']:.1f}GB usados "
                f"({status['memory_percent']:.1f}% > {critical_pct:.0f}%). Liberando memoria..."
            )
            if logger:
                logger.error(message)
            else:
                print(message)
            MemoryManager.optimize_memory()
        elif status["memory_percent"] > warning_pct:
            message = (
                f"⚠️ Advertencia de memoria: {status['used_gb']:.1f}GB usados "
                f"({status['memory_percent']:.1f}% > {warning_pct:.0f}%)"
            )
            if logger:
                logger.warning(message)
            else:
                print(message)

    @staticmethod
    def estimate_dataframe_memory(df: pd.DataFrame) -> float:
        return df.memory_usage(deep=True).sum() / (1024**3)

    @staticmethod
    def can_fit_in_memory(df: pd.DataFrame, safety_factor: float = 1.5) -> bool:
        required_gb = MemoryManager.estimate_dataframe_memory(df) * safety_factor
        return MemoryManager.check_memory_availability(required_gb)


class AdaptiveMemoryManager:
    """Gestor adaptativo de memoria con umbrales PORCENTUALES.

    v0.12.0 (cierre del hallazgo H4 de la auditoría 2026-08-26): la versión
    anterior usaba umbrales ABSOLUTOS invertidos — "crítico" se disparaba
    cuando quedaban menos de 9 GB *libres*, es decir casi siempre en Colab
    Free (~12.7 GB totales), y la rama "warning" (< 8 GB libres) era
    inalcanzable (código muerto): el motor legacy vivía en modo emergencia
    permanente con batches de 5.000. Medido en la auditoría (E11): con 7.1 GB
    libres de 7.8 declaraba "memoria crítica: 0.7GB usados".

    Semántica nueva (porcentaje de RAM DISPONIBLE sobre el total):
        - disponible < ``critical_pct``  → modo emergencia.
        - disponible < ``warning_pct``   → reducir batch sizes.
        - disponible > ``warning_pct + histeresis_pct`` → restaurar config
          original (histéresis: evita oscilar entre reducir y restaurar).

    Los kwargs legados ``warning_threshold_gb``/``critical_threshold_gb`` se
    aceptan por compatibilidad de firma pero se IGNORAN con un warning: su
    semántica original era el bug. Portable a cualquier máquina (Colab Free,
    Colab Pro, local) sin recalibrar constantes absolutas.
    """

    def __init__(
        self,
        warning_threshold_gb: float | None = None,
        critical_threshold_gb: float | None = None,
        *,
        warning_pct: float = 25.0,
        critical_pct: float = 12.0,
        histeresis_pct: float = 10.0,
    ):
        """
        Inicializar con umbrales porcentuales de RAM disponible.

        Args:
            warning_threshold_gb: LEGADO, ignorado (la semántica GB-absoluta
                era el bug H4). Se acepta para no romper llamadores 0.11.x.
            critical_threshold_gb: LEGADO, ignorado (ídem).
            warning_pct: % de RAM disponible bajo el cual se reducen batches.
            critical_pct: % de RAM disponible bajo el cual se entra en
                emergencia. Debe ser < warning_pct.
            histeresis_pct: puntos porcentuales POR ENCIMA de warning_pct
                exigidos para restaurar la config original.

        Raises:
            ValueError: si los porcentajes no cumplen 0 < critical < warning <= 100.
        """
        if not (0.0 < critical_pct < warning_pct <= 100.0):
            raise ValueError(
                f"Umbrales inválidos: se requiere 0 < critical_pct ({critical_pct}) "
                f"< warning_pct ({warning_pct}) <= 100."
            )
        self.warning_pct = float(warning_pct)
        self.critical_pct = float(critical_pct)
        self.histeresis_pct = float(histeresis_pct)
        self.original_config: dict[str, Any] = {}
        # Import diferido para evitar ciclo memory <-> logger.
        from .logger import CustomLogger

        self.logger = CustomLogger("AdaptiveMemoryManager")

        if warning_threshold_gb is not None or critical_threshold_gb is not None:
            self.logger.warning(
                "AdaptiveMemoryManager: los umbrales en GB absolutos están "
                "deprecados y se ignoran (su semántica invertida era el bug H4; "
                "ver CHANGELOG 0.12.0). Se usan umbrales porcentuales: "
                f"warning<{self.warning_pct:.0f}% · critical<{self.critical_pct:.0f}% "
                f"de RAM disponible."
            )

    def _estado_memoria(self) -> tuple[float, float, float]:
        """(pct_disponible, disponible_gb, total_gb) de la máquina actual."""
        mem = psutil.virtual_memory()
        total = float(mem.total)
        disponible = float(mem.available)
        pct = 100.0 * disponible / total if total > 0 else 0.0
        return pct, disponible / 1024**3, total / 1024**3

    def monitor_and_adapt(self, config: dict[str, Any]) -> dict[str, Any]:
        """Monitorear memoria y adaptar configuración si es necesario."""
        if not self.original_config:
            self.original_config = config.copy()

        pct, disp_gb, total_gb = self._estado_memoria()

        if pct < self.critical_pct:
            self.logger.warning(
                f"Memoria crítica: {disp_gb:.1f} GB libres de {total_gb:.1f} "
                f"({pct:.0f}% < {self.critical_pct:.0f}%). Activando modo emergencia"
            )
            return self._emergency_mode(config)
        elif pct < self.warning_pct:
            self.logger.warning(
                f"Memoria alta: {disp_gb:.1f} GB libres de {total_gb:.1f} "
                f"({pct:.0f}% < {self.warning_pct:.0f}%). Reduciendo batch sizes"
            )
            return self._reduce_batch_sizes(config)

        return self._try_restore_config(config)

    def _emergency_mode(self, config: dict[str, Any]) -> dict[str, Any]:
        """Configuración de emergencia para evitar OOM."""
        emergency_config = config.copy()
        emergency_config["batch_size"] = 5_000
        emergency_config["lsh_batch_size"] = 2_000
        emergency_config["chunk_size"] = 10_000
        emergency_config["use_disk_cache"] = True
        emergency_config["aggressive_gc"] = True
        emergency_config["max_workers"] = 1
        self.logger.info("Configuración de emergencia aplicada")
        return emergency_config

    def _reduce_batch_sizes(self, config: dict[str, Any]) -> dict[str, Any]:
        """Reducir tamaños de lote proporcionalmente."""
        adapted_config = config.copy()
        batch_params = ["batch_size", "lsh_batch_size", "chunk_size"]

        for param in batch_params:
            if param in adapted_config:
                adapted_config[param] = max(1000, int(adapted_config[param] * 0.5))

        return adapted_config

    def _try_restore_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Restaurar la config original si hay holgura (histéresis porcentual)."""
        pct, _disp_gb, _total_gb = self._estado_memoria()

        if pct > self.warning_pct + self.histeresis_pct:
            if config != self.original_config:
                self.logger.info(
                    f"Memoria suficiente ({pct:.0f}% libre), restaurando configuración original"
                )
            return self.original_config.copy()

        return config


def memoria_disponible() -> dict[str, float]:
    """Retorna información sobre memoria disponible."""
    mem = psutil.virtual_memory()
    return {
        "total_gb": mem.total / (1024**3),
        "disponible_gb": mem.available / (1024**3),
        "usada_gb": mem.used / (1024**3),
        "porcentaje_usado": mem.percent,
    }


def limpiar_memoria():
    """Fuerza liberación de memoria."""
    gc.collect()
    mem = memoria_disponible()
    print(
        f"🧹 Memoria liberada. Disponible: {mem['disponible_gb']:.2f} GB ({100 - mem['porcentaje_usado']:.1f}%)"
    )


def mostrar_memoria():
    """Muestra estado actual de memoria."""
    mem = memoria_disponible()
    usado = mem["porcentaje_usado"]

    # Indicador visual
    if usado < 60:
        icono = "🟢"
    elif usado < 80:
        icono = "🟡"
    else:
        icono = "🔴"

    print(f"{icono} Memoria: {mem['usada_gb']:.1f}/{mem['total_gb']:.1f} GB ({usado:.1f}% usado)")
