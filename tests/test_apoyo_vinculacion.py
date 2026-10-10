"""Los insumos inventados de la vinculación: deterministas y con todos sus casos etiquetados."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from tests import apoyo_vinculacion as av

RAIZ = Path(__file__).resolve().parents[1]


def test_los_insumos_tienen_las_tres_bases_y_los_casos(tmp_path):
    ins = av.construir_insumos(tmp_path)
    assert len(ins.ancla) > 150 and len(ins.orbis) > 100 and len(ins.fdi) > 50
    assert {"EMP_A_IDENTICO", "GRP_B_OTRO_PAIS", "REV_C_SIGLA", "WEB_EMPRESA", "DUP_VETADO"} <= set(
        ins.casos
    )
    for archivo in (
        ins.archivo_ancla,
        ins.archivo_descargas,
        ins.archivo_decisiones,
        ins.archivo_decisiones_2,
    ):
        assert archivo.exists()


def test_la_clave_de_cada_caso_existe_en_la_ancla(tmp_path):
    ins = av.construir_insumos(tmp_path)
    claves = {
        f"{p}|{n}" for p, n in zip(ins.ancla["País de Empresa"], ins.ancla["Empresa"], strict=True)
    }
    sin_clave = [
        t for t, c in ins.casos.items() if "clave_ancla" in c and c["clave_ancla"] not in claves
    ]
    assert sin_clave == []


def _huella_de_proceso(semilla_hash: str, destino: Path) -> str:
    """Construye los insumos en un proceso aparte con otra semilla de hash y devuelve su huella."""
    codigo = (
        "import sys, tempfile, pathlib;"
        f"sys.path.insert(0, {str(RAIZ)!r});"
        "from tests import apoyo_vinculacion as av;"
        f"ins = av.construir_insumos(pathlib.Path({str(destino)!r}));"
        "print(av.huella_tabla(ins.ancla), av.huella_tabla(ins.orbis), av.huella_tabla(ins.fdi))"
    )
    salida = subprocess.run(
        [sys.executable, "-c", codigo],
        check=True, capture_output=True, text=True, env={"PYTHONHASHSEED": semilla_hash, "PATH": ""},
    )  # fmt: skip
    return salida.stdout.strip()


def test_los_insumos_no_dependen_de_la_semilla_de_hash(tmp_path):
    assert _huella_de_proceso("1", tmp_path / "a") == _huella_de_proceso("2", tmp_path / "b")


def test_huella_tabla_ignora_el_dtype_y_el_tipo_del_ausente():
    a = pd.DataFrame({"x": pd.Series(["a", None], dtype="object"), "y": [1.0, float("nan")]})
    b = pd.DataFrame(
        {"x": pd.Series(["a", pd.NA], dtype="string"), "y": pd.Series([1.0, None], dtype="Float64")}
    )
    assert av.huella_tabla(a) == av.huella_tabla(b)


def test_huella_tabla_distingue_un_valor_y_el_orden():
    base = pd.DataFrame({"x": ["a", "b"]})
    assert av.huella_tabla(base) != av.huella_tabla(pd.DataFrame({"x": ["a", "c"]}))
    assert av.huella_tabla(base) != av.huella_tabla(base.iloc[::-1].reset_index(drop=True))
