"""Tests del helper de alto nivel `linkage()` y de la API pública (v3.0.0)."""

from __future__ import annotations

import pandas as pd

import record_linkage
from record_linkage import linkage


def test_api_publica_exporta_simbolos_clave():
    """El namespace raíz debe exponer la API de alto nivel."""
    for nombre in ("linkage", "Orchestrator", "deduplicate_unified", "evaluar_pares"):
        assert hasattr(record_linkage, nombre), f"Falta {nombre} en la API pública"
    assert "linkage" in record_linkage.__all__
    assert record_linkage.SourceSpec is not None


def test_linkage_una_fuente_dedup_interna(tmp_path):
    """linkage() sobre una sola fuente deduplica registros de la misma entidad."""
    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "800555111"],
            "RAZON_SOCIAL": ["ACME COLOMBIA SAS", "ACME COLOMBIA S.A.S.", "GLOBEX LTDA"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "MEDELLIN"],
        }
    )
    res = linkage(sources={"RUES": df}, work_dir=str(tmp_path / "run"))
    assert "golden" in res
    assert "correlative" in res
    # Las dos ACME (mismo NIT) deben colapsar: 3 registros -> 2 entidades.
    assert len(res["golden"]) == 2


def test_linkage_requiere_fuentes():
    """linkage() sin fuentes debe fallar claramente."""
    import pytest

    with pytest.raises(ValueError, match="al menos una fuente"):
        linkage(sources={})


def test_linkage_crea_work_dir_temporal_si_no_se_da():
    """Si no se pasa work_dir, linkage() usa un temporal y no rompe."""
    df = pd.DataFrame({"NIT": ["900123456"], "RAZON_SOCIAL": ["ACME SAS"], "CIUDAD": ["BOGOTA"]})
    res = linkage(sources={"RUES": df})
    assert "golden" in res


def test_linkage_acepta_mapeo_y_formato_distinto_por_fuente(tmp_path):
    """CSV y TXT CP1252 convergen al esquema canónico sin cargar columnas extra."""
    from record_linkage import ColumnType, SourceSpec

    rues_path = tmp_path / "rues.csv"
    export_path = tmp_path / "export.txt"
    pd.DataFrame(
        {
            "NUMERO_IDENTIFICACION": ["900123456"],
            "RAZON_SOCIAL": ["CAFÉ COLOMBIA S.A.S."],
            "COLUMNA_PESADA": ["x" * 1_000],
        }
    ).to_csv(rues_path, index=False, encoding="cp1252")
    pd.DataFrame(
        {
            "Nit Exportador": ["900123456"],
            "Razon Social": ["CAFE COLOMBIA SAS"],
            "Año USD": ["1.234,50"],
        }
    ).to_csv(export_path, index=False, sep="\t", encoding="cp1252")

    specs = [
        SourceSpec(
            name="RUES",
            path=rues_path,
            column_mapping={
                "NIT": "NUMERO_IDENTIFICACION",
                "RAZON_SOCIAL": "RAZON_SOCIAL",
            },
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="EXPORT",
            path=export_path,
            column_mapping={
                "NIT": "Nit Exportador",
                "RAZON_SOCIAL": "Razon Social",
            },
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]

    res = linkage(
        sources=specs,
        work_dir=str(tmp_path / "run-files"),
        skip_reporting=True,
    )

    assert len(res["correlative"]) == 2
    assert set(res["ingestion_reports"]) == {"RUES", "EXPORT"}
    assert all(
        report.columns == ("NIT", "RAZON_SOCIAL") for report in res["ingestion_reports"].values()
    )
