"""Los instrumentos de la Fase 2 corren de punta a punta sobre una corrida pequeña de ``linkage()``.

``scripts/recall_bloqueo.py`` mide el recall de pares del bloqueo contra ``ID_GROUP``; ``scripts/anatomia_fp.py``
disecciona los pares de L3 por régimen, NIT y vía de candidatos. Los dos leen el directorio de trabajo del
orquestador, así que la prueba lo produce con el perfil de producción y verifica que salen cifras coherentes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from cargar_script import cargar_script

from record_linkage import linkage


@pytest.fixture(scope="module")
def trabajo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Una corrida con 8 registros, 3 entidades, dos regímenes y un negativo diseñado."""
    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "", "", "800555111", "800555111", "", ""],
            "RAZON_SOCIAL": [
                "KRONOS VALDORIA SAS",
                "KRONOS VALDORIA S.A.S.",
                "KRONOS VALDORIA",
                "TRIVENTA ALIMENTOS",
                "ZELVORA LTDA",
                "ZELVORA LIMITADA",
                "PELUVIKA TEXTIL",
                "PELUVIKA TEXTIL SAS",
            ],
            "CIUDAD": ["BOGOTA"] * 8,
            "ID_GROUP": ["G1", "G1", "G1", "G2", "G3", "G3", "G4", "G4"],
            "REGIMEN": [
                "CON_ID",
                "CON_ID",
                "SIN_ID",
                "SIN_ID",
                "CON_ID",
                "CON_ID",
                "SIN_ID",
                "SIN_ID",
            ],
        }
    )
    carpeta = tmp_path_factory.mktemp("instrumentos")
    linkage(sources={"CRM": df}, work_dir=str(carpeta), skip_reporting=True)
    assert (carpeta / "L2_lsh_candidates" / "candidates.db").is_file()
    assert (carpeta / "L3_scoring" / "scored.db").is_file()
    return carpeta


def test_recall_bloqueo_mide_los_pares_verdaderos_por_regimen(
    trabajo: Path, tmp_path: Path
) -> None:
    recall_bloqueo = cargar_script("recall_bloqueo")
    salida = tmp_path / "recall.json"
    assert recall_bloqueo.main(["--trabajo", str(trabajo), "--salida", str(salida)]) == 0
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["registros"] == 8
    # G1 (3 filas) = 3 pares, G3 = 1, G4 = 1: cinco pares verdaderos; el híbrido los propone todos.
    assert datos["pares_verdaderos"] == 5
    assert datos["recall_pares"] == 1.0
    assert set(datos["por_regimen"]) >= {"CON_ID", "SIN_ID"}
    assert datos["candidatos"] >= 5


def test_anatomia_fp_lee_la_corrida_y_atribuye_por_via(
    trabajo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    anatomia = cargar_script("anatomia_fp")
    assert anatomia.main(["--trabajo", str(trabajo), "--hibrido", "--ejemplos", "2"]) == 0
    texto = capsys.readouterr().out
    assert "pares L3=" in texto and "Recall de PARES en L3" in texto
    assert "Atribución por vía de candidatos" in texto and "verdad_cubierta=" in texto
    # Ningún par de L3 cruza entidades distintas en esta base: FP=0.
    assert "FP=0" in texto
