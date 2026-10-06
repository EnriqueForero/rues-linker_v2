"""F1.7 · El resumen ejecutivo se alimenta de la verdad, no de claves ausentes.

Antes de F1.7 `reporting/reports.py` leía ``candidates_found``, ``pairs_scored``
y ``max_memory_gb``, claves que ``Orchestrator._build_metrics`` nunca producía:
el Excel mostraba «Candidatos 0», «Pares evaluados 0» y «Memoria 0.0 GB» en
toda corrida. Aquí se corre ``linkage()`` de verdad sobre empresas inventadas y
se exige que lo que muestra el resumen coincida con los conteos de las bases
SQLite de L2/L3 y con el pico de RSS medido por fase.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.evaluation.banco import _contar_filas_sqlite
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.reporting.reports import ReportGenerator

# Grafías por empresa: dos con NIT (unión por identificador) y una sin NIT
# (candidata solo por nombre). Así L2 y L3 tienen trabajo real.
_GRAFIAS = ("{nombre} SAS", "{nombre} S.A.S.", "{nombre} S A S")
_EMPRESAS = (
    "COMERCIALIZADORA ANDINA DE PRUEBA",
    "TEXTILES DEL EJEMPLO",
    "LOGISTICA FICTICIA DEL CARIBE",
    "AGROINDUSTRIAS INVENTADAS",
    "SOLUCIONES IMAGINARIAS",
    "DISTRIBUIDORA DE MENTIRA",
    "CAFES SUPUESTOS DEL HUILA",
    "METALMECANICA HIPOTETICA",
    "SERVICIOS IRREALES",
    "ALIMENTOS SIMULADOS",
    "CONSTRUCTORA DE PEGA",
    "FLORES DE MUESTRA",
    "QUIMICOS DE LABORATORIO FALSO",
    "TRANSPORTES DE ENSAYO",
    "EDITORIAL DE JUGUETE",
    "MADERAS DE FANTASIA",
    "CALZADO DE FABULA",
    "PLASTICOS DE BROMA",
    "ENERGIA DE CUENTO",
    "TURISMO DE FICCION",
)


def _conjunto_sintetico() -> pd.DataFrame:
    """60 filas = 20 empresas inventadas x 3 grafías; sin datos licenciados."""
    filas = []
    for i, nombre in enumerate(_EMPRESAS):
        nit = f"9009{i:05d}"
        for j, grafia in enumerate(_GRAFIAS):
            filas.append((nit if j < 2 else "", grafia.format(nombre=nombre), "BOGOTA"))
    return pd.DataFrame(filas, columns=["NIT", "RAZON_SOCIAL", "CIUDAD"])


@pytest.fixture(scope="module")
def corrida(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Una sola corrida con reportes; captura las métricas que L6 entrega a los reportes."""
    dir_trabajo = tmp_path_factory.mktemp("f1_7") / "corrida"
    capturadas: list[dict[str, Any]] = []
    original = Orchestrator._build_metrics

    def espia(self: Orchestrator, golden_df: pd.DataFrame, correl_df: pd.DataFrame) -> dict:
        metricas = original(self, golden_df, correl_df)
        capturadas.append(metricas)
        return metricas

    parche = pytest.MonkeyPatch()
    parche.setattr(Orchestrator, "_build_metrics", espia)
    try:
        resultado = linkage(
            sources={"FUENTE_A": _conjunto_sintetico()},
            work_dir=str(dir_trabajo),
            skip_reporting=False,
        )
    finally:
        parche.undo()

    assert len(capturadas) == 1, "L6 debe construir las métricas exactamente una vez"
    archivos = [Path(p) for p in resultado["report_files"]]
    excel = [p for p in archivos if p.name == "reporte_resumen_ejecutivo.xlsx"]
    assert len(excel) == 1, f"Falta reporte_resumen_ejecutivo.xlsx entre {archivos}"
    resumen = pd.read_excel(excel[0]).set_index("Métrica")["Valor"]
    return {"dir": dir_trabajo, "metricas": capturadas[0], "resumen": resumen}


def test_candidatos_y_pares_evaluados_coinciden_con_sqlite(corrida: dict[str, Any]) -> None:
    dir_trabajo: Path = corrida["dir"]
    candidatos = _contar_filas_sqlite(
        dir_trabajo / "L2_lsh_candidates" / "candidates.db", "candidate_pairs"
    )
    pares = _contar_filas_sqlite(dir_trabajo / "L3_scoring" / "scored.db", "scored_pairs")
    assert candidatos is not None and candidatos > 0, "el conjunto debe generar candidatos"
    assert pares is not None and pares > 0, "el conjunto debe generar pares puntuados"

    metricas = corrida["metricas"]
    assert metricas["candidatos"] == candidatos
    assert metricas["pares_puntuados"] == pares

    resumen = corrida["resumen"]
    assert resumen["Candidatos Encontrados"] == f"{candidatos:,}"
    assert resumen["Pares Evaluados"] == f"{pares:,}"


def test_memoria_pico_es_el_maximo_del_rss_por_fase(corrida: dict[str, Any]) -> None:
    metricas = corrida["metricas"]
    por_fase: dict[str, float] = metricas["peak_rss_mib_by_phase"]
    assert por_fase, "L1..L5 deben haber registrado su RSS pico"
    assert metricas["rss_pico_mib"] > 0
    assert metricas["rss_pico_mib"] == max(por_fase.values())

    # El manifiesto guarda el mismo pico por fase (redondeado a 3 decimales);
    # L6 aún no estaba cerrada cuando se construyeron las métricas.
    manifiesto = json.loads((corrida["dir"] / "manifest.json").read_text(encoding="utf-8"))
    picos_manifiesto = {
        fase: datos["meta"]["peak_rss_mib"]
        for fase, datos in manifiesto.items()
        if fase in por_fase and isinstance(datos, dict) and "meta" in datos
    }
    assert picos_manifiesto == {fase: round(v, 3) for fase, v in por_fase.items()}

    valor = corrida["resumen"]["Memoria Máxima"]
    coincidencia = re.fullmatch(r"([\d.]+) GiB", str(valor))
    assert coincidencia, f"formato inesperado: {valor!r}"
    assert float(coincidencia.group(1)) > 0
    assert float(coincidencia.group(1)) == round(metricas["rss_pico_mib"] / 1024, 2)


def test_sin_bases_en_disco_el_resumen_no_inventa_ceros() -> None:
    """Si L6 no encuentra candidates.db/scored.db, dice N/A; un 0 sería mentira."""
    orquestador = object.__new__(Orchestrator)
    orquestador._start_time = None
    orquestador._phase_times = {}
    orquestador._phase_peak_rss_mib = {}
    datos = pd.DataFrame({"ID_GRUPO": [1]})

    metricas = orquestador._build_metrics(datos, datos)

    assert metricas["candidatos"] is None
    assert metricas["pares_puntuados"] is None
    assert metricas["rss_pico_mib"] is None

    generador = ReportGenerator(correlative_data=datos, golden_records_data=datos, metrics=metricas)
    resumen = generador._generate_executive_summary().set_index("Métrica")["Valor"]
    assert resumen["Candidatos Encontrados"] == "N/A"
    assert resumen["Pares Evaluados"] == "N/A"
    assert "ERROR" not in resumen.index
