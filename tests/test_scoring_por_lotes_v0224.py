"""Scoring por lotes y decisiones podadas (v0.22.4).

Origen: la corrida de la base real de importadores (355.681 filas, 214
particiones por país) murió por OOM (exit 137) en un contenedor de 15 GiB.
Medido en la partición USA sola (54.669 nombres, 24.476.082 candidatos): pico
de 11,4 GiB puntuando toda la unión de una vez, y una tabla de decisiones de
2,6 GiB con los dos nombres repetidos en cada par. Tres cambios, cada uno con
su prueba de identidad:

1. La unión de pares del bloqueo se calcula por clave escalar ``i·n + j``
   (mitad de memoria que ``np.unique(axis=0)`` sobre pares int64) y se
   compacta por lotes mientras se acumula.
2. ``evaluar_esquema`` puntúa por lotes (``pares_por_lote``). El score de un
   par no depende de ningún otro par, así que el resultado es bit-idéntico.
3. ``ejecutar()`` conserva solo las decisiones auditables (fusionadas,
   vetadas o ≥ ``sim_minima_auditoria``), con ``i``/``j`` como posiciones
   globales en ``representantes``; los nombres se resuelven bajo demanda.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo import (
    ConfigImportadores,
    decisiones_con_nombres,
    deduplicar_importadores,
    preparar,
    sensibilidad_umbral,
    similitud_nombre_desde_score,
)
from record_linkage.flujo.importadores import ejecutar
from record_linkage.matching.campos import CorroboracionVeto, esquema_multicampo_completo
from record_linkage.matching.motor_bloqueo import (
    BloqueoComponible,
    LlaveExacta,
    LSHTexto,
    _canonizar,
    _union_pares,
    _UnionIncremental,
)
from record_linkage.matching.motor_multicampo import evaluar_esquema

# ── 1 · Unión de pares por clave escalar ─────────────────────────────────────


def _piezas_aleatorias(semilla: int, n: int = 300, k: int = 12) -> list[np.ndarray]:
    rng = np.random.default_rng(semilla)
    piezas = []
    for _ in range(k):
        m = int(rng.integers(0, 400))
        a = rng.integers(0, n, size=m)
        b = rng.integers(0, n, size=m)
        keep = a != b
        par = np.sort(np.column_stack((a[keep], b[keep])), axis=1)
        piezas.append(par.astype(np.int64))
    piezas.append(np.empty((0, 2), dtype=np.int64))  # una pieza vacía no molesta
    return piezas


@pytest.mark.parametrize("semilla", [0, 1, 2, 3])
def test_union_por_claves_es_identica_a_np_unique_axis0(semilla: int) -> None:
    piezas = _piezas_aleatorias(semilla)
    referencia = np.unique(np.vstack([p for p in piezas if len(p)]), axis=0)
    assert np.array_equal(_union_pares(piezas), referencia)


def test_union_vacia() -> None:
    assert _union_pares([]).shape == (0, 2)
    assert _union_pares([np.empty((0, 2), dtype=np.int64)]).shape == (0, 2)


def test_canonizar_sigue_ordenando_cada_par() -> None:
    """``_canonizar`` recibe pares en cualquier orden (j, i) y los deja i<j únicos."""
    arr = np.array([[5, 2], [2, 5], [1, 9], [9, 1], [3, 4]], dtype=np.int64)
    esperado = np.array([[1, 9], [2, 5], [3, 4]], dtype=np.int64)
    assert np.array_equal(_canonizar([arr]), esperado)
    assert np.array_equal(_canonizar([(5, 2), (2, 5), (1, 9), (3, 4)]), esperado)


@pytest.mark.parametrize("lote", [1, 7, 10_000])
def test_union_incremental_compacta_sin_perder_ni_duplicar(lote: int) -> None:
    n = 50
    rng = np.random.default_rng(7)
    claves = [rng.integers(0, n * n, size=int(rng.integers(1, 60))) for _ in range(30)]
    u = _UnionIncremental(n, lote=lote)
    for c in claves:
        u.agregar(c.astype(np.int64))
    esperado = np.unique(np.concatenate(claves))
    assert np.array_equal(u.compactar(), esperado)
    pares = u.pares()
    assert np.array_equal(pares[:, 0] * n + pares[:, 1], esperado)


def test_lsh_devuelve_pares_canonicos() -> None:
    nombres = np.array(
        [f"EMPRESA {chr(65 + i % 26)} DEL NORTE {i % 7}" for i in range(400)], dtype=object
    )
    union = LSHTexto("N", umbral=0.3, permutaciones=32, ngram=3).pares({"N": nombres})
    assert len(union) > 0
    assert (union[:, 0] < union[:, 1]).all()
    assert np.array_equal(union, np.unique(union, axis=0)), "ordenados y únicos"
    assert union.dtype == np.int64


# ── 2 · Motor: puntuar por lotes es bit-idéntico ────────────────────────────


def _fila(reg_id, nit, razon, email="", tel="", ciudad="BOGOTA"):
    return {
        "REG_ID": reg_id,
        "NIT": nit,
        "RAZON_SOCIAL": razon,
        "EMAIL": email,
        "TELEFONO": tel,
        "DIRECCION": "",
        "CIUDAD": ciudad,
        "LATITUD": np.nan,
        "LONGITUD": np.nan,
    }


def _base_motor() -> pd.DataFrame:
    """Fusiones, vetos, vetos levantados por corroboración y faltantes, todo junto."""
    filas = []
    k = 0
    for g in range(12):
        nit = f"9001{g:05d}"
        nombre = f"TEXTILES DEL {['PACIFICO', 'CARIBE', 'LLANO', 'VALLE'][g % 4]} {g} SAS"
        filas.append(_fila(k := k + 1, nit, nombre, email=f"info{g}@tex.com"))
        filas.append(_fila(k := k + 1, nit, nombre.replace(" SAS", " S.A.S."), tel=f"60155{g:05d}"))
        # NIT distinto, mismo email y nombre → veto que la corroboración levanta.
        filas.append(_fila(k := k + 1, f"8002{g:05d}", nombre, email=f"info{g}@tex.com"))
        # NIT distinto, nombre parecido, sin corroboración → veto firme.
        filas.append(_fila(k := k + 1, f"7003{g:05d}", nombre + " LTDA"))
        # Sin NIT → faltante; el nombre decide.
        filas.append(_fila(k := k + 1, "", nombre))
    return pd.DataFrame(filas)


def _bloqueo() -> BloqueoComponible:
    return BloqueoComponible(
        [
            LlaveExacta("NIT"),
            LlaveExacta("EMAIL"),
            LlaveExacta("TELEFONO"),
            LSHTexto("RAZON_SOCIAL", umbral=0.35, permutaciones=64, ngram=3),
        ]
    )


def _esquema():
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(campos_corroborantes=("EMAIL", "TELEFONO"), activa=True)
    return esq


@pytest.fixture(scope="module")
def referencia_motor():
    df = _base_motor()
    res = evaluar_esquema(df, _esquema(), _bloqueo())
    dec = res.decisiones
    # El caso debe ejercitar TODAS las ramas; si no, la identidad no probaría nada.
    assert dec["fusion"].any() and dec["veto"].any() and dec["veto_levantado"].any()
    assert res.n_candidatos > 20
    return df, res


@pytest.mark.parametrize("lote", [1, 3, 7, 10_000_000])
def test_motor_por_lotes_es_bit_identico(referencia_motor, lote: int) -> None:
    df, ref = referencia_motor
    res = evaluar_esquema(df, _esquema(), _bloqueo(), pares_por_lote=lote)
    pd.testing.assert_frame_equal(res.decisiones, ref.decisiones)
    pd.testing.assert_frame_equal(res.desglose, ref.desglose)
    assert (res.n_candidatos, res.n_fusiones) == (ref.n_candidatos, ref.n_fusiones)


def test_sin_desglose_no_cambia_las_decisiones(referencia_motor) -> None:
    df, ref = referencia_motor
    res = evaluar_esquema(df, _esquema(), _bloqueo(), pares_por_lote=5, incluir_desglose=False)
    pd.testing.assert_frame_equal(res.decisiones, ref.decisiones)
    assert res.desglose.empty
    assert not ref.desglose.empty


def test_lote_invalido_es_error_accionable(referencia_motor) -> None:
    df, _ = referencia_motor
    with pytest.raises(ValueError, match="pares_por_lote"):
        evaluar_esquema(df, _esquema(), _bloqueo(), pares_por_lote=0)


# ── 3 · Flujo: lotes, poda e índices globales ───────────────────────────────


def _cfg(**extra) -> ConfigImportadores:
    base = dict(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
        paises_sin_clasificar="aislar",
    )
    base.update(extra)
    return ConfigImportadores(**base)


def _base_flujo() -> pd.DataFrame:
    """Tres países reales + dos grafías sin clasificar (→ vetos en ZZZ)."""
    raices = [
        "GLOBAL TRADING PARTNERS",
        "ACME LOGISTICS",
        "BETA FOODS",
        "NORTHERN STEEL WORKS",
        "PACIFIC FLOWERS",
        "ANDES COFFEE ROASTERS",
        "SUN VALLEY PRODUCE",
        "BLUE OCEAN SEAFOOD",
    ]
    variantes = [" LLC", " L.L.C.", " INC", " CORP", " CORPORATION", " CO", ""]
    filas = []
    for p, pais in enumerate(["ESTADOS UNIDOS", "ALEMANIA", "PANAMA", "NO DEFINIDO", "VARIOS"]):
        for r, raiz in enumerate(raices):
            for v, suf in enumerate(variantes[: 3 + (r + p) % 4]):
                filas.append((raiz + suf, pais, float(10 * (r + 1) + v + p)))
            filas.append((raiz.replace("A", "E", 1) + " LLC", pais, 1.0))  # una errata
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])


@pytest.fixture(scope="module")
def corridas():
    df = _base_flujo()
    cfg_ref = _cfg(pares_por_lote=1_000_000, sim_minima_auditoria=0.0)  # sin poda, un lote
    cfg_lotes = _cfg(pares_por_lote=5, sim_minima_auditoria=0.0)  # sin poda, lotes de 5
    cfg_poda = _cfg(pares_por_lote=5)  # poda por defecto (0,70)
    prep = preparar(df, cfg_ref)
    return {
        "prep": prep,
        "ref": ejecutar(prep, cfg_ref),
        "lotes": ejecutar(prep, cfg_lotes),
        "poda": ejecutar(prep, cfg_poda),
        "cfg_poda": cfg_poda,
    }


def test_flujo_por_lotes_es_identico(corridas) -> None:
    ref, lotes = corridas["ref"], corridas["lotes"]
    pd.testing.assert_frame_equal(ref["representantes"], lotes["representantes"])
    pd.testing.assert_frame_equal(ref["decisiones"], lotes["decisiones"])
    assert ref["n_candidatos"] == lotes["n_candidatos"] > 50
    assert ref["n_cortes"] == lotes["n_cortes"]


def test_sin_poda_conserva_todos_los_candidatos(corridas) -> None:
    ref = corridas["ref"]
    assert len(ref["decisiones"]) == ref["n_candidatos"]


def test_indices_globales_resuelven_al_representante_correcto(corridas) -> None:
    ref = corridas["ref"]
    dec, rep = ref["decisiones"], ref["representantes"]
    assert "NOMBRE_A" not in dec.columns and "NOMBRE_B" not in dec.columns
    assert dec["PAIS_ISO3"].to_numpy().tolist() == rep["PAIS_ISO3"].to_numpy()[dec["i"]].tolist()
    assert dec["PAIS_ISO3"].to_numpy().tolist() == rep["PAIS_ISO3"].to_numpy()[dec["j"]].tolist()
    assert (dec["i"] < dec["j"]).all()
    con = decisiones_con_nombres(dec, rep)
    assert con["NOMBRE_A"].tolist() == rep["NOMBRE_NORM"].to_numpy()[dec["i"]].tolist()
    assert con["NOMBRE_B"].tolist() == rep["NOMBRE_NORM"].to_numpy()[dec["j"]].tolist()
    # Los pares fusionados resuelven a nombres del MISMO importador.
    ids = rep["ID_IMPORTADOR"].to_numpy()
    fus = dec[dec["fusion"]]
    assert len(fus) > 0
    # (la cobertura por estrellas puede partir un grupo, nunca unirlo; por eso ⊆)
    mismo = ids[fus["i"]] == ids[fus["j"]]
    assert mismo.mean() > 0.5


def test_poda_conserva_exactamente_lo_auditable(corridas) -> None:
    ref, poda, cfg = corridas["ref"], corridas["poda"], corridas["cfg_poda"]
    pd.testing.assert_frame_equal(ref["representantes"], poda["representantes"])
    assert ref["n_candidatos"] == poda["n_candidatos"]
    completa = ref["decisiones"]
    sim01 = similitud_nombre_desde_score(completa["score"].to_numpy(), cfg)
    esperada = completa[
        completa["fusion"] | completa["veto"] | (sim01 >= cfg.sim_minima_auditoria)
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(poda["decisiones"], esperada)
    assert 0 < len(esperada) < len(completa), "la poda debe quitar algo y dejar algo"
    assert completa["veto"].any(), "el caso debe traer vetos (grafías sin clasificar)"
    assert poda["decisiones"]["veto"].sum() == completa["veto"].sum()
    assert poda["decisiones"]["fusion"].sum() == completa["fusion"].sum()


def test_sensibilidad_identica_con_y_sin_poda(corridas) -> None:
    ref, poda, cfg = corridas["ref"], corridas["poda"], corridas["cfg_poda"]
    pd.testing.assert_frame_equal(sensibilidad_umbral(ref, cfg), sensibilidad_umbral(poda, cfg))


def test_sensibilidad_rechaza_umbral_bajo_el_piso(corridas) -> None:
    poda, cfg = corridas["poda"], corridas["cfg_poda"]
    with pytest.raises(ValueError, match="sim_minima_auditoria"):
        sensibilidad_umbral(poda, cfg, umbrales=(0.5, 0.84))
    # El piso mismo sí se admite (≥).
    tabla = sensibilidad_umbral(poda, cfg, umbrales=(cfg.sim_minima_auditoria, 0.84))
    assert list(tabla["umbral_nombre"]) == [cfg.sim_minima_auditoria, 0.84]


def test_config_valida_los_campos_nuevos() -> None:
    with pytest.raises(ValueError, match="pares_por_lote"):
        _cfg(pares_por_lote=0)
    with pytest.raises(ValueError, match="sim_minima_auditoria"):
        _cfg(sim_minima_auditoria=0.9, umbral_nombre=0.84)
    with pytest.raises(ValueError, match="sim_minima_auditoria"):
        _cfg(sim_minima_auditoria=-0.1)
    assert _cfg(sim_minima_auditoria=0.84, umbral_nombre=0.84).sim_minima_auditoria == 0.84


def test_fachada_expone_nombres_bajo_demanda() -> None:
    r = deduplicar_importadores(_base_flujo(), _cfg())
    assert r.todo_ok
    assert "NOMBRE_A" not in r.decisiones.columns
    con = r.decisiones_con_nombres()
    assert {"NOMBRE_A", "NOMBRE_B"} <= set(con.columns)
    assert len(con) == len(r.decisiones) <= r.n_candidatos
    assert "SENSIBILIDAD" in r.extra and len(r.extra["SENSIBILIDAD"]) == 7
