"""
reporting.time_estimator — record_linkage_pipeline

Componentes:
    - class RiskLevel  (origen: notebook celda [193])
    - class IterationData  (origen: notebook celda [193])
    - class TimeEstimator  (origen: notebook celda [193])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from pathlib import Path

from ._lsh_refs import (
    CONFIGURACIONES_LSH,
    ITERACIONES_REFERENCIA,
    N_REF,
    TIEMPO_POR_BANDA,
)
from ._models import IterationData, RiskLevel


class TimeEstimator:
    def __init__(self, n: int, config: dict, workspace: Path | None = None):
        self.n = n
        self.config = config or {}
        self.workspace = workspace
        self.profile = self.config.get("profiles", {}).get(self.config.get("profile", ""), {})

        self.perms = self.profile.get("lsh_permutations", 128)
        self.lsh_th = self.profile.get("lsh_threshold", 0.55)

        self.bandas, self.filas, self.th_eff = self._calc_threshold()
        self.scale = self.n / N_REF
        self.cands = self._leer_cands() or self._estimar_cands()
        self.mem = self._estimar_mem()

    def _calc_threshold(self) -> tuple[int, int, float]:
        configs = CONFIGURACIONES_LSH.get(self.perms, [(16, 8, 0.707)])
        return min(configs, key=lambda x: abs(x[2] - self.lsh_th))

    def _leer_cands(self) -> float | None:
        if not self.workspace:
            return None
        db = self.workspace / "L2_lsh_candidates" / "candidates.db"
        if not db.exists():
            return None
        try:
            return (db.stat().st_size / 30) / 1e6
        except:
            return None

    def _estimar_cands(self) -> float:
        # Modelo por zonas calibrado
        if self.th_eff < 0.55:
            return 480 * self.scale  # Zona IT-8
        if self.th_eff < 0.62:
            return 193 * self.scale  # Zona IT-7
        if self.th_eff < 0.68:
            return 77 * self.scale  # Zona IT-4
        return 44 * self.scale  # Zona IT-2

    def _estimar_mem(self) -> float:
        if self.cands > 200:
            return 12.0
        if self.cands > 100:
            return 8.0
        return 6.0

    def _tiempo_por_banda(self) -> float:
        # Interpolación basada en datos reales
        return TIEMPO_POR_BANDA.get(self.bandas, 5.0 * (self.bandas / 32))

    def estimar_fase(self, fase: str) -> tuple[float, str, float]:
        if fase == "L1_prep":
            return 4.5 * self.scale, "~4min", 3.0

        elif fase == "L2_lsh_candidates":
            t_mh = 56 * (self.perms / 128) * self.scale
            t_idx = 12 * (self.bandas / 16) ** 1.3 * self.scale
            t_cand = self.bandas * self._tiempo_por_banda() * self.scale
            total = t_mh + t_idx + t_cand
            return total, f"MH:{t_mh:.0f}m + Idx:{t_idx:.0f}m + Cand:{t_cand:.0f}m", self.mem

        elif fase == "L3_scoring":
            t = 23 * (self.cands / 44) ** 1.2 * self.scale
            return t, f"~{t:.0f}min ({self.cands:.0f}M pares)", self.mem * 0.7

        elif fase == "L4_clustering":
            return 1.0, "<1min", 2.0

        elif fase == "L5_golden":
            return 28 * self.scale, "~28min", 4.0

        elif fase == "L6_reporting":
            return 5.0, "~5min", 2.0

        return 0, "", 0

    def estimar_total(self) -> dict:
        fases = [
            "L1_prep",
            "L2_lsh_candidates",
            "L3_scoring",
            "L4_clustering",
            "L5_golden",
            "L6_reporting",
        ]
        res = {}
        total_min = 0
        max_mem = 0

        for f in fases:
            m, d, mem = self.estimar_fase(f)
            res[f] = {"minutos": m, "detalle": d, "memoria_GB": mem}
            total_min += m
            max_mem = max(max_mem, mem)

        # Clasificación de riesgo
        if self.cands > 250 or total_min > 660:
            riesgo = RiskLevel.CRITICAL
        elif self.cands > 150 or total_min > 540:
            riesgo = RiskLevel.HIGH
        elif self.cands > 80 or total_min > 360:
            riesgo = RiskLevel.MEDIUM
        else:
            riesgo = RiskLevel.LOW

        res["total"] = {
            "minutos": total_min,
            "horas": total_min / 60,
            "candidatos_M": self.cands,
            "th_efectivo": self.th_eff,
            "bandas": self.bandas,
            "filas": self.filas,
            "perms": self.perms,
            "memoria_GB": max_mem,
            "riesgo": riesgo,
        }
        return res

    def obtener_referencia(self) -> IterationData | None:
        for it in ITERACIONES_REFERENCIA.values():
            if abs(self.th_eff - it.threshold_efectivo) < 0.02:
                return it
        return None
