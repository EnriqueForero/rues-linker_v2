"""Bloqueo por NIT vectorizado y consciente de la política (v0.17.4).

Regresión que motivó estas pruebas: sobre 4,37 M de registros reales la
corrida moría por RAM **después** de terminar las bandas del LSH. La causa
medida no era el tamaño de la base sino la estructura del bloqueo por NIT:
19 cadenas de Python por registro —83 millones de objetos— en un diccionario,
entre 6 y 10 GB, construidos antes de emitir un solo par; y encima ese par se
enumeraba aunque la política lo fuera a descartar.

Lo que se protege aquí:
  · que la vecindad sea COMPLETA para la distancia declarada (contra fuerza
    bruta, que es el único juez que no comparte los errores del código);
  · que la política se aplique ANTES de materializar;
  · que nada se acumule: los pares salen por lotes;
  · que el atajo por cobertura no cambie ni un par del resultado.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.lsh.nit_blocking import (
    NitBlockingConfig,
    claves_por_borrado,
    codificar_nits,
    iter_pares_por_nit,
    patrones_de_borrado,
)
from record_linkage.engine.lsh.politica_pares import (
    bloques_utiles,
    indices_de_grupos,
    pares_permitidos,
    pares_por_bloque,
)
from record_linkage.engine.lsh.trusted import TrustedSourceLSHEngine

# ── Utilidades ────────────────────────────────────────────────────────────


def distancia_osa(a: str, b: str) -> int:
    """Damerau-Levenshtein restringida, escrita a mano como juez independiente."""
    m, n = len(a), len(b)
    d = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        d[i][0] = i
    for j in range(n + 1):
        d[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            costo = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + costo)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[m][n]


def _nits_variados(n: int, semilla: int) -> list[str]:
    rng = np.random.default_rng(semilla)
    nits = [f"{900000000 + int(x)}" for x in rng.integers(0, max(20, n * 4), n)]
    for i in rng.choice(n, n // 4, replace=False):
        nits[i] = nits[i][:-1] if i % 2 else nits[i] + str(i % 10)
    return nits


# ── Codificación entera del NIT ───────────────────────────────────────────


def test_codigo_distingue_longitud_de_valor() -> None:
    """'0123456789' y '123456789' son NITs distintos: el valor no basta."""
    con_cero, sin_cero = codificar_nits(np.array(["0123456789", "123456789"]))
    assert con_cero != sin_cero


def test_codigo_rechaza_lo_que_no_son_digitos() -> None:
    assert (codificar_nits(np.array(["", "89A", "9" * 16, "nan"])) == -1).all()


def test_borrado_produce_el_nit_sin_ese_digito() -> None:
    codigos = codificar_nits(np.array(["900123456"]))
    sin_ultimo = claves_por_borrado(codigos, (0,))
    assert sin_ultimo[0] == codificar_nits(np.array(["90012345"]))[0]
    sin_primero = claves_por_borrado(codigos, (8,))
    assert sin_primero[0] == codificar_nits(np.array(["00123456"]))[0]


def test_borrado_no_aplica_a_nits_mas_cortos_que_la_posicion() -> None:
    codigos = codificar_nits(np.array(["123456"]))
    assert claves_por_borrado(codigos, (9,))[0] == -1


def test_patrones_crecen_como_combinaciones() -> None:
    assert len(patrones_de_borrado(9, 0)) == 1
    assert len(patrones_de_borrado(9, 1)) == 1 + 9
    assert len(patrones_de_borrado(9, 2)) == 1 + 9 + 36


# ── Completitud de la vecindad ────────────────────────────────────────────


@pytest.mark.parametrize("radio", [1, 2])
def test_vecindad_no_pierde_ningun_par_a_distancia_declarada(radio: int) -> None:
    """El juez es la fuerza bruta. Cero falsos negativos es el contrato.

    Se admiten pares de más —el bloqueo genera candidatos, el scorer decide—
    pero perder uno es un techo de recall que ningún umbral levanta después.
    """
    nits = _nits_variados(400, semilla=radio)
    df = pd.DataFrame({"NIT_BASE": nits})
    cfg = NitBlockingConfig(
        enable_exact=False, radio_vecindad=radio, max_bucket_size=10_000, min_nit_length=6
    )
    obtenidos = set()
    for lote in iter_pares_por_nit(df, config=cfg):
        obtenidos |= {(int(a), int(b)) for a, b in lote}
    esperados = {
        (i, j)
        for i, j in combinations(range(len(nits)), 2)
        if distancia_osa(nits[i], nits[j]) <= radio
    }
    assert not (esperados - obtenidos), f"faltan {len(esperados - obtenidos)} pares verdaderos"


def test_radio_cero_no_produce_vecindad() -> None:
    df = pd.DataFrame({"NIT_BASE": _nits_variados(50, 5)})
    cfg = NitBlockingConfig(enable_exact=False, radio_vecindad=0, min_nit_length=6)
    assert list(iter_pares_por_nit(df, config=cfg)) == []


def test_exacto_encuentra_todos_los_nits_repetidos() -> None:
    df = pd.DataFrame({"NIT_BASE": ["900111222", "900111222", "800333444", "900111222"]})
    cfg = NitBlockingConfig(enable_exact=True, radio_vecindad=0, min_nit_length=6)
    pares = set()
    for lote in iter_pares_por_nit(df, config=cfg):
        pares |= {(int(a), int(b)) for a, b in lote}
    assert pares == {(0, 1), (0, 3), (1, 3)}


# ── La política se aplica antes de materializar ───────────────────────────


def _politica_confiable() -> tuple[np.ndarray, np.ndarray]:
    """FUENTE 0 = EXPORTACIONES (se deduplica), 1 = RUES (confiable, no)."""
    return np.array([[True, True], [True, False]], dtype=bool), None  # type: ignore[return-value]


def test_no_se_emiten_pares_internos_de_la_fuente_confiable() -> None:
    df = pd.DataFrame({"NIT_BASE": ["900111222"] * 4})
    codigos = np.array([1, 1, 0, 0], dtype=np.int16)  # dos RUES, dos EXPO
    politica = np.array([[True, True], [True, False]], dtype=bool)
    cfg = NitBlockingConfig(enable_exact=True, radio_vecindad=1, min_nit_length=6)
    pares = set()
    for lote in iter_pares_por_nit(
        df, config=cfg, codigos_fuente=codigos, politica_fuentes=politica
    ):
        pares |= {(int(a), int(b)) for a, b in lote}
    assert (0, 1) not in pares, "par RUES x RUES emitido pese a la política"
    assert (2, 3) in pares, "par EXPO x EXPO debía emitirse"
    assert {(0, 2), (0, 3), (1, 2), (1, 3)} <= pares


def test_el_orden_de_los_lados_no_cambia_el_resultado() -> None:
    """Se indexa el lado pequeño; da igual cuál de los dos sea."""
    nits = _nits_variados(120, 9)
    politica = np.array([[False, True], [True, False]], dtype=bool)
    cfg = NitBlockingConfig(enable_exact=True, radio_vecindad=1, min_nit_length=6)

    def correr(codigos: np.ndarray) -> set[tuple[int, int]]:
        salida = set()
        for lote in iter_pares_por_nit(
            pd.DataFrame({"NIT_BASE": nits}),
            config=cfg,
            codigos_fuente=codigos,
            politica_fuentes=politica,
        ):
            salida |= {(int(a), int(b)) for a, b in lote}
        return salida

    pocos_primero = np.array([0] * 10 + [1] * 110, dtype=np.int16)
    assert correr(pocos_primero) == correr(1 - pocos_primero)


# ── Nada se acumula: los pares salen por lotes ────────────────────────────


def test_los_pares_salen_por_lotes_acotados() -> None:
    """El corte se hace ENTRE bloques: un bloque nunca se parte a la mitad.

    Un bloque está acotado por ``max_bucket_size``, así que su contribución
    tiene techo propio; lo que no puede crecer sin control es el acumulado
    entre bloques, y eso es lo que ``tam_lote`` corta.
    """
    df = pd.DataFrame({"NIT_BASE": [f"90011{i // 4:04d}" for i in range(200)]})
    cfg = NitBlockingConfig(enable_exact=True, radio_vecindad=0, min_nit_length=6)
    lotes = list(iter_pares_por_nit(df, config=cfg, tam_lote=50))
    assert len(lotes) > 1, "50 bloques de 4 registros debían salir en varios lotes"
    assert sum(len(x) for x in lotes) == 50 * (4 * 3 // 2)


def test_bucket_gigante_se_descarta_por_la_cota() -> None:
    df = pd.DataFrame({"NIT_BASE": ["900111222"] * 300})
    cfg = NitBlockingConfig(enable_exact=True, radio_vecindad=0, max_bucket_size=200)
    assert list(iter_pares_por_nit(df, config=cfg)) == []


# ── Primitivas compartidas de política ────────────────────────────────────


def test_indices_de_grupos_concatena_rangos() -> None:
    np.testing.assert_array_equal(
        indices_de_grupos(np.array([3, 10]), np.array([2, 3])), [3, 4, 10, 11, 12]
    )
    assert indices_de_grupos(np.array([], dtype=int), np.array([], dtype=int)).size == 0


def test_bloques_utiles_descarta_los_que_no_pueden_producir_pares() -> None:
    codigos = np.array([1, 1, 2, 2, 3, 3])
    fuentes = np.array([1, 1, 0, 1, 0, 0], dtype=np.int16)  # 1 = confiable
    politica = np.array([[True, True], [True, False]], dtype=bool)
    _orden, inicio, tam = bloques_utiles(codigos, fuentes, politica, tam_maximo=10)
    # El bloque 1 es RUES x RUES (inútil); el 2 es mixto y el 3 es EXPO x EXPO.
    assert len(inicio) == 2 and set(tam.tolist()) == {2}


def test_pares_permitidos_sin_politica_es_el_triangulo_completo() -> None:
    lo, hi = pares_permitidos(np.array([5, 7, 9]), None, None)
    assert set(zip(lo.tolist(), hi.tolist(), strict=True)) == {(5, 7), (5, 9), (7, 9)}


def test_pares_por_bloque_respeta_el_tamano_de_lote() -> None:
    codigos = np.zeros(40, dtype=np.int64)
    fuentes = np.zeros(40, dtype=np.int16)
    politica = np.array([[True]], dtype=bool)
    lotes = list(
        pares_por_bloque(codigos, np.arange(40), fuentes, politica, tam_maximo=100, tam_lote=100)
    )
    assert sum(len(x) for x in lotes) == 40 * 39 // 2


# ── Cobertura de la política: el atajo no cambia el resultado ─────────────


def test_cobertura_elige_la_fuente_pequena() -> None:
    motor = TrustedSourceLSHEngine(profile={}, trusted_sources={"RUES"})
    codigos = np.array([1] * 1000 + [0] * 10, dtype=np.int16)
    politica = np.array([[True, True], [True, False]], dtype=bool)
    semillas = motor._semillas_de_cobertura(codigos, politica)
    assert semillas is not None
    assert set(semillas.tolist()) == set(range(1000, 1010))


def test_cobertura_se_desactiva_cuando_no_ahorra() -> None:
    """Un dedupe clásico permite todo contra todo: no hay nada que recortar."""
    motor = TrustedSourceLSHEngine(profile={}, trusted_sources=set())
    codigos = np.array([0] * 500 + [1] * 500, dtype=np.int16)
    politica = np.ones((2, 2), dtype=bool)
    assert motor._semillas_de_cobertura(codigos, politica) is None


def test_cobertura_sin_politica_no_aplica() -> None:
    motor = TrustedSourceLSHEngine(profile={}, trusted_sources={"RUES"})
    assert motor._semillas_de_cobertura(None, None) is None


def test_el_atajo_por_cobertura_da_los_mismos_candidatos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con y sin atajo, el conjunto de candidatos debe ser idéntico.

    Es la prueba que hace segura la optimización: si algún bucket útil no
    contuviera una semilla, aquí aparecería la diferencia.
    """
    import sqlite3

    rng = np.random.default_rng(4)
    nombres = [f"EMPRESA {i % 300} {'SAS' if i % 3 else 'LTDA'}" for i in range(600)]
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": nombres,
            "NIT_BASE": [f"{900000000 + int(x)}" for x in rng.integers(0, 250, 600)],
            "FUENTE": ["RUES"] * 500 + ["EXPORTACIONES"] * 100,
        }
    )

    def candidatos(directorio: Path, con_atajo: bool) -> set[tuple[int, int]]:
        motor = TrustedSourceLSHEngine(
            profile={"lsh_permutations": 64, "lsh_threshold": 0.5},
            trusted_sources={"RUES"},
        )
        if not con_atajo:
            monkeypatch.setattr(
                type(motor), "_semillas_de_cobertura", lambda *_a, **_k: None, raising=True
            )
        motor.find_candidates(df.copy(), output_dir=str(directorio))
        base = next(directorio.rglob("candidates.db"))
        with sqlite3.connect(base) as cx:
            return {(a, b) for a, b in cx.execute("SELECT idx_0, idx_1 FROM candidate_pairs")}

    con = candidatos(tmp_path / "con", True)
    monkeypatch.undo()
    sin = candidatos(tmp_path / "sin", False)
    assert con == sin, f"el atajo cambió {len(con ^ sin)} pares"
    assert con, "la prueba no vale si no se generó ningún candidato"


# ── Anotación de buckets con semilla durante la indexación ────────────────


def _motor_con_semillas(df: pd.DataFrame, directorio: Path) -> TrustedSourceLSHEngine:
    motor = TrustedSourceLSHEngine(
        profile={"lsh_permutations": 64, "lsh_threshold": 0.5}, trusted_sources={"RUES"}
    )
    motor.find_candidates(df.copy(), output_dir=str(directorio))
    return motor


def _marco_dos_fuentes(n_rues: int = 400, n_expo: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    total = n_rues + n_expo
    return pd.DataFrame(
        {
            "NOMBRE_LIMPIO": [f"EMPRESA {i % 200} SAS" for i in range(total)],
            "NIT_BASE": [f"{900000000 + int(x)}" for x in rng.integers(0, 180, total)],
            "FUENTE": ["RUES"] * n_rues + ["EXPORTACIONES"] * n_expo,
        }
    )


def test_la_indexacion_anota_los_buckets_con_semilla(tmp_path: Path) -> None:
    """Anotar cuesta nada durante la indexación; averiguarlo después cuesta
    recorrer el índice entero (5,5 min medidos sobre 184 M de filas)."""
    import sqlite3

    motor = _motor_con_semillas(_marco_dos_fuentes(), tmp_path)
    indice = next(tmp_path.rglob("lsh_index.db"))
    with sqlite3.connect(indice) as cx:
        anotados = cx.execute("SELECT COUNT(*) FROM semilla_buckets").fetchone()[0]
        huella = cx.execute("SELECT value FROM metadata WHERE key = 'semillas_fp'").fetchone()
    assert anotados > 0
    assert huella and huella[0] == motor._huella_semillas()


def test_la_anotacion_coincide_con_recorrer_el_indice(tmp_path: Path) -> None:
    """El atajo y el camino de respaldo deben dar el mismo conjunto."""
    import sqlite3

    motor = _motor_con_semillas(_marco_dos_fuentes(), tmp_path)
    indice = next(tmp_path.rglob("lsh_index.db"))
    semillas = motor._semillas
    assert semillas is not None
    with sqlite3.connect(indice) as cx:
        anotado = motor._hashes_con_semilla(cx, semillas)
        # Invalidar la huella obliga al camino de respaldo.
        cx.execute("UPDATE metadata SET value = 'otra' WHERE key = 'semillas_fp'")
        recorrido = motor._hashes_con_semilla(cx, semillas)
    assert {k: sorted(v) for k, v in anotado.items()} == {
        k: sorted(v) for k, v in recorrido.items()
    }


def test_huella_de_semillas_cambia_con_las_semillas() -> None:
    motor = TrustedSourceLSHEngine(profile={}, trusted_sources={"RUES"})
    assert motor._huella_semillas() == "sin-semillas"
    motor._semillas = np.array([1, 2, 3], dtype=np.int64)
    primera = motor._huella_semillas()
    motor._semillas = np.array([1, 2, 4], dtype=np.int64)
    assert motor._huella_semillas() != primera
