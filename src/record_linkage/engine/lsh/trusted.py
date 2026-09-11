"""
engine.trusted — record_linkage_pipeline

Componentes:
    - class TrustedSourceLSHEngine  (origen: notebook celda [194])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .disk_based import DiskBasedLSHEngine


class TrustedSourceLSHEngine(DiskBasedLSHEngine):
    """
    Motor LSH que bloquea comparaciones intra-fuente para fuentes TRUSTED.

    Hereda TODO de DiskBasedLSHEngine v4.0.0 y solo modifica la lógica
    de filtrado de pares candidatos.

    Lógica de filtrado
    ------------------
    - Fuentes TRUSTED (ej: RUES, SUPERSOCIEDADES):
      Sus registros NO se comparan entre sí (son únicos por definición).
    - Fuentes NO trusted (ej: CRM, EXPORTACIONES):
      SÍ se comparan internamente (pueden tener duplicados reales).
    - Cruces entre fuentes:
      SIEMPRE se permiten (RUES↔CRM, RUES↔EXPORTACIONES, etc.).

    Ejemplo
    -------
        ✅ RUES↔RUES → BLOQUEADO (trusted)
        ✅ SUPERSOCIEDADES↔SUPERSOCIEDADES → BLOQUEADO (trusted)
        ✅ CRM↔CRM → PERMITIDO (no trusted)
        ✅ RUES↔CRM → PERMITIDO (cross-source)

    Parameters
    ----------
    profile : dict
        Perfil de configuración LSH.
    config : dict
        Configuración global del pipeline.
    trusted_sources : set[str]
        Nombres de fuentes cuya deduplicación interna se omite.

    Notes
    -----
    RUES tiene 1.92M de 1.97M registros (97.5%). Eliminar comparaciones
    intra-RUES reduce candidatos de ~214M a ~10-15M (>93% reducción).
    """

    VERSION: str = "4.1.0-trusted"

    def __init__(
        self,
        profile: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        trusted_sources: set[str] | None = None,
    ) -> None:
        """Inicializa motor LSH con soporte para Trusted Sources."""
        super().__init__(profile, config)
        self._trusted_sources: frozenset[str] = frozenset(trusted_sources or set())
        self.logger.info(
            f"🛡️ TrustedSourceLSHEngine v{self.VERSION} | "
            f"trusted={sorted(self._trusted_sources) if self._trusted_sources else 'ninguna'}"
        )

    # ── Override: find_candidates ─────────────────────────────────────────────
    def find_candidates(
        self,
        df: pd.DataFrame,
        output_dir: str | None = None,
        cross_source_only: bool = False,
        trusted_unique_sources: set | None = None,
    ) -> set[tuple[int, int]] | str:
        """
        Override que garantiza compatibilidad SRC→FUENTE y activa cross_source.

        v2.10.0 bug-fix: acepta `trusted_unique_sources` como kwarg para
        compatibilidad con `RecordLinkageEngine.link()`. Si se pasa, se
        agrega a `self._trusted_sources`. Antes este parámetro generaba
        TypeError en el path disk_based desde `linkage.py`. Ver
        MIGRATION_LOG §21.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame con columnas NOMBRE_LIMPIO y SRC (o FUENTE).
        output_dir : str
            Directorio de trabajo para archivos SQLite/HDF5.
        cross_source_only : bool
            Si True, solo genera pares cross-source (se activa automáticamente
            si hay trusted_sources configuradas).
        trusted_unique_sources : set | None
            Fuentes confiables adicionales. Se unen al set pasado en __init__.

        Returns
        -------
        Union[Set[Tuple[int, int]], str]
            Set de pares candidatos o ruta a candidates.db.
        """
        # Mezclar con el set del __init__ si se pasó por kwarg.
        if trusted_unique_sources:
            extra = frozenset(trusted_unique_sources)
            if extra - self._trusted_sources:
                self.logger.info(
                    "Agregando trusted_unique_sources al motor: %s",
                    sorted(extra - self._trusted_sources),
                )
                self._trusted_sources = self._trusted_sources | extra
        # ── Mapear SRC → FUENTE si no existe ──
        if "FUENTE" not in df.columns and "SRC" in df.columns:
            df = df.copy()  # No mutar el original
            df["FUENTE"] = df["SRC"]
            self.logger.info("   📝 Columna FUENTE creada desde SRC")

        # ── Activar cross_source si hay trusted sources ──
        if self._trusted_sources and not cross_source_only:
            cross_source_only = True
            self.logger.info("   🔄 cross_source_only=True activado por Trusted Sources")

        return super().find_candidates(
            df, output_dir=output_dir, cross_source_only=cross_source_only
        )

    def _source_pair_mask(
        self,
        left_sources: np.ndarray,
        right_sources: np.ndarray,
        *,
        cross_source_only: bool,
    ) -> np.ndarray:
        """Permite intra-fuente salvo cuando la fuente es confiable.

        ``cross_source_only`` se activa internamente para disponer del mapa de
        fuentes. En este motor no significa vetar toda pareja intra-fuente:
        EXPORTACIONES/CRM pueden contener duplicados reales; RUES y las demás
        fuentes confiables no.
        """

        if not self._trusted_sources:
            return super()._source_pair_mask(
                left_sources,
                right_sources,
                cross_source_only=cross_source_only,
            )
        same_source = left_sources == right_sources
        same_trusted_source = same_source & np.isin(left_sources, tuple(self._trusted_sources))
        return ~same_trusted_source
