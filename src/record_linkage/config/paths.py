"""record_linkage.config.paths — Manejo centralizado de rutas con pathlib.

Toda ruta del proyecto pasa por aquí. Nunca usar os.path.join ni
concatenación de strings con '/'. Las propiedades son derivadas — la
única ruta que se asigna es la base.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..utils.output import safe_print as print
from .settings import Config


@dataclass
class Rutas:
    """Single Source of Truth para todas las rutas del proyecto.

    Construye paths con pathlib a partir de la Config. Las subcarpetas
    son propiedades derivadas: nunca se asignan directamente. Llama a
    `crear_directorios()` antes de cualquier operación de I/O.

    Attributes:
        base: Ruta raíz para esta corrida (workspace / iteracion).

    Example:
        >>> rutas = Rutas.desde_config(cfg)
        >>> rutas.crear_directorios()
        >>> ruta_input = rutas.entrada / "fuente_crm.parquet"
    """

    base: Path

    @classmethod
    def desde_config(cls, config: Config) -> Rutas:
        """Build paths from central configuration.

        Args:
            config: Configuración del proyecto.

        Returns:
            Rutas con base = workspace / iteracion.
        """
        return cls(base=Path(config.workspace) / config.iteracion)

    @property
    def entrada(self) -> Path:
        """Carpeta de datos de entrada (fuentes RUES, DIAN, CRM, etc.)."""
        return self.base / "input"

    @property
    def salida(self) -> Path:
        """Carpeta de resultados finales (golden records, correlativas)."""
        return self.base / "output"

    @property
    def checkpoints(self) -> Path:
        """Carpeta de checkpoints intermedios (.parquet por fase)."""
        return self.base / "checkpoints"

    @property
    def ground_truth(self) -> Path:
        """Carpeta de datasets de prueba (ground truth)."""
        return self.base / "ground_truth"

    @property
    def logs(self) -> Path:
        """Carpeta de logs de ejecución."""
        return self.base / "logs"

    @property
    def reports(self) -> Path:
        """Carpeta de reportes (Excel, HTML dashboards)."""
        return self.base / "reports"

    def crear_directorios(self) -> None:
        """Crea todos los directorios necesarios si no existen."""
        for ruta in [
            self.entrada,
            self.salida,
            self.checkpoints,
            self.ground_truth,
            self.logs,
            self.reports,
        ]:
            ruta.mkdir(parents=True, exist_ok=True)
        print(f"✅ Directorios listos en: {self.base}")
