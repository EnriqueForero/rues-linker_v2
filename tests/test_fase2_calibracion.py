"""Tests Fase 2 — `rues-linker` v3.2.5.

Verifica:
1. `AdvancedValueSelector` con `source_quality_weights` usa pesos
   numéricos para desempate (cuando hay >1 fuente en prioridad).
2. `AdvancedValueSelector` SIN `source_quality_weights` mantiene
   comportamiento idéntico a v3.2.4 (retrocompat).
3. `OptimizedClusterer` con `max_sources_per_group=N` divide clusters
   que superan el límite.
4. `OptimizedClusterer` con `max_sources_per_group=None` (default)
   mantiene comportamiento idéntico a v3.2.4 (retrocompat).
5. `GoldenRecordGenerator` propaga `source_quality_weights` desde
   `config["profiles"][active]["source_quality_weights"]`.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.engine.clusterer import OptimizedClusterer
from record_linkage.golden.generator import GoldenRecordGeneratorV7
from record_linkage.golden.selector import AdvancedValueSelector


# ═════════════════════════════════════════════════════════════════════════
#  Tests 1-2: AdvancedValueSelector con/sin source_quality_weights
# ═════════════════════════════════════════════════════════════════════════
class TestSourceQualityWeights:
    """Verifica que los pesos numéricos influyen en desempate."""

    def _build_grupo_empate(self) -> pd.DataFrame:
        """Construye un grupo con dos fuentes empatadas en prioridad.

        Si la prioridad es {A: 0, B: 0, C: 1}, A y B empatan como "más
        prioritarias". Sin pesos, queda según consenso (puede ser cualquiera).
        Con pesos {A: 0.5, B: 0.99}, B debería ganar.
        """
        return pd.DataFrame(
            {
                "RAZON_SOCIAL": ["NOMBRE A1", "NOMBRE B1", "NOMBRE C1"],
                "SRC": ["A", "B", "C"],
            }
        )

    def test_sin_pesos_comportamiento_retrocompat(self):
        """Sin source_quality_weights, comportamiento es estable y NO usa pesos."""
        priority_map = {"A": 0, "B": 0, "C": 1}  # A y B empatan
        sel = AdvancedValueSelector(priority_map)
        grupo = self._build_grupo_empate()
        nombre = sel.select_best_name(grupo)
        # Sin pesos, se aplica _consensus_name entre A1 y B1.
        # El resultado debe ser determinista (uno de los dos).
        assert nombre in ["NOMBRE A1", "NOMBRE B1"]
        # Asegurar que C no fue elegido (es de menor prioridad)
        assert nombre != "NOMBRE C1"

    def test_con_pesos_desempate_por_peso(self):
        """Con source_quality_weights, B (peso 0.99) gana sobre A (peso 0.5)."""
        priority_map = {"A": 0, "B": 0, "C": 1}  # A y B empatan en prioridad
        weights = {"A": 0.50, "B": 0.99, "C": 0.30}
        sel = AdvancedValueSelector(priority_map, source_quality_weights=weights)
        grupo = self._build_grupo_empate()
        nombre = sel.select_best_name(grupo)
        assert nombre == "NOMBRE B1", (
            f"Con pesos {{A:0.5, B:0.99}} debió ganar B; obtenido: {nombre}"
        )

    def test_con_pesos_invertidos_gana_a(self):
        """Pesos invertidos: A (0.99) gana sobre B (0.30)."""
        priority_map = {"A": 0, "B": 0, "C": 1}
        weights = {"A": 0.99, "B": 0.30, "C": 0.10}
        sel = AdvancedValueSelector(priority_map, source_quality_weights=weights)
        grupo = self._build_grupo_empate()
        nombre = sel.select_best_name(grupo)
        assert nombre == "NOMBRE A1"

    def test_singleton_no_afectado(self):
        """Singleton retorna su único nombre sin importar pesos."""
        priority_map = {"A": 0}
        weights = {"A": 0.1}
        sel = AdvancedValueSelector(priority_map, source_quality_weights=weights)
        grupo = pd.DataFrame({"RAZON_SOCIAL": ["UNICO"], "SRC": ["A"]})
        assert sel.select_best_name(grupo) == "UNICO"

    def test_fuente_unica_no_afectado(self):
        """Si todos los registros son de la misma fuente, ignora pesos."""
        priority_map = {"A": 0}
        weights = {"A": 0.1}
        sel = AdvancedValueSelector(priority_map, source_quality_weights=weights)
        grupo = pd.DataFrame(
            {
                "RAZON_SOCIAL": ["NOMBRE X", "NOMBRE X", "NOMBRE Y"],
                "SRC": ["A", "A", "A"],
            }
        )
        # Consenso por frecuencia → "NOMBRE X" gana (aparece 2 veces)
        assert sel.select_best_name(grupo) == "NOMBRE X"


# ═════════════════════════════════════════════════════════════════════════
#  Tests 3: GoldenRecordGenerator propaga pesos desde config
# ═════════════════════════════════════════════════════════════════════════
class TestGeneratorPropagation:
    """Verifica que GoldenRecordGeneratorV7 lee source_quality_weights del config."""

    def test_propagacion_desde_profile_activo(self):
        """El generator debe extraer pesos desde profiles[active]."""
        config = {
            "profile": "test_prof",
            "profiles": {
                "test_prof": {
                    "source_quality_weights": {"RUES": 0.99, "CRM": 0.60},
                },
            },
        }
        gen = GoldenRecordGeneratorV7(["RUES", "CRM"], config=config)
        assert gen.source_quality_weights == {"RUES": 0.99, "CRM": 0.60}
        # Propagado al selector
        assert gen.value_selector.source_quality_weights == {"RUES": 0.99, "CRM": 0.60}

    def test_propagacion_fallback_top_level(self):
        """Si no está en el profile activo, busca top-level."""
        config = {
            "source_quality_weights": {"RUES": 0.99, "CRM": 0.60},
        }
        gen = GoldenRecordGeneratorV7(["RUES", "CRM"], config=config)
        assert gen.source_quality_weights == {"RUES": 0.99, "CRM": 0.60}

    def test_sin_pesos_retrocompat(self):
        """Sin pesos en config, generator funciona como antes."""
        gen = GoldenRecordGeneratorV7(["RUES", "CRM"], config={})
        assert gen.source_quality_weights == {}
        # El selector recibe None (no dict vacío) por diseño
        assert gen.value_selector.source_quality_weights == {}


# ═════════════════════════════════════════════════════════════════════════
#  Tests 4-5: max_sources_per_group en OptimizedClusterer
# ═════════════════════════════════════════════════════════════════════════
class TestMaxSourcesPerGroup:
    """Verifica el split post-clustering por límite de fuentes."""

    def _build_clusterer(self, max_sources: int | None = None) -> OptimizedClusterer:
        """Crea un clusterer con la configuración deseada."""
        profile = {
            "clustering_batch_size": 1000,
            "use_strict_clusters": False,
            "max_nit_distance": 2,
            "max_sources_per_group": max_sources,
        }
        return OptimizedClusterer(profile=profile)

    def test_default_none_no_splitting(self):
        """max_sources_per_group=None → no aplica split (retrocompat)."""
        clu = self._build_clusterer(max_sources=None)
        assert clu.max_sources_per_group is None

    def test_split_real_cluster_excede_limite(self):
        """Cluster con 5 fuentes y límite=3 debe dividirse."""
        clu = self._build_clusterer(max_sources=3)
        # df_full simula 5 registros de 5 fuentes distintas, unidos en 1 cluster
        df_full = pd.DataFrame(
            {
                "SRC": ["RUES", "SUPER", "CRM", "EXPO", "DIAN"],
                "NIT": ["111", "111", "111", "222", "222"],
            }
        )
        clusters_input = [{0, 1, 2, 3, 4}]  # un solo mega-cluster
        clusters_output = clu._split_mega_clusters(clusters_input, df_full, max_sources=3)
        # Debe haberse dividido. Sub-clusters por (SRC, NIT):
        # (RUES, 111), (SUPER, 111), (CRM, 111), (EXPO, 222), (DIAN, 222) → 5
        assert len(clusters_output) == 5

    def test_no_split_si_cumple_limite(self):
        """Cluster con 3 fuentes y límite=4 NO se divide."""
        clu = self._build_clusterer(max_sources=4)
        df_full = pd.DataFrame(
            {
                "SRC": ["RUES", "SUPER", "CRM"],
                "NIT": ["111", "111", "111"],
            }
        )
        clusters_input = [{0, 1, 2}]
        clusters_output = clu._split_mega_clusters(clusters_input, df_full, max_sources=4)
        assert len(clusters_output) == 1
        assert clusters_output[0] == {0, 1, 2}

    def test_split_preserva_dedup_intra_fuente(self):
        """Si dos registros tienen misma (SRC, NIT), quedan en el mismo sub-cluster."""
        clu = self._build_clusterer(max_sources=2)
        # 4 registros, 4 fuentes, pero 2 tienen mismo NIT en mismas fuentes
        df_full = pd.DataFrame(
            {
                "SRC": ["RUES", "RUES", "SUPER", "CRM", "EXPO"],
                "NIT": ["111", "111", "111", "111", "111"],
            }
        )
        # Un mega-cluster con los 5 nodos
        clusters_input = [{0, 1, 2, 3, 4}]
        clusters_output = clu._split_mega_clusters(clusters_input, df_full, max_sources=2)
        # Sub-clusters por (SRC, NIT):
        # (RUES, 111) → nodos 0,1 (juntos!)
        # (SUPER, 111) → nodo 2
        # (CRM, 111) → nodo 3
        # (EXPO, 111) → nodo 4
        # = 4 sub-clusters
        assert len(clusters_output) == 4
        # Verificar que 0 y 1 están juntos
        for sub in clusters_output:
            if 0 in sub:
                assert 1 in sub, "Nodos 0 y 1 (misma fuente, mismo NIT) deben quedar juntos"

    def test_max_sources_invalido_no_crashea(self):
        """max_sources=0 debe imprimir warning y no romper."""
        clu = self._build_clusterer(max_sources=0)
        df_full = pd.DataFrame({"SRC": ["A"], "NIT": ["111"]})
        clusters_input = [{0}]
        # No debe lanzar excepción
        clusters_output = clu._split_mega_clusters(clusters_input, df_full, max_sources=0)
        assert clusters_output == clusters_input


# ═════════════════════════════════════════════════════════════════════════
#  Tests 6: Limpieza de perfiles auxiliares
# ═════════════════════════════════════════════════════════════════════════
class TestPerfilesLimpios:
    """Verifica que los perfiles auxiliares ya no tienen dead code."""

    def test_produccion_estandar_sin_aggressive_gc(self):
        from record_linkage.config.profiles import PERFILES_BASE

        assert "aggressive_gc" not in PERFILES_BASE["produccion_estandar"]

    def test_produccion_exhaustiva_sin_aggressive_gc(self):
        from record_linkage.config.profiles import PERFILES_BASE

        assert "aggressive_gc" not in PERFILES_BASE["produccion_exhaustiva"]

    def test_alta_precision_recalibrado(self):
        """alta_precision debe tener los nuevos umbrales conservadores."""
        from record_linkage.config.profiles import PERFILES_BASE

        prof = PERFILES_BASE["alta_precision"]
        # v3.2.5: alineado con produccion_calibrada
        assert prof["score_threshold"] == 0.60
        assert prof["min_name_similarity"] == 0.65
        assert prof["max_nit_distance"] == 0
        assert prof["nit_empty_passes_filter"] is False
        # Sin dead code
        assert "aggressive_gc" not in prof

    def test_it7_fue_limpiado_en_sprint_0_5_0(self):
        """config_produccion_it7 fue limpiado en Sprint 0.5.0 (era retrocompat
        documental en versiones 0.3.x-0.4.x; v0.5.0 lo limpió definitivamente).

        Este test reemplaza al previo `test_it7_mantiene_dead_code_retrocompat`
        (v0.3.x-0.4.x) que verificaba lo opuesto. Ver CHANGELOG.md entrada 0.5.0.
        """
        from record_linkage.config.profiles import config_produccion_it7

        prof = config_produccion_it7["profiles"]["enterprise_scale_4_sources"]
        # Las claves DEAD que estaban en IT-7 hasta v0.4.0 fueron REMOVIDAS:
        keys_dead_removidas = [
            "aggressive_gc",
            "confidence_weights",
            "max_sources_per_group",
            "min_sources_for_golden",
            "memory_monitor_interval",
            "sqlite_cache_size",
            "commit_interval",
            "correlative_chunk_size",
            "cross_source_validation",
        ]
        for k in keys_dead_removidas:
            assert k not in prof, (
                f"Sprint 0.5.0 debería haber removido '{k}' de IT-7. "
                f"Si lo agregaste de vuelta, revisa la auditoría."
            )
        # Las claves top-level dead también fueron removidas:
        assert "validation_rules" not in config_produccion_it7
        assert "performance_settings" not in config_produccion_it7
