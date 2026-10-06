"""Los scripts que leían ``NIT_BASE``/``NIT_VALID`` de la correlativa (F1).

Desde F1.9 el entregable no trae las columnas técnicas; ``scripts/
verify_real_archives.py`` y ``scripts/verificar_rues_x_exportaciones.py`` las
recuperan de ``<dir_trabajo>/L5_golden/correlative.parquet`` con
``salida.tecnicas.adjuntar_tecnicas``, y ``scripts/benchmark_e2e_matcher.py``
cruza con el ``ID_REGISTRO`` de la fuente, que el contrato conserva como
``ID_REGISTRO_FUENTE``.

Las dos verificaciones corren de extremo a extremo sobre ZIP inventados en
``tmp_path`` (RUES con 2 columnas, exportaciones con filas repetidas para que
haya colapso y la alineación sea por contenido); tardan unos segundos. El
benchmark no se corre: depende de un CSV fuera del repositorio y de dos
``linkage()`` sobre 12.427 registros; se prueba la función que depende del
contrato.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from cargar_script import cargar_script

verificar = cargar_script("verificar_rues_x_exportaciones")
verify = cargar_script("verify_real_archives")
benchmark = cargar_script("benchmark_e2e_matcher")


def _zip(ruta: Path, miembro: str, texto: str) -> Path:
    with zipfile.ZipFile(ruta, "w") as archivo:
        archivo.writestr(miembro, texto.encode("cp1252"))
    return ruta


@pytest.fixture
def archivos(tmp_path: Path) -> tuple[Path, Path]:
    """RUES (9 dígitos) y exportaciones (10 dígitos, con filas idénticas repetidas)."""
    rues = _zip(
        tmp_path / "rues.zip",
        "rues.csv",
        "NUMERO_IDENTIFICACION,RAZON_SOCIAL\n"
        "900111222,ACME COLOMBIA SAS\n"
        "800333444,BETA LTDA\n"
        "900555666,GAMA S.A.\n"
        "900555666,GAMA S.A.\n",
    )
    dane = _zip(
        tmp_path / "dane.zip",
        "exportaciones.txt",
        "Nit Exportador\tRazon Social\n"
        "9001112221\tACME COLOMBIA S.A.S.\n"
        "9001112221\tACME COLOMBIA S.A.S.\n"
        "9001112221\tACME COLOMBIA S.A.S.\n"
        "700999888\tDELTA EU\n"
        "-1\tNO DEFINIDO\n",
    )
    return rues, dane


# ── verificar_rues_x_exportaciones.py ─────────────────────────────────────


def test_conflictos_de_identificador_es_la_regla_del_contrato() -> None:
    """Dos bases válidas distintas en un grupo = 1; una base inválida no cuenta."""
    correlativa = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0, 1, 1, 2, 2],
            "NIT_BASE": ["900111222", "900111222", "800333444", "700555666", "1", "2"],
            "NIT_VALID": ["1", "True", "1", "1", "1", "0"],
        }
    )
    assert verificar.conflictos_de_identificador(correlativa) == 1


def test_verificar_rues_recupera_tecnicas_del_dir_trabajo_y_coincide_con_el_manifiesto(
    archivos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    rues, dane = archivos
    salida = tmp_path / "salida"
    monkeypatch.setattr(
        sys,
        "argv",
        ["verificar", "--rues", str(rues), "--dane", str(dane), "--salida", str(salida)],
    )
    assert verificar.main() == 0

    reporte = json.loads((salida / "verificacion_rues_x_exportaciones.json").read_text("utf-8"))
    assert reporte["filas_entrada"] == reporte["filas_correlativa"] == 9
    assert reporte["grupos_con_nit_valido_en_conflicto"] == 0
    assert reporte["grupos_con_nit_valido_en_conflicto_manifiesto"] == 0
    assert reporte["exportaciones_enlazadas"] == 3 and reporte["exportaciones_totales"] == 5
    tecnicas = reporte["columnas_tecnicas"]
    assert tecnicas["columnas"] == ["NIT_BASE", "NIT_VALID"]
    assert tecnicas["alineacion"] == "contenido"  # hubo filas idénticas colapsadas
    assert tecnicas["filas_parquet"] == 6 and tecnicas["filas_correlativa"] == 9
    assert tecnicas["origen"].endswith("L5_golden/correlative.parquet")
    assert "Todas las invariantes pasaron" in capsys.readouterr().out


def test_verificar_rues_falla_si_el_recuento_difiere_del_manifiesto() -> None:
    """El script verifica: si lo recalculado desde _trabajo no coincide con lo
    publicado, es un fallo, no una nota."""
    fallos = verificar.fallos_de(
        {
            "filas_entrada": 3,
            "filas_correlativa": 3,
            "grupos_con_nit_valido_en_conflicto": 1,
            "grupos_con_nit_valido_en_conflicto_manifiesto": 0,
        }
    )
    assert any("manifiesto" in f for f in fallos)
    assert any("1 grupos mezclan" in f for f in fallos)


# ── verify_real_archives.py ───────────────────────────────────────────────


def test_verify_real_archives_recupera_nit_base_del_dir_trabajo(
    archivos: tuple[Path, Path], tmp_path: Path
) -> None:
    rues, dane = archivos
    args = argparse.Namespace(
        rues_zip=rues,
        exports_zip=dane,
        work_dir=tmp_path / "trabajo",
        output=tmp_path / "evidencia.json",
        profile="produccion_estandar",
    )
    resultado = verify.verify(args)

    assert resultado["status"] == "PASS"
    assert resultado["result"]["input_rows"] == 9 and resultado["result"]["correlative_rows"] == 9
    proxies = resultado["nit_consistency_proxies"]
    assert proxies["canonical_nit_field"] == "NIT_BASE"
    assert proxies["canonical_nit_source"]["alineacion"] == "contenido"
    assert proxies["canonical_nit_source"]["columnas"] == ["NIT_BASE"]
    # 900111222 está en las dos fuentes (9 y 10 dígitos): una base común, un grupo.
    assert proxies["common_canonical_nit_bases"] == 1
    assert proxies["common_bases_in_one_predicted_group"] == 1
    assert proxies["export_rows_with_common_base"] == 3
    assert proxies["export_rows_linked_to_same_rues_base"] == 3
    assert proxies["cross_source_groups_with_conflicting_bases"] == 0
    # El JSON es serializable (allow_nan=False) y se escribe atómico.
    verify._atomic_json_dump(resultado, args.output)
    assert json.loads(args.output.read_text("utf-8"))["status"] == "PASS"


def test_verify_real_archives_proxies_exigen_nit_base_pegado() -> None:
    sin_tecnicas = pd.DataFrame({"ORIGINAL_INDEX": [0], "EXPECTED_SRC": ["RUES"], "ID_GRUPO": [0]})
    with pytest.raises(AssertionError, match="adjuntar_tecnicas"):
        verify._nit_consistency_proxies(sin_tecnicas)


# ── benchmark_e2e_matcher.py ──────────────────────────────────────────────


def test_pares_predichos_cruza_por_id_registro_fuente() -> None:
    gt = pd.DataFrame({"ID_REGISTRO": ["r1", "r2", "r3", "r4"], "ID_GROUP": [1, 1, 2, 2]})
    correlativa = pd.DataFrame(
        {
            "ID_REGISTRO": ["A-r1", "A-r2", "B-r3", "B-r4"],  # el del contrato: NO cruza
            "ID_REGISTRO_FUENTE": ["r1", "r2", "r3", "r4"],
            "ID_GRUPO": [7, 7, 8, 9],
        }
    )
    assert benchmark.pares_predichos(gt, correlativa) == {frozenset({"r1", "r2"})}


def test_pares_predichos_falla_si_la_fuente_no_trajo_id_registro() -> None:
    gt = pd.DataFrame({"ID_REGISTRO": ["r1"], "ID_GROUP": [1]})
    correlativa = pd.DataFrame({"ID_REGISTRO": ["A-F0"], "ID_GRUPO": [0]})
    with pytest.raises(KeyError, match="ID_REGISTRO_FUENTE"):
        benchmark.pares_predichos(gt, correlativa)
