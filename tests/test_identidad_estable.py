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
* un id retirado no resucita mientras siga en el crosswalk: si el contenido
  vuelve a dar ese hash, el grupo recibe otro id (determinista) y el reporte lo
  cuenta; un id que salió del crosswalk (sin sobreviviente) sí puede reaparecer
  con su contenido original (comportamiento documentado en ADR-0012 §5);
* una entidad anterior sin ningún registro en esta corrida desaparece del
  crosswalk activo y el reporte la cuenta; los retiros que apuntaban a ella
  dejan de arrastrarse (todo ``RETIRADO_EN`` publicado es un id activo del
  mismo crosswalk) y el reporte los cuenta;
* en empate de registros compartidos, el destino de una entidad anterior es un
  grupo que puede heredar (sin identificador) antes que uno ``NIT-…``;
* un ``ID_REGISTRO`` que contenga ``|`` (el separador de ``REGISTROS``) se
  rechaza con ``IdRegistroInvalidoError`` (dato de entrada, no «defecto del
  motor»): ANTES de L1 en ``linkage()``/``link()``/``dedupe()`` (un valor de
  ``col_id`` o un nombre de fuente; no queda ``work_dir``) y como guardia en el
  módulo;
* ``cargar_corrida_anterior`` falla rápido (ruta inexistente, crosswalk sin
  ``REGISTROS``, un ``ID_REGISTRO`` en dos entidades activas) con mensaje
  accionable;
* ``linkage(corrida_anterior=…)`` hereda desde la carpeta del estándar de la
  corrida previa, el manifiesto declara la huella usada y el escritor publica
  el crosswalk con ``REGISTROS`` y ``RETIRADO_EN``;
* ``ConfigCruce(corrida_anterior=…)`` carga y valida el crosswalk al
  construirse (ruta inexistente y crosswalk sin ``REGISTROS`` fallan ahí);
* ``leer_resultado`` devuelve ``ID_GRUPO`` del crosswalk como ``Int64``;
* ``ResultadoLinkage.validar()`` comprueba el crosswalk: columnas del contrato,
  ``ID_ENTIDAD`` único, ``ID_GRUPO`` sin nulos, todo ``RETIRADO_EN`` apunta a
  un id activo y las activas coinciden con la correlativa.

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
    exigir_fuentes_sin_separador,
    exigir_nombres_sin_separador,
    localizar_crosswalk,
)
from record_linkage.pipeline.errores import (
    ContratoSalidaError,
    CorridaAnteriorError,
    IdRegistroInvalidoError,
)
from record_linkage.resultado import ResultadoLinkage

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


def test_un_retiro_sin_sobreviviente_deja_de_arrastrarse_y_se_cuenta(tmp_path: Path) -> None:
    """Corrida 1: E1, E2. Corrida 2: fusión → E2 retirado en E1. Corrida 3: ni A
    ni B tienen registros → E1 desaparece y E2 → E1 no puede seguir
    publicándose apuntando a un id que no está en la tabla."""
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]})
    e1, e2 = _id_de(r1, 1), _id_de(r1, 2)
    r2 = _asignar({1: ["A-1", "A-2", "A-3", "B-1", "B-2"]}, anterior=_guardar(r1, tmp_path / "r1"))
    assert _retirados(r2).loc[e2, "RETIRADO_EN"] == e1
    r3 = _asignar({9: ["Z-1"]}, anterior=_guardar(r2, tmp_path / "r2"))
    x = r3.entidades_ids
    assert e1 not in set(x["ID_ENTIDAD"]) and e2 not in set(x["ID_ENTIDAD"])
    activos = set(x.loc[x["RETIRADO_EN"].isna(), "ID_ENTIDAD"])
    assert x["RETIRADO_EN"].dropna().isin(activos).all()
    assert x["ID_GRUPO"].notna().all()
    assert r3.reporte["desaparecidas"] == 1
    assert r3.reporte["arrastres_sin_sobreviviente"] == 1
    # Con sobreviviente, el arrastre sigue intacto (y lo cuenta como 0).
    r3b = _asignar({9: ["A-1"]}, anterior=_guardar(r2, tmp_path / "r2b"))
    assert _id_de(r3b, 9) == e1 and _retirados(r3b).loc[e2, "RETIRADO_EN"] == e1
    assert r3b.reporte["arrastres_sin_sobreviviente"] == 0


def test_un_id_registro_con_el_separador_lo_rechaza_el_modulo(tmp_path: Path) -> None:
    """Guardia del módulo: un ``ID_REGISTRO`` con ``|`` no entra al crosswalk,
    y el error es el de dato de entrada (no «defecto del motor»)."""
    with pytest.raises(IdRegistroInvalidoError, match=r"1 ID_REGISTRO contienen '\|'") as exc:
        _asignar({1: ["A-x|y", "A-z"]})
    assert "col_id" in str(exc.value) and "'A-x|y'" in str(exc.value)
    assert "defecto del motor" not in str(exc.value)
    assert not isinstance(exc.value, ContratoSalidaError)
    # También con corrida anterior, y la carga de la anterior no se ve afectada.
    previa = _guardar(_asignar({1: ["A-1"]}), tmp_path / "r1")
    with pytest.raises(IdRegistroInvalidoError, match="ID_REGISTRO contienen"):
        _asignar({1: ["A-1", "B-1|2"]}, anterior=previa)
    correl = _correl({1: ["A-1|2"]})
    correl["ID_ENTIDAD"] = "ENT-x"
    with pytest.raises(IdRegistroInvalidoError, match="ID_REGISTRO contienen"):
        crosswalk_de_corrida(correl)


def test_exigir_fuentes_sin_separador_acepta_lo_valido_y_rechaza_lo_demas() -> None:
    a = pd.DataFrame({"CODIGO": ["1", "2", None], "RAZON_SOCIAL": ["X|Y", "Z", "W"]})
    # Un '|' en una columna que NO es col_id no importa; col_id ausente en
    # una fuente tampoco (completar usa <SRC>-F<fila> y lo dice).
    exigir_fuentes_sin_separador({"CRM": a, "ADU": a[["RAZON_SOCIAL"]]}, "CODIGO")
    exigir_fuentes_sin_separador({"CRM": a}, None)
    b = pd.DataFrame({"CODIGO": ["1", "X|1", "Y|2", "Z|3", "W|4"]})
    with pytest.raises(IdRegistroInvalidoError, match=r"col_id='CODIGO'.*'ADU'.*4 valor") as exc:
        exigir_fuentes_sin_separador({"CRM": a, "ADU": b}, "CODIGO")
    texto = str(exc.value)
    assert "'X|1'" in texto and "'W|4'" not in texto, "3 ejemplos, no todos"
    assert "defecto del motor" not in texto and "col_id=None" in texto
    with pytest.raises(IdRegistroInvalidoError, match=r"fuente.*'A\|B'"):
        exigir_fuentes_sin_separador({"A|B": a}, None)


def test_un_id_que_salio_del_crosswalk_puede_reaparecer_con_su_contenido(tmp_path: Path) -> None:
    """La garantía «un id retirado no resucita» vale mientras el id siga en el
    crosswalk anterior (ADR-0012 §5). Un retiro que se dejó de arrastrar por no
    tener sobreviviente (corrida 3) sale del crosswalk, y si en la corrida 4
    vuelve exactamente su contenido, vuelve su hash: no hay con qué chocar.
    Esta prueba congela ese comportamiento documentado."""
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]})
    e1, e2 = _id_de(r1, 1), _id_de(r1, 2)
    r2 = _asignar({1: ["A-1", "A-2", "A-3", "B-1", "B-2"]}, anterior=_guardar(r1, tmp_path / "r1"))
    assert _retirados(r2).loc[e2, "RETIRADO_EN"] == e1
    r3 = _asignar({9: ["Z-1"]}, anterior=_guardar(r2, tmp_path / "r2"))
    assert r3.reporte["desaparecidas"] == 1 and r3.reporte["arrastres_sin_sobreviviente"] == 1
    assert e2 not in set(r3.entidades_ids["ID_ENTIDAD"])
    r4 = _asignar({9: ["Z-1"], 5: ["B-1", "B-2"]}, anterior=_guardar(r3, tmp_path / "r3"))
    assert _id_de(r4, 5) == e2 == _ent_por_contenido(["B-1", "B-2"])
    assert r4.reporte["colisiones_resueltas"] == 0 and _retirados(r4).empty
    # Mientras el id SÍ sigue en el crosswalk (retirado y arrastrado), no resucita.
    r3b = _asignar({9: ["A-1"]}, anterior=_guardar(r2, tmp_path / "r2b"))
    r4b = _asignar({9: ["A-1"], 5: ["B-1", "B-2"]}, anterior=_guardar(r3b, tmp_path / "r3b"))
    assert _id_de(r4b, 5) != e2 and r4b.reporte["colisiones_resueltas"] == 1


def test_en_empate_de_destino_gana_el_grupo_que_puede_heredar(tmp_path: Path) -> None:
    """Anterior E1 = {A-1, A-2}. Ahora el grupo 1 (con NIT) tiene A-1 y el grupo 2
    (sin NIT) tiene A-2: un compartido cada uno. El NIT nunca hereda, así que
    el empate se resuelve hacia el grupo 2, que conserva E1; nada se retira."""
    r1 = _asignar({1: ["A-1", "A-2"]})
    e1 = _id_de(r1, 1)
    r2 = _asignar(
        {1: ["A-1", "X-1", "X-2"], 2: ["A-2"]},
        nit={1: "900000009"},
        anterior=_guardar(r1, tmp_path / "r1"),
    )
    assert _id_de(r2, 1) == "NIT-900000009"
    assert _id_de(r2, 2) == e1
    assert _retirados(r2).empty
    assert r2.reporte["heredadas"] == 1 and r2.reporte["ent_a_nit"] == 0
    # Con más compartidos hacia el NIT, el retiro hacia el NIT sigue siendo correcto.
    r2b = _asignar(
        {1: ["A-1", "A-2", "X-1"], 2: ["A-3"]},
        nit={1: "900000009"},
        anterior=_guardar(_asignar({1: ["A-1", "A-2", "A-3"]}), tmp_path / "r1b"),
    )
    assert _retirados(r2b)["RETIRADO_EN"].tolist() == ["NIT-900000009"]


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


def _crosswalk_a_mano(filas: list[tuple[str, int | None, str | None, str | None]]) -> pd.DataFrame:
    """Crosswalk con el contrato a partir de (ID_ENTIDAD, ID_GRUPO, RETIRADO_EN, REGISTROS)."""
    return pd.DataFrame(
        {
            "ID_ENTIDAD": pd.Series([f[0] for f in filas], dtype="string"),
            "ID_GRUPO": pd.Series([f[1] for f in filas], dtype="Int64"),
            "N_REGISTROS": pd.Series(
                [0 if f[3] is None else f[3].count("|") + 1 for f in filas], dtype="int64"
            ),
            "RETIRADO_EN": pd.Series([f[2] for f in filas], dtype="string"),
            COLUMNA_REGISTROS: pd.Series([f[3] for f in filas], dtype="string"),
        }
    )


def test_cargar_corrida_anterior_rechaza_un_id_registro_en_dos_entidades_activas(
    tmp_path: Path,
) -> None:
    """Un crosswalk editado a mano con A-1 en dos entidades activas contaría el
    solapamiento doble: se rechaza al cargar (antes de L1, también desde
    ``ConfigCruce``), no se hereda con él."""
    roto = _crosswalk_a_mano(
        [
            ("ENT-aaaaaaaaaaaaaaaa", 1, None, "A-1|A-2"),
            ("ENT-bbbbbbbbbbbbbbbb", 2, None, "A-1|B-1"),
            ("ENT-cccccccccccccccc", 1, "ENT-aaaaaaaaaaaaaaaa", None),
        ]
    )
    (tmp_path / "roto").mkdir()
    escribir_parquet(roto, tmp_path / "roto" / NOMBRE_CROSSWALK, contrato.ENTIDADES_IDS)
    with pytest.raises(CorridaAnteriorError, match="A-1") as exc:
        cargar_corrida_anterior(tmp_path / "roto")
    assert "1 ID_REGISTRO" in str(exc.value) and "una sola entidad activa" in str(exc.value)
    # El mismo registro en una activa y en una retirada (REGISTROS nulo) no es
    # un conflicto: las retiradas no tienen registros.
    sano = _crosswalk_a_mano(
        [
            ("ENT-aaaaaaaaaaaaaaaa", 1, None, "A-1|A-2"),
            ("ENT-bbbbbbbbbbbbbbbb", 1, "ENT-aaaaaaaaaaaaaaaa", None),
        ]
    )
    (tmp_path / "sano").mkdir()
    escribir_parquet(sano, tmp_path / "sano" / NOMBRE_CROSSWALK, contrato.ENTIDADES_IDS)
    previa = cargar_corrida_anterior(tmp_path / "sano")
    assert _id_de(_asignar({9: ["A-1", "A-2", "A-3"]}, anterior=previa), 9) == (
        "ENT-aaaaaaaaaaaaaaaa"
    )


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
    leido = leer_resultado(carpeta_dos)
    assert leido.entidades_ids is not None
    assert leido.entidades_ids["ID_GRUPO"].dtype == "Int64"
    assert dos.entidades_ids["ID_GRUPO"].dtype == "Int64"
    texto = ["ID_ENTIDAD", "RETIRADO_EN", COLUMNA_REGISTROS]
    publicado = leido.entidades_ids.astype(dict.fromkeys(texto, "string"))
    en_memoria = dos.entidades_ids.astype(dict.fromkeys(texto, "string"))
    pd.testing.assert_frame_equal(publicado, en_memoria)


def test_linkage_corrida_anterior_inexistente_falla_antes_de_correr(tmp_path: Path) -> None:
    with pytest.raises(CorridaAnteriorError):
        _correr_linkage(tmp_path / "t", corrida_anterior=tmp_path / "nada")
    assert not (tmp_path / "t").exists(), "falla antes de L1: no deja trabajo"


def test_linkage_col_id_con_separador_falla_antes_de_correr(tmp_path: Path) -> None:
    """Un ``col_id`` con ``|`` es un dato de entrada: se rechaza ANTES de L1
    (sin work_dir) con un solo «qué hacer», no al cierre como defecto del motor."""
    fuentes = conjunto_sintetico()
    fuentes["CRM"].loc[0, "CODIGO"] = "X|1"
    with pytest.raises(IdRegistroInvalidoError, match=r"'X\|1'") as exc, _silencio():
        rl.linkage(
            fuentes,
            work_dir=str(tmp_path / "t"),
            skip_reporting=True,
            col_ciudad="CIUDAD",
            col_id="CODIGO",
        )
    assert not (tmp_path / "t").exists(), "falla antes de L1: no deja trabajo"
    texto = str(exc.value)
    assert "defecto del motor" not in texto
    assert texto.count("Qué hacer") == 1 and "col_id=None" in texto
    assert "'CRM'" in texto and "col_id='CODIGO'" in texto


def test_linkage_fuente_con_separador_en_el_nombre_falla_antes_de_correr(tmp_path: Path) -> None:
    fuentes = conjunto_sintetico()
    fuentes = {"A|B": fuentes["CRM"], "ADUANAS": fuentes["ADUANAS"]}
    with pytest.raises(IdRegistroInvalidoError, match=r"'A\|B'") as exc, _silencio():
        rl.linkage(fuentes, work_dir=str(tmp_path / "t"), skip_reporting=True, col_ciudad="CIUDAD")
    assert not (tmp_path / "t").exists()
    assert "defecto del motor" not in str(exc.value)
    # link() pasa por linkage(): la etiqueta de fuente también se rechaza antes.
    with pytest.raises(IdRegistroInvalidoError, match=r"'A\|B'"), _silencio():
        rl.link(
            fuentes["A|B"],
            fuentes["ADUANAS"],
            nombre_a="A|B",
            nombre_b="ADUANAS",
            work_dir=str(tmp_path / "t2"),
            skip_reporting=True,
        )
    assert not (tmp_path / "t2").exists()


def test_dedupe_col_id_con_separador_falla_antes_de_correr(tmp_path: Path) -> None:
    df = conjunto_sintetico()["CRM"]
    df.loc[0, "CODIGO"] = "X|1"
    with pytest.raises(IdRegistroInvalidoError, match=r"'X\|1'") as exc, _silencio():
        rl.dedupe(df, output_dir=str(tmp_path / "d"), col_id="CODIGO")
    assert not (tmp_path / "d").exists(), "falla antes del motor: no deja salida"
    assert "defecto del motor" not in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# validar(): el crosswalk también cumple su contrato
# ─────────────────────────────────────────────────────────────────────────────


_DTYPE_POR_FAMILIA = {
    "texto": "string",
    "entero": "Int64",
    "decimal": "float64",
    "booleano": "bool",
}


def _resultado_con_crosswalk(
    grupos: dict[int, list[str]],
    identidad: ResultadoIdentidad,
    entidades_ids: pd.DataFrame | None = None,
) -> ResultadoLinkage:
    """``ResultadoLinkage`` mínimo: correlativa con las columnas fijas tipadas
    según el contrato y los ids de ``identidad``; crosswalk el de ``identidad``
    o el que la prueba rompa. Sin manifiesto ni golden (no se validan)."""
    correl = _correl(grupos).assign(ID_ENTIDAD=identidad.id_entidad.astype("string"))
    correl["ID_GRUPO"] = correl["ID_GRUPO"].astype("int64")
    correl["ID_REGISTRO"] = correl["ID_REGISTRO"].astype("string")
    for col in contrato.CORRELATIVA:
        if col.nombre not in correl.columns:
            dtype = _DTYPE_POR_FAMILIA[contrato.familia_tipo(col.tipo)]
            vacio = float("nan") if dtype == "float64" else pd.NA
            correl[col.nombre] = pd.Series(vacio, index=correl.index, dtype=dtype)
    correl = correl[list(contrato.COLUMNAS_CORRELATIVA)]
    return ResultadoLinkage(
        correlativa=correl,
        entidades_ids=identidad.entidades_ids if entidades_ids is None else entidades_ids,
    )


def test_validar_acepta_el_crosswalk_que_escribe_el_modulo(tmp_path: Path) -> None:
    r1 = _asignar({1: ["A-1", "A-2", "A-3"], 2: ["B-1", "B-2"]})
    grupos = {4: ["A-1", "A-2", "A-3", "B-1", "B-2"]}
    r2 = _asignar(grupos, anterior=_guardar(r1, tmp_path / "r1"))
    assert not _retirados(r2).empty
    reporte = _resultado_con_crosswalk(grupos, r2).validar()
    assert reporte.ok, reporte.resumen()
    grupos = {1: ["A-1"], 2: ["B-1"]}
    reporte = _resultado_con_crosswalk(grupos, _asignar(grupos, nit={2: "900111222"})).validar()
    assert reporte.ok, reporte.resumen()


def test_validar_rechaza_un_retirado_en_hacia_un_id_inexistente_y_un_id_grupo_nulo() -> None:
    grupos = {1: ["A-1", "A-2"]}
    identidad = _asignar(grupos)
    roto = pd.concat(
        [
            identidad.entidades_ids,
            _crosswalk_a_mano([("ENT-ffffffffffffffff", None, "ENT-0000000000000000", None)]),
        ],
        ignore_index=True,
    )
    res = _resultado_con_crosswalk(grupos, identidad, roto)
    reporte = res.validar()
    assert not reporte.ok
    assert any("entidades_ids" in f and "RETIRADO_EN" in f for f in reporte.fallos), reporte.fallos
    assert any("entidades_ids" in f and "ID_GRUPO" in f and "nulo" in f for f in reporte.fallos)
    with pytest.raises(ContratoSalidaError, match="entidades_ids"):
        res.validar(estricto=True)


def test_validar_rechaza_un_crosswalk_que_no_coincide_con_la_correlativa() -> None:
    grupos = {1: ["A-1", "A-2"], 2: ["B-1"]}
    identidad = _asignar(grupos)
    base = identidad.entidades_ids
    # Un ID_ENTIDAD repetido (y por tanto un par que sobra y otro que falta).
    otro = base.copy()
    otro.loc[1, "ID_ENTIDAD"] = otro.loc[0, "ID_ENTIDAD"]
    fallos = _resultado_con_crosswalk(grupos, identidad, otro).validar().fallos
    assert any("entidades_ids" in f and "ID_ENTIDAD" in f and "repetido" in f for f in fallos)
    assert any("entidades_ids" in f and "correlativa" in f for f in fallos), fallos
    # Una entidad activa cuyo grupo no existe en la correlativa.
    fuera = base.copy()
    fuera.loc[1, "ID_GRUPO"] = 99
    fallos = _resultado_con_crosswalk(grupos, identidad, fuera).validar().fallos
    assert any("entidades_ids" in f and "99" in f for f in fallos), fallos
    # Columnas faltantes: lo mismo que en las otras tablas.
    sin_registros = base.drop(columns=[COLUMNA_REGISTROS])
    fallos = _resultado_con_crosswalk(grupos, identidad, sin_registros).validar().fallos
    assert any("entidades_ids: faltan" in f for f in fallos), fallos


def test_config_cruce_rechaza_un_nombre_de_fuente_con_separador_al_construirse(
    tmp_path: Path,
) -> None:
    """Pulido F2 (hallazgo 13 de F2.4): un ``SourceSpec.name`` con ``|`` solo lo
    rechazaba ``exigir_fuentes_sin_separador`` dentro de ``linkage()``, después de la
    ingesta (lo caro en DuckDB). Ahora ``ConfigCruce`` falla al construirse, con la
    misma excepción y el mismo texto, y sin crear el workspace."""
    from record_linkage.flujo.cruce import ConfigCruce
    from record_linkage.ingestion import ColumnType, SourceSpec

    fuente = tmp_path / "f.csv"
    fuente.write_text("NIT,RAZON_SOCIAL\n900111222,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="A|B",
        path=fuente,
        column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
        column_types={"NIT": ColumnType.IDENTIFIER},
    )
    with pytest.raises(IdRegistroInvalidoError, match=r"(?s)'A\|B'.*Qué hacer"):
        ConfigCruce(fuentes=[spec], workspace=tmp_path / "ws")
    assert not (tmp_path / "ws").exists()
    # La regla es una: la función pública que linkage() también usa.
    with pytest.raises(IdRegistroInvalidoError, match=r"'A\|B'"):
        exigir_nombres_sin_separador(["OK", "A|B"])
    exigir_nombres_sin_separador(["OK", "OTRA"])


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
    # Un crosswalk anterior a F2.4 (sin REGISTROS) existe pero no sirve para
    # heredar: debe fallar al construir, no dentro de linkage().
    viejo = tmp_path / "viejo"
    viejo.mkdir()
    pd.DataFrame(
        {
            "ID_ENTIDAD": pd.Series(["ENT-0123456789abcdef"], dtype="string"),
            "ID_GRUPO": pd.Series([0], dtype="int64"),
            "N_REGISTROS": pd.Series([2], dtype="int64"),
            "RETIRADO_EN": pd.Series([pd.NA], dtype="string"),
        }
    ).to_parquet(viejo / NOMBRE_CROSSWALK)
    with pytest.raises(CorridaAnteriorError, match=COLUMNA_REGISTROS):
        ConfigCruce(fuentes=[spec], workspace=tmp_path / "ws", corrida_anterior=viejo)
    _guardar(_asignar({1: ["A-1"]}), tmp_path / "r1")
    cfg = ConfigCruce(fuentes=[spec], workspace=tmp_path / "ws", corrida_anterior=tmp_path / "r1")
    assert cfg.corrida_anterior == tmp_path / "r1"
