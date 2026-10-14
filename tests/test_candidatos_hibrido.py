"""Pruebas del generador de candidatos híbrido (Fase 2). Empresas inventadas."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.candidatos import (
    ConfigCandidatosHibrido,
    escribir_candidatos_sqlite,
    generar_candidatos_hibrido,
    matriz_politica_fuentes,
    pares_por_nombre_tfidf,
    pares_por_token_distintivo,
    recall_de_pares,
    variantes_por_borrado,
)


def _base() -> pd.DataFrame:
    # 0-1: misma empresa por NIT y nombre; 2-3: mismo nombre con errata, sin NIT; 4-5: TF-IDF (palabra partida);
    # 6: genérica sola; 7-8: mismo NIT con nombres distintos (solo el identificador los une).
    return pd.DataFrame(
        {
            "NOMBRE_LIMPIO": [
                "KRONOS EMPRESARIAL VALDORIA",
                "KRONOS EMPRESARIAL VALDORIA SAS",
                "TRIVENTA",
                "TRIVENTE",
                "PELUVIKA",
                "PELU VIKA",
                "INVERSIONES",
                "ZELVORA ALIMENTOS",
                "DULCEVENT",
            ],
            "NIT_BASE": ["900100200", "900100200", "", "", "", "", "", "800300400", "800300400"],
            "FUENTE": ["RUES", "CRM", "CRM", "DIAN", "CRM", "DIAN", "CRM", "RUES", "DIAN"],
        }
    )


def _pares(lotes) -> set[tuple[int, int]]:
    salida: set[tuple[int, int]] = set()
    for lote in lotes:
        lote = lote[1] if isinstance(lote, tuple) else lote
        salida.update(map(tuple, np.sort(lote, axis=1).tolist()))
    return salida


def test_variantes_por_borrado_incluye_el_token_y_sus_borrados():
    v = variantes_por_borrado(pd.Series(["ABCDE", "XY"]), largo_minimo=5)
    por_fila = v.groupby("fila")["variante"].apply(set).to_dict()
    assert por_fila[0] == {"ABCDE", "BCDE", "ACDE", "ABDE", "ABCE", "ABCD"}
    assert por_fila[1] == {"XY"}  # corto: solo exacto


def test_tokens_distintivos_unen_por_token_raro_y_toleran_una_errata():
    pares = _pares(
        pares_por_token_distintivo(_base()["NOMBRE_LIMPIO"], genericos=frozenset({"INVERSIONES"}))
    )
    assert (0, 1) in pares  # KRONOS / VALDORIA
    assert (2, 3) in pares  # TRIVENTA ↔ TRIVENTE: una letra
    assert (4, 5) not in pares  # PELUVIKA vs PELU VIKA: no comparten token
    assert not any(6 in p for p in pares)  # la genérica sola no propone nada


def test_tokens_sin_borrados_no_toleran_la_errata():
    pares = _pares(pares_por_token_distintivo(_base()["NOMBRE_LIMPIO"], con_borrados=False))
    assert (0, 1) in pares and (2, 3) not in pares


def test_tfidf_une_la_palabra_partida_y_respeta_umbral_y_top_k():
    nombres = _base()["NOMBRE_LIMPIO"]
    pares = _pares(pares_por_nombre_tfidf(nombres, umbral=0.3, top_k=5, min_df=1, max_df=1.0))
    assert (4, 5) in pares and (0, 1) in pares and (2, 3) in pares
    assert not any(6 in p or 8 in p for p in pares)
    assert (
        _pares(pares_por_nombre_tfidf(nombres, umbral=0.99, top_k=5, min_df=1, max_df=1.0)) == set()
    )
    uno = _pares(pares_por_nombre_tfidf(nombres, umbral=0.1, top_k=1, min_df=1, max_df=1.0))
    assert len(uno) <= len(nombres)  # a lo sumo un par por fila


def test_tfidf_se_omite_con_mas_filas_que_el_tope(caplog):
    nombres = _base()["NOMBRE_LIMPIO"]
    with caplog.at_level("WARNING"):
        assert list(pares_por_nombre_tfidf(nombres, max_filas=3, min_df=1)) == []
    assert "max_filas" in caplog.text


def test_matriz_politica_de_fuentes_como_los_motores_lsh():
    fuentes = np.array(["CRM", "DIAN", "RUES"])
    assert matriz_politica_fuentes(fuentes, cross_source_only=False).all()
    solo_cruzado = matriz_politica_fuentes(fuentes, cross_source_only=True)
    assert not solo_cruzado.diagonal().any() and solo_cruzado[0, 1]
    confiable = matriz_politica_fuentes(
        fuentes, cross_source_only=True, fuentes_confiables=frozenset({"RUES"})
    )
    assert confiable[0, 0] and confiable[1, 1] and not confiable[2, 2] and confiable[0, 2]


def test_el_hibrido_compone_estrategias_y_aplica_la_politica():
    df = _base()
    por_estrategia: dict[str, set[tuple[int, int]]] = {}
    for estrategia, lote in generar_candidatos_hibrido(
        df,
        # Borrados encendidos a propósito: la errata TRIVENTA/TRIVENTE entra por token solo con ellos (ADR-0016 los
        # apaga por defecto porque TF-IDF ya la cubre; la fila (4, 5) lo muestra).
        ConfigCandidatosHibrido(tfidf_umbral=0.3, tfidf_max_df=1.0, tokens_borrados=True),
        fuentes_confiables=frozenset({"RUES"}),
    ):
        por_estrategia.setdefault(estrategia, set()).update(_pares([lote]))
    assert (7, 8) in por_estrategia["por_identificador"]  # solo el NIT los une
    assert (0, 1) in por_estrategia["por_identificador"]
    assert (2, 3) in por_estrategia["por_token"]
    assert (4, 5) in por_estrategia["por_tfidf"]
    # RUES es confiable: ningún par RUES-RUES; CRM-CRM sí puede (2 y 4 son CRM y no se parecen: no aparece)
    todos = set().union(*por_estrategia.values())
    assert all(not (df.FUENTE[i] == "RUES" and df.FUENTE[j] == "RUES") for i, j in todos)


def test_el_hibrido_con_llave_exacta_y_sin_nombre(caplog):
    df = (
        _base()
        .assign(TELEFONO=["3001", "3001", "", "", "3002", "3002", "", "", ""])
        .drop(columns="NOMBRE_LIMPIO")
    )
    with caplog.at_level("WARNING"):
        salida = list(
            generar_candidatos_hibrido(
                df, ConfigCandidatosHibrido(llaves=("TELEFONO", "NO_EXISTE"))
            )
        )
    estrategias = {e for e, _ in salida}
    assert "por_llave:TELEFONO" in estrategias and "por_token" not in estrategias
    assert _pares([lote for e, lote in salida if e == "por_llave:TELEFONO"]) == {(0, 1), (4, 5)}
    assert "NO_EXISTE" in caplog.text and "NOMBRE_LIMPIO" in caplog.text


def test_escribir_candidatos_sqlite_funde_repetidos_y_deja_el_esquema_de_l3(tmp_path):
    lotes = iter(
        [
            ("por_identificador", np.array([[0, 1], [1, 0], [7, 8]])),
            ("por_token", np.array([[0, 1], [2, 3], [3, 3]])),
            ("por_tfidf", np.empty((0, 2), dtype=np.int64)),
        ]
    )
    ruta = tmp_path / "candidates.db"
    est = escribir_candidatos_sqlite(lotes, ruta, registros=9, huella="abc")
    assert est.total == 3 and est.emitidos == {"por_identificador": 2, "por_token": 2}
    assert est.nuevos == {"por_identificador": 2, "por_token": 1}
    con = sqlite3.connect(ruta)
    assert con.execute("SELECT idx_0, idx_1 FROM candidate_pairs ORDER BY 1, 2").fetchall() == [
        (0, 1),
        (2, 3),
        (7, 8),
    ]
    assert con.execute("SELECT completed_bands, fingerprint FROM _progress").fetchone() == (
        0,
        "abc",
    )
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'candidate_pairs'").fetchone()[0]
    assert "PRIMARY KEY (idx_0, idx_1)" in sql and "WITHOUT ROWID" in sql
    con.close()
    d = est.como_dict()
    assert d["por_registro"] == pytest.approx(3 / 9, abs=0.01) and "segundos_total" in d


def test_config_desde_perfil_hereda_las_perillas_de_nit_y_rechaza_claves_raras():
    perfil = {
        "enable_nit_blocking": False,
        "nit_blocking_radio": 2,
        "nit_blocking_column": "NIT_OK",
        "llaves_bloqueo": ["TELEFONO"],
        "candidatos_hibrido": {"tfidf_umbral": 0.5, "llaves": ["EMAIL"]},
    }
    cfg = ConfigCandidatosHibrido.desde_perfil(perfil)
    assert (cfg.nit, cfg.nit_radio, cfg.columna_nit, cfg.tfidf_umbral, cfg.llaves) == (
        False,
        2,
        "NIT_OK",
        0.5,
        ("EMAIL",),
    )
    assert ConfigCandidatosHibrido.desde_perfil({}) == ConfigCandidatosHibrido()
    with pytest.raises(ValueError, match="desconocidas"):
        ConfigCandidatosHibrido.desde_perfil({"candidatos_hibrido": {"umbral": 0.5}})


def test_recall_de_pares():
    grupos = np.array(["a", "a", "b", "b", "b", "c"])
    assert recall_de_pares(np.array([[0, 1], [2, 3]]), grupos) == pytest.approx(2 / 4)
    assert recall_de_pares(np.empty((0, 2), dtype=np.int64), grupos) == 0.0
    assert recall_de_pares(np.array([[0, 1]]), np.array(["x", "y"])) == 0.0


# ── integración: linkage() con motor_candidatos="hibrido" ───────────────────────────────────────────
@pytest.mark.parametrize("motor", ["lsh", "hibrido"])
def test_linkage_con_el_motor_de_candidatos_elegido(tmp_path, motor):
    """Los dos motores llegan al mismo golden en una base pequeña: 3 registros → 2 entidades."""
    from record_linkage import linkage

    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "800555111"],
            "RAZON_SOCIAL": ["KRONOS VALDORIA SAS", "KRONOS VALDORIA S.A.S.", "ZELVORA LTDA"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "MEDELLIN"],
        }
    )
    res = linkage(
        sources={"RUES": df},
        work_dir=str(tmp_path / motor),
        ajustes_perfil={"motor_candidatos": motor},
    )
    assert len(res.golden) == 2
    con = sqlite3.connect(tmp_path / motor / "L2_lsh_candidates" / "candidates.db")
    pares = con.execute("SELECT idx_0, idx_1 FROM candidate_pairs ORDER BY 1, 2").fetchall()
    con.close()
    assert (0, 1) in pares


def test_un_motor_de_candidatos_desconocido_cae_al_lsh_sin_romper(tmp_path):
    """La perilla solo reconoce «hibrido»; cualquier otro texto deja el LSH (sin cambio de comportamiento)."""
    from record_linkage import linkage

    df = pd.DataFrame({"NIT": ["900123456"], "RAZON_SOCIAL": ["KRONOS SAS"], "CIUDAD": ["BOGOTA"]})
    res = linkage(
        sources={"RUES": df},
        work_dir=str(tmp_path / "x"),
        ajustes_perfil={"motor_candidatos": "otro"},
    )
    assert len(res.golden) == 1


def test_las_variantes_por_borrado_vienen_apagadas_por_defecto():
    """Fase 2, hallazgo 4: no cambian ningún resultado del banco y cuestan 138.000 candidatos."""
    assert ConfigCandidatosHibrido().tokens_borrados is False
    assert (
        ConfigCandidatosHibrido.desde_perfil(
            {"candidatos_hibrido": {"tokens_borrados": True}}
        ).tokens_borrados
        is True
    )


def test_el_tamano_del_lote_de_tfidf_no_cambia_los_pares():
    """El lote solo acota memoria (a 139k: 500 filas = 820 MiB, 2.000 = 1.383 MiB); los pares son los mismos."""
    from record_linkage.engine.candidatos import pares_por_nombre_tfidf

    nombres = _base()["NOMBRE_LIMPIO"]
    chico = _pares(pares_por_nombre_tfidf(nombres, umbral=0.3, max_df=1.0, lote_filas=2))
    grande = _pares(pares_por_nombre_tfidf(nombres, umbral=0.3, max_df=1.0, lote_filas=1000))
    assert chico == grande and chico
