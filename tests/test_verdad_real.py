"""Pruebas de ``evaluation.verdad_real``: formato único de etiquetas, métricas por régimen con intervalos y kappa.

Empresas y personas inventadas. La verdad real de producción vive fuera del repositorio (docs/PROTOCOLO_GROUND_TRUTH.md).
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.evaluation.verdad_real import (
    COLUMNAS_ETIQUETAS,
    acuerdo_entre_anotadores,
    intervalo_wilson,
    leer_etiquetas,
    medir_verdad_real,
    regimen_de_los_pares,
)


def _correlativa() -> pd.DataFrame:
    # Seis registros: A y B con NIT (misma empresa), C y D sin NIT (misma), E con NIT y F sin NIT (distintas).
    return pd.DataFrame(
        {
            "ID_REGISTRO": ["A", "B", "C", "D", "E", "F"],
            "NIT": ["900100200", "900100200", "", None, "800300400", ""],
            "ID_GRUPO": [
                "g1",
                "g1",
                "g2",
                "g3",
                "g4",
                "g4",
            ],  # el motor separó C de D y juntó E con F
        }
    )


def _etiquetas() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_1": ["A", "C", "E", "A"],
            "ID_2": ["B", "D", "F", "E"],
            "IS_MATCH": [1, 1, 0, 0],  # A-B misma; C-D misma; E-F distinta; A-E distinta
        }
    )


def test_formato_unico():
    assert COLUMNAS_ETIQUETAS == ("ID_1", "ID_2", "IS_MATCH")


def test_medir_global_y_por_regimen():
    res = medir_verdad_real(_etiquetas(), _correlativa(), col_nit="NIT")
    g = res.global_
    # A-B: TP · C-D: FN · E-F: FP · A-E: TN → P = 1/2, R = 1/2
    assert (g.tp, g.fp, g.fn) == (1, 1, 1)
    assert g.precision == pytest.approx(0.5) and g.recall == pytest.approx(0.5)
    assert set(res.por_regimen) == {"CON_ID", "SIN_ID", "MIXTO"}
    assert res.por_regimen["CON_ID"].tp == 1 and res.por_regimen["CON_ID"].fp == 0  # A-B y A-E
    assert res.por_regimen["SIN_ID"].fn == 1  # C-D
    assert res.por_regimen["MIXTO"].fp == 1  # E-F
    tabla = res.tabla()
    assert list(tabla["REGIMEN"]) == ["GLOBAL", "CON_ID", "SIN_ID", "MIXTO"]
    assert tabla.loc[0, "PRECISION_IC95"][0] < 0.5 < tabla.loc[0, "PRECISION_IC95"][1]
    d = res.como_dict()
    assert d["global"]["tp"] == 1 and "CON_ID" in d["por_regimen"]


def test_sin_columna_de_identificador_solo_global():
    res = medir_verdad_real(_etiquetas(), _correlativa(), col_nit="NO_EXISTE")
    assert res.por_regimen == {} and res.notas and "NO_EXISTE" in res.notas[0]


def test_regimen_de_los_pares_con_registro_desconocido():
    ident = pd.Series(["1", "", None], index=["A", "C", "D"])
    pares = pd.DataFrame(
        {"ID_1": ["A", "C", "A", "Z"], "ID_2": ["A", "D", "C", "A"], "IS_MATCH": [1, 1, 0, 0]}
    )
    assert regimen_de_los_pares(pares, ident).tolist() == ["CON_ID", "SIN_ID", "MIXTO", "MIXTO"]


@pytest.mark.parametrize(
    ("exitos", "total", "esperado"),
    [(0, 0, (0.0, 0.0)), (10, 10, (0.722, 1.0)), (0, 10, (0.0, 0.278)), (50, 100, (0.404, 0.596))],
)
def test_intervalo_wilson(exitos, total, esperado):
    lo, hi = intervalo_wilson(exitos, total)
    assert lo == pytest.approx(esperado[0], abs=0.002) and hi == pytest.approx(
        esperado[1], abs=0.002
    )


def test_leer_etiquetas_acepta_los_textos_de_los_anotadores_y_descarta_las_dudas(tmp_path):
    ruta = tmp_path / "etiquetas.csv"
    pd.DataFrame(
        {
            "id_left": ["A", "C", "E", "A", "B"],
            "id_right": ["B", "D", "F", "E", "C"],
            "etiqueta_humana": ["SAME", "misma", "DIFFERENT", "no", "UNCLEAR"],
            "anotador": ["ana"] * 5,
        }
    ).to_csv(ruta, index=False)
    et = leer_etiquetas(ruta)
    assert list(et.columns) == ["ID_1", "ID_2", "IS_MATCH", "ANOTADOR"]
    assert et["IS_MATCH"].tolist() == [1, 1, 0, 0]
    assert et.attrs["descartados"] == 1


def test_leer_etiquetas_excel_con_mismo_grupo(tmp_path):
    ruta = tmp_path / "etiquetas.xlsx"
    pd.DataFrame({"ID_A": ["A"], "ID_B": ["B"], "MISMO_GRUPO": [1]}).to_excel(ruta, index=False)
    assert leer_etiquetas(ruta)["IS_MATCH"].tolist() == [1]


def test_leer_etiquetas_rechaza_un_texto_desconocido_y_columnas_faltantes(tmp_path):
    ruta = tmp_path / "e.csv"
    pd.DataFrame({"ID_1": ["A"], "ID_2": ["B"], "IS_MATCH": ["quizas"]}).to_csv(ruta, index=False)
    with pytest.raises(ValueError, match="no reconocida"):
        leer_etiquetas(ruta)
    pd.DataFrame({"X": ["A"]}).to_csv(ruta, index=False)
    with pytest.raises(ValueError, match="faltan"):
        leer_etiquetas(ruta)


def test_acuerdo_entre_anotadores():
    a = pd.DataFrame(
        {"ID_1": ["A", "C", "E", "G"], "ID_2": ["B", "D", "F", "H"], "IS_MATCH": [1, 1, 0, 0]}
    )
    b = pd.DataFrame(
        {"ID_1": ["B", "D", "F", "X"], "ID_2": ["A", "C", "E", "Y"], "IS_MATCH": [1, 0, 0, 1]}
    )
    ac = acuerdo_entre_anotadores(a, b)
    assert ac.n_comunes == 3 and ac.acuerdo_observado == pytest.approx(2 / 3)
    assert ac.desacuerdos["PAR"].tolist() == ["C|D"]
    assert (0.0 < ac.kappa < 0.8 and "resolver" in ac.lectura) or "bajo" in ac.lectura
    vacio = acuerdo_entre_anotadores(
        a, pd.DataFrame({"ID_1": ["Q"], "ID_2": ["R"], "IS_MATCH": [1]})
    )
    assert vacio.n_comunes == 0 and vacio.lectura == "sin pares en común"
