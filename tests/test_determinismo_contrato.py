"""Contrato de determinismo bit a bit (F0.6, v0.8.0).

Dos corridas del pipeline de producción (``deduplicate_auto``) sobre el
mismo input deben producir correlativas IDÉNTICAS (mismos ID_GRUPO fila a
fila y mismo hash SHA-256 de la columna). La reproducibilidad exacta fue la
herramienta que permitió cerrar el diagnóstico del incidente datasketch
(CHANGELOG [0.7.6]); este test la congela como contrato permanente.

El dataset es sintético e inline (sin dependencias de archivos) para que el
contrato sea verificable en cualquier clon limpio.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
from pathlib import Path

import pandas as pd

from record_linkage.deduplication.auto import deduplicate_auto


def _dataset_mixto() -> pd.DataFrame:
    """~90 filas mixtas: duplicados CON_NIT y SIN_NIT con variantes de nombre."""
    filas: list[dict[str, str]] = []
    variantes = ["{} S.A.S.", "{} SAS", "{}  S A S", "{} LTDA"]
    for i in range(15):  # CON_NIT: 15 grupos x 3 variantes
        nit = f"{900100000 + i}"
        base = f"COMERCIALIZADORA ANDINA {i:02d}"
        for j in range(3):
            filas.append({"NIT": nit, "RAZON_SOCIAL": variantes[j].format(base)})
    for i in range(15):  # SIN_NIT: 15 grupos x 3 variantes (NIT vacío)
        base = f"DISTRIBUCIONES DEL ORIENTE {i:02d}"
        for j in range(3):
            filas.append({"NIT": "", "RAZON_SOCIAL": variantes[j].format(base)})
    return pd.DataFrame(filas, dtype=str)


def _hash_id_grupo(corr: pd.DataFrame) -> str:
    orden = corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    contenido = "|".join(orden["ID_GRUPO"].astype(str).tolist())
    return hashlib.sha256(contenido.encode()).hexdigest()


def _correr(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        corr, _ = deduplicate_auto(
            df_input=df.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(out_dir),
        )
    return corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)


def test_doble_corrida_produce_correlativa_identica(tmp_path: Path) -> None:
    """Corrida 1 == Corrida 2: ID_GRUPO fila a fila y hash SHA-256."""
    df = _dataset_mixto()
    corr_1 = _correr(df, tmp_path / "run1")
    corr_2 = _correr(df, tmp_path / "run2")

    assert len(corr_1) == len(corr_2) == len(df)
    pd.testing.assert_series_equal(
        corr_1["ID_GRUPO"].astype(str),
        corr_2["ID_GRUPO"].astype(str),
        check_names=False,
    )
    assert _hash_id_grupo(corr_1) == _hash_id_grupo(corr_2), (
        "Determinismo roto: dos corridas con el mismo input produjeron "
        "correlativas distintas. Buscar aleatoriedad sin seed fijo."
    )
