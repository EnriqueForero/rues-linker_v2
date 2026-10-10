"""F2.18 — ``flujo.importadores`` aplica las reglas únicas de identidad (F2.3/F2.4).

* ``ID_REGISTRO`` con ``salida.identificadores_registro.asignar_id_registro``.
* ``ID_GRUPO`` entero determinista por contenido (menor ``ID_REGISTRO`` del
  grupo; ``id_grupo_por_clave_minima``), no por primera aparición: barajar la
  entrada no cambia el mapa ``ID_REGISTRO → ID_GRUPO``.
* ``ID_ENTIDAD`` con ``identidad.estable.asignar_id_entidad`` (todos ``ENT-…``:
  la base no trae identificador) y su crosswalk (``REGISTROS``, ``RETIRADO_EN``
  nulo) viaja como ``entidades_ids`` del ``ResultadoLinkage`` y de la carpeta.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage import contrato
from record_linkage.exporters.escritor import leer_resultado
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
from record_linkage.identidad.estable import COLUMNA_REGISTROS, crosswalk_de_corrida
from record_linkage.salida.identificadores_registro import id_grupo_por_clave_minima


def _base() -> pd.DataFrame:
    filas = [
        ("C07", "ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
        ("C03", "ACME TRADING L.L.C.", "Estados Unidos", 50.0),
        ("C11", "ACME TRADING", "ESTADOS UNIDOS", 25.0),
        ("C02", "BETA LOGISTICS INC", "ESTADOS UNIDOS", 70.0),
        ("C09", "ACME TRADING LLC", "ALEMANIA", 30.0),
        ("C05", "GAMMA IMPORT GMBH", "ALEMANIA", 10.0),
        ("C01", "GAMMA IMPORT G.M.B.H.", "Alemania", 12.0),
        ("C08", "0", "PANAMÁ", 9_000.0),
    ]
    return pd.DataFrame(filas, columns=["CODIGO", "RAZON_SOCIAL", "PAIS", "FOB"])


def _cfg(**extra) -> ConfigImportadores:
    return ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        col_id="CODIGO",
        nombre_fuente="IMP",
        verboso=False,
        **extra,
    )


@pytest.fixture(scope="module")
def escrito(tmp_path_factory: pytest.TempPathFactory):
    carpeta = tmp_path_factory.mktemp("importadores")
    return deduplicar_importadores(_base(), _cfg(), carpeta_salida=carpeta, excel=False)


def _sha16(ids: list[str]) -> str:
    return "ENT-" + hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()[:16]


def test_id_registro_sale_de_col_id_con_la_regla_unica(escrito) -> None:
    correl = escrito.correlativa
    assert correl["ID_REGISTRO"].tolist() == ("IMP-" + _base()["CODIGO"]).tolist()
    assert escrito.manifiesto["completar"]["id_registro"] == {
        "IMP": {"regla": "col_id", "columna": "CODIGO"}
    }


def test_id_grupo_es_por_contenido_no_por_primera_aparicion(escrito) -> None:
    correl = escrito.correlativa
    esperado = id_grupo_por_clave_minima(
        correl["ID_IMPORTADOR"].to_numpy(), correl["ID_REGISTRO"].to_numpy()
    )
    assert correl["ID_GRUPO"].to_numpy().tolist() == esperado.tolist()
    assert correl["ID_GRUPO"].dtype == np.dtype("int64")
    # Por primera aparición, la primera fila (C07, ACME USA) sería el grupo 0;
    # por contenido lo es el grupo cuyo menor ID_REGISTRO es IMP-C01 (GAMMA DEU).
    assert correl.loc[correl["ID_REGISTRO"] == "IMP-C01", "ID_GRUPO"].iloc[0] == 0
    assert escrito.manifiesto["completar"]["id_grupo"]["recodificado"] is True
    assert "menor ID_REGISTRO" in escrito.manifiesto["completar"]["id_grupo"]["regla"]


def test_barajar_la_entrada_no_cambia_ids_ni_crosswalk(escrito) -> None:
    barajada = _base().sample(frac=1, random_state=11).reset_index(drop=True)
    otro = deduplicar_importadores(barajada, _cfg())
    a = escrito.correlativa.set_index("ID_REGISTRO")[["ID_GRUPO", "ID_ENTIDAD"]].sort_index()
    b = otro.correlativa.set_index("ID_REGISTRO")[["ID_GRUPO", "ID_ENTIDAD"]].sort_index()
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(
        escrito.entidades_ids.reset_index(drop=True), otro.entidades_ids.reset_index(drop=True)
    )


def test_id_entidad_y_crosswalk_son_los_de_identidad_estable(escrito) -> None:
    correl = escrito.correlativa
    por_grupo = correl.groupby("ID_GRUPO")["ID_REGISTRO"].agg(list)
    esperado = correl["ID_GRUPO"].map(por_grupo.map(_sha16))
    assert correl["ID_ENTIDAD"].tolist() == esperado.tolist()
    assert correl["ID_ENTIDAD"].str.startswith("ENT-").all()
    x = escrito.entidades_ids
    assert list(x.columns) == list(contrato.COLUMNAS_ENTIDADES_IDS)
    assert COLUMNA_REGISTROS in x.columns
    assert x["RETIRADO_EN"].isna().all()
    assert len(x) == correl["ID_GRUPO"].nunique()
    pd.testing.assert_frame_equal(x, crosswalk_de_corrida(correl))
    herencia = escrito.manifiesto["completar"]["id_entidad"]["herencia"]
    assert herencia["corrida_anterior"] is None
    assert herencia["nuevas"] == len(x) and herencia["heredadas"] == 0


def test_a_resultado_linkage_lleva_el_crosswalk_y_valida(escrito) -> None:
    res = escrito.a_resultado_linkage()
    assert res.entidades_ids is not None
    pd.testing.assert_frame_equal(res.entidades_ids, escrito.entidades_ids)
    veredicto = res.validar()
    assert veredicto.ok, veredicto.fallos


def test_la_carpeta_escrita_trae_el_crosswalk_con_registros(escrito) -> None:
    carpeta = escrito.carpeta_salida
    assert isinstance(carpeta, Path) and (carpeta / "entidades_ids.parquet").is_file()
    leido = leer_resultado(carpeta)
    assert leido.entidades_ids is not None
    assert list(leido.entidades_ids.columns) == list(contrato.COLUMNAS_ENTIDADES_IDS)
    assert leido.entidades_ids[COLUMNA_REGISTROS].notna().all()
    assert leido.validar().ok, leido.validar().fallos
    assert leido.correlativa["ID_GRUPO"].tolist() == escrito.correlativa["ID_GRUPO"].tolist()


def test_id_registro_con_separador_se_rechaza_antes_de_escribir() -> None:
    from record_linkage.pipeline.errores import IdRegistroInvalidoError

    base = _base()
    base.loc[0, "CODIGO"] = "C0|7"
    with pytest.raises(IdRegistroInvalidoError):
        deduplicar_importadores(base, _cfg())
