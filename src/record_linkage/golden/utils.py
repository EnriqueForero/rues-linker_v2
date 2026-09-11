"""
golden.utils — record_linkage_pipeline

Componentes:
    - class MemoryMonitor  (origen: notebook celda [126])
    - class SafeSQLiteConnection  (origen: notebook celda [126])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import sqlite3

import psutil

from ..utils.memory import get_process_rss_bytes


class MemoryMonitor:
    """Monitor de memoria autocontenido para gestión inteligente de recursos."""

    @staticmethod
    def get_memory_status() -> dict[str, float]:
        """Obtiene estado actual de memoria del sistema."""
        try:
            system_memory = psutil.virtual_memory()

            return {
                "process_memory_gb": get_process_rss_bytes() / (1024**3),
                "memory_percent": system_memory.percent,
                "available_gb": system_memory.available / (1024**3),
                "total_gb": system_memory.total / (1024**3),
            }
        except Exception:
            return {"process_memory_gb": 0, "memory_percent": 50, "available_gb": 4, "total_gb": 8}

    @staticmethod
    def calculate_optimal_batch_size(base_size: int, max_size: int = 50_000) -> int:
        """Calcula batch size óptimo basado en memoria disponible."""
        status = MemoryMonitor.get_memory_status()
        memory_percent = status["memory_percent"]

        if memory_percent > 85:
            return min(base_size // 4, 5_000)
        elif memory_percent > 75:
            return min(base_size // 2, 10_000)
        elif memory_percent > 60:
            return min(base_size, 20_000)
        else:
            return min(base_size * 2, max_size)

    @staticmethod
    def should_clear_memory(threshold_gb: float = 1.5) -> bool:
        """Determina si se debe limpiar memoria."""
        status = MemoryMonitor.get_memory_status()
        return status["process_memory_gb"] > threshold_gb or status["memory_percent"] > 80


class SafeSQLiteConnection:
    """Context manager optimizado para conexiones SQLite."""

    def __init__(self, db_path: str, mode: str = "rw"):
        self.db_path = db_path
        self.mode = mode
        self.conn = None

    def __enter__(self):
        if self.mode == "ro":
            self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(self.db_path)

        # Optimizaciones para máximo rendimiento
        pragmas = [
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA mmap_size = 268435456",
            "PRAGMA cache_size = -51200",
            "PRAGMA page_size = 32768",  # Página más grande para mejor I/O
        ]

        for pragma in pragmas:
            with contextlib.suppress(Exception):
                self.conn.execute(pragma)

        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            try:
                if exc_type is None:
                    self.conn.execute("PRAGMA optimize")
            except Exception:
                pass
            self.conn.close()
