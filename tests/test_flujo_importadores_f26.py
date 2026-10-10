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

import json
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


def _base_cohesion_baja() -> pd.DataFrame:
    """Un grupo de tres grafías con una arista ≈ 0,80 (bajo umbral 0,78 + 0,03 → cohesion_baja)."""
    filas = [
        ("ACME TRADING LLC", "PERU", 1.0, "r1"),
        ("ACME TRADING LLC A", "PERU", 1.0, "r2"),
        ("ACME TRADIN", "PERU", 1.0, "r3"),
        ("BETA INC", "PERU", 1.0, "r4"),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB", "REF"])


def _base_grupo_grande_de_un_solo_nombre() -> pd.DataFrame:
    """16 grafías (una letra en minúscula distinta cada vez) que normalizan al MISMO nombre."""
    letras = [i for i, ch in enumerate("ACME TRADING COMPANY LLC") if ch != " "]
    grafias = [
        "".join(
            ch.lower() if i == letras[k] else ch for i, ch in enumerate("ACME TRADING COMPANY LLC")
        )
        for k in range(16)
    ]
    assert len(set(grafias)) == 16
    return pd.DataFrame(
        [(g, "PERU", 1.0) for g in grafias] + [("BETA INC", "PERU", 1.0)],
        columns=["RAZON_SOCIAL", "PAIS", "FOB"],
    )


def test_revision_emite_un_par_lider_miembro_por_estrella_marcada() -> None:
    """Un grupo con cohesión baja produce un par por miembro, con claves de la correlativa."""
    r = deduplicar_importadores(_base_cohesion_baja(), _cfg(umbral_nombre=0.78))
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


def test_grupo_marcado_sin_segundo_nombre_normalizado_no_genera_par_y_se_cuenta() -> None:
    """16 grafías de un mismo NOMBRE_NORM: grupo_grande con líder y cero miembros.

    revision.csv solo lleva pares entre representantes (país, NOMBRE_NORM) y
    aquí hay uno solo: la marca sobrevive en ``revision_grupos`` y la métrica
    «grupos marcados sin par en revision.csv» la hace visible.
    """
    r = deduplicar_importadores(_base_grupo_grande_de_un_solo_nombre(), _cfg())
    assert r.todo_ok, r.invariantes.to_string(index=False)
    assert r.correlativa["ID_GRUPO"].nunique() == 2
    assert r.revision_grupos["MOTIVO_REVISION"].tolist() == ["grupo_grande;"]
    assert len(r.revision) == 0 and list(r.revision.columns) == list(contrato.COLUMNAS_REVISION)
    m = r.metricas.set_index("indicador")["valor"]
    assert m["grupos marcados para revisión"] == "1"
    assert m["pares (líder, miembro) en revision.csv"] == "0"
    assert m["grupos marcados sin par en revision.csv"] == "1"
    assert r.manifiesto["flujo"]["grupos_marcados_sin_par"] == 1
    assert "no generan par" in r.manifiesto["completar"]["revision"]["regla"]


def test_los_grupos_marcados_con_par_no_se_cuentan_como_sin_par() -> None:
    r = deduplicar_importadores(_base_cohesion_baja(), _cfg(umbral_nombre=0.78))
    m = r.metricas.set_index("indicador")["valor"]
    assert m["grupos marcados para revisión"] == "1"
    assert m["grupos marcados sin par en revision.csv"] == "0"


# ── Hoja REVISION de tablas(): nombre de v1 → contenido de v1 ─────────────


def test_tablas_conserva_revision_de_v1_y_publica_los_pares_aparte(resultado) -> None:
    """docs/CONSUMIDORES.md 3.8.i.5: MOTIVO_REVISION y compañía son nombres de v1
    que terceros recibieron en la hoja REVISION del 07; el archivo de decisiones
    del estándar (``.revision`` / ``revision.csv``) va como hoja REVISION_PARES."""
    tablas = resultado.tablas()
    assert "MOTIVO_REVISION" in tablas["REVISION"].columns
    assert tablas["REVISION"] is resultado.revision_grupos
    assert list(tablas["REVISION_PARES"].columns) == list(contrato.COLUMNAS_REVISION)
    assert tablas["REVISION_PARES"] is resultado.revision
    assert "REVISION_GRUPOS" not in tablas


# ── CREATED_AT: una marca por corrida, inyectable ─────────────────────────


def test_created_at_es_una_marca_por_corrida_e_inyectable() -> None:
    from record_linkage.flujo.importadores import construir_entregables, ejecutar, preparar

    cfg = _cfg()
    prep = preparar(_base(), cfg)
    corrida = ejecutar(prep, cfg)
    assert corrida["creado_en"], "ejecutar fija la marca una sola vez por corrida"
    corrida["creado_en"] = "2026-10-06 15:00:00"
    golden = construir_entregables(prep, corrida, cfg)["GOLDEN"]
    assert golden["CREATED_AT"].eq("2026-10-06 15:00:00").all()
    r = deduplicar_importadores(_base(), cfg)
    assert r.golden["CREATED_AT"].nunique() == 1
    datetime.strptime(r.golden["CREATED_AT"].iloc[0], "%Y-%m-%d %H:%M:%S")


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
    # cols_metricas NO llegan «sin cambios»: preparar las convierte a numérico (NaN → 0).
    assert "numérica" in correl.loc["FOB", "significado"]
    assert "0" in correl.loc["FOB", "significado"]
    assert correl.loc["FOB", "origen"] == "fuente"
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


def test_revision_lleva_el_nombre_de_la_fuente_y_sus_claves() -> None:
    """Con una base que SÍ produce pares (cohesión baja): FUENTE y las claves son de la fuente."""
    r = deduplicar_importadores(
        _base_cohesion_baja(), _cfg(umbral_nombre=0.78, col_id="REF", nombre_fuente="DIAN")
    )
    assert len(r.revision) == 2 and (r.revision["FUENTE"] == "DIAN").all()
    assert r.revision["CLAVE_A"].str.startswith("DIAN-r").all()
    assert r.revision["CLAVE_B"].str.startswith("DIAN-r").all()


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


# ── Ronda 1 de revisión ───────────────────────────────────────────────────


def test_manifiesto_declara_la_regla_real_de_id_registro() -> None:
    """Con col_id único cuyos valores empiezan por 'F' el manifiesto no dice «fila»."""
    df = _base()
    df["REF"] = [f"F{i:02d}" for i in range(1, len(df) + 1)]
    r = deduplicar_importadores(df, _cfg(col_id="REF"))
    assert r.correlativa["ID_REGISTRO"].tolist() == [f"IMPORTADORES-F{i:02d}" for i in range(1, 10)]
    assert r.manifiesto["completar"]["id_registro"]["IMPORTADORES"] == {
        "regla": "col_id",
        "columna": "REF",
    }


def test_sin_col_id_el_manifiesto_lo_dice() -> None:
    r = deduplicar_importadores(_base(), _cfg())
    assert r.manifiesto["completar"]["id_registro"]["IMPORTADORES"] == {
        "regla": "fila",
        "motivo": "sin col_id",
    }


@pytest.mark.parametrize("interna", ["ES_LIDER", "NOMBRE_NORM_FINAL"])
def test_columna_interna_del_merge_en_la_fuente_se_conserva_renombrada(interna: str) -> None:
    df = _base().assign(**{interna: 1})
    assert renombres_por_colision(df, _cfg()) == {interna: f"{interna}_FUENTE"}
    r = deduplicar_importadores(df, _cfg())
    assert r.todo_ok
    assert (r.correlativa[f"{interna}_FUENTE"] == 1).all()
    assert interna not in r.correlativa.columns
    assert r.manifiesto["completar"]["renombres"] == {interna: f"{interna}_FUENTE"}


def test_sufijo_de_colision_es_el_de_salida_completar() -> None:
    """Una regla se escribe una vez: el sufijo viene de salida.completar, sin copia local."""
    import inspect
    import re

    from record_linkage.flujo import importadores
    from record_linkage.salida import completar

    assert importadores._SUFIJO_COLISION == completar._SUFIJO_COLISION
    fuente = inspect.getsource(importadores)
    assert re.search(r"^_SUFIJO_COLISION\s*=", fuente, re.M) is None
    assert "_SUFIJO_COLISION" in fuente.split("from ..salida.completar import", 1)[1][:200]


# ── Ronda 2: un solo vocabulario, marca inyectable y el 07 arma con la librería ──


def test_construir_entregables_habla_el_vocabulario_de_tablas() -> None:
    """``construir_entregables`` y ``tablas()`` nombran igual: ``REVISION`` son los
    GRUPOS (contenido de v1) y ``REVISION_PARES`` el archivo de decisiones."""
    from record_linkage.flujo.importadores import construir_entregables, ejecutar, preparar

    cfg = _cfg()
    prep = preparar(_base(), cfg)
    t = construir_entregables(prep, ejecutar(prep, cfg), cfg)
    assert list(t["REVISION_PARES"].columns) == list(contrato.COLUMNAS_REVISION)
    assert "MOTIVO_REVISION" in t["REVISION"].columns
    assert "REVISION_GRUPOS" not in t
    r = deduplicar_importadores(_base(), cfg)
    assert r.tablas()["REVISION"] is r.revision_grupos
    assert r.tablas()["REVISION_PARES"] is r.revision


def test_marca_tiempo_inyectable_en_la_fachada_fija_golden_y_carpeta(tmp_path: Path) -> None:
    """Dos corridas con la misma ``marca_tiempo`` dan el mismo golden (CREATED_AT
    incluido) y la carpeta del estándar usa ESA marca, no otra lectura del reloj."""
    cfg = _cfg()
    a = deduplicar_importadores(_base(), cfg, marca_tiempo=MARCA)
    b = deduplicar_importadores(_base(), cfg, marca_tiempo=MARCA)
    assert a.golden.equals(b.golden)
    r = deduplicar_importadores(_base(), cfg, carpeta_salida=tmp_path, marca_tiempo=MARCA)
    assert r.golden["CREATED_AT"].eq(MARCA.strftime("%Y-%m-%d %H:%M:%S")).all()
    assert r.carpeta_salida is not None
    assert r.carpeta_salida.name == "2026-10-06_1500_importadores"
    manifest = json.loads((r.carpeta_salida / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["marca_tiempo"] == MARCA.isoformat(timespec="seconds")


def _sin_reloj(metricas: pd.DataFrame) -> pd.DataFrame:
    return metricas[metricas["indicador"] != "segundos de ejecución"].reset_index(drop=True)


def test_ensamblar_resultado_arma_lo_mismo_que_la_fachada() -> None:
    """El 07 corre las etapas una a una (para cronometrarlas) y arma el resultado
    con ``ensamblar_resultado``: hoja ``REVISION`` de v1 llena, manifiesto y
    diccionario presentes, estándar escribible. Antes armaba
    ``ResultadoImportadores(...)`` a mano y perdía las tres cosas."""
    from record_linkage.flujo.importadores import (
        construir_entregables,
        ejecutar,
        ensamblar_resultado,
        muestra_para_revision,
        preparar,
    )

    cfg = _cfg()
    df = _base()
    prep = preparar(df, cfg)
    corrida = ejecutar(prep, cfg, marca_tiempo=MARCA)
    tablas = construir_entregables(prep, corrida, cfg)
    r = ensamblar_resultado(
        df, prep, corrida, tablas, cfg, muestra=muestra_para_revision(tablas, cfg), extra={}
    )
    hojas = r.tablas()
    assert "MOTIVO_REVISION" in hojas["REVISION"].columns and len(hojas["REVISION"]) >= 2
    assert list(hojas["REVISION_PARES"].columns) == list(contrato.COLUMNAS_REVISION)
    assert r.manifiesto["funcion"] == "deduplicar_importadores"
    assert not r.diccionario.empty
    r.a_resultado_linkage().validar(estricto=True)
    assert r.todo_ok
    fachada = deduplicar_importadores(df, cfg, marca_tiempo=MARCA)
    assert r.golden.equals(fachada.golden)
    assert r.correlativa.equals(fachada.correlativa)
    assert r.revision_grupos.equals(fachada.revision_grupos)
    # Sin la fila de reloj (pulido F2, hallazgo 27 de F2.6): «segundos de
    # ejecución» es round() de cada corrida y bajo carga una cruza 0,5 s.
    assert _sin_reloj(r.metricas).equals(_sin_reloj(fachada.metricas))
    assert "segundos de ejecución" in set(r.metricas["indicador"])
