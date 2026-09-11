"""Test aislado del bloqueo por NIT base (v2.5.0 P0-1 Paso 1.1).

Verifica que el módulo ``engine.lsh.nit_blocking`` cumple su contrato:

1. Dos registros con NIT base idéntico DEBEN aparecer como par candidato,
   aunque sus nombres no se parezcan en absoluto. Este es el caso de
   negocio crítico que motivó el ítem (p. ej. "EY COLOMBIA" ↔ "ERNST &
   YOUNG" con mismo NIT).
2. Dos registros con NIT base a distancia Levenshtein 1 también deben
   aparecer si los vecinos están activos (typo de un dígito, dígito
   extra/faltante).
3. NITs vacíos / muy cortos no producen pares (no se debe agrupar 'todos
   los registros sin NIT' como uno solo).
4. Buckets de tamaño superior a ``max_bucket_size`` se descartan (cota
   contra explosión cuadrática).
5. La salida es determinista entre ejecuciones (paridad de reproducibilidad).
6. El módulo no rompe con DataFrame vacío.

Estos tests se mantienen fuera del pipeline para garantizar que el bloqueo
funciona como unidad aislada, independiente de las regresiones del scorer
o el clusterer.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.engine.lsh.nit_blocking import (
    NitBlockingConfig,
    block_by_nit_base,
)


def test_nit_exacto_produce_par() -> None:
    """Mismo NIT_BASE no vacío → par candidato, aunque nombres sean distintos."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900123456", "900123456"],
            "RAZON_SOCIAL": ["EY COLOMBIA", "ERNST AND YOUNG"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=False)
    pairs = block_by_nit_base(df, config=cfg)
    assert pairs == {(0, 1)}, (
        f"Esperaba {{(0, 1)}} para dos registros con mismo NIT, obtuve {pairs}"
    )


def test_nit_vacio_no_produce_par() -> None:
    """NITs vacíos no se agrupan entre sí (sería una sobre-fusión catastrófica)."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["", "", "  ", "nan"],
            "RAZON_SOCIAL": ["EMPRESA A", "EMPRESA B", "EMPRESA C", "EMPRESA D"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=True)
    pairs = block_by_nit_base(df, config=cfg)
    assert pairs == set(), f"NITs vacíos no deben generar pares; obtuve {pairs}"


def test_nit_corto_descartado() -> None:
    """NITs con longitud < min_nit_length se descartan."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["123", "123", "9001234567", "9001234567"],
            "RAZON_SOCIAL": ["A", "B", "C", "D"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=False, min_nit_length=6)
    pairs = block_by_nit_base(df, config=cfg)
    # Solo el par (2, 3) con NIT 9001234567 debe aparecer.
    assert pairs == {(2, 3)}, f"Esperaba solo (2, 3); obtuve {pairs}"


def test_bucket_grande_descartado() -> None:
    """Buckets > max_bucket_size se omiten (cota contra explosión cuadrática)."""
    # 50 registros con el mismo NIT genérico — simula un código falso compartido.
    n = 50
    df = pd.DataFrame(
        {
            "NIT_BASE": ["999999999"] * n,
            "RAZON_SOCIAL": [f"EMPRESA {i}" for i in range(n)],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=False, max_bucket_size=10)
    pairs = block_by_nit_base(df, config=cfg)
    assert pairs == set(), (
        f"Bucket de tamaño 50 debe descartarse con max_bucket_size=10; obtuve {len(pairs)} pares"
    )


def test_vecino_distancia_uno_produce_par() -> None:
    """NITs que difieren en un dígito (sustitución) → par candidato si neighbors=True."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900123456", "900123457"],  # difieren solo en el último dígito
            "RAZON_SOCIAL": ["PINTUCO", "PINTUCO SUCURSAL"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=False, enable_neighbors=True)
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) in pairs, (
        f"NITs a distancia 1 deben producir par cuando neighbors=True; pares={pairs}"
    )


def test_vecino_inserción_dígito_extra() -> None:
    """NIT con un dígito extra al final → vecino del NIT base."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900123456", "9001234560"],  # uno tiene un dígito extra
            "RAZON_SOCIAL": ["A", "B"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=False, enable_neighbors=True)
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) in pairs, f"NIT con dígito extra debe ser vecino del original; pares={pairs}"


def test_vecinos_desactivados_no_producen_pares() -> None:
    """Si neighbors=False, solo el bloqueo exacto produce pares."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900123456", "900123457"],  # distancia 1
            "RAZON_SOCIAL": ["A", "B"],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=False)
    pairs = block_by_nit_base(df, config=cfg)
    assert pairs == set(), f"Sin neighbors no deben aparecer pares por distancia 1; obtuve {pairs}"


def test_dataframe_vacio() -> None:
    """No debe romper con DataFrame vacío."""
    df = pd.DataFrame({"NIT_BASE": []})
    pairs = block_by_nit_base(df)
    assert pairs == set()


def test_columna_inexistente_lanza_error() -> None:
    """Pre-condición clara: la columna NIT debe existir."""
    df = pd.DataFrame({"OTRA_COL": ["x"]})
    with pytest.raises(ValueError, match="no encontrada"):
        block_by_nit_base(df, nit_column="NIT_BASE")


def test_resultado_determinista() -> None:
    """Dos ejecuciones sobre el mismo input dan el mismo resultado."""
    df = pd.DataFrame(
        {
            "NIT_BASE": [
                "900123456",
                "900123456",
                "900123457",
                "800111111",
                "800111111",
                "800111112",
            ],
            "RAZON_SOCIAL": [f"E{i}" for i in range(6)],
        }
    )
    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=True)
    p1 = block_by_nit_base(df, config=cfg)
    p2 = block_by_nit_base(df, config=cfg)
    assert p1 == p2, "El bloqueo debe ser determinista"
    # Verifica además que sí encuentra los pares por NIT exacto.
    assert (0, 1) in p1
    assert (3, 4) in p1


def test_config_invalida_lanza_error() -> None:
    """Config inválida falla en __post_init__ (fail-fast)."""
    with pytest.raises(ValueError):
        NitBlockingConfig(max_bucket_size=0)
    with pytest.raises(ValueError):
        NitBlockingConfig(min_nit_length=0)


def test_ground_truth_real_aumenta_cobertura() -> None:
    """Sobre el ground truth exhaustivo, el bloqueo por NIT captura ≥ 42 % de pares.

    Esta es la propiedad de NEGOCIO: el bloqueo por NIT cubre el cuello
    estructural del recall (pares cuyo único conector posible es el NIT).
    Medido (v0.10.0, dataset reconstruido de 1460 regs con una fracción alta
    de casos 'positivo_sin_nit'): 46.8 %. Floor 42 % deja margen de seguridad.
    """
    from itertools import combinations
    from pathlib import Path

    from record_linkage.processing.nit import AdvancedNitProcessor

    csv = Path(__file__).parent / "data" / "golden_truth_exhaustivo.csv"
    truth = pd.read_csv(csv, dtype={"NIT": str})
    truth["NIT"] = truth["NIT"].fillna("")
    nproc = AdvancedNitProcessor()
    nit_res = nproc.process_for_deduplication(truth["NIT"])
    truth["NIT_BASE"] = nit_res["NIT_BASE"]

    cfg = NitBlockingConfig(enable_exact=True, enable_neighbors=True)
    pairs_block = block_by_nit_base(truth, config=cfg)

    # Pares verdaderos
    truth_pairs: set[tuple[int, int]] = set()
    for _, sub in truth.groupby("ID_GROUP"):
        if len(sub) > 1:
            truth_pairs.update(combinations(sorted(sub.index.tolist()), 2))

    # Recall del bloqueo: fracción de pares verdaderos capturados.
    tp = len(pairs_block & truth_pairs)
    recall_blocking = tp / len(truth_pairs)
    assert recall_blocking >= 0.42, (
        f"El bloqueo por NIT solo captura {recall_blocking:.1%} de pares verdaderos; "
        f"esperaba ≥ 42 %. tp={tp}/{len(truth_pairs)}"
    )


# ════════════════════════════════════════════════════════════════════════
# v2.7.0 — Tests del filtro min_name_similarity (P2 Camino #2)
# ════════════════════════════════════════════════════════════════════════


def test_min_name_similarity_off_no_filtra() -> None:
    """Con min_name_similarity=0.0 (default), no se filtra ningún par."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900111222", "900111222"],
            "RAZON_SOCIAL": ["EMPRESA UNO", "ALGO TOTALMENTE DISTINTO"],
        }
    )
    cfg = NitBlockingConfig(min_name_similarity=0.0)
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) in pairs, "Sin filtro, el par debe aparecer."


def test_min_name_similarity_alto_descarta_nombres_distintos() -> None:
    """Con min_name_similarity alto, descarta pares con nombres muy distintos."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900111222", "900111222"],
            "RAZON_SOCIAL": [
                "EY COLOMBIA",  # tokens muy diferentes a 'ERNST AND YOUNG'
                "ERNST AND YOUNG",
            ],
        }
    )
    cfg = NitBlockingConfig(min_name_similarity=0.50)
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) not in pairs, "EY ↔ ERNST debe descartarse con thr=0.50 (name_sim ~31 %)."


def test_min_name_similarity_conserva_nombres_similares() -> None:
    """Con min_name_similarity moderado, conserva pares con nombres similares."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900111222", "900111222"],
            "RAZON_SOCIAL": ["ECOPETROL SA", "ECOPETROL LIMITADA"],
        }
    )
    cfg = NitBlockingConfig(min_name_similarity=0.50)
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) in pairs, "ECOPETROL SA ↔ ECOPETROL LIMITADA debe conservarse (name_sim alta)."


def test_min_name_similarity_columna_inexistente_no_rompe() -> None:
    """Si la columna no existe, se omite el filtro (no rompe el pipeline)."""
    df = pd.DataFrame(
        {
            "NIT_BASE": ["900111222", "900111222"],
            # Sin columna RAZON_SOCIAL.
        }
    )
    cfg = NitBlockingConfig(min_name_similarity=0.50)
    # No debe lanzar; debe emitir warning y devolver pares sin filtro.
    pairs = block_by_nit_base(df, config=cfg)
    assert (0, 1) in pairs


def test_min_name_similarity_valor_invalido() -> None:
    """Validación: min_name_similarity ∈ [0, 1]."""
    with pytest.raises(ValueError, match=r"min_name_similarity"):
        NitBlockingConfig(min_name_similarity=-0.1)
    with pytest.raises(ValueError, match=r"min_name_similarity"):
        NitBlockingConfig(min_name_similarity=1.5)
