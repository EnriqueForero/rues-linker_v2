"""Tests para NITPrescreener (Sprint 0.8.0)."""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.engine.lsh import (
    NITPrescreener,
    PrescreenResult,
    prescreen_and_split,
)


# ─────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def df_basic():
    """DataFrame con 3 grupos NIT + 2 registros únicos."""
    return pd.DataFrame(
        {
            "RAZON_SOCIAL": ["A", "B", "C", "D", "E", "F", "G"],
            "NIT_BASE": ["111", "111", "222", "222", "333", "444", ""],
            "NIT_VALID": [True, True, True, True, True, True, False],
            "SRC": ["RUES", "CRM", "RUES", "CRM", "RUES", "RUES", "CRM"],
        }
    )


# ─────────────────────────────────────────────────────────────────────
#  Tests 1: Comportamiento básico
# ─────────────────────────────────────────────────────────────────────
class TestBasicPartitioning:
    def test_partition_devuelve_prescreen_result(self, df_basic):
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        assert isinstance(result, PrescreenResult)

    def test_identifica_pares_nit_exact(self, df_basic):
        """NIT '111' tiene 2 registros (A,B) → 1 par."""
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        # NIT 111: A(0), B(1) → par (0,1)
        # NIT 222: C(2), D(3) → par (2,3)
        # Total: 2 pares
        assert len(result.exact_match_pairs) == 2
        assert (0, 1) in result.exact_match_pairs
        assert (2, 3) in result.exact_match_pairs

    def test_residual_contiene_singletons(self, df_basic):
        """Records con NIT único (E, F) y vacío (G) van al residual."""
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        # Residual: E (NIT 333 único), F (NIT 444 único), G (NIT vacío)
        assert len(result.residual_df) == 3
        assert set(result.residual_df.index) == {4, 5, 6}

    def test_nit_vacio_va_a_residual(self, df_basic):
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        # G tiene NIT vacío
        assert 6 in result.residual_df.index
        assert result.excluded_nits_empty == 1

    def test_metrics_basicas(self, df_basic):
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        assert result.n_input == 7
        assert result.n_residual == 3
        assert result.n_exact_pairs == 2
        assert result.n_groups == 2
        assert 0 < result.reduction_pct < 1.0


# ─────────────────────────────────────────────────────────────────────
#  Tests 2: cross_source_only
# ─────────────────────────────────────────────────────────────────────
class TestCrossSourceOnly:
    def test_solo_genera_pares_inter_fuente(self):
        """Con cross_source_only=True, RUES-RUES no genera par."""
        df = pd.DataFrame(
            {
                "NIT_BASE": ["111", "111", "111"],
                "NIT_VALID": [True, True, True],
                "SRC": ["RUES", "RUES", "CRM"],
            }
        )
        ps = NITPrescreener(cross_source_only=True)
        result = ps.partition(df)
        # Pares: (0,2) RUES-CRM, (1,2) RUES-CRM. NO (0,1) RUES-RUES
        assert (0, 2) in result.exact_match_pairs
        assert (1, 2) in result.exact_match_pairs
        assert (0, 1) not in result.exact_match_pairs

    def test_sin_cross_source_genera_todos(self):
        df = pd.DataFrame(
            {
                "NIT_BASE": ["111", "111", "111"],
                "NIT_VALID": [True, True, True],
                "SRC": ["RUES", "RUES", "CRM"],
            }
        )
        ps = NITPrescreener(cross_source_only=False)
        result = ps.partition(df)
        assert len(result.exact_match_pairs) == 3  # C(3,2) = 3


# ─────────────────────────────────────────────────────────────────────
#  Tests 3: trusted_unique_sources
# ─────────────────────────────────────────────────────────────────────
class TestTrustedSources:
    def test_no_pares_intra_trusted(self):
        """RUES-RUES NO genera par si RUES está en trusted."""
        df = pd.DataFrame(
            {
                "NIT_BASE": ["111", "111", "111", "111"],
                "NIT_VALID": [True, True, True, True],
                "SRC": ["RUES", "RUES", "CRM", "SUPERSOCIEDADES"],
            }
        )
        ps = NITPrescreener(trusted_unique_sources={"RUES", "SUPERSOCIEDADES"})
        result = ps.partition(df)
        # NO debe estar (0,1) [RUES-RUES] porque RUES es trusted
        assert (0, 1) not in result.exact_match_pairs
        # SÍ pares cross-source:
        assert (0, 2) in result.exact_match_pairs  # RUES-CRM
        assert (0, 3) in result.exact_match_pairs  # RUES-SUPER (cross trusted OK)


# ─────────────────────────────────────────────────────────────────────
#  Tests 4: max_group_size (NITs sospechosos)
# ─────────────────────────────────────────────────────────────────────
class TestMaxGroupSize:
    def test_grupo_grande_descartado(self):
        """NIT con >max_group_size registros va al residual."""
        df = pd.DataFrame(
            {
                "NIT_BASE": ["999"] * 10,
                "NIT_VALID": [True] * 10,
                "SRC": ["RUES"] * 10,
            }
        )
        ps = NITPrescreener(max_group_size=5)
        result = ps.partition(df)
        # NIT '999' tiene 10 registros > max_group_size=5 → descartado
        assert len(result.exact_match_pairs) == 0
        assert len(result.residual_df) == 10
        assert result.metadata["n_oversize_groups"] == 1


# ─────────────────────────────────────────────────────────────────────
#  Tests 5: Edge cases
# ─────────────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_df_vacio(self):
        df = pd.DataFrame({"NIT_BASE": [], "NIT_VALID": [], "SRC": []})
        ps = NITPrescreener()
        result = ps.partition(df)
        assert result.n_input == 0
        assert result.n_residual == 0
        assert result.n_exact_pairs == 0

    def test_sin_columna_nit_devuelve_residual_completo(self):
        """Si falta nit_column, devuelve df completo como residual."""
        df = pd.DataFrame({"RAZON_SOCIAL": ["A", "B"]})
        ps = NITPrescreener(nit_column="NIT_BASE")
        result = ps.partition(df)
        assert result.n_residual == 2
        assert result.metadata.get("reason") == "nit_column_missing"

    def test_min_group_size_invalido(self):
        with pytest.raises(ValueError, match="min_group_size"):
            NITPrescreener(min_group_size=1)

    def test_max_menor_que_min(self):
        with pytest.raises(ValueError, match="max_group_size"):
            NITPrescreener(min_group_size=5, max_group_size=2)

    def test_pares_ordenados_i_menor_j(self, df_basic):
        """Todos los pares deben tener i < j."""
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        for i, j in result.exact_match_pairs:
            assert i < j

    def test_atajo_funcional_prescreen_and_split(self, df_basic):
        result = prescreen_and_split(df_basic)
        assert isinstance(result, PrescreenResult)
        assert result.n_exact_pairs == 2


# ─────────────────────────────────────────────────────────────────────
#  Tests 6: Idempotencia + determinismo
# ─────────────────────────────────────────────────────────────────────
class TestDeterminism:
    def test_corrida_repetida_mismo_resultado(self, df_basic):
        ps = NITPrescreener()
        r1 = ps.partition(df_basic)
        r2 = ps.partition(df_basic)
        assert r1.exact_match_pairs == r2.exact_match_pairs
        assert r1.n_residual == r2.n_residual

    def test_speedup_estimate_es_consistente(self, df_basic):
        ps = NITPrescreener()
        result = ps.partition(df_basic)
        # 7 input → 3 residual → speedup = 7/3 ≈ 2.33
        assert abs(result.speedup_estimate_lsh - 7 / 3) < 0.01
