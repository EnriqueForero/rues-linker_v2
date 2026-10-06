"""F2.6 · ``flujo.importadores`` escribe el estándar de salida (contrato 1.0).

Qué congela
-----------
* ``correlativa``: las 12 columnas fijas del contrato primero, en su orden,
  después TODAS las columnas de la fuente sin cambios y al final las propias
  del flujo (``ID_IMPORTADOR``, ``ID_EMPRESA_GLOBAL``, ``PAIS_*``,
  ``SIM_AL_FINAL``…), que siguen ahí como columnas adicionales documentadas.
* ``golden``: las 13 columnas de v1 más ``ID_ENTIDAD`` primero; después las
  propias (``ID_IMPORTADOR``, sumas de ``cols_metricas``…).
* ``ID_GRUPO`` entero, uno por ``ID_IMPORTADOR``; ``ID_ENTIDAD`` = ``ENT-…``
  determinista por contenido (la base no trae identificador).
* ``METODO_UNION`` ∈ {``nombre``, ``sin_pareja``}; ``SCORE_PAR`` nulo solo
  en ``sin_pareja``; ``CONFIANZA`` = la del golden.
* ``revision`` tiene la forma del archivo de decisiones (``contrato.REVISION``)
  con un par (líder, miembro) por estrella de los grupos marcados; la tabla
  de grupos de v0.22 sigue en ``revision_grupos``.
* ``validar(estricto=True)`` pasa, ``escribir_importadores`` deja la carpeta
  del estándar vía ``escribir_resultado`` y ``leer_resultado`` la devuelve;
  ``diccionario.csv`` explica cada columna adicional.
* La partición (``ID_IMPORTADOR``) no cambia: el estándar se añade encima.

Las empresas son inventadas.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import contrato
from record_linkage.exporters.escritor import leer_resultado
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
from record_linkage.flujo.importadores import (
    COLUMNAS_PROPIAS,
    escribir_importadores,
    renombres_por_colision,
)
from record_linkage.pipeline.errores import ContratoSalidaError

MARCA = datetime(2026, 10, 6, 15, 0, 0)


def _base() -> pd.DataFrame:
    """La misma base de ``test_flujo_importadores_v022`` más una columna libre."""
    filas = [
        ("ACME TRADING LLC", "ESTADOS UNIDOS DE AMÉRICA", 100.0, "r1"),
        ("ACME TRADING L.L.C.", "Estados Unidos", 50.0, "r2"),
        ("ACME TRADING", "ESTADOS UNIDOS", 25.0, "r3"),
        ("¿ACME TRADING¿ LLC", "estados unidos de america", 400.0, "r4"),
        ("BETA LOGISTICS INC", "ESTADOS UNIDOS", 70.0, "r5"),
        ("ACME TRADING LLC", "ALEMANIA", 30.0, "r6"),
        ("0", "PANAMÁ", 9_000.0, "r7"),
        ("0", "Panama", 8_000.0, "r8"),
        ("GAMMA SAS", "ZONA FRANCA PERMANENTE BOGOTA", 10.0, "r9"),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB", "REF"])


def _cfg(**extra) -> ConfigImportadores:
    return ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
        **extra,
    )


@pytest.fixture(scope="module")
def resultado():
    return deduplicar_importadores(_base(), _cfg())


# ── Correlativa ───────────────────────────────────────────────────────────


def test_correlativa_lleva_las_fijas_primero_y_toda_la_fuente(resultado) -> None:
    c = resultado.correlativa
    fijas = list(contrato.COLUMNAS_CORRELATIVA)
    assert list(c.columns[: len(fijas)]) == fijas
    fuente = list(_base().columns)
    assert list(c.columns[len(fijas) : len(fijas) + len(fuente)]) == fuente
    # Las columnas de v0.22 siguen, como adicionales, después de la fuente.
    for col in ("ID_IMPORTADOR", "ID_EMPRESA_GLOBAL", "PAIS_FINAL", "PAIS_ISO3", "SIM_AL_FINAL"):
        assert col in c.columns[len(fijas) + len(fuente) :]
    assert len(c) == len(_base())
    assert c["REF"].tolist() == _base()["REF"].tolist(), "una columna libre llega sin cambios"


def test_id_grupo_es_entero_y_uno_por_importador(resultado) -> None:
    c = resultado.correlativa
    assert pd.api.types.is_integer_dtype(c["ID_GRUPO"])
    assert c.groupby("ID_IMPORTADOR")["ID_GRUPO"].nunique().max() == 1
    assert c.groupby("ID_GRUPO")["ID_IMPORTADOR"].nunique().max() == 1
    assert c["SRC"].eq("IMPORTADORES").all()
    assert c["ORIGINAL_INDEX"].tolist() == list(range(len(c)))
    assert c["ID_REGISTRO"].tolist() == [f"IMPORTADORES-F{i}" for i in range(len(c))]


def test_id_entidad_es_surrogado_determinista(resultado) -> None:
    c = resultado.correlativa
    assert c["ID_ENTIDAD"].str.startswith("ENT-").all()
    assert c.groupby("ID_GRUPO")["ID_ENTIDAD"].nunique().max() == 1
    assert c.groupby("ID_ENTIDAD")["ID_GRUPO"].nunique().max() == 1
    otra = deduplicar_importadores(_base(), _cfg()).correlativa
    assert otra["ID_ENTIDAD"].tolist() == c["ID_ENTIDAD"].tolist()
    assert otra["ID_GRUPO"].tolist() == c["ID_GRUPO"].tolist()


def test_metodo_union_score_par_y_confianza(resultado) -> None:
    c = resultado.correlativa
    tamano = c.groupby("ID_GRUPO")["ID_GRUPO"].transform("size")
    assert (c.loc[tamano == 1, "METODO_UNION"] == "sin_pareja").all()
    assert (c.loc[tamano > 1, "METODO_UNION"] == "nombre").all()
    assert c.loc[tamano == 1, "SCORE_PAR"].isna().all()
    cfg = _cfg()
    assert (c.loc[tamano > 1, "SCORE_PAR"] >= cfg.umbral_nombre - 1e-9).all()
    # Las cuatro grafías de ACME-USA normalizan al MISMO nombre: unión por
    # igualdad exacta, puntaje 1.0 (no hay par puntuado por el motor, pero el
    # comparador daría 1.0 y así se declara en el diccionario).
    acme = c[(c.RAZON_SOCIAL.str.contains("ACME")) & (c.PAIS_ISO3 == "USA")]
    assert acme["NOMBRE_NORM"].nunique() == 1
    assert (acme["SCORE_PAR"] == 1.0).all()
    assert (c["NAME_SIMILARITY_SCORE"] == c["SIM_AL_FINAL"]).all()
    assert c["NIT_FINAL"].isna().all() and c["NIT_DISTANCE"].isna().all()
    confianza = resultado.golden.set_index("ID_GRUPO")["CONFIANZA"]
    assert (c["CONFIANZA"] == c["ID_GRUPO"].map(confianza)).all()


def test_score_par_del_lider_es_la_mejor_arista_que_lo_une() -> None:
    """Líder sin duplicado exacto: su SCORE_PAR es la mejor arista a un miembro."""
    filas = [
        ("ACME TRADING LLC", "PERU", 1.0),
        ("ACME TRADING LLC A", "PERU", 1.0),
        ("ACME TRADIN", "PERU", 1.0),
    ]
    r = deduplicar_importadores(
        pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"]), _cfg(umbral_nombre=0.70)
    )
    c = r.correlativa
    assert c["ID_GRUPO"].nunique() == 1, c[["RAZON_SOCIAL", "ID_GRUPO", "SIM_AL_FINAL"]]
    lider = c[c.SIM_AL_FINAL == 1.0]
    miembros = c[c.SIM_AL_FINAL < 1.0]
    assert len(lider) == 1 and len(miembros) == 2
    assert float(lider["SCORE_PAR"].iloc[0]) == pytest.approx(float(miembros["SIM_AL_FINAL"].max()))
    assert (miembros["SCORE_PAR"] == miembros["SIM_AL_FINAL"]).all()
    assert (c["METODO_UNION"] == "nombre").all()


def test_la_particion_de_v022_no_cambia(resultado) -> None:
    """El estándar se añade encima: ID_IMPORTADOR sigue siendo el mismo."""
    c = resultado.correlativa
    acme_usa = c[(c.RAZON_SOCIAL.str.contains("ACME")) & (c.PAIS_ISO3 == "USA")]
    assert acme_usa["ID_IMPORTADOR"].nunique() == 1 and len(acme_usa) == 4
    assert c[c.RAZON_SOCIAL == "0"]["ID_IMPORTADOR"].str.startswith("SINNOMBRE").all()
    assert (c["SIM_AL_FINAL"] >= _cfg().umbral_nombre - 1e-9).all()


# ── Golden ────────────────────────────────────────────────────────────────


def test_golden_lleva_las_catorce_fijas_y_las_propias(resultado) -> None:
    g = resultado.golden
    fijas = list(contrato.COLUMNAS_GOLDEN)
    assert list(g.columns[: len(fijas)]) == fijas
    for col in ("ID_IMPORTADOR", "ID_EMPRESA_GLOBAL", "PAIS_FINAL", "N_VARIANTES_NOMBRE", "FOB"):
        assert col in g.columns
    assert len(g) == resultado.correlativa["ID_GRUPO"].nunique()
    assert g["ID_GRUPO"].is_unique
    assert pd.api.types.is_bool_dtype(g["REQUIRES_REVIEW"])
    for col in contrato.COLUMNAS_METRICAS_GOLDEN:
        assert not g[col].isna().any(), col
    assert (g["RECORD_COUNT"] == g["N_FILAS_ORIGEN"]).all()
    assert (g["NAME_VARIATIONS"] == g["N_VARIANTES_NOMBRE"]).all()
    assert (g["SOURCES_COUNT"] == 1).all() and (g["NIT_VARIATIONS"] == 0).all()
    assert float(g["FOB"].sum()) == pytest.approx(float(_base()["FOB"].sum()))


# ── Revisión ──────────────────────────────────────────────────────────────


def test_revision_tiene_la_forma_del_archivo_de_decisiones(resultado) -> None:
    r = resultado.revision
    assert list(r.columns) == list(contrato.COLUMNAS_REVISION)
    assert "MOTIVO_REVISION" in resultado.revision_grupos.columns
    assert len(resultado.revision_grupos) >= 2, "los dos '0' siguen marcados como grupo"


def test_revision_emite_un_par_lider_miembro_por_estrella_marcada() -> None:
    """Un grupo con cohesión baja produce un par por miembro, con claves de la correlativa."""
    filas = [
        ("ACME TRADING LLC", "PERU", 1.0),
        ("ACME TRADING LLC A", "PERU", 1.0),
        ("ACME TRADIN", "PERU", 1.0),  # similitud ≈ 0,80: bajo umbral + 0,03 → cohesion_baja
        ("BETA INC", "PERU", 1.0),
    ]
    r = deduplicar_importadores(
        pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"]), _cfg(umbral_nombre=0.78)
    )
    assert r.todo_ok, r.invariantes.to_string(index=False)
    c = r.correlativa
    grupo = c[c.RAZON_SOCIAL.str.startswith("ACME")]
    assert grupo["ID_GRUPO"].nunique() == 1
    assert r.revision_grupos["MOTIVO_REVISION"].tolist() == ["cohesion_baja;"]
    rev = r.revision
    assert list(rev.columns) == list(contrato.COLUMNAS_REVISION)
    assert len(rev) == 2, rev
    lider = grupo.loc[grupo.SIM_AL_FINAL == 1.0, "ID_REGISTRO"].iloc[0]
    assert (rev["CLAVE_A"] == lider).all()
    assert set(rev["CLAVE_B"]) == set(grupo.loc[grupo.SIM_AL_FINAL < 1.0, "ID_REGISTRO"])
    assert (rev["TIPO"] == "duplicado").all() and (rev["FUENTE"] == "IMPORTADORES").all()
    nombre_lider = grupo.loc[grupo.SIM_AL_FINAL == 1.0, "RAZON_SOCIAL"].iloc[0]
    assert (rev["NOMBRE_A"] == nombre_lider).all(), (
        "el nombre es el del MISMO registro que la clave"
    )
    assert set(rev["NOMBRE_B"]) == set(grupo.loc[grupo.SIM_AL_FINAL < 1.0, "RAZON_SOCIAL"])
    assert rev["DECISION"].isna().all() and rev["AUTOR"].isna().all()
    assert rev["RAZON"].str.startswith("cohesion_baja · similitud al líder 0.").all()


# ── Contrato, escritura y lectura ─────────────────────────────────────────


def test_invariante_del_contrato_se_mide(resultado) -> None:
    inv = resultado.invariantes.set_index("invariante")["cumple"]
    assert bool(inv["cumple el contrato de salida 1.0"])
    assert resultado.todo_ok


def test_a_resultado_linkage_valida_estricto(resultado) -> None:
    res = resultado.a_resultado_linkage()
    res.validar(estricto=True)
    assert res.manifiesto["funcion"] == "deduplicar_importadores"
    assert res.manifiesto["entradas"]["IMPORTADORES"]["filas"] == len(_base())
    assert res.manifiesto["configuracion"]["perfil"] == "importadores"
    assert res.metricas["n_registros"] == len(_base())


def test_escribir_importadores_deja_la_carpeta_del_estandar(resultado, tmp_path: Path) -> None:
    man = escribir_importadores(resultado, tmp_path, "importadores", marca_tiempo=MARCA)
    carpeta = man.carpeta
    assert carpeta.name == "2026-10-06_1500_importadores"
    for nombre in (
        "correlativa.parquet",
        "golden.parquet",
        "entidades_ids.parquet",
        "revision.csv",
        "diccionario.csv",
        "manifest.json",
        "excel/correlativa.xlsx",
        "excel/golden.xlsx",
    ):
        assert (carpeta / nombre).is_file(), nombre
    assert not (carpeta / "enlaces.parquet").exists()
    leido = leer_resultado(carpeta)
    assert leido.validar().ok, leido.validar().resumen()
    assert len(leido.correlativa) == len(_base())
    assert (
        leido.correlativa["ID_IMPORTADOR"].tolist()
        == resultado.correlativa["ID_IMPORTADOR"].tolist()
    )
    assert man.conteos["entidades_sin_nit"] == man.conteos["grupos"]
    assert resultado.carpeta_salida == carpeta


def test_diccionario_explica_las_columnas_adicionales(resultado, tmp_path: Path) -> None:
    man = escribir_importadores(resultado, tmp_path, "importadores", marca_tiempo=MARCA)
    dic = pd.read_csv(man.carpeta / "diccionario.csv", dtype="string", keep_default_na=False)
    correl = dic[dic["tabla"] == "correlativa"].set_index("columna")
    golden = dic[dic["tabla"] == "golden"].set_index("columna")
    assert set(resultado.correlativa.columns) <= set(correl.index)
    assert set(resultado.golden.columns) <= set(golden.index)
    for col in COLUMNAS_PROPIAS:
        if col in correl.index:
            assert "fuera del contrato" not in correl.loc[col, "significado"], col
    assert "país" in correl.loc["ID_IMPORTADOR", "significado"].lower()
    assert "exacta" in correl.loc["ID_EMPRESA_GLOBAL", "significado"].lower()
    assert correl.loc["REF", "significado"] == "Columna de la fuente, sin cambios."
    assert correl.loc["REF", "origen"] == "fuente"
    assert "suma" in golden.loc["FOB", "significado"].lower()
    assert "fuera del contrato" not in golden.loc["ID_IMPORTADOR", "significado"]


def test_deduplicar_con_carpeta_salida_escribe_de_una_vez(tmp_path: Path) -> None:
    r = deduplicar_importadores(_base(), _cfg(), carpeta_salida=tmp_path, nombre="corrida")
    assert r.carpeta_salida is not None and r.carpeta_salida.parent == tmp_path
    assert (r.carpeta_salida / "manifest.json").is_file()


def test_col_id_da_el_id_registro_nativo() -> None:
    r = deduplicar_importadores(_base(), _cfg(col_id="REF", nombre_fuente="DIAN"))
    assert r.correlativa["ID_REGISTRO"].tolist() == [f"DIAN-{x}" for x in _base()["REF"]]
    assert r.correlativa["SRC"].eq("DIAN").all()
    assert r.revision.empty or (r.revision["FUENTE"] == "DIAN").all()


def test_col_id_repetido_cae_a_la_fila_y_lo_declara() -> None:
    df = _base()
    df.loc[1, "REF"] = "r1"
    r = deduplicar_importadores(df, _cfg(col_id="REF"))
    assert r.correlativa["ID_REGISTRO"].str.startswith("IMPORTADORES-F").all()
    regla = r.manifiesto["completar"]["id_registro"]["IMPORTADORES"]
    assert regla["regla"] == "fila" and "repetido" in regla["motivo"]


def test_columna_de_la_fuente_que_choca_se_conserva_renombrada() -> None:
    df = _base().assign(ID_GRUPO="viejo", PAIS_FINAL="x")
    assert renombres_por_colision(df, _cfg()) == {
        "ID_GRUPO": "ID_GRUPO_FUENTE",
        "PAIS_FINAL": "PAIS_FINAL_FUENTE",
    }
    r = deduplicar_importadores(df, _cfg())
    assert r.todo_ok
    assert (r.correlativa["ID_GRUPO_FUENTE"] == "viejo").all()
    assert (r.correlativa["PAIS_FINAL_FUENTE"] == "x").all()
    assert r.manifiesto["completar"]["renombres"] == {
        "ID_GRUPO": "ID_GRUPO_FUENTE",
        "PAIS_FINAL": "PAIS_FINAL_FUENTE",
    }


def test_columna_declarada_que_choca_falla_con_mensaje() -> None:
    df = _base().rename(columns={"PAIS": "PAIS_FINAL"})
    cfg = ConfigImportadores(col_razon_social="RAZON_SOCIAL", col_pais="PAIS_FINAL", verboso=False)
    with pytest.raises(ContratoSalidaError, match="PAIS_FINAL"):
        deduplicar_importadores(df, cfg)
