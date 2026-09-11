"""record_linkage.config.settings — Configuración central del pipeline.

Centraliza TODOS los parámetros configurables del proyecto. Es la única
fuente de verdad para rutas, umbrales, perfiles e iteración. Cero
magic numbers en lógica de negocio.

Origen: consolidación de las celdas 79, 87 y 143 del notebook fuente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Config:
    """Configuración central del pipeline de Record Linkage.

    Esta clase es la ÚNICA fuente de verdad para rutas base, identificador
    de iteración, batch sizes, perfil LSH y semilla. Cualquier parámetro
    que el usuario pueda querer cambiar va aquí.

    Attributes:
        workspace: Ruta base del proyecto (en disco o Google Drive).
        iteracion: Identificador de la corrida (e.g., 'IT7', 'PROD_2026_02').
        batch_size: Tamaño de lote para procesamiento por chunks.
        random_seed: Semilla global para reproducibilidad.
        profile_name: Nombre del perfil LSH a usar
            (e.g., 'enterprise_scale_4_sources').
        trusted_sources: Fuentes cuya deduplicación interna se omite
            (e.g., {'RUES', 'SUPERSOCIEDADES'}).
        snowflake_config_path: Ruta opcional al config.json con credenciales
            de Snowflake. NO versionar este archivo en git.

    Raises:
        ValueError: Si batch_size < 100 o random_seed < 0.

    Example:
        >>> cfg = Config(
        ...     workspace="/content/drive/MyDrive/record_linkage",
        ...     iteracion="IT7",
        ...     profile_name="enterprise_scale_4_sources",
        ...     trusted_sources={"RUES", "SUPERSOCIEDADES"},
        ... )
    """

    workspace: str = "/content/drive/MyDrive/record_linkage_pipeline"
    iteracion: str = "IT1"
    batch_size: int = 50_000
    random_seed: int = 42
    profile_name: str = "balanced"
    trusted_sources: set[str] = field(default_factory=set)
    snowflake_config_path: str | None = None

    BATCH_MIN: ClassVar[int] = 100
    BATCH_MAX: ClassVar[int] = 500_000

    def __post_init__(self) -> None:
        """Valida invariantes de negocio inmediatamente al crear la config."""
        if not (self.BATCH_MIN <= self.batch_size <= self.BATCH_MAX):
            raise ValueError(
                f"batch_size={self.batch_size} fuera de rango [{self.BATCH_MIN}, {self.BATCH_MAX}]"
            )
        if self.random_seed < 0:
            raise ValueError("random_seed debe ser >= 0")
        if not self.iteracion or not self.iteracion.strip():
            raise ValueError("iteracion no puede estar vacío")
        # Normalize trusted_sources to uppercase to match SRC column convention
        self.trusted_sources = {s.upper() for s in self.trusted_sources}
