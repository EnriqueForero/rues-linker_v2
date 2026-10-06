"""Contrato de los notebooks 01–06 con el estándar de salida (F1.15).

Dos compuertas, con propósitos distintos:

* **Rápida** (``test_ningun_notebook_usa_la_api_vieja``): ningún notebook 01–06
  menciona ``PipelineResult`` ni los alias de v1 (``tabla_correlativa``,
  ``golden_records``) ni lee el resultado como ``dict`` (``res["correlative"]``,
  ``res.get("report_files")``…). Se comprueba con ``ast`` sobre el código y con
  texto sobre todas las celdas; la única mención admitida es la que explica la
  migración (una línea que dice «hasta 0.22.x», «alias de v1», «obsoleto»…).
* **Lenta** (``test_el_notebook_corre_entero_sobre_la_sintetica``, marcada
  ``slow``): cada notebook se ejecuta ENTERO con ``nbclient`` sobre
  ``tests/data_sintetica/`` —la prueba materializa los archivos con la forma
  exacta que la Celda B de cada notebook declara (nombres, hojas, columnas,
  separadores)— con un tope de 10 minutos por notebook. Un notebook que nunca
  se ejecutó no es un entregable.

Cómo se ejecuta un notebook aquí y no en Colab
----------------------------------------------
* La celda de configuración ``RUTA_DATOS`` (la que sigue a la celda de
  entorno) apunta por defecto a Drive/``/content``; la prueba inserta justo
  después una celda que la sobreescribe con ``tmp_path``. Los notebooks no
  llevan ganchos de prueba: se inyecta una celda, no se edita el archivo.
* ``06_orquestador_configurable`` ya se configura por variables de entorno
  (``RUES_LINKER_*``), que es su mecanismo documentado y probado; la prueba
  las fija en vez de inyectar.
* Las celdas de entorno de 05 y 06_ejemplo instalan la librería desde una
  rueda en Drive y montan Drive: eso no existe fuera de Colab, así que se
  sustituyen por una celda que importa la librería ya instalada y exige la
  misma versión. Las de 01–04 no se tocan: detectan la versión instalada y
  no instalan nada.
* El kernel es ``rues-linker-v2`` (registrado en el venv con
  ``python -m ipykernel install --user --name rues-linker-v2``); si no está,
  el ``python3`` del venv. Sin ninguno la prueba FALLA, no se salta.
* El cwd del kernel es ``tmp_path``: así ``06_orquestador`` no encuentra un
  checkout y no instala nada en el venv.
"""

from __future__ import annotations

import ast
import json
import os
import zipfile
from collections.abc import Callable
from pathlib import Path

import nbformat
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
NOTEBOOKS = RAIZ / "notebooks"
SINTETICA = RAIZ / "tests" / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"

NB01 = "01_deduplicar_una_base.ipynb"
NB02 = "02_cruzar_dos_bases.ipynb"
NB03 = "03_produccion_multifuente.ipynb"
NB04 = "04_multicampo_y_evaluacion.ipynb"
NB05 = "05_general_cruce_configurable.ipynb"
NB06_PLANTILLA = "06_ejemplo_rues_x_exportaciones.ipynb"
NB06_ORQUESTADOR = "06_orquestador_configurable.ipynb"
CONTRATO = (NB01, NB02, NB03, NB04, NB05, NB06_PLANTILLA, NB06_ORQUESTADOR)

#: Entornos que instalan desde Drive: fuera de Colab se sustituyen (ver módulo).
INSTALAN_DESDE_DRIVE = (NB05, NB06_PLANTILLA)

KERNEL_PROPIO = "rues-linker-v2"
TIEMPO_LIMITE_S = 600

#: Nombres que ningún notebook del contrato puede usar en código.
NOMBRES_PROHIBIDOS = ("PipelineResult", "tabla_correlativa", "golden_records")
MODULOS_PROHIBIDOS = ("record_linkage.pipeline.result",)
#: Claves del ``dict`` viejo de ``linkage()`` (0.22.x): leerlas con ``[...]``,
#: ``.get`` o ``in`` sobre el resultado es la API obsoleta.
CLAVES_VIEJAS = frozenset(
    {
        "correlative",
        "golden",
        "report_files",
        "preprocessing",
        "ingestion_reports",
        "matcher_stats",
        "matcher_decisions",
        "work_dir",
    }
)
#: Una línea de texto que nombra la API vieja es legítima solo si explica la
#: migración. Son clases de enunciado, no excepciones puntuales.
MARCAS_MIGRACION = ("hasta 0.22", "Hasta 0.22", "alias de v1", "obsolet", "migra", "desaparece")


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades comunes
# ─────────────────────────────────────────────────────────────────────────────


def _cargar(nombre: str) -> dict:
    return json.loads((NOTEBOOKS / nombre).read_text(encoding="utf-8"))


def _fuente(celda: dict) -> str:
    valor = celda.get("source", "")
    return "".join(valor) if isinstance(valor, list) else str(valor)


def _sin_magics(fuente: str) -> str:
    return "".join(
        linea for linea in fuente.splitlines(True) if not linea.lstrip().startswith(("%", "!"))
    )


def _codigo(nb: dict) -> list[str]:
    return [_fuente(c) for c in nb["cells"] if c["cell_type"] == "code"]


# ─────────────────────────────────────────────────────────────────────────────
# Compuerta rápida: nada de PipelineResult ni del dict viejo
# ─────────────────────────────────────────────────────────────────────────────


def _usos_prohibidos(arbol: ast.AST) -> list[str]:
    """Los nodos del código que usan la API vieja, descritos para el mensaje."""
    hallazgos: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and (nodo.module or "") in MODULOS_PROHIBIDOS:
            hallazgos.append(f"import de {nodo.module}")
        elif isinstance(nodo, ast.Name) and nodo.id in NOMBRES_PROHIBIDOS:
            hallazgos.append(f"nombre {nodo.id}")
        elif isinstance(nodo, ast.Attribute) and nodo.attr in NOMBRES_PROHIBIDOS:
            hallazgos.append(f"atributo .{nodo.attr}")
        elif isinstance(nodo, ast.Subscript) and isinstance(nodo.value, ast.Name):
            # res["correlative"]: el dict viejo. Un subíndice sobre un atributo
            # (res.metricas["report_files"]) es la API nueva y se admite.
            clave = nodo.slice
            if isinstance(clave, ast.Constant) and clave.value in CLAVES_VIEJAS:
                hallazgos.append(f'{nodo.value.id}["{clave.value}"]')
        elif isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
            # res.get("report_files")
            if (
                nodo.func.attr == "get"
                and isinstance(nodo.func.value, ast.Name)
                and nodo.args
                and isinstance(nodo.args[0], ast.Constant)
                and nodo.args[0].value in CLAVES_VIEJAS
            ):
                hallazgos.append(f'{nodo.func.value.id}.get("{nodo.args[0].value}")')
        elif (
            isinstance(nodo, ast.Compare)
            and isinstance(nodo.left, ast.Constant)
            and nodo.left.value in CLAVES_VIEJAS
            and any(
                isinstance(op, ast.In) and isinstance(c, ast.Name)
                for op, c in zip(nodo.ops, nodo.comparators, strict=True)
            )
        ):
            # "matcher_decisions" in res
            hallazgos.append(f'"{nodo.left.value}" in <nombre>')
    return hallazgos


@pytest.mark.parametrize("nombre", CONTRATO)
def test_ningun_notebook_usa_la_api_vieja(nombre: str) -> None:
    """Código por ``ast`` y texto por línea: ninguna mención fuera de una de migración."""
    nb = _cargar(nombre)
    fallos: list[str] = []
    for i, celda in enumerate(nb["cells"]):
        texto = _fuente(celda)
        if celda["cell_type"] == "code":
            fallos += [f"celda {i}: {h}" for h in _usos_prohibidos(ast.parse(_sin_magics(texto)))]
        for linea in texto.splitlines():
            if any(p in linea for p in NOMBRES_PROHIBIDOS) and not any(
                m in linea for m in MARCAS_MIGRACION
            ):
                fallos.append(
                    f"celda {i}: menciona la API vieja sin explicar la migración: "
                    f"{linea.strip()[:90]!r}"
                )
    assert not fallos, f"{nombre} sigue usando la API anterior a F1.9:\n" + "\n".join(
        f"  - {f}" for f in fallos
    )


@pytest.mark.parametrize("nombre", CONTRATO)
def test_el_notebook_declara_ruta_datos_o_variables_de_entorno(nombre: str) -> None:
    """Lo que permite correr sobre otra carpeta sin editar el archivo."""
    codigo = "\n".join(_codigo(_cargar(nombre)))
    if nombre == NB06_ORQUESTADOR:
        assert "RUES_LINKER_DATA_DIR" in codigo
        return
    assert "RUTA_DATOS = Path(" in codigo, "falta la celda de configuración con RUTA_DATOS"
    assert "/content" not in "\n".join(
        ln for ln in codigo.splitlines() if ln.strip().startswith("RUTA_TRABAJO") and "drive" in ln
    ), "el trabajo nunca va en Drive"


# ─────────────────────────────────────────────────────────────────────────────
# Compuerta lenta: cada notebook corre entero sobre la sintética
# ─────────────────────────────────────────────────────────────────────────────


def _sintetica() -> pd.DataFrame:
    """28 empresas inventadas: NIT · RAZON_SOCIAL · CIUDAD · TELEFONO · ID_GROUP."""
    return pd.read_csv(SINTETICA, dtype=str).fillna("")


def _csv_en_zip(df: pd.DataFrame, ruta_zip: Path, miembro: str, sep: str = ",") -> None:
    ruta_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ruta_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(miembro, df.to_csv(index=False, sep=sep))


def _xlsx(df: pd.DataFrame, ruta: Path, hoja: str = "Sheet1") -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(ruta, sheet_name=hoja, index=False)


def _preparar_01(datos: Path) -> dict[str, str]:
    base = _sintetica()[["NIT", "RAZON_SOCIAL"]].rename(columns={"RAZON_SOCIAL": "RAZON SOCIAL"})
    _xlsx(base, datos / "mi_base.xlsx", hoja="Hoja1")
    return {}


def _preparar_02(datos: Path) -> dict[str, str]:
    base = _sintetica()
    rues = base.iloc[::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
        columns={"RAZON_SOCIAL": "RAZON SOCIAL"}
    )
    crm = base.iloc[1::2][["NIT", "RAZON_SOCIAL"]].rename(
        columns={"NIT": "NIT EMPRESA", "RAZON_SOCIAL": "NOMBRE"}
    )
    _xlsx(rues, datos / "base_rues.xlsx", hoja="Base")
    _xlsx(crm, datos / "base_crm.xlsx")
    return {}


def _preparar_03(datos: Path) -> dict[str, str]:
    base = _sintetica()
    datos.mkdir(parents=True, exist_ok=True)
    _csv_en_zip(
        base.iloc[::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
            columns={"RAZON_SOCIAL": "RAZON SOCIAL"}
        ),
        datos / "rues.zip",
        "rues.csv",
    )
    base.iloc[1::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
        columns={"RAZON_SOCIAL": "NOMBRE"}
    ).to_csv(datos / "crm.csv", index=False)
    base.iloc[::3][["NIT", "RAZON_SOCIAL"]].rename(
        columns={"NIT": "IDENTIFICACION", "RAZON_SOCIAL": "EMPRESA"}
    ).to_csv(datos / "eventos.txt", index=False, sep="\t")
    _xlsx(
        base.iloc[1::3][["NIT", "RAZON_SOCIAL"]].rename(
            columns={"NIT": "NIT9", "RAZON_SOCIAL": "COMPANY"}
        ),
        datos / "gazelle.xlsx",
    )
    return {}


def _preparar_04(datos: Path) -> dict[str, str]:
    base = _sintetica()
    multi = pd.DataFrame(
        {
            "NIT": base["NIT"],
            "RAZON SOCIAL": base["RAZON_SOCIAL"],
            "EMAIL": "",
            "TELEFONO": base["TELEFONO"],
            "DIRECCION": "",
            "CIUDAD": base["CIUDAD"],
            "ID_GRUPO_REAL": base["ID_GROUP"],
        }
    )
    _xlsx(multi, datos / "base_multicampo.xlsx")
    return {}


def _preparar_05(datos: Path) -> dict[str, str]:
    base = _sintetica()
    _csv_en_zip(
        base.iloc[::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
            columns={"NIT": "NUMERO_IDENTIFICACION", "CIUDAD": "CODIGO_MUNICIPIO_COMERCIAL"}
        ),
        datos / "base_a.zip",
        "base_a.csv",
    )
    base.iloc[1::2][["NIT", "RAZON_SOCIAL"]].rename(
        columns={"NIT": "Nit", "RAZON_SOCIAL": "Razon Social"}
    ).to_csv(datos / "base_b.txt", index=False, sep="\t")
    return {}


def _preparar_06_plantilla(datos: Path) -> dict[str, str]:
    base = _sintetica()
    _csv_en_zip(
        base.iloc[::2][["NIT", "RAZON_SOCIAL", "CIUDAD", "TELEFONO"]],
        datos / "RUES" / "2026_06_30_Historico_Limpio.zip",
        "historico.csv",
    )
    _csv_en_zip(
        base.iloc[1::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
            columns={
                "NIT": "Nit Exportador",
                "RAZON_SOCIAL": "Razon Social",
                "CIUDAD": "Departamento Origen",
            }
        ),
        datos / "DANE" / "2026-08-21 Base_Exportaciones_Colombianas_2021-2026 (Junio).zip",
        "exportaciones.csv",
    )
    return {}


def _preparar_06_orquestador(datos: Path) -> dict[str, str]:
    base = _sintetica()
    _csv_en_zip(
        base.iloc[::2][["NIT", "RAZON_SOCIAL", "CIUDAD", "TELEFONO"]].rename(
            columns={
                "NIT": "NUMERO_IDENTIFICACION",
                "CIUDAD": "CODIGO_MUNICIPIO_COMERCIAL",
                "TELEFONO": "TELEFONO_COMERCIAL_1",
            }
        ),
        datos / "2026-08-25 A00 - data_CSV_RUES.zip",
        "rues.csv",
    )
    _csv_en_zip(
        base.iloc[1::2][["NIT", "RAZON_SOCIAL", "CIUDAD"]].rename(
            columns={
                "NIT": "Nit Exportador",
                "RAZON_SOCIAL": "Razon Social",
                "CIUDAD": "Departamento Origen",
            }
        ),
        datos / "2026-08-21 Base_Exportaciones_Colombianas_2021-2026 (Junio).zip",
        "exportaciones.csv",
        sep="\t",
    )
    return {"RUES_LINKER_DATA_DIR": str(datos)}


PREPARADORES: dict[str, Callable[[Path], dict[str, str]]] = {
    NB01: _preparar_01,
    NB02: _preparar_02,
    NB03: _preparar_03,
    NB04: _preparar_04,
    NB05: _preparar_05,
    NB06_PLANTILLA: _preparar_06_plantilla,
    NB06_ORQUESTADOR: _preparar_06_orquestador,
}

#: Lo que cada notebook tiene que haber dejado en disco (glob relativo a tmp_path).
ARTEFACTOS_ESPERADOS: dict[str, tuple[str, ...]] = {
    NB01: (
        "resultados/*_deduplicacion/manifest.json",
        "resultados/*_deduplicacion/correlativa.parquet",
    ),
    NB02: (
        "resultados/*_cruce_RUES_CRM/manifest.json",
        "resultados/*_cruce_RUES_CRM/golden.parquet",
    ),
    NB03: (
        "resultados/*_CONSOLIDADO_2026_07/manifest.json",
        "resultados/*_CONSOLIDADO_2026_07/excel/golden.xlsx",
    ),
    NB04: ("resultados/multicampo__CLUSTERS.xlsx", "resultados/multicampo__DECISIONES.xlsx"),
    NB05: ("resultados/correlativa.parquet", "resultados/golden.parquet"),
    NB06_PLANTILLA: ("datos/RUES/_procesados/resultados_rues_linker/correlativa.parquet",),
    NB06_ORQUESTADOR: ("resultados/rues_x_exportaciones/resultados.manifest.json",),
}


def _kernel() -> str:
    from jupyter_client.kernelspec import KernelSpecManager

    disponibles = KernelSpecManager().find_kernel_specs()
    for candidato in (KERNEL_PROPIO, "python3"):
        if candidato in disponibles:
            return candidato
    pytest.fail(
        f"no hay kernel de Jupyter. Regístrelo en el venv: "
        f"python -m ipykernel install --user --name {KERNEL_PROPIO}. "
        f"Disponibles: {sorted(disponibles)}"
    )


def _celda_codigo(fuente: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(fuente)


def _sustituir_entorno(nb: nbformat.NotebookNode, nombre: str) -> None:
    """La celda de entorno instala desde Drive: aquí la librería ya está instalada."""
    import record_linkage

    primera = next(c for c in nb.cells if c.cell_type == "code")
    assert "ENTORNO" in primera.source.upper(), f"{nombre}: la primera celda no es la de entorno"
    primera.source = (
        "# Entorno SUSTITUIDO por tests/test_notebooks_contrato.py: fuera de Colab no hay\n"
        "# Drive ni rueda; se exige la versión instalada y se sigue.\n"
        "import importlib, sys\n"
        "from pathlib import Path\n"
        "record_linkage = importlib.import_module('record_linkage')\n"
        f"assert record_linkage.__version__ == {record_linkage.__version__!r}, "
        "record_linkage.__version__\n"
        "print('rues-linker', record_linkage.__version__, 'desde', record_linkage.__file__)\n"
    )


def _sobreescribir_rutas(
    nb: nbformat.NotebookNode, datos: Path, resultados: Path, trabajo: Path
) -> None:
    """Inserta, tras la celda ``RUTA_DATOS``, una que apunta todo a ``tmp_path``."""
    indice = next(
        (
            i
            for i, c in enumerate(nb.cells)
            if c.cell_type == "code" and "RUTA_DATOS = Path(" in c.source
        ),
        None,
    )
    assert indice is not None, "no hay celda de configuración con RUTA_DATOS"
    nb.cells.insert(
        indice + 1,
        _celda_codigo(
            "# Inyectada por tests/test_notebooks_contrato.py\n"
            f"RUTA_DATOS = Path({str(datos)!r})\n"
            f"RUTA_RESULTADOS = Path({str(resultados)!r})\n"
            f"RUTA_TRABAJO = Path({str(trabajo)!r})\n"
        ),
    )


def _errores(nb: nbformat.NotebookNode) -> list[str]:
    errores = []
    for i, celda in enumerate(nb.cells):
        for salida in celda.get("outputs", []):
            if salida.get("output_type") == "error":
                errores.append(f"celda {i}: {salida.get('ename')}: {salida.get('evalue')}")
    return errores


@pytest.mark.slow
@pytest.mark.parametrize("nombre", CONTRATO)
def test_el_notebook_corre_entero_sobre_la_sintetica(
    nombre: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        from nbclient import NotebookClient
    except ModuleNotFoundError as exc:  # pragma: no cover - el mensaje ES el producto
        pytest.fail(
            f"falta {exc.name}: pip install nbclient ipykernel && "
            f"python -m ipykernel install --user --name {KERNEL_PROPIO}"
        )

    datos, resultados, trabajo = tmp_path / "datos", tmp_path / "resultados", tmp_path / "trabajo"
    entorno = PREPARADORES[nombre](datos)
    for variable in ("RUES_LINKER_WHEEL", "RUES_LINKER_PROJECT_DIR", "RUES_LINKER_SNAPSHOT_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("RUES_LINKER_OUTPUT_DIR", str(resultados))
    monkeypatch.setenv("RUES_LINKER_WORK_DIR", str(trabajo))
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)

    nb = nbformat.read(NOTEBOOKS / nombre, as_version=4)
    if nombre in INSTALAN_DESDE_DRIVE:
        _sustituir_entorno(nb, nombre)
    if nombre != NB06_ORQUESTADOR:
        _sobreescribir_rutas(nb, datos, resultados, trabajo)

    cliente = NotebookClient(
        nb,
        timeout=TIEMPO_LIMITE_S,
        kernel_name=_kernel(),
        allow_errors=True,
        resources={"metadata": {"path": str(tmp_path)}},
    )
    cliente.execute()

    errores = _errores(nb)
    assert not errores, f"{nombre} falló al ejecutarse:\n" + "\n".join(f"  - {e}" for e in errores)
    for patron in ARTEFACTOS_ESPERADOS[nombre]:
        assert list(tmp_path.glob(patron)), f"{nombre} no dejó {patron} en {tmp_path}"
