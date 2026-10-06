"""``ID_ENTIDAD`` estable entre corridas (F2.4, ADR-0012): ``identidad/estable.py``.

Qué congela
-----------
Sobre correlativas mínimas inventadas (grupos de ``ID_REGISTRO``) y el
crosswalk ``entidades_ids.parquet`` escrito con el contrato:

* (a) misma entrada → mismos ids, con y sin corrida anterior; sin corrida
  anterior el ``ENT-…`` es el de F1 (SHA-256 de los ``ID_REGISTRO`` ordenados);
* (b) una fila nueva que se une a un grupo → el grupo conserva su id;
* (c) fusión → sobrevive el id del grupo anterior mayor (empate: menor id) y
  el retirado queda en el crosswalk como ``RETIRADO_EN → sobreviviente``;
* (d) división → conserva el id la parte con más registros compartidos; la
  otra recibe id nuevo y nada se retira;
* un grupo ``ENT-…`` que adopta un identificador pasa a ``NIT-…`` y el
  ``ENT-…`` queda retirado hacia el ``NIT-…``;
* los retiros de corridas anteriores se arrastran y las cadenas se comprimen
  (``X → Y`` y luego ``Y → Z`` deja ``X → Z``);
* un id retirado no resucita: si el contenido vuelve a dar ese hash, el grupo
  recibe otro id (determinista) y el reporte lo cuenta;
* una entidad anterior sin ningún registro en esta corrida desaparece del
  crosswalk activo y el reporte la cuenta;
* ``cargar_corrida_anterior`` falla rápido (ruta inexistente, crosswalk sin
  ``REGISTROS``) con mensaje accionable;
* ``linkage(corrida_anterior=…)`` hereda desde la carpeta del estándar de la
  corrida previa, el manifiesto declara la huella usada y el escritor publica
  el crosswalk con ``REGISTROS`` y ``RETIRADO_EN``;
* ``ConfigCruce(corrida_anterior=…)`` valida la ruta al construirse.

Las empresas son inventadas. Ningún dato licenciado entra aquí.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from test_contrato_salida import _correr_linkage, _silencio, conjunto_sintetico

import record_linkage as rl
from record_linkage import contrato
from record_linkage.exporters.escritor import escribir_parquet, leer_resultado
from record_linkage.identidad.estable import (
    COLUMNA_REGISTROS,
    NOMBRE_CROSSWALK,
    SEPARADOR_REGISTROS,
    CorridaAnterior,
    ResultadoIdentidad,
    asignar_id_entidad,
    cargar_corrida_anterior,
    crosswalk_de_corrida,
    localizar_crosswalk,
)
from record_linkage.pipeline.errores import CorridaAnteriorError

# ─────────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────────


def _correl(grupos: dict[int, list[str]]) -> pd.DataFrame:
    filas = [(g, r) for g, registros in grupos.items() for r in registros]
    return pd.DataFrame(filas, columns=["ID_GRUPO", "ID_REGISTRO"])


def _asignar(
    grupos: dict[int, list[str]],
    nit: dict[int, str] | None = None,
    anterior: CorridaAnterior | None = None,
) -> ResultadoIdentidad:
    correl = _correl(grupos)
    base = correl["ID_GRUPO"].map(nit or {}).astype("string")
    return asignar_id_entidad(correl, base, base.notna().to_numpy(), corrida_anterior=anterior)


def _guardar(res: ResultadoIdentidad, carpeta: Path) -> CorridaAnterior:
    carpeta.mkdir(parents=True, exist_ok=True)
    escribir_parquet(res.entidades_ids, carpeta / NOMBRE_CROSSWALK, contrato.ENTIDADES_IDS)
    return cargar_corrida_anterior(carpeta)


def _ent_por_contenido(registros: list[str]) -> str:
    clave = SEPARADOR_REGISTROS.join(sorted(registros))
    return "ENT-" + hashlib.sha256(clave.encode("utf-8")).hexdigest()[:16]


def _id_de(res: ResultadoIdentidad, grupo: int) -> str:
    x = res.entidades_ids
    activas = x[x["RETIRADO_EN"].isna()].set_index("ID_GRUPO")
    return str(activas.loc[grupo, "ID_ENTIDAD"])


def _retirados(res: ResultadoIdentidad) -> pd.DataFrame:
    x = res.entidades_ids
    return x[x["RETIRADO_EN"].notna()].set_index("ID_ENTIDAD")


# ─────────────────────────────────────────────────────────────────────────────
# (a) determinismo
# ─────────────────────────────────────────────────────────────────────────────


def test_sin_corrida_anterior_el_id_es_el_de_f1_por_contenido() -> None:
    res = _asignar({1: ["B-2", "A-1"], 2: ["C-9"]}, nit={2: "900111222"})
    assert _id_de(res, 1) == _ent_por_contenido(["A-1", "B-2"])
    assert _id_de(res, 2) == "NIT-900111222"
    assert res.reporte["corrida_anterior"] is None and "motivo" in res.reporte
    otra = _asignar({1: ["B-2", "A-1"], 2: ["C-9"]}, nit={2: "900111222"})
    pd.testing.assert_series_equal(res.id_entidad, otra.id_entidad)


def test_misma_entrada_y_misma_corrida_anterior_mismos_ids(tmp_path: Path) -> None:
    previa = _guardar(_asignar({1: ["A-1", "A-2"], 2: ["B-1"]}), tmp_path / "r1")
    uno = _asignar({7: ["A-1", "A-2", "A-3"], 8: ["B-1"], 9: ["Z-1"]}, anterior=previa)
    dos = _asignar({7: ["A-1", "A-2", "A-3"], 8: ["B-1"], 9: ["Z-1"]}, anterior=previa)
    pd.testing.assert_series_equal(uno.id_entidad, dos.id_entidad)
    pd.testing.assert_frame_equal(uno.entidades_ids, dos.entidades_ids)


# ─────────────────────────────────────────────────────────────────────────────
# (b) fila nueva · (c) fusión · (d) división
# ─────────────────────────────────────────────────────────────────────────────


def test_fila_nueva_conserva_el_id_del_grupo(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2"]})
    previa = _guardar(r1, tmp_path / "r1")
    r2 = _asignar({5: ["A-1", "A-2", "A-3"]}, anterior=previa)
    assert _id_de(r2, 5) == _id_de(r1, 1)
    assert _id_de(r2, 5) != _ent_por_contenido(["A-1", "A-2", "A-3"])
    assert r2.reporte["heredadas"] == 1 and r2.reporte["nuevas"] == 0
    assert r2.reporte["retiradas"] == 0
    assert _retirados(r2).empty


def test_fusion_sobrevive_el_mayor_y_el_otro_queda_retirado(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]})
    previa = _guardar(r1, tmp_path / "r1")
    r2 = _asignar({4: ["A-1", "A-2", "A-3", "B-1", "B-2"]}, anterior=previa)
    mayor, menor = _id_de(r1, 1), _id_de(r1, 2)
    assert _id_de(r2, 4) == mayor
    retirados = _retirados(r2)
    assert list(retirados.index) == [menor]
    assert retirados.loc[menor, "RETIRADO_EN"] == mayor
    assert int(retirados.loc[menor, "ID_GRUPO"]) == 4
    assert int(retirados.loc[menor, "N_REGISTROS"]) == 0
    assert pd.isna(retirados.loc[menor, COLUMNA_REGISTROS])
    assert r2.reporte["retiradas"] == 1 and r2.reporte["heredadas"] == 1


def test_fusion_con_empate_sobrevive_el_id_menor(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2"], 2: ["B-1", "B-2"]})
    previa = _guardar(r1, tmp_path / "r1")
    r2 = _asignar({4: ["A-1", "A-2", "B-1", "B-2"]}, anterior=previa)
    assert _id_de(r2, 4) == min(_id_de(r1, 1), _id_de(r1, 2))


def test_division_conserva_el_id_la_parte_con_mas_registros(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2", "A-3", "B-1", "B-2"]})
    previa = _guardar(r1, tmp_path / "r1")
    r2 = _asignar({10: ["B-1", "B-2"], 11: ["A-1", "A-2", "A-3"]}, anterior=previa)
    assert _id_de(r2, 11) == _id_de(r1, 1)
    assert _id_de(r2, 10) == _ent_por_contenido(["B-1", "B-2"])
    assert _retirados(r2).empty
    assert r2.reporte["heredadas"] == 1 and r2.reporte["nuevas"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Transiciones y mantenimiento del crosswalk
# ─────────────────────────────────────────────────────────────────────────────


def test_grupo_que_adopta_identificador_pasa_a_nit_y_retira_el_ent(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2"]})
    previa = _guardar(r1, tmp_path / "r1")
    r2 = _asignar({1: ["A-1", "A-2", "A-3"]}, nit={1: "800333444"}, anterior=previa)
    assert _id_de(r2, 1) == "NIT-800333444"
    retirados = _retirados(r2)
    assert retirados.loc[_id_de(r1, 1), "RETIRADO_EN"] == "NIT-800333444"
    assert r2.reporte["ent_a_nit"] == 1


def test_los_retiros_anteriores_se_arrastran_y_las_cadenas_se_comprimen(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"], 3: [f"C-{i}" for i in range(6)]})
    e1, e2, e3 = (_id_de(r1, g) for g in (1, 2, 3))
    r2 = _asignar(
        {1: ["A-1", "A-2", "A-3", "B-1", "B-2"], 3: [f"C-{i}" for i in range(6)]},
        anterior=_guardar(r1, tmp_path / "r1"),
    )
    assert _retirados(r2).loc[e2, "RETIRADO_EN"] == e1
    r3 = _asignar(
        {1: ["A-1", "A-2", "A-3", "B-1", "B-2"] + [f"C-{i}" for i in range(6)]},
        anterior=_guardar(r2, tmp_path / "r2"),
    )
    assert _id_de(r3, 1) == e3
    retirados = _retirados(r3)
    assert retirados.loc[e1, "RETIRADO_EN"] == e3
    assert retirados.loc[e2, "RETIRADO_EN"] == e3, "la cadena E2 → E1 → E3 se comprime"
    assert int(retirados.loc[e2, "ID_GRUPO"]) == 1
    assert r3.entidades_ids["ID_ENTIDAD"].is_unique


def test_un_id_retirado_no_resucita(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]})
    e2 = _id_de(r1, 2)
    r2 = _asignar({1: ["A-1", "A-2", "A-3", "B-1", "B-2"]}, anterior=_guardar(r1, tmp_path / "r1"))
    r3 = _asignar(
        {1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]}, anterior=_guardar(r2, tmp_path / "r2")
    )
    assert _id_de(r3, 2) != e2 and _id_de(r3, 2).startswith("ENT-")
    assert _retirados(r3).loc[e2, "RETIRADO_EN"] == _id_de(r1, 1)
    assert r3.reporte["colisiones_resueltas"] == 1
    assert r3.entidades_ids["ID_ENTIDAD"].is_unique
    # Determinista: la misma situación da el mismo id alternativo.
    r3b = _asignar(
        {1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]}, anterior=_guardar(r2, tmp_path / "r2b")
    )
    assert _id_de(r3b, 2) == _id_de(r3, 2)


def test_entidad_anterior_sin_registros_desaparece_y_se_cuenta(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1"], 2: ["B-1", "B-2"]})
    r2 = _asignar({1: ["A-1"]}, anterior=_guardar(r1, tmp_path / "r1"))
    assert set(r2.entidades_ids["ID_ENTIDAD"]) == {_id_de(r1, 1)}
    assert r2.reporte["desaparecidas"] == 1


def test_un_nit_anterior_no_se_hereda_a_un_grupo_sin_identificador(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2"]}, nit={1: "900111222"})
    r2 = _asignar({1: ["A-1", "A-2"]}, anterior=_guardar(r1, tmp_path / "r1"))
    assert _id_de(r2, 1) == _ent_por_contenido(["A-1", "A-2"])
    assert r2.reporte["nit_anteriores_ausentes"] == 1
    assert _retirados(r2).empty


def test_crosswalk_de_corrida_lleva_los_registros_ordenados() -> None:
    correl = _correl({3: ["Z-1", "A-1"], 1: ["M-5"]})
    correl["ID_ENTIDAD"] = correl["ID_GRUPO"].map({3: "ENT-x", 1: "NIT-1"})
    x = crosswalk_de_corrida(correl)
    assert list(x.columns) == list(contrato.COLUMNAS_ENTIDADES_IDS)
    assert x["ID_GRUPO"].tolist() == [1, 3]
    assert x[COLUMNA_REGISTROS].tolist() == ["M-5", "A-1|Z-1"]
    assert x["RETIRADO_EN"].isna().all() and x["N_REGISTROS"].tolist() == [1, 2]


# ─────────────────────────────────────────────────────────────────────────────
# Carga de la corrida anterior: fail-fast
# ─────────────────────────────────────────────────────────────────────────────


def test_cargar_corrida_anterior_falla_rapido_si_no_existe(tmp_path: Path) -> None:
    with pytest.raises(CorridaAnteriorError, match="Qué hacer"):
        cargar_corrida_anterior(tmp_path / "no_existe")


def test_cargar_corrida_anterior_rechaza_crosswalk_sin_registros(tmp_path: Path) -> None:
    viejo = pd.DataFrame(
        {
            "ID_ENTIDAD": pd.Series(["ENT-0123456789abcdef"], dtype="string"),
            "ID_GRUPO": pd.Series([0], dtype="int64"),
            "N_REGISTROS": pd.Series([2], dtype="int64"),
            "RETIRADO_EN": pd.Series([pd.NA], dtype="string"),
        }
    )
    viejo.to_parquet(tmp_path / NOMBRE_CROSSWALK)
    with pytest.raises(CorridaAnteriorError, match=COLUMNA_REGISTROS):
        cargar_corrida_anterior(tmp_path / NOMBRE_CROSSWALK)


def test_localizar_crosswalk_acepta_carpeta_o_archivo(tmp_path: Path) -> None:
    res = _asignar({1: ["A-1"]})
    _guardar(res, tmp_path / "r1")
    assert localizar_crosswalk(tmp_path / "r1") == tmp_path / "r1" / NOMBRE_CROSSWALK
    assert localizar_crosswalk(tmp_path / "r1" / NOMBRE_CROSSWALK) == (
        tmp_path / "r1" / NOMBRE_CROSSWALK
    )
    previa = cargar_corrida_anterior(tmp_path / "r1")
    esperada = hashlib.sha256((tmp_path / "r1" / NOMBRE_CROSSWALK).read_bytes()).hexdigest()
    assert previa.huella_sha256 == esperada


# ─────────────────────────────────────────────────────────────────────────────
# linkage(corrida_anterior=…) y ConfigCruce
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_linkage_hereda_desde_la_carpeta_de_la_corrida_anterior(tmp_path: Path) -> None:
    uno = _correr_linkage(tmp_path / "t1", carpeta_salida=tmp_path / "salida", nombre="uno")
    carpeta_uno = Path(uno.manifiesto["carpeta_salida"])
    crosswalk_uno = pd.read_parquet(carpeta_uno / NOMBRE_CROSSWALK)
    assert list(crosswalk_uno.columns) == list(contrato.COLUMNAS_ENTIDADES_IDS)
    assert crosswalk_uno[COLUMNA_REGISTROS].notna().all()

    # Segunda corrida: una fila nueva en una entidad SIN_NIT y una entidad
    # SIN_NIT que desaparece. Los ids de los grupos que siguen se conservan.
    fuentes = conjunto_sintetico()
    a = fuentes["CRM"]
    sin_nit = a[a["NIT"].eq("")]
    primera = sin_nit.iloc[0]
    nueva = primera.copy()
    nueva["CODIGO"] = "A-NUEVA"
    retirada = sin_nit.iloc[-1]["RAZON_SOCIAL"]
    a = pd.concat([a[a["RAZON_SOCIAL"] != retirada], nueva.to_frame().T], ignore_index=True)
    fuentes["CRM"] = a.astype(str)

    with _silencio():
        dos = rl.linkage(
            fuentes,
            work_dir=str(tmp_path / "t2"),
            skip_reporting=True,
            col_ciudad="CIUDAD",
            col_id="CODIGO",
            carpeta_salida=tmp_path / "salida",
            nombre="dos",
            corrida_anterior=carpeta_uno,
        )
    c1 = uno.correlativa.set_index("ID_REGISTRO")["ID_ENTIDAD"]
    c2 = dos.correlativa.set_index("ID_REGISTRO")["ID_ENTIDAD"]
    comunes = c1.index.intersection(c2.index)
    assert len(comunes) > 0
    assert c1.loc[comunes].equals(c2.loc[comunes]), "los grupos que siguen conservan su id"
    nuevo = c2.loc["CRM-A-NUEVA"]
    assert nuevo == c1.loc[c1.index[c1.index.str.endswith(f"-{primera['CODIGO']}")][0]]

    herencia = dos.manifiesto["completar"]["id_entidad"]["herencia"]
    esperada = hashlib.sha256((carpeta_uno / NOMBRE_CROSSWALK).read_bytes()).hexdigest()
    assert herencia["huella_sha256"] == esperada
    assert herencia["corrida_anterior"] == str(carpeta_uno / NOMBRE_CROSSWALK)
    assert herencia["heredadas"] >= 1 and herencia["desaparecidas"] >= 1
    assert dos.manifiesto["parametros"]["corrida_anterior"] == str(carpeta_uno)
    assert dos.entidades_ids is not None

    carpeta_dos = Path(dos.manifiesto["carpeta_salida"])
    publicado = pd.read_parquet(carpeta_dos / NOMBRE_CROSSWALK)
    pd.testing.assert_frame_equal(
        publicado.astype("string"), dos.entidades_ids.astype("string"), check_dtype=False
    )
    leido = leer_resultado(carpeta_dos)
    assert leido.entidades_ids is not None and len(leido.entidades_ids) == len(publicado)


def test_linkage_corrida_anterior_inexistente_falla_antes_de_correr(tmp_path: Path) -> None:
    with pytest.raises(CorridaAnteriorError):
        _correr_linkage(tmp_path / "t", corrida_anterior=tmp_path / "nada")
    assert not (tmp_path / "t").exists(), "falla antes de L1: no deja trabajo"


def test_config_cruce_valida_corrida_anterior(tmp_path: Path) -> None:
    from record_linkage.flujo.cruce import ConfigCruce
    from record_linkage.ingestion import ColumnType, SourceSpec

    fuente = tmp_path / "f.csv"
    fuente.write_text("NIT,RAZON_SOCIAL\n900111222,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="F",
        path=fuente,
        column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
        column_types={"NIT": ColumnType.IDENTIFIER},
    )
    with pytest.raises(CorridaAnteriorError):
        ConfigCruce(fuentes=[spec], workspace=tmp_path / "ws", corrida_anterior=tmp_path / "x")
    _guardar(_asignar({1: ["A-1"]}), tmp_path / "r1")
    cfg = ConfigCruce(fuentes=[spec], workspace=tmp_path / "ws", corrida_anterior=tmp_path / "r1")
    assert cfg.corrida_anterior == tmp_path / "r1"
