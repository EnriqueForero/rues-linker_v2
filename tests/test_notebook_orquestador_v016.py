"""Contrato estructural del orquestador configurable de producción.

Las aserciones de versión siguen a record_linkage.__version__: un bump
no debe requerir editar el gate, pero un notebook desalineado sí debe fallar.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nbformat
import pandas as pd

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "06_orquestador_configurable.ipynb"


def _load_notebook() -> dict[str, Any]:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _source(cell: dict[str, Any]) -> str:
    value = cell.get("source", "")
    return "".join(value) if isinstance(value, list) else str(value)


def _code_cells(notebook: dict[str, Any]) -> list[dict[str, Any]]:
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_notebook_es_nbformat_45_limpio_y_python_valido() -> None:
    notebook = _load_notebook()
    nbformat.validate(notebook)

    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] == 5
    assert notebook["cells"]
    assert len({cell["id"] for cell in notebook["cells"]}) == len(notebook["cells"])

    for cell in _code_cells(notebook):
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        ast.parse(_source(cell))


def test_notebook_es_delgado_y_declarativo() -> None:
    notebook = _load_notebook()
    cells = _code_cells(notebook)
    code = "\n".join(_source(cell) for cell in cells)
    tree = ast.parse(code)

    assert len(cells) <= 6
    # El bootstrap reproducible y los dos contratos SourceSpec ocupan la mayor
    # parte; el gate impide que vuelva a crecer hasta ser una segunda librería.
    assert len(code.splitlines()) <= 410
    helpers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert helpers == ["_sha256_archivo"]
    assert code.count("ConfigCruce(") == 1
    assert "FUENTES: list[SourceSpec]" in code
    assert code.count("SourceSpec(") >= 2
    assert "for spec in FUENTES" in code
    assert "source_quality_weights del perfil" in code
    assert "Una fuente ejecuta deduplicación; dos o más ejecutan linkage" in code
    assert "FUENTE_PRINCIPAL" not in code


def test_notebook_fija_version_y_origen_sin_busqueda_ambigua_de_wheel() -> None:
    code = "\n".join(_source(cell) for cell in _code_cells(_load_notebook()))

    import record_linkage

    assert f'VERSION_OBJETIVO = "{record_linkage.__version__}"' in code
    assert "record_linkage.__version__ != VERSION_OBJETIVO" in code
    assert "ORIGEN_PAQUETE = Path(record_linkage.__file__).resolve()" in code
    assert "RUES_LINKER_WHEEL" in code
    assert "RUES_LINKER_PROJECT_DIR" in code
    assert f"rues_linker-{record_linkage.__version__}-py3-none-any.whl" in code
    assert ".glob(" not in code
    assert ".rglob(" not in code


def test_notebook_configura_duckdb_resultado_disco_y_presupuesto() -> None:
    code = "\n".join(_source(cell) for cell in _code_cells(_load_notebook()))

    required = (
        "DuckDBIngestionSettings(",
        'memory_limit="512MB"',
        "threads=2",
        "temp_directory=RUTA_TRABAJO",
        'max_temp_directory_size="10GB"',
        'motor_ingesta="duckdb"',
        'modo_resultado="disco"',
        "preservar_payload=PRESERVAR_PAYLOAD",
        "exportar_excel=False",
        "limite_filas=LIMITES_FILAS",
        "filas_smoke=FILAS_SMOKE",
        "confiables=FUENTES_CONFIABLES",
        "perfil=PERFIL",
        "ajustes_perfil=AJUSTES_PERFIL",
        "perfil_multicampo=PERFIL_MULTICAMPO",
        '"liberar_fuentes_tras_l1": True',
        '"lsh_chunk_size": 75_000',
    )
    for fragment in required:
        assert fragment in code


def test_notebook_usa_solo_consultas_acotadas_para_salida_y_qa_n_fuente() -> None:
    code = "\n".join(_source(cell) for cell in _code_cells(_load_notebook()))

    required = (
        "isinstance(resultado, ResultadoCruceDisco)",
        "resultado.golden.preview(rows=FILAS_PREVIEW)",
        "resultado.correlativa.preview(rows=FILAS_PREVIEW)",
        "resultado.cruce_por_fuente()",
        "resultado.matriz_presencia()",
        "for _spec in FUENTES",
        "resultado.entidades_ausentes_de(_spec.name, limit=FILAS_QA)",
    )
    for fragment in required:
        assert fragment in code

    forbidden = (
        "import pandas",
        "pandas as pd",
        "pd.read_",
        "read_csv(",
        "read_parquet(",
        ".to_pandas(",
        "load_source(",
        "load_sources(",
        "iter_source_chunks(",
    )
    for fragment in forbidden:
        assert fragment not in code


def test_notebook_centraliza_rutas_preflight_payload_y_modo_muestra() -> None:
    code = "\n".join(_source(cell) for cell in _code_cells(_load_notebook()))

    required = (
        "RUES_LINKER_DATA_DIR",
        "RUES_LINKER_RUES_PATH",
        "RUES_LINKER_EXPORT_PATH",
        "RUES_LINKER_OUTPUT_DIR",
        "RUES_LINKER_WORK_DIR",
        "RUES_LINKER_SNAPSHOT_DIR",
        "CORRIDA_COMPLETA = False",
        "LIMITES_ESPECIFICOS",
        "PRESERVAR_PAYLOAD = True",
        "COPIAR_SNAPSHOT_A_DRIVE = False",
        "spec.path.is_file()",
        "shutil.disk_usage(RUTA_TRABAJO)",
        "RUTA_TRABAJO.is_relative_to(_drive_colab)",
        "RUTA_SALIDA.is_relative_to(_drive_colab)",
        "shutil.copytree(RUTA_SALIDA, _destino_snapshot)",
        "_sha256_archivo(_copia) != _sha256_archivo(_origen)",
        'if _nombre_ruta == "generation_dir"',
        "_ruta.is_dir()",
    )
    for fragment in required:
        assert fragment in code

    assert "if LIMITES_FILAS is not None and set(LIMITES_FILAS)" in code
    assert "PERMITIR_SALIDA_EFIMERA" not in code
    assert 'Path("/content/drive/MyDrive/resultados_rues_linker")' not in code


def test_notebook_no_expone_controles_muertos_en_ruta_duckdb() -> None:
    code = "\n".join(_source(cell) for cell in _code_cells(_load_notebook()))

    forbidden = (
        "FORZAR_RELECTURA",
        "forzar_relectura=",
        "chunksize=",
        "temp_dir=",
        '"lsh_batch_size"',
        '"max_memory_gb"',
        "MODO =",
        "set(LIMITES_ESPECIFICOS) - set(_nombres)",
    )
    for fragment in forbidden:
        assert fragment not in code


def test_notebook_valida_directorio_generacional_y_copia_snapshot(tmp_path: Path) -> None:
    notebook = _load_notebook()
    qa_cell = next(cell for cell in notebook["cells"] if cell.get("id") == "qa-disco")
    code = compile(_source(qa_cell), str(NOTEBOOK), "exec")

    output = tmp_path / "output"
    generation = output / "resultados.generations" / "gen-001"
    generation.mkdir(parents=True)
    golden_path = generation / "golden.parquet"
    correlative_path = generation / "correlativa.parquet"
    metadata_path = generation / "metadatos_corrida.json"
    manifest_path = output / "resultados.manifest.json"
    for path, contents in (
        (golden_path, b"golden"),
        (correlative_path, b"correlativa"),
        (metadata_path, b"{}\n"),
        (manifest_path, b"{}\n"),
    ):
        path.write_bytes(contents)

    result = SimpleNamespace(
        golden=SimpleNamespace(rows=2),
        correlativa=SimpleNamespace(rows=3),
        metricas={"filas_entrada": 3, "entidades": 2},
        rutas={
            "golden": golden_path,
            "correlativa": correlative_path,
            "metadatos": metadata_path,
            "manifest": manifest_path,
            "generation_dir": generation,
        },
        cruce_por_fuente=lambda: pd.DataFrame({"FUENTE": ["A", "B"]}),
        matriz_presencia=lambda: pd.DataFrame(),
        entidades_ausentes_de=lambda _source, limit: pd.DataFrame({"limit": [limit]}),
        # v0.21.0 — `identidad_adoptada` es parte del protocolo ControlCalidad
        # (ADR-0008): todo resultado real la tiene, así que el doble también.
        identidad_adoptada=lambda limite: pd.DataFrame(
            {"ID_GRUPO": [0], "NIT_FINAL": ["9001"], "RAZON_SOCIAL_FINAL": ["ACME"]}
        ).head(limite),
    )
    namespace: dict[str, Any] = {
        "COPIAR_SNAPSHOT_A_DRIVE": True,
        "ES_COLAB": False,
        "FILAS_QA": 5,
        "FUENTES": [SimpleNamespace(name="A"), SimpleNamespace(name="B")],
        "RUTA_SALIDA": output,
        "RUTA_SNAPSHOT_PERSISTENTE": tmp_path / "snapshots",
        "Path": Path,
        "display": lambda _value: None,
        "hashlib": hashlib,
        "resultado": result,
        "shutil": shutil,
        "_nombres": ["A", "B"],
    }

    exec(code, namespace)

    snapshot = tmp_path / "snapshots" / generation.name
    assert (snapshot / manifest_path.relative_to(output)).read_bytes() == b"{}\n"
    assert (snapshot / golden_path.relative_to(output)).read_bytes() == b"golden"
    assert (snapshot / generation.relative_to(output)).is_dir()
