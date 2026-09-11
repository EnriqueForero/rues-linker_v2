"""Suite de tests para record_linkage.matching."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching import (
    DECISION_BELOW_K,
    DECISION_BELOW_T,
    DECISION_MATCH,
    DECISION_VETO,
    AddressTokenSet,
    CityNormalizedEqual,
    EmailDomainLocal,
    ExactWithDV,
    JaroWinklerSigned,
    MatchingProfile,
    PhoneLastDigits,
    TokenSetSigned,
    VariableMatcher,
    VariableSpec,
    default_colombia_profile,
    default_international_profile,
)

# =============================================================================
# Comparadores: tests unitarios
# =============================================================================


class TestExactWithDV:
    def test_exact_match(self):
        cmp = ExactWithDV()
        out = cmp.compare(np.array(["900123456-1"]), np.array(["900.123.456-1"]))
        assert out[0] == 1.0

    def test_dv_tolerance(self):
        """Mismos 9 dígitos base, DV distinto → debe matchear."""
        cmp = ExactWithDV()
        out = cmp.compare(np.array(["900123456-1"]), np.array(["900123456-7"]))
        assert out[0] == 1.0

    def test_mismatch(self):
        cmp = ExactWithDV()
        out = cmp.compare(np.array(["900123456-1"]), np.array(["900123457-1"]))
        assert out[0] == -1.0

    def test_null_returns_zero(self):
        cmp = ExactWithDV()
        out = cmp.compare(np.array(["900123456-1"]), np.array([""]))
        assert out[0] == 0.0

    def test_short_invalid(self):
        """NITs muy cortos no son válidos."""
        cmp = ExactWithDV()
        out = cmp.compare(np.array(["123"]), np.array(["123"]))
        assert out[0] == 0.0  # validez requiere >=8 dígitos

    def test_vectorized_batch(self):
        cmp = ExactWithDV()
        n = 1000
        left = np.array([f"90012{i:04d}" for i in range(n)])
        right = np.array([f"90012{i:04d}" for i in range(n)])
        out = cmp.compare(left, right)
        assert len(out) == n
        assert (out == 1.0).all()


class TestPhoneLastDigits:
    def test_match_with_country_prefix(self):
        cmp = PhoneLastDigits(n=7)
        out = cmp.compare(np.array(["+57 301 234 5678"]), np.array(["3012345678"]))
        assert out[0] == 1.0

    def test_mismatch(self):
        cmp = PhoneLastDigits(n=7)
        out = cmp.compare(np.array(["3012345678"]), np.array(["3019876543"]))
        assert out[0] == -1.0

    def test_too_short_invalid(self):
        cmp = PhoneLastDigits(n=7)
        out = cmp.compare(np.array(["12345"]), np.array(["12345"]))
        assert out[0] == 0.0

    def test_validation_range(self):
        with pytest.raises(ValueError):
            PhoneLastDigits(n=2)
        with pytest.raises(ValueError):
            PhoneLastDigits(n=20)


class TestEmailDomainLocal:
    def test_full_match(self):
        cmp = EmailDomainLocal()
        out = cmp.compare(np.array(["juan@empresa.com"]), np.array(["juan@empresa.com"]))
        # Mismo dominio corporativo + mismo local = casi máximo
        assert out[0] > 0.95

    def test_corporate_domain_same_local_similar(self):
        cmp = EmailDomainLocal()
        out = cmp.compare(np.array(["jp@empresa.com"]), np.array(["juan.perez@empresa.com"]))
        # Mismo dominio + local similar
        assert 0.3 < out[0] < 0.9

    def test_free_email_same_domain_low_evidence(self):
        cmp = EmailDomainLocal()
        out_corp = cmp.compare(np.array(["x@empresa.com"]), np.array(["x@empresa.com"]))
        out_free = cmp.compare(np.array(["x@gmail.com"]), np.array(["x@gmail.com"]))
        # Mismo local-part en gmail debe valer MENOS que en dominio corporativo
        assert out_corp[0] > out_free[0]

    def test_different_domains_penalty(self):
        cmp = EmailDomainLocal()
        out = cmp.compare(np.array(["jp@empresa.com"]), np.array(["jp@otra.com"]))
        # Mismo local en dominios distintos → penalización
        assert out[0] < 0.0

    def test_invalid_returns_zero(self):
        cmp = EmailDomainLocal()
        out = cmp.compare(np.array(["no_es_email"]), np.array(["juan@empresa.com"]))
        assert out[0] == 0.0


class TestJaroWinklerSigned:
    def test_identical(self):
        cmp = JaroWinklerSigned()
        out = cmp.compare(np.array(["ACME CORP"]), np.array(["ACME CORP"]))
        assert out[0] == pytest.approx(1.0, abs=1e-6)

    def test_completely_different(self):
        cmp = JaroWinklerSigned()
        out = cmp.compare(np.array(["AAAA"]), np.array(["ZZZZ"]))
        assert out[0] < 0

    def test_signed_range(self):
        cmp = JaroWinklerSigned()
        n = 100
        left = np.array([f"NAME_{i}" for i in range(n)])
        right = np.array([f"OTHER_{i}" for i in range(n)])
        out = cmp.compare(left, right)
        assert (out >= -1.0).all() and (out <= 1.0).all()


class TestAddressTokenSet:
    def test_abbreviation_handling(self):
        cmp = AddressTokenSet()
        out = cmp.compare(np.array(["CRA 7 # 70 - 25"]), np.array(["CARRERA 7 70 25"]))
        # Tras normalizar CRA→CARRERA y # → espacio, deben quedar muy similares
        assert out[0] > 0.7


class TestCityNormalizedEqual:
    def test_accent_insensitive(self):
        cmp = CityNormalizedEqual()
        out = cmp.compare(np.array(["BOGOTÁ"]), np.array(["BOGOTA"]))
        assert out[0] == 1.0

    def test_case_insensitive(self):
        cmp = CityNormalizedEqual()
        out = cmp.compare(np.array(["bogota"]), np.array(["BOGOTA"]))
        assert out[0] == 1.0


# =============================================================================
# VariableSpec / MatchingProfile
# =============================================================================


class TestVariableSpec:
    def test_default_veto_threshold_signed(self):
        spec = VariableSpec("NIT", ExactWithDV(), vetoes_mismatch=True)
        assert spec.veto_threshold == -0.5  # default para signed

    def test_default_veto_threshold_unsigned(self):
        from record_linkage.matching import ExactOrZero

        spec = VariableSpec("FLAG", ExactOrZero(), vetoes_mismatch=True)
        assert spec.veto_threshold == 0.0  # default para unsigned

    def test_weight_validation(self):
        with pytest.raises(ValueError):
            VariableSpec("X", ExactWithDV(), weight=-1.0)

    def test_concordance_threshold_validation(self):
        with pytest.raises(ValueError):
            VariableSpec("X", ExactWithDV(), concordance_threshold=1.5)


class TestMatchingProfile:
    def test_empty_variables_fails(self):
        with pytest.raises(ValueError):
            MatchingProfile(variables=[])

    def test_duplicate_names_fails(self):
        with pytest.raises(ValueError):
            MatchingProfile(
                variables=[
                    VariableSpec("X", ExactWithDV()),
                    VariableSpec("X", JaroWinklerSigned()),
                ]
            )

    def test_weights_normalized(self):
        profile = MatchingProfile(
            variables=[
                VariableSpec("A", ExactWithDV(), weight=2.0),
                VariableSpec("B", JaroWinklerSigned(), weight=8.0),
            ]
        )
        # Suma debe ser 1
        s = sum(profile.normalized_weights.values())
        assert s == pytest.approx(1.0, abs=1e-6)
        assert profile.normalized_weights["A"] == pytest.approx(0.2)
        assert profile.normalized_weights["B"] == pytest.approx(0.8)

    def test_to_dict_is_serializable(self):
        profile = default_colombia_profile()
        d = profile.to_dict()
        import json

        s = json.dumps(d)
        assert len(s) > 100


# =============================================================================
# VariableMatcher end-to-end
# =============================================================================


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {
            "ID_REGISTRO": ["R1", "R2", "R3", "R4", "R5"],
            "NIT": ["900123456-1", "900123456-7", "800555111-3", "", ""],
            "RAZON_SOCIAL": [
                "ACME COLOMBIA S.A.",
                "ACME COLOMBIA",
                "OTRA EMPRESA SAS",
                "SHINHWANG PRIMEINC",
                "SHINHWANG TEXTILE CO. LTD",
            ],
            "TELEFONO": ["3012345678", "3012345678", "3019999999", "", ""],
            "EMAIL": ["a@acme.com", "a@acme.com", "b@otra.com", "", ""],
            "DIRECCION": ["CRA 7 # 70 - 25", "CARRERA 7 70 25", "CL 100 # 5 - 1", "", ""],
            "CIUDAD": ["BOGOTA", "BOGOTA", "MEDELLIN", "INCHON", "PUSAN"],
        }
    ).set_index("ID_REGISTRO")


class TestVariableMatcherEndToEnd:
    def test_match_dv_tolerance(self, sample_df):
        """R1 y R2: mismo NIT con DV distinto + todo lo demás concuerda."""
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R1"], "id_right": ["R2"]})
        result = matcher.score_pairs(pairs, sample_df)
        assert result.iloc[0]["decision"] == DECISION_MATCH

    def test_non_match_different_company(self, sample_df):
        """R1 y R3: NITs distintos → debe VETOAR."""
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R1"], "id_right": ["R3"]})
        result = matcher.score_pairs(pairs, sample_df)
        assert result.iloc[0]["decision"] == DECISION_VETO
        assert result.iloc[0]["vetoed"]

    def test_korean_fp_case_rejected(self, sample_df):
        """R4 y R5: SHINHWANG PRIMEINC vs SHINHWANG TEXTILE, diferentes ciudades.
        Este es el modo de falla principal del paquete actual."""
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R4"], "id_right": ["R5"]})
        result = matcher.score_pairs(pairs, sample_df)
        # NO debe ser MATCH (es FP del paquete actual)
        assert result.iloc[0]["decision"] != DECISION_MATCH

    def test_run_stats_populated(self, sample_df):
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R1", "R1"], "id_right": ["R2", "R3"]})
        matcher.score_pairs(pairs, sample_df)
        stats = matcher.last_run_stats
        assert stats["n_pairs"] == 2
        assert stats["pairs_per_sec"] > 0
        assert "timings_per_variable_sec" in stats
        assert len(stats["timings_per_variable_sec"]) == 6  # 6 vars en default

    def test_per_variable_scores_returned(self, sample_df):
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R1"], "id_right": ["R2"]})
        result = matcher.score_pairs(pairs, sample_df)
        # Debe haber columnas score_X y conc_X para cada variable
        for var in ["NIT", "RAZON_SOCIAL", "TELEFONO", "EMAIL", "DIRECCION", "CIUDAD"]:
            assert f"score_{var}" in result.columns
            assert f"conc_{var}" in result.columns

    def test_empty_input(self, sample_df):
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": [], "id_right": []})
        result = matcher.score_pairs(pairs, sample_df)
        assert len(result) == 0

    def test_missing_column_raises(self, sample_df):
        """Si una variable del profile no está en el df, debe levantar."""
        # Borrar columna EMAIL para forzar error
        df_no_email = sample_df.drop(columns=["EMAIL"])
        matcher = VariableMatcher(default_colombia_profile())
        pairs = pd.DataFrame({"id_left": ["R1"], "id_right": ["R2"]})
        with pytest.raises(KeyError, match="EMAIL"):
            matcher.score_pairs(pairs, df_no_email)

    def test_missing_optional_can_be_neutral_when_explicit(self, sample_df):
        """La fachada flexible puede neutralizar variables opcionales ausentes."""
        df_min = sample_df[["NIT", "RAZON_SOCIAL"]]
        matcher = VariableMatcher(default_colombia_profile(), allow_missing_optional=True)
        pairs = pd.DataFrame({"id_left": ["R1"], "id_right": ["R2"]})

        result = matcher.score_pairs(pairs, df_min)

        assert len(result) == 1
        assert matcher.last_run_stats["missing_optional_variables"] == [
            "TELEFONO",
            "EMAIL",
            "DIRECCION",
            "CIUDAD",
        ]


class TestDefaultProfiles:
    def test_colombia_profile_loads(self):
        p = default_colombia_profile()
        assert len(p.variables) == 6
        assert any(v.name == "NIT" and v.vetoes_mismatch for v in p.variables)

    def test_international_profile_loads(self):
        p = default_international_profile()
        assert len(p.variables) == 4
        assert any(v.name == "COUNTRY" and v.vetoes_mismatch for v in p.variables)

    def test_calibration_metadata_present(self):
        p = default_colombia_profile()
        assert p.calibration_source != "uncalibrated"
        assert len(p.calibration_notes) > 50  # explicación sustancial
