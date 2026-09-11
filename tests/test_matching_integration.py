"""Tests del MatcherPostProcessor y su integración a linkage()."""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.matching import (
    MatcherPostProcessor,
    apply_matcher_to_linkage_result,
    default_colombia_profile,
    default_colombia_profile_conservative,
    default_colombia_profile_recall,
)


@pytest.fixture
def synthetic_data():
    """Crea fuentes sintéticas con casos conocidos."""
    sources = {
        "RUES": pd.DataFrame(
            {
                "ID_REGISTRO": ["R1", "R2", "R3"],
                "NIT": ["900111111-1", "900222222-2", "900333333-3"],
                "RAZON_SOCIAL": ["ACME COLOMBIA S.A.", "INDUSTRIA XYZ LTDA", "CONSTRUCTORA ABC"],
                "CIUDAD": ["BOGOTA", "MEDELLIN", "CALI"],
                "TELEFONO": ["3001111111", "3002222222", "3003333333"],
                "EMAIL": ["info@acme.com", "ventas@xyz.com", "contacto@abc.com"],
                "DIRECCION": ["CRA 7 # 70 - 25", "CL 50 # 30 - 10", "AV 5 # 20 - 15"],
            }
        ),
        "CRM": pd.DataFrame(
            {
                "ID_REGISTRO": ["C1", "C2", "C3"],
                "NIT": ["900111111-9", "900999999-9", "900333333-3"],  # C1 mismo NIT base que R1
                "RAZON_SOCIAL": ["ACME COLOMBIA", "OTRA EMPRESA DISTINTA", "CONSTRUCTORA ABC SAS"],
                "CIUDAD": ["BOGOTA", "BARRANQUILLA", "CALI"],
                "TELEFONO": ["3001111111", "3009999999", "3003333333"],
                "EMAIL": ["info@acme.com", "info@otra.com", "contacto@abc.com"],
                "DIRECCION": ["CARRERA 7 70 25", "CL 100 # 5 - 1", "AV 5 # 20 - 15"],
            }
        ),
    }
    return sources


class TestLinkageWithMatchingProfile:
    def test_default_no_matcher_same_as_v3_0(self, synthetic_data):
        """Sin matching_profile, comportamiento idéntico a v3.0.0."""
        result = linkage(
            sources=synthetic_data,
            col_ciudad="CIUDAD",
            extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
        )
        assert "golden" in result
        assert "correlative" in result
        assert "matcher_stats" not in result
        assert "matcher_decisions" not in result

    def test_with_matching_profile_colombia(self, synthetic_data):
        """Con matching_profile='colombia' añade refinamiento."""
        result = linkage(
            sources=synthetic_data,
            col_ciudad="CIUDAD",
            extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
            matching_profile="colombia",
            return_matcher_audit=True,
        )
        assert "matcher_stats" in result
        assert "matcher_decisions" in result
        assert isinstance(result["matcher_decisions"], pd.DataFrame)
        # Stats deben tener campos esperados
        s = result["matcher_stats"]
        assert "n_pairs_evaluated" in s
        assert "n_kept" in s
        assert "n_separated" in s
        assert s["n_kept"] + s["n_separated"] == s["n_pairs_evaluated"]

    def test_matching_profile_invalid_string_raises(self, synthetic_data):
        with pytest.raises(ValueError, match="matching_profile string desconocido"):
            linkage(
                sources=synthetic_data,
                col_ciudad="CIUDAD",
                matching_profile="invalid_name",
            )

    def test_matching_profile_invalid_type_raises(self, synthetic_data):
        with pytest.raises(TypeError, match="MatchingProfile"):
            linkage(
                sources=synthetic_data,
                col_ciudad="CIUDAD",
                matching_profile=123,
            )

    def test_three_profile_variants_load(self, synthetic_data):
        """Los 3 perfiles preset funcionan."""
        for prof_name in ["colombia", "colombia_conservative", "colombia_recall"]:
            result = linkage(
                sources=synthetic_data,
                col_ciudad="CIUDAD",
                extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
                matching_profile=prof_name,
            )
            assert len(result["correlative"]) > 0

    def test_custom_profile_instance(self, synthetic_data):
        """Aceptar instancia de MatchingProfile directamente."""
        custom = default_colombia_profile()
        custom.min_concordances_without_nit = 3  # más estricto
        result = linkage(
            sources=synthetic_data,
            col_ciudad="CIUDAD",
            extra_features=["TELEFONO", "EMAIL", "DIRECCION"],
            matching_profile=custom,
        )
        assert "correlative" in result


class TestMatcherPostProcessorUnit:
    def test_empty_clusters_no_op(self, synthetic_data):
        """Correlativa toda singletons: no debe hacer nada."""
        corr = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B", "C"],
                "ID_GRUPO": [1, 2, 3],
            }
        )
        df_src = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B", "C"],
                "NIT": ["111", "222", "333"],
                "RAZON_SOCIAL": ["X", "Y", "Z"],
                "TELEFONO": ["", "", ""],
                "EMAIL": ["", "", ""],
                "DIRECCION": ["", "", ""],
                "CIUDAD": ["BOGOTA", "CALI", "MEDELLIN"],
            }
        )
        postproc = MatcherPostProcessor(default_colombia_profile())
        out = postproc.apply(corr, df_src)
        assert out["ID_GRUPO"].nunique() == 3
        assert postproc.last_stats["n_pairs_evaluated"] == 0

    def test_separates_bad_cluster(self):
        """Cluster con NITs distintos debe separarse (veto NIT)."""
        corr = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B"],
                "ID_GRUPO": [1, 1],  # mismo cluster pero NITs distintos
            }
        )
        df_src = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B"],
                "NIT": ["900111111-1", "800222222-2"],  # NITs CLARAMENTE distintos
                "RAZON_SOCIAL": ["ACME S.A.", "ACME S.A."],
                "TELEFONO": ["3001111111", "3002222222"],
                "EMAIL": ["", ""],
                "DIRECCION": ["", ""],
                "CIUDAD": ["BOGOTA", "BOGOTA"],
            }
        )
        postproc = MatcherPostProcessor(default_colombia_profile())
        out = postproc.apply(corr, df_src)
        # Veto NIT → debe separarse en 2 clusters
        assert out["ID_GRUPO"].nunique() == 2

    def test_keeps_good_cluster(self):
        """Cluster con mismo NIT (DV diferente) debe mantenerse."""
        corr = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B"],
                "ID_GRUPO": [1, 1],
            }
        )
        df_src = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B"],
                "NIT": ["900111111-1", "900111111-7"],  # mismo base, DV distinto
                "RAZON_SOCIAL": ["ACME S.A.", "ACME COLOMBIA"],
                "TELEFONO": ["3001111111", "3001111111"],
                "EMAIL": ["a@acme.com", "b@acme.com"],
                "DIRECCION": ["CRA 7", "CARRERA 7"],
                "CIUDAD": ["BOGOTA", "BOGOTA"],
            }
        )
        postproc = MatcherPostProcessor(default_colombia_profile())
        out = postproc.apply(corr, df_src)
        # Mismo NIT base + todo lo demás coincide → cluster sigue unido
        assert out["ID_GRUPO"].nunique() == 1

    def test_apply_helper_function(self, synthetic_data):
        """El helper apply_matcher_to_linkage_result funciona."""
        result = linkage(sources=synthetic_data, col_ciudad="CIUDAD")
        combined = pd.concat(synthetic_data.values(), ignore_index=True)
        refined = apply_matcher_to_linkage_result(result, combined)
        assert "golden" in refined
        assert "correlative" in refined
        assert "matcher_stats" in refined
        assert "matcher_decisions" in refined

    def test_stats_populated(self):
        corr = pd.DataFrame({"ID_REGISTRO": ["A", "B"], "ID_GRUPO": [1, 1]})
        df_src = pd.DataFrame(
            {
                "ID_REGISTRO": ["A", "B"],
                "NIT": ["900111111-1", "900111111-7"],
                "RAZON_SOCIAL": ["ACME", "ACME"],
                "TELEFONO": ["", ""],
                "EMAIL": ["", ""],
                "DIRECCION": ["", ""],
                "CIUDAD": ["BOGOTA", "BOGOTA"],
            }
        )
        postproc = MatcherPostProcessor(default_colombia_profile(), verbose=False)
        postproc.apply(corr, df_src)
        s = postproc.last_stats
        assert s["n_clusters_before"] == 1
        assert s["n_pairs_evaluated"] == 1
        assert "elapsed_sec" in s
        assert "matcher_stats" in s

    def test_budget_falla_antes_de_materializar_pares_cuadraticos(self):
        corr = pd.DataFrame(
            {
                "ID_REGISTRO": [f"R{i}" for i in range(2_000)],
                "ID_GRUPO": 1,
            }
        )
        src = corr.assign(NIT="", RAZON_SOCIAL="ACME").drop(columns="ID_GRUPO")
        postproc = MatcherPostProcessor(
            default_colombia_profile(),
            max_pairs=10_000,
            allow_missing_optional=True,
        )

        with pytest.raises(ValueError, match="1,999,000"):
            postproc.apply(corr, src)


class TestExportsFromRoot:
    """La auditoría externa señaló: 'ni siquiera está exportada en el namespace raíz'."""

    def test_matcher_post_processor_exported_from_root(self):
        from record_linkage import MatcherPostProcessor as MPP

        assert MPP is not None

    def test_matching_profile_exported_from_root(self):
        from record_linkage import MatchingProfile as MP

        assert MP is not None

    def test_default_profile_exported_from_root(self):
        from record_linkage import default_colombia_profile as dcp

        assert dcp is not None
        p = dcp()
        assert p.name == "default_colombia_balanced"

    def test_three_preset_profiles_exported(self):
        from record_linkage.matching import (
            default_colombia_profile,
            default_colombia_profile_conservative,
            default_colombia_profile_recall,
        )

        balanced = default_colombia_profile()
        conservative = default_colombia_profile_conservative()
        recall = default_colombia_profile_recall()
        # Distintos K
        assert balanced.min_concordances_without_nit == 1
        assert balanced.score_threshold == 0.50
        assert conservative.min_concordances_without_nit == 2
        assert recall.min_concordances_without_nit == 1
        assert recall.score_threshold == 0.40


class TestVersionBumped:
    def test_version_coincide_con_metadata(self):
        """La versión expuesta coincide con la del paquete instalado y es semver.

        Se valida contra la metadata real (no un literal hardcodeado que hay
        que editar en cada release), de modo que el test no se rompe al subir
        de versión.
        """
        import re
        from importlib.metadata import version

        import record_linkage

        assert re.match(r"^\d+\.\d+\.\d+$", record_linkage.__version__), (
            f"__version__ no es semver: {record_linkage.__version__}"
        )
        assert record_linkage.__version__ == version("rues-linker")
