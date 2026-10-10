"""Ejemplos del README como tests ejecutables (F1.6, v0.9.0).

Los tres ejemplos del quickstart corren en CI: si el README miente, la suite
se pone roja. Mantener SINCRONIZADOS con la sección "Inicio rápido".
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd

import record_linkage as rl


def _silencio():
    return contextlib.redirect_stdout(io.StringIO())


def test_ejemplo_1_dedupe_en_cinco_lineas(tmp_path: Path) -> None:
    """Ejemplo 1 del README: deduplicar una tabla."""
    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "", ""],
            "RAZON_SOCIAL": [
                "ACME COLOMBIA SAS",
                "ACME COLOMBIA S.A.S.",
                "GLOBEX DE ORIENTE LTDA",
                "GLOBEX DE ORIENTE  LTDA.",
            ],
        }
    )
    with _silencio():
        res = rl.dedupe(df, carpeta_salida=tmp_path / "salidas")

    assert res.metricas["n_grupos"] == 2  # dos entidades reales
    assert set(res.correlativa.columns) >= {"ID_GRUPO", "ORIGINAL_INDEX"}
    assert "dedupe" in res.resumen()
    # F2.7: carpeta_salida= escribe la carpeta del estándar con el escritor único.
    carpeta = Path(res.manifiesto["carpeta_salida"])
    assert carpeta.parent == tmp_path / "salidas" and carpeta.name.endswith("_dedupe")
    assert (carpeta / "correlativa.parquet").is_file() and (carpeta / "manifest.json").is_file()


def test_ejemplo_2_perfiles_del_registro_unico(tmp_path: Path) -> None:
    """Ejemplo 2 del README: inspeccionar y usar perfiles por nombre."""
    perfil = rl.get_profile("deduplication_sin_nit_conservador")
    assert 0 < perfil["lsh_threshold"] < 1  # parámetros documentados y validados

    df = pd.DataFrame(
        {
            "NIT": ["", "", ""],
            "RAZON_SOCIAL": [
                "INVERSIONES DEL PACIFICO SAS",
                "INVERSIONES DEL PACIFICO S.A.S.",
                "TITAN GROUP SA",
            ],
        }
    )
    with _silencio():
        res = rl.dedupe(
            df,
            profile_sin_nit="deduplication_sin_nit_conservador",
            output_dir=str(tmp_path / "salida"),
        )
    assert res.metricas["n_grupos"] == 2
    assert res.manifiesto["parametros"]["profile_sin_nit"] == ("deduplication_sin_nit_conservador")


def test_ejemplo_3_link_dos_bases(tmp_path: Path) -> None:
    """Ejemplo 3 del README: cruzar dos bases (A↔B) con métricas de cruce."""
    df_rues = pd.DataFrame(
        {
            "NIT": ["900111222", "900333444", ""],
            "RAZON_SOCIAL": ["ACME COLOMBIA SAS", "GLOBEX LTDA", "INICIATIVA VERDE SA"],
            "CIUDAD": ["BOGOTA", "MEDELLIN", "CALI"],
        }
    )
    df_aduanas = pd.DataFrame(
        {
            "NIT": ["900111222", "", "900999888"],
            "RAZON_SOCIAL": [
                "ACME COLOMBIA S.A.S.",
                "INICIATIVA VERDE S A",
                "TITAN GROUP SAS",
            ],
            "CIUDAD": ["BOGOTA", "CALI", "BARRANQUILLA"],
        }
    )
    with _silencio():
        res = rl.link(
            df_rues,
            df_aduanas,
            nombre_a="RUES",
            nombre_b="ADUANAS",
            trusted={"RUES"},
            work_dir=str(tmp_path / "cruce"),
        )

    assert res.metricas["n_grupos_cruzados"] == 2  # ACME e INICIATIVA VERDE
    assert res.metricas["n_pares_a_b"] == 2
    assert "SRC" in res.correlativa.columns
    assert res.golden is not None and len(res.golden) == 4  # entidades únicas
    assert set(res.manifiesto["entradas"]) == {"RUES", "ADUANAS"}
